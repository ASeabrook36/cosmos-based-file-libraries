"""What the worker does with each queue message (spec §7.1, §7.2, §7.8).

Messages for one library arrive one at a time, in order (Service Bus sessions keyed by
``libraryId``); that ordering is what makes the purge rule safe. Different libraries run in
parallel, sharing one cap on chunk writes (``fli.chunks``).

Errors fall into three kinds:

* the file itself is unusable (encrypted, unsupported, unparseable, no text): the document is
  marked ``failed`` with a ``statusDetail`` and the message completes. Retrying cannot help.
* anything else raises. The runtime abandons the message and Service Bus redelivers it; chunk
  ids are deterministic, so a retry overwrites what the last attempt wrote.
* on the final delivery the runtime calls ``fail`` before dead-lettering, so the document
  record and the dead-letter queue always agree.

Document records change only through ``_mutate``: an etag-conditional replace that re-reads
and re-decides on conflict, because the API may change the record at any moment (delete,
and in Phase 3, replace).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fli.blobs import BlobStore
from fli.chunking import Chunker
from fli.chunks import ChunkStore
from fli.config import EMBEDDING_MODEL
from fli.embedding import Embedder
from fli.extraction import Extracted, OcrClient, extract
from fli.ids import chunk_id
from fli.queue import Message, Queue
from fli.store import Conflict, DocumentStore, LibraryStore, Record

log = logging.getLogger("fli.worker")

MUTATE_ATTEMPTS = 5


class BadMessage(ValueError):
    """A message the worker can never process: the runtime dead-letters it at once."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def status_detail(result: Extracted) -> str:
    """Map an extraction outcome to the spec's ``statusDetail`` values (§4.2)."""
    if result.outcome == "encrypted":
        return "encrypted"
    if result.outcome == "categorical":
        return result.categorical_reason or "unreadable"
    if result.outcome == "needs_ocr":
        return "ocr_unavailable"
    reason = result.unsupported_reason or result.failure_reason or "parser_error"
    return "parser_error" if reason.startswith("parser:") else reason


@dataclass
class Worker:
    libraries: LibraryStore
    documents: DocumentStore
    chunks: ChunkStore
    blobs: BlobStore
    queue: Queue
    embedder: Embedder
    chunker: Chunker
    ocr: OcrClient | None = None

    async def handle(self, message: Message) -> None:
        kind = message.get("type")
        library_id = message.get("libraryId")
        if not isinstance(library_id, str) or not library_id:
            raise BadMessage("libraryId is required")
        if kind == "ingest":
            document_id, generation = message.get("documentId"), message.get("generation")
            if not isinstance(document_id, str) or not isinstance(generation, int) or generation < 1:
                raise BadMessage("ingest needs documentId and a positive generation")
            await self.ingest(library_id, document_id, generation)
        elif kind == "purgeDocument":
            document_id = message.get("documentId")
            if not isinstance(document_id, str) or not document_id:
                raise BadMessage("purgeDocument needs documentId")
            await self.purge_document(library_id, document_id)
        elif kind == "purgeLibrary":
            await self.purge_library(library_id)
        else:
            raise BadMessage(f"unknown message type {kind!r}")

    # -- ingest -------------------------------------------------------------------------------

    async def ingest(self, library_id: str, document_id: str, generation: int) -> None:
        library = await self.libraries.get(library_id)
        if library is None or library.get("status") == "deleting":
            # Uploaded as the library was being deleted: never index it.
            await self._mark_deleting(library_id, document_id)
            return

        def start(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False  # deleted, superseded or already committed: nothing to do
            doc.update(status="processing", updatedAt=_now())
            return True

        document = await self._mutate(library_id, document_id, start)
        if document is None:
            return

        data = await self.blobs.download(document["blobPath"])
        result = await extract(data, filename=document["fileName"], content_type=document["contentType"], ocr=self.ocr)
        if result.outcome != "ok":
            await self._fail(library_id, document_id, generation, status_detail(result))
            return
        kind = "email" if document.get("fileType") == "eml" else "document"
        pieces = self.chunker.chunk(result.sections, kind)
        if not pieces:
            await self._fail(library_id, document_id, generation, "no_text")
            return

        vectors = await self.embedder.embed([p.text for p in pieces])
        author = document.get("author") or result.author
        authored_at = document.get("authoredAt") or (result.authored_at.isoformat() if result.authored_at else None)
        created = _now()
        records = [
            {
                "id": chunk_id(document_id, generation, p.index),
                "libraryId": library_id,
                "documentId": document_id,
                "generation": generation,
                "chunkIndex": p.index,
                "contentKind": "text",
                "text": p.text,
                "heading": p.heading,
                "location": p.location or {},
                "imageRef": None,
                "imageMeta": None,
                "fileName": document["fileName"],
                "fileType": document["fileType"],
                "uploadedAt": document["uploadedAt"],
                "authoredAt": authored_at,
                "author": author,
                "periodStart": document.get("periodStart"),
                "periodEnd": document.get("periodEnd"),
                "facets": document.get("facets", []),
                "tokenCount": p.token_count,
                "embedding": vector,
                "embeddingModel": EMBEDDING_MODEL,
                "chunkingVersion": self.chunker.version,
                "createdAt": created,
            }
            for p, vector in zip(pieces, vectors, strict=True)
        ]
        # The worker-wide cap inside the chunk store decides how many of these run at once.
        await asyncio.gather(*(self.chunks.upsert(r) for r in records))

        def commit(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False  # deleted or replaced while we wrote: abandon this generation
            doc.update(
                status="ready",
                statusDetail=None,
                activeGeneration=generation,
                pendingGeneration=None,
                chunkCount=len(records),
                author=doc.get("author") or result.author,
                authoredAt=doc.get("authoredAt") or authored_at,
                extraction={
                    "method": str(result.method),
                    "ocrApplied": result.ocr_applied,
                    "pageCount": result.page_count,
                    "extractionVersion": result.extraction_version,
                    "chunkingVersion": self.chunker.version,
                },
                updatedAt=_now(),
            )
            return True

        committed = await self._mutate(library_id, document_id, commit)
        if committed is None or generation > 1:
            await self._purge_later(library_id, document_id)  # abandoned chunks, or older generations
        if committed is not None:
            await self.refresh_library(library_id)

    async def fail(self, message: Message, reason: str) -> None:
        """Called by the runtime on the last delivery, before it dead-letters the message."""
        if message.get("type") != "ingest":
            return
        library_id, document_id, generation = message["libraryId"], message["documentId"], message["generation"]
        await self._fail(library_id, document_id, generation, reason)

    async def _fail(self, library_id: str, document_id: str, generation: int, detail: str) -> None:
        def mark(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False
            # activeGeneration is untouched: a failed replacement keeps the previous version searchable.
            doc.update(status="failed", statusDetail=detail, pendingGeneration=None, updatedAt=_now())
            return True

        if await self._mutate(library_id, document_id, mark) is not None:
            log.info("document failed", extra={"libraryId": library_id, "documentId": document_id, "detail": detail})
            await self._purge_later(library_id, document_id)  # anything this generation half-wrote

    # -- purges -------------------------------------------------------------------------------

    async def purge_document(self, library_id: str, document_id: str) -> None:
        document = await self.documents.get(library_id, document_id)
        existing = await self.chunks.generations(library_id, document_id)
        if document is None or document.get("status") == "deleting":
            for item_id, _ in existing:
                await self.chunks.delete(library_id, document_id, item_id)
            await self.blobs.delete_prefix(f"{library_id}/{document_id}/")
            await self.documents.delete(library_id, document_id)
            if document is not None:
                await self.refresh_library(library_id)
            return
        keep = {document.get("activeGeneration"), document.get("pendingGeneration")} - {None}
        doomed = [item_id for item_id, gen in existing if gen not in keep]
        await asyncio.gather(*(self.chunks.delete(library_id, document_id, i) for i in doomed))

    async def purge_library(self, library_id: str) -> None:
        library = await self.libraries.get(library_id)
        if library is not None and library.get("status") != "deleting":
            log.warning("purgeLibrary for an active library ignored", extra={"libraryId": library_id})
            return
        for document in await self.documents.list(library_id, status=None, facet=None):
            if document.get("status") != "deleting":
                await self._mark_deleting(library_id, document["id"], enqueue=False)
            await self.purge_document(library_id, document["id"])
        await self.blobs.delete_prefix(f"{library_id}/")
        await self.libraries.delete(library_id)

    # -- library summary ------------------------------------------------------------------------

    async def refresh_library(self, library_id: str) -> None:
        """Recompute ``stats`` and ``tagVocabulary`` from the documents (spec §7.2 step 11)."""
        documents = await self.documents.list(library_id, status=None, facet=None)
        ready = [d for d in documents if d.get("activeGeneration") is not None and d.get("status") != "deleting"]
        vocabulary: dict[str, Counter[str]] = {}
        for d in ready:
            for facet in d.get("facets", []):
                key, _, value = facet.partition("=")
                vocabulary.setdefault(key, Counter())[value] += 1
        stats = {
            "documentCount": len(ready),
            "chunkCount": sum(d.get("chunkCount", 0) for d in ready),
            "imageCount": sum(d.get("imageCount", 0) for d in ready),
            "lastIngestedAt": _now(),
        }
        for _ in range(MUTATE_ATTEMPTS):
            library = await self.libraries.get(library_id)
            if library is None or library.get("status") == "deleting":
                return
            library.update(stats=stats, tagVocabulary={k: dict(v) for k, v in vocabulary.items()}, updatedAt=_now())
            try:
                await self.libraries.replace(library)
                return
            except Conflict:
                continue
        raise Conflict(library_id)

    # -- helpers --------------------------------------------------------------------------------

    async def _mutate(self, library_id: str, document_id: str, change: Callable[[Record], bool]) -> Record | None:
        """Apply ``change`` to the current record. ``None`` when the record is gone or ``change`` declines."""
        for _ in range(MUTATE_ATTEMPTS):
            document = await self.documents.get(library_id, document_id)
            if document is None or not change(document):
                return None
            try:
                return await self.documents.replace(document)
            except Conflict:
                continue
        raise Conflict(document_id)

    async def _mark_deleting(self, library_id: str, document_id: str, *, enqueue: bool = True) -> None:
        def mark(doc: Record) -> bool:
            if doc.get("status") == "deleting":
                return False
            doc.update(status="deleting", updatedAt=_now())
            return True

        await self._mutate(library_id, document_id, mark)
        if enqueue:
            await self._purge_later(library_id, document_id)

    async def _purge_later(self, library_id: str, document_id: str) -> None:
        message: dict[str, Any] = {"type": "purgeDocument", "libraryId": library_id, "documentId": document_id}
        await self.queue.send(message, session_id=library_id)
