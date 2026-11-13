"""Reporter guards and recomputation use synthetic local records, never services."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts import scale_ablation_large_report as reporting


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fake_state(tmp_path, *, cap=True):
    state, repo = tmp_path / "private", tmp_path / "checkout"
    state.mkdir()
    repo.mkdir()
    (repo / ".git").mkdir()
    for relative in reporting.SOURCES:
        for root in (repo, state / "frozen-source"):
            file = root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("unit fixture source")
    historical = {
        "stages": {
            "3": {
                arm: {
                    "probes": {
                        lib: {"ruMedian": 10, "retrievedMedian": 50, "msMedian": 100, "recallAt10Mean": 1}
                        for lib in reporting.SMALL
                    }
                }
                for arm in reporting.ARMS
            }
        }
    }
    save(repo / "records/scale-ablation-20261111.json", historical)
    for name in (
        "question-sources.json",
        "old-question-sources.json",
        "old-vectors.json",
        "question-vectors.json",
        "question-ordinals.json",
    ):
        save(state / name, {"private": "PRIVATE_CONTENT_NEVER_COPY"})
    (state / "assignment.ndjson").write_text("PRIVATE_SOURCE_IDENTITIES_NEVER_COPY")
    libraries = {}
    for lib in reporting.NEW:
        payload, offsets = state / (lib + ".ndjson"), state / (lib + "-offsets.json")
        payload.write_text("PRIVATE_PASSAGE_NEVER_COPY")
        offsets.write_text("[0]")
        libraries[lib] = {
            "size": reporting.SIZES[lib],
            "questions": 20,
            "chunksPerDocument": 10,
            "payloadFile": str(payload),
            "offsetsFile": str(offsets),
            "payloadBytes": payload.stat().st_size,
            "sha256": reporting.sha(payload),
            "offsetsSha256": reporting.sha(offsets),
            "publishedVectorValuesSha256": "a" * 64,
            "textSequenceSha256": "b" * 64,
        }
    hashes = dict.fromkeys(reporting.INPUT_HASHES, "c" * 64)
    for field, filename in (
        ("questionSourcesSha256", "question-sources.json"),
        ("oldQuestionSourcesSha256", "old-question-sources.json"),
        ("oldVectorsSha256", "old-vectors.json"),
        ("assignmentSha256", "assignment.ndjson"),
        ("questionOrdinalsSha256", "question-ordinals.json"),
    ):
        hashes[field] = reporting.sha(state / filename)
    manifest = {
        "complete": True,
        "seed": 20261029,
        "sourceSelectionRows": 185000,
        "preparationStartedAt": "2026-11-12T00:00:00Z",
        "libraries": libraries,
        "hashes": hashes,
        "newQuestionSourcesFile": str(state / "question-sources.json"),
        "oldQuestionSourcesFile": str(state / "old-question-sources.json"),
        "oldVectorsFile": str(state / "old-vectors.json"),
    }
    save(state / "input-manifest.json", manifest)
    config = {
        "inputManifest": str(state / "input-manifest.json"),
        "selected": list(reporting.NEW),
        "e68Commit": "a" * 40,
        "driverCommit": "b" * 40,
        "prior": {"endpoint": "PRIVATE_ENDPOINT_NEVER_COPY"},
    }
    save(state / "config.json", config)
    binding = {
        "configSha256": reporting.digest(config),
        "inputManifestSha256": reporting.sha(state / "input-manifest.json"),
        "sourceFiles": {relative: reporting.sha(repo / relative) for relative in reporting.SOURCES},
        "e68ReceiptSha256": reporting.sha(repo / "records/scale-ablation-20261111.json"),
        "client": {"python": "3.14.3", "azureCosmos": "4.17.1"},
    }
    save(state / "binding.json", binding)
    save(state / "question-vector-hash.json", {"sha256": reporting.sha(state / "question-vectors.json")})
    model = {"model": "text-embedding-3-large", "version": "1", "format": "OpenAI", "dimensions": 3072}
    for suffix in ("before", "after-questions", "after-main", "after-late"):
        save(state / f"embedding-model-{suffix}.json", model)
    save(
        state / "embedding-usage.json",
        {"requests": 8, "prompt_tokens": 100, "tokens_reported": True, "endpoint": "PRIVATE_USAGE_NEVER_COPY"},
    )
    selected = list(reporting.NEW)
    save(
        state / "selection-decision.json",
        {"selectedLibraries": selected, "additionalKnownRu": 1000, "additionalSeconds": 1000},
    )
    status = {
        "phase": "complete",
        "complete": True,
        "mainComplete": True,
        "selectedLibraries": selected,
        "lastWriteUpperBoundAt": "2026-11-12T04:00:00Z",
        "lastWriteAckAt": "2026-11-12T03:59:59Z",
        "mainCompletedAt": "2026-11-12T07:00:00Z",
        "lateCompletedAt": "2026-11-12T16:01:00Z",
    }
    save(state / "status.json", status)
    n = 429773
    plan = {}
    for lib in ("scale-p10", "large-p050000", "large-p100000"):
        limit = 5 if cap and lib == "scale-p10" else 20
        plan[lib] = {"questions": limit, "queryOrdinals": list(range(limit)), "inventoryVectors": n}
    save(state / "diagnostic-plan.json", plan)
    for phase in ("before", "final", "late"):
        counts = {
            lib: 0 if phase == "before" and lib in reporting.NEW else reporting.SIZES[lib]
            for lib in (*reporting.SMALL, *selected)
        }
        save(
            state / f"inventory-{phase}.json",
            {
                "checkedAt": {
                    "before": "2026-11-12T00:30:00Z",
                    "final": "2026-11-12T04:10:00Z",
                    "late": "2026-11-12T16:00:01Z",
                }[phase],
                "arms": {
                    arm: {
                        "vectors": 244773 if phase == "before" else n,
                        "libraries": 1014 if phase == "before" else 1018,
                        "physicalRanges": 1,
                        "policyMatches": True,
                        "matchesExpected": True,
                        "perLibrary": counts,
                    }
                    for arm in reporting.ARMS
                },
            },
        )
    load_total = 0
    for lib in selected:
        ru = reporting.SIZES[lib] * 91.878
        save(
            state / f"load-{lib}.json",
            {
                arm: {"written": reporting.SIZES[lib], "ru": ru, "seconds": reporting.SIZES[lib] * 0.041}
                for arm in reporting.ARMS
            },
        )
        load_total += 2 * ru
    save(
        state / "budget.json",
        {
            "knownRu": load_total + 1000,
            "categories": {"load-split": load_total / 2, "load-unsplit": load_total / 2, "other": 1000},
        },
    )
    for phase, specs in (
        ("baseline", reporting.expected_rows(reporting.SMALL, "routed")),
        ("routed", reporting.expected_rows((*reporting.SMALL, *selected), "routed")),
        ("global", reporting.expected_rows(("global",), "global", questions=1)),
        (
            "immediate",
            reporting.expected_rows((*reporting.SMALL, *selected), "routed", questions=1, exact=False)
            + reporting.expected_rows(("global",), "global", questions=1, exact=False),
        ),
        (
            "late",
            reporting.expected_rows((*reporting.SMALL, *selected), "routed", questions=1, exact=False)
            + reporting.expected_rows(("global",), "global", questions=1, exact=False),
        ),
        (
            "no-prefix",
            sum(
                (
                    reporting.expected_rows((lib,), "no-prefix", questions=entry["questions"], exact=False)
                    for lib, entry in plan.items()
                ),
                reporting.Counter(),
            ),
        ),
    ):
        values = []
        for arm, lib, shape, kind, query, run in specs:
            size = reporting.SIZES.get(lib, n)
            ru = (100 if size >= 50000 else 10) + (40 if size >= 50000 else 10) * (arm == "unsplit")
            retrieved = n if shape == "no-prefix" and plan[lib]["questions"] == 5 else size if kind == "exact" else 50
            report = (
                {}
                if kind == "exact"
                else {
                    "UtilizedVectorIndexes": [
                        {
                            "IndexSpec": {
                                "VectorIndexType": "QuantizedFlat",
                                "ShardKeys": ["/libraryId"] if arm == "split" else [],
                            }
                        }
                    ]
                }
            )
            values.append(
                {
                    "arm": arm,
                    "library": lib,
                    "shape": shape,
                    "kind": kind,
                    "query": query,
                    "run": run,
                    "request_charge": ru,
                    "retrieved_document_count": retrieved,
                    "milliseconds": 100,
                    "result_ids": [f"{lib}-d0000:1:{i}" for i in range(10)],
                    "error": None,
                    "http_responses": 1,
                    "response_metrics": [{"retrieved": retrieved, "indexUtilization": report}],
                    "recordedAt": (
                        datetime(2026, 11, 12, tzinfo=UTC)
                        + timedelta(
                            seconds={
                                "baseline": 3600,
                                "routed": 18000,
                                "global": 18600,
                                "immediate": 18660,
                                "no-prefix": 18720,
                                "late": 57602,
                            }[phase]
                            + len(values) * 0.2
                        )
                    ).isoformat(),
                    "private": "PRIVATE_ROW_NEVER_COPY",
                }
            )
        save(state / f"measure-{phase}.json", {"rows": values})
    return state, repo


def test_complete_report_recomputes_subset_global_and_absolute_benefit(tmp_path):
    state, repo = fake_state(tmp_path)
    result = reporting.build_report(state, repo=repo)
    assert result["coverage"]["late"]["observedRows"] == 42
    assert result["timing"]["lateAgeAtLeastTwelveHours"]
    assert result["routed"]["5000"]["split"]["expectedQueries"] == 20
    assert result["routingComparisons"]["5000"]["split"]["noPrefix"]["expectedQueries"] == 5
    assert result["routingComparisons"]["5000"]["split"]["routedMatchedSubset"]["expectedQueries"] == 5
    assert result["sizeBenefitVersusFinal5k"]["50000"]["classification"] == "grown"
    assert result["globalObservationIsSharedAcrossLibrarySizes"]
    encoded = json.dumps(result)
    assert "PRIVATE_" not in encoded and str(state) not in encoded
    reporting.privacy_guard(encoded)
    tables = reporting.render_tables(result)
    assert "SAME shared observation" in tables
    assert "SAME five routed questions" in tables
    assert "Conservative observed savings intervals" in tables


@pytest.mark.parametrize(
    "failure",
    [
        "not-complete",
        "late-missing",
        "late-young",
        "duplicate",
        "wrong-cap",
        "inventory",
        "vectors-changed",
        "payload-changed",
        "model-changed",
    ],
)
def test_invalid_or_incomplete_state_never_writes_public_files(tmp_path, failure):
    state, repo = fake_state(tmp_path)
    if failure == "not-complete":
        status = reporting.read(state / "status.json")
        status["complete"] = False
        save(state / "status.json", status)
    elif failure in ("late-missing", "late-young", "duplicate"):
        values = reporting.read(state / "measure-late.json")
        if failure == "late-missing":
            values["rows"].pop()
        elif failure == "duplicate":
            values["rows"][-1] = copy.deepcopy(values["rows"][0])
        else:
            values["rows"][0]["recordedAt"] = "2026-11-12T15:59:59Z"
        save(state / "measure-late.json", values)
    elif failure == "wrong-cap":
        plan = reporting.read(state / "diagnostic-plan.json")
        plan["scale-p10"]["questions"] = 20
        save(state / "diagnostic-plan.json", plan)
    elif failure == "inventory":
        inventory = reporting.read(state / "inventory-late.json")
        inventory["arms"]["split"]["vectors"] += 1
        save(state / "inventory-late.json", inventory)
    elif failure == "vectors-changed":
        (state / "question-vectors.json").write_text("changed")
    elif failure == "payload-changed":
        (state / (reporting.NEW[0] + ".ndjson")).write_text("changed")
    else:
        model = reporting.read(state / "embedding-model-before.json")
        model["version"] = "2"
        save(state / "embedding-model-before.json", model)
    out, tables = repo / "records/result.json", repo / "docs/result.md"
    with pytest.raises(reporting.InvalidEvidence):
        reporting.write_report(state, out, tables, repo=repo)
    assert not out.exists() and not tables.exists()


def test_current_post_merge_source_changes_do_not_replace_measured_hashes(tmp_path):
    state, repo = fake_state(tmp_path)
    relative = "src/fli/query.py"
    original = reporting.read(state / "binding.json")["sourceFiles"][relative]
    (repo / relative).write_text("new post-merge source")
    result = reporting.build_report(state, repo=repo)
    assert result["provenance"]["recordedMeasurementSourceFilesSha256"][relative] == original
    assert not result["provenance"]["currentSourceMatchesRecorded"][relative]


def test_quality_failure_is_retained_instead_of_erasing_complete_measurements(tmp_path):
    state, repo = fake_state(tmp_path)
    values = reporting.read(state / "measure-routed.json")
    row = next(r for r in values["rows"] if r["kind"] == "vector" and r["library"] == "large-p050000")
    row["result_ids"][0] = "other-d0000:1:0"
    save(state / "measure-routed.json", values)
    result = reporting.build_report(state, repo=repo)
    assert not result["routed"]["50000"][row["arm"]]["qualityAccepted"]
    assert result["sizeBenefitVersusFinal5k"]["50000"]["classification"] is None


def test_partial_output_must_stay_outside_every_checkout(tmp_path):
    state, repo = fake_state(tmp_path)
    with pytest.raises(reporting.InvalidEvidence, match="outside"):
        reporting.write_report(state, repo / "records/partial.json", repo / "docs/partial.md", partial=True, repo=repo)


def test_late_mode_changes_are_explicit(tmp_path):
    state, repo = fake_state(tmp_path)
    raw = reporting.read(state / "measure-late.json")
    for row in raw["rows"]:
        if row["library"] == "large-p100000" and row["arm"] == "unsplit":
            row["response_metrics"][0]["indexUtilization"]["UtilizedVectorIndexes"][0]["IndexSpec"][
                "VectorIndexType"
            ] = "DiskANN"
    save(state / "measure-late.json", raw)
    assert reporting.build_report(state, repo=repo)["sentinels"]["100000"]["unsplit"]["observedModeSignatureChanged"]


def test_audit_attachment_copies_only_safe_counts_and_status(tmp_path):
    state, repo = fake_state(tmp_path)
    save(
        state / "audit-main.json",
        {
            "passed": False,
            "counts": {"routedRows": 960, "PRIVATE_ACCOUNT_NAME": 2},
            "issues": ["PRIVATE_RESULT_IDS_NEVER_COPY"],
            "endpoint": "PRIVATE_AUDIT_ENDPOINT_NEVER_COPY",
        },
    )
    result = reporting.build_report(state, repo=repo)
    audit = result["independentAudits"]["main"]
    assert not audit["passed"] and audit["issueCount"] == 1
    assert audit["counts"] == {"routedRows": 960}
    assert "PRIVATE_" not in json.dumps(result)


def test_mode_values_have_a_strict_enum_and_private_unknown_values_are_hashed(tmp_path):
    state, repo = fake_state(tmp_path)
    raw = reporting.read(state / "measure-routed.json")
    for row in raw["rows"]:
        if row["library"] == "large-p050000" and row["kind"] == "vector":
            row["response_metrics"][0]["indexUtilization"]["UtilizedVectorIndexes"][0]["IndexSpec"][
                "VectorIndexType"
            ] = "PrivateOperatorAccount"
    save(state / "measure-routed.json", raw)
    result = reporting.build_report(state, repo=repo)
    encoded = json.dumps(result)
    assert "PrivateOperatorAccount" not in encoded
    mode = result["routed"]["50000"]["split"]["indexDiagnostics"]["vectorIndexes"][0]
    assert mode["type"] == "Unrecognized"
    assert reporting.HEX.fullmatch(mode["unrecognizedTypeSha256"])
    assert not result["routed"]["50000"]["split"]["qualityAccepted"]
    assert result["routed"]["50000"]["split"]["indexDiagnostics"]["unexpectedVectorModeObserved"]
    assert result["routedArmComparisons"]["50000"]["classification"] is None
    assert result["sizeBenefitVersusFinal5k"]["50000"]["classification"] is None
    assert result["routed"]["50000"]["split"]["ruMedian"] == 100
    assert result["routed"]["50000"]["split"]["recallAt10Mean"] == 1
    assert "UNRECOGNIZED MODE" in reporting.render_tables(result)


def test_client_versions_cannot_contain_arbitrary_private_labels(tmp_path):
    state, repo = fake_state(tmp_path)
    binding = reporting.read(state / "binding.json")
    binding["client"]["python"] = "PrivateOperatorAccount"
    save(state / "binding.json", binding)
    with pytest.raises(reporting.InvalidEvidence, match="client"):
        reporting.build_report(state, repo=repo)


def test_empty_non_data_report_cannot_mask_missing_data_index_evidence(tmp_path):
    state, repo = fake_state(tmp_path)
    raw = reporting.read(state / "measure-routed.json")
    for row in raw["rows"]:
        if row["library"] == "large-p050000" and row["arm"] == "unsplit" and row["kind"] == "vector":
            row["http_responses"] = 2
            row["response_metrics"] = [
                {"retrieved": None, "indexUtilization": {}},
                {"retrieved": row["retrieved_document_count"], "indexUtilization": None},
            ]
    save(state / "measure-routed.json", raw)
    result = reporting.build_report(state, repo=repo)
    value = result["routed"]["50000"]["unsplit"]
    assert value["frozenQualityAccepted"]  # The frozen implementation's false acceptance is explicit.
    assert not value["qualityAccepted"]
    assert not value["indexDiagnostics"]["dataResponseDiagnosticsComplete"]
    assert value["indexDiagnostics"]["dataResponsesWithMissingOrUnusableIndexReport"] == 60
    assert result["sizeBenefitVersusFinal5k"]["50000"]["classification"] is None
    assert "DATA DIAGNOSTIC GAP" in reporting.render_tables(result)


def test_explicit_no_utilized_index_on_actual_data_response_remains_valid(tmp_path):
    state, repo = fake_state(tmp_path)
    raw = reporting.read(state / "measure-routed.json")
    for row in raw["rows"]:
        if row["library"] == "large-p050000" and row["kind"] == "vector":
            row["response_metrics"] = [{"retrieved": 50000, "indexUtilization": {}}]
            row["retrieved_document_count"] = 50000
    save(state / "measure-routed.json", raw)
    value = reporting.build_report(state, repo=repo)["routed"]["50000"]["unsplit"]
    assert value["qualityAccepted"]
    assert value["indexDiagnostics"]["dataResponseDiagnosticsComplete"]
    assert value["indexDiagnostics"]["indexReportsWithoutUtilizedVectorIndex"] == 60


def test_paged_data_with_one_usable_header_and_missing_continuation_is_accepted_as_partial(tmp_path):
    state, repo = fake_state(tmp_path)
    raw = reporting.read(state / "measure-routed.json")
    for row in raw["rows"]:
        if row["library"] == "large-p050000" and row["kind"] == "vector":
            observed = copy.deepcopy(row["response_metrics"][0]["indexUtilization"])
            row["http_responses"] = 3
            row["response_metrics"] = [
                {"retrieved": None, "indexUtilization": {}},
                {"retrieved": 25, "indexUtilization": observed},
                {"retrieved": 25, "indexUtilization": None},
            ]
    save(state / "measure-routed.json", raw)
    result = reporting.build_report(state, repo=repo)
    value = result["routed"]["50000"]["unsplit"]
    info = value["indexDiagnostics"]
    assert value["qualityAccepted"]
    assert info["diagnosticsUsable"] and info["dataResponseDiagnosticsPartial"]
    assert not info["dataResponseDiagnosticsComplete"]
    assert info["dataResponsesWithMissingOrUnusableIndexReport"] == 60
    assert info["queriesWithUsableDataResponseDiagnostics"] == 60
    assert info["vectorIndexes"][0]["type"] == "QuantizedFlat"
    assert "PARTIAL DATA HEADERS (usable)" in reporting.render_tables(result)
    assert result["sizeBenefitVersusFinal5k"]["50000"]["classification"] == "grown"


def test_late_completion_after_due_does_not_prove_the_query_started_after_due(tmp_path):
    state, repo = fake_state(tmp_path)
    raw = reporting.read(state / "measure-late.json")
    raw["rows"][0].update(recordedAt="2026-11-12T16:00:00.500000Z", milliseconds=1000)
    save(state / "measure-late.json", raw)
    result = reporting.build_report(state, repo=repo, partial=True)
    times = result["timing"]
    assert times["lateQueryCompletionHoursSinceLastWriteUpperBoundRange"][0] >= 12
    assert times["lateQueryStartHoursSinceLastWriteUpperBoundRange"][0] < 12
    assert not times["lateAgeAtLeastTwelveHours"]
    with pytest.raises(reporting.InvalidEvidence, match="chronology"):
        reporting.build_report(state, repo=repo)


@pytest.mark.parametrize(
    "failure",
    [
        "baseline-after-load",
        "routed-before-load",
        "global-before-routed",
        "stale-late-inventory",
        "late-inventory-after-query",
    ],
)
def test_phase_chronology_and_fresh_late_inventory_are_checked(tmp_path, failure):
    state, repo = fake_state(tmp_path)
    if failure in ("baseline-after-load", "routed-before-load", "global-before-routed"):
        phase, when = {
            "baseline-after-load": ("baseline", "2026-11-12T06:00:00Z"),
            "routed-before-load": ("routed", "2026-11-12T03:59:59Z"),
            "global-before-routed": ("global", "2026-11-12T05:00:01Z"),
        }[failure]
        raw = reporting.read(state / f"measure-{phase}.json")
        raw["rows"][0]["recordedAt"] = when
        save(state / f"measure-{phase}.json", raw)
    else:
        inventory = reporting.read(state / "inventory-late.json")
        inventory["checkedAt"] = "2026-11-12T15:59:59Z" if failure == "stale-late-inventory" else "2026-11-12T16:00:05Z"
        save(state / "inventory-late.json", inventory)
    with pytest.raises(reporting.InvalidEvidence, match="chronology"):
        reporting.build_report(state, repo=repo)


def test_existing_and_protected_evidence_destinations_are_never_overwritten(tmp_path):
    state, repo = fake_state(tmp_path)
    historical = repo / "records/scale-ablation-20261111.json"
    before = historical.read_bytes()
    with pytest.raises(reporting.InvalidEvidence, match="already exist"):
        reporting.write_report(state, historical, repo / "docs/new.md", repo=repo)
    assert historical.read_bytes() == before and not (repo / "docs/new.md").exists()
    protected = repo / "docs/scale-ablation.md"
    assert not protected.exists()
    with pytest.raises(reporting.InvalidEvidence, match="protected"):
        reporting.write_report(state, repo / "records/new.json", protected, repo=repo)
    assert not protected.exists() and not (repo / "records/new.json").exists()


def test_recall_floor_failure_is_visible_in_tables(tmp_path):
    state, repo = fake_state(tmp_path)
    raw = reporting.read(state / "measure-routed.json")
    for row in raw["rows"]:
        if (
            row["library"] == "large-p100000"
            and row["arm"] == "unsplit"
            and row["kind"] == "vector"
            and row["run"] == 0
        ):
            row["result_ids"][0] = "large-p100000-d9999:1:0"
    save(state / "measure-routed.json", raw)
    result = reporting.build_report(state, repo=repo)
    assert not result["routed"]["100000"]["unsplit"]["recallAtFloor"]
    assert "RECALL FLOOR FAILURE" in reporting.render_tables(result)


def repaired_state(tmp_path, monkeypatch):
    """Use the real union-proof helper, with synthetic population validation only."""
    from scripts import scale_ablation_large_exact as exact

    state, repo = fake_state(tmp_path)
    rows = reporting.phase_rows(state)
    for phase, values in rows.items():
        for row in values:
            if row["library"] == "global":
                row["result_ids"] = [f"scale-p01-d0000:1:{i}" for i in range(10)]
        save(state / f"measure-{phase}.json", {"rows": values})
    failure = next(
        row
        for row in rows["routed"]
        if (row["arm"], row["library"], row["kind"], row["query"]) == ("unsplit", "large-p025000", "exact", 0)
    )
    truth = failure["result_ids"]
    noise = [f"large-p025000-d0001:1:{i}" for i in range(10)]
    failure["result_ids"] = truth[:8] + noise[:2] + truth[8:] + noise[2:]
    failure["http_responses"] = 2
    save(state / "measure-routed.json", {"rows": rows["routed"]})
    oracle = {"scopeSizes": reporting.SIZES.copy(), "references": {}}
    for lib in (*reporting.SMALL, *reporting.NEW, "global"):
        owner = "scale-p01" if lib == "global" else lib
        ids = [f"{owner}-d0000:1:{i}" for i in range(10)]
        refs = []
        for q in range(1 if lib == "global" else 20):
            original = ids.copy()
            differs = lib == "large-p050000" and q == 4
            if differs:
                original[-1] = f"{owner}-d0001:1:0"
            refs.append(
                {
                    "queryOrdinal": q,
                    "result_ids": ids.copy(),
                    "top11": [{"id": identifier, "cosineDistance": i / 100} for i, identifier in enumerate(ids)]
                    + [{"id": f"{owner}-d0001:1:0", "cosineDistance": 0.1}],
                    "boundaryMargin": 0.01,
                    "boundaryTolerance": 1e-10,
                    "numericVariants": {
                        "originalFloat64": {
                            "result_ids": original,
                            "boundaryMargin": 0.009,
                            "sameTopTenSet": not differs,
                        },
                        "float16CorpusFtz": {"result_ids": ids.copy(), "boundaryMargin": 0.008, "sameTopTenSet": True},
                    },
                }
            )
        oracle["references"][lib] = refs
    oracle["safe"] = {
        "dimensions": 3072,
        "k": 10,
        "topRetained": 11,
        "populationVectors": 429773,
        "populationLibraries": 1018,
        "measuredLibraries": 6,
        "libraryReferenceQuestions": 120,
        "globalReferenceQuestions": 1,
        "sourcePrefixRows": 244773,
        "sourcePrefixBytes": 16303644419,
        "sourcePrefixSha256": "d" * 64,
        "queryVectorsSha256": "e" * 64,
        "payloadSha256": {"50000": "f" * 64},
        "librarySizeHistogram": {"50000": 1},
        "corpusNormRange": [0.99, 1.01],
        "roundedCorpusSubnormalValues": 100,
        "minimumBoundaryMargin": 0.01,
        "boundaryTolerance": 1e-10,
        "referencesWithNumericModelTopTenDifferences": 1,
        "variants": ["float16", "originalFloat64", "float16CorpusFtz"],
        "networkCalls": 0,
        "databaseMutations": 0,
        "unknown": "PRIVATE_ORACLE_IDENTIFIER_NEVER_COPY",
    }
    oracle["oracleSha256"] = exact._oracle_hash(oracle)
    save(state / "offline-exact.json", oracle)
    derivations = []
    for phase in ("baseline", "routed", "global"):
        for row in rows[phase]:
            if row["kind"] == "exact":
                derivations.append(
                    {
                        "key": [phase, *reporting.key(row)],
                        "rawRowSha256": reporting.digest(row),
                        "derived": exact.derive_reference(row, oracle),
                    }
                )
    save(state / "exact-reference-derivations.json", {"rows": derivations})
    for relative in reporting.REPAIR_SOURCES:
        for root in (repo, state / "repair-source"):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unit repair fixture source")
    binding = {
        "version": 1,
        "repair": "unchanged-cosmos-query-independent-exhaustive-candidate-validation",
        "originalBindingSha256": reporting.sha(state / "binding.json"),
        "offlineOracleSha256": reporting.sha(state / "offline-exact.json"),
        "sourceFiles": {relative: reporting.sha(repo / relative) for relative in reporting.REPAIR_SOURCES},
        "numericalClient": {"numpy": "2.4.1"},
        "initialFailure": {
            "phase": "routed",
            "rowSha256": reporting.digest(failure),
            "arm": "unsplit",
            "librarySize": 25000,
            "questionOrdinal": 0,
            "rawResultCount": 20,
            "requestCharge": failure["request_charge"],
            "retrievedDocuments": failure["retrieved_document_count"],
            "milliseconds": failure["milliseconds"],
        },
    }
    save(state / "reference-repair-binding.json", binding)

    def validate_synthetic_population(config, supplied_state, supplied_oracle):
        assert supplied_state == state and config == reporting.read(state / "config.json")
        assert supplied_oracle == oracle and supplied_oracle["oracleSha256"] == exact._oracle_hash(supplied_oracle)

    # Full-stream input/population validation has its own helper tests. The real
    # derive_reference still verifies each union, owner, boundary, hash and COPY.
    monkeypatch.setattr(exact, "validate_oracle", validate_synthetic_population)
    return state, repo


def test_reference_repair_checks_every_raw_union_and_only_derives_reference_copies(tmp_path, monkeypatch):
    state, repo = repaired_state(tmp_path, monkeypatch)
    raw_before = (state / "measure-routed.json").read_bytes()
    result = reporting.build_report(state, repo=repo)
    repair = result["referenceRepair"]
    assert repair["enabled"] and repair["exactReferencesValidated"] == 322
    assert repair["rawCandidateCountHistogram"] == {"10": 321, "20": 1}
    assert repair["referencesWithMoreThanTenCandidates"] == 1
    assert repair["referencesReorderedOrReduced"] == 1
    assert repair["numericalClient"] == {"numpy": "2.4.1"}
    # The first raw page overlaps only eight true neighbors; simple truncation
    # would reduce this recall, whereas the verified derived reference does not.
    assert result["routed"]["25000"]["unsplit"]["recallAt10Mean"] == 1
    assert result["routed"]["25000"]["unsplit"]["qualityAccepted"]
    assert (state / "measure-routed.json").read_bytes() == raw_before
    model = repair["numericModelSensitivity"]["originalFloat64"]
    assert model["referenceQuestions"] == 121 and model["topTenSetDifferencesFromPrimary"] == 1
    assert model["exactObservationsWithoutVariantCoveredByCandidateUnion"] == 2
    assert repair["numericModelSensitivity"]["float16CorpusFtz"]["topTenSetAgreementsWithPrimary"] == 121
    encoded = json.dumps(result)
    assert "PRIVATE_" not in encoded and str(state) not in encoded and "result_ids" not in encoded
    tables = reporting.render_tables(result)
    assert "validated all 322 retained candidate unions" in tables
    assert "1 of 121 reference questions" in tables and "not replacement references" in tables


@pytest.mark.parametrize(
    "failure",
    ["cache-hash", "missing-ten-proof", "raw-hash", "derived-copy", "failure-metadata", "archive", "numpy-version"],
)
def test_reference_repair_binding_fails_closed_without_publishing(tmp_path, monkeypatch, failure):
    state, repo = repaired_state(tmp_path, monkeypatch)
    if failure == "cache-hash":
        path = state / "offline-exact.json"
        path.write_text(path.read_text() + " ")
    elif failure in ("missing-ten-proof", "raw-hash", "derived-copy"):
        path = state / "exact-reference-derivations.json"
        value = reporting.read(path)
        if failure == "missing-ten-proof":
            value["rows"].pop(0)
        elif failure == "raw-hash":
            value["rows"][0]["rawRowSha256"] = "0" * 64
        else:
            value["rows"][0]["derived"]["result_ids"].reverse()
        save(path, value)
    elif failure == "archive":
        (state / "repair-source" / sorted(reporting.REPAIR_SOURCES)[0]).write_text("tampered")
    else:
        path = state / "reference-repair-binding.json"
        binding = reporting.read(path)
        if failure == "failure-metadata":
            binding["initialFailure"]["requestCharge"] += 1
        else:
            binding["numericalClient"]["numpy"] = "PRIVATE_CLIENT_IDENTIFIER"
        save(path, binding)
    out, tables = repo / "records/new-result.json", repo / "docs/new-result.md"
    with pytest.raises(reporting.InvalidEvidence):
        reporting.write_report(state, out, tables, repo=repo)
    assert not out.exists() and not tables.exists()


def test_reference_repair_rejects_candidate_union_that_misses_primary_truth(tmp_path, monkeypatch):
    state, repo = repaired_state(tmp_path, monkeypatch)
    values = reporting.read(state / "measure-routed.json")
    row = next(r for r in values["rows"] if r["kind"] == "exact" and r["library"] == "large-p050000")
    row["result_ids"][-1] = "large-p050000-d0001:1:0"
    save(state / "measure-routed.json", values)
    path = state / "exact-reference-derivations.json"
    derivations = reporting.read(path)
    entry = next(entry for entry in derivations["rows"] if entry["key"] == ["routed", *reporting.key(row)])
    entry["rawRowSha256"] = reporting.digest(row)
    save(path, derivations)
    with pytest.raises(reporting.InvalidEvidence, match="candidate union"):
        reporting.build_report(state, repo=repo)
