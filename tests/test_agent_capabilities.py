"""Natural-test regressions: explicit modes, resolvable source scope and selected visuals."""

import copy
import json

import pytest

from fli.query import SearchFilters, check_query, partition_key_for, render_search
from fli.tool import tool_definitions
from fli_demo.agent import Reply, ToolCall, run_turn
from fli_demo.telemetry import metrics
from tests.fakes import FakeChat
from tests.test_demo import scripted
from tests.test_images import office
from tests.test_page_images import prepared
from tests.test_reading import ctx as reading_ctx
from tests.test_reading import drain, upload
from tests.test_retrieval_followups import EvidenceIndex
from tests.test_worker_handlers import ctx as worker_ctx
from tests.test_worker_handlers import drain as drain_images
from tests.test_worker_handlers import upload as upload_image

ctx = reading_ctx
image_ctx = worker_ctx


def selected(client, lib, doc, **scope):
    return client.post(
        f"/v1/libraries/{lib}/documents:read",
        headers={"X-Agent-Id": "agent"},
        json={"mode": "selected", "documents": [{"documentId": doc, **scope}]},
    )


async def test_semantic_file_selection_is_explicit_and_cannot_collapse_evidence_search(ctx):
    client, services, lib, worker = ctx
    doc = upload(client, lib, text="Planning evidence. " * 500)
    await drain(services, worker)
    path = f"/v1/libraries/{lib}"
    defs = tool_definitions({"name": "Evidence"})
    assert len(defs) == 4
    props = defs[0]["function"]["parameters"]["properties"]
    assert "distinctDocuments" not in props and "document" in props
    candidate = client.post(
        path + "/documents:discover", json={"mode": "semantic_candidates", "query": "planning"}
    ).json()
    assert candidate["resultUnit"] == "documents" and candidate["selectionStatus"] == "candidates_found"
    assert not candidate["exhaustive"] and candidate["candidateWindow"] == 40
    assert [d["documentId"] for d in candidate["documents"]] == [doc]
    assert not candidate["results"]
    evidence = client.post(path + "/search", json={"query": "planning", "document": {"documentId": doc}}).json()
    assert evidence["mode"] == "evidence_search" and evidence["resultUnit"] == "passages"
    assert len(evidence["results"]) > 1
    assert all(r["documentId"] == doc and r["evidenceId"] for r in evidence["results"])
    assert client.post(path + "/documents:discover", json={"mode": "semantic_candidates"}).status_code == 400
    assert client.post(path + "/documents:discover", json={"query": "planning"}).status_code == 400


def test_scoped_sql_keeps_float16_top_single_library_and_rank_routing():
    for terms in ([], ["continuity"]):
        q = render_search("bound", [0.5], terms, SearchFilters(), 16, "selected", 2)
        check_query(q.sql)
        assert "c.documentId = @documentId" in q.sql and "c.generation = @generation" in q.sql
        assert q.sql.startswith("SELECT TOP 16 ") and q.sql.count("c.libraryId = @libraryId") == 1
        assert partition_key_for(q.sql, "bound") == (None if terms else ["bound"])
        assert {p["name"]: p["value"] for p in q.parameters}["@documentId"] == "selected"


async def test_scoped_search_rejects_foreign_stale_and_changed_empty_results(ctx, monkeypatch):
    client, services, lib, worker = ctx
    doc = upload(client, lib)
    await drain(services, worker)
    endpoint = f"/v1/libraries/{lib}/search"
    for selector, status in (
        ({"documentId": "foreign"}, 404),
        ({"documentId": doc, "generation": 99}, 409),
        ({"documentId": doc, "contentRevision": 99}, 409),
    ):
        assert client.post(endpoint, json={"query": "safety", "document": selector}).status_code == status

    async def change(*args):
        record = await services.documents.get(lib, doc)
        record["contentRevision"] += 1
        await services.documents.replace(record)
        return []

    monkeypatch.setattr(services.reader, "search", change)
    assert client.post(endpoint, json={"query": "safety", "document": {"documentId": doc}}).status_code == 409


async def test_outline_paging_selected_content_budgets_and_stale_section_references(ctx):
    client, services, lib, worker = ctx
    doc = upload(client, lib, text="# Start\n\n" + "Read the procedure. " * 250 + "\n\n# Finish\n\nFinal signoff.")
    await drain(services, worker)
    endpoint = f"/v1/libraries/{lib}/documents:read"
    outline = client.post(
        endpoint, json={"mode": "outline", "sectionLimit": 1, "documents": [{"documentId": doc}]}
    ).json()
    item = outline["documents"][0]
    assert not outline["complete"] and not outline["documentComplete"] and outline["selectionComplete"]
    assert item["hasMore"] and item["nextAfterSection"] == 1
    assert "text" not in item["outline"][0]
    handle = item["outline"][0]["sectionId"]
    data = selected(client, lib, doc, sectionIds=[handle]).json()
    assert data["selectionComplete"] and not data["complete"]
    assert data["documents"][0]["sections"][0]["text"].count("Read the procedure.") == 250
    assert data["documents"][0]["omittedSections"] > 0
    refused = client.post(
        endpoint, json={"mode": "selected", "maxTokens": 1, "documents": [{"documentId": doc, "sectionIds": [handle]}]}
    ).json()
    assert refused["disposition"] == "budget_exceeded" and not refused["selectionComplete"]
    assert "sections" not in refused["documents"][0]
    assert client.post(endpoint, json={"documents": [{"documentId": doc, "sectionIds": [handle]}]}).status_code == 400
    record = await services.documents.get(lib, doc)
    record["contentRevision"] += 1
    await services.documents.replace(record)
    assert selected(client, lib, doc, sectionIds=[handle]).status_code == 409


@pytest.mark.parametrize("kind", ["docx", "pptx"])
async def test_selected_source_region_offers_unindexed_office_images_and_inspection_without_writes(image_ctx, kind):
    client, services, lib, worker = image_ctx
    worker.vision = FakeChat({"description": "A growth chart"})
    worker.image_max_per_document = 0
    doc = upload_image(client, lib, "source." + kind, office(kind))
    await drain_images(services, worker)
    snapshot = copy.deepcopy(services.chunks.items)
    endpoint = f"/v1/libraries/{lib}/documents:read"
    outline = client.post(
        endpoint, headers={"X-Agent-Id": "agent"}, json={"mode": "outline", "documents": [{"documentId": doc}]}
    ).json()
    ids = [s["sectionId"] for s in outline["documents"][0]["outline"]]
    response = selected(client, lib, doc, sectionIds=ids)
    assert response.status_code == 200, response.text
    figures = response.json()["documents"][0]["candidateImages"]
    assert figures
    inspect = client.post(
        f"/v1/libraries/{lib}/documents:images",
        headers={"X-Agent-Id": "agent"},
        json={"documentId": doc, "imageIds": [figures[0]["imageId"]]},
    )
    assert inspect.status_code == 200, inspect.text
    result = inspect.json()
    assert result["scope"] == "selected_stored_images" and result["selectionComplete"]
    assert result["results"][0]["descriptionSource"] == "not_available"
    assert result["pageSections"] and services.chunks.items == snapshot
    assert not worker.vision.calls
    assert (
        client.post(
            f"/v1/libraries/{lib}/documents:images",
            headers={"X-Agent-Id": "agent"},
            json={"documentId": doc, "imageIds": ["foreign"]},
        ).status_code
        == 409
    )


async def test_search_reference_expands_source_page_and_retained_figure_beyond_cap(image_ctx):
    client, services, lib, _ = image_ctx
    doc = await prepared(image_ctx)
    response = client.post(
        f"/v1/libraries/{lib}/search",
        headers={"X-Agent-Id": "agent"},
        json={"query": "expansion", "document": {"documentId": doc}, "top": 20},
    ).json()
    hit = next(r for r in response["results"] if r["location"].get("page") == 4)
    result = selected(client, lib, doc, evidenceIds=[hit["evidenceId"]])
    assert result.status_code == 200, result.text
    data = result.json()["documents"][0]
    assert all(s["location"]["page"] == 4 for s in data["sections"])
    assert data["candidateImages"] and data["candidateImages"][0]["location"]["page"] == 4
    assert "blobPath" not in result.text and "imageRef" not in result.text


def test_repeated_evidence_signals_recovery_and_ledger_reuses_only_source_references():
    index, ledger = EvidenceIndex(), []
    calls = [
        Reply(None, [ToolCall(str(i), "search_library_evidence", json.dumps({"query": q}))])
        for i, q in enumerate(["context", "more context", "continuity"])
    ]
    chat = scripted(*calls, Reply("Supported answer"))
    turn = run_turn(chat, index, {"id": "a", "libraryIds": ["bound"]}, [], "q", evidence_ledger=ledger)
    replies = [json.loads(m["content"]) for m in turn.messages if m["role"] == "tool"]
    assert replies[-1]["progress"]["recoveryRequired"]
    assert replies[-1]["progress"]["repeatedExtents"] > 0
    assert replies[0]["nativeImageDelivery"]["supplied"] == ["image-1"]
    assert replies[1]["nativeImageDelivery"]["alreadySupplied"] == ["image-1"]
    assert ledger[0]["imageIds"] == ["image-1"] and "Chart description" not in json.dumps(ledger)
    follow = scripted(Reply("Need source revalidation"))
    run_turn(follow, index, {"id": "a", "libraryIds": ["bound"]}, [], "show that again", evidence_ledger=ledger)
    assert "image-1" in follow.seen[0][0][0]["content"]
    public = metrics(turn)
    assert public["tools"][-1]["noProgress"] and "sig=" not in json.dumps(public)
    assert "Chart description" not in json.dumps(public) and "continuity" not in json.dumps(public)


def test_disabled_visual_inputs_explicitly_report_withheld_pixels():
    chat = scripted(Reply(None, [ToolCall("s", "search_library_evidence", '{"query":"chart"}')]), Reply("Description"))
    turn = run_turn(chat, EvidenceIndex(), {"id": "a", "libraryIds": ["bound"]}, [], "q", image_inputs=False)
    payload = json.loads(next(m["content"] for m in turn.messages if m["role"] == "tool"))
    assert payload["nativeImageDelivery"]["withheld"] == ["image-1"]
    assert payload["nativeImageDelivery"]["reason"] == "model_image_inputs_disabled"
    assert turn.searches[0].images_withheld == 1


def test_missing_requested_visuals_trigger_bounded_answer_repair_and_selective_inspection():
    index = EvidenceIndex()

    def search(*args, **kwargs):
        return {
            "library": {"name": "Evidence"},
            "coverage": {"disposition": "results"},
            "results": [index.row, {**index.row, "imageId": "image-2"}],
        }

    def inspect(*args, **kwargs):
        return {
            "library": {"name": "Evidence"},
            "results": [{**index.row, "imageId": i} for i in kwargs["imageIds"]],
            "pageSections": [{"text": "Original source."}],
        }

    index.search, index.read_images = search, inspect
    chat = scripted(
        Reply(None, [ToolCall("s", "search_library_evidence", '{"query":"context diagrams"}')]),
        Reply("I can show you the diagrams if you like."),
        Reply(
            None,
            [ToolCall("i", "inspect_images_library_evidence", '{"documentId":"d","imageIds":["image-1","image-2"]}')],
        ),
        Reply("Two relevant diagrams: ![First](fli-image:image-1) ![Second](fli-image:image-2)"),
    )
    turn = run_turn(chat, index, {"id": "a", "libraryIds": ["bound"]}, [], "Show useful images.")
    assert turn.model_calls[1]["purpose"] == "answer_repair"
    payload = json.loads(next(m["content"] for m in turn.messages if m.get("tool_call_id") == "i"))
    assert payload["nativeImageDelivery"]["alreadySupplied"] == ["image-1"]
    assert payload["nativeImageDelivery"]["supplied"] == ["image-2"]
    assert len(turn.searches[-1].images) == 2
    assert metrics(turn)["modelCalls"][1]["purpose"] == "answer_repair"


async def test_selected_read_isolation_revision_race_and_legacy_precision(ctx, monkeypatch):
    client, services, lib, worker = ctx
    doc = upload(client, lib)
    await drain(services, worker)
    other = client.post("/v1/libraries", json={"kind": "personal", "name": "Private", "boundAgentId": "owner"}).json()[
        "id"
    ]
    for mode in ("outline", "selected"):
        selector = {"documentId": doc, **({"pages": [1]} if mode == "selected" else {})}
        endpoint = f"/v1/libraries/{other}/documents:read"
        assert client.post(endpoint, json={"mode": mode, "documents": [selector]}).status_code == 403
        assert (
            client.post(
                endpoint, headers={"X-Agent-Id": "owner"}, json={"mode": mode, "documents": [selector]}
            ).status_code
            == 404
        )
    stored = await services.documents.get(lib, doc)
    stored.pop("fullText")
    await services.documents.replace(stored)
    endpoint = f"/v1/libraries/{lib}/documents:read"
    outline = client.post(endpoint, json={"mode": "outline", "documents": [{"documentId": doc}]}).json()
    handle = outline["documents"][0]["outline"][0]["sectionId"]
    result = selected(client, lib, doc, sectionIds=[handle]).json()
    assert result["selectionComplete"] and result["documents"][0]["representation"] == "indexed_chunks"
    assert any("overlap" in n for n in result["notes"])
    original = services.reader.document

    async def changing(*args):
        data = await original(*args)
        current = await services.documents.get(lib, doc)
        current["contentRevision"] += 1
        await services.documents.replace(current)
        return data

    monkeypatch.setattr(services.reader, "document", changing)
    response = selected(client, lib, doc, sectionIds=[handle])
    assert response.status_code == 409 and "sections" not in response.text
