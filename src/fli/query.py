"""Search SQL for the ``chunks`` container (spec §8), with its invariants enforced.

Every rule here prevents a failure that was measured on live Cosmos (docs/evidence.md):

* ``SELECT TOP n`` on every vector or hybrid query. ``TOP`` must be a literal, so it is
  rendered as a validated positive integer, never a parameter.
* Exactly one ``c.libraryId = @libraryId`` equality per query, never ``IN``/``OR`` across
  libraries (E4: an ``IN`` list across shard values scanned, about 300x the cost) and never
  omitted (E5: no shard value scanned the container, about 220x).
* ``VectorDistance`` declares ``float16``, and the query vector is first rounded to float16
  with subnormals flushed to zero (E8). Declaring ``float32`` against the float16 index makes
  Cosmos scan instead of using the index; a float16 query vector holding a subnormal value
  gets no distance at all. ``VectorDistance`` takes its options object as a literal, so the
  data type is interpolated from a fixed value, never from input.
* ``c.embedding`` is never selected.
* ``filterPriority``, ``searchListSizeMultiplier`` and RRF weights are left at their
  defaults (E13).
* A query ordered by ``RANK`` (hybrid or full-text) is never sent with a partition key. The
  Python SDK then skips the query plan that ranking needs, and Cosmos returns the first
  ``TOP`` items unranked, silently (E27). ``partition_key_for`` decides the routing.

Hybrid queries cannot run on the local Cosmos emulator (it rejects ``FullTextScore``); test
the SQL as text locally and its behaviour against a real account (E21).
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

FLOAT16_MIN_NORMAL = 2.0**-14  # smallest positive normal float16; anything smaller is subnormal
STORED_DTYPE = "float16"  # must equal cosmos/chunks.container.json vectorEmbeddings[0].dataType
MAX_LEXICAL_TERMS = 16
MAX_FETCH = 40

PROJECTION: tuple[str, ...] = (
    "c.id",
    "c.documentId",
    "c.generation",
    "c.chunkIndex",
    "c.contentKind",
    "c.text",
    "c.heading",
    "c.location",
    "c.imageRef",
    "c.imageMeta",
    "c.fileName",
    "c.fileType",
)

_TERM_RE = re.compile(r"[0-9A-Za-z]+")


class QueryError(ValueError):
    pass


_RANK_ORDER = re.compile(r"\bORDER\s+BY\s+RANK\b", re.IGNORECASE)


def partition_key_for(sql: str, library_id: str) -> list[str] | None:
    """How to route a chunks query: by the library prefix of the partition key, or not at all.

    Vector-only queries are routed to the library's partition (one HTTP response, measured
    working: E8, E12). Queries ordered by ``RANK`` must not be: with a partition key the SDK
    sends them without the query plan and Cosmos returns them unranked (E27). They name the
    library in ``WHERE`` only, and the SDK runs its hybrid pipeline.
    """
    return None if _RANK_ORDER.search(sql) else [library_id]


def to_float16_query(vector: Sequence[float]) -> list[float]:
    """Round each value to float16 (round-half-to-even), then set subnormals to 0."""
    out: list[float] = []
    for value in vector:
        x = float(value)
        if not math.isfinite(x):
            raise QueryError("query vector holds a non-finite value")
        try:
            rounded = struct.unpack("<e", struct.pack("<e", x))[0]
        except OverflowError as exc:
            raise QueryError("query vector value outside float16 range") from exc
        out.append(0.0 if abs(rounded) < FLOAT16_MIN_NORMAL else rounded)
    return out


def float16_flush_count(vector: Sequence[float]) -> int:
    """How many values the flush zeroes: worth recording, never silent."""
    count = 0
    for value in vector:
        rounded = struct.unpack("<e", struct.pack("<e", float(value)))[0]
        if rounded != 0.0 and abs(rounded) < FLOAT16_MIN_NORMAL:
            count += 1
    return count


def lexical_terms(query: str) -> list[str]:
    """Lower-cased alphanumeric terms, de-duplicated in order, capped."""
    terms: list[str] = []
    for match in _TERM_RE.finditer(query.lower()):
        term = match.group(0)
        if term not in terms:
            terms.append(term)
        if len(terms) == MAX_LEXICAL_TERMS:
            break
    return terms


@dataclass(frozen=True)
class DateRange:
    start: str  # ISO date, inclusive
    end: str  # ISO date, inclusive for period overlap, exclusive upper bound for timestamps


@dataclass(frozen=True)
class SearchFilters:
    period: DateRange | None = None
    authored_between: DateRange | None = None
    uploaded_between: DateRange | None = None
    authors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    file_types: tuple[str, ...] = ()
    content_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedQuery:
    sql: str
    parameters: list[dict[str, Any]] = field(default_factory=list)


def _any_of(column: str, prefix: str, values: Sequence[str], params: list[dict[str, Any]]) -> str:
    names = []
    for i, value in enumerate(values):
        names.append(f"@{prefix}{i}")
        params.append({"name": f"@{prefix}{i}", "value": value})
    return f"{column} IN ({', '.join(names)})"


def metadata_clauses(filters: SearchFilters, params: list[dict[str, Any]]) -> list[str]:
    """Shared document/chunk filter semantics. Content kind is a chunk-only filter."""
    where: list[str] = []
    for window, start, end, lo, hi in (
        (filters.period, "periodStart", "periodEnd", "pFrom", "pTo"),
        (filters.authored_between, "authoredAt", "authoredAt", "aFrom", "aTo"),
        (filters.uploaded_between, "uploadedAt", "uploadedAt", "uFrom", "uTo"),
    ):
        if window is None:
            continue
        if lo == "pFrom":
            where.append(f"c.{start} <= @{hi} AND c.{end} >= @{lo}")
        else:
            where.append(f"c.{start} >= @{lo} AND c.{end} < @{hi}")
        params.extend([{"name": f"@{lo}", "value": window.start}, {"name": f"@{hi}", "value": window.end}])
    if filters.authors:
        where.append(_any_of("c.author", "au", filters.authors, params))
    for i, tag in enumerate(filters.tags):
        where.append(f"ARRAY_CONTAINS(c.facets, @tag{i})")
        params.append({"name": f"@tag{i}", "value": tag})
    if filters.file_types:
        where.append(_any_of("c.fileType", "ft", filters.file_types, params))
    return where


def render_search(
    library_id: str,
    query_vector_f16: Sequence[float],
    terms: Sequence[str],
    filters: SearchFilters,
    fetch: int,
    document_id: str | None = None,
    generation: int | None = None,
) -> RenderedQuery:
    """The spec §8 query. ``query_vector_f16`` must already be ``to_float16_query`` output."""
    if not library_id:
        raise QueryError("a search names exactly one library")
    if not isinstance(fetch, int) or isinstance(fetch, bool) or not 1 <= fetch <= MAX_FETCH:
        raise QueryError(f"TOP must be an integer from 1 to {MAX_FETCH}")
    if any(v != 0.0 and abs(v) < FLOAT16_MIN_NORMAL for v in query_vector_f16):
        raise QueryError("query vector was not converted with to_float16_query")

    params: list[dict[str, Any]] = [
        {"name": "@libraryId", "value": library_id},
        {"name": "@queryVector", "value": list(query_vector_f16)},
    ]
    where = ["c.libraryId = @libraryId"]
    where.extend(metadata_clauses(filters, params))
    if document_id is not None:
        where.append("c.documentId = @documentId")
        params.append({"name": "@documentId", "value": document_id})
    if generation is not None:
        where.append("c.generation = @generation")
        params.append({"name": "@generation", "value": generation})
    if filters.content_kinds:
        where.append(_any_of("c.contentKind", "ck", filters.content_kinds, params))

    vector = f"VectorDistance(c.embedding, @queryVector, false, {{'dataType': '{STORED_DTYPE}'}})"
    if terms:
        names = []
        for i, term in enumerate(list(terms)[:MAX_LEXICAL_TERMS]):
            names.append(f"@t{i}")
            params.append({"name": f"@t{i}", "value": term})
        order = f"ORDER BY RANK RRF({vector}, FullTextScore(c.text, {', '.join(names)}))"
    else:
        order = f"ORDER BY {vector}"

    sql = f"SELECT TOP {fetch} {', '.join(PROJECTION)} FROM c WHERE {' AND '.join(where)} {order}"
    check_query(sql)
    return RenderedQuery(sql, params)


def check_query(sql: str) -> None:
    """Raise if a rendered chunks query breaks an invariant. Use it in tests and at runtime."""
    match = re.match(
        r"^\s*SELECT\s+TOP\s+(\d+)\s+(.*?)\s+FROM\s+c\s+WHERE\s+(.*)$", sql, flags=re.IGNORECASE | re.DOTALL
    )
    if not match:
        raise QueryError("chunks queries must be SELECT TOP n … FROM c WHERE …")
    projection, rest = match.group(2), match.group(3)
    if re.search(r"\bembedding\b|\*|\bVALUE\s+c\b", projection, flags=re.IGNORECASE):
        raise QueryError("the embedding is never selected")
    if len(re.findall(r"c\.libraryId\s*=\s*@libraryId\b", rest)) != 1:
        raise QueryError("exactly one c.libraryId equality is required")
    if re.search(
        r"c\.libraryId\s+(IN|<>|!=)|\bOR\b[^)]*c\.libraryId|c\.libraryId[^)]*\bOR\b", rest, flags=re.IGNORECASE
    ):
        raise QueryError("never IN, OR or inequality on c.libraryId")
    if "VectorDistance" in rest and f"'dataType': '{STORED_DTYPE}'" not in rest:
        raise QueryError(f"VectorDistance must declare dataType {STORED_DTYPE}")
