"""The ablation's measurements retain drift, repeats and incomplete evidence."""

import copy

import pytest

from scripts.scale_ablation_stats import compare_arms, compare_summaries, summarize_control, summarize_probe


def report(kind="QuantizedFlat", shards=None):
    return {
        "UtilizedVectorIndexes": [
            {"IndexSpec": {"VectorIndexType": kind, "ShardKeys": ["/libraryId"] if shards is None else shards}}
        ]
    }


def rows(charges=((10, 10, 10),), *, shards=None):
    ids = [f"result-{i}" for i in range(10)]
    out = []
    for query, repeats in enumerate(charges):
        for run, ru in enumerate(repeats):
            out.append(
                {
                    "kind": "vector",
                    "query": query,
                    "run": run,
                    "request_charge": ru,
                    "retrieved_document_count": 50,
                    "milliseconds": 100,
                    "result_ids": ids.copy(),
                    "index_utilizations": [report(shards=shards)],
                    "http_responses": 1,
                    "error": None,
                }
            )
        out.append(
            {
                "kind": "exact",
                "query": query,
                "run": 0,
                "request_charge": 100,
                "retrieved_document_count": 1000,
                "milliseconds": 200,
                "result_ids": ids.copy(),
                "error": None,
            }
        )
    return out


def summary(charges=((10, 10, 10),)):
    return summarize_probe(rows(charges), expected_queries=len(charges))


def test_cost_uses_per_question_repeat_medians_rather_than_pooling_calls():
    result = summary(((1, 2, 100), (4, 5, 6)))
    assert result["ruMedian"] == 3.5
    assert result["ruRunMedians"] == [2.5, 3.5, 53.0]
    assert result["ruRunRange"] == [2.5, 53.0]
    assert result["complete"] and result["recallAtFloor"]


def test_reports_every_index_from_every_response_without_requiring_sharding():
    raw = rows(shards=[])
    raw[0]["http_responses"] = 2
    raw[0]["index_utilizations"].append(report("DiskANN", []))
    result = summarize_probe(raw, expected_queries=1)
    assert result["indexReportsComplete"] and result["complete"]
    assert result["vectorIndexes"] == [
        {"type": "DiskANN", "shardKeys": [], "occurrences": 1},
        {"type": "QuantizedFlat", "shardKeys": [], "occurrences": 3},
    ]


def test_exact_document_reads_are_reported_without_requiring_an_exact_index():
    raw = rows()
    result = summarize_probe(raw, expected_queries=1)
    assert result["complete"] and result["recallAtFloor"]
    assert result["exactRetrievedMedian"] == 1000
    diagnostics = result["exactIndexDiagnostics"]
    assert not diagnostics["indexReportsComplete"]
    assert diagnostics["vectorIndexes"] == []
    assert diagnostics["indexHeadersMissing"] == 1


@pytest.mark.parametrize("fault", ["missing-repeat", "duplicate", "error", "missing-reference", "bad-result"])
def test_partial_and_failed_measurements_cannot_pass(fault):
    raw = rows()
    if fault == "missing-repeat":
        raw.pop(1)
    elif fault == "duplicate":
        raw[1] = copy.deepcopy(raw[0])
    elif fault == "error":
        raw[1]["error"] = "HTTP 500"
    elif fault == "missing-reference":
        raw.pop()
    else:
        raw[1]["result_ids"][1] = raw[1]["result_ids"][0]
    result = summarize_probe(raw, expected_queries=1)
    assert not result["complete"]
    assert not compare_summaries(summary(), result)["ruFlat"]


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -1, True])
def test_missing_or_invalid_metrics_are_not_treated_as_zero(bad):
    raw = rows()
    raw[0]["retrieved_document_count"] = bad
    result = summarize_probe(raw, expected_queries=1)
    assert not result["metricsComplete"] and not result["complete"]


def test_missing_one_response_index_is_detected_even_when_first_report_is_valid():
    raw = rows()
    raw[1]["http_responses"] = 2
    result = summarize_probe(raw, expected_queries=1)
    assert result["expectedIndexResponses"] == 4
    assert result["reportedIndexResponses"] == 3
    assert not result["indexReportsComplete"] and not result["complete"]


def test_an_explicit_missing_report_or_empty_utilized_list_is_not_index_evidence():
    for bad_report in (None, {}, {"UtilizedVectorIndexes": []}):
        raw = rows()
        raw[1]["index_utilizations"].append(bad_report)
        raw[1]["http_responses"] = 2
        result = summarize_probe(raw, expected_queries=1)
        assert not result["indexReportsComplete"]


def test_every_repeat_contributes_to_recall_without_averaging_away_a_bad_repeat():
    raw = rows()
    raw[1]["result_ids"][0] = "different"
    result = summarize_probe(raw, expected_queries=1)
    assert result["recallAt10RunMeans"] == [1.0, 0.9, 1.0]
    assert result["recallAt10Mean"] == pytest.approx(0.966667)
    assert result["recallComparisons"] == 3
    assert not result["recallAtFloor"]
    assert not compare_arms(summary(), result)["unsplitNoMoreExpensiveWithinTolerance"]


def test_numerical_drift_is_retained_even_when_within_the_acceptance_band():
    result = compare_summaries(summary(), summary(((10.1, 10.1, 10.1),)))
    assert result["ruDelta"] == 0.1
    assert result["ruDriftPct"] == 1.0
    assert result["ruAbsoluteDriftPct"] == 1.0
    assert result["ruFlat"]


def test_repeat_ranges_distinguish_growth_from_a_noisy_median():
    result = compare_summaries(summary(((9, 10, 12),)), summary(((12, 13, 14),)))
    assert result["ruDriftPct"] == 30.0
    assert not result["ruWithinTolerance"]
    assert not result["ruGrowthBeyondRepeatRanges"]
    result = compare_summaries(summary(((9, 10, 12),)), summary(((15, 16, 17),)))
    assert result["ruGrowthBeyondRepeatRanges"]


def test_lower_unsplit_cost_is_acceptable_but_not_called_equal():
    result = compare_arms(summary(), summary(((5, 5, 5),)))
    assert result["ruDelta"] == -5.0
    assert result["ruDriftPct"] == -50.0
    assert result["ruAbsoluteDriftPct"] == 50.0
    assert result["unsplitNoMoreExpensiveWithinTolerance"]
    assert not result["ruWithinTolerance"]


def test_control_requires_three_distinct_repeats_and_reports_their_range():
    raw = [r for r in rows(((10, 11, 12),)) if r["kind"] == "vector"]
    result = summarize_control(raw)
    assert result["complete"]
    assert result["ruMedian"] == 11.0
    assert result["ruRunRange"] == [10, 12]
    assert "recallAtFloor" not in result
    assert not summarize_control(raw[:1])["complete"]


def test_indexless_control_is_complete_with_index_absence_reported_separately():
    raw = [r for r in rows(((10, 11, 12),)) if r["kind"] == "vector"]
    for row in raw:
        row["index_utilizations"] = []
    result = summarize_control(raw)
    assert result["complete"]
    assert not result["indexReportsComplete"]
    assert result["indexHeadersMissing"] == 3
    assert result["indexHeadersPresent"] == 0
    assert result["vectorIndexes"] == []
    assert result["indexReportsWithoutUtilizedVectorIndex"] == 0


def test_present_index_header_without_utilized_vector_index_differs_from_missing_header():
    raw = [r for r in rows(((10, 11, 12),)) if r["kind"] == "vector"]
    for row in raw:
        row["index_utilizations"] = [{"UtilizedVectorIndexes": [], "PotentialVectorIndexes": []}]
    result = summarize_control(raw)
    assert result["complete"]
    assert not result["indexReportsComplete"]
    assert result["indexHeadersMissing"] == 0
    assert result["indexHeadersPresent"] == 3
    assert result["indexReportsWithoutUtilizedVectorIndex"] == 3


def test_empty_probe_and_control_are_explicitly_incomplete():
    result = summarize_probe([])
    assert not result["complete"] and result["ruMedian"] is None
    assert result["recallAt10RunMeans"] == [None, None, None]
    assert not summarize_control([])["complete"]
