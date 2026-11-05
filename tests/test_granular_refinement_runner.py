"""Fresh failed-prior continuation gates; no cloud calls or live provider requests."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from scripts import granular_followup as runner
from scripts import granular_pacing as pacing
from scripts.evaluate_agent_retrieval import capture
from tests import test_granular_outline_runner as prior_helpers
from tests.test_granular_followup import execution_ready, hashes, write
from tests.test_granular_live_models import binding
from tests.test_granular_navigation import ready
from tests.test_granular_pacing import capacity, policy

REVISION = prior_helpers.REVISION
direct_prior = prior_helpers.direct_prior


@pytest.fixture
def failed_prior(direct_prior, tmp_path):
    prior, canary, manifest = direct_prior
    controls = json.loads((prior / "models/controls.json").read_text())
    controls.update(schema="fli.granular-outline-models.controls.v1", modelTurns=1)
    write(prior / "models/controls.json", controls)
    write(
        prior / "execution-result.json",
        {
            "status": "failed",
            "errorType": "PacingRefusal",
            "cleanup": {"passed": True},
            "finalOwnership": {"unresolvedOperations": 0, "unfinishedMessages": 0},
        },
    )
    write(
        prior / "models/status.json",
        {"status": "failed", "errorType": "PacingRefusal", "completedTurns": 0},
    )
    write(prior / "ownership-final.json", {"retained": True})
    write(
        prior / "post-failure-control-verification.json",
        {"passed": True, "beforeAndAfterExactPriorBindingsMatched": True, "executionCodeHashesUnchanged": True},
    )
    write(manifest, {"files": hashes(prior)})
    public = runner.REPO / "records/granular-outline-qualification-20261104.json"
    write(
        public,
        {
            "privateArchives": {"reviewedFinal": {"manifestSha256": runner.sha(manifest)}},
            "privateEvidenceSha256": hashes(prior),
        },
    )
    receipt = tmp_path / "capacity-policy.json"
    write(receipt, policy())
    return prior, canary, manifest, receipt


def prepare(root, prior, **overrides):
    previous, canary, manifest, receipt = prior
    return runner.prepare(
        root,
        previous,
        canary,
        prior_manifest=manifest,
        protocol="outline-v2",
        runtime_revision=REVISION,
        capacity_receipt=overrides.pop("capacity_receipt", receipt),
        **overrides,
    )


def reseal_prior(prior):
    previous, _, manifest, _ = prior
    write(manifest, {"files": hashes(previous)})
    public = runner.REPO / "records/granular-outline-qualification-20261104.json"
    write(
        public,
        {
            "privateArchives": {"reviewedFinal": {"manifestSha256": runner.sha(manifest)}},
            "privateEvidenceSha256": hashes(previous),
        },
    )


def test_failed_prior_is_a_fresh_single_turn_with_source_and_policy_sealed(tmp_path, failed_prior, monkeypatch):
    old = hashes(failed_prior[0])
    root = tmp_path / "refinement"
    result = prepare(root, failed_prior)
    assert result["limits"] == runner.protocol_limits("outline-v1")
    assert result["limits"]["modelTurns"] == 1 and result["cloudCalls"] == 0
    frozen = runner.verify_preparation(root, protocol="outline-v2")
    assert frozen["baseline"] == "9fcc9bc" and frozen["runtimeRevision"] == REVISION
    assert (root / "capacity-policy.json").read_bytes() == failed_prior[3].read_bytes()
    assert (root / "fixtures/source.docx").read_bytes() == (failed_prior[0] / "fixtures/source.docx").read_bytes()
    assert hashes(failed_prior[0]) == old
    assert not (root / "models/provider/ledger.json").exists()
    execution_ready(root, monkeypatch)
    write(root / "answering-preflight.json", capacity())
    control = runner.execution_controls(root, protocol="outline-v2")
    assert control["protocol"] == "outline-v2" and not control["runtimeUnchangedFromCandidate"]
    write(root / "execution-started.json", {"attempt": 1})
    with pytest.raises(ValueError, match="single-use"):
        runner.execution_controls(root, protocol="outline-v2")
    with pytest.raises(ValueError, match="prepared protocol differs"):
        runner.verify_preparation(root, protocol="outline-v1")


@pytest.mark.parametrize(
    "name",
    [
        "execution-result.json",
        "models/status.json",
        "ownership-final.json",
        "post-failure-control-verification.json",
        "fixtures/source.docx",
    ],
)
def test_failed_prior_rejects_unanchored_evidence_before_state_creation(tmp_path, failed_prior, name):
    path = failed_prior[0] / name
    path.write_bytes(path.read_bytes() + b"\n")
    root = tmp_path / "refinement"
    with pytest.raises(ValueError, match="changed"):
        prepare(root, failed_prior)
    assert not root.exists()


@pytest.mark.parametrize(
    "damage",
    ["success", "other-error", "incomplete-cleanup", "completed-turn", "pending-write", "pending-message", "postcheck"],
)
def test_failed_prior_must_be_exact_expected_failure_and_safely_finalized(tmp_path, failed_prior, damage):
    previous = failed_prior[0]
    result = json.loads((previous / "execution-result.json").read_text())
    status = json.loads((previous / "models/status.json").read_text())
    if damage == "success":
        result["status"] = "complete"
    elif damage == "other-error":
        result["errorType"] = "TimeoutError"
    elif damage == "incomplete-cleanup":
        result["cleanup"]["passed"] = False
    elif damage == "completed-turn":
        status["completedTurns"] = 1
    elif damage == "pending-write":
        result["finalOwnership"]["unresolvedOperations"] = 1
    elif damage == "pending-message":
        result["finalOwnership"]["unfinishedMessages"] = 1
    else:
        write(previous / "post-failure-control-verification.json", {"passed": False})
    write(previous / "execution-result.json", result)
    write(previous / "models/status.json", status)
    reseal_prior(failed_prior)
    with pytest.raises(ValueError, match="prior"):
        prepare(tmp_path / "refinement", failed_prior)


@pytest.mark.parametrize("damage", ["absent", "raised-ceiling", "wrong-binding", "changed-after-prepare"])
def test_capacity_is_required_bound_and_immutable(tmp_path, failed_prior, damage):
    root = tmp_path / "refinement"
    if damage == "absent":
        with pytest.raises(ValueError, match="fresh capacity policy required"):
            prepare(root, failed_prior, capacity_receipt=None)
        return
    if damage == "changed-after-prepare":
        prepare(root, failed_prior)
        write(root / "capacity-policy.json", {"new": "policy"})
        with pytest.raises(ValueError, match="frozen follow-up input changed"):
            runner.verify_preparation(root, protocol="outline-v2")
        return
    value = policy()
    if damage == "raised-ceiling":
        value["localInputPlusMaxOutputCeiling"] = 200000
    else:
        value = pacing.policy_from_capacity(capacity(), binding_sha256="b" * 64)
    write(failed_prior[3], value)
    with pytest.raises(ValueError, match="policy"):
        prepare(root, failed_prior)
    assert not root.exists()


def test_refinement_runtime_uses_new_baseline_and_exact_five_file_allowlist(monkeypatch):
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0)

    def output(args, **kwargs):
        commands.append(args)
        return "\n".join(runner.REFINEMENT_RUNTIME_PATHS)

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner.subprocess, "check_output", output)
    checked = []
    monkeypatch.setattr(runner, "runtime_baseline", lambda revision: checked.append(revision))
    runner.verify_runtime("outline-v2", REVISION)
    assert checked == [REVISION] and all("9fcc9bc" in args for args in commands)
    assert runner.REFINEMENT_RUNTIME_PATHS - runner.OUTLINE_RUNTIME_PATHS == {"src/fli/granular_navigation.py"}
    with pytest.raises(ValueError, match="runtime scope"):
        runner.verify_runtime("outline-v1", REVISION)


def test_existing_protocols_retain_defaults_and_reject_pacing_override(tmp_path, direct_prior):
    original = copy.deepcopy(runner.LIMITS)
    assert runner.protocol_limits() == original
    assert runner.protocol_limits("outline-v1") == {**original, "modelTurns": 1}
    previous, canary, manifest = direct_prior
    with pytest.raises(ValueError, match="only supported by outline-v2"):
        runner.prepare(
            tmp_path / "not-created",
            previous,
            canary,
            prior_manifest=manifest,
            protocol="outline-v1",
            runtime_revision=REVISION,
            capacity_receipt=tmp_path / "anything",
        )


async def test_fresh_capacity_mismatch_stops_preflight_before_ownership(tmp_path, failed_prior, monkeypatch):
    root = tmp_path / "refinement"
    prepare(root, failed_prior)

    async def preflight(*args):
        return {"passed": True}

    def current_model(root, settings, role, *, embedding=False):
        if not embedding:
            changed = capacity()
            changed["properties"]["rateLimits"][0]["count"] += 1000
            write(root / f"answering-{role}.json", changed)
        return json.loads((root / f"prior-{'embedding' if embedding else 'answering'}-binding.json").read_text())

    monkeypatch.setattr(runner, "preflight", preflight)
    monkeypatch.setattr(runner.Settings, "from_env", lambda: object())
    monkeypatch.setattr(runner.live, "current_model", current_model)
    with pytest.raises(ValueError, match="fresh deployment"):
        await runner.check_health(root, protocol="outline-v2")
    assert not (root / "matched-preflight.json").exists() and not (root / "ownership").exists()


@pytest.mark.parametrize("failure", [None, "answering", "embedding", "capacity", "code", "source", "no-publication"])
async def test_final_checks_use_actual_api_independent_failures_and_persist_receipt(tmp_path, monkeypatch, failure):
    env = await ready()
    root = tmp_path / "state"
    root.mkdir()
    source = {
        "docx": {
            "libraryId": env.lib,
            "documentId": env.doc["id"],
            "generation": env.doc["activeGeneration"],
            "contentRevision": env.doc["contentRevision"],
            "fileName": env.doc["fileName"],
            "sourceSha256": env.doc["sha256"],
        }
    }
    before = {
        "docx": {
            "library": capture(env.index.get_library(env.lib)),
            "document": capture(env.index.list_documents(env.lib)[0]),
        }
    }
    write(root / "models/sources-before.json", before)
    write(root / "capacity-policy.json", policy())
    write(root / "prior-answering-binding.json", binding())
    write(root / "prior-embedding-binding.json", {"same": "embedding"})
    called = []

    def current_model(root, settings, role, *, embedding=False):
        label = "embedding" if embedding else "answering"
        called.append(label)
        if failure == label:
            raise TimeoutError(label + " observation failed")
        if not embedding:
            observed = capacity()
            if failure == "capacity":
                observed["properties"]["rateLimits"][0]["count"] += 1000
            write(root / f"answering-{role}.json", observed)
        return json.loads((root / f"prior-{label}-binding.json").read_text())

    monkeypatch.setattr(runner.live, "current_model", current_model)
    monkeypatch.setattr(runner.live, "code_hashes", lambda: {"same": failure != "code"})
    if failure == "source":
        source["docx"]["contentRevision"] += 1
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.client.app),
        base_url="http://local.invalid",
        headers={"Authorization": "Bearer k", "X-Agent-Id": "owner"},
    ) as client:
        result = await runner.postchecks(
            root, object(), client, None if failure == "no-publication" else source, {"sourceSha256": {"same": True}}
        )
    assert result["passed"] is (failure is None)
    assert sorted(called) == ["answering", "embedding"]
    assert result["modelCalls"] == result["dataMutations"] == 0
    assert json.loads((root / "postchecks.json").read_text()) == result
    assert result["checks"]["code"]["status"] == ("failed" if failure == "code" else "passed")
    if failure == "no-publication":
        assert result["checks"]["source"]["status"] == "unavailable"
    elif failure != "source":
        assert result["checks"]["source"]["status"] == "passed"


async def test_final_binding_observations_are_parallel_and_joined(tmp_path, monkeypatch):
    import threading

    root = tmp_path / "state"
    root.mkdir()
    write(root / "capacity-policy.json", policy())
    write(root / "prior-answering-binding.json", binding())
    write(root / "prior-embedding-binding.json", binding())
    barrier = threading.Barrier(2, timeout=2)
    completed = []

    def current_model(root, settings, role, *, embedding=False):
        barrier.wait()
        completed.append(embedding)
        if not embedding:
            write(root / f"answering-{role}.json", capacity())
        return binding()

    monkeypatch.setattr(runner.live, "current_model", current_model)
    monkeypatch.setattr(runner.live, "code_hashes", lambda: {})
    result = await asyncio.wait_for(runner.postchecks(root, object(), None, None, {"sourceSha256": {}}), timeout=3)
    assert sorted(completed) == [False, True]
    assert result["checks"]["answering"]["status"] == result["checks"]["embedding"]["status"] == "passed"


@pytest.mark.parametrize("damage", ["postcheck-raises", "postcheck-fails", "capacity-before-model"])
async def test_execute_preserves_primary_failure_runs_postchecks_and_owned_cleanup(tmp_path, monkeypatch, damage):
    from scripts import granular_followup_models as models
    from scripts.granular_live_models import PacingRefusal

    root, repo = tmp_path / "state", tmp_path / "repo"
    root.mkdir()
    for name in ("libraries", "documents", "chunks"):
        write(repo / "cosmos" / (name + ".container.json"), {})
    write(root / "environment.json", {})
    write(root / "protected-before.json", {"protected": {"same": True}})
    write(root / "preflight.json", {"targetFingerprint": "same"})
    write(root / "prior-answering-binding.json", binding())
    write(root / "prior-embedding-binding.json", binding())
    write(root / "capacity-policy.json", policy())
    controls = {
        "candidateCommit": REVISION,
        "limits": runner.protocol_limits("outline-v2"),
        "sourceSha256": {"same": True},
    }
    settings = SimpleNamespace(
        environment="local",
        auth_mode="dev",
        search_mode="vector",
        dev_api_key="not-real",
        cosmos_endpoint="https://unused.invalid",
        cosmos_database="unused",
        aoai_endpoint="https://unused.invalid",
        aoai_chat_deployment="unused",
    )
    events = []

    class Container:
        async def read(self):
            return {"_rid": "same"}

    service = SimpleNamespace(
        database=SimpleNamespace(get_container_client=lambda name: Container()),
        blob_container=object(),
        app=object(),
        embedder=SimpleNamespace(rows=[]),
    )

    @asynccontextmanager
    async def services(*args):
        yield service

    class URL(str):
        async def quiesce(self):
            events.append("quiesced")

    @asynccontextmanager
    async def loopback(app):
        yield URL("http://never-contacted.invalid")

    class Qualification:
        def __init__(self, *args):
            self.checks = []

        async def create(self, kind):
            events.append("publication-start")
            return "owned-library"

        async def upload(self, *args):
            return "owned-document"

        async def drain(self):
            pass

        async def publication(self, *args):
            return {"activeGeneration": 1, "contentRevision": 1, "fileName": "source.docx", "sha256": "f" * 64}

        def passed(self, name):
            self.checks.append(name)

    def current_model(root, settings, role, *, embedding=False):
        events.append(("embedding-" if embedding else "answering-") + role)
        if not embedding:
            observed = capacity()
            if damage == "capacity-before-model" and role == "model-start":
                observed["properties"]["rateLimits"][0]["count"] += 1000
            write(root / f"answering-{role}.json", observed)
        return binding()

    async def protected(*args):
        return {"protected": {"same": True}}

    async def postchecks(*args):
        events.append("postchecks")
        if damage == "postcheck-raises":
            raise RuntimeError("diagnostic failed")
        return {"passed": False, "reason": "independent postcheck failed"}

    async def cleanup(*args):
        events.append("cleanup")
        return {"passed": True}

    def run_models(*args, **kwargs):
        events.append("model")
        raise PacingRefusal("original model pacing error")

    def prepare_cases(*args, **kwargs):
        events.append("prepared-models")
        assert kwargs["protocol"] == "outline-v2"
        assert kwargs["capacity_receipt"] == root / "capacity-policy.json"

    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "execution_controls", lambda *args, **kwargs: controls)
    monkeypatch.setattr(runner.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(runner, "replace", lambda obj, **kwargs: obj)
    monkeypatch.setattr(runner, "definition_mismatches", lambda *args: [])
    monkeypatch.setattr(runner, "target_fingerprint", lambda *args: "same")
    monkeypatch.setattr(runner, "protected_snapshot", protected)
    monkeypatch.setattr(runner.live, "services", services)
    monkeypatch.setattr(runner.live, "loopback", loopback)
    monkeypatch.setattr(runner.live, "current_model", current_model)
    monkeypatch.setattr(runner.live, "Qualification", Qualification)
    monkeypatch.setattr(runner.live, "cleanup", cleanup)
    monkeypatch.setattr(runner.live, "archive", lambda *args: {"files": 1, "manifestSha256": "a" * 64})
    monkeypatch.setattr(
        runner, "actual_client", lambda endpoint: SimpleNamespace(close=lambda: events.append("closed"))
    )
    monkeypatch.setattr(runner, "postchecks", postchecks)
    monkeypatch.setattr(models, "prepare_cases", prepare_cases)
    monkeypatch.setattr(models, "run_models", run_models)
    expected = ValueError if damage == "capacity-before-model" else PacingRefusal
    message = "fresh deployment" if damage == "capacity-before-model" else "original model pacing error"
    with pytest.raises(expected, match=message):
        await runner.execute(root, tmp_path / "archive", protocol="outline-v2")
    result = json.loads((root / "execution-result.json").read_text())
    assert result["errorType"] == expected.__name__
    assert result["cleanup"]["passed"] and result["postchecks"]["passed"] is False
    assert events.index("postchecks") < events.index("quiesced") < events.index("cleanup")
    if damage == "capacity-before-model":
        assert "prepared-models" not in events and "model" not in events
    else:
        assert events.index("model") < events.index("closed") < events.index("postchecks")
    assert (root / "ownership-final.json").exists() and (root / "archive-receipts.json").exists()
