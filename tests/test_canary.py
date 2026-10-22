"""The canary's pure logic. Its Azure behaviour is tested only by running it (build-plan Phase 0)."""

from __future__ import annotations

import pytest

from inherited.query import QueryError, SearchFilters, check_query, render_search, to_float16_query
from scripts.canary import (
    MAX_INDEXED_SEARCH_RU,
    QUERIES_PER_KIND,
    CanaryError,
    SearchMeasurement,
    chunk_id,
    definition_mismatches,
    evaluate,
    exact_variant,
    float32_trap_variant,
    parse_query_metrics,
    pick_query_sources,
    query_sentence,
    recall_at_k,
    scored_variant,
)

VECTOR = to_float16_query([0.01 * ((i % 7) - 3) for i in range(3072)])


def vector_sql() -> str:
    return render_search("lib-1", VECTOR, [], SearchFilters(), 10).sql


def hybrid_sql() -> str:
    return render_search("lib-1", VECTOR, ["gross", "margin"], SearchFilters(), 10).sql


def test_parse_query_metrics():
    header = "totalExecutionTimeInMs=1.5;retrievedDocumentCount=42;outputDocumentCount=10;bad;x=y"
    assert parse_query_metrics(header) == {
        "totalExecutionTimeInMs": 1.5,
        "retrievedDocumentCount": 42.0,
        "outputDocumentCount": 10.0,
    }
    assert parse_query_metrics(None) == {}


def test_recall_at_k():
    exact = [str(i) for i in range(10)]
    assert recall_at_k(exact, exact) == 1.0
    assert recall_at_k([*exact[:9], "x"], exact) == 0.9
    assert recall_at_k(["a"], []) == 0.0


def test_scored_variant_keeps_the_spec_query_and_adds_only_the_distance():
    sql = vector_sql()
    check_query(sql)
    scored = scored_variant(sql)
    assert scored.startswith(
        "SELECT TOP 10 VectorDistance(c.embedding, @queryVector, false, {'dataType': 'float16'}) AS score, c.id,"
    )
    assert scored.endswith(sql.split(" FROM c ", 1)[1])
    assert " c.embedding," not in scored.split(" FROM c ")[0].replace("VectorDistance(c.embedding,", "")


@pytest.mark.parametrize("make", [vector_sql, hybrid_sql])
def test_exact_variant_forces_brute_force_and_still_passes_the_invariants(make):
    exact = exact_variant(make())
    assert "VectorDistance(c.embedding, @queryVector, true, {'dataType': 'float16'})" in exact
    assert ", false," not in exact
    check_query(exact)


def test_exact_variant_of_scored_query_replaces_both_distances():
    exact = exact_variant(scored_variant(vector_sql()))
    assert exact.count(", true,") == 2 and ", false," not in exact


def test_float32_trap_is_rejected_by_the_query_check():
    trap = float32_trap_variant(scored_variant(vector_sql()))
    assert "'float32'" in trap and "'float16'" not in trap
    with pytest.raises(QueryError):
        check_query(
            trap.replace("VectorDistance(c.embedding, @queryVector, false, {'dataType': 'float32'}) AS score, ", "")
        )


def test_chunk_ids_are_deterministic():
    assert chunk_id("doc", 1, 7) == chunk_id("doc", 1, 7) == "doc:1:7"


def test_definition_mismatches_allow_service_additions_and_report_differences():
    expected = {
        "id": "chunks",
        "indexingPolicy": {
            "excludedPaths": [{"path": "/*"}],
            "vectorIndexes": [{"path": "/embedding", "type": "diskANN"}],
        },
    }
    actual = {
        "id": "chunks",
        "_rid": "x",
        "indexingPolicy": {
            "excludedPaths": [{"path": "/*"}, {"path": '/"_etag"/?'}],
            "vectorIndexes": [{"path": "/embedding", "type": "diskANN", "extra": 1}],
        },
    }
    assert definition_mismatches(expected, actual) == []
    actual["indexingPolicy"]["vectorIndexes"][0]["type"] = "flat"
    del actual["indexingPolicy"]["excludedPaths"][0]
    problems = definition_mismatches(expected, actual)
    assert problems == [
        "/indexingPolicy/excludedPaths[/*]: missing",
        "/indexingPolicy/vectorIndexes[/embedding]/type: expected 'diskANN', found 'flat'",
    ]


def test_definition_mismatches_compare_plain_lists_by_position():
    assert definition_mismatches({"paths": ["/libraryId", "/documentId"]}, {"paths": ["/libraryId"]}) == [
        "/paths: expected 2 items, found 1"
    ]


def test_query_sentence_skips_the_heading_and_short_lines():
    text = "Margin bridge\nQ4.\nGross margin in EMEA rose because freight costs fell sharply."
    assert query_sentence(text, "Margin bridge") == "Gross margin in EMEA rose because freight costs fell sharply."
    assert query_sentence("Too short.", None) is None


def test_pick_query_sources_is_deterministic_and_fails_on_a_thin_corpus():
    chunks = [(f"Sentence number {i} has enough words to be a search.", None) for i in range(50)]
    first = pick_query_sources(chunks, 10)
    assert first == pick_query_sources(chunks, 10) and len(set(first)) == 10
    with pytest.raises(CanaryError):
        pick_query_sources(chunks[:5], 10)


def _ms(kind: str, i: int, ids: list[str], charge: float = 20.0, retrieved: float | None = 60.0) -> SearchMeasurement:
    return SearchMeasurement(kind, i, request_charge=charge, retrieved_document_count=retrieved, result_ids=ids)


def _passing(**overrides):
    ids = [str(i) for i in range(10)]
    kwargs = {
        "loaded": 1500,
        "crud": {"libraries": None, "documents": None, "chunks": None},
        "definition_problems": {"libraries": [], "documents": [], "chunks": []},
        "vector": [_ms("vector", i, ids) for i in range(QUERIES_PER_KIND)],
        "exact_vector": [_ms("exact-vector", i, ids, 2000, 1500) for i in range(QUERIES_PER_KIND)],
        "hybrid": [_ms("hybrid", i, ids, 300, 1500) for i in range(QUERIES_PER_KIND)],
        "exact_hybrid": [_ms("exact-hybrid", i, ids, 3000, 1500) for i in range(QUERIES_PER_KIND)],
    }
    kwargs.update(overrides)
    return {c.name: c for c in evaluate(**kwargs)}


def test_evaluate_passes_a_healthy_run_and_does_not_assert_hybrid_cost():
    checks = _passing()
    assert all(c.passed for c in checks.values()), [c for c in checks.values() if not c.passed]


def test_evaluate_fails_a_scan():
    ids = [str(i) for i in range(10)]
    scanned = [_ms("vector", i, ids, 4789, 1500) for i in range(QUERIES_PER_KIND)]
    checks = _passing(vector=scanned)
    assert not checks["index_engaged_charge"].passed and not checks["index_engaged_retrieved"].passed


def test_evaluate_fails_one_expensive_search_and_missing_metrics():
    ids = [str(i) for i in range(10)]
    vector = [_ms("vector", i, ids) for i in range(QUERIES_PER_KIND)]
    vector[3].request_charge = MAX_INDEXED_SEARCH_RU
    vector[4].retrieved_document_count = None
    checks = _passing(vector=vector)
    assert not checks["index_engaged_charge"].passed
    assert not checks["index_engaged_retrieved"].passed


def test_evaluate_fails_low_recall_null_scores_errors_and_a_small_corpus():
    ids = [str(i) for i in range(10)]
    poor = [_ms("vector", i, ids[:8] + ["x", "y"]) for i in range(QUERIES_PER_KIND)]
    poor[0].null_scores = 1
    checks = _passing(vector=poor, loaded=900)
    assert not checks["vector_recall_at_10"].passed and checks["vector_recall_at_10"].observed["mean"] == 0.8
    assert not checks["no_null_scores"].passed
    assert not checks["corpus_large_enough"].passed
    broken = [_ms("hybrid", i, ids) for i in range(QUERIES_PER_KIND)]
    broken[2].error = "HTTP 400"
    broken[2].result_ids = []
    checks = _passing(hybrid=broken)
    assert not checks["searches_executed"].passed and not checks["hybrid_recall_at_10"].passed


def test_evaluate_fails_definition_and_crud_problems():
    checks = _passing(
        crud={"libraries": None, "documents": "write: Forbidden", "chunks": None},
        definition_problems={"libraries": [], "documents": [], "chunks": ["/vectorEmbeddingPolicy: missing"]},
    )
    assert not checks["crud_documents"].passed and checks["crud_chunks"].passed
    assert not checks["container_definitions_match"].passed


def test_deployment_env_restores_mangled_output_names():
    from scripts.deployment_env import exports

    # Names as `az deployment group show` really returns them (observed 2026-10-22).
    outputs = {
        "cosmoS_ENDPOINT": {"type": "String", "value": "https://a.documents.azure.com:443/"},
        "aoaI_EMBEDDING_DIMENSIONS": {"type": "Int", "value": 3072},
        "cosmoS_CONTAINERS": {"type": "Array", "value": ["chunks"]},
        "applicationinsightS_CONNECTION_STRING": {
            "type": "String",
            "value": "InstrumentationKey=x;IngestionEndpoint=y",
        },
    }
    assert exports(outputs) == [
        "export AOAI_EMBEDDING_DIMENSIONS=3072",
        "export APPLICATIONINSIGHTS_CONNECTION_STRING='InstrumentationKey=x;IngestionEndpoint=y'",
        "export COSMOS_ENDPOINT=https://a.documents.azure.com:443/",
    ]


def _throttled(retry_after_ms: str | None = None):
    from azure.cosmos.exceptions import CosmosHttpResponseError

    exc = CosmosHttpResponseError(status_code=429, message="Request rate is large")
    exc.headers = {"x-ms-retry-after-ms": retry_after_ms} if retry_after_ms else {}
    return exc


async def test_throttle_retries_429_honouring_retry_after():
    from scripts.canary import Throttle

    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    failures = [_throttled("250"), _throttled()]

    async def call():
        if failures:
            raise failures.pop(0)
        return "ok"

    throttle = Throttle(sleep=sleep)
    assert await throttle.run(call) == "ok"
    assert throttle.count == 2 and waits[0] == 0.25 and 0.1 <= waits[1] <= 30


async def test_throttle_gives_up_and_never_retries_other_errors():
    from azure.cosmos.exceptions import CosmosHttpResponseError

    from scripts.canary import THROTTLE_ATTEMPTS, Throttle

    async def sleep(seconds: float) -> None:
        pass

    calls = 0

    async def always_throttled():
        nonlocal calls
        calls += 1
        raise _throttled("1")

    with pytest.raises(CosmosHttpResponseError):
        await Throttle(sleep=sleep).run(always_throttled)
    assert calls == THROTTLE_ATTEMPTS

    async def forbidden():
        raise CosmosHttpResponseError(status_code=403, message="Forbidden")

    throttle = Throttle(sleep=sleep)
    with pytest.raises(CosmosHttpResponseError):
        await throttle.run(forbidden)
    assert throttle.count == 0


def test_decode_index_utilization():
    import base64
    import json

    from scripts.canary import decode_index_utilization

    report = {"UtilizedSingleIndexes": [{"IndexSpec": "/libraryId ASC"}]}
    assert decode_index_utilization(base64.b64encode(json.dumps(report).encode()).decode()) == report
    assert decode_index_utilization("not base64 json") == "not base64 json"
    assert decode_index_utilization(None) is None
