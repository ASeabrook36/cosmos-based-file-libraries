"""Document endpoints (spec §6.2): upload, list, read, delete.

An upload stores the original in Blob, records the document as ``queued`` with generation 1
pending and nothing active (spec §4.2), and enqueues ``ingest`` on the library's session.
Replacing content (``PUT …/content``) is Phase 3.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fli.api.libraries import Libraries, now
from fli.auth import require_caller
from fli.blobs import BlobStore
from fli.errors import ApiError, document_not_found, library_not_found
from fli.ids import new_id, original_blob_path, safe_file_name
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
    data = await read_capped(file, request.app.state.settings.upload_max_bytes)
    if not data:
        raise ApiError(400, "empty_file", "the uploaded file is empty")
    document_id = new_id()
    file_name = safe_file_name(file.filename or "")
    blob_path = original_blob_path(library_id, document_id, file_name)
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
        "activeGeneration": None,
        "pendingGeneration": 1,
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
        "extraction": None,
        "chunkCount": 0,
        "imageCount": 0,
        "imagesSkipped": 0,
        "updatedAt": now(),
    }
    created = await store.create(record)
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


@router.delete("/{document_id}", status_code=202)
async def delete_document(
    library_id: str, document_id: str, libraries: Libraries, store: Documents, ingest: Ingest
) -> Response:
    await active_library(libraries, library_id)
    for _ in range(REPLACE_ATTEMPTS):
        record = await load(store, library_id, document_id)
        record.update(status="deleting", updatedAt=now())
        try:
            await store.replace(record)
            break
        except Conflict:
            continue
    else:
        raise ApiError(409, "conflict", "the document changed while it was being deleted; retry")
    await ingest.send(
        {"type": "purgeDocument", "libraryId": library_id, "documentId": document_id}, session_id=library_id
    )
    return Response(status_code=202)
