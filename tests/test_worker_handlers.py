"""Worker handlers against in-memory services (spec §7.1, §7.2, §7.8)."""

from __future__ import annotations

import json
import logging

import pytest

from fli.chunking import ApproxTokenizer, Chunker
from fli.worker.handlers import BadMessage, NotYetDeleting, Worker
from tests.fakes import FakeEmbedder, api

OLE = bytes.fromhex("D0CF11E0A1B11AE1")
TEXT = "Gross margin in EMEA fell two points in Q4 2025. " * 200  # several chunks


@pytest.fixture
def ctx():
    client, s = api()
    lib = client.post("/v1/libraries", json={"kind": "shared", "name": "Finance"}).json()["id"]
    worker = Worker(s.libraries, s.documents, s.chunks, s.blobs, s.queue, FakeEmbedder(), Chunker(ApproxTokenizer()))
    worker.stale_read_pause = 0
    return client, s, lib, worker


def upload(client, lib, name="q4.txt", data=None, metadata=None):
    data = TEXT.encode() if data is None else data
    form = {"metadata": json.dumps(metadata)} if metadata else {}
    r = client.post(f"/v1/libraries/{lib}/documents", files={"file": (name, data, "text/plain")}, data=form)
    return r.json()["documentId"]


def doc(client, lib, doc_id):
    return client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()


async def drain(s, worker):
    """Process queued messages in order, as one session lane would."""
    while s.queue.sent:
        _, message = s.queue.sent.pop(0)
        await worker.handle(message)


async def test_ingest_writes_chunks_and_commits_generation_1(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib, metadata={"tags": [{"key": "region", "value": "EMEA"}]})
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert (d["status"], d["activeGeneration"], d["pendingGeneration"]) == ("ready", 1, None)
    assert d["chunkCount"] == len(s.chunks.items) > 1
    assert d["extraction"]["chunkingVersion"].startswith("chunking-v1.1")
    chunk = s.chunks.items[(lib, doc_id, f"{doc_id}:1:0")]
    assert chunk["generation"] == 1 and chunk["facets"] == ["region=EMEA"] and chunk["fileName"] == "q4.txt"
    assert len(chunk["embedding"]) == 3 and chunk["contentKind"] == "text"
    library = client.get(f"/v1/libraries/{lib}").json()
    assert library["stats"]["documentCount"] == 1 and library["stats"]["chunkCount"] == d["chunkCount"]
    assert library["tagVocabulary"] == {"region": {"EMEA": 1}}


async def test_email_metadata_fills_author_and_authored_at(ctx):
    client, s, lib, worker = ctx
    eml = b"From: Jane Doe <jane@example.org>\nSubject: Q4\nDate: Tue, 11 Mar 2025 09:30:00 +0000\n\nMargins fell."
    doc_id = upload(client, lib, name="m.eml", data=eml)
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert d["author"] == "Jane Doe" and d["authoredAt"].startswith("2025-03-11T09:30")


@pytest.mark.parametrize(
    "name,data,detail",
    [
        (
            "locked.docx",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"EncryptionInfo".decode().encode("utf-16-le"),
            "password_protected",
        ),
        ("old.doc", b"plain bytes", "unsupported_file_type"),
        ("blank.txt", b"   \n  ", "no_text"),
    ],
)
async def test_unusable_files_fail_once_with_a_reason_and_write_nothing(ctx, name, data, detail):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib, name=name, data=data)
    await drain(s, worker)  # completes without raising: no retry
    d = doc(client, lib, doc_id)
    assert (d["status"], d["statusDetail"], d["activeGeneration"], d["pendingGeneration"]) == (
        "failed",
        detail,
        None,
        None,
    )
    assert not s.chunks.items


async def test_a_retry_after_a_crash_overwrites_rather_than_duplicates(ctx, monkeypatch):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    _, message = s.queue.sent.pop(0)
    real, count = s.chunks.upsert, {"n": 0}

    async def crash_after_three(chunk):
        count["n"] += 1
        if count["n"] > 3:
            raise RuntimeError("connection reset")
        await real(chunk)

    monkeypatch.setattr(s.chunks, "upsert", crash_after_three)
    with pytest.raises(RuntimeError):
        await worker.handle(message)
    assert doc(client, lib, doc_id)["activeGeneration"] is None  # nothing half-written is searchable
    monkeypatch.setattr(s.chunks, "upsert", real)
    await worker.handle(message)  # the redelivery
    d = doc(client, lib, doc_id)
    assert d["status"] == "ready" and len(s.chunks.items) == d["chunkCount"]


async def test_delete_during_processing_abandons_the_generation_and_purges_everything(ctx, monkeypatch):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    _, message = s.queue.sent.pop(0)
    real = s.chunks.upsert

    async def delete_midway(chunk):
        await real(chunk)
        if chunk["chunkIndex"] == 0:
            assert client.delete(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 202

    monkeypatch.setattr(s.chunks, "upsert", delete_midway)
    await worker.handle(message)
    await drain(s, worker)
    assert not s.chunks.items and not s.blobs.blobs
    assert await s.documents.get(lib, doc_id) is None


async def test_final_delivery_marks_failed_and_cleans_up(ctx, monkeypatch):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    _, message = s.queue.sent.pop(0)

    async def broken(texts):
        raise RuntimeError("embeddings unavailable")

    monkeypatch.setattr(worker.embedder, "embed", broken)
    with pytest.raises(RuntimeError):
        await worker.handle(message)
    await worker.fail(message, "processing_error:RuntimeError")
    d = doc(client, lib, doc_id)
    assert (d["status"], d["statusDetail"], d["pendingGeneration"]) == ("failed", "processing_error:RuntimeError", None)
    assert s.queue.sent[-1][1]["type"] == "purgeDocument"


async def test_a_failed_replacement_keeps_the_previous_version(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    # Phase 3's replace endpoint will do this: generation 2 pending, generation 1 still active.
    record = await s.documents.get(lib, doc_id)
    record.update(pendingGeneration=2, status="queued")
    await s.documents.replace(record)
    await worker.fail({"type": "ingest", "libraryId": lib, "documentId": doc_id, "generation": 2}, "x")
    d = doc(client, lib, doc_id)
    assert (d["status"], d["activeGeneration"], d["pendingGeneration"]) == ("failed", 1, None)


async def test_purge_keeps_the_active_and_pending_generations_only(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    record = await s.documents.get(lib, doc_id)
    record.update(activeGeneration=2, pendingGeneration=3)
    await s.documents.replace(record)
    for gen in (1, 2, 3, 4):
        await s.chunks.upsert({"id": f"{doc_id}:{gen}:99", "libraryId": lib, "documentId": doc_id, "generation": gen})
    await worker.purge_document(lib, doc_id)
    assert sorted({g for _, g in await s.chunks.generations(lib, doc_id)}) == [2, 3]


async def test_stale_or_repeated_ingest_messages_do_nothing(ctx):
    client, s, lib, worker = ctx
    upload(client, lib)
    _, message = s.queue.sent[0]
    await drain(s, worker)
    writes = s.chunks.writes
    await worker.handle(message)  # duplicate delivery after commit
    await worker.handle({**message, "generation": 7})  # a generation that is not pending
    assert s.chunks.writes == writes and worker.embedder.calls == 1


async def test_purge_library_deletes_everything_and_refuses_an_active_library(ctx):
    client, s, lib, worker = ctx
    upload(client, lib)
    upload(client, lib, name="b.txt")
    await drain(s, worker)
    with pytest.raises(NotYetDeleting):  # still active: retried, never purged
        await worker.purge_library(lib)
    assert s.chunks.items and await s.libraries.get(lib) is not None
    client.delete(f"/v1/libraries/{lib}")
    await drain(s, worker)
    assert not s.chunks.items and not s.blobs.blobs and not s.documents.m.items
    assert await s.libraries.get(lib) is None


async def test_a_document_over_the_chunk_cap_fails_before_embedding(ctx):
    client, s, lib, worker = ctx
    worker.max_chunks_per_document = 2
    doc_id = upload(client, lib)  # TEXT makes several chunks
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert (d["status"], d["statusDetail"], d["activeGeneration"]) == ("failed", "too_many_chunks", None)
    assert not s.chunks.items and worker.embedder.calls == 0


async def test_a_deletion_purge_that_reads_a_stale_record_retries_instead_of_completing(ctx):
    """E28: the API's ``deleting`` write may not have reached the replica the worker reads."""
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    message = {"type": "purgeDocument", "libraryId": lib, "documentId": doc_id, "deleted": True}
    with pytest.raises(NotYetDeleting):  # the record still reads as ready
        await worker.handle(message)
    assert s.chunks.items and await s.documents.get(lib, doc_id) is not None
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")  # the write becomes visible
    await worker.handle(message)  # the redelivery
    assert not s.chunks.items and not s.blobs.blobs and await s.documents.get(lib, doc_id) is None


async def test_a_dead_lettered_purge_is_logged_and_leaves_the_record_hidden(ctx, caplog):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
    with caplog.at_level(logging.ERROR, logger="fli.worker"):
        await worker.fail({"type": "purgeDocument", "libraryId": lib, "documentId": doc_id}, "processing_error:X")
    assert any(r.getMessage() == "purge dead-lettered" for r in caplog.records)
    assert (await s.documents.get(lib, doc_id))["status"] == "deleting"


async def test_an_upload_that_raced_a_library_delete_is_never_indexed(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    library = await s.libraries.get(lib)
    library["status"] = "deleting"
    await s.libraries.replace(library)  # deleted after the upload was accepted
    _, message = s.queue.sent.pop(0)
    await worker.handle(message)
    assert not s.chunks.items and (await s.documents.get(lib, doc_id))["status"] == "deleting"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "ingest"},
        {"type": "ingest", "libraryId": "L", "documentId": "D", "generation": 0},
        {"type": "ingest", "libraryId": "L", "documentId": "D", "generation": "1"},
        {"type": "purgeDocument", "libraryId": "L"},
        {"type": "retag", "libraryId": "L", "documentId": "D"},
    ],
)
async def test_malformed_or_unknown_messages_are_bad_messages(ctx, message):
    *_, worker = ctx
    with pytest.raises(BadMessage):
        await worker.handle(message)
