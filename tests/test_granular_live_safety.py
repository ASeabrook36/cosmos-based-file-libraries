"""Offline adversarial checks for the new exact-owned live qualification guards."""

import copy
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest

from fli.blobs import MemoryBlobs
from fli.checkpoints import digest
from fli.chunks import MemoryChunks
from fli.ids import chunk_id
from fli.store import Conflict, MemoryDocumentStore, MemoryLibraryStore, public
from scripts.granular_live_safety import (
    CHUNK_HASH_FIELDS,
    LIMITS,
    GuardedBlobs,
    GuardedChunks,
    GuardedDocuments,
    GuardedLibraries,
    LedgerQueue,
    OwnedLedger,
    OwnershipError,
    guard_owned_inventory,
    semantic_json,
    semantic_record_digest,
)

BINDING = {"candidate": "a" * 40, "fixtureManifest": "b" * 64}
PROTECTED = ["existing-protected-library"]


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def scope(tmp_path):
    ledger = OwnedLedger.create(tmp_path / "private-run", binding=BINDING, protected_library_ids=PROTECTED, live=False)
    raw = SimpleNamespace(
        libraries=MemoryLibraryStore(), documents=MemoryDocumentStore(), blobs=MemoryBlobs(), chunks=MemoryChunks()
    )
    scope = SimpleNamespace(
        ledger=ledger,
        raw=raw,
        libraries=GuardedLibraries(raw.libraries, ledger),
        documents=GuardedDocuments(raw.documents, ledger),
        blobs=GuardedBlobs(raw.blobs, ledger),
        chunks=GuardedChunks(raw.chunks, ledger),
        queue=LedgerQueue(ledger),
    )
    yield scope
    ledger.close()


def library_row(library=None):
    library = library or uid()
    return {"id": library, "libraryId": library, "kind": "user", "status": "active", "createdBy": "test"}


async def owned_document(scope):
    library = (await scope.libraries.create(library_row()))["id"]
    document = uid()
    path = f"{library}/{document}/original/1/source.md"
    await scope.blobs.upload(path, b"# Heading\nOne fact.\n", "text/markdown")
    row = await scope.documents.create(
        {"id": document, "libraryId": library, "generation": 1, "status": "ready", "source": {"blobPath": path}}
    )
    return library, document, path, row


def chunk_row(library, document, index=0):
    row = {
        "id": chunk_id(document, 1, index),
        "libraryId": library,
        "documentId": document,
        "generation": 1,
        "chunkIndex": index,
        "text": f"One fact {index}.",
        "heading": "Heading",
        "location": {"paragraph": index},
        "imageRef": None,
        "imageMeta": None,
        "embeddingModel": "test-embedding",
        "chunkingVersion": "test-granular-v1",
        "embedding": [1.0, 0.0],
    }
    row["contentHash"] = digest({key: row[key] for key in CHUNK_HASH_FIELDS})
    return row


def test_ledger_private_write_ahead_chain_reopens_and_locks(scope):
    ledger = scope.ledger
    operation = ledger.begin("example-create", "example:key", bodySha256="f" * 64)
    assert ledger.snapshot()["operations"][-1]["status"] == "started"
    assert ledger.root.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (ledger.root / "events").iterdir())
    with pytest.raises(BlockingIOError):
        OwnedLedger.open(ledger.root, binding=BINDING, protected_library_ids=PROTECTED, live=False)
    ledger.finish(operation)
    original = ledger.snapshot()
    ledger.close()
    with OwnedLedger.open(ledger.root, binding=BINDING, protected_library_ids=PROTECTED, live=False) as reopened:
        assert reopened.snapshot() == original
    with pytest.raises(OwnershipError, match="binding differs"):
        OwnedLedger.open(ledger.root, binding={"candidate": "changed"}, protected_library_ids=PROTECTED, live=False)


def test_ledger_refuses_new_directory_collision_and_git_path(scope, tmp_path):
    with pytest.raises(OwnershipError, match="new private"):
        OwnedLedger.create(scope.ledger.root, binding=BINDING, protected_library_ids=PROTECTED, live=False)
    git = tmp_path / "checkout"
    git.mkdir()
    (git / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(ValueError, match="outside every Git"):
        OwnedLedger.create(git / "ledger", binding=BINDING, protected_library_ids=PROTECTED, live=False)


def test_ledger_detects_disk_tampering_and_retains_unresolved_intent(scope):
    ledger = scope.ledger
    ledger.begin("create", "ambiguous")
    ledger.close()
    with OwnedLedger.open(ledger.root, binding=BINDING, protected_library_ids=PROTECTED, live=False) as reopened:
        with pytest.raises(OwnershipError, match="independent reconciliation"):
            reopened.assert_settled()
    event = sorted((ledger.root / "events").iterdir())[-1]
    body = json.loads(event.read_text())
    body["stateAfter"]["operations"][0]["status"] = "confirmed"
    event.write_text(json.dumps(body))
    with pytest.raises(OwnershipError, match="hash differs"):
        OwnedLedger.open(ledger.root, binding=BINDING, protected_library_ids=PROTECTED, live=False)


async def test_library_intent_is_flushed_before_store_call_and_lost_response_blocks(scope, monkeypatch):
    original = scope.raw.libraries.create
    record = library_row()

    async def lost_response(row):
        latest = json.loads(sorted((scope.ledger.root / "events").iterdir())[-1].read_text())
        assert latest["kind"] == "record-payload"
        assert latest["stateAfter"]["operations"][-1]["status"] == "started"
        reference = latest["stateAfter"]["operations"][-1]["recordPayloads"]["proposed"]
        payload = scope.ledger.root / reference["path"]
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == reference["sha256"]
        assert json.loads(payload.read_bytes()) == row
        assert row["id"] in latest["stateAfter"]["libraries"]
        await original(row)
        raise TimeoutError("lost response after actual creation")

    monkeypatch.setattr(scope.raw.libraries, "create", lost_response)
    with pytest.raises(TimeoutError):
        await scope.libraries.create(record)
    assert await scope.raw.libraries.get(record["id"]) is not None
    with pytest.raises(OwnershipError, match="single-attempt"):
        await scope.libraries.create(record)
    with pytest.raises(OwnershipError, match="not confirmed"):
        await scope.libraries.delete(record["id"])
    assert scope.ledger.summary()["unresolvedOperations"] == 1


async def test_library_existing_protected_and_ceiling_do_not_adopt(scope):
    existing = library_row()
    await scope.raw.libraries.create(existing)
    with pytest.raises(OwnershipError, match="never adopt"):
        await scope.libraries.create(existing)
    protected = uid()
    scope.ledger.protected.append(protected)
    with pytest.raises(OwnershipError, match="scope collision"):
        await scope.libraries.create(library_row(protected))
    for _ in range(LIMITS["libraries"]):
        await scope.libraries.create(library_row())
    with pytest.raises(OwnershipError, match="library ceiling"):
        await scope.libraries.create(library_row())
    assert len(scope.raw.libraries.m.items) == 4  # Three owned plus existing untouched.


async def test_blob_upload_reserves_document_generation_before_record_creation(scope):
    library, document, path, _ = await owned_document(scope)
    state = scope.ledger.snapshot()
    assert state["documents"][document]["libraryId"] == library
    assert state["generations"] == [[document, 1]]
    operations = state["operations"]
    assert [row["kind"] for row in operations] == ["library-create", "blob-create", "document-create"]
    assert operations[1]["key"] == "blob:" + path
    await scope.blobs.upload(path, b"# Heading\nOne fact.\n", "text/markdown")
    assert scope.ledger.summary()["recordedBlobs"] == 1


async def test_lost_document_create_blocks_original_exception_cleanup(scope, monkeypatch):
    library = (await scope.libraries.create(library_row()))["id"]
    document = uid()
    path = f"{library}/{document}/original/1/source.md"
    await scope.blobs.upload(path, b"source", "text/plain")
    original = scope.raw.documents.create

    async def lost_response(row):
        assert scope.ledger.snapshot()["operations"][-1]["kind"] == "document-create"
        await original(row)
        raise TimeoutError("lost response")

    monkeypatch.setattr(scope.raw.documents, "create", lost_response)
    with pytest.raises(TimeoutError):
        await scope.documents.create({"id": document, "libraryId": library, "generation": 1})
    with pytest.raises(OwnershipError, match="ambiguous"):
        await scope.blobs.delete_prefix(f"{library}/{document}/")
    assert path in scope.raw.blobs.blobs
    assert await scope.raw.documents.get(library, document) is not None


async def test_lost_blob_create_is_ledger_owned_but_not_automatically_retried_or_deleted(scope, monkeypatch):
    library = (await scope.libraries.create(library_row()))["id"]
    path = f"{library}/{uid()}/original/1/source.md"
    original = scope.raw.blobs.create

    async def lost_response(*args):
        assert scope.ledger.snapshot()["operations"][-1]["kind"] == "blob-create"
        await original(*args)
        raise TimeoutError("lost blob create")

    monkeypatch.setattr(scope.raw.blobs, "create", lost_response)
    with pytest.raises(TimeoutError):
        await scope.blobs.create(path, b"source", "text/plain")
    for attempt in (scope.blobs.create(path, b"source", "text/plain"), scope.blobs.delete(path)):
        with pytest.raises(OwnershipError, match="ambiguous"):
            await attempt
    assert path in scope.raw.blobs.blobs


async def test_document_conflict_is_known_rejection_and_can_be_retried(scope):
    library, document, _, row = await owned_document(scope)
    stale = copy.deepcopy(row)
    row["author"] = "first"
    await scope.documents.replace(row)
    stale["author"] = "stale"
    with pytest.raises(Conflict):
        await scope.documents.replace(stale)
    scope.ledger.assert_settled()
    latest = await scope.documents.get(library, document)
    latest["author"] = "second"
    await scope.documents.replace(latest)
    assert (await scope.documents.get(library, document))["author"] == "second"


async def test_known_stale_record_read_preserves_candidate_behavior_but_cannot_authorize_cleanup(scope, monkeypatch):
    library, document, _, row = await owned_document(scope)
    old = copy.deepcopy(row)
    row.update(status="deleting", generation=2)
    await scope.documents.replace(row)

    async def stale_get(*args, **kwargs):
        return copy.deepcopy(old)

    monkeypatch.setattr(scope.raw.documents, "get", stale_get)
    assert await scope.documents.get(library, document) == old
    with pytest.raises(OwnershipError, match="latest document body"):
        await scope.documents.delete(library, document)
    with pytest.raises(OwnershipError, match="latest document body"):
        await guard_owned_inventory(scope.libraries, scope.documents, scope.chunks, scope.blobs, scope.ledger)
    assert (library, document) in scope.raw.documents.m.items


async def test_source_generation_ceiling_counts_distinct_generations(scope):
    library, document, _, _ = await owned_document(scope)
    for generation in range(2, LIMITS["generations"] + 1):
        scope.ledger.reserve_document(library, document, generation)
    with pytest.raises(OwnershipError, match="generation ceiling"):
        scope.ledger.reserve_document(library, document, 6)
    assert scope.ledger.summary()["reservedGenerations"] == 5


async def test_document_ceiling_counts_distinct_documents(scope):
    library, _, _, _ = await owned_document(scope)
    for _ in range(3):
        scope.ledger.reserve_document(library, uid(), 1)
    with pytest.raises(OwnershipError, match="document ceiling"):
        scope.ledger.reserve_document(library, uid(), 1)


@pytest.mark.parametrize("case", ["unknown_generation", "mutable_original", "too_large", "unknown_existing"])
async def test_blob_scope_and_immutability_refuse_before_write(scope, case):
    library, document, path, _ = await owned_document(scope)
    before = copy.deepcopy(scope.raw.blobs.blobs)
    if case == "unknown_generation":
        path, data = f"{library}/{document}/text/9/full.txt", b"unknown"
    elif case == "mutable_original":
        data = b"changed source"
    elif case == "too_large":
        path, data = f"{library}/{document}/original/2/source.md", b"x" * (LIMITS["sourceBytes"] + 1)
    else:
        path, data = f"{library}/{document}/text/1/foreign.txt", b"foreign"
        scope.raw.blobs.blobs[path] = (data, "text/plain")
        before = copy.deepcopy(scope.raw.blobs.blobs)
    with pytest.raises(OwnershipError):
        await scope.blobs.upload(path, data, "text/plain")
    assert scope.raw.blobs.blobs == before


async def test_unavailable_blob_read_is_not_mistaken_for_absence(scope, monkeypatch):
    library, document, _, _ = await owned_document(scope)

    async def unavailable(_):
        raise TimeoutError("read service unavailable")

    monkeypatch.setattr(scope.raw.blobs, "download", unavailable)
    before = scope.ledger.summary()["recordedBlobs"]
    with pytest.raises(TimeoutError):
        await scope.blobs.create(f"{library}/{document}/text/1/new.json", b"{}", "application/json")
    assert scope.ledger.summary()["recordedBlobs"] == before


@pytest.mark.parametrize("case", ["unknown_blob", "changed_blob"])
async def test_blob_prefix_validates_entire_inventory_before_any_delete(scope, case):
    library, document, _, _ = await owned_document(scope)
    later = f"{library}/{document}/text/1/full.txt"
    await scope.blobs.create(later, b"valid", "text/plain")
    if case == "unknown_blob":
        scope.raw.blobs.blobs[later + ".unknown"] = (b"foreign", "text/plain")
    else:
        scope.raw.blobs.blobs[later] = (b"tampered", "text/plain")
    before = copy.deepcopy(scope.raw.blobs.blobs)
    with pytest.raises(OwnershipError):
        await scope.blobs.delete_prefix(library + "/")
    assert scope.raw.blobs.blobs == before


async def test_live_blob_delete_requires_snapshot_and_conditional_callback(scope):
    _, _, path, _ = await owned_document(scope)
    calls = []

    async def snapshot(path):
        data = await scope.raw.blobs.download(path)
        return {"etag": "snapshot-etag", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    async def delete(path, observed):
        assert scope.ledger.snapshot()["operations"][-1]["status"] == "started"
        assert observed["etag"] == "snapshot-etag"
        calls.append(path)
        await scope.raw.blobs.delete(path)

    scope.ledger.live = True
    with pytest.raises(OwnershipError, match="ETag"):
        GuardedBlobs(scope.raw.blobs, scope.ledger)
    blobs = GuardedBlobs(scope.raw.blobs, scope.ledger, snapshot_blob=snapshot, delete_if_unchanged=delete)
    await blobs.delete(path)
    assert calls == [path]

    async def absent(_):
        return None

    blobs.snapshot_blob = absent
    assert await blobs.deletion_snapshot(path) is None


async def test_blob_conditional_delete_failure_retains_unknown_state_and_does_not_delete_next(scope):
    library, document, path, _ = await owned_document(scope)
    other = f"{library}/{document}/text/1/full.txt"
    await scope.blobs.create(other, b"text", "text/plain")

    async def raced(path, snapshot):
        raise Conflict("ETag differs")

    scope.blobs.delete_if_unchanged = raced
    with pytest.raises(Conflict):
        await scope.blobs.delete_prefix(library + "/")
    assert path in scope.raw.blobs.blobs and other in scope.raw.blobs.blobs
    assert scope.ledger.summary()["unresolvedOperations"] == 1


async def test_blob_reappearance_after_confirmed_delete_is_not_readopted(scope):
    _, _, path, _ = await owned_document(scope)
    old = scope.raw.blobs.blobs[path]
    await scope.blobs.delete(path)
    scope.raw.blobs.blobs[path] = old
    with pytest.raises(OwnershipError, match="reappeared"):
        await scope.blobs.delete(path)
    assert scope.raw.blobs.blobs[path] == old


async def test_chunks_capped_hash_checked_and_metadata_only(scope):
    library, document, _, _ = await owned_document(scope)
    for index in range(LIMITS["chunks"]):
        await scope.chunks.upsert(chunk_row(library, document, index))
    with pytest.raises(OwnershipError, match="chunk ceiling"):
        await scope.chunks.upsert(chunk_row(library, document, 64))
    row = chunk_row(library, document)
    await scope.chunks.patch(library, document, row["id"], {"facets": ["reviewed"], "author": "test"})
    await scope.chunks.upsert(row)  # Same immutable chunk does not erase newer metadata.
    assert scope.raw.chunks.items[(library, document, row["id"])]["facets"] == ["reviewed"]
    with pytest.raises(OwnershipError, match="metadata scope"):
        await scope.chunks.patch(library, document, row["id"], {"text": "tampered"})
    row["text"] = "modified but old label"
    with pytest.raises(OwnershipError, match="canonical content"):
        await scope.chunks.upsert(row)
    assert len(scope.raw.chunks.items) == 64


@pytest.mark.parametrize("field", ["text", "location"])
async def test_chunk_existing_hash_label_does_not_authorize_changed_content(scope, field):
    library, document, _, _ = await owned_document(scope)
    row = chunk_row(library, document)
    await scope.chunks.upsert(row)
    scope.raw.chunks.items[(library, document, row["id"])][field] = "tampered"
    for attempt in (scope.chunks.delete(library, document, row["id"]), scope.chunks.upsert(row)):
        with pytest.raises(OwnershipError, match="snapshot differs"):
            await attempt
    assert len(scope.raw.chunks.items) == 1


async def test_chunk_delete_uses_current_validated_snapshot_etag_callback(scope):
    library, document, _, _ = await owned_document(scope)
    row = chunk_row(library, document)
    await scope.chunks.upsert(row)
    scope.raw.chunks.items[(library, document, row["id"])]["_etag"] = "exact-etag"
    calls = []

    async def conditional(record):
        assert record["_etag"] == "exact-etag"
        assert scope.ledger.snapshot()["operations"][-1]["status"] == "started"
        calls.append(record["id"])
        await scope.raw.chunks.delete(library, document, record["id"])

    scope.chunks.delete_if_unchanged = conditional
    scope.ledger.live = True
    await scope.chunks.delete(library, document, row["id"])
    assert calls == [row["id"]]
    assert not scope.raw.chunks.items


@pytest.mark.parametrize(
    "case", ["unknown_document", "unknown_chunk", "orphan_chunk", "changed_source", "changed_library"]
)
async def test_complete_inventory_rejects_unexpected_records_before_any_cleanup(scope, case):
    library, document, _, _ = await owned_document(scope)
    row = chunk_row(library, document)
    await scope.chunks.upsert(row)
    if case == "unknown_document":
        await scope.raw.documents.create({"id": uid(), "libraryId": library, "generation": 1})
    elif case in {"unknown_chunk", "orphan_chunk"}:
        await scope.raw.chunks.upsert(chunk_row(library, uid() if case == "orphan_chunk" else document, 99))
    elif case == "changed_source":
        scope.raw.documents.m.items[(library, document)].update(generation=92, source={"blobPath": "foreign"})
    else:
        scope.raw.libraries.m.items[(library, library)]["description"] = "outside writer"
    before = copy.deepcopy((scope.raw.documents.m.items, scope.raw.chunks.items, scope.raw.blobs.blobs))
    with pytest.raises(OwnershipError):
        await guard_owned_inventory(scope.libraries, scope.documents, scope.chunks, scope.blobs, scope.ledger)
    assert (scope.raw.documents.m.items, scope.raw.chunks.items, scope.raw.blobs.blobs) == before


async def test_exact_record_delete_requires_fence_and_conditional_etag(scope):
    library, document, _, row = await owned_document(scope)
    with pytest.raises(OwnershipError, match="not fenced"):
        await scope.documents.delete(library, document)
    row["status"] = "deleting"
    saved = await scope.documents.replace(row)
    calls = []

    async def conditional_delete(record):
        assert record == saved
        assert record["_etag"]
        calls.append(record["id"])
        await scope.raw.documents.delete(record["libraryId"], record["id"])

    scope.documents.delete_if_unchanged = conditional_delete
    scope.ledger.live = True
    await scope.documents.delete(library, document)
    assert calls == [document]
    with pytest.raises(OwnershipError, match="resurrected"):
        await scope.documents.replace(saved)


async def test_queue_persists_before_dispatch_and_never_automatically_retries_failed(scope):
    library, document, _, _ = await owned_document(scope)
    message = {"type": "ingest", "libraryId": library, "documentId": document, "generation": 1}
    await scope.queue.send(message, session_id=library)
    assert len(scope.queue.pending) == 1
    seen = []

    async def fail(msg):
        event = json.loads(sorted((scope.ledger.root / "events").iterdir())[-1].read_text())
        assert event["kind"] == "queue-running"
        seen.append(msg)
        raise RuntimeError("worker failed")

    worker = SimpleNamespace(handle=fail)
    with pytest.raises(RuntimeError, match="worker failed"):
        await scope.queue.drain(worker)
    with pytest.raises(OwnershipError, match="explicit review"):
        await scope.queue.drain(worker)
    assert seen == [message]
    scope.queue.cancel_pending("reviewed local failure; no ambiguous store mutation")
    assert scope.ledger.summary()["unfinishedMessages"] == 0
    with pytest.raises(OwnershipError):
        await scope.queue.send({**message, "libraryId": "foreign"}, session_id=library)


async def test_queue_reopen_and_mutation_uncertainty_survive_process_exit(scope):
    library, document, _, _ = await owned_document(scope)
    await scope.queue.send({"type": "purgeDocument", "libraryId": library, "documentId": document}, session_id=library)
    scope.ledger.begin("document-replace", "document:" + document)
    scope.ledger.close()
    with OwnedLedger.open(scope.ledger.root, binding=BINDING, protected_library_ids=PROTECTED, live=False) as ledger:
        assert len(LedgerQueue(ledger).pending) == 1
        with pytest.raises(OwnershipError, match="independent reconciliation"):
            LedgerQueue(ledger).cancel_pending("cannot clean up unknown write")


async def test_queue_rejects_unknown_generation_missing_document_and_message_overrun(scope):
    library, document, _, _ = await owned_document(scope)
    with pytest.raises(OwnershipError, match="document ID"):
        await scope.queue.send({"type": "ingest", "libraryId": library}, session_id=library)
    with pytest.raises(OwnershipError, match="generation"):
        await scope.queue.send(
            {"type": "ingest", "libraryId": library, "documentId": document, "generation": 99}, session_id=library
        )
    message = {"type": "reconcileLibrary", "libraryId": library}
    for _ in range(200):
        await scope.queue.send(message, session_id=library)
    with pytest.raises(OwnershipError, match="message ceiling"):
        await scope.queue.send(message, session_id=library)
    assert len(scope.queue.pending) == 200


async def test_live_inventory_requires_complete_library_chunk_inventory(scope):
    await owned_document(scope)
    scope.ledger.live = True
    with pytest.raises(OwnershipError, match="library chunk inventory"):
        await guard_owned_inventory(scope.libraries, scope.documents, scope.chunks, scope.blobs, scope.ledger)


async def test_safe_summary_contains_no_owned_ids_or_source_bytes(scope):
    library, document, path, _ = await owned_document(scope)
    summary = json.dumps(scope.ledger.summary())
    assert all(value not in summary for value in (library, document, path, "Heading", str(scope.ledger.root)))


@pytest.mark.parametrize("value", [0.0, -0.0, 1.0, -1.0, 1000000000.0])
def test_json_number_canonicalization_equates_only_identical_numeric_values(value):
    assert semantic_record_digest({"nested": [value]}) == semantic_record_digest({"nested": [int(value)]})
    assert semantic_record_digest({"nested": [value]}) != semantic_record_digest({"nested": [int(value) + 1]})


@pytest.mark.parametrize("left,right", [(False, 0), (True, 1), ("0", 0), (None, 0), (0.5, 0), ([1, 2], [2, 1])])
def test_json_normalization_preserves_types_values_and_array_order(left, right):
    assert semantic_record_digest({"value": left}) != semantic_record_digest({"value": right})
    assert semantic_record_digest({}) != semantic_record_digest({"value": None})


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_numbers_are_refused(number):
    with pytest.raises(OwnershipError, match="non-finite"):
        semantic_record_digest({"metrics": {"value": number}})


async def test_cosmos_integral_number_roundtrip_preserves_raw_evidence_and_allows_records(scope, monkeypatch):
    original_create = scope.raw.libraries.create

    async def create_normalized(row):
        return await original_create(semantic_json(row))

    monkeypatch.setattr(scope.raw.libraries, "create", create_normalized)
    proposed_library = {**library_row(), "ingestionProfile": {"version": 1.0}}
    library = (await scope.libraries.create(proposed_library))["id"]
    assert (await scope.libraries.get(library))["ingestionProfile"]["version"] == 1
    document = uid()
    proposed = {"id": document, "libraryId": library, "generation": 1, "metrics": {"elapsed": 0.0}}
    original_doc_create = scope.raw.documents.create

    async def create_doc_normalized(row):
        return await original_doc_create(semantic_json(row))

    monkeypatch.setattr(scope.raw.documents, "create", create_doc_normalized)
    row = await scope.documents.create(proposed)
    row["metrics"] = {"elapsed": 0.0, "values": [1.0, -0.0, 0.25]}
    original_replace = scope.raw.documents.replace

    async def replace_normalized(record):
        return await original_replace(semantic_json(record))

    monkeypatch.setattr(scope.raw.documents, "replace", replace_normalized)
    await scope.documents.replace(row)
    observed = await scope.documents.get(library, document)
    assert observed["metrics"] == {"elapsed": 0, "values": [1, 0, 0.25]}
    operation = scope.ledger.snapshot()["operations"][-1]
    assert operation["status"] == "confirmed"
    assert operation["bodySha256"] != operation["semanticBodySha256"]
    for role in ("proposed", "response"):
        reference = operation["recordPayloads"][role]
        payload = scope.ledger.root / reference["path"]
        assert payload.stat().st_mode & 0o777 == 0o600
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == reference["sha256"]
    sent = json.loads((scope.ledger.root / operation["recordPayloads"]["proposed"]["path"]).read_bytes())
    received = json.loads((scope.ledger.root / operation["recordPayloads"]["response"]["path"]).read_bytes())
    assert type(sent["metrics"]["elapsed"]) is float
    assert type(received["metrics"]["elapsed"]) is int
    await guard_owned_inventory(scope.libraries, scope.documents, scope.chunks, scope.blobs, scope.ledger)


async def test_actual_changed_response_is_preserved_but_not_confirmed(scope, monkeypatch):
    library, document, _, row = await owned_document(scope)
    original = scope.raw.documents.replace
    row["processingMetrics"] = {"visualExtractionMs": 0.0}

    async def changed(record):
        altered = copy.deepcopy(record)
        altered["processingMetrics"]["visualExtractionMs"] = 1
        return await original(altered)

    monkeypatch.setattr(scope.raw.documents, "replace", changed)
    with pytest.raises(OwnershipError, match="returned record body"):
        await scope.documents.replace(row)
    operation = scope.ledger.snapshot()["operations"][-1]
    assert operation["status"] == "unknown"
    assert set(operation["recordPayloads"]) == {"proposed", "response"}
    response = json.loads((scope.ledger.root / operation["recordPayloads"]["response"]["path"]).read_bytes())
    assert response["processingMetrics"]["visualExtractionMs"] == 1
    with pytest.raises(OwnershipError, match="ambiguous"):
        await scope.documents.get(library, document)


async def legacy_ambiguous_publication(scope):
    library, document, _, proposed = await owned_document(scope)
    proposed.update(processingMetrics={"visualExtractionMs": 0.0, "visionMs": 0.0}, activeGeneration=1)
    operation = scope.ledger.begin("document-replace", "document:" + document, bodySha256=digest(public(proposed)))
    observed = await scope.raw.documents.replace(semantic_json(proposed))
    scope.ledger.finish(operation, status="unknown")
    proof = scope.ledger.root.parent / "independent-proof.json"
    proof.write_text(json.dumps({"observed": observed, "proposedSha256": digest(public(proposed))}))
    proof.chmod(0o600)
    return library, document, operation, proposed, observed, proof, hashlib.sha256(proof.read_bytes()).hexdigest()


async def test_exact_legacy_reconciliation_appends_without_rewriting_or_reserving_new_scopes(scope):
    library, document, operation, proposed, observed, proof, proof_hash = await legacy_ambiguous_publication(scope)
    before = {path.name: path.read_bytes() for path in (scope.ledger.root / "events").iterdir()}
    counts = scope.ledger.summary()
    scope.ledger.confirm_reconciled(operation, proposed, observed, evidence_path=proof, evidence_sha256=proof_hash)
    assert all((scope.ledger.root / "events" / name).read_bytes() == content for name, content in before.items())
    result = scope.ledger.snapshot()["operations"][-1]
    assert result["status"] == "confirmed"
    assert result["bodySha256"] == digest(public(proposed))
    assert result["semanticBodySha256"] == semantic_record_digest(observed)
    assert result["reconciliation"]["originalBodySha256"] == result["bodySha256"]
    assert scope.ledger.summary()["unresolvedOperations"] == 0
    for count in ("reservedLibraries", "reservedDocuments", "reservedGenerations", "reservedChunks"):
        assert scope.ledger.summary()[count] == counts[count]
    assert await scope.documents.get(library, document) == observed
    await guard_owned_inventory(scope.libraries, scope.documents, scope.chunks, scope.blobs, scope.ledger)
    with pytest.raises(OwnershipError, match="only an ambiguous"):
        scope.ledger.confirm_reconciled(operation, proposed, observed, evidence_path=proof, evidence_sha256=proof_hash)


@pytest.mark.parametrize(
    "case", ["wrong_proposal", "changed_observation", "wrong_proof", "wrong_operation", "missing_etag"]
)
async def test_reconciliation_refuses_any_unproved_change_without_appending(scope, case):
    _, _, operation, proposed, observed, proof, proof_hash = await legacy_ambiguous_publication(scope)
    before = scope.ledger.snapshot()
    if case == "wrong_proposal":
        proposed["processingMetrics"]["visionMs"] = 0  # Even semantically equal must match the original raw WAL.
    elif case == "changed_observation":
        observed["source"]["blobPath"] = "foreign"
    elif case == "wrong_proof":
        proof_hash = "0" * 64
    elif case == "wrong_operation":
        operation = "not-this-operation"
    else:
        scope.ledger.live = True
        observed.pop("_etag")
    with pytest.raises(OwnershipError):
        scope.ledger.confirm_reconciled(operation, proposed, observed, evidence_path=proof, evidence_sha256=proof_hash)
    assert scope.ledger.snapshot() == before
