"""Search execution (spec §8).

1. The route authenticates the caller and validates the body.
2. Point-read the library; apply the personal-library check (spec §5 rule 3).
3. Embed the query and convert it for the float16 index (CLAUDE.md rule 4, E8).
4-5. One hybrid query (vector-only when the query has no terms), library by equality only.
6. The generation check: point-read each result's document; keep only active generations.
7. Neighbours: one query per document, scoped to the full partition key.
9. Coverage: what was searched and how many results.
10. A metrics-only log line. Query text and chunk text are never logged (CLAUDE.md rule 12).

Phase 1 runs no metadata filters; Phase 3 adds them (the query builder already renders them).

``SEARCH_MODE`` chooses step 4-5: ``vector`` (the default) or ``hybrid``. Hybrid ranks only when
sent without a partition key, and then costs about ten times more (evidence.md E27).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from fli.embedding import Embedder
from fli.generation import filter_visible
from fli.permissions import check_search_allowed
from fli.query import (
    MAX_FETCH,
    RenderedQuery,
    SearchFilters,
    lexical_terms,
    partition_key_for,
    render_search,
    to_float16_query,
)
from fli.store import DocumentStore, LibraryStore, Record

log = logging.getLogger("fli.search")


@dataclass
class QueryCost:
    request_charge: float = 0.0
    responses: int = 0

    def __call__(self, pipeline_response: Any) -> None:
        """A ``raw_response_hook``: counts every HTTP response, including hybrid components."""
        headers = pipeline_response.http_response.headers
        self.responses += 1
        self.request_charge += float(headers.get("x-ms-request-charge", 0) or 0)


class ChunkReader(Protocol):
    async def search(self, library_id: str, query: RenderedQuery, cost: QueryCost) -> list[Record]: ...
    async def neighbors(
        self, library_id: str, document_id: str, generation: int, indexes: Sequence[int], cost: QueryCost
    ) -> list[Record]: ...


class CosmosChunkReader:
    def __init__(self, container: Any) -> None:  # azure.cosmos.aio.ContainerProxy for "chunks"
        self._c = container

    async def search(self, library_id: str, query: RenderedQuery, cost: QueryCost) -> list[Record]:
        routing: dict[str, Any] = {}
        if (key := partition_key_for(query.sql, library_id)) is not None:
            routing["partition_key"] = key
        items = self._c.query_items(query.sql, parameters=query.parameters, raw_response_hook=cost, **routing)
        return [item async for item in items]

    async def neighbors(
        self, library_id: str, document_id: str, generation: int, indexes: Sequence[int], cost: QueryCost
    ) -> list[Record]:
        names = [f"@i{n}" for n in range(len(indexes))]
        sql = (
            "SELECT c.chunkIndex, c.text FROM c WHERE c.libraryId = @libraryId AND c.documentId = @documentId"
            f" AND c.generation = @generation AND c.chunkIndex IN ({', '.join(names)})"
        )
        params = [
            {"name": "@libraryId", "value": library_id},
            {"name": "@documentId", "value": document_id},
            {"name": "@generation", "value": generation},
            *({"name": name, "value": index} for name, index in zip(names, indexes, strict=True)),
        ]
        items = self._c.query_items(
            sql, parameters=params, partition_key=[library_id, document_id], raw_response_hook=cost
        )
        return [item async for item in items]


@dataclass(frozen=True)
class SearchRequest:
    query: str
    top: int
    include_neighbors: bool = False
    filters: SearchFilters = field(default_factory=SearchFilters)


def fetch_size(top: int) -> int:
    return min(2 * top, MAX_FETCH)


def disposition(count: int) -> str:
    return "results" if count >= 3 else "thin" if count >= 1 else "empty"


@dataclass
class Searcher:
    libraries: LibraryStore
    documents: DocumentStore
    chunks: ChunkReader
    embedder: Embedder
    mode: str = "vector"  # SEARCH_MODE: "vector" or "hybrid"

    async def search(self, library_id: str, agent_id: str | None, request: SearchRequest) -> dict[str, Any]:
        started = time.perf_counter()
        library = await self.libraries.get(library_id)
        check_search_allowed(library_id, library, agent_id)
        assert library is not None

        [vector] = await self.embedder.embed([request.query])
        terms = lexical_terms(request.query) if self.mode == "hybrid" else []
        rendered = render_search(library_id, to_float16_query(vector), terms, request.filters, fetch_size(request.top))
        cost = QueryCost()
        cosmos_started = time.perf_counter()
        candidates = await self.chunks.search(library_id, rendered, cost)
        cosmos_ms = (time.perf_counter() - cosmos_started) * 1000

        document_ids = list(dict.fromkeys(c["documentId"] for c in candidates))
        records = await asyncio.gather(*(self.documents.get(library_id, d) for d in document_ids))
        documents = {d: r for d, r in zip(document_ids, records, strict=True) if r is not None}
        kept = filter_visible(candidates, documents, request.top)

        neighbors: dict[tuple[str, int], list[Record]] = {}
        if request.include_neighbors:
            neighbors = await self._neighbors(library_id, kept, cost)

        results = [
            {
                "rank": rank,
                "chunkId": c["id"],
                "documentId": c["documentId"],
                "fileName": c.get("fileName"),
                "location": c.get("location") or {},
                "heading": c.get("heading"),
                "contentKind": c.get("contentKind", "text"),
                "text": c.get("text", ""),
                "neighbors": neighbors.get((c["documentId"], c["chunkIndex"]), [])
                if request.include_neighbors
                else None,
                "image": None,  # image chunks and their read links are Phase 3
            }
            for rank, c in enumerate(kept, start=1)
        ]
        telemetry_id = str(uuid.uuid4())
        total_ms = round((time.perf_counter() - started) * 1000, 1)
        document_count = (library.get("stats") or {}).get("documentCount", 0)
        response = {
            "library": {"libraryId": library_id, "name": library.get("name")},
            "coverage": {
                "searched": f"{library.get('name')} ({document_count} documents)",
                "filtersApplied": {},
                "notes": [],
                "resultCount": len(results),
                "disposition": disposition(len(results)),
            },
            "results": results,
            "usage": {"requestCharge": round(cost.request_charge, 2), "totalMs": total_ms},
            "telemetryId": telemetry_id,
        }
        log.info(
            "library_search",
            extra={
                "telemetryId": telemetry_id,
                "libraryId": library_id,
                "libraryKind": library.get("kind"),
                "agentId": agent_id,
                "mode": self.mode,
                "top": request.top,
                "candidates": len(candidates),
                "resultCount": len(results),
                "disposition": response["coverage"]["disposition"],
                "requestCharge": round(cost.request_charge, 2),
                "cosmosMs": round(cosmos_ms, 1),
                "totalMs": total_ms,
            },
        )
        return response

    async def _neighbors(
        self, library_id: str, kept: Sequence[Mapping[str, Any]], cost: QueryCost
    ) -> dict[tuple[str, int], list[Record]]:
        """One query per document for chunkIndex ± 1 of each text result (spec §8 step 7)."""
        wanted: dict[tuple[str, int], set[int]] = {}
        for c in kept:
            if c.get("contentKind", "text") != "text":
                continue
            indexes = wanted.setdefault((c["documentId"], c["generation"]), set())
            indexes.update(i for i in (c["chunkIndex"] - 1, c["chunkIndex"] + 1) if i >= 0)
        keys = list(wanted)
        fetched = await asyncio.gather(
            *(self.chunks.neighbors(library_id, d, g, sorted(wanted[(d, g)]), cost) for d, g in keys)
        )
        by_document: dict[str, dict[int, str]] = {
            d: {n["chunkIndex"]: n["text"] for n in rows} for (d, _), rows in zip(keys, fetched, strict=True)
        }
        out: dict[tuple[str, int], list[Record]] = {}
        for c in kept:
            texts = by_document.get(c["documentId"], {})
            out[(c["documentId"], c["chunkIndex"])] = [
                {"chunkIndex": i, "text": texts[i]} for i in (c["chunkIndex"] - 1, c["chunkIndex"] + 1) if i in texts
            ]
        return out
