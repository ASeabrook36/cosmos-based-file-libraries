import json
from collections import Counter

import pytest

from scripts.scale_probe import (
    CHUNKS_PER_DOCUMENT,
    PROBE_SIZES,
    assignment,
    grade,
    main,
    readiness_library,
    stage_libraries,
    stage_line_range,
    summarize,
    vector_index,
)


def test_layout_is_deterministic_and_sized():
    assert stage_libraries(2) == stage_libraries(2)
    probes = stage_libraries(1)
    assert [lib.size for lib in probes] == list(PROBE_SIZES)
    assert all(1000 <= lib.size <= 5000 for lib in probes)  # below 1,000 Cosmos scans
    stage2 = stage_libraries(2)
    assert len(stage2) == 403 and sum(lib.role == "shared" for lib in stage2) == 3
    assert len(stage_libraries(1)) + len(stage2) + len(stage_libraries(3)) > 1000


def test_line_ranges_follow_each_other_without_overlap():
    ranges = [stage_line_range(s) for s in (1, 2, 3, 4)]
    assert ranges[0].start == 0
    for before, after in zip(ranges, ranges[1:], strict=False):
        assert before.stop == after.start
    assert len(ranges[0]) == sum(PROBE_SIZES)


def test_assignment_deals_every_line_and_groups_documents_of_ten():
    plan = assignment(1)
    assert len(plan) == len(stage_line_range(1))
    per_library = Counter(lib for lib, _doc, _i in plan)
    assert per_library == {lib.library_id: lib.size for lib in stage_libraries(1)}
    per_document = Counter(doc for _lib, doc, _i in plan)
    assert max(per_document.values()) == CHUNKS_PER_DOCUMENT
    assert len({(doc, i) for _lib, doc, i in plan}) == len(plan)  # chunk ids are unique
    assert plan[:50] != sorted(plan[:50])  # shuffled: a library is not a run of neighbours


def test_vector_index_reads_the_shard_report():
    report = {
        "UtilizedVectorIndexes": [{"IndexSpec": {"VectorIndexType": "QuantizedFlat", "ShardKeys": ["/libraryId"]}}]
    }
    assert vector_index(report) == ("QuantizedFlat", ["/libraryId"])
    assert vector_index({"PotentialVectorIndexes": []}) == (None, [])
    assert vector_index(None) == (None, [])


def _rows(ru, retrieved, ids_normal, ids_exact, index="QuantizedFlat"):
    report = {"UtilizedVectorIndexes": [{"IndexSpec": {"VectorIndexType": index, "ShardKeys": ["/libraryId"]}}]}
    rows = []
    for run in range(3):
        rows.append(
            {
                "kind": "vector",
                "query": 0,
                "run": run,
                "error": None,
                "request_charge": ru[run],
                "retrieved_document_count": retrieved,
                "milliseconds": 10.0,
                "null_scores": 0,
                "throttled": 0,
                "result_ids": ids_normal,
                "index_utilization": report,
            }
        )
    rows.append(
        {
            "kind": "exact",
            "query": 0,
            "run": 0,
            "error": None,
            "request_charge": 100.0,
            "retrieved_document_count": 1000,
            "milliseconds": 50.0,
            "null_scores": 0,
            "throttled": 0,
            "result_ids": ids_exact,
            "index_utilization": None,
        }
    )
    return rows


def test_grade_scoped_when_cost_reads_and_recall_hold():
    ids = [str(i) for i in range(10)]
    base = summarize(_rows([15.0, 15.2, 14.8], 50, ids, ids), expected_queries=1)
    later = summarize(_rows([15.1, 15.2, 15.0], 50, ids, ids), expected_queries=1)
    g = grade(base, later)
    assert g["scoped"] and all(g.values())


def test_grade_not_scoped_when_cost_grows_with_the_container():
    ids = [str(i) for i in range(10)]
    base = summarize(_rows([15.0, 15.2, 14.8], 50, ids, ids), expected_queries=1)
    later = summarize(_rows([60.0, 61.0, 59.0], 900, ids, ids), expected_queries=1)
    g = grade(base, later)
    assert not g["scoped"] and not g["ruHolds"] and not g["retrievedHolds"]


def test_grade_not_scoped_on_low_recall_or_missing_shard():
    ids = [str(i) for i in range(10)]
    base = summarize(_rows([15.0, 15.0, 15.0], 50, ids, ids), expected_queries=1)
    low = summarize(_rows([15.0, 15.0, 15.0], 50, ids[:5] + ["x"] * 5, ids), expected_queries=1)
    assert not grade(base, low)["recallAtFloor"]
    unsharded = summarize(_rows([15.0, 15.0, 15.0], 50, ids, ids), expected_queries=1)
    unsharded["vectorIndexes"] = ["QuantizedFlat:"]
    assert not grade(base, unsharded)["libraryShardReported"]


def test_state_must_be_outside_the_repository(tmp_path):
    with pytest.raises(SystemExit):
        main(["report", "--state", "records", "--out", str(tmp_path / "r.json")])


def test_grade_distinguishes_tolerance_from_repeat_spread():
    ids = [str(i) for i in range(10)]
    base = summarize(_rows([15.0, 15.2, 14.8], 50, ids, ids), expected_queries=1)
    later = summarize(_rows([16.0, 16.1, 15.9], 50, ids, ids), expected_queries=1)
    result = grade(base, later)
    assert result["ruWithinTolerance"]
    assert not result["ruWithinBaselineSpread"]
    assert not result["ruHolds"] and not result["scoped"]


def test_every_repeat_affects_recall():
    ids = [str(i) for i in range(10)]
    rows = _rows([15.0, 15.0, 15.0], 50, ids, ids)
    base = summarize(rows, expected_queries=1)
    rows[1]["result_ids"] = ["wrong"] * 10
    later = summarize(rows, expected_queries=1)
    assert later["recallComparisons"] == 3
    assert later["recallAt10RunMeans"] == [1.0, 0.0, 1.0]
    assert later["recallAt10Mean"] == pytest.approx(2 / 3, abs=0.0001)
    assert not grade(base, later)["recallAtFloor"]


@pytest.mark.parametrize("failure", ["missing-repeat", "missing-exact", "duplicate", "error", "empty-results"])
def test_partial_or_failed_runs_cannot_grade_scoped(failure):
    ids = [str(i) for i in range(10)]
    rows = _rows([15.0, 15.0, 15.0], 50, ids, ids)
    base = summarize(rows, expected_queries=1)
    if failure == "missing-repeat":
        rows.pop(1)
    elif failure == "missing-exact":
        rows.pop()
    elif failure == "duplicate":
        rows[1] = rows[0].copy()
    elif failure == "error":
        rows[1]["error"] = "unavailable"
    else:
        rows[1]["result_ids"] = []
    result = grade(base, summarize(rows, expected_queries=1))
    assert not result["completeRuns"] and not result["scoped"]


def test_missing_entire_question_or_index_report_is_not_a_pass():
    ids = [str(i) for i in range(10)]
    rows = _rows([15.0, 15.0, 15.0], 50, ids, ids)
    assert not summarize(rows, expected_queries=2)["complete"]
    base = summarize(rows, expected_queries=1)
    rows[1]["index_utilization"] = None
    assert not grade(base, summarize(rows, expected_queries=1))["libraryShardReported"]
    assert not grade(summarize(rows, expected_queries=1), base)["libraryShardReported"]


@pytest.mark.parametrize("missing", [None, float("nan"), -1])
def test_missing_or_invalid_cost_metrics_cannot_grade_scoped(missing):
    ids = [str(i) for i in range(10)]
    rows = _rows([15.0, 15.0, 15.0], 50, ids, ids)
    base = summarize(rows, expected_queries=1)
    rows[1]["retrieved_document_count"] = missing
    result = grade(base, summarize(rows, expected_queries=1))
    assert not result["completeCostMetrics"] and not result["scoped"]


def test_null_score_cannot_grade_scoped():
    ids = [str(i) for i in range(10)]
    rows = _rows([15.0, 15.0, 15.0], 50, ids, ids)
    base = summarize(rows, expected_queries=1)
    rows[-1]["null_scores"] = 1
    result = grade(base, summarize(rows, expected_queries=1))
    assert not result["noErrorsOrNullScores"] and not result["scoped"]


def test_readiness_probe_avoids_tiny_stage_three_shards():
    assert readiness_library(1).size == max(PROBE_SIZES)
    assert readiness_library(2).size >= 1000
    assert max(lib.size for lib in stage_libraries(3)) < 1000
    assert readiness_library(3) in stage_libraries(1)


def test_retrieved_rows_also_require_the_predeclared_repeat_spread():
    ids = [str(i) for i in range(10)]
    base = summarize(_rows([15.0, 15.0, 15.0], 50, ids, ids), expected_queries=1)
    later = summarize(_rows([15.0, 15.0, 15.0], 51, ids, ids), expected_queries=1)
    result = grade(base, later)
    assert result["retrievedWithinTolerance"]
    assert not result["retrievedWithinBaselineSpread"] and not result["scoped"]


@pytest.mark.parametrize("result_ids", [["same"] * 10, [str(i) for i in range(20)]])
def test_duplicate_or_appended_partition_results_cannot_pass(result_ids):
    rows = _rows([15.0, 15.0, 15.0], 50, result_ids, result_ids)
    result = summarize(rows, expected_queries=1)
    assert not result["complete"]
    assert not grade(result, result)["scoped"]


def test_boolean_cost_metric_is_not_a_measured_number():
    ids = [str(i) for i in range(10)]
    result = summarize(_rows([True, True, True], 50, ids, ids), expected_queries=1)
    assert not result["metricsComplete"] and not grade(result, result)["scoped"]


def test_report_does_not_treat_missing_baseline_or_probe_as_pass(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    ids = [str(i) for i in range(10)]
    rows = []
    for question in range(20):
        for row in _rows([15.0, 15.0, 15.0], 50, ids, ids):
            rows.append(row | {"library": "scale-p01", "query": question})
    (state / "measure-2.json").write_text(json.dumps({"rows": rows}))
    output = tmp_path / "report.json"
    assert main(["report", "--state", str(state), "--out", str(output)]) == 0
    result = json.loads(output.read_text())
    assert not result["baselineAvailable"]
    assert result["stages"]["2"]["scale-p01"]["complete"]
    assert not result["stages"]["2"]["scale-p02"]["complete"]
    assert not result["scopedEverywhere"]["2"]
    assert all(not value["scoped"] for value in result["grades"]["2"].values())
