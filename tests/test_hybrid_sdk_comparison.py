"""Offline safeguards for the new SDK comparison; no live rank/latency claims."""

import copy
import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from fli.ingestion import profile
from fli.query import SearchFilters, render_search, to_float16_query
from scripts import hybrid_sdk_comparison as h


def query():
    return {"vector": [2**-20, 0.123456] + [0.0] * 3070, "terms": ["qualifier", "table"]}


def test_schedule_is_complete_and_counterbalanced():
    cases = [f"hq-{i:02}" for i in range(1, 49)]
    schedule = h.schedule(cases)
    assert len(schedule) == len({r["id"] for r in schedule}) == 576
    assert set(Counter((r["sdk"], r["route"]) for r in schedule).values()) == {96}
    for repeat in (1, 2):
        for position in range(6):
            rows = [r for r in schedule if r["repeat"] == repeat][position::6]
            assert set(Counter((r["sdk"], r["route"]) for r in rows).values()) == {8}
    for case in cases:
        first = [(r["sdk"], r["route"]) for r in schedule if r["case"] == case and r["repeat"] == 1]
        second = [(r["sdk"], r["route"]) for r in schedule if r["case"] == case and r["repeat"] == 2]
        assert first == second[::-1]


def test_hybrid_routes_share_exact_sql_projection_parameters():
    prefix = h.make_command("case", "hybrid-prefix", "owned", query(), identifier="prefix")
    where = h.make_command("case", "hybrid-where", "owned", query(), identifier="where")
    assert prefix["sql"] == where["sql"]
    assert prefix["parameters"] == where["parameters"]
    assert prefix["partitionKey"] == ["owned"]
    assert where["partitionKey"] is None
    expected = render_search("owned", to_float16_query(query()["vector"]), query()["terms"], SearchFilters(), 16)
    assert prefix["sql"] == expected.sql
    assert prefix["parameters"] == expected.parameters
    vector = next(p["value"] for p in prefix["parameters"] if p["name"] == "@queryVector")
    assert vector[0] == 0
    assert vector[1] != query()["vector"][1]
    assert "c.embedding," not in prefix["sql"].split("FROM")[0]


def test_exact_reference_has_complete_key_and_brute_force_score():
    command = h.make_command(
        "case", "vector-prefix", "owned", query(), identifier="exact", document="document", reference_query=True
    )
    assert command["partitionKey"] == ["owned", "document"]
    assert "VectorDistance(c.embedding, @queryVector, true, {'dataType': 'float16'}) AS score" in command["sql"]
    assert "false" not in command["sql"]
    assert "c.documentId = @documentId" in command["sql"]
    assert "c.generation = @generation" in command["sql"]
    assert "RRF" not in command["sql"]
    with pytest.raises(ValueError, match="full document"):
        h.make_command("case", "hybrid-prefix", "owned", query(), identifier="bad", reference_query=True)


@pytest.fixture
def scope():
    manifest = h.read(h.REPO / "docs/testing/hybrid-quality/manifest.json")
    recipe = profile("microsoft-indexer-layout-v1")
    recipe["embedding"]["modelRevision"] = "1"
    documents, chunks, sources = [], [], {}
    for index, source in enumerate(manifest["documents"]):
        doc_id = f"private-document-{index}"
        sources[source["id"]] = {"documentId": doc_id}
        doc = {
            "id": doc_id,
            "fileName": source["fileName"],
            "sha256": source["sha256"],
            "status": "ready",
            "generation": 1,
            "activeGeneration": 1,
            "chunkCount": 3,
            "ingestionJob": {"profile": recipe, "generation": 1, "sourceSha256": source["sha256"]},
        }
        for key, value in source.get("metadata", {}).items():
            doc[key] = [{**tag, "source": "user"} for tag in value] if key == "tags" else value
        documents.append(doc)
        for part in range(3):
            chunks.append(
                {
                    "id": f"{doc_id}-{part}",
                    "documentId": doc_id,
                    "generation": 1,
                    "chunkIndex": part,
                    "contentHash": "opaque",
                    "_etag": "opaque",
                    "embeddingModel": "text-embedding-3-large",
                }
            )
    snapshot = {
        "library": {"id": "owned", "createdBy": "run", "status": "active", "ingestionProfile": recipe},
        "documents": documents,
        "chunks": chunks,
    }
    owner = {
        "createdByThisRun": True,
        "readyCompleted": True,
        "library": {"id": "owned"},
        "runId": "run",
        "sources": sources,
        "contract": {"protectedLibraryIds": ["protected"]},
    }
    return {"libraryId": "owned"}, snapshot, owner, ["protected"]


def test_scope_freezes_actual_inventory_not_previous_count(scope):
    assert len(scope[1]["chunks"]) == 24
    assert len(h.validate_scope(*scope)) == 8


@pytest.mark.parametrize("mutation", ["protected", "owner", "generation", "missing", "oversized", "hash"])
def test_scope_guards(scope, mutation):
    config, snapshot, owner, protected = copy.deepcopy(scope)
    if mutation == "protected":
        protected.append("owned")
    elif mutation == "owner":
        snapshot["library"]["createdBy"] = "foreign"
    elif mutation == "generation":
        snapshot["documents"][0]["generation"] = 2
    elif mutation == "missing":
        snapshot["chunks"].pop()
    elif mutation == "oversized":
        snapshot["documents"][0]["chunkCount"] = 17
    elif mutation == "hash":
        snapshot["documents"][0]["sha256"] = "different"
    with pytest.raises(ValueError):
        h.validate_scope(config, snapshot, owner, protected)


def test_vector_cutoff_ties_allow_alternative_equal_rank_not_missing_strict_winner():
    scores = {"best": 0.9, "tie-a": 0.8, "tie-b": 0.8, "low": 0.1}
    assert h.vector_accuracy(["best", "tie-b"], scores, 2) == {
        "tieAwareRecall": 1.0,
        "descendingExactScoreOrder": True,
    }
    assert h.vector_accuracy(["tie-a", "tie-b"], scores, 2)["tieAwareRecall"] == 0.5
    assert h.vector_accuracy(["tie-a", "best"], scores, 2)["descendingExactScoreOrder"] is False


def exact_rows():
    return [
        {
            "status": "complete",
            "case": f"q{i}",
            "documentId": "doc",
            "rows": [
                {"id": "best", "documentId": "doc", "generation": 1, "score": 0.9},
                {"id": "next", "documentId": "doc", "generation": 1, "score": 0.8},
            ],
        }
        for i in range(48)
    ]


def test_exact_reference_requires_every_case_and_complete_unique_descending_rows():
    expected = {"chunks": [{"id": "best"}, {"id": "next"}]}
    assert len(h.exact_scores(exact_rows(), expected)) == 48
    for mutation in ("missing", "duplicate", "ascending", "nonfinite", "foreign"):
        rows = exact_rows()
        if mutation == "missing":
            rows.pop()
        elif mutation == "duplicate":
            rows[0]["rows"].append(rows[0]["rows"][0])
        elif mutation == "ascending":
            rows[0]["rows"].reverse()
        elif mutation == "nonfinite":
            rows[0]["rows"][0]["score"] = float("nan")
        else:
            rows[0]["rows"][0]["documentId"] = "foreign"
        with pytest.raises(ValueError):
            h.exact_scores(rows, expected)


def event(status=200, request=None, response=None, method="POST"):
    return {
        "httpStatus": status,
        "requestHeaders": request or {},
        "responseHeaders": response or {},
        "method": method,
        "requestBodySha256": "body",
        "url": "https://private.invalid/query",
        "totalMs": 5,
    }


def test_wire_plan_fallback_scope_and_retry_facts_remain_distinct():
    rows = [
        event(400, response={"x-ms-substatus": "1004", "x-ms-request-charge": "1"}),
        event(request={"x-ms-cosmos-is-query-plan-request": "True"}, response={"x-ms-request-charge": "2"}),
        event(request={"x-ms-documentdb-partitionkeyrangeid": "range-a"}, response={"x-ms-request-charge": "3"}),
        event(429, response={"x-ms-retry-after-ms": "100", "x-ms-request-charge": "0"}),
        event(304, method="GET"),
    ]
    result = h.logical_wire_summary(rows)
    assert result["wireRequests"] == 5
    assert result["queryPlanRequests"] == 1
    assert result["dataQueryRequests"] == 3
    assert result["queryPlanFallback4001004"] == result["visibleThrottleResponses"] == 1
    assert result["visibleDataRangeIdsPrivate"] == ["range-a"]
    assert result["knownWireRequestCharge"] == 6
    assert result["totalBilledRu"] is result["sdkTotalRetries"] is None


def test_missing_range_is_unknown_and_header_capture_excludes_credentials():
    assert h.logical_wire_summary([event()])["successfulDataResponsesWithoutRange"] == 1
    selected = h.selected_headers({"Authorization": "secret", "Cookie": "secret", "x-ms-start-epk": "bound"})
    assert selected == {"x-ms-start-epk": "bound"}


def test_document_coverage_is_not_region_or_negative_answer_quality():
    known = {"a": {"documentId": "doc"}}
    mapping = {"doc": "source"}
    task = {"expectedSourceIds": ["source", "other"], "answerable": True}
    assert h.document_coverage(["a"], known, mapping, task) == 0.5
    assert h.document_coverage(["a"], known, mapping, {**task, "answerable": False}) is None


def test_reference_hash_and_exclusive_receipts(tmp_path):
    path = tmp_path / "receipt.json"
    h.save(path, {"public": 1})
    ref = h.reference(path)
    assert h.checked(ref) == path.resolve()
    with pytest.raises(FileExistsError):
        h.save(path, {"public": 2})
    path.write_text("{}")
    with pytest.raises(ValueError, match="binding"):
        h.checked(ref)


def test_expired_token_rejected_locally_without_printing_credentials(tmp_path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"accessToken": "private-secret", "expires_on": datetime.now(UTC).timestamp()}))
    with pytest.raises(ValueError, match="bounded window") as error:
        h.FileCredential(path)
    assert "private-secret" not in str(error.value)


def test_embedding_cache_rejects_revision_drift_before_embedding_use(tmp_path):
    cache = {
        "status": "complete",
        "tasksSha256": h.sha(h.REPO / "docs/testing/hybrid-quality/tasks.json"),
        "deploymentBinding": {"model": {"format": "OpenAI", "name": "text-embedding-3-large", "version": "2"}},
    }
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(cache))
    with pytest.raises(ValueError, match="model differs"):
        h.validate_cache(path, [])


def test_frozen_benchmark_hash_guard():
    controls = {
        "tasksSha256": h.sha(h.REPO / "docs/testing/hybrid-quality/tasks.json"),
        "manifestSha256": h.sha(h.REPO / "docs/testing/hybrid-quality/manifest.json"),
    }
    h.verify_benchmark(controls)
    with pytest.raises(ValueError, match="benchmark files changed"):
        h.verify_benchmark({**controls, "tasksSha256": "different"})


def test_missing_charge_is_unknown_but_observed_zero_is_known():
    assert h.logical_wire_summary([event()])["knownWireRequestCharge"] is None
    assert h.logical_wire_summary([event(response={"x-ms-request-charge": "0"})])["knownWireRequestCharge"] == 0
    assert h.logical_wire_summary([event(method="GET")])["successfulDataResponses"] == 0


def test_failed_pair_has_no_ranking_agreement_or_recall_claim():
    result = h.pair_agreement(None, list(range(16)))
    assert result["bothCompleteTop16"] is False
    assert result["top16SetOverlap"] is result["exactOrderAgreement"] is None
    assert h.pair_agreement([], [])["exactOrderAgreement"] is None
    assert h.pair_agreement(list(range(16)), list(range(16)))["top16SetOverlap"] == 1


def test_nullable_aggregates_keep_missing_measurements_distinct_from_zero():
    for aggregate in (h.optional_sum, h.optional_mean, h.optional_median):
        assert aggregate([None, None]) is None
        assert aggregate([None, 0]) == 0
        assert aggregate([None, 2]) == 2
