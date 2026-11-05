"""Two-turn, same-controls granular follow-up; private evidence and owned scopes only."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from fli.config import Settings
from fli_demo.client import IndexClient
from scripts import granular_live as live
from scripts.canary import definition_mismatches, target_fingerprint
from scripts.granular_live_preflight import preflight, protected_snapshot
from scripts.granular_live_safety import OwnedLedger
from scripts.hybrid_model_meter import actual_client
from scripts.hybrid_quality_scope import save
from scripts.qualify_granular_navigation import REPO, require, sha
from scripts.retrieval_controls import private_path

BASELINE = "83168a8"
OUTLINE_BASELINE = "d332d1d"
REFINEMENT_BASELINE = "9fcc9bc"
OUTLINE_RUNTIME_PATHS = {
    "src/fli/tool.py",
    "src/fli/selector_hints.py",
    "src/fli/reading.py",
    "src/fli/presentation.py",
}
REFINEMENT_RUNTIME_PATHS = OUTLINE_RUNTIME_PATHS | {"src/fli/granular_navigation.py"}
SCOPE_LIMITS = {"libraries": 1, "documents": 1, "generations": 1, "chunks": 16}
LIMITS = {
    **SCOPE_LIMITS,
    "sourceBytesEach": 1048576,
    "embeddingLogicalCalls": 20,
    "embeddingAttemptsEach": 3,
    "embeddingLocalInputTokens": 50000,
    "modelTurns": 2,
    "executionSeconds": 1200,
    "cleanupSeconds": 600,
    "lateAbsenceCheckSeconds": 60,
}


class FollowupLedger(OwnedLedger):
    """Keep reviewed ownership mechanics, with narrower pre-persistence ceilings."""

    def _change(self, kind, details, change):
        def bounded(state):
            change(state)
            for key, ceiling in SCOPE_LIMITS.items():
                require(len(state[key]) <= ceiling, "follow-up " + key + " ceiling")

        super()._change(kind, details, bounded)


def protocol_limits(protocol="two-turn-v1"):
    require(protocol in {"two-turn-v1", "outline-v1", "outline-v2"}, "unknown qualification protocol")
    return {**LIMITS, "modelTurns": 1} if protocol != "two-turn-v1" else dict(LIMITS)


def protocol_baseline(protocol):
    return {"two-turn-v1": BASELINE, "outline-v1": OUTLINE_BASELINE, "outline-v2": REFINEMENT_BASELINE}[protocol]


def runtime_baseline(revision=BASELINE):
    paths = [*REPO.glob("src/**/*.py"), *REPO.glob("cosmos/*.json"), REPO / "requirements-local.lock"]
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", revision], cwd=REPO, text=True)
    expected = {
        name
        for name in tracked.splitlines()
        if (name.startswith("src/") and name.endswith(".py"))
        or (name.startswith("cosmos/") and name.endswith(".json"))
        or name == "requirements-local.lock"
    }
    require({str(path.relative_to(REPO)) for path in paths} == expected, "candidate runtime inventory changed")
    for path in paths:
        relative = str(path.relative_to(REPO))
        frozen = subprocess.check_output(["git", "show", revision + ":" + relative], cwd=REPO)
        require(path.read_bytes() == frozen, "follow-up cannot change candidate runtime: " + relative)
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair remains separate")


def verify_runtime(protocol, runtime_revision=None):
    protocol_limits(protocol)
    if protocol == "two-turn-v1":
        require(runtime_revision is None, "original follow-up runtime cannot be overridden")
        runtime_baseline()
        return
    require(
        isinstance(runtime_revision, str) and re.fullmatch(r"[a-f0-9]{40}", runtime_revision),
        "exact reviewed runtime commit required",
    )
    baseline = protocol_baseline(protocol)
    require(
        subprocess.run(["git", "merge-base", "--is-ancestor", baseline, runtime_revision], cwd=REPO).returncode == 0,
        "reviewed runtime does not extend outline baseline",
    )
    changed = set(
        subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                baseline,
                runtime_revision,
                "--",
                "src",
                "cosmos",
                "requirements-local.lock",
            ],
            cwd=REPO,
            text=True,
        ).splitlines()
    )
    allowed = REFINEMENT_RUNTIME_PATHS if protocol == "outline-v2" else OUTLINE_RUNTIME_PATHS
    require(changed and changed <= allowed, "outline runtime scope exceeds reviewed contract files")
    runtime_baseline(revision=runtime_revision)


def prepare(
    state, prior, canary, *, prior_manifest, protocol="two-turn-v1", runtime_revision=None, capacity_receipt=None
):
    """Local only: freeze original bytes and controls, without touching prior evidence."""
    verify_runtime(protocol, runtime_revision)
    limits = protocol_limits(protocol)
    root, prior, canary = private_path(state), private_path(prior), private_path(canary)
    require(not root.exists(), "fresh follow-up state required")
    require(not root.is_relative_to(prior) and not prior.is_relative_to(root), "prior evidence overlaps state")
    refinement = protocol == "outline-v2"
    require(refinement or capacity_receipt is None, "capacity policy is only supported by outline-v2")
    if refinement:
        from scripts.granular_pacing import validate_policy

        require(capacity_receipt is not None, "fresh capacity policy required")
        capacity_receipt = private_path(capacity_receipt)
        capacity_policy = validate_policy(json.loads(capacity_receipt.read_text()))
    outline = protocol != "two-turn-v1"
    completed = prior if outline else prior / "continuation-2"
    prior_result = json.loads((completed / "execution-result.json").read_text())
    if refinement:
        prior_status = json.loads((completed / "models/status.json").read_text())
        require(
            prior_result.get("status") == "failed"
            and prior_result.get("errorType") == "PacingRefusal"
            and prior_result["cleanup"]["passed"]
            and prior_status.get("status") == "failed"
            and prior_status.get("errorType") == "PacingRefusal"
            and prior_status.get("completedTurns") == 0
            and prior_result["finalOwnership"]["unresolvedOperations"] == 0
            and prior_result["finalOwnership"]["unfinishedMessages"] == 0,
            "prior failed outline run is not safely finalized",
        )
    else:
        require(
            prior_result["cleanup"]["passed"] and prior_result["models"]["completedTurns"] == (2 if outline else 6),
            "prior run incomplete",
        )
    public_path = (
        REPO
        / "records"
        / (
            "granular-outline-qualification-20261104.json"
            if refinement
            else "granular-navigation-followup-20261104.json"
            if outline
            else "granular-navigation-live-qualification-20261104.json"
        )
    )
    public = json.loads(public_path.read_text())
    prior_manifest = private_path(prior_manifest)
    require(
        sha(prior_manifest) == public["privateArchives"]["reviewedFinal"]["manifestSha256"],
        "prior private archive manifest differs",
    )
    archived = json.loads(prior_manifest.read_text())["files"]
    prefix = "" if outline else "continuation-2/"
    for name in (
        "fixtures/source.docx",
        "preparation.json" if outline else "fixtures/plan.json",
        "environment.json",
        "preflight.json",
        "account.json",
        prefix + "models/controls.json",
        prefix + "answering-binding-before.json",
        prefix + "embedding-binding-before.json",
    ):
        require(sha(prior / name) == archived[name], "archived prior evidence changed: " + name)
    receipt_names = ["models/controls.json", "execution-result.json", "cleanup-immediate.json", "cleanup-late.json"]
    if refinement:
        receipt_names += ["models/status.json", "ownership-final.json", "post-failure-control-verification.json"]
    for name in receipt_names:
        require(sha(completed / name) == public["privateEvidenceSha256"][name], "prior receipt changed: " + name)
        if refinement:
            require(sha(completed / name) == archived[name], "archived failed-prior receipt changed: " + name)
    prior_preflight = json.loads((prior / "preflight.json").read_text())
    require(sha(canary) == prior_preflight["canarySha256"], "prior canary hash differs")
    require(sha(prior / "environment.json") == prior_preflight["environmentSha256"], "prior environment changed")
    if outline:
        plan = json.loads((prior / "preparation.json").read_text())
        source = {"sha256": plan["sourceSha256"], "bytes": (prior / "fixtures/source.docx").stat().st_size}
    else:
        plan = json.loads((prior / "fixtures/plan.json").read_text())
        source = next(row for row in plan["sources"] if row["role"] == "source.docx")
    require(sha(prior / "fixtures/source.docx") == source["sha256"], "original fixture changed")
    prior_controls = json.loads((completed / "models/controls.json").read_text())
    if refinement:
        require(
            prior_controls.get("schema") == "fli.granular-outline-models.controls.v1"
            and prior_controls.get("modelTurns") == 1,
            "prior outline protocol differs",
        )
        require(
            capacity_policy["bindingSha256"]
            == json.loads((completed / "answering-binding-before.json").read_text())["bindingSha256"],
            "capacity policy answering binding differs",
        )
        postcheck = json.loads((completed / "post-failure-control-verification.json").read_text())
        require(
            postcheck.get("passed") is True
            and postcheck.get("beforeAndAfterExactPriorBindingsMatched") is True
            and postcheck.get("executionCodeHashesUnchanged") is True,
            "failed prior final control verification missing",
        )
    require(
        source["sha256"] == prior_controls["sourceBindings"]["docx"]["sourceSha256"],
        "fixture differs from prior model source",
    )
    require(source["bytes"] <= limits["sourceBytesEach"], "fixture exceeds byte cap")
    root.mkdir(parents=True, mode=0o700)
    (root / "fixtures").mkdir(mode=0o700)
    copies = {
        "account-seed.json": prior / "account.json",
        "inherited-preflight.json": prior / "preflight.json",
        "canary.json": canary,
        "prior-model-controls.json": completed / "models/controls.json",
        "prior-answering-binding.json": completed / "answering-binding-before.json",
        "prior-embedding-binding.json": completed / "embedding-binding-before.json",
        "fixtures/source.docx": prior / "fixtures/source.docx",
        "prior-archive-manifest.json": prior_manifest,
    }
    if refinement:
        copies["capacity-policy.json"] = capacity_receipt
        copies.update({"prior-" + name.replace("/", "-"): completed / name for name in receipt_names[1:]})
    for name, path in copies.items():
        (root / name).write_bytes(path.read_bytes())
        (root / name).chmod(0o600)
    env = json.loads((prior / "environment.json").read_text())
    env["FLI_METRICS_FILE"] = str(root / "metrics.jsonl")
    save(root / "environment.json", env)
    save(
        root / "preparation.json",
        {
            "baseline": protocol_baseline(protocol),
            **({"protocol": protocol, "runtimeRevision": runtime_revision} if outline else {}),
            "sourceSha256": source["sha256"],
            "priorReceiptSha256": sha(public_path),
            "copiedFileSha256": {name: sha(root / name) for name in copies},
            "limits": limits,
            "cloudCalls": 0,
            "priorEvidenceModified": False,
        },
    )
    return {"prepared": True, "sourceBytes": source["bytes"], "limits": limits, "cloudCalls": 0}


def verify_preparation(root, *, protocol="two-turn-v1"):
    prepared = json.loads((root / "preparation.json").read_text())
    require(prepared.get("protocol", "two-turn-v1") == protocol, "prepared protocol differs")
    verify_runtime(protocol, prepared.get("runtimeRevision"))
    baseline = protocol_baseline(protocol)
    require(
        prepared["limits"] == protocol_limits(protocol) and prepared["baseline"] == baseline, "follow-up limits changed"
    )
    for name, expected in prepared["copiedFileSha256"].items():
        require(sha(root / name) == expected, "frozen follow-up input changed: " + name)
    require(sha(root / "fixtures/source.docx") == prepared["sourceSha256"], "fixture bytes changed")
    if protocol == "outline-v2":
        from scripts.granular_pacing import validate_policy

        require("capacity-policy.json" in prepared["copiedFileSha256"], "unsealed capacity policy")
        validate_policy(json.loads((root / "capacity-policy.json").read_text()))
    return prepared


def require_capacity(root, binding, role):
    from scripts.granular_pacing import validate_capacity

    return validate_capacity(
        json.loads((root / "capacity-policy.json").read_text()),
        json.loads((root / f"answering-{role}.json").read_text()),
        binding_sha256=binding["bindingSha256"],
    )


def require_prior_bindings(root, answering, embedding):
    require(
        answering == json.loads((root / "prior-answering-binding.json").read_text()), "prior answering binding differs"
    )
    require(
        embedding == json.loads((root / "prior-embedding-binding.json").read_text()), "prior embedding binding differs"
    )


async def check_health(state, *, protocol="two-turn-v1"):
    root = private_path(state)
    verify_preparation(root, protocol=protocol)
    result = await preflight(root, root / "account-seed.json", root / "canary.json")
    settings = Settings.from_env()
    answering = await asyncio.to_thread(live.current_model, root, settings, "preflight")
    embedding = await asyncio.to_thread(live.current_model, root, settings, "preflight", embedding=True)
    require_prior_bindings(root, answering, embedding)
    if protocol == "outline-v2":
        require_capacity(root, answering, "preflight")
    save(
        root / "matched-preflight.json",
        {
            "passed": True,
            "capturedAt": datetime.now(UTC).isoformat(),
            "preflightSha256": sha(root / "preflight.json"),
            "answering": answering,
            "embedding": embedding,
        },
    )
    return {**result, "samePinnedModelAndEmbedding": True}


def execution_controls(root, *, protocol="two-turn-v1"):
    prepared = verify_preparation(root, protocol=protocol)
    require(not (root / "execution-started.json").exists(), "follow-up is single-use; preserve all attempts")
    require(
        not subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip(),
        "commit runner first",
    )
    preflight_receipt = json.loads((root / "preflight.json").read_text())
    matched = json.loads((root / "matched-preflight.json").read_text())
    age = (datetime.now(UTC) - datetime.fromisoformat(matched["capturedAt"])).total_seconds()
    require(0 <= age <= 900, "follow-up preflight older than fifteen minutes")
    require(
        matched["passed"] and matched["preflightSha256"] == sha(root / "preflight.json"), "matched preflight differs"
    )
    require_prior_bindings(root, matched["answering"], matched["embedding"])
    if protocol == "outline-v2":
        require_capacity(root, matched["answering"], "preflight")
    require(preflight_receipt["environmentSha256"] == sha(root / "environment.json"), "environment changed")
    require(preflight_receipt["protectedSha256"] == sha(root / "protected-before.json"), "protected receipt changed")
    return {
        "baseline": prepared["baseline"],
        **(
            {"protocol": protocol, "reviewedRuntimeRevision": prepared["runtimeRevision"]}
            if protocol != "two-turn-v1"
            else {}
        ),
        "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "sourceSha256": live.code_hashes(),
        "preparationSha256": sha(root / "preparation.json"),
        "matchedPreflightSha256": sha(root / "matched-preflight.json"),
        "limits": protocol_limits(protocol),
        "searchMode": "vector",
        "contextAccounting": "legacy-v1",
        "runtimeUnchangedFromCandidate": protocol == "two-turn-v1",
    }


async def postchecks(root, settings, client, bindings, controls):
    """Independent bounded observations after any attempted v2 run; never raise over its error.

    Source GETs have a 30s aggregate deadline. The two management reads run in
    parallel and each underlying subprocess has a 60s timeout; both are joined.
    This leaves at least 480s of the existing 600s window for owned cleanup.
    """
    from scripts.evaluate_agent_retrieval import capture

    started = time.monotonic()
    checks = {}

    def failed(error):
        return {"status": "failed", "errorType": type(error).__name__, "error": str(error)[:500]}

    if not bindings:
        checks["source"] = {"status": "unavailable", "reason": "source publication did not complete"}
    else:
        try:
            async with asyncio.timeout(30):
                observed = {}
                for role, source in bindings.items():
                    library = await client.get(f"/v1/libraries/{source['libraryId']}", timeout=10)
                    library.raise_for_status()
                    documents = await client.get(f"/v1/libraries/{source['libraryId']}/documents", timeout=10)
                    documents.raise_for_status()
                    matches = [row for row in documents.json()["documents"] if row.get("id") == source["documentId"]]
                    require(len(matches) == 1, "postcheck source document unavailable")
                    document, lib = matches[0], library.json()
                    require(
                        lib.get("status") == "active"
                        and lib.get("ingestionProfile", {}).get("id") == "custom-granular-v1"
                        and document.get("status") == "ready"
                        and document.get("pendingGeneration") is None
                        and document.get("activeGeneration") == source["generation"]
                        and document.get("contentRevision") == source["contentRevision"]
                        and document.get("sha256") == source["sourceSha256"]
                        and document.get("fileName") == source["fileName"],
                        "postcheck source publication pins changed",
                    )
                    observed[role] = {"library": capture(lib), "document": capture(document)}
                save(root / "postchecks-source-after.json", observed)
                before = root / "models/sources-before.json"
                if before.exists():
                    require(observed == json.loads(before.read_text()), "postcheck source snapshot changed")
                    checks["source"] = {"status": "passed", "comparison": "exact_pre_model_snapshot"}
                else:
                    checks["source"] = {
                        "status": "unavailable",
                        "reason": "pre-model source snapshot unavailable; publication pins verified only",
                    }
        except BaseException as error:
            checks["source"] = failed(error)

    async def binding_read(embedding):
        try:
            binding = await asyncio.to_thread(live.current_model, root, settings, "after", embedding=embedding)
            role = "embedding" if embedding else "answering"
            require(binding == json.loads((root / f"prior-{role}-binding.json").read_text()), role + " binding changed")
            return {"status": "passed", "binding": binding}
        except BaseException as error:
            return failed(error)

    reads = asyncio.gather(binding_read(False), binding_read(True))
    try:
        answering, embedding = await asyncio.shield(reads)
    except BaseException:
        # A caller cancellation cannot leave management threads using closing resources.
        answering, embedding = await asyncio.shield(reads)
    checks["answering"], checks["embedding"] = answering, embedding
    try:
        require(answering["status"] == "passed", "answering observation unavailable")
        require_capacity(root, answering["binding"], "after")
        checks["capacity"] = {"status": "passed"}
    except BaseException as error:
        checks["capacity"] = failed(error)
    try:
        require(live.code_hashes() == controls["sourceSha256"], "code changed during execution")
        checks["code"] = {"status": "passed"}
    except BaseException as error:
        checks["code"] = failed(error)
    result = {
        "passed": all(row["status"] == "passed" for row in checks.values()),
        "capturedAt": datetime.now(UTC).isoformat(),
        "elapsedSeconds": time.monotonic() - started,
        "checks": checks,
        "limits": {"sourceSeconds": 30, "parallelManagementSecondsEach": 60, "reservedCleanupSeconds": 480},
        "modelCalls": 0,
        "dataMutations": 0,
    }
    save(root / "postchecks.json", result)
    return result


async def execute(state, archive_root, *, protocol="two-turn-v1"):
    from scripts.granular_followup_models import prepare_cases, run_models

    root, destination = private_path(state), private_path(archive_root)
    require(not destination.exists(), "use a new archive destination")
    require(not destination.is_relative_to(root) and not root.is_relative_to(destination), "archive overlaps state")
    controls = execution_controls(root, protocol=protocol)
    limits = controls["limits"]
    save(root / "controls.json", controls)
    os.environ.update(json.loads((root / "environment.json").read_text()))
    settings = replace(Settings.from_env(), ingestion_code_revision=controls["candidateCommit"])
    require(
        settings.environment == "local" and settings.auth_mode == "dev" and settings.search_mode == "vector",
        "API controls differ",
    )
    protected = json.loads((root / "protected-before.json").read_text())
    preflight_receipt = json.loads((root / "preflight.json").read_text())
    deadline = time.monotonic() + limits["executionSeconds"]
    save(
        root / "execution-started.json",
        {"startedAt": datetime.now(UTC).isoformat(), "controlsSha256": sha(root / "controls.json")},
    )
    result, failure = {"candidateCommit": controls["candidateCommit"], "status": "started"}, None
    with FollowupLedger.create(root / "ownership", binding=controls, protected_library_ids=list(protected)) as ledger:
        async with (
            live.services(settings, ledger, root, deadline) as a,
            live.services(settings, ledger, root, deadline) as b,
        ):
            async with asyncio.timeout(300):
                for name in ("libraries", "documents", "chunks"):
                    actual = await a.database.get_container_client(name).read()
                    expected = json.loads((REPO / "cosmos" / f"{name}.container.json").read_text())
                    require(not definition_mismatches(expected, actual), "execution policy differs")
                    if name == "chunks":
                        require(
                            target_fingerprint(settings.cosmos_endpoint, settings.cosmos_database, name, actual["_rid"])
                            == preflight_receipt["targetFingerprint"],
                            "container incarnation changed",
                        )
                require(
                    await protected_snapshot(b.database, b.blob_container) == protected, "protected inventory changed"
                )
                model_binding = await asyncio.to_thread(live.current_model, root, settings, "before")
                embedding_binding = await asyncio.to_thread(
                    live.current_model, root, settings, "before", embedding=True
                )
                require_prior_bindings(root, model_binding, embedding_binding)
                if protocol == "outline-v2":
                    require_capacity(root, model_binding, "before")
            async with (
                live.loopback(a.app) as url,
                httpx.AsyncClient(
                    base_url=url,
                    timeout=120,
                    headers={"Authorization": "Bearer " + settings.dev_api_key, "X-Agent-Id": "owner"},
                ) as client,
            ):
                index = IndexClient(url, settings.dev_api_key, selector_mode="handles")
                q = live.Qualification(root, root / "fixtures", ledger, a, b, client, index, deadline)
                bindings = None
                try:
                    async with asyncio.timeout(max(1, deadline - time.monotonic())):
                        library = await q.create("shared")
                        document = await q.upload(library, "source.docx")
                        await q.drain()
                        row = await q.publication(library, document, "source.docx", 1)
                        q.passed("actual_same_source_publication_verified")
                    bindings = {
                        "docx": {
                            "libraryId": library,
                            "documentId": document,
                            "generation": row["activeGeneration"],
                            "contentRevision": row["contentRevision"],
                            "fileName": row["fileName"],
                            "sourceSha256": row["sha256"],
                        }
                    }
                    save(root / "source-bindings.json", bindings)
                    if protocol == "outline-v2":
                        model_binding = await asyncio.to_thread(live.current_model, root, settings, "model-start")
                        require_prior_bindings(root, model_binding, embedding_binding)
                        require_capacity(root, model_binding, "model-start")
                    prepare_cases(
                        root / "models",
                        bindings,
                        prior_controls=root / "prior-model-controls.json",
                        prior_model_binding=root / "prior-answering-binding.json",
                        protocol=protocol,
                        **({"capacity_receipt": root / "capacity-policy.json"} if protocol == "outline-v2" else {}),
                    )
                    provider = actual_client(settings.aoai_endpoint)
                    task = asyncio.create_task(
                        asyncio.to_thread(
                            run_models,
                            root / "models",
                            index=index,
                            agent_id="owner",
                            actual_client=provider,
                            deployment=settings.aoai_chat_deployment,
                            model_binding=model_binding,
                            deadline=deadline,
                            api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
                            protocol=protocol,
                        )
                    )
                    try:
                        result["models"] = await asyncio.shield(task)
                    finally:
                        try:
                            if not task.done():
                                await asyncio.shield(task)
                        finally:
                            provider.close()
                    require(
                        result["models"]["completedTurns"] == limits["modelTurns"],
                        "qualification turn count incomplete",
                    )
                    if protocol != "outline-v2":
                        require_prior_bindings(
                            root,
                            await asyncio.to_thread(live.current_model, root, settings, "after"),
                            await asyncio.to_thread(live.current_model, root, settings, "after", embedding=True),
                        )
                        require(live.code_hashes() == controls["sourceSha256"], "code changed during execution")
                    result["status"] = (
                        "one_outline_turn_complete_pending_review"
                        if protocol != "two-turn-v1"
                        else "two_model_turns_complete_pending_review"
                    )
                except BaseException as error:
                    failure = error
                    result.update(status="failed", errorType=type(error).__name__, error=str(error))
                finally:
                    cleanup_deadline = time.monotonic() + limits["cleanupSeconds"]
                    if protocol == "outline-v2":
                        try:
                            result["postchecks"] = await postchecks(root, settings, client, bindings, controls)
                            require(
                                result["postchecks"]["passed"], "one or more final control checks unavailable or failed"
                            )
                        except BaseException as error:
                            result.setdefault(
                                "postchecks",
                                {"passed": False, "errorType": type(error).__name__, "error": str(error)[:500]},
                            )
                            if failure is None:
                                failure = error
                                result.update(status="failed", errorType=type(error).__name__, error=str(error)[:500])
                    try:
                        await url.quiesce()
                    except BaseException as error:
                        save(
                            root / "quiescence-failed.json",
                            {
                                "status": "cleanup_blocked",
                                "errorType": type(error).__name__,
                                "ownership": ledger.summary(),
                                "cleanupAttempted": False,
                            },
                        )
                        raise
                    result.update(checks=q.checks, ownership=ledger.summary(), embedding=a.embedder.rows)
                    save(root / "execution-result.json", result)
                    save(root / "ownership-before-cleanup.json", ledger.snapshot())
                    result["precleanupArchive"] = live.archive(root, destination / "before-cleanup")
                    print(
                        json.dumps(
                            {
                                "status": result["status"],
                                "modelTurns": result.get("models", {}).get("completedTurns", 0),
                                "archivedBeforeCleanup": True,
                            }
                        ),
                        flush=True,
                    )
                    try:
                        remaining = (
                            max(1, cleanup_deadline - time.monotonic())
                            if protocol == "outline-v2"
                            else limits["cleanupSeconds"]
                        )
                        async with asyncio.timeout(remaining):
                            async with httpx.AsyncClient(
                                transport=httpx.ASGITransport(app=a.app),
                                base_url="http://qualification.invalid",
                                headers={"Authorization": "Bearer " + settings.dev_api_key, "X-Agent-Id": "owner"},
                            ) as cleanup_client:
                                result["cleanup"] = await live.cleanup(root, a, b, ledger, cleanup_client, protected)
                    except BaseException as error:
                        result["cleanup"] = {"passed": False, "errorType": type(error).__name__, "error": str(error)}
                        failure = failure or error
                    index.http.close()
                    result["finalOwnership"] = ledger.summary()
                    save(root / "execution-result.json", result)
                    save(root / "ownership-final.json", ledger.snapshot())
    result["finalArchive"] = live.archive(root, destination / "after-cleanup")
    save(root / "archive-receipts.json", {key: result[key] for key in ("precleanupArchive", "finalArchive")})
    print(
        json.dumps(
            {
                "status": result["status"],
                "cleanupPassed": result.get("cleanup", {}).get("passed"),
                "modelTurns": result.get("models", {}).get("completedTurns", 0),
            }
        ),
        flush=True,
    )
    if failure:
        raise failure
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "preflight", "execute"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--canary", type=Path)
    parser.add_argument("--prior-manifest", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--protocol", choices=("two-turn-v1", "outline-v1", "outline-v2"), default="two-turn-v1")
    parser.add_argument("--runtime-revision")
    parser.add_argument("--capacity-receipt", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    if args.action == "prepare":
        require(
            args.prior is not None and args.canary is not None and args.prior_manifest is not None,
            "prior, canary and prior manifest required",
        )
        result = prepare(
            args.state,
            args.prior,
            args.canary,
            prior_manifest=args.prior_manifest,
            protocol=args.protocol,
            runtime_revision=args.runtime_revision,
            capacity_receipt=args.capacity_receipt,
        )
    elif args.action == "preflight":
        result = asyncio.run(check_health(args.state, protocol=args.protocol))
    else:
        require(args.archive is not None, "archive destination required")
        result = asyncio.run(execute(args.state, args.archive, protocol=args.protocol))
        result = {"status": result["status"], "cleanupPassed": result.get("cleanup", {}).get("passed")}
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
