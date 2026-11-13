"""A reference repair must never repair an ANN result or replay an observation."""

import asyncio
import copy

import pytest

from scripts import scale_ablation_large as original
from scripts import scale_ablation_large_resume as repair
from scripts.scale_ablation import read


def observation(kind="exact", count=20):
    return {
        "arm": "unsplit",
        "library": "large-p025000",
        "shape": "routed",
        "kind": kind,
        "query": 0,
        "run": 0,
        "result_ids": [f"large-p025000-d0000:1:{n}" for n in range(count)],
        "result_bindings": [],
        "request_charge": 1687.8,
        "retrieved_document_count": 25_000,
        "milliseconds": 5215.1,
        "error": None,
    }


def runner(tmp_path):
    value = object.__new__(repair.Runner)
    value.state = tmp_path
    value.oracle = {}
    return value


def test_ann_cardinality_is_never_corrected():
    with pytest.raises(original.RunStopped, match="cardinality"):
        repair.validate_observation(observation("vector"))
    repair.validate_observation(observation("exact"), exact=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("error", "HTTP 429"),
        ("retrieved_document_count", None),
        ("request_charge", float("nan")),
        ("milliseconds", -1),
        ("result_ids", ["foreign-d0000:1:0"] * 10),
        ("result_ids", [f"foreign-d0000:1:{n}" for n in range(10)]),
    ],
)
def test_invalid_cached_observations_still_fail(field, value):
    row = observation("vector", 10)
    row[field] = value
    with pytest.raises(original.RunStopped):
        repair.validate_observation(row)


def test_reference_derivation_retains_raw_candidate_order_and_is_repeatable(tmp_path, monkeypatch):
    row = observation()
    untouched = copy.deepcopy(row)
    chosen = row["result_ids"][:8] + row["result_ids"][-2:]

    def derive(value, _oracle):
        return copy.deepcopy(value) | {"result_ids": chosen}

    monkeypatch.setattr(repair, "derive_reference", derive)
    value = runner(tmp_path)
    first = value.checked("routed", row)
    second = value.checked("routed", row)
    assert first == second
    assert row == untouched
    assert first["result_ids"] != row["result_ids"][:10]
    saved = read(tmp_path / "exact-reference-derivations.json")["rows"]
    assert len(saved) == 1
    assert saved[0]["rawRowSha256"] == repair.digest(untouched)
    assert saved[0]["derived"]["result_ids"] == chosen


def test_prior_derived_reference_cannot_silently_change(tmp_path, monkeypatch):
    row = observation()
    chosen = row["result_ids"][:10]
    monkeypatch.setattr(repair, "derive_reference", lambda value, _: copy.deepcopy(value) | {"result_ids": chosen})
    value = runner(tmp_path)
    value.checked("routed", row)
    chosen = row["result_ids"][-10:]
    with pytest.raises(original.RunStopped, match="previously derived"):
        value.checked("routed", row)


@pytest.mark.parametrize("cached", [False, True])
def test_exact_pagination_failure_can_use_verified_reference_without_new_query(tmp_path, monkeypatch, cached):
    row = observation()
    value = runner(tmp_path)
    calls = []

    async def perform(*args):
        calls.append(args)
        if cached:
            return row
        raise original.RunStopped(repair.CARDINALITY_STOP)

    monkeypatch.setattr(original.Runner, "perform", perform)
    monkeypatch.setattr(value, "find", lambda *args: row)
    monkeypatch.setattr(
        repair,
        "derive_reference",
        lambda raw, _: copy.deepcopy(raw) | {"result_ids": raw["result_ids"][5:15]},
    )
    result = asyncio.run(value.perform("routed", "unsplit", row["library"], "routed", "exact", 0, 0))
    assert len(result["result_ids"]) == 10
    assert len(row["result_ids"]) == 20
    assert len(calls) == 1


def test_noncardinality_failure_cannot_be_swallowed(tmp_path, monkeypatch):
    value = runner(tmp_path)

    async def perform(*_args):
        raise original.RunStopped("query error retained; user decision required")

    monkeypatch.setattr(original.Runner, "perform", perform)
    with pytest.raises(original.RunStopped, match="query error"):
        asyncio.run(value.perform("routed", "unsplit", "large-p025000", "routed", "exact", 0, 0))


def test_failed_offline_verification_never_produces_a_reference(tmp_path, monkeypatch):
    value = runner(tmp_path)

    def fail(*_args):
        raise ValueError("a true neighbor is missing")

    monkeypatch.setattr(repair, "derive_reference", fail)
    with pytest.raises(original.RunStopped, match="exhaustive reference validation failed"):
        value.checked("routed", observation())
    assert not (tmp_path / "exact-reference-derivations.json").exists()


@pytest.mark.parametrize("field", ["request_charge", "milliseconds", "query", "arm", "recordedAt"])
def test_derivation_cannot_change_metrics_or_context(tmp_path, monkeypatch, field):
    value = runner(tmp_path)

    def changed(raw, _oracle):
        return copy.deepcopy(raw) | {"result_ids": raw["result_ids"][:10], field: "changed"}

    monkeypatch.setattr(repair, "derive_reference", changed)
    with pytest.raises(original.RunStopped, match="raw metric or measurement context"):
        value.checked("routed", observation())
    assert not (tmp_path / "exact-reference-derivations.json").exists()


def test_scoped_bindings_cannot_lie_about_library():
    row = observation("vector", 10)
    row["result_bindings"] = [{"id": identity, "libraryId": "other"} for identity in row["result_ids"]]
    with pytest.raises(original.RunStopped, match="bindings disagree"):
        repair.validate_observation(row)
