"""Build a fail-closed owned-scope proof; optionally perform exact recovery.

Build is local/read-only. Execute is coordinator-only, explicit, and mutates only
objects enumerated by an accepted immutable proof. No chunk SQL/feed operations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

WORKER_FILES = (
    "src/fli/worker/handlers.py",
    "src/fli/worker/__init__.py",
    "src/fli/chunks.py",
    "src/fli/ids.py",
    "src/fli/ingestion.py",
    "src/fli/microsoft.py",
    "src/fli/checkpoints.py",
    "src/fli/blobs.py",
)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def now():
    return datetime.now(UTC).isoformat()


def require(value, message):
    if not value:
        raise ValueError(message)


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def event(root, kind, **value):
    write_new(
        Path(root) / "events" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex + ".json"),
        {"at": now(), "kind": kind, **value},
    )


def checked_archived_file(item):
    """Retained originals/checkpoints must remain intact before live copies are removed."""
    from scripts.retrieval_controls import private_path

    file = Path(item["localFile"])
    require(
        file.is_absolute()
        and file == file.resolve()
        and not file.is_symlink()
        and file.is_file()
        and private_path(file) == file,
        "archived blob must be an unchanged private regular file",
    )
    require(file.stat().st_size == item["bytes"] and sha(file) == item["sha256"], "archived blob hash differs")
    return file


def load_archive(directory, lib, known_docs):
    directory = Path(directory).resolve()
    manifest = read(directory / "blob-manifest-private.json")
    result = {}
    for item in manifest:
        name = item["blobPath"]
        parts = name.split("/")
        require(len(parts) > 2 and parts[0] == lib and parts[1] in known_docs, "archive contains unknown scope")
        file = directory / item["archiveFile"]
        require(
            Path(item["archiveFile"]).name == item["archiveFile"] and file.parent == directory,
            "archive file is not a direct child",
        )
        bound = {**item, "localFile": str(file)}
        checked_archived_file(bound)
        require(name not in result, "duplicate archived blob path")
        result[name] = bound
    return result


def document_identity(document, source, library_id, expected_job=None):
    require(
        document["libraryId"] == library_id
        and document["generation"] == 1
        and document["sha256"] == source["sha256"]
        and document["sizeBytes"] == source["sizeBytes"]
        and document["fileName"] == source["fileName"]
        and document.get("metadataVersion") == 0
        and document.get("author") == source["metadata"]["author"]
        and document.get("tags") == [{**tag, "source": "user"} for tag in source["metadata"]["tags"]]
        and document.get("periodStart") == source["metadata"].get("periodStart")
        and document.get("periodEnd") == source["metadata"].get("periodEnd")
        and not document.get("pendingFile"),
        "document/source identity changed",
    )
    if expected_job is not None:
        require(document["ingestionJob"] == expected_job, "immutable source job changed")


def build(args):
    from fli.config import Settings
    from fli.ids import chunk_id
    from fli.ingestion import validate_microsoft
    from scripts.hybrid_quality_scope import binding
    from scripts.retrieval_controls import private_path

    root = private_path(args.output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        manifest = read(args.manifest)
        ownership = read(private_path(args.ownership))
        lib = ownership["library"]["id"]
        protected = read(private_path(args.protected_scope))["libraryIds"]
        require(
            ownership.get("createdByThisRun") is True
            and ownership.get("createAttempted") is True
            and lib not in protected
            and len(protected) > 0,
            "owned scope/protected identity invalid",
        )
        require(
            ownership["contract"]["manifestSha256"] == sha(args.manifest), "manifest differs from owned source contract"
        )
        require(sorted(protected) == ownership["contract"]["protectedLibraryIds"], "protected set changed")
        settings = Settings.from_env(read(private_path(args.env)))
        require(binding(settings) == ownership["contract"]["settingsBinding"], "old runtime binding changed")
        sources = {source["id"]: source for source in manifest["documents"]}
        require(set(ownership["sources"]) == set(sources), "source ownership matrix incomplete")
        doc_sources = {}
        for source_id, item in ownership["sources"].items():
            require(
                item.get("attempted") is True
                and item.get("documentId")
                and item["sourceSha256"] == sources[source_id]["sha256"],
                "ambiguous upload or changed source",
            )
            doc_id = item["documentId"]
            require(
                str(uuid.UUID(doc_id)) == doc_id and doc_id not in doc_sources, "duplicate or invalid document identity"
            )
            doc_sources[doc_id] = sources[source_id]
        archives = [load_archive(private_path(path), lib, doc_sources) for path in args.archive]
        merged = {}
        for archive in archives:
            for name, item in archive.items():
                require(
                    name not in merged or merged[name]["sha256"] == item["sha256"],
                    "immutable blob changed between archives",
                )
                merged[name] = item
        latest = private_path(args.archive[-1])
        capture = read(latest / "capture-receipt.json")
        docs = read(latest / "documents-after-private.json")
        before = read(latest / "documents-before-private.json")
        library = read(latest / "library-private.json")
        require(
            capture["libraryFenced"] is True
            and capture["pathsStableDuringCapture"] is True
            and capture["documentsStableDuringCapture"] is True
            and docs == before,
            "latest archive is not stable and fenced",
        )
        require(
            set(read(latest / "blob-paths-after-private.json")) == set(archives[-1])
            and capture["archivedFiles"] == len(archives[-1]),
            "latest blob archive incomplete",
        )
        require(
            library["id"] == lib
            and library["status"] == "deleting"
            and library["createdBy"] == ownership["runId"]
            and library["name"] == "Hybrid quality confirmation " + ownership["runId"]
            and library["ingestionProfile"] == ownership["library"]["ingestionProfile"],
            "live archived library is not the owned deletion fence",
        )
        fence_at = library["updatedAt"]
        require(capture["startedAt"] >= fence_at, "archive predates deletion fence")
        require(
            len(docs) == len(doc_sources) and {doc["id"] for doc in docs} == set(doc_sources),
            "unknown or missing current document",
        )
        # Bind the exact recorded deployment revision to analyzed repository code.
        worker = read(private_path(args.worker_arm))
        worker_summary = read(private_path(args.worker_summary))
        require(
            worker_summary["privateConfigurationSha256"]
            == hashlib.sha256(
                json.dumps(
                    {
                        key: worker["properties"]["template"]["containers"][0].get(key)
                        for key in ("image", "env", "command", "args")
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            and worker_summary["existingWorkerConfigurationUnchanged"] is True,
            "worker management receipt hash mismatch",
        )
        revision = worker_summary["workerCodeRevision"]
        require(revision == args.worker_revision, "unexpected deployed worker revision")
        containers = worker["properties"]["template"]["containers"]
        relevant = [
            container
            for container in containers
            if any(v.get("name") == "FLI_CODE_REVISION" for v in container.get("env", []))
        ]
        require(len(relevant) == 1, "ambiguous worker container")
        worker_env = {item["name"]: item.get("value") for item in relevant[0]["env"]}
        require(worker_env.get("FLI_CODE_REVISION") == revision, "worker environment revision differs")
        for key in (
            "COSMOS_ENDPOINT",
            "COSMOS_DATABASE",
            "BLOB_ACCOUNT_URL",
            "BLOB_CONTAINER",
            "SERVICEBUS_NAMESPACE",
            "SERVICEBUS_QUEUE",
        ):
            require(worker_env.get(key) == read(args.env).get(key), "worker targets differ from old owned scope")
        code = {}
        for name in WORKER_FILES:
            deployed = subprocess.check_output(["git", "show", revision + ":" + name], cwd=args.repository)
            code[name] = hashlib.sha256(deployed).hexdigest()
            require(
                code[name] == sha(Path(args.repository) / name), "deployed worker code differs from analyzed repository"
            )
        candidates = []
        source_proofs = []
        all_attempts = []
        for doc in docs:
            doc_id = doc["id"]
            source = doc_sources[doc_id]
            document_identity(doc, source, lib)
            require(
                doc["status"] in ("deleting", "failed", "ready") and doc.get("activeGeneration") in (None, 1),
                "source is still queued/processing or has unknown generation",
            )
            if doc["status"] != "deleting":
                require(doc.get("pendingGeneration") is None, "unfenced pending generation remains")
            else:
                require(doc.get("pendingGeneration") in (None, 1), "unknown fenced pending generation")
            job = doc["ingestionJob"]
            without_id = {key: value for key, value in job.items() if key != "executionId"}
            require(
                job["executionId"] == digest(without_id)
                and job["libraryId"] == lib
                and job["documentId"] == doc_id
                and job["generation"] == 1
                and job["sourceSha256"] == source["sha256"]
                and job["metadataVersion"] == 0
                and job["profile"] == library["ingestionProfile"],
                "immutable job binding differs",
            )
            original = merged.get(job["sourcePath"])
            require(original is not None, "original source archive missing")
            original_bytes = Path(original["localFile"]).read_bytes()
            require(
                hashlib.sha256(original_bytes).hexdigest() == source["sha256"]
                and len(original_bytes) == source["sizeBytes"],
                "archived original differs from frozen manifest",
            )
            prefix = f"{lib}/{doc_id}/"
            attempts = {}
            for name, item in merged.items():
                if not name.startswith(prefix + "attempts/"):
                    continue
                relative = name[len(prefix + "attempts/") :].split("/")
                require(
                    len(relative) == 2 and relative[1] in ("start.json", "finish.json"),
                    "unknown owned attempt artifact",
                )
                attempts.setdefault(relative[0], {})[relative[1]] = read(item["localFile"])
            require(attempts, "no terminal attempt evidence for owned source")
            provider_invocations = 0
            publish_attempts = 0
            post_fence_early_stops = 0
            for attempt_id, parts in attempts.items():
                require(set(parts) == {"start.json", "finish.json"}, "unmatched worker attempt start or finish")
                start, finish = parts["start.json"], parts["finish.json"]
                require(
                    start["attemptId"] == finish["attemptId"]
                    and attempt_id == "1-" + start["attemptId"]
                    and start["generation"] == finish["generation"] == 1
                    and start["stage"] == finish["stage"] == "ingest"
                    and start["startedAt"] == finish["startedAt"]
                    and finish["finishedAt"] >= start["startedAt"]
                    and finish["finishedAt"] <= capture["completedAt"]
                    and finish["status"] in ("error", "failed", "stopped", "published"),
                    "invalid or nonterminal attempt identity",
                )
                if finish.get("executionId") is None:
                    require(start["startedAt"] >= fence_at, "pre-fence attempt has no execution identity")
                    require(
                        not any(
                            key in finish
                            for key in ("localEmbeddingUsage", "providerResultUsage", "chunkWriteChargeKnown")
                        ),
                        "unbound attempt may have entered publication",
                    )
                    post_fence_early_stops += 1
                else:
                    require(
                        finish["executionId"] == job["executionId"]
                        and finish.get("sourceSha256") == source["sha256"]
                        and finish.get("profileId") == "microsoft-indexer-layout-v1",
                        "attempt references another job/source",
                    )
                    require(
                        "localEmbeddingUsage" in finish and "chunkWriteChargeComplete" in finish,
                        "provider/publishing attempt did not reach bounded completed chunk-write phase",
                    )
                    publish_attempts += 1
                    if "providerResultUsage" in finish:
                        require(type(finish.get("providerResultReused")) is bool, "ambiguous provider invocation state")
                        provider_invocations += not finish["providerResultReused"]
                all_attempts.append(
                    {
                        "sourceId": source["id"],
                        "attemptId": finish["attemptId"],
                        "finishedAt": finish["finishedAt"],
                        "executionId": finish.get("executionId"),
                        "status": finish["status"],
                    }
                )
            plan_name = prefix + "text/1/plan.json"
            result_name = prefix + "text/1/microsoft-result.json"
            plan_item = merged.get(plan_name)
            result_item = merged.get(result_name)
            source_candidates = []
            if plan_item is None:
                require(
                    publish_attempts == 0
                    and provider_invocations == 0
                    and result_item is None
                    and post_fence_early_stops > 0
                    and doc.get("activeGeneration") is None,
                    "missing plan cannot prove an empty candidate universe",
                )
            else:
                require(result_item is not None, "provider plan lacks archived source-bound result")
                plan = read(plan_item["localFile"])
                result = read(result_item["localFile"])
                validated = validate_microsoft(result, job, original_bytes, 3072)
                require(
                    plan["version"] == 1
                    and plan["job"] == job
                    and plan["generation"] == 1
                    and plan["sourceSha256"] == source["sha256"]
                    and plan["chunkingVersion"] == "microsoft-token-window-v1"
                    and plan["imageRecords"] == {}
                    and plan["imageCount"] == 0
                    and len(plan["pieces"]) == len(plan["vectors"]) == len(validated.pieces) == plan["textChunkCount"]
                    and plan["vectors"] == validated.vectors,
                    "frozen plan/provider result does not close chunk universe",
                )
                indices = [piece["index"] for piece in plan["pieces"]]
                require(
                    all(type(index) is int and index >= 0 for index in indices) and len(set(indices)) == len(indices),
                    "invalid or duplicated planned chunk index",
                )
                for piece, expected in zip(plan["pieces"], validated.pieces, strict=True):
                    require(
                        piece["index"] == expected.index
                        and piece["text"] == expected.text
                        and piece["heading"] == expected.heading
                        and piece["location"] == expected.location,
                        "provider/plan source body differs",
                    )
                    content = {
                        "text": piece["text"],
                        "heading": piece["heading"],
                        "location": piece.get("location") or {},
                        "imageRef": None,
                        "imageMeta": None,
                        "embeddingModel": "text-embedding-3-large",
                        "chunkingVersion": plan["chunkingVersion"],
                    }
                    source_candidates.append(
                        {
                            "id": chunk_id(doc_id, 1, piece["index"]),
                            "libraryId": lib,
                            "documentId": doc_id,
                            "generation": 1,
                            "chunkIndex": piece["index"],
                            "contentHash": digest(content),
                        }
                    )
                candidates.extend(source_candidates)
            source_proofs.append(
                {
                    "sourceId": source["id"],
                    "documentId": doc_id,
                    "job": job,
                    "status": doc["status"],
                    "source": source,
                    "attemptCount": len(attempts),
                    "publishAttemptCount": publish_attempts,
                    "providerInvocationCount": provider_invocations,
                    "postFenceEarlyStops": post_fence_early_stops,
                    "candidateCount": len(source_candidates),
                    "planSha256": plan_item["sha256"] if plan_item else None,
                    "providerResultSha256": result_item["sha256"] if result_item else None,
                }
            )
        require(
            len(candidates) <= 1000 and len({item["id"] for item in candidates}) == len(candidates),
            "candidate set exceeds bound or duplicates",
        )
        paths = [args.manifest, args.ownership, args.env, args.protected_scope, args.worker_arm, args.worker_summary]
        for directory in args.archive:
            paths.append(Path(directory) / "blob-manifest-private.json")
        paths.extend(
            latest / name
            for name in (
                "capture-receipt.json",
                "documents-before-private.json",
                "documents-after-private.json",
                "library-private.json",
                "blob-paths-after-private.json",
            )
        )
        proof = {
            "schema": "fli.hybrid-quality.failed-owned-proof.v1",
            "status": "eligible",
            "builtAt": now(),
            "builderSha256": sha(__file__),
            "fencedAt": fence_at,
            "libraryId": lib,
            "runId": ownership["runId"],
            "library": library,
            "contract": ownership["contract"],
            "workerRevision": revision,
            "workerCodeSha256": code,
            "sources": source_proofs,
            "candidates": candidates,
            "attempts": all_attempts,
            "currentBlobArchive": list(archives[-1].values()),
            "archivedBlobBindings": [item for archive in archives for item in archive.values()],
            "inputBindings": {str(Path(path).resolve()): sha(path) for path in paths},
            "candidateUniverseBasis": (
                "All observed generation1 plans validated against original source and Microsoft result; "
                "Every observed writer finished; no ambiguous provider phase. "
                "Current namespace fenced and stable."
            ),
            "providerLimit": (
                "Completed source-bound provider result and matched worker return; "
                "no independent provider management audit. Late blob checks remain required."
            ),
            "chunkInventoryQueryPerformed": False,
            "sourceBodiesAndVectorsRemainPrivate": True,
        }
        write_new(root / "proof-private.json", proof)
        summary = {
            "status": "eligible",
            "sources": len(source_proofs),
            "candidateChunks": len(candidates),
            "terminalAttempts": len(all_attempts),
            "noPlanPostFenceStops": sum(item["planSha256"] is None for item in source_proofs),
            "proofSha256": sha(root / "proof-private.json"),
            "cleanupExecuted": False,
            "chunkInventoryQueryPerformed": False,
        }
        write_new(root / "build-summary.json", summary)
        print(json.dumps(summary))
    except Exception as exc:
        event(root, "proof-failed", errorType=type(exc).__name__, message=str(exc))
        summary = {
            "status": "unknown",
            "proofComplete": False,
            "cleanupExecuted": False,
            "failureCategory": type(exc).__name__,
        }
        write_new(root / "build-summary.json", summary)
        print(json.dumps(summary))
        raise


async def execute(args):
    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceNotFoundError
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    from fli.config import Settings
    from scripts.hybrid_quality_scope import binding
    from scripts.retrieval_controls import private_path
    from scripts.retrieval_i1_lifecycle import api_instance

    root = private_path(args.output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    proof = read(private_path(args.proof))
    try:
        require(
            sha(args.proof) == args.proof_sha256 and proof.get("status") == "eligible", "accepted exact proof required"
        )
        require(proof.get("builderSha256") == sha(__file__), "accepted proof builder changed")
        require(30 <= args.late_recheck <= 60, "late recheck must be 30–60 seconds")
        require(
            args.maximum_candidates == len(proof["candidates"]) and args.maximum_candidates <= 1000,
            "explicit exact candidate limit required",
        )
        require(proof.get("archivedBlobBindings"), "retained archive bindings required")
        for item in proof["archivedBlobBindings"]:
            checked_archived_file(item)
        for path, value in proof["inputBindings"].items():
            require(sha(path) == value, "proof input changed")
        for path, value in proof["workerCodeSha256"].items():
            require(sha(Path(args.repository) / path) == value, "analyzed worker code changed")
        environment = read(private_path(args.env))
        settings = Settings.from_env(environment)
        require(binding(settings) == proof["contract"]["settingsBinding"], "old runtime binding changed")
        os.environ.update(environment)
        lib = proof["libraryId"]
        require(lib not in proof["contract"]["protectedLibraryIds"], "protected library collision")
        from fli.ids import chunk_id

        owned_docs = {item["documentId"] for item in proof["sources"]}
        for item in proof["candidates"]:
            require(
                item["libraryId"] == lib
                and item["documentId"] in owned_docs
                and item["generation"] == 1
                and type(item["chunkIndex"]) is int
                and item["chunkIndex"] >= 0
                and item["id"] == chunk_id(item["documentId"], 1, item["chunkIndex"]),
                "candidate escaped derived owned document/generation",
            )
        async with api_instance(settings) as (app, _client):
            actual = await app.state.libraries.get(lib)
            require(
                actual
                and actual["status"] == "deleting"
                and actual["createdBy"] == proof["runId"]
                and actual["ingestionProfile"] == proof["library"]["ingestionProfile"],
                "owned fence changed",
            )
            documents = []
            for item in proof["sources"]:
                doc = await app.state.documents.get(lib, item["documentId"])
                require(doc is not None, "current document disappeared; rebuild proof")
                document_identity(doc, item["source"], lib, item["job"])
                require(
                    doc["status"] in ("deleting", "ready", "failed")
                    and doc.get("activeGeneration") in (None, 1)
                    and (
                        doc.get("pendingGeneration") is None
                        or doc["status"] == "deleting"
                        and doc.get("pendingGeneration") == 1
                    ),
                    "a source became active or pending after proof",
                )
                documents.append(doc)
            archived = {item["blobPath"]: item for item in proof["currentBlobArchive"]}
            paths = await app.state.blobs.list_prefix(lib + "/")
            require(set(paths) == set(archived), "owned blob/attempt set changed after quiescence proof")
            for path in paths:
                require(
                    hashlib.sha256(await app.state.blobs.download(path)).hexdigest() == archived[path]["sha256"],
                    "owned blob changed after archive",
                )
            container = app.state.searcher.chunks._c
            # All candidate point reads must succeed or return actual 404 BEFORE any delete.
            found = []
            for candidate in proof["candidates"]:
                try:
                    row = await container.read_item(candidate["id"], partition_key=[lib, candidate["documentId"]])
                except ResourceNotFoundError:
                    event(root, "candidate-precheck-absent", candidate=candidate)
                    continue
                require(
                    all(row.get(key) == value for key, value in candidate.items()),
                    "candidate row identity/content mismatch",
                )
                found.append((candidate, row["_etag"]))
                event(root, "candidate-precheck-found", candidate=candidate, etag=row["_etag"])
            for candidate, etag in found:
                event(root, "candidate-delete-attempt", candidate=candidate, etag=etag)
                try:
                    await container.delete_item(
                        candidate["id"],
                        partition_key=[lib, candidate["documentId"]],
                        etag=etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
                except ResourceNotFoundError:
                    pass
                event(root, "candidate-delete-finished", candidate=candidate)

            async def verify_points():
                async with (
                    DefaultAzureCredential() as credential,
                    CosmosClient(settings.cosmos_endpoint, credential=credential) as cosmos,
                ):
                    independent = cosmos.get_database_client(settings.cosmos_database).get_container_client("chunks")
                    for candidate in proof["candidates"]:
                        try:
                            await independent.read_item(candidate["id"], partition_key=[lib, candidate["documentId"]])
                        except ResourceNotFoundError:
                            event(root, "candidate-independent-404", candidate=candidate)
                            continue
                        raise ValueError("derived candidate still exists")

            await verify_points()
            # Recheck that no new worker/provider artifact appeared before deleting archived blobs.
            require(
                set(await app.state.blobs.list_prefix(lib + "/")) == set(archived),
                "late artifact before owned blob cleanup",
            )
            for path in paths:
                require(
                    hashlib.sha256(await app.state.blobs.download(path)).hexdigest() == archived[path]["sha256"],
                    "owned checkpoint changed before deletion",
                )
            for path in paths:
                event(root, "owned-blob-delete-attempt", blobPath=path)
                await app.state.blobs.delete(path)
                event(root, "owned-blob-delete-finished", blobPath=path)
            for document in documents:
                # Exact known document point-delete; retain the fence until every derived chunk is verified absent.
                event(root, "owned-document-delete-attempt", documentId=document["id"])
                await app.state.documents.delete(lib, document["id"])
                event(root, "owned-document-delete-finished", documentId=document["id"])
            event(root, "owned-library-delete-attempt", libraryId=lib)
            await app.state.libraries.delete(lib)
            event(root, "owned-library-delete-finished", libraryId=lib)

            async def verify_other():
                async with (
                    DefaultAzureCredential() as credential,
                    CosmosClient(settings.cosmos_endpoint, credential=credential) as cosmos,
                ):
                    database = cosmos.get_database_client(settings.cosmos_database)
                    for name, item_id in [
                        ("libraries", lib),
                        *[("documents", item["documentId"]) for item in proof["sources"]],
                    ]:
                        try:
                            await database.get_container_client(name).read_item(item_id, partition_key=lib)
                        except ResourceNotFoundError:
                            continue
                        raise ValueError("owned metadata record remains")
                require(not await app.state.blobs.list_prefix(lib + "/"), "owned live blob prefix not empty")

            await verify_other()
            await asyncio.sleep(args.late_recheck)
            await verify_points()
            await verify_other()
        summary = {
            "schema": "fli.hybrid-quality.exact-owned-recovery.v1",
            "status": "complete",
            "proofSha256": args.proof_sha256,
            "derivedCandidateCount": len(proof["candidates"]),
            "allDerivedCandidatesIndependently404": True,
            "ownedDocumentCount": len(proof["sources"]),
            "ownedRecordsAbsent": True,
            "ownedLiveBlobPrefixEmpty": True,
            "lateRecheckSeconds": args.late_recheck,
            "lateRecheckPassed": True,
            "chunkInventoryQueryPerformed": False,
            "broaderFeedOrSqlUsed": False,
            "protectedScopesMutated": 0,
            "retentionUnchanged": True,
            "scopeLimit": (
                "Owned universe closed by archived generation plans/terminal attempts; no global chunk inventory claim."
            ),
        }
        write_new(root / "summary.json", summary)
        print(json.dumps(summary))
    except BaseException as exc:
        event(root, "recovery-failed", errorType=type(exc).__name__, message=str(exc))
        summary = {
            "status": "incomplete",
            "cleanupComplete": False,
            "failedCheck": "unknown; see immutable private error event",
            "failureCategory": type(exc).__name__,
            "chunkInventoryQueryPerformed": False,
        }
        write_new(root / "summary.json", summary)
        print(json.dumps(summary))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "execute"))
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    for name in ("manifest", "ownership", "protected-scope", "worker-arm", "worker-summary", "proof"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--archive", type=Path, action="append")
    parser.add_argument("--worker-revision")
    parser.add_argument("--proof-sha256")
    parser.add_argument("--maximum-candidates", type=int)
    parser.add_argument("--late-recheck", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repository.resolve()))
    require(30 <= args.late_recheck <= 60 and 0 < args.timeout <= 1800, "bounded timeouts and late recheck required")
    if args.action == "build":
        require(
            all(
                getattr(args, name)
                for name in (
                    "archive",
                    "manifest",
                    "ownership",
                    "protected_scope",
                    "worker_arm",
                    "worker_summary",
                    "worker_revision",
                )
            ),
            "all explicit local proof inputs required",
        )
        build(args)
    else:
        require(
            args.proof and args.proof_sha256 and args.maximum_candidates is not None,
            "exact accepted proof/limit required",
        )
        asyncio.run(asyncio.wait_for(execute(args), timeout=args.timeout))


if __name__ == "__main__":
    main()
