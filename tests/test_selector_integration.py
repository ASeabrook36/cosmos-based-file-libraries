"""Accepted selector defaults reach the normal adapter without changing frozen cells."""

import copy
import json

import httpx
import pytest

from fli.selector_hints import GUIDANCE
from fli_demo.agent import Reply, ToolCall, _run_documents, _run_search, run_turn
from fli_demo.client import IndexClient
from scripts.retrieval_p1 import make_index
from tests.test_reading import ctx as reading_ctx
from tests.test_reading import drain, upload

ctx = reading_ctx


@pytest.mark.parametrize("mode", [None, "legacy"])
def test_normal_and_rollback_clients_send_consistent_selectors_on_every_retrieval_route(mode):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"tools": []})

    with httpx.Client(base_url="http://index", transport=httpx.MockTransport(handler)) as http:
        index = IndexClient("http://index", "k", http=http, **({"selector_mode": mode} if mode else {}))
        pin = {"documentId": "doc", "generation": 2, "contentRevision": 3}
        index.tools("bound", "agent")
        index.discover_documents("bound", "agent")
        index.search("bound", "agent", "question", document=pin, max_evidence_tokens=2000)
        index.read_documents("bound", "agent", documents=[pin])
        index.read_images("bound", "agent", **pin, imageIds=["image"])
    assert len(requests) == 5
    for request in requests:
        assert request.url.path.startswith("/v1/libraries/bound/")
        assert request.headers["X-Agent-Id"] == "agent"
        assert request.url.params["selectors"] == (mode or "handles")
        if not request.url.path.endswith("/tools"):
            assert request.url.params["view"] == "compact"
    assert json.loads(requests[2].content)["document"] == pin
    assert requests[2].url.params["maxEvidenceTokens"] == "2000"


@pytest.mark.parametrize(
    "environment,explicit,expected",
    [
        (None, None, "handles"),
        ("legacy", None, "legacy"),
        ("handles", "legacy", "legacy"),
        ("invalid", "handles", "handles"),
    ],
)
def test_environment_default_and_explicit_rollback_precedence(monkeypatch, environment, explicit, expected):
    monkeypatch.setenv("DEV_API_KEY", "k")
    monkeypatch.setenv("INDEX_API_URL", "http://index")
    if environment is None:
        monkeypatch.delenv("DEMO_SELECTOR_MODE", raising=False)
    else:
        monkeypatch.setenv("DEMO_SELECTOR_MODE", environment)
    index = IndexClient.from_env(selector_mode=explicit)
    try:
        assert index.selector_mode == expected
        assert index.evidence_view == "compact"
    finally:
        index.http.close()


@pytest.mark.parametrize("explicit", [None, "invalid"])
def test_invalid_selector_configuration_fails_before_requests(monkeypatch, explicit):
    monkeypatch.setenv("DEV_API_KEY", "k")
    monkeypatch.setenv("DEMO_SELECTOR_MODE", "invalid")
    with pytest.raises(ValueError, match="unsupported retrieval presentation"):
        IndexClient.from_env(selector_mode=explicit)


@pytest.mark.parametrize(
    "cell,view,mode",
    [
        ("baseline", "compact", "legacy"),
        ("format-only", "references", "legacy"),
        ("selectors-only", "compact", "handles"),
    ],
)
def test_frozen_p1_and_i1_cell_factory_ignores_normal_environment_default(monkeypatch, cell, view, mode):
    monkeypatch.setenv("DEV_API_KEY", "k")
    monkeypatch.setenv("DEMO_SELECTOR_MODE", "invalid")
    index = make_index({"indexUrl": "http://index"}, cell)
    try:
        assert (index.evidence_view, index.selector_mode) == (view, mode)
    finally:
        index.http.close()


async def test_normal_agent_copies_search_outline_and_selected_handles_without_losing_source_or_scope(ctx):
    client, services, library, worker = ctx
    document = upload(client, library, text="# First\n\nExact first.\n\n# Next\n\nExact second.")
    await drain(services, worker)
    index = IndexClient("http://testserver", "k", http=client)
    selected = {}
    calls = 0

    def chat(messages, tools):
        nonlocal calls
        calls += 1
        names = {t["function"]["name"].split("_library_")[0]: t["function"]["name"] for t in tools}
        assert all(GUIDANCE in t["function"]["description"] for t in tools)
        if calls == 1:
            return Reply(None, [ToolCall("search", names["search"], '{"query":"first instruction"}')])
        evidence = json.loads(messages[-1]["content"])
        assert evidence["selectorGuidance"] == GUIDANCE
        if calls == 2:
            hit = evidence["results"][0]
            assert hit["documentSelector"]["documentId"] == document
            assert set(hit["documentSelector"]) == {"documentId", "generation", "contentRevision"}
            selected["pin"] = hit["documentSelector"]
            return Reply(
                None,
                [
                    ToolCall(
                        "outline",
                        names["read_documents"],
                        json.dumps({**hit["outlineSelector"], "libraryId": "untrusted-library"}),
                    )
                ],
            )
        if calls == 3:
            selector = evidence["documents"][0]["outline"][0]["readSelector"]
            assert all(selector["documents"][0][k] == v for k, v in selected["pin"].items())
            return Reply(None, [ToolCall("selected", names["read_documents"], json.dumps(selector))])
        assert calls == 4
        assert evidence["selectionComplete"] and not evidence["complete"]
        assert not evidence["documentComplete"]
        section = evidence["documents"][0]["sections"][0]
        assert section["text"] == "Exact first."
        assert "procedure.md" in section["citation"]
        assert "blobPath" not in json.dumps(evidence) and "embedding" not in json.dumps(evidence)
        return Reply("The first instruction is Exact first. (procedure.md)")

    turn = run_turn(
        chat,
        index,
        {"id": "agent", "libraryIds": [library]},
        [],
        "What is the first instruction in procedure.md?",
        image_inputs=False,
    )
    assert calls == 4
    assert [trace.operation for trace in turn.searches] == ["search", "read", "read"]
    assert all(trace.library_id == library and trace.error is None for trace in turn.searches)
    assert all(query_library == library for query_library, _ in services.reader.queries)


@pytest.mark.parametrize(
    "change,category",
    [
        ("revision", "document_changed"),
        ("delete", "document_not_found"),
        ("personal", "library_not_bound_to_agent"),
        ("foreign", "document_not_found"),
    ],
)
async def test_normal_adapter_does_not_unpin_or_broaden_rejected_read(ctx, change, category):
    client, services, library, worker = ctx
    document = upload(client, library)
    await drain(services, worker)
    index = IndexClient("http://testserver", "k", http=client)
    outline = index.read_documents(library, "agent", mode="outline", documents=[{"documentId": document}])
    selection = outline["evidence"]["documents"][0]["outline"][0]["readSelector"]
    before = copy.deepcopy(selection)
    if change == "revision":
        record = await services.documents.get(library, document)
        record["contentRevision"] += 1
        await services.documents.replace(record)
    elif change == "delete":
        index.delete_document(library, document)
    elif change == "personal":
        record = await services.libraries.get(library)
        record.update(kind="personal", boundAgentId="different-agent")
        await services.libraries.replace(record)
    else:
        library = index.create_library("shared", "Different library")["id"]
    traces = []
    call = ToolCall("read", "read_documents_library_procedures", json.dumps(selection))
    content, images = _run_documents(call, index, {"id": "agent"}, {call.name: library}, {}, traces, lambda: 0, 200000)
    error = json.loads(content)
    assert error["errorCategory"] == category and not error["complete"]
    assert not images and len(traces) == 1 and not services.reader.queries
    assert selection == before
    assert document not in content and "Exact" not in content


async def test_normal_selector_client_keeps_full_batch_budget_refusal_atomic(ctx):
    client, services, library, worker = ctx
    documents = [upload(client, library, name=f"source-{i}.md") for i in range(2)]
    await drain(services, worker)
    index = IndexClient("http://testserver", "k", http=client)
    discovery = index.discover_documents(library, "agent")
    pins = [row["documentSelector"] for row in discovery["evidence"]["documents"]]
    assert {pin["documentId"] for pin in pins} == set(documents)
    response = index.read_documents(library, "agent", documents=pins, maxTokens=1)
    evidence = response["evidence"]
    assert evidence["disposition"] == "budget_exceeded" and not evidence["complete"]
    assert not response["attachments"]
    assert len(evidence["documents"]) == 2
    assert all("sections" not in row and "outline" not in row for row in evidence["documents"])
    assert response["metadata"]["budget"]["requiredTokens"] > 1


def test_normal_adapter_rejects_bare_document_id_before_any_search():
    def handler(request):
        pytest.fail("A bare ID must not be repaired into an unpinned request")

    with httpx.Client(base_url="http://index", transport=httpx.MockTransport(handler)) as http:
        index = IndexClient("http://index", "k", http=http)
        call = ToolCall("search", "search_library_bound", '{"query":"fact","document":"private-bare-id"}')
        traces = []
        content, _ = _run_search(call, index, {"id": "agent"}, {call.name: "bound"}, {}, traces, lambda: 0)
    assert json.loads(content)["errorCategory"] == "invalid_selector"
    assert "private-bare-id" not in content and len(traces) == 1
