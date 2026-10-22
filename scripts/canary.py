"""Phase 0 canary: prove a freshly deployed account's containers work at the data plane.

A successful deployment is not evidence of a working vector index (docs/evidence.md E17), and
this exact configuration has never been built before (N1). The canary, per
docs/build-plan.md Phase 0:

1. Checks each container's stored definition against ``cosmos/*.json``, then writes, reads
   and queries a record in each container.
2. Loads about 1,500 chunks of real text, from a corpus directory you supply, embedded with
   the real model, into one new test library. Random vectors and filler text are invalid
   test data (E14). DiskANN indexing is asynchronous, so it then polls one vector search
   (with and without the library as partition-key prefix) until it stops reading every
   chunk, for up to ``--index-wait-minutes``, and records how long that took.
3. Runs 20 vector-only and 20 hybrid searches through the float16 query path
   (``src/fli/query.py``; spec §8 step 3), and the exact (brute-force) version of each.
4. Asserts: no null scores; the index is engaged (each vector-only search costs under
   100 RU and retrieves far fewer documents than were loaded); mean recall@10 of at least
   0.95 against exact search, for vector-only searches.
5. Runs one search that declares ``float32`` with an unconverted vector, and records whether
   it scans (E8). Recorded, not asserted: it documents the trap.
6. Writes ``records/canary-<UTC timestamp>.json``. The record holds metrics only: no query
   text and no chunk text (CLAUDE.md rule 12).

Charges and query metrics are summed over every HTTP response of a search, through the
pipeline's ``raw_response_hook``. The SDK's ``response_hook`` misses the component requests
of a hybrid search: the first live run recorded one response per hybrid search, and its
hybrid charges were meaningless.

The index-engagement thresholds and recall apply to vector-only searches. Hybrid searches are
checked for execution and results; their cost is recorded but not asserted (E21, N3). Each is
also run with ``VectorDistance(…, true, …)`` and the agreement recorded, but not asserted:
the first passing run showed that inside ``RRF`` the brute-force flag changes neither cost nor
results, so there is no exact hybrid reference to compare against (E24).

Records name no account, subscription, tenant or machine: no endpoint is written, and GUID
paths in Cosmos's index-utilization reports are replaced with ``<internal>``.

Authentication is ``DefaultAzureCredential``: run ``az login`` as a principal holding the
Cosmos DB Built-in Data Contributor and Cognitive Services OpenAI User roles (the Bicep grants
them to ``operatorPrincipalId``).

Usage, from the repo root::

    python scripts/canary.py --corpus ~/canary-corpus --count-only   # is the corpus big enough? no Azure
    python scripts/canary.py --corpus ~/canary-corpus
    python scripts/canary.py --cleanup <libraryId>   # remove a run kept with --keep or left by a crash

Environment: ``COSMOS_ENDPOINT``, ``AOAI_ENDPOINT``, ``AOAI_EMBEDDING_DEPLOYMENT``;
optionally ``COSMOS_DATABASE`` (default ``fli``) and ``AOAI_EMBEDDING_DIMENSIONS`` (3072).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # run as `python scripts/canary.py` from anywhere
    sys.path.insert(0, str(REPO))

from fli.chunking import Chunker, default_tokenizer, split_sentences  # noqa: E402
from fli.query import (  # noqa: E402
    STORED_DTYPE,
    SearchFilters,
    check_query,
    float16_flush_count,
    lexical_terms,
    render_search,
    to_float16_query,
)

CONTAINERS = ("libraries", "documents", "chunks")
TARGET_CHUNKS = 1500
MIN_CHUNKS = 1200  # below this the "far below the loaded count" check means little (E12)
QUERIES_PER_KIND = 20
K = 10
RECALL_FLOOR = 0.95
MAX_INDEXED_SEARCH_RU = 100.0  # "tens of RU"; a scan of this library costs thousands (E8)
MAX_RETRIEVED_FRACTION = 0.2  # of the chunks loaded; the indexed path read about 0.06% in E8
# A serverless container saturates at about two concurrent writers (E10). The first live run,
# at 8 (the spec §7.2 per-document cap), exhausted the SDK's own 429 retries within seconds.
WRITE_CONCURRENCY = 2
INDEX_POLL_SECONDS = 30
THROTTLE_ATTEMPTS = 12  # our retries on 429, on top of the SDK's
MAX_THROTTLE_WAIT_SECONDS = 30.0
QUERY_SEED = 20261022
CORPUS_TYPES = {"pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "html", "htm"}
EMBEDDING_MODEL = "text-embedding-3-large"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"

_VECTOR_F16 = f"VectorDistance(c.embedding, @queryVector, false, {{'dataType': '{STORED_DTYPE}'}})"


class CanaryError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_canary.py)
# ---------------------------------------------------------------------------------------------


def parse_query_metrics(header: str | None) -> dict[str, float]:
    """``x-ms-documentdb-query-metrics``: ``key=value;key=value``."""
    metrics: dict[str, float] = {}
    for part in (header or "").split(";"):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        try:
            metrics[key.strip()] = float(value)
        except ValueError:
            continue
    return metrics


def recall_at_k(found: Sequence[str], exact: Sequence[str], k: int = K) -> float:
    reference = list(exact)[:k]
    if not reference:
        return 0.0
    return len(set(found[:k]) & set(reference)) / len(reference)


def scored_variant(sql: str) -> str:
    """Add the vector distance to the projection, so a null score is visible.

    ``c.embedding`` appears only as the argument to ``VectorDistance``; the embedding itself
    is still never selected (CLAUDE.md rule 5). ``check_query`` is run on the spec query
    before this is applied, because its projection rule cannot tell the two apart.
    """
    head = sql.split(" ", 3)  # SELECT, TOP, n, rest
    if len(head) != 4 or head[0] != "SELECT" or head[1] != "TOP":
        raise CanaryError("not a SELECT TOP query")
    return f"SELECT TOP {head[2]} {_VECTOR_F16} AS score, {head[3]}"


def exact_variant(sql: str) -> str:
    """Brute force: ``VectorDistance(…, true, …)`` compares every vector in scope (E14)."""
    if _VECTOR_F16 not in sql:
        raise CanaryError("no float16 VectorDistance to replace")
    return sql.replace(_VECTOR_F16, _VECTOR_F16.replace(", false,", ", true,"))


def float32_trap_variant(sql: str) -> str:
    """The wrong declaration (E8). Built only to document the trap; ``check_query`` rejects it."""
    if _VECTOR_F16 not in sql:
        raise CanaryError("no float16 VectorDistance to replace")
    return sql.replace(_VECTOR_F16, _VECTOR_F16.replace(f"'{STORED_DTYPE}'", "'float32'"))


_GUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


def _scrub_guids(value: Any) -> Any:
    if isinstance(value, str):
        return _GUID.sub("<internal>", value)
    if isinstance(value, list):
        return [_scrub_guids(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_guids(v) for k, v in value.items()}
    return value


def decode_index_utilization(header: str | None) -> Any:
    """``x-ms-cosmos-index-utilization`` is base64-encoded JSON. Undecodable values are dropped.

    Cosmos lists some indexes under GUID paths whose meaning is undocumented and which may be
    specific to the account, so they become ``"<internal>"`` before anything is recorded.
    """
    if not header:
        return None
    try:
        report = json.loads(base64.b64decode(header).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "undecodable"
    return _scrub_guids(report)


def chunk_id(document_id: str, generation: int, chunk_index: int) -> str:
    """Deterministic, so a retried write overwrites rather than duplicates (spec §4.3)."""
    return f"{document_id}:{generation}:{chunk_index}"


def definition_mismatches(expected: Any, actual: Any, where: str = "") -> list[str]:
    """Where the stored container definition lacks or differs from ``cosmos/*.json``.

    Subset semantics: the service adds fields of its own (``_etag`` exclusions, ``_rid`` …),
    which are fine. Lists of ``{"path": …}`` objects are matched by path.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{where or '/'}: expected an object"]
        out: list[str] = []
        for key, value in expected.items():
            if key not in actual:
                out.append(f"{where}/{key}: missing")
            else:
                out.extend(definition_mismatches(value, actual[key], f"{where}/{key}"))
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{where}: expected a list"]
        if expected and all(isinstance(e, dict) and "path" in e for e in expected):
            by_path = {a.get("path"): a for a in actual if isinstance(a, dict)}
            out = []
            for item in expected:
                if item["path"] not in by_path:
                    out.append(f"{where}[{item['path']}]: missing")
                else:
                    out.extend(definition_mismatches(item, by_path[item["path"]], f"{where}[{item['path']}]"))
            return out
        if len(expected) != len(actual):
            return [f"{where}: expected {len(expected)} items, found {len(actual)}"]
        out = []
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            out.extend(definition_mismatches(e, a, f"{where}[{i}]"))
        return out
    if expected != actual:
        return [f"{where}: expected {expected!r}, found {actual!r}"]
    return []


def query_sentence(text: str, heading: str | None) -> str | None:
    """A real sentence from a chunk, used as a search: the first with 6–40 words."""
    body = text[len(heading) + 1 :] if heading and text.startswith(heading + "\n") else text
    for sentence in split_sentences(body):
        words = sentence.split()
        if 6 <= len(words) <= 40:
            return sentence
    return None


def pick_query_sources(chunks: Sequence[tuple[str, str | None]], n: int, seed: int = QUERY_SEED) -> list[int]:
    """Deterministic choice of ``n`` chunk positions that each yield a query sentence."""
    order = list(range(len(chunks)))
    random.Random(seed).shuffle(order)
    picked = [i for i in order if query_sentence(*chunks[i]) is not None][:n]
    if len(picked) < n:
        raise CanaryError(f"the corpus yields only {len(picked)} usable query sentences; need {n}")
    return picked


@dataclass
class SearchMeasurement:
    kind: str  # vector | hybrid | exact-vector | exact-hybrid | float32-trap
    query: int  # position in its kind's query list; never the text
    request_charge: float = 0.0
    retrieved_document_count: float | None = None
    output_document_count: float | None = None
    http_responses: int = 0
    result_ids: list[str] = field(default_factory=list)
    null_scores: int = 0
    lexical_terms: int = 0
    flushed_subnormals: int = 0
    throttled: int = 0  # 429s that outlasted the SDK's retries and were retried here
    index_utilization: Any = None  # Cosmos's own report, from x-ms-cosmos-index-utilization
    measured_by: str = "raw_response_hook"  # or "response_hook", which misses hybrid components
    milliseconds: float = 0.0
    error: str | None = None


@dataclass
class Check:
    name: str
    passed: bool
    observed: Any
    rule: str


def evaluate(
    *,
    loaded: int,
    crud: Mapping[str, str | None],
    definition_problems: Mapping[str, list[str]],
    vector: Sequence[SearchMeasurement],
    exact_vector: Sequence[SearchMeasurement],
    hybrid: Sequence[SearchMeasurement],
    exact_hybrid: Sequence[SearchMeasurement],
) -> list[Check]:
    checks = [
        Check(
            "container_definitions_match",
            not any(definition_problems.values()),
            {k: v for k, v in definition_problems.items() if v},
            "stored definitions contain cosmos/*.json",
        ),
    ]
    for name in CONTAINERS:
        checks.append(
            Check(f"crud_{name}", crud.get(name) is None, crud.get(name) or "ok", "write, read and query one record")
        )
    checks.append(Check("corpus_large_enough", loaded >= MIN_CHUNKS, loaded, f">= {MIN_CHUNKS} chunks loaded"))

    def errors(ms: Sequence[SearchMeasurement]) -> list[str]:
        return [f"{m.kind}[{m.query}]: {m.error}" for m in ms if m.error]

    ran = errors(vector) + errors(exact_vector) + errors(hybrid) + errors(exact_hybrid)
    checks.append(
        Check(
            "searches_executed",
            not ran and len(vector) == len(hybrid) == QUERIES_PER_KIND,
            ran or "ok",
            f"{QUERIES_PER_KIND} vector-only and {QUERIES_PER_KIND} hybrid searches, and their exact"
            " references, all ran",
        )
    )
    empty = [f"{m.kind}[{m.query}]" for m in [*vector, *hybrid] if not m.error and not m.result_ids]
    checks.append(Check("no_empty_results", not empty, empty or "ok", "every search returns results"))
    nulls = sum(m.null_scores for m in vector)
    checks.append(Check("no_null_scores", nulls == 0, nulls, "every vector-only result has a distance"))

    charges = [m.request_charge for m in vector if not m.error]
    checks.append(
        Check(
            "index_engaged_charge",
            bool(charges) and max(charges) < MAX_INDEXED_SEARCH_RU,
            _summary(charges),
            f"every vector-only search < {MAX_INDEXED_SEARCH_RU:g} RU",
        )
    )
    retrieved = [m.retrieved_document_count for m in vector if not m.error]
    limit = MAX_RETRIEVED_FRACTION * loaded
    known = [r for r in retrieved if r is not None]
    checks.append(
        Check(
            "index_engaged_retrieved",
            bool(retrieved) and len(known) == len(retrieved) and max(known) < limit,
            _summary(known) | {"missingMetrics": len(retrieved) - len(known), "limit": limit},
            f"every vector-only search retrieves < {MAX_RETRIEVED_FRACTION:g} x chunks loaded",
        )
    )

    agreement = agreement_at_k(vector, exact_vector)
    checks.append(
        Check(
            f"vector_recall_at_{K}",
            agreement["queries"] == QUERIES_PER_KIND and agreement["mean"] >= RECALL_FLOOR,
            agreement,
            f"mean recall@{K} >= {RECALL_FLOOR} against exact search",
        )
    )
    return checks


def agreement_at_k(found: Sequence[SearchMeasurement], reference: Sequence[SearchMeasurement]) -> dict[str, Any]:
    """Mean and minimum recall@K of ``found`` against ``reference``, query by query."""
    by_query = {m.query: m.result_ids for m in reference if not m.error}
    recalls = [recall_at_k(m.result_ids, by_query[m.query]) for m in found if not m.error and m.query in by_query]
    return {
        "mean": round(statistics.fmean(recalls), 4) if recalls else 0.0,
        "min": round(min(recalls), 4) if recalls else None,
        "queries": len(recalls),
    }


def _summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 2),
        "median": round(statistics.median(ordered), 2),
        "max": round(ordered[-1], 2),
    }


# ---------------------------------------------------------------------------------------------
# Azure I/O
# ---------------------------------------------------------------------------------------------


class ResponseTally:
    """A ``response_hook`` that sums request charge and query metrics over backend pages."""

    def __init__(self) -> None:
        self.request_charge = 0.0
        self.pages = 0
        self.metrics: dict[str, float] = {}
        self.saw_metrics = False

    def __call__(self, headers: Mapping[str, str], _result: Any) -> None:
        self.pages += 1
        self.request_charge += float(headers.get("x-ms-request-charge", 0) or 0)
        raw = headers.get("x-ms-documentdb-query-metrics")
        if raw:
            self.saw_metrics = True
            for key, value in parse_query_metrics(raw).items():
                self.metrics[key] = self.metrics.get(key, 0.0) + value


class RawTally:
    """A ``raw_response_hook``: every HTTP response of one call, including each component
    request of a hybrid search, which ``ResponseTally`` as a ``response_hook`` misses."""

    def __init__(self) -> None:
        self.request_charge = 0.0
        self.responses = 0
        self.metrics: dict[str, float] = {}
        self.saw_metrics = False
        self.index_utilization: Any = None

    def __call__(self, pipeline_response: Any) -> None:
        headers = pipeline_response.http_response.headers
        self.responses += 1
        self.request_charge += float(headers.get("x-ms-request-charge", 0) or 0)
        raw = headers.get("x-ms-documentdb-query-metrics")
        if raw:
            self.saw_metrics = True
            for key, value in parse_query_metrics(raw).items():
                self.metrics[key] = self.metrics.get(key, 0.0) + value
        if self.index_utilization is None:
            self.index_utilization = decode_index_utilization(headers.get("x-ms-cosmos-index-utilization"))


class Throttle:
    """Retries a Cosmos call on 429 after the SDK's own retries give up.

    Waits ``x-ms-retry-after-ms`` when the service sends it, else a jittered backoff, and
    counts every retry so the record shows how throttled the run was (E10).
    """

    def __init__(self, sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.count = 0
        self._sleep = sleep

    async def run(self, call: Callable[[], Any]) -> Any:
        from azure.cosmos.exceptions import CosmosHttpResponseError

        from fli.http import backoff_seconds

        for attempt in range(THROTTLE_ATTEMPTS):
            try:
                return await call()
            except CosmosHttpResponseError as exc:
                if exc.status_code != 429 or attempt + 1 == THROTTLE_ATTEMPTS:
                    raise
                self.count += 1
                hint = (exc.headers or {}).get("x-ms-retry-after-ms")
                try:
                    wait = float(hint) / 1000 if hint is not None else backoff_seconds(attempt)
                except ValueError:
                    wait = backoff_seconds(attempt)
                await self._sleep(min(MAX_THROTTLE_WAIT_SECONDS, max(wait, 0.1)))
        raise CanaryError("unreachable")


class TokenCache:
    """One token per hour instead of one ``az`` call per embedding batch."""

    def __init__(self, credential: Any) -> None:
        self._credential = credential
        self._token: Any = None

    async def __call__(self) -> str:
        if self._token is None or self._token.expires_on - time.time() < 300:
            self._token = await self._credential.get_token(COGNITIVE_SCOPE)
        return self._token.token


@dataclass
class CorpusChunk:
    document_id: str
    file_index: int
    chunk_index: int
    text: str
    heading: str | None
    location: dict[str, Any] | None
    token_count: int


async def read_corpus(directory: Path, target: int) -> tuple[list[dict[str, Any]], list[CorpusChunk], str]:
    from fli.extraction import extract

    chunker = Chunker(default_tokenizer())
    files = sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower().lstrip(".") in CORPUS_TYPES)
    if not files:
        raise CanaryError(f"no supported files ({', '.join(sorted(CORPUS_TYPES))}) under {directory}")
    documents: list[dict[str, Any]] = []
    chunks: list[CorpusChunk] = []
    for path in files:
        if len(chunks) >= target:
            break
        data = path.read_bytes()
        extracted = await extract(data, filename=path.name)  # no OCR client: scanned files are skipped
        ext = path.suffix.lower().lstrip(".")
        file_type = {"htm": "html"}.get(ext, ext)
        summary = {
            "fileIndex": len(documents),
            "fileType": file_type,
            "sizeBytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "outcome": extracted.outcome,
            "chunks": 0,
        }
        documents.append(summary)
        if extracted.outcome != "ok":
            continue
        document_id = str(uuid.uuid4())
        summary["documentId"] = document_id
        summary["fileName"] = path.name  # used in Cosmos records; dropped from the canary record
        for chunk in chunker.chunk(extracted.sections):
            if len(chunks) >= target:
                break
            chunks.append(
                CorpusChunk(
                    document_id,
                    summary["fileIndex"],
                    chunk.index,
                    chunk.text,
                    chunk.heading,
                    chunk.location,
                    chunk.token_count,
                )
            )
            summary["chunks"] += 1
    return documents, chunks, chunker.version


def library_record(library_id: str, now: str) -> dict[str, Any]:
    return {
        "id": library_id,
        "libraryId": library_id,
        "kind": "shared",
        "name": f"canary {now}",
        "description": "Phase 0 canary library. Safe to delete.",
        "autoAcceptTagProposals": False,
        "tagVocabulary": {},
        "stats": {},
        "embeddingModel": EMBEDDING_MODEL,
        "embeddingDimensions": 3072,
        "status": "active",
        "createdAt": now,
        "createdBy": "scripts/canary.py",
        "updatedAt": now,
    }


def document_record(library_id: str, doc: dict[str, Any], now: str, chunking_version: str) -> dict[str, Any]:
    document_id = doc["documentId"]
    return {
        "id": document_id,
        "documentId": document_id,
        "libraryId": library_id,
        "fileName": doc["fileName"],
        "fileType": doc["fileType"],
        "sizeBytes": doc["sizeBytes"],
        "sha256": doc["sha256"],
        "blobPath": None,
        "status": "ready",
        "activeGeneration": 1,
        "pendingGeneration": None,
        "uploadedAt": now,
        "uploadedBy": "scripts/canary.py",
        "tags": [],
        "facets": [],
        "extraction": {"chunkingVersion": chunking_version},
        "chunkCount": doc["chunks"],
        "updatedAt": now,
    }


def chunk_record(
    library_id: str, chunk: CorpusChunk, doc: dict[str, Any], embedding: list[float], now: str, chunking_version: str
) -> dict[str, Any]:
    return {
        "id": chunk_id(chunk.document_id, 1, chunk.chunk_index),
        "libraryId": library_id,
        "documentId": chunk.document_id,
        "generation": 1,
        "chunkIndex": chunk.chunk_index,
        "contentKind": "text",
        "text": chunk.text,
        "heading": chunk.heading,
        "location": chunk.location or {},
        "imageRef": None,
        "imageMeta": None,
        "fileName": doc["fileName"],
        "fileType": doc["fileType"],
        "uploadedAt": now,
        "authoredAt": None,
        "author": None,
        "periodStart": None,
        "periodEnd": None,
        "facets": [],
        "tokenCount": chunk.token_count,
        "embedding": embedding,
        "embeddingModel": EMBEDDING_MODEL,
        "chunkingVersion": chunking_version,
        "createdAt": now,
    }


async def run_query(
    container: Any,
    sql: str,
    parameters: list[dict[str, Any]],
    library_id: str,
    measurement: SearchMeasurement,
    throttle: Throttle,
    *,
    scored: bool,
    prefix_partition_key: bool = True,
) -> SearchMeasurement:
    """Run one search. Charge and metrics are those of the attempt that succeeded.

    ``prefix_partition_key=False`` sends the same query with the library only in ``WHERE``,
    to tell whether routing by the hierarchical key's prefix changes how Cosmos runs it.
    """
    from azure.cosmos.exceptions import CosmosHttpResponseError

    tally, fallback = RawTally(), ResponseTally()
    routing = {"partition_key": [library_id]} if prefix_partition_key else {}

    async def attempt() -> list[dict[str, Any]]:
        nonlocal tally, fallback
        tally, fallback = RawTally(), ResponseTally()
        return [
            item
            async for item in container.query_items(
                sql,
                parameters=parameters,
                populate_query_metrics=True,
                populate_index_metrics=True,
                raw_response_hook=tally,
                response_hook=fallback,
                **routing,
            )
        ]

    before = throttle.count
    started = time.perf_counter()
    try:
        items = await throttle.run(attempt)
    except CosmosHttpResponseError as exc:
        measurement.error = f"HTTP {exc.status_code} {(exc.message or '')[:300]}"
        items = []
    measurement.throttled = throttle.count - before
    measurement.milliseconds = round((time.perf_counter() - started) * 1000, 1)
    if tally.responses:
        charge, responses, metrics, saw_metrics = (
            tally.request_charge,
            tally.responses,
            tally.metrics,
            tally.saw_metrics,
        )
        measurement.index_utilization = tally.index_utilization
    else:  # the pipeline hook saw nothing: fall back, and say so in the record
        charge, responses, metrics, saw_metrics = (
            fallback.request_charge,
            fallback.pages,
            fallback.metrics,
            fallback.saw_metrics,
        )
        measurement.measured_by = "response_hook"
    measurement.request_charge = round(charge, 2)
    measurement.http_responses = responses
    if saw_metrics:
        measurement.retrieved_document_count = metrics.get("retrievedDocumentCount")
        measurement.output_document_count = metrics.get("outputDocumentCount")
    if not measurement.error and responses == 0:
        measurement.error = "no responses reached either hook; charges unmeasured"
    measurement.result_ids = [item["id"] for item in items]
    if scored:
        measurement.null_scores = sum(1 for item in items if item.get("score") is None)
    return measurement


async def wait_for_index(
    container: Any,
    library_id: str,
    query_vector: list[float],
    loaded: int,
    throttle: Throttle,
    minutes: float,
    *,
    sleep: Callable[[float], Any] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Poll one vector search until it stops reading every chunk, or time runs out.

    DiskANN indexing is asynchronous (Microsoft's vector-search documentation); the first live
    run searched 30 seconds after loading and every search scanned. Each poll also runs the
    query without the partition-key prefix, so a routing effect shows up in the record.
    """
    query = render_search(library_id, to_float16_query(query_vector), [], SearchFilters(), K)
    check_query(query.sql)
    sql = scored_variant(query.sql)
    limit = MAX_RETRIEVED_FRACTION * loaded
    start = clock()
    polls: list[dict[str, Any]] = []

    def summary(m: SearchMeasurement, with_index: bool) -> dict[str, Any]:
        out = {"requestCharge": m.request_charge, "retrieved": m.retrieved_document_count, "error": m.error}
        return out | ({"indexUtilization": m.index_utilization} if with_index else {})

    while True:
        n = len(polls)
        prefix = await run_query(
            container, sql, query.parameters, library_id, SearchMeasurement("index-poll", n), throttle, scored=True
        )
        cross = await run_query(
            container,
            sql,
            query.parameters,
            library_id,
            SearchMeasurement("index-poll-no-prefix", n),
            throttle,
            scored=True,
            prefix_partition_key=False,
        )
        elapsed = clock() - start
        engaged = prefix.retrieved_document_count is not None and prefix.retrieved_document_count < limit
        done = engaged or elapsed >= minutes * 60
        polls.append(
            {
                "seconds": round(elapsed),
                "prefix": summary(prefix, n == 0 or done),
                "noPrefix": summary(cross, n == 0 or done),
            }
        )
        log(
            f"  index poll {n + 1} at {elapsed:.0f}s: {prefix.request_charge} RU, retrieved"
            f" {prefix.retrieved_document_count} (without prefix: {cross.request_charge} RU,"
            f" retrieved {cross.retrieved_document_count})"
        )
        if done:
            return {"engaged": engaged, "secondsToEngage": round(elapsed) if engaged else None,
                    "waitMinutes": minutes, "retrievedLimit": limit, "polls": polls}  # fmt: skip
        await sleep(INDEX_POLL_SECONDS)


async def check_definitions(database: Any) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {}
    for name in CONTAINERS:
        expected = json.loads((REPO / "cosmos" / f"{name}.container.json").read_text())
        try:
            actual = await database.get_container_client(name).read()
        except Exception as exc:  # noqa: BLE001 — recorded as the check's observation
            problems[name] = [f"read failed: {type(exc).__name__}: {str(exc)[:200]}"]
            continue
        problems[name] = definition_mismatches(expected, actual)
    return problems


async def write_all(
    container: Any, bodies: Sequence[dict[str, Any]], throttle: Throttle, progress: Callable[[int], None]
) -> float:
    gate = asyncio.Semaphore(WRITE_CONCURRENCY)
    charge = 0.0
    done = 0

    async def one(body: dict[str, Any]) -> None:
        nonlocal charge, done
        tally = ResponseTally()
        async with gate:
            await throttle.run(lambda: container.upsert_item(body, response_hook=tally))
        charge += tally.request_charge
        done += 1
        if done % 100 == 0:
            progress(done)

    await asyncio.gather(*(one(b) for b in bodies))
    return charge


async def crud_probe(
    container: Any, body: dict[str, Any], partition_key: Any, library_id: str, throttle: Throttle
) -> str | None:
    """Write, point-read and query one record. ``None`` on success, else what failed."""
    try:
        await throttle.run(lambda: container.upsert_item(body))
    except Exception as exc:  # noqa: BLE001
        return f"write: {type(exc).__name__}: {str(exc)[:200]}"
    try:
        read = await throttle.run(lambda: container.read_item(body["id"], partition_key=partition_key))
        if read["id"] != body["id"]:
            return "read: wrong record"
    except Exception as exc:  # noqa: BLE001
        return f"read: {type(exc).__name__}: {str(exc)[:200]}"
    try:
        found = [
            item
            async for item in container.query_items(
                "SELECT TOP 1 c.id FROM c WHERE c.libraryId = @libraryId AND c.id = @id",
                parameters=[{"name": "@libraryId", "value": library_id}, {"name": "@id", "value": body["id"]}],
            )
        ]
        if [f["id"] for f in found] != [body["id"]]:
            return "query: record not found"
    except Exception as exc:  # noqa: BLE001
        return f"query: {type(exc).__name__}: {str(exc)[:200]}"
    return None


def log(message: str) -> None:
    print(f"[canary {datetime.now(UTC):%H:%M:%S}] {message}", file=sys.stderr, flush=True)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise CanaryError(f"{name} is not set (see the deployment outputs)")
    return value


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


async def run(args: argparse.Namespace) -> int:
    import httpx
    from azure.cosmos import __version__ as cosmos_version
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    from fli.embedding import AzureOpenAIEmbedder

    cosmos_endpoint = _require_env("COSMOS_ENDPOINT")
    database_name = os.environ.get("COSMOS_DATABASE") or "fli"
    aoai_endpoint = _require_env("AOAI_ENDPOINT")
    deployment = _require_env("AOAI_EMBEDDING_DEPLOYMENT")
    dimensions = int(os.environ.get("AOAI_EMBEDDING_DIMENSIONS") or 3072)
    started = datetime.now(UTC)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    now = started.isoformat().replace("+00:00", "Z")

    log(f"reading corpus under {args.corpus}")
    documents, chunks, chunking_version = await read_corpus(args.corpus, args.target_chunks)
    loaded_docs = [d for d in documents if d["chunks"]]
    log(f"{len(chunks)} chunks from {len(loaded_docs)} of {len(documents)} files ({chunking_version})")
    if len(chunks) < MIN_CHUNKS:
        raise CanaryError(f"the corpus gave {len(chunks)} chunks; add real documents until it gives {MIN_CHUNKS}+")

    if args.queries:
        lines = [ln.strip() for ln in args.queries.read_text().splitlines()]
        questions = [ln for ln in lines if ln and not ln.startswith("#")]
        if len(questions) < QUERIES_PER_KIND:
            raise CanaryError(f"{args.queries} holds {len(questions)} queries; need {QUERIES_PER_KIND}")
        vector_queries = questions[:QUERIES_PER_KIND]
        hybrid_queries = questions[QUERIES_PER_KIND : 2 * QUERIES_PER_KIND]
        if len(hybrid_queries) < QUERIES_PER_KIND:  # fewer than 40: both kinds use the first 20
            hybrid_queries = vector_queries
        query_source = "queries-file"
    else:
        picks = pick_query_sources([(c.text, c.heading) for c in chunks], 2 * QUERIES_PER_KIND)
        sentences = [query_sentence(chunks[i].text, chunks[i].heading) or "" for i in picks]
        vector_queries, hybrid_queries = sentences[:QUERIES_PER_KIND], sentences[QUERIES_PER_KIND:]
        query_source = "corpus-sentences"

    library_id = str(uuid.uuid4())
    by_document = {d["documentId"]: d for d in loaded_docs}
    record: dict[str, Any] = {
        "canary": "phase-0",
        "startedAt": now,
        "libraryId": library_id,
        "gitCommit": _git_commit(),
        "account": {"database": database_name},  # no endpoint: it names the account
        "embedding": {"deployment": deployment, "model": EMBEDDING_MODEL, "dimensions": dimensions},
        "client": {"python": platform.python_version(), "azureCosmos": cosmos_version},
        "thresholds": {
            "targetChunks": args.target_chunks,
            "minChunks": MIN_CHUNKS,
            "k": K,
            "recallFloor": RECALL_FLOOR,
            "maxIndexedSearchRu": MAX_INDEXED_SEARCH_RU,
            "maxRetrievedFraction": MAX_RETRIEVED_FRACTION,
            "querySeed": QUERY_SEED,
            "indexWaitMinutes": args.index_wait_minutes,
            "indexPollSeconds": INDEX_POLL_SECONDS,
        },
        "corpus": {
            "files": [{k: v for k, v in d.items() if k not in ("fileName", "documentId")} for d in documents],
            "chunks": len(chunks),
            "tokens": sum(c.token_count for c in chunks),
            "chunkingVersion": chunking_version,
            "querySource": query_source,
        },
    }

    credential = DefaultAzureCredential()
    try:
        async with CosmosClient(cosmos_endpoint, credential=credential) as cosmos, httpx.AsyncClient() as http:
            database = cosmos.get_database_client(database_name)
            embedder = AzureOpenAIEmbedder(
                endpoint=aoai_endpoint,
                deployment=deployment,
                token_provider=TokenCache(credential),
                http=http,
                dimensions=dimensions,
            )

            log("checking stored container definitions against cosmos/*.json")
            definition_problems = await check_definitions(database)
            for name, problems in definition_problems.items():
                for problem in problems:
                    log(f"definition mismatch in {name}: {problem}")

            log(f"embedding {len(chunks)} chunks")
            t0 = time.perf_counter()
            vectors = await embedder.embed([c.text for c in chunks])
            record["embedding"] |= {
                "seconds": round(time.perf_counter() - t0, 1),
                "promptTokens": embedder.usage.prompt_tokens,
                "throttled": embedder.usage.throttled,
            }

            libraries = database.get_container_client("libraries")
            docs_container = database.get_container_client("documents")
            chunks_container = database.get_container_client("chunks")
            load_throttle, search_throttle = Throttle(), Throttle()

            log(f"step 1: write, read and query one record per container (library {library_id})")
            log(f"  if this run is interrupted: python scripts/canary.py --cleanup {library_id}")
            bodies = [
                chunk_record(library_id, c, by_document[c.document_id], v, now, chunking_version)
                for c, v in zip(chunks, vectors, strict=True)
            ]
            doc_bodies = [document_record(library_id, d, now, chunking_version) for d in loaded_docs]
            crud = {
                "libraries": await crud_probe(
                    libraries, library_record(library_id, now), library_id, library_id, load_throttle
                ),
                "documents": await crud_probe(docs_container, doc_bodies[0], library_id, library_id, load_throttle),
                "chunks": await crud_probe(
                    chunks_container, bodies[0], [library_id, bodies[0]["documentId"]], library_id, load_throttle
                ),
            }
            for name, problem in crud.items():
                if problem:
                    log(f"crud {name}: {problem}")
            if crud["chunks"] is None:
                stored = await chunks_container.read_item(
                    bodies[0]["id"], partition_key=[library_id, bodies[0]["documentId"]]
                )
                stored_vector = stored.get("embedding") or []
                rounded = to_float16_query(vectors[0])
                record["storedVector"] = {
                    "dimensions": len(stored_vector),
                    "maxAbsDiffFromFloat16Rounding": max(
                        (abs(a - b) for a, b in zip(stored_vector, rounded, strict=False)), default=None
                    ),
                }

            log(f"step 2: loading {len(bodies)} chunks, {WRITE_CONCURRENCY} concurrent writes")
            t0 = time.perf_counter()
            doc_charge = await write_all(docs_container, doc_bodies[1:], load_throttle, lambda n: None)
            chunk_charge = await write_all(
                chunks_container, bodies[1:], load_throttle, lambda n: log(f"  {n} chunks written")
            )
            seconds = time.perf_counter() - t0
            record["load"] = {
                "chunks": len(bodies),
                "documents": len(doc_bodies),
                "seconds": round(seconds, 1),
                "chunksPerSecond": round(len(bodies) / seconds, 1),
                "chunkRequestCharge": round(chunk_charge, 1),
                "ruPerChunk": round(chunk_charge / max(1, len(bodies) - 1), 1),
                "documentRequestCharge": round(doc_charge, 1),
                "concurrentWrites": WRITE_CONCURRENCY,
                "throttledRetries": load_throttle.count,
            }
            log(
                f"  loaded in {seconds:.0f}s, {record['load']['ruPerChunk']} RU per chunk,"
                f" {load_throttle.count} throttled retries"
            )

            raw_vectors = await embedder.embed([*vector_queries, *hybrid_queries])
            log(f"waiting for the vector index (up to {args.index_wait_minutes:g} minutes)")
            record["indexWait"] = await wait_for_index(
                chunks_container, library_id, raw_vectors[0], len(bodies), search_throttle, args.index_wait_minutes
            )
            if record["indexWait"]["engaged"]:
                log(f"  index engaged after {record['indexWait']['secondsToEngage']}s")
            else:
                log("  index never engaged; searching anyway so the record shows the scan")

            log("step 3: 20 vector-only and 20 hybrid searches, each with its exact reference")
            vector_ms: list[SearchMeasurement] = []
            exact_vector_ms: list[SearchMeasurement] = []
            hybrid_ms: list[SearchMeasurement] = []
            exact_hybrid_ms: list[SearchMeasurement] = []
            for i, raw in enumerate(raw_vectors[:QUERIES_PER_KIND]):
                query = render_search(library_id, to_float16_query(raw), [], SearchFilters(), K)
                check_query(query.sql)
                flushed = float16_flush_count(raw)
                m = await run_query(
                    chunks_container,
                    scored_variant(query.sql),
                    query.parameters,
                    library_id,
                    SearchMeasurement("vector", i, flushed_subnormals=flushed),
                    search_throttle,
                    scored=True,
                )
                vector_ms.append(m)
                exact_vector_ms.append(
                    await run_query(
                        chunks_container,
                        exact_variant(query.sql),
                        query.parameters,
                        library_id,
                        SearchMeasurement("exact-vector", i),
                        search_throttle,
                        scored=False,
                    )
                )
            for i, raw in enumerate(raw_vectors[QUERIES_PER_KIND:]):
                terms = lexical_terms(hybrid_queries[i])
                query = render_search(library_id, to_float16_query(raw), terms, SearchFilters(), K)
                check_query(query.sql)
                hybrid_ms.append(
                    await run_query(
                        chunks_container,
                        query.sql,
                        query.parameters,
                        library_id,
                        SearchMeasurement(
                            "hybrid", i, lexical_terms=len(terms), flushed_subnormals=float16_flush_count(raw)
                        ),
                        search_throttle,
                        scored=False,
                    )
                )
                exact_hybrid_ms.append(
                    await run_query(
                        chunks_container,
                        exact_variant(query.sql),
                        query.parameters,
                        library_id,
                        SearchMeasurement("exact-hybrid", i),
                        search_throttle,
                        scored=False,
                    )
                )

            log("step 5: one search declaring float32 with an unconverted vector (the E8 trap)")
            trap_query = render_search(library_id, to_float16_query(raw_vectors[0]), [], SearchFilters(), K)
            trap_params = [
                p if p["name"] != "@queryVector" else {"name": "@queryVector", "value": raw_vectors[0]}
                for p in trap_query.parameters
            ]
            trap = await run_query(
                chunks_container,
                float32_trap_variant(scored_variant(trap_query.sql)),
                trap_params,
                library_id,
                SearchMeasurement("float32-trap", 0),
                search_throttle,
                scored=True,
            )
            record["float32Trap"] = asdict(trap) | {
                "scanned": trap.retrieved_document_count is not None
                and trap.retrieved_document_count >= 0.9 * len(bodies),
                "comparedWithVectorQuery0": {
                    "requestCharge": vector_ms[0].request_charge,
                    "retrievedDocumentCount": vector_ms[0].retrieved_document_count,
                },
            }

            checks = evaluate(
                loaded=len(bodies),
                crud=crud,
                definition_problems=definition_problems,
                vector=vector_ms,
                exact_vector=exact_vector_ms,
                hybrid=hybrid_ms,
                exact_hybrid=exact_hybrid_ms,
            )
            record["searches"] = {
                "vector": [asdict(m) for m in vector_ms],
                "exactVector": [asdict(m) for m in exact_vector_ms],
                "hybrid": [asdict(m) for m in hybrid_ms],
                "exactHybrid": [asdict(m) for m in exact_hybrid_ms],
                "summary": {
                    "vectorCharge": _summary([m.request_charge for m in vector_ms]),
                    "hybridCharge": _summary([m.request_charge for m in hybrid_ms]),
                    "exactVectorCharge": _summary([m.request_charge for m in exact_vector_ms]),
                    "vectorRetrieved": _summary([m.retrieved_document_count or 0 for m in vector_ms]),
                    "hybridRetrieved": _summary([m.retrieved_document_count or 0 for m in hybrid_ms]),
                    "exactVectorRetrieved": _summary([m.retrieved_document_count or 0 for m in exact_vector_ms]),
                },
            }
            record["searches"]["throttledRetries"] = search_throttle.count
            # Recorded, not asserted: inside RRF the brute-force flag changed nothing on the first
            # passing run, so this is not a comparison against an exact reference (E24).
            record["searches"]["hybridVsBruteForceVariant"] = agreement_at_k(hybrid_ms, exact_hybrid_ms) | {
                "sameChargeAndRetrieved": sum(
                    (a.request_charge, a.retrieved_document_count) == (b.request_charge, b.retrieved_document_count)
                    for a, b in zip(hybrid_ms, exact_hybrid_ms, strict=True)
                )
            }
            record["checks"] = [asdict(c) for c in checks]
            record["passed"] = all(c.passed for c in checks)

            if not args.keep:
                log("removing the canary library (use --keep to retain it)")
                await purge(database, library_id)
                record["cleanedUp"] = True
            else:
                record["cleanedUp"] = False
    finally:
        await credential.close()

    record["finishedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    args.records_dir.mkdir(parents=True, exist_ok=True)
    out = args.records_dir / f"canary-{stamp}.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    for check in checks:
        log(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {json.dumps(check.observed)}")
    trap_note = "scanned" if record["float32Trap"]["scanned"] else "did NOT scan"
    log(f"float32 trap {trap_note}: {trap.request_charge} RU, retrieved {trap.retrieved_document_count}")
    shown = out.relative_to(REPO) if out.is_relative_to(REPO) else out
    log(f"{'PASSED' if record['passed'] else 'FAILED'}; record written to {shown}")
    if args.keep:
        log(f"kept library {library_id}; remove it with: python scripts/canary.py --cleanup {library_id}")
    return 0 if record["passed"] else 1


async def purge(database: Any, library_id: str) -> int:
    """Delete one canary library's chunks, documents and record. Returns the items deleted."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    gate = asyncio.Semaphore(WRITE_CONCURRENCY)
    throttle = Throttle()
    deleted = 0

    async def delete(container: Any, item_id: str, partition_key: Any) -> None:
        nonlocal deleted
        async with gate:
            try:
                await throttle.run(lambda: container.delete_item(item_id, partition_key=partition_key))
                deleted += 1
            except CosmosResourceNotFoundError:
                pass

    chunks = database.get_container_client("chunks")
    docs = database.get_container_client("documents")
    params = [{"name": "@libraryId", "value": library_id}]
    chunk_keys = [
        item
        async for item in chunks.query_items(
            "SELECT c.id, c.documentId FROM c WHERE c.libraryId = @libraryId",
            parameters=params,
            partition_key=[library_id],
        )
    ]
    await asyncio.gather(*(delete(chunks, c["id"], [library_id, c["documentId"]]) for c in chunk_keys))
    doc_ids = [
        item["id"]
        async for item in docs.query_items(
            "SELECT c.id FROM c WHERE c.libraryId = @libraryId", parameters=params, partition_key=library_id
        )
    ]
    await asyncio.gather(*(delete(docs, d, library_id) for d in doc_ids))
    await delete(database.get_container_client("libraries"), library_id, library_id)
    return deleted


async def cleanup(library_id: str) -> int:
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    try:
        async with CosmosClient(_require_env("COSMOS_ENDPOINT"), credential=credential) as cosmos:
            database = cosmos.get_database_client(os.environ.get("COSMOS_DATABASE") or "fli")
            library = database.get_container_client("libraries")
            from azure.cosmos.exceptions import CosmosResourceNotFoundError

            try:
                body = await library.read_item(library_id, partition_key=library_id)
                if body.get("createdBy") != "scripts/canary.py":
                    raise CanaryError(f"library {library_id} was not created by the canary; refusing to delete it")
            except CosmosResourceNotFoundError:
                log("no library record; deleting any leftover chunks and documents")
            deleted = await purge(database, library_id)
            log(f"deleted {deleted} items")
            return 0
    finally:
        await credential.close()


async def count_corpus(directory: Path, target: int) -> int:
    documents, chunks, version = await read_corpus(directory, target)
    for doc in documents:
        log(f"{doc['fileName'] if 'fileName' in doc else doc['fileIndex']}: {doc['outcome']}, {doc['chunks']} chunks")
    enough = len(chunks) >= MIN_CHUNKS
    stop = " (stopped at the target)" if len(chunks) >= target else ""
    log(f"{len(chunks)} chunks{stop}, {version}: {'enough' if enough else f'need at least {MIN_CHUNKS}'}")
    return 0 if enough else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--corpus", type=Path, help="directory of real documents (pdf, docx, pptx, xlsx, txt, md, html)"
    )
    parser.add_argument("--queries", type=Path, help="optional file of real questions, one per line (20 or 40)")
    parser.add_argument("--target-chunks", type=int, default=TARGET_CHUNKS)
    parser.add_argument(
        "--index-wait-minutes", type=float, default=20, help="how long to wait for the vector index to engage"
    )
    parser.add_argument("--records-dir", type=Path, default=REPO / "records")
    parser.add_argument("--keep", action="store_true", help="keep the canary library instead of deleting it")
    parser.add_argument("--cleanup", metavar="LIBRARY_ID", help="delete a kept canary library, then exit")
    parser.add_argument("--count-only", action="store_true", help="count the corpus's chunks without touching Azure")
    args = parser.parse_args(argv)
    try:
        if args.cleanup:
            return asyncio.run(cleanup(args.cleanup))
        if not args.corpus:
            parser.error("--corpus is required")
        if args.count_only:
            return asyncio.run(count_corpus(args.corpus, args.target_chunks))
        return asyncio.run(run(args))
    except CanaryError as exc:
        log(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
