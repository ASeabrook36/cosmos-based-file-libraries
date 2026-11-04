"""One-turn protocol gates and normal-agent execution without Azure/model calls."""

import copy
import json
from types import SimpleNamespace

import pytest

from scripts import granular_followup as runner
from scripts import granular_followup_models as models
from scripts import granular_live_models as baseline
from scripts.hybrid_model_meter import Meter
from scripts.qualify_granular_navigation import fixtures
from tests.test_granular_followup import execution_ready, hashes, write
from tests.test_granular_live_models import Clock, binding, source_bindings
from tests.test_granular_navigation import ready
from tests.test_responses_chat import message, response

REVISION = "a" * 40


def test_protocol_limits_are_allowlisted_copied_and_preserve_old_defaults():
    old = copy.deepcopy(runner.LIMITS)
    assert runner.protocol_limits() == old
    single = runner.protocol_limits("outline-v1")
    assert single == {**old, "modelTurns": 1}
    single["libraries"] = 999
    assert runner.protocol_limits("outline-v1")["libraries"] == 1
    assert runner.LIMITS == old
    old_models = copy.deepcopy(models.METER_LIMITS)
    original, outline = models.protocol_settings(), models.protocol_settings("outline-v1")
    assert original["turns"] == 2 and original["priorTurns"] == 6
    assert outline["turns"] == 1 and outline["priorTurns"] == 2
    assert outline["meter"] == {
        "logicalCalls": 13,
        "physicalResponsesAttempts": 18,
        "inputTokensWithUnknownReservations": 1000000,
        "outputTokensWithUnknownReservations": 100000,
    }
    outline["meter"]["logicalCalls"] = 100
    assert models.protocol_settings("outline-v1")["meter"]["logicalCalls"] == 13
    assert models.METER_LIMITS == old_models
    for getter in (runner.protocol_limits, models.protocol_settings):
        with pytest.raises(ValueError, match="unknown qualification protocol"):
            getter("arbitrary-case-or-budgets")


@pytest.fixture
def direct_prior(tmp_path, monkeypatch):
    repo, prior = tmp_path / "repo", tmp_path / "prior-followup"
    source = prior / "fixtures/source.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original exact archive-bound DOCX bytes")
    canary = tmp_path / "canary.json"
    write(canary, {"passed": True})
    write(prior / "preparation.json", {"sourceSha256": runner.sha(source)})
    write(prior / "environment.json", {"FLI_METRICS_FILE": str(prior / "metrics.jsonl")})
    write(prior / "account.json", {"id": "same-existing-account"})
    write(
        prior / "preflight.json",
        {"canarySha256": runner.sha(canary), "environmentSha256": runner.sha(prior / "environment.json")},
    )
    write(prior / "models/controls.json", {"sourceBindings": {"docx": {"sourceSha256": runner.sha(source)}}})
    write(prior / "answering-binding-before.json", binding())
    write(prior / "embedding-binding-before.json", {"model": {"version": "1"}, "sku": {"capacity": 150}})
    write(prior / "execution-result.json", {"cleanup": {"passed": True}, "models": {"completedTurns": 2}})
    for name in ("cleanup-immediate.json", "cleanup-late.json"):
        write(prior / name, {"absent": True})
    manifest = tmp_path / "reviewed-final-manifest.json"
    write(manifest, {"files": hashes(prior)})
    public = repo / "records/granular-navigation-followup-20261104.json"
    write(
        public,
        {
            "privateArchives": {"reviewedFinal": {"manifestSha256": runner.sha(manifest)}},
            "privateEvidenceSha256": {
                name: runner.sha(prior / name)
                for name in (
                    "models/controls.json",
                    "execution-result.json",
                    "cleanup-immediate.json",
                    "cleanup-late.json",
                )
            },
        },
    )
    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "verify_runtime", lambda protocol, revision=None: None)
    return prior, canary, manifest


def prepare_direct(root, direct_prior):
    prior, canary, manifest = direct_prior
    return runner.prepare(
        root, prior, canary, prior_manifest=manifest, protocol="outline-v1", runtime_revision=REVISION
    )


def test_direct_two_turn_prior_is_anchored_preserved_and_bound_to_reviewed_runtime(tmp_path, direct_prior, monkeypatch):
    prior, _, _ = direct_prior
    previous = hashes(prior)
    root = tmp_path / "outline"
    result = prepare_direct(root, direct_prior)
    assert result["limits"]["modelTurns"] == 1 and result["cloudCalls"] == 0
    prepared = runner.verify_preparation(root, protocol="outline-v1")
    assert prepared["baseline"] == runner.OUTLINE_BASELINE
    assert prepared["runtimeRevision"] == REVISION
    assert hashes(prior) == previous
    assert (root / "fixtures/source.docx").read_bytes() == (prior / "fixtures/source.docx").read_bytes()
    assert json.loads((root / "environment.json").read_text())["FLI_METRICS_FILE"] == str(root / "metrics.jsonl")
    execution_ready(root, monkeypatch)
    control = runner.execution_controls(root, protocol="outline-v1")
    assert control["reviewedRuntimeRevision"] == REVISION and control["protocol"] == "outline-v1"
    assert control["limits"]["modelTurns"] == 1
    assert control["runtimeUnchangedFromCandidate"] is False
    with pytest.raises(ValueError, match="prepared protocol differs"):
        runner.verify_preparation(root)
    with pytest.raises(ValueError, match="fresh follow-up state"):
        prepare_direct(root, direct_prior)


@pytest.mark.parametrize(
    "name",
    ["fixtures/source.docx", "preparation.json", "answering-binding-before.json", "embedding-binding-before.json"],
)
def test_direct_prior_anchor_rejects_replaced_bytes_before_new_state(tmp_path, direct_prior, name):
    prior, *_ = direct_prior
    (prior / name).write_bytes(b"changed")
    root = tmp_path / "outline"
    with pytest.raises(ValueError, match="archived prior evidence changed"):
        prepare_direct(root, direct_prior)
    assert not root.exists()


@pytest.mark.parametrize("change", ["manifest", "unfinished", "turn-count", "canary"])
def test_direct_prior_requires_exact_archive_completed_two_turns_and_canary(tmp_path, direct_prior, change):
    prior, canary, manifest = direct_prior
    if change == "manifest":
        write(manifest, {"files": {}})
    elif change == "canary":
        write(canary, {"passed": False})
    else:
        write(
            prior / "execution-result.json",
            {
                "cleanup": {"passed": change != "unfinished"},
                "models": {"completedTurns": 6 if change == "turn-count" else 2},
            },
        )
    root = tmp_path / "outline"
    with pytest.raises(ValueError):
        prepare_direct(root, direct_prior)
    assert not root.exists()


@pytest.mark.parametrize("revision", [None, "d332d1d", "g" * 40])
def test_outline_requires_exact_commit_before_git_or_execution(revision, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected git operation"))
    with pytest.raises(ValueError, match="exact reviewed runtime commit required"):
        runner.verify_runtime("outline-v1", revision)
    with pytest.raises(ValueError, match="cannot be overridden"):
        runner.verify_runtime("two-turn-v1", REVISION)


@pytest.mark.parametrize("failure", ["unrelated-ancestor", "unallowed-file", "no-product-change", "current-drift"])
def test_outline_runtime_rejects_wrong_ancestry_scope_or_current_bytes(failure, monkeypatch):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=int(failure == "unrelated-ancestor")),
    )
    changed = {"unallowed-file": "src/fli/search.py\n", "no-product-change": ""}.get(failure, "src/fli/tool.py\n")
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *args, **kwargs: changed)
    calls = []

    def baseline(revision):
        calls.append(revision)
        raise ValueError("current runtime drift")

    monkeypatch.setattr(runner, "runtime_baseline", baseline)
    with pytest.raises(ValueError):
        runner.verify_runtime("outline-v1", REVISION)
    assert calls == ([REVISION] if failure == "current-drift" else [])


def test_outline_runtime_accepts_only_reviewed_four_file_change_and_exact_commit(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        runner.subprocess, "check_output", lambda *args, **kwargs: "\n".join(runner.OUTLINE_RUNTIME_PATHS)
    )
    checked = []
    monkeypatch.setattr(runner, "runtime_baseline", lambda revision: checked.append(revision))
    runner.verify_runtime("outline-v1", REVISION)
    assert checked == [REVISION]


@pytest.fixture
def model_prior(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-model-controls"
    baseline.prepare_cases(legacy, source_bindings())
    bound = tmp_path / "binding.json"
    write(bound, binding())
    monkeypatch.setattr(models, "code_hashes", lambda: {"reviewed-code.py": "b" * 64})
    previous = tmp_path / "previous-two-turn-models"
    models.prepare_cases(
        previous,
        {"docx": source_bindings()["docx"]},
        prior_controls=legacy / "controls.json",
        prior_model_binding=bound,
    )
    return previous / "controls.json", bound


def test_one_turn_model_controls_preserve_prior_bytes_and_per_turn_settings(tmp_path, model_prior):
    previous = [path.read_bytes() for path in model_prior]
    root = tmp_path / "outline-models"
    models.prepare_cases(
        root,
        {"docx": source_bindings()["docx"]},
        prior_controls=model_prior[0],
        prior_model_binding=model_prior[1],
        protocol="outline-v1",
    )
    controls = models._verify(root, protocol="outline-v1")
    assert controls["modelTurns"] == len(controls["cases"]) == 1
    assert controls["meterLimits"] == models.protocol_settings("outline-v1")["meter"]
    assert all(controls[key] == value for key, value in models.PER_TURN_CONTROLS.items())
    assert [path.read_bytes() for path in model_prior] == previous
    with pytest.raises(ValueError, match="unknown follow-up controls"):
        models._verify(root)


def test_single_turn_logical_limit_is_cumulative_and_failed_usage_is_not_zero(tmp_path):
    limits = models.protocol_settings("outline-v1")["meter"]
    meter = Meter(tmp_path / "meter", limits, context=models.CONTEXT, output=models.OUTPUT)
    for _ in range(13):
        logical = meter.logical({"run": "one-outline"})
    with pytest.raises(ValueError, match="logical call ceiling"):
        meter.logical({"run": "unauthorized-second-turn"})
    row = meter.begin_attempt(logical, {"input": [], "tools": []})
    meter.finish_attempt(row, error=TimeoutError(), elapsed_ms=1)
    assert meter.state["unmeteredAttempts"] == 1
    assert meter.state["unmeteredInputReservations"] == 200000
    assert meter.state["unmeteredOutputReservations"] == 8000


async def test_one_fresh_turn_uses_actual_normal_agent_outline_serialization(tmp_path, model_prior, monkeypatch):
    env = await ready(source=fixtures()["source.docx"], name="source.docx")
    document = env.doc
    source = {
        "libraryId": env.lib,
        "documentId": document["id"],
        "generation": document["activeGeneration"],
        "contentRevision": document["contentRevision"],
        "fileName": document["fileName"],
        "sourceSha256": document["sha256"],
    }
    root = tmp_path / "outline-models"
    models.prepare_cases(
        root, {"docx": source}, prior_controls=model_prior[0], prior_model_binding=model_prior[1], protocol="outline-v1"
    )
    clock, pacer = Clock(), models.Pacer
    monkeypatch.setattr(models, "Pacer", lambda deadline: pacer(deadline, clock=clock, sleep=clock.sleep))
    requests = []

    def create(**request):
        requests.append(copy.deepcopy(request))
        outputs = [item for item in request["input"] if item.get("type") == "function_call_output"]
        if not outputs:
            name = next(tool["name"] for tool in request["tools"] if tool["name"].startswith("read_documents_library_"))
            pin = {key: source[key] for key in ("documentId", "generation", "contentRevision")}
            return response(
                {
                    "type": "function_call",
                    "id": "fc",
                    "call_id": "outline",
                    "name": name,
                    "arguments": json.dumps({"mode": "outline", "documents": [pin]}),
                    "status": "completed",
                }
            )
        evidence = json.loads(outputs[0]["output"])
        assert evidence["documents"][0]["outline"]
        if len(outputs) == 1:
            selector = evidence["documents"][0]["outline"][0]["readSelector"]
            name = next(tool["name"] for tool in request["tools"] if tool["name"].startswith("read_documents_library_"))
            return response(
                {
                    "type": "function_call",
                    "id": "fc_read",
                    "call_id": "read",
                    "name": name,
                    "arguments": json.dumps(selector),
                    "status": "completed",
                }
            )
        return response(message("The amber recorder stores each inspection result. (source.docx)"))

    result = models.run_models(
        root,
        index=env.index,
        agent_id="reader",
        actual_client=SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create)),
        deployment="chosen",
        model_binding=binding(),
        deadline=10000,
        api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
        protocol="outline-v1",
    )
    assert result["completedTurns"] == 1 and len(requests) == 3
    assert result["cases"][0]["toolRepliesInSuccessfulModelCalls"] == 2
    assert result["cases"][0]["manualReview"]["navigationRequirementsMet"] is None
    assert result["provider"]["limits"] == models.protocol_settings("outline-v1")["meter"]
    assert result["provider"]["knownInputTokens"] == 300 and result["provider"]["knownOutputTokens"] == 150
    for request in requests:
        assert request["reasoning"] == {"effort": "medium"} and request["max_output_tokens"] == 8000
        assert request["store"] is False and request["truncation"] == "disabled"
