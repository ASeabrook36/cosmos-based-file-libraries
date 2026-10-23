"""Worker handlers against in-memory services (spec §7.1, §7.2, §7.8)."""

from __future__ import annotations

import json
import logging

import pytest

from fli.chunking import ApproxTokenizer, Chunker
from fli.worker.handlers import BadMessage, GenerationNotVisible, NotYetDeleting, Worker
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


def replace(client, lib, doc_id, name="q4.txt", data=None):
    data = TEXT.replace("fell", "rose").encode() if data is None else data
    r = client.put(f"/v1/libraries/{lib}/documents/{doc_id}/content", files={"file": (name, data, "text/plain")})
    assert r.status_code == 202, r.text
    return r.json()["generation"]


def originals(s, lib, doc_id):
    return sorted(p for p in s.blobs.blobs if p.startswith(f"{lib}/{doc_id}/original/"))


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
    replace(client, lib, doc_id, name="v2.txt")
    s.queue.sent.clear()
    await worker.fail({"type": "ingest", "libraryId": lib, "documentId": doc_id, "generation": 2}, "x")
    d = doc(client, lib, doc_id)
    assert (d["status"], d["activeGeneration"], d["pendingGeneration"], d["pendingFile"]) == ("failed", 1, None, None)
    assert d["fileName"] == "q4.txt"
    await drain(s, worker)  # the purge queued by the failure
    assert originals(s, lib, doc_id) == [f"{lib}/{doc_id}/original/1/q4.txt"]
    assert {c["generation"] for c in s.chunks.items.values()} == {1}


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
    # 4 is newer than this read knows (a lagging replica can only under-report): left for a later purge.
    assert sorted({g for _, g in await s.chunks.generations(lib, doc_id)}) == [2, 3, 4]


async def test_a_repeated_ingest_message_writes_nothing(ctx):
    client, s, lib, worker = ctx
    upload(client, lib)
    _, message = s.queue.sent[0]
    await drain(s, worker)
    writes = s.chunks.writes
    await worker.handle(message)  # duplicate delivery after commit
    assert s.chunks.writes == writes and worker.embedder.calls == 1
    assert not s.queue.sent  # generation 1 has nothing older to purge


async def test_an_ingest_for_a_generation_the_record_does_not_show_yet_is_retried(ctx):
    """E28: the API's replacement write may not have reached the replica the worker reads."""
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    _, message = s.queue.sent[0]
    await drain(s, worker)
    with pytest.raises(GenerationNotVisible):  # the record still shows generation 1 as the newest
        await worker.handle({**message, "generation": 2})
    assert worker.embedder.calls == 1 and not s.queue.sent
    replace(client, lib, doc_id)  # the write becomes visible
    await drain(s, worker)
    assert doc(client, lib, doc_id)["activeGeneration"] == 2


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


# -- replacement (spec §7.8) ---------------------------------------------------------------------


async def test_a_replacement_commits_its_file_and_purges_the_old_generation(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib, metadata={"author": "Jane Doe"})
    await drain(s, worker)
    replace(client, lib, doc_id, name="q4-final.txt")
    pending = doc(client, lib, doc_id)["pendingFile"]
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert (d["status"], d["activeGeneration"], d["pendingGeneration"], d["pendingFile"]) == ("ready", 2, None, None)
    assert d["fileName"] == "q4-final.txt" and d["blobPath"] == pending["blobPath"]
    assert d["uploadedAt"] == pending["uploadedAt"] and d["author"] == "Jane Doe"  # user metadata survives
    assert {c["generation"] for c in s.chunks.items.values()} == {2}
    chunk = s.chunks.items[(lib, doc_id, f"{doc_id}:2:0")]
    assert "rose" in chunk["text"] and chunk["fileName"] == "q4-final.txt"
    assert chunk["uploadedAt"] == pending["uploadedAt"]
    assert originals(s, lib, doc_id) == [f"{lib}/{doc_id}/original/2/q4-final.txt"]


async def test_an_original_stored_before_phase_3_is_purged_as_generation_1(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    legacy = f"{lib}/{doc_id}/original/q4.txt"
    s.blobs.blobs[legacy] = s.blobs.blobs.pop(f"{lib}/{doc_id}/original/1/q4.txt")
    record = await s.documents.get(lib, doc_id)
    del record["generation"]  # nor did records have the counter
    record["blobPath"] = legacy
    await s.documents.replace(record)
    assert replace(client, lib, doc_id) == 2
    await drain(s, worker)
    assert originals(s, lib, doc_id) == [f"{lib}/{doc_id}/original/2/q4.txt"]


async def test_a_redelivery_after_commit_queues_the_purge_again(ctx, monkeypatch):
    """Review finding 1: the purge send failed after the commit, so the message was redelivered."""
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    replace(client, lib, doc_id)
    _, message = s.queue.sent.pop(0)
    real = s.queue.send

    async def purge_send_fails(msg, *, session_id):
        if msg["type"] == "purgeDocument":
            raise RuntimeError("bus down")
        await real(msg, session_id=session_id)

    monkeypatch.setattr(s.queue, "send", purge_send_fails)
    with pytest.raises(RuntimeError):
        await worker.handle(message)
    assert doc(client, lib, doc_id)["activeGeneration"] == 2 and not s.queue.sent
    monkeypatch.setattr(s.queue, "send", real)
    calls = worker.embedder.calls
    await worker.handle(message)  # the redelivery: nothing is re-embedded, the purge is queued again
    assert worker.embedder.calls == calls
    await drain(s, worker)
    assert {c["generation"] for c in s.chunks.items.values()} == {2}
    assert originals(s, lib, doc_id) == [f"{lib}/{doc_id}/original/2/q4.txt"]


async def test_a_replacement_superseded_mid_write_is_abandoned(ctx, monkeypatch):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    replace(client, lib, doc_id, name="v2.txt")
    _, message = s.queue.sent.pop(0)
    real = s.chunks.upsert

    async def replace_midway(chunk):
        await real(chunk)
        if chunk["generation"] == 2 and chunk["chunkIndex"] == 0:
            replace(client, lib, doc_id, name="v3.txt", data=b"Headcount grew in APAC. " * 50)

    monkeypatch.setattr(s.chunks, "upsert", replace_midway)
    await worker.handle(message)  # generation 2 is written, then abandoned at commit
    assert doc(client, lib, doc_id)["activeGeneration"] == 1
    monkeypatch.setattr(s.chunks, "upsert", real)
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert (d["activeGeneration"], d["fileName"]) == (3, "v3.txt")
    assert {c["generation"] for c in s.chunks.items.values()} == {3}
    assert originals(s, lib, doc_id) == [f"{lib}/{doc_id}/original/3/v3.txt"]


async def test_a_purge_on_a_lagging_read_keeps_a_newer_original(ctx):
    """The API stores a replacement's original before any worker read can show its generation."""
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    s.blobs.blobs[f"{lib}/{doc_id}/original/2/new.txt"] = (b"new", "text/plain")  # reserved and stored by the API
    await worker.purge_document(lib, doc_id)  # this read still shows generation 1 as the newest
    assert originals(s, lib, doc_id) == [f"{lib}/{doc_id}/original/1/q4.txt", f"{lib}/{doc_id}/original/2/new.txt"]


async def test_a_replaced_document_takes_its_authored_date_from_the_new_file(ctx):
    client, s, lib, worker = ctx
    eml = b"From: Jane Doe <jane@example.org>\nSubject: Q4\nDate: Tue, 11 Mar 2025 09:30:00 +0000\n\nMargins fell."
    doc_id = upload(client, lib, name="m.eml", data=eml)
    await drain(s, worker)
    replace(client, lib, doc_id, name="m.eml", data=eml.replace(b"11 Mar 2025", b"02 Apr 2025"))
    await drain(s, worker)
    assert doc(client, lib, doc_id)["authoredAt"].startswith("2025-04-02")
