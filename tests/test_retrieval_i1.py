"""Held-out execution cannot bypass live lifecycle, frozen controls or review sets."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from fli.selector_hints import tool_hints
from fli.structure_lab import digest
from scripts import retrieval_i1 as i1
from scripts.evaluate_agent_retrieval import snapshot
from scripts.retrieval_controls import AGENT_INSTRUCTIONS


class Index:
    def __init__(self, cell):
        self.cell = cell

    def get_library(self, library):
        return {"id": library, "name": "Isolated evaluation"}

    def list_documents(self, library):
        return [{"id": "document", "activeGeneration": 1}]

    def tools(self, library, agent):
        definitions = [{"function": {"name": "read_documents_library_frozen", "description": "Read source."}}]
        return tool_hints(definitions) if self.cell == "selectors-only" else definitions


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    config = {
        "model": {"deployment": "fixed", "model": "gpt-6.1-sol", "version": "2026-09-29"},
        "agent": {"id": "agent", "name": "Agent", "instructions": AGENT_INSTRUCTIONS},
        "libraryId": "evaluation-library",
        "indexUrl": "http://127.0.0.1:8124",
        "searchMode": "vector",
    }
    binding = {key: "isolated-test-setting" for key in i1.BINDING_KEYS}
    binding.update(
        AOAI_ENDPOINT="https://models.example.test",
        SEARCH_MODE="vector",
        AOAI_EMBEDDING_MODEL_REVISION="1",
        AOAI_EMBEDDING_DIMENSIONS="3072",
        SEARCH_DEFAULT_TOP="8",
        SEARCH_MAX_TOP="20",
    )
    now = datetime.now(UTC)
    runtime = {
        "schema": "fli.i1.api-runtime.v1",
        "capturedAt": now.isoformat(),
        "indexUrl": config["indexUrl"],
        "apiSourceSha256": i1.api_hashes(),
        "settingsBinding": binding,
        "settingsBindingSha256": digest(binding),
        "effectiveSettings": i1.SETTINGS,
        "authenticationMode": "dev",
        "environment": "local",
        "processId": 123,
        "ownEnvironmentAndImportVerified": True,
        "sourceRoot": str(tmp_path / "source"),
        "pythonPrefix": str(tmp_path / "source/.venv"),
        "gitRevision": "frozen-code",
        "declaredCodeRevision": "frozen-code",
    }
    raw = tmp_path / "raw.json"
    raw.write_text('{"actualCheck":true}\n')
    lifecycle = {
        "schema": "fli.retrieval-i1-lifecycle.v1",
        "status": "complete",
        "live": True,
        "authenticationMode": "dev",
        "profile": "microsoft-indexer-layout-v1",
        "experimentPlanSha256": i1.PLAN_SHA256,
        "apiSourceSha256": i1.api_hashes(),
        "settingsBindingSha256": digest(binding),
        "ownedLibraryIds": ["disposable-lifecycle-library"],
        "startedAt": (now - timedelta(minutes=2)).isoformat(),
        "finishedAt": (now - timedelta(minutes=1)).isoformat(),
        "checks": [{"id": name, "passed": True} for name in i1.read(i1.PLAN)["requiredLifecycleChecks"]],
        "rawReceiptPaths": {"checks": str(raw)},
        "rawReceiptsSha256": {"checks": i1.sha(raw)},
    }
    path = tmp_path / "lifecycle.json"
    # Noncanonical whitespace must survive exact-byte authorization binding.
    path.write_text(json.dumps(lifecycle, separators=(",", ":")))
    out = tmp_path / "run"
    authorization = {
        "schema": "fli.i1.execution-authorization.v1",
        "status": "accepted",
        "reviewedAt": now.isoformat(),
        "lifecycleReceiptSha256": i1.sha(path),
        "experimentPlanSha256": i1.PLAN_SHA256,
        "settingsBindingSha256": digest(binding),
        "libraryId": config["libraryId"],
        "outputDirectory": str(out),
        "executionLedger": str(tmp_path / "one-execution.json"),
    }
    monkeypatch.setenv("AOAI_ENDPOINT", binding["AOAI_ENDPOINT"])
    return config, runtime, path, authorization, out


def mocked_services(monkeypatch, seen):
    monkeypatch.setattr(i1, "make_index", lambda config, cell: Index(cell))
    monkeypatch.setattr(i1, "bound_deployment", lambda *args: {"binding": "same"})
    monkeypatch.setattr(i1, "validate_heldout_sources", lambda *args: None)
    monkeypatch.setattr(i1, "default_chat_factory", lambda *args: object())

    def replay(index, chat, agent, cases, output, budget):
        assert budget == 200000
        assert all(c["split"] == "heldout" for c in cases)
        seen.append((index.cell, [c["case"] for c in cases]))
        output.mkdir()
        before = snapshot(index, agent)
        rows = [{"case": c["case"], "turn": 1, "question": c["question"]} for c in cases]
        for name, value in [
            ("run-status", {"status": "complete", "completedTurns": len(rows), "budget": budget}),
            ("cases", cases),
            ("source-snapshot", before),
            ("snapshot-check", {"unchanged": True, "before": before, "after": before}),
            ("results", rows),
        ]:
            i1.save(output / f"{name}.json", value)
        return rows

    monkeypatch.setattr(i1, "replay", replay)


def test_entire_frozen_heldout_schedule_and_exact_lifecycle_binding(inputs, monkeypatch):
    config, runtime, lifecycle, authorization, out = inputs
    seen = []
    mocked_services(monkeypatch, seen)
    result = i1.run(config, {}, runtime, lifecycle, authorization, out)
    assert result == {"status": "pending_deployment_recheck", "completedTurns": 64, "heldoutExecuted": True}
    assert [cell for cell, cases in seen] == ["baseline", "selectors-only", "selectors-only", "baseline"]
    assert all(cases == [f"r0-{i}" for i in range(33, 49)] for cell, cases in seen)
    assert (out / "lifecycle.json").read_bytes() == lifecycle.read_bytes()
    assert i1.finalize(out, {}, runtime)["status"] == "complete"
    assert len(i1.read(out / "finalization.json")["artifactsSha256"]) == 8


@pytest.mark.parametrize(
    "change",
    [
        "failed",
        "not-live",
        "missing-check",
        "raw-drift",
        "wrong-plan",
        "wrong-api",
        "same-library",
        "unreviewed",
        "stale",
        "future",
        "wrong-settings",
    ],
)
def test_lifecycle_failure_prevents_any_source_or_model_call(inputs, monkeypatch, change):
    config, runtime, path, authorization, out = inputs
    receipt = i1.read(path)
    if change == "failed":
        receipt["checks"][0]["passed"] = False
    elif change == "not-live":
        receipt["live"] = False
    elif change == "missing-check":
        receipt["checks"].pop()
    elif change == "raw-drift":
        (path.parent / "raw.json").write_text("changed")
    elif change == "wrong-plan":
        receipt["experimentPlanSha256"] = "wrong"
    elif change == "wrong-api":
        receipt["apiSourceSha256"] = {}
    elif change == "same-library":
        receipt["ownedLibraryIds"] = [config["libraryId"]]
    elif change == "unreviewed":
        authorization["status"] = "pending"
    elif change == "stale":
        receipt["finishedAt"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    elif change == "future":
        receipt["finishedAt"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    elif change == "wrong-settings":
        receipt["settingsBindingSha256"] = "wrong"
    path.write_text(json.dumps(receipt))
    authorization["lifecycleReceiptSha256"] = i1.sha(path)
    monkeypatch.setattr(i1, "make_index", lambda *args: pytest.fail("must not access sources"))
    monkeypatch.setattr(i1, "default_chat_factory", lambda *args: pytest.fail("must not call answering model"))
    with pytest.raises(ValueError):
        i1.run(config, {}, runtime, path, authorization, out)


@pytest.mark.parametrize(
    "change", ["prompt", "model", "query", "runtime-settings", "runtime-old", "endpoint", "import", "code", "revision"]
)
def test_comparison_control_drift_prevents_execution(inputs, monkeypatch, change):
    config, runtime, lifecycle, authorization, out = inputs
    if change == "prompt":
        config["agent"]["instructions"] = "changed"
    elif change == "model":
        config["model"]["version"] = "changed"
    elif change == "query":
        config["searchMode"] = "hybrid"
    elif change == "runtime-settings":
        runtime["effectiveSettings"] = {**i1.SETTINGS, "searchMaxTop": 30}
    elif change == "runtime-old":
        runtime["capturedAt"] = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    elif change == "endpoint":
        monkeypatch.setenv("AOAI_ENDPOINT", "https://wrong.example.test")
    elif change == "import":
        runtime["ownEnvironmentAndImportVerified"] = False
    elif change == "code":
        runtime["apiSourceSha256"] = {}
    elif change == "revision":
        runtime["declaredCodeRevision"] = "wrong"
    monkeypatch.setattr(i1, "make_index", lambda *args: pytest.fail("must not access sources"))
    with pytest.raises(ValueError):
        i1.run(config, {}, runtime, lifecycle, authorization, out)


def test_partial_execution_stays_claimed_and_cannot_be_finalized_or_rerun(inputs, monkeypatch):
    config, runtime, lifecycle, authorization, out = inputs
    seen = []
    mocked_services(monkeypatch, seen)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(i1, "replay", interrupt)
    with pytest.raises(KeyboardInterrupt):
        i1.run(config, {}, runtime, lifecycle, authorization, out)
    assert i1.read(out / "run-status.json")["status"] == "interrupted"
    assert i1.private_path(authorization["executionLedger"]).exists()
    with pytest.raises((ValueError, FileNotFoundError)):
        i1.finalize(out, {}, runtime)
    new = out.parent / "rerun"
    authorization["outputDirectory"] = str(new)
    with pytest.raises(FileExistsError):
        i1.run(config, {}, runtime, lifecycle, authorization, new)
    assert i1.read(new / "run-status.json")["heldoutExecuted"] is False


def test_blank_original_paragraphs_need_explicit_region_review():
    case = {"sourceFacts": [{"regions": [{"id": "form:p1"}, {"id": "form:p2"}]}]}
    gold = {"regions": {"form:p1": {"expectedText": "Name"}, "form:p2": {"expectedText": ""}}}
    review = {
        "deliveredRegionIds": ["form:p1", "form:p2"],
        "blankRegionIdsVerified": [],
        "relevantSourceCoverage": {"requiredRegionCount": 2, "deliveredRegionCount": 2},
    }
    with pytest.raises(ValueError, match="blank paragraphs"):
        i1.reviewed_regions(case, review, gold)
    review["blankRegionIdsVerified"] = ["form:p2"]
    assert i1.reviewed_regions(case, review, gold)[0] == ["form:p1", "form:p2"]
    review["deliveredRegionIds"] = ["form:p1", "unrelated:p2"]
    with pytest.raises(ValueError, match="distinct original"):
        i1.reviewed_regions(case, review, gold)


def test_equal_coverage_counts_cannot_hide_lost_region_or_relationship():
    rows = []
    for repeat in (1, 2):
        for cell in i1.CELLS:
            rows.append(
                {
                    "repeat": repeat,
                    "cell": cell,
                    "case": "task",
                    "deliveredRegionIds": ["a", "b"],
                    "quality": dict.fromkeys(i1.QUALITY, True),
                    "errorCategories": {},
                    "relevantSourceCoverage": {
                        "relationshipsDelivered": True,
                        "completeOrderedSection": True,
                        "catalogueComplete": None,
                    },
                }
            )
    changed = rows[1]
    changed["deliveredRegionIds"] = ["a", "c"]
    changed["relevantSourceCoverage"]["completeOrderedSection"] = None
    paired = i1.paired_comparison(rows, [{"case": "task"}])
    assert paired[0]["lostSourceRegionIds"] == ["b"]
    assert paired[0]["qualityRegressions"] == ["completeOrderedSection"]
    assert paired[0]["noRegression"] is False and paired[1]["noRegression"] is True


def test_plan_cannot_silently_drop_heldout_tasks(tmp_path, monkeypatch):
    plan = copy.deepcopy(i1.read(i1.PLAN))
    plan["cases"].pop()
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(i1, "PLAN", path)
    with pytest.raises(ValueError, match="plan bytes changed"):
        i1.frozen_inputs()


@pytest.mark.parametrize("change", ["library-revision", "document-revision", "generation", "source"])
def test_source_recipe_and_active_generation_bind_the_fixed_embedding_revision(monkeypatch, change):
    monkeypatch.setattr(i1, "validate_sources", lambda *args: None)
    recipe = {"embedding": {"modelRevision": "1"}}
    document = {
        "activeGeneration": 1,
        "sha256": "source-bytes",
        "ingestionJob": {"profile": copy.deepcopy(recipe), "generation": 1, "sourceSha256": "source-bytes"},
    }
    captured = {"libraries": {"library": {"library": {"ingestionProfile": recipe}, "documents": [document]}}}
    if change == "library-revision":
        recipe["embedding"]["modelRevision"] = "2"
    elif change == "document-revision":
        document["ingestionJob"]["profile"]["embedding"]["modelRevision"] = "2"
    elif change == "generation":
        document["ingestionJob"]["generation"] = 2
    elif change == "source":
        document["ingestionJob"]["sourceSha256"] = "different-bytes"
    with pytest.raises(ValueError):
        i1.validate_heldout_sources(captured, {"libraryId": "library"}, {"baseline": {}})


@pytest.mark.parametrize("kind", ["table", "section", "safe-refusal"])
def test_unknown_applicable_quality_never_establishes_no_regression(kind):
    case = {"case": "task"}
    if kind == "table":
        case["relationshipReviewRequired"] = True
    elif kind == "section":
        case["family"] = "complete-section-read"
    rows = []
    for repeat in (1, 2):
        for cell in i1.CELLS:
            quality = dict.fromkeys(i1.QUALITY, True)
            quality["sourceRelationships"] = None
            if kind == "safe-refusal":
                quality.update(taskCompletion=False, factualSupport=None, citationAccuracy=None)
            rows.append(
                {
                    "repeat": repeat,
                    "cell": cell,
                    "case": "task",
                    "deliveredRegionIds": [],
                    "quality": quality,
                    "errorCategories": {},
                    "relevantSourceCoverage": dict.fromkeys(
                        ("relationshipsDelivered", "completeOrderedSection", "catalogueComplete"), None
                    ),
                }
            )
    paired = i1.paired_comparison(rows, [case])
    assert all(p["unresolvedQualityFields"] and not p["noRegression"] for p in paired)


def test_actual_wire_rejects_disappearing_earlier_evidence_and_shared_history():
    evidence = {"text": "Original source"}
    row = {"case": "task", "question": "Frozen?", "toolReplies": [evidence]}
    final = {"messages": [{"role": "user", "content": "Frozen?"}, {"role": "tool", "content": json.dumps(evidence)}]}
    assert i1.verify_i1_wire_sources([final], [row]) == {"task": [evidence]}
    earlier = copy.deepcopy(final)
    earlier["messages"][1]["content"] = json.dumps({"text": "Untracked earlier source"})
    with pytest.raises(ValueError, match="earlier provider evidence"):
        i1.verify_i1_wire_sources([earlier, final], [row])
    shared = copy.deepcopy(final)
    shared["messages"].append({"role": "user", "content": "Another task?"})
    with pytest.raises(ValueError, match="shared task history"):
        i1.verify_i1_wire_sources([shared], [row])
    repair = copy.deepcopy(final)
    repair["messages"].append(
        {
            "role": "user",
            "content": "Before finalizing, correct these unmet requirements: "
            "Cite the source. If the source cannot support a deliverable, explain the limit.",
        }
    )
    assert i1.verify_i1_wire_sources([repair], [row]) == {"task": [evidence]}
