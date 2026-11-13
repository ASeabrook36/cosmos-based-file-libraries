"""One sealed six-turn compact/surviving-source-bookmark qualification.

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
from fli_demo.conversation import CHECKPOINT, prepare_conversation, source_bookmark_turn
from fli_demo.navigation_refs import target_of
from scripts.evaluate_agent_retrieval import capture
from scripts.granular_live import archive, code_hashes
from scripts.granular_live_models import CapturedIndex, DeadlineHTTP, _source_snapshot
from scripts.granular_live_preflight import management_get
from scripts.granular_outline_case import cases
from scripts.granular_pacing import CONTEXT, MODEL, OUTPUT, Pacer, chat, validate_capacity, validate_policy
from scripts.granular_reference_live import _fixture, local_index, now, read
from scripts.granular_reference_repair_live import ACKNOWLEDGMENT, FOLLOWUP, NO_BOOKMARK
from scripts.hybrid_model_meter import Meter, actual_client, meter_by_cell, meter_summary
from scripts.qualify_granular_navigation import require, save_new, sha
from scripts.retrieval_controls import AGENT_INSTRUCTIONS, deployment_binding, private_path

REPO = Path(__file__).resolve().parents[1]
BASELINE = "90e0d90f53ffeb7be05e78fdfddb6fec0a7a9d4a"
SCHEMA = "fli.granular-source-bookmark.live.v1"
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
BOOKMARK_STATE = {
    "ttlSeconds": 86400,
    "maxEntries": 32,
    "overflow": "refuse_new_bookmark_without_evicting_existing_bindings",
    "authorityKey": "fresh_32_byte_application_key_in_memory_only",
    "lifetime": "one_trusted_application_conversation_with_signed_checkpoint_restore_each_turn_then_discard",
    "mechanism": "fli_demo.conversation.prepare_conversation_and_source_bookmark_turn",
}
BOOKMARK_POLICY = (
    "Scan successful actual model-input captures newest-first, and their tool replies newest-first. "
    "Use the latest delivered selected/full response containing original source text, in document/wire order. "
    "Navigation copies that document's actual sourceBookmark.read, preserving the whole original completed "
    "selection; compact copies the first original source row's readSelector. If no appropriate source bookmark "
    "was actually delivered, report it unavailable; do not "
    "substitute an expiring action or manufacture a target from gold source facts or hidden application state."
)
RETENTION = (
    "Both arms retain exact prior user prompts and delivered assistant prose, plus one actually "
    "model-delivered bookmark shown explicitly in B's user question. No prior tool/source payload, "
    "evidence hint ledger or raw model draft is restored outside that declared prose. Navigation retains "
    "trusted application conversation bookmark state across A/acknowledgment/B, but each public run_turn "
    "creates/closes a separate short-action session. The application resolves only the supplied delivered "
    "bookmark and still calls the normal authorized API. Compact retains its full selector. This explicit "
    "state-policy change is not a matched-prose-only or old-action revival comparison."
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
        "navigation delivered source bookmark survives the new action session and resolves exact prior scope",
        "normal current permission/source/snapshot validation on the read; no implicit target replacement",
        "authorized reacquisition after genuine failure if required; no silently repaired request",
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
        "applicationBookmarkState": BOOKMARK_STATE,
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


def unchanged_controls(prior):
    """Bind the new lifetime comparison to the preceding exact six-turn inputs."""
    expected = preparation_controls()
    for name in (
        "cells",
        "stages",
        "order",
        "meterLimits",
        "contextAllowance",
        "outputAllowance",
        "reasoningEffort",
        "roundLimits",
        "executionAdmissionSeconds",
        "acknowledgment",
        "followupTemplate",
        "noBookmarkTemplate",
        "agentInstructions",
        "model",
        "evidenceView",
        "selectors",
        "contextAccounting",
        "sourceBackend",
        "cloudDataWrites",
        "largerConfirmation",
    ):
        require(
            type(prior.get(name)) is type(expected[name]) and prior[name] == expected[name],
            "prior control differs: " + name,
        )


def prepare(state, prior, *, prior_manifest, prior_snapshot):
    root, previous = private_path(state), private_path(prior)
    manifest, snapshot = private_path(prior_manifest), private_path(prior_snapshot)
    require(not root.exists(), "fresh private qualification directory required")
    require(not root.is_relative_to(previous) and not previous.is_relative_to(root), "prior evidence overlaps")
    public_path = REPO / "records/granular-reference-contract-repair-20261105.json"
    public = read(public_path)
    require(
        sha(manifest) == public["privateEvidence"]["reviewedFinalArchive"]["manifestSha256"],
        "reviewed prior archive differs",
    )
    require(inventory(previous) == read(snapshot)["files"], "complete prior evidence inventory differs")
    archived = read(manifest)["files"]
    copies = {
        name: "live-check/" + name
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
            "prior-binding-receipt.json": "live-check/binding-after.json",
            "prior-execution-result.json": "live-check/execution-result.json",
            "prior-qualification-controls.json": "live-check/controls.json",
        }
    )
    for name in copies.values():
        require(sha(previous / name) == archived[name], "prior archive input changed: " + name)
    prior_result = read(previous / "live-check/execution-result.json")
    require(
        prior_result["providerClosed"]
        and prior_result["postchecks"]["passed"]
        and prior_result["qualification"]["completedTurns"] == 6
        and prior_result["qualification"]["completedSourceTurns"] == 4
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
    previous_controls = read(root / "prior-qualification-controls.json")
    unchanged_controls(previous_controls)
    require(previous_controls["caseA"] == cases(source)[0], "original A question/source changed")
    require(
        previous_controls["modelBinding"] == binding and previous_controls["pacing"] == policy,
        "prior model/pacing differs",
    )
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
    prior = read(root / "prior-qualification-controls.json")
    unchanged_controls(prior)
    require(prior["caseA"] == controls["caseA"], "prior source question differs")
    require(
        prior["modelBinding"] == controls["modelBinding"] and prior["pacing"] == controls["pacing"],
        "prior model/pacing differs",
    )
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
    for logical, call_id, payload in deliveries:
        if payload.get("mode") not in {"selected", "full"}:
            continue
        for document_ordinal, document in enumerate(payload.get("documents") or []):
            for ordinal, row in enumerate(document.get("sections") or []):
                if (
                    not row.get("text")
                    or row.get("contentKind", "text") != "text"
                    or row.get("textSource", "extracted_source_text") != "extracted_source_text"
                ):
                    continue
                value = (
                    (document.get("sourceBookmark") or {}).get("read")
                    if cell == "navigation"
                    else row.get("readSelector")
                )
                if not isinstance(value, dict):
                    continue
                if cell == "navigation" and (
                    set(value) != {"bookmark"} or not isinstance(value["bookmark"], str) or not value["bookmark"]
                ):
                    continue
                return {
                    "available": True,
                    "originalSourceBookmark": True,
                    "origin": "surviving_source_bookmark" if cell == "navigation" else "source_read_selector",
                    "focusedTargetJudgment": (
                        "pending manual source/scope review; source presence is not target success"
                    ),
                    "sourceFileName": document.get("fileName"),
                    "scope": "exact original completed source selection from the last delivered source response"
                    if cell == "navigation"
                    else "first source atom of the last delivered source response",
                    "readRequest": copy.deepcopy(value),
                    "logicalCallDelivered": logical,
                    "toolCallId": call_id,
                    "documentOrdinal": document_ordinal,
                    "rowOrdinal": ordinal,
                    "rawAction": copy.deepcopy(value),
                    "selectionUsesHiddenRegistry": False,
                }
    return {
        "available": False,
        "originalSourceBookmark": False,
        "origin": "none",
        "scope": "no actually delivered usable original-source bookmark",
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


def checkpoint(root, name, state):
    """Archive only the supported signed metadata checkpoint, never session state/key."""
    value = copy.deepcopy(state[CHECKPOINT])
    require(set(value) == {"schema", "payload", "signature"}, "unexpected bookmark checkpoint shape")
    payload = value["payload"]
    require(
        payload["maxEntries"] == BOOKMARK_STATE["maxEntries"]
        and payload["expiresAt"] - payload["createdAt"] == BOOKMARK_STATE["ttlSeconds"],
        "application bookmark limits changed",
    )
    save_new(root / name, value)


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
    results, failure, active, bookmark_owners, application_states = [], None, None, [], {}
    try:
        for cell in CELLS:
            history, bookmark, bookmark_state = [], None, {}
            if cell == "navigation":
                # Cleanup receipts must exist even if admission fails before
                # the first stage directory (or application owner) is created.
                (root / cell).mkdir(mode=0o700)
                application_states[cell] = bookmark_state
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
                    target = target_of(index)
                    if cell == "navigation":
                        prepare_conversation(bookmark_state, "reference-qualification", target, enabled=True)
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
                        with source_bookmark_turn(
                            bookmark_state, "reference-qualification", target, enabled=cell == "navigation"
                        ) as bookmark_arguments:
                            if cell == "navigation":
                                bookmark_owners.append((cell, stage, bookmark_arguments["bookmark_store"]))
                                checkpoint(directory, "application-bookmarks-before.json", bookmark_state)
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
                                presentation_diagnostics=diagnostics,
                                **bookmark_arguments,
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
                        if cell == "navigation" and CHECKPOINT in bookmark_state:
                            try:
                                checkpoint(directory, "application-bookmarks-after.json", bookmark_state)
                            except BaseException:
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
        bookmark_closures = []
        for cell, stage, store in bookmark_owners:
            try:
                if not store.closed:
                    store.close()
            except BaseException as error:
                failure = failure or error
            receipt = {"cell": cell, "stage": stage, "closed": store.closed, "authorityKeyPersisted": False}
            bookmark_closures.append(receipt)
            save_new(root / cell / stage / "application-bookmark-closure.json", receipt)
        for cell, state in application_states.items():
            try:
                if CHECKPOINT in state:
                    checkpoint(root / cell, "application-bookmarks-final.json", state)
            except BaseException as error:
                failure = failure or error
            finally:
                state.clear()
            save_new(
                root / cell / "application-bookmark-state-discarded.json",
                {"cleared": not state, "authorityKeyPersisted": False},
            )
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
            "applicationBookmarkClosures": bookmark_closures,
            "applicationBookmarkStateDiscarded": all(not state for state in application_states.values()),
            "cloudDataWrites": 0,
            "embeddingCalls": 0,
            "cosmosRu": None,
            "limitation": (
                "Local source snapshot navigation with explicit trusted application bookmark retention; "
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
