"""Private, predeclared hybrid/vector replay after direct source-recall measurement.

This driver reads existing immutable libraries. It provisions nothing, never
loads gold into an answering request, and cannot resume or rerun a claimed pack.
Management/runtime captures are operator evidence, not signed attestations.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fli.structure_lab import digest
from fli_demo.client import IndexClient
from scripts.compare_agent_models import default_chat_factory
from scripts.evaluate_agent_retrieval import capture, prepare_output, replay, snapshot
from scripts.retrieval_benchmark import validate_sources
from scripts.retrieval_controls import (
    AGENT_INSTRUCTIONS,
    ANSWER_CONTROLS,
    answer_controls,
    deployment_binding,
    private_path,
    read,
    verify_cell,
)
from scripts.retrieval_i1 import BINDING_KEYS, SETTINGS, api_hashes, hashes, recent, sha, verify_i1_wire_sources
from scripts.retrieval_p1 import RecordedChat
from scripts.score_retrieval_benchmark import coverage, delivered_bodies, normalize, usage, validate_gold
from scripts.structure_lab import save

ARMS = ("vector", "hybrid")
BASE = "3980333ee8453af48c7133cab24522868701d1cc"
DIRECT = {
    "top": 8,
    "includeNeighbors": True,
    "repeats": 1,
    "order": "alternate-arm-first-by-case",
    "maxEvidenceTokens": 200000,
    "filters": {},
}
FINAL_ARTIFACTS = {
    f"{n}.json"
    for n in (
        "controls",
        "private-config",
        "source-snapshot",
        "deployment-before",
        "deployment-after",
        "runtime-before",
        "runtime-after",
        "authorization",
        "blind-map",
        "plan",
        "manifest",
        "tasks",
        "direct",
        "embedding-before",
        "embedding-after",
    )
}
QUALITY = ("taskCompletion", "factualSupport", "citationAccuracy", "appropriateAbstention", "sourceRelationships")
ANSWERING = {
    **ANSWER_CONTROLS,
    "model": {"model": "gpt-6.1-sol", "version": "2026-09-29"},
    "instructions": AGENT_INSTRUCTIONS,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inputs(plan_path, manifest_path, tasks_path):
    plan, manifest, tasks = (read(p) for p in (plan_path, manifest_path, tasks_path))
    require(
        plan.get("schema") == "fli.hybrid-quality.plan.v1"
        and plan.get("baseCommit") == BASE
        and plan.get("arms") == list(ARMS)
        and plan.get("repeats") == 2
        and plan.get("evidenceView") == "compact"
        and plan.get("selectorMode") == "handles"
        and plan.get("sourceStrategy") == "microsoft-indexer-layout-v1"
        and digest(plan.get("answering")) == digest(ANSWERING)
        and digest(plan.get("direct")) == digest(DIRECT)
        and plan.get("manifestSha256") == sha(manifest_path)
        and plan.get("tasksSha256") == sha(tasks_path)
        and re.fullmatch(r"[0-9a-f]{64}", plan.get("goldSha256", "")),
        "plan or corpus differs from frozen comparison controls",
    )
    require(isinstance(tasks, list) and 40 <= len(tasks) <= 60, "confirmation requires 40–60 frozen questions")
    ids, questions = [t["case"] for t in tasks], [t["question"] for t in tasks]
    require(len(set(ids)) == len(ids) and plan.get("cases") == ids, "task identities/order differ")
    require(len(set(questions)) == len(questions), "questions must be distinct independent tasks")
    for task in tasks:
        require(
            re.fullmatch(r"[A-Za-z0-9_-]+", task["case"])
            and isinstance(task["question"], str)
            and task["question"].strip()
            and task.get("split") == "confirmation"
            and type(task.get("answerable")) is bool
            and task.get("families")
            and all(isinstance(f, str) and f for f in task["families"])
            and len(set(task["families"])) == len(task["families"])
            and "questions" not in task,
            "malformed confirmation question",
        )
        regions = [r["id"] for f in task.get("sourceFacts", []) for r in f["regions"]]
        facts = task.get("requiredFactIds", [])
        require(len(regions) == len(set(regions)), "duplicate task region")
        require(
            bool(regions) == task["answerable"],
            "answerable tasks require source labels; absence has no recall denominator",
        )
        require(
            isinstance(facts, list) and len(facts) == len(set(facts)) and bool(facts) == task["answerable"],
            "required fact identities differ from answerability",
        )
    require(
        any(t["answerable"] for t in tasks) and any(not t["answerable"] for t in tasks),
        "both answerability classes required",
    )
    return plan, manifest, tasks


def schedule(tasks):
    return [
        {"repeat": repeat, "arm": arm, "case": task["case"], "run": f"r{repeat}-{task['case']}-{arm}"}
        for repeat in (1, 2)
        for task in tasks
        for arm in (ARMS if repeat == 1 else tuple(reversed(ARMS)))
    ]


def answering_cases(tasks):
    """Only question and bookkeeping identity cross into the existing replay."""
    return {t["case"]: {"case": t["case"], "question": t["question"]} for t in tasks}


def validate_config(config):
    require(set(config) == {"model", "agent", "libraryId", "indexUrls"}, "unexpected replay configuration")
    require(set(config["model"]) == {"model", "version", "deployment"}, "model overrides forbidden")
    answer_controls({**ANSWER_CONTROLS, "model": config["model"], "agent": config["agent"]})
    require(set(config["agent"]) == {"id", "name", "instructions"}, "unexpected agent context")
    require(all(isinstance(v, str) and v for v in config["agent"].values()), "agent fields required")
    require(isinstance(config["libraryId"], str) and config["libraryId"], "one owned library required")
    require(set(config["indexUrls"]) == set(ARMS), "both API endpoints required")
    for value in config["indexUrls"].values():
        url = urlsplit(value)
        require(
            url.scheme == "http"
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
            and url.port
            and url.path in {"", "/"}
            and not url.query
            and not url.fragment
            and not url.username
            and not url.password,
            "development authentication is restricted to loopback APIs",
        )
    require(len(set(config["indexUrls"].values())) == 2, "independent mode-specific APIs required")


def runtime_bindings(receipts, config, *, fresh=True):
    require(set(receipts) == set(ARMS), "missing API runtime receipt")
    bindings, common = {}, []
    for arm in ARMS:
        receipt = receipts[arm]
        if fresh:
            recent(receipt["capturedAt"], 900)
        settings = receipt["settingsBinding"]
        expected = {**SETTINGS, "searchMode": arm}
        require(
            receipt.get("schema") == "fli.hybrid-quality.api-runtime.v1"
            and receipt.get("indexUrl") == config["indexUrls"][arm]
            and receipt.get("apiSourceSha256") == api_hashes()
            and receipt.get("measurementCodeSha256") == hashes()
            and receipt.get("launcherSha256") == sha(Path(__file__).with_name("hybrid_quality_api.py"))
            and set(settings) == BINDING_KEYS
            and all(isinstance(v, str) and v for v in settings.values())
            and settings["SEARCH_MODE"] == arm
            and settings["AOAI_EMBEDDING_MODEL_REVISION"] == "1"
            and settings["AOAI_EMBEDDING_DIMENSIONS"] == "3072"
            and settings["SEARCH_DEFAULT_TOP"] == "8"
            and settings["SEARCH_MAX_TOP"] == "20"
            and receipt.get("settingsBindingSha256") == digest(settings)
            and receipt.get("effectiveSettings") == expected
            and receipt.get("authenticationMode") == "dev"
            and receipt.get("environment") == "local"
            and type(receipt.get("processId")) is int
            and receipt["processId"] > 0
            and receipt.get("ownEnvironmentAndImportVerified") is True
            and receipt.get("sourceRoot")
            and receipt.get("pythonPrefix")
            and receipt.get("declaredCodeRevision") == receipt.get("gitRevision")
            and receipt.get("gitRevision"),
            "actual API runtime differs from frozen comparison",
        )
        common.append({k: v for k, v in settings.items() if k != "SEARCH_MODE"})
        require(
            re.fullmatch(r"[0-9a-f]{64}", receipt.get("storedRowsSha256", "")),
            "actual stored-row snapshot fingerprint required",
        )
        bindings[arm] = {
            "apiSourceSha256": receipt["apiSourceSha256"],
            "measurementCodeSha256": receipt["measurementCodeSha256"],
            "launcherSha256": receipt["launcherSha256"],
            "settingsBindingSha256": receipt["settingsBindingSha256"],
            "indexUrl": receipt["indexUrl"],
            "processId": receipt["processId"],
            "gitRevision": receipt["gitRevision"],
            "effectiveSettings": expected,
            "storedRowsSha256": receipt["storedRowsSha256"],
            "sourceRoot": receipt["sourceRoot"],
            "pythonPrefix": receipt["pythonPrefix"],
            "commonSettingsBindingSha256": digest(common[-1]),
        }
    require(common[0] == common[1], "non-mode API settings differ between arms")
    require(bindings["vector"]["gitRevision"] == bindings["hybrid"]["gitRevision"], "API code revisions differ")
    require(bindings["vector"]["processId"] != bindings["hybrid"]["processId"], "mode-specific API processes required")
    for key in ("storedRowsSha256", "sourceRoot", "pythonPrefix"):
        require(bindings["vector"][key] == bindings["hybrid"][key], "non-mode storage/import control differs")
    return bindings


def model_binding(receipt, config, runtime, *, fresh=True):
    endpoint = runtime["vector"]["settingsBinding"]["AOAI_ENDPOINT"]
    return deployment_binding(
        receipt,
        configured_endpoint=endpoint,
        configured_deployment=config["model"]["deployment"],
        model=config["model"]["model"],
        revision=config["model"]["version"],
        now=None if fresh else datetime.fromisoformat(receipt["capturedAt"]),
    )


def embedding_binding(receipt, runtime, *, fresh=True):
    binding = runtime["vector"]["settingsBinding"]
    return deployment_binding(
        receipt,
        configured_endpoint=binding["AOAI_ENDPOINT"],
        configured_deployment=binding["AOAI_EMBEDDING_DEPLOYMENT"],
        model="text-embedding-3-large",
        revision="1",
        now=None if fresh else datetime.fromisoformat(receipt["capturedAt"]),
    )


def make_index(config, arm):
    return IndexClient(
        config["indexUrls"][arm], os.environ["DEV_API_KEY"], evidence_view="compact", selector_mode="handles"
    )


def validate_snapshot(frozen, config, manifest):
    selected = copy.deepcopy(manifest)
    selected["baseline"] = {"sourceIds": [d["id"] for d in manifest["documents"]]}
    selected["controls"] = {"query": {"embeddingModel": "text-embedding-3-large", "dimensions": 3072}}
    validate_sources(frozen, {"libraries": {"microsoft": config["libraryId"]}}, selected)
    require(set(frozen["libraries"]) == {config["libraryId"]}, "snapshot contains other libraries")
    captured = frozen["libraries"][config["libraryId"]]
    recipe = captured["library"]["ingestionProfile"]
    require(recipe["embedding"].get("modelRevision") == "1", "source embedding revision drift")
    for document in captured["documents"]:
        job = document.get("ingestionJob") or {}
        require(
            job.get("profile") == recipe
            and job.get("generation") == document["activeGeneration"]
            and job.get("sourceSha256") == document["sha256"],
            "active source provenance differs",
        )


def direct_rows(receipt, tasks):
    expected = {(t["case"], arm) for t in tasks for arm in ARMS}
    rows = receipt.get("rows", [])
    require(receipt.get("status") == "complete" and len(rows) == len(expected), "direct recall matrix incomplete")
    require({(r["case"], r["arm"]) for r in rows} == expected, "direct cases/arms differ")
    by_case = {t["case"]: t for t in tasks}
    for row in rows:
        required = {r["id"] for f in by_case[row["case"]].get("sourceFacts", []) for r in f["regions"]}
        for field in ("initialRegions", "expandedRegions"):
            values = row[field]
            require(
                isinstance(values, list) and len(values) == len(set(values)) and set(values) <= required,
                "direct recall region set differs from frozen source labels",
            )
        require(set(row["initialRegions"]) <= set(row["expandedRegions"]), "expansion cannot lose initial evidence")
    return {(r["case"], r["arm"]): r for r in rows}


def gate(path, authorization, plan_path, plan, tasks, baseline, runtimes, output, embedding, *, fresh=True):
    direct = read(private_path(path))
    direct_rows(direct, tasks)
    require(
        direct.get("manifestSha256") == plan["manifestSha256"]
        and direct.get("tasksSha256") == plan["tasksSha256"]
        and direct.get("planSha256") == sha(plan_path),
        "direct comparison uses different corpus",
    )
    paths, expected = direct.get("rawArtifactPaths", {}), direct.get("rawArtifactsSha256", {})
    require(paths and set(paths) == set(expected), "direct raw evidence bindings incomplete")
    for key, value in paths.items():
        require(sha(private_path(value)) == expected[key], "direct raw evidence changed")
    require(
        all(r["commonSettingsBindingSha256"] == direct.get("settingsBindingSha256") for r in runtimes.values()),
        "direct and answering common settings differ",
    )
    require(
        all(direct.get("codeSha256", {}).get(k) == v for k, v in api_hashes().items()),
        "direct and answering API code differ",
    )
    require(
        "query-cache-private.json" in paths
        and read(paths["query-cache-private.json"])["deploymentBinding"] == embedding,
        "direct and adaptive-query embedding deployments differ",
    )
    require({"source-before.json", "source-after.json"} <= set(paths), "direct source snapshots missing")
    direct_before = read(paths["source-before.json"])
    direct_after = read(paths["source-after.json"])
    require(
        direct_before == direct_after and digest(direct_before) == direct.get("sourceSnapshotSha256"),
        "direct source snapshot binding changed",
    )
    require(
        len(baseline["libraries"]) == 1
        and capture({k: direct_before[k] for k in ("library", "documents", "tools")})
        == next(iter(baseline["libraries"].values())),
        "direct and answering source/tool snapshots differ",
    )
    require(
        all(r["storedRowsSha256"] == digest(direct_before["chunks"]) for r in runtimes.values()),
        "stored rows changed between direct retrieval and answering",
    )
    if fresh:
        reviewed = recent(authorization["reviewedAt"], 900)
        require(datetime.fromisoformat(direct["finishedAt"]) <= reviewed, "direct recall must precede accepted review")
    require(
        authorization.get("schema") == "fli.hybrid-quality.execution-authorization.v1"
        and authorization.get("status") == "accepted"
        and authorization.get("directReceiptSha256") == sha(path)
        and authorization.get("planSha256") == sha(plan_path)
        and authorization.get("sourceSnapshotSha256") == digest(baseline)
        and authorization.get("runtimeBindingsSha256") == digest(runtimes)
        and authorization.get("codeSha256") == digest(hashes())
        and authorization.get("corpusIndependentlyReviewed") is True
        and authorization.get("sourceLibraryBoundariesVerified") is True
        and authorization.get("hybridRanksMeaningfullyVerified") is True
        and private_path(authorization["outputDirectory"]) == private_path(output),
        "independent direct/corpus/control acceptance is required before answering",
    )
    ledger = private_path(authorization["executionLedger"])
    require(not ledger.is_relative_to(private_path(output)), "ledger must survive outside run directory")
    return {
        "directReceiptSha256": sha(path),
        "authorizationSha256": digest(authorization),
        "executionLedger": str(ledger),
    }


def run(
    plan_path, manifest_path, tasks_path, config, before, runtimes, direct_path, authorization, output, *, embedding
):
    plan, manifest, tasks = inputs(plan_path, manifest_path, tasks_path)
    validate_config(config)
    bindings = runtime_bindings(runtimes, config)
    require(
        os.environ.get("AOAI_ENDPOINT") == runtimes["vector"]["settingsBinding"]["AOAI_ENDPOINT"],
        "invoked answering endpoint differs from runtime capture",
    )
    deployment = model_binding(before, config, runtimes)
    embedding_model = embedding_binding(embedding, runtimes)
    indexes = {a: make_index(config, a) for a in ARMS}
    agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
    baseline = snapshot(indexes["vector"], agent)
    validate_snapshot(baseline, config, manifest)
    require(snapshot(indexes["hybrid"], agent) == baseline, "arms differ in source metadata/generations/tools")
    accepted = gate(direct_path, authorization, plan_path, plan, tasks, baseline, bindings, output, embedding_model)
    root = prepare_output(private_path(output))
    save(root / "run-status.json", {"status": "prepared", "completedTurns": 0, "confirmationExecuted": False})
    matrix = schedule(tasks)
    # Mapping is frozen before first answer, never inferred from outcomes or usage.
    blind = [{**r, "reviewId": uuid.uuid4().hex} for r in matrix]
    controls = {
        "schema": "fli.hybrid-quality.replay-controls.v1",
        **ANSWER_CONTROLS,
        "model": config["model"],
        "agent": config["agent"],
        "plan": plan,
        "planSha256": sha(plan_path),
        "manifestSha256": sha(manifest_path),
        "tasksSha256": sha(tasks_path),
        "codeSha256": hashes(),
        "runtimeBindings": bindings,
        "deploymentBinding": deployment,
        "embeddingDeploymentBinding": embedding_model,
        "acceptedGate": accepted,
        "schedule": matrix,
        "blindMapSha256": digest(blind),
    }
    for name, value in (
        ("controls", controls),
        ("private-config", config),
        ("source-snapshot", baseline),
        ("deployment-before", before),
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
        ("direct", direct_path),
    ):
        (root / f"{name}.json").write_bytes(Path(path).read_bytes())
        (root / f"{name}.json").chmod(0o600)
    ledger = Path(accepted["executionLedger"])
    ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with ledger.open("x") as handle:
        json.dump(
            {"planSha256": sha(plan_path), "outputDirectory": str(root), "claimedAt": datetime.now(UTC).isoformat()},
            handle,
        )
    ledger.chmod(0o600)
    cases = answering_cases(tasks)
    completed, started = 0, False
    try:
        for member in matrix:
            require(hashes() == controls["codeSha256"], "code drift before answering turn")
            index = indexes[member["arm"]]
            require(snapshot(index, agent) == baseline, "source drift before answering turn")
            wire = root / ("wire-" + member["run"])
            wire.mkdir(mode=0o700)
            chat = RecordedChat(default_chat_factory(config["model"], "responses"), wire, "selectors-only")
            started = True
            case = cases[member["case"]]
            replay(index, chat, agent, [case], root / member["run"], budget=200000)
            verify_cell(root / member["run"], [case], baseline)
            require(hashes() == controls["codeSha256"], "code drift after answering turn")
            completed += 1
            save(
                root / "run-status.json",
                {"status": "running", "completedTurns": completed, "confirmationExecuted": True},
            )
    except (Exception, KeyboardInterrupt) as error:
        save(
            root / "run-status.json",
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                "completedTurns": completed,
                "confirmationExecuted": started,
                "errorType": type(error).__name__,
                "unfinishedUsage": "Completed and partial turn/wire receipts retained; "
                "unavailable counters are not zero. No resume or rerun.",
            },
        )
        raise
    status = {
        "status": "pending_recheck",
        "completedTurns": completed,
        "confirmationExecuted": True,
        "completedAt": datetime.now(UTC).isoformat(),
    }
    save(root / "run-status.json", status)
    return status


def verify_execution(root):
    root = private_path(root)
    plan, manifest, tasks = inputs(root / "plan.json", root / "manifest.json", root / "tasks.json")
    controls, config, baseline = (read(root / f"{n}.json") for n in ("controls", "private-config", "source-snapshot"))
    validate_config(config)
    answer_controls(controls)
    require(
        controls["plan"] == plan
        and controls["planSha256"] == sha(root / "plan.json")
        and controls["codeSha256"] == hashes()
        and controls["schedule"] == schedule(tasks),
        "frozen execution controls drifted",
    )
    validate_snapshot(baseline, config, manifest)
    runtime = read(root / "runtime-before.json")
    bindings = runtime_bindings(runtime, config, fresh=False)
    require(bindings == controls["runtimeBindings"], "runtime controls changed")
    require(
        model_binding(read(root / "deployment-before.json"), config, runtime, fresh=False)
        == controls["deploymentBinding"],
        "saved model control mismatch",
    )
    require(
        embedding_binding(read(root / "embedding-before.json"), runtime, fresh=False)
        == controls["embeddingDeploymentBinding"],
        "saved query embedding control mismatch",
    )
    accepted = gate(
        root / "direct.json",
        read(root / "authorization.json"),
        root / "plan.json",
        plan,
        tasks,
        baseline,
        bindings,
        root,
        controls["embeddingDeploymentBinding"],
        fresh=False,
    )
    require(accepted == controls["acceptedGate"], "accepted gate changed")
    ledger = read(Path(accepted["executionLedger"]))
    require(
        ledger["planSha256"] == controls["planSha256"] and private_path(ledger["outputDirectory"]) == root,
        "single execution ledger changed",
    )
    blind = read(root / "blind-map.json")
    require(
        digest(blind) == controls["blindMapSha256"]
        and [{k: r[k] for k in ("repeat", "arm", "case", "run")} for r in blind] == schedule(tasks)
        and len({r["reviewId"] for r in blind}) == len(blind),
        "blind mapping changed",
    )
    cases = answering_cases(tasks)
    system_hashes = set()
    for member in controls["schedule"]:
        verify_cell(root / member["run"], [cases[member["case"]]], baseline)
        wire_result(root, member)
        receipt = read(root / ("wire-" + member["run"]) / "wire-receipts.json")
        expected_tools = baseline["libraries"][config["libraryId"]]["tools"]
        for ordinal in range(1, len(receipt) + 1):
            wire = read(root / ("wire-" + member["run"]) / f"wire-{ordinal:03}.json")
            system = [m["content"] for m in wire["messages"] if m.get("role") == "system"]
            require(len(system) == 1 and system[0].startswith(AGENT_INSTRUCTIONS), "frozen system prompt missing")
            system_hashes.add(digest(system))
            final_limit = wire["messages"][-1].get("content", "")
            require(
                wire["tools"] == expected_tools
                or (
                    ordinal == len(receipt)
                    and wire["tools"] == []
                    and isinstance(final_limit, str)
                    and final_limit.startswith("Tool-round limit reached. Answer from content actually received.")
                ),
                "actual provider tool definitions changed",
            )
    require(len(system_hashes) == 1, "answering system context changed across tasks/arms")
    return controls, config, baseline, manifest, tasks


def finalize(root, after, runtimes, embedding):
    root = private_path(root)
    controls, config, baseline, _, _ = verify_execution(root)
    status = read(root / "run-status.json")
    require(
        status.get("status") == "pending_recheck" and status.get("completedTurns") == len(controls["schedule"]),
        "only the complete comparison can be finalized",
    )
    completed = datetime.fromisoformat(status["completedAt"])
    require(
        datetime.fromisoformat(after["capturedAt"]) >= completed
        and datetime.fromisoformat(embedding["capturedAt"]) >= completed
        and all(datetime.fromisoformat(r["capturedAt"]) >= completed for r in runtimes.values()),
        "post-run receipts must follow last completed answering turn",
    )
    require(runtime_bindings(runtimes, config) == controls["runtimeBindings"], "API runtime changed")
    require(model_binding(after, config, runtimes) == controls["deploymentBinding"], "answering deployment changed")
    require(
        embedding_binding(embedding, runtimes) == controls["embeddingDeploymentBinding"],
        "adaptive-query embedding deployment changed",
    )
    agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
    for arm in ARMS:
        require(snapshot(make_index(config, arm), agent) == baseline, "source changed after replay")
    save(root / "deployment-after.json", after)
    save(root / "embedding-after.json", embedding)
    save(root / "runtime-after.json", runtimes)
    save(
        root / "finalization.json",
        {"artifactsSha256": {n: sha(root / n) for n in artifact_inventory(root)}, "completedAt": status["completedAt"]},
    )
    result = {
        "status": "complete",
        "completedTurns": len(controls["schedule"]),
        "confirmationExecuted": True,
        "sourceCodeModelRuntimeUnchanged": True,
    }
    save(root / "run-status.json", result)
    return result


def artifact_inventory(root):
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p not in {root / "finalization.json", root / "run-status.json"}
    )


def finalized(root):
    values = verify_execution(root)
    controls = values[0]
    require(
        read(root / "run-status.json")
        == {
            "status": "complete",
            "completedTurns": len(controls["schedule"]),
            "confirmationExecuted": True,
            "sourceCodeModelRuntimeUnchanged": True,
        },
        "comparison not finalized",
    )
    final = read(root / "finalization.json")
    require(
        set(final["artifactsSha256"]) == set(artifact_inventory(root))
        and FINAL_ARTIFACTS <= set(final["artifactsSha256"]),
        "finalization artifact inventory incomplete",
    )
    for name, expected in final["artifactsSha256"].items():
        require(sha(root / name) == expected, "finalized artifact changed")
    config = values[1]
    runtime = read(root / "runtime-after.json")
    completed = datetime.fromisoformat(final["completedAt"])
    require(
        datetime.fromisoformat(read(root / "deployment-after.json")["capturedAt"]) >= completed
        and datetime.fromisoformat(read(root / "embedding-after.json")["capturedAt"]) >= completed
        and all(datetime.fromisoformat(r["capturedAt"]) >= completed for r in runtime.values()),
        "saved post-run receipts predate completion",
    )
    require(
        runtime_bindings(runtime, config, fresh=False) == controls["runtimeBindings"], "final runtime binding mismatch"
    )
    require(
        model_binding(read(root / "deployment-after.json"), config, runtime, fresh=False)
        == controls["deploymentBinding"],
        "final model binding mismatch",
    )
    require(
        embedding_binding(read(root / "embedding-after.json"), runtime, fresh=False)
        == controls["embeddingDeploymentBinding"],
        "final query embedding binding mismatch",
    )
    return values


def wire_result(root, member):
    results = read(root / member["run"] / "results.json")
    wires = read(root / ("wire-" + member["run"]) / "wire-receipts.json")
    require(len(results) == 1 and len(wires) == len(results[0]["metrics"]["modelCalls"]), "wire/model calls incomplete")
    calls = []
    for ordinal, wire in enumerate(wires, 1):
        call = read(root / ("wire-" + member["run"]) / f"wire-{ordinal:03}.json")
        require(
            wire["call"] == ordinal
            and digest(call["messages"]) == wire["wireSha256"]
            and wire["referenceToolMessages"] == 0,
            "actual compact wire evidence changed",
        )
        calls.append(call)
    delivered = verify_i1_wire_sources(calls, results)
    return results[0], delivered[member["case"]], wires


def gold_check(path, plan, manifest, tasks):
    require(sha(private_path(path)) == plan["goldSha256"], "private gold changed after freeze")
    gold = read(path)
    validate_gold(manifest, tasks, gold)
    for task in tasks:
        require(set(task.get("requiredFactIds", [])) <= set(gold.get("facts", {})), "unknown required fact")
    return gold


def blinded_evidence(value):
    """Retain source/selection content while withholding explicit measurement clues."""
    hidden = {"usage", "telemetryId", "score", "searchMode", "arm", "repeat"}
    if isinstance(value, list):
        return [blinded_evidence(v) for v in value]
    if isinstance(value, dict):
        return {k: blinded_evidence(v) for k, v in value.items() if k not in hidden}
    return value


def review_packets(root, gold_path, output):
    root = private_path(root)
    controls, _, _, manifest, tasks = finalized(root)
    gold = gold_check(gold_path, controls["plan"], manifest, tasks)
    output = private_path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    task_map = {t["case"]: t for t in tasks}
    blind = read(root / "blind-map.json")
    # Lexical ordering of random IDs hides arm and run order in directory listings.
    for member in sorted(blind, key=lambda r: r["reviewId"]):
        result, evidence, _ = wire_result(root, member)
        task = task_map[member["case"]]
        regions = [r["id"] for f in task.get("sourceFacts", []) for r in f["regions"]]
        packet = {
            "reviewId": member["reviewId"],
            "case": task["case"],
            "question": task["question"],
            "answerable": task["answerable"],
            "answer": result["answer"],
            "evidence": blinded_evidence(evidence),
            "originalSourceRegions": {k: gold["regions"][k] for k in regions},
            "requiredFacts": {k: gold["facts"][k] for k in task.get("requiredFactIds", [])},
            "goldTask": gold.get("tasks", {}).get(task["case"]),
            "reviewTemplate": {
                "reviewId": member["reviewId"],
                **dict.fromkeys(QUALITY),
                "deliveredRegionIds": [],
                "supportedFactIds": [],
                "fullReadRegionIds": [],
                "searchRegionIds": [],
                "selectedReadRegionIds": [],
                "blankRegionIdsVerified": [],
            },
        }
        save(output / (member["reviewId"] + ".json"), packet)
    return {
        "packets": len(blind),
        "armLabelsHidden": True,
        "limitations": "Random identifiers hide explicit arm/order/usage, "
        "not all inferable differences in evidence or answers.",
    }


def validate_review(review, task, gold):
    require(
        all(k in review and (review[k] is None or type(review[k]) is bool) for k in QUALITY),
        "explicit quality judgments required",
    )
    require(
        review["appropriateAbstention"] is None
        if task["answerable"]
        else type(review["appropriateAbstention"]) is bool,
        "abstention is applicable exactly to unanswerable tasks",
    )
    required = {r["id"] for f in task.get("sourceFacts", []) for r in f["regions"]}
    for field, allowed in (
        ("deliveredRegionIds", required),
        ("fullReadRegionIds", required),
        ("searchRegionIds", required),
        ("selectedReadRegionIds", required),
        ("supportedFactIds", set(task.get("requiredFactIds", []))),
        ("blankRegionIdsVerified", required),
    ):
        value = review[field]
        require(
            isinstance(value, list) and len(value) == len(set(value)) and set(value) <= allowed,
            "reviewed source/fact identities differ from frozen labels",
        )
    require(
        all(
            set(review[k]) <= set(review["deliveredRegionIds"])
            for k in ("fullReadRegionIds", "searchRegionIds", "selectedReadRegionIds")
        ),
        "channel-specific evidence must also be delivered",
    )
    blanks = {k for k in required if not normalize(gold["regions"][k]["expectedText"])}
    require(
        set(review["blankRegionIdsVerified"]) == blanks & set(review["deliveredRegionIds"]),
        "blank regions require explicit positional review",
    )


def paired_summary(rows, tasks):
    lookup = {(r["case"], r["repeat"], r["arm"]): r for r in rows}
    require(len(lookup) == len(tasks) * 4 == len(rows), "missing or duplicate reviewed turns")
    pairs = []
    for task in tasks:
        for repeat in (1, 2):
            a, b = (lookup[task["case"], repeat, arm] for arm in ARMS)
            judgments = {}
            for metric in (*QUALITY, "requiredRegionRecall", "requiredFactCoverage"):
                left, right = a[metric], b[metric]
                judgments[metric] = (
                    "unavailable"
                    if left is None or right is None
                    else ("hybrid_win" if right > left else "vector_win" if left > right else "tie")
                )
            pairs.append(
                {
                    "case": task["case"],
                    "repeat": repeat,
                    "families": task["families"],
                    "comparisons": judgments,
                    "hybridGainedRegions": sorted(set(b["deliveredRegionIds"]) - set(a["deliveredRegionIds"])),
                    "hybridLostRegions": sorted(set(a["deliveredRegionIds"]) - set(b["deliveredRegionIds"])),
                }
            )
    families = {}
    for family in sorted({f for t in tasks for f in t["families"]} | {"all"}):
        selected = [p for p in pairs if family == "all" or family in p["families"]]
        families[family] = {
            "uniqueQuestions": len({p["case"] for p in selected}),
            "pairedRepeats": len(selected),
            "comparisons": {
                k: dict(Counter(p["comparisons"][k] for p in selected))
                for k in (*QUALITY, "requiredRegionRecall", "requiredFactCoverage")
            },
        }
    return {
        "pairs": pairs,
        "families": families,
        "byArm": {
            arm: {
                "turns": sum(r["arm"] == arm for r in rows),
                "quality": {
                    metric: {
                        "passed": sum(r[metric] is True for r in rows if r["arm"] == arm),
                        "failed": sum(r[metric] is False for r in rows if r["arm"] == arm),
                        "unavailableOrInapplicable": sum(r[metric] is None for r in rows if r["arm"] == arm),
                    }
                    for metric in QUALITY
                },
            }
            for arm in ARMS
        },
        "uncertainty": "Purposeful finite corpus; paired descriptive counts. "
        "Two repeats per question are correlated, not new questions. Family tags overlap.",
    }


def score(root, gold_path, reviews):
    root = private_path(root)
    controls, _, _, manifest, tasks = finalized(root)
    gold = gold_check(gold_path, controls["plan"], manifest, tasks)
    blind = read(root / "blind-map.json")
    by_review = {r["reviewId"]: r for r in reviews}
    require(
        len(by_review) == len(reviews) and set(by_review) == {m["reviewId"] for m in blind},
        "all opaque review IDs required exactly once",
    )
    by_task = {t["case"]: t for t in tasks}
    direct = direct_rows(read(root / "direct.json"), tasks)
    rows, wires = [], []
    for member in blind:
        task, review = by_task[member["case"]], by_review[member["reviewId"]]
        validate_review(review, task, gold)
        result, delivered, captured = wire_result(root, member)
        full = [
            r
            for r in delivered
            if r.get("mode") == "full" and r.get("complete") is True and r.get("disposition") != "budget_exceeded"
        ]
        search = [
            r
            for r in delivered
            if r.get("mode") == "evidence_search" or r.get("automaticRecovery") == "document_scoped_passages"
        ]
        selected = [r for r in delivered if r.get("mode") == "selected" and not r.get("automaticRecovery")]
        require(not review["fullReadRegionIds"] or full, "full-read recovery cannot be credited without a full read")
        for field, evidence in (
            ("deliveredRegionIds", delivered),
            ("fullReadRegionIds", full),
            ("searchRegionIds", search),
            ("selectedReadRegionIds", selected),
        ):
            files = delivered_bodies(evidence)
            require(
                all(gold["regions"][r]["fileName"] in files for r in review[field]),
                "source channel attributed to a file that supplied no original body in that channel",
            )
        original = {r["id"] for f in task.get("sourceFacts", []) for r in f["regions"]}
        facts = set(task.get("requiredFactIds", []))
        direct_row = direct[task["case"], member["arm"]]
        recovered = set(review["fullReadRegionIds"]) - set(direct_row["expandedRegions"])
        rows.append(
            {
                "repeat": member["repeat"],
                "arm": member["arm"],
                "case": member["case"],
                "families": task["families"],
                "answerable": task["answerable"],
                **{k: review[k] for k in QUALITY},
                "deliveredRegionIds": sorted(review["deliveredRegionIds"]),
                "supportedFactIds": sorted(review["supportedFactIds"]),
                "requiredRegionRecall": len(review["deliveredRegionIds"]) / len(original) if original else None,
                "requiredFactCoverage": len(review["supportedFactIds"]) / len(facts) if facts else None,
                "allRequiredRegionsDelivered": original <= set(review["deliveredRegionIds"]) if original else None,
                "directInitialRegions": direct_row["initialRegions"],
                "directExpandedRegions": direct_row["expandedRegions"],
                "searchRegionIds": sorted(review["searchRegionIds"]),
                "selectedReadRegionIds": sorted(review["selectedReadRegionIds"]),
                "fullReadRegionIds": sorted(review["fullReadRegionIds"]),
                "fullReadRegionsBeyondDirectExpanded": sorted(recovered),
                "fullReadRegionsBeyondAgentSearch": sorted(
                    set(review["fullReadRegionIds"]) - set(review["searchRegionIds"])
                ),
                "fullReadOnlyRegions": sorted(
                    set(review["fullReadRegionIds"])
                    - set(review["searchRegionIds"])
                    - set(review["selectedReadRegionIds"])
                ),
                "fullReadNormalizedTextDiagnostic": coverage(task, full, gold),
                "sourceTextDiagnostic": coverage(task, delivered, gold),
                "usage": usage(result),
                "sourceReviewSha256": digest(
                    {
                        k: review[k]
                        for k in [
                            *QUALITY,
                            "deliveredRegionIds",
                            "supportedFactIds",
                            "fullReadRegionIds",
                            "searchRegionIds",
                            "selectedReadRegionIds",
                            "blankRegionIdsVerified",
                        ]
                    }
                ),
            }
        )
        wires.extend({"repeat": member["repeat"], "arm": member["arm"], "case": member["case"], **w} for w in captured)
    return {
        "schema": "fli.hybrid-quality.answer-scorecard.v1",
        "status": "complete",
        "uniqueQuestions": len(tasks),
        "reviewedTurns": len(rows),
        "planSha256": controls["planSha256"],
        "controlsSha256": digest(controls),
        "finalizationSha256": sha(root / "finalization.json"),
        "rows": rows,
        "wireReceipts": wires,
        **paired_summary(rows, tasks),
        "decision": "Coordinator reviews recall first, answer quality separately, efficiency second; "
        "no automatic default promotion.",
        "limitations": [
            "Assistant source review, not blinded human accuracy; opaque packets hide explicit arm labels only.",
            "Complete-file recovery is separate from initial search recall.",
            "RRF uses validated unpartitioned routing; vector uses library prefix. "
            "Cost is not a pure ranking comparison.",
            "Unavailable usage stays unavailable; cache/backend placement uncontrolled; no complete billed-cost claim.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("run", "finalize", "review-packets", "score"))
    for name in (
        "plan",
        "manifest",
        "tasks",
        "config",
        "deployment",
        "runtimes",
        "direct",
        "authorization",
        "source",
        "gold",
        "reviews",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embedding-deployment", type=Path)
    args = parser.parse_args()
    needed = {
        "run": ("plan", "manifest", "tasks", "config", "deployment", "runtimes", "direct", "authorization"),
        "finalize": ("deployment", "runtimes"),
        "review-packets": ("source", "gold"),
        "score": ("source", "gold", "reviews"),
    }
    for key in needed[args.operation]:
        if getattr(args, key) is None:
            parser.error(f"--{key} is required for {args.operation}")
    if args.operation in {"run", "finalize"} and args.embedding_deployment is None:
        parser.error("--embedding-deployment is required for run and finalize")
    os.umask(0o077)
    if args.operation == "run":
        result = run(
            args.plan,
            args.manifest,
            args.tasks,
            read(private_path(args.config)),
            read(private_path(args.deployment)),
            read(private_path(args.runtimes)),
            args.direct,
            read(private_path(args.authorization)),
            args.output,
            embedding=read(private_path(args.embedding_deployment)),
        )
    elif args.operation == "finalize":
        result = finalize(
            args.output,
            read(private_path(args.deployment)),
            read(private_path(args.runtimes)),
            read(private_path(args.embedding_deployment)),
        )
    elif args.operation == "review-packets":
        result = review_packets(args.source, args.gold, args.output)
    else:
        result = score(args.source, args.gold, read(private_path(args.reviews)))
        save(private_path(args.output), result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k in {"schema", "status", "completedTurns", "packets", "reviewedTurns", "uniqueQuestions"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
