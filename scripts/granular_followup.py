"""Two-turn, same-controls granular follow-up; private evidence and owned scopes only."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
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


def runtime_baseline():
    paths = [*REPO.glob("src/**/*.py"), *REPO.glob("cosmos/*.json"), REPO / "requirements-local.lock"]
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASELINE], cwd=REPO, text=True)
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
        frozen = subprocess.check_output(["git", "show", BASELINE + ":" + relative], cwd=REPO)
        require(path.read_bytes() == frozen, "follow-up cannot change candidate runtime: " + relative)
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair remains separate")


def prepare(state, prior, canary, *, prior_manifest):
    """Local only: freeze original bytes and controls, without touching prior evidence."""
    runtime_baseline()
    root, prior, canary = private_path(state), private_path(prior), private_path(canary)
    require(not root.exists(), "fresh follow-up state required")
    require(not root.is_relative_to(prior) and not prior.is_relative_to(root), "prior evidence overlaps state")
    completed = prior / "continuation-2"
    prior_result = json.loads((completed / "execution-result.json").read_text())
    require(prior_result["cleanup"]["passed"] and prior_result["models"]["completedTurns"] == 6, "prior run incomplete")
    public = json.loads((REPO / "records/granular-navigation-live-qualification-20261104.json").read_text())
    prior_manifest = private_path(prior_manifest)
    require(
        sha(prior_manifest) == public["privateArchives"]["reviewedFinal"]["manifestSha256"],
        "prior private archive manifest differs",
    )
    archived = json.loads(prior_manifest.read_text())["files"]
    for name in (
        "fixtures/source.docx",
        "fixtures/plan.json",
        "environment.json",
        "preflight.json",
        "account.json",
        "continuation-2/models/controls.json",
        "continuation-2/answering-binding-before.json",
        "continuation-2/embedding-binding-before.json",
    ):
        require(sha(prior / name) == archived[name], "archived prior evidence changed: " + name)
    for name in ("models/controls.json", "execution-result.json", "cleanup-immediate.json", "cleanup-late.json"):
        require(sha(completed / name) == public["privateEvidenceSha256"][name], "prior receipt changed: " + name)
    prior_preflight = json.loads((prior / "preflight.json").read_text())
    require(sha(canary) == prior_preflight["canarySha256"], "prior canary hash differs")
    require(sha(prior / "environment.json") == prior_preflight["environmentSha256"], "prior environment changed")
    plan = json.loads((prior / "fixtures/plan.json").read_text())
    source = next(row for row in plan["sources"] if row["role"] == "source.docx")
    require(sha(prior / "fixtures/source.docx") == source["sha256"], "original fixture changed")
    prior_controls = json.loads((completed / "models/controls.json").read_text())
    require(
        source["sha256"] == prior_controls["sourceBindings"]["docx"]["sourceSha256"],
        "fixture differs from prior model source",
    )
    require(source["bytes"] <= LIMITS["sourceBytesEach"], "fixture exceeds byte cap")
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
    for name, path in copies.items():
        (root / name).write_bytes(path.read_bytes())
        (root / name).chmod(0o600)
    env = json.loads((prior / "environment.json").read_text())
    env["FLI_METRICS_FILE"] = str(root / "metrics.jsonl")
    save(root / "environment.json", env)
    save(
        root / "preparation.json",
        {
            "baseline": BASELINE,
            "sourceSha256": source["sha256"],
            "priorReceiptSha256": sha(REPO / "records/granular-navigation-live-qualification-20261104.json"),
            "copiedFileSha256": {name: sha(root / name) for name in copies},
            "limits": LIMITS,
            "cloudCalls": 0,
            "priorEvidenceModified": False,
        },
    )
    return {"prepared": True, "sourceBytes": source["bytes"], "limits": LIMITS, "cloudCalls": 0}


def verify_preparation(root):
    runtime_baseline()
    prepared = json.loads((root / "preparation.json").read_text())
    require(prepared["limits"] == LIMITS and prepared["baseline"] == BASELINE, "follow-up limits changed")
    for name, expected in prepared["copiedFileSha256"].items():
        require(sha(root / name) == expected, "frozen follow-up input changed: " + name)
    require(sha(root / "fixtures/source.docx") == prepared["sourceSha256"], "fixture bytes changed")
    return prepared


def require_prior_bindings(root, answering, embedding):
    require(
        answering == json.loads((root / "prior-answering-binding.json").read_text()), "prior answering binding differs"
    )
    require(
        embedding == json.loads((root / "prior-embedding-binding.json").read_text()), "prior embedding binding differs"
    )


async def check_health(state):
    root = private_path(state)
    verify_preparation(root)
    result = await preflight(root, root / "account-seed.json", root / "canary.json")
    settings = Settings.from_env()
    answering = await asyncio.to_thread(live.current_model, root, settings, "preflight")
    embedding = await asyncio.to_thread(live.current_model, root, settings, "preflight", embedding=True)
    require_prior_bindings(root, answering, embedding)
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


def execution_controls(root):
    verify_preparation(root)
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
    require(preflight_receipt["environmentSha256"] == sha(root / "environment.json"), "environment changed")
    require(preflight_receipt["protectedSha256"] == sha(root / "protected-before.json"), "protected receipt changed")
    return {
        "baseline": BASELINE,
        "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "sourceSha256": live.code_hashes(),
        "preparationSha256": sha(root / "preparation.json"),
        "matchedPreflightSha256": sha(root / "matched-preflight.json"),
        "limits": LIMITS,
        "searchMode": "vector",
        "contextAccounting": "legacy-v1",
        "runtimeUnchangedFromCandidate": True,
    }


async def execute(state, archive_root):
    from scripts.granular_followup_models import prepare_cases, run_models

    root, destination = private_path(state), private_path(archive_root)
    require(not destination.exists(), "use a new archive destination")
    require(not destination.is_relative_to(root) and not root.is_relative_to(destination), "archive overlaps state")
    controls = execution_controls(root)
    save(root / "controls.json", controls)
    os.environ.update(json.loads((root / "environment.json").read_text()))
    settings = replace(Settings.from_env(), ingestion_code_revision=controls["candidateCommit"])
    require(
        settings.environment == "local" and settings.auth_mode == "dev" and settings.search_mode == "vector",
        "API controls differ",
    )
    protected = json.loads((root / "protected-before.json").read_text())
    preflight_receipt = json.loads((root / "preflight.json").read_text())
    deadline = time.monotonic() + LIMITS["executionSeconds"]
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
                    prepare_cases(
                        root / "models",
                        bindings,
                        prior_controls=root / "prior-model-controls.json",
                        prior_model_binding=root / "prior-answering-binding.json",
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
                    require(result["models"]["completedTurns"] == 2, "two-turn qualification incomplete")
                    require_prior_bindings(
                        root,
                        await asyncio.to_thread(live.current_model, root, settings, "after"),
                        await asyncio.to_thread(live.current_model, root, settings, "after", embedding=True),
                    )
                    require(live.code_hashes() == controls["sourceSha256"], "code changed during execution")
                    result["status"] = "two_model_turns_complete_pending_review"
                except BaseException as error:
                    failure = error
                    result.update(status="failed", errorType=type(error).__name__, error=str(error))
                finally:
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
                        async with asyncio.timeout(LIMITS["cleanupSeconds"]):
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
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    if args.action == "prepare":
        require(
            args.prior is not None and args.canary is not None and args.prior_manifest is not None,
            "prior, canary and prior manifest required",
        )
        result = prepare(args.state, args.prior, args.canary, prior_manifest=args.prior_manifest)
    elif args.action == "preflight":
        result = asyncio.run(check_health(args.state))
    else:
        require(args.archive is not None, "archive destination required")
        result = asyncio.run(execute(args.state, args.archive))
        result = {"status": result["status"], "cleanupPassed": result.get("cleanup", {}).get("passed")}
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
