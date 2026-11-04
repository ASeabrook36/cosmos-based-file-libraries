"""Bounded no-deployment granular source qualification against owned Azure scopes.

Private evidence only. This runner does not provision, deploy, or use Service Bus.
Existing experiments and context-accounting code are deliberately untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import uvicorn

from fli.api import create_app
from fli.blobs import AzureBlobs
from fli.chunking import Chunker, TiktokenTokenizer
from fli.chunks import CosmosChunks
from fli.config import Settings
from fli.docintel import SCOPE
from fli.embedding import AzureOpenAIEmbedder
from fli.governor import Governor
from fli.search import CosmosChunkReader, Searcher
from fli.store import CosmosDocumentStore, CosmosLibraryStore
from fli.tokens import CachedToken
from fli.worker.handlers import Worker
from fli_demo.client import IndexClient
from scripts.granular_live_preflight import management_get, protected_snapshot
from scripts.hybrid_quality_scope import save
from scripts.qualify_granular_navigation import LIMITS, PROFILE, REPO, prepare, require, sha, source_hashes, verify
from scripts.retrieval_controls import deployment_binding, private_path
from scripts.retrieval_i1_lifecycle import DownloadBarrier


def code_hashes():
    return {**source_hashes(), **{str(p.relative_to(REPO)): sha(p) for p in sorted((REPO / "scripts").glob("*.py"))}}


class BoundedEmbedder(AzureOpenAIEmbedder):
    """Shared ingestion/search meter; each physical retry remains visible."""

    def bind(self, root, deadline):
        self.root, self.deadline = root, deadline
        previous = root / "embedding-attempts.json"
        self.rows = json.loads(previous.read_text()) if previous.exists() else []
        require(
            all(row.get("status") in {"response", "error"} for row in self.rows),
            "unfinished embedding attempt requires review",
        )
        self.tokenizer = TiktokenTokenizer()
        self.max_attempts = LIMITS["embeddingAttemptsEach"]
        return self

    async def _embed_batch(self, batch):
        require(time.monotonic() < self.deadline, "embedding execution deadline")
        require(len(self.rows) < LIMITS["logicalEmbeddingRequests"], "logical embedding request ceiling")
        tokens = sum(len(self.tokenizer.encode(text)) for text in batch)
        require(
            sum(r["localInputTokens"] for r in self.rows) + tokens <= LIMITS["sourceEmbeddingTokens"],
            "embedding input ceiling",
        )
        row = {"logicalCall": len(self.rows) + 1, "localInputTokens": tokens, "inputs": len(batch), "status": "started"}
        self.rows.append(row)
        save(self.root / "embedding-attempts.json", self.rows)
        before = copy.copy(self.usage)
        self.usage.tokens_reported = False
        try:
            result = await super()._embed_batch(batch)
            row["status"] = "response"
            return result
        except BaseException as error:
            row.update(status="error", errorType=type(error).__name__)
            raise
        finally:
            row.update(
                physicalRequests=self.usage.requests - before.requests,
                retries=self.usage.retries - before.retries,
                throttles=self.usage.throttled - before.throttled,
                reportedPromptTokens=(self.usage.prompt_tokens - before.prompt_tokens)
                if self.usage.tokens_reported
                else None,
            )
            row["unmeteredAttempts"] = max(0, row["physicalRequests"] - (row["reportedPromptTokens"] is not None))
            self.usage.tokens_reported = self.usage.tokens_reported or before.tokens_reported
            save(self.root / "embedding-attempts.json", self.rows)


@asynccontextmanager
async def loopback(app):
    class Address(str):
        pass

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, timeout_graceful_shutdown=10))
    task = asyncio.create_task(server.serve(sockets=[sock]))

    async def quiesce():
        # Client cancellation does not imply server-handler cancellation. Stop
        # acceptance, then join every handler before the ownership snapshot.
        active = set(server.server_state.tasks)
        server.should_exit = True
        await asyncio.wait_for(asyncio.shield(task), 30)
        active.update(server.server_state.tasks)
        if active:
            await asyncio.wait_for(asyncio.gather(*active, return_exceptions=True), 30)
        require(not server.server_state.tasks, "loopback handlers remain active")

    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        require(server.started, "private loopback API startup failed")
        address = Address(f"http://127.0.0.1:{sock.getsockname()[1]}")
        address.quiesce = quiesce
        yield address
    finally:
        await quiesce()
        sock.close()


@asynccontextmanager
async def services(settings, ledger, root, deadline):
    from azure.cosmos._retry_options import RetryOptions
    from azure.cosmos.aio import CosmosClient
    from azure.cosmos.documents import ConnectionPolicy
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient

    from scripts.granular_live_safety import (
        GuardedBlobs,
        GuardedChunks,
        GuardedDocuments,
        GuardedLibraries,
        LedgerQueue,
    )

    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential())
        policy = ConnectionPolicy()
        policy.RetryOptions = RetryOptions(max_retry_attempt_count=0, max_wait_time_in_seconds=0)
        cosmos = await stack.enter_async_context(
            CosmosClient(
                settings.cosmos_endpoint,
                credential=credential,
                connection_policy=policy,
                retry_total=0,
                retry_write=0,
                retry_connect=0,
                retry_read=0,
                retry_status=0,
                connection_timeout=10,
                read_timeout=30,
            )
        )
        require(
            cosmos.client_connection.connection_policy.RetryOptions.MaxRetryAttemptCount == 0,
            "Cosmos throttle retry bound differs",
        )
        require(
            cosmos.client_connection.connection_policy.ConnectionRetryConfiguration.total_retries == 0,
            "Cosmos transport retry bound differs",
        )
        database = cosmos.get_database_client(settings.cosmos_database)
        blob_service = await stack.enter_async_context(
            BlobServiceClient(settings.blob_account_url, credential=credential, retry_total=0)
        )
        blob_container = blob_service.get_container_client(settings.blob_container)
        http = await stack.enter_async_context(httpx.AsyncClient())
        embedder = BoundedEmbedder(
            endpoint=settings.aoai_endpoint,
            deployment=settings.aoai_embedding_deployment,
            token_provider=CachedToken(credential, SCOPE),
            http=http,
            dimensions=settings.aoai_embedding_dimensions,
        ).bind(root, deadline)
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceNotFoundError
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        async def delete_record(container, record, partition):
            try:
                await database.get_container_client(container).delete_item(
                    record["id"],
                    partition_key=partition,
                    etag=record["_etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
            except CosmosResourceNotFoundError:
                pass

        async def delete_library(record):
            await delete_record("libraries", record, record["id"])

        async def delete_document(record):
            await delete_record("documents", record, record["libraryId"])

        async def delete_chunk(record):
            await delete_record("chunks", record, [record["libraryId"], record["documentId"]])

        async def read_chunk(library, document, identity):
            try:
                return await database.get_container_client("chunks").read_item(
                    identity, partition_key=[library, document]
                )
            except CosmosResourceNotFoundError:
                return None

        async def list_library_chunks(library):
            rows = [
                r
                async for r in database.get_container_client("chunks").query_items(
                    "SELECT TOP 65 c.id, c.libraryId, c.documentId, c.generation, c.contentHash, c._etag FROM c "
                    "WHERE c.libraryId = @libraryId",
                    parameters=[{"name": "@libraryId", "value": library}],
                    partition_key=[library],
                )
            ]
            require(len(rows) < 65, "owned chunk census ceiling")
            return rows

        async def snapshot_blob(path):
            client = blob_container.get_blob_client(path)
            try:
                props = await client.get_blob_properties()
                require(props.size <= LIMITS["sourceBytesEach"] * 4, "owned artifact byte ceiling")
                stream = await client.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified)
                data = await stream.readall()
                return {"etag": props.etag, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            except ResourceNotFoundError:
                raise

        async def delete_blob(path, snapshot):
            try:
                await blob_container.get_blob_client(path).delete_blob(
                    delete_snapshots="include", etag=snapshot["etag"], match_condition=MatchConditions.IfNotModified
                )
            except ResourceNotFoundError:
                pass

        libraries = GuardedLibraries(
            CosmosLibraryStore(database.get_container_client("libraries")), ledger, delete_if_unchanged=delete_library
        )
        documents = GuardedDocuments(
            CosmosDocumentStore(database.get_container_client("documents")), ledger, delete_if_unchanged=delete_document
        )
        blobs = GuardedBlobs(
            AzureBlobs(blob_container, blob_service),
            ledger,
            snapshot_blob=snapshot_blob,
            delete_if_unchanged=delete_blob,
        )
        chunks = GuardedChunks(
            CosmosChunks(database.get_container_client("chunks"), Governor("qualification", 2), max_attempts=1),
            ledger,
            read_chunk=read_chunk,
            delete_if_unchanged=delete_chunk,
            list_library_chunks=list_library_chunks,
        )
        queue = LedgerQueue(ledger)
        searcher = Searcher(
            libraries,
            documents,
            CosmosChunkReader(database.get_container_client("chunks")),
            embedder,
            mode="vector",
            blobs=blobs,
        )
        app = create_app(
            settings, libraries=libraries, documents=documents, blobs=blobs, queue=queue, searcher=searcher
        )
        worker = Worker(
            libraries,
            documents,
            chunks,
            blobs,
            queue,
            embedder,
            Chunker(TiktokenTokenizer()),
            max_chunks_per_document=64,
            embedding_model_revision=settings.aoai_embedding_model_revision,
        )
        yield SimpleNamespace(
            app=app,
            worker=worker,
            libraries=libraries,
            documents=documents,
            blobs=blobs,
            chunks=chunks,
            queue=queue,
            database=database,
            blob_container=blob_container,
            embedder=embedder,
        )


class Qualification:
    def __init__(self, root, fixtures, ledger, service, second, client, index, deadline):
        self.root, self.fixtures, self.ledger, self.s, self.b = root, fixtures, ledger, service, second
        self.client, self.index, self.deadline = client, index, deadline
        self.captures, self.checks, self.owned, self.documents = [], [], [], []

    def record(self, role, payload):
        self.captures.append({"role": role, "response": payload})
        save(self.root / "tool-responses.json", self.captures)
        return payload.get("evidence", payload)

    def passed(self, name):
        self.checks.append(name)
        save(self.root / "checks.json", self.checks)
        print(json.dumps({"passed": name}), flush=True)

    async def request(self, method, path, **kw):
        require(time.monotonic() < self.deadline, "execution deadline")
        response = await self.client.request(method, path, **kw)
        self.record(
            method + ":" + path, {"status": response.status_code, "body": response.json() if response.content else None}
        )
        return response

    async def call(self, role, method, *args, **kw):
        require(time.monotonic() < self.deadline, "execution deadline")
        return self.record(role, await asyncio.to_thread(method, *args, **kw))

    async def create(self, kind):
        body = {"kind": kind, "name": "Granular bounded qualification", "ingestionProfile": PROFILE}
        if kind == "personal":
            body["boundAgentId"] = "owner"
        response = await self.request("POST", "/v1/libraries", json=body)
        response.raise_for_status()
        identity = response.json()["id"]
        self.owned.append(identity)
        return identity

    async def upload(self, library, name):
        response = await self.request(
            "POST", f"/v1/libraries/{library}/documents", files={"file": (name, (self.fixtures / name).read_bytes())}
        )
        response.raise_for_status()
        document = response.json()["documentId"]
        self.documents.append((library, document, name))
        return document

    async def drain(self):
        await self.s.queue.drain(self.s.worker)

    async def read(self, library, **selection):
        return await self.call("read", self.index.read_documents, library, "owner", **selection)

    async def publication(self, library, document, name, generation):
        from fli.checkpoints import digest
        from fli.granular_source import validate_sections
        from fli.ids import chunk_id

        row = await self.s.documents.get(library, document)
        source = await self.s.blobs.download(row["blobPath"])
        require(
            hashlib.sha256(source).hexdigest() == row["sha256"] == sha(self.fixtures / name),
            "original source hash differs",
        )
        require(len(source) == row["sizeBytes"], "original source size differs")
        job = row["ingestionJob"]
        require(
            job["generation"] == generation == row["activeGeneration"] == row["fullText"]["generation"],
            "publication generations differ",
        )
        require(
            job["sourceSha256"] == row["sha256"] and job["profile"]["id"] == PROFILE, "publication job identity differs"
        )
        payload = await self.s.blobs.download(row["fullText"]["blobPath"])
        require(hashlib.sha256(payload).hexdigest() == row["fullText"]["sha256"], "source artifact hash differs")
        sections = json.loads(payload)
        validate_sections(sections, row)
        locations = {part["location"]["sourceAtomId"]: part["location"] for part in sections}
        records = [
            r
            async for r in self.s.database.get_container_client("chunks").query_items(
                "SELECT TOP 64 c.id, c.chunkIndex, c.generation, c.contentHash, c.text, c.heading, c.location, "
                "c.imageRef, c.imageMeta, c.embeddingModel, c.chunkingVersion FROM c "
                "WHERE c.libraryId = @libraryId AND c.documentId = @documentId AND c.generation = @generation",
                parameters=[
                    {"name": "@libraryId", "value": library},
                    {"name": "@documentId", "value": document},
                    {"name": "@generation", "value": generation},
                ],
                partition_key=[library, document],
            )
        ]
        require(len(records) == row["chunkCount"] < 64, "published chunk count differs")
        for record in records:
            require(record["id"] == chunk_id(document, generation, record["chunkIndex"]), "chunk identity differs")
            require(
                record["location"] == locations[record["location"]["sourceAtomId"]], "chunk source location differs"
            )
            require(
                record["contentHash"]
                == digest(
                    {
                        key: record[key]
                        for key in (
                            "text",
                            "heading",
                            "location",
                            "imageRef",
                            "imageMeta",
                            "embeddingModel",
                            "chunkingVersion",
                        )
                    }
                ),
                "chunk content hash differs",
            )
        save(
            self.root / f"{document}-publication-{generation}.json",
            {"document": row, "sections": sections, "chunks": records},
        )
        return row

    async def race(self, library, selector, source_path, mutate):
        barrier = DownloadBarrier(self.b.app.state.blobs)
        self.b.app.state.blobs = barrier
        self.b.app.state.searcher.blobs = barrier
        barrier.arm(source_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.b.app),
            base_url="http://qualification.invalid",
            headers={"Authorization": self.client.headers["Authorization"], "X-Agent-Id": "owner"},
        ) as other:
            task = asyncio.create_task(
                other.post(f"/v1/libraries/{library}/documents:read?view=compact&selectors=handles", json=selector)
            )
            try:
                await asyncio.wait_for(barrier.arrived.wait(), 30)
                await mutate()
                barrier.release.set()
                response = await asyncio.wait_for(task, 30)
                self.record("independent-client-race", {"status": response.status_code, "body": response.json()})
                require(
                    response.status_code == 409 and response.json()["error"]["code"] == "document_changed",
                    "post-I/O mutation escaped fence",
                )
            finally:
                barrier.release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self.b.app.state.blobs = barrier.wrapped
                self.b.app.state.searcher.blobs = barrier.wrapped

    async def mechanics(self, existing=None):
        if existing is None:
            shared, personal, foreign = (
                await self.create("shared"),
                await self.create("personal"),
                await self.create("shared"),
            )
            docx, markdown = await self.upload(shared, "source.docx"), await self.upload(shared, "source.md")
            lifecycle = await self.upload(personal, "source.docx")
        else:
            shared, personal, foreign = (existing[k] for k in ("shared", "personal", "foreign"))
            docx, markdown, lifecycle = (existing[k] for k in ("docx", "markdown", "lifecycle"))
            self.owned = [shared, personal, foreign]
            self.documents = [
                (shared, docx, "source.docx"),
                (shared, markdown, "source.md"),
                (personal, lifecycle, "source.docx"),
            ]
        await self.drain()
        for library, document, _ in self.documents:
            row = await self.publication(library, document, _, 1)
            require(row["status"] == "ready" and row["activeGeneration"] == 1, "actual source publication failed")
            save(self.root / (document + "-publication.json"), row)
        self.passed("actual_ingestion_and_immutable_source_publication")
        endpoints = {
            "tools": None,
            "search": {"query": "qualification permission check"},
            "documents:discover": {},
            "documents:read": {"documents": [{"documentId": lifecycle}]},
            "documents:images": {"documentId": lifecycle, "pages": [1]},
        }
        for endpoint, body in endpoints.items():
            method = "GET" if body is None else "POST"
            denied = await self.request(
                method, f"/v1/libraries/{personal}/{endpoint}", json=body, headers={"X-Agent-Id": "other"}
            )
            require(denied.status_code == 403, "personal binding failure")
            denied = await self.request(
                method, f"/v1/libraries/{personal}/{endpoint}", json=body, headers={"Authorization": "Bearer invalid"}
            )
            require(denied.status_code == 401, "caller authentication failure")
        self.passed("personal_binding_and_development_caller_authentication")
        root = await self.read(shared, mode="outline", documents=[{"documentId": docx}])
        pending, nodes = list(root["documents"][0]["outline"]), {}
        while pending:
            node = pending.pop(0)
            require(node["navigationNodeId"] not in nodes and len(nodes) < 64, "outline bound")
            nodes[node["navigationNodeId"]] = node
            require(
                node.get("sizeEstimateScope") and node["subtreeTokens"] >= node["sectionTokens"],
                "outline estimate scope",
            )
            if node["hasChildren"]:
                children = await self.read(shared, **node["outlineSelector"])
                require(not children["documents"][0]["hasMore"], "bounded outline exceeded page")
                pending.extend(children["documents"][0]["outline"])
        section = next(n for n in nodes.values() if n.get("title") == "Operating rules")
        cell = [n for n in nodes.values() if n["navigationKind"] == "tableCell"][-1]
        paragraph = [n for n in nodes.values() if n.get("parentNodeId") == cell["navigationNodeId"]][-1]
        selected = {}
        for role, selector in (
            ("paragraph", paragraph["readSelector"]),
            ("cell", cell["readSelector"]),
            ("section", section["readSelector"]),
            ("subtree", section["subtreeSelector"]),
            ("containing", paragraph["containingSectionSelector"]),
            ("whole", paragraph["fullSelector"]),
        ):
            selected[role] = await self.read(shared, **selector)
            self.record("scope:" + role, selected[role])
        parts = selected["cell"]["documents"][0]["sections"]
        require(
            len(parts) == 4
            and "<list-item " in parts[1]["text"]
            and "<list-item " not in parts[-1]["text"]
            and "These steps apply only" in parts[-1]["text"],
            "cell qualifier fidelity",
        )
        require(len(selected["paragraph"]["documents"][0]["sections"]) == 1, "paragraph broadened")
        require(
            len(selected["section"]["documents"][0]["sections"]) < len(selected["subtree"]["documents"][0]["sections"]),
            "section/subtree scopes equal",
        )
        require(selected["whole"]["complete"], "document expansion incomplete")
        self.passed("paragraph_cell_section_subtree_and_containing_document_navigation")
        md_outline = await self.read(shared, mode="outline", documents=[{"documentId": markdown}])
        md_heading = md_outline["documents"][0]["outline"][0]
        md_own = await self.read(shared, **md_heading["readSelector"])
        md_subtree = await self.read(shared, **md_heading["subtreeSelector"])
        require(
            len(md_own["documents"][0]["sections"]) < len(md_subtree["documents"][0]["sections"]),
            "Markdown section/subtree scope",
        )
        md_text = (self.fixtures / "source.md").read_text()
        for atom in md_subtree["documents"][0]["sections"]:
            loc = atom["location"]
            require(atom["text"] == md_text[loc["charStart"] : loc["charEnd"]], "Markdown exact source span differs")
        self.passed("markdown_hierarchy_and_exact_character_spans")
        # Quiet index warmup precedes search receipts; this tiny scope cannot prove ANN engagement.
        for _ in range(4):
            print(json.dumps({"status": "waiting_for_index_settlement", "qualificationOnly": True}), flush=True)
            await asyncio.sleep(30)
        hits = await self.call(
            "search",
            self.index.search,
            shared,
            "owner",
            "cold storage vessel steps",
            document=section["documentSelector"],
            top=3,
        )
        require(hits["results"], "real vector retrieval empty")
        for hit in hits["results"]:
            require(hit["passageToSourceMapping"] == "recorded_containing_source_atom", "source mapping absent")
            expanded = await self.read(shared, **hit["readSelector"])
            require(expanded["selectionComplete"], "search expansion incomplete")
        self.passed("real_vector_search_to_verified_source_expansion")
        pin = section["documentSelector"]
        page = await self.request(
            "POST",
            f"/v1/libraries/{shared}/documents:read",
            json={"mode": "selected", "documents": [{**pin, "pages": [1]}]},
        )
        require(
            page.status_code == 409 and page.json()["error"]["code"] == "source_scope_unavailable",
            "unsupported page accepted",
        )
        foreign_read = await self.request(
            "POST", f"/v1/libraries/{foreign}/documents:read", json=section["readSelector"]
        )
        require(foreign_read.status_code == 404, "foreign source accepted")
        budget = await self.read(shared, documents=[{"documentId": docx}, {"documentId": markdown}], maxTokens=1)
        require(
            budget["disposition"] == "budget_exceeded" and all("sections" not in d for d in budget["documents"]),
            "atomic budget failure",
        )
        self.passed("unavailable_page_foreign_scope_and_atomic_budget_refusals")
        original = await self.s.documents.get(personal, lifecycle)
        await self.s.worker.ingest(personal, lifecycle, 1)
        await self.drain()
        require(
            (await self.s.documents.get(personal, lifecycle))["fullText"] == original["fullText"],
            "retry changed source descriptor",
        )
        self.passed("actual_worker_idempotent_ingestion_retry")
        outline = await self.read(personal, mode="outline", documents=[{"documentId": lifecycle}])
        old = outline["documents"][0]["outline"][0]["readSelector"]

        async def metadata():
            response = await self.request(
                "PATCH", f"/v1/libraries/{personal}/documents/{lifecycle}", json={"author": "Qualification author"}
            )
            response.raise_for_status()

        await self.race(personal, old, original["fullText"]["blobPath"], metadata)
        await self.drain()
        require(
            (await self.s.documents.get(personal, lifecycle))["fullText"] == original["fullText"],
            "metadata altered source",
        )
        self.passed("independent_client_metadata_race_and_source_identity")
        outline = await self.read(personal, mode="outline", documents=[{"documentId": lifecycle}])
        old = outline["documents"][0]["outline"][0]["readSelector"]
        response = await self.request(
            "PUT",
            f"/v1/libraries/{personal}/documents/{lifecycle}/content",
            files={"file": ("replacement.docx", (self.fixtures / "replacement.docx").read_bytes())},
        )
        response.raise_for_status()
        pending_source = await self.read(personal, **old)
        require(
            pending_source["documents"][0]["generation"] == 1, "pending replacement failed to preserve old generation"
        )
        require(
            any("amber" in p["text"] for p in pending_source["documents"][0]["sections"]),
            "pending source content changed",
        )

        async def publish_only():
            # Leave the queued purge until the paused read has performed its
            # post-I/O revision check. The durable ingest message is replayed next.
            await self.s.worker.ingest(personal, lifecycle, 2)

        await self.race(personal, old, original["fullText"]["blobPath"], publish_only)
        await self.drain()
        stale = await self.request("POST", f"/v1/libraries/{personal}/documents:read", json=old)
        require(stale.status_code == 409, "replaced selector accepted")
        replacement = await self.publication(personal, lifecycle, "replacement.docx", 2)
        require(replacement["activeGeneration"] == 2, "replacement did not publish")
        self.passed("pending_replacement_inflight_publication_and_stale_selector")
        fresh = await self.read(personal, mode="outline", documents=[{"documentId": lifecycle}])
        selector = fresh["documents"][0]["outline"][0]["readSelector"]

        async def delete():
            response = await self.request("DELETE", f"/v1/libraries/{personal}/documents/{lifecycle}")
            require(response.status_code == 202, "deletion refused")

        await self.race(personal, selector, replacement["fullText"]["blobPath"], delete)
        denied = await self.request("POST", f"/v1/libraries/{personal}/documents:read", json=selector)
        require(denied.status_code == 404, "deleted source readable")
        await self.drain()
        await self.s.worker.purge_document(personal, lifecycle, deleted=True)
        require(
            await self.s.documents.get(personal, lifecycle) is None
            and not await self.s.blobs.list_prefix(f"{personal}/{lifecycle}/"),
            "owned deletion left artifacts",
        )
        self.passed("independent_client_deletion_fence_and_idempotent_purge")
        bindings = {}
        for role, document in (("docx", docx), ("markdown", markdown)):
            row = await self.s.documents.get(shared, document)
            bindings[role] = {
                "libraryId": shared,
                "documentId": document,
                "generation": row["activeGeneration"],
                "contentRevision": row["contentRevision"],
                "fileName": row["fileName"],
                "sourceSha256": row["sha256"],
            }
        save(self.root / "source-bindings.json", bindings)
        return bindings


def archive(root, destination):
    """Hash-verified private snapshot, without mutable logs or lock files."""
    destination = private_path(destination)
    require(not destination.is_relative_to(root), "archive destination cannot be inside live state")
    require(not destination.exists(), "archive destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(root, destination, ignore=shutil.ignore_patterns("*.log", "*.lock", "*.tmp"))
    files = {}
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            relative = path.relative_to(destination)
            require(sha(path) == sha(root / relative), "archive hash mismatch")
            files[str(relative)] = sha(path)
            path.chmod(0o600)
        elif path.is_dir():
            path.chmod(0o700)
    destination.chmod(0o700)
    save(destination / "archive-manifest.json", {"schema": "fli.granular.private-archive.v1", "files": files})
    return {"files": len(files), "manifestSha256": sha(destination / "archive-manifest.json")}


async def absence(service, ledger, protected):
    """Independent SDK client: point absence, scoped emptiness, exact prefixes."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    state = ledger.snapshot()
    counts = {"library404": 0, "document404": 0, "chunk404": 0, "emptyQueries": 0, "emptyPrefixes": 0}

    async def point(container, identity, partition, counter):
        try:
            await service.database.get_container_client(container).read_item(identity, partition_key=partition)
        except CosmosResourceNotFoundError:
            counts[counter] += 1
            return
        raise ValueError("owned item remains after cleanup")

    for library in state["libraries"]:
        await point("libraries", library, library, "library404")
        for container in ("documents", "chunks"):
            rows = [
                r
                async for r in service.database.get_container_client(container).query_items(
                    "SELECT TOP 1 c.id FROM c WHERE c.libraryId = @libraryId",
                    parameters=[{"name": "@libraryId", "value": library}],
                    partition_key=[library] if container == "chunks" else library,
                )
            ]
            require(not rows, "owned query is not empty")
            counts["emptyQueries"] += 1
        paths = [r.name async for r in service.blob_container.list_blobs(name_starts_with=library + "/")]
        require(not paths, "owned blob prefix is not empty")
        counts["emptyPrefixes"] += 1
    for document, row in state["documents"].items():
        await point("documents", document, row["libraryId"], "document404")
    for identity, row in state["chunks"].items():
        await point("chunks", identity, [row["libraryId"], row["documentId"]], "chunk404")
    current = await protected_snapshot(service.database, service.blob_container, list(protected))
    require(current == protected, "protected scope changed; preserve evidence without modifying it")
    return {**counts, "protectedUnchanged": True, "verifiedAt": datetime.now(UTC).isoformat()}


async def cleanup(root, service, independent, ledger, client, protected):
    from scripts.granular_live_safety import guard_owned_inventory

    ledger.assert_settled()
    service.queue.cancel_pending("qualification ended; owned writers quiesced before lifecycle cleanup")
    inventory = await guard_owned_inventory(service.libraries, service.documents, service.chunks, service.blobs, ledger)
    save(root / "cleanup-inventory.json", inventory)
    # Preflight every scope before any broad normal lifecycle purge.
    for library in ledger.snapshot()["libraries"]:
        if await service.libraries.get(library) is not None:
            response = await client.delete(f"/v1/libraries/{library}")
            require(response.status_code == 202, "normal owned library deletion refused")
    await service.queue.drain(service.worker)
    ledger.assert_settled()
    first = await absence(independent, ledger, protected)
    save(root / "cleanup-immediate.json", first)
    print(json.dumps({"cleanup": "immediate absence verified; waiting for late check"}), flush=True)
    await asyncio.sleep(30)
    await asyncio.sleep(30)
    late = await absence(independent, ledger, protected)
    save(root / "cleanup-late.json", late)
    return {
        "passed": True,
        "immediate": first,
        "late": late,
        "limitation": (
            "Current live items only; soft-delete/backups, hosted workers and indefinitely late output "
            "are outside this proof."
        ),
    }


def current_model(root, settings, role, *, embedding=False):
    account = json.loads((root / "account.json").read_text())
    deployment = settings.aoai_embedding_deployment if embedding else settings.aoai_chat_deployment
    label = "embedding" if embedding else "answering"
    actual = management_get(
        "https://management.azure.com" + account["id"] + "/deployments/" + deployment + "?api-version=2024-10-01",
        root / (label + "-" + role + ".json"),
    )
    expected = next(d for d in json.loads((root / "deployments.json").read_text())["value"] if d["name"] == deployment)
    require(
        actual["properties"]["model"] == expected["properties"]["model"] and actual["sku"] == expected["sku"],
        "answering model changed since preflight",
    )
    binding = deployment_binding(
        {"capturedAt": datetime.now(UTC).isoformat(), "account": account, "deployment": actual},
        configured_endpoint=settings.aoai_endpoint,
        configured_deployment=deployment,
        model=actual["properties"]["model"]["name"],
        revision=actual["properties"]["model"]["version"],
    )
    save(root / (label + "-binding-" + role + ".json"), binding)
    return binding


async def execute(state, archive_root):
    from scripts.canary import definition_mismatches, target_fingerprint
    from scripts.granular_live_models import prepare_cases, run_models
    from scripts.granular_live_safety import OwnedLedger
    from scripts.hybrid_model_meter import actual_client

    root, archive_root = private_path(state), private_path(archive_root)
    require(
        not archive_root.is_relative_to(root) and not root.is_relative_to(archive_root),
        "archive and live state must be separate",
    )
    require(not archive_root.exists(), "use a fresh private archive destination")
    require(not (root / "execution-started.json").exists(), "execution is single-use; retain all attempts")
    preflight = json.loads((root / "preflight.json").read_text())
    require(
        preflight["passed"] and preflight["environmentSha256"] == sha(root / "environment.json"),
        "preflight binding differs",
    )
    env = json.loads((root / "environment.json").read_text())
    os.environ.update(env)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    require(
        not subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip(),
        "commit reviewed runner before execution",
    )
    settings = replace(Settings.from_env(), ingestion_code_revision=revision)
    require(
        settings.search_mode == "vector" and settings.aoai_embedding_model_revision == "1", "frozen foundation differs"
    )
    prepare(root / "fixtures")
    verify(root / "fixtures")
    controls = {
        "candidateCommit": revision,
        "sourceSha256": code_hashes(),
        "limits": LIMITS,
        "environmentSha256": sha(root / "environment.json"),
        "searchMode": "vector",
        "contextAccounting": "legacy-v1",
        "contextRepairIncluded": False,
        "applicationDeployment": False,
        "sharedQueueAccess": False,
        "fixturePlanSha256": sha(root / "fixtures" / "plan.json"),
    }
    save(root / "controls.json", controls)
    protected = json.loads((root / "protected-before.json").read_text())
    require(sha(root / "protected-before.json") == preflight["protectedSha256"], "protected snapshot changed")
    deadline = time.monotonic() + LIMITS["executionSeconds"]
    save(
        root / "execution-started.json",
        {"startedAt": datetime.now(UTC).isoformat(), "controlsSha256": sha(root / "controls.json")},
    )
    result, failure = {"candidateCommit": revision, "status": "started"}, None
    with OwnedLedger.create(root / "ownership", binding=controls, protected_library_ids=list(protected)) as ledger:
        async with services(settings, ledger, root, deadline) as a, services(settings, ledger, root, deadline) as b:
            async with asyncio.timeout(300):
                for name in ("libraries", "documents", "chunks"):
                    actual = await a.database.get_container_client(name).read()
                    require(
                        not definition_mismatches(
                            json.loads((REPO / "cosmos" / f"{name}.container.json").read_text()), actual
                        ),
                        "execution policy differs",
                    )
                    if name == "chunks":
                        require(
                            target_fingerprint(settings.cosmos_endpoint, settings.cosmos_database, name, actual["_rid"])
                            == preflight["targetFingerprint"],
                            "execution incarnation differs",
                        )
                current = await protected_snapshot(a.database, a.blob_container)
                require(current == protected, "protected inventory changed before writes")
                model_binding = await asyncio.to_thread(current_model, root, settings, "before")
                embedding_binding = await asyncio.to_thread(current_model, root, settings, "before", embedding=True)
            async with (
                loopback(a.app) as url,
                httpx.AsyncClient(
                    base_url=url,
                    timeout=120,
                    headers={"Authorization": "Bearer " + settings.dev_api_key, "X-Agent-Id": "owner"},
                ) as client,
            ):
                index = IndexClient(url, settings.dev_api_key, selector_mode="handles")
                q = Qualification(root, root / "fixtures", ledger, a, b, client, index, deadline)
                try:
                    async with asyncio.timeout(max(1, deadline - time.monotonic())):
                        bindings = await q.mechanics()
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
                        # A cancelled await cannot kill a Python worker thread.
                        # Join its internally deadline-bounded work before any cleanup.
                        if not model_task.done():
                            await asyncio.shield(model_task)
                        provider.close()
                    require(
                        await asyncio.to_thread(current_model, root, settings, "after") == model_binding,
                        "model binding drift",
                    )
                    require(
                        await asyncio.to_thread(current_model, root, settings, "after", embedding=True)
                        == embedding_binding,
                        "embedding binding drift",
                    )
                    require(code_hashes() == controls["sourceSha256"], "candidate code drift during execution")
                    result["status"] = "mechanics_and_model_execution_complete_pending_review"
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
                                "reason": (
                                    "API handlers are not proven stopped; "
                                    "retain durable ownership evidence and resources."
                                ),
                            },
                        )
                        raise
                    result.update(checks=q.checks, ownership=ledger.summary(), embedding=a.embedder.rows)
                    save(root / "execution-result.json", result)
                    save(root / "ownership-before-cleanup.json", ledger.snapshot())
                    result["precleanupArchive"] = archive(root, archive_root / "before-cleanup")
                    print(
                        json.dumps(
                            {"status": result["status"], "checksPassed": len(q.checks), "archivedBeforeCleanup": True}
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
                                result["cleanup"] = await cleanup(root, a, b, ledger, cleanup_client, protected)
                    except BaseException as error:
                        result["cleanup"] = {"passed": False, "errorType": type(error).__name__, "error": str(error)}
                        failure = failure or error
                    index.http.close()
                    result["finalOwnership"] = ledger.summary()
                    save(root / "execution-result.json", result)
                    save(root / "ownership-final.json", ledger.snapshot())
    result["finalArchive"] = archive(root, archive_root / "after-cleanup")
    save(root / "archive-receipts.json", {k: result[k] for k in ("precleanupArchive", "finalArchive")})
    print(
        json.dumps(
            {
                "status": result["status"],
                "cleanupPassed": result.get("cleanup", {}).get("passed"),
                "checksPassed": len(result.get("checks", [])),
                "finalOwnership": result.get("finalOwnership"),
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
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--action", choices=("execute",), required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.WARNING)
    asyncio.run(execute(args.state, args.archive))


if __name__ == "__main__":
    main()
