"""Recompute content-free large-library receipts; publishing requires the late check.

This module performs local reads only. It is separate from the frozen driver.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.scale_ablation_large_stats import (
    compare_arms,
    compare_routes,
    compare_size_benefit,
    compare_small_drift,
    summarize_shape,
    summarize_subset,
)

REPO = Path(__file__).resolve().parents[1]
ARMS = ("split", "unsplit")
NEW = ("large-p050000", "large-p100000", "large-p010000", "large-p025000")
SMALL = ("scale-p01", "scale-p10")
SIZES = dict(zip(NEW, (50000, 100000, 10000, 25000), strict=True)) | dict(zip(SMALL, (1000, 5000), strict=True))
SOURCES = {
    "scripts/scale_ablation_large.py",
    "scripts/scale_ablation_large_stats.py",
    "scripts/scale_ablation.py",
    "scripts/scale_ablation_stats.py",
    "scripts/canary.py",
    "scripts/scale_probe.py",
    "src/fli/query.py",
    "src/fli/embedding.py",
    "cosmos/chunks.container.json",
    "docs/scale-ablation-large-library-protocol.md",
}
INPUT_HASHES = {
    "assignmentSha256",
    "corpusGateSha256",
    "oldQuestionSourcesSha256",
    "oldVectorsSha256",
    "originalPrefixSha256",
    "preparationProtocolSha256",
    "preparerSha256",
    "priorE68ReceiptSha256",
    "priorFullQuestionSourcesSha256",
    "priorFullQuestionVectorsSha256",
    "questionOrdinalsSha256",
    "questionSourcesSha256",
    "rowOrdinalManifestSha256",
    "selectedRawRowsSha256",
}
HEX = re.compile(r"[a-f0-9]{64}\Z")
PROTECTED = SOURCES | {
    "records/scale-ablation-20261111.json",
    "records/scale-probe-20261029.json",
    "records/scale-ablation-large-preflight-20261112.json",
    "docs/scale-ablation-results.md",
    "docs/scale-ablation.md",
    "docs/scale-ablation-large-library-protocol.md",
}
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}\Z")
MODES = {"diskann": "DiskANN", "quantizedflat": "QuantizedFlat", "flat": "Flat"}
REPAIR_SOURCES = {
    "scripts/scale_ablation_large_resume.py",
    "scripts/scale_ablation_large_exact.py",
    "docs/scale-ablation-large-reference-repair.md",
}
PROTECTED |= REPAIR_SOURCES


class InvalidEvidence(ValueError):
    """Validation reason that contains no private values."""


def require(condition, message):
    if not condition:
        raise InvalidEvidence(message)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def numbers(value, keys):
    return {key: value[key] for key in keys if numeric(value.get(key))}


def stamp(value):
    require(isinstance(value, str), "required timestamp unavailable")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidEvidence("invalid timestamp") from None
    require(result.tzinfo is not None, "timestamp lacks timezone")
    return result.astimezone(UTC)


def source_provenance(state, repo, config, binding):
    files = binding.get("sourceFiles", {})
    require(set(files) == SOURCES, "measurement source membership differs")
    current, archived = {}, {}
    for relative, value in files.items():
        require(isinstance(value, str) and HEX.fullmatch(value), "invalid source digest")
        require((repo / relative).resolve().is_relative_to(repo), "unsafe source member")
        current[relative] = (repo / relative).is_file() and sha(repo / relative) == value
        frozen = state / "frozen-source" / relative
        archived[relative] = frozen.is_file() and sha(frozen) == value
    require(all(archived.values()), "frozen measurement-source copies cannot be verified")
    commits = {}
    for key, source in (("priorE68", config.get("e68Commit")), ("measurementDriver", config.get("driverCommit"))):
        require(isinstance(source, str) and re.fullmatch(r"[a-f0-9]{7,40}", source), "invalid source commit")
        commits[key] = source
    prior_hash = binding.get("e68ReceiptSha256")
    require(isinstance(prior_hash, str) and HEX.fullmatch(prior_hash), "invalid historical receipt digest")
    require(sha(repo / "records/scale-ablation-20261111.json") == prior_hash, "historical E68 receipt changed")
    client = binding.get("client", {})
    require(
        all(isinstance(client.get(k), str) and VERSION.fullmatch(client[k]) for k in ("python", "azureCosmos")),
        "invalid measured client versions",
    )
    return {
        "recordedMeasurementSourceFilesSha256": files,
        "frozenCopiesVerified": True,
        "currentSourceMatchesRecorded": current,
        "postMergeCurrentMatchRequired": False,
        "commits": commits,
        "historicalE68ReceiptSha256": prior_hash,
        "client": {key: client[key] for key in ("python", "azureCosmos")},
        "recomputationSourceFilesSha256": {
            "scripts/" + name: sha(Path(__file__).with_name(name))
            for name in ("scale_ablation_large_report.py", "scale_ablation_large_stats.py", "scale_ablation_stats.py")
        },
    }


def verify_inputs(state, config, binding, manifest, verify_payloads):
    require(binding.get("configSha256") == digest(config), "private configuration binding changed")
    require(binding.get("inputManifestSha256") == sha(Path(config["inputManifest"])), "input manifest changed")
    hashes = manifest.get("hashes", {})
    require(
        set(hashes) == INPUT_HASHES and all(isinstance(v, str) and HEX.fullmatch(v) for v in hashes.values()),
        "input digest schema differs",
    )
    require(manifest.get("complete") is True and manifest.get("seed") == 20261029, "prepared inputs are incomplete")
    require(manifest.get("sourceSelectionRows") == 185000, "prepared selection size differs")
    checked = {}
    for field, key in (
        ("newQuestionSourcesFile", "questionSourcesSha256"),
        ("oldQuestionSourcesFile", "oldQuestionSourcesSha256"),
        ("oldVectorsFile", "oldVectorsSha256"),
    ):
        require(sha(Path(manifest[field])) == hashes[key], "frozen question input changed")
    require(sha(state / "assignment.ndjson") == hashes["assignmentSha256"], "assignment changed")
    require(sha(state / "question-ordinals.json") == hashes["questionOrdinalsSha256"], "question ordinals changed")
    vector_hash = read(state / "question-vector-hash.json", {}).get("sha256")
    require(
        isinstance(vector_hash, str)
        and HEX.fullmatch(vector_hash)
        and sha(state / "question-vectors.json") == vector_hash,
        "query vector cache changed",
    )
    require(set(manifest.get("libraries", {})) == set(NEW), "prepared library membership differs")
    for library in NEW:
        entry = manifest["libraries"][library]
        require(
            entry.get("size") == SIZES[library]
            and entry.get("questions") == 20
            and entry.get("chunksPerDocument") == 10,
            "prepared library layout differs",
        )
        item = numbers(
            entry, ("size", "documents", "chunksPerDocument", "questions", "sourceCandidateQuestions", "payloadBytes")
        )
        for key in ("sha256", "offsetsSha256", "publishedVectorValuesSha256", "textSequenceSha256"):
            require(isinstance(entry.get(key), str) and HEX.fullmatch(entry[key]), "invalid prepared library digest")
            item[key] = entry[key]
        if verify_payloads:
            require(
                sha(Path(entry["payloadFile"])) == entry["sha256"]
                and sha(Path(entry["offsetsFile"])) == entry["offsetsSha256"],
                "prepared payload or offset file changed",
            )
        checked[str(SIZES[library])] = item
    return {
        "hashes": hashes,
        "queryVectorsSha256": vector_hash,
        "libraries": checked,
        "payloadsVerifiedAtReport": verify_payloads,
    }


def model_metadata(state):
    expected = {"model": "text-embedding-3-large", "version": "1", "format": "OpenAI", "dimensions": 3072}
    result = {}
    for label, suffix in (
        ("before", "before"),
        ("afterQuestions", "after-questions"),
        ("afterMain", "after-main"),
        ("afterLate", "after-late"),
        ("after", "after"),
    ):
        value = read(state / f"embedding-model-{suffix}.json")
        if value is None:
            result[label] = {"available": False}
        else:
            require(value == expected, "embedding model metadata differs from the frozen binding")
            result[label] = {"available": True} | expected
    require(
        result["before"]["available"] and result["afterQuestions"]["available"],
        "question embedding model gates unavailable",
    )
    usage = read(state / "embedding-usage.json", {})
    result["queryEmbeddingUsage"] = numbers(usage, ("requests", "prompt_tokens", "throttled", "retries"))
    result["queryEmbeddingUsage"]["tokensReported"] = usage.get("tokens_reported") is True
    result["passageEmbeddingCalls"] = 0
    return result


def phase_rows(state):
    return {
        phase: read(state / f"measure-{phase}.json", {"rows": []})["rows"]
        for phase in ("baseline", "routed", "no-prefix", "global", "immediate", "late")
    }


def reference_helper():
    from scripts import scale_ablation_large_exact

    return scale_ablation_large_exact


def oracle_safe_summary(value):
    require(
        value.get("dimensions") == 3072 and value.get("k") == 10 and value.get("topRetained") == 11,
        "offline oracle numerical shape differs",
    )
    result = numbers(
        value,
        (
            "dimensions",
            "k",
            "topRetained",
            "populationVectors",
            "populationLibraries",
            "measuredLibraries",
            "libraryReferenceQuestions",
            "globalReferenceQuestions",
            "sourcePrefixRows",
            "sourcePrefixBytes",
            "roundedCorpusSubnormalValues",
            "minimumBoundaryMargin",
            "boundaryTolerance",
            "referencesWithNumericModelTopTenDifferences",
            "networkCalls",
            "databaseMutations",
        ),
    )
    for name in ("sourcePrefixSha256", "queryVectorsSha256"):
        require(isinstance(value.get(name), str) and HEX.fullmatch(value[name]), "invalid offline oracle safe digest")
        result[name] = value[name]
    for name in ("payloadSha256", "librarySizeHistogram"):
        source = value.get(name, {})
        require(isinstance(source, dict), "invalid offline oracle safe map")
        result[name] = {}
        for label, entry in source.items():
            require(isinstance(label, str) and re.fullmatch(r"[0-9]+", label), "invalid offline oracle safe map role")
            if name == "payloadSha256":
                require(isinstance(entry, str) and HEX.fullmatch(entry), "invalid offline payload digest")
            else:
                require(type(entry) is int and entry >= 0, "invalid offline library histogram")
            result[name][label] = entry
    norms = value.get("corpusNormRange")
    require(
        isinstance(norms, list) and len(norms) == 2 and all(numeric(v) and v > 0 for v in norms),
        "invalid offline corpus norm range",
    )
    result["corpusNormRange"] = norms
    variants = value.get("variants")
    require(variants == ["float16", "originalFloat64", "float16CorpusFtz"], "offline numeric variants differ")
    result["variants"] = variants
    result["method"] = "float16 corpus without FTZ; frozen FTZ query cache; exhaustive float64 cosine"
    return result


def numeric_sensitivity(oracle, derived_rows):
    """Count precision checks without copying identities or per-question details."""
    result = {}
    references = [ref for refs in oracle.get("references", {}).values() for ref in refs]
    for model in ("originalFloat64", "float16CorpusFtz"):
        values = []
        for ref in references:
            variants = ref.get("numericVariants", {})
            require(set(variants) == {"originalFloat64", "float16CorpusFtz"}, "offline numeric model coverage differs")
            variant = variants[model]
            require(
                isinstance(variant.get("sameTopTenSet"), bool) and numeric(variant.get("boundaryMargin")),
                "offline numeric model diagnostics differ",
            )
            values.append(variant)
        coverage = [row["referenceDerivation"]["numericVariantCandidateCoverage"][model] for row in derived_rows]
        require(all(type(value) is bool for value in coverage), "invalid numeric-model candidate coverage")
        result[model] = {
            "referenceQuestions": len(values),
            "topTenSetAgreementsWithPrimary": sum(value["sameTopTenSet"] for value in values),
            "topTenSetDifferencesFromPrimary": sum(not value["sameTopTenSet"] for value in values),
            "minimumBoundaryMargin": min((value["boundaryMargin"] for value in values), default=None),
            "exactObservationsWithVariantCoveredByCandidateUnion": sum(coverage),
            "exactObservationsWithoutVariantCoveredByCandidateUnion": sum(not value for value in coverage),
        }
    return result


def reference_repair(state, config, rows, repo):
    """Validate every retained exact observation and derive copies, never raw rewrites."""
    path = state / "reference-repair-binding.json"
    if not path.exists():
        return {}, {"enabled": False}
    binding = read(path)
    require(binding.get("version") == 1, "unsupported exact-reference repair binding")
    require(
        binding.get("repair") == "unchanged-cosmos-query-independent-exhaustive-candidate-validation",
        "unsupported exact-reference repair method",
    )
    require(binding.get("originalBindingSha256") == sha(state / "binding.json"), "original measurement binding changed")
    require(binding.get("offlineOracleSha256") == sha(state / "offline-exact.json"), "offline oracle cache changed")
    source_files = binding.get("sourceFiles", {})
    require(set(source_files) == REPAIR_SOURCES, "reference-repair source membership differs")
    current_matches = {}
    for relative, expected in source_files.items():
        require(isinstance(expected, str) and HEX.fullmatch(expected), "invalid reference-repair source digest")
        archive = state / "repair-source" / relative
        require(archive.is_file() and sha(archive) == expected, "archived reference-repair source changed")
        current_matches[relative] = (repo / relative).is_file() and sha(repo / relative) == expected
    oracle = read(state / "offline-exact.json")
    helper = reference_helper()
    try:
        helper.validate_oracle(config, state, oracle)
    except (ValueError, KeyError, TypeError, OSError):
        raise InvalidEvidence("offline exact oracle validation failed") from None
    retained = read(state / "exact-reference-derivations.json", {}).get("rows", [])
    require(isinstance(retained, list), "private reference derivation records are invalid")
    by_key = {}
    for entry in retained:
        key_ = tuple(entry.get("key", []))
        require(key_ not in by_key, "private reference derivation keys are duplicated")
        by_key[key_] = entry
    normalized, counts, margins = {}, Counter(), []
    changed, reordered, failure_verified = 0, 0, False
    for phase in ("baseline", "routed", "global"):
        for row in rows[phase]:
            if row.get("kind") != "exact":
                continue
            key_ = (phase, *key(row))
            entry = by_key.get(key_)
            require(
                entry is not None and entry.get("rawRowSha256") == digest(row),
                "raw exact reference or retained derivation changed",
            )
            try:
                derived = helper.derive_reference(row, oracle)
            except (ValueError, KeyError, TypeError):
                raise InvalidEvidence("an exact candidate union failed exhaustive validation") from None
            require(derived == entry.get("derived"), "derived exact reference differs from the retained proof")
            require(
                len(derived.get("result_ids", [])) == 10 and len(set(derived["result_ids"])) == 10,
                "derived reference is not ten unique results",
            )
            normalized[key_] = derived
            count = len(row.get("result_ids", []))
            counts[str(count)] += 1
            changed += count > 10
            reordered += row.get("result_ids") != derived.get("result_ids")
            margin = derived.get("referenceDerivation", {}).get("boundaryMargin")
            if numeric(margin):
                margins.append(margin)
            failure = binding.get("initialFailure", {})
            if phase == failure.get("phase") and digest(row) == failure.get("rowSha256"):
                require(
                    count == 20
                    and row.get("arm") == "unsplit"
                    and row.get("library") == "large-p025000"
                    and row.get("query") == 0,
                    "retained initial pagination failure differs",
                )
                expected_failure = {
                    "phase": phase,
                    "rowSha256": digest(row),
                    "arm": "unsplit",
                    "librarySize": 25000,
                    "questionOrdinal": 0,
                    "rawResultCount": count,
                    "requestCharge": row["request_charge"],
                    "retrievedDocuments": row["retrieved_document_count"],
                    "milliseconds": row["milliseconds"],
                }
                require(
                    failure == expected_failure, "initial pagination-failure metadata differs from the raw observation"
                )
                failure_verified = True
    require(failure_verified, "initial reference-pagination failure cannot be verified")
    require(set(by_key) == set(normalized), "reference derivation coverage differs from retained exact observations")
    oracle_hash = oracle.get("oracleSha256")
    require(isinstance(oracle_hash, str) and HEX.fullmatch(oracle_hash), "invalid offline oracle body digest")
    safe = oracle.get("safe", {})
    safe_public = oracle_safe_summary(safe)
    numpy_version = binding.get("numericalClient", {}).get("numpy")
    require(isinstance(numpy_version, str) and VERSION.fullmatch(numpy_version), "invalid numerical client version")
    return normalized, {
        "enabled": True,
        "rawObservationsUnchanged": True,
        "exactSqlAndRoutingUnchanged": True,
        "oracleFileSha256": binding["offlineOracleSha256"],
        "oracleBodySha256": oracle_hash,
        "oracleSafeSummarySha256": digest(safe),
        "oracleSafe": safe_public,
        "numericModelSensitivity": numeric_sensitivity(oracle, normalized.values()),
        "numericalClient": {"numpy": numpy_version},
        "bindingSha256": sha(path),
        "derivationsSha256": sha(state / "exact-reference-derivations.json"),
        "recordedSourceFilesSha256": source_files,
        "archivedSourcesVerified": True,
        "currentSourceMatchesRecorded": current_matches,
        "initialFailure": numbers(
            binding.get("initialFailure", {}),
            ("librarySize", "questionOrdinal", "rawResultCount", "requestCharge", "retrievedDocuments", "milliseconds"),
        )
        | {"rawRowSha256": binding["initialFailure"]["rowSha256"]},
        "exactReferencesValidated": len(normalized),
        "referencesWithMoreThanTenCandidates": changed,
        "referencesReorderedOrReduced": reordered,
        "rawCandidateCountHistogram": dict(sorted(counts.items())),
        "minimumDerivedBoundaryMargin": min(margins) if margins else None,
        "scope": (
            "Every baseline, routed and global exact candidate union is checked, "
            "including original ten-result observations."
        ),
        "method": (
            "Independent exhaustive local float16 cosine truth selects the ranked ten "
            "only after proving complete candidate coverage."
        ),
        "precisionLimit": (
            "The primary reference rounds published corpus values to float16 without corpus FTZ, "
            "then computes float64 cosine against the frozen FTZ query vectors. Original unrounded "
            "float64 corpus values and corpus FTZ are sensitivity checks, not replacement references."
        ),
    }


def select(rows, arm, library, shape, kind):
    return [
        row
        for row in rows
        if (row.get("arm"), row.get("library"), row.get("shape"), row.get("kind")) == (arm, library, shape, kind)
    ]


def response_rows(row):
    responses = row.get("response_metrics")
    if isinstance(responses, list) and responses:
        return responses
    attempts = row.get("attempts", [])
    return attempts[-1].get("rawResponses", []) if attempts and isinstance(attempts[-1], dict) else []


def data_report_usable(report):
    if not isinstance(report, dict):
        return False
    entries = report.get("UtilizedVectorIndexes")
    return (
        entries is None
        or isinstance(entries, list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("IndexSpec"), dict)
            and isinstance(item["IndexSpec"].get("VectorIndexType"), str)
            and bool(item["IndexSpec"]["VectorIndexType"])
            for item in entries
        )
    )


def guarded_diagnostics(original, rows):
    """An empty non-data report cannot stand in for a missing data-response header."""
    value = dict(original)
    mode_counts, unknown_counts = Counter(), Counter()
    no_vector, data_count, missing, usable_queries, complete_queries = 0, 0, 0, 0, 0
    for row in rows:
        data = [response for response in response_rows(row) if response.get("retrieved") is not None]
        usable_reports = 0
        for response in data:
            data_count += 1
            report = response.get("indexUtilization")
            decoded = numeric(response.get("retrieved")) and data_report_usable(report)
            recognized = decoded and all(
                item["IndexSpec"]["VectorIndexType"].casefold() in MODES
                for item in report.get("UtilizedVectorIndexes") or []
            )
            good = decoded and recognized
            usable_reports += good
            missing += not good
            if not decoded:
                continue
            entries = report.get("UtilizedVectorIndexes") or []
            no_vector += not entries
            for entry in entries:
                spec = entry["IndexSpec"]
                raw = spec["VectorIndexType"]
                mode = MODES.get(raw.casefold(), "Unrecognized")
                unknown = hashlib.sha256(json.dumps(raw).encode()).hexdigest() if mode == "Unrecognized" else None
                if unknown:
                    unknown_counts[unknown] += 1
                paths = spec.get("ShardKeys", [])
                safe_paths = (
                    tuple(path if path in {"/libraryId", "/documentId", "/embedding"} else "<other>" for path in paths)
                    if isinstance(paths, list)
                    else ("<other>",)
                )
                mode_counts[(mode, safe_paths, unknown)] += 1
        usable_queries += usable_reports > 0
        complete_queries += bool(data) and usable_reports == len(data)
    value["frozenDiagnosticsUsable"] = bool(original.get("diagnosticsUsable"))
    value["httpReportsWithoutUtilizedVectorIndex"] = value.get("indexReportsWithoutUtilizedVectorIndex", 0)
    value["indexReportsWithoutUtilizedVectorIndex"] = no_vector
    value["vectorIndexes"] = [
        {"type": mode, "shardKeys": list(paths), "occurrences": count}
        | ({"unrecognizedTypeSha256": unknown} if unknown else {})
        for (mode, paths, unknown), count in sorted(mode_counts.items(), key=lambda item: repr(item[0]))
    ]
    value["unrecognizedTypeValues"] = [
        {"sha256": key, "occurrences": count} for key, count in sorted(unknown_counts.items())
    ]
    value["dataResponseDiagnosticsComplete"] = bool(rows) and complete_queries == len(rows)
    value["unexpectedVectorModeObserved"] = bool(unknown_counts)
    value["diagnosticsUsable"] = bool(rows) and usable_queries == len(rows) and not unknown_counts
    value["dataResponseDiagnosticsPartial"] = (
        value["diagnosticsUsable"] and not value["dataResponseDiagnosticsComplete"]
    )
    value["modeEvidenceCoverage"] = (
        "observed union of available data-bearing reports; missing continuation headers may hide additional modes"
    )
    value["dataResponsesChecked"] = data_count
    value["dataResponsesWithMissingOrUnusableIndexReport"] = missing
    value["queriesWithUsableDataResponseDiagnostics"] = usable_queries
    value["dataDiagnosticGuardApplied"] = True
    return value


def apply_diagnostic_guard(summary, ann_rows, references):
    value = dict(summary)
    value["indexDiagnostics"] = guarded_diagnostics(summary["indexDiagnostics"], ann_rows)
    value["indexDiagnosticsByRun"] = [
        guarded_diagnostics(info, [row for row in ann_rows if row.get("run") == run])
        for run, info in enumerate(summary["indexDiagnosticsByRun"])
    ]
    value["referenceIndexDiagnostics"] = guarded_diagnostics(summary["referenceIndexDiagnostics"], references)
    signatures = [
        (
            tuple(
                (item["type"], tuple(item["shardKeys"]), item.get("unrecognizedTypeSha256"))
                for item in info["vectorIndexes"]
            ),
            info["indexReportsWithoutUtilizedVectorIndex"] > 0,
        )
        for info in value["indexDiagnosticsByRun"]
    ]
    value["modeSignatureStableAcrossRuns"] = (
        all(info["diagnosticsUsable"] for info in value["indexDiagnosticsByRun"]) and len(set(signatures)) == 1
    )
    value["frozenQualityAccepted"] = summary["qualityAccepted"]
    value["complete"] = (
        all(
            value[key]
            for key in (
                "querySetComplete",
                "referenceSetComplete",
                "resultsComplete",
                "metricsComplete",
                "ownershipComplete",
            )
        )
        and value["indexDiagnostics"]["diagnosticsUsable"]
    )
    value["qualityAccepted"] = value["complete"] and value["recallAtFloor"]
    value["dataDiagnosticGuardApplied"] = True
    return value


def key(row):
    return tuple(row.get(field) for field in ("arm", "library", "shape", "kind", "query", "run"))


def expected_rows(libraries, shape, *, questions=20, exact=True):
    return Counter(
        (arm, lib, shape, kind, query, run)
        for arm in ARMS
        for lib in libraries
        for query in range(questions)
        for kind, repeats in (("vector", 3), ("exact", 1 if exact else 0))
        for run in range(repeats)
    )


def coverage_and_plan(rows, selected, plan, inventory, partial):
    limits, diagnostic = {}, {}
    for library in ("scale-p10", "large-p050000", "large-p100000"):
        first = [row for row in rows["no-prefix"] if row.get("library") == library and row.get("query") == 0]
        triggers = [
            row
            for row in first
            if numeric(row.get("retrieved_document_count")) and row["retrieved_document_count"] >= inventory
        ]
        entry = plan.get(library, {})
        limit = 5 if triggers else 20
        valid = (
            entry.get("questions") == limit
            and entry.get("queryOrdinals") == list(range(limit))
            and entry.get("inventoryVectors") == inventory
        )
        if not partial:
            require(valid and len(first) == 6, "adaptive routing diagnostic decision cannot be verified")
        limits[library] = entry.get("questions", limit) if entry.get("questions") in (5, 20) else limit
        diagnostic[str(SIZES[library])] = {
            "questions": limits[library],
            "queryOrdinals": list(range(limits[library])),
            "wholeContainerReadTriggeredCap": bool(triggers),
            "triggerObservations": len(triggers),
            "decisionVerified": valid,
        }
    libraries = [*SMALL, *selected]
    expected = {
        "baseline": expected_rows(SMALL, "routed"),
        "routed": expected_rows(libraries, "routed"),
        "global": expected_rows(("global",), "global", questions=1),
        "immediate": expected_rows(libraries, "routed", questions=1, exact=False)
        + expected_rows(("global",), "global", questions=1, exact=False),
        "late": expected_rows(libraries, "routed", questions=1, exact=False)
        + expected_rows(("global",), "global", questions=1, exact=False),
        "no-prefix": sum(
            (expected_rows((lib,), "no-prefix", questions=n, exact=False) for lib, n in limits.items()), Counter()
        ),
    }
    coverage = {}
    for phase, desired in expected.items():
        observed = Counter(key(row) for row in rows[phase])
        complete = observed == desired
        coverage[phase] = {
            "expectedRows": sum(desired.values()),
            "observedRows": len(rows[phase]),
            "missingRows": sum((desired - observed).values()),
            "extraOrDuplicateRows": sum((observed - desired).values()),
            "structurallyComplete": complete,
        }
        if not partial:
            require(complete, "measurement ordinals are incomplete or duplicated")
    return coverage, diagnostic, limits


def inventories(state, selected, inventory, partial):
    result = {}
    for phase in ("before", "final", "late"):
        raw = read(state / f"inventory-{phase}.json", {})
        result[phase] = {"available": bool(raw), "arms": {}}
        for arm in ARMS:
            value = raw.get("arms", {}).get(arm, {})
            expected = 244773 if phase == "before" else inventory
            valid = (
                value.get("vectors") == expected
                and value.get("libraries") == 1014 + (0 if phase == "before" else len(selected))
                and value.get("physicalRanges") == 1
                and value.get("policyMatches") is True
                and value.get("matchesExpected") is True
            )
            counts = value.get("perLibrary", {})
            valid = valid and all(
                counts.get(lib) == (0 if phase == "before" and lib in NEW else SIZES[lib])
                for lib in (*SMALL, *selected)
            )
            result[phase]["arms"][arm] = numbers(value, ("vectors", "libraries", "physicalRanges")) | {
                "policyMatches": value.get("policyMatches") is True,
                "matchesExpected": valid,
                "perLibrary": {str(SIZES[lib]): counts[lib] for lib in (*SMALL, *selected) if numeric(counts.get(lib))},
            }
            if not partial:
                require(valid, "actual inventory or policy gates do not match")
    return result


def timing(status, manifest, rows, partial, state):
    started = stamp(manifest["preparationStartedAt"])
    upper = stamp(status["lastWriteUpperBoundAt"]) if status.get("lastWriteUpperBoundAt") else None
    ack = stamp(status["lastWriteAckAt"]) if status.get("lastWriteAckAt") else None
    main_end = stamp(status["mainCompletedAt"]) if status.get("mainCompletedAt") else None
    tolerance = timedelta(milliseconds=2)
    periods, durations_valid = {}, True
    result = {
        "startedAt": started.isoformat(),
        "mainComplete": status.get("mainComplete") is True,
        "complete": status.get("complete") is True,
        "chronologyToleranceMilliseconds": 2,
        "queryStartEstimate": "client completion timestamp minus recorded elapsed milliseconds",
    }
    for phase, values in rows.items():
        periods[phase] = []
        for row in values:
            ended = stamp(row["recordedAt"])
            valid = numeric(row.get("milliseconds"))
            durations_valid &= valid
            began = ended - timedelta(milliseconds=row["milliseconds"] if valid else 0)
            periods[phase].append((began, ended))
        for label, anchor in (("LastWriteUpperBound", upper), ("LastWriteAck", ack)):
            if anchor and periods[phase]:
                for which, index in (("QueryStart", 0), ("QueryCompletion", 1)):
                    ages = [(pair[index] - anchor).total_seconds() / 3600 for pair in periods[phase]]
                    result[phase + which + "HoursSince" + label + "Range"] = [min(ages), max(ages)]
    if ack:
        result["lastWriteAckClientObservedAt"] = ack.isoformat()
    if upper:
        result["lastWriteConservativeUpperBoundAt"] = upper.isoformat()
    if main_end:
        result["mainElapsedHours"] = (main_end - started).total_seconds() / 3600
        result["mainWithinTwelveHours"] = 0 <= result["mainElapsedHours"] <= 12
    if status.get("lateCompletedAt"):
        result["totalElapsedHoursIncludingLateWait"] = (
            stamp(status["lateCompletedAt"]) - started
        ).total_seconds() / 3600
    main_phases = ("routed", "global", "immediate", "no-prefix")
    main = [pair for phase in main_phases for pair in periods[phase]]
    late_starts = [pair[0] for pair in periods["late"]]
    baseline_ends = [pair[1] for pair in periods["baseline"]]
    late_inventory = read(state / "inventory-late.json", {})
    checked = stamp(late_inventory["checkedAt"]) if late_inventory.get("checkedAt") else None
    due = upper + timedelta(hours=12) if upper else None
    late_old = bool(due and late_starts and min(late_starts) >= due)
    checks = {
        "allQueryDurationsAvailable": durations_valid,
        "queriesAfterPreparation": all(
            pair[0] >= started - tolerance for values in periods.values() for pair in values
        ),
        "lastWriteAckBeforeUpperBound": bool(ack and upper and ack <= upper),
        "baselineCompleteByLastWriteAck": bool(ack and baseline_ends and max(baseline_ends) <= ack + tolerance),
        "mainQueriesAfterLastLoad": bool(upper and main and min(pair[0] for pair in main) >= upper - tolerance),
        "orderedMainPhases": all(
            periods[a]
            and periods[b]
            and max(pair[1] for pair in periods[a]) <= min(pair[0] for pair in periods[b]) + tolerance
            for a, b in zip(main_phases, main_phases[1:], strict=False)
        ),
        "mainCompletionAfterQueries": bool(main_end and main and max(pair[1] for pair in main) <= main_end + tolerance),
        "freshLateInventoryBeforeQueries": bool(
            checked and due and late_starts and due <= checked <= min(late_starts) + tolerance
        ),
        "lateQueryStartsAtLeastTwelveHours": late_old,
    }
    result["chronologyChecks"] = {key: bool(value) for key, value in checks.items()}
    result["chronologyValidated"] = all(checks.values())
    result["lateAgeAtLeastTwelveHours"] = late_old
    result["baselineTemporalLimit"] = (
        "First-write acknowledgement is not persisted; pre-load baseline order also relies on frozen driver flow."
    )
    if not partial:
        require(result["chronologyValidated"], "query chronology or conservative late-start ages cannot be verified")
    return result


def budget_and_loads(state, selected):
    budget = read(state / "budget.json", {})
    total = budget.get("knownRu")
    categories = budget.get("categories", {})
    require(numeric(total) and all(numeric(v) for v in categories.values()), "invalid request ledger")
    require(abs(sum(categories.values()) - total) <= max(0.01, total * 1e-9), "request ledger does not reconcile")
    loads, written, charged, seconds = {}, 0, 0.0, 0.0
    for lib in selected:
        entry = read(state / f"load-{lib}.json", {})
        loads[str(SIZES[lib])] = {}
        for arm in ARMS:
            safe = numbers(entry.get(arm, {}), ("written", "ru", "seconds", "throttled"))
            loads[str(SIZES[lib])][arm] = safe
            written += safe.get("written", 0)
            charged += safe.get("ru", 0)
            seconds += safe.get("seconds", 0)
    require(total >= charged, "request ledger is smaller than recorded load charges")
    return {
        "newExtensionOnly": True,
        "knownRu": total,
        "loadRu": charged,
        "knownNonLoadRu": total - charged,
        "loadedRowsAcrossArms": written,
        "timedWriteBatchSeconds": seconds,
        "loadCounters": loads,
        "planningUsdPerMillionRu": 0.25,
        "estimatedKnownRequestUsd": total / 4_000_000,
        "reserveThresholdKnownRu": 64000000,
        "reserveThresholdExceeded": total >= 64000000,
        "billComplete": False,
        "limits": (
            "Visible charges only; model, incremental storage and SDK-hidden requests are not fully priced. "
            "E68's earlier charges are excluded."
        ),
    }


def numeric_change(before, after):
    result = {}
    for label in ("ru", "retrieved", "ms"):
        a, b = before.get(label + "Median"), after.get(label + "Median")
        result[label + "Delta"] = b - a if numeric(a) and numeric(b) else None
        result[label + "DifferencePct"] = 100 * (b - a) / a if numeric(a) and a > 0 and numeric(b) else None
    return result


def independent_audits(state):
    fields = {
        "baselineRows",
        "routedRows",
        "noPrefixRows",
        "globalRows",
        "immediateRows",
        "lateRows",
        "totalRows",
        "annRows",
        "exactRows",
        "queries",
        "arms",
        "libraries",
        "qualityFailures",
        "sourceFilesVerified",
        "inputFilesVerified",
        "rows",
        "vector",
        "exact",
        "controls",
        "baseline",
        "routed",
        "global",
        "immediate",
        "late",
    }
    result = {}
    for phase in ("main", "late"):
        path = state / f"audit-{phase}.json"
        if path.exists():
            raw = read(path)
            result[phase] = {
                "available": True,
                "sha256": sha(path),
                "passed": raw.get("passed") is True,
                "counts": numbers(raw.get("counts", {}), fields),
                "issueCount": len(raw.get("issues", [])),
            }
        else:
            result[phase] = {"available": False}
    return result


def build_report(state: Path, *, repo: Path = REPO, partial: bool = False, verify_payloads: bool = True) -> dict:
    state, repo = Path(state).resolve(), Path(repo).resolve()
    config, status = read(state / "config.json"), read(state / "status.json", {})
    require(isinstance(config, dict), "private configuration missing")
    if not partial:
        require(
            status.get("complete") is True and status.get("mainComplete") is True and status.get("phase") == "complete",
            "run and late sentinels are not complete",
        )
    binding = read(state / "binding.json", {})
    manifest = read(Path(config["inputManifest"]), {})
    decision = read(state / "selection-decision.json", {})
    selected = status.get("selectedLibraries", decision.get("selectedLibraries", config.get("selected", list(NEW))))
    require(
        isinstance(selected, list)
        and selected[:2] == list(NEW[:2])
        and len(set(selected)) == len(selected)
        and all(lib in NEW for lib in selected),
        "invalid selected benchmark roles",
    )
    inventory = 244773 + sum(SIZES[lib] for lib in selected)
    if not partial:
        require(decision.get("selectedLibraries") == selected, "optional selection decision does not match execution")
    rows = phase_rows(state)
    normalized_references, repair = reference_repair(state, config, rows, repo)
    coverage, adaptive, limits = coverage_and_plan(
        rows, selected, read(state / "diagnostic-plan.json", {}), inventory, partial
    )
    result = {
        "kind": "large-library-shard-and-routing-extension",
        "createdAt": datetime.now(UTC).isoformat(),
        "partial": partial,
        "coverage": coverage,
        "selectedNewLibrarySizes": [SIZES[lib] for lib in selected],
        "omittedOptionalSizes": [SIZES[lib] for lib in NEW[2:] if lib not in selected],
        "optionalSelectionBasis": (
            "observed load charges and rate plus remaining frozen workload forecast; no large-library query outcome"
        ),
        "expectedFinalInventory": {"vectors": inventory, "libraries": 1014 + len(selected)},
        "provenance": source_provenance(state, repo, config, binding),
        "inputs": verify_inputs(state, config, binding, manifest, verify_payloads),
        "models": model_metadata(state),
        "inventories": inventories(state, selected, inventory, partial),
        "timing": timing(status, manifest, rows, partial, state),
        "costAccounting": budget_and_loads(state, selected),
        "diagnosticCoverage": adaptive,
        "referenceRepair": repair,
        "optionalSelectionDecisionAvailable": bool(decision),
        "optionalSelectionForecast": numbers(decision, ("additionalKnownRu", "additionalSeconds")),
        "omittedOptionalReason": "not admitted by the frozen cost/time forecast"
        if decision and any(lib not in selected for lib in NEW[2:])
        else None,
        "independentAudits": independent_audits(state),
    }
    if not partial:
        require(
            all(
                result["costAccounting"]["loadCounters"][str(SIZES[lib])][arm].get("written") == SIZES[lib]
                for lib in selected
                for arm in ARMS
            ),
            "recorded loads are incomplete",
        )

    def summary(phase, arm, lib, *, shape="routed", ordinals=tuple(range(20)), subset=False):
        values = select(rows[phase], arm, lib, shape, "vector")
        ref_phase = "global" if shape == "global" else phase if phase == "baseline" else "routed"
        refs = select(rows[ref_phase], arm, lib, "global" if shape == "global" else "routed", "exact")
        refs = [normalized_references.get((ref_phase, *key(row)), row) for row in refs]
        fn = summarize_subset if subset else summarize_shape
        value = fn(
            values,
            refs,
            query_ordinals=ordinals,
            library_id=None if lib == "global" else lib,
            library_size=None if lib == "global" else SIZES[lib],
            inventory_vectors=244773 if phase == "baseline" else inventory,
            query_shape=shape,
        )
        selected_values = [row for row in values if row.get("query") in ordinals] if subset else values
        selected_refs = [row for row in refs if row.get("query") in ordinals]
        return apply_diagnostic_guard(value, selected_values, selected_refs)

    libraries = sorted((*SMALL, *selected), key=lambda lib: SIZES[lib])
    baseline = {str(SIZES[lib]): {arm: summary("baseline", arm, lib) for arm in ARMS} for lib in SMALL}
    routed = {str(SIZES[lib]): {arm: summary("routed", arm, lib) for arm in ARMS} for lib in libraries}
    globals_ = {arm: summary("global", arm, "global", shape="global", ordinals=(0,)) for arm in ARMS}
    result.update(
        {
            "baseline": baseline,
            "routed": routed,
            "global": globals_,
            "globalObservationIsSharedAcrossLibrarySizes": True,
            "globalIsMatchedOnlyToOldFirstProbeQuestion": True,
            "routedArmComparisons": {
                size: compare_arms(value["split"], value["unsplit"]) for size, value in routed.items()
            },
        }
    )
    result["sizeBenefitVersusFinal5k"] = {
        str(SIZES[lib]): compare_size_benefit(
            routed["5000"]["split"],
            routed["5000"]["unsplit"],
            routed[str(SIZES[lib])]["split"],
            routed[str(SIZES[lib])]["unsplit"],
        )
        for lib in selected
    }
    result["routingComparisons"] = {}
    for lib, limit in limits.items():
        result["routingComparisons"][str(SIZES[lib])] = {}
        for arm in ARMS:
            on = summary("routed", arm, lib, ordinals=tuple(range(limit)), subset=True)
            off = summary("no-prefix", arm, lib, shape="no-prefix", ordinals=tuple(range(limit)))
            result["routingComparisons"][str(SIZES[lib])][arm] = {
                "routedMatchedSubset": on,
                "noPrefix": off,
                "comparison": compare_routes(on, off),
            }
    historical = read(repo / "records/scale-ablation-20261111.json")
    result["smallProbeDrift"] = {}
    for lib in SMALL:
        size = str(SIZES[lib])
        result["smallProbeDrift"][size] = {}
        for arm in ARMS:
            old = historical["stages"]["3"][arm]["probes"][lib]
            old_metrics = {key: old[key] for key in ("ruMedian", "retrievedMedian", "msMedian", "recallAt10Mean")}
            result["smallProbeDrift"][size][arm] = {
                "freshBaselineToFinal": compare_small_drift(baseline[size][arm], routed[size][arm]),
                "historicalE68": old_metrics,
                "freshBaselineMinusHistorical": numeric_change(old, baseline[size][arm]),
                "finalMinusHistorical": numeric_change(old, routed[size][arm]),
            }
    result["sentinels"] = {}
    for lib in (*libraries, "global"):
        label = "global" if lib == "global" else str(SIZES[lib])
        shape = "global" if lib == "global" else "routed"
        result["sentinels"][label] = {}
        for arm in ARMS:
            early = summary("immediate", arm, lib, shape=shape, ordinals=(0,))
            late = summary("late", arm, lib, shape=shape, ordinals=(0,))

            def mode_pairs(value):
                return [(spec["type"], tuple(spec["shardKeys"])) for spec in value["indexDiagnostics"]["vectorIndexes"]]

            def signature(info):
                return (
                    tuple((item["type"], tuple(item["shardKeys"])) for item in info["vectorIndexes"]),
                    info["indexReportsWithoutUtilizedVectorIndex"] > 0,
                )

            paired_mode_changes = [
                signature(before) != signature(after)
                for before, after in zip(early["indexDiagnosticsByRun"], late["indexDiagnosticsByRun"], strict=True)
            ]

            result["sentinels"][label][arm] = {
                "questionOrdinal": 0,
                "immediate": early,
                "late": late,
                "lateMinusImmediate": numeric_change(early, late),
                "pairedRunModeSignatureChanges": paired_mode_changes,
                "observedModeSignatureChanged": any(paired_mode_changes)
                or mode_pairs(early) != mode_pairs(late)
                or (early["indexDiagnostics"]["indexReportsWithoutUtilizedVectorIndex"] > 0)
                != (late["indexDiagnostics"]["indexReportsWithoutUtilizedVectorIndex"] > 0),
            }
    result["smallControlsStableWithin20Pct"] = all(
        result["smallProbeDrift"][str(SIZES[lib])][arm]["freshBaselineToFinal"]["ruStableWithin20Pct"]
        for lib in SMALL
        for arm in ARMS
    )
    result["limits"] = [
        "Three repeats describe observed runs, not confidence intervals or independent index builds.",
        "One physical range and one index build per arm were measured; concurrency and throughput were not tested.",
        (
            "Each size has one assigned nonnested library and a different question set at one fixed final total "
            "inventory. Library size, selectivity, corpus content and query difficulty are not independently varied; "
            "their separate causal effects cannot be identified."
        ),
        (
            "The fresh small-library controls measure background-growth drift. The new large libraries were measured "
            "only at the final inventory, so their flatness as unrelated background grows is not established."
        ),
        "The shared global control is id-only, omits filter and prefix, and has its own eligible answer set.",
        "Five-question diagnostic coverage, when triggered, is compared with the same frozen routed subset.",
        (
            "The no-prefix diagnostic ran last in the main sequence; its latency comparison cannot separate request "
            "shape from phase order, cache state and index age."
        ),
        (
            "The WHERE equality constrains /libraryId, the first hierarchical partition-key component. Omitting the "
            "explicit prefix does not establish the absence of inferred partition pruning, especially with one "
            "physical range."
        ),
        "Late sentinels cover one fixed question per scope, not the full twenty-question suite.",
        (
            "A client connectivity or sleep transition was reported between the main and late phases; cross-time "
            "client-latency changes cannot be attributed solely to index aging."
        ),
        (
            "Stable scans without vector-specific build completion cannot separate a lasting planner choice "
            "from unfinished graph construction."
        ),
    ]
    return result


def modes(summary):
    info = summary["indexDiagnostics"]
    labels = sorted({spec["type"] for spec in info["vectorIndexes"]})
    if info["indexReportsWithoutUtilizedVectorIndex"]:
        labels.append("no utilized vector index")
    if info["indexHeadersMissing"]:
        labels.append("missing headers")
    return ", ".join(labels) or "unavailable"


def quality_label(summary):
    labels = []
    if not summary["querySetComplete"] or not summary["referenceSetComplete"]:
        labels.append("INCOMPLETE")
    if not all(summary[key] for key in ("resultsComplete", "metricsComplete", "ownershipComplete")):
        labels.append("RESULT / METRIC / SCOPE FAILURE")
    info = summary["indexDiagnostics"]
    if not info.get("diagnosticsUsable"):
        labels.append("DATA DIAGNOSTIC GAP")
    elif not info.get("dataResponseDiagnosticsComplete"):
        labels.append("PARTIAL DATA HEADERS (usable)")
    if not summary["recallComplete"]:
        labels.append("RECALL INCOMPLETE")
    elif not summary["recallAtFloor"]:
        labels.append("RECALL FLOOR FAILURE")
    if info.get("diagnosticsUsable") and not summary["modeSignatureStableAcrossRuns"]:
        labels.append("MODE CHANGES")
    if info.get("unrecognizedTypeValues"):
        labels.append("UNRECOGNIZED MODE")
    return "; ".join(labels) or "accepted"


def fmt(value):
    return "unavailable" if value is None else f"{value:,.3f}".rstrip("0").rstrip(".")


def render_tables(report):
    lines = [
        "# Large-library measurement tables",
        "",
        "Client elapsed milliseconds include retries. Ranges describe observed repeats, not confidence intervals.",
    ]
    repair = report.get("referenceRepair", {})
    if repair.get("enabled"):
        lines.extend(
            [
                "",
                f"Exact-reference repair validated all {repair['exactReferencesValidated']:,} "
                "retained candidate unions "
                f"against exhaustive local float16 cosine truth; {repair['referencesWithMoreThanTenCandidates']:,} "
                "contained more than ten candidates. Raw observations, SQL and routing remain unchanged.",
                "",
                repair["precisionLimit"],
            ]
        )
        for model, value in repair["numericModelSensitivity"].items():
            lines.extend(
                [
                    "",
                    f"Numeric sensitivity ({model}): {value['topTenSetDifferencesFromPrimary']:,} of "
                    f"{value['referenceQuestions']:,} reference questions have a different top-ten set from primary; "
                    f"minimum observed 10/11 margin {value['minimumBoundaryMargin']:.9g}.",
                ]
            )

    def table(title, note, headings, values):
        lines.extend(["", "## " + title, "", note, ""])
        lines.append("| " + " | ".join(headings) + " |")
        lines.append("|" + "|".join("---" for _ in headings) + "|")
        for cells in values:
            lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")

    def bounds(value):
        return "unavailable" if not value else fmt(value[0]) + " to " + fmt(value[1])

    primary = []
    for size, arms in sorted(report["routed"].items(), key=lambda item: int(item[0])):
        for arm in ARMS:
            for label, value in (("routed", arms[arm]), ("global (shared)", report["global"][arm])):
                primary.append(
                    [
                        f"{int(size):,}",
                        arm,
                        label,
                        value["expectedQueries"],
                        fmt(value["ruMedian"]),
                        bounds(value["ruRunRange"]),
                        fmt(value["retrievedMedian"]),
                        fmt(value["msMedian"]),
                        fmt(value["recallAt10Mean"]),
                        modes(value),
                        quality_label(value),
                    ]
                )
    table(
        "Routed and global requests",
        "Global rows repeat the SAME shared observation; they are neither independent per-library measurements "
        "nor matched scope-omission effects.",
        [
            "Library vectors",
            "Arm",
            "Request",
            "Questions",
            "RU",
            "RU run range",
            "Documents read",
            "Latency ms",
            "Recall@10",
            "Observed index",
            "Evidence status",
        ],
        primary,
    )
    routing = []
    for size, arms in sorted(report["routingComparisons"].items(), key=lambda item: int(item[0])):
        for arm, value in arms.items():
            for name, field in (("prefix", "routedMatchedSubset"), ("no prefix", "noPrefix")):
                metric = value[field]
                routing.append(
                    [
                        f"{int(size):,}",
                        arm,
                        name,
                        metric["expectedQueries"],
                        fmt(metric["ruMedian"]),
                        fmt(metric["retrievedMedian"]),
                        fmt(metric["msMedian"]),
                        fmt(metric["recallAt10Mean"]),
                        modes(metric),
                        quality_label(metric),
                    ]
                )
    table(
        "Matched prefix-on / prefix-off diagnostic",
        "A capped diagnostic uses the first five frozen questions and the SAME five routed questions. "
        "The primary routed table continues to use twenty.",
        ["Vectors", "Arm", "Route", "Questions", "RU", "Documents read", "Latency ms", "Recall@10", "Index", "Status"],
        routing,
    )
    benefit, intervals = [], []
    for size, value in sorted(report["sizeBenefitVersusFinal5k"].items(), key=lambda item: int(item[0])):
        benefit.append(
            [
                f"{int(size):,}",
                fmt(value["baselineSavingsRu"]),
                fmt(value["savingsRu"]),
                fmt(value["savingsDeltaRu"]),
                fmt(value["savingsChangePct"]),
                value["classification"] or "inconclusive",
            ]
        )
        intervals.append(
            [
                f"{int(size):,}",
                bounds(value["baselineRepeatSavingsRange"]),
                bounds(value["repeatSavingsRange"]),
                bounds(value["repeatSavingsRatioRange"]),
                bounds(value["repeatSavingsChangePctRange"]),
            ]
        )
    table(
        "Absolute sharding benefit versus the final-inventory 5,000-vector control",
        "Saving means unsplit RU minus split RU, using routed searches at the same final inventory.",
        ["Vectors", "Saving at 5k RU", "Saving RU", "Difference RU", "Saving change %", "Classification"],
        benefit,
    )
    table(
        "Conservative observed savings intervals",
        "Independent run-median endpoints bound observed savings; these are not confidence intervals.",
        ["Vectors", "5k saving range RU", "Saving range RU", "Ratio interval", "Saving change % interval"],
        intervals,
    )
    small = []
    for size, arms in report["smallProbeDrift"].items():
        for arm, value in arms.items():
            change = value["freshBaselineToFinal"]
            small.append(
                [
                    f"{int(size):,}",
                    arm,
                    fmt(value["historicalE68"]["ruMedian"]),
                    fmt(report["baseline"][size][arm]["ruMedian"]),
                    fmt(report["routed"][size][arm]["ruMedian"]),
                    fmt(change["ruDelta"]),
                    fmt(change["ruDifferencePct"]),
                    fmt(change["retrievedDifferencePct"]),
                    fmt(change["msDifferencePct"]),
                ]
            )
    table(
        "Small-probe drift",
        "Fresh baseline and historical E68 are distinct observations.",
        [
            "Vectors",
            "Arm",
            "E68 RU",
            "Fresh RU",
            "Final RU",
            "Difference RU",
            "RU drift %",
            "Documents drift %",
            "Latency drift %",
        ],
        small,
    )
    sentinels = []
    for scope, arms in report["sentinels"].items():
        for arm, value in arms.items():
            for phase in ("immediate", "late"):
                metric = value[phase]
                sentinels.append(
                    [
                        scope,
                        arm,
                        phase,
                        fmt(metric["ruMedian"]),
                        fmt(metric["retrievedMedian"]),
                        fmt(metric["msMedian"]),
                        fmt(metric["recallAt10Mean"]),
                        modes(metric),
                        quality_label(metric),
                    ]
                )
    table(
        "Immediate and late sentinels",
        "One fixed question per scope, three repeats, separately from the twenty-question result. "
        "Late ages use the conservative last-write upper bound.",
        ["Scope vectors", "Arm", "Sentinel", "RU", "Documents read", "Latency ms", "Recall@10", "Index", "Status"],
        sentinels,
    )
    lines.extend(["", "## Scientific limits", ""])
    lines.extend("- " + value for value in report["limits"])
    return "\n".join(lines) + "\n"


def privacy_guard(payload):
    for forbidden in (
        "http://",
        "https://",
        "/Users/",
        "/private/",
        "/home/",
        "result_ids",
        "result_bindings",
        "targetFingerprint",
    ):
        require(forbidden not in payload, "public output failed its privacy guard")
    require(
        re.search(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", payload) is None,
        "public output contains an identifier-shaped value",
    )


def outside_checkout(path):
    target = path.resolve()
    return ".git" not in target.parts and not any((parent / ".git").exists() for parent in (target, *target.parents))


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".large-report-", dir=path.parent)
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Exclusive publication: an existing destination is never replaced.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_report(state, output, tables, *, partial=False, repo=REPO):
    require(not Path(output).is_symlink() and not Path(tables).is_symlink(), "report destinations must be new files")
    state, output, tables, repo = map(lambda value: Path(value).resolve(), (state, output, tables, repo))
    require(output != tables, "report destinations must differ")
    require(not output.exists() and not tables.exists(), "report destinations already exist")
    protected = {repo / relative for relative in PROTECTED}
    require(output not in protected and tables not in protected, "protected evidence destinations cannot be written")
    if partial:
        require(
            outside_checkout(output) and outside_checkout(tables), "partial outputs must stay outside every checkout"
        )
    else:
        require(
            output.is_relative_to(repo / "records") and tables.is_relative_to(repo / "docs"),
            "final output destinations must be records and docs",
        )
    result = build_report(state, repo=repo, partial=partial)
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    markdown = render_tables(result)
    privacy_guard(encoded + markdown)
    atomic_write(output, encoded)
    atomic_write(tables, markdown)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    try:
        value = write_report(args.state, args.out, args.tables, partial=args.partial)
    except InvalidEvidence as exc:
        print("not published: " + str(exc))
        return 2
    except Exception as exc:
        print("not published: validation could not finish (" + type(exc).__name__ + ")")
        return 3
    print(
        json.dumps(
            {
                "written": True,
                "partial": value["partial"],
                "selectedNewLibraries": len(value["selectedNewLibrarySizes"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
