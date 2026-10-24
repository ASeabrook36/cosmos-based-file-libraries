"""Document endpoints (spec §6.2)."""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.fakes import api


@pytest.fixture
def ctx():
    client, services = api(UPLOAD_MAX_BYTES="1000")
    library = client.post("/v1/libraries", json={"kind": "shared", "name": "Finance"}).json()
    return client, services, library["id"]


def upload(client, library_id, name="Q4 review.txt", data=b"Margins fell in EMEA.", **form):
    return client.post(f"/v1/libraries/{library_id}/documents", files={"file": (name, data, "text/plain")}, data=form)


def test_upload_stores_the_file_records_it_and_queues_generation_1(ctx):
    client, s, lib = ctx
    r = upload(client, lib, metadata=json.dumps({"author": "Jane Doe", "tags": [{"key": "region", "value": "EMEA"}]}))
    assert r.status_code == 202 and r.json()["status"] == "queued"
    doc_id = r.json()["documentId"]
    doc = client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()
    assert doc["blobPath"] == f"{lib}/{doc_id}/original/1/Q4 review.txt"
    assert s.blobs.blobs[doc["blobPath"]][0] == b"Margins fell in EMEA."
    assert doc["sha256"] == hashlib.sha256(b"Margins fell in EMEA.").hexdigest() and doc["sizeBytes"] == 21
    assert (doc["status"], doc["activeGeneration"], doc["pendingGeneration"]) == ("queued", None, 1)
    assert doc["generation"] == 1 and doc["pendingFile"] is None
    assert doc["fileType"] == "txt" and doc["author"] == "Jane Doe" and doc["facets"] == ["region=EMEA"]
    assert doc["tags"] == [{"key": "region", "value": "EMEA", "source": "user"}]
    assert s.queue.sent == [(lib, {"type": "ingest", "libraryId": lib, "documentId": doc_id, "generation": 1})]


def test_uploaded_names_are_made_safe(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib, name="../../other/evil.txt").json()["documentId"]
    assert list(s.blobs.blobs) == [f"{lib}/{doc_id}/original/1/evil.txt"]


@pytest.mark.parametrize(
    "kwargs,status,code",
    [
        ({"data": b"x" * 1001}, 413, "file_too_large"),
        ({"data": b""}, 400, "empty_file"),
        ({"metadata": "not json"}, 400, "invalid_request"),
        ({"metadata": json.dumps({"periodStart": "2025-10-01"})}, 400, "invalid_request"),
        ({"metadata": json.dumps({"periodStart": "2025-12-31", "periodEnd": "2025-10-01"})}, 400, "invalid_request"),
        ({"metadata": json.dumps({"tags": [{"key": "a=b", "value": "c"}]})}, 400, "invalid_request"),
    ],
)
def test_bad_uploads_are_refused_before_anything_is_stored(ctx, kwargs, status, code):
    client, s, lib = ctx
    r = upload(client, lib, **kwargs)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    assert not s.blobs.blobs and not s.queue.sent


def test_upload_to_a_deleting_library_is_409(ctx):
    client, _, lib = ctx
    client.delete(f"/v1/libraries/{lib}")
    r = upload(client, lib)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "library_deleting")


def test_upload_to_an_unknown_library_is_404(ctx):
    client, _, _ = ctx
    assert upload(client, "nope").status_code == 404


def test_a_queue_failure_marks_the_document_failed_and_says_so(ctx, monkeypatch):
    client, s, lib = ctx

    async def broken(message, *, session_id):
        raise RuntimeError("bus down")

    monkeypatch.setattr(s.queue, "send", broken)
    r = upload(client, lib)
    assert (r.status_code, r.json()["error"]["code"]) == (503, "queue_unavailable")
    [doc] = client.get(f"/v1/libraries/{lib}/documents").json()["documents"]
    assert (doc["status"], doc["statusDetail"], doc["pendingGeneration"]) == ("failed", "enqueue_failed", None)


def test_list_filters_by_status_and_facet(ctx):
    client, _, lib = ctx
    a = upload(client, lib, metadata=json.dumps({"tags": [{"key": "region", "value": "EMEA"}]})).json()["documentId"]
    upload(client, lib)
    listed = client.get(f"/v1/libraries/{lib}/documents", params={"facet": "region=EMEA"}).json()["documents"]
    assert [d["id"] for d in listed] == [a]
    assert len(client.get(f"/v1/libraries/{lib}/documents", params={"status": "queued"}).json()["documents"]) == 2


def test_delete_hides_the_document_and_queues_its_purge(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    purge = (lib, {"type": "purgeDocument", "libraryId": lib, "documentId": doc_id, "deleted": True})
    assert client.delete(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 202
    assert s.queue.sent[-1] == purge
    assert client.get(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 404
    # Deleting again queues the purge again: the retry path for a purge that never ran (E28).
    assert client.delete(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 202
    assert s.queue.sent[-2:] == [purge, purge]
    assert client.delete(f"/v1/libraries/{lib}/documents/nope").status_code == 404


def test_a_queue_failure_on_delete_is_503_and_the_delete_can_be_retried(ctx, monkeypatch):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    real = s.queue.send

    async def broken(message, *, session_id):
        raise RuntimeError("bus down")

    monkeypatch.setattr(s.queue, "send", broken)
    r = client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "queue_unavailable")
    assert client.get(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 404  # hidden all the same
    monkeypatch.setattr(s.queue, "send", real)
    assert client.delete(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 202
    assert s.queue.sent[-1][1]["type"] == "purgeDocument"


def test_a_failed_record_create_removes_the_uploaded_blob(ctx, monkeypatch):
    client, s, lib = ctx

    async def broken(record):
        raise RuntimeError("cosmos down")

    monkeypatch.setattr(s.documents, "create", broken)
    with pytest.raises(RuntimeError):
        upload(client, lib)
    assert not s.blobs.blobs and not s.queue.sent


def test_file_types_are_normalised():
    from fli.api.documents import file_type

    assert [file_type(n) for n in ("a.PDF", "b.htm", "c.markdown", "d.jpeg", "e.doc", "noext")] == [
        "pdf",
        "html",
        "md",
        "image",
        "doc",
        "unknown",
    ]


# -- replacement (PUT …/content, spec §7.8) ------------------------------------------------------


def replace(client, library_id, doc_id, name="Q4 review.txt", data=b"Margins rose in EMEA.", **form):
    return client.put(
        f"/v1/libraries/{library_id}/documents/{doc_id}/content", files={"file": (name, data, "text/plain")}, data=form
    )


async def committed(s, lib, doc_id):
    """Mark generation 1 committed, as the worker would."""
    record = await s.documents.get(lib, doc_id)
    record.update(status="ready", activeGeneration=1, pendingGeneration=None)
    await s.documents.replace(record)


async def test_a_replacement_reserves_the_next_generation_and_keeps_the_active_one(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    await committed(s, lib, doc_id)
    r = replace(client, lib, doc_id, uploadedBy="ann")
    assert r.status_code == 202 and r.json() == {"documentId": doc_id, "status": "queued", "generation": 2}
    doc = client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()
    assert (doc["generation"], doc["activeGeneration"], doc["pendingGeneration"], doc["status"]) == (2, 1, 2, "queued")
    # The record still describes what search returns; the replacement waits in pendingFile.
    assert doc["blobPath"] == f"{lib}/{doc_id}/original/1/Q4 review.txt" and doc["sizeBytes"] == 21
    pending = doc["pendingFile"]
    assert pending["blobPath"] == f"{lib}/{doc_id}/original/2/Q4 review.txt" and pending["uploadedBy"] == "ann"
    assert pending["sha256"] == hashlib.sha256(b"Margins rose in EMEA.").hexdigest()
    assert s.queue.sent[-1] == (lib, {"type": "ingest", "libraryId": lib, "documentId": doc_id, "generation": 2})


async def test_a_same_name_replacement_never_overwrites_the_served_original(ctx):
    """Review finding 7: originals were keyed by file name only."""
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    await committed(s, lib, doc_id)
    replace(client, lib, doc_id)
    assert s.blobs.blobs[f"{lib}/{doc_id}/original/1/Q4 review.txt"][0] == b"Margins fell in EMEA."
    assert s.blobs.blobs[f"{lib}/{doc_id}/original/2/Q4 review.txt"][0] == b"Margins rose in EMEA."


async def test_a_replacement_supersedes_one_still_pending(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    await committed(s, lib, doc_id)
    replace(client, lib, doc_id, name="v2.txt")
    assert replace(client, lib, doc_id, name="v3.txt").json()["generation"] == 3
    doc = client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()
    assert (doc["activeGeneration"], doc["pendingGeneration"], doc["pendingFile"]["fileName"]) == (1, 3, "v3.txt")


async def test_generations_are_never_reused_after_a_failed_replacement(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    await committed(s, lib, doc_id)
    replace(client, lib, doc_id)
    record = await s.documents.get(lib, doc_id)
    record.update(status="failed", pendingGeneration=None, pendingFile=None)  # generation 2 failed
    await s.documents.replace(record)
    assert replace(client, lib, doc_id).json()["generation"] == 3


@pytest.mark.parametrize(
    "kwargs,status,code",
    [({"data": b"x" * 1001}, 413, "file_too_large"), ({"data": b""}, 400, "empty_file")],
)
async def test_bad_replacements_change_nothing(ctx, kwargs, status, code):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    before = await s.documents.get(lib, doc_id)
    r = replace(client, lib, doc_id, **kwargs)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    assert await s.documents.get(lib, doc_id) == before and len(s.queue.sent) == 1


def test_replacing_a_deleted_or_unknown_document_is_404_and_in_a_deleting_library_409(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    assert replace(client, lib, "nope").json()["error"]["code"] == "document_not_found"
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
    r = replace(client, lib, doc_id)
    assert (r.status_code, r.json()["error"]["code"]) == (404, "document_not_found")
    other = upload(client, lib).json()["documentId"]
    client.delete(f"/v1/libraries/{lib}")
    r = replace(client, lib, other)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "library_deleting")


async def test_a_queue_failure_fails_the_replacement_only(ctx, monkeypatch):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    await committed(s, lib, doc_id)

    async def broken(message, *, session_id):
        raise RuntimeError("bus down")

    monkeypatch.setattr(s.queue, "send", broken)
    r = replace(client, lib, doc_id)
    assert (r.status_code, r.json()["error"]["code"]) == (503, "queue_unavailable")
    doc = client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()
    assert (doc["status"], doc["statusDetail"], doc["activeGeneration"]) == ("failed", "enqueue_failed", 1)
    assert doc["pendingGeneration"] is None and doc["pendingFile"] is None and doc["generation"] == 2
    assert list(s.blobs.blobs) == [f"{lib}/{doc_id}/original/1/Q4 review.txt"]  # the orphan is gone


async def test_a_storage_failure_fails_the_replacement_only(ctx, monkeypatch):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    await committed(s, lib, doc_id)

    async def broken(path, data, content_type):
        raise RuntimeError("storage down")

    monkeypatch.setattr(s.blobs, "upload", broken)
    with pytest.raises(RuntimeError):
        replace(client, lib, doc_id)
    doc = client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()
    assert (doc["status"], doc["statusDetail"], doc["activeGeneration"]) == ("failed", "upload_failed", 1)
    assert len(s.queue.sent) == 1  # only the original upload's ingest


async def test_a_concurrent_change_is_retried_with_a_fresh_generation(ctx, monkeypatch):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    await committed(s, lib, doc_id)
    real, raced = s.documents.replace, {"done": False}

    async def race_once(record):
        if not raced["done"]:
            raced["done"] = True
            current = await s.documents.get(lib, doc_id)
            current.update(generation=5)  # another replacement reserved generation 5 first
            await real(current)
        return await real(record)

    monkeypatch.setattr(s.documents, "replace", race_once)
    assert replace(client, lib, doc_id).json()["generation"] == 6


# -- metadata updates (PATCH, spec §6.2, §7.7) ---------------------------------------------------


def patch(client, library_id, doc_id, **body):
    return client.patch(f"/v1/libraries/{library_id}/documents/{doc_id}", json=body)


def test_an_upload_starts_at_metadata_version_0(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    assert client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()["metadataVersion"] == 0


def test_an_update_changes_the_record_at_once_and_queues_retag(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    r = patch(
        client,
        lib,
        doc_id,
        author="Jane Doe",
        periodStart="2025-10-01",
        periodEnd="2025-12-31",
        tags=[{"key": "region", "value": "EMEA"}, {"key": "region", "value": "EMEA"}],
    )
    assert r.status_code == 200
    doc = r.json()
    assert (doc["author"], doc["periodStart"], doc["periodEnd"]) == ("Jane Doe", "2025-10-01", "2025-12-31")
    assert doc["tags"] == [{"key": "region", "value": "EMEA", "source": "user"}] and doc["facets"] == ["region=EMEA"]
    assert doc["metadataVersion"] == 1 and "_etag" not in doc
    retag = {"type": "retag", "libraryId": lib, "documentId": doc_id, "metadataVersion": 1}
    assert s.queue.sent[-1] == (lib, retag)


async def test_kept_tags_keep_their_source(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    record = await s.documents.get(lib, doc_id)
    record["tags"] = [{"key": "topic", "value": "margin", "source": "accepted-proposal"}]
    await s.documents.replace(record)
    tags = [{"key": "topic", "value": "margin"}, {"key": "region", "value": "APAC"}]
    doc = patch(client, lib, doc_id, tags=tags).json()
    assert doc["tags"] == [
        {"key": "topic", "value": "margin", "source": "accepted-proposal"},
        {"key": "region", "value": "APAC", "source": "user"},
    ]
    assert doc["facets"] == ["region=APAC", "topic=margin"]


def test_only_named_fields_change_and_a_period_can_be_cleared(ctx):
    client, s, lib = ctx
    meta = json.dumps({"author": "Jane Doe", "periodStart": "2025-10-01", "periodEnd": "2025-12-31"})
    doc_id = upload(client, lib, metadata=meta).json()["documentId"]
    doc = patch(client, lib, doc_id, periodStart=None, periodEnd=None).json()
    assert (doc["author"], doc["periodStart"], doc["periodEnd"]) == ("Jane Doe", None, None)
    assert patch(client, lib, doc_id, tags=[]).json()["facets"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"periodStart": "2025-10-01"},  # half a period
        {"periodStart": "2025-12-31", "periodEnd": "2025-10-01"},
        {"tags": None},
        {"tags": [{"key": "a=b", "value": "c"}]},
        {"acceptProposals": ["period"]},  # arrives with proposals
        {"fileName": "renamed.pdf"},
        {"author": "x" * 201},
    ],
)
def test_bad_updates_change_nothing(ctx, body):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    sent = len(s.queue.sent)
    r = patch(client, lib, doc_id, **body)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "invalid_request")
    assert client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()["metadataVersion"] == 0
    assert len(s.queue.sent) == sent


def test_updating_a_deleted_document_is_404_and_in_a_deleting_library_409(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
    assert patch(client, lib, doc_id, author="x").json()["error"]["code"] == "document_not_found"
    other = upload(client, lib).json()["documentId"]
    client.delete(f"/v1/libraries/{lib}")
    r = patch(client, lib, other, author="x")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "library_deleting")


def test_a_queue_failure_keeps_the_update_and_says_so(ctx, monkeypatch):
    client, s, lib = ctx
    doc_id = upload(client, lib).json()["documentId"]

    async def broken(message, *, session_id):
        raise RuntimeError("bus down")

    monkeypatch.setattr(s.queue, "send", broken)
    r = patch(client, lib, doc_id, author="Jane Doe")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "queue_unavailable")
    doc = client.get(f"/v1/libraries/{lib}/documents/{doc_id}").json()
    assert (doc["author"], doc["metadataVersion"]) == ("Jane Doe", 1)
