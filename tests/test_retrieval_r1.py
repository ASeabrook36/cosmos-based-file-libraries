"""R1 diagnostic mechanism tests, not simulated cloud evidence."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from fli.query import check_query
from scripts.canary import SearchMeasurement, Throttle, run_query
from scripts.retrieval_r1 import diagnostic_query, merged_exact, report, result_checks, save


def reference(document, start, count=10):
    bindings = [
        {"id": f"{document}-{i}", "documentId": document, "libraryId": "fixture-library", "generation": 1}
        for i in range(count)
    ]
    return {
        "result_ids": [b["id"] for b in bindings],
        "scores": [start + i * 0.001 for i in range(count)],
        "result_bindings": bindings,
        "error": None,
        "null_scores": 0,
    }


def inputs():
    first, second = reference("first", 0.7), reference("second", 0.1)
    allowed = {b["id"]: b for result in (first, second) for b in result["result_bindings"]}
    return first, second, allowed


def test_independent_full_key_merge_exposes_prefix_reference_self_agreement():
    first, second, allowed = inputs()
    truth = merged_exact({"first": first, "second": second}, {"first": 10, "second": 10}, allowed)
    assert truth == list(zip(second["result_ids"], second["scores"], strict=True))
    # A defective prefix ANN and prefix exact can agree perfectly while both omit
    # the best partition. Only the independently merged reference detects this.
    checks = result_checks(first, truth, allowed)
    assert checks["recallAt10"] == 0 and not checks["passed"]
    assert result_checks(second, truth, allowed)["passed"]


def test_partition_append_is_rejected_even_when_first_partition_has_global_best():
    first, second, allowed = inputs()
    truth = merged_exact({"first": first, "second": second}, {"first": 10, "second": 10}, allowed)
    appended = deepcopy(second)
    for name in ("result_ids", "scores", "result_bindings"):
        appended[name] += first[name]
    checks = result_checks(appended, truth, allowed)
    assert checks["recallAt10"] == 1
    assert not checks["complete"] and not checks["passed"]


@pytest.mark.parametrize(
    "defect", ["missing-document", "null-score", "wrong-document", "stale-generation", "duplicate-id", "misordered"]
)
def test_independent_reference_fails_closed(defect):
    first, second, allowed = inputs()
    documents = {"first": first, "second": second}
    if defect == "missing-document":
        documents.pop("second")
    elif defect == "null-score":
        first["scores"][0] = None
    elif defect == "wrong-document":
        first["result_bindings"][0] = dict(first["result_bindings"][0], documentId="second")
    elif defect == "stale-generation":
        first["result_bindings"][0] = dict(first["result_bindings"][0], generation=2)
    elif defect == "duplicate-id":
        first["result_ids"][0] = first["result_ids"][1]
    else:
        first["scores"] = first["scores"][::-1]
    with pytest.raises(ValueError):
        merged_exact(documents, {"first": 10, "second": 10}, allowed)


def test_exact_reference_handles_document_smaller_than_k():
    small = reference("small", 0.4, 2)
    other = reference("other", 0.1)
    allowed = {b["id"]: b for result in (small, other) for b in result["result_bindings"]}
    assert len(merged_exact({"small": small, "other": other}, {"small": 2, "other": 10}, allowed)) == 10


@pytest.mark.parametrize("key,value", [("libraryId", "wrong"), ("documentId", "wrong"), ("generation", 2)])
def test_returned_source_bindings_must_match_frozen_scope(key, value):
    first, second, allowed = inputs()
    truth = merged_exact({"first": first, "second": second}, {"first": 10, "second": 10}, allowed)
    second["result_bindings"][0] = dict(second["result_bindings"][0], **{key: value})
    result = result_checks(second, truth, allowed)
    assert result["recallAt10"] == 1 and not result["sourceBindings"] and not result["passed"]


def test_measured_ties_do_not_require_an_arbitrary_id_order():
    first, second, allowed = inputs()
    second["scores"] = [0.1] * 10
    truth = [(b["id"], 0.1) for b in second["result_bindings"]]
    second["result_ids"] = second["result_ids"][::-1]
    second["result_bindings"] = second["result_bindings"][::-1]
    assert result_checks(second, truth, allowed)["passed"]


def test_diagnostic_queries_reuse_defaults_and_never_return_vectors_or_text():
    # A deterministic fixture vector validates SQL plumbing only.
    vector = [0.01] * 3072
    sql, params = diagnostic_query("fixture-library", vector, document="fixture-document", exact=True)
    check_query("SELECT TOP 10 c.id FROM c " + sql.split(" FROM c ", 1)[1])
    assert "SELECT TOP 10" in sql and "c.libraryId = @libraryId" in sql
    assert "c.documentId = @documentId" in sql and "c.generation = @generation" in sql
    assert "'dataType': 'float16'" in sql and sql.count(", true,") == 2
    projection = sql.split(" FROM c ")[0]
    assert "c.text" not in projection and " c.embedding," not in projection
    assert dict((p["name"], p["value"]) for p in params)["@documentId"] == "fixture-document"
    hybrid, _ = diagnostic_query("fixture-library", vector, terms=["source", "condition"])
    assert "ORDER BY RANK RRF(" in hybrid


def test_full_key_routing_requires_the_same_document_filter():
    class NeverCalled:
        def query_items(self, *_args, **_kwargs):
            raise AssertionError("bad routing must fail before network")

    with pytest.raises(ValueError, match="constrained"):
        asyncio.run(
            run_query(
                NeverCalled(),
                "SELECT TOP 10 c.id FROM c WHERE c.libraryId = @libraryId",
                [],
                "fixture-library",
                SearchMeasurement("test", 0),
                Throttle(),
                scored=True,
                document_partition="fixture-document",
            )
        )


def test_incomplete_report_keeps_the_full_expected_denominator(tmp_path, monkeypatch):
    import scripts.retrieval_r1 as r1

    first, _, allowed = inputs()
    frozen_inputs = {"chunks": list(allowed.values()), "documents": []}
    monkeypatch.setattr(r1, "frozen", lambda _state: ({"schema": "fixture-controls"}, frozen_inputs))
    row = first | {
        "kind": "prefix",
        "query": 0,
        "repeat": 0,
        "document": None,
        "known_attempt_charge": 3.5,
        "attempt_charge_complete": False,
        "milliseconds": 10,
        "throttled": 0,
        "retry_wait_ms": 0,
        "range_diagnostics_complete": False,
        "partition_ranges": [],
        "index_utilizations": [],
    }
    import json

    (tmp_path / "measurements.jsonl").write_text(json.dumps(row) + "\n")
    save(tmp_path / "placement-before.json", {"knownRanges": [], "rangeComplete": False})
    save(tmp_path / "readiness.json", {"engaged": False})
    save(tmp_path / "status.json", {"status": "incomplete"})
    output = report(SimpleNamespace(state=tmp_path, out=tmp_path / "sanitized.json"))
    assert output["expectedCalls"] == 220 and output["measuredCalls"] == 1
    assert not output["complete"] and output["exactReferenceFailures"] == 20
    assert all(not value["correctInMeasuredScope"] for value in output["results"].values())
    assert output["results"]["prefix"]["knownAttemptRU"] == 3.5
    assert output["results"]["prefix"]["totalRU"] is None
    assert output["verifiedLibraryRangeCount"] is None and not output["multiRangeLibraryProven"]
