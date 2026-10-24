"""Search (spec §6.3, §8) against in-memory services. Ranking itself is tested live (E14, E24)."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

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
        ({"filters": {"tags": ["EMEA"]}}, "invalid_request"),  # a tag is key=value
        ({"filters": {"period": {"from": "2025-12-31", "to": "2025-10-01"}}}, "invalid_request"),
        ({"filters": {"period": {}}}, "invalid_request"),
        ({"filters": {"period": {"from": "Q4"}}}, "invalid_request"),
        ({"filters": {"fileTypes": ["exe"]}}, "invalid_request"),
        ({"filters": {"contentKinds": ["video"]}}, "invalid_request"),
        ({"filters": {"authors": ["x"] * 21}}, "invalid_request"),
        ({"filters": {"libraryId": "other"}}, "invalid_request"),  # nor a filter (rule 3)
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


async def test_the_response_reports_its_cost(ctx):
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    usage = search(client, lib).json()["usage"]
    assert set(usage) == {"requestCharge", "totalMs"}
    assert usage["requestCharge"] >= 0 and usage["totalMs"] >= 0


def charging(get, charge):
    """Wrap a store's ``get`` so each call reports ``charge`` RU through the hook, as Cosmos does."""
    response = SimpleNamespace(http_response=SimpleNamespace(headers={"x-ms-request-charge": str(charge)}))

    async def get_and_charge(*args, raw_response_hook=None):
        if raw_response_hook is not None:
            raw_response_hook(response)
        return await get(*args)

    return get_and_charge


async def test_the_reported_charge_covers_every_cosmos_call(ctx, monkeypatch):
    """Review finding 3: the library read and the generation check's document reads count too."""
    client, s, worker = ctx
    lib, _ = await library_with_document(client, s, worker)
    client.post(f"/v1/libraries/{lib}/documents", files={"file": ("q3.md", TEXT.encode(), "text/markdown")})
    while s.queue.sent:
        await worker.handle(s.queue.sent.pop(0)[1])
    monkeypatch.setattr(s.libraries, "get", charging(s.libraries.get, 1.0))
    monkeypatch.setattr(s.documents, "get", charging(s.documents.get, 1.25))
    body = search(client, lib, top=20).json()  # every chunk: both documents are candidates
    assert {r["documentId"] for r in body["results"]} == {d["id"] for d in s.documents.m.items.values()}
    assert body["usage"]["requestCharge"] == 4.0 + 1.0 + 2 * 1.25  # chunk query (fake), library, two documents


async def pending_replacement_ranked_first(s, lib, doc_id, stale: int) -> list[str]:
    """Generation 2 is being written: ``stale`` of its chunks rank above every active one."""
    record = await s.documents.get(lib, doc_id)
    record.update(status="processing", generation=2, pendingGeneration=2)
    await s.documents.replace(record)
    base = s.chunks.items[(lib, doc_id, f"{doc_id}:1:0")]
    for i in range(stale):
        await s.chunks.upsert({**base, "id": f"{doc_id}:2:{i}", "generation": 2, "chunkIndex": i})
    active = sorted(c["id"] for c in s.chunks.items.values() if c["generation"] == 1)
    s.reader.order = [f"{doc_id}:2:{i}" for i in range(stale)] + active
    return active


async def test_stale_chunks_that_fill_the_window_are_reported_not_hidden(ctx):
    """Review finding 6: a full window of mostly stale chunks leaves fewer than ``top``."""
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    await pending_replacement_ranked_first(s, lib, doc_id, stale=14)
    body = search(client, lib, top=8).json()  # fetch 16: 14 stale, 2 active
    assert len(s.reader.queries) == 1  # nothing is fetched again
    assert [r["chunkId"].split(":")[1] for r in body["results"]] == ["1", "1"]
    assert body["coverage"]["disposition"] == "thin"
    [note] = body["coverage"]["notes"]
    assert note.startswith("14 matching passages were left out") and "replaced or deleted" in note


async def test_no_note_when_the_window_was_not_full_or_top_was_reached(ctx):
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    active = await pending_replacement_ranked_first(s, lib, doc_id, stale=3)
    short = search(client, lib, top=8).json()  # 3 + len(active) < 16: every visible match came back
    assert short["coverage"]["notes"] == [] and short["coverage"]["resultCount"] == len(active)
    full = search(client, lib, top=2).json()  # fetch 4: 3 stale and 1 active, a full window below top
    assert full["coverage"]["resultCount"] == 1 and len(full["coverage"]["notes"]) == 1
    s.reader.order = s.reader.order[2:]  # 1 stale, then active: top reached despite a dropped chunk
    reached = search(client, lib, top=2).json()
    assert reached["coverage"]["resultCount"] == 2 and reached["coverage"]["notes"] == []


async def test_the_underfill_log_line_carries_counts_only(ctx, caplog):
    client, s, worker = ctx
    lib, doc_id = await library_with_document(client, s, worker)
    await pending_replacement_ranked_first(s, lib, doc_id, stale=14)
    with caplog.at_level(logging.INFO, logger="fli.search"):
        search(client, lib, top=8)
    [line] = [r for r in caplog.records if r.getMessage() == "library_search"]
    assert (line.hidden, line.underfilled, line.resultCount) == (14, True, 2)


# -- filters (spec §6.3, §8, §9.2) ---------------------------------------------------------------


async def tagged_library(client, s, worker):
    """Three documents: one with every field set, one with a tag only, one with nothing."""
    lib = client.post("/v1/libraries", json={"kind": "shared", "name": "Finance"}).json()["id"]
    full = {
        "author": "Jane Doe",
        "periodStart": "2025-10-01",
        "periodEnd": "2025-12-31",
        "tags": [{"key": "region", "value": "EMEA"}, {"key": "topic", "value": "margin"}],
    }
    for name, metadata in (
        ("q4.md", full),
        ("apac.md", {"tags": [{"key": "region", "value": "APAC"}]}),
        ("notes.md", None),
    ):
        form = {"metadata": json.dumps(metadata)} if metadata else {}
        client.post(
            f"/v1/libraries/{lib}/documents",
            files={"file": (name, f"# Margins\n\n{TEXT}".encode(), "text/markdown")},
            data=form,
        )
    while s.queue.sent:
        await worker.handle(s.queue.sent.pop(0)[1])
    return lib


async def test_filters_render_into_the_query_with_inclusive_dates(ctx):
    client, s, worker = ctx
    lib = await tagged_library(client, s, worker)
    filters = {
        "period": {"from": "2025-10-01", "to": "2025-12-31"},
        "authoredBetween": {"to": "2025-03-31"},
        "uploadedBetween": {"from": "2026-01-01"},
        "authors": ["Jane Doe"],
        "tags": ["region=EMEA", "topic=margin"],
        "fileTypes": ["md"],
        "contentKinds": ["text"],
    }
    assert search(client, lib, filters=filters).status_code == 200
    query = s.reader.queries[-1][1]
    params = {p["name"]: p["value"] for p in query.parameters}
    assert "c.periodStart <= @pTo AND c.periodEnd >= @pFrom" in query.sql
    assert (params["@pFrom"], params["@pTo"]) == ("2025-10-01", "2025-12-31")  # overlap, both inclusive
    assert (params["@aFrom"], params["@aTo"]) == ("0001-01-01", "2025-04-01")  # to is the next day, exclusive
    assert (params["@uFrom"], params["@uTo"]) == ("2026-01-01", "9999-12-31")  # an open side is unbounded
    assert "ARRAY_CONTAINS(c.facets, @tag0) AND ARRAY_CONTAINS(c.facets, @tag1)" in query.sql  # tags: all
    assert [params["@tag0"], params["@tag1"], params["@au0"], params["@ft0"], params["@ck0"]] == [
        "region=EMEA",
        "topic=margin",
        "Jane Doe",
        "md",
        "text",
    ]
    assert query.sql.count("c.libraryId") == 1 and "{'dataType': 'float16'}" in query.sql  # rules 2 and 4


async def test_coverage_says_which_filters_applied(ctx):
    client, s, worker = ctx
    lib = await tagged_library(client, s, worker)
    body = search(client, lib, filters={"period": {"from": "2025-10-01"}, "tags": ["region=EMEA", "region=EMEA"]})
    assert body.json()["coverage"]["filtersApplied"] == {"period": "2025-10-01..", "tags": ["region=EMEA"]}
    assert search(client, lib).json()["coverage"]["filtersApplied"] == {}


async def test_coverage_counts_documents_a_filter_left_out_for_lacking_the_field(ctx):
    client, s, worker = ctx
    lib = await tagged_library(client, s, worker)
    filters = {"period": {"from": "2025-10-01"}, "authors": ["Jane Doe"], "authoredBetween": {"from": "2025-01-01"}}
    notes = search(client, lib, filters=filters).json()["coverage"]["notes"]
    assert notes == [
        "2 of 3 documents have no period set, so filtering by period left them out.",
        "3 of 3 documents have no authored date, so filtering by authoredBetween left them out.",
        "2 of 3 documents have no author, so filtering by authors left them out.",
    ]


async def test_coverage_names_filter_values_no_document_has(ctx):
    client, s, worker = ctx
    lib = await tagged_library(client, s, worker)
    filters = {
        "tags": ["region=EMEA", "region=LATAM"],
        "authors": ["Jane Doe", "Sam Lee"],
        "fileTypes": ["md", "pptx"],
        "contentKinds": ["image"],
    }
    notes = search(client, lib, filters=filters).json()["coverage"]["notes"]
    assert "No document in this library is tagged region=LATAM." in notes
    assert "No document in this library has the author Sam Lee." in notes
    assert "This library has no documents of type pptx." in notes
    assert "This library has no image content." in notes
    assert "2 of 3 documents have no author, so filtering by authors left them out." in notes
    assert len(notes) == 5  # values the library does have (region=EMEA, Jane Doe, md) get no note


async def test_a_library_summarised_before_filter_values_gets_general_notes(ctx):
    client, s, worker = ctx
    lib = await tagged_library(client, s, worker)
    library = await s.libraries.get(lib)
    del library["filterValues"]
    await s.libraries.replace(library)
    notes = search(client, lib, filters={"period": {"from": "2025-10-01"}, "tags": ["x=y"]}).json()["coverage"]["notes"]
    assert notes == [
        "Documents without a period set are not included when filtering by period.",
        "No document in this library is tagged x=y.",
    ]


async def test_filter_keys_are_logged_but_never_their_values(ctx, caplog):
    client, s, worker = ctx
    lib = await tagged_library(client, s, worker)
    with caplog.at_level(logging.INFO, logger="fli.search"):
        search(client, lib, filters={"tags": ["diagnosis=private"], "authors": ["Jane Doe"]})
    [line] = [r for r in caplog.records if r.getMessage() == "library_search"]
    assert line.filterKeys == ["authors", "tags"]
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "private" not in logged and "Jane" not in logged


async def test_a_filtered_search_returns_only_matching_documents(ctx):
    client, s, worker = ctx
    lib = await tagged_library(client, s, worker)
    body = search(client, lib, top=20, filters={"tags": ["region=EMEA"], "period": {"from": "2025-11-01"}}).json()
    assert body["results"] and {r["fileName"] for r in body["results"]} == {"q4.md"}
    assert {r["fileName"] for r in search(client, lib, top=20).json()["results"]} == {"q4.md", "apac.md", "notes.md"}
