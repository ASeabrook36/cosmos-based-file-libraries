"""The optional late suite remains local until explicit execution is requested."""

import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from scripts import scale_ablation_large_late_suite as suite
from scripts.scale_ablation import save


def test_plan_has_exactly_one_fixed_epoch_and_balanced_arm_pairs():
    rows = suite.query_plan(list(suite.original.NEW_LIBRARIES))
    assert len(rows) == len(set(rows)) == 726
    assert Counter(v[2] for v in rows) == {"routed": 720, "global": 6}
    assert all(row[2] == "routed" for row in rows[:720])
    assert all(row[1:4] == ("global", "global", 0) for row in rows[720:])
    assert Counter(row[1] for row in rows[:720]) == dict.fromkeys(suite.original.SIZES, 120)
    for first, second in zip(rows[::2], rows[1::2], strict=True):
        assert first[1:] == second[1:] and {first[0], second[0]} == {"split", "unsplit"}
    assert tuple(v[0] for v in rows[:2]) != tuple(v[0] for v in rows[2:4])


def test_supplementary_ledger_keeps_parent_seed_and_reserves_before_queries(tmp_path):
    started = datetime.now(UTC).isoformat()
    ledger = suite.Ledger(tmp_path, 35_008_768.41, started)
    ledger.add("inventory", 5.8)
    assert ledger.data["knownRu"] == pytest.approx(35_008_774.21)
    assert ledger.data["additionalKnownRu"] == pytest.approx(5.8)
    ledger.check(additional_ru=suite.RESERVE_RU)
    ledger.add("routed", 700_000)
    with pytest.raises(suite.original.RunStopped, match="visible-RU"):
        ledger.check(additional_ru=suite.RESERVE_RU)
    assert (tmp_path / "budget.json").exists()


def test_time_and_extension_reserve_cannot_be_bypassed_by_late_flag(tmp_path):
    old = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    with pytest.raises(suite.original.RunStopped, match="thirty-minute"):
        suite.Ledger(tmp_path, 35_008_768.41, old).check(late=True)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(suite.original.RunStopped, match="reserve"):
        suite.Ledger(other, 63_900_000, datetime.now(UTC).isoformat()).check(additional_ru=300_000)


def prepared_runner(tmp_path, monkeypatch):
    parent, state = tmp_path / "parent", tmp_path / "supplement"
    parent.mkdir()
    manifest = parent / "manifest.json"
    save(manifest, {"preparationStartedAt": "2026-11-13T00:00:00Z"})
    config = {"prior": {}, "inputManifest": str(manifest), "selected": list(suite.original.NEW_LIBRARIES)}
    save(parent / "status.json", {"complete": True, "phase": "complete", "selectedLibraries": config["selected"]})
    save(parent / "budget.json", {"knownRu": 35_008_768.41})
    save(parent / "question-vectors.json", {lib: [[1]] * 20 for lib in suite.original.SIZES})
    save(parent / "offline-exact.json", {"private": "frozen references"})

    def snapshot(path):
        return {str(p.relative_to(path)): p.read_bytes().hex() for p in path.rglob("*") if p.is_file()}

    before = snapshot(parent)
    binding = {"parentFiles": before, "parentKnownRu": 35_008_768.41, "notBefore": "2026-11-13T00:00:00Z"}
    monkeypatch.setattr(suite, "plan", lambda *_: (config, binding))
    monkeypatch.setattr(suite, "parent_snapshot", snapshot)
    monkeypatch.setattr(suite, "validate_oracle", lambda *args: None)
    runner = suite.Runner(parent, state)
    assert snapshot(parent) == before
    return runner, parent, state, snapshot


def test_isolated_orchestration_runs_inventory_then_726_ann_calls_only(tmp_path, monkeypatch):
    runner, parent, state, snapshot = prepared_runner(tmp_path, monkeypatch)
    before, calls = snapshot(parent), []

    async def connect():
        calls.append("connect")

    async def inventory(label):
        calls.append(("inventory", label))

    async def perform(*args):
        calls.append(args)
        runner.ledger.add(suite.PHASE + "-vector", 1)
        _, arm, lib, shape, kind, q, run = args
        runner.phase_rows(suite.PHASE).append(
            {
                "arm": arm,
                "library": lib,
                "shape": shape,
                "kind": kind,
                "query": q,
                "run": run,
                "result_ids": [f"{lib}-d0000:1:{i}" for i in range(10)],
                "request_charge": 1,
                "retrieved_document_count": 50,
                "milliseconds": 1,
            }
        )

    async def close():
        calls.append("close")

    monkeypatch.setattr(runner, "connect", connect)
    monkeypatch.setattr(runner, "inventory", inventory)
    monkeypatch.setattr(runner, "perform", perform)
    monkeypatch.setattr(runner, "close", close)
    asyncio.run(runner.run())
    assert calls[:2] == ["connect", ("inventory", "supplementary")]
    assert calls[-1] == "close"
    rows = calls[2:-1]
    assert len(rows) == 726 and all(row[4] == "vector" and row[0] == suite.PHASE for row in rows)
    assert Counter(row[3] for row in rows) == {"routed": 720, "global": 6}
    assert snapshot(parent) == before
    assert json.loads((state / "status.json").read_text())["parentArtifactsUnchanged"]
    assert json.loads((state / "budget.json").read_text())["additionalKnownRu"] == 726
    assert 0 < runner.remaining_main_seconds() < 1800


def test_parent_changes_fail_instead_of_becoming_a_new_parent_state(tmp_path, monkeypatch):
    runner, parent, _state, _snapshot = prepared_runner(tmp_path, monkeypatch)

    async def noop(*args):
        return None

    async def inventory(_label):
        (parent / "status.json").write_text("changed by another actor")

    for method in ("connect", "close"):
        monkeypatch.setattr(runner, method, noop)
    monkeypatch.setattr(runner, "perform", noop)
    monkeypatch.setattr(runner, "validate_rows", lambda **kwargs: None)
    monkeypatch.setattr(runner, "inventory", inventory)
    with pytest.raises(suite.original.RunStopped, match="parent artifacts"):
        asyncio.run(runner.run())


def test_default_cli_dry_run_never_constructs_or_connects_runner(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(suite, "plan", lambda *_: ({}, {"parentFiles": {}, "additionalKnownRuCap": 1_000_000}))
    monkeypatch.setattr(suite, "Runner", lambda *_: pytest.fail("dry run attempted execution"))
    monkeypatch.setattr(
        sys, "argv", ["late-suite", "--parent-state", str(tmp_path / "parent"), "--state", str(tmp_path / "new")]
    )
    assert suite.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plannedAnnCalls"] == 726 and result["networkCalls"] == 0
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan")])
def test_stored_ledger_rejects_invalid_balanced_totals(tmp_path, bad):
    (tmp_path / "budget.json").write_text(
        json.dumps({"parentKnownRu": 1, "knownRu": 1 + bad, "additionalKnownRu": bad, "categories": {"inventory": bad}})
    )
    with pytest.raises(suite.original.RunStopped, match="invalid charges"):
        suite.Ledger(tmp_path, 1, datetime.now(UTC).isoformat())


def test_interrupted_binding_keeps_persisted_start_and_cannot_reset_epoch(tmp_path, monkeypatch):
    runner, parent, state, _ = prepared_runner(tmp_path, monkeypatch)
    started = json.loads((state / "status.json").read_text())["startedAt"]
    runner_again = suite.Runner(parent, state)
    assert runner_again.started_at == started
    (state / "budget.json").unlink()
    with pytest.raises(suite.original.RunStopped, match="checkpoints are incomplete"):
        suite.Runner(parent, state)


def test_persisted_timer_precedes_expensive_parent_binding(tmp_path, monkeypatch):
    parent, state = tmp_path / "parent", tmp_path / "supplement"
    parent.mkdir()

    def interrupted_plan(*_):
        assert json.loads((state / "status.json").read_text())["startedAt"]
        raise ValueError("interrupted during binding")

    monkeypatch.setattr(suite, "plan", interrupted_plan)
    with pytest.raises(ValueError, match="interrupted"):
        suite.Runner(parent, state)
    with pytest.raises(suite.original.RunStopped, match="checkpoints are incomplete"):
        suite.Runner(parent, state)


def test_ungraceful_request_interruption_cannot_replay_automatically(tmp_path, monkeypatch):
    runner, parent, _, _ = prepared_runner(tmp_path, monkeypatch)
    runner.checkpoint(suite.PHASE, inFlight=list(suite.query_plan(runner.selected)[0]))
    with pytest.raises(suite.original.RunStopped, match="interrupted supplementary request"):
        suite.Runner(parent, runner.state)


def test_interrupted_inventory_keeps_durable_marker_and_blocks_replay(tmp_path, monkeypatch):
    runner, parent, state, _ = prepared_runner(tmp_path, monkeypatch)

    async def noop():
        return None

    async def interrupted_inventory(_):
        assert json.loads((state / "status.json").read_text())["inFlight"] == ["inventory", "supplementary"]
        raise asyncio.CancelledError()

    monkeypatch.setattr(runner, "connect", noop)
    monkeypatch.setattr(runner, "close", noop)
    monkeypatch.setattr(runner, "inventory", interrupted_inventory)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.run())
    assert json.loads((state / "status.json").read_text())["inFlight"] == ["inventory", "supplementary"]
    with pytest.raises(suite.original.RunStopped, match="interrupted supplementary request"):
        suite.Runner(parent, state)


def test_cached_coverage_and_charge_failures_stop_before_connect(tmp_path, monkeypatch):
    runner, _, _, _ = prepared_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "connect", lambda: pytest.fail("bad cached state reached network"))
    runner.ledger.add(suite.PHASE + "-vector", 10)
    with pytest.raises(suite.original.RunStopped, match="do not reconcile"):
        asyncio.run(runner.run())
    runner.ledger.data["categories"][suite.PHASE + "-vector"] = 0
    runner.phase_rows(suite.PHASE).append({"arm": "unknown"})
    with pytest.raises(suite.original.RunStopped, match="extras or duplicates"):
        asyncio.run(runner.run())


def test_inventory_cancellation_retains_partial_visible_charge(tmp_path, monkeypatch):
    runner, _, state, _ = prepared_runner(tmp_path, monkeypatch)

    class Container:
        async def read(self):
            return {}

        async def read_feed_ranges(self, **_):
            yield "one-range"

        async def query_items(self, sql, *, raw_response_hook, **_):
            raw_response_hook.request_charge += 17.25
            raise asyncio.CancelledError()
            yield 0

    monkeypatch.setattr(suite.original, "definition_mismatches", lambda *_: [])
    runner.clients = {arm: (None, None, Container()) for arm in suite.original.ARMS}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.inventory("supplementary"))
    assert runner.ledger.data["categories"]["inventory"] == 17.25
    assert json.loads((state / "inventory-supplementary.json").read_text())["partial"] is True
