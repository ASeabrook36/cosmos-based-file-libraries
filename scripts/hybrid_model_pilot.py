"""Four matched exploratory model/retrieval cells; immutable, private and single-use.

This driver provisions nothing. It preserves the consumed hybrid-quality pack and
reuses the existing agent, Responses mapping, compact evidence and selector tools.
"""

from __future__ import annotations

import argparse
import copy
import inspect
import json
import os
import uuid
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import median

from fli.structure_lab import digest
from fli_demo.agent import MAX_ROUNDS, run_turn
from scripts import hybrid_quality_replay as h
from scripts.evaluate_agent_retrieval import prepare_output, replay, snapshot
from scripts.hybrid_model_meter import Meter, actual_client, meter_summary
from scripts.retrieval_controls import AGENT_INSTRUCTIONS, deployment_binding, private_path, read
from scripts.retrieval_p1 import RecordedChat
from scripts.score_retrieval_benchmark import delivered_bodies, usage
from scripts.structure_lab import save

REPO = Path(__file__).resolve().parents[1]
BASE = "8f7d436987724abac1efe633d071db66f8cbf4ea"
ROLES = ("sol", "budget")
CELLS = (("sol", "vector"), ("sol", "hybrid"), ("budget", "vector"), ("budget", "hybrid"))
MODELS = {
    "sol": {"model": "gpt-6.1-sol", "version": "2026-09-29"},
    "budget": {"model": "gpt-5.4-mini", "version": "2026-03-17"},
}
ANSWERING = {
    "contextAllowancePerRun": 64000,
    "reasoningEffort": "medium",
    "maxOutputTokens": 8000,
    "maxAttempts": 3,
    "maxToolRounds": 12,
    "maxLogicalCallsPerTurn": 13,
    "transport": "responses",
    "store": False,
    "automaticTruncation": False,
    "instructions": AGENT_INSTRUCTIONS,
}
LIMITS = {
    "logicalCalls": 2496,
    "physicalResponsesAttempts": 7488,
    "inputTokensWithUnknownReservations": 32000000,
    "outputTokensWithUnknownReservations": 2000000,
}
PROBE_LIMITS = {
    "logicalCalls": 4,
    "physicalResponsesAttempts": 12,
    "inputTokensWithUnknownReservations": 768000,
    "outputTokensWithUnknownReservations": 96000,
}
QUALITY = h.QUALITY
FINAL_FILES = {
    f"{n}.json"
    for n in (
        "controls",
        "private-config",
        "source-snapshot",
        "deployments-before",
        "deployments-after",
        "runtime-before",
        "runtime-after",
        "embedding-before",
        "embedding-after",
        "authorization",
        "preparation",
        "probe",
        "blind-map",
        "plan",
        "manifest",
        "tasks",
    )
}
require = h.require
sha = h.sha


def inputs(plan_path, manifest_path, tasks_path):
    _, manifest, tasks = h.inputs(REPO / "docs/testing/hybrid-quality/plan.json", manifest_path, tasks_path)
    plan = read(plan_path)
    require(
        plan.get("schema") == "fli.hybrid-model-pilot.plan.v1"
        and plan.get("baseCommit") == BASE
        and plan.get("packUse") == "consumed-exploratory-regression"
        and plan.get("repeats") == 1
        and plan.get("models") == MODELS
        and plan.get("answering") == ANSWERING
        and plan.get("runLimits") == LIMITS
        and plan.get("probeLimits") == PROBE_LIMITS
        and plan.get("evidenceView") == "compact"
        and plan.get("selectorMode") == "handles"
        and plan.get("sourceStrategy") == "microsoft-indexer-layout-v1"
        and plan.get("client") == {"package": "azure-cosmos", "version": "4.17.1"}
        and plan.get("manifestSha256") == sha(manifest_path)
        and plan.get("tasksSha256") == sha(tasks_path)
        and plan.get("cases") == [t["case"] for t in tasks]
        and len(tasks) == 48
        and plan.get("goldSha256") == read(REPO / "docs/testing/hybrid-quality/plan.json")["goldSha256"],
        "new pilot controls differ from the prospective freeze",
    )
    require(
        MAX_ROUNDS == inspect.signature(run_turn).parameters["max_rounds"].default == 12, "tool-round limit changed"
    )
    require(version("azure-cosmos") == "4.17.1" and version("openai") == "3.37.0", "measured SDK versions differ")
    return plan, manifest, tasks


def schedule(tasks):
    # Balanced four-period Williams design: each cell occupies each position and
    # each ordered within-question predecessor pair equally over 48 questions.
    pattern = (0, 1, 3, 2)
    return [
        {
            "repeat": 1,
            "modelRole": CELLS[cell][0],
            "arm": CELLS[cell][1],
            "case": task["case"],
            "cell": "-".join(CELLS[cell]),
            "run": f"r1-{task['case']}-{'-'.join(CELLS[cell])}",
        }
        for ordinal, task in enumerate(tasks)
        for cell in ((p + ordinal % 4) % 4 for p in pattern)
    ]


def api_config(config):
    return {"model": config["models"]["sol"], **{k: config[k] for k in ("agent", "libraryId", "indexUrls")}}


def validate_config(config):
    require(set(config) == {"models", "agent", "libraryId", "indexUrls"}, "unexpected pilot configuration")
    require(set(config["models"]) == set(ROLES), "both pinned model roles required")
    for role in ROLES:
        value = config["models"][role]
        require(set(value) == {"model", "version", "deployment"}, "model overrides forbidden")
        require({k: value[k] for k in ("model", "version")} == MODELS[role], "model identity differs")
        require(isinstance(value["deployment"], str) and value["deployment"], "deployment alias required")
    require(config["models"]["sol"]["deployment"] != config["models"]["budget"]["deployment"], "model aliases collide")
    h.validate_config(api_config(config))


def model_bindings(receipts, config, endpoint, *, fresh=True):
    require(set(receipts) == set(ROLES), "both actual model deployment captures required")
    bindings = {
        role: deployment_binding(
            receipts[role],
            configured_endpoint=endpoint,
            configured_deployment=config["models"][role]["deployment"],
            model=MODELS[role]["model"],
            revision=MODELS[role]["version"],
            now=None if fresh else datetime.fromisoformat(receipts[role]["capturedAt"]),
        )
        for role in ROLES
    }
    require(
        all(v["sku"].get("name") == "GlobalStandard" and v["sku"].get("capacity") == 200 for v in bindings.values()),
        "matched 200k TPM deployment capacity differs",
    )
    return bindings


def preparation_gate(path, baseline, runtimes, embedding, plan, *, fresh=True):
    receipt = read(private_path(path))
    if fresh:
        h.recent(receipt["capturedAt"], 3600)
    require(
        receipt.get("schema") == "fli.hybrid-model-pilot.preparation.v1"
        and receipt.get("status") == "accepted"
        and receipt.get("manifestSha256") == plan["manifestSha256"]
        and receipt.get("sourceStrategy") == plan["sourceStrategy"]
        and receipt.get("sourceSnapshotSha256")
        and receipt.get("embeddingDeploymentBinding") == embedding
        and receipt.get("sourceOriginalsVerified") is True
        and receipt.get("allSourcesCommittedAndObservedAttemptsTerminal") is True
        and receipt.get("hybridWhereOnlyRanksMeaningfully") is True
        and receipt.get("protectedScopesUnchanged") is True,
        "fresh preparation acceptance incomplete",
    )
    source_path = private_path(receipt["sourceSnapshotPath"])
    require(sha(source_path) == receipt["sourceSnapshotFileSha256"], "preparation source bytes changed")
    source = read(source_path)
    require(digest(source) == receipt["sourceSnapshotSha256"], "preparation source digest differs")
    require(
        h.capture({k: source[k] for k in ("library", "documents", "tools")})
        == next(iter(baseline["libraries"].values())),
        "preparation and answering sources differ",
    )
    require(all(v["storedRowsSha256"] == digest(source["chunks"]) for v in runtimes.values()), "stored rows differ")
    refs = receipt.get("evidenceReferences", [])
    require(refs, "preparation evidence references required")
    for ref in refs:
        require(sha(private_path(ref["path"])) == ref["sha256"], "preparation evidence changed")
    return digest(receipt)


def probe_gate(path, model_bindings_value, config):
    p = private_path(path)
    result = read(p)
    require(
        result.get("schema") == "fli.hybrid-model-pilot.tool-probe.v1"
        and result.get("status") == "complete"
        and result.get("modelBindings") == model_bindings_value
        and result.get("configSha256") == digest(config)
        and result.get("logicalCalls") == 4
        and result.get("reasoningContinuityVerified") is True
        and result.get("sameToolSchema") is True,
        "both models need the separate matched four-call tool probe",
    )
    evidence = private_path(result["evidenceDirectory"])
    require(result["artifactsSha256"], "probe raw artifact inventory missing")
    for name, expected in result["artifactsSha256"].items():
        candidate = (evidence / name).resolve()
        require(candidate.is_relative_to(evidence), "probe artifact escapes evidence root")
        require(sha(candidate) == expected, "tool probe evidence changed")
    return sha(p)


def prepare(plan_path, manifest_path, tasks_path, config, deployments, runtimes, preparation, probe, output, embedding):
    plan, manifest, tasks = inputs(plan_path, manifest_path, tasks_path)
    validate_config(config)
    bindings = h.runtime_bindings(runtimes, api_config(config))
    endpoint = runtimes["vector"]["settingsBinding"]["AOAI_ENDPOINT"]
    require(os.environ.get("AOAI_ENDPOINT") == endpoint, "actual answering endpoint differs")
    models = model_bindings(deployments, config, endpoint)
    embedded = h.embedding_binding(embedding, runtimes)
    indexes = {arm: h.make_index(api_config(config), arm) for arm in h.ARMS}
    try:
        agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
        baseline = snapshot(indexes["vector"], agent)
        h.validate_snapshot(baseline, config, manifest)
        require(snapshot(indexes["hybrid"], agent) == baseline, "source/tool context differs across routes")
    finally:
        for index in indexes.values():
            index.http.close()
    preparation_gate(preparation, baseline, bindings, embedded, plan)
    probe_hash = probe_gate(probe, models, config)
    return {
        "plan": plan,
        "manifest": manifest,
        "tasks": tasks,
        "baseline": baseline,
        "runtimeBindings": bindings,
        "modelBindings": models,
        "embeddingBinding": embedded,
        "authorizationTemplate": {
            "schema": "fli.hybrid-model-pilot.authorization.v1",
            "status": "requires_coordinator_review",
            "planSha256": sha(plan_path),
            "preparationSha256": sha(preparation),
            "probeSha256": probe_hash,
            "sourceSnapshotSha256": digest(baseline),
            "runtimeBindingsSha256": digest(bindings),
            "modelBindingsSha256": digest(models),
            "embeddingBindingSha256": digest(embedded),
            "codeSha256": digest(h.hashes()),
            "outputDirectory": str(private_path(output)),
            "executionLedger": None,
            "reviewedAt": None,
            "withinExistingResourceAuthorization": False,
            "exclusiveMeasurementWindow": False,
            "consumedPackAcknowledged": False,
            "noRepresentationChanges": False,
        },
    }


def authorize(authorization, template, *, fresh=True):
    if fresh:
        h.recent(authorization["reviewedAt"], 900)
    for key, value in template.items():
        if key not in {
            "status",
            "executionLedger",
            "reviewedAt",
            "withinExistingResourceAuthorization",
            "exclusiveMeasurementWindow",
            "consumedPackAcknowledged",
            "noRepresentationChanges",
        }:
            require(authorization.get(key) == value, "coordinator authorization binding differs")
    require(authorization.get("status") == "accepted", "coordinator acceptance required")
    for key in (
        "withinExistingResourceAuthorization",
        "exclusiveMeasurementWindow",
        "consumedPackAcknowledged",
        "noRepresentationChanges",
    ):
        require(authorization.get(key) is True, "coordinator gate incomplete")
    ledger = private_path(authorization["executionLedger"])
    require(not ledger.is_relative_to(private_path(template["outputDirectory"])), "ledger must remain outside run")
    return ledger


def verify_cell(directory, case, baseline):
    before = read(directory / "source-snapshot.json")
    check, status = read(directory / "snapshot-check.json"), read(directory / "run-status.json")
    rows = read(directory / "results.json")
    require(
        read(directory / "cases.json") == [case]
        and before == baseline == check.get("before") == check.get("after")
        and check.get("unchanged") is True
        and status == {"status": "complete", "completedTurns": 1, "budget": 64000}
        and len(rows) == 1
        and rows[0]["case"] == case["case"]
        and rows[0]["question"] == case["question"]
        and rows[0]["turn"] == 1,
        "incomplete or mismatched pilot cell",
    )
    calls = rows[0]["metrics"]["modelCalls"]
    require(0 < len(calls) <= 13, "logical turn limit exceeded or no provider answer captured")
    require(
        all(type(c.get("promptTokens")) is int and 0 <= c["promptTokens"] <= 64000 for c in calls),
        "provider input exceeds matched allowance or is unknown",
    )
    require(
        all(type(c.get("completionTokens")) is int and 0 <= c["completionTokens"] <= 8000 for c in calls),
        "provider output exceeds matched allowance or is unknown",
    )


def run(
    plan_path,
    manifest_path,
    tasks_path,
    config,
    deployments,
    runtimes,
    preparation,
    probe,
    authorization,
    output,
    *,
    embedding,
):
    prepared = prepare(
        plan_path, manifest_path, tasks_path, config, deployments, runtimes, preparation, probe, output, embedding
    )
    ledger = authorize(authorization, prepared["authorizationTemplate"])
    root = prepare_output(private_path(output))
    root.chmod(0o700)
    matrix = schedule(prepared["tasks"])
    blind = [{**member, "reviewId": uuid.uuid4().hex} for member in matrix]
    controls = {
        "schema": "fli.hybrid-model-pilot.controls.v1",
        "plan": prepared["plan"],
        "planSha256": sha(plan_path),
        "codeSha256": h.hashes(),
        "runtimeBindings": prepared["runtimeBindings"],
        "modelBindings": prepared["modelBindings"],
        "embeddingBinding": prepared["embeddingBinding"],
        "schedule": matrix,
        "blindMapSha256": digest(blind),
        "authorizationTemplate": prepared["authorizationTemplate"],
    }
    for name, value in (
        ("controls", controls),
        ("private-config", config),
        ("source-snapshot", prepared["baseline"]),
        ("deployments-before", deployments),
        ("embedding-before", embedding),
        ("runtime-before", runtimes),
        ("authorization", authorization),
        ("blind-map", blind),
    ):
        save(root / f"{name}.json", value)
    for name, path in (
        ("plan", plan_path),
        ("manifest", manifest_path),
        ("tasks", tasks_path),
        ("preparation", preparation),
        ("probe", probe),
    ):
        (root / f"{name}.json").write_bytes(Path(path).read_bytes())
        (root / f"{name}.json").chmod(0o600)
    ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with ledger.open("x") as f:
        json.dump(
            {"planSha256": sha(plan_path), "outputDirectory": str(root), "claimedAt": datetime.now(UTC).isoformat()}, f
        )
    ledger.chmod(0o600)
    meter = Meter(root / "provider", LIMITS)
    client, indexes = None, {}
    cases = h.answering_cases(prepared["tasks"])
    agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
    completed, active = 0, None
    try:
        client = actual_client(os.environ["AOAI_ENDPOINT"])
        for arm in h.ARMS:
            indexes[arm] = h.make_index(api_config(config), arm)
        for active in matrix:
            require(h.hashes() == controls["codeSha256"], "code changed before turn")
            index = indexes[active["arm"]]
            require(snapshot(index, agent) == prepared["baseline"], "source changed before turn")
            wire = root / ("wire-" + active["run"])
            wire.mkdir(mode=0o700)
            bounded = meter.client(client, active, config["models"][active["modelRole"]]["deployment"])
            chat = RecordedChat(bounded, wire, "selectors-only")
            replay(index, chat, agent, [cases[active["case"]]], root / active["run"], budget=64000)
            verify_cell(root / active["run"], cases[active["case"]], prepared["baseline"])
            require(h.hashes() == controls["codeSha256"], "code changed after turn")
            completed += 1
            save(root / "run-status.json", {"status": "running", "completedTurns": completed, "exploratory": True})
    except BaseException as error:
        save(
            root / "run-status.json",
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                "completedTurns": completed,
                "activeRun": active,
                "errorType": type(error).__name__,
                "exploratory": True,
                "providerMeter": meter_summary(meter.path),
                "limitation": (
                    "Whole schedule stops; partial artifacts remain. No resume, selective rerun or raised ceiling."
                ),
            },
        )
        raise
    finally:
        if client is not None:
            client.close()
        for index in indexes.values():
            index.http.close()
    status = {
        "status": "pending_recheck",
        "completedTurns": completed,
        "exploratory": True,
        "completedAt": datetime.now(UTC).isoformat(),
    }
    save(root / "run-status.json", status)
    return status


def verify_execution(root):
    root = private_path(root)
    plan, manifest, tasks = inputs(root / "plan.json", root / "manifest.json", root / "tasks.json")
    controls, config, baseline = (
        read(root / f"{name}.json") for name in ("controls", "private-config", "source-snapshot")
    )
    validate_config(config)
    require(
        controls["plan"] == plan
        and controls["planSha256"] == sha(root / "plan.json")
        and controls["schedule"] == schedule(tasks)
        and controls["codeSha256"] == h.hashes(),
        "execution drift",
    )
    h.validate_snapshot(baseline, config, manifest)
    runtime = read(root / "runtime-before.json")
    bindings = h.runtime_bindings(runtime, api_config(config), fresh=False)
    models = model_bindings(
        read(root / "deployments-before.json"),
        config,
        runtime["vector"]["settingsBinding"]["AOAI_ENDPOINT"],
        fresh=False,
    )
    embedding = h.embedding_binding(read(root / "embedding-before.json"), runtime, fresh=False)
    require(
        bindings == controls["runtimeBindings"]
        and models == controls["modelBindings"]
        and embedding == controls["embeddingBinding"],
        "saved runtime/model binding differs",
    )
    preparation_gate(root / "preparation.json", baseline, bindings, embedding, plan, fresh=False)
    require(
        probe_gate(root / "probe.json", models, config) == controls["authorizationTemplate"]["probeSha256"],
        "probe receipt drift",
    )
    template = controls["authorizationTemplate"]
    require(
        template["planSha256"] == controls["planSha256"]
        and template["preparationSha256"] == sha(root / "preparation.json")
        and template["sourceSnapshotSha256"] == digest(baseline)
        and template["runtimeBindingsSha256"] == digest(bindings)
        and template["modelBindingsSha256"] == digest(models)
        and template["embeddingBindingSha256"] == digest(embedding)
        and template["codeSha256"] == digest(h.hashes())
        and private_path(template["outputDirectory"]) == root,
        "authorization evidence differs",
    )
    auth = read(root / "authorization.json")
    ledger = read(authorize(auth, controls["authorizationTemplate"], fresh=False))
    require(
        ledger["planSha256"] == controls["planSha256"] and private_path(ledger["outputDirectory"]) == root,
        "single-use execution ledger changed",
    )
    blind = read(root / "blind-map.json")
    require(
        digest(blind) == controls["blindMapSha256"]
        and len({r["reviewId"] for r in blind}) == len(blind)
        and [{k: row[k] for k in schedule(tasks)[0]} for row in blind] == schedule(tasks),
        "blind map differs",
    )
    system_hashes, cases = set(), h.answering_cases(tasks)
    for member in controls["schedule"]:
        verify_cell(root / member["run"], cases[member["case"]], baseline)
        result, _, receipts = h.wire_result(root, member)
        for ordinal in range(1, len(receipts) + 1):
            wire = read(root / ("wire-" + member["run"]) / f"wire-{ordinal:03}.json")
            system = [m["content"] for m in wire["messages"] if m.get("role") == "system"]
            require(len(system) == 1 and system[0].startswith(AGENT_INSTRUCTIONS), "system context differs")
            system_hashes.add(digest(system))
            tools = baseline["libraries"][config["libraryId"]]["tools"]
            require(
                wire["tools"] == tools
                or (
                    ordinal == len(receipts)
                    and wire["tools"] == []
                    and wire["messages"][-1].get("content", "").startswith("Tool-round limit reached.")
                ),
                "actual tool definitions differ",
            )
        require(len(result["metrics"]["modelCalls"]) <= ANSWERING["maxLogicalCallsPerTurn"], "turn call cap")
    require(len(system_hashes) == 1, "system context varied across cells")
    meter = meter_summary(root / "provider/ledger.json")
    successful = sum(len(read(root / m["run"] / "results.json")[0]["metrics"]["modelCalls"]) for m in schedule(tasks))
    require(meter["logicalCallsStarted"] == successful, "logical meter and successful matrix differ")
    verify_provider(root, controls, config)
    return controls, config, baseline, manifest, tasks


def finalize(root, deployments, runtimes, embedding):
    root = private_path(root)
    controls, config, baseline, _, _ = verify_execution(root)
    status = read(root / "run-status.json")
    require(status.get("status") == "pending_recheck" and status.get("completedTurns") == 192, "matrix incomplete")
    done = datetime.fromisoformat(status["completedAt"])
    require(
        all(
            datetime.fromisoformat(r["capturedAt"]) >= done
            for r in [*deployments.values(), *runtimes.values(), embedding]
        ),
        "post-run captures predate completion",
    )
    require(h.runtime_bindings(runtimes, api_config(config)) == controls["runtimeBindings"], "runtime changed")
    endpoint = runtimes["vector"]["settingsBinding"]["AOAI_ENDPOINT"]
    require(model_bindings(deployments, config, endpoint) == controls["modelBindings"], "model deployment changed")
    require(h.embedding_binding(embedding, runtimes) == controls["embeddingBinding"], "embedding deployment changed")
    agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
    for arm in h.ARMS:
        index = h.make_index(api_config(config), arm)
        try:
            require(snapshot(index, agent) == baseline, "source changed after matrix")
        finally:
            index.http.close()
    for name, value in (
        ("deployments-after", deployments),
        ("runtime-after", runtimes),
        ("embedding-after", embedding),
    ):
        save(root / f"{name}.json", value)
    save(
        root / "finalization.json",
        {
            "artifactsSha256": {n: sha(root / n) for n in h.artifact_inventory(root)},
            "completedAt": status["completedAt"],
        },
    )
    result = {"status": "complete", "completedTurns": 192, "exploratory": True, "sourceCodeModelRuntimeUnchanged": True}
    save(root / "run-status.json", result)
    return result


def finalized(root):
    root = private_path(root)
    values = verify_execution(root)
    require(
        read(root / "run-status.json")
        == {"status": "complete", "completedTurns": 192, "exploratory": True, "sourceCodeModelRuntimeUnchanged": True},
        "run not finalized",
    )
    final = read(root / "finalization.json")
    require(
        set(final["artifactsSha256"]) == set(h.artifact_inventory(root))
        and FINAL_FILES <= set(final["artifactsSha256"]),
        "final artifact inventory incomplete",
    )
    for path, expected in final["artifactsSha256"].items():
        require(sha(root / path) == expected, "finalized evidence changed")
    controls, config = values[:2]
    runtime, deployments = read(root / "runtime-after.json"), read(root / "deployments-after.json")
    embedding = read(root / "embedding-after.json")
    done = datetime.fromisoformat(final["completedAt"])
    require(
        all(
            datetime.fromisoformat(r["capturedAt"]) >= done
            for r in [*runtime.values(), *deployments.values(), embedding]
        ),
        "saved post-run captures predate completion",
    )
    require(
        h.runtime_bindings(runtime, api_config(config), fresh=False) == controls["runtimeBindings"],
        "final runtime drift",
    )
    require(
        model_bindings(deployments, config, runtime["vector"]["settingsBinding"]["AOAI_ENDPOINT"], fresh=False)
        == controls["modelBindings"],
        "final model drift",
    )
    require(
        h.embedding_binding(embedding, runtime, fresh=False) == controls["embeddingBinding"], "final embedding drift"
    )
    return values


def review_packets(root, gold_path, output):
    root = private_path(root)
    controls, _, _, manifest, tasks = finalized(root)
    gold = h.gold_check(gold_path, controls["plan"], manifest, tasks)
    output = private_path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    task_map = {t["case"]: t for t in tasks}
    for member in sorted(read(root / "blind-map.json"), key=lambda r: r["reviewId"]):
        result, evidence, _ = h.wire_result(root, member)
        task = task_map[member["case"]]
        regions = [r["id"] for f in task["sourceFacts"] for r in f["regions"]]
        save(
            output / (member["reviewId"] + ".json"),
            {
                "reviewId": member["reviewId"],
                "case": task["case"],
                "question": task["question"],
                "answerable": task["answerable"],
                "answer": result["answer"],
                "evidence": h.blinded_evidence(evidence),
                "originalSourceRegions": {k: gold["regions"][k] for k in regions},
                "requiredFacts": {k: gold["facts"][k] for k in task["requiredFactIds"]},
                "goldTask": gold.get("tasks", {}).get(task["case"]),
                "reviewTemplate": {
                    "reviewId": member["reviewId"],
                    **dict.fromkeys(QUALITY),
                    **{
                        k: []
                        for k in (
                            "deliveredRegionIds",
                            "supportedFactIds",
                            "fullReadRegionIds",
                            "searchRegionIds",
                            "selectedReadRegionIds",
                            "blankRegionIdsVerified",
                        )
                    },
                },
            },
        )
    return {"status": "complete", "packets": 192, "modelAndArmLabelsHidden": True}


def trace_counts(result):
    replies = result.get("toolReplies", [])
    searches = [r for r in replies if r.get("mode") == "evidence_search"]
    return {
        "explicitEvidenceSearchReplies": len(searches),
        "additionalEvidenceSearchReplies": max(0, len(searches) - 1),
        "automaticDocumentPassageReplies": sum(
            r.get("automaticRecovery") == "document_scoped_passages" for r in replies
        ),
        "replyModes": dict(Counter(r.get("mode", "unspecified") for r in replies)),
        "remainingContextWithheldReplies": sum(r.get("error") == "remaining_context_budget_exceeded" for r in replies),
        "scope": "Additional replies are observed activity, not proof that each search causally recovered a miss.",
    }


def paired(rows):
    lookup = {(r["case"], r["modelRole"], r["arm"]): r for r in rows}
    require(len(lookup) == len(rows) == 192, "reviewed matrix incomplete")
    comparisons = []
    metrics = (*QUALITY, "requiredRegionRecall", "requiredFactCoverage")
    for case in sorted({r["case"] for r in rows}):
        for role in ROLES:
            a, b = lookup[case, role, "vector"], lookup[case, role, "hybrid"]
            comparisons.append(
                {
                    "case": case,
                    "modelRole": role,
                    "answerable": a["answerable"],
                    "families": a["families"],
                    "comparisons": {
                        key: "unavailable"
                        if a[key] is None or b[key] is None
                        else "hybrid_win"
                        if b[key] > a[key]
                        else "vector_win"
                        if a[key] > b[key]
                        else "tie"
                        for key in metrics
                    },
                }
            )
    by_cell = {}
    for role, arm in CELLS:
        selected = [r for r in rows if r["modelRole"] == role and r["arm"] == arm]
        by_cell[f"{role}-{arm}"] = {
            "turns": len(selected),
            "strata": {
                stratum: {
                    "turns": len(group),
                    "quality": {
                        key: {
                            "passed": sum(r[key] is True for r in group),
                            "failed": sum(r[key] is False for r in group),
                            "unavailableOrInapplicable": sum(r[key] is None for r in group),
                        }
                        for key in QUALITY
                    },
                    "coverage": {
                        key: {"applicableTurns": len(values), "mean": sum(values) / len(values) if values else None}
                        for key in ("requiredRegionRecall", "requiredFactCoverage")
                        for values in [[r[key] for r in group if r[key] is not None]]
                    },
                }
                for stratum, flag in (("answerable", True), ("missingValue", False))
                for group in [[r for r in selected if r["answerable"] == flag]]
            },
        }
    families = {
        family: {
            role: {
                "uniqueQuestions": len(group),
                "comparisons": {key: dict(Counter(p["comparisons"][key] for p in group)) for key in metrics},
            }
            for role in ROLES
            for group in [
                [p for p in comparisons if p["modelRole"] == role and (family == "all" or family in p["families"])]
            ]
        }
        for family in sorted({"all", *(f for r in rows for f in r["families"])})
    }
    interaction = []
    for case in sorted({r["case"] for r in rows}):
        metrics_by_case = {}
        for metric in metrics:
            values = {(role, arm): lookup[case, role, arm][metric] for role, arm in CELLS}
            if any(v is None for v in values.values()):
                metrics_by_case[metric] = None
            else:
                sol_gain = values["sol", "hybrid"] - values["sol", "vector"]
                budget_gain = values["budget", "hybrid"] - values["budget", "vector"]
                metrics_by_case[metric] = {
                    "solHybridGain": sol_gain,
                    "budgetHybridGain": budget_gain,
                    "budgetMinusSolGain": budget_gain - sol_gain,
                }
        interaction.append({"case": case, "metrics": metrics_by_case})
    interaction_summary = {
        metric: {
            "applicableQuestions": len(values),
            "meanBudgetMinusSolHybridGain": sum(values) / len(values) if values else None,
            "largerBudgetGainQuestions": sum(v > 0 for v in values),
            "largerSolGainQuestions": sum(v < 0 for v in values),
            "equalGainQuestions": sum(v == 0 for v in values),
        }
        for metric in metrics
        for values in [
            [r["metrics"][metric]["budgetMinusSolGain"] for r in interaction if r["metrics"][metric] is not None]
        ]
    }
    return {
        "pairs": comparisons,
        "byCell": by_cell,
        "families": families,
        "retrievalModelInteractionByQuestion": interaction,
        "retrievalModelInteraction": interaction_summary,
        "usageByCell": {
            "-".join(cell): summarize_usage([r for r in rows if (r["modelRole"], r["arm"]) == cell]) for cell in CELLS
        },
    }


def summarize_usage(rows):
    keys = (
        "promptTokens",
        "completionTokens",
        "modelCalls",
        "retrievalRequestCharge",
        "retrievalMs",
        "totalMs",
        "providerMs",
        "rateLimitWaitMs",
        "rateLimitRetries",
        "elapsedMinusRecordedRateWaitMs",
        "cachedPromptTokens",
        "reasoningTokens",
        "toolAttempts",
        "toolErrors",
        "toolBudgetRefusals",
    )
    result = {}
    for key in keys:
        values = [r["usage"].get(key) for r in rows]
        complete = all(v is not None for v in values)
        if key == "retrievalRequestCharge":
            complete = complete and all(r["usage"].get("retrievalChargeComplete") is True for r in rows)
        known = [v for v in values if v is not None]
        result[key] = {
            "completeTotal": sum(known) if complete else None,
            "knownSubtotal": sum(known),
            "knownTurns": len(known),
            "totalTurns": len(rows),
            "medianKnownTurn": median(known) if known else None,
        }
    result["sourceActivity"] = {
        key: sum(r["sourceActivity"][key] for r in rows)
        for key in (
            "explicitEvidenceSearchReplies",
            "additionalEvidenceSearchReplies",
            "automaticDocumentPassageReplies",
            "remainingContextWithheldReplies",
        )
    }
    return result


def verify_provider(root, controls, config):
    """Reconcile every physical request/response with logical calls and replay usage."""
    provider = root / "provider"
    state = read(provider / "ledger.json")
    require(
        state["limits"] == LIMITS and state["contextAllowance"] == 64000 and state["outputAllowance"] == 8000,
        "provider meter controls differ",
    )
    logical_cursor = 0
    for member in controls["schedule"]:
        metrics = read(root / member["run"] / "results.json")[0]["metrics"]["modelCalls"]
        for measured in metrics:
            logical_cursor += 1
            logical = state["logicalCalls"][logical_cursor - 1]
            require(logical == {"logicalCall": logical_cursor, **member}, "provider turn/order binding differs")
            attempts = [r for r in state["calls"] if r["logicalCall"] == logical_cursor]
            require(
                1 <= len(attempts) <= 3 and attempts[-1]["status"] == "response", "physical attempt sequence incomplete"
            )
            require(
                all(r["status"] == "error" and r.get("httpStatus") == 429 for r in attempts[:-1]),
                "unexpected retried failure",
            )
            for row in attempts:
                request = read(provider / f"request-{row['attempt']:05}.json")
                require(
                    request.get("model") == config["models"][member["modelRole"]]["deployment"]
                    and request.get("reasoning") == {"effort": "medium"}
                    and request.get("max_output_tokens") == 8000
                    and request.get("store") is False
                    and request.get("truncation") == "disabled"
                    and request.get("include") == ["reasoning.encrypted_content"]
                    and "previous_response_id" not in request,
                    "saved physical request controls differ",
                )
            response = read(provider / f"response-{attempts[-1]['attempt']:05}.json")
            require(response.get("status") == "completed", "accepted provider response incomplete")
            ip, op = response["usage"]["input_tokens"], response["usage"]["output_tokens"]
            require(
                ip == measured["promptTokens"] == attempts[-1]["knownInputTokens"]
                and op == measured["completionTokens"] == attempts[-1]["knownOutputTokens"],
                "provider/replay token meters disagree",
            )
    require(logical_cursor == len(state["logicalCalls"]), "extra provider logical calls")


def score(root, gold_path, reviews):
    root = private_path(root)
    controls, _, _, manifest, tasks = finalized(root)
    gold = h.gold_check(gold_path, controls["plan"], manifest, tasks)
    blind = read(root / "blind-map.json")
    mapping = {r["reviewId"]: r for r in reviews}
    require(len(mapping) == len(reviews) and set(mapping) == {r["reviewId"] for r in blind}, "all reviews exactly once")
    task_map, rows = {t["case"]: t for t in tasks}, []
    for member in blind:
        task, review = task_map[member["case"]], mapping[member["reviewId"]]
        h.validate_review(review, task, gold)
        result, evidence, _ = h.wire_result(root, member)
        channels = {
            "deliveredRegionIds": evidence,
            "fullReadRegionIds": [
                r
                for r in evidence
                if r.get("mode") == "full" and r.get("complete") is True and r.get("disposition") != "budget_exceeded"
            ],
            "searchRegionIds": [
                r
                for r in evidence
                if r.get("mode") == "evidence_search" or r.get("automaticRecovery") == "document_scoped_passages"
            ],
            "selectedReadRegionIds": [
                r for r in evidence if r.get("mode") == "selected" and not r.get("automaticRecovery")
            ],
        }
        for field, bodies in channels.items():
            files = delivered_bodies(bodies)
            require(
                all(gold["regions"][region]["fileName"] in files for region in review[field]),
                "invalid channel attribution",
            )
        required, facts = set(task["requiredRegionIds"]), set(task["requiredFactIds"])
        u = usage(result)
        elapsed, wait = u["totalMs"], u["rateLimitWaitMs"]
        u["elapsedMinusRecordedRateWaitMs"] = (
            elapsed - wait if elapsed is not None and wait is not None and elapsed >= wait else None
        )
        rows.append(
            {k: member[k] for k in ("case", "modelRole", "arm", "cell", "repeat")}
            | {
                "families": task["families"],
                "answerable": task["answerable"],
                **{k: review[k] for k in QUALITY},
                **{k: sorted(review[k]) for k in (*channels, "supportedFactIds")},
                "requiredRegionRecall": len(review["deliveredRegionIds"]) / len(required) if required else None,
                "requiredFactCoverage": len(review["supportedFactIds"]) / len(facts) if facts else None,
                "allRequiredRegionsDelivered": required <= set(review["deliveredRegionIds"]) if required else None,
                "fullReadOnlyRegions": sorted(
                    set(review["fullReadRegionIds"])
                    - set(review["searchRegionIds"])
                    - set(review["selectedReadRegionIds"])
                ),
                "usage": u,
                "sourceActivity": trace_counts(result),
                "sourceReviewSha256": digest(review),
            }
        )
    return {
        "schema": "fli.hybrid-model-pilot.scorecard.v1",
        "status": "complete",
        "exploratory": True,
        "uniqueQuestions": 48,
        "reviewedTurns": 192,
        "planSha256": controls["planSha256"],
        "finalizationSha256": sha(root / "finalization.json"),
        "models": MODELS,
        "commonControls": ANSWERING,
        "rows": rows,
        **paired(rows),
        "providerMeter": meter_summary(root / "provider/ledger.json"),
        "limitations": [
            "Consumed purposeful DOCX/procurement pack; exploratory evidence, not fresh confirmation.",
            "One observation per question/cell; model/retrieval interaction is descriptive, not a broad causal claim.",
            "Microsoft one-canonical-section representation with improved selectors, not full custom hierarchy.",
            "Assistant source review; explicit labels hidden but model identity may be inferred from style.",
            "Latency excluding recorded rate waits still includes retries, backend scheduling and local tool work.",
            "Unknown provider attempts/partial RU remain unavailable; reservations are not usage or billed cost.",
            "No direct-recall claim is imported from the previous deleted library.",
        ],
    }


PROBE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "pilot_echo",
            "description": "Return supplied values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "selectors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["value"],
                "additionalProperties": False,
            },
            "strict": False,
        },
    }
]


def probe(config, deployments, output):
    validate_config(config)
    endpoint = os.environ["AOAI_ENDPOINT"]
    bindings = model_bindings(deployments, config, endpoint)
    root = prepare_output(private_path(output))
    root.chmod(0o700)
    meter = Meter(root / "provider", PROBE_LIMITS)
    client = actual_client(endpoint)
    records = []
    try:
        for role in ROLES:
            chat = meter.client(
                client, {"modelRole": role, "run": "tool-support-" + role}, config["models"][role]["deployment"]
            )
            messages = [
                {
                    "role": "system",
                    "content": "This is a tool-interface check, not a document question. "
                    "First call pilot_echo exactly once with value READY and selectors [{id: section-1}]. "
                    "After its output, answer exactly READY and make no further tool call.",
                },
                {"role": "user", "content": "Check the tool interface."},
            ]
            first = chat(messages, copy.deepcopy(PROBE_TOOLS))
            require(
                len(first.tool_calls) == 1 and first.tool_calls[0].name == "pilot_echo", "tool probe first call failed"
            )
            call = first.tool_calls[0]
            require(
                json.loads(call.arguments) == {"value": "READY", "selectors": [{"id": "section-1"}]},
                "probe arguments differ",
            )
            messages += [
                {
                    "role": "assistant",
                    "content": first.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call.id, "content": '{"value":"READY"}'},
            ]
            second = chat(messages, copy.deepcopy(PROBE_TOOLS))
            require(
                not second.tool_calls and (second.content or "").strip() == "READY", "tool result continuation failed"
            )
            # Existing adapter carries actual reasoning items back verbatim; require
            # every returned item to occur in the second physical request input.
            attempts = [r for r in meter.state["calls"] if r["modelRole"] == role and r["status"] == "response"]
            require(len(attempts) == 2, "probe successful attempt matrix differs")
            response = read(meter.root / f"response-{attempts[0]['attempt']:05}.json")
            following = read(meter.root / f"request-{attempts[1]['attempt']:05}.json")
            for item in response.get("output", []):
                require(item in following["input"], "actual Responses output/continuity item lost")
            records.append(
                {
                    "modelRole": role,
                    "logicalCalls": 2,
                    "toolArgumentsPassed": True,
                    "continuationPassed": True,
                    "reasoningItems": sum(i.get("type") == "reasoning" for i in response.get("output", [])),
                }
            )
    except BaseException as error:
        save(root / "run-status.json", {"status": "failed", "errorType": type(error).__name__, "records": records})
        raise
    finally:
        client.close()
    save(root / "run-status.json", {"status": "complete", "logicalCalls": 4})
    result = {
        "schema": "fli.hybrid-model-pilot.tool-probe.v1",
        "status": "complete",
        "logicalCalls": 4,
        "modelBindings": bindings,
        "evidenceDirectory": str(root),
        "configSha256": digest(config),
        "sameToolSchema": True,
        "toolSchemaSha256": digest(PROBE_TOOLS),
        "reasoningContinuityVerified": True,
        "records": records,
        "providerMeter": meter_summary(meter.path),
        "artifactsSha256": {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()},
        "limitation": (
            "Synthetic local echo verifies schema/tool-result/Responses continuity, "
            "not corpus quality or every tool behavior."
        ),
    }
    save(root / "receipt.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("probe", "preflight", "run", "finalize", "review-packets", "score"))
    for name in (
        "plan",
        "manifest",
        "tasks",
        "config",
        "deployments",
        "runtimes",
        "preparation",
        "probe",
        "authorization",
        "source",
        "gold",
        "reviews",
        "embedding",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    needed = {
        "probe": ("config", "deployments"),
        "preflight": (
            "plan",
            "manifest",
            "tasks",
            "config",
            "deployments",
            "runtimes",
            "preparation",
            "probe",
            "embedding",
        ),
        "run": (
            "plan",
            "manifest",
            "tasks",
            "config",
            "deployments",
            "runtimes",
            "preparation",
            "probe",
            "embedding",
            "authorization",
        ),
        "finalize": ("deployments", "runtimes", "embedding"),
        "review-packets": ("source", "gold"),
        "score": ("source", "gold", "reviews"),
    }
    for name in needed[args.operation]:
        if getattr(args, name) is None:
            parser.error(f"--{name} is required")
    os.umask(0o077)

    def load(name):
        return read(private_path(getattr(args, name)))

    if args.operation == "probe":
        result = probe(load("config"), load("deployments"), args.output)
    elif args.operation in {"preflight", "run"}:
        positional = (
            args.plan,
            args.manifest,
            args.tasks,
            load("config"),
            load("deployments"),
            load("runtimes"),
            args.preparation,
            args.probe,
        )
        if args.operation == "preflight":
            result = prepare(*positional, args.output, load("embedding"))
            print(json.dumps(result["authorizationTemplate"], indent=2))
            return
        result = run(*positional, load("authorization"), args.output, embedding=load("embedding"))
    elif args.operation == "finalize":
        result = finalize(args.output, load("deployments"), load("runtimes"), load("embedding"))
    elif args.operation == "review-packets":
        result = review_packets(args.source, args.gold, args.output)
    else:
        result = score(args.source, args.gold, load("reviews"))
        save(private_path(args.output), result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k
                in {"schema", "status", "completedTurns", "packets", "reviewedTurns", "uniqueQuestions", "logicalCalls"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
