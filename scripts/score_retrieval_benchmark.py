"""Export content-free source-region/usage receipts from private R0 replay captures.

Single-body normalized text matches are diagnostics, not source-coverage or quality
judgments. Split text can be delivered without matching any one body. Table geometry,
section completeness/order, citations and task success require source-based review.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import unicodedata
from collections import defaultdict
from pathlib import Path

from fli.structure_lab import digest
from scripts.retrieval_benchmark import REPO, read, sha
from scripts.retrieval_controls import private_path, verify_cell
from scripts.structure_lab import save

QUALITY = ("taskCompletion", "factualSupport", "citationAccuracy", "sourceRelationships")
ROLES = ("current", "custom", "microsoft")
LOCATOR_KEYS = ("kind", "index", "table", "row", "column")
COVERAGE_FLAGS = ("relationshipsDelivered", "completeOrderedSection", "catalogueComplete")
QUALITY_DEFINITIONS = {
    "taskCompletion": "All frozen task success criteria met; source delivery alone is insufficient.",
    "factualSupport": "Material claims are supported by delivered original evidence; unavailable on refusal.",
    "citationAccuracy": "Citations identify the supporting original file and region; unavailable without a claim.",
    "sourceRelationships": "Requested table grid/blankness and complete-section order/coverage verified from original "
    "source and delivered evidence; no pass inferred from unordered text matches or metadata.",
}
FAILURES = {
    "missing_source",
    "incomplete_section",
    "table_relationship_lost",
    "wrong_attribution",
    "unsupported_claim",
    "context_limited",
    "selector_error",
    "provider_error",
    "none",
}


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def delivered_bodies(replies):
    """Only original source bodies count, with each body bound to its cited file."""
    bodies = defaultdict(list)
    for reply in replies:
        recovered = reply.get("automaticRecovery") == "document_scoped_passages"
        if reply.get("mode") in {"catalogue", "semantic_candidates", "outline", "inspect"} and not recovered:
            continue
        refused = reply.get("disposition") == "budget_exceeded"
        if refused and not recovered:
            continue

        def add(row, name):
            if (
                name
                and row.get("text")
                and row.get("contentKind", "text") == "text"
                and row.get("textSource", "extracted_source_text") == "extracted_source_text"
            ):
                bodies[name].append(normalize(row["text"]))

        for result in reply.get("results") or []:
            citation = result.get("citation")
            name = result.get("fileName") or (citation.get("fileName") if isinstance(citation, dict) else None)
            add(result, name)
            for neighbor in result.get("neighbors") or []:
                add(neighbor, name)
        for document in ([] if refused else reply.get("documents")) or []:
            for section in document.get("sections") or []:
                add(section, document.get("fileName"))
    return bodies


def coverage(case, replies, gold):
    bodies = delivered_bodies(replies)
    regions = {r["id"]: (r, fact) for fact in case.get("sourceFacts", []) for r in fact.get("regions", [])}
    results = []
    for region_id, (region, fact) in regions.items():
        expected = gold["regions"][region_id]
        if expected["fileName"] != fact["source"] or expected["sourceId"] != fact["sourceId"]:
            raise ValueError("gold source region belongs to a different file")
        text = normalize(expected["expectedText"])
        if hashlib.sha256(text.encode()).hexdigest() != region["normalizedTextSha256"]:
            raise ValueError("gold source region differs from frozen task label")
        # Empty cells / table relations cannot be proved by substring containment.
        delivered = any(text in body for body in bodies[expected["fileName"]]) if text else None
        results.append(
            {"regionId": region_id, "singleBodyNormalizedTextMatch": delivered, "relationshipVerified": None}
        )
    scored = [r["singleBodyNormalizedTextMatch"] for r in results if r["singleBodyNormalizedTextMatch"] is not None]
    return {
        "regions": results,
        "matched": sum(scored),
        "textApplicable": len(scored),
        "totalRegions": len(results),
        "meaning": "single-body normalized-text match only; false can mean text split across delivered bodies, "
        "not source loss; true does not verify source order, snapshot, relevance or complete section coverage",
        "completeSectionInOrderVerified": None,
        "sourceSnapshotVerified": None,
    }


def relevant_source_coverage(case, value):
    """Allowlisted manual coverage, separate from single-body matching and answer quality."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("manual source coverage must be an object or unavailable")
    required = len({r["id"] for fact in case.get("sourceFacts", []) for r in fact.get("regions", [])})
    if type(value.get("requiredRegionCount")) is not int or value["requiredRegionCount"] != required:
        raise ValueError("manual required region count differs from frozen task labels")
    delivered = value.get("deliveredRegionCount")
    if delivered is not None and (type(delivered) is not int or not 0 <= delivered <= required):
        raise ValueError("manual delivered region count must be an integer within the required count or unavailable")
    for key in COVERAGE_FLAGS:
        if value.get(key) is not None and type(value[key]) is not bool:
            raise ValueError("manual source coverage flags must be boolean or unavailable")
    return {
        "requiredRegionCount": required,
        "deliveredRegionCount": delivered,
        **{key: value.get(key) for key in COVERAGE_FLAGS},
    }


def counted_source_coverage(value):
    """Zero-region catalogue tasks and unavailable table counts are not automatic passes."""
    return value is not None and value["requiredRegionCount"] > 0 and value["deliveredRegionCount"] is not None


def validate_gold(manifest, tasks, gold):
    """Bind each source-relative label to the manifest bytes and private gold locator."""
    documents = {d["id"]: d for d in manifest["documents"]}
    if len(documents) != len(manifest["documents"]):
        raise ValueError("duplicate manifest source identifier")
    definitions = {}
    for case in tasks:
        seen = set()
        for fact in case.get("sourceFacts", []):
            source = documents.get(fact["sourceId"])
            if source is None or fact["source"] != source["fileName"]:
                raise ValueError("task source does not match manifest")
            for region in fact.get("regions", []):
                region_id = region["id"]
                if region_id in seen:
                    raise ValueError("duplicate source region within task")
                seen.add(region_id)
                if region["sourceSha256"] != source["sha256"]:
                    raise ValueError("region source hash differs from manifest")
                expected = gold["regions"][region_id]
                if expected["sourceId"] != fact["sourceId"] or expected["fileName"] != source["fileName"]:
                    raise ValueError("gold source region belongs to a different manifest source")
                if expected.get("sourceSha256", source["sha256"]) != source["sha256"]:
                    raise ValueError("gold source hash differs from manifest")
                locator = {k: region[k] for k in LOCATOR_KEYS if k in region}
                if expected.get("locator") != locator:
                    raise ValueError("gold source locator differs from frozen label")
                text_hash = hashlib.sha256(normalize(expected["expectedText"]).encode()).hexdigest()
                if text_hash != region["normalizedTextSha256"]:
                    raise ValueError("gold source text differs from frozen label")
                definition = (fact["sourceId"], source["sha256"], text_hash, digest(locator))
                if region_id in definitions and definitions[region_id] != definition:
                    raise ValueError("source region identifier has inconsistent definitions")
                definitions[region_id] = definition


def expected_matrix(controls, tasks):
    """Reject ambiguous or manipulated schedules before reading private cell paths."""
    cases = {c["case"]: c for c in tasks}
    if len(cases) != len(tasks):
        raise ValueError("duplicate task identifier")
    selected = controls.get("cases") or []
    if not selected or len({c["case"] for c in selected}) != len(selected):
        raise ValueError("missing or duplicate frozen selected case")
    for case in selected:
        if case != cases.get(case["case"]) or case.get("split") != "development":
            raise ValueError("selected case differs from frozen development task")
        questions = case.get("questions") or [case.get("question")]
        if any(not isinstance(q, str) or not q.strip() for q in questions):
            raise ValueError("selected case has no executable question")
    schedule = controls.get("schedule") or []
    if not schedule or any(
        set(cell) != {"role", "repeat"}
        or cell["role"] not in ROLES
        or type(cell["repeat"]) is not int
        or not 1 <= cell["repeat"] <= 3
        for cell in schedule
    ):
        raise ValueError("unexpected role, repeat or schedule field")
    repeats = max(cell["repeat"] for cell in schedule)
    required_schedule = [
        {"role": role, "repeat": repeat}
        for repeat in range(1, repeats + 1)
        for role in (ROLES if repeat % 2 else tuple(reversed(ROLES)))
    ]
    if schedule != required_schedule:
        raise ValueError("missing, duplicate or reordered comparison cell")
    return {
        (cell["repeat"], cell["role"], case["case"], turn)
        for cell in schedule
        for case in selected
        for turn in range(1, len(case.get("questions") or [case["question"]]) + 1)
    }


def numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def usage(row):
    measured = row["metrics"]
    keys = (
        "promptTokens",
        "completionTokens",
        "totalTokens",
        "totalMs",
        "providerMs",
        "rateLimitWaitMs",
        "rateLimitRetries",
        "retrievalRequestCharge",
        "retrievalMs",
    )
    result = {key: numeric(measured.get(key)) for key in keys}
    calls = measured.get("modelCalls", [])
    result["modelCalls"] = len(calls)
    inputs = [numeric(c.get("promptTokens")) for c in calls]
    result["maxProviderInputTokens"] = max(inputs) if inputs and all(v is not None for v in inputs) else None
    result["maxKnownProviderInputTokens"] = max((v for v in inputs if v is not None), default=None)
    for key in ("cachedPromptTokens", "reasoningTokens"):
        values = [numeric(c.get(key)) for c in calls]
        result[key] = sum(values) if values and all(v is not None for v in values) else None
        result[key + "KnownSubtotal"] = sum(v for v in values if v is not None)
    result["toolAttempts"] = len(measured.get("tools", []))
    result["toolErrors"] = sum(bool(t.get("error")) for t in measured.get("tools", []))
    charge_complete = measured.get("retrievalChargeComplete")
    result["retrievalChargeComplete"] = charge_complete if type(charge_complete) is bool else None
    result["toolBudgetRefusals"] = sum(
        r.get("disposition") == "budget_exceeded" or (r.get("coverage") or {}).get("disposition") == "budget_exceeded"
        for r in row.get("toolReplies", [])
    )
    return result


def status_receipt(status):
    """Only defined status/counter fields leave the private run directory."""
    result = {
        "status": status.get("status")
        if status.get("status") in {"complete", "failed", "interrupted", "running"}
        else "unknown",
        "completedTurns": numeric(status.get("completedTurns")),
    }
    for key in ("snapshotsUnchanged", "heldoutExecuted"):
        if type(status.get(key)) is bool:
            result[key] = status[key]
    failure = status.get("failedAttempt")
    if isinstance(failure, dict):
        counters = failure.get("usage") or {}
        result["failedAttempt"] = {
            "scope": "final provider call only; earlier unfinished-turn usage unavailable",
            "usage": {
                key: numeric(counters.get(key)) for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
            "timing": {
                key: numeric((failure.get("timing") or {}).get(key))
                for key in ("totalMs", "providerMs", "rateLimitWaitMs", "rateLimitRetries")
            },
        }
    return result


def score(run, manifest, tasks, gold, reviews=None):
    run = private_path(run)
    controls, status = read(run / "controls.json"), read(run / "run-status.json")
    if controls["manifestSha256"] != digest(manifest) or controls["tasksSha256"] != digest(tasks):
        raise ValueError("frozen scoring inputs differ from run")
    expected = expected_matrix(controls, tasks)
    validate_gold(manifest, tasks, gold)
    cases = {c["case"]: c for c in tasks}
    judgments = {}
    for review in reviews or []:
        key = (review["repeat"], review["role"], review["case"], review.get("turn", 1))
        if type(review["repeat"]) is not int or type(key[3]) is not int or key not in expected:
            raise ValueError("judgment references an unexpected turn")
        if key in judgments:
            raise ValueError("duplicate quality judgment")
        if any(review.get(k) is not None and type(review[k]) is not bool for k in QUALITY):
            raise ValueError("quality judgments must be boolean or unavailable")
        if not set(review.get("failureCategories", [])) <= FAILURES:
            raise ValueError("unrecognized sanitized failure category")
        judgments[key] = {
            **review,
            "relevantSourceCoverage": relevant_source_coverage(cases[key[2]], review.get("relevantSourceCoverage")),
        }
    rows, attempts, observed = [], [], set()
    cells_complete = True
    source_artifacts_verified = True
    root_snapshot = read(run / "source-snapshot.json") if (run / "source-snapshot.json").exists() else None
    for cell in controls["schedule"]:
        directory = run / f"r{cell['repeat']}-{cell['role']}"
        cell_expected = {key for key in expected if key[:2] == (cell["repeat"], cell["role"])}
        cell_status = {}
        if (directory / "run-status.json").exists():
            cell_status = read(directory / "run-status.json")
        attempts.append({**cell, **status_receipt(cell_status)})
        cells_complete = cells_complete and (
            cell_status.get("status") == "complete"
            and type(cell_status.get("completedTurns")) is int
            and cell_status["completedTurns"] == len(cell_expected)
        )
        try:
            before = read(directory / "source-snapshot.json")
            libraries = list(before["libraries"])
            if root_snapshot is None or len(libraries) != 1:
                raise ValueError("missing or invalid root source binding")
            expected_snapshot = copy.deepcopy(root_snapshot)
            expected_snapshot["agent"]["libraryIds"] = libraries
            expected_snapshot["libraries"] = {key: root_snapshot["libraries"][key] for key in libraries}
            verify_cell(directory, controls["cases"], expected_snapshot)
        except (ValueError, OSError, KeyError, TypeError):
            source_artifacts_verified = False
        if not (directory / "results.json").exists():
            continue
        for result in read(directory / "results.json"):
            key = (cell["repeat"], cell["role"], result["case"], result["turn"])
            if type(result["turn"]) is not int or key not in expected:
                raise ValueError("result references an unexpected case or turn")
            if key in observed:
                raise ValueError("duplicate result for comparison turn")
            case = cases[result["case"]]
            questions = case.get("questions") or [case["question"]]
            if result.get("question") != questions[result["turn"] - 1]:
                raise ValueError("executed question differs from frozen selected task")
            observed.add(key)
            review = judgments.get(key, {})
            rows.append(
                {
                    **cell,
                    "case": result["case"],
                    "turn": result["turn"],
                    "sourceDelivery": coverage(cases[result["case"]], result["toolReplies"], gold),
                    "relevantSourceCoverage": review.get("relevantSourceCoverage"),
                    "usage": usage(result),
                    "quality": {k: review.get(k) for k in QUALITY},
                    "failureCategories": review.get("failureCategories", []),
                    "reviewAvailable": bool(review),
                }
            )
    allowed = {(r["repeat"], r["role"], r["case"], r["turn"]) for r in rows}
    if set(judgments) - allowed:
        raise ValueError("judgment references an unexecuted turn")
    matrix_complete = observed == expected
    count_matches = type(status.get("completedTurns")) is int and status["completedTurns"] == len(rows)
    execution_complete = (
        matrix_complete
        and count_matches
        and cells_complete
        and source_artifacts_verified
        and status.get("status") == "complete"
        and status.get("snapshotsUnchanged") is True
        and status.get("heldoutExecuted") is False
    )
    sanitized_status = status_receipt(status)
    if sanitized_status["status"] == "complete" and not execution_complete:
        sanitized_status.update(status="incomplete", reportedRunStatus="complete")
    coverage_by_task = defaultdict(dict)
    for row in rows:
        coverage_by_task[(row["repeat"], row["case"], row["turn"])][row["role"]] = row["relevantSourceCoverage"]
    paired_coverage = {
        key
        for key, members in coverage_by_task.items()
        if all(counted_source_coverage(members.get(role)) for role in ROLES)
    }
    summary = {}
    for role in ROLES:
        role_rows = [r for r in rows if r["role"] == role]
        planned = sum(key[1] == role for key in expected)
        summary[role] = {"turns": len(role_rows), "plannedTurns": planned, "missingTurns": planned - len(role_rows)}
        for field in QUALITY:
            applicable = [r["quality"][field] for r in role_rows if r["quality"][field] is not None]
            summary[role][field] = {"passed": sum(applicable), "applicable": len(applicable)}
        manual = [r["relevantSourceCoverage"] for r in role_rows if r["relevantSourceCoverage"] is not None]
        countable = [v for v in manual if counted_source_coverage(v)]
        paired = [
            r["relevantSourceCoverage"] for r in role_rows if (r["repeat"], r["case"], r["turn"]) in paired_coverage
        ]
        coverage_summary = {"reviewedTurns": len(manual)}
        for name, values in (("allRequiredRegions", countable), ("pairedAllRequiredRegions", paired)):
            coverage_summary[name] = {
                "passed": sum(v["deliveredRegionCount"] == v["requiredRegionCount"] for v in values),
                "applicable": len(values),
            }
        for field in COVERAGE_FLAGS:
            values = [v[field] for v in manual if v[field] is not None]
            coverage_summary[field] = {"passed": sum(values), "applicable": len(values)}
        summary[role]["relevantSourceCoverage"] = coverage_summary
    return {
        "schema": "fli.r0.scorecard.v1",
        "status": sanitized_status,
        "completeness": {
            "executionComplete": execution_complete,
            "turnMatrixComplete": matrix_complete,
            "cellReceiptsComplete": cells_complete,
            "sourceArtifactsVerified": source_artifacts_verified,
            "reportedCountMatchesRows": count_matches,
            "plannedTurns": len(expected),
            "capturedTurns": len(rows),
            "missingTurns": [
                {"repeat": repeat, "role": role, "case": case, "turn": turn}
                for repeat, role, case, turn in sorted(expected - observed)
            ],
            "completeMatchedTriples": sum(
                all((repeat, role, case, turn) in observed for role in ROLES)
                for repeat, case, turn in {(r, c, t) for r, _role, c, t in expected}
            ),
        },
        "manifestSha256": digest(manifest),
        "tasksSha256": digest(tasks),
        "model": {k: controls["model"][k] for k in ("model", "version")},
        "contextAllowancePerRun": controls["contextAllowancePerRun"],
        "rawControlsSha256": sha(run / "controls.json"),
        "codeSha256": controls["codeSha256"],
        "scorerSha256": sha(Path(__file__)),
        "reviewMethod": "source-based assistant review; not independent human or blinded validation",
        "qualityDefinitions": QUALITY_DEFINITIONS,
        "meaning": "bounded functional baseline; no ANN, strategy, provider or latency/cost winner",
        "unavailable": ["billed currency", "connector tokens", "allocated hosting", "actual inference region"],
        "summary": summary,
        "attemptReceipts": attempts,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=REPO / "docs/testing/retrieval-r0/manifest.json")
    parser.add_argument("--tasks", type=Path, default=REPO / "docs/testing/retrieval-r0/tasks.json")
    args = parser.parse_args()
    if args.gold.resolve().is_relative_to(REPO) or (args.reviews and args.reviews.resolve().is_relative_to(REPO)):
        parser.error("gold source text and raw reviews must remain private")
    receipt = score(
        args.run, read(args.manifest), read(args.tasks), read(args.gold), read(args.reviews) if args.reviews else None
    )
    save(args.output, receipt)
    print(json.dumps(receipt["summary"]))


if __name__ == "__main__":
    main()
