"""New pacing protocol remains separate and drives the real normal tool adapter."""

import copy
import json
from types import SimpleNamespace

import pytest

from scripts import granular_followup_models as models
from scripts import granular_live_models as baseline
from scripts import granular_pacing as pacing
from scripts.granular_followup_cases import RULE
from scripts.qualify_granular_navigation import fixtures
from tests.test_granular_live_models import Clock, binding, source_bindings
from tests.test_granular_navigation import ready
from tests.test_responses_chat import message, response


def arm():
    return {
        "sku": binding()["sku"],
        "properties": {
            "model": binding()["model"],
            "versionUpgradeOption": "NoAutoUpgrade",
            "rateLimits": [
                {"key": "token", "count": 50000, "renewalPeriod": 60},
                {"key": "request", "count": 50, "renewalPeriod": 60},
            ],
        },
    }


def write(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


@pytest.fixture
def prepared_prior(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "code_hashes", lambda: {"frozen.py": "a" * 64})
    legacy = tmp_path / "legacy"
    baseline.prepare_cases(legacy, source_bindings())
    bound = write(tmp_path / "binding.json", binding())
    two = tmp_path / "two"
    models.prepare_cases(
        two, {"docx": source_bindings()["docx"]}, prior_controls=legacy / "controls.json", prior_model_binding=bound
    )
    outline = tmp_path / "outline"
    models.prepare_cases(
        outline,
        {"docx": source_bindings()["docx"]},
        prior_controls=two / "controls.json",
        prior_model_binding=bound,
        protocol="outline-v1",
    )
    policy = pacing.policy_from_capacity(arm(), binding_sha256=binding()["bindingSha256"])
    capacity = write(tmp_path / "capacity.json", policy)
    return outline / "controls.json", bound, capacity


def prepare(root, sources, prior):
    return models.prepare_cases(
        root,
        sources,
        prior_controls=prior[0],
        prior_model_binding=prior[1],
        capacity_receipt=prior[2],
        protocol="outline-v2",
    )


def test_fresh_protocol_changes_only_explicit_pacing_controls_and_preserves_failed_prior(prepared_prior, tmp_path):
    prior = [p.read_bytes() for p in prepared_prior]
    old = copy.deepcopy(models.PER_TURN_CONTROLS)
    root = tmp_path / "new"
    prepare(root, {"docx": source_bindings()["docx"]}, prepared_prior)
    controls = models._verify(root, protocol="outline-v2")
    assert controls["schema"] == "fli.granular-refinement-models.controls.v1"
    assert controls["modelTurns"] == 1 and controls["meterLimits"]["logicalCalls"] == 13
    assert controls["cases"] == json.loads(prior[0])["cases"]
    assert controls["pacing"]["localInputPlusMaxOutputCeiling"] == 45000
    assert controls["pacing"]["outputReservation"] == 8000
    for key in old.keys() - {"pacing"}:
        assert controls[key] == old[key]
    assert models.PER_TURN_CONTROLS == old
    assert [p.read_bytes() for p in prepared_prior] == prior
    assert (root / "capacity-policy.json").read_bytes() == prior[2]
    assert models.protocol_settings("outline-v1")["priorTurns"] == 2
    assert models.protocol_settings()["turns"] == 2


@pytest.mark.parametrize("change", ["missing", "capacity", "binding", "prior-pacing", "prior-schema"])
def test_drift_refuses_preparation_without_starting_calls(tmp_path, prepared_prior, change):
    source = list(prepared_prior)
    if change == "missing":
        source[2] = None
    elif change in {"capacity", "binding"}:
        policy = json.loads(source[2].read_text())
        policy["localInputPlusMaxOutputCeiling" if change == "capacity" else "bindingSha256"] = (
            999999 if change == "capacity" else "f" * 64
        )
        write(source[2], policy)
    else:
        controls = json.loads(source[0].read_text())
        controls["pacing" if change == "prior-pacing" else "schema"] = "changed"
        write(source[0], controls)
    root = tmp_path / "refused"
    with pytest.raises(ValueError):
        prepare(root, {"docx": source_bindings()["docx"]}, source)
    assert not root.exists()


async def test_refinement_uses_actual_outline_preview_and_copied_focused_source_with_new_pacer(
    tmp_path, prepared_prior, monkeypatch
):
    env = await ready(source=fixtures()["source.docx"], name="source.docx")
    doc = env.doc
    source = {
        "libraryId": env.lib,
        "documentId": doc["id"],
        "generation": doc["activeGeneration"],
        "contentRevision": doc["contentRevision"],
        "fileName": doc["fileName"],
        "sourceSha256": doc["sha256"],
    }
    root = tmp_path / "new"
    prepare(root, {"docx": source}, prepared_prior)
    clock, pacer = Clock(), pacing.Pacer
    monkeypatch.setattr(
        pacing, "Pacer", lambda deadline, *, policy: pacer(deadline, policy=policy, clock=clock, sleep=clock.sleep)
    )
    requests, selectors = [], []

    def create(**request):
        requests.append(copy.deepcopy(request))
        name = next(tool["name"] for tool in request["tools"] if tool["name"].startswith("read_documents_library_"))
        outputs = [item for item in request["input"] if item.get("type") == "function_call_output"]
        if not outputs:
            selector = {
                "mode": "outline",
                "documents": [{k: source[k] for k in ("documentId", "generation", "contentRevision")}],
            }
        else:
            evidence = json.loads(outputs[-1]["output"])
            document = evidence["documents"][0]
            if evidence["mode"] == "selected":
                assert document["selectionComplete"] is True and document["documentComplete"] is False
                assert len(document["sections"]) == 1 and RULE in document["sections"][0]["text"]
                return response(message(RULE + " " + document["sections"][0]["citation"]))
            assert document["outline"]
            matching = [
                entry for entry in document["outline"] if RULE in entry.get("navigationPreview", {}).get("prefix", "")
            ]
            if matching:
                selector = matching[0]["readSelector"]
            else:
                title = {1: "Operating rules", 2: "Sampling", 3: "Exceptions"}[len(outputs)]
                selector = next(entry for entry in document["outline"] if entry["title"] == title)["outlineSelector"]
        selectors.append(copy.deepcopy(selector))
        return response(
            {
                "type": "function_call",
                "id": f"fc{len(requests)}",
                "call_id": f"c{len(requests)}",
                "name": name,
                "arguments": json.dumps(selector),
                "status": "completed",
            }
        )

    kwargs = dict(
        index=env.index,
        agent_id="reader",
        actual_client=SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create)),
        deployment="chosen",
        model_binding=binding(),
        deadline=10000,
        api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
        protocol="outline-v2",
    )
    result = models.run_models(root, **kwargs)
    assert result["completedTurns"] == 1 and len(requests) == 6
    assert [s["mode"] for s in selectors] == ["outline"] * 4 + ["selected"]
    assert len(list((root / "outline-focused-source").glob("pacing-plan-*.json"))) == 6
    assert result["provider"]["physicalResponsesAttempts"] == 6
    assert result["sourceSnapshotsUnchanged"]
    assert clock.sleeps and max(clock.sleeps) <= 30
    for request in requests:
        assert request["reasoning"] == {"effort": "medium"}
        assert request["max_output_tokens"] == 8000 and request["store"] is False
        assert request["truncation"] == "disabled" and request["include"] == ["reasoning.encrypted_content"]
    with pytest.raises(FileExistsError):
        models.run_models(root, **kwargs)
    assert len(requests) == 6
