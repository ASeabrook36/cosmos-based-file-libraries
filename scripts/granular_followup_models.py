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


def _prior_controls(controls, binding):
    require(controls.get("schema") == "fli.granular-live-models.controls.v1", "unexpected prior controls")
    require(len(controls.get("cases", [])) == 6, "prior six-turn qualification controls required")
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


def prepare_cases(output, source_bindings, *, prior_controls, prior_model_binding):
    root = private_path(output)
    require(not root.exists(), "follow-up model qualification requires a new private directory")
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair is separately attributable")
    prior_bytes = private_path(prior_controls).read_bytes()
    binding_bytes = private_path(prior_model_binding).read_bytes()
    prior, binding = json.loads(prior_bytes), json.loads(binding_bytes)
    _prior_controls(prior, binding)
    sealed_cases = cases(source_bindings)
    require(
        len(sealed_cases) == TURNS and len({row["case"] for row in sealed_cases}) == TURNS, "exactly two cases required"
    )
    controls = {
        "schema": "fli.granular-followup-models.controls.v1",
        "sourceBindings": copy.deepcopy(source_bindings),
        "cases": sealed_cases,
        "codeSha256": code_hashes(),
        "meterLimits": METER_LIMITS,
        "modelTurns": TURNS,
        **copy.deepcopy(PER_TURN_CONTROLS),
        "expectedModelBinding": binding,
        "priorEvidence": {
            "prior-model-controls.json": hashlib.sha256(prior_bytes).hexdigest(),
            "prior-model-binding.json": hashlib.sha256(binding_bytes).hexdigest(),
        },
        "qualificationScope": "Two separately sealed fresh follow-up turns with platform-provided DOCX document pins.",
        "priorEvidenceModified": False,
        "largerConfirmation": False,
    }
    root.mkdir(parents=True, mode=0o700)
    _save_bytes(root / "prior-model-controls.json", prior_bytes)
    _save_bytes(root / "prior-model-binding.json", binding_bytes)
    save_new(root / "controls.json", controls)
    return {"controlsSha256": sha(root / "controls.json"), "cases": TURNS, "status": "prepared"}


def _verify(root):
    controls = json.loads((root / "controls.json").read_text())
    require(controls.get("schema") == "fli.granular-followup-models.controls.v1", "unknown follow-up controls")
    require(
        controls.get("cases") == cases(controls.get("sourceBindings")) and len(controls["cases"]) == TURNS,
        "sealed follow-up cases changed",
    )
    require(controls.get("codeSha256") == code_hashes(), "follow-up code drift")
    for key, value in {
        "meterLimits": METER_LIMITS,
        "modelTurns": TURNS,
        **PER_TURN_CONTROLS,
        "priorEvidenceModified": False,
        "largerConfirmation": False,
    }.items():
        require(controls.get(key) == value, "follow-up control changed: " + key)
    expected_names = {"prior-model-controls.json", "prior-model-binding.json"}
    require(set(controls.get("priorEvidence", {})) == expected_names, "prior evidence inventory differs")
    for name, expected in controls["priorEvidence"].items():
        require(sha(root / name) == expected, "prior evidence snapshot changed")
    prior = json.loads((root / "prior-model-controls.json").read_text())
    binding = json.loads((root / "prior-model-binding.json").read_text())
    _prior_controls(prior, binding)
    require(controls.get("expectedModelBinding") == binding, "expected answering deployment changed")
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair is separately attributable")
    return controls


def run_models(state, *, index, agent_id, actual_client, deployment, model_binding, deadline, api_controls):
    """Run exactly two fresh normal-agent turns; preserve every failed attempt."""
    root = private_path(state)
    controls = _verify(root)
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
    pacer = Pacer(deadline)
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
    meter = Meter(root / "provider", METER_LIMITS, context=CONTEXT, output=OUTPUT)
    summaries, active = [], None
    try:
        before = _source_snapshot(index, controls["sourceBindings"])
        save_new(root / "sources-before.json", before)
        for active in controls["cases"]:
            pacer.remaining()
            _verify(root)
            require(_source_snapshot(index, controls["sourceBindings"]) == before, "source changed before model turn")
            directory = root / active["case"]
            directory.mkdir(mode=0o700)
            calls = []
            captured = CapturedIndex(index, directory)
            source = controls["sourceBindings"][active["sourceRole"]]
            agent = {"id": agent_id, "libraryIds": [source["libraryId"]], "instructions": AGENT_INSTRUCTIONS}
            chat = _chat(
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
        _verify(root)
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
