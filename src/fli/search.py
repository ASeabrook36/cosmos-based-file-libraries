"""Search execution (spec §8).

1. The route authenticates the caller and validates the body.
2. Point-read the library; apply the personal-library check (spec §5 rule 3).
3. Embed the query and convert it for the float16 index (CLAUDE.md rule 4, E8).
4-5. One hybrid query (vector-only when the query has no terms), library by equality only.
6. The generation check: point-read each result's document; keep only active generations.
   When dropped chunks leave fewer than ``top`` from a full fetch window, coverage says so
   (review finding 6). Nothing is fetched again: the shortfall is reported, not hidden.
7. Neighbours: one query per document, scoped to the full partition key.
9. Coverage: what was searched, which filters applied, how many results, and notes on what a
   filter left out: documents lacking the filtered field, and filter values no document in the
   library has. Counts come from the library's ``filterValues`` (``fli.vocabulary``).
10. A metrics-only log line. Query text and chunk text are never logged (CLAUDE.md rule 12).

``SEARCH_MODE`` chooses step 4-5: ``vector`` (the default) or ``hybrid``. Hybrid ranks only when
sent without a partition key, and then costs about ten times more (evidence.md E27).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from fli.blobs import BlobStore
from fli.embedding import Embedder, measure_embeddings
from fli.errors import ApiError
from fli.generation import filter_visible, is_visible
from fli.ids import image_prefix
from fli.metrics import record as record_metric
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
    """Every Cosmos call one search makes: library read, chunk query, document reads, neighbours."""

    request_charge: float = 0.0
    responses: int = 0

    def __call__(self, pipeline_response: Any) -> None:
        """A ``raw_response_hook``: counts every HTTP response, including hybrid components."""
        headers = pipeline_response.http_response.headers
        self.responses += 1
        self.request_charge += float(headers.get("x-ms-request-charge", 0) or 0)


class ChunkReader(Protocol):
    async def document(
        self, library_id: str, document_id: str, generation: int, limit: int, cost: QueryCost
    ) -> list[Record]: ...
    async def search(self, library_id: str, query: RenderedQuery, cost: QueryCost) -> list[Record]: ...
    async def neighbors(
        self, library_id: str, document_id: str, generation: int, indexes: Sequence[int], cost: QueryCost
    ) -> list[Record]: ...


class CosmosChunkReader:
    def __init__(self, container: Any) -> None:  # azure.cosmos.aio.ContainerProxy for "chunks"
        self._c = container

    async def document(
        self, library_id: str, document_id: str, generation: int, limit: int, cost: QueryCost
    ) -> list[Record]:
        if not 1 <= limit <= 100_001:
            raise ValueError("invalid complete-document chunk limit")
        sql = (
            f"SELECT TOP {limit} c.chunkIndex, c.text, c.heading, c.location, "
            "c.contentKind, c.imageRef, c.imageMeta FROM c "
            "WHERE c.libraryId = @libraryId AND c.documentId = @documentId AND c.generation = @generation"
        )
        params = [
            {"name": "@libraryId", "value": library_id},
            {"name": "@documentId", "value": document_id},
            {"name": "@generation", "value": generation},
        ]
        items = self._c.query_items(
            sql, parameters=params, partition_key=[library_id, document_id], raw_response_hook=cost
        )
        return [item async for item in items]

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
            f" AND c.generation = @generation AND c.contentKind = 'text' AND c.chunkIndex IN ({', '.join(names)})"
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
    filters_applied: Mapping[str, Any] = field(default_factory=dict)  # coverage.filtersApplied
    distinct_documents: bool = False


def fetch_size(top: int) -> int:
    return min(2 * top, MAX_FETCH)


def disposition(count: int) -> str:
    return "results" if count >= 3 else "thin" if count >= 1 else "empty"


MISSING = (  # (SearchFilters attribute, filterValues["missing"] key, what is lacking, filter name)
    ("period", "period", "a period set", "period"),
    ("authored_between", "authoredAt", "an authored date", "authoredBetween"),
    ("authors", "author", "an author", "authors"),
)


def filter_notes(filters: SearchFilters, library: Mapping[str, Any]) -> list[str]:
    """What the filters left out (spec §9.2). An empty result must not read as "the answer does not exist".

    A library refreshed before ``filterValues`` existed gets the spec's general notes instead of counts.
    """
    values = library.get("filterValues")
    vocabulary = library.get("tagVocabulary") or {}
    total = (library.get("stats") or {}).get("documentCount", 0)
    notes: list[str] = []
    pending = (library.get("stats") or {}).get("imageProcessingDocuments", 0)
    failed = (library.get("stats") or {}).get("imageFailedDocuments", 0)
    if pending:
        notes.append(
            f"{pending} documents have searchable text while images are still processing; "
            "visual search coverage will expand when enrichment finishes."
        )
    if failed:
        notes.append(f"Image enrichment failed for {failed} documents; their published text remains searchable.")
    skipped = (library.get("stats") or {}).get("imagesSkipped", 0)
    if skipped:
        notes.append(
            f"{skipped} visual candidates were skipped by configured size, format or per-document limits; "
            "search does not cover those visuals."
        )
    for attribute, missing_key, lacking, name in MISSING:
        if not getattr(filters, attribute):
            continue
        if values is None:
            notes.append(f"Documents without {lacking} are not included when filtering by {name}.")
        elif count := values["missing"].get(missing_key, 0):
            bare = lacking.split(" ", 1)[1]
            notes.append(f"{count} of {total} documents have no {bare}, so filtering by {name} left them out.")
    unknown_tags = [t for t in filters.tags if t.partition("=")[2] not in vocabulary.get(t.partition("=")[0], {})]
    if unknown_tags:
        notes.append(f"No document in this library is tagged {', '.join(unknown_tags)}.")
    if values is not None:
        if unknown := [a for a in filters.authors if a not in values["authors"]]:
            notes.append(f"No document in this library has the author {', '.join(unknown)}.")
        if unknown := [t for t in filters.file_types if t not in values["fileTypes"]]:
            notes.append(f"This library has no documents of type {', '.join(unknown)}.")
        if unknown := [k for k in filters.content_kinds if k not in values["contentKinds"]]:
            notes.append(f"This library has no {' or '.join(unknown)} content.")
    return notes


def underfill_note(hidden: int) -> str:
    return (
        f"{hidden} matching passages were left out because their documents are being added, replaced or deleted;"
        " searching again later may return more."
    )


@dataclass
class Searcher:
    libraries: LibraryStore
    documents: DocumentStore
    chunks: ChunkReader
    embedder: Embedder
    mode: str = "vector"  # SEARCH_MODE: "vector" or "hybrid"
    blobs: BlobStore | None = None

    async def image(self, library_id: str, document_id: str, generation: int, chunk: Mapping[str, Any]):
        if chunk.get("contentKind") != "image":
            return None
        path = chunk.get("imageRef") or ""
        prefix = f"{image_prefix(library_id, document_id)}{generation}/"
        if (
            not path.startswith(prefix)
            or not re.fullmatch(r"[A-Za-z0-9-]+\.[a-z0-9]{1,5}", path[len(prefix) :])
            or not self.blobs
        ):
            raise ApiError(409, "image_unavailable", "image unavailable; reprocess this document")
        try:
            link = await self.blobs.read_link(path)
        except Exception as exc:
            raise ApiError(503, "image_unavailable", "image read link unavailable; retry later") from exc
        return {**(chunk.get("imageMeta") or {}), **link}

    async def search(self, library_id: str, agent_id: str | None, request: SearchRequest) -> dict[str, Any]:
        started = time.perf_counter()
        cost = QueryCost()
        library = await self.libraries.get(library_id, raw_response_hook=cost)
        check_search_allowed(library_id, library, agent_id)
        assert library is not None

        embedding_started = time.perf_counter()
        with measure_embeddings() as embedding_usage:
            [vector] = await self.embedder.embed([request.query])
        embedding_ms = (time.perf_counter() - embedding_started) * 1000
        terms = lexical_terms(request.query) if self.mode == "hybrid" else []
        fetch = MAX_FETCH if request.distinct_documents else fetch_size(request.top)
        rendered = render_search(library_id, to_float16_query(vector), terms, request.filters, fetch)
        cosmos_started = time.perf_counter()
        candidates = await self.chunks.search(library_id, rendered, cost)
        cosmos_ms = (time.perf_counter() - cosmos_started) * 1000

        document_ids = list(dict.fromkeys(c["documentId"] for c in candidates))
        records = await asyncio.gather(
            *(self.documents.get(library_id, d, raw_response_hook=cost) for d in document_ids)
        )
        documents = {d: r for d, r in zip(document_ids, records, strict=True) if r is not None}
        visible = filter_visible(candidates, documents, fetch)
        passage_counts = Counter(c["documentId"] for c in visible)
        if request.distinct_documents:
            best = {}
            for candidate in visible:
                best.setdefault(candidate["documentId"], candidate)
            kept = list(best.values())[: request.top]
        else:
            kept = visible[: request.top]
        hidden = sum(1 for c in candidates if not is_visible(c, documents.get(c["documentId"])))
        # Only a full window can be hiding more visible matches; a short one returned them all.
        underfilled = len(kept) < request.top and hidden > 0 and len(candidates) == fetch

        neighbors: dict[tuple[str, int], list[Record]] = {}
        if request.include_neighbors:
            neighbors = await self._neighbors(library_id, kept, cost)

        results = [
            {
                "rank": rank,
                "chunkId": c["id"],
                "imageId": c["id"] if c.get("contentKind") == "image" else None,
                "documentId": c["documentId"],
                "generation": c["generation"],
                "documentTokens": (documents[c["documentId"]].get("fullText") or {}).get("tokenCount"),
                "representation": "extracted_sections"
                if (documents[c["documentId"]].get("fullText") or {}).get("generation") == c["generation"]
                else "indexed_chunks",
                **({"passagesInWindow": passage_counts[c["documentId"]]} if request.distinct_documents else {}),
                "fileName": c.get("fileName"),
                "location": c.get("location") or {},
                "heading": c.get("heading"),
                "contentKind": c.get("contentKind", "text"),
                "text": c.get("text", ""),
                "neighbors": neighbors.get((c["documentId"], c["chunkIndex"]), [])
                if request.include_neighbors
                else None,
                "image": await self.image(library_id, c["documentId"], c["generation"], c),
            }
            for rank, c in enumerate(kept, start=1)
        ]
        # Links can require a network round trip. Recheck after fetching evidence so
        # a deletion/replacement during that work cannot publish stale image results.
        image_documents = {c["documentId"] for c in kept if c.get("contentKind") == "image"}
        latest_records = await asyncio.gather(
            *(self.documents.get(library_id, document_id, raw_response_hook=cost) for document_id in image_documents)
        )
        for document_id, current in zip(image_documents, latest_records, strict=True):
            if any(not is_visible(c, current) for c in kept if c["documentId"] == document_id):
                raise ApiError(409, "document_changed", "a document changed during search; retry")
        if any(c.get("contentKind") == "image" for c in kept):
            check_search_allowed(library_id, await self.libraries.get(library_id, raw_response_hook=cost), agent_id)
        telemetry_id = str(uuid.uuid4())
        total_ms = round((time.perf_counter() - started) * 1000, 1)
        document_count = (library.get("stats") or {}).get("documentCount", 0)
        response = {
            "library": {"libraryId": library_id, "name": library.get("name")},
            "coverage": {
                "searched": f"{library.get('name')} ({document_count} documents)",
                "filtersApplied": dict(request.filters_applied),
                "notes": filter_notes(request.filters, library)
                + ([underfill_note(hidden)] if underfilled else [])
                + (
                    ["One best passage per document from at most 40 ranked passages; not an exhaustive file set."]
                    if request.distinct_documents
                    else []
                ),
                "distinctDocuments": request.distinct_documents,
                "resultCount": len(results),
                "disposition": disposition(len(results)),
            },
            "results": results,
            "usage": {
                "requestCharge": round(cost.request_charge, 2),
                "totalMs": total_ms,
                "embeddingMs": round(embedding_ms, 1),
                "embeddingTokens": embedding_usage.prompt_tokens if embedding_usage.tokens_reported else None,
            },
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
                "filterKeys": sorted(request.filters_applied),  # keys only: values may be personal
                "top": request.top,
                "candidates": len(candidates),
                "hidden": hidden,
                "underfilled": underfilled,
                "resultCount": len(results),
                "disposition": response["coverage"]["disposition"],
                "requestCharge": round(cost.request_charge, 2),
                "cosmosMs": round(cosmos_ms, 1),
                "totalMs": total_ms,
            },
        )
        record_metric(
            "search",
            library_id,
            agentId=agent_id,
            requestCharge=round(cost.request_charge, 2),
            totalMs=total_ms,
            cosmosMs=round(cosmos_ms, 1),
            resultCount=len(results),
            disposition=response["coverage"]["disposition"],
            embeddingMs=round(embedding_ms, 1),
            embeddingTokens=embedding_usage.prompt_tokens if embedding_usage.tokens_reported else None,
            embeddingRequests=embedding_usage.requests,
            embeddingRetries=embedding_usage.retries,
            embeddingThrottles=embedding_usage.throttled,
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
