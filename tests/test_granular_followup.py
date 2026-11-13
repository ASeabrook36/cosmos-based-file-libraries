"""Offline gates for the fresh two-turn coordinator; no cloud or model calls."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from fli.store import MemoryLibraryStore, public
from scripts import granular_followup as followup
from scripts import granular_live_safety as safety
from tests.test_granular_live_safety import library_row


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def hashes(root):
    return {str(path.relative_to(root)): followup.sha(path) for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("scope", ["libraries", "documents", "generations", "chunks"])
def test_narrow_caps_reject_before_event_or_state_persistence(tmp_path, scope):
    original_limits = copy.deepcopy(safety.LIMITS)
    with followup.FollowupLedger.create(
        tmp_path / "ownership", binding={"test": True}, protected_library_ids=["protected"], live=False
    ) as ledger:
        cap = followup.SCOPE_LIMITS[scope]

        def populate(state):
            state[scope] = (
                [[str(i), 1] for i in range(cap)] if scope == "generations" else {str(i): {} for i in range(cap)}
            )

        ledger._change("fill-test-scope", {}, populate)
        before, files = ledger.snapshot(), hashes(ledger.root)

        def exceed(state):
            if scope == "generations":
                state[scope].append(["extra", 1])
            else:
                state[scope]["extra"] = {}

        with pytest.raises(ValueError, match="follow-up " + scope + " ceiling"):
            ledger._change("must-never-persist", {}, exceed)
        assert ledger.snapshot() == before
        assert hashes(ledger.root) == files
        assert safety.LIMITS == original_limits


async def test_second_library_is_refused_before_store_create(tmp_path):
    raw = MemoryLibraryStore()
    with followup.FollowupLedger.create(
        tmp_path / "ownership", binding={"test": True}, protected_library_ids=["protected"], live=False
    ) as ledger:
        store = safety.GuardedLibraries(raw, ledger)
        first, second = library_row(), library_row()
        await store.create(first)
        before = hashes(ledger.root)
        with pytest.raises(ValueError, match="follow-up libraries ceiling"):
            await store.create(second)
        assert await raw.get(second["id"]) is None
        assert public(await raw.get(first["id"])) == first
        assert hashes(ledger.root) == before


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    repo, prior, root = tmp_path / "repo", tmp_path / "prior", tmp_path / "fresh"
    completed = prior / "continuation-2"
    prior.mkdir()
    canary = tmp_path / "canary.json"
    write(canary, {"passed": True})
    write(prior / "account.json", {"id": "private-account"})
    env = {"FLI_METRICS_FILE": str(prior / "metrics.jsonl"), "FLI_SEARCH_MODE": "vector"}
    write(prior / "environment.json", env)
    write(
        prior / "preflight.json",
        {"canarySha256": followup.sha(canary), "environmentSha256": followup.sha(prior / "environment.json")},
    )
    source = prior / "fixtures/source.docx"
    source.parent.mkdir()
    source.write_bytes(b"frozen original DOCX bytes, not regenerated")
    write(
        prior / "fixtures/plan.json",
        {"sources": [{"role": "source.docx", "sha256": followup.sha(source), "bytes": source.stat().st_size}]},
    )
    records = {
        "models/controls.json": {
            "old": "six cases",
            "sourceBindings": {"docx": {"sourceSha256": followup.sha(source)}},
        },
        "execution-result.json": {"cleanup": {"passed": True}, "models": {"completedTurns": 6}},
        "cleanup-immediate.json": {"absent": True},
        "cleanup-late.json": {"absent": True},
        "answering-binding-before.json": {"model": "pinned-answer", "sku": {"capacity": 50}},
        "embedding-binding-before.json": {"model": "pinned-embedding", "sku": {"capacity": 100}},
    }
    for name, value in records.items():
        write(completed / name, value)
    prior_manifest = canary.with_name("prior-manifest.json")
    write(prior_manifest, {"files": hashes(prior)})
    write(
        repo / "records/granular-navigation-live-qualification-20261104.json",
        {
            "privateEvidenceSha256": {name: followup.sha(completed / name) for name in records},
            "privateArchives": {"reviewedFinal": {"manifestSha256": followup.sha(prior_manifest)}},
        },
    )
    monkeypatch.setattr(followup, "REPO", repo)
    monkeypatch.setattr(followup, "runtime_baseline", lambda: None)
    before = hashes(prior)
    result = followup.prepare(root, prior, canary, prior_manifest=prior_manifest)
    return root, prior, canary, before, result


def test_prepare_preserves_old_files_and_original_bytes_and_retargets_only_metrics(prepared):
    root, prior, _, before, result = prepared
    assert hashes(prior) == before
    assert (root / "fixtures/source.docx").read_bytes() == (prior / "fixtures/source.docx").read_bytes()
    original = json.loads((prior / "environment.json").read_text())
    actual = json.loads((root / "environment.json").read_text())
    assert actual == {**original, "FLI_METRICS_FILE": str(root / "metrics.jsonl")}
    assert result["cloudCalls"] == 0 and result["limits"] == followup.LIMITS
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.rglob("*") if path.is_file())
    followup.verify_preparation(root)


@pytest.mark.parametrize(
    "path", ["fixtures/source.docx", "prior-model-controls.json", "canary.json", "prior-archive-manifest.json"]
)
def test_frozen_followup_copy_tampering_is_refused(prepared, path):
    root, prior, _, before, _ = prepared
    (root / path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="frozen follow-up input changed"):
        followup.verify_preparation(root)
    assert hashes(prior) == before


def test_prepare_is_single_use_and_refuses_changed_prior_receipt(prepared, tmp_path):
    root, prior, canary, _, _ = prepared
    with pytest.raises(ValueError, match="fresh follow-up state"):
        followup.prepare(root, prior, canary, prior_manifest=canary.with_name("prior-manifest.json"))
    write(prior / "continuation-2/models/controls.json", {"changed": True})
    second = tmp_path / "second"
    with pytest.raises(ValueError, match="archived prior evidence changed"):
        followup.prepare(second, prior, canary, prior_manifest=canary.with_name("prior-manifest.json"))
    assert not second.exists()


@pytest.mark.parametrize("target", ["manifest", "answering-binding-before.json", "embedding-binding-before.json"])
def test_archived_prior_model_bindings_cannot_be_replaced(prepared, tmp_path, target):
    _, prior, canary, _, _ = prepared
    manifest = canary.with_name("prior-manifest.json")
    path = manifest if target == "manifest" else prior / "continuation-2" / target
    write(path, {"different": True})
    second = tmp_path / "second"
    with pytest.raises(ValueError, match="prior private archive manifest differs|archived prior evidence changed"):
        followup.prepare(second, prior, canary, prior_manifest=manifest)
    assert not second.exists()


def test_fixture_must_match_previous_model_source_even_with_consistent_archive(prepared, tmp_path):
    _, prior, canary, _, _ = prepared
    controls = prior / "continuation-2/models/controls.json"
    data = json.loads(controls.read_text())
    data["sourceBindings"]["docx"]["sourceSha256"] = "0" * 64
    write(controls, data)
    manifest = canary.with_name("prior-manifest.json")
    write(manifest, {"files": hashes(prior)})
    public_path = followup.REPO / "records/granular-navigation-live-qualification-20261104.json"
    public = json.loads(public_path.read_text())
    public["privateArchives"]["reviewedFinal"]["manifestSha256"] = followup.sha(manifest)
    public["privateEvidenceSha256"]["models/controls.json"] = followup.sha(controls)
    write(public_path, public)
    with pytest.raises(ValueError, match="fixture differs from prior model source"):
        followup.prepare(tmp_path / "second", prior, canary, prior_manifest=manifest)
    assert not (tmp_path / "second").exists()


@pytest.mark.parametrize("role", ["answering", "embedding"])
@pytest.mark.parametrize("change", ["model", "capacity"])
def test_prior_bindings_require_exact_model_and_capacity(prepared, role, change):
    root, *_ = prepared
    bindings = {
        name: json.loads((root / f"prior-{name}-binding.json").read_text()) for name in ("answering", "embedding")
    }
    followup.require_prior_bindings(root, **bindings)
    if change == "model":
        bindings[role]["model"] = "different-version"
    else:
        bindings[role]["sku"]["capacity"] += 1
    with pytest.raises(ValueError, match="prior " + role + " binding differs"):
        followup.require_prior_bindings(root, **bindings)


def execution_ready(root, monkeypatch):
    write(root / "protected-before.json", {"protected": {"unchanged": True}})
    write(
        root / "preflight.json",
        {
            "environmentSha256": followup.sha(root / "environment.json"),
            "protectedSha256": followup.sha(root / "protected-before.json"),
        },
    )
    write(
        root / "matched-preflight.json",
        {
            "passed": True,
            "capturedAt": datetime.now(UTC).isoformat(),
            "preflightSha256": followup.sha(root / "preflight.json"),
            **{
                name: json.loads((root / f"prior-{name}-binding.json").read_text())
                for name in ("answering", "embedding")
            },
        },
    )
    monkeypatch.setattr(
        followup.subprocess, "check_output", lambda args, **kwargs: "" if "status" in args else "f" * 40
    )
    monkeypatch.setattr(followup.live, "code_hashes", lambda: {"src/fli/tool.py": "same-source"})


@pytest.mark.parametrize(
    "damage", ["stale", "future", "environment", "protected", "preflight", "already-started", "dirty"]
)
def test_execution_gate_refuses_changed_or_stale_controls_before_any_ledger(prepared, monkeypatch, damage):
    root, *_ = prepared
    execution_ready(root, monkeypatch)
    assert followup.execution_controls(root)["runtimeUnchangedFromCandidate"] is True
    if damage in {"stale", "future"}:
        path = root / "matched-preflight.json"
        data = json.loads(path.read_text())
        data["capturedAt"] = (datetime.now(UTC) + timedelta(seconds=-901 if damage == "stale" else 60)).isoformat()
        write(path, data)
    elif damage in {"environment", "protected", "preflight"}:
        name = {"environment": "environment.json", "protected": "protected-before.json", "preflight": "preflight.json"}[
            damage
        ]
        data = json.loads((root / name).read_text())
        data["changed"] = True
        write(root / name, data)
    elif damage == "already-started":
        write(root / "execution-started.json", {})
    else:
        monkeypatch.setattr(followup.subprocess, "check_output", lambda *args, **kwargs: " M changed.py")
    with pytest.raises(ValueError):
        followup.execution_controls(root)
    assert not (root / "ownership").exists()


async def test_model_binding_mismatch_stops_preflight_before_execution_receipt(prepared, monkeypatch):
    root, *_ = prepared

    async def fake_preflight(*args):
        return {"passed": True}

    monkeypatch.setattr(followup, "preflight", fake_preflight)
    monkeypatch.setattr(followup.Settings, "from_env", lambda: object())
    monkeypatch.setattr(followup.live, "current_model", lambda *args, **kwargs: {"model": "wrong-deployment"})
    with pytest.raises(ValueError, match="prior answering binding differs"):
        await followup.check_health(root)
    assert not (root / "matched-preflight.json").exists()
    assert not (root / "ownership").exists()


@pytest.mark.parametrize("change", ["bytes", "delete", "add"])
def test_runtime_gate_rejects_candidate_source_change(tmp_path, monkeypatch, change):
    repo = tmp_path / "repo"
    source = repo / "src/fli/tool.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"candidate guidance")
    (repo / "requirements-local.lock").write_bytes(b"locked")
    monkeypatch.setattr(followup, "REPO", repo)

    def frozen(args, **kwargs):
        if "ls-tree" in args:
            return "src/fli/tool.py\nrequirements-local.lock\n"
        return b"candidate guidance" if args[-1].endswith("src/fli/tool.py") else b"locked"

    monkeypatch.setattr(followup.subprocess, "check_output", frozen)
    followup.runtime_baseline()
    if change == "bytes":
        source.write_bytes(b"changed runtime")
    elif change == "delete":
        source.unlink()
    else:
        (source.parent / "new.py").write_bytes(b"new runtime")
    with pytest.raises(ValueError, match="cannot change candidate runtime|candidate runtime inventory changed"):
        followup.runtime_baseline()
