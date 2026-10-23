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
    assert doc["blobPath"] == f"{lib}/{doc_id}/original/Q4 review.txt"
    assert s.blobs.blobs[doc["blobPath"]][0] == b"Margins fell in EMEA."
    assert doc["sha256"] == hashlib.sha256(b"Margins fell in EMEA.").hexdigest() and doc["sizeBytes"] == 21
    assert (doc["status"], doc["activeGeneration"], doc["pendingGeneration"]) == ("queued", None, 1)
    assert doc["fileType"] == "txt" and doc["author"] == "Jane Doe" and doc["facets"] == ["region=EMEA"]
    assert doc["tags"] == [{"key": "region", "value": "EMEA", "source": "user"}]
    assert s.queue.sent == [(lib, {"type": "ingest", "libraryId": lib, "documentId": doc_id, "generation": 1})]


def test_uploaded_names_are_made_safe(ctx):
    client, s, lib = ctx
    doc_id = upload(client, lib, name="../../other/evil.txt").json()["documentId"]
    assert list(s.blobs.blobs) == [f"{lib}/{doc_id}/original/evil.txt"]


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
    assert client.delete(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 202
    assert s.queue.sent[-1] == (lib, {"type": "purgeDocument", "libraryId": lib, "documentId": doc_id})
    assert client.get(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 404
    assert client.delete(f"/v1/libraries/{lib}/documents/{doc_id}").status_code == 404


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
