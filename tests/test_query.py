"""The search SQL and its invariants (spec §8; docs/evidence.md E4, E5, E8, E13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fli.query import (
    FLOAT16_MIN_NORMAL,
    STORED_DTYPE,
    DateRange,
    QueryError,
    SearchFilters,
    check_query,
    float16_flush_count,
    lexical_terms,
    render_search,
    to_float16_query,
)

REPO = Path(__file__).resolve().parents[1]
VECTOR = to_float16_query([0.1, -0.2, 0.3])


def test_float16_rounding_and_subnormal_flush():
    out = to_float16_query([0.1, 1e-6, -3e-5, 2.0**-14, 0.0])
    assert out[0] == pytest.approx(0.1, abs=1e-4) and out[0] != 0.1  # rounded to float16 precision
    assert out[1] == 0.0 and out[2] == 0.0  # subnormal in float16: flushed
    assert out[3] == FLOAT16_MIN_NORMAL  # smallest normal: kept
    assert float16_flush_count([0.1, 1e-6, -3e-5, 0.0]) == 2


def test_float16_conversion_rejects_unrepresentable_values():
    with pytest.raises(QueryError):
        to_float16_query([float("nan")])
    with pytest.raises(QueryError):
        to_float16_query([1e6])


def test_stored_dtype_matches_the_container_definition():
    container = json.loads((REPO / "cosmos" / "chunks.container.json").read_text())
    assert container["vectorEmbeddingPolicy"]["vectorEmbeddings"][0]["dataType"] == STORED_DTYPE


def test_hybrid_query_shape():
    q = render_search("lib-1", VECTOR, ["margin", "emea"], SearchFilters(), fetch=16)
    assert q.sql.startswith("SELECT TOP 16 ")
    assert "WHERE c.libraryId = @libraryId " in q.sql
    assert (
        "ORDER BY RANK RRF(VectorDistance(c.embedding, @queryVector, false, {'dataType': 'float16'}), "
        "FullTextScore(c.text, @t0, @t1))" in q.sql
    )
    assert "embedding," not in q.sql.split(" FROM ")[0]
    assert "filterPriority" not in q.sql and "searchListSizeMultiplier" not in q.sql


def test_vector_only_when_no_terms():
    q = render_search("lib-1", VECTOR, [], SearchFilters(), fetch=8)
    assert "RRF" not in q.sql and q.sql.endswith(
        "ORDER BY VectorDistance(c.embedding, @queryVector, false, {'dataType': 'float16'})"
    )


def test_filters_render_as_parameters():
    filters = SearchFilters(
        period=DateRange("2025-10-01", "2025-12-31"),
        authors=("Jane Doe",),
        tags=("region=EMEA", "topic=margin"),
        file_types=("pdf", "pptx"),
        content_kinds=("image",),
    )
    q = render_search("lib-1", VECTOR, ["margin"], filters, fetch=8)
    assert "c.periodStart <= @pTo AND c.periodEnd >= @pFrom" in q.sql
    assert "ARRAY_CONTAINS(c.facets, @tag0) AND ARRAY_CONTAINS(c.facets, @tag1)" in q.sql
    assert (
        "c.author IN (@au0)" in q.sql and "c.fileType IN (@ft0, @ft1)" in q.sql and "c.contentKind IN (@ck0)" in q.sql
    )
    values = {p["name"]: p["value"] for p in q.parameters}
    assert values["@tag0"] == "region=EMEA" and values["@pFrom"] == "2025-10-01" and values["@libraryId"] == "lib-1"
    assert "EMEA" not in q.sql and "Jane" not in q.sql  # values never interpolated


@pytest.mark.parametrize("fetch", [0, -1, 41, 8.0, True, "8"])
def test_top_must_be_a_bounded_integer(fetch):
    with pytest.raises(QueryError):
        render_search("lib-1", VECTOR, [], SearchFilters(), fetch=fetch)  # type: ignore[arg-type]


def test_a_library_is_required():
    with pytest.raises(QueryError):
        render_search("", VECTOR, [], SearchFilters(), fetch=8)


def test_an_unconverted_query_vector_is_refused():
    with pytest.raises(QueryError, match="to_float16_query"):
        render_search("lib-1", [0.1, 1e-6], [], SearchFilters(), fetch=8)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT c.id FROM c WHERE c.libraryId = @libraryId",  # no TOP
        "SELECT TOP 8 c.id, c.embedding FROM c WHERE c.libraryId = @libraryId",  # embedding selected
        "SELECT TOP 8 * FROM c WHERE c.libraryId = @libraryId",
        "SELECT TOP 8 c.id FROM c WHERE c.fileType = @ft0",  # no library
        "SELECT TOP 8 c.id FROM c WHERE c.libraryId IN (@a, @b)",  # several libraries
        "SELECT TOP 8 c.id FROM c WHERE c.libraryId = @libraryId OR c.libraryId = @other",
        "SELECT TOP 8 c.id FROM c WHERE c.libraryId = @libraryId "
        "ORDER BY VectorDistance(c.embedding, @queryVector, false, {'dataType': 'float32'})",  # wrong dtype: scans
    ],
)
def test_check_query_rejects_each_broken_invariant(sql):
    with pytest.raises(QueryError):
        check_query(sql)


def test_lexical_terms_are_lowercase_deduplicated_and_capped():
    assert lexical_terms("Margin, EMEA margin; Q4-2025") == ["margin", "emea", "q4", "2025"]
    assert len(lexical_terms(" ".join(f"w{i}" for i in range(40)))) == 16
