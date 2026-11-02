"""Source-delivery and privacy boundaries of sanitized R0 scorecards."""

import copy
import hashlib
import json

import pytest

from scripts import score_retrieval_benchmark as scoring
from scripts.structure_lab import save

SOURCE = "The exact original source assertion."
PRIVATE = "PRIVATE_CONTENT_OR_ENVIRONMENT_SENTINEL"


def labels(text=SOURCE):
    region = {
        "id": "d01:p1",
        "kind": "paragraph",
        "index": 1,
        "sourceSha256": "source-bytes",
        "normalizedTextSha256": hashlib.sha256(scoring.normalize(text).encode()).hexdigest(),
    }
    case = {
        "case": "r0-local-test",
        "split": "development",
        "question": "What is the source assertion?",
        "sourceFacts": [{"sourceId": "d01", "source": "source.docx", "regions": [region]}],
    }
    gold = {
        "regions": {
            region["id"]: {
                "sourceId": "d01",
                "fileName": "source.docx",
                "expectedText": text,
                "locator": {"kind": "paragraph", "index": 1},
            }
        }
    }
    return case, gold


def body(text=SOURCE, filename="source.docx", **kwargs):
    return {"mode": "passages", "results": [{"fileName": filename, "text": text, **kwargs}]}


@pytest.mark.parametrize("mode", ["catalogue", "semantic_candidates", "outline", "inspect"])
def test_metadata_and_navigation_do_not_count_as_source_delivery(mode):
    case, gold = labels()
    response = {**body(), "mode": mode}
    assert scoring.coverage(case, [response], gold)["matched"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"textSource": "generated_summary"},
        {"contentKind": "image"},
        {"textSource": "generated_description"},
    ],
)
def test_generated_or_visual_text_does_not_count_as_original_source(changes):
    case, gold = labels()
    assert scoring.coverage(case, [body(**changes)], gold)["matched"] == 0


def test_refused_read_is_excluded_but_original_recovery_passages_count():
    case, gold = labels()
    refused = {**body(), "disposition": "budget_exceeded"}
    assert scoring.coverage(case, [refused], gold)["matched"] == 0
    recovered = {**refused, "automaticRecovery": "document_scoped_passages"}
    assert scoring.coverage(case, [recovered], gold)["matched"] == 1


def test_exact_delivery_is_bound_to_file_and_not_assembled_across_fragments():
    case, gold = labels()
    assert scoring.coverage(case, [body(filename="different.docx")], gold)["matched"] == 0
    fragments = [body("The exact original"), body("source assertion.")]
    result = scoring.coverage(case, fragments, gold)
    assert result["matched"] == 0
    assert "not source loss" in result["meaning"]
    assert result["completeSectionInOrderVerified"] is None
    assert scoring.coverage(case, [body("The\n exact  original source assertion.")], gold)["matched"] == 1


def test_neighbor_original_text_can_supply_evidence_without_counting_it_twice():
    case, gold = labels()
    reply = body("Another paragraph.")
    reply["results"][0]["neighbors"] = [{"text": SOURCE}, {"text": SOURCE}]
    result = scoring.coverage(case, [reply], gold)
    assert result["matched"] == result["textApplicable"] == 1
    assert result["regions"][0]["relationshipVerified"] is None


def test_blank_table_cell_never_becomes_a_substring_match():
    case, gold = labels("")
    result = scoring.coverage(case, [body()], gold)
    assert result["totalRegions"] == 1
    assert result["textApplicable"] == result["matched"] == 0
    assert result["regions"][0]["singleBodyNormalizedTextMatch"] is None
    assert result["regions"][0]["relationshipVerified"] is None


@pytest.mark.parametrize("damage", ["text", "file"])
def test_gold_cannot_change_text_or_source_identity(damage):
    case, gold = labels()
    if damage == "text":
        gold["regions"]["d01:p1"]["expectedText"] = "Different assertion."
    else:
        gold["regions"]["d01:p1"]["fileName"] = "different.docx"
    with pytest.raises(ValueError):
        scoring.coverage(case, [body(filename="different.docx")], gold)


def test_usage_keeps_unknown_totals_distinct_from_known_subtotals():
    result = scoring.usage(
        {
            "metrics": {
                "promptTokens": None,
                "retrievalRequestCharge": 3.5,
                "retrievalChargeComplete": False,
                "modelCalls": [
                    {"promptTokens": 10, "cachedPromptTokens": 2, "reasoningTokens": 5},
                    {"promptTokens": None, "cachedPromptTokens": None, "reasoningTokens": None},
                ],
            },
            "toolReplies": [],
        }
    )
    assert result["promptTokens"] is None
    assert result["cachedPromptTokens"] is None and result["cachedPromptTokensKnownSubtotal"] == 2
    assert result["reasoningTokens"] is None and result["reasoningTokensKnownSubtotal"] == 5
    assert result["maxProviderInputTokens"] is None
    assert result["retrievalRequestCharge"] == 3.5 and result["retrievalChargeComplete"] is False


@pytest.mark.parametrize("value", [True, PRIVATE, float("nan"), float("inf")])
def test_usage_rejects_nonfinite_or_nonnumeric_values(value):
    assert scoring.numeric(value) is None


def private_run(tmp_path):
    case, gold = labels()
    manifest, tasks = {"documents": [{"id": "d01", "sha256": "source-bytes", "fileName": "source.docx"}]}, [case]
    run = tmp_path / "private-replay"
    run.mkdir()
    save(
        run / "controls.json",
        {
            "manifestSha256": scoring.digest(manifest),
            "tasksSha256": scoring.digest(tasks),
            "cases": tasks,
            "schedule": [{"role": role, "repeat": 1} for role in scoring.ROLES],
            "model": {"model": "gpt-6.1-sol", "version": "2026-09-29", "deployment": PRIVATE},
            "contextAllowancePerRun": 200000,
            "codeSha256": {"src/fli_demo/agent.py": "code-digest"},
            "agent": {"id": PRIVATE},
        },
    )
    save(run / "run-status.json", {"status": "failed", "completedTurns": 1, "privateDiagnostic": PRIVATE})
    cell = run / "r1-current"
    cell.mkdir()
    save(
        cell / "results.json",
        [
            {
                "case": case["case"],
                "turn": 1,
                "question": case["question"],
                "answer": PRIVATE,
                "toolReplies": [body()],
                "traces": [{"libraryId": PRIVATE}],
                "metrics": {
                    "modelCalls": [{"promptTokens": 42, "responseModel": PRIVATE}],
                    "retrievalChargeComplete": PRIVATE,
                },
            }
        ],
    )
    save(
        cell / "run-status.json",
        {
            "status": "failed",
            "completedTurns": 1,
            "errorType": "ResponsesProtocolError",
            "failedAttempt": {
                "scope": "final_provider_call_only; earlier calls in unfinished turn are not included",
                "usage": {"total_tokens": 17, "secret": PRIVATE},
                "timing": {"providerMs": 1.5},
                "usageAvailability": "provider_reported",
            },
        },
    )
    return run, manifest, tasks, gold


def test_export_omits_content_environment_and_unreviewed_quality(tmp_path):
    result = scoring.score(*private_run(tmp_path))
    encoded = json.dumps(result)
    assert PRIVATE not in encoded and SOURCE not in encoded
    assert all(value is None for value in result["rows"][0]["quality"].values())
    assert result["rows"][0]["reviewAvailable"] is False
    assert result["rows"][0]["usage"]["retrievalChargeComplete"] is None
    assert result["summary"]["current"]["taskCompletion"] == {"passed": 0, "applicable": 0}


def test_known_failed_attempt_usage_survives_sanitized_export(tmp_path):
    result = scoring.score(*private_run(tmp_path))
    failure = result["attemptReceipts"][0]["failedAttempt"]
    assert failure["usage"]["total_tokens"] == 17
    assert failure["timing"]["providerMs"] == 1.5
    assert PRIVATE not in json.dumps(failure)


def test_manual_quality_is_separate_and_review_comments_stay_private(tmp_path):
    args = private_run(tmp_path)
    review = {
        "repeat": 1,
        "role": "current",
        "case": "r0-local-test",
        "turn": 1,
        "taskCompletion": True,
        "factualSupport": False,
        "citationAccuracy": None,
        "sourceRelationships": None,
        "failureCategories": ["wrong_attribution"],
        "notes": PRIVATE,
    }
    result = scoring.score(*args, reviews=[review])
    row = result["rows"][0]
    assert row["sourceDelivery"]["matched"] == 1
    assert row["quality"]["factualSupport"] is False
    assert row["quality"]["citationAccuracy"] is None
    assert "notes" not in row
    assert result["summary"]["current"]["factualSupport"] == {"passed": 0, "applicable": 1}
    for invalid in ([review, review], [{**review, "case": "unexecuted"}], [{**review, "taskCompletion": 1}]):
        with pytest.raises(ValueError):
            scoring.score(*args, reviews=invalid)


def test_scoring_refuses_changed_manifest_or_task_pack(tmp_path):
    run, manifest, tasks, gold = private_run(tmp_path)
    changed = copy.deepcopy(tasks)
    changed[0]["case"] = "changed"
    with pytest.raises(ValueError, match="frozen scoring inputs"):
        scoring.score(run, manifest, changed, gold)


def complete_run(tmp_path):
    args = private_run(tmp_path)
    run, _manifest, tasks, _gold = args
    row = scoring.read(run / "r1-current/results.json")[0]
    root = {"agent": {"libraryIds": list(scoring.ROLES)}, "libraries": {role: {} for role in scoring.ROLES}}
    save(run / "source-snapshot.json", root)
    for role in scoring.ROLES:
        cell = run / f"r1-{role}"
        cell.mkdir(exist_ok=True)
        save(cell / "results.json", [row])
        save(cell / "run-status.json", {"status": "complete", "completedTurns": 1, "budget": 200000})
        before = {"agent": {"libraryIds": [role]}, "libraries": {role: {}}}
        save(cell / "source-snapshot.json", before)
        save(cell / "cases.json", tasks)
        save(cell / "snapshot-check.json", {"unchanged": True, "before": before, "after": before})
    save(
        run / "run-status.json",
        {"status": "complete", "completedTurns": 3, "snapshotsUnchanged": True, "heldoutExecuted": False},
    )
    return args


def test_complete_scorecard_requires_every_matched_cell_but_not_quality_passes(tmp_path):
    result = scoring.score(*complete_run(tmp_path))
    assert result["status"]["status"] == "complete"
    assert result["completeness"]["executionComplete"]
    assert result["completeness"]["completeMatchedTriples"] == 1
    assert result["completeness"]["missingTurns"] == []
    assert all(row["quality"]["taskCompletion"] is None for row in result["rows"])


@pytest.mark.parametrize("damage", ["missing-check", "snapshot", "root-binding"])
def test_claimed_unchanged_status_requires_original_snapshot_artifacts(tmp_path, damage):
    args = complete_run(tmp_path)
    path = args[0] / "r1-current/snapshot-check.json"
    if damage == "missing-check":
        path.unlink()
    elif damage == "snapshot":
        value = scoring.read(path)
        value["after"]["libraries"]["current"] = {"generation": 2}
        save(path, value)
    else:
        root = scoring.read(args[0] / "source-snapshot.json")
        root["libraries"]["current"] = {"generation": 2}
        save(args[0] / "source-snapshot.json", root)
    result = scoring.score(*args)
    assert not result["completeness"]["executionComplete"]
    assert not result["completeness"]["sourceArtifactsVerified"]


def test_executed_question_cannot_differ_from_selected_question(tmp_path):
    args = complete_run(tmp_path)
    path = args[0] / "r1-current/results.json"
    rows = scoring.read(path)
    rows[0]["question"] = "A different easier question"
    save(path, rows)
    with pytest.raises(ValueError, match="executed question"):
        scoring.score(*args)


@pytest.mark.parametrize("damage", ["missing-file", "missing-row", "root-count", "cell-status", "failed-cell", "drift"])
def test_missing_or_inconsistent_receipts_cannot_inherit_complete_status(tmp_path, damage):
    args = complete_run(tmp_path)
    run = args[0]
    if damage == "missing-file":
        (run / "r1-custom/results.json").unlink()
    elif damage == "missing-row":
        save(run / "r1-custom/results.json", [])
    elif damage == "root-count":
        state = scoring.read(run / "run-status.json")
        save(run / "run-status.json", state | {"completedTurns": 99})
    elif damage == "cell-status":
        (run / "r1-custom/run-status.json").unlink()
    elif damage == "failed-cell":
        save(run / "r1-custom/run-status.json", {"status": "failed", "completedTurns": 1})
    else:
        state = scoring.read(run / "run-status.json")
        save(run / "run-status.json", state | {"snapshotsUnchanged": False})
    result = scoring.score(*args)
    assert result["status"]["status"] == "incomplete"
    assert result["status"]["reportedRunStatus"] == "complete"
    assert not result["completeness"]["executionComplete"]
    if damage in {"missing-file", "missing-row"}:
        assert result["summary"]["custom"]["missingTurns"] == 1
        assert result["completeness"]["completeMatchedTriples"] == 0


@pytest.mark.parametrize("damage", ["duplicate", "unknown-case", "extra-turn", "boolean-turn"])
def test_duplicate_and_unexpected_result_turns_are_refused(tmp_path, damage):
    args = complete_run(tmp_path)
    target = args[0] / "r1-current/results.json"
    rows = scoring.read(target)
    if damage == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif damage == "unknown-case":
        rows[0]["case"] = PRIVATE
    else:
        rows[0]["turn"] = True if damage == "boolean-turn" else 2
    save(target, rows)
    with pytest.raises(ValueError, match="duplicate|unexpected"):
        scoring.score(*args)


@pytest.mark.parametrize("damage", ["duplicate", "missing-role", "unknown-role", "extra-field", "boolean-repeat"])
def test_schedule_is_validated_before_reading_or_exporting_cells(tmp_path, damage):
    args = private_run(tmp_path)
    target = args[0] / "controls.json"
    controls = scoring.read(target)
    if damage == "duplicate":
        controls["schedule"].append(controls["schedule"][0].copy())
    elif damage == "missing-role":
        controls["schedule"].pop()
    elif damage == "unknown-role":
        controls["schedule"][0]["role"] = "../" + PRIVATE
    elif damage == "extra-field":
        controls["schedule"][0]["privateEndpoint"] = PRIVATE
    else:
        controls["schedule"][0]["repeat"] = True
    save(target, controls)
    with pytest.raises(ValueError, match="schedule|comparison cell"):
        scoring.score(*args)


def test_selected_cases_must_match_frozen_task_content(tmp_path):
    args = private_run(tmp_path)
    target = args[0] / "controls.json"
    controls = scoring.read(target)
    controls["cases"][0]["question"] = "Changed after task freeze."
    save(target, controls)
    with pytest.raises(ValueError, match="selected case"):
        scoring.score(*args)


@pytest.mark.parametrize("damage", ["source-hash", "gold-source-hash", "locator", "source-file", "duplicate-region"])
def test_gold_binds_to_manifest_bytes_and_source_locator(damage):
    case, gold = labels()
    manifest = {"documents": [{"id": "d01", "sha256": "source-bytes", "fileName": "source.docx"}]}
    if damage == "source-hash":
        case["sourceFacts"][0]["regions"][0]["sourceSha256"] = "different-source"
    elif damage == "gold-source-hash":
        gold["regions"]["d01:p1"]["sourceSha256"] = "different-source"
    elif damage == "locator":
        gold["regions"]["d01:p1"]["locator"]["index"] = 2
    elif damage == "source-file":
        case["sourceFacts"][0]["source"] = "different.docx"
        gold["regions"]["d01:p1"]["fileName"] = "different.docx"
    else:
        case["sourceFacts"][0]["regions"] *= 2
    with pytest.raises(ValueError):
        scoring.validate_gold(manifest, [case], gold)


def test_string_citation_without_bound_filename_cannot_credit_source_text():
    case, gold = labels()
    reply = {"results": [{"citation": "source.docx (paragraph 1)", "text": SOURCE}]}
    assert scoring.coverage(case, [reply], gold)["matched"] == 0


def coverage_review(role="current", delivered=1):
    return {
        "repeat": 1,
        "role": role,
        "case": "r0-local-test",
        "turn": 1,
        "relevantSourceCoverage": {
            "requiredRegionCount": 1,
            "deliveredRegionCount": delivered,
            "relationshipsDelivered": None,
            "completeOrderedSection": None,
            "catalogueComplete": None,
            "notes": PRIVATE,
        },
    }


def test_manual_source_coverage_is_distinct_allowlisted_and_missing_stays_unavailable(tmp_path):
    args = complete_run(tmp_path)
    result = scoring.score(*args, reviews=[coverage_review(delivered=0)])
    row = result["rows"][0]
    assert row["sourceDelivery"]["matched"] == 1
    assert row["relevantSourceCoverage"] == {
        "requiredRegionCount": 1,
        "deliveredRegionCount": 0,
        "relationshipsDelivered": None,
        "completeOrderedSection": None,
        "catalogueComplete": None,
    }
    assert result["rows"][1]["relevantSourceCoverage"] is None
    assert PRIVATE not in json.dumps(result)
    assert result["summary"]["current"]["relevantSourceCoverage"]["allRequiredRegions"] == {
        "passed": 0,
        "applicable": 1,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requiredRegionCount", True),
        ("requiredRegionCount", 2),
        ("requiredRegionCount", 1.0),
        ("deliveredRegionCount", True),
        ("deliveredRegionCount", -1),
        ("deliveredRegionCount", 2),
        ("deliveredRegionCount", 1.0),
        ("relationshipsDelivered", 1),
        ("completeOrderedSection", "true"),
        ("catalogueComplete", PRIVATE),
    ],
)
def test_manual_source_coverage_rejects_wrong_counts_and_nonboolean_flags(tmp_path, field, value):
    review = coverage_review()
    review["relevantSourceCoverage"][field] = value
    with pytest.raises(ValueError, match="manual"):
        scoring.score(*private_run(tmp_path), reviews=[review])


def test_paired_manual_coverage_excludes_unavailable_counts_and_keeps_false_flags(tmp_path):
    args = complete_run(tmp_path)
    reviews = [coverage_review("current", 1), coverage_review("custom", 0), coverage_review("microsoft", None)]
    reviews[2]["relevantSourceCoverage"]["relationshipsDelivered"] = False
    result = scoring.score(*args, reviews=reviews)
    for role in scoring.ROLES:
        assert result["summary"][role]["relevantSourceCoverage"]["pairedAllRequiredRegions"] == {
            "passed": 0,
            "applicable": 0,
        }
    microsoft = result["summary"]["microsoft"]["relevantSourceCoverage"]
    assert microsoft["allRequiredRegions"] == {"passed": 0, "applicable": 0}
    assert microsoft["relationshipsDelivered"] == {"passed": 0, "applicable": 1}
    reviews[2]["relevantSourceCoverage"]["deliveredRegionCount"] = 1
    result = scoring.score(*args, reviews=reviews)
    for role, passed in [("current", 1), ("custom", 0), ("microsoft", 1)]:
        assert result["summary"][role]["relevantSourceCoverage"]["pairedAllRequiredRegions"] == {
            "passed": passed,
            "applicable": 1,
        }


def test_zero_region_catalogue_coverage_does_not_create_a_vacuous_source_pass():
    case = {"sourceFacts": []}
    value = scoring.relevant_source_coverage(
        case, {"requiredRegionCount": 0, "deliveredRegionCount": 0, "catalogueComplete": True}
    )
    assert not scoring.counted_source_coverage(value)
    assert value["catalogueComplete"] is True
    assert value["relationshipsDelivered"] is None


def test_manual_coverage_counts_unique_frozen_region_identifiers():
    case, _gold = labels()
    case["sourceFacts"][0]["regions"] *= 2
    value = scoring.relevant_source_coverage(case, {"requiredRegionCount": 1})
    assert value["requiredRegionCount"] == 1
    assert value["deliveredRegionCount"] is None
