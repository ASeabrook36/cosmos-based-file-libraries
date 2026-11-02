"""Bounded I1 lifecycle checks through two loopback APIs and real Azure services.

No provisioning, receiver, worker configuration, or held-out execution. The
coordinator supplies an existing canary-validated reservation. Only libraries
created by this invocation may be mutated. Raw HTTP receipts stay private.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import socket
import time
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import uvicorn

from fli.api import create_app
from fli.config import Settings
from fli.structure_lab import digest
from scripts.retrieval_controls import frozen_pack, private_path, read

REPO = Path(__file__).resolve().parents[1]
PROFILE = "microsoft-indexer-layout-v1"
BINDING_KEYS = (
    "COSMOS_ENDPOINT",
    "COSMOS_DATABASE",
    "BLOB_ACCOUNT_URL",
    "BLOB_CONTAINER",
    "SERVICEBUS_NAMESPACE",
    "SERVICEBUS_QUEUE",
    "MICROSOFT_WORKFLOW_ENDPOINT",
    "AOAI_ENDPOINT",
    "AOAI_EMBEDDING_DEPLOYMENT",
    "AOAI_EMBEDDING_DIMENSIONS",
    "SEARCH_MODE",
    "SEARCH_DEFAULT_TOP",
    "SEARCH_MAX_TOP",
    "AOAI_EMBEDDING_MODEL_REVISION",
)
CHECKS = (
    "caller_authentication",
    "personal_binding_all_retrieval",
    "foreign_selector_rejection",
    "selector_pin_roundtrip",
    "metadata_inflight_read",
    "metadata_propagation",
    "stale_generation_rejection",
    "stale_revision_rejection",
    "replacement_inflight_read",
    "replacement_queue_retry",
    "deletion_inflight_read",
    "deletion_retry_purge",
    "two_client_freshness",
)


def now():
    return datetime.now(UTC).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {
        str(p.relative_to(REPO)): sha(p)
        for directory in ("src/fli", "src/fli_demo")
        for p in sorted((REPO / directory).rglob("*.py"))
    }


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def require(value, label):
    if not value:
        raise AssertionError(label)


def error_only(response, status, code):
    body = response.json()
    require(response.status_code == status and set(body) == {"error"}, "error response status/envelope")
    require(body["error"]["code"] == code, "error response code")


def evidence(response):
    response.raise_for_status()
    body = response.json()
    return body.get("evidence", body)


async def chunk_rows(app, lib, doc, *, generation=None):
    """Diagnostic projection over one owned document; never retrieve vectors/text."""
    params = [{"name": "@libraryId", "value": lib}, {"name": "@documentId", "value": doc}]
    sql = (
        "SELECT TOP 100 c.id, c.generation, c.author, c.facets, c.periodStart, c.periodEnd FROM c "
        "WHERE c.libraryId = @libraryId AND c.documentId = @documentId"
    )
    if generation is not None:
        sql += " AND c.generation = @generation"
        params.append({"name": "@generation", "value": generation})
    rows = app.state.searcher.chunks._c.query_items(sql, parameters=params, partition_key=[lib, doc])
    return [row async for row in rows]


async def library_chunks(app, lib):
    rows = app.state.searcher.chunks._c.query_items(
        "SELECT TOP 1 c.id FROM c WHERE c.libraryId = @libraryId",
        parameters=[{"name": "@libraryId", "value": lib}],
        partition_key=[lib],
    )
    return [row async for row in rows]


class DownloadBarrier:
    """Pause once AFTER fetching real bytes, before normal endpoint rechecks."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.path = None
        self.arrived, self.release = asyncio.Event(), asyncio.Event()
        self.triggered = 0

    def __getattr__(self, key):
        return getattr(self.wrapped, key)

    def arm(self, path):
        require(self.path is None, "barrier already armed")
        self.path = path
        self.arrived.clear()
        self.release.clear()

    async def download(self, path):
        data = await self.wrapped.download(path)
        if path == self.path:
            self.path = None
            self.triggered += 1
            self.arrived.set()
            await asyncio.wait_for(self.release.wait(), 900)
        return data


class QueueFault:
    """One deliberate failure before send, limited to an exact owned message."""

    def __init__(self, wrapped, owned):
        self.wrapped, self.owned = wrapped, owned
        self.expected = None
        self.fired = 0

    def arm(self, message):
        require(message["libraryId"] in self.owned and self.expected is None, "queue fault scope")
        self.expected = message

    async def send(self, message, *, session_id):
        if self.expected and all(message.get(k) == v for k, v in self.expected.items()):
            require(session_id == message["libraryId"] and session_id in self.owned, "queue session scope")
            self.expected = None
            self.fired += 1
            raise RuntimeError("i1_injected_failure_before_send")
        await self.wrapped.send(message, session_id=session_id)


class LocalServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Both instances share one test driver; its normal signal handling owns exit.
        yield


@asynccontextmanager
async def api_instance(settings):
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = LocalServer(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(300):
            if server.started:
                break
            if task.done():
                await task
                raise RuntimeError("loopback API failed startup")
            await asyncio.sleep(0.1)
        require(server.started, "loopback API startup deadline")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
            timeout=960,
            headers={"Authorization": f"Bearer {settings.dev_api_key}", "X-Agent-Id": "i1-owner"},
        ) as client:
            yield app, client
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
        sock.close()


class Probe:
    def __init__(self, root, plan, preflight):
        self.root, self.plan, self.preflight = root, plan, preflight
        self.owned = []
        self.checks = []
        self.calls = []
        self.cleanup = []
        self.diagnostics = []
        self.started = now()
        self.initial_code = source_hashes()

    def diagnostic(self, kind, **facts):
        self.diagnostics.append({"at": now(), "kind": kind, **facts})
        save(self.root / "sdk-diagnostics-private.json", self.diagnostics)

    async def request(self, client, method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        # This complete content-bearing receipt is PRIVATE, never a repo export.
        self.calls.append(
            {
                "at": now(),
                "method": method,
                "path": path,
                "request": kwargs.get("json"),
                "status": response.status_code,
                "body": response.json() if response.content else None,
            }
        )
        save(self.root / "http-receipts.json", self.calls)
        return response

    def passed(self, check, **facts):
        require(check in CHECKS and check not in {r["id"] for r in self.checks}, "unique planned gate")
        self.checks.append({"id": check, "passed": True, "evidence": facts})
        save(self.root / "checks-partial.json", self.checks)

    async def create(self, client, kind):
        body = {"kind": kind, "name": "I1 lifecycle disposable", "ingestionProfile": PROFILE}
        if kind == "personal":
            body["boundAgentId"] = "i1-owner"
        result = await self.request(client, "POST", "/v1/libraries", json=body)
        result.raise_for_status()
        lib = result.json()["id"]
        self.owned.append(lib)
        save(self.root / "owned-scopes.json", self.owned)
        require(result.json()["ingestionProfile"]["id"] == PROFILE, "actual profile")
        return lib

    async def upload(self, client, lib, source, doc=None):
        require(lib in self.owned, "upload scope")
        path = f"/v1/libraries/{lib}/documents" + (f"/{doc}/content" if doc else "")
        return await self.request(
            client,
            "PUT" if doc else "POST",
            path,
            files={
                "file": (
                    "lifecycle-source.docx",
                    source.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    async def document(self, client, lib, doc):
        return await self.request(client, "GET", f"/v1/libraries/{lib}/documents/{doc}")

    async def ready(self, client, lib, doc, generation):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            response = await self.document(client, lib, doc)
            response.raise_for_status()
            body = response.json()
            require(body["status"] != "failed", "real ingestion failed")
            if body.get("activeGeneration") == generation and body["status"] == "ready":
                require(body["ingestionJob"]["profile"]["id"] == PROFILE, "published profile")
                return body
            await asyncio.sleep(2)
        raise TimeoutError("real ingestion deadline")

    async def read(self, client, lib, body):
        return await self.request(
            client,
            "POST",
            f"/v1/libraries/{lib}/documents:read?view=compact&selectors=handles",
            json={"maxTokens": 200000, **body},
        )

    async def selector(self, client, lib, doc):
        response = evidence(await self.read(client, lib, {"mode": "outline", "documents": [{"documentId": doc}]}))
        item = response["documents"][0]
        selection = item["outline"][0]["readSelector"]
        require(selection["documents"][0]["documentId"] == doc, "selector document binding")
        require(
            all(k in selection["documents"][0] for k in ("generation", "contentRevision", "sectionIds")),
            "selector carries pins",
        )
        return item["documentSelector"], selection

    async def search(self, client, lib, query, selector, **extra):
        return await self.request(
            client,
            "POST",
            f"/v1/libraries/{lib}/search?view=compact&selectors=handles",
            json={"query": query, "document": selector, **extra},
        )

    async def race(self, client, barrier, lib, documents, path, mutate):
        barrier.arm(path)
        task = asyncio.create_task(self.read(client, lib, {"documents": documents}))
        try:
            await asyncio.wait_for(barrier.arrived.wait(), 30)
            await mutate()
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            barrier.path = None
            barrier.release.set()
        response = await task
        error_only(response, 409, "document_changed")
        return response

    async def checks_run(self, a, b, old, new):
        app_a, ca = a
        app_b, cb = b
        lib = await self.create(ca, "shared")
        foreign = await self.create(ca, "shared")
        personal = await self.create(ca, "personal")
        qfault = QueueFault(app_a.state.queue, self.owned)
        app_a.state.queue = qfault
        barrier = DownloadBarrier(app_b.state.blobs)
        app_b.state.blobs = barrier
        app_b.state.searcher.blobs = barrier

        # These denial paths must reject before embedding, Blob access or document lookup.
        endpoints = {
            "tools": None,
            "search": {"query": "lifecycle authorization check"},
            "documents:discover": {},
            "documents:read": {"documents": [{"documentId": "absent"}]},
            "documents:images": {"documentId": "absent", "pages": [1]},
        }
        for endpoint, body in endpoints.items():
            for auth in ("", "Bearer invalid"):
                r = await self.request(
                    cb,
                    "GET" if body is None else "POST",
                    f"/v1/libraries/{personal}/{endpoint}",
                    json=body,
                    headers={"Authorization": auth},
                )
                error_only(r, 401, "unauthorized")
            r = await self.request(
                cb,
                "GET" if body is None else "POST",
                f"/v1/libraries/{personal}/{endpoint}",
                json=body,
                headers={"X-Agent-Id": "i1-other"},
            )
            error_only(r, 403, "library_not_bound_to_agent")
            r = await self.request(
                cb,
                "GET" if body is None else "POST",
                f"/v1/libraries/{personal}/{endpoint}",
                json=body,
                headers={"X-Agent-Id": ""},
            )
            error_only(r, 400, "missing_agent_id")
        for field, value in (("kind", "shared"), ("boundAgentId", "i1-other")):
            r = await self.request(ca, "PATCH", f"/v1/libraries/{personal}", json={field: value})
            error_only(r, 400, "immutable_field")
        self.passed("caller_authentication", endpointCount=5, denials=10, authenticationMode="dev")
        self.passed(
            "personal_binding_all_retrieval",
            endpointCount=5,
            wrongAgentDenials=5,
            missingAgentDenials=5,
            immutableFieldDenials=2,
        )

        first = await self.upload(ca, lib, old)
        first.raise_for_status()
        doc = first.json()["documentId"]
        other = await self.upload(ca, lib, new)
        other.raise_for_status()
        second = other.json()["documentId"]
        before = await self.ready(ca, lib, doc, 1)
        await self.ready(ca, lib, second, 1)
        pin, selection = await self.selector(cb, lib, doc)
        pin2, _ = await self.selector(cb, lib, second)
        selected = evidence(await self.read(cb, lib, selection))
        require(
            selected["selectionComplete"] and selected["documents"][0]["generation"] == pin["generation"],
            "selected source roundtrip",
        )
        query = " ".join(selected["documents"][0]["sections"][0]["text"].split()[:25])
        hits = evidence(await self.search(cb, lib, query, pin))["results"]
        require(
            hits
            and all(all(hit[k] == pin[k] for k in ("documentId", "generation", "contentRevision")) for hit in hits),
            "document-scoped search source binding",
        )
        self.passed(
            "selector_pin_roundtrip",
            generation=pin["generation"],
            contentRevision=pin["contentRevision"],
            completeSelectedRead=True,
            documentScopedSearchMatches=True,
            searchResults=len(hits),
        )
        error_only(await self.read(cb, foreign, selection), 404, "document_not_found")
        error_only(await self.search(cb, foreign, query, pin), 404, "document_not_found")
        forged = copy.deepcopy(selection)
        forged["documents"][0].update(pin2)
        error_only(await self.read(cb, lib, forged), 409, "evidence_changed")
        injected_library = {**selection, "libraryId": foreign}
        error_only(await self.read(cb, lib, injected_library), 400, "invalid_request")
        error_only(await self.search(cb, lib, query, pin, libraryId=foreign), 400, "invalid_request")
        self.passed(
            "foreign_selector_rejection",
            foreignLibraryRead=1,
            foreignLibrarySearch=1,
            foreignDocumentHandle=1,
            bodyLibraryInjectionRead=1,
            bodyLibraryInjectionSearch=1,
        )
        stale = copy.deepcopy(selection)
        stale["documents"][0]["contentRevision"] += 1
        error_only(await self.read(cb, lib, stale), 409, "document_changed")
        error_only(
            await self.search(cb, lib, query, {**pin, "contentRevision": pin["contentRevision"] + 1}),
            409,
            "document_changed",
        )
        self.passed(
            "stale_revision_rejection", wrongPinnedRevisionReadRejected=True, wrongPinnedRevisionSearchRejected=True
        )

        metadata = {
            "author": "I1 lifecycle author",
            "tags": [{"key": "i1", "value": "updated"}],
            "periodStart": "2026-01-01",
            "periodEnd": "2026-12-31",
        }

        async def patch():
            r = await self.request(ca, "PATCH", f"/v1/libraries/{lib}/documents/{doc}", json=metadata)
            r.raise_for_status()

        await self.race(cb, barrier, lib, [pin, pin2], before["fullText"]["blobPath"], patch)
        self.passed("metadata_inflight_read", batchDocuments=2, bodyReturned=False, independentClients=True)
        refreshed = evidence(await self.read(cb, lib, {"documents": [pin]}))["documents"][0]
        require(all(refreshed[k] == metadata[k] for k in ("author", "periodStart", "periodEnd")), "metadata read")
        require(refreshed["tags"][0]["value"] == "updated", "metadata tag")
        catalogue = evidence(
            await self.request(
                cb,
                "POST",
                f"/v1/libraries/{lib}/documents:discover?view=compact&selectors=handles",
                json={"filters": {"authors": [metadata["author"]], "tags": ["i1=updated"]}},
            )
        )
        require(
            len(catalogue["documents"]) == 1
            and catalogue["documents"][0]["documentId"] == doc
            and catalogue["documents"][0]["author"] == metadata["author"]
            and catalogue["documents"][0]["tags"][0]["value"] == "updated",
            "metadata catalogue reflects edit",
        )
        deadline = time.monotonic() + 180
        rows = []
        while time.monotonic() < deadline:
            rows = await chunk_rows(app_a, lib, doc, generation=1)
            self.diagnostic(
                "metadata_chunks",
                libraryId=lib,
                documentId=doc,
                generation=1,
                fullDocumentPartition=True,
                projection="id,generation,author,facets,periodStart,periodEnd",
                limit=100,
                expectedChunks=before["chunkCount"],
                rows=rows,
            )
            if (
                rows
                and len(rows) == before["chunkCount"]
                and all(
                    row.get("author") == metadata["author"]
                    and "i1=updated" in row.get("facets", [])
                    and all(row.get(k) == metadata[k] for k in ("periodStart", "periodEnd"))
                    for row in rows
                )
            ):
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError("metadata chunk propagation")
        self.passed(
            "metadata_propagation",
            matchingActiveChunks=len(rows),
            expectedChunks=before["chunkCount"],
            readMetadataMatches=True,
            filteredDiscoveryMetadataMatches=True,
        )

        qfault.arm({"type": "ingest", "libraryId": lib, "documentId": doc})
        error_only(await self.upload(ca, lib, new, doc), 503, "queue_unavailable")
        failed = (await self.document(ca, lib, doc)).json()
        require(
            failed["activeGeneration"] == 1 and failed["generation"] == 2 and failed["pendingGeneration"] is None,
            "failed replacement preserves active generation",
        )
        require(evidence(await self.read(cb, lib, selection))["selectionComplete"], "old pinned source still readable")

        async def replace():
            r = await self.upload(ca, lib, new, doc)
            r.raise_for_status()
            require(r.json()["generation"] == 3, "failed generation never reused")
            fresh = await self.ready(ca, lib, doc, 3)
            require(fresh["sha256"] == sha(new), "replacement source provenance")

        await self.race(cb, barrier, lib, [pin, pin2], before["fullText"]["blobPath"], replace)
        self.passed("replacement_inflight_read", batchDocuments=2, bodyReturned=False, independentClients=True)
        error_only(await self.read(cb, lib, selection), 409, "document_changed")
        error_only(await self.search(cb, lib, query, pin), 409, "document_changed")
        self.passed(
            "stale_generation_rejection",
            staleSelectedReadRejected=True,
            staleDocumentSearchRejected=True,
            pinsPreserved=True,
        )
        current_pin, current_selection = await self.selector(cb, lib, doc)
        current = evidence(await self.read(cb, lib, current_selection))["documents"][0]
        require(current["generation"] == 3, "retry generation")
        self.passed(
            "replacement_queue_retry",
            injectedFailures=1,
            activeAfterFailure=1,
            failedGeneration=2,
            retryGeneration=3,
            sourceHashMatches=True,
            oldPinsRejected=True,
            scope="API enqueue retry; no broker worker redelivery claim",
        )

        fresh = (await self.document(ca, lib, doc)).json()

        async def delete():
            qfault.arm({"type": "purgeDocument", "libraryId": lib, "documentId": doc})
            error_only(
                await self.request(ca, "DELETE", f"/v1/libraries/{lib}/documents/{doc}"), 503, "queue_unavailable"
            )

        await self.race(cb, barrier, lib, [current_pin, pin2], fresh["fullText"]["blobPath"], delete)
        self.passed("deletion_inflight_read", batchDocuments=2, bodyReturned=False, independentClients=True)
        errors = []
        for _ in range(10):
            r = await self.read(cb, lib, current_selection)
            error_only(r, 404, "document_not_found")
            errors.append(r.status_code)
        require(evidence(await self.read(cb, lib, {"documents": [pin2]}))["complete"], "unrelated document intact")
        retried = await self.request(ca, "DELETE", f"/v1/libraries/{lib}/documents/{doc}")
        require(retried.status_code == 202 and qfault.fired == 2, "repeat deletion enqueue")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            record = await app_a.state.documents.get(lib, doc)
            paths = await app_a.state.blobs.list_prefix(f"{lib}/{doc}/")
            self.diagnostic("document_purge_poll", libraryId=lib, documentId=doc, record=record, blobPaths=paths)
            if record is None and not paths:
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError("document purge deadline")
        remaining = await chunk_rows(app_a, lib, doc)
        self.diagnostic(
            "document_purge_chunks",
            libraryId=lib,
            documentId=doc,
            allGenerations=True,
            fullDocumentPartition=True,
            limit=100,
            rows=remaining,
        )
        require(not remaining, "deleted document chunks remain")
        self.passed(
            "deletion_retry_purge",
            repeatedDeleteStatus=202,
            recordAbsent=True,
            blobPrefixEmpty=True,
            allGenerationChunks=0,
            unrelatedDocumentReadable=True,
        )
        self.passed(
            "two_client_freshness",
            independentCosmosClients=True,
            copiedSessionTokens=False,
            crossClientMutationBarriers=3,
            postDeleteReadDenials=len(errors),
            observedStaleBodies=0,
            limitation="bounded observations; Session consistency does not imply global immediate revocation",
        )

    async def cleanup_owned(self, app, client):
        for lib in self.owned:
            try:
                await self.cleanup_one(app, client, lib)
            except Exception as exc:
                self.cleanup.append({"libraryId": lib, "passed": False, "failureCategory": type(exc).__name__})
                save(self.root / "cleanup-private.json", self.cleanup)
        require(
            len(self.cleanup) == len(self.owned) and all(row["passed"] for row in self.cleanup),
            "owned scope cleanup incomplete",
        )

    async def cleanup_one(self, app, client, lib):
        r = await self.request(client, "DELETE", f"/v1/libraries/{lib}")
        require(r.status_code in (202, 404), "owned library cleanup enqueue")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            library = await app.state.libraries.get(lib)
            paths = await app.state.blobs.list_prefix(f"{lib}/")
            documents = await app.state.documents.list(lib, status=None, facet=None)
            chunks = await library_chunks(app, lib)
            self.diagnostic(
                "library_purge_poll",
                libraryId=lib,
                libraryRecord=library,
                documentRecords=documents,
                anyChunkProjection=chunks,
                chunkLimit=1,
                blobPaths=paths,
            )
            if library is None and not paths and not documents and not chunks:
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError("owned library cleanup deadline")
        self.cleanup.append(
            {
                "libraryId": lib,
                "passed": True,
                "recordAbsent": True,
                "blobPrefixEmpty": True,
                "documentRecordsAbsent": True,
                "chunksAbsent": True,
            }
        )
        save(self.root / "cleanup-private.json", self.cleanup)


async def run(args):
    root = private_path(args.out)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan, preflight = read(args.plan), read(private_path(args.preflight))
    require(plan["requiredLifecycleChecks"] == list(CHECKS), "lifecycle plan gates differ")
    manifest, _ = frozen_pack(REPO / "docs/testing/retrieval-r0")
    sources = {d["id"]: d for d in manifest["documents"]}
    require(
        sha(private_path(args.old_docx)) == sources["d01"]["sha256"]
        and sha(private_path(args.new_docx)) == sources["d02"]["sha256"],
        "declared development sources differ",
    )
    settings = Settings.from_env()
    require(settings.environment == "local" and settings.auth_mode == "dev", "loopback dev auth only")
    binding = {key: str(getattr(settings, key.lower())) for key in BINDING_KEYS}
    require(
        binding == preflight["settingsBinding"] and digest(binding) == preflight["settingsBindingSha256"],
        "actual runtime binding differs",
    )
    require(
        preflight["inheritedCanaryPassed"] is True
        and preflight["incarnationAndPoliciesMatch"] is True
        and preflight["canarySha256"]
        and preflight["newContainerCreated"] is False,
        "coordinator canary evidence missing",
    )
    require(
        settings.aoai_embedding_model_revision == "1"
        and settings.search_mode == "vector"
        and settings.aoai_embedding_dimensions == 3072
        and settings.search_default_top == 8
        and settings.search_max_top == 20,
        "frozen foundation",
    )
    input_hashes = {name: sha(getattr(args, name)) for name in ("plan", "preflight", "old_docx", "new_docx")}
    harness_hash = sha(__file__)
    probe = Probe(root, plan, preflight)
    status, failure = "failed", None
    try:
        async with AsyncExitStack() as stack:
            a = await stack.enter_async_context(api_instance(settings))
            b = await stack.enter_async_context(api_instance(settings))
            try:
                async with asyncio.timeout(1800):
                    await probe.checks_run(a, b, private_path(args.old_docx), private_path(args.new_docx))
                require(
                    source_hashes() == probe.initial_code and sha(__file__) == harness_hash,
                    "code changed during lifecycle",
                )
                require(
                    all(sha(getattr(args, name)) == value for name, value in input_hashes.items()),
                    "frozen inputs changed during lifecycle",
                )
                require({r["id"] for r in probe.checks} == set(CHECKS), "missing lifecycle gate")
                status = "complete"
            finally:
                await probe.cleanup_owned(*a)
    except Exception as exc:
        status, failure = "failed", type(exc).__name__
        save(root / "failure-private.json", {"type": type(exc).__name__, "message": str(exc)})
    raw_paths = {p.name: str(p) for p in root.glob("*.json")}
    receipt = {
        "schema": "fli.retrieval-i1-lifecycle.v1",
        "status": status,
        "live": True,
        "experimentPlanSha256": input_hashes["plan"],
        "apiSourceSha256": probe.initial_code,
        "settingsBindingSha256": digest(binding),
        "profile": PROFILE,
        "authenticationMode": "dev",
        "startedAt": probe.started,
        "finishedAt": now(),
        "checks": probe.checks,
        "ownedLibraryIds": probe.owned,
        "rawReceiptPaths": raw_paths,
        "rawReceiptsSha256": {k: sha(v) for k, v in raw_paths.items()},
        "sourceSha256": {"old": input_hashes["old_docx"], "new": input_hashes["new_docx"]},
        "preflightSha256": input_hashes["preflight"],
        "harnessSha256": harness_hash,
        "cleanupComplete": len(probe.cleanup) == len(probe.owned) and all(row["passed"] for row in probe.cleanup),
        "failureCategory": failure,
        "untested": [
            "production Entra platform authentication",
            "broker worker redelivery",
            "global immediate Session-consistency revocation",
            "performance or sustained concurrency",
        ],
    }
    save(root / "receipt-private.json", receipt)
    print(
        json.dumps({"status": status, "passedChecks": len(probe.checks), "cleanupComplete": receipt["cleanupComplete"]})
    )
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "preflight", "old-docx", "new-docx", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    logging.getLogger("azure").setLevel(logging.CRITICAL)
    result = asyncio.run(run(parser.parse_args()))
    raise SystemExit(0 if result["status"] == "complete" else 1)


if __name__ == "__main__":
    main()
