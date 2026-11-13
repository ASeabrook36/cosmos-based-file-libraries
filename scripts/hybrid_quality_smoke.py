"""Fresh, exact-owned data-plane smoke for a reused hybrid-quality container.

This coordinator-only command is not the full new-container canary and makes no
index-engagement, recall or performance claim. All runtime material stays private.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from azure.core import MatchConditions  # noqa: E402
from azure.core.exceptions import ResourceNotFoundError  # noqa: E402

from fli.config import Settings  # noqa: E402
from fli.embedding import AzureOpenAIEmbedder  # noqa: E402
from fli.query import SearchFilters, lexical_terms, partition_key_for, render_search, to_float16_query  # noqa: E402
from scripts.canary import (  # noqa: E402
    TokenCache,
    chunk_record,
    definition_mismatches,
    document_record,
    library_record,
    query_sentence,
    read_corpus,
    target_fingerprint,
)
from scripts.hybrid_quality_direct import model_binding  # noqa: E402
from scripts.hybrid_quality_scope import binding, locked, save  # noqa: E402
from scripts.retrieval_controls import private_path, read  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CHUNKS = 12
TOP = 3


def now():
    return datetime.now(UTC).isoformat()


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact(path, role):
    path = private_path(path)
    return {"role": role, "path": str(path), "sha256": sha(path)}


def write_new(path, value):
    path = private_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def same_row(actual, expected):
    return all(actual.get(key) == value for key, value in expected.items())


def item_key(item):
    row = item["body"]
    return [row["libraryId"], row["documentId"]] if item["container"] == "chunks" else row["libraryId"]


def check_ownership(ledger, settings, protected):
    require(ledger["schema"] == "fli.hybrid-quality.smoke-ownership.v1", "invalid smoke ownership schema")
    require(ledger["settingsBinding"] == binding(settings), "smoke settings changed")
    require(sorted(protected) == ledger["protectedLibraryIds"], "protected inventory changed")
    lib = ledger["libraryId"]
    require(str(uuid.UUID(lib)) == lib and lib not in protected, "unsafe smoke scope")
    require(len(ledger["rows"]) >= CHUNKS + 2, "incomplete owned row inventory")
    for item in ledger["rows"]:
        row = item["body"]
        require(item["container"] in {"libraries", "documents", "chunks"}, "unexpected container")
        require(row["libraryId"] == lib and row["smokeOwner"] == ledger["runId"], "foreign smoke row")
    keys = [(item["container"], item["body"]["id"], json.dumps(item_key(item))) for item in ledger["rows"]]
    require(len(keys) == len(set(keys)), "duplicate owned row key")


def event(root, kind, **values):
    folder = root / "events"
    folder.mkdir(exist_ok=True, mode=0o700)
    write_new(folder / (uuid.uuid4().hex + ".json"), {"at": now(), "kind": kind, **values})


def response_hook(root, role):
    def hook(response):
        headers = response.http_response.headers
        selected = {key: value for key, value in headers.items() if key.lower().startswith("x-ms-")}
        event(root, "response", role=role, status=response.http_response.status_code, headers=selected)

    return hook


async def bounded(operation):
    async with asyncio.timeout(45):
        return await operation()


async def policies(database, settings, fingerprint):
    for name in ("libraries", "documents", "chunks"):
        actual = await bounded(lambda name=name: database.get_container_client(name).read())
        expected = json.loads((REPO / "cosmos" / (name + ".container.json")).read_text())
        require(not definition_mismatches(expected, actual), "actual smoke container policy differs")
        if name == "chunks":
            actual_fingerprint = target_fingerprint(
                settings.cosmos_endpoint, settings.cosmos_database, name, actual["_rid"]
            )
            require(actual_fingerprint == fingerprint, "smoke container incarnation changed")


async def point(database, item):
    return await bounded(
        lambda: database.get_container_client(item["container"]).read_item(
            item["body"]["id"], partition_key=item_key(item)
        )
    )


async def scoped(database, lib, top):
    container = database.get_container_client("chunks")

    async def query():
        return [
            row
            async for row in container.query_items(
                f"SELECT TOP {top} c.id, c.libraryId FROM c WHERE c.libraryId = @libraryId",
                parameters=[{"name": "@libraryId", "value": lib}],
                partition_key=[lib],
            )
        ]

    return await bounded(query)


async def cleanup_owned(database, root, ledger, late_seconds, *, sleep=asyncio.sleep):
    """Precheck ALL exact rows before deleting any; fail on unknown reads/changed rows."""
    require(30 <= late_seconds <= 60, "late verification must be 30–60 seconds")
    found = []
    for item in ledger["rows"]:
        try:
            actual = await point(database, item)
        except ResourceNotFoundError:
            event(root, "cleanup-precheck-404", container=item["container"], id=item["body"]["id"])
            continue
        require(same_row(actual, item["body"]), "owned row changed; stop before any cleanup delete")
        require(item["body"]["id"] in ledger["attemptedIds"], "unattempted row exists; never adopt it for cleanup")
        found.append((item, actual["_etag"]))
    deleted = 0
    for item, etag in sorted(
        found, key=lambda pair: {"chunks": 0, "documents": 1, "libraries": 2}[pair[0]["container"]]
    ):
        event(root, "delete-attempt", container=item["container"], id=item["body"]["id"], etag=etag)
        await bounded(
            lambda item=item, etag=etag: database.get_container_client(item["container"]).delete_item(
                item["body"]["id"],
                partition_key=item_key(item),
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        )
        deleted += 1
        event(root, "delete-completed", container=item["container"], id=item["body"]["id"])

    async def absent():
        count = 0
        for item in ledger["rows"]:
            try:
                await point(database, item)
            except ResourceNotFoundError:
                count += 1
                continue
            raise ValueError("owned smoke row remains after cleanup")
        remaining = await scoped(database, ledger["libraryId"], 1)
        require(not remaining, "unknown owned-prefix chunk remains; no broad cleanup permitted")
        return count

    initial = await absent()
    await sleep(late_seconds)
    late = await absent()
    return {
        "expectedRows": len(ledger["rows"]),
        "deletedRows": deleted,
        "point404Count": initial,
        "scopedRows": 0,
        "latePoint404Count": late,
        "lateScopedRows": 0,
        "lateRecheckSeconds": late_seconds,
    }


async def prepare(args, settings, protected):
    canary = read(private_path(args.canary))
    require(canary.get("passed") is True and canary.get("checks"), "passing historical canary required")
    require(all(check.get("passed") is True for check in canary["checks"]), "historical canary has failure")
    require(
        settings.aoai_embedding_dimensions == 3072 and settings.aoai_embedding_model_revision == "1",
        "embedding settings changed",
    )
    before = read(private_path(args.embedding_before))
    deployed = model_binding(before, settings)
    documents, chunks, version = await read_corpus(private_path(args.corpus), CHUNKS)
    require(
        len(chunks) == CHUNKS and len({chunk.text for chunk in chunks}) == CHUNKS, "need twelve distinct genuine chunks"
    )
    documents = [doc for doc in documents if doc["chunks"]]
    source_refs = []
    for doc in documents:
        matches = [path for path in private_path(args.corpus).rglob(doc["fileName"]) if sha(path) == doc["sha256"]]
        require(len(matches) == 1, "source bytes are ambiguous")
        source_refs.append(artifact(matches[0], "corpus-original"))
    benchmark = json.loads((REPO / "docs/testing/hybrid-quality/manifest.json").read_text())
    require(
        not {ref["sha256"] for ref in source_refs} & {doc["sha256"] for doc in benchmark["documents"]},
        "smoke must not use confirmation originals",
    )
    questions = [query_sentence(chunks[index].text, chunks[index].heading) for index in (0, CHUNKS - 1)]
    require(
        all(questions) and len(set(questions)) == 2 and all(lexical_terms(q) for q in questions),
        "need two distinct real-source queries",
    )
    return canary, before, deployed, documents, chunks, version, questions, source_refs


async def execute(args):
    import httpx
    from azure.cosmos._retry_options import RetryOptions
    from azure.cosmos.aio import CosmosClient
    from azure.cosmos.documents import ConnectionPolicy
    from azure.identity.aio import AzureCliCredential

    os.umask(0o077)
    settings = Settings.from_env(read(private_path(args.env)))
    protected = sorted(read(private_path(args.protected_scope))["libraryIds"])
    require(bool(protected), "explicit protected inventory required")
    with locked(args.state_dir) as root:
        policy = ConnectionPolicy()
        policy.RetryOptions = RetryOptions(max_retry_attempt_count=0, max_wait_time_in_seconds=0)
        if args.action == "run":
            require(
                not (root / "ownership.json").exists() and not (root / "execution.json").exists(), "smoke is single-use"
            )
            canary, before, deployed, docs, chunks, version, questions, source_refs = await prepare(
                args, settings, protected
            )
            write_new(
                root / "execution.json", {"startedAt": now(), "action": "run", "producerSourceSha256": sha(__file__)}
            )
            write_new(root / "embedding-before.json", before)
            write_new(root / "canary.json", canary)
        else:
            ledger = read(root / "ownership.json")
            check_ownership(ledger, settings, protected)
        async with (
            AzureCliCredential(process_timeout=30) as credential,
            CosmosClient(
                settings.cosmos_endpoint,
                credential=credential,
                connection_policy=policy,
                retry_total=0,
                retry_connect=0,
                retry_read=0,
                retry_status=0,
                connection_timeout=10,
                read_timeout=30,
            ) as cosmos,
            httpx.AsyncClient(timeout=30) as http,
        ):
            require(
                cosmos.client_connection.connection_policy.RetryOptions.MaxRetryAttemptCount == 0,
                "throttle retry bound differs",
            )
            require(
                cosmos.client_connection.connection_policy.ConnectionRetryConfiguration.total_retries == 0,
                "connection retry bound differs",
            )
            database = cosmos.get_database_client(settings.cosmos_database)
            if args.action == "cleanup":
                await policies(database, settings, ledger["targetFingerprint"])
                result = await cleanup_owned(database, root, ledger, args.late_recheck)
                write_new(root / ("recovery-cleanup-" + uuid.uuid4().hex + ".json"), result)
                return {"status": "cleanup_complete", "rowsAbsent": result["point404Count"]}
            await policies(database, settings, canary["targetFingerprint"])
            embedder = AzureOpenAIEmbedder(
                settings.aoai_endpoint,
                settings.aoai_embedding_deployment,
                TokenCache(credential),
                http,
                dimensions=3072,
                max_attempts=3,
            )
            async with asyncio.timeout(180):
                vectors = await embedder.embed([chunk.text for chunk in chunks] + questions)
            require(
                len(vectors) == CHUNKS + 2
                and all(len(v) == 3072 and all(math.isfinite(x) for x in v) for v in vectors),
                "invalid actual embeddings",
            )
            lib, run_id, started = str(uuid.uuid4()), uuid.uuid4().hex, now()
            require(lib not in protected, "smoke collides with protected scope")
            rows = [{"container": "libraries", "body": library_record(lib, started)}]
            rows += [{"container": "documents", "body": document_record(lib, doc, started, version)} for doc in docs]
            by_doc = {doc["documentId"]: doc for doc in docs}
            rows += [
                {
                    "container": "chunks",
                    "body": chunk_record(lib, chunk, by_doc[chunk.document_id], vectors[i], started, version),
                }
                for i, chunk in enumerate(chunks)
            ]
            for item in rows:
                item["body"]["smokeOwner"] = run_id
            ledger = {
                "schema": "fli.hybrid-quality.smoke-ownership.v1",
                "runId": run_id,
                "libraryId": lib,
                "startedAt": started,
                "settingsBinding": binding(settings),
                "protectedLibraryIds": protected,
                "targetFingerprint": canary["targetFingerprint"],
                "rows": rows,
                "noAsyncWriters": True,
                "attemptedIds": [],
            }
            check_ownership(ledger, settings, protected)
            write_new(root / "ownership.json", ledger)
            write_new(
                root / "inputs-private.json",
                {
                    "documents": docs,
                    "questions": questions,
                    "queryVectors": vectors[CHUNKS:],
                    "sourceArtifacts": source_refs,
                },
            )
            health = {
                "schema": "fli.hybrid-quality.restart-health.v1",
                "status": "incomplete",
                "startedAt": started,
                "producerSourceSha256": sha(__file__),
                "targetFingerprint": canary["targetFingerprint"],
                "settingsBinding": binding(settings),
                "canarySha256": sha(args.canary),
                "libraryId": lib,
                "protectedLibraryIds": protected,
                "sourceHashes": sorted({ref["sha256"] for ref in source_refs}),
                "sourceArtifacts": source_refs,
                "sourceDisjointFromBenchmark": True,
                "embeddingBinding": deployed,
                "embeddingDimensions": 3072,
                "checks": {},
                "indexEngagementClaim": False,
                "performanceClaim": False,
            }
            save(root / "health-pending.json", health)
            error = None
            try:
                # Every exact key must be absent before the first create. Never adopt an existing row.
                for item in rows:
                    try:
                        await point(database, item)
                    except ResourceNotFoundError:
                        continue
                    raise ValueError("smoke row already exists; no writes allowed")
                written = 0
                for item in rows:
                    ledger["attemptedIds"].append(item["body"]["id"])
                    save(root / "ownership.json", ledger)
                    event(root, "create-attempt", container=item["container"], id=item["body"]["id"])
                    actual = await bounded(
                        lambda item=item: database.get_container_client(item["container"]).create_item(
                            item["body"], raw_response_hook=response_hook(root, "create")
                        )
                    )
                    require(same_row(actual, item["body"]), "create response differs from owned payload")
                    written += 1
                    event(
                        root,
                        "create-completed",
                        container=item["container"],
                        id=item["body"]["id"],
                        etag=actual["_etag"],
                    )
                health["checks"]["writes"] = {"expectedRows": len(rows), "completedRows": written}
                matched = 0
                for item in rows:
                    require(same_row(await point(database, item), item["body"]), "fresh point read changed")
                    matched += 1
                health["checks"]["pointReads"] = {"expectedRows": len(rows), "matchingRows": matched}
                found = await scoped(database, lib, CHUNKS + 1)
                expected_chunks = {item["body"]["id"] for item in rows if item["container"] == "chunks"}
                require(
                    {row["id"] for row in found} == expected_chunks
                    and len(found) == CHUNKS
                    and all(row["libraryId"] == lib for row in found),
                    "scoped inventory differs",
                )
                health["checks"]["scopedQuery"] = {"expectedChunks": CHUNKS, "returnedChunks": len(found)}
                lists = {"vector": [], "hybrid": []}
                chunk_by_id = {item["body"]["id"]: item["body"] for item in rows if item["container"] == "chunks"}
                for mode in ("vector", "hybrid"):
                    checks = []
                    for index, question in enumerate(questions):
                        query = render_search(
                            lib,
                            to_float16_query(vectors[CHUNKS + index]),
                            lexical_terms(question) if mode == "hybrid" else [],
                            SearchFilters(),
                            TOP,
                        )
                        key = partition_key_for(query.sql, lib)

                        async def search(query=query, key=key, mode=mode):
                            return [
                                row
                                async for row in database.get_container_client("chunks").query_items(
                                    query.sql,
                                    parameters=query.parameters,
                                    raw_response_hook=response_hook(root, mode),
                                    **({"partition_key": key} if key else {}),
                                )
                            ]

                        results = await bounded(search)
                        foreign = sum(
                            row.get("id") not in chunk_by_id
                            or any(
                                row.get(k) != chunk_by_id.get(row.get("id"), {}).get(k)
                                for k in ("documentId", "generation")
                            )
                            for row in results
                        )
                        require(len(results) == TOP and not foreign, "search did not return three owned rows")
                        lists[mode].append([row["id"] for row in results])
                        write_new(
                            root / f"{mode}-{index + 1}-private.json",
                            {
                                "query": query.sql,
                                "parameters": query.parameters,
                                "partitionKey": key,
                                "results": results,
                            },
                        )
                        checks.append(
                            {
                                "queryId": f"smoke-{index + 1}",
                                "resultCount": len(results),
                                "foreignRows": foreign,
                                "route": "library-prefix" if key else "no-partition-key",
                            }
                        )
                    health["checks"][mode + "Queries"] = checks
                health["checks"]["hybridListsDiffer"] = lists["hybrid"][0] != lists["hybrid"][1]
                require(
                    health["checks"]["hybridListsDiffer"],
                    "distinct source queries did not produce distinct hybrid ordering",
                )
            except Exception as caught:
                error = caught
                event(root, "smoke-failure", errorType=type(caught).__name__, message=str(caught))
            # No worker, queue or blob exists for this smoke; exact owned writes are quiescent here.
            try:
                health["checks"]["cleanup"] = await cleanup_owned(database, root, ledger, args.late_recheck)
            except Exception as caught:
                event(root, "cleanup-failure", errorType=type(caught).__name__, message=str(caught))
                error = error or caught
            health["embeddingUsage"] = asdict(embedder.usage)
            health["completedAt"] = now()
            health["status"] = "failed" if error else "pending_model_recheck"
            save(root / "health-pending.json", health)
            return {"status": health["status"], "chunks": CHUNKS, "cleanupComplete": "cleanup" in health["checks"]}


def finalize(args):
    settings = Settings.from_env(read(private_path(args.env)))
    with locked(args.state_dir) as root:
        health = read(root / "health-pending.json")
        require(health["status"] == "pending_model_recheck", "smoke did not pass all live checks")
        require(health["producerSourceSha256"] == sha(__file__), "smoke helper changed")
        require(health["settingsBinding"] == binding(settings), "smoke settings changed")
        before = read(root / "embedding-before.json")
        after = read(private_path(args.embedding_after))
        require(
            model_binding(before, settings, fresh=False)
            == health["embeddingBinding"]
            == model_binding(after, settings),
            "embedding deployment changed",
        )
        require(
            datetime.fromisoformat(after["capturedAt"]) >= datetime.fromisoformat(health["completedAt"]),
            "post-smoke model receipt predates completion",
        )
        write_new(root / "embedding-after.json", after)
        health["ownershipReceipt"] = artifact(root / "ownership.json", "ownership")
        health["embeddingBeforeReceipt"] = artifact(root / "embedding-before.json", "embedding-before")
        health["embeddingAfterReceipt"] = artifact(root / "embedding-after.json", "embedding-after")
        health["rawArtifacts"] = [artifact(path, path.name) for path in sorted(root.rglob("*.json"))]
        health["rawArtifacts"] += health["sourceArtifacts"]
        health["status"] = "passed"
        health["finalizedAt"] = now()
        write_new(root / "health.json", health)
        return {"status": "passed", "chunks": CHUNKS, "indexEngagementClaim": False, "performanceClaim": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "cleanup", "finalize"))
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--canary", type=Path)
    parser.add_argument("--protected-scope", type=Path)
    parser.add_argument("--embedding-before", type=Path)
    parser.add_argument("--embedding-after", type=Path)
    parser.add_argument("--late-recheck", type=int, default=30)
    args = parser.parse_args()
    required = {
        "run": ("corpus", "canary", "protected_scope", "embedding_before"),
        "cleanup": ("protected_scope",),
        "finalize": ("embedding_after",),
    }
    for name in required[args.action]:
        if getattr(args, name) is None:
            parser.error("--" + name.replace("_", "-") + " is required")

    async def limited():
        async with asyncio.timeout(900):
            return await execute(args)

    result = finalize(args) if args.action == "finalize" else asyncio.run(limited())
    print(json.dumps(result))
    return 0 if result["status"] in {"passed", "pending_model_recheck", "cleanup_complete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
