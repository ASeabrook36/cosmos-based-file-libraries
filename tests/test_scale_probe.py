from collections import Counter

import pytest

from scripts.scale_probe import (
    CHUNKS_PER_DOCUMENT,
    PROBE_SIZES,
    assignment,
    grade,
    main,
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
    base = summarize(_rows([15.0, 15.2, 14.8], 50, ids, ids))
    later = summarize(_rows([16.0, 16.1, 15.9], 52, ids, ids))
    g = grade(base, later)
    assert g["scoped"] and all(g.values())


def test_grade_not_scoped_when_cost_grows_with_the_container():
    ids = [str(i) for i in range(10)]
    base = summarize(_rows([15.0, 15.2, 14.8], 50, ids, ids))
    later = summarize(_rows([60.0, 61.0, 59.0], 900, ids, ids))
    g = grade(base, later)
    assert not g["scoped"] and not g["ruHolds"] and not g["retrievedHolds"]


def test_grade_not_scoped_on_low_recall_or_missing_shard():
    ids = [str(i) for i in range(10)]
    base = summarize(_rows([15.0, 15.0, 15.0], 50, ids, ids))
    low = summarize(_rows([15.0, 15.0, 15.0], 50, ids[:5] + ["x"] * 5, ids))
    assert not grade(base, low)["recallAtFloor"]
    unsharded = summarize(_rows([15.0, 15.0, 15.0], 50, ids, ids))
    unsharded["vectorIndexes"] = ["QuantizedFlat:"]
    assert not grade(base, unsharded)["libraryShardReported"]


def test_state_must_be_outside_the_repository(tmp_path):
    with pytest.raises(SystemExit):
        main(["report", "--state", "records", "--out", str(tmp_path / "r.json")])
