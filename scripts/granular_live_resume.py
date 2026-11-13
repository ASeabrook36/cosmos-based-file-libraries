"""Single bounded continuation of the independently reconciled numeric-only guard failure.

Reuses the same owned scopes and remaining counters. The original attempt, controls
and hash-chained ledger are preserved; no failed turn or resource count is reset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from fli.config import Settings
from fli.store import public
from fli_demo.client import IndexClient
from scripts import granular_live as live
from scripts.canary import definition_mismatches, target_fingerprint
from scripts.granular_live_models import prepare_cases, run_models
from scripts.granular_live_preflight import protected_snapshot
from scripts.granular_live_safety import OwnedLedger
from scripts.hybrid_model_meter import actual_client
from scripts.hybrid_quality_scope import save
from scripts.qualify_granular_navigation import LIMITS, REPO, require, sha, verify
from scripts.retrieval_controls import private_path


def continuation_controls(prior):
    controls = json.loads((prior / "controls.json").read_text())
    result = json.loads((prior / "execution-result.json").read_text())
    ownership = json.loads((prior / "ownership-final.json").read_text())
    proof = json.loads((prior / "reconciliation-proof.json").read_text())
    require(
        result["status"] == "failed" and not result["checks"] and not (prior / "models").exists(),
        "not the reviewed pre-model publication failure",
    )
    require(
        result["cleanup"]["passed"] is False and proof["semanticEquality"] is True, "reviewed reconciliation required"
    )
    require(
        len(ownership["libraries"]) == 3 and len(ownership["documents"]) == 3 and len(ownership["generations"]) == 3,
        "prior scope counts differ",
    )
    unknown = [row for row in ownership["operations"] if row["status"] not in {"confirmed", "rejected"}]
    require(len(unknown) == 1 and unknown[0]["kind"] == "document-replace", "unexpected unresolved mutations")
    require(
        unknown[0]["bodySha256"] == proof["matchedExactOriginalProposedSha256"], "reconciliation proposal hash differs"
    )
    embeddings = json.loads((prior / "embedding-attempts.json").read_text())
    require(all(row["status"] in {"response", "error"} for row in embeddings), "unfinished embedding call")
    require(embeddings == result["embedding"], "prior embedding receipt differs from execution")
    require(0 < len(embeddings) <= LIMITS["logicalEmbeddingRequests"], "inherited embedding call ceiling")
    require(
        [r["logicalCall"] for r in embeddings] == list(range(1, len(embeddings) + 1)),
        "inherited embedding call sequence",
    )
    for row in embeddings:
        for key in ("localInputTokens", "physicalRequests", "retries", "throttles", "unmeteredAttempts"):
            require(type(row[key]) is int and row[key] >= 0, "invalid inherited embedding counter")
        require(1 <= row["physicalRequests"] <= LIMITS["embeddingAttemptsEach"], "inherited embedding attempt ceiling")
        require(
            row["retries"] == row["physicalRequests"] - 1 and row["throttles"] <= row["physicalRequests"],
            "inherited attempt counters differ",
        )
        known = row["reportedPromptTokens"]
        require(known is None or (type(known) is int and known >= 0), "invalid inherited provider usage")
        require(
            row["unmeteredAttempts"] == row["physicalRequests"] - (known is not None),
            "inherited unknown attempts differ",
        )
    require(
        sum(row["localInputTokens"] for row in embeddings) <= LIMITS["sourceEmbeddingTokens"],
        "inherited embedding token ceiling",
    )
    started = datetime.fromisoformat(json.loads((prior / "execution-started.json").read_text())["startedAt"])
    last = json.loads(sorted((prior / "ownership/events").glob("*.json"))[-1].read_text())
    finished = datetime.fromisoformat(last["at"])
    require(
        started.tzinfo is not None and finished.tzinfo is not None and finished >= started,
        "invalid prior execution chronology",
    )
    # Deliberately round up and reserve another minute for the failed teardown.
    consumed = math.ceil((finished - started).total_seconds()) + 60
    require(0 < consumed < LIMITS["executionSeconds"], "prior execution consumed the time allowance")
    return controls, ownership, unknown[0], embeddings, consumed


async def resume(prior, destination):
    prior, destination = private_path(prior), private_path(destination)
    root = prior / "continuation-2"
    require(not root.exists() and not destination.exists(), "continuation is single-use")
    require(not destination.is_relative_to(prior) and not prior.is_relative_to(destination), "archive overlaps state")
    original_controls, old_ownership, operation, embeddings, consumed = continuation_controls(prior)
    current_hashes = live.code_hashes()
    require(
        {k: v for k, v in current_hashes.items() if k.startswith("src/")}
        == {k: v for k, v in original_controls["sourceSha256"].items() if k.startswith("src/")},
        "numeric guard continuation cannot change source-navigation runtime",
    )
    require(
        not subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip(),
        "commit reviewed continuation first",
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    verify(prior / "fixtures")
    root.mkdir(mode=0o700)
    for name in ("environment.json", "account.json", "deployments.json"):
        (root / name).write_bytes((prior / name).read_bytes())
        (root / name).chmod(0o600)
    save(root / "embedding-attempts.json", embeddings)
    controls = {
        "candidateCommit": revision,
        "sourceSha256": live.code_hashes(),
        "limits": LIMITS,
        "priorControlsSha256": sha(prior / "controls.json"),
        "priorResultSha256": sha(prior / "execution-result.json"),
        "priorOwnershipHeadSha256": old_ownership["eventHeadSha256"],
        "reconciliationProofSha256": sha(prior / "reconciliation-proof.json"),
        "priorExecutionSecondsUpperBound": consumed,
        "remainingExecutionSeconds": LIMITS["executionSeconds"] - consumed,
        "priorEmbeddingLogicalCalls": len(embeddings),
        "priorModelTurns": 0,
        "newLibraryAllowance": 0,
        "searchMode": "vector",
        "contextAccounting": "legacy-v1",
        "sourceRuntimeUnchanged": True,
    }
    save(root / "controls.json", controls)
    env = json.loads((root / "environment.json").read_text())
    os.environ.update(env)
    settings = replace(Settings.from_env(), ingestion_code_revision=revision)
    protected = json.loads((prior / "protected-before.json").read_text())
    preflight = json.loads((prior / "preflight.json").read_text())
    result, failure = {"candidateCommit": revision, "status": "started", "continuesPriorAttempt": True}, None
    deadline = time.monotonic() + controls["remainingExecutionSeconds"]
    save(root / "execution-started.json", {"startedAt": datetime.now(UTC).isoformat()})
    with OwnedLedger.open(
        prior / "ownership", binding=original_controls, protected_library_ids=list(protected)
    ) as ledger:
        require(ledger.snapshot()["eventHeadSha256"] == old_ownership["eventHeadSha256"], "prior ledger changed")
        async with (
            live.services(settings, ledger, root, deadline) as a,
            live.services(settings, ledger, root, deadline) as b,
        ):
            async with asyncio.timeout(300):
                for name in ("libraries", "documents", "chunks"):
                    actual = await a.database.get_container_client(name).read()
                    require(
                        not definition_mismatches(
                            json.loads((REPO / "cosmos" / f"{name}.container.json").read_text()), actual
                        ),
                        "container policy changed",
                    )
                    if name == "chunks":
                        require(
                            target_fingerprint(settings.cosmos_endpoint, settings.cosmos_database, name, actual["_rid"])
                            == preflight["targetFingerprint"],
                            "container incarnation changed",
                        )
                require(
                    await protected_snapshot(b.database, b.blob_container, list(protected)) == protected,
                    "protected scope changed",
                )
                document = operation["key"].split(":", 1)[1]
                library = old_ownership["documents"][document]["libraryId"]
                observed = await b.database.get_container_client("documents").read_item(document, partition_key=library)
                previous = json.loads((prior / "reconciliation-readback.json").read_text())
                require(
                    public(observed) == public(previous) and observed["_etag"] == previous["_etag"],
                    "reconciliation readback changed",
                )
                save(root / "fresh-reconciliation-observation.json", observed)
                ledger.confirm_reconciled(
                    operation["id"],
                    json.loads((prior / "reconciled-proposed-body.json").read_text()),
                    observed,
                    evidence_path=prior / "reconciliation-proof.json",
                    evidence_sha256=sha(prior / "reconciliation-proof.json"),
                )
                a.queue.cancel_pending(
                    "reviewed numeric-only publication confirmation; replay exact existing ingestion jobs"
                )
                libraries = [await a.libraries.get(identity) for identity in old_ownership["libraries"]]
                personal = next(row["id"] for row in libraries if row["kind"] == "personal")
                documents = [
                    await a.documents.get(row["libraryId"], identity)
                    for identity, row in old_ownership["documents"].items()
                ]
                docx = next(
                    row for row in documents if row["fileName"] == "source.docx" and row["libraryId"] != personal
                )
                markdown = next(row for row in documents if row["fileName"] == "source.md")
                lifecycle = next(row for row in documents if row["libraryId"] == personal)
                shared = docx["libraryId"]
                existing = {
                    "shared": shared,
                    "personal": personal,
                    "foreign": next(row["id"] for row in libraries if row["id"] not in {shared, personal}),
                    "docx": docx["id"],
                    "markdown": markdown["id"],
                    "lifecycle": lifecycle["id"],
                }
                save(root / "existing-source-roles.json", existing)
                for row in documents:
                    require(
                        row["generation"] == 1 and row["fileName"] in {"source.docx", "source.md"},
                        "prior source changed",
                    )
                    await a.queue.send(
                        {"type": "ingest", "libraryId": row["libraryId"], "documentId": row["id"], "generation": 1},
                        session_id=row["libraryId"],
                    )
                model_binding = await asyncio.to_thread(live.current_model, root, settings, "before")
                embedding_binding = await asyncio.to_thread(
                    live.current_model, root, settings, "before", embedding=True
                )
            async with (
                live.loopback(a.app) as url,
                httpx.AsyncClient(
                    base_url=url,
                    timeout=120,
                    headers={"Authorization": "Bearer " + settings.dev_api_key, "X-Agent-Id": "owner"},
                ) as client,
            ):
                index = IndexClient(url, settings.dev_api_key, selector_mode="handles")
                q = live.Qualification(root, prior / "fixtures", ledger, a, b, client, index, deadline)
                try:
                    async with asyncio.timeout(max(1, deadline - time.monotonic())):
                        bindings = await q.mechanics(existing)
                    prepare_cases(root / "models", bindings)
                    provider = actual_client(settings.aoai_endpoint)
                    model_task = asyncio.create_task(
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
                        result["models"] = await asyncio.shield(model_task)
                    finally:
                        if not model_task.done():
                            await asyncio.shield(model_task)
                        provider.close()
                    require(
                        await asyncio.to_thread(live.current_model, root, settings, "after") == model_binding,
                        "answering model drift",
                    )
                    require(
                        await asyncio.to_thread(live.current_model, root, settings, "after", embedding=True)
                        == embedding_binding,
                        "embedding model drift",
                    )
                    require(live.code_hashes() == controls["sourceSha256"], "continuation source drift")
                    result["status"] = "mechanics_and_model_execution_complete_pending_review"
                except BaseException as error:
                    failure = error
                    result.update(status="failed", errorType=type(error).__name__, error=str(error))
                finally:
                    await url.quiesce()
                    result.update(checks=q.checks, ownership=ledger.summary(), embedding=a.embedder.rows)
                    save(root / "execution-result.json", result)
                    save(root / "ownership-before-cleanup.json", ledger.snapshot())
                    result["precleanupArchive"] = live.archive(prior, destination / "before-cleanup")
                    print(
                        json.dumps(
                            {"status": result["status"], "checksPassed": len(q.checks), "archivedBeforeCleanup": True}
                        ),
                        flush=True,
                    )
                    try:
                        async with asyncio.timeout(LIMITS["cleanupSeconds"] - 60):
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
    result["finalArchive"] = live.archive(prior, destination / "after-cleanup")
    save(root / "archive-receipts.json", {key: result[key] for key in ("precleanupArchive", "finalArchive")})
    print(
        json.dumps(
            {
                "status": result["status"],
                "checksPassed": len(result.get("checks", [])),
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
    parser.add_argument("--prior-state", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.WARNING)
    try:
        asyncio.run(resume(args.prior_state, args.archive))
    except BaseException as error:
        root = private_path(args.prior_state) / "continuation-2"
        if root.exists() and not (root / "execution-result.json").exists():
            save(
                root / "preflight-failed.json",
                {
                    "status": "continuation_preflight_failed",
                    "errorType": type(error).__name__,
                    "cleanupAttempted": False,
                    "reason": "Retain original owned scopes and appended reconciliation ledger for review.",
                },
            )
            live.archive(private_path(args.prior_state), private_path(args.archive) / "preflight-failed")
        raise


if __name__ == "__main__":
    main()
