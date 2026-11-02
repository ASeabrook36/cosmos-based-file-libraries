import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import retrieval_controls as controls
from scripts.structure_lab import save


def envelope():
    return {
        "capturedAt": "2026-11-01T12:00:00+00:00",
        "account": {"id": "/accounts/isolated", "properties": {"endpoint": "https://example.invalid/"}},
        "deployment": {
            "id": "/accounts/isolated/deployments/diagnostic",
            "sku": {"name": "GlobalStandard", "capacity": 200},
            "properties": {
                "model": {"format": "OpenAI", "name": "gpt-6.1-sol", "version": "2026-09-29"},
                "versionUpgradeOption": "NoAutoUpgrade",
                "provisioningState": "Succeeded",
            },
        },
    }


def bind(receipt, **kwargs):
    return controls.deployment_binding(
        receipt,
        configured_endpoint="https://example.invalid",
        configured_deployment="diagnostic",
        model="gpt-6.1-sol",
        revision="2026-09-29",
        now=kwargs.get("now", datetime(2026, 11, 1, 12, 1, tzinfo=UTC)),
    )


@pytest.mark.parametrize("damage", ["account", "deployment", "endpoint", "model", "upgrade", "state", "sku"])
def test_model_receipt_must_bind_actual_invocation(damage):
    receipt = envelope()
    if damage == "account":
        receipt["account"]["id"] = "/accounts/other"
    elif damage == "deployment":
        receipt["deployment"]["id"] += "-other"
    elif damage == "endpoint":
        receipt["account"]["properties"]["endpoint"] = "https://other.invalid"
    elif damage == "model":
        receipt["deployment"]["properties"]["model"]["version"] = "other"
    elif damage == "upgrade":
        receipt["deployment"]["properties"]["versionUpgradeOption"] = "OnceNewDefaultVersionAvailable"
    elif damage == "state":
        receipt["deployment"]["properties"]["provisioningState"] = "Creating"
    else:
        receipt["deployment"].pop("sku")
    with pytest.raises(ValueError):
        bind(receipt)


@pytest.mark.parametrize("minutes", [-1, 16])
def test_stale_or_future_receipts_rejected(minutes):
    with pytest.raises(ValueError, match="timestamp"):
        bind(envelope(), now=datetime(2026, 11, 1, 12, tzinfo=UTC) + timedelta(minutes=minutes))


def test_same_model_on_different_capacity_changes_fingerprint():
    receipt = envelope()
    first = bind(receipt)
    receipt["deployment"]["sku"]["capacity"] = 100
    assert bind(receipt)["bindingSha256"] != first["bindingSha256"]
    assert "example.invalid" not in str(first)


def test_frozen_pack_and_prompt_enforcement():
    manifest, _tasks = controls.frozen_pack(Path(__file__).resolve().parents[1] / "docs/testing/retrieval-r0")
    assert manifest["baseline"]["repeats"] == 2
    value = {
        **controls.ANSWER_CONTROLS,
        "model": {"model": "gpt-6.1-sol", "version": "2026-09-29"},
        "agent": {"instructions": controls.AGENT_INSTRUCTIONS},
    }
    controls.answer_controls(value)
    for key, changed in [("contextAllowancePerRun", 24000), ("store", 0), ("reasoningEffort", "low")]:
        with pytest.raises(ValueError):
            controls.answer_controls({**value, key: changed})
    with pytest.raises(ValueError, match="instructions"):
        controls.answer_controls({**value, "agent": {"instructions": "Changed task-specific hint"}})


def test_private_paths_reject_other_worktrees_and_symlinks(tmp_path):
    other = tmp_path / "other-checkout"
    other.mkdir()
    (other / ".git").write_text("gitdir: elsewhere")
    link = tmp_path / "shortcut"
    link.symlink_to(other)
    for path in [other / "captures", link / "captures"]:
        with pytest.raises(ValueError, match="every Git"):
            controls.private_path(path)
    assert controls.private_path(tmp_path / "private") == tmp_path / "private"


def make_cell(path):
    cases = [{"case": "development", "question": "Frozen question"}]
    snapshot = {"agent": {"id": "evaluation"}, "libraries": {"library": {"generation": 1}}}
    path.mkdir()
    save(path / "cases.json", cases)
    save(path / "source-snapshot.json", snapshot)
    save(path / "snapshot-check.json", {"before": snapshot, "after": snapshot, "unchanged": True})
    save(path / "results.json", [{"case": "development", "turn": 1, "question": "Frozen question"}])
    save(path / "run-status.json", {"status": "complete", "completedTurns": 1, "budget": 200000})
    return cases, snapshot


@pytest.mark.parametrize("damage", ["missing", "snapshot", "before", "question", "duplicate", "cases", "budget"])
def test_boolean_status_cannot_hide_artifact_drift(tmp_path, damage):
    directory = tmp_path / "cell"
    cases, expected = make_cell(directory)
    assert controls.verify_cell(directory, cases, expected)["sourceArtifactsVerified"]
    if damage == "missing":
        (directory / "snapshot-check.json").unlink()
    elif damage in {"snapshot", "before"}:
        value = controls.read(directory / "snapshot-check.json")
        key = "after" if damage == "snapshot" else "before"
        value[key] = copy.deepcopy(expected)
        value[key]["libraries"]["library"]["generation"] = 2
        save(directory / "snapshot-check.json", value)
    elif damage in {"question", "duplicate"}:
        rows = controls.read(directory / "results.json")
        if damage == "question":
            rows[0]["question"] = "Changed question"
        else:
            rows.append(rows[0])
        save(directory / "results.json", rows)
    elif damage == "cases":
        save(directory / "cases.json", [])
    else:
        save(directory / "run-status.json", {"status": "complete", "completedTurns": 1, "budget": 24000})
    with pytest.raises((ValueError, FileNotFoundError)):
        controls.verify_cell(directory, cases, expected)
