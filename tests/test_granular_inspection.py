"""Actual API/client/adapter checks; scripted chat is not real-model qualification."""

import json

import pytest

from fli.chunking import default_tokenizer
from fli.presentation import evidence_json
from fli.reference_format import expanded
from fli_demo.agent import Reply, ToolCall, run_turn
from fli_demo.client import IndexClient
from tests.test_granular_navigation import OPENING, QUALIFIER, child_named, read, ready, text_of
from tests.test_modular_ingestion import setup, upload
from tests.test_worker_handlers import drain


def api_read(env, selector, *, view="compact", selectors="handles", budget=None):
    params = {"view": view, "selectors": selectors}
    if budget is not None:
        params["maxEvidenceTokens"] = budget
    return env.client.post(
        f"/v1/libraries/{env.lib}/documents:read",
        params=params,
        json=selector,
        headers={"X-Agent-Id": "reader"},
    )


def assert_inspection(evidence):
    assert evidence["disposition"] == "inspected"
    assert not evidence["complete"] and not evidence["selectionComplete"] and not evidence["documentComplete"]
    assert "size/coverage inspection (includeText=false), not a source or outline read" in evidence["nextAction"]
    assert "The same maxTokens and snapshot checks apply" in evidence["nextAction"]
    for document in evidence["documents"]:
        assert not document["complete"] and not document["selectionComplete"] and not document["documentComplete"]
        assert not {"sections", "outline", "candidateImages", "sourceNavigation"} & document.keys()
    serialized = json.dumps(evidence)
    assert OPENING not in serialized and QUALIFIER not in serialized and "numberingDefinitions" not in serialized


@pytest.mark.parametrize("view", ["raw", "compact", "references"])
@pytest.mark.parametrize("selectors", ["handles", "legacy"])
async def test_outline_inspection_returns_pinned_same_page_recovery_without_source_or_entries(view, selectors):
    env = await ready()
    parent = child_named(env, None, "Operating procedure")
    original = {
        **parent["outlineSelector"],
        "includeText": False,
        "afterSection": 2,
        "sectionLimit": 1,
        "maxTokens": 200000,
    }
    response = api_read(env, original, view=view, selectors=selectors)
    assert response.status_code == 200, response.text
    payload = response.json()
    evidence = payload if view == "raw" else expanded(payload["evidence"])
    assert_inspection(evidence)
    assert evidence["readSelector"] == {**original, "includeText": True}
    if view != "raw":
        assert not payload["attachments"]
        assert payload["metadata"]["budget"]["returnedTokens"] == len(
            default_tokenizer().encode(evidence_json(payload["evidence"]))
        )
    recovered = api_read(env, evidence["readSelector"], view=view, selectors=selectors).json()
    recovered = recovered if view == "raw" else expanded(recovered["evidence"])
    assert recovered["disposition"] == "outline"
    rows = recovered["documents"][0]["outline"]
    assert len(rows) == 1 and rows[0]["heading"] == "Eligibility"
    assert all("text" not in row for row in rows)
    assert not text_of(recovered) and OPENING not in json.dumps(recovered)


@pytest.mark.parametrize("view", ["compact", "references"])
async def test_client_outline_true_and_default_deliver_same_entries(view):
    env = await ready(view=view)
    default = read(env, {"mode": "outline", "documents": [env.pin]})
    explicit = read(env, {"mode": "outline", "documents": [env.pin], "includeText": True})
    assert default["documents"][0]["outline"] == explicit["documents"][0]["outline"]
    assert default["documents"][0]["outline"]
    assert all("text" not in row for row in default["documents"][0]["outline"])


@pytest.mark.parametrize("view", ["raw", "compact"])
@pytest.mark.parametrize("mode", ["full", "selected"])
async def test_inspection_preserves_exact_full_or_selected_source_scope(view, mode):
    env = await ready()
    selection = {"mode": "full", "documents": [{"documentId": env.doc["id"]}]}
    if mode == "selected":
        section = child_named(env, None, "Operating procedure")
        selection = section["readSelector"]
        assert selection["documents"][0]["nodeScope"] == "section"
    original = {**selection, "includeText": False, "maxTokens": 200000}
    response = api_read(env, original, view=view).json()
    evidence = response if view == "raw" else response["evidence"]
    assert_inspection(evidence)
    assert evidence["readSelector"] == {
        **original,
        "includeText": True,
        "documents": [{**selection["documents"][0], **env.pin}],
    }
    recovered = api_read(env, evidence["readSelector"], view=view).json()
    recovered = recovered if view == "raw" else recovered["evidence"]
    assert OPENING in text_of(recovered)
    assert (QUALIFIER in text_of(recovered)) == (mode == "full")
    assert recovered["documentComplete"] == (mode == "full") and recovered["selectionComplete"]


@pytest.mark.parametrize("view", ["raw", "compact", "references"])
async def test_inspection_recovery_never_bypasses_tiny_effective_budget(view):
    env = await ready()
    response = api_read(
        env,
        {"mode": "outline", "documents": [env.pin], "includeText": False, "maxTokens": 1 if view == "raw" else 999},
        view=view,
        budget=None if view == "raw" else 1,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    evidence = payload if view == "raw" else expanded(payload["evidence"])
    assert_inspection(evidence)
    assert evidence["readSelector"]["maxTokens"] == 1
    result = api_read(env, evidence["readSelector"], view=view).json()
    refused = result if view == "raw" else expanded(result["evidence"])
    assert refused["disposition"] == "budget_exceeded"
    assert "readSelector" not in refused
    assert all("outline" not in document and "sections" not in document for document in refused["documents"])
    assert QUALIFIER not in json.dumps(refused) and OPENING not in json.dumps(refused)
    if view != "raw":
        assert not result["attachments"] and not result["metadata"]["budget"]["fits"]


async def test_inspection_recovery_still_refuses_changed_snapshot():
    env = await ready()
    evidence = read(env, {"mode": "outline", "documents": [env.pin], "includeText": False})
    document = await env.s.documents.get(env.lib, env.doc["id"])
    document["contentRevision"] += 1
    await env.s.documents.replace(document)
    response = api_read(env, evidence["readSelector"])
    assert response.status_code == 409
    assert "readSelector" not in response.json() and "outline" not in response.json()
    assert OPENING not in response.text


async def test_inspection_during_source_change_returns_no_recovery_selector(monkeypatch):
    env = await ready()
    original_download = env.s.blobs.download

    async def download(path, *args, **kwargs):
        result = await original_download(path, *args, **kwargs)
        document = await env.s.documents.get(env.lib, env.doc["id"])
        document["metadataVersion"] += 1
        await env.s.documents.replace(document)
        return result

    monkeypatch.setattr(env.s.blobs, "download", download)
    response = api_read(env, {"mode": "outline", "documents": [env.pin], "includeText": False})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "document_changed"
    assert "readSelector" not in response.json() and OPENING not in response.text


@pytest.mark.parametrize("profile", ["current-v1", "custom-structured-v1", "microsoft-indexer-layout-v1"])
async def test_other_profile_inspection_has_no_granular_recovery_contract(profile):
    client, services, library, worker = setup(profile)
    document = upload(client, library)
    await drain(services, worker)
    index = IndexClient("http://testserver", "k", http=client)
    result = index.read_documents(library, "reader", documents=[{"documentId": document}], includeText=False)
    assert result["evidence"]["disposition"] == "inspected"
    assert "readSelector" not in result["evidence"]
    assert "size/coverage inspection" not in result["evidence"]["nextAction"]


async def test_normal_agent_receives_and_copies_outline_inspection_recovery_then_reads_source():
    env = await ready()
    calls, seen = 0, []

    def chat(messages, tools):
        nonlocal calls
        calls += 1
        name = next(
            tool["function"]["name"] for tool in tools if tool["function"]["name"].startswith("read_documents_")
        )

        def invoke(selector):
            return Reply(None, [ToolCall(str(calls), name, json.dumps(selector))])

        if calls == 1:
            return invoke({"mode": "outline", "documents": [env.pin], "includeText": False, "maxTokens": 18000})
        evidence = json.loads(messages[-1]["content"])
        seen.append(evidence)
        if calls == 2:
            assert_inspection(evidence)
            assert evidence["readSelector"] == {
                "mode": "outline",
                "documents": [env.pin],
                "includeText": True,
                "maxTokens": 18000,
                "afterSection": 0,
                "sectionLimit": 100,
            }
            return invoke(evidence["readSelector"])
        if calls == 3:
            assert evidence["disposition"] == "outline" and evidence["documents"][0]["outline"]
            assert not text_of(evidence)
            return invoke(evidence["documents"][0]["outline"][0]["readSelector"])
        assert calls == 4 and OPENING in text_of(evidence) and QUALIFIER not in text_of(evidence)
        citation = evidence["documents"][0]["sections"][-1]["citation"]
        return Reply(f"The procedure starts with a source review. ({citation})")

    turn = run_turn(
        chat,
        env.index,
        {"id": "reader", "libraryIds": [env.lib]},
        [],
        "Inspect the outline, then read the opening section and cite how the procedure starts.",
        context_token_budget=200000,
        image_inputs=False,
    )
    assert calls == 4 and len(turn.searches) == 3 and len(seen) == 3
    assert all(trace.library_id == env.lib and trace.error is None for trace in turn.searches)
