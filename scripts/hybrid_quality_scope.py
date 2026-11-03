"""Coordinator-run ingestion and cleanup of one ledger-owned confirmation library.

Cloud calls are explicit CLI actions. No resource provisioning, worker deployment,
model answering, search measurements, or mutation of pre-existing libraries.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fli.config import Settings  # noqa: E402
from scripts.canary import definition_mismatches, target_fingerprint  # noqa: E402
from scripts.hybrid_quality_corpus import PACK, load_pack, serialized, verify  # noqa: E402
from scripts.retrieval_controls import private_path  # noqa: E402
from scripts.retrieval_i1_lifecycle import BINDING_KEYS, PROFILE, api_instance, library_chunks  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
USAGE_KEYS = (
    "embeddingTokens",
    "embeddingRequests",
    "embeddingRetries",
    "embeddingThrottles",
    "embeddingMs",
    "chunkWriteCharge",
    "chunkWriteMs",
    "chunkTokens",
    "sourceTextTokensEstimate",
)


def now():
    return datetime.now(UTC).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def save(path, value):
    """Atomic durable private state, including BEFORE every uncertain mutation."""
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(root):
    root = private_path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(root / "operation.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield root
    finally:
        os.close(descriptor)


def binding(settings):
    return {key: str(getattr(settings, key.lower())) for key in BINDING_KEYS}


def checked_preflight(settings, preflight, canary_path):
    canary = json.loads(private_path(canary_path).read_text())
    require(
        preflight.get("inheritedCanaryPassed") is True
        and preflight.get("incarnationAndPoliciesMatch") is True
        and preflight.get("newContainerCreated") is False
        and preflight.get("scaleResourcesAccessed") is False
        and preflight.get("canarySha256", preflight.get("inheritedCanarySha256")) == sha(canary_path)
        and canary.get("passed") is True
        and all(row.get("passed") is True for row in canary.get("checks", []))
        and bool(canary.get("checks"))
        and preflight.get("targetFingerprint") == canary.get("targetFingerprint"),
        "passing coordinator preflight and canary binding required",
    )
    require(
        settings.environment == "local"
        and settings.auth_mode == "dev"
        and settings.aoai_embedding_dimensions == 3072
        and settings.aoai_embedding_model_revision == "1"
        and settings.search_default_top == 8
        and settings.search_max_top == 20,
        "frozen local foundation controls differ",
    )


async def check_incarnation(app, settings, preflight):
    containers = {
        "chunks": app.state.searcher.chunks._c,
        "libraries": app.state.libraries._c._c,
        "documents": app.state.documents._c._c,
    }
    for name, container in containers.items():
        actual = await container.read()
        expected = json.loads((REPO / "cosmos" / f"{name}.container.json").read_text())
        require(not definition_mismatches(expected, actual), "actual container policy differs")
        if name == "chunks":
            fingerprint = target_fingerprint(settings.cosmos_endpoint, settings.cosmos_database, name, actual["_rid"])
            require(fingerprint == preflight["targetFingerprint"], "actual canary container incarnation differs")


def summary(documents, sources):
    """Only public source roles and whitelisted numeric usage; never IDs or text."""
    by_id = {document["id"]: document for document in documents}
    rows = []
    for source_id, item in sources.items():
        document = by_id.get(item.get("documentId"), {})
        metrics = document.get("processingMetrics") or {}
        rows.append(
            {
                "sourceId": source_id,
                "status": document.get("status", "unavailable"),
                "publishedChunkCount": document.get("chunkCount"),
                "providerUsage": {
                    key: metrics.get(key) if type(metrics.get(key)) in (int, float) else None for key in USAGE_KEYS
                },
            }
        )
    return {
        "sources": rows,
        "readyDocuments": sum(row["status"] == "ready" for row in rows),
        "publishedChunkCount": sum(row["publishedChunkCount"] or 0 for row in rows),
        "providerUsageCaveat": (
            "Null means unavailable, never zero. Per-document generation totals are not per-attempt billing."
        ),
    }


class Scope:
    def __init__(self, root, manifest, binding_value, protected, manifest_hash):
        self.root, self.manifest = private_path(root), manifest
        self.path = self.root / "ownership.json"
        self.protected = set(protected)
        require(
            bool(self.protected) and all(isinstance(value, str) and value for value in self.protected),
            "explicit nonempty protected library inventory required",
        )
        self.contract = {
            "manifestSha256": manifest_hash,
            "tasksSha256": manifest["tasksSha256"],
            "settingsBinding": binding_value,
            "profile": PROFILE,
            "protectedLibraryIds": sorted(self.protected),
        }
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            require(
                self.state.get("contract") == self.contract and self.state.get("createdByThisRun") is True,
                "private ownership ledger binding differs",
            )
        else:
            self.state = {
                "schema": "fli.hybrid-quality.ownership.v1",
                "createdByThisRun": True,
                "runId": uuid.uuid4().hex,
                "createdAt": now(),
                "contract": self.contract,
                "sources": {},
            }
            self.persist()

    def persist(self):
        save(self.path, self.state)

    def owned(self):
        library_id = self.state.get("library", {}).get("id")
        require(bool(library_id) and str(uuid.UUID(library_id)) == library_id, "owned library ID missing or invalid")
        require(library_id not in self.protected, "owned library collides with protected scope")
        require(self.state.get("createAttempted") is True, "library has no durable creation attempt")
        return library_id

    async def request(self, client, method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        self.state.setdefault("httpReceipts", []).append(
            {
                "at": now(),
                "method": method,
                "path": path,
                "status": response.status_code,
                "body": response.json() if response.content else None,
            }
        )
        self.persist()
        return response

    async def guard_library(self, app, allow_absent=False):
        library_id = self.owned()
        actual = await app.state.libraries.get(library_id)
        if allow_absent and actual is None:
            return None
        require(
            actual
            and actual.get("createdBy") == self.state["runId"]
            and actual.get("name") == "Hybrid quality confirmation " + self.state["runId"]
            and actual.get("kind") == "shared"
            and actual.get("ingestionProfile") == self.state["library"]["ingestionProfile"],
            "actual library identity or frozen profile differs from ownership",
        )
        return actual

    async def documents(self, app):
        library_id = self.owned()
        documents = await app.state.documents.list(library_id, status=None, facet=None)
        expected = {item["documentId"] for item in self.state["sources"].values() if item.get("documentId")}
        require({document["id"] for document in documents} <= expected, "unrecorded document in owned scope")
        sources = {source["id"]: source for source in self.manifest["documents"]}
        by_id = {document["id"]: document for document in documents}
        for source_id, item in self.state["sources"].items():
            if item.get("documentId") not in by_id:
                continue
            document, source = by_id[item["documentId"]], sources[source_id]
            require(
                document.get("libraryId") == library_id
                and document.get("fileName") == source["fileName"]
                and document.get("sha256") == source["sha256"]
                and document.get("sizeBytes") == source["sizeBytes"]
                and document.get("generation") == 1
                and document.get("metadataVersion") == 0
                and document.get("author") == source["metadata"]["author"]
                and document.get("periodStart") == source["metadata"].get("periodStart")
                and document.get("periodEnd") == source["metadata"].get("periodEnd")
                and document.get("tags") == [{**tag, "source": "user"} for tag in source["metadata"]["tags"]]
                and document.get("ingestionJob", {}).get("profile", {}).get("id") == PROFILE,
                "published original, generation, profile, or metadata differs",
            )
        save(self.root / "documents-private.json", documents)
        save(self.root / "ingestion-summary.json", summary(documents, self.state["sources"]))
        return documents

    async def wait_ready(self, app, timeout, interval):
        deadline = time.monotonic() + timeout
        while True:
            await self.guard_library(app)
            documents = await self.documents(app)
            require(
                not any(document["status"] == "failed" for document in documents),
                "ingestion failed; private receipts retained",
            )
            if len(documents) == len(self.manifest["documents"]) and all(
                document["status"] == "ready"
                and not document.get("pendingGeneration")
                and document.get("activeGeneration") == 1
                for document in documents
            ):
                self.state["readyCompleted"] = True
                self.state["readyAt"] = now()
                self.state["readyDocuments"] = documents
                self.persist()
                save(
                    self.root / "evaluation-library.json",
                    {
                        "libraryId": self.owned(),
                        "documents": documents,
                        "confirmationExecuted": False,
                        "manifestSha256": self.contract["manifestSha256"],
                    },
                )
                return summary(documents, self.state["sources"])
            if time.monotonic() >= deadline:
                raise TimeoutError("ingestion pending; resume the same ownership ledger")
            await asyncio.sleep(interval)

    async def ingest(self, app, client, source_dir, timeout=1800, interval=10):
        require(not self.state.get("deleteAttempted"), "cleanup already started; ingestion cannot resume")
        if "library" not in self.state:
            require(not self.state.get("createAttempted"), "ambiguous library creation; do not create a duplicate")
            self.state["createAttempted"] = True
            self.persist()
            response = await self.request(
                client,
                "POST",
                "/v1/libraries",
                json={
                    "kind": "shared",
                    "name": "Hybrid quality confirmation " + self.state["runId"],
                    "createdBy": self.state["runId"],
                    "description": "Frozen public procurement contract confirmation corpus.",
                    "ingestionProfile": PROFILE,
                },
            )
            require(response.status_code == 201, "library creation response not successful; inspect private receipt")
            self.state["library"] = response.json()
            self.persist()
        await self.guard_library(app)
        await self.documents(app)
        library_id = self.owned()
        for source in self.manifest["documents"]:
            item = self.state["sources"].setdefault(source["id"], {})
            if item.get("documentId"):
                continue
            require(not item.get("attempted"), "ambiguous source upload; do not upload a duplicate")
            data = (private_path(source_dir) / source["fileName"]).read_bytes()
            require(
                hashlib.sha256(data).hexdigest() == source["sha256"] and len(data) == source["sizeBytes"],
                "original bytes changed",
            )
            item.update(attempted=True, attemptedAt=now(), sourceSha256=source["sha256"])
            self.persist()
            response = await self.request(
                client,
                "POST",
                f"/v1/libraries/{library_id}/documents",
                files={
                    "file": (
                        source["fileName"],
                        data,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"metadata": json.dumps(source["metadata"])},
            )
            require(response.status_code == 202, "upload response not successful; inspect private receipt")
            document_id = response.json()["documentId"]
            require(
                str(uuid.UUID(document_id)) == document_id
                and document_id not in {value.get("documentId") for value in self.state["sources"].values()},
                "unexpected upload document identity",
            )
            item["documentId"] = document_id
            self.persist()
        return await self.wait_ready(app, timeout, interval)

    async def absent(self, app):
        library_id = self.owned()
        observation = {
            "at": now(),
            "library": await app.state.libraries.get(library_id),
            "documents": await app.state.documents.list(library_id, status=None, facet=None),
            "chunks": await library_chunks(app, library_id),
            "blobPaths": await app.state.blobs.list_prefix(library_id + "/"),
        }
        self.state.setdefault("cleanupObservations", []).append(observation)
        self.persist()
        return (
            not observation["library"]
            and not observation["documents"]
            and not observation["chunks"]
            and not observation["blobPaths"]
        )

    async def cleanup(self, app, client, timeout=300, interval=5, late_recheck=30):
        require(
            self.state.get("readyCompleted") is True,
            "cleanup requires completed ready ingestion",
        )
        actual = await self.guard_library(app, allow_absent=True)
        if not self.state.get("deleteAttempted"):
            require(actual is not None, "owned library disappeared before cleanup")
            documents = await self.documents(app)
            require(
                len(documents) == len(self.manifest["documents"])
                and all(
                    document["status"] == "ready" and not document.get("pendingGeneration") for document in documents
                ),
                "all owned ingestions must be ready before cleanup",
            )
        self.state["deleteAttempted"] = True
        self.persist()
        response = await self.request(client, "DELETE", f"/v1/libraries/{self.owned()}")
        require(response.status_code in {202, 404}, "ordinary cleanup enqueue failed; resume same ledger")
        deadline = time.monotonic() + timeout
        while not await self.absent(app):
            if time.monotonic() >= deadline:
                raise TimeoutError("owned library purge still pending")
            await asyncio.sleep(interval)
        await asyncio.sleep(late_recheck)
        require(await self.absent(app), "late artifact reappeared; cleanup is incomplete")
        result = {
            "schema": "fli.hybrid-quality.cleanup.v1",
            "ownedLibrariesDeleted": 1,
            "protectedLibrariesMutated": 0,
            "libraryRecordsRemaining": 0,
            "documentRecordsRemaining": 0,
            "liveChunksRemaining": 0,
            "liveBlobPrefixEntriesRemaining": 0,
            "lateRecheckSeconds": late_recheck,
            "lateRecheckPassed": True,
            "retentionCaveat": "Live namespace absence only; configured soft-delete/backup retention remains.",
        }
        self.state["cleanupComplete"] = True
        self.persist()
        save(self.root / "cleanup-summary.json", result)
        return result


async def run(args):
    root = private_path(args.state_dir)
    manifest, tasks = load_pack(args.manifest, args.tasks)
    protected = json.loads(private_path(args.protected_scope).read_text())["libraryIds"]
    environment = json.loads(private_path(args.env).read_text())
    require(
        all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()),
        "string environment mapping required",
    )
    os.environ.update(environment)
    settings = Settings.from_env()
    preflight = json.loads(private_path(args.preflight).read_text())
    checked_preflight(settings, preflight, args.canary)
    if args.action == "ingest":
        require(args.source_dir is not None, "ingest requires private source directory")
        gold = verify(manifest, tasks, private_path(args.source_dir))
        require(hashlib.sha256(serialized(gold)).hexdigest() == manifest["goldSha256"], "frozen source gold differs")
    with locked(root):
        require(args.action == "ingest" or (root / "ownership.json").exists(), "existing ownership ledger required")
        scope = Scope(root, manifest, binding(settings), protected, sha(args.manifest))
        try:
            async with api_instance(settings) as (app, client):
                client.headers["X-Agent-Id"] = "hybrid-quality-owner"
                await check_incarnation(app, settings, preflight)
                if args.action == "ingest":
                    result = await scope.ingest(app, client, args.source_dir, args.timeout, args.poll_interval)
                elif args.action == "status":
                    await scope.guard_library(app)
                    result = summary(await scope.documents(app), scope.state["sources"])
                else:
                    result = await scope.cleanup(app, client, args.timeout, args.poll_interval, args.late_recheck)
            print(json.dumps(result))
        except Exception as exc:
            save(root / "failure-private.json", {"at": now(), "type": type(exc).__name__, "message": str(exc)})
            raise RuntimeError("scope action failed; inspect private failure and ownership receipts") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ingest", "status", "cleanup"))
    for name in ("state-dir", "env", "preflight", "canary", "protected-scope"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=PACK / "manifest.json")
    parser.add_argument("--tasks", type=Path, default=PACK / "tasks.json")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--poll-interval", type=float, default=10)
    parser.add_argument("--late-recheck", type=float, default=30)
    args = parser.parse_args()
    require(
        0 < args.timeout <= 3600 and 0 < args.poll_interval <= 60 and 5 <= args.late_recheck <= 60,
        "bounded timeouts and at least five-second late recheck required",
    )
    logging.getLogger("azure").setLevel(logging.CRITICAL)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
