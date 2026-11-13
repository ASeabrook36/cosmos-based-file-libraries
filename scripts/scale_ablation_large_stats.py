"""Content-free summaries for the large-library extension; E68 remains frozen.

Repeat ranges describe the observed executions, not confidence intervals.
References can be shared between routed and no-prefix queries over the same set.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.scale_ablation_stats import index_summary

METRICS = {"ru": "request_charge", "retrieved": "retrieved_document_count", "ms": "milliseconds"}
SHAPES = {
    "routed": "routed",
    "noPrefix": "no-prefix",
    "whereOnly": "no-prefix",
    "no-prefix": "no-prefix",
    "global": "global",
}


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _median(values: Sequence[Any]) -> float | None:
    valid = [value for value in values if _number(value)]
    return round(statistics.median(valid), 6) if valid else None


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _result_ok(row: Mapping[str, Any], k: int) -> bool:
    ids = row.get("result_ids")
    return (
        isinstance(ids, list) and len(ids) == k and all(isinstance(value, str) for value in ids) and len(set(ids)) == k
    )


def _ownership(row: Mapping[str, Any], library_id: str | None, k: int) -> bool:
    if library_id is None or not _result_ok(row, k):
        return False
    bindings = row.get("result_bindings", [])
    declared = [binding["libraryId"] for binding in bindings if isinstance(binding, dict) and "libraryId" in binding]
    if declared and (len(declared) != k or any(value != library_id for value in declared)):
        return False
    return all(value.startswith(library_id + "-d") for value in row["result_ids"])


def _response_reports(row: Mapping[str, Any]) -> tuple[list[Any], list[Mapping[str, Any]]]:
    responses = row.get("response_metrics")
    if not isinstance(responses, list) or not responses:
        attempts = row.get("attempts", [])
        responses = attempts[-1].get("rawResponses", []) if attempts and isinstance(attempts[-1], dict) else []
    if responses:
        return [response.get("indexUtilization") for response in responses], responses
    reports = row.get("index_utilizations")
    return (reports if isinstance(reports, list) and reports else [row.get("index_utilization")]), []


def _report_usable(report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    entries = report.get("UtilizedVectorIndexes")
    if not entries:
        return True  # Explicit decoded report with no utilized vector index.
    return isinstance(entries, list) and all(
        isinstance(entry, dict)
        and isinstance(entry.get("IndexSpec"), dict)
        and isinstance(entry["IndexSpec"].get("VectorIndexType"), str)
        and bool(entry["IndexSpec"]["VectorIndexType"])
        for entry in entries
    )


def _diagnostics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized, usable_queries = [], 0
    with_retrieved, without_retrieved, missing_with_retrieved = 0, 0, 0
    for row in rows:
        reports, responses = _response_reports(row)
        normalized.append(dict(row) | {"index_utilizations": reports})
        usable_queries += any(_report_usable(report) for report in reports)
        for response in responses:
            if response.get("retrieved") is not None:
                with_retrieved += 1
                missing_with_retrieved += response.get("indexUtilization") is None
            else:
                without_retrieved += 1
    summary = index_summary(normalized)
    # Only public vector mode names and known policy paths leave this module.
    for spec in summary["vectorIndexes"]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,31}", spec["type"]):
            spec["type"] = "Unrecognized"
        spec["shardKeys"] = [
            path if path in {"/libraryId", "/documentId", "/embedding"} else "<other>" for path in spec["shardKeys"]
        ]
    return summary | {
        "diagnosticsUsable": bool(rows) and usable_queries == len(rows),
        "queriesWithUsableDiagnostics": usable_queries,
        "responsesWithRetrievedMetric": with_retrieved,
        "responsesWithoutRetrievedMetric": without_retrieved,
        "missingIndexHeaderOnResponsesWithRetrievedMetric": missing_with_retrieved,
    }


def _mode_signature(diagnostics: Mapping[str, Any]) -> tuple:
    specs = tuple(sorted((spec["type"], tuple(spec["shardKeys"])) for spec in diagnostics["vectorIndexes"]))
    return specs, diagnostics["indexReportsWithoutUtilizedVectorIndex"] > 0


def summarize_shape(
    ann_rows: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
    *,
    query_ordinals: Sequence[int] = tuple(range(20)),
    library_id: str | None = None,
    library_size: int | None = None,
    inventory_vectors: int | None = None,
    query_shape: str = "routed",
    runs: int = 3,
    k: int = 10,
) -> dict[str, Any]:
    """One library/arm/shape; ANN ordinals are strict, reference pools may be larger."""
    if query_shape not in SHAPES:
        raise ValueError("unknown experiment query shape")
    if any(value is not None and (type(value) is not int or value <= 0) for value in (library_size, inventory_vectors)):
        raise ValueError("library and inventory sizes must be positive integers")
    shape = SHAPES[query_shape]
    ordinals = tuple(query_ordinals)
    if not ordinals or len(set(ordinals)) != len(ordinals) or any(type(q) is not int or q < 0 for q in ordinals):
        raise ValueError("query ordinals must be distinct nonnegative integers")
    expected = Counter((query, run) for query in ordinals for run in range(runs))
    query_complete = Counter((row.get("query"), row.get("run")) for row in ann_rows) == expected
    selected_refs = [row for row in references if row.get("query") in ordinals]
    reference_complete = Counter((row.get("query"), row.get("run", 0)) for row in selected_refs) == Counter(
        (q, 0) for q in ordinals
    )
    reference_by_query = {row.get("query"): row for row in selected_refs if not row.get("error")}
    good = [row for row in ann_rows if not row.get("error")]
    all_rows = [*ann_rows, *selected_refs]
    results_complete = bool(all_rows) and all(not row.get("error") and _result_ok(row, k) for row in all_rows)
    metrics_complete = bool(all_rows) and all(_number(row.get(key)) for row in all_rows for key in METRICS.values())
    ownership_checked = shape != "global"
    ownership_complete = not ownership_checked or all(_ownership(row, library_id, k) for row in all_rows)
    diagnostics = _diagnostics(ann_rows)
    run_diagnostics = [_diagnostics([row for row in ann_rows if row.get("run") == run]) for run in range(runs)]
    mode_stable = (
        bool(run_diagnostics)
        and all(d["diagnosticsUsable"] for d in run_diagnostics)
        and len({_mode_signature(d) for d in run_diagnostics}) == 1
    )
    recalls = [[] for _ in range(runs)]
    for row in good:
        reference = reference_by_query.get(row.get("query"))
        run = row.get("run")
        if (
            reference is not None
            and type(run) is int
            and 0 <= run < runs
            and _result_ok(row, k)
            and _result_ok(reference, k)
        ):
            recalls[run].append(len(set(row["result_ids"]) & set(reference["result_ids"])) / k)
    recall_means = [statistics.fmean(values) if values else None for values in recalls]
    recall_complete = all(len(values) == len(ordinals) for values in recalls)
    recall_floor = recall_complete and all(value is not None and value >= 0.95 for value in recall_means)
    complete = (
        query_complete
        and reference_complete
        and results_complete
        and metrics_complete
        and ownership_complete
        and diagnostics["diagnosticsUsable"]
    )
    result = {
        "summaryVersion": 1,
        "queryShape": shape,
        "queryOrdinals": list(ordinals),
        "librarySize": library_size,
        "inventoryVectors": inventory_vectors,
        "scopeBindingSha256": hashlib.sha256(library_id.encode()).hexdigest() if library_id is not None else None,
        "expectedQueries": len(ordinals),
        "expectedSearches": len(ordinals) * runs,
        "searches": len(ann_rows),
        "referenceSearches": len(selected_refs),
        "querySetComplete": query_complete,
        "referenceSetComplete": reference_complete,
        "resultsComplete": results_complete,
        "metricsComplete": metrics_complete,
        "ownershipChecked": ownership_checked,
        "ownershipComplete": ownership_complete,
        "complete": complete,
        "qualityAccepted": complete and recall_floor,
        "errors": sum(bool(row.get("error")) for row in ann_rows),
        "referenceErrors": sum(bool(row.get("error")) for row in selected_refs),
        "throttled": sum(row.get("throttled", 0) for row in ann_rows),
        "recallAt10Mean": _round(statistics.fmean(value for group in recalls for value in group))
        if any(recalls)
        else None,
        "recallAt10RunMeans": [_round(value) for value in recall_means],
        "recallComparisons": sum(map(len, recalls)),
        "recallComplete": recall_complete,
        "recallAtFloor": recall_floor,
        "indexDiagnostics": diagnostics,
        "indexDiagnosticsByRun": run_diagnostics,
        "modeSignatureStableAcrossRuns": mode_stable,
        "referenceIndexDiagnostics": _diagnostics(selected_refs),
    }
    for label, key in METRICS.items():
        run_medians = [_median([row.get(key) for row in good if row.get("run") == run]) for run in range(runs)]
        by_question = [_median([row.get(key) for row in good if row.get("query") == q]) for q in ordinals]
        result[label + "Median"] = _median(by_question)
        result[label + "RunMedians"] = run_medians
        finite = [value for value in run_medians if value is not None]
        result[label + "RunRange"] = [min(finite), max(finite)] if finite else None
        result["reference" + label[0].upper() + label[1:] + "Median"] = _median([row.get(key) for row in selected_refs])
    return result


def summarize_subset(ann_rows, references, *, query_ordinals=tuple(range(5)), **kwargs):
    """Explicitly restrict a larger routed question set to the frozen diagnostic subset."""
    selected = [row for row in ann_rows if row.get("query") in query_ordinals]
    return summarize_shape(selected, references, query_ordinals=query_ordinals, **kwargs) | {
        "restrictedQuestionSubset": True,
        "sourceAnnRowCount": len(ann_rows),
        "selectedAnnRowCount": len(selected),
    }


def _same_questions(a, b):
    return a.get("queryOrdinals") == b.get("queryOrdinals")


def _same_scope(a, b):
    return a.get("scopeBindingSha256") == b.get("scopeBindingSha256")


def _accepted(summary):
    return bool(summary.get("qualityAccepted"))


def _changes(a, b):
    result = {}
    for label in METRICS:
        before, after = a.get(label + "Median"), b.get(label + "Median")
        valid = _number(before) and _number(after)
        result[label + "Delta"] = _round(after - before) if valid else None
        result[label + "Ratio"] = _round(after / before) if valid and before > 0 else None
        result[label + "DifferencePct"] = _round(100 * (after - before) / before) if valid and before > 0 else None
    return result


def compare_arms(split, unsplit):
    """Compare unsplit minus split; numerical effects survive an inconclusive grade."""
    result = _changes(split, unsplit)
    same_context = (
        _same_questions(split, unsplit)
        and _same_scope(split, unsplit)
        and split.get("queryShape") == unsplit.get("queryShape")
        and split.get("inventoryVectors") == unsplit.get("inventoryVectors")
        and split.get("librarySize") == unsplit.get("librarySize")
    )
    valid = _accepted(split) and _accepted(unsplit) and same_context
    stable = bool(split.get("modeSignatureStableAcrossRuns")) and bool(unsplit.get("modeSignatureStableAcrossRuns"))
    sr, ur = split.get("ruRunMedians", []), unsplit.get("ruRunMedians", [])
    repeat_valid = len(sr) == len(ur) == 3 and all(_number(value) and value > 0 for value in [*sr, *ur])
    ratios = [u / s for s, u in zip(sr, ur, strict=True)] if repeat_valid else []
    before, after = split.get("ruMedian"), unsplit.get("ruMedian")
    ratio = after / before if _number(before) and before > 0 and _number(after) else None
    strong = valid and stable and repeat_valid and ratio is not None and ratio >= 2 and min(ur) >= 2 * max(sr)
    equivalent = (
        valid
        and stable
        and repeat_valid
        and ratio is not None
        and 0.8 <= ratio <= 1.2
        and all(0.8 <= value <= 1.2 for value in ratios)
    )
    result.update(
        {
            "comparison": "unsplit minus split",
            "qualityAccepted": valid,
            "sameMeasurementContext": same_context,
            "modeSignaturesStable": stable,
            "ruPairedRunRatios": [_round(value) for value in ratios],
            "strongShardingBenefit": strong,
            "noMaterialObservedRuDifference": equivalent,
            "classification": "strong-benefit"
            if strong
            else "similar-ru"
            if equivalent
            else "other"
            if valid and stable
            else None,
            "estimatedUsdSavedPerMillionSearchesAtPlanningRate": _round(result["ruDelta"] * 0.25)
            if result["ruDelta"] is not None
            else None,
            "planningUsdPerMillionRu": 0.25,
            "recallDifference": _round(unsplit["recallAt10Mean"] - split["recallAt10Mean"])
            if _number(split.get("recallAt10Mean")) and _number(unsplit.get("recallAt10Mean"))
            else None,
        }
    )
    return result


def compare_routes(routed, no_prefix):
    """Matched no-prefix minus routed diagnostics; frozen question subsets must agree."""
    result = _changes(routed, no_prefix)
    inventory = routed.get("inventoryVectors")
    same_context = (
        _same_questions(routed, no_prefix)
        and _same_scope(routed, no_prefix)
        and routed.get("queryShape") == "routed"
        and no_prefix.get("queryShape") == "no-prefix"
        and type(inventory) is int
        and inventory > 0
        and no_prefix.get("inventoryVectors") == inventory
        and routed.get("librarySize") == no_prefix.get("librarySize")
    )
    accepted = _accepted(routed) and _accepted(no_prefix) and same_context
    a, b = routed.get("ruRunMedians", []), no_prefix.get("ruRunMedians", [])
    repeat_valid = len(a) == len(b) == 3 and all(_number(value) and value > 0 for value in [*a, *b])
    ratios = [after / before for before, after in zip(a, b, strict=True)] if repeat_valid else []
    recall_before, recall_after = routed.get("recallAt10Mean"), no_prefix.get("recallAt10Mean")
    return result | {
        "comparison": "no-prefix minus routed on the same frozen questions",
        "sameMeasurementContext": same_context,
        "qualityAccepted": accepted,
        "ruPairedRunRatios": [_round(value) for value in ratios],
        "recallDifference": _round(recall_after - recall_before)
        if _number(recall_before) and _number(recall_after)
        else None,
        "routedModeSignatureStable": bool(routed.get("modeSignatureStableAcrossRuns")),
        "noPrefixModeSignatureStable": bool(no_prefix.get("modeSignatureStableAcrossRuns")),
    }


def compare_size_benefit(baseline_split, baseline_unsplit, large_split, large_unsplit):
    """Absolute routed RU saving at a larger size versus the same-inventory 5k saving."""
    baseline = compare_arms(baseline_split, baseline_unsplit)
    large = compare_arms(large_split, large_unsplit)
    before, after = baseline["ruDelta"], large["ruDelta"]
    numeric = before is not None and after is not None
    positive = numeric and before > 0
    four = (baseline_split, baseline_unsplit, large_split, large_unsplit)
    inventory = baseline_split.get("inventoryVectors")
    common = (
        type(inventory) is int
        and inventory > 0
        and all(summary.get("inventoryVectors") == inventory for summary in four)
    )
    size_context = (
        all(summary.get("queryShape") == "routed" for summary in four)
        and baseline_split.get("librarySize") == baseline_unsplit.get("librarySize") == 5000
        and large_split.get("librarySize") == large_unsplit.get("librarySize")
        and _number(large_split.get("librarySize"))
        and large_split["librarySize"] > 5000
    )
    valid = (
        common
        and size_context
        and baseline["qualityAccepted"]
        and large["qualityAccepted"]
        and baseline["modeSignaturesStable"]
        and large["modeSignaturesStable"]
    )

    def paired_savings(split, unsplit):
        sr, ur = split.get("ruRunMedians", []), unsplit.get("ruRunMedians", [])
        if len(sr) != 3 or len(ur) != 3 or not all(_number(value) for value in [*sr, *ur]):
            return []
        return [u - s for s, u in zip(sr, ur, strict=True)]

    baseline_runs = paired_savings(baseline_split, baseline_unsplit)
    large_runs = paired_savings(large_split, large_unsplit)
    repeats = len(baseline_runs) == len(large_runs) == 3 and all(
        type(value) in (int, float) and math.isfinite(value) for value in [*baseline_runs, *large_runs]
    )
    pct = 100 * (after - before) / before if positive else None

    def independent_range(split, unsplit):
        sr, ur = split.get("ruRunMedians", []), unsplit.get("ruRunMedians", [])
        if len(sr) != 3 or len(ur) != 3 or not all(_number(value) for value in [*sr, *ur]):
            return None
        return [min(ur) - max(sr), max(ur) - min(sr)]

    baseline_range = independent_range(baseline_split, baseline_unsplit)
    large_range = independent_range(large_split, large_unsplit)
    ratio_range = None
    if baseline_range and large_range and not baseline_range[0] <= 0 <= baseline_range[1]:
        quotients = [value / baseline for value in large_range for baseline in baseline_range]
        ratio_range = [min(quotients), max(quotients)]
    central = (
        "grown"
        if pct is not None and pct > 20
        else "shrunk"
        if pct is not None and pct < -20
        else "held"
        if pct is not None
        else None
    )
    classification = None
    if valid and positive and repeats and baseline_range[0] > 0 and ratio_range:
        if central == "grown" and ratio_range[0] > 1.2:
            classification = "grown"
        elif central == "shrunk" and ratio_range[1] < 0.8:
            classification = "shrunk"
        elif central == "held" and 0.8 <= ratio_range[0] <= ratio_range[1] <= 1.2:
            classification = "held"
        else:
            classification = "borderline"
    elif valid and positive and repeats:
        classification = "borderline"
    return {
        "baselineSize": 5000,
        "size": large_split.get("librarySize"),
        "inventoryVectors": inventory if common else None,
        "baselineSavingsRu": before,
        "savingsRu": after,
        "savingsDeltaRu": _round(after - before) if numeric else None,
        "savingsRatioToBaseline": _round(after / before) if positive else None,
        "savingsChangePct": _round(pct),
        "positiveBaselineSaving": bool(positive),
        "baselinePairedRunSavingsRu": [_round(value) for value in baseline_runs],
        "pairedRunSavingsRu": [_round(value) for value in large_runs],
        "baselinePairedRunSavingsRange": [min(baseline_runs), max(baseline_runs)] if repeats else None,
        "pairedRunSavingsRange": [min(large_runs), max(large_runs)] if repeats else None,
        "baselineRepeatSavingsRange": [_round(value) for value in baseline_range] if baseline_range else None,
        "repeatSavingsRange": [_round(value) for value in large_range] if large_range else None,
        "repeatSavingsRatioRange": [_round(value) for value in ratio_range] if ratio_range else None,
        "repeatSavingsChangePctRange": [_round(100 * (value - 1)) for value in ratio_range] if ratio_range else None,
        "baselineRepeatSavingsRangeStrictlyPositive": bool(baseline_range and baseline_range[0] > 0),
        "repeatSavingsRangeMethod": "min unsplit minus max split through max unsplit minus min split",
        "sameFinalInventory": common,
        "qualityAccepted": valid,
        "observedChangeBand": central if valid else None,
        "classification": classification,
        "repeatRangeIsConfidenceInterval": False,
        "limits": (
            "Absolute RU saving relative to the 5k routed control at one common final inventory; "
            "nonpositive baseline or overlapping bands is inconclusive."
        ),
    }


def compare_small_drift(before, after):
    """Numerical post-load minus fresh pre-load drift; inventories intentionally differ."""
    result = _changes(before, after)
    accepted = (
        _accepted(before)
        and _accepted(after)
        and _same_questions(before, after)
        and _same_scope(before, after)
        and before.get("queryShape") == after.get("queryShape") == "routed"
    )
    pct = result["ruDifferencePct"]
    return result | {
        "qualityAccepted": accepted,
        "ruStableWithin20Pct": accepted and pct is not None and abs(pct) <= 20,
    }
