"""Search (spec §6.3, §8) against in-memory services. Ranking itself is tested live (E14, E24)."""

from __future__ import annotations

import logging

import pytest

from fli.chunking import ApproxTokenizer, Chunker
from fli.worker.handlers import Worker
from tests.fakes import api

TEXT = "\n\n".join(f"Paragraph {i}. Gross margin in EMEA moved by {i} points in quarter {i}. " * 40 for i in range(4))
AGENT = {"X-Agent-Id": "agent-1"}


@pytest.fixture
def ctx():
    client, s = api()
    worker = Worker(s.libraries, s.documents, s.chunks, s.blobs, s.queue, s.embedder, Chunker(ApproxTokenizer()))
    return client, s, worker


async def library_with_document(client, s, worker, kind="shared", **extra):
    body = {"kind": kind, "name": "Finance", **({"boundAgentId": "agent-1"} if kind == "personal" else {}), **extra}
    lib = client.post("/v1/libraries", json=body).json()["id"]
    r = client.post(
        f"/v1/libraries/{lib}/documents", files={"file": ("q4.md", f"# Margins\n\n{TEXT}".encode(), "text/markdown")}
    )
    while s.queue.sent:
        await worker.handle(s.queue.sent.pop(0)[1])
    return lib, r.json()["documentId"]


def search(client, lib, headers=AGENT, **body):
    return client.post(
        f"/v1/libraries/{lib}/search", json={"query": "Why did EMEA gross margin fall?", **body}, headers=headers
    )


async def test_search_returns_chunks_with_citations_and_coverage(ctx):
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    r = search(client, lib, top=3)
    assert r.status_code == 200
    body = r.json()
    assert body["library"] == {"libraryId": lib, "name": "Finance"}
    assert body["coverage"]["resultCount"] == 3 and body["coverage"]["disposition"] == "results"
    assert body["coverage"]["searched"] == "Finance (1 documents)"
    first = body["results"][0]
    assert first["rank"] == 1 and first["fileName"] == "q4.md" and first["heading"] == "Margins"
    assert first["documentId"] == doc_id and first["chunkId"] == f"{doc_id}:1:0" and first["image"] is None
    assert "embedding" not in first and "score" not in first


async def test_the_default_vector_query_obeys_the_invariants(ctx):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    search(client, lib, top=8)
    [(routed_to, query)] = s.reader.queries
    params = {p["name"]: p["value"] for p in query.parameters}
    assert routed_to == lib and params["@libraryId"] == lib  # the library comes from the path
    assert query.sql.startswith("SELECT TOP 16 ")  # min(2 x top, 40)
    assert query.sql.endswith("ORDER BY VectorDistance(c.embedding, @queryVector, false, {'dataType': 'float16'})")
    assert "RRF" not in query.sql and not any(p.startswith("@t") for p in params)
    assert params["@queryVector"][2] == 0.0  # the subnormal was flushed (rule 4)
    search(client, lib, top=20)
    assert s.reader.queries[-1][1].sql.startswith("SELECT TOP 40 ")


async def test_hybrid_mode_adds_the_lexical_terms(ctx):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    client.app.state.searcher.mode = "hybrid"
    search(client, lib, top=8)
    query = s.reader.queries[-1][1]
    params = {p["name"]: p["value"] for p in query.parameters}
    assert "ORDER BY RANK RRF(" in query.sql and "{'dataType': 'float16'}" in query.sql
    assert [params[f"@t{i}"] for i in range(3)] == ["why", "did", "emea"]


async def test_the_cosmos_reader_routes_vector_queries_by_partition_and_hybrid_queries_not():
    from fli.query import SearchFilters, render_search
    from fli.search import CosmosChunkReader, QueryCost

    class Container:
        def __init__(self):
            self.calls = []

        def query_items(self, sql, **kwargs):
            self.calls.append(kwargs.get("partition_key"))

            async def rows():
                return
                yield

            return rows()

    container = Container()
    reader = CosmosChunkReader(container)
    vector = [0.5, 0.25, 0.0]
    await reader.search("L", render_search("L", vector, [], SearchFilters(), 8), QueryCost())
    await reader.search("L", render_search("L", vector, ["margin"], SearchFilters(), 8), QueryCost())
    assert container.calls == [["L"], None]  # evidence.md E27


async def test_deleted_documents_vanish_before_the_purge_runs(ctx):
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")  # purge queued, not yet run
    assert s.chunks.items
    body = search(client, lib).json()
    assert body["results"] == [] and body["coverage"]["disposition"] == "empty"


async def test_a_pending_replacement_keeps_the_old_version_searchable(ctx):
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    record = await s.documents.get(lib, doc_id)
    record.update(status="processing", pendingGeneration=2)
    await s.documents.replace(record)
    await s.chunks.upsert({**s.chunks.items[(lib, doc_id, f"{doc_id}:1:0")], "id": f"{doc_id}:2:0", "generation": 2})
    ids = [r["chunkId"] for r in search(client, lib, top=20).json()["results"]]
    assert ids and all(i.split(":")[1] == "1" for i in ids)


async def test_thin_results_are_reported_as_thin(ctx):
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    s.reader.order = [f"{doc_id}:1:0"]
    assert search(client, lib).json()["coverage"]["disposition"] == "thin"


async def test_neighbours_come_from_the_same_document_and_generation(ctx):
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    s.reader.order = [f"{doc_id}:1:1"]
    [result] = search(client, lib, includeNeighbors=True).json()["results"]
    assert [n["chunkIndex"] for n in result["neighbors"]] == [0, 2]
    assert search(client, lib).json()["results"][0]["neighbors"] is None


async def test_a_personal_library_answers_only_its_agent(ctx):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker, kind="personal")
    assert search(client, lib).status_code == 200
    embeds = s.embedder.calls  # ingest and the allowed search
    r = search(client, lib, headers={"X-Agent-Id": "agent-2"})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "library_not_bound_to_agent")
    r = search(client, lib, headers={})
    assert (r.status_code, r.json()["error"]["code"]) == (400, "missing_agent_id")
    assert s.embedder.calls == embeds  # refused before any embedding or query
    assert len(s.reader.queries) == 1


async def test_deleted_or_unknown_libraries_are_404(ctx):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    client.delete(f"/v1/libraries/{lib}")
    assert search(client, lib).json()["error"]["code"] == "library_not_found"
    assert search(client, "nope").json()["error"]["code"] == "library_not_found"


@pytest.mark.parametrize(
    "body,code",
    [
        ({"top": 0}, "invalid_request"),
        ({"top": 21}, "invalid_request"),
        ({"top": "8"}, "invalid_request"),
        ({"query": "   "}, "invalid_request"),
        ({"filters": {"tags": ["region=EMEA"]}}, "filters_not_supported"),
        ({"libraryId": "other"}, "invalid_request"),  # the library is never a body field (rule 3)
    ],
)
async def test_bad_search_bodies(ctx, body, code):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    r = search(client, lib, **body)
    assert (r.status_code, r.json()["error"]["code"]) == (400, code)


async def test_empty_filters_are_accepted(ctx):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    assert search(client, lib, filters={"tags": [], "period": None}).status_code == 200


async def test_query_and_chunk_text_are_never_logged(ctx, caplog):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    with caplog.at_level(logging.DEBUG):
        search(client, lib, query="my private medical question")
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "library_search" in logged and "requestCharge" in logged
    assert "medical" not in logged and "Gross margin" not in logged
