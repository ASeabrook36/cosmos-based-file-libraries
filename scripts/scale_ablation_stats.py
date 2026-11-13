"""Pure summaries for the paired vector-shard ablation.

Numbers are the primary results. The 20% descriptive grades never replace the
observed absolute and percentage changes, and three repeats are not confidence
intervals. This module handles metrics and result identifiers, never query text.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

METRICS = {
    "ru": "request_charge",
    "retrieved": "retrieved_document_count",
    "ms": "milliseconds",
}


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _median(values: Sequence[Any]) -> float | None:
    valid = [v for v in values if _number(v)]
    return round(statistics.median(valid), 6) if valid else None


def _range(values: Sequence[Any]) -> list[float] | None:
    valid = [v for v in values if _number(v)]
    return [min(valid), max(valid)] if valid else None


def _result_ok(row: Mapping[str, Any], k: int) -> bool:
    ids = row.get("result_ids")
    return isinstance(ids, list) and len(ids) == k and all(isinstance(i, str) for i in ids) and len(set(ids)) == k


def _reports(row: Mapping[str, Any]) -> list[Any]:
    """Use the complete response list when supplied; retain missing reports."""
    reports = row.get("index_utilizations")
    if isinstance(reports, list) and reports:
        return reports
    return [row.get("index_utilization")]


def index_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Retain every utilized vector-index spec and count missing response reports."""
    counts: Counter[tuple[str, tuple[str, ...]]] = Counter()
    expected_responses = 0
    reported_responses = 0
    valid_responses = 0
    present_headers = 0
    reports_without_utilized = 0
    for row in rows:
        reports = _reports(row)
        responses = row.get("http_responses")
        expected_responses += responses if type(responses) is int and responses > 0 else len(reports)
        reported_responses += len(reports)
        for report in reports:
            present_headers += report is not None
            entries = report.get("UtilizedVectorIndexes") if isinstance(report, dict) else None
            reports_without_utilized += isinstance(report, dict) and not entries
            valid = isinstance(entries, list) and bool(entries)
            for entry in entries if isinstance(entries, list) else []:
                spec = entry.get("IndexSpec") if isinstance(entry, dict) else None
                kind = spec.get("VectorIndexType") if isinstance(spec, dict) else None
                shards = spec.get("ShardKeys", []) if isinstance(spec, dict) else None
                if not isinstance(kind, str) or not kind or not isinstance(shards, list):
                    valid = False
                    continue
                if not all(isinstance(path, str) for path in shards):
                    valid = False
                    continue
                counts[(kind, tuple(shards))] += 1
            valid_responses += bool(valid)
    return {
        "indexReportsComplete": bool(rows)
        and expected_responses == reported_responses
        and valid_responses == expected_responses,
        "expectedIndexResponses": expected_responses,
        "reportedIndexResponses": reported_responses,
        "validIndexResponses": valid_responses,
        "indexHeadersPresent": present_headers,
        "indexHeadersMissing": max(0, expected_responses - present_headers),
        "indexReportsWithoutUtilizedVectorIndex": reports_without_utilized,
        "vectorIndexes": [
            {"type": kind, "shardKeys": list(shards), "occurrences": count}
            for (kind, shards), count in sorted(counts.items())
        ],
    }


def _metric_summaries(rows: Sequence[Mapping[str, Any]], runs: int) -> dict[str, Any]:
    by_query: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_query.setdefault(row.get("query", 0), []).append(row)
    out = {}
    for name, key in METRICS.items():
        medians = [_median([r.get(key) for r in rows if r.get("run") == run]) for run in range(runs)]
        out[f"{name}Median"] = _median([_median([r.get(key) for r in group]) for group in by_query.values()])
        out[f"{name}RunMedians"] = medians
        out[f"{name}RunRange"] = _range(medians)
    return out


def summarize_probe(
    rows: Sequence[Mapping[str, Any]], *, expected_queries: int = 20, runs: int = 3, k: int = 10
) -> dict[str, Any]:
    """Summarize one library/stage/arm; absent and duplicate queries cannot pass."""
    expected = {
        (kind, query, run)
        for query in range(expected_queries)
        for kind, repeat_count in (("vector", runs), ("exact", 1))
        for run in range(repeat_count)
    }
    observed = [(r.get("kind"), r.get("query"), r.get("run")) for r in rows]
    query_complete = len(observed) == len(expected) and set(observed) == expected
    normal = [r for r in rows if r.get("kind") == "vector" and not r.get("error")]
    exact_rows = [r for r in rows if r.get("kind") == "exact" and not r.get("error")]
    exact = {r.get("query"): r for r in exact_rows}
    recalls: list[list[float]] = [[] for _ in range(runs)]
    for row in normal:
        reference = exact.get(row.get("query"))
        run = row.get("run")
        if reference is not None and type(run) is int and 0 <= run < runs:
            if _result_ok(row, k) and _result_ok(reference, k):
                recalls[run].append(len(set(row["result_ids"]) & set(reference["result_ids"])) / k)
    recall_means = [statistics.fmean(values) if values else None for values in recalls]
    recall_complete = all(len(values) == expected_queries for values in recalls)
    recall_at_floor = recall_complete and all(value is not None and value >= 0.95 for value in recall_means)
    metrics_complete = bool(rows) and all(_number(r.get(key)) for r in rows for key in METRICS.values())
    results_complete = bool(rows) and all(not r.get("error") and _result_ok(r, k) for r in rows)
    indexes = index_summary(normal)
    return {
        "summaryVersion": 1,
        "expectedQueries": expected_queries,
        "expectedSearches": expected_queries * runs,
        "expectedExactSearches": expected_queries,
        "searches": len(normal),
        "exactSearches": len(exact_rows),
        "querySetComplete": query_complete,
        "resultsComplete": results_complete,
        "metricsComplete": metrics_complete,
        "complete": query_complete and results_complete and metrics_complete and indexes["indexReportsComplete"],
        "errors": sum(bool(r.get("error")) for r in rows),
        "nullScores": sum(r.get("null_scores", 0) for r in rows),
        "throttled": sum(r.get("throttled", 0) for r in rows),
        **_metric_summaries(normal, runs),
        "exactRuMedian": _median([r.get("request_charge") for r in exact_rows]),
        "exactRetrievedMedian": _median([r.get("retrieved_document_count") for r in exact_rows]),
        "exactIndexDiagnostics": index_summary(exact_rows),
        "recallAt10Mean": round(statistics.fmean(v for values in recalls for v in values), 6) if any(recalls) else None,
        "recallAt10RunMeans": [round(v, 6) if v is not None else None for v in recall_means],
        "recallComparisons": sum(map(len, recalls)),
        "recallComplete": recall_complete,
        "recallAtFloor": recall_at_floor,
        **indexes,
    }


def summarize_control(rows: Sequence[Mapping[str, Any]], *, runs: int = 3, k: int = 10) -> dict[str, Any]:
    """Summarize three repeats of the fixed unfiltered question, without recall."""
    observed = [r.get("run") for r in rows]
    query_complete = len(observed) == runs and set(observed) == set(range(runs))
    metrics_complete = bool(rows) and all(_number(r.get(key)) for r in rows for key in METRICS.values())
    results_complete = bool(rows) and all(not r.get("error") and _result_ok(r, k) for r in rows)
    indexes = index_summary(rows)
    return {
        "expectedSearches": runs,
        "searches": len(rows),
        "querySetComplete": query_complete,
        "resultsComplete": results_complete,
        "metricsComplete": metrics_complete,
        "complete": query_complete and results_complete and metrics_complete,
        "errors": sum(bool(r.get("error")) for r in rows),
        "throttled": sum(r.get("throttled", 0) for r in rows),
        **_metric_summaries([r for r in rows if not r.get("error")], runs),
        **indexes,
    }


def _accepted(summary: Mapping[str, Any]) -> bool:
    return bool(summary.get("complete")) and bool(summary.get("recallAtFloor")) and not summary.get("nullScores")


def compare_summaries(
    baseline: Mapping[str, Any], later: Mapping[str, Any], *, tolerance: float = 0.20
) -> dict[str, Any]:
    """Report changes from baseline; robust growth must clear every run median."""
    out: dict[str, Any] = {"tolerance": tolerance, "measurementsAccepted": _accepted(baseline) and _accepted(later)}
    for name in METRICS:
        before, after = baseline.get(f"{name}Median"), later.get(f"{name}Median")
        valid = _number(before) and _number(after)
        delta = after - before if valid else None
        pct = 100 * delta / before if valid and before > 0 else None
        out[f"{name}Delta"] = round(delta, 6) if delta is not None else None
        out[f"{name}DriftPct"] = round(pct, 6) if pct is not None else None
        out[f"{name}AbsoluteDriftPct"] = round(abs(pct), 6) if pct is not None else None
        out[f"{name}WithinTolerance"] = abs(pct) <= 100 * tolerance if pct is not None else False
    before_runs, later_runs = baseline.get("ruRunMedians", []), later.get("ruRunMedians", [])
    valid_runs = (
        len(before_runs) == len(later_runs)
        and len(before_runs) > 0
        and all(_number(v) for v in [*before_runs, *later_runs])
    )
    out["ruGrowthBeyondRepeatRanges"] = bool(valid_runs and min(later_runs) > (1 + tolerance) * max(before_runs))
    out["ruFlat"] = out["measurementsAccepted"] and out["ruWithinTolerance"]
    return out


def compare_arms(split: Mapping[str, Any], unsplit: Mapping[str, Any], *, tolerance: float = 0.20) -> dict[str, Any]:
    """Report unsplit minus split, allowing lower unsplit cost in cheapness grade."""
    out = compare_summaries(split, unsplit, tolerance=tolerance)
    out["comparison"] = "unsplit minus split at the same stage"
    drift = out["ruDriftPct"]
    out["unsplitNoMoreExpensiveWithinTolerance"] = bool(
        out["measurementsAccepted"] and drift is not None and drift <= 100 * tolerance
    )
    return out
