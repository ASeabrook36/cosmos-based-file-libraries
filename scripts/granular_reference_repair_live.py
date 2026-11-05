"""One sealed six-turn compact/navigation bookmark-lifetime qualification.

Only explicit preflight/execute contacts Azure (answering model + management GETs).
All source APIs use the exact retained publication in local in-memory stores.
Frozen earlier runners and evidence are never modified.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fli_demo.agent import run_turn
from scripts.evaluate_agent_retrieval import capture
from scripts.granular_live import archive, code_hashes
from scripts.granular_live_models import CapturedIndex, DeadlineHTTP, _source_snapshot
from scripts.granular_live_preflight import management_get
from scripts.granular_outline_case import cases
from scripts.granular_pacing import CONTEXT, MODEL, OUTPUT, Pacer, chat, validate_capacity, validate_policy
from scripts.granular_reference_live import _fixture, local_index, now, read
from scripts.hybrid_model_meter import Meter, actual_client, meter_by_cell, meter_summary
from scripts.qualify_granular_navigation import require, save_new, sha
from scripts.retrieval_controls import AGENT_INSTRUCTIONS, deployment_binding, private_path

REPO = Path(__file__).resolve().parents[1]
BASELINE = "13986b6d58b2fe5112edebb00ade21109bea8c6d"
SCHEMA = "fli.granular-reference-repair.live.v1"
CELLS = ("compact", "navigation")
STAGES = ("A", "acknowledgment", "B")
LIMITS = {
    "logicalCalls": 60,
    "physicalResponsesAttempts": 80,
    "inputTokensWithUnknownReservations": 4_000_000,
    "outputTokensWithUnknownReservations": 400_000,
}
ROUNDS = {"A": 12, "acknowledgment": 3, "B": 12}
EXECUTION_SECONDS = 1800
ACKNOWLEDGMENT = "Thanks. Please just acknowledge receipt briefly. I will ask a follow-up next."
FOLLOWUP = (
    "Please revisit the damaged-vessel rule in {fileName}. Read its original source again and state the rule "
    "with its human source citation. Do not read the whole document. The application retained the following "
    "bookmark exactly from an earlier tool reply that was delivered to you. Its recorded scope is {scope}. "
    "First try that bookmark with the read tool. If it is rejected or expired, reacquire the file and relevant "
    "region through the currently available authorized tools; do not invent or edit references or remove "
    "snapshot checks. A failed bookmark is not evidence for the rule.\nRetained read request:\n{bookmark}"
)
NO_BOOKMARK = (
    "Please revisit the damaged-vessel rule in {fileName}. Read its original source again and state the rule "
    "with its human source citation. Do not read the whole document. No usable read or outline bookmark was "
    "delivered in the earlier source turn. Reacquire the file and relevant region through the currently "
    "available authorized tools; do not infer the rule from prior assistant prose."
)
BOOKMARK_POLICY = (
    "Scan successful actual model-input captures newest-first, and their tool replies newest-first. "
    "Prefer the latest delivered selected/full response containing original source text: take its first "
    "source row in document/wire order and copy readSelector or actions.read exactly. Otherwise take the "
    "latest delivered nonempty outline's first entry with an outline action, copying outlineSelector or "
    "actions.outline. If none, report no bookmark. Never synthesize a selector, consult a hidden registry "
    "to construct a call, or substitute a target using gold source facts."
)
RETENTION = (
    "Both arms retain exact prior user prompts and delivered assistant prose only, plus one actually "
    "model-delivered bookmark shown explicitly in B's user question. No prior tool transcript, source body, "
    "registry, evidence hint ledger or raw model draft is restored. Each public run_turn creates/closes its "
    "own reference session. Compact retains a full selector while navigation retains a short action; this "
    "is an intentional information-lifetime tradeoff, not a matched-prose-only comparison."
)
ACCEPTANCE = {
    "A": [
        "nonempty authored outline actually delivered",
        "subsequent copied focused source read",
        "correct rule and citation",
        "no whole-document content request",
    ],
    "B": [
        "new public run_turn with exact declared prose history",
        "visible bookmark attempted before reacquisition when available",
        "navigation prior-turn reference rejected without API access",
        "current authorized reacquisition after rejected bookmark",
        "fresh complete focused source received",
        "correct rule and citation",
        "no whole-document content request",
    ],
    "all": [
        "record every source and acknowledgment turn including failures",
        "no selective rerun after semantic failure",
        "no unrequested internal IDs in delivered answer",
        "unchanged source/model/context/pacing",
        "all six-turn input/output and recovery costs counted",
    ],
    "interpretation": (
        "Manual review of actual successful model delivery, requests, resolution and source. "
        "A completed loop or safe refusal is not source-task success. Missing focused bookmark after failed A "
        "remains a failure, never repaired by the harness."
    ),
}


def inventory(root):
    return {
        str(p.relative_to(root)): {"sha256": sha(p), "bytes": p.stat().st_size}
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def preparation_controls():
    return {
        "schema": SCHEMA,
        "baseline": BASELINE,
        "cells": list(CELLS),
        "stages": list(STAGES),
        "order": [f"{cell}/{stage}" for cell in CELLS for stage in STAGES],
        "meterLimits": LIMITS,
        "contextAllowance": CONTEXT,
        "outputAllowance": OUTPUT,
        "reasoningEffort": "medium",
        "roundLimits": ROUNDS,
        "executionAdmissionSeconds": EXECUTION_SECONDS,
        "acknowledgment": ACKNOWLEDGMENT,
        "followupTemplate": FOLLOWUP,
        "noBookmarkTemplate": NO_BOOKMARK,
        "bookmarkPolicy": BOOKMARK_POLICY,
        "retentionPolicy": RETENTION,
        "acceptance": ACCEPTANCE,
        "agentInstructions": AGENT_INSTRUCTIONS,
        "model": MODEL,
        "evidenceView": "compact",
        "selectors": "handles",
        "contextAccounting": "legacy-v1",
        "sourceBackend": "exact_local_snapshot; search_explicitly_unavailable",
        "cloudDataWrites": False,
        "largerConfirmation": False,
    }


def prepare(state, prior, *, prior_manifest, prior_snapshot):
    root, previous = private_path(state), private_path(prior)
    manifest, snapshot = private_path(prior_manifest), private_path(prior_snapshot)
    require(not root.exists(), "fresh private qualification directory required")
    require(not root.is_relative_to(previous) and not previous.is_relative_to(root), "prior evidence overlaps")
    public_path = REPO / "records/granular-reference-presentation-20261105.json"
    public = read(public_path)
    require(
        sha(manifest) == public["privateEvidence"]["reviewedFinalArchive"]["manifestSha256"],
        "reviewed prior archive differs",
    )
    require(inventory(previous) == read(snapshot)["files"], "complete prior evidence inventory differs")
    archived = read(manifest)["files"]
    copies = {
        name: "live-pair/" + name
        for name in (
            "fixture/source.docx",
            "fixture/publication.json",
            "fixture/source-snapshot.json",
            "fixture/source-bindings.json",
            "capacity-policy.json",
            "model-config.json",
            "account-seed.json",
        )
    }
    copies.update(
        {
            "prior-binding-receipt.json": "live-pair/binding-after.json",
            "prior-execution-result.json": "live-pair/execution-result.json",
        }
    )
    for name in copies.values():
        require(sha(previous / name) == archived[name], "prior archive input changed: " + name)
    prior_result = read(previous / "live-pair/execution-result.json")
    require(
        prior_result["providerClosed"]
        and prior_result["postchecks"]["passed"]
        and prior_result["pair"]["completedTurns"] == 2
        and prior_result["cloudDataResourcesCreated"] == 0,
        "prior execution is not finalized",
    )
    root.mkdir(parents=True, mode=0o700)
    for destination, original in copies.items():
        target = root / destination
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes((previous / original).read_bytes())
        target.chmod(0o600)
    for destination, original in (
        ("prior-archive-manifest.json", manifest),
        ("prior-evidence-snapshot.json", snapshot),
        ("prior-public-receipt.json", public_path),
    ):
        target = root / destination
        target.write_bytes(original.read_bytes())
        target.chmod(0o600)
    _, _, source = _fixture(root)
    binding = read(root / "prior-binding-receipt.json")["binding"]
    policy = validate_policy(read(root / "capacity-policy.json"))
    require(policy["bindingSha256"] == binding["bindingSha256"], "capacity policy binding differs")
    save_new(
        root / "controls.json",
        {
            **preparation_controls(),
            "caseA": cases(source)[0],
            "modelBinding": binding,
            "pacing": policy,
            "priorRoot": str(previous),
            "codeSha256": code_hashes(),
            "inputSha256": {str(p.relative_to(root)): sha(p) for p in root.rglob("*") if p.is_file()},
        },
    )
    save_new(root / "prepared.json", {"capturedAt": now(), "controlsSha256": sha(root / "controls.json")})
    return {"prepared": True, "turns": 6, "sourceTurns": 4, "modelCalls": 0, "cloudCalls": 0}


def verify(root, *, prior_inventory=False):
    require(sha(root / "controls.json") == read(root / "prepared.json")["controlsSha256"], "sealed controls changed")
    controls = read(root / "controls.json")
    for name, expected in preparation_controls().items():
        require(
            type(controls.get(name)) is type(expected) and controls[name] == expected,
            "qualification control drift: " + name,
        )
    require(controls["codeSha256"] == code_hashes(), "qualification code drift")
    require(
        all(sha(root / name) == value for name, value in controls["inputSha256"].items()), "qualification input drift"
    )
    _, _, source = _fixture(root)
    require(controls["caseA"] == cases(source)[0], "original A question or source changed")
    require(controls["modelBinding"] == read(root / "prior-binding-receipt.json")["binding"], "model binding changed")
    require(controls["pacing"] == read(root / "capacity-policy.json"), "pacing changed")
    validate_policy(controls["pacing"])
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair remains separate")
    if prior_inventory:
        require(
            inventory(private_path(controls["priorRoot"])) == read(root / "prior-evidence-snapshot.json")["files"],
            "historical evidence changed",
        )
    return controls


def current_binding(root, role):
    controls, config = verify(root), read(root / "model-config.json")
    seed = read(root / "account-seed.json")
    base = "https://management.azure.com" + seed["id"]
    account = management_get(base + "?api-version=2024-10-01", root / f"account-{role}.json")
    deployment = management_get(
        base + "/deployments/" + config["AOAI_CHAT_DEPLOYMENT"] + "?api-version=2024-10-01",
        root / f"answering-{role}.json",
    )
    binding = deployment_binding(
        {"capturedAt": now(), "account": account, "deployment": deployment},
        configured_endpoint=config["AOAI_ENDPOINT"],
        configured_deployment=config["AOAI_CHAT_DEPLOYMENT"],
        model=MODEL["name"],
        revision=MODEL["version"],
    )
    require(binding == controls["modelBinding"], "fresh answering binding differs")
    validate_capacity(controls["pacing"], deployment, binding_sha256=binding["bindingSha256"])
    save_new(root / f"binding-{role}.json", {"capturedAt": now(), "binding": binding})
    return binding


def preflight(state):
    root = private_path(state)
    verify(root, prior_inventory=True)
    require(not (root / "preflight.json").exists(), "preflight is single-use")
    binding = current_binding(root, "preflight")
    result = {"passed": True, "capturedAt": now(), "binding": binding, "modelCalls": 0, "cloudDataWrites": 0}
    save_new(root / "preflight.json", result)
    return result


def delivered_bookmark(calls, cell):
    """Select only an actual model-delivered action; never a hidden registry value."""
    require(cell in CELLS, "unknown presentation")
    seen, deliveries = set(), []
    for model_input in reversed(calls):
        if model_input.get("status") != "returned":
            continue
        for message in reversed(model_input["messages"]):
            if message.get("role") != "tool" or not isinstance(message.get("content"), str):
                continue
            key = (message.get("tool_call_id"), message["content"])
            if key in seen:
                continue
            seen.add(key)
            payload = json.loads(message["content"])
            if not isinstance(payload, dict) or payload.get("error") or payload.get("disposition") == "budget_exceeded":
                continue
            deliveries.append((model_input["logicalCall"], message.get("tool_call_id"), payload))
    for kind in ("source", "outline"):
        for logical, call_id, payload in deliveries:
            if kind == "source" and payload.get("mode") not in {"selected", "full"}:
                continue
            if kind == "outline" and payload.get("mode") != "outline":
                continue
            for document in payload.get("documents") or []:
                collection = "sections" if kind == "source" else "outline"
                for ordinal, row in enumerate(document.get(collection) or []):
                    if kind == "source" and (
                        not row.get("text")
                        or row.get("contentKind", "text") != "text"
                        or row.get("textSource", "extracted_source_text") != "extracted_source_text"
                    ):
                        continue
                    action = "read" if kind == "source" else "outline"
                    value = (
                        (row.get("actions") or {}).get(action) if cell == "navigation" else row.get(action + "Selector")
                    )
                    if (cell == "navigation" and not isinstance(value, str)) or (
                        cell == "compact" and not isinstance(value, dict)
                    ):
                        continue
                    request = {"reference": value} if cell == "navigation" else copy.deepcopy(value)
                    return {
                        "available": True,
                        "originalSourceBookmark": kind == "source",
                        "origin": kind,
                        "focusedTargetJudgment": (
                            "pending manual source/scope review; source presence is not target success"
                        ),
                        "sourceFileName": document.get("fileName"),
                        "scope": "first source atom of the last delivered source response"
                        if kind == "source"
                        else (
                            "outline of first navigable entry in last delivered outline; focused source was unavailable"
                        ),
                        "readRequest": request,
                        "logicalCallDelivered": logical,
                        "toolCallId": call_id,
                        "rowOrdinal": ordinal,
                        "rawAction": copy.deepcopy(value),
                        "selectionUsesHiddenRegistry": False,
                    }
    return {
        "available": False,
        "originalSourceBookmark": False,
        "origin": "none",
        "scope": "no actually delivered usable bookmark",
        "readRequest": None,
        "selectionUsesHiddenRegistry": False,
    }


def followup_question(source, bookmark):
    if not bookmark["available"]:
        return NO_BOOKMARK.format(fileName=source["fileName"])
    return FOLLOWUP.format(
        fileName=source["fileName"],
        scope=bookmark["scope"],
        bookmark=json.dumps(bookmark["readRequest"], ensure_ascii=False, sort_keys=True),
    )


def run_qualification(root, *, provider, deployment, binding, deadline):
    controls = verify(root)
    require(binding == controls["modelBinding"], "runtime model binding differs")
    require(provider.max_retries == 0, "provider transport retries must be disabled")
    save_new(root / "qualification-started.json", {"controlsSha256": sha(root / "controls.json"), "startedAt": now()})
    meter, pacer = (
        Meter(root / "provider", LIMITS, context=CONTEXT, output=OUTPUT),
        Pacer(deadline, policy=controls["pacing"]),
    )
    source = controls["caseA"]["sourceBinding"]
    bindings = {"docx": source}
    results, failure, active = [], None, None
    try:
        for cell in CELLS:
            history, bookmark = [], None
            for stage in STAGES:
                active = f"{cell}/{stage}"
                verify(root)
                pacer.remaining()
                directory = root / cell / stage
                directory.mkdir(parents=True, mode=0o700)
                question = (
                    controls["caseA"]["question"]
                    if stage == "A"
                    else ACKNOWLEDGMENT
                    if stage == "acknowledgment"
                    else followup_question(source, bookmark)
                )
                save_new(
                    directory / "turn-input.json",
                    {
                        "question": question,
                        "history": history,
                        "presentation": cell,
                        "stage": stage,
                        "roundLimit": ROUNDS[stage],
                        "bookmark": bookmark if stage == "B" else None,
                    },
                )
                calls, diagnostics = [], {}
                pacer.previous, pacer.opaque = None, {}
                with local_index(root) as index:
                    before = _source_snapshot(index, bindings)
                    save_new(directory / "sources-before.json", before)
                    local_http = index.http
                    index.http = DeadlineHTTP(local_http, pacer)
                    captured = CapturedIndex(index, directory)
                    invoke = chat(
                        provider,
                        deployment,
                        meter,
                        {"run": f"{cell}-{stage}", "cell": cell, "stage": stage},
                        pacer,
                        directory,
                        calls,
                    )
                    cell_error = None
                    try:
                        turn = run_turn(
                            invoke,
                            captured,
                            {
                                "id": "reference-qualification",
                                "libraryIds": [source["libraryId"]],
                                "instructions": AGENT_INSTRUCTIONS,
                            },
                            copy.deepcopy(history),
                            question,
                            context_token_budget=CONTEXT,
                            max_rounds=ROUNDS[stage],
                            image_inputs=False,
                            presentation="navigation" if cell == "navigation" else None,
                            presentation_diagnostics=diagnostics,
                        )
                        save_new(directory / "turn.json", capture(asdict(turn)))
                    except BaseException as error:
                        cell_error = error
                        raise
                    finally:
                        save_new(directory / "presentation-diagnostics.json", capture(diagnostics))
                        index.http = local_http
                        try:
                            after = _source_snapshot(index, bindings)
                            save_new(directory / "sources-after.json", after)
                            require(before == after, "source changed during model turn")
                            save_new(directory / "source-check.json", {"passed": True})
                        except BaseException as error:
                            save_new(
                                directory / "source-check.json", {"passed": False, "errorType": type(error).__name__}
                            )
                            if cell_error is None:
                                raise
                    if cell == "navigation":
                        require(diagnostics.get("closed") is True, "navigation session did not close")
                    if stage == "A":
                        bookmark = delivered_bookmark(calls, cell)
                        save_new(root / cell / "retained-bookmark.json", bookmark)
                    history.extend(
                        [{"role": "user", "content": question}, {"role": "assistant", "content": turn.answer}]
                    )
                    save_new(directory / "retained-prose-after.json", history)
                    summary = {
                        "cell": cell,
                        "stage": stage,
                        "completedAgentTurn": True,
                        "sourceTaskSuccess": "pending manual review; semantic refusal does not skip later stages",
                        "sourceSnapshotsUnchanged": True,
                        "logicalCalls": len(calls),
                        "capturedApiOperationsIncludingDefinitions": len(captured.calls),
                        "referenceSessionClosed": diagnostics.get("closed") if cell == "navigation" else None,
                    }
                    results.append(summary)
                    save_new(directory / "summary.json", summary)
    except BaseException as error:
        failure = error
    finally:
        result = {
            "status": "failed_guard_or_runtime" if failure else "six_turns_complete_pending_source_review",
            "completedTurns": len(results),
            "completedSourceTurns": sum(row["stage"] != "acknowledgment" for row in results),
            "activeTurn": active,
            "turns": results,
            "uncompletedDeclaredTurns": [
                x for x in controls["order"] if x not in {f"{v['cell']}/{v['stage']}" for v in results}
            ],
            "provider": meter_summary(meter.path),
            "usageByPresentation": meter_by_cell(meter.path),
            "localApiContextsClosed": True,
            "cloudDataWrites": 0,
            "embeddingCalls": 0,
            "cosmosRu": None,
            "limitation": (
                "Local source snapshot navigation; explicit bookmark lifetime tradeoff, "
                "not matched-prose-only quality or cloud lifecycle/ingestion/search qualification."
            ),
        }
        if failure:
            result["errorType"] = type(failure).__name__
        save_new(root / "qualification-result.json", result)
    if failure:
        raise failure
    return result


def execute(state, archive_path):
    root, destination = private_path(state), private_path(archive_path)
    require(not destination.exists(), "fresh archive destination required")
    require(not destination.is_relative_to(root) and not root.is_relative_to(destination), "archive overlaps state")
    controls = verify(root, prior_inventory=True)
    require(
        not subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip(),
        "commit qualification first",
    )
    require(
        subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=REPO).returncode == 0,
        "wrong baseline",
    )
    health = read(root / "preflight.json")
    age = (datetime.now(UTC) - datetime.fromisoformat(health["capturedAt"])).total_seconds()
    require(
        health["passed"] and 0 <= age <= 900 and health["binding"] == controls["modelBinding"],
        "fresh matching preflight required",
    )
    save_new(
        root / "execution-started.json",
        {
            "startedAt": now(),
            "controlsSha256": sha(root / "controls.json"),
            "preflightSha256": sha(root / "preflight.json"),
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        },
    )
    deadline, result, failure, provider = time.monotonic() + EXECUTION_SECONDS, {}, None, None
    previous_metrics = os.environ.get("FLI_METRICS_FILE")
    os.environ["FLI_METRICS_FILE"] = str(root / "local-api-metrics.jsonl")
    try:
        binding = current_binding(root, "before")
        config = read(root / "model-config.json")
        provider = actual_client(config["AOAI_ENDPOINT"])
        result["qualification"] = run_qualification(
            root, provider=provider, deployment=config["AOAI_CHAT_DEPLOYMENT"], binding=binding, deadline=deadline
        )
    except BaseException as error:
        failure = error
        result.update(status="failed", errorType=type(error).__name__)
    finally:
        if provider is not None:
            try:
                provider.close()
                result["providerClosed"] = True
            except BaseException as error:
                result["providerClosed"] = False
                failure = failure or error
        if previous_metrics is None:
            os.environ.pop("FLI_METRICS_FILE", None)
        else:
            os.environ["FLI_METRICS_FILE"] = previous_metrics
        try:
            current_binding(root, "after")
            verify(root, prior_inventory=True)
            result["postchecks"] = {"passed": True, "codeSourceBindingCapacityAndPriorEvidence": "unchanged"}
        except BaseException as error:
            result["postchecks"] = {"passed": False, "errorType": type(error).__name__}
            failure = failure or error
        result.update(
            status="failed" if failure else "six_turns_complete_pending_source_review",
            cloudDataResourcesCreated=0,
            cloudDataCleanupRequired=False,
        )
        if failure:
            result.setdefault("errorType", type(failure).__name__)
        save_new(root / "execution-result.json", result)
        save_new(root / "archive-receipt.json", archive(root, destination))
    if failure:
        raise failure
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "preflight", "execute"))
    for name in ("state", "prior", "prior-manifest", "prior-snapshot", "archive"):
        parser.add_argument("--" + name, type=Path, required=name == "state")
    args = parser.parse_args()
    if args.action == "prepare":
        require(args.prior and args.prior_manifest and args.prior_snapshot, "prior archive and full snapshot required")
        result = prepare(args.state, args.prior, prior_manifest=args.prior_manifest, prior_snapshot=args.prior_snapshot)
    elif args.action == "preflight":
        result = preflight(args.state)
    else:
        require(args.archive, "archive destination required")
        result = execute(args.state, args.archive)
    print(
        json.dumps(
            {
                "status": result.get("status", "prepared"),
                "completedTurns": result.get("qualification", {}).get("completedTurns"),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
