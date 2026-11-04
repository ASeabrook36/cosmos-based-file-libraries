"""Two-turn follow-up controls and normal-agent execution, entirely local."""

import copy
import json
from types import SimpleNamespace

import pytest

from scripts import granular_followup_models as models
from scripts import granular_live_models as baseline
from scripts.hybrid_model_meter import Meter, meter_summary
from scripts.qualify_granular_navigation import fixtures
from tests.test_granular_live_models import Clock, binding, source_bindings
from tests.test_granular_navigation import ready
from tests.test_responses_chat import message, response


@pytest.fixture
def prior(tmp_path, monkeypatch):
    directory = tmp_path / "prior"
    baseline.prepare_cases(directory, source_bindings())
    bound = directory / "answering-binding.json"
    bound.write_text(json.dumps(binding()))
    bound.chmod(0o600)
    # Root/reviewer may still be editing the new coordinator in this worktree.
    # Production freezes live.code_hashes; source inventory is tested separately.
    monkeypatch.setattr(models, "code_hashes", lambda: {"synthetic-frozen-source.py": "a" * 64})
    return directory / "controls.json", bound


def prepare(root, bindings, prior):
    return models.prepare_cases(root, bindings, prior_controls=prior[0], prior_model_binding=prior[1])


def test_prepare_copies_prior_bytes_seals_exact_two_cases_and_does_not_change_baseline(tmp_path, prior):
    original = [path.read_bytes() for path in prior]
    old_limits = copy.deepcopy(baseline.METER_LIMITS)
    root = tmp_path / "follow-up"
    result = prepare(root, {"docx": source_bindings()["docx"]}, prior)
    controls = models._verify(root)
    assert result["cases"] == controls["modelTurns"] == 2
    assert {row["case"] for row in controls["cases"]} == {"cell-completeness", "source-identifier-fields"}
    assert controls["meterLimits"] == {
        "logicalCalls": 26,
        "physicalResponsesAttempts": 36,
        "inputTokensWithUnknownReservations": 2000000,
        "outputTokensWithUnknownReservations": 200000,
    }
    assert controls["expectedModelBinding"] == binding()
    assert all(controls[key] == value for key, value in models.PER_TURN_CONTROLS.items())
    assert original == [path.read_bytes() for path in prior]
    assert (root / "prior-model-controls.json").read_bytes() == original[0]
    assert (root / "prior-model-binding.json").read_bytes() == original[1]
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.iterdir())
    assert baseline.METER_LIMITS == old_limits and len(baseline.cases(source_bindings())) == 6
    for case in controls["cases"]:
        assert all(fact not in case["question"] for fact in case["expectedSourceFacts"])
        assert case["reviewCriteria"] and "contentRevision" in case["question"]
    with pytest.raises(ValueError, match="new private"):
        prepare(root, {"docx": source_bindings()["docx"]}, prior)


@pytest.mark.parametrize(
    "field",
    [
        "contextAllowance",
        "outputAllowance",
        "maxRounds",
        "searchMode",
        "reasoningEffort",
        "pacing",
        "agentInstructions",
    ],
)
def test_prior_per_turn_drift_refuses_prepare_without_output_directory(tmp_path, prior, field):
    original = json.loads(prior[0].read_text())
    original[field] = "changed"
    prior[0].write_text(json.dumps(original))
    root = tmp_path / "refused"
    with pytest.raises(ValueError, match="prior per-turn"):
        prepare(root, {"docx": source_bindings()["docx"]}, prior)
    assert not root.exists()


@pytest.mark.parametrize(
    "field",
    [
        "cases",
        "codeSha256",
        "meterLimits",
        "modelTurns",
        "contextAccounting",
        "expectedModelBinding",
        "largerConfirmation",
    ],
)
def test_new_control_drift_stops_before_model_start(tmp_path, prior, field):
    root = tmp_path / "follow-up"
    prepare(root, {"docx": source_bindings()["docx"]}, prior)
    path = root / "controls.json"
    controls = json.loads(path.read_text())
    controls[field] = None
    path.write_text(json.dumps(controls))
    with pytest.raises(ValueError):
        models._verify(root)
    assert not (root / "started.json").exists()


def test_prior_snapshot_tampering_cannot_change_the_frozen_expected_model(tmp_path, prior):
    root = tmp_path / "follow-up"
    prepare(root, {"docx": source_bindings()["docx"]}, prior)
    snapshot = root / "prior-model-binding.json"
    altered = json.loads(snapshot.read_text())
    altered["model"]["version"] = "different"
    snapshot.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="prior evidence snapshot"):
        models._verify(root)


@pytest.mark.parametrize("mutation", ["model", "revision", "sku", "endpoint_binding"])
def test_fresh_deployment_must_equal_frozen_prior_binding_before_any_api_or_model_call(tmp_path, prior, mutation):
    root = tmp_path / "follow-up"
    prepare(root, {"docx": source_bindings()["docx"]}, prior)
    changed = copy.deepcopy(binding())
    if mutation == "model":
        changed["model"]["name"] = "gpt-6"
    elif mutation == "revision":
        changed["model"]["version"] = "other"
    elif mutation == "sku":
        changed["sku"]["capacity"] += 1
    else:
        changed["bindingSha256"] = "b" * 64
    with pytest.raises(ValueError, match="fresh model binding"):
        models.run_models(
            root,
            index=SimpleNamespace(evidence_view="compact", selector_mode="handles"),
            agent_id="reader",
            actual_client=SimpleNamespace(max_retries=0),
            deployment="chosen",
            model_binding=changed,
            deadline=10000,
            api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
        )
    assert not (root / "started.json").exists() and not (root / "provider").exists()


def test_global_logical_cap_preserves_two_native_twelve_rounds_plus_final_calls(tmp_path):
    meter = Meter(tmp_path / "meter", models.METER_LIMITS, context=models.CONTEXT, output=models.OUTPUT)
    for name in ("cell-completeness", "source-identifier-fields"):
        for _ in range(13):
            meter.logical({"run": name})
    with pytest.raises(ValueError, match="logical call ceiling"):
        meter.logical({"run": "an unauthorized third turn"})
    assert meter.state["logicalCallsStarted"] == 26


def test_model_source_inventory_includes_new_cases_runner_and_meter():
    inventory = models.live.code_hashes()
    assert inventory["scripts/granular_followup_models.py"] == models.sha(models.__file__)
    assert "scripts/granular_followup_cases.py" in inventory
    assert "scripts/hybrid_model_meter.py" in inventory
    assert "src/fli_demo/agent.py" in inventory


async def local_setup(root, prior, monkeypatch):
    env = await ready(source=fixtures()["source.docx"], name="source.docx")
    doc = env.doc
    bindings = {
        "docx": {
            "libraryId": env.lib,
            "documentId": doc["id"],
            "generation": doc["activeGeneration"],
            "contentRevision": doc["contentRevision"],
            "fileName": doc["fileName"],
            "sourceSha256": doc["sha256"],
        }
    }
    prepare(root, bindings, prior)
    clock, original_pacer = Clock(), models.Pacer
    monkeypatch.setattr(models, "Pacer", lambda deadline: original_pacer(deadline, clock=clock, sleep=clock.sleep))
    return env, bindings


async def test_two_fresh_turns_use_real_agent_tool_responses_and_same_actual_provider_parameters(
    tmp_path, prior, monkeypatch
):
    root = tmp_path / "follow-up"
    env, bindings = await local_setup(root, prior, monkeypatch)
    requests = []

    def create(**request):
        requests.append(copy.deepcopy(request))
        if not any(item.get("type") == "function_call_output" for item in request["input"]):
            name = next(tool["name"] for tool in request["tools"] if tool["name"].startswith("read_documents_library_"))
            pin = {key: bindings["docx"][key] for key in ("documentId", "generation", "contentRevision")}
            return response(
                {
                    "type": "function_call",
                    "id": "fc",
                    "call_id": "full",
                    "name": name,
                    "arguments": json.dumps({"mode": "full", "documents": [pin]}),
                    "status": "completed",
                }
            )
        return response(message("The source text was read completely. (source.docx)"))

    kwargs = dict(
        index=env.index,
        agent_id="reader",
        actual_client=SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create)),
        deployment="chosen",
        model_binding=binding(),
        deadline=10000,
        api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
    )
    result = models.run_models(root, **kwargs)
    assert result["status"] == "complete_pending_source_review" and result["completedTurns"] == 2
    assert len(requests) == 4
    assert result["provider"]["knownInputTokens"] == 400 and result["provider"]["knownOutputTokens"] == 200
    assert result["provider"]["usageComplete"] is True
    assert result["sourceSnapshotsUnchanged"] and result["followupNotConfirmation"]
    for request in requests:
        assert request["reasoning"] == {"effort": "medium"} and request["max_output_tokens"] == 8000
        assert request["store"] is False and request["truncation"] == "disabled"
        assert request["include"] == ["reasoning.encrypted_content"]
    for first in (requests[0], requests[2]):
        assert not any(item.get("type") == "function_call_output" for item in first["input"])
        assert len([item for item in first["input"] if item.get("role") == "user"]) == 1
    for case in result["cases"]:
        assert case["toolRepliesInSuccessfulModelCalls"] == 1
        assert all(fact["delivered"] for fact in case["factDeliveryIndicators"])
        assert (root / case["case"] / "tool-result-02.json").exists()
        assert case["manualReview"]["navigationRequirementsMet"] is None
        sent = json.loads((root / case["case"] / "model-input-01.json").read_text())
        user = next(m["content"] for m in sent["messages"] if m["role"] == "user")
        assert "reviewCriteria" not in user and "expectedSourceFacts" not in user
    with pytest.raises(FileExistsError):
        models.run_models(root, **kwargs)
    assert len(requests) == 4


async def test_missing_provider_usage_retains_failure_and_unknown_reservation_without_second_turn(
    tmp_path, prior, monkeypatch
):
    root = tmp_path / "follow-up"
    env, _ = await local_setup(root, prior, monkeypatch)
    requests = []

    def create(**request):
        requests.append(request)
        return response(message("Unmetered answer"), usage=None)

    with pytest.raises(ValueError, match="meters unavailable"):
        models.run_models(
            root,
            index=env.index,
            agent_id="reader",
            actual_client=SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create)),
            deployment="chosen",
            model_binding=binding(),
            deadline=10000,
            api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
        )
    status = json.loads((root / "status.json").read_text())
    assert status["status"] == "failed" and status["completedTurns"] == 0 and len(requests) == 1
    measured = meter_summary(root / "provider/ledger.json")
    assert measured["unmeteredAttempts"] == 1 and measured["unmeteredInputReservations"] == 200000
    assert measured["completeInputTokens"] is None and measured["completeOutputTokens"] is None
    assert not (root / "source-identifier-fields").exists()
    assert not (root / "results.json").exists()
