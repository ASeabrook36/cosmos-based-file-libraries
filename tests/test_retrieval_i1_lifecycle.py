"""Local rehearsal of the cloud harness; no live claims or cloud calls."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from fli.blobs import MemoryBlobs
from fli.chunking import ApproxTokenizer, Chunker
from fli.queue import MemoryQueue
from fli.worker.handlers import Worker
from scripts import retrieval_i1_lifecycle as lab
from tests.fakes import Services, api
from tests.test_modular_ingestion import MicrosoftFake
from tests.test_worker_handlers import drain


async def test_barrier_fetches_real_bytes_then_pauses_only_exact_path():
    blobs = MemoryBlobs()
    await blobs.upload("owned/a", b"complete source", "text/plain")
    await blobs.upload("owned/b", b"other source", "text/plain")
    barrier = lab.DownloadBarrier(blobs)
    barrier.arm("owned/a")
    task = asyncio.create_task(barrier.download("owned/a"))
    await asyncio.wait_for(barrier.arrived.wait(), 1)
    assert not task.done()
    assert await barrier.download("owned/b") == b"other source"
    await blobs.delete("owned/a")  # Already-fetched old bytes must still reach the normal freshness fence.
    barrier.release.set()
    assert await task == b"complete source"
    assert barrier.triggered == 1


async def test_fault_is_once_before_send_and_cannot_target_foreign_library():
    queue = MemoryQueue()
    fault = lab.QueueFault(queue, ["owned"])
    with pytest.raises(AssertionError):
        fault.arm({"type": "purgeDocument", "libraryId": "foreign"})
    message = {"type": "purgeDocument", "libraryId": "owned", "documentId": "one"}
    fault.arm(message)
    sibling = {**message, "documentId": "two"}
    await fault.send(sibling, session_id="owned")
    with pytest.raises(RuntimeError, match="injected"):
        await fault.send(message, session_id="owned")
    assert queue.sent == [("owned", sibling)]
    await fault.send(message, session_id="owned")
    assert queue.sent[-1] == ("owned", message) and fault.fired == 1


async def test_chunk_probe_is_full_document_scoped_and_excludes_payloads():
    class Container:
        def query_items(self, sql, *, parameters, partition_key):
            assert "SELECT TOP 100" in sql
            assert "c.libraryId = @libraryId AND c.documentId = @documentId" in sql
            assert "c.generation = @generation" in sql
            assert "c.embedding" not in sql and "c.text" not in sql
            assert partition_key == ["owned", "doc"]
            assert {x["name"]: x["value"] for x in parameters} == {
                "@libraryId": "owned",
                "@documentId": "doc",
                "@generation": 3,
            }

            async def rows():
                yield {"id": "chunk"}

            return rows()

    app = SimpleNamespace(state=SimpleNamespace(searcher=SimpleNamespace(chunks=SimpleNamespace(_c=Container()))))
    assert await lab.chunk_rows(app, "owned", "doc", generation=3) == [{"id": "chunk"}]


def test_errors_cannot_pass_with_partial_evidence_body():
    r = httpx.Response(409, json={"error": {"code": "document_changed"}, "documents": [{"text": "leak"}]})
    with pytest.raises(AssertionError):
        lab.error_only(r, 409, "document_changed")


async def test_cleanup_attempts_later_owned_scopes_after_failure(tmp_path):
    class PartialCleanup(lab.Probe):
        async def cleanup_one(self, app, client, lib):
            visited.append(lib)
            if lib == "first":
                raise TimeoutError("deliberate cleanup fault")
            self.cleanup.append({"libraryId": lib, "passed": True})

    visited = []
    probe = PartialCleanup(tmp_path, {}, {})
    probe.owned = ["first", "second", "third"]
    with pytest.raises(AssertionError, match="cleanup incomplete"):
        await probe.cleanup_owned(None, None)
    assert visited == probe.owned
    assert [row["passed"] for row in probe.cleanup] == [False, True, True]


async def test_entire_lifecycle_driver_rehearsal_uses_api_and_worker(tmp_path, monkeypatch):
    services = Services()
    test_a, _ = api(services, AOAI_EMBEDDING_MODEL_REVISION="test-revision")
    test_b, _ = api(services, AOAI_EMBEDDING_MODEL_REVISION="test-revision")
    worker = Worker(
        services.libraries,
        services.documents,
        services.chunks,
        services.blobs,
        services.queue,
        services.embedder,
        Chunker(ApproxTokenizer()),
        microsoft=MicrosoftFake(),
        stale_read_pause=0,
        embedding_model_revision="test-revision",
    )

    class Rehearsal(lab.Probe):
        async def request(self, *args, **kwargs):
            response = await super().request(*args, **kwargs)
            await drain(services, worker)
            return response

    async def memory_rows(app, lib, doc, *, generation=None):
        return [
            dict(row)
            for (library, document, _), row in services.chunks.items.items()
            if library == lib and document == doc and (generation is None or row["generation"] == generation)
        ]

    monkeypatch.setattr(lab, "chunk_rows", memory_rows)

    async def memory_library_chunks(app, lib):
        return [{"id": row["id"]} for (library, _, _), row in services.chunks.items.items() if library == lib][:1]

    monkeypatch.setattr(lab, "library_chunks", memory_library_chunks)
    old, new = tmp_path / "first.docx", tmp_path / "second.docx"
    old.write_bytes(b"Original source for API lifecycle rehearsal.")
    new.write_bytes(b"Replacement source for API lifecycle rehearsal.")
    root = tmp_path / "private"
    root.mkdir()
    probe = Rehearsal(root, {}, {})
    headers = {"Authorization": "Bearer k", "X-Agent-Id": "i1-owner"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_a.app), base_url="http://local-a", headers=headers
    ) as a:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_b.app), base_url="http://local-b", headers=headers
        ) as b:
            await probe.checks_run((test_a.app, a), (test_b.app, b), old, new)
            await probe.cleanup_owned(test_a.app, a)
    assert {x["id"] for x in probe.checks} == set(lab.CHECKS)
    assert len(probe.cleanup) == len(probe.owned) == 3
    assert not services.chunks.items and not services.blobs.blobs
    assert all(row["passed"] for row in probe.checks)
    assert {row["kind"] for row in probe.diagnostics} == {
        "metadata_chunks",
        "document_purge_poll",
        "document_purge_chunks",
        "library_purge_poll",
    }
    # This is a rehearsal, never a live receipt or production authentication claim.
    assert not (root / "receipt-private.json").exists()
