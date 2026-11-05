"""Single-use compact/navigation model pair against an exact local source snapshot.

Only explicit preflight/execute actions contact the pinned answering deployment.
The normal local API uses in-memory stores: no Cosmos, Blob, embedding, worker,
queue, deployment, or resource-management writes are performed.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from fli.api import create_app
from fli.blobs import MemoryBlobs
from fli.checkpoints import digest, encoded
from fli.config import Settings
from fli.errors import ApiError
from fli.granular_source import validate_sections
from fli.queue import MemoryQueue
from fli.search import Searcher
from fli.store import MemoryDocumentStore, MemoryLibraryStore
from fli_demo.agent import MAX_ROUNDS, run_turn
from fli_demo.client import IndexClient
from scripts.evaluate_agent_retrieval import capture
from scripts.granular_live import archive, code_hashes
from scripts.granular_live_models import CapturedIndex, DeadlineHTTP, _source_snapshot
from scripts.granular_live_preflight import management_get
from scripts.granular_outline_case import cases, review_template
from scripts.granular_pacing import CONTEXT, MODEL, OUTPUT, Pacer, chat, validate_capacity, validate_policy
from scripts.hybrid_model_meter import Meter, actual_client, meter_summary
from scripts.qualify_granular_navigation import require, save_new, sha
from scripts.retrieval_controls import AGENT_INSTRUCTIONS, deployment_binding, private_path

REPO = Path(__file__).resolve().parents[1]
BASELINE = "e8058d7"
SCHEMA = "fli.granular-reference-pair.v1"
CELLS = ("compact", "navigation")
LIMITS = {
    "logicalCalls": 26,
    "physicalResponsesAttempts": 36,
    "inputTokensWithUnknownReservations": 2000000,
    "outputTokensWithUnknownReservations": 200000,
}
EXECUTION_SECONDS = 1200


def read(path):
    return json.loads(Path(path).read_text())


def now():
    return datetime.now(UTC).isoformat()


def _fixture(root):
    publication = read(root / "fixture/publication.json")
    snapshot = read(root / "fixture/source-snapshot.json")
    source = read(root / "fixture/source-bindings.json")
    require(set(source) == set(snapshot) == {"docx"}, "one DOCX snapshot required")
    document, sections, binding = publication["document"], publication["sections"], source["docx"]
    require(
        document["sha256"] == binding["sourceSha256"] == sha(root / "fixture/source.docx")
        and document["sizeBytes"] == (root / "fixture/source.docx").stat().st_size,
        "original DOCX changed",
    )
    require(
        document["id"] == binding["documentId"]
        and document["libraryId"] == binding["libraryId"]
        and document["activeGeneration"] == binding["generation"]
        and document["contentRevision"] == binding["contentRevision"]
        and document["fileName"] == binding["fileName"]
        and document["status"] == "ready"
        and document["pendingGeneration"] is None,
        "source publication pin mismatch",
    )
    require(
        snapshot["docx"]["library"]["id"] == binding["libraryId"]
        and snapshot["docx"]["library"]["status"] == "active"
        and snapshot["docx"]["library"]["ingestionProfile"]["id"] == "custom-granular-v1",
        "source library mismatch",
    )
    validate_sections(sections, document)
    require(digest(sections) == document["fullText"]["sha256"], "immutable source artifact changed")
    require(document["fullText"]["generation"] == binding["generation"], "source artifact generation changed")
    require(document["imageAssets"]["assetCount"] == 0, "this qualification has no image backend")
    return publication, snapshot, source


def prepare(state, prior, *, prior_manifest):
    """Copy hash-anchored inputs locally; no clients, credentials or cloud calls."""
    root, previous, manifest_path = private_path(state), private_path(prior), private_path(prior_manifest)
    require(not root.exists(), "fresh private comparison directory required")
    require(not root.is_relative_to(previous) and not previous.is_relative_to(root), "prior evidence overlaps")
    public_path = REPO / "records/granular-refinement-qualification-20261105.json"
    public = read(public_path)
    expected = next(row for row in public["privateArchives"]["archives"] if row["role"] == "reviewed-final")
    require(sha(manifest_path) == expected["manifestSha256"], "reviewed prior archive differs")
    files = read(manifest_path)["files"]
    bindings = read(previous / "source-bindings.json")
    doc = bindings["docx"]
    publication = f"{doc['documentId']}-publication-{doc['generation']}.json"
    copies = {
        "fixture/source.docx": "fixtures/source.docx",
        "fixture/publication.json": publication,
        "fixture/source-snapshot.json": "models/sources-before.json",
        "fixture/source-bindings.json": "source-bindings.json",
        "prior-answering-binding.json": "answering-binding-after.json",
        "capacity-policy.json": "capacity-policy.json",
        "account-seed.json": "account.json",
        "prior-execution-result.json": "execution-result.json",
    }
    for name in (*copies.values(), "environment.json"):
        require(sha(previous / name) == files[name], "prior archive input changed: " + name)
    prior_result = read(previous / "execution-result.json")
    require(
        prior_result["cleanup"]["passed"]
        and prior_result["models"]["completedTurns"] == 1
        and prior_result["finalOwnership"]["unresolvedOperations"] == 0
        and prior_result["finalOwnership"]["unfinishedMessages"] == 0,
        "prior qualification not safely finalized",
    )
    policy = validate_policy(read(previous / "capacity-policy.json"))
    require(
        policy["bindingSha256"] == read(previous / "answering-binding-after.json")["bindingSha256"], "policy binding"
    )
    config = {key: read(previous / "environment.json")[key] for key in ("AOAI_ENDPOINT", "AOAI_CHAT_DEPLOYMENT")}
    root.mkdir(mode=0o700, parents=True)
    for destination, name in copies.items():
        target = root / destination
        target.parent.mkdir(mode=0o700, exist_ok=True)
        target.write_bytes((previous / name).read_bytes())
        target.chmod(0o600)
    (root / "prior-archive-manifest.json").write_bytes(manifest_path.read_bytes())
    (root / "prior-archive-manifest.json").chmod(0o600)
    save_new(root / "model-config.json", config)
    _, _, source = _fixture(root)
    case = cases(source)[0]
    save_new(
        root / "controls.json",
        {
            "schema": SCHEMA,
            "baseline": BASELINE,
            "cells": list(CELLS),
            "cellOrderInterpretation": "Fixed compact then navigation; development pair, not a randomized benchmark.",
            "case": case,
            "model": MODEL,
            "modelBinding": read(root / "prior-answering-binding.json"),
            "pacing": policy,
            "meterLimits": LIMITS,
            "contextAllowance": CONTEXT,
            "outputAllowance": OUTPUT,
            "reasoningEffort": "medium",
            "maxRounds": MAX_ROUNDS,
            "agentInstructions": AGENT_INSTRUCTIONS,
            "evidenceView": "compact",
            "selectors": "handles",
            "contextAccounting": "legacy-v1",
            "searchMode": "vector",
            "sourceBackend": "hash_verified_local_snapshot; search_unavailable",
            "executionAdmissionSeconds": EXECUTION_SECONDS,
            "codeSha256": code_hashes(),
            "inputSha256": {str(p.relative_to(root)): sha(p) for p in root.rglob("*") if p.is_file()},
            "priorPublicReceiptSha256": sha(public_path),
            "privateEvidence": True,
            "cloudDataWrites": False,
            "largerConfirmation": False,
        },
    )
    save_new(root / "prepared.json", {"capturedAt": now(), "controlsSha256": sha(root / "controls.json")})
    return {"prepared": True, "cells": list(CELLS), "modelCalls": 0, "cloudCalls": 0}


def verify(root):
    require(sha(root / "controls.json") == read(root / "prepared.json")["controlsSha256"], "sealed controls changed")
    controls = read(root / "controls.json")
    require(controls["schema"] == SCHEMA and controls["baseline"] == BASELINE, "comparison controls differ")
    require(controls["codeSha256"] == code_hashes(), "comparison code drift")
    require(all(sha(root / name) == value for name, value in controls["inputSha256"].items()), "comparison input drift")
    _, _, source = _fixture(root)
    require(controls["case"] == cases(source)[0], "sealed question or source criteria changed")
    for key, value in {
        "cells": list(CELLS),
        "model": MODEL,
        "meterLimits": LIMITS,
        "contextAllowance": CONTEXT,
        "outputAllowance": OUTPUT,
        "reasoningEffort": "medium",
        "maxRounds": MAX_ROUNDS,
        "agentInstructions": AGENT_INSTRUCTIONS,
        "evidenceView": "compact",
        "selectors": "handles",
        "contextAccounting": "legacy-v1",
        "searchMode": "vector",
        "executionAdmissionSeconds": EXECUTION_SECONDS,
        "sourceBackend": "hash_verified_local_snapshot; search_unavailable",
        "cloudDataWrites": False,
        "largerConfirmation": False,
    }.items():
        require(type(controls.get(key)) is type(value) and controls[key] == value, "changed comparison control: " + key)
    require(controls["modelBinding"] == read(root / "prior-answering-binding.json"), "answering binding changed")
    require(controls["pacing"] == read(root / "capacity-policy.json"), "pacing changed")
    validate_policy(controls["pacing"])
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair remains separate")
    return controls


def current_binding(root, role):
    config, controls = read(root / "model-config.json"), verify(root)
    seed = read(root / "account-seed.json")
    account = management_get(
        "https://management.azure.com" + seed["id"] + "?api-version=2024-10-01", root / f"account-{role}.json"
    )
    deployment = management_get(
        "https://management.azure.com"
        + seed["id"]
        + "/deployments/"
        + config["AOAI_CHAT_DEPLOYMENT"]
        + "?api-version=2024-10-01",
        root / f"answering-{role}.json",
    )
    binding = deployment_binding(
        {"capturedAt": now(), "account": account, "deployment": deployment},
        configured_endpoint=config["AOAI_ENDPOINT"],
        configured_deployment=config["AOAI_CHAT_DEPLOYMENT"],
        model=MODEL["name"],
        revision=MODEL["version"],
    )
    require(binding == controls["modelBinding"], "fresh answering deployment differs")
    validate_capacity(controls["pacing"], deployment, binding_sha256=binding["bindingSha256"])
    save_new(root / f"binding-{role}.json", {"capturedAt": now(), "binding": binding})
    return binding


def preflight(state):
    root = private_path(state)
    verify(root)
    require(not (root / "preflight.json").exists(), "preflight is single-use")
    binding = current_binding(root, "preflight")
    result = {"passed": True, "capturedAt": now(), "binding": binding, "modelCalls": 0, "cloudDataWrites": 0}
    save_new(root / "preflight.json", result)
    return result


class NoSearch:
    """Do not manufacture ranking or silently call an embedding service."""

    model, dimensions, version = "text-embedding-3-large", 3072, "1"

    async def embed(self, *args, **kwargs):
        raise ApiError(
            409, "snapshot_search_unavailable", "This frozen local source qualification supports navigation reads only."
        )

    async def search(self, *args, **kwargs):
        return await self.embed()


@contextmanager
def local_index(root):
    publication, snapshot, source = _fixture(root)
    libraries, documents, blobs = MemoryLibraryStore(), MemoryDocumentStore(), MemoryBlobs()
    library, document = copy.deepcopy(snapshot["docx"]["library"]), copy.deepcopy(publication["document"])
    libraries.m.items[(library["id"], library["id"])] = library
    documents.m.items[(library["id"], document["id"])] = document
    blobs.blobs[document["fullText"]["blobPath"]] = (encoded(publication["sections"]), "application/json")
    # Navigation reads validate the complete empty-image manifest as normal.
    empty = {
        "version": 1,
        "generation": source["docx"]["generation"],
        "sourceSha256": document["sha256"],
        "assets": [],
        "skipped": 0,
        "skippedPages": {},
    }
    require(digest(empty) == document["imageAssets"]["sha256"], "empty image manifest differs")
    blobs.blobs[document["imageAssets"]["blobPath"]] = (encoded(empty), "application/json")
    key = secrets.token_urlsafe(24)
    settings = Settings(environment="local", auth_mode="dev", dev_api_key=key, search_mode="vector")
    denied = NoSearch()
    app = create_app(
        settings,
        libraries=libraries,
        documents=documents,
        blobs=blobs,
        queue=MemoryQueue(),
        searcher=Searcher(libraries, documents, denied, denied, mode="vector", blobs=blobs),
    )
    with TestClient(app, raise_server_exceptions=False) as http:
        index = IndexClient("http://testserver", key, http=http, evidence_view="compact", selector_mode="handles")
        try:
            require(_source_snapshot(index, source) == snapshot, "local source differs from archived public snapshot")
            yield index
        finally:
            http.close()


def run_pair(root, *, provider, deployment, binding, deadline):
    controls = verify(root)
    require(binding == controls["modelBinding"], "runtime model binding differs")
    require(provider.max_retries == 0, "provider transport retries must be disabled")
    save_new(root / "pair-started.json", {"controlsSha256": sha(root / "controls.json"), "startedAt": now()})
    meter = Meter(root / "provider", LIMITS, context=CONTEXT, output=OUTPUT)
    pacer = Pacer(deadline, policy=controls["pacing"])
    source = controls["case"]["sourceBinding"]
    bindings = {"docx": source}
    results, failure, active = [], None, None
    try:
        for active in CELLS:
            verify(root)
            pacer.remaining()
            directory = root / active
            directory.mkdir(mode=0o700)
            calls = []
            save_new(directory / "review-template.json", review_template(controls["case"]))
            # The rate window spans both cells, but reasoning provenance does not.
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
                    {"run": active, "cell": active, "case": controls["case"]["case"]},
                    pacer,
                    directory,
                    calls,
                )
                cell_error = None
                diagnostics = {}
                try:
                    turn = run_turn(
                        invoke,
                        captured,
                        {
                            "id": "reference-qualification",
                            "libraryIds": [source["libraryId"]],
                            "instructions": AGENT_INSTRUCTIONS,
                        },
                        [],
                        controls["case"]["question"],
                        context_token_budget=CONTEXT,
                        max_rounds=MAX_ROUNDS,
                        image_inputs=False,
                        presentation="navigation" if active == "navigation" else None,
                        presentation_diagnostics=diagnostics,
                    )
                    save_new(directory / "turn.json", capture(asdict(turn)))
                except BaseException as error:
                    cell_error = error
                    raise
                finally:
                    save_new(directory / "presentation-diagnostics.json", capture(diagnostics))
                    # No external IO: observe the local source even after a model deadline.
                    index.http = local_http
                    try:
                        after = _source_snapshot(index, bindings)
                        save_new(directory / "sources-after.json", after)
                        require(before == after, "source changed during model turn")
                        save_new(directory / "source-check.json", {"passed": True})
                    except BaseException as error:
                        save_new(directory / "source-check.json", {"passed": False, "errorType": type(error).__name__})
                        if cell_error is None:
                            raise
                summary = {
                    "cell": active,
                    "completed": True,
                    "sourceSnapshotsUnchanged": True,
                    "logicalCalls": len(calls),
                    "actualApiOperations": len(captured.calls),
                    "presentationDiagnostics": capture(diagnostics),
                    "sourceJudgment": "pending independent review of wire and resolved API calls",
                    "cellListCompleteness": "not exercised live; separate local regressions",
                }
                if active == "navigation":
                    require(
                        summary["presentationDiagnostics"].get("closed") is True, "reference session did not expire"
                    )
                results.append(summary)
                save_new(directory / "summary.json", summary)
    except BaseException as error:
        failure = error
    finally:
        result = {
            "status": "failed" if failure else "pair_complete_pending_source_review",
            "completedTurns": len(results),
            "activeCell": active,
            "cells": results,
            "provider": meter_summary(meter.path),
            "localApiContextsClosed": True,
            "cloudDataWrites": 0,
            "embeddingCalls": 0,
            "cosmosRu": None,
            "limitation": "Local snapshot navigation only; no cloud lifecycle, ingestion, "
            "search quality or scale proof.",
        }
        if failure:
            result["errorType"] = type(failure).__name__
        save_new(root / "pair-result.json", result)
    if failure:
        raise failure
    return result


def execute(state, archive_path):
    root, destination = private_path(state), private_path(archive_path)
    require(not destination.exists(), "fresh archive destination required")
    require(not destination.is_relative_to(root) and not root.is_relative_to(destination), "archive overlaps state")
    controls = verify(root)
    require(
        not subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip(),
        "commit comparison first",
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
        result["pair"] = run_pair(
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
            verify(root)
            result["postchecks"] = {"passed": True, "codeSourceBindingCapacity": "unchanged"}
        except BaseException as error:
            result["postchecks"] = {"passed": False, "errorType": type(error).__name__}
            failure = failure or error
        result.update(
            status="failed" if failure else "pair_complete_pending_source_review",
            cloudDataResourcesCreated=0,
            cloudDataCleanupRequired=False,
            archiveExcludesMutableLogs=True,
        )
        if failure:
            result.setdefault("errorType", type(failure).__name__)
        save_new(root / "execution-result.json", result)
        saved = archive(root, destination)
        save_new(root / "archive-receipt.json", saved)
    if failure:
        raise failure
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "preflight", "execute"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--prior-manifest", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        require(args.prior is not None and args.prior_manifest is not None, "prior and reviewed manifest required")
        result = prepare(args.state, args.prior, prior_manifest=args.prior_manifest)
    elif args.action == "preflight":
        result = preflight(args.state)
    else:
        require(args.archive is not None, "archive destination required")
        result = execute(args.state, args.archive)
    print(json.dumps({"status": result.get("status", "prepared"), "modelCalls": result.get("modelCalls")}), flush=True)


if __name__ == "__main__":
    main()
