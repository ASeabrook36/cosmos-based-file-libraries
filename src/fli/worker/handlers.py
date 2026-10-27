"""What the worker does with each queue message (spec §7.1, §7.2, §7.8).

Messages for one library arrive one at a time, in order (Service Bus sessions keyed by
``libraryId``); that ordering is what makes the purge rule safe. Different libraries run in
parallel, sharing one cap on chunk writes (``fli.chunks``).

Errors fall into three kinds:

* the file itself is unusable (encrypted, unsupported, unparseable, no text): the document is
  marked ``failed`` with a ``statusDetail`` and the message completes. Retrying cannot help.
* anything else raises. The runtime abandons the message and Service Bus redelivers it; chunk
  ids are deterministic; a frozen plan and create-only writes let retries resume without
  overwriting published evidence.
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
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from fli.blobs import BlobStore
from fli.checkpoints import digest, encoded, freeze, load
from fli.chunking import Chunk, Chunker
from fli.chunks import ChunkStore
from fli.config import EMBEDDING_MODEL
from fli.embedding import Embedder, measure_embeddings
from fli.enums import ExtractionMethod
from fli.extraction import Extracted, OcrClient, OcrError, extract, inspect_pdf
from fli.figures import LayoutReader, extract_pdf_figures
from fli.generation import is_obsolete, latest_generation
from fli.ids import (
    blob_generation,
    chunk_id,
    image_blob_path,
    image_prefix,
    original_prefix,
    text_blob_path,
    text_prefix,
)
from fli.image_assets import store_assets
from fli.images import IMAGE_TYPES, Visual, describe_cached, normalize_visuals, visual_candidates
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


class ImageWorkStopped(Exception):
    """Stop sibling descriptions when the generation expires or a description is oversized."""


def interleave_sections(text_sections, image_sections, file_type):
    """Reading order is independent of immutable chunk indexes; preserve source order on ties."""
    key = {"pdf": "page", "pptx": "slide", "docx": "paragraph"}.get(file_type)
    if key is None:
        return text_sections + image_sections
    positions = [(section.get("location") or {}).get(key) for section in text_sections]
    after = {}
    for image in image_sections:
        position = (image.get("location") or {}).get(key)
        slot = len(text_sections) - 1
        if position is not None and any(p is not None for p in positions):
            slot = -1
            for i, text_position in enumerate(positions):
                if text_position is not None and text_position <= position:
                    slot = i
        after.setdefault(slot, []).append(image)
    merged = list(after.get(-1, []))
    for i, section in enumerate(text_sections):
        merged.append(section)
        merged.extend(after.get(i, []))
    return merged


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
    layout: LayoutReader | None = None
    layout_batch_pages: int = 20
    vision: ChatModel | None = None
    image_min_pixels: int = 16_384
    image_min_bytes: int = 0
    image_max_per_document: int = 50
    image_asset_max_per_document: int = 1000
    vision_concurrency: int = 2

    async def handle(self, message: Message) -> None:
        kind = message.get("type")
        library_id = message.get("libraryId")
        if not isinstance(library_id, str) or not library_id:
            raise BadMessage("libraryId is required")
        if kind in ("ingest", "enrichImages"):
            document_id, generation = message.get("documentId"), message.get("generation")
            if not isinstance(document_id, str) or not isinstance(generation, int) or generation < 1:
                raise BadMessage("ingest needs documentId and a positive generation")
            await self.ingest(library_id, document_id, generation, images=kind == "enrichImages")
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

    async def ingest(self, library_id: str, document_id: str, generation: int, *, images: bool = False) -> None:
        library = await self.libraries.get(library_id)
        if library is None or library.get("status") == "deleting":
            # Uploaded as the library was being deleted: never index it.
            await self._mark_deleting(library_id, document_id, enqueue=False)
            await self._purge_later(library_id, document_id)
            return

        seen: list[Record] = []

        def start(doc: Record) -> bool:
            seen[:] = [doc]
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False  # deleted, superseded, failed or already committed
            if doc.get("processingGeneration") != generation:
                doc.update(processingGeneration=generation, processingStartedAt=_now())
            doc.update(status="processing", updatedAt=_now())
            if images and doc.get("imageStatus") == "queued":
                doc["imageStatus"] = "processing"
            return True

        document = await self._mutate(library_id, document_id, start)
        if document is None:
            await self._not_pending(library_id, document_id, generation, seen[0] if seen else None)
            return

        # A replacement's file waits in pendingFile; a first upload's is the record's own (spec §4.2).
        source = document.get("pendingFile") or document
        stages = {
            "extractionMs": 0,
            "chunkingMs": 0,
            "visualExtractionMs": 0,
            "visionMs": 0,
            "visionPromptTokens": 0,
            "visionCompletionTokens": 0,
            "visionCacheHits": 0,
        }
        plan_path = f"{text_prefix(library_id, document_id)}{generation}/plan.json"
        plan = await load(self.blobs, plan_path)
        plan_hit = plan is not None
        if plan is None:
            preparation_path = f"{text_prefix(library_id, document_id)}{generation}/preparation.json"
            preparation = await load(self.blobs, preparation_path)
            if preparation is None:
                proposed = await self._prepare_plan(
                    library, document, source, library_id, document_id, generation, stages
                )
                if proposed is None:
                    return
                preparation = await freeze(self.blobs, preparation_path, proposed)
            if preparation.get("sourceSha256") != source["sha256"] or preparation.get("generation") != generation:
                raise ValueError("preparation does not match the requested generation")
            stages.update(preparation.get("preparationMetrics") or {})
            if preparation["visuals"] and preparation["pieces"] and not images:
                await self._publish(
                    library_id,
                    document_id,
                    generation,
                    source,
                    preparation,
                    stages,
                    partial=True,
                )
                current = await self.documents.get(library_id, document_id)
                if current and current.get("status") != "deleting" and current.get("pendingGeneration") == generation:
                    await self.queue.send(
                        {
                            "type": "enrichImages",
                            "libraryId": library_id,
                            "documentId": document_id,
                            "generation": generation,
                        },
                        session_id=library_id,
                    )
                return
            proposed = await self._complete_images(
                preparation, library, document, source, library_id, document_id, generation, stages
            )
            if proposed is None:
                return
            plan = await freeze(self.blobs, plan_path, proposed)
        await self._publish(library_id, document_id, generation, source, plan, stages, plan_hit=plan_hit)

    async def _publish(
        self,
        library_id,
        document_id,
        generation,
        source,
        plan,
        stages,
        *,
        partial=False,
        plan_hit=False,
    ):
        current = await self.documents.get(library_id, document_id)
        if not current or current.get("status") == "deleting" or current.get("pendingGeneration") != generation:
            await self._purge_later(library_id, document_id)
            return
        text_published = current.get("activeGeneration") == generation and current.get("contentRevision", 0) >= 1
        if partial and text_published:
            return
        if (
            plan.get("version") != 1
            or plan.get("generation") != generation
            or plan.get("sourceSha256") != source["sha256"]
        ):
            raise ValueError("ingestion plan does not match the requested generation")
        pieces = [Chunk(**piece) for piece in plan["pieces"]]
        image_records = {int(index): value for index, value in plan["imageRecords"].items()}
        proposals = DocumentProposals(**plan["proposals"]) if plan["proposals"] is not None else None
        meta, accept = plan["meta"], plan["accept"]
        if text_published:
            meta = {**meta, **{name: current.get(name) for name in RETAG_FIELDS}}
        authored_at = plan["authoredAt"]

        new_pieces = [p for p in pieces if not (text_published and p.index < plan.get("textChunkCount", 0))]
        embedding_started = time.perf_counter()
        with measure_embeddings() as embedding_usage:
            vectors = await self.embedder.embed([p.text for p in new_pieces]) if new_pieces else []
        embedding_ms = (time.perf_counter() - embedding_started) * 1000
        # authoredAt only ever comes from the file (spec §4.2), so each generation takes its own file's.
        created = _now()
        records = [
            {
                "id": chunk_id(document_id, generation, p.index),
                "libraryId": library_id,
                "documentId": document_id,
                "generation": generation,
                "chunkIndex": p.index,
                "contentKind": "image" if p.index in image_records else "text",
                "text": p.text,
                "heading": p.heading,
                "location": p.location or {},
                "imageRef": image_records.get(p.index, {}).get("imageRef"),
                "imageMeta": image_records.get(p.index, {}).get("imageMeta"),
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
                "chunkingVersion": plan["chunkingVersion"],
                "createdAt": created,
            }
            for p, vector in zip(new_pieces, vectors, strict=True)
        ]
        for record in records:
            record["contentHash"] = digest(
                {
                    key: record[key]
                    for key in (
                        "text",
                        "heading",
                        "location",
                        "imageRef",
                        "imageMeta",
                        "embeddingModel",
                        "chunkingVersion",
                    )
                }
            )
        # The worker-wide cap inside the chunk store decides how many of these run at once.
        writes_started = time.perf_counter()
        write_charges = await asyncio.gather(*(self.chunks.upsert(r) for r in records))
        writes_ms = (time.perf_counter() - writes_started) * 1000
        chunk_tokens = [p.token_count for p in pieces]
        previous = current.get("processingMetrics") or {} if text_published else {}
        processing_metrics = {
            **{key: round(value, 1) for key, value in stages.items()},
            "planCacheHit": plan_hit,
            "imageCount": plan["imageCount"],
            "imagesSkipped": plan["imagesSkipped"],
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

        if text_published:
            for key in (
                "embeddingMs",
                "chunkWriteMs",
                "embeddingTokens",
                "embeddingRequests",
                "embeddingRetries",
                "embeddingThrottles",
                "chunkWriteCharge",
            ):
                if processing_metrics.get(key) is not None and previous.get(key) is not None:
                    processing_metrics[key] += previous[key]
        # Preserve extraction order before chunk overlap/normalization. Only the generation
        # committed below may be read; an abandoned artifact is removed by the normal purge.
        sections = plan["sections"]
        serialized = encoded(sections).decode("utf-8")
        full_text = {
            "blobPath": text_blob_path(library_id, document_id, generation).replace(
                "sections.json", "text-sections.json"
            )
            if partial
            else text_blob_path(library_id, document_id, generation),
            "tokenCount": await asyncio.to_thread(self.chunker.count, serialized),
            "tokenizer": self.chunker.tokenizer.name,
            "sectionCount": len(sections),
            "generation": generation,
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        }
        if not await self.blobs.create(full_text["blobPath"], serialized.encode("utf-8"), "application/json"):
            existing = await self.blobs.download(full_text["blobPath"])
            if hashlib.sha256(existing).hexdigest() != full_text["sha256"]:
                raise ValueError("immutable extraction conflicts with the generation plan; reprocess")

        image_assets = None
        if plan.get("sourceAssets") is not None:
            image_assets = await store_assets(
                self.blobs,
                library_id,
                document_id,
                generation,
                source["sha256"],
                plan["sourceAssets"],
                plan["sourceAssetsSkipped"],
                plan["sourceAssetsSkippedPages"],
            )

        def commit(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False  # deleted or replaced while we wrote: abandon this generation
            already_text = doc.get("activeGeneration") == generation and doc.get("contentRevision", 0) >= 1
            if partial and already_text:
                return False  # a late text publisher cannot downgrade an enriched snapshot
            doc.update(doc.get("pendingFile") or {})  # the replacement's file becomes the record's own
            doc.update(
                status="processing" if partial else "ready",
                pendingFile=None,
                statusDetail=None,
                activeGeneration=generation,
                pendingGeneration=generation if partial else None,
                publishedChunkCount=len(pieces),
                contentRevision=1 if partial else 2,
                imageStatus="queued" if partial else "ready" if plan["imageCount"] else "none",
                imagePendingCount=len(plan.get("visuals") or []) if partial else 0,
                textReadyAt=doc.get("textReadyAt") if already_text else _now(),
                chunkCount=len(pieces),
                imageCount=plan["imageCount"],
                imagesSkipped=plan["imagesSkipped"],
                fullText=full_text,
                imageAssets=image_assets,
                sourceImageCount=(image_assets or {}).get("assetCount", 0),
                sourceImagesSkipped=(image_assets or {}).get("skipped", 0),
                processingMetrics={
                    **processing_metrics,
                    "totalMs": round(
                        (datetime.now(UTC) - datetime.fromisoformat(doc["processingStartedAt"])).total_seconds() * 1000,
                        1,
                    ),
                },
                description=proposals.description if proposals is not None else None,
                author=doc.get("author") or plan["author"],
                authoredAt=authored_at,
                extraction={**plan["extraction"], "chunkingVersion": plan["chunkingVersion"]},
                updatedAt=_now(),
            )
            if proposals is not None and not already_text:
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

    async def _prepare_plan(
        self,
        library: Record,
        document: Record,
        source: Record,
        library_id: str,
        document_id: str,
        generation: int,
        stages: dict,
    ) -> dict | None:
        data = await self.blobs.download(source["blobPath"])
        extraction_started = time.perf_counter()
        standalone = source["fileName"].rsplit(".", 1)[-1].lower() in IMAGE_TYPES
        is_pdf = source["fileName"].lower().endswith(".pdf")
        result = (
            await asyncio.to_thread(inspect_pdf, data)
            if is_pdf
            else Extracted("ok", method=ExtractionMethod.VISION_DESCRIPTION)
            if standalone
            else await extract(data, filename=source["fileName"], content_type=source["contentType"], ocr=self.ocr)
        )
        if result.outcome != "ok":
            await self._fail(library_id, document_id, generation, status_detail(result))
            return
        visuals, images_skipped = None, 0
        asset_skipped_pages = {}
        if is_pdf:
            if self.layout is None:
                await self._fail(library_id, document_id, generation, "layout_unavailable")
                return
            try:
                figures = await extract_pdf_figures(
                    data,
                    result.page_count or 0,
                    self.layout,
                    self.blobs,
                    f"{text_prefix(library_id, document_id)}{generation}/",
                    f"{image_prefix(library_id, document_id)}{generation}/",
                    cap=self.image_asset_max_per_document,
                    load_pixels=False,
                    min_pixels=self.image_min_pixels,
                    min_bytes=self.image_min_bytes,
                    batch_pages=self.layout_batch_pages,
                )
            except OcrError as exc:
                if exc.transient:
                    raise
                await self._fail(library_id, document_id, generation, exc.code)
                return
            result.sections, result.method = figures.sections, ExtractionMethod.DOCUMENT_INTELLIGENCE_LAYOUT
            result.ocr_applied = True
            result.extraction_version = "layout-source-assets-v1"
            visuals, images_skipped = figures.visuals, figures.skipped
            asset_skipped_pages = figures.skipped_pages
            stages.update(
                layoutPages=figures.page_count,
                layoutBatchesAnalyzed=figures.batches_analyzed,
                layoutBatchesReused=figures.batches_reused,
            )
        stages["extractionMs"] = (time.perf_counter() - extraction_started) * 1000
        chunking_started = time.perf_counter()
        kind = "email" if source.get("fileType") == "eml" else "document"
        pieces = await asyncio.to_thread(self.chunker.chunk, result.sections, kind)  # CPU-bound tokenizing
        stages["chunkingMs"] = (time.perf_counter() - chunking_started) * 1000
        visual_started = time.perf_counter()
        if visuals is None:
            visuals, images_skipped = await self._native_assets(
                data, source["fileName"], library_id, document_id, generation
            )
        all_visuals = visuals
        asset_skipped = images_skipped
        visuals = all_visuals[: self.image_max_per_document]
        images_skipped += len(all_visuals) - len(visuals)
        stages["visualExtractionMs"] = (time.perf_counter() - visual_started) * 1000
        if len(pieces) + len(visuals) > self.max_chunks_per_document:
            await self._fail(library_id, document_id, generation, "too_many_chunks")
            return
        if not pieces and not visuals:
            await self._fail(library_id, document_id, generation, "no_usable_images" if standalone else "no_text")
            return
        visual_records = []
        for number, visual in enumerate(all_visuals):
            path = visual.blob_path or image_blob_path(
                library_id, document_id, generation, f"image-{number}-{hashlib.sha256(visual.data).hexdigest()}", "png"
            )
            if not visual.blob_path:
                await self.blobs.create(path, visual.data, "image/png")
            visual_records.append(
                {
                    "blob_path": path,
                    **{
                        name: getattr(visual, name)
                        for name in (
                            "heading",
                            "location",
                            "context",
                            "kind",
                            "width",
                            "height",
                            "caption",
                            "caption_association",
                            "caption_location",
                            "regions",
                        )
                    },
                }
            )
        # Proposals come before embedding, so accepted ones are written with the chunks (spec §7.2 step 6).
        proposals = await self._propose_metadata(library, document_id, source["fileName"], pieces) if pieces else None
        accept = library.get("autoAcceptTagProposals") is True
        meta = {name: document.get(name) for name in ("tags", "periodStart", "periodEnd")}
        meta["author"] = document.get("author") or result.author
        if proposals is not None:
            settle_proposals(meta, proposals, accept=accept)
        else:
            meta["facets"] = document.get("facets", [])

        sections = [
            {"heading": sec.heading, "text": sec.text, "location": sec.location or {}} for sec in result.sections
        ]
        return {
            "version": 1,
            "generation": generation,
            "sourceSha256": source["sha256"],
            "pieces": [asdict(piece) for piece in pieces],
            "imageRecords": {},
            "sections": sections,
            "meta": meta,
            "accept": accept,
            "proposals": asdict(proposals) if proposals is not None else None,
            "author": result.author,
            "authoredAt": result.authored_at.isoformat() if result.authored_at else None,
            "imageCount": 0,
            "visuals": visual_records[: self.image_max_per_document],
            "sourceAssets": [
                {
                    "imageId": chunk_id(document_id, generation, len(pieces) + i)
                    if i < self.image_max_per_document
                    else f"{document_id}:{generation}:asset-{i}",
                    "contentKind": "image",
                    "imageRef": v["blob_path"],
                    "imageMeta": all_visuals[i].meta,
                    "heading": v["heading"],
                    "location": v["location"],
                }
                for i, v in enumerate(visual_records)
            ]
            if is_pdf or source.get("fileType") in ("docx", "pptx") or standalone
            else None,
            "sourceAssetsSkipped": asset_skipped,
            "sourceAssetsSkippedPages": {str(page): count for page, count in asset_skipped_pages.items()},
            "textChunkCount": len(pieces),
            "preparationMetrics": stages.copy(),
            "imagesSkipped": images_skipped,
            "chunkingVersion": self.chunker.version,
            "extraction": {
                "method": str(result.method),
                "ocrApplied": result.ocr_applied,
                "pageCount": result.page_count,
                "extractionVersion": result.extraction_version,
            },
        }

    async def _native_assets(self, data, filename, library_id, document_id, generation):
        # Normalize and persist one image at a time; retain metadata rather than all PNG bytes.
        candidates = visual_candidates(data, filename)
        assets, skipped = [], 0
        while True:
            visual = await asyncio.to_thread(next, candidates, None)
            if visual is None:
                break
            if len(assets) >= self.image_asset_max_per_document:
                skipped += 1
                continue
            kept, rejected = await asyncio.to_thread(
                normalize_visuals, [visual], min_pixels=self.image_min_pixels, min_bytes=self.image_min_bytes, cap=1
            )
            skipped += rejected
            if not kept:
                continue
            visual.blob_path = image_blob_path(
                library_id,
                document_id,
                generation,
                f"source-{len(assets)}-{hashlib.sha256(visual.data).hexdigest()}",
                "png",
            )
            await self.blobs.create(visual.blob_path, visual.data, "image/png")
            visual.data = b""
            assets.append(visual)
        return assets, skipped

    async def _complete_images(
        self, preparation, library, document, source, library_id, document_id, generation, stages
    ):
        pieces = [Chunk(**p) for p in preparation["pieces"]]
        visuals = preparation["visuals"]
        if visuals and self.vision is None:
            await self._fail(library_id, document_id, generation, "vision_unavailable")
            return
        image_records = {}
        image_sections = []
        vision_started = time.perf_counter()
        slots = asyncio.Semaphore(self.vision_concurrency)

        async def describe_one(stored):
            # Bound downloads as well as HTTP calls. The model retains its shared
            # worker-wide governor, so multiple libraries cannot multiply HTTP quota.
            async with slots:
                current = await self.documents.get(library_id, document_id)
                if not current or current.get("status") == "deleting" or current.get("pendingGeneration") != generation:
                    raise ImageWorkStopped("superseded")
                visual = Visual(await self.blobs.download(stored["blob_path"]), **stored)
                try:
                    text = await describe_cached(
                        self.vision,
                        visual,
                        source["fileName"],
                        self.blobs,
                        f"{text_prefix(library_id, document_id)}{generation}/",
                    )
                    tokens = self.chunker.count(text)
                    if tokens > 8_000:
                        raise ImageWorkStopped("image_description_too_long")
                    return visual, text, tokens
                finally:
                    visual.data = b""

        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(describe_one(stored)) for stored in visuals]
        except ExceptionGroup as errors:
            stopped = [e for e in errors.exceptions if isinstance(e, ImageWorkStopped)]
            other = [e for e in errors.exceptions if not isinstance(e, ImageWorkStopped)]
            if other:
                if len(other) == 1:
                    raise other[0] from None
                raise
            if any(str(e) == "image_description_too_long" for e in stopped):
                await self._fail(library_id, document_id, generation, "image_description_too_long")
            else:
                await self._purge_later(library_id, document_id)
            return

        described = [task.result() for task in tasks]
        for i, (visual, text, tokens) in enumerate(described):
            index = len(preparation["pieces"]) + i
            stored = visuals[i]
            image_records[index] = {"imageRef": stored["blob_path"], "imageMeta": visual.meta}
            pieces.append(Chunk(index, text, tokens, visual.heading, visual.location))
            image_sections.append(
                {
                    "text": text,
                    "heading": visual.heading,
                    "location": visual.location,
                    "contentKind": "image",
                    "imageId": chunk_id(document_id, generation, index),
                    **image_records[index],
                }
            )

        stages.update(
            visionMs=(time.perf_counter() - vision_started) * 1000,
            visionPromptTokens=sum(v.prompt_tokens for v, _, _ in described),
            visionCompletionTokens=sum(v.completion_tokens for v, _, _ in described),
            visionCacheHits=sum(v.cache_hit for v, _, _ in described),
        )
        plan = {
            **preparation,
            "pieces": [asdict(p) for p in pieces],
            "imageRecords": image_records,
            "sections": interleave_sections(preparation["sections"], image_sections, source.get("fileType")),
            "imageCount": len(visuals),
        }
        if not preparation["textChunkCount"] and visuals:
            proposals = await self._propose_metadata(library, document_id, source["fileName"], pieces)
            plan["proposals"] = asdict(proposals) if proposals else None
            if proposals:
                settle_proposals(plan["meta"], proposals, accept=plan["accept"])
        return plan

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
            await self._purge_later(library_id, document_id)  # also retry cleanup of any late writer
            return
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
        if message.get("type") not in ("ingest", "enrichImages"):
            return
        library_id, document_id, generation = message["libraryId"], message["documentId"], message["generation"]
        await self._fail(library_id, document_id, generation, reason)

    async def _fail(self, library_id: str, document_id: str, generation: int, detail: str) -> None:
        def mark(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc.get("pendingGeneration") != generation:
                return False
            # activeGeneration is untouched: a failed replacement keeps the previous version searchable.
            text_available = doc.get("activeGeneration") == generation and doc.get("contentRevision") == 1
            doc.update(
                status="ready" if text_available else "failed",
                statusDetail=detail,
                pendingGeneration=None,
                pendingFile=None,
                updatedAt=_now(),
            )
            if text_available:
                doc["imageStatus"] = "failed"
            return True

        if await self._mutate(library_id, document_id, mark) is not None:
            log.info("document failed", extra={"libraryId": library_id, "documentId": document_id, "detail": detail})
            await self._purge_later(library_id, document_id)  # anything this generation half-wrote
            await self.refresh_library(library_id)

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
            "imagesSkipped": sum(d.get("imagesSkipped", 0) for d in ready),
            "imagesPending": sum(
                d.get("imagePendingCount", 0) for d in ready if d.get("imageStatus") in ("queued", "processing")
            ),
            "imageProcessingDocuments": sum(d.get("imageStatus") in ("queued", "processing") for d in ready),
            "imageFailedDocuments": sum(d.get("imageStatus") == "failed" for d in ready),
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
