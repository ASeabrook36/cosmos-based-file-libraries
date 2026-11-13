"""Two separately attributable granular-navigation follow-up model turns.

Reuses the existing normal agent, Responses adapter and private attempt meter.
No prior case, artifact, control or implementation is modified. Importing and
preparing this module make no network calls.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fli_demo.agent import MAX_ROUNDS, run_turn
from scripts import granular_live as live
from scripts import granular_live_models as baseline
from scripts.evaluate_agent_retrieval import capture
from scripts.granular_followup_cases import cases
from scripts.hybrid_model_meter import Meter, meter_summary
from scripts.qualify_granular_navigation import save_new, sha
from scripts.retrieval_controls import AGENT_INSTRUCTIONS, private_path
from scripts.structure_lab import save

REPO = Path(__file__).resolve().parents[1]
CONTEXT, OUTPUT, TURNS = 200000, 8000, 2
PINNED_MODEL = {"format": "OpenAI", "name": "gpt-5-mini", "version": "2025-08-07"}
METER_LIMITS = {
    "logicalCalls": 26,  # Twelve normal rounds plus the agent's final-only call, for each of two turns.
    "physicalResponsesAttempts": 36,
    "inputTokensWithUnknownReservations": 2000000,
    "outputTokensWithUnknownReservations": 200000,
}
PER_TURN_CONTROLS = {
    "contextAllowance": CONTEXT,
    "outputAllowance": OUTPUT,
    "reasoningEffort": "medium",
    "maxRounds": MAX_ROUNDS,
    "searchMode": "vector",
    "evidenceView": "compact",
    "selectors": "handles",
    "contextAccounting": "legacy-v1",
    "agentInstructions": AGENT_INSTRUCTIONS,
    "pacing": copy.deepcopy(baseline.PACING),
}

# Reuse functions without altering their module globals or the prior six-case loop.
Pacer = baseline.Pacer
DeadlineHTTP = baseline.DeadlineHTTP
CapturedIndex = baseline.CapturedIndex
_source_snapshot = baseline._source_snapshot
_chat = baseline._chat
_turn_summary = baseline._turn_summary
code_hashes = live.code_hashes


def require(condition, message):
    if not condition:
        raise ValueError(message)


def protocol_settings(protocol="two-turn-v1"):
    if protocol == "two-turn-v1":
        return {
            "cases": cases,
            "turns": TURNS,
            "meter": copy.deepcopy(METER_LIMITS),
            "schema": "fli.granular-followup-models.controls.v1",
            "priorSchema": "fli.granular-live-models.controls.v1",
            "priorTurns": 6,
            "scope": "Two separately sealed fresh follow-up turns with platform-provided DOCX document pins.",
        }
    require(protocol in {"outline-v1", "outline-v2"}, "unknown qualification protocol")
    from scripts.granular_outline_case import cases as outline_cases

    return {
        "cases": outline_cases,
        "turns": 1,
        "meter": {
            "logicalCalls": 13,
            "physicalResponsesAttempts": 18,
            "inputTokensWithUnknownReservations": 1000000,
            "outputTokensWithUnknownReservations": 100000,
        },
        "schema": (
            "fli.granular-refinement-models.controls.v1"
            if protocol == "outline-v2"
            else "fli.granular-outline-models.controls.v1"
        ),
        "priorSchema": (
            "fli.granular-outline-models.controls.v1"
            if protocol == "outline-v2"
            else "fli.granular-followup-models.controls.v1"
        ),
        "priorTurns": 1 if protocol == "outline-v2" else 2,
        "scope": "One sealed outline-to-focused-read turn; whole-document fallback does not pass.",
    }


def _prior_controls(controls, binding, *, protocol="two-turn-v1"):
    selected = protocol_settings(protocol)
    require(controls.get("schema") == selected["priorSchema"], "unexpected prior controls")
    require(len(controls.get("cases", [])) == selected["priorTurns"], "prior qualification turn count differs")
    for key, value in PER_TURN_CONTROLS.items():
        require(controls.get(key) == value, "prior per-turn control differs: " + key)
    require(
        binding.get("model") == PINNED_MODEL
        and isinstance(binding.get("sku"), dict)
        and binding["sku"].get("name")
        and type(binding["sku"].get("capacity")) is int
        and binding["sku"]["capacity"] > 0
        and re.fullmatch(r"[a-f0-9]{64}", binding.get("bindingSha256", "")),
        "prior answering deployment binding differs",
    )


def _save_bytes(path, content):
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _per_turn(protocol, capacity_policy=None, binding=None):
    controls = copy.deepcopy(PER_TURN_CONTROLS)
    if protocol == "outline-v2":
        from scripts.granular_pacing import validate_policy

        validate_policy(capacity_policy)
        require(
            binding is not None
            and capacity_policy["bindingSha256"] == binding.get("bindingSha256")
            and capacity_policy["capacityBinding"]["model"] == binding.get("model")
            and capacity_policy["capacityBinding"]["sku"] == binding.get("sku"),
            "pacing policy differs from expected model binding",
        )
        controls["pacing"] = copy.deepcopy(capacity_policy)
    else:
        require(capacity_policy is None, "capacity policy belongs to outline-v2 only")
    return controls


def prepare_cases(
    output,
    source_bindings,
    *,
    prior_controls,
    prior_model_binding,
    protocol="two-turn-v1",
    capacity_receipt=None,
):
    selected = protocol_settings(protocol)
    root = private_path(output)
    require(not root.exists(), "follow-up model qualification requires a new private directory")
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair is separately attributable")
    prior_bytes = private_path(prior_controls).read_bytes()
    binding_bytes = private_path(prior_model_binding).read_bytes()
    prior, binding = json.loads(prior_bytes), json.loads(binding_bytes)
    _prior_controls(prior, binding, protocol=protocol)
    capacity_bytes = private_path(capacity_receipt).read_bytes() if capacity_receipt is not None else None
    per_turn = _per_turn(protocol, json.loads(capacity_bytes) if capacity_bytes is not None else None, binding)
    sealed_cases = selected["cases"](source_bindings)
    require(
        len(sealed_cases) == selected["turns"] and len({row["case"] for row in sealed_cases}) == selected["turns"],
        "sealed qualification case count differs",
    )
    controls = {
        "schema": selected["schema"],
        "sourceBindings": copy.deepcopy(source_bindings),
        "cases": sealed_cases,
        "codeSha256": code_hashes(),
        "meterLimits": selected["meter"],
        "modelTurns": selected["turns"],
        **per_turn,
        "expectedModelBinding": binding,
        "priorEvidence": {
            "prior-model-controls.json": hashlib.sha256(prior_bytes).hexdigest(),
            "prior-model-binding.json": hashlib.sha256(binding_bytes).hexdigest(),
        },
        "qualificationScope": selected["scope"],
        "priorEvidenceModified": False,
        "largerConfirmation": False,
    }
    if capacity_bytes is not None:
        controls["priorEvidence"]["capacity-policy.json"] = hashlib.sha256(capacity_bytes).hexdigest()
    root.mkdir(parents=True, mode=0o700)
    _save_bytes(root / "prior-model-controls.json", prior_bytes)
    _save_bytes(root / "prior-model-binding.json", binding_bytes)
    if capacity_bytes is not None:
        _save_bytes(root / "capacity-policy.json", capacity_bytes)
    save_new(root / "controls.json", controls)
    return {"controlsSha256": sha(root / "controls.json"), "cases": selected["turns"], "status": "prepared"}


def _verify(root, *, protocol="two-turn-v1"):
    selected = protocol_settings(protocol)
    controls = json.loads((root / "controls.json").read_text())
    require(controls.get("schema") == selected["schema"], "unknown follow-up controls")
    require(
        controls.get("cases") == selected["cases"](controls.get("sourceBindings"))
        and len(controls["cases"]) == selected["turns"],
        "sealed follow-up cases changed",
    )
    require(controls.get("codeSha256") == code_hashes(), "follow-up code drift")
    capacity = json.loads((root / "capacity-policy.json").read_text()) if protocol == "outline-v2" else None
    for key, value in {
        "meterLimits": selected["meter"],
        "modelTurns": selected["turns"],
        **_per_turn(protocol, capacity, controls.get("expectedModelBinding")),
        "priorEvidenceModified": False,
        "largerConfirmation": False,
    }.items():
        require(controls.get(key) == value, "follow-up control changed: " + key)
    expected_names = {"prior-model-controls.json", "prior-model-binding.json"}
    if protocol == "outline-v2":
        expected_names.add("capacity-policy.json")
    require(set(controls.get("priorEvidence", {})) == expected_names, "prior evidence inventory differs")
    for name, expected in controls["priorEvidence"].items():
        require(sha(root / name) == expected, "prior evidence snapshot changed")
    prior = json.loads((root / "prior-model-controls.json").read_text())
    binding = json.loads((root / "prior-model-binding.json").read_text())
    _prior_controls(prior, binding, protocol=protocol)
    require(controls.get("expectedModelBinding") == binding, "expected answering deployment changed")
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair is separately attributable")
    return controls


def run_models(
    state, *, index, agent_id, actual_client, deployment, model_binding, deadline, api_controls, protocol="two-turn-v1"
):
    """Run the allowlisted sealed protocol; preserve every failed attempt."""
    selected = protocol_settings(protocol)
    root = private_path(state)
    controls = _verify(root, protocol=protocol)
    require(index.evidence_view == "compact" and index.selector_mode == "handles", "normal presentation differs")
    require(
        api_controls.get("searchMode") == "vector" and api_controls.get("contextAccounting") == "legacy-v1",
        "API/context controls differ",
    )
    require(actual_client.max_retries == 0, "OpenAI transport retries must be zero")
    require(
        isinstance(deployment, str) and deployment and isinstance(agent_id, str) and agent_id,
        "runtime model/agent binding unavailable",
    )
    require(model_binding == controls["expectedModelBinding"], "fresh model binding differs from prior qualification")
    if protocol == "outline-v2":
        from scripts.granular_pacing import Pacer as RefinementPacer
        from scripts.granular_pacing import chat as refinement_chat

        pacer = RefinementPacer(deadline, policy=controls["pacing"])
        chat_factory = refinement_chat
    else:
        pacer, chat_factory = Pacer(deadline), _chat
    pacer.remaining()
    index = copy.copy(index)
    index.http = DeadlineHTTP(index.http, pacer)
    save_new(
        root / "started.json",
        {
            "controlsSha256": sha(root / "controls.json"),
            "modelBinding": model_binding,
            "deployment": deployment,
            "apiControls": api_controls,
            "startedAt": datetime.now(UTC).isoformat(),
        },
    )
    meter = Meter(root / "provider", selected["meter"], context=CONTEXT, output=OUTPUT)
    summaries, active = [], None
    try:
        before = _source_snapshot(index, controls["sourceBindings"])
        save_new(root / "sources-before.json", before)
        for active in controls["cases"]:
            pacer.remaining()
            _verify(root, protocol=protocol)
            require(_source_snapshot(index, controls["sourceBindings"]) == before, "source changed before model turn")
            directory = root / active["case"]
            directory.mkdir(mode=0o700)
            calls = []
            captured = CapturedIndex(index, directory)
            source = controls["sourceBindings"][active["sourceRole"]]
            agent = {"id": agent_id, "libraryIds": [source["libraryId"]], "instructions": AGENT_INSTRUCTIONS}
            chat = chat_factory(
                actual_client,
                deployment,
                meter,
                {"run": active["case"], "cell": "granular-followup-vector", "case": active["case"]},
                pacer,
                directory,
                calls,
            )
            turn = run_turn(
                chat,
                captured,
                agent,
                [],
                active["question"],
                context_token_budget=CONTEXT,
                max_rounds=MAX_ROUNDS,
                image_inputs=False,
            )
            save_new(directory / "turn.json", capture(asdict(turn)))
            require(_source_snapshot(index, controls["sourceBindings"]) == before, "source changed during model turn")
            summary = _turn_summary(active, turn, calls)
            summaries.append(summary)
            save_new(directory / "summary.json", summary)
            save(
                root / "status.json",
                {"status": "running", "completedTurns": len(summaries), "provider": meter_summary(meter.path)},
            )
        _verify(root, protocol=protocol)
        after = _source_snapshot(index, controls["sourceBindings"])
        require(after == before, "source snapshot changed at model exit")
        save_new(root / "sources-after.json", after)
        result = {
            "status": "complete_pending_source_review",
            "completedTurns": len(summaries),
            "cases": summaries,
            "provider": meter_summary(meter.path),
            "sourceSnapshotsUnchanged": True,
            "followupNotConfirmation": True,
            "largerConfirmation": False,
        }
        save_new(root / "results.json", result)
        save(root / "status.json", {key: value for key, value in result.items() if key != "cases"})
        return result
    except BaseException as error:
        save(
            root / "status.json",
            {
                "status": "failed",
                "completedTurns": len(summaries),
                "activeCase": active["case"] if active else None,
                "errorType": type(error).__name__,
                "provider": meter_summary(meter.path),
                "limitation": "Single-use follow-up stopped. Preserve every attempt; "
                "no selective rerun or raised ceiling.",
            },
        )
        raise
