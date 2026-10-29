"""Normal API -> provider -> publication -> retrieval/lifecycle vertical slices."""

import base64
import copy
import hashlib
import json

import httpx
import pytest

from fli.checkpoints import encoded, load
from fli.chunking import ApproxTokenizer, Chunker
from fli.ingestion import job, profile, validate_microsoft
from fli.microsoft import MicrosoftWorkflow
from fli.worker.handlers import Worker
from tests.fakes import FakeEmbedder, api
from tests.test_worker_handlers import drain

SOURCE = b"# Report\nIntroduction.\n\n## Findings\nRainfall reduced salinity.\n\n### Limits\nOnly two sites.\n"


class MicrosoftFake:
    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    async def prepare(self, work, source):
        self.calls += 1
        if self.callback:
            await self.callback(work)
        result = output(work, source)
        if work["profile"].get("extractionOnly"):
            result.update(schema="fli.indexer-extraction.v1", rows=[], embedding=None)
        return result


def output(work, source=SOURCE):
    return {
        "schema": "fli.indexer-result.v1",
        "job": copy.deepcopy(work),
        "embedding": copy.deepcopy(work["profile"]["embedding"]),
        "sourceBase64": base64.b64encode(source).decode(),
        "analysis": {
            "analyzeResult": {
                "modelId": "prebuilt-layout",
                "content": provider_text(source),
                "figures": [{"id": "1"}],
                "pages": [{}],
            }
        },
        "rows": [
            {
                "chunkIndex": i,
                "totalChunks": 2,
                "text": text,
                "embedding": [1.0, 2.0, 3.0],
                "libraryId": "untrusted-and-discarded",
                "location": {"page": 999},
            }
            for i, text in enumerate(["Rainfall reduced salinity.", "Only two sites."])
        ],
        "usage": {},
    }


def setup(profile_id="current-v1"):
    client, services = api(AOAI_EMBEDDING_MODEL_REVISION="test-revision")
    lib = client.post(
        "/v1/libraries", json={"kind": "shared", "name": "Reports", "ingestionProfile": profile_id}
    ).json()["id"]
    worker = Worker(
        services.libraries,
        services.documents,
        services.chunks,
        services.blobs,
        services.queue,
        FakeEmbedder(),
        Chunker(ApproxTokenizer()),
        microsoft=MicrosoftFake(),
        stale_read_pause=0,
    )
    return client, services, lib, worker


def upload(client, lib, source=SOURCE, name="report.md", metadata=None):
    response = client.post(
        f"/v1/libraries/{lib}/documents",
        files={"file": (name, source)},
        data={"metadata": json.dumps(metadata)} if metadata else {},
    )
    assert response.status_code == 202, response.text
    return response.json()["documentId"]


async def test_structured_upload_search_outline_selected_read_and_complete_source():
    client, s, lib, worker = setup("custom-structured-v1")
    doc_id = upload(client, lib)
    await drain(s, worker)
    document = await s.documents.get(lib, doc_id)
    assert document["status"] == "ready"
    assert document["ingestionJob"]["profile"]["enrichment"]["adapter"] == "source-structure-v1"
    assert document["processingCoverage"]["generatedSummaries"] == "unavailable"
    structure = await load(s.blobs, document["structure"]["blobPath"])
    assert structure["nodes"][-1]["parentId"] == structure["nodes"][-2]["id"]
    sections = json.loads(await s.blobs.download(document["fullText"]["blobPath"]))
    assert "".join(section["text"] for section in sections).encode() == SOURCE
    body = {"mode": "outline", "documents": [{"documentId": doc_id}], "maxTokens": 10000}
    result = client.post(f"/v1/libraries/{lib}/documents:read", json=body, headers={"X-Agent-Id": "a"})
    assert result.status_code == 200, result.text
    outline = result.json()["documents"][0]["outline"]
    assert outline[-1]["location"]["parentNodeId"] == "n2"
    body = {"mode": "selected", "documents": [{"documentId": doc_id, "sectionIds": [outline[-1]["sectionId"]]}]}
    selected = client.post(f"/v1/libraries/{lib}/documents:read", json=body, headers={"X-Agent-Id": "a"}).json()
    assert selected["documents"][0]["sections"][0]["text"].endswith("Only two sites.\n")
    subtree = client.post(
        f"/v1/libraries/{lib}/documents:read",
        json={
            "mode": "selected",
            "documents": [{"documentId": doc_id, "generation": 1, "contentRevision": 2, "nodeIds": ["n2"]}],
            "maxTokens": 10000,
        },
        headers={"X-Agent-Id": "a"},
    )
    assert subtree.status_code == 200, subtree.text
    assert len(subtree.json()["documents"][0]["sections"]) == 2
    refused = client.post(
        f"/v1/libraries/{lib}/documents:read",
        json={
            "mode": "selected",
            "documents": [{"documentId": doc_id, "generation": 1, "contentRevision": 2, "nodeIds": ["n2"]}],
            "maxTokens": 1,
        },
        headers={"X-Agent-Id": "a"},
    )
    assert refused.status_code == 200 and refused.json()["disposition"] == "budget_exceeded"
    assert not refused.json()["documents"][0].get("sections")
    search = client.post(f"/v1/libraries/{lib}/search", json={"query": "salinity"}, headers={"X-Agent-Id": "a"})
    assert search.status_code == 200 and search.json()["results"]
    before = len(s.chunks.items)
    await worker.ingest(lib, doc_id, 1)
    assert len(s.chunks.items) == before


async def test_structure_unsupported_format_is_visible_failure_not_silent_fallback():
    client, s, lib, worker = setup("custom-structured-v1")
    doc_id = upload(client, lib, name="report.txt")
    await drain(s, worker)
    document = await s.documents.get(lib, doc_id)
    assert document["statusDetail"] == "structured_format_unsupported"
    assert not s.chunks.items


async def test_microsoft_vectors_not_reembedded_metadata_and_complete_provider_text_preserved():
    client, s, lib, worker = setup("microsoft-indexer-layout-v1")
    doc_id = upload(client, lib, metadata={"author": "Owner", "tags": [{"key": "region", "value": "north"}]})
    await drain(s, worker)
    document = await s.documents.get(lib, doc_id)
    assert document["status"] == "ready" and worker.embedder.calls == 0
    assert document["author"] == "Owner" and document["facets"] == ["region=north"]
    assert document["metadataVersion"] == 0
    assert document["processingMetrics"]["embeddingTokens"] is None
    assert document["processingMetrics"]["embeddingRequests"] is None
    assert document["processingCoverage"]["visuals"] == "unavailable"
    sections = json.loads(await s.blobs.download(document["fullText"]["blobPath"]))
    assert sections[0]["text"].encode() == SOURCE
    rows = list(s.chunks.items.values())
    assert all(r["libraryId"] == lib and r["location"] == {} and r["facets"] == ["region=north"] for r in rows)
    assert all(r["embedding"] == [1.0, 2.0, 3.0] for r in rows)
    metrics = client.get(f"/v1/libraries/{lib}/metrics", headers={"X-Agent-Id": "a"}).json()
    assert metrics["documents"][0]["attemptEvents"][-1]["status"] in {"started", "published"}


async def test_provider_retry_resumes_frozen_vectors_and_retains_concurrent_metadata(monkeypatch):
    client, s, lib, worker = setup("microsoft-indexer-layout-v1")
    doc_id = upload(client, lib)
    _, message = s.queue.sent.pop(0)
    real = s.chunks.upsert
    calls = 0

    async def fail_once(row):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return await real(row)

    monkeypatch.setattr(s.chunks, "upsert", fail_once)
    with pytest.raises(RuntimeError):
        await worker.handle(message)
    client.patch(f"/v1/libraries/{lib}/documents/{doc_id}", json={"author": "Edited"})
    monkeypatch.setattr(s.chunks, "upsert", real)
    await worker.handle(message)
    assert worker.microsoft.calls == 1 and worker.embedder.calls == 0
    doc = await s.documents.get(lib, doc_id)
    assert doc["author"] == "Edited" and doc["metadataVersion"] == 1
    # Existing create-only rows can precede the metadata change. Session-ordered retag repairs those too.
    await drain(s, worker)
    assert all(r["author"] == "Edited" for r in s.chunks.items.values())
    paths = await s.blobs.list_prefix(f"{lib}/{doc_id}/attempts/")
    events = [await load(s.blobs, p) for p in paths]
    assert {e["status"] for e in events} >= {"error", "published", "started"}


@pytest.mark.parametrize("action", ["delete", "replace"])
async def test_late_microsoft_completion_cannot_publish_deleted_or_superseded_generation(action):
    client, s, lib, worker = setup("microsoft-indexer-layout-v1")
    doc_id = upload(client, lib)
    _, message = s.queue.sent.pop(0)

    async def intervene(work):
        if action == "delete":
            client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
        else:
            client.put(f"/v1/libraries/{lib}/documents/{doc_id}/content", files={"file": ("next.md", SOURCE + b"New.")})

    worker.microsoft = MicrosoftFake(intervene)
    await worker.handle(message)
    doc = await s.documents.get(lib, doc_id)
    assert doc["activeGeneration"] is None and not s.chunks.items
    worker.microsoft = MicrosoftFake()
    await drain(s, worker)
    if action == "delete":
        assert await s.documents.get(lib, doc_id) is None
        assert not await s.blobs.list_prefix(f"{lib}/{doc_id}/")
    else:
        assert (await s.documents.get(lib, doc_id))["activeGeneration"] == 2
        assert {r["generation"] for r in s.chunks.items.values()} == {2}


async def test_profile_snapshot_is_per_generation_and_legacy_defaults_to_current():
    client, s, lib, worker = setup()
    doc_id = upload(client, lib, name="report.txt")
    library = await s.libraries.get(lib)
    library.pop("ingestionProfile")
    await s.libraries.replace(library)
    await drain(s, worker)
    doc = await s.documents.get(lib, doc_id)
    assert doc["ingestionJob"]["profile"]["id"] == "current-v1"
    assert client.patch(f"/v1/libraries/{lib}", json={"ingestionProfile": "custom-structured-v1"}).status_code == 400
    client.put(f"/v1/libraries/{lib}/documents/{doc_id}/content", files={"file": ("next.txt", SOURCE)})
    pending = await s.documents.get(lib, doc_id)
    assert pending["ingestionJob"]["generation"] == 1
    assert pending["pendingFile"]["ingestionJob"]["generation"] == 2


def work():
    recipe = profile("microsoft-indexer-layout-v1")
    recipe["embedding"]["modelRevision"] = "test-revision"
    return job("lib", "doc", 1, {"blobPath": "source", "sha256": hashlib.sha256(SOURCE).hexdigest()}, recipe, 0)


@pytest.mark.parametrize(
    "damage", ["scope", "source", "count", "ordinal", "nan", "dimensions", "model", "negative-usage"]
)
def test_invalid_provider_result_rejected_before_publication(damage):
    request = work()
    result = output(request)
    if damage == "scope":
        result["job"]["libraryId"] = "elsewhere"
    elif damage == "source":
        result["sourceBase64"] = base64.b64encode(b"other").decode()
    elif damage == "count":
        result["rows"].pop()
    elif damage == "ordinal":
        result["rows"][1]["chunkIndex"] = 0
    elif damage == "nan":
        result["rows"][0]["embedding"][0] = float("nan")
    elif damage == "dimensions":
        result["rows"][0]["embedding"].append(1.0)
    elif damage == "model":
        result["embedding"]["modelRevision"] = "different"
    elif damage == "negative-usage":
        result["usage"]["embeddingTokens"] = -1
    with pytest.raises(ValueError):
        validate_microsoft(result, request, SOURCE, 3)


async def test_workflow_bridge_uses_trusted_job_and_cached_result_no_duplicate_dispatch():
    from fli.blobs import MemoryBlobs

    blobs = MemoryBlobs()
    request = work()
    calls = []

    async def token():
        return "token"

    async def send(req):
        calls.append(req)
        body = json.loads(req.content)
        await blobs.create(body["resultPath"], encoded(output(request)), "application/json")
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as http:
        provider = MicrosoftWorkflow("https://workflow.example.invalid/invoke", token, http, blobs, wait_seconds=0)
        first = await provider.prepare(request, SOURCE)
        second = await provider.prepare(request, SOURCE)
    assert first.pop("resultReused") is False and second.pop("resultReused") is True
    assert first == second and len(calls) == 1
    assert calls[0].headers["Authorization"] == "Bearer token"
    assert json.loads(calls[0].content)["job"] == request


@pytest.mark.parametrize("endpoint", ["http://workflow.example.invalid", "https://host/invoke?sig=secret"])
def test_bridge_refuses_insecure_or_sas_endpoints(endpoint):
    with pytest.raises(ValueError):
        MicrosoftWorkflow(endpoint, None, None, None)


async def test_reconciliation_removes_artifacts_written_after_a_completed_deletion():
    client, s, lib, worker = setup("microsoft-indexer-layout-v1")
    doc_id = upload(client, lib)
    await drain(s, worker)
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
    await drain(s, worker)
    late_path = f"{lib}/{doc_id}/text/1/microsoft-result.json"
    await s.blobs.create(late_path, b"{}", "application/json")
    await worker.handle({"type": "reconcileLibrary", "libraryId": lib})
    await drain(s, worker)
    assert not await s.blobs.list_prefix(f"{lib}/{doc_id}/")
    assert await s.documents.get(lib, doc_id) is None


async def test_reconciliation_can_resume_a_pending_microsoft_job_without_reembedding():
    client, s, lib, worker = setup("microsoft-indexer-layout-v1")
    doc_id = upload(client, lib)
    s.queue.sent.clear()
    doc = await s.documents.get(lib, doc_id)
    path = f"{lib}/{doc_id}/text/1/microsoft-result.json"
    await s.blobs.create(path, encoded(output(doc["ingestionJob"])), "application/json")
    await worker.reconcile_library(lib)
    assert any(m["type"] == "ingest" for _, m in s.queue.sent)
    await drain(s, worker)
    assert (await s.documents.get(lib, doc_id))["status"] == "ready"
    assert worker.embedder.calls == 0


async def test_unconfigured_microsoft_provider_fails_explicitly():
    client, s, lib, worker = setup("microsoft-indexer-layout-v1")
    worker.microsoft = None
    doc_id = upload(client, lib)
    await drain(s, worker)
    assert (await s.documents.get(lib, doc_id))["statusDetail"] == "microsoft_provider_unconfigured"


def provider_text(source):
    if source.startswith(b"PK"):
        from fli.structural_enrichment import docx_structure

        sections, _ = docx_structure(source, "fixture", "report.docx")
        return "".join(section.text for section in sections)
    return source.decode()


def structured_docx():
    import io

    from docx import Document

    document = Document()
    document.core_properties.author = ""
    document.add_heading("Report", level=1)
    document.add_paragraph("Introduction.")
    document.add_heading("Findings", level=2)
    document.add_paragraph("Rainfall reduced salinity.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Site", "Result"
    table.cell(1, 0).text, table.cell(1, 1).text = "North", "Lower salinity"
    document.add_heading("Limits", level=3)
    document.add_paragraph("Only two sites.")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


async def test_docx_structure_preserves_heading_ancestry_body_tables_and_bounded_source_reads():
    client, s, lib, worker = setup("custom-structured-v1")
    doc_id = upload(client, lib, structured_docx(), "report.docx")
    await drain(s, worker)
    doc = await s.documents.get(lib, doc_id)
    assert doc["status"] == "ready"
    graph = await load(s.blobs, doc["structure"]["blobPath"])
    assert graph["adapter"] == "docx-heading-body-v1"
    assert graph["nodes"][-1]["parentId"] == "n2"
    sections = json.loads(await s.blobs.download(doc["fullText"]["blobPath"]))
    text = "".join(section["text"] for section in sections)
    assert "Site | Result\nNorth | Lower salinity\n" in text
    assert text.endswith("Limits\nOnly two sites.\n")
    body = {
        "mode": "selected",
        "documents": [{"documentId": doc_id, "generation": 1, "contentRevision": 2, "nodeIds": ["n2"]}],
        "maxTokens": 10000,
    }
    response = client.post(f"/v1/libraries/{lib}/documents:read", json=body, headers={"X-Agent-Id": "a"})
    assert response.status_code == 200, response.text
    chosen = "".join(section["text"] for section in response.json()["documents"][0]["sections"])
    assert chosen.startswith("Findings\n") and "Lower salinity" in chosen and "Only two sites" in chosen
    assert "Introduction" not in chosen


def test_bridge_allows_only_a_noncredential_api_version_query():
    MicrosoftWorkflow("https://workflow.example.invalid/invoke?api-version=2022-05-01", None, None, None)
    with pytest.raises(ValueError):
        MicrosoftWorkflow("https://workflow.example.invalid/invoke?api-version=2022-05-01&sig=secret", None, None, None)
