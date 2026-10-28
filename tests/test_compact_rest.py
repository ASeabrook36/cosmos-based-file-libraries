"""REST contract: complete evidence, independent attachments, unchanged access fences."""

import copy
import json

import pytest

from examples.compact_rest import display_selected, native_image_parts, resolve_selected, search
from fli.chunking import default_tokenizer
from fli.presentation import evidence_json, project
from fli_demo.agent import Reply, ToolCall, run_turn
from fli_demo.client import IndexClient
from tests.fakes import FakeChat
from tests.test_demo import scripted
from tests.test_images import office
from tests.test_page_images import prepared
from tests.test_reading import ctx as reading_ctx
from tests.test_reading import drain, upload
from tests.test_worker_handlers import ctx as worker_ctx
from tests.test_worker_handlers import drain as drain_images
from tests.test_worker_handlers import upload as upload_image

ctx = reading_ctx
image_ctx = worker_ctx


def post(client, lib, operation, body, **params):
    return client.post(f"/v1/libraries/{lib}/{operation}", params={"view": "compact", **params}, json=body)


def contract(response):
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert set(envelope) == {"contract", "evidence", "attachments", "metadata"}
    assert envelope["contract"] == "fli.compact.v1"
    evidence = envelope["evidence"]
    budget = envelope["metadata"]["budget"]
    assert budget["returnedTokens"] == len(default_tokenizer().encode(evidence_json(evidence)))
    assert not budget["nativeImageTokensIncluded"]
    for forbidden in ('"url"', '"expiresAt"', '"usage"', '"telemetryId"', '"regions"', '"blobPath"', '"imageRef"'):
        assert forbidden not in evidence_json(evidence)
    return envelope


async def test_raw_defaults_and_compact_preserve_passages_neighbors_scope_and_citations(ctx):
    client, services, lib, worker = ctx
    doc = upload(client, lib, text="# Étude\n\n" + "  Observe pressure; retain exact source words.\n" * 180)
    await drain(services, worker)
    body = {"query": "pressure", "includeNeighbors": True, "document": {"documentId": doc}}
    endpoint = f"/v1/libraries/{lib}/search"
    raw = client.post(endpoint, json=body).json()
    explicit = client.post(endpoint, params={"view": "raw"}, json=body).json()
    assert set(raw) == set(explicit) and "contract" not in raw
    assert raw["results"] == explicit["results"]
    result = contract(post(client, lib, "search", body))
    evidence = result["evidence"]
    assert evidence["documentScope"] == body["document"]
    assert evidence["coverage"] == raw["coverage"]
    assert result["metadata"]["usage"]["requestCharge"] == raw["usage"]["requestCharge"]
    for original, projected in zip(raw["results"], evidence["results"], strict=True):
        for field in ("text", "evidenceId", "documentId", "generation", "contentRevision", "sourceScope"):
            assert projected[field] == original[field]
        assert projected["citation"].startswith(original["fileName"])
        assert projected["textSource"] == "extracted_source_text"
        assert [n["text"] for n in projected["neighbors"]] == [n["text"] for n in original["neighbors"]]
    assert any(len(r["text"]) > 1500 for r in evidence["results"])
    assert [r["chunkId"] for r in result["metadata"]["references"]] == [r["chunkId"] for r in raw["results"]]
    assert "chunkId" not in evidence_json(evidence)


async def test_catalogue_and_semantic_discovery_keep_enumeration_and_size_hints(ctx):
    client, services, lib, worker = ctx
    first = upload(client, lib)
    await drain(services, worker)
    upload(client, lib, name="unreadable.md")  # still queued
    found = []
    selector = {"limit": 1}
    while True:
        evidence = contract(post(client, lib, "documents:discover", selector))["evidence"]
        found.extend(evidence["documents"])
        assert evidence["coverage"]["notes"]
        if not evidence["hasMore"]:
            assert evidence["nextAfter"] is None
            break
        selector["after"] = evidence["nextAfter"]
    assert len(found) == 2 and any(not d["readable"] for d in found)
    assert next(d for d in found if d["documentId"] == first)["documentTokens"] > 0
    semantic = contract(post(client, lib, "documents:discover", {"mode": "semantic_candidates", "query": "safety"}))
    evidence = semantic["evidence"]
    assert evidence["mode"] == "semantic_candidates" and evidence["candidateWindow"] == 40
    assert not evidence["exhaustive"] and evidence["selectionStatus"] == "candidates_found"
    assert evidence["coverage"]["notes"] and not semantic["attachments"]


@pytest.mark.parametrize("mode", ["full", "selected", "outline"])
async def test_read_modes_budget_the_exact_evidence_and_refuse_the_whole_batch(ctx, mode):
    client, services, lib, worker = ctx
    ids = [upload(client, lib, name=f"source-{i}.md") for i in range(2)]
    await drain(services, worker)
    selections = [{"documentId": d, **({"paragraphs": [1]} if mode == "selected" else {})} for d in ids]
    # Markdown lacks paragraph selectors, so select source handles instead.
    if mode == "selected":
        for selection in selections:
            outline = contract(
                post(
                    client,
                    lib,
                    "documents:read",
                    {"mode": "outline", "documents": [{"documentId": selection["documentId"]}]},
                )
            )["evidence"]
            selection.pop("paragraphs")
            selection["sectionIds"] = [outline["documents"][0]["outline"][0]["sectionId"]]
    body = {"mode": mode, "documents": selections, "maxTokens": 200_000}
    complete = contract(post(client, lib, "documents:read", body))
    required = complete["metadata"]["budget"]["requiredTokens"]
    exact = contract(post(client, lib, "documents:read", {**body, "maxTokens": required}))
    assert exact["evidence"] == complete["evidence"] and exact["metadata"]["budget"]["fits"]
    refused = contract(post(client, lib, "documents:read", {**body, "maxTokens": required - 1}))
    assert refused["metadata"]["budget"]["requiredTokens"] == required
    assert refused["evidence"]["disposition"] == "budget_exceeded" and not refused["attachments"]
    assert not refused["evidence"]["selectionComplete"]
    assert all(
        not any(k in d for k in ("sections", "outline", "candidateImages")) for d in refused["evidence"]["documents"]
    )
    assert all(not d["complete"] for d in refused["evidence"]["documents"])
    assert len(refused["evidence"]["documents"]) == 2
    if mode == "outline":
        assert complete["evidence"]["documents"][0]["outline"][0]["sectionTokens"] > 0
    else:
        raw = client.post(f"/v1/libraries/{lib}/documents:read", json=body).json()
        for a, b in zip(raw["documents"], complete["evidence"]["documents"], strict=True):
            assert [s["text"] for s in a["sections"]] == [s["text"] for s in b["sections"]]
        assert complete["evidence"]["documentComplete"] == (mode == "full")
        if mode == "selected":
            assert complete["evidence"]["documents"][0]["scope"]["sectionIds"] == selections[0]["sectionIds"]
    inspect = contract(post(client, lib, "documents:read", {**body, "includeText": False}))
    assert inspect["evidence"]["disposition"] == "inspected" and not inspect["attachments"]
    assert len(inspect["evidence"]["documents"]) == 2


async def test_image_inspection_and_independent_consumer_resolve_display_and_native_inputs(image_ctx):
    client, services, lib, worker = image_ctx
    client.headers["X-Agent-Id"] = "agent"
    doc = await prepared(image_ctx)
    before = copy.deepcopy(services.chunks.items)
    body = {"documentId": doc, "pages": [4]}
    envelope = contract(post(client, lib, "documents:images", body))
    raw = client.post(f"/v1/libraries/{lib}/documents:images", json=body).json()
    evidence, attachments = envelope["evidence"], envelope["attachments"]
    assert [s["text"] for s in evidence["pageSections"]] == [s["text"] for s in raw["pageSections"]]
    assert evidence["pages"] == [4] and evidence["scope"] == "stored_pdf_page_images"
    assert evidence["complete"] and not evidence["documentComplete"]
    assert {k: v for k, v in attachments[0]["image"].items() if k != "expiresAt"} == {
        k: v for k, v in raw["results"][0]["image"].items() if k != "expiresAt"
    }
    image_id = evidence["results"][0]["imageId"]
    assert attachments[0]["imageId"] == image_id and attachments[0]["contentRevision"] == evidence["contentRevision"]
    assert evidence["results"][0]["image"]["caption"] == attachments[0]["image"]["caption"]
    assert evidence["results"][0]["descriptionSource"] == "not_available"
    assert evidence["results"][0]["textSource"] == "unavailable_image_description"
    text, resolved, metadata = resolve_selected(client, lib, "agent", evidence, [image_id])
    assert image_id in text and metadata["budget"]["fits"]
    displayed = []
    display_selected(resolved, [image_id], lambda *args: displayed.append(args))
    assert len(displayed) == 1 and "Figure 1" in displayed[0][1]
    assert displayed[0][2]["location"] == {"page": 4}
    # Display alone adds no native model content; the optional explicit call does.
    native = native_image_parts(resolved, [image_id])
    assert native[0]["image_url"]["url"] == resolved[0]["image"]["url"]
    assert services.chunks.items == before and not worker.vision.calls
    assert not client.app.state.layout.calls
    refused = contract(post(client, lib, "documents:images", body, maxEvidenceTokens=1))
    assert not refused["attachments"] and "pageSections" not in refused["evidence"]
    assert "results" not in refused["evidence"] and refused["evidence"]["disposition"] == "budget_exceeded"
    foreign = post(client, lib, "documents:images", {"documentId": doc, "imageIds": ["foreign"]})
    assert foreign.status_code == 409 and "evidence" not in foreign.json()


async def test_compact_full_and_selected_office_images_keep_captions_without_raw_budget_charge(image_ctx):
    client, services, lib, worker = image_ctx
    client.headers["X-Agent-Id"] = "agent"
    worker.vision = FakeChat({"description": "A growth chart."})
    doc = upload_image(client, lib, "growth.docx", office("docx"))
    await drain_images(services, worker)
    body = {"documents": [{"documentId": doc}], "maxTokens": 200_000}
    raw = client.post(f"/v1/libraries/{lib}/documents:read", json=body).json()
    compact = contract(post(client, lib, "documents:read", body))
    assert compact["attachments"]
    assert [s["text"] for s in compact["evidence"]["documents"][0]["sections"]] == [
        s["text"] for s in raw["documents"][0]["sections"]
    ]
    required = compact["metadata"]["budget"]["requiredTokens"]
    assert required < raw["estimatedTokens"]
    assert contract(post(client, lib, "documents:read", {**body, "maxTokens": required}))["evidence"]["complete"]
    assert (
        client.post(f"/v1/libraries/{lib}/documents:read", json={**body, "maxTokens": required}).json()["disposition"]
        == "budget_exceeded"
    )
    sections = compact["evidence"]["documents"][0]["sections"]
    chosen = next(s for s in sections if s.get("imageId"))
    selected = contract(
        post(
            client,
            lib,
            "documents:read",
            {"mode": "selected", "documents": [{"documentId": doc, "sectionIds": [chosen["sectionId"]]}]},
        )
    )
    assert selected["evidence"]["documents"][0]["candidateImages"]
    assert selected["attachments"][0]["generation"] == 1


OPERATIONS = [
    ("search", {"query": "safety"}),
    ("documents:discover", {}),
    ("documents:discover", {"mode": "semantic_candidates", "query": "safety"}),
    ("documents:read", {"documents": [{"documentId": "foreign"}]}),
    ("documents:images", {"documentId": "foreign", "pages": [1]}),
]


@pytest.mark.parametrize("operation,body", OPERATIONS)
async def test_all_compact_operations_keep_authentication_and_personal_binding(ctx, operation, body):
    client, services, _, _ = ctx
    personal = client.post(
        "/v1/libraries", json={"kind": "personal", "name": "Private", "boundAgentId": "owner"}
    ).json()["id"]
    response = post(client, personal, operation, body)
    assert response.status_code == 403 and response.json()["error"]["code"] == "library_not_bound_to_agent"
    client.headers.pop("X-Agent-Id")
    assert post(client, personal, operation, body).status_code == 400
    client.headers["X-Agent-Id"] = "owner"
    client.headers.pop("Authorization")
    assert post(client, personal, operation, body).status_code == 401


async def test_stale_foreign_references_replacement_and_link_races_never_return_evidence(image_ctx, monkeypatch):
    client, services, lib, _ = image_ctx
    client.headers["X-Agent-Id"] = "agent"
    doc = await prepared(image_ctx)
    hit = contract(post(client, lib, "search", {"query": "expansion", "document": {"documentId": doc}}))["evidence"][
        "results"
    ][0]
    selector = {"mode": "selected", "documents": [{"documentId": doc, "evidenceIds": [hit["evidenceId"]]}]}
    current = await services.documents.get(lib, doc)
    current["contentRevision"] += 1
    await services.documents.replace(current)
    assert post(client, lib, "documents:read", selector).json()["error"]["code"] == "evidence_changed"
    assert (
        post(
            client, lib, "search", {"query": "expansion", "document": {"documentId": doc, "generation": 99}}
        ).status_code
        == 409
    )
    other = client.post("/v1/libraries", json={"kind": "shared", "name": "Other"}).json()["id"]
    assert post(client, other, "documents:images", {"documentId": doc, "pages": [4]}).status_code == 404
    original = services.blobs.read_link

    async def change(path):
        link = await original(path)
        record = await services.documents.get(lib, doc)
        record["status"] = "deleting"
        await services.documents.replace(record)
        return link

    monkeypatch.setattr(services.blobs, "read_link", change)
    response = post(client, lib, "documents:images", {"documentId": doc, "pages": [4]})
    assert response.status_code == 409 and "attachments" not in response.json()


async def test_demo_uses_supported_evidence_and_preserves_attachments_for_text_only_display(image_ctx):
    client, services, lib, worker = image_ctx
    worker.vision = FakeChat({"description": "A chart."})
    doc = upload_image(client, lib, "growth.docx", office("docx"))
    await drain_images(services, worker)
    index = IndexClient("http://testserver", "k", http=client)
    envelope = index.read_documents(lib, "agent", documents=[{"documentId": doc}])
    name = next(
        t["function"]["name"] for t in index.tools(lib, "agent") if t["function"]["name"].startswith("read_documents")
    )
    chat = scripted(
        Reply(None, [ToolCall("r", name, json.dumps({"documents": [{"documentId": doc}]}))]), Reply("Read it.")
    )
    turn = run_turn(chat, index, {"id": "agent", "libraryIds": [lib]}, [], "Read it", image_inputs=False)
    payload = json.loads(next(m["content"] for m in turn.messages if m["role"] == "tool"))
    payload.pop("nativeImageDelivery")
    payload.pop("progress")
    assert payload == envelope["evidence"]
    assert turn.searches[0].images
    assert not any(isinstance(m.get("content"), list) for m in turn.messages)
    text, attachments, metadata = search(client, lib, "agent", "chart")
    assert "sig=" not in text and metadata["budget"]["fits"]


@pytest.mark.parametrize("params", [{"view": "unknown"}, {"maxEvidenceTokens": 0}, {"maxEvidenceTokens": 200001}])
def test_view_and_budget_validation(ctx, params):
    client, _, lib, _ = ctx
    assert post(client, lib, "search", {"query": "safety"}, **params).status_code == 400


@pytest.mark.parametrize("mode", ["full", "selected", "search", "images"])
async def test_compact_lifecycle_fences_after_attachment_issuance(image_ctx, monkeypatch, mode):
    client, services, lib, worker = image_ctx
    client.headers["X-Agent-Id"] = "agent"
    worker.vision = FakeChat({"description": "A chart."})
    doc = upload_image(client, lib, "growth.docx", office("docx"))
    await drain_images(services, worker)
    full = contract(post(client, lib, "documents:read", {"documents": [{"documentId": doc}]}))
    if mode == "search":
        operation, body = "search", {"query": "growth", "document": {"documentId": doc}, "top": 20}
    elif mode == "images":
        operation, body = "documents:images", {"documentId": doc, "imageIds": [full["attachments"][0]["imageId"]]}
    else:
        selected = {"documentId": doc}
        if mode == "selected":
            selected["sectionIds"] = [s["sectionId"] for s in full["evidence"]["documents"][0]["sections"]]
        operation, body = "documents:read", {"mode": mode, "documents": [selected]}
    original = services.blobs.read_link

    async def change(path):
        link = await original(path)
        current = await services.documents.get(lib, doc)
        current["contentRevision"] += 1
        await services.documents.replace(current)
        return link

    monkeypatch.setattr(services.blobs, "read_link", change)
    response = post(client, lib, operation, body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "document_changed"
    assert "attachments" not in response.json() and "evidence" not in response.json()


def test_openapi_exposes_opt_in_contract_without_changing_raw_schema(ctx):
    client, _, _, _ = ctx
    schema = client.get("/openapi.json").json()
    for operation in ("search", "documents:discover", "documents:read", "documents:images"):
        endpoint = schema["paths"]["/v1/libraries/{library_id}/" + operation]["post"]
        params = {p["name"]: p for p in endpoint["parameters"]}
        assert params["view"]["schema"]["default"] == "raw"
        assert "maxEvidenceTokens" in params
        alternatives = endpoint["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]
        assert {"$ref": "#/components/schemas/CompactResponse"} in alternatives


async def test_legacy_text_keeps_overlap_and_foreign_handles_are_not_authority(ctx):
    client, services, lib, worker = ctx
    ids = [upload(client, lib, name=f"source-{i}.md", text="Repeated legacy source. " * 300) for i in range(2)]
    await drain(services, worker)
    outline = contract(post(client, lib, "documents:read", {"mode": "outline", "documents": [{"documentId": ids[0]}]}))
    handle = outline["evidence"]["documents"][0]["outline"][0]["sectionId"]
    wrong = post(
        client,
        lib,
        "documents:read",
        {"mode": "selected", "documents": [{"documentId": ids[1], "sectionIds": [handle]}]},
    )
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "evidence_changed"
    record = await services.documents.get(lib, ids[0])
    record.pop("fullText")
    await services.documents.replace(record)
    body = {"documents": [{"documentId": ids[0]}]}
    raw = client.post(f"/v1/libraries/{lib}/documents:read", json=body).json()
    compact = contract(post(client, lib, "documents:read", body))["evidence"]
    assert compact["documents"][0]["representation"] == "indexed_chunks"
    assert [s["text"] for s in compact["documents"][0]["sections"]] == [
        s["text"] for s in raw["documents"][0]["sections"]
    ]
    assert any("overlapping" in n for n in compact["notes"])


@pytest.mark.parametrize("operation,body", OPERATIONS)
async def test_deleting_library_refuses_every_compact_operation(ctx, operation, body):
    client, services, lib, _ = ctx
    library = await services.libraries.get(lib)
    library["status"] = "deleting"
    await services.libraries.replace(library)
    response = post(client, lib, operation, body)
    assert response.status_code == 404 and response.json()["error"]["code"] == "library_not_found"
    assert "evidence" not in response.json() and "attachments" not in response.json()


async def test_compact_integrity_errors_are_not_hidden_by_budget_refusal(ctx):
    client, services, lib, worker = ctx
    doc = upload(client, lib)
    await drain(services, worker)
    record = await services.documents.get(lib, doc)
    record["fullText"]["sha256"] = "invalid"
    await services.documents.replace(record)
    response = post(client, lib, "documents:read", {"documents": [{"documentId": doc}], "maxTokens": 1})
    assert response.status_code == 409 and response.json()["error"]["code"] == "document_text_incomplete"


def test_neighbor_citations_do_not_invent_the_parent_hits_page():
    evidence, _ = project(
        {
            "results": [
                {
                    "fileName": "report.pdf",
                    "location": {"page": 4},
                    "text": "Main passage.",
                    "neighbors": [{"chunkIndex": 12, "text": "Adjacent text may start on another page."}],
                }
            ]
        }
    )
    row = evidence["results"][0]
    assert row["citation"] == "report.pdf (page 4)"
    assert row["neighbors"][0]["citation"] == "report.pdf"
    assert "do_not_inherit_hit_location" in row["neighbors"][0]["locationStatus"]
