"""Supplement summaries, frozen-reference reuse and fail-closed publication guards."""

import copy
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import scale_ablation_large_late_suite_report as reporting
from scripts import scale_ablation_large_report as base


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def parent_fixture(tmp_path, monkeypatch):
    # Reuse the parent reporter's proven local fixture and its real union-proof
    # derive_reference implementation. No fixture invokes a service.
    source = Path(__file__).with_name("test_scale_ablation_large_report.py")
    spec = importlib.util.spec_from_file_location("parent_report_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.repaired_state(tmp_path, monkeypatch)


def complete_epoch(tmp_path, monkeypatch):
    parent, repo = parent_fixture(tmp_path, monkeypatch)
    parent_record = base.build_report(parent, repo=repo)
    save(repo / reporting.PARENT_RECORD, parent_record)
    (repo / reporting.PARENT_TABLE).write_text(base.render_tables(parent_record))
    monkeypatch.setattr(reporting, "PARENT_SHA", base.sha(repo / reporting.PARENT_RECORD))
    monkeypatch.setattr(reporting, "PARENT_TABLE_SHA", base.sha(repo / reporting.PARENT_TABLE))
    for relative in reporting.SOURCE_FILES | reporting.RECOMPUTATION_FILES:
        path = repo / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic supplementary source")
    monkeypatch.setattr(reporting, "git_sources", lambda *_: reporting.LAUNCH_COMMIT)
    state = tmp_path / "supplement"
    state.mkdir()
    initial = base.phase_rows(parent)
    initial_rows = initial["routed"] + initial["global"]
    by_key = {
        tuple(row[name] for name in ("arm", "library", "shape", "query", "run")): row
        for row in initial_rows
        if row["kind"] == "vector"
    }
    rows = []
    for index, key in enumerate(reporting.expected_plan()):
        row = copy.deepcopy(by_key[key])
        if row["library"] == "scale-p10" and row["arm"] == "unsplit":
            row["request_charge"] = 40
        if row["library"] == "global" and row["arm"] == "unsplit":
            row["result_ids"][-1] = "scale-p01-d0001:1:0"
        row["recordedAt"] = (datetime(2026, 11, 12, 17, tzinfo=UTC) + timedelta(seconds=index * 0.2)).isoformat()
        rows.append(row)
    save(state / f"measure-{reporting.PHASE}.json", {"rows": rows})
    old_status = base.read(parent / "status.json")
    status = {
        "phase": "complete",
        "complete": True,
        "parentArtifactsUnchanged": True,
        "inFlight": None,
        "selectedLibraries": list(base.NEW),
        "startedAt": "2026-11-12T16:59:50Z",
        "completedAt": "2026-11-12T17:03:00Z",
        "lastWriteAckAt": old_status["lastWriteAckAt"],
        "lastWriteUpperBoundAt": old_status["lastWriteUpperBoundAt"],
    }
    save(state / "status.json", status)
    inventory = base.read(parent / "inventory-late.json")
    inventory.update(checkedAt="2026-11-12T16:59:55Z", partial=False, expectedVectors=429773)
    save(state / "inventory-supplementary.json", inventory)
    parent_ru = parent_record["costAccounting"]["knownRu"]
    ann_ru = sum(row["request_charge"] for row in rows)
    save(
        state / "budget.json",
        {
            "parentKnownRu": parent_ru,
            "additionalKnownRu": ann_ru + 5.8,
            "knownRu": parent_ru + ann_ru + 5.8,
            "categories": {"inventory": 5.8, reporting.PHASE + "-vector": ann_ru},
        },
    )
    hashes = reporting.parent_snapshot(parent, repo)
    save(
        state / "binding.json",
        {
            "parentRecordSha256": reporting.PARENT_SHA,
            "parentFiles": hashes,
            "parentEndStateSha256": base.digest(hashes),
            "parentKnownRu": parent_ru,
            "additionalKnownRuCap": 1000000,
            "elapsedSecondsCap": 1800,
            "notBefore": "2026-11-12T16:00:00Z",
            "sourceFiles": {name: base.sha(repo / name) for name in reporting.SOURCE_FILES},
            "queryPlanSha256": base.digest({"rows": reporting.expected_plan()}),
        },
    )
    save(
        state / "audit.json",
        {
            "passed": True,
            "parentArtifactsUnchanged": True,
            "issues": [],
            "qualityFindings": ["PRIVATE_RESULT_ID_NEVER_COPY"],
            "counts": {
                "routedAnnRows": 720,
                "globalAnnRows": 6,
                "totalAnnRows": 726,
                "expectedReferenceSets": 121,
                "validatedArmReferenceSets": 242,
                "parentExactReferencesVerified": 322,
                "PRIVATE_ACCOUNT": 1,
            },
            "endpoint": "PRIVATE_ENDPOINT_NEVER_COPY",
        },
    )
    return state, parent, repo


def test_complete_report_uses_new_five_k_control_preserves_initial_and_reports_variation(tmp_path, monkeypatch):
    state, parent, repo = complete_epoch(tmp_path, monkeypatch)
    parent_before = reporting.parent_snapshot(parent, repo)
    result = reporting.build_report(state, parent, repo=repo)
    assert result["sizeBenefitVersusSupplement5k"]["50000"]["baselineSavingsRu"] == 30
    assert result["initialSizeBenefitVersusInitial5k"]["50000"]["baselineSavingsRu"] == 10
    assert result["initialToSupplementDrift"]["5000"]["unsplit"]["centralSummaryChange"]["ruDifferencePct"] == 100
    paired = result["initialToSupplementDrift"]["5000"]["unsplit"]["pairedQuestionRepeatChange"]
    assert paired["matchedQuestionRepeatPairs"] == 60 and paired["ru"]["pairedPctRange"] == [100, 100]
    questions = result["routed"]["50000"]["split"]["questionVariation"]["questions"]
    assert len(questions) == 20 and all(len(q["ruByRun"]) == 3 for q in questions)
    assert result["global"]["unsplit"]["recallAt10RunMeans"] == [0.9, 0.9, 0.9]
    assert not result["global"]["unsplit"]["qualityAccepted"]
    assert result["globalArmComparison"]["classification"] is None
    assert result["questionZeroSentinelComparisons"]["5000"]["split"]["supplementQuestionZero"]["expectedQueries"] == 1
    assert result["provenance"]["parentReferenceRepair"]["exactReferencesValidated"] == 322
    assert result["independentAudit"]["qualityFindingCount"] == 1
    assert not result["clientAndModelContext"]["measurementClientSeparatelyCaptured"]
    assert reporting.parent_snapshot(parent, repo) == parent_before
    encoded = json.dumps(result)
    assert "PRIVATE_" not in encoded and str(parent) not in encoded and "result_ids" not in encoded
    base.privacy_guard(encoded)
    tables = reporting.render_tables(result)
    assert "RECALL FLOOR FAILURE" in tables
    assert "this epoch's 5k control" in tables and "not evidence of a causal maturity" in tables


@pytest.mark.parametrize(
    "bad", ["status", "audit", "parent", "row-count", "order", "cardinality", "ledger", "time", "inventory"]
)
def test_invalid_epoch_never_writes_outputs(tmp_path, monkeypatch, bad):
    state, parent, repo = complete_epoch(tmp_path, monkeypatch)
    if bad == "status":
        value = base.read(state / "status.json")
        value["complete"] = False
        save(state / "status.json", value)
    elif bad == "audit":
        value = base.read(state / "audit.json")
        value["passed"] = False
        save(state / "audit.json", value)
    elif bad == "parent":
        (parent / "question-vectors.json").write_text("tampered")
    elif bad in {"row-count", "order", "cardinality"}:
        value = base.read(state / f"measure-{reporting.PHASE}.json")
        if bad == "row-count":
            value["rows"].pop()
        elif bad == "order":
            value["rows"][0], value["rows"][1] = value["rows"][1], value["rows"][0]
        else:
            value["rows"][0]["result_ids"].pop()
        save(state / f"measure-{reporting.PHASE}.json", value)
    elif bad == "ledger":
        value = base.read(state / "budget.json")
        value["additionalKnownRu"] += 1
        save(state / "budget.json", value)
    elif bad == "time":
        value = base.read(state / "status.json")
        value["completedAt"] = "2026-11-12T17:31:00Z"
        save(state / "status.json", value)
    else:
        value = base.read(state / "inventory-supplementary.json")
        value["arms"]["split"]["vectors"] -= 1
        save(state / "inventory-supplementary.json", value)
    output, tables = repo / "records/new.json", repo / "docs/new.md"
    with pytest.raises(reporting.InvalidEvidence):
        reporting.write_report(state, parent, output, tables, repo=repo)
    assert not output.exists() and not tables.exists()


def test_incomplete_epoch_fails_before_any_parent_hashing(tmp_path, monkeypatch):
    state, parent = tmp_path / "child", tmp_path / "parent"
    save(state / "status.json", {"complete": False})
    monkeypatch.setattr(reporting, "parent_snapshot", lambda *_: pytest.fail("live run reached expensive hashing"))
    with pytest.raises(reporting.InvalidEvidence, match="not complete"):
        reporting.build_report(state, parent)


def test_unknown_mode_is_hashed_and_does_not_accept_benefit(tmp_path, monkeypatch):
    state, parent, repo = complete_epoch(tmp_path, monkeypatch)
    data = base.read(state / f"measure-{reporting.PHASE}.json")
    for row in data["rows"]:
        if row["library"] == "large-p050000" and row["arm"] == "split":
            row["response_metrics"][0]["indexUtilization"]["UtilizedVectorIndexes"][0]["IndexSpec"][
                "VectorIndexType"
            ] = "PRIVATE_SERVING_ACCOUNT"
    save(state / f"measure-{reporting.PHASE}.json", data)
    result = reporting.build_report(state, parent, repo=repo)
    assert "PRIVATE_SERVING_ACCOUNT" not in json.dumps(result)
    assert not result["routed"]["50000"]["split"]["qualityAccepted"]
    assert result["sizeBenefitVersusSupplement5k"]["50000"]["classification"] is None
    assert result["routed"]["50000"]["split"]["ruMedian"] == 100


def test_protected_existing_destinations_rejected_before_build(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    path = repo / reporting.PARENT_RECORD
    save(path, {"immutable": True})
    before = path.read_bytes()
    monkeypatch.setattr(
        reporting, "build_report", lambda *_args, **_kwargs: pytest.fail("protected path reached build")
    )
    with pytest.raises(reporting.InvalidEvidence):
        reporting.write_report(tmp_path / "state", tmp_path / "parent", path, repo / "docs/new.md", repo=repo)
    assert path.read_bytes() == before


def test_launch_commit_sources_are_verified_against_blobs_not_head(tmp_path, monkeypatch):
    payloads = {name: name.encode() for name in reporting.SOURCE_FILES}
    expected = {name: reporting.hashlib.sha256(value).hexdigest() for name, value in payloads.items()}

    def run(command, **kwargs):
        if "rev-parse" in command:
            return SimpleNamespace(stdout=reporting.LAUNCH_COMMIT + "\n")
        assert "show" in command
        name = command[-1].split(":", 1)[1]
        return SimpleNamespace(stdout=payloads[name])

    monkeypatch.setattr(reporting.subprocess, "run", run)
    assert reporting.git_sources(tmp_path, expected) == reporting.LAUNCH_COMMIT
    expected[next(iter(expected))] = "0" * 64
    with pytest.raises(reporting.InvalidEvidence, match="source differs"):
        reporting.git_sources(tmp_path, expected)
