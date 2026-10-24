"""Document endpoints (spec §6.2): upload, list, read, update metadata, replace, delete.

An upload stores the original in Blob, records the document as ``queued`` with generation 1
pending and nothing active (spec §4.2), and enqueues ``ingest`` on the library's session.

A replacement (``PUT …/content``) reserves the next generation on the record first, then
stores its original under that generation's own path, then enqueues ``ingest``. Reserving
first means two concurrent replacements never write the same path. The record's file fields
keep describing the active generation, the one search returns; the replacement's wait in
``pendingFile`` until the worker commits it (spec §7.8).

A metadata update (``PATCH``) changes the record at once and queues ``retag``, which copies the
change onto the chunks that filters match (spec §7.7). Each update raises ``metadataVersion``;
the message carries it, so a worker that reads an older record retries (evidence.md E28).
``acceptProposals`` moves proposals made during ingestion into the record's own fields, with
``source: accepted-proposal`` for tags (spec §9.1); ``dismissProposals`` drops them. A PATCH
that only dismisses changes nothing filters match, so it raises no version and queues no retag.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fli.api.libraries import Libraries, enqueue, now
from fli.auth import require_caller
from fli.blobs import BlobStore
from fli.errors import ApiError, document_not_found, library_not_found
from fli.generation import latest_generation
from fli.ids import new_id, original_blob_path, safe_file_name
from fli.proposals import facets
from fli.queue import Queue
from fli.store import Conflict, DocumentStore, Record, public

router = APIRouter(prefix="/v1/libraries/{library_id}/documents", dependencies=[Depends(require_caller)])
log = logging.getLogger("fli.api")

READ_CHUNK = 1024 * 1024
REPLACE_ATTEMPTS = 3

FILE_TYPES = {
    "pdf": "pdf",
    "docx": "docx",
    "pptx": "pptx",
    "xlsx": "xlsx",
    "csv": "csv",
    "txt": "txt",
    "md": "md",
    "markdown": "md",
    "html": "html",
    "htm": "html",
    "eml": "eml",
    "msg": "msg",
    **{ext: "image" for ext in ("png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "bmp")},
}


def file_type(file_name: str) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return FILE_TYPES.get(ext, ext or "unknown")


class Tag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100, pattern=r"^[^=]+$")
    value: str = Field(min_length=1, max_length=200)


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: str | None = Field(default=None, max_length=200)
    periodStart: date | None = None
    periodEnd: date | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def period_is_ordered(self) -> UploadMetadata:
        if (self.periodStart is None) != (self.periodEnd is None):
            raise ValueError("periodStart and periodEnd go together")
        if self.periodStart and self.periodEnd and self.periodStart > self.periodEnd:
            raise ValueError("periodStart is after periodEnd")
        return self


def documents(request: Request) -> DocumentStore:
    return request.app.state.documents


def blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def queue(request: Request) -> Queue:
    return request.app.state.queue


Documents = Annotated[DocumentStore, Depends(documents)]
Blobs = Annotated[BlobStore, Depends(blobs)]
Ingest = Annotated[Queue, Depends(queue)]


async def active_library(libraries: Libraries, library_id: str) -> Record:
    library = await libraries.get(library_id)
    if library is None:
        raise library_not_found(library_id)
    if library.get("status") == "deleting":
        raise ApiError(409, "library_deleting", "the library is being deleted")
    return library


async def read_capped(upload: UploadFile, limit: int) -> bytes:
    parts, size = [], 0
    while chunk := await upload.read(READ_CHUNK):
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "file_too_large", f"uploads are limited to {limit} bytes")
        parts.append(chunk)
    return b"".join(parts)


async def read_file(request: Request, upload: UploadFile) -> bytes:
    data = await read_capped(upload, request.app.state.settings.upload_max_bytes)
    if not data:
        raise ApiError(400, "empty_file", "the uploaded file is empty")
    return data


@router.post("", status_code=202)
async def upload_document(
    library_id: str,
    request: Request,
    libraries: Libraries,
    store: Documents,
    blob_store: Blobs,
    ingest: Ingest,
    file: Annotated[UploadFile, File()],
    metadata: Annotated[str | None, Form()] = None,
    uploadedBy: Annotated[str | None, Form(max_length=200)] = None,  # noqa: N803 — the API's field name
) -> dict[str, str]:
    await active_library(libraries, library_id)
    try:
        meta = UploadMetadata.model_validate(json.loads(metadata)) if metadata else UploadMetadata()
    except json.JSONDecodeError as exc:
        raise ApiError(400, "invalid_request", "metadata must be a JSON object") from exc
    data = await read_file(request, file)
    document_id = new_id()
    file_name = safe_file_name(file.filename or "")
    blob_path = original_blob_path(library_id, document_id, 1, file_name)
    await blob_store.upload(blob_path, data, file.content_type or "application/octet-stream")
    tags = [{"key": t.key, "value": t.value, "source": "user"} for t in meta.tags]
    record: Record = {
        "id": document_id,
        "documentId": document_id,
        "libraryId": library_id,
        "fileName": file_name,
        "contentType": file.content_type or "application/octet-stream",
        "fileType": file_type(file_name),
        "sizeBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "blobPath": blob_path,
        "status": "queued",
        "statusDetail": None,
        "generation": 1,
        "activeGeneration": None,
        "pendingGeneration": 1,
        "pendingFile": None,
        "uploadedAt": now(),
        "uploadedBy": uploadedBy,
        "authoredAt": None,
        "authoredAtSuspect": False,
        "author": meta.author,
        "periodStart": meta.periodStart.isoformat() if meta.periodStart else None,
        "periodEnd": meta.periodEnd.isoformat() if meta.periodEnd else None,
        "tags": tags,
        "tagProposals": [],
        "periodProposal": None,
        "authorProposal": None,
        "facets": sorted({f"{t['key']}={t['value']}" for t in tags}),
        "metadataVersion": 0,
        "extraction": None,
        "chunkCount": 0,
        "imageCount": 0,
        "imagesSkipped": 0,
        "updatedAt": now(),
    }
    try:
        created = await store.create(record)
    except Exception:
        # No record will ever point at the blob, so no purge would find it: remove it now.
        try:
            await blob_store.delete_prefix(f"{library_id}/{document_id}/")
        except Exception:
            log.exception("orphan blob not removed", extra={"libraryId": library_id, "documentId": document_id})
        raise
    message = {"type": "ingest", "libraryId": library_id, "documentId": document_id, "generation": 1}
    try:
        await ingest.send(message, session_id=library_id)
    except Exception as exc:
        log.exception("enqueue failed", extra={"libraryId": library_id, "documentId": document_id})
        created.update(status="failed", statusDetail="enqueue_failed", pendingGeneration=None, updatedAt=now())
        try:
            await store.replace(created)
        except Conflict:
            pass
        raise ApiError(503, "queue_unavailable", "the document could not be queued; upload it again") from exc
    return {"documentId": document_id, "status": "queued"}


@router.get("")
async def list_documents(
    library_id: str,
    libraries: Libraries,
    store: Documents,
    status: Literal["queued", "processing", "ready", "failed", "deleting"] | None = None,
    facet: str | None = None,
) -> dict[str, list[Record]]:
    library = await libraries.get(library_id)
    if library is None or library.get("status") == "deleting":
        raise library_not_found(library_id)
    return {"documents": [public(d) for d in await store.list(library_id, status=status, facet=facet)]}


async def load(store: DocumentStore, library_id: str, document_id: str) -> Record:
    record = await store.get(library_id, document_id)
    if record is None or record.get("status") == "deleting":
        raise document_not_found(document_id)
    return record


@router.get("/{document_id}")
async def get_document(library_id: str, document_id: str, libraries: Libraries, store: Documents) -> Record:
    library = await libraries.get(library_id)
    if library is None or library.get("status") == "deleting":
        raise library_not_found(library_id)
    return public(await load(store, library_id, document_id))


ProposalItem = Annotated[str, Field(min_length=1, max_length=301)]  # "key=value", "period" or "author"


class DocumentUpdate(BaseModel):
    """The fields a person may set, and the proposals they accept or dismiss (spec §6.2)."""

    model_config = ConfigDict(extra="forbid")

    author: str | None = Field(default=None, max_length=200)
    periodStart: date | None = None
    periodEnd: date | None = None
    tags: list[Tag] | None = Field(default=None, max_length=100)
    acceptProposals: list[ProposalItem] = Field(default_factory=list, max_length=100)
    dismissProposals: list[ProposalItem] = Field(default_factory=list, max_length=100)


def resolve_proposals(record: Record, changes: dict[str, Any]) -> None:
    """Accept or dismiss proposals named as ``key=value``, ``period`` or ``author``.

    Each must be a current proposal: one regenerated or resolved meanwhile is refused, so a
    stale screen never accepts what it did not show. Error messages never echo the values.
    """
    accept, dismiss = changes.get("acceptProposals") or [], changes.get("dismissProposals") or []
    if set(accept) & set(dismiss):
        raise ApiError(400, "invalid_request", "a proposal cannot be both accepted and dismissed")
    if "period" in accept and ("periodStart" in changes or "periodEnd" in changes):
        raise ApiError(400, "invalid_request", "accept the period proposal or set the period, not both")
    if "author" in accept and "author" in changes:
        raise ApiError(400, "invalid_request", "accept the author proposal or set the author, not both")
    proposed = {f"{t['key']}={t['value']}" for t in record.get("tagProposals") or []}
    for item in [*accept, *dismiss]:
        known = record.get(f"{item}Proposal") is not None if item in ("period", "author") else item in proposed
        if not known:
            raise ApiError(400, "invalid_request", "a named proposal is not among the document's current proposals")
    tags = list(record.get("tags") or [])
    have = {f"{t['key']}={t['value']}" for t in tags}
    for item in accept:
        if item == "period":
            period = record["periodProposal"]
            record["periodStart"], record["periodEnd"] = period["start"], period["end"]
        elif item == "author":
            record["author"] = record["authorProposal"]
        elif item not in have:
            key, _, value = item.partition("=")
            tags.append({"key": key, "value": value, "source": "accepted-proposal"})
    record["tags"] = tags
    record["facets"] = facets(tags)
    resolved = set(accept) | set(dismiss)
    record["tagProposals"] = [t for t in record.get("tagProposals") or [] if f"{t['key']}={t['value']}" not in resolved]
    for name in ("period", "author"):
        if name in resolved:
            record[f"{name}Proposal"] = None


def apply_update(record: Record, changes: dict[str, Any]) -> None:
    """Apply validated changes. Tags kept from before keep their source; new ones are ``user``.

    Fields set explicitly apply first; accepted proposals then add to them.
    """
    if "author" in changes:
        record["author"] = changes["author"]
    for name in ("periodStart", "periodEnd"):
        if name in changes:
            record[name] = changes[name].isoformat() if changes[name] else None
    if changes.get("tags") is not None:
        sources = {(t["key"], t["value"]): t.get("source", "user") for t in record.get("tags", [])}
        pairs = dict.fromkeys((t["key"], t["value"]) for t in changes["tags"])
        record["tags"] = [{"key": k, "value": v, "source": sources.get((k, v), "user")} for k, v in pairs]
        record["facets"] = sorted({f"{k}={v}" for k, v in pairs})
    resolve_proposals(record, changes)
    if (record.get("periodStart") is None) != (record.get("periodEnd") is None):
        raise ApiError(400, "invalid_request", "periodStart and periodEnd go together")
    if record.get("periodStart") and record["periodStart"] > record["periodEnd"]:
        raise ApiError(400, "invalid_request", "periodStart is after periodEnd")
    if set(changes) - {"dismissProposals"}:
        record["metadataVersion"] = (record.get("metadataVersion") or 0) + 1


@router.patch("/{document_id}")
async def update_document(
    library_id: str,
    document_id: str,
    libraries: Libraries,
    store: Documents,
    ingest: Ingest,
    body: Annotated[dict[str, Any], Body()],
) -> Record:
    """Update author, period or tags now; ``retag`` copies them to the chunks (spec §7.7).

    Until ``retag`` runs, filters still match the chunks' previous metadata. If it cannot be
    queued the update is kept and the response is ``503``: repeating the request queues it again.
    """
    await active_library(libraries, library_id)
    changes = DocumentUpdate.model_validate(body).model_dump(exclude_unset=True)
    if "tags" in changes and changes["tags"] is None:
        raise ApiError(400, "invalid_request", "tags is a list; send [] to remove every tag")
    for _ in range(REPLACE_ATTEMPTS):
        record = await load(store, library_id, document_id)
        apply_update(record, changes)
        record["updatedAt"] = now()
        try:
            record = await store.replace(record)
            break
        except Conflict:
            continue
    else:
        raise ApiError(409, "conflict", "the document changed while it was being updated; retry")
    if not set(changes) - {"dismissProposals"}:
        return public(record)  # nothing filters match changed: no retag
    message = {
        "type": "retag",
        "libraryId": library_id,
        "documentId": document_id,
        "metadataVersion": record["metadataVersion"],
    }
    await enqueue(ingest, message, library_id)
    return public(record)


@router.put("/{document_id}/content", status_code=202)
async def replace_content(
    library_id: str,
    document_id: str,
    request: Request,
    libraries: Libraries,
    store: Documents,
    blob_store: Blobs,
    ingest: Ingest,
    file: Annotated[UploadFile, File()],
    uploadedBy: Annotated[str | None, Form(max_length=200)] = None,  # noqa: N803 — the API's field name
) -> dict[str, Any]:
    """Reprocess the document from a new file, as the next generation (spec §7.8).

    The active generation stays searchable until the new one commits. A replacement sent while
    another is still pending supersedes it: the worker abandons the older one.
    """
    await active_library(libraries, library_id)
    data = await read_file(request, file)
    file_name = safe_file_name(file.filename or "")
    content_type = file.content_type or "application/octet-stream"
    for _ in range(REPLACE_ATTEMPTS):
        record = await load(store, library_id, document_id)
        generation = latest_generation(record) + 1
        pending: Record = {
            "fileName": file_name,
            "contentType": content_type,
            "fileType": file_type(file_name),
            "sizeBytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "blobPath": original_blob_path(library_id, document_id, generation, file_name),
            "uploadedAt": now(),
            "uploadedBy": uploadedBy,
        }
        record.update(
            generation=generation,
            pendingGeneration=generation,
            pendingFile=pending,
            status="queued",
            statusDetail=None,
            updatedAt=now(),
        )
        try:
            await store.replace(record)
            break
        except Conflict:
            continue
    else:
        raise ApiError(409, "conflict", "the document changed while it was being replaced; retry")

    ids = {"libraryId": library_id, "documentId": document_id, "generation": generation}
    try:
        await blob_store.upload(pending["blobPath"], data, content_type)
    except Exception:
        log.exception("replacement not stored", extra=ids)
        await abandon_replacement(store, blob_store, ids, pending["blobPath"], "upload_failed")
        raise
    try:
        await ingest.send({"type": "ingest", **ids}, session_id=library_id)
    except Exception as exc:
        log.exception("enqueue failed", extra=ids)
        await abandon_replacement(store, blob_store, ids, pending["blobPath"], "enqueue_failed")
        raise ApiError(503, "queue_unavailable", "the replacement could not be queued; send it again") from exc
    return {"documentId": document_id, "status": "queued", "generation": generation}


async def abandon_replacement(
    store: DocumentStore, blob_store: BlobStore, ids: dict[str, Any], blob_path: str, detail: str
) -> None:
    """A replacement no worker will ever see: mark it failed and remove its original.

    The active generation is untouched and stays searchable, as after any failed replacement.
    """
    library_id, document_id, generation = ids["libraryId"], ids["documentId"], ids["generation"]
    for _ in range(REPLACE_ATTEMPTS):
        record = await store.get(library_id, document_id)
        if record is None or record.get("pendingGeneration") != generation:
            break  # deleted or superseded meanwhile: its own purge covers this generation
        record.update(status="failed", statusDetail=detail, pendingGeneration=None, pendingFile=None, updatedAt=now())
        try:
            await store.replace(record)
            break
        except Conflict:
            continue
    try:
        await blob_store.delete(blob_path)
    except Exception:
        log.exception("orphan blob not removed", extra=ids)


@router.delete("/{document_id}", status_code=202)
async def delete_document(
    library_id: str, document_id: str, libraries: Libraries, store: Documents, ingest: Ingest
) -> Response:
    """Mark the document ``deleting``, then queue its purge (spec §7.8).

    As for libraries, deleting a document already ``deleting`` queues the purge again. The
    message says the document was deleted, so a worker that reads the record before the
    ``deleting`` write reaches its replica retries instead of skipping the purge (E28).
    """
    await active_library(libraries, library_id)
    for _ in range(REPLACE_ATTEMPTS):
        record = await store.get(library_id, document_id)
        if record is None:
            raise document_not_found(document_id)
        if record.get("status") == "deleting":
            break
        record.update(status="deleting", updatedAt=now())
        try:
            await store.replace(record)
            break
        except Conflict:
            continue
    else:
        raise ApiError(409, "conflict", "the document changed while it was being deleted; retry")
    message = {"type": "purgeDocument", "libraryId": library_id, "documentId": document_id, "deleted": True}
    await enqueue(ingest, message, library_id)
    return Response(status_code=202)
