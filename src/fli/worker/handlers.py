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
replace).

The API writes records through its own Cosmos client, so the worker's first read of a change
may come from a replica that lags (evidence.md E28). Where a message proves the record must
already show something (a deletion, or the generation the message names), a read that does
not show it raises ``StaleRead`` and the message is redelivered.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fli.blobs import BlobStore
from fli.chunking import Chunker
from fli.chunks import ChunkStore
from fli.config import EMBEDDING_MODEL
from fli.embedding import Embedder, measure_embeddings
from fli.extraction import Extracted, OcrClient, extract
from fli.generation import is_obsolete, latest_generation
from fli.ids import blob_generation, chunk_id, image_prefix, original_prefix, text_blob_path, text_prefix
from fli.metrics import record as record_metric
from fli.proposals import (
    OPENING_CHARS,
    ChatModel,
    DocumentProposals,
    description_due,
    description_messages,
    document_messages,
    opening_text,
    parse_description,
    parse_document_proposals,
    prompt_vocabulary,
    sample_documents,
    settle_proposals,
)
from fli.queue import Message, Queue
from fli.store import Conflict, DocumentStore, LibraryStore, Record
from fli.vocabulary import filter_values, searchable, tag_vocabulary

log = logging.getLogger("fli.worker")

MUTATE_ATTEMPTS = 5


class BadMessage(ValueError):
    """A message the worker can never process: the runtime dead-letters it at once."""


class StaleRead(RuntimeError):
    """A record read that misses a write the message proves the API already made.

    The API wrote it through another Cosmos client, and a read with no session token for that
    write may come from a replica that has not caught up (evidence.md E28). Raising lets the
    message be redelivered instead of completing having done nothing.
    """


class NotYetDeleting(StaleRead):
    """A purge for a deletion whose ``deleting`` write this worker cannot see yet."""


class GenerationNotVisible(StaleRead):
    """An ingest for a generation newer than any the record shows: the replacement's write is not visible yet."""


class MetadataNotVisible(StaleRead):
    """A retag for a metadata version newer than the record shows: the PATCH's write is not visible yet."""


RETAG_FIELDS = ("facets", "author", "periodStart", "periodEnd")  # copied from the document to its chunks


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
    max_chunks_per_document: int = 5_000  # INGEST_MAX_CHUNKS_PER_DOCUMENT
    proposer: ChatModel | None = None  # the chat deployment; proposals are off without one (spec §9.1)
    stale_read_pause: float = 1.0  # seconds before retrying a message whose record read is stale

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
            await self.purge_document(library_id, document_id, deleted=message.get("deleted") is True)
        elif kind == "purgeLibrary":
            await self.purge_library(library_id)
        elif kind == "retag":
            document_id, version = message.get("documentId"), message.get("metadataVersion")
            if not isinstance(document_id, str) or not isinstance(version, int) or version < 1:
                raise BadMessage("retag needs documentId and a positive metadataVersion")
            await self.retag(library_id, document_id, version)
        elif kind == "proposeDescription":
            await self.propose_description(library_id, requested=message.get("requested") is True)
        else:
            raise BadMessage(f"unknown message type {kind!r}")

    # -- ingest -------------------------------------------------------------------------------

    async def ingest(self, library_id: str, document_id: str, generation: int) -> None:
        started = time.perf_counter()
        library = await self.libraries.get(library_id)
        if library is None or library.get("status") == "deleting":
            # Uploaded as the library was being deleted: never index it.
            await self._mark_deleting(library_id, document_id)
            return

        seen: list[Record] = []

        def start(doc: Record) -> bool:
            seen[:] = [doc]
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False  # deleted, superseded, failed or already committed
            doc.update(status="processing", updatedAt=_now())
            return True

        document = await self._mutate(library_id, document_id, start)
        if document is None:
            await self._not_pending(library_id, document_id, generation, seen[0] if seen else None)
            return

        # A replacement's file waits in pendingFile; a first upload's is the record's own (spec §4.2).
        source = document.get("pendingFile") or document
        data = await self.blobs.download(source["blobPath"])
        extraction_started = time.perf_counter()
        result = await extract(data, filename=source["fileName"], content_type=source["contentType"], ocr=self.ocr)
        if result.outcome != "ok":
            await self._fail(library_id, document_id, generation, status_detail(result))
            return
        extraction_ms = (time.perf_counter() - extraction_started) * 1000
        chunking_started = time.perf_counter()
        kind = "email" if source.get("fileType") == "eml" else "document"
        pieces = await asyncio.to_thread(self.chunker.chunk, result.sections, kind)  # CPU-bound tokenizing
        if not pieces:
            await self._fail(library_id, document_id, generation, "no_text")
            return
        if len(pieces) > self.max_chunks_per_document:
            # Checked before embedding, so refusing costs nothing (spec §7.2 step 4).
            await self._fail(library_id, document_id, generation, "too_many_chunks")
            return

        chunking_ms = (time.perf_counter() - chunking_started) * 1000

        # Proposals come before embedding, so accepted ones are written with the chunks (spec §7.2 step 6).
        proposals = await self._propose_metadata(library, document_id, source["fileName"], pieces)
        accept = library.get("autoAcceptTagProposals") is True
        meta = {name: document.get(name) for name in ("tags", "periodStart", "periodEnd")}
        meta["author"] = document.get("author") or result.author
        if proposals is not None:
            settle_proposals(meta, proposals, accept=accept)
        else:
            meta["facets"] = document.get("facets", [])

        embedding_started = time.perf_counter()
        with measure_embeddings() as embedding_usage:
            vectors = await self.embedder.embed([p.text for p in pieces])
        embedding_ms = (time.perf_counter() - embedding_started) * 1000
        # authoredAt only ever comes from the file (spec §4.2), so each generation takes its own file's.
        authored_at = result.authored_at.isoformat() if result.authored_at else None
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
                "fileName": source["fileName"],
                "fileType": source["fileType"],
                "uploadedAt": source["uploadedAt"],
                "authoredAt": authored_at,
                "author": meta["author"],
                "periodStart": meta["periodStart"],
                "periodEnd": meta["periodEnd"],
                "facets": meta["facets"],
                "tokenCount": p.token_count,
                "embedding": vector,
                "embeddingModel": EMBEDDING_MODEL,
                "chunkingVersion": self.chunker.version,
                "createdAt": created,
            }
            for p, vector in zip(pieces, vectors, strict=True)
        ]
        # The worker-wide cap inside the chunk store decides how many of these run at once.
        writes_started = time.perf_counter()
        write_charges = await asyncio.gather(*(self.chunks.upsert(r) for r in records))
        writes_ms = (time.perf_counter() - writes_started) * 1000
        chunk_tokens = [p.token_count for p in pieces]
        processing_metrics = {
            "extractionMs": round(extraction_ms, 1),
            "chunkingMs": round(chunking_ms, 1),
            "embeddingMs": round(embedding_ms, 1),
            "chunkWriteMs": round(writes_ms, 1),
            "chunkCount": len(pieces),
            "chunkTokens": sum(chunk_tokens),
            "chunkTokensMin": min(chunk_tokens),
            "chunkTokensMax": max(chunk_tokens),
            "chunkTokensMean": round(sum(chunk_tokens) / len(pieces), 1),
            "embeddingTokens": embedding_usage.prompt_tokens if embedding_usage.tokens_reported else None,
            "embeddingRequests": embedding_usage.requests,
            "embeddingRetries": embedding_usage.retries,
            "embeddingThrottles": embedding_usage.throttled,
            "embeddingModel": self.embedder.model,
            "embeddingDimensions": self.embedder.dimensions,
            "chunkWriteCharge": round(sum(write_charges), 2) if all(x is not None for x in write_charges) else None,
            "chunkingVersion": self.chunker.version,
            "tokenizer": self.chunker.tokenizer.name,
        }

        # Preserve extraction order before chunk overlap/normalization. Only the generation
        # committed below may be read; an abandoned artifact is removed by the normal purge.
        sections = [{"heading": s.heading, "text": s.text, "location": s.location or {}} for s in result.sections]
        serialized = json.dumps(sections, ensure_ascii=False)
        full_text = {
            "blobPath": text_blob_path(library_id, document_id, generation),
            "tokenCount": await asyncio.to_thread(self.chunker.count, serialized),
            "tokenizer": self.chunker.tokenizer.name,
            "sectionCount": len(sections),
            "generation": generation,
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        }
        await self.blobs.upload(full_text["blobPath"], serialized.encode("utf-8"), "application/json")

        def commit(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False  # deleted or replaced while we wrote: abandon this generation
            doc.update(doc.get("pendingFile") or {})  # the replacement's file becomes the record's own
            doc.update(
                status="ready",
                pendingFile=None,
                statusDetail=None,
                activeGeneration=generation,
                pendingGeneration=None,
                chunkCount=len(records),
                fullText=full_text,
                processingMetrics={**processing_metrics, "totalMs": round((time.perf_counter() - started) * 1000, 1)},
                description=proposals.description if proposals is not None else None,
                author=doc.get("author") or result.author,
                authoredAt=authored_at,
                extraction={
                    "method": str(result.method),
                    "ocrApplied": result.ocr_applied,
                    "pageCount": result.page_count,
                    "extractionVersion": result.extraction_version,
                    "chunkingVersion": self.chunker.version,
                },
                updatedAt=_now(),
            )
            if proposals is not None:
                # On the record as it is now: a PATCH made meanwhile is kept, and its retag follows.
                settle_proposals(doc, proposals, accept=accept)
            return True

        committed = await self._mutate(library_id, document_id, commit)
        if committed is None or generation > 1:
            await self._purge_later(library_id, document_id)  # abandoned chunks, or older generations
        if committed is not None:
            record_metric(
                "ingest",
                library_id,
                documentId=document_id,
                generation=generation,
                **{k: v for k, v in committed["processingMetrics"].items()},
            )
            await self.refresh_library(library_id)

    async def _propose_metadata(
        self, library: Record, document_id: str, file_name: str, pieces: list[Any]
    ) -> DocumentProposals | None:
        """One chat call for tags, period and author. ``None`` if proposals are off or the call failed.

        A failed call never fails the document: proposals are an enhancement. Only counts and
        token usage are logged, never the text sent or received (CLAUDE.md rule 12).
        """
        if self.proposer is None:
            return None
        ids = {"libraryId": library["id"], "documentId": document_id}
        started = time.monotonic()
        try:
            messages = document_messages(file_name, opening_text(pieces), prompt_vocabulary(library))
            reply = await self.proposer.complete_json(messages)
            proposals = parse_document_proposals(reply.data, library)
        except Exception as exc:
            log.warning("proposals failed", extra={**ids, "reason": type(exc).__name__})
            return None
        log.info(
            "proposals made",
            extra={
                **ids,
                "tags": len(proposals.tags),
                "period": proposals.period is not None,
                "author": proposals.author is not None,
                "promptTokens": reply.prompt_tokens,
                "completionTokens": reply.completion_tokens,
                "ms": round((time.monotonic() - started) * 1000),
            },
        )
        return proposals

    async def _not_pending(self, library_id: str, document_id: str, generation: int, document: Record | None) -> None:
        """An ingest whose generation is not pending: decide what, if anything, is left to do.

        Messages are delivered at least once, and a delivery may be the retry of one that
        committed and then failed to queue its purge (review finding 1). So the purge is queued
        again: it is idempotent, and deletes only what is obsolete.
        """
        if document is None or document.get("status") == "deleting":
            return  # gone, or its deletion's purge removes everything
        if latest_generation(document) < generation:
            await self._stale(GenerationNotVisible(document_id), library_id, document_id)
        if document.get("activeGeneration") == generation:
            if generation > 1:
                await self._purge_later(library_id, document_id)
            await self.refresh_library(library_id)
        else:
            await self._purge_later(library_id, document_id)  # superseded or failed: its leftovers

    async def fail(self, message: Message, reason: str) -> None:
        """Called by the runtime on the last delivery, before it dead-letters the message."""
        ids = {k: message.get(k) for k in ("libraryId", "documentId")}
        if message.get("type") in ("purgeDocument", "purgeLibrary"):
            # The record stays hidden as ``deleting``; repeating the DELETE queues the purge again.
            log.error("purge dead-lettered", extra={**ids, "type": message["type"], "reason": reason})
            return
        if message.get("type") == "retag":
            # Filters keep matching the chunks' previous metadata; repeating the PATCH queues it again.
            log.error("retag dead-lettered", extra={**ids, "reason": reason})
            return
        if message.get("type") == "proposeDescription":
            # The previous proposal, if any, stays; the next refresh or a POST …/description:propose retries.
            log.error("proposeDescription dead-lettered", extra={**ids, "reason": reason})
            return
        if message.get("type") != "ingest":
            return
        library_id, document_id, generation = message["libraryId"], message["documentId"], message["generation"]
        await self._fail(library_id, document_id, generation, reason)

    async def _fail(self, library_id: str, document_id: str, generation: int, detail: str) -> None:
        def mark(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False
            # activeGeneration is untouched: a failed replacement keeps the previous version searchable.
            doc.update(status="failed", statusDetail=detail, pendingGeneration=None, pendingFile=None, updatedAt=_now())
            return True

        if await self._mutate(library_id, document_id, mark) is not None:
            log.info("document failed", extra={"libraryId": library_id, "documentId": document_id, "detail": detail})
            await self._purge_later(library_id, document_id)  # anything this generation half-wrote

    # -- metadata -----------------------------------------------------------------------------

    async def retag(self, library_id: str, document_id: str, version: int) -> None:
        """Copy the document's filterable metadata to its chunks (spec §7.7).

        Only the active and pending generations: older ones are never searchable and await their
        purge. Session order puts this after any ingest queued before the PATCH, and an ingest
        queued after it copies the new metadata itself.
        """
        document = await self.documents.get(library_id, document_id)
        if document is None or document.get("status") == "deleting":
            return
        current = document.get("metadataVersion") or 0
        if current < version:
            await self._stale(MetadataNotVisible(document_id), library_id, document_id)
        if current > version:
            return  # a later PATCH queued its own retag, which copies everything
        keep = {document.get("activeGeneration"), document.get("pendingGeneration")} - {None}
        targets = [item_id for item_id, gen in await self.chunks.generations(library_id, document_id) if gen in keep]
        fields = {name: document.get(name) for name in RETAG_FIELDS}
        fields["facets"] = fields["facets"] or []
        charges = await asyncio.gather(*(self.chunks.patch(library_id, document_id, i, fields) for i in targets))
        log.info(
            "retag done",
            extra={
                "libraryId": library_id,
                "documentId": document_id,
                "metadataVersion": version,
                "chunks": len(targets),
                "requestCharge": round(sum(charges), 2),
            },
        )
        await self.refresh_library(library_id)

    # -- library description ------------------------------------------------------------------

    async def propose_description(self, library_id: str, *, requested: bool = False) -> None:
        """Propose a library description from a sample of its documents (spec §9.1).

        ``requested`` marks a ``POST …/description:propose``, which always regenerates. A message
        queued by a refresh checks again whether one is due: a burst of uploads queues one per
        document, and only the first, run after them all, makes the call.
        """
        library = await self.libraries.get(library_id)
        if library is None or library.get("status") == "deleting":
            return
        if self.proposer is None:
            log.info("proposals off; description not proposed", extra={"libraryId": library_id})
            return
        documents = searchable(await self.documents.list(library_id, status=None, facet=None))
        if not documents or not (requested or description_due(library, len(documents))):
            return
        samples = []
        for document in sample_documents(documents):
            opening = await self.chunks.opening(library_id, document["id"], document["activeGeneration"])
            samples.append((document.get("fileName") or "", (opening or "")[:OPENING_CHARS]))
        started = time.monotonic()
        reply = await self.proposer.complete_json(description_messages(library, samples))
        proposal = {
            "text": parse_description(reply.data),  # raises on nothing usable: the message is retried
            "generatedAt": _now(),
            "basedOnDocumentCount": len(documents),
        }
        for _ in range(MUTATE_ATTEMPTS):
            library = await self.libraries.get(library_id)
            if library is None or library.get("status") == "deleting":
                return
            library.update(descriptionProposal=proposal, updatedAt=_now())  # never description itself
            try:
                await self.libraries.replace(library)
                break
            except Conflict:
                continue
        else:
            raise Conflict(library_id)
        log.info(
            "description proposed",
            extra={
                "libraryId": library_id,
                "documents": len(documents),
                "sampled": len(samples),
                "promptTokens": reply.prompt_tokens,
                "completionTokens": reply.completion_tokens,
                "ms": round((time.monotonic() - started) * 1000),
            },
        )

    # -- purges -------------------------------------------------------------------------------

    async def purge_document(self, library_id: str, document_id: str, *, deleted: bool = False) -> None:
        """Remove what search must never return again.

        ``deleted`` marks a purge queued by a deletion. If the record does not read as
        ``deleting`` yet, the read is stale (E28): retry rather than tidy generations only.
        """
        document = await self.documents.get(library_id, document_id)
        if deleted and document is not None and document.get("status") != "deleting":
            await self._stale(NotYetDeleting(document_id), library_id, document_id)
        existing = await self.chunks.generations(library_id, document_id)
        if document is None or document.get("status") == "deleting":
            for item_id, _ in existing:
                await self.chunks.delete(library_id, document_id, item_id)
            await self.blobs.delete_prefix(f"{library_id}/{document_id}/")
            await self.documents.delete(library_id, document_id)
            if document is not None:
                await self.refresh_library(library_id)
            return
        doomed = [item_id for item_id, gen in existing if is_obsolete(gen, document)]
        await asyncio.gather(*(self.chunks.delete(library_id, document_id, i) for i in doomed))
        # Each generation's original and images have their own paths; only the active ones are
        # needed to rebuild (E16) or to hand back.
        for prefix in (
            original_prefix(library_id, document_id),
            image_prefix(library_id, document_id),
            text_prefix(library_id, document_id),
        ):
            for path in await self.blobs.list_prefix(prefix):
                if is_obsolete(blob_generation(path), document):
                    await self.blobs.delete(path)

    async def purge_library(self, library_id: str) -> None:
        library = await self.libraries.get(library_id)
        if library is not None and library.get("status") != "deleting":
            await self._stale(NotYetDeleting(library_id), library_id)  # only the API's DELETE queues this message
        for document in await self.documents.list(library_id, status=None, facet=None):
            if document.get("status") != "deleting":
                await self._mark_deleting(library_id, document["id"], enqueue=False)
            await self.purge_document(library_id, document["id"])
        await self.blobs.delete_prefix(f"{library_id}/")
        await self.libraries.delete(library_id)

    # -- library summary ------------------------------------------------------------------------

    async def refresh_library(self, library_id: str) -> None:
        """Recompute ``stats``, ``tagVocabulary`` and ``filterValues`` from the documents (spec §7.2 step 11).

        Then queue ``proposeDescription`` if the library changed enough since its last proposal.
        """
        ready = searchable(await self.documents.list(library_id, status=None, facet=None))
        summary = {"tagVocabulary": tag_vocabulary(ready), "filterValues": filter_values(ready)}
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
            library.update(stats=stats, **summary, updatedAt=_now())
            try:
                await self.libraries.replace(library)
                break
            except Conflict:
                continue
        else:
            raise Conflict(library_id)
        if self.proposer is not None and description_due(library, len(ready)):
            message = {"type": "proposeDescription", "libraryId": library_id}
            await self.queue.send(message, session_id=library_id)

    # -- helpers --------------------------------------------------------------------------------

    async def _stale(self, error: StaleRead, library_id: str, document_id: str | None = None) -> None:
        log.warning(
            "stale read; message retried",
            extra={"libraryId": library_id, "documentId": document_id, "reason": type(error).__name__},
        )
        await asyncio.sleep(self.stale_read_pause)  # give the replica time before the redelivery
        raise error

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
            await self._purge_later(library_id, document_id, deleted=True)

    async def _purge_later(self, library_id: str, document_id: str, *, deleted: bool = False) -> None:
        message: dict[str, Any] = {"type": "purgeDocument", "libraryId": library_id, "documentId": document_id}
        if deleted:
            message["deleted"] = True
        await self.queue.send(message, session_id=library_id)
