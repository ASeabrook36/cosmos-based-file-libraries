"""Content-free report for the separately approved full late measurement epoch.

All validation and hashing happen only after the epoch and independent audit
complete. This module makes no service calls and never writes parent state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import scale_ablation_large_report as base
from scripts.scale_ablation_large_stats import compare_arms, compare_size_benefit, compare_small_drift, summarize_shape

REPO = base.REPO
PARENT_RECORD = "records/scale-ablation-large-20261113.json"
PARENT_TABLE = "docs/scale-ablation-large-results.md"
PARENT_SHA = "6df1337e5d54e583b81ba7eee5469d8b67b8b9cd05ab9242bb82952879e17e19"
PARENT_TABLE_SHA = "fe4d4f0b335b71926e1b4f21205a3d8559b879a7389eb2b00048c88180317227"
LAUNCH_COMMIT = "a4ba12098875a9658ebb12df33831d11c99ce747"
SOURCE_FILES = {
    "scripts/scale_ablation_large_late_suite.py",
    "docs/scale-ablation-large-late-suite-proposal.md",
}
RECOMPUTATION_FILES = {
    "scripts/scale_ablation_large_late_suite_report.py",
    "scripts/scale_ablation_large_report.py",
    "scripts/scale_ablation_large_stats.py",
    "scripts/scale_ablation_stats.py",
    "scripts/scale_ablation_large_exact.py",
}
SIZES = base.SIZES
LIBRARIES = sorted(SIZES, key=SIZES.get)
PHASE = "late-suite"
InvalidEvidence = base.InvalidEvidence
require = base.require


def expected_plan():
    def arms(run, question, ordinal=0):
        return base.ARMS if (run + question + ordinal) % 2 == 0 else base.ARMS[::-1]

    return [
        (arm, lib, "routed", q, run)
        for run in range(3)
        for ordinal, lib in enumerate(LIBRARIES)
        for q in range(20)
        for arm in arms(run, q, ordinal)
    ] + [(arm, "global", "global", 0, run) for run in range(3) for arm in arms(run, 0)]


def git_sources(repo, expected):
    """Verify the two executed files against the identified commit, not HEAD."""
    require(set(expected) == SOURCE_FILES, "supplementary source membership differs")
    try:
        resolved = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", LAUNCH_COMMIT], check=True, capture_output=True, text=True
        ).stdout.strip()
        require(resolved == LAUNCH_COMMIT, "supplementary launch commit cannot be verified")
        for relative, digest in expected.items():
            require(isinstance(digest, str) and base.HEX.fullmatch(digest), "invalid supplementary source digest")
            blob = subprocess.run(
                ["git", "-C", str(repo), "show", f"{resolved}:{relative}"], check=True, capture_output=True
            ).stdout
            require(
                hashlib.sha256(blob).hexdigest() == digest, "executed supplementary source differs from launch commit"
            )
    except (subprocess.CalledProcessError, OSError):
        raise InvalidEvidence("supplementary launch source cannot be verified") from None
    return resolved


def parent_snapshot(parent, repo):
    return {
        "parent/" + str(path.relative_to(parent)): base.sha(path)
        for path in sorted(parent.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    } | {relative: base.sha(repo / relative) for relative in (PARENT_RECORD, PARENT_TABLE)}


def verify_parent(parent, repo, binding):
    require(base.sha(repo / PARENT_RECORD) == PARENT_SHA, "immutable parent record differs")
    require(base.sha(repo / PARENT_TABLE) == PARENT_TABLE_SHA, "immutable parent tables differ")
    require(binding.get("parentRecordSha256") == PARENT_SHA, "supplementary parent receipt binding differs")
    expected = binding.get("parentFiles", {})
    require(isinstance(expected, dict) and expected, "parent end-state manifest missing")
    require(
        all(isinstance(value, str) and base.HEX.fullmatch(value) for value in expected.values()),
        "invalid parent end-state digest",
    )
    require(base.digest(expected) == binding.get("parentEndStateSha256"), "parent manifest digest differs")
    actual = parent_snapshot(parent, repo)
    require(actual == expected, "immutable parent end-state files differ")
    return {
        "recordSha256": PARENT_SHA,
        "tablesSha256": PARENT_TABLE_SHA,
        "endStateManifestSha256": binding["parentEndStateSha256"],
        "verifiedPrivateFiles": sum(key.startswith("parent/") for key in actual),
        "allParentFilesUnchanged": True,
    }


def audit_metadata(state):
    path = state / "audit.json"
    raw = base.read(path, {})
    require(raw.get("passed") is True and raw.get("parentArtifactsUnchanged") is True, "independent audit did not pass")
    require(isinstance(raw.get("issues"), list) and not raw["issues"], "independent audit integrity issues remain")
    counts = raw.get("counts", {})
    expected = {
        "routedAnnRows": 720,
        "globalAnnRows": 6,
        "totalAnnRows": 726,
        "expectedReferenceSets": 121,
        "validatedArmReferenceSets": 242,
        "parentExactReferencesVerified": 322,
    }
    require(all(counts.get(key) == value for key, value in expected.items()), "independent audit coverage differs")
    return {
        "passed": True,
        "parentArtifactsUnchanged": True,
        "sha256": base.sha(path),
        "issueCount": 0,
        "qualityFindingCount": len(raw.get("qualityFindings", [])),
        "counts": base.numbers(counts, tuple(expected)),
    }


def validate_rows(rows, binding):
    keys = [tuple(row.get(key) for key in ("arm", "library", "shape", "query", "run")) for row in rows]
    plan = expected_plan()
    require(keys == plan and len(set(keys)) == 726, "supplementary order or 726-call coverage differs")
    require(binding.get("queryPlanSha256") == base.digest({"rows": plan}), "supplementary query-plan binding differs")
    for row in rows:
        require(
            row.get("kind") == "vector" and not row.get("error"), "supplementary ANN error or unexpected query kind"
        )
        ids = row.get("result_ids")
        require(
            isinstance(ids, list)
            and len(ids) == 10
            and all(isinstance(value, str) and value for value in ids)
            and len(set(ids)) == 10,
            "supplementary ANN cardinality differs",
        )
        require(
            all(base.numeric(row.get(key)) for key in ("request_charge", "retrieved_document_count", "milliseconds"))
            and type(row.get("http_responses")) is int
            and row["http_responses"] > 0,
            "supplementary metrics are incomplete",
        )


def timing_and_inventory(parent, state, status, binding, rows):
    prior = base.read(parent / "status.json")
    started, completed = base.stamp(status["startedAt"]), base.stamp(status["completedAt"])
    upper, ack = base.stamp(prior["lastWriteUpperBoundAt"]), base.stamp(prior["lastWriteAckAt"])
    due = upper + timedelta(hours=12)
    require(base.stamp(binding["notBefore"]) == due, "supplementary late-age binding differs")
    require(
        status.get("lastWriteUpperBoundAt") == prior["lastWriteUpperBoundAt"]
        and status.get("lastWriteAckAt") == prior["lastWriteAckAt"],
        "supplementary write-age context differs",
    )
    ends = [base.stamp(row["recordedAt"]) for row in rows]
    starts = [end - timedelta(milliseconds=row["milliseconds"]) for end, row in zip(ends, rows, strict=True)]
    tolerance = timedelta(milliseconds=2)
    elapsed = (completed - started).total_seconds()
    require(0 <= elapsed < 1800, "supplementary elapsed limit exceeded")
    require(
        min(starts) + tolerance >= max(due, started) and max(ends) <= completed + tolerance, "query chronology differs"
    )
    require(
        all(before <= after + tolerance for before, after in zip(ends, starts[1:], strict=False)),
        "supplementary query sequence chronology differs",
    )
    raw = base.read(state / "inventory-supplementary.json", {})
    checked = base.stamp(raw.get("checkedAt"))
    require(
        started <= checked <= min(starts) + tolerance and checked >= due, "fresh supplementary inventory time differs"
    )
    require(
        raw.get("partial") is False and raw.get("expectedVectors") == 429773, "supplementary inventory is incomplete"
    )
    inventories = {}
    for arm in base.ARMS:
        value = raw.get("arms", {}).get(arm, {})
        require(
            value.get("vectors") == 429773
            and value.get("libraries") == 1018
            and value.get("physicalRanges") == 1
            and value.get("policyMatches") is True
            and value.get("matchesExpected") is True
            and all(value.get("perLibrary", {}).get(lib) == size for lib, size in SIZES.items()),
            "supplementary inventory or policy differs",
        )
        inventories[arm] = base.numbers(value, ("vectors", "libraries", "physicalRanges")) | {
            "policyMatches": True,
            "matchesExpected": True,
            "perLibraryVectors": {str(size): value["perLibrary"][lib] for lib, size in SIZES.items()},
        }

    def ages(values, origin):
        return [
            min((value - origin).total_seconds() / 3600 for value in values),
            max((value - origin).total_seconds() / 3600 for value in values),
        ]

    return {
        "startedAt": started.isoformat(),
        "completedAt": completed.isoformat(),
        "elapsedSeconds": elapsed,
        "elapsedSecondsCap": 1800,
        "queryStartEstimate": "client completion timestamp minus recorded elapsed milliseconds",
        "chronologyToleranceMilliseconds": 2,
        "queryStartHoursSinceLastWriteUpperBoundRange": ages(starts, upper),
        "queryCompletionHoursSinceLastWriteUpperBoundRange": ages(ends, upper),
        "queryStartHoursSinceLastWriteAckRange": ages(starts, ack),
        "queryCompletionHoursSinceLastWriteAckRange": ages(ends, ack),
        "queryStartHoursSinceLastWriteUpperBoundMean": statistics.fmean(
            (value - upper).total_seconds() / 3600 for value in starts
        ),
        "allQueryStartsAtLeastTwelveHours": True,
    }, {"checkedAt": checked.isoformat(), "arms": inventories, "partial": False}


def budget_metadata(parent_record, state, binding, rows):
    raw = base.read(state / "budget.json", {})
    categories = raw.get("categories", {})
    require(
        isinstance(categories, dict) and set(categories) <= {"inventory", PHASE + "-vector"}, "unexpected cost category"
    )
    values = [raw.get(key) for key in ("parentKnownRu", "knownRu", "additionalKnownRu")] + list(categories.values())
    require(all(base.numeric(value) for value in values), "invalid supplementary cost counters")
    parent_ru, added, combined = raw["parentKnownRu"], raw["additionalKnownRu"], raw["knownRu"]
    require(
        abs(parent_ru - parent_record["costAccounting"]["knownRu"]) < 0.01
        and parent_ru == binding.get("parentKnownRu")
        and abs(combined - parent_ru - added) < 0.01
        and abs(sum(categories.values()) - added) < 0.01,
        "supplementary cost ledger does not reconcile",
    )
    charges = [row.get("known_attempt_charge") or row["request_charge"] for row in rows]
    require(all(base.numeric(value) for value in charges), "invalid observed ANN charge")
    require(
        abs(sum(charges) - categories.get(PHASE + "-vector", 0)) < 0.01, "supplementary ANN charges differ from ledger"
    )
    require(
        binding.get("additionalKnownRuCap") == 1000000 and binding.get("elapsedSecondsCap") == 1800,
        "supplementary approved limits differ",
    )
    require(added < 1000000 and combined < 64000000, "supplementary cost threshold exceeded")
    return {
        "parentKnownRu": parent_ru,
        "additionalKnownRu": added,
        "combinedKnownRu": combined,
        "categoriesRu": {"inventory": categories.get("inventory", 0), "ann": categories.get(PHASE + "-vector", 0)},
        "additionalKnownRuCap": 1000000,
        "preQueryReserveRu": 300000,
        "combinedReserveThresholdRu": 64000000,
        "planningUsdPerMillionRu": 0.25,
        "estimatedAdditionalKnownRequestUsd": added * 0.25 / 1000000,
        "estimatedCombinedKnownRequestUsd": combined * 0.25 / 1000000,
        "billComplete": False,
        "limits": (
            "Visible charges only; SDK-hidden requests and incremental storage are not fully priced. "
            "The stop threshold is not a provider-enforced bill ceiling."
        ),
    }


def question_variation(rows, references, ordinals):
    by_query = {row["query"]: row for row in references}
    result = []
    for q in ordinals:
        values = sorted((row for row in rows if row["query"] == q), key=lambda row: row["run"])
        reference = set(by_query[q]["result_ids"])
        entry = {
            "questionOrdinal": q,
            "recallAt10ByRun": [len(set(row["result_ids"]) & reference) / 10 for row in values],
        }
        for label, key in (("ru", "request_charge"), ("retrieved", "retrieved_document_count"), ("ms", "milliseconds")):
            data = [row[key] for row in values]
            entry[label + "ByRun"] = data
            entry[label + "Median"] = statistics.median(data)
            entry[label + "Range"] = [min(data), max(data)]
        result.append(entry)
    return {
        "questions": result,
        "questionMedianRuRange": [
            min(value["ruMedian"] for value in result),
            max(value["ruMedian"] for value in result),
        ],
        "allRepeatRuRange": [min(row["request_charge"] for row in rows), max(row["request_charge"] for row in rows)],
        "allQuestionRepeatRecallRange": [
            min(v for value in result for v in value["recallAt10ByRun"]),
            max(v for value in result for v in value["recallAt10ByRun"]),
        ],
        "questionsWithAnyRecallBelowOne": sum(min(value["recallAt10ByRun"]) < 1 for value in result),
    }


def paired_drift(before, after):
    def key(row):
        return (row["arm"], row["library"], row["shape"], row["query"], row["run"])

    a, b = {key(row): row for row in before}, {key(row): row for row in after}
    require(
        len(a) == len(before) and len(b) == len(after) and set(a) == set(b), "epoch questions or repeat pairs differ"
    )
    result = {"matchedQuestionRepeatPairs": len(a), "rangeIsConfidenceInterval": False}
    for label, field in (("ru", "request_charge"), ("retrieved", "retrieved_document_count"), ("ms", "milliseconds")):
        deltas, percentages = [], []
        for key_ in sorted(a):
            first, second = a[key_][field], b[key_][field]
            deltas.append(second - first)
            if first > 0:
                percentages.append(100 * (second - first) / first)
        result[label] = {
            "pairedDeltaMedian": statistics.median(deltas),
            "pairedDeltaRange": [min(deltas), max(deltas)],
            "pairedPctMedian": statistics.median(percentages) if percentages else None,
            "pairedPctRange": [min(percentages), max(percentages)] if percentages else None,
            "pairedPctMean": statistics.fmean(percentages) if percentages else None,
        }
    return result


def mode_signature(summary):
    info = summary["indexDiagnostics"]
    return (
        tuple(
            (value["type"], tuple(value["shardKeys"]), value.get("unrecognizedTypeSha256"))
            for value in info["vectorIndexes"]
        ),
        info["indexReportsWithoutUtilizedVectorIndex"] > 0,
    )


def build_report(state, parent, *, repo=REPO):
    state, parent, repo = Path(state).resolve(), Path(parent).resolve(), Path(repo).resolve()
    require(
        not state.is_relative_to(parent) and not parent.is_relative_to(state), "supplementary state must be separate"
    )
    status = base.read(state / "status.json", {})
    # These checks precede expensive parent hashing, including during a live run.
    require(status.get("complete") is True and status.get("phase") == "complete", "supplementary suite is not complete")
    require(
        status.get("parentArtifactsUnchanged") is True and not status.get("inFlight"),
        "supplementary completion is unverified",
    )
    require(status.get("selectedLibraries") == list(base.NEW), "supplementary six-library selection differs")
    audit = audit_metadata(state)
    binding = base.read(state / "binding.json", {})
    launch = git_sources(repo, binding.get("sourceFiles", {}))
    parent_proof = verify_parent(parent, repo, binding)
    parent_record = base.read(repo / PARENT_RECORD)
    rows = base.read(state / f"measure-{PHASE}.json", {}).get("rows", [])
    validate_rows(rows, binding)
    timing, inventory = timing_and_inventory(parent, state, status, binding, rows)
    cost = budget_metadata(parent_record, state, binding, rows)
    parent_rows = base.phase_rows(parent)
    config = base.read(parent / "config.json")
    references, repair = base.reference_repair(parent, config, parent_rows, repo)
    require(
        repair.get("enabled") and repair.get("exactReferencesValidated") == 322, "parent exact-reference proof differs"
    )
    require(
        repair["derivationsSha256"] == parent_record["referenceRepair"]["derivationsSha256"],
        "parent reference derivations differ",
    )
    provenance = base.source_provenance(parent, repo, config, base.read(parent / "binding.json"))

    def summary(values, arm, lib, *, one=False):
        shape, ref_phase = ("global", "global") if lib == "global" else ("routed", "routed")
        ann = base.select(values, arm, lib, shape, "vector")
        if one:
            ann = [row for row in ann if row["query"] == 0]
        refs = [
            references[(ref_phase, *base.key(row))]
            for row in base.select(parent_rows[ref_phase], arm, lib, shape, "exact")
            if not one or row["query"] == 0
        ]
        ordinals = (0,) if one or lib == "global" else tuple(range(20))
        value = summarize_shape(
            ann,
            refs,
            query_ordinals=ordinals,
            library_id=None if lib == "global" else lib,
            library_size=None if lib == "global" else SIZES[lib],
            inventory_vectors=429773,
            query_shape=shape,
        )
        value = base.apply_diagnostic_guard(value, ann, refs)
        value["questionVariation"] = question_variation(ann, refs, ordinals)
        return value, ann

    routed, globals_, drifts, sentinel_comparisons = {}, {}, {}, {}
    for lib in [*LIBRARIES, "global"]:
        role = "global" if lib == "global" else str(SIZES[lib])
        new, old, changes, sentinels = {}, {}, {}, {}
        for arm in base.ARMS:
            current, current_rows = summary(rows, arm, lib)
            prior, prior_rows = summary(parent_rows["global" if lib == "global" else "routed"], arm, lib)
            new[arm], old[arm] = current, prior
            changes[arm] = {
                "initialSummary": prior,
                "centralSummaryChange": compare_small_drift(prior, current),
                "pairedQuestionRepeatChange": paired_drift(prior_rows, current_rows),
                "observedModeSignatureChanged": mode_signature(prior) != mode_signature(current),
                "initialQualityAccepted": prior["qualityAccepted"],
                "supplementQualityAccepted": current["qualityAccepted"],
            }
            fixed, fixed_rows = summary(rows, arm, lib, one=True)
            sentinels[arm] = {"supplementQuestionZero": fixed, "priorWindows": {}}
            for phase in ("immediate", "late"):
                previous, previous_rows = summary(parent_rows[phase], arm, lib, one=True)
                sentinels[arm]["priorWindows"][phase] = {
                    "summary": previous,
                    "centralSummaryChange": compare_small_drift(previous, fixed),
                    "pairedQuestionRepeatChange": paired_drift(previous_rows, fixed_rows),
                    "observedModeSignatureChanged": mode_signature(previous) != mode_signature(fixed),
                }
        if lib == "global":
            globals_ = new
        else:
            routed[role] = new
        drifts[role], sentinel_comparisons[role] = changes, sentinels
    benefit = {
        str(SIZES[lib]): compare_size_benefit(
            routed["5000"]["split"],
            routed["5000"]["unsplit"],
            routed[str(SIZES[lib])]["split"],
            routed[str(SIZES[lib])]["unsplit"],
        )
        for lib in base.NEW
    }
    result = {
        "kind": "large-library-separately-approved-full-late-epoch",
        "createdAt": datetime.now(UTC).isoformat(),
        "partial": False,
        "parent": parent_proof,
        "coverage": {
            "routedAnnRows": 720,
            "globalAnnRows": 6,
            "totalAnnRows": 726,
            "eligibleQuestionReferenceSets": 121,
            "armQuestionReferenceSets": 242,
            "newExactCalls": 0,
            "newEmbeddingCalls": 0,
            "newModelCalls": 0,
            "newDataRows": 0,
        },
        "provenance": {
            "measurementSourceCommit": launch,
            "recordedSupplementSourceFilesSha256": binding["sourceFiles"],
            "launchCommitFilesVerified": True,
            "parentMeasurementSources": provenance,
            "parentReferenceRepair": repair,
            "queryPlanSha256": binding["queryPlanSha256"],
            "bindingSha256": base.sha(state / "binding.json"),
            "rawObservationsSha256": base.sha(state / f"measure-{PHASE}.json"),
            "recomputationSourceFilesSha256": {name: base.sha(repo / name) for name in RECOMPUTATION_FILES},
        },
        "clientAndModelContext": {
            "measurementClientSeparatelyCaptured": False,
            "modelMetadataSeparatelyRead": False,
            "parentClientMetadata": provenance["client"],
            "parentQuestionEmbeddingModel": parent_record["models"]["before"],
            "frozenQueryVectorsSha256": repair["oracleSafe"]["queryVectorsSha256"],
            "limits": (
                "Client and embedding-model metadata are inherited from the verified parent. "
                "This epoch reused cached query vectors and made no embedding or model calls."
            ),
        },
        "timing": timing,
        "inventory": inventory,
        "costAccounting": cost,
        "independentAudit": audit,
        "routed": routed,
        "global": globals_,
        "globalObservationIsSharedAcrossLibrarySizes": True,
        "routedArmComparisons": {
            size: compare_arms(values["split"], values["unsplit"]) for size, values in routed.items()
        },
        "globalArmComparison": compare_arms(globals_["split"], globals_["unsplit"]),
        "sizeBenefitVersusSupplement5k": benefit,
        "initialSizeBenefitVersusInitial5k": parent_record["sizeBenefitVersusFinal5k"],
        "initialToSupplementDrift": drifts,
        "questionZeroSentinelComparisons": sentinel_comparisons,
        "limits": [
            (
                "This is one additional epoch on one physical range and one index build per arm; "
                "three repeats are descriptive, not independent workloads or confidence intervals."
            ),
            (
                "Each size has one assigned nonnested library and a different question set at fixed total inventory. "
                "Size, selectivity, content and query difficulty are not independently varied."
            ),
            (
                "The full-suite absolute RU benefit uses this epoch's own 5,000-vector control; "
                "the initial epoch's comparison is preserved separately."
            ),
            (
                "Matched question/repeat drift is an observed time-window change, not evidence of a causal maturity "
                "mechanism or permanent settling, even when serving modes and documents read are unchanged."
            ),
            (
                "Question-zero sentinel comparisons cover one fixed question per scope and remain separate "
                "from the twenty-question summaries."
            ),
            (
                "The global control omits library equality and partition prefix, has a different eligible answer set, "
                "and uses one fixed question. A recall-floor failure precludes a quality-equivalent cost conclusion."
            ),
            (
                "No prefix-omission diagnostic was repeated; this epoch adds no evidence about implicit partition "
                "pruning or the earlier shape comparison."
            ),
            (
                "Client connectivity or sleep transitions were reported between parent windows. Client latency across "
                "epochs also depends on network, connection, cache and phase timing and cannot be attributed solely "
                "to index aging."
            ),
            (
                "Large-library flatness as unrelated background grows remains untested; all new large-library "
                "epochs use the same total inventory."
            ),
        ],
    }
    require(set(references) and len(references) == 322, "parent reference set coverage differs")
    return result


def render_tables(report):
    lines = [
        "# Full late-suite measurement tables",
        "",
        "This is a separate approved epoch; the initial record remains unchanged.",
    ]

    def table(title, note, headings, values):
        lines.extend(
            [
                "",
                "## " + title,
                "",
                note,
                "",
                "| " + " | ".join(headings) + " |",
                "|" + "|".join("---" for _ in headings) + "|",
            ]
        )
        lines.extend("| " + " | ".join(map(str, row)) + " |" for row in values)

    def bounds(value):
        return "unavailable" if not value else base.fmt(value[0]) + " to " + base.fmt(value[1])

    rows = []
    for size, arms in sorted(report["routed"].items(), key=lambda pair: int(pair[0])):
        for arm, value in arms.items():
            rows.append(
                [
                    size,
                    arm,
                    base.fmt(value["ruMedian"]),
                    bounds(value["ruRunRange"]),
                    base.fmt(value["retrievedMedian"]),
                    base.fmt(value["msMedian"]),
                    base.fmt(value["recallAt10Mean"]),
                    base.modes(value),
                    base.quality_label(value),
                ]
            )
    table(
        "Routed twenty-question suite",
        "Each value is the median of question-level three-repeat medians; recall is averaged "
        "over the full eligible question/repeat set.",
        [
            "Vectors",
            "Arm",
            "RU",
            "RU run range",
            "Documents read",
            "Latency ms",
            "Recall@10",
            "Observed index",
            "Status",
        ],
        rows,
    )
    table(
        "Shared global control",
        "One fixed global question, three repeats per arm. This observation is shared across library sizes "
        "and is not a matched scope-omission effect.",
        ["Arm", "RU", "Documents read", "Latency ms", "Recall@10", "Observed index", "Status"],
        [
            [
                arm,
                base.fmt(v["ruMedian"]),
                base.fmt(v["retrievedMedian"]),
                base.fmt(v["msMedian"]),
                base.fmt(v["recallAt10Mean"]),
                base.modes(v),
                base.quality_label(v),
            ]
            for arm, v in report["global"].items()
        ],
    )
    table(
        "Absolute benefit versus this epoch's 5k control",
        "Savings = unsplit RU minus split RU. Conservative repeat endpoints are descriptive, not confidence intervals.",
        ["Vectors", "5k saving RU", "Saving RU", "Change %", "Saving ratio range", "Classification"],
        [
            [
                size,
                base.fmt(v["baselineSavingsRu"]),
                base.fmt(v["savingsRu"]),
                base.fmt(v["savingsChangePct"]),
                bounds(v["repeatSavingsRatioRange"]),
                v["classification"] or "inconclusive",
            ]
            for size, v in report["sizeBenefitVersusSupplement5k"].items()
        ],
    )
    table(
        "Initial to full-late drift",
        "The aggregate change and matched question/repeat changes are distinct summaries of the same frozen pairs.",
        [
            "Vectors / scope",
            "Arm",
            "Initial RU",
            "Full-late RU",
            "RU change %",
            "Paired RU change % range",
            "Documents change %",
            "Latency change %",
            "Mode change",
            "Quality accepted, initial / late",
        ],
        [
            [
                size,
                arm,
                base.fmt(v["initialSummary"]["ruMedian"]),
                base.fmt((report["global"] if size == "global" else report["routed"][size])[arm]["ruMedian"]),
                base.fmt(v["centralSummaryChange"]["ruDifferencePct"]),
                bounds(v["pairedQuestionRepeatChange"]["ru"]["pairedPctRange"]),
                base.fmt(v["centralSummaryChange"]["retrievedDifferencePct"]),
                base.fmt(v["centralSummaryChange"]["msDifferencePct"]),
                v["observedModeSignatureChanged"],
                str(v["initialQualityAccepted"]) + " / " + str(v["supplementQualityAccepted"]),
            ]
            for size, arms in report["initialToSupplementDrift"].items()
            for arm, v in arms.items()
        ],
    )
    table(
        "Variation across frozen questions",
        "Question-level ranges preserve outliers; full per-question repeat values are in the JSON record.",
        [
            "Vectors / scope",
            "Arm",
            "Question median RU range",
            "All-repeat RU range",
            "Individual recall range",
            "Questions with recall below 1",
        ],
        [
            [
                size,
                arm,
                bounds(v["questionVariation"]["questionMedianRuRange"]),
                bounds(v["questionVariation"]["allRepeatRuRange"]),
                bounds(v["questionVariation"]["allQuestionRepeatRecallRange"]),
                v["questionVariation"]["questionsWithAnyRecallBelowOne"],
            ]
            for size, arms in [*report["routed"].items(), ("global", report["global"])]
            for arm, v in arms.items()
        ],
    )
    sentinel = []
    for size, arms in report["questionZeroSentinelComparisons"].items():
        for arm, value in arms.items():
            for phase, old in value["priorWindows"].items():
                current = value["supplementQuestionZero"]
                sentinel.append(
                    [
                        size,
                        arm,
                        phase,
                        base.fmt(old["summary"]["ruMedian"]),
                        base.fmt(current["ruMedian"]),
                        base.fmt(old["centralSummaryChange"]["ruDifferencePct"]),
                        old["observedModeSignatureChanged"],
                        base.quality_label(current),
                    ]
                )
    table(
        "Question-zero sentinel comparisons",
        "These are the same single question and repeat ordinals, separately from the full twenty-question result.",
        [
            "Vectors / scope",
            "Arm",
            "Parent window",
            "Parent RU",
            "Full-late q0 RU",
            "RU change %",
            "Mode change",
            "Current status",
        ],
        sentinel,
    )
    lines.extend(["", "## Scientific limits", ""])
    lines.extend("- " + value for value in report["limits"])
    return "\n".join(lines) + "\n"


def write_report(state, parent, output, tables, *, repo=REPO):
    require(not Path(output).is_symlink() and not Path(tables).is_symlink(), "report destinations must be new files")
    output, tables, repo = Path(output).resolve(), Path(tables).resolve(), Path(repo).resolve()
    require(
        output != tables and not output.exists() and not tables.exists(),
        "report destinations already exist or coincide",
    )
    protected = base.PROTECTED | SOURCE_FILES | RECOMPUTATION_FILES | {PARENT_RECORD, PARENT_TABLE}
    require(
        output not in {repo / name for name in protected} and tables not in {repo / name for name in protected},
        "protected artifact destination",
    )
    require(
        output.is_relative_to(repo / "records") and tables.is_relative_to(repo / "docs"),
        "final destinations must be records and docs",
    )
    result = build_report(state, parent, repo=repo)
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    markdown = render_tables(result)
    base.privacy_guard(encoded + markdown)
    base.atomic_write(output, encoded)
    base.atomic_write(tables, markdown)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--parent-state", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tables", required=True, type=Path)
    args = parser.parse_args()
    try:
        write_report(args.state, args.parent_state, args.out, args.tables)
    except InvalidEvidence as exc:
        print("not published: " + str(exc))
        return 2
    except Exception as exc:
        print("not published: validation could not finish (" + type(exc).__name__ + ")")
        return 3
    print(json.dumps({"written": True, "partial": False, "annObservations": 726}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
