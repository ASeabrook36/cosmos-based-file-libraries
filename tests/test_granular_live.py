"""Local regression gates for the exact runner that will qualify Azure services.

These use real API serialization, worker ingestion and ownership guards. Storage,
embedding transport and ranking are substitutes; no cloud/model calls occur.
"""

import asyncio
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from fli.api import create_app
from fli.chunking import Chunker, TiktokenTokenizer
from fli.embedding import EmbeddingError
from fli.search import Searcher
from fli.worker.handlers import Worker
from fli_demo.client import IndexClient
from scripts import granular_live as live
from scripts.granular_live_safety import (
    GuardedBlobs,
    GuardedChunks,
    GuardedDocuments,
    GuardedLibraries,
    LedgerQueue,
    OwnedLedger,
)
from scripts.qualify_granular_navigation import fixtures
from tests.fakes import AUTH, Services, settings


async def token():
    return "local-transport-test"


async def test_embedding_meter_does_not_turn_missing_usage_into_measured_zero(tmp_path):
    replies = [
        {"data": [{"index": 0, "embedding": [1.0]}], "usage": {"prompt_tokens": 7}},
        {"data": [{"index": 0, "embedding": [2.0]}]},
        {"data": [{"index": 0, "embedding": [3.0]}], "usage": {"prompt_tokens": 0}},
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=replies.pop(0)))
    ) as http:
        embedder = live.BoundedEmbedder("https://local.invalid", "embedding", token, http, dimensions=1).bind(
            tmp_path, time.monotonic() + 30
        )
        for expected in (1.0, 2.0, 3.0):
            assert await embedder.embed(["local source"]) == [[expected]]
    rows = json.loads((tmp_path / "embedding-attempts.json").read_text())
    assert [row["reportedPromptTokens"] for row in rows] == [7, None, 0]
    assert [row["unmeteredAttempts"] for row in rows] == [0, 1, 0]
    assert [row["physicalRequests"] for row in rows] == [1, 1, 1]


async def test_embedding_retries_are_bounded_and_failed_usage_is_unknown(tmp_path):
    attempts = []

    def transport(request):
        attempts.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        embedder = live.BoundedEmbedder("https://local.invalid", "embedding", token, http, dimensions=1).bind(
            tmp_path, time.monotonic() + 30
        )
        with pytest.raises(EmbeddingError):
            await embedder.embed(["local source"])
    assert len(attempts) == live.LIMITS["embeddingAttemptsEach"]
    row = json.loads((tmp_path / "embedding-attempts.json").read_text())[0]
    assert row["status"] == "error" and row["reportedPromptTokens"] is None
    assert row["unmeteredAttempts"] == row["physicalRequests"] == 3
    assert row["retries"] == 2 and row["throttles"] == 3


async def test_embedding_usage_remains_measured_when_response_dimensions_are_invalid(tmp_path):
    payload = {"data": [{"index": 0, "embedding": [1.0, 2.0]}], "usage": {"prompt_tokens": 11}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        embedder = live.BoundedEmbedder("https://local.invalid", "embedding", token, http, dimensions=1).bind(
            tmp_path, time.monotonic() + 30
        )
        with pytest.raises(EmbeddingError, match="dimensions differ"):
            await embedder.embed(["local source"])
    row = json.loads((tmp_path / "embedding-attempts.json").read_text())[0]
    assert row["status"] == "error" and row["reportedPromptTokens"] == 11
    assert row["physicalRequests"] == 1 and row["unmeteredAttempts"] == 0


async def test_resumed_embedding_meter_keeps_prior_consumption_and_call_sequence(tmp_path):
    previous = [
        {
            "logicalCall": 1,
            "localInputTokens": 98,
            "status": "response",
            "reportedPromptTokens": 98,
            "physicalRequests": 1,
            "retries": 0,
            "throttles": 0,
            "unmeteredAttempts": 0,
        }
    ]
    (tmp_path / "embedding-attempts.json").write_text(json.dumps(previous))
    payload = {"data": [{"index": 0, "embedding": [1.0]}], "usage": {"prompt_tokens": 7}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        embedder = live.BoundedEmbedder("https://local.invalid", "embedding", token, http, dimensions=1).bind(
            tmp_path, time.monotonic() + 30
        )
        await embedder.embed(["new source"])
    rows = json.loads((tmp_path / "embedding-attempts.json").read_text())
    assert rows[0] == previous[0] and rows[1]["logicalCall"] == 2
    assert sum(row["reportedPromptTokens"] for row in rows) == 105
    assert sum(row["physicalRequests"] for row in rows) == 2


def test_resumed_embedding_meter_refuses_unfinished_prior_attempt(tmp_path):
    (tmp_path / "embedding-attempts.json").write_text(json.dumps([{"logicalCall": 1, "status": "started"}]))
    with pytest.raises(ValueError, match="unfinished embedding attempt"):
        live.BoundedEmbedder("https://local.invalid", "embedding", token, None, dimensions=1).bind(
            tmp_path, time.monotonic() + 30
        )


async def test_loopback_quiescence_joins_handler_that_survives_client_cancellation():
    app = FastAPI()
    arrived, release = asyncio.Event(), asyncio.Event()
    writes = []

    @app.post("/delayed-write")
    async def delayed_write():
        arrived.set()
        await release.wait()
        writes.append("completed before cleanup")
        return {"ok": True}

    async with live.loopback(app) as url:
        async with httpx.AsyncClient(base_url=url, timeout=5) as client:
            request = asyncio.create_task(client.post("/delayed-write"))
            await asyncio.wait_for(arrived.wait(), 2)
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            stopping = asyncio.create_task(url.quiesce())
            await asyncio.sleep(0.05)
            assert not stopping.done() and not writes
            release.set()
            await asyncio.wait_for(stopping, 3)
            assert writes == ["completed before cleanup"]
            with pytest.raises(httpx.TransportError):
                await client.post("/delayed-write")


@pytest.mark.parametrize("ceiling", ["deadline", "calls", "tokens"])
async def test_embedding_gate_refuses_before_transport(tmp_path, ceiling):
    attempts = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: attempts.append(request))) as http:
        embedder = live.BoundedEmbedder("https://local.invalid", "embedding", token, http, dimensions=1).bind(
            tmp_path, time.monotonic() + 30
        )
        if ceiling == "deadline":
            embedder.deadline = time.monotonic() - 1
        elif ceiling == "calls":
            embedder.rows = [{"localInputTokens": 0}] * live.LIMITS["logicalEmbeddingRequests"]
        else:
            embedder.rows = [{"localInputTokens": live.LIMITS["sourceEmbeddingTokens"]}]
        with pytest.raises(ValueError, match="ceiling|deadline"):
            await embedder.embed(["local source"])
    assert not attempts


class ChunkDatabase:
    """Only the runner's scoped publication projection, backed by stored chunks."""

    def __init__(self, chunks):
        self.chunks, self.queries = chunks, []

    def get_container_client(self, name):
        assert name == "chunks"
        return self

    async def query_items(self, sql, *, parameters, partition_key):
        params = {p["name"]: p["value"] for p in parameters}
        assert sql.startswith("SELECT TOP 64 ") and "c.libraryId = @libraryId" in sql
        assert partition_key == [params["@libraryId"], params["@documentId"]]
        self.queries.append((sql, params, partition_key))
        for (library, document, _), row in self.chunks.items.items():
            if (library, document, row["generation"]) == (
                params["@libraryId"],
                params["@documentId"],
                params["@generation"],
            ):
                yield copy.deepcopy(row)


class InventoryDatabase:
    """Read-only SDK-shaped inventory used by the actual absence verifier."""

    def __init__(self, stores):
        self.stores = stores

    def get_container_client(self, name):
        stores = self.stores

        class Container:
            async def read_item(self, identity, *, partition_key):
                from azure.cosmos.exceptions import CosmosResourceNotFoundError

                if name == "libraries":
                    row = await stores.libraries.get(identity)
                elif name == "documents":
                    row = await stores.documents.get(partition_key, identity)
                else:
                    row = stores.chunks.items.get((*partition_key, identity))
                if row is None:
                    raise CosmosResourceNotFoundError(status_code=404, message="local missing item")
                return copy.deepcopy(row)

            async def query_items(self, sql, *, parameters=None, partition_key=None):
                params = {p["name"]: p["value"] for p in parameters or []}
                if name == "libraries":
                    rows = stores.libraries.m.items.values()
                elif name == "documents":
                    rows = stores.documents.m.items.values()
                else:
                    rows = stores.chunks.items.values()
                if name != "libraries":
                    assert "c.libraryId = @libraryId" in sql
                    assert partition_key in (params["@libraryId"], [params["@libraryId"]])
                selected = [row for row in rows if not params or row["libraryId"] == params["@libraryId"]]
                for row in selected[: int(sql.split()[2])]:
                    yield copy.deepcopy(row)

        return Container()


class BlobInventory:
    def __init__(self, blobs):
        self.blobs = blobs

    async def list_blobs(self, *, name_starts_with):
        for path, (data, _) in self.blobs.blobs.items():
            if path.startswith(name_starts_with):
                yield SimpleNamespace(name=path, size=len(data), etag=hashlib.sha256(data).hexdigest())


def normalize_cosmos_numbers(value):
    if isinstance(value, dict):
        return {key: normalize_cosmos_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_cosmos_numbers(item) for item in value]
    if type(value) is float and value.is_integer():
        return int(value)
    return value


def normalize_stored_records(stores):
    """Cosmos may emit JSON numeric zero as 0 when the submitted value was 0.0."""
    for store in (stores.libraries, stores.documents):
        original = store.m._stamp

        def stamp(record, original=original):
            return original(normalize_cosmos_numbers(record))

        store.m._stamp = stamp


def local_service(stores, ledger):
    libraries = GuardedLibraries(stores.libraries, ledger)
    documents = GuardedDocuments(stores.documents, ledger)
    chunks = GuardedChunks(stores.chunks, ledger)
    blobs = GuardedBlobs(stores.blobs, ledger)
    queue = LedgerQueue(ledger)
    searcher = Searcher(libraries, documents, stores.reader, stores.embedder, mode="vector", blobs=blobs)
    app = create_app(
        settings(AOAI_EMBEDDING_MODEL_REVISION="test-revision"),
        libraries=libraries,
        documents=documents,
        queue=queue,
        blobs=blobs,
        searcher=searcher,
    )
    worker = Worker(
        libraries,
        documents,
        chunks,
        blobs,
        queue,
        stores.embedder,
        Chunker(TiktokenTokenizer()),
        embedding_model_revision="test-revision",
        stale_read_pause=0,
    )
    return SimpleNamespace(
        app=app,
        libraries=libraries,
        documents=documents,
        chunks=chunks,
        blobs=blobs,
        queue=queue,
        worker=worker,
        database=ChunkDatabase(stores.chunks),
    )


@pytest.mark.parametrize("damage", ["fixture", "chunk-content", "chunk-coordinate"])
async def test_publication_gate_rejects_changed_source_or_passage_binding(tmp_path, damage):
    inputs, captures = tmp_path / "fixtures", tmp_path / "capture"
    inputs.mkdir()
    captures.mkdir()
    data = fixtures()["source.docx"]
    (inputs / "source.docx").write_bytes(data)
    stores = Services()
    with OwnedLedger.create(
        tmp_path / "ledger", binding={"local": True}, protected_library_ids=["protected"], live=False
    ) as ledger:
        service = local_service(stores, ledger)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service.app), base_url="http://local.invalid", headers=AUTH
        ) as client:
            qualification = live.Qualification(
                captures, inputs, ledger, service, service, client, None, time.monotonic() + 30
            )
            library = await qualification.create("shared")
            document = await qualification.upload(library, "source.docx")
            await qualification.drain()
            await qualification.publication(library, document, "source.docx", 1)
            if damage == "fixture":
                (inputs / "source.docx").write_bytes(data + b"changed")
            else:
                row = next(iter(stores.chunks.items.values()))
                if damage == "chunk-content":
                    row["text"] += " unrelated injected content"
                else:
                    row["location"]["paragraph"] += 1
            with pytest.raises(ValueError, match="source hash differs|content hash differs|source location differs"):
                await qualification.publication(library, document, "source.docx", 1)


@pytest.mark.parametrize("cosmos_number_normalization", [False, True])
async def test_complete_live_mechanics_locally_with_normal_http_client_and_owned_guards(
    tmp_path, monkeypatch, cosmos_number_normalization
):
    # Keep task scheduling and server startup real; skip only the Azure index delay.
    original_sleep = asyncio.sleep

    async def no_index_warmup(seconds, *args, **kwargs):
        return await original_sleep(0 if seconds == 30 else seconds, *args, **kwargs)

    monkeypatch.setattr(live.asyncio, "sleep", no_index_warmup)
    inputs = tmp_path / "fixtures"
    inputs.mkdir()
    fixture_bytes = fixtures()
    for name, data in fixture_bytes.items():
        (inputs / name).write_bytes(data)
    captures = tmp_path / "capture"
    captures.mkdir()
    stores = Services()
    if cosmos_number_normalization:
        normalize_stored_records(stores)
    await stores.libraries.create({"id": "protected", "libraryId": "protected", "status": "active"})
    independent = SimpleNamespace(database=InventoryDatabase(stores), blob_container=BlobInventory(stores.blobs))
    protected = await live.protected_snapshot(independent.database, independent.blob_container, ["protected"])
    with OwnedLedger.create(
        tmp_path / "ledger", binding={"local": True}, protected_library_ids=["protected"], live=False
    ) as ledger:
        service, second = local_service(stores, ledger), local_service(stores, ledger)
        async with live.loopback(service.app) as url:
            async with httpx.AsyncClient(base_url=url, headers={**AUTH, "X-Agent-Id": "owner"}, timeout=30) as client:
                with httpx.Client(base_url=url, timeout=30) as sync_client:
                    index = IndexClient(url, "k", http=sync_client)
                    qualification = live.Qualification(
                        captures, inputs, ledger, service, second, client, index, time.monotonic() + 90
                    )
                    bindings = await qualification.mechanics()
                    await url.quiesce()
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=service.app), base_url="http://local.invalid", headers=AUTH
                    ) as cleanup_client:
                        cleanup = await live.cleanup(captures, service, independent, ledger, cleanup_client, protected)
        assert set(bindings) == {"docx", "markdown"}
        for role, name in (("docx", "source.docx"), ("markdown", "source.md")):
            assert bindings[role]["sourceSha256"] == hashlib.sha256(fixture_bytes[name]).hexdigest()
            assert bindings[role]["generation"] == 1 and bindings[role]["contentRevision"] == 2
        assert "markdown_hierarchy_and_exact_character_spans" in qualification.checks
        assert "independent_client_metadata_race_and_source_identity" in qualification.checks
        assert "pending_replacement_inflight_publication_and_stale_selector" in qualification.checks
        assert "independent_client_deletion_fence_and_idempotent_purge" in qualification.checks
        assert len(service.database.queries) == 4
        if cosmos_number_normalization:
            published = json.loads(next(captures.glob("*-publication-1.json")).read_text())["document"]
            assert type(published["processingMetrics"]["visualExtractionMs"]) is int
            assert type(published["processingMetrics"]["visionMs"]) is int
        assert cleanup["passed"]
        for check in (cleanup["immediate"], cleanup["late"]):
            assert check["library404"] == 3 and check["document404"] == 3
            assert check["chunk404"] == ledger.summary()["reservedChunks"] > 0
            assert check["emptyQueries"] == 6 and check["emptyPrefixes"] == 3
            assert check["protectedUnchanged"]
        assert list(stores.libraries.m.items) == [("protected", "protected")]
        assert not stores.documents.m.items and not stores.chunks.items and not stores.blobs.blobs
        assert ledger.summary()["unresolvedOperations"] == ledger.summary()["unfinishedMessages"] == 0
        captures_json = json.loads((captures / "tool-responses.json").read_text())
        races = [row["response"] for row in captures_json if row["role"] == "independent-client-race"]
        assert len(races) == 3 and all(row["status"] == 409 for row in races)
        assert all(row["body"]["error"]["code"] == "document_changed" for row in races)
