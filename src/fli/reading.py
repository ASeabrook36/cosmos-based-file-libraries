"""Document discovery and complete reads, scoped to a platform-bound library.

Reading is deliberately not a vector query. It returns all extracted sections of the
active generation or no content at all, with an explicit reason and cost estimate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

from fli.blobs import BlobStore
from fli.chunking import default_tokenizer
from fli.errors import ApiError
from fli.ids import chunk_id
from fli.metrics import record as record_metric
from fli.permissions import check_search_allowed
from fli.query import RenderedQuery, SearchFilters, metadata_clauses
from fli.search import QueryCost, Searcher
from fli.store import Record

log = logging.getLogger("fli.reading")
MAX_READ_TOKENS = 200_000
DEFAULT_READ_TOKENS = 32_000
CATALOG_FIELDS = (
    "id",
    "fileName",
    "fileType",
    "sizeBytes",
    "status",
    "statusDetail",
    "activeGeneration",
    "author",
    "authoredAt",
    "uploadedAt",
    "periodStart",
    "periodEnd",
    "tags",
    "description",
    "fullText",
    "chunkCount",
    "imageCount",
    "imagesSkipped",
    "sourceImageCount",
    "sourceImagesSkipped",
    "extraction",
    "metadataVersion",
    "imageStatus",
    "imagePendingCount",
    "contentRevision",
)


def render_catalog(
    library_id: str, filters: SearchFilters, names: list[str], contains: str | None, after: str | None, limit: int
) -> RenderedQuery:
    if not 1 <= limit <= 100:
        raise ValueError("catalogue limit must be 1–100")
    params = [{"name": "@libraryId", "value": library_id}]
    where = ["c.libraryId = @libraryId", "c.status != 'deleting'"]
    where.extend(metadata_clauses(filters, params))
    if names:
        clauses = []
        for i, name in enumerate(names):
            clauses.append(f"STRINGEQUALS(c.fileName, @name{i}, true)")
            params.append({"name": f"@name{i}", "value": name})
        where.append(f"({' OR '.join(clauses)})")
    if contains:
        where.append("CONTAINS(c.fileName, @contains, true)")
        params.append({"name": "@contains", "value": contains})
    if after:
        where.append("c.id > @after")
        params.append({"name": "@after", "value": after})
    fields = ", ".join(f"c.{f}" for f in CATALOG_FIELDS)
    return RenderedQuery(f"SELECT TOP {limit + 1} {fields} FROM c WHERE {' AND '.join(where)} ORDER BY c.id", params)


def manifest(doc: Record) -> Record:
    full = doc.get("fullText") or {}
    active = doc.get("activeGeneration")
    return {
        "documentId": doc["id"],
        "fileName": doc.get("fileName"),
        "generation": active,
        "fileType": doc.get("fileType"),
        "status": doc.get("status"),
        "statusDetail": doc.get("statusDetail"),
        "readable": active is not None and doc.get("status") != "deleting",
        "author": doc.get("author"),
        "authoredAt": doc.get("authoredAt"),
        "uploadedAt": doc.get("uploadedAt"),
        "periodStart": doc.get("periodStart"),
        "periodEnd": doc.get("periodEnd"),
        "tags": doc.get("tags") or [],
        "description": doc.get("description"),
        "descriptionSource": "generated-from-opening" if doc.get("description") else None,
        "estimatedTokens": full.get("tokenCount"),
        "tokenizer": full.get("tokenizer"),
        "representation": "extracted_sections" if full.get("generation") == active and active else "indexed_chunks",
        "chunkCount": doc.get("chunkCount"),
        "imageCount": doc.get("imageCount", 0),
        "imagesSkipped": doc.get("imagesSkipped", 0),
        "sourceImageCount": doc.get("sourceImageCount", 0),
        "sourceImagesSkipped": doc.get("sourceImagesSkipped", 0),
        "imageStatus": doc.get("imageStatus", "ready" if doc.get("imageCount") else "none"),
        "imagePendingCount": doc.get("imagePendingCount", 0),
        "visualsComplete": (
            doc.get("imageStatus") not in ("queued", "processing", "failed")
            and doc.get("sourceImageCount", 0) <= doc.get("imageCount", 0)
            and not doc.get("sourceImagesSkipped", 0)
            and not doc.get("imagesSkipped", 0)
        ),
        "contentRevision": doc.get("contentRevision", 0),
    }


async def read_documents(
    searcher: Searcher,
    blobs: BlobStore,
    library_id: str,
    agent_id: str | None,
    selections: list[tuple[str, int | None]],
    max_tokens: int,
    include_text: bool,
) -> Record:
    started = time.perf_counter()
    cost = QueryCost()
    library = await searcher.libraries.get(library_id, raw_response_hook=cost)
    check_search_allowed(library_id, library, agent_id)
    tokenizer = await asyncio.to_thread(default_tokenizer)
    records: list[Record] = []
    items: list[Record] = []
    notes = [
        "Complete extracted text and kept visual descriptions; visual inputs require consuming-platform support. "
        "Extraction can omit figures, attachments and other unsupported content; OCR may contain errors.",
        "Token counts use the named tokenizer, not necessarily the answering model's. "
        "More context increases input cost, latency and attention demands; "
        "no fixed dollar or time estimate is implied.",
    ]
    for document_id, expected in selections:
        doc = await searcher.documents.get(library_id, document_id, raw_response_hook=cost)
        if doc is None or doc.get("status") == "deleting":
            raise ApiError(404, "document_not_found", "a requested document does not exist in this library")
        generation = doc.get("activeGeneration")
        if generation is None:
            raise ApiError(409, "document_not_ready", "a requested document has no active text; inspect its status")
        if expected is not None and expected != generation:
            raise ApiError(409, "document_changed", "a requested generation is no longer active; discover again")
        records.append(doc)

    known_tokens = sum(
        max(0, (d.get("fullText") or {}).get("tokenCount", 0) - 64)
        for d in records
        if (d.get("fullText") or {}).get("generation") == d["activeGeneration"]
        and (d.get("fullText") or {}).get("tokenizer") == tokenizer.name
    )
    if include_text and known_tokens > max_tokens:
        # The stored sections alone already exceed the budget. Avoid downloading and
        # tokenizing a large artifact just to refuse it. Unknown legacy sizes contribute
        # zero here, as do counts made with another tokenizer. The per-document margin
        # allows for token boundaries where section JSON joins the response envelope.
        latest = await searcher.libraries.get(library_id, raw_response_hook=cost)
        check_search_allowed(library_id, latest, agent_id)
        response = {
            "library": {"libraryId": library_id, "name": library.get("name")},
            "documents": [{**manifest(d), "complete": False} for d in records],
            "complete": False,
            "disposition": "budget_exceeded",
            "estimatedTokens": known_tokens,
            "estimateScope": "stored_sections_lower_bound",
            "tokenizer": tokenizer.name,
            "maxTokens": max_tokens,
            "notes": notes
            + [
                "Stored section counts already exceed this budget; no text was downloaded. "
                "Per-document tokenizer names identify the stored estimates."
            ],
            "usage": {
                "requestCharge": round(cost.request_charge, 2),
                "totalMs": round((time.perf_counter() - started) * 1000, 1),
            },
            "nextAction": "Inspect or split the selected files, or raise the budget within model capacity. "
            "No file content was returned.",
        }
        log.info(
            "library_read",
            extra={
                "libraryId": library_id,
                "agentId": agent_id,
                "documentCount": len(records),
                "estimatedTokens": known_tokens,
                "contentReturned": False,
                **response["usage"],
            },
        )
        record_metric(
            "read",
            library_id,
            agentId=agent_id,
            disposition="budget_exceeded",
            resultCount=len(records),
            estimatedTokens=known_tokens,
            contentReturned=False,
            **response["usage"],
        )
        return response

    for doc in records:
        document_id, generation = doc["id"], doc["activeGeneration"]
        full = doc.get("fullText") or {}
        if full.get("generation") == generation:
            try:
                data = await blobs.download(full["blobPath"])
                sections = json.loads(data)
            except Exception as exc:
                # Never fall back to a different representation after an artifact read fails.
                raise ApiError(
                    409, "document_text_unavailable", "complete text is unavailable; retry or reprocess"
                ) from exc
            if (
                hashlib.sha256(data).hexdigest() != full.get("sha256")
                or not isinstance(sections, list)
                or len(sections) != full.get("sectionCount")
                or not all(isinstance(s, dict) and isinstance(s.get("text"), str) for s in sections)
            ):
                raise ApiError(409, "document_text_incomplete", "the extracted text artifact is incomplete; reprocess")
            representation = "extracted_sections"
        else:
            # Pre-enhancement documents remain usable. Preserve every indexed passage in
            # order; never claim that stripping overlaps reconstructed the original file.
            count = doc.get("chunkCount") or 0
            if not 1 <= count <= 100_000:
                raise ApiError(409, "document_text_unavailable", "reprocess this document for a complete text artifact")
            chunks = await searcher.chunks.document(library_id, document_id, generation, count + 1, cost)
            chunks.sort(key=lambda c: c["chunkIndex"])
            if len(chunks) != count or [c["chunkIndex"] for c in chunks] != list(range(count)):
                raise ApiError(
                    409, "document_text_incomplete", "not all active passages are available; retry or reprocess"
                )
            sections = [
                {
                    "text": c["text"],
                    "heading": c.get("heading"),
                    "location": c.get("location") or {},
                    "contentKind": c.get("contentKind", "text"),
                    "imageId": chunk_id(document_id, generation, c["chunkIndex"])
                    if c.get("contentKind") == "image"
                    else None,
                    "imageRef": c.get("imageRef"),
                    "imageMeta": c.get("imageMeta"),
                }
                for c in chunks
            ]
            representation = "indexed_chunks"
            notes.append(
                f"{document_id}: legacy indexed text includes repeated headings and overlapping passages. "
                "Reprocess the original to preserve the pre-chunking extraction."
            )
        public_sections = []
        for section in sections:
            public = {k: v for k, v in section.items() if k not in ("imageRef", "imageMeta")}
            public["image"] = await searcher.image(library_id, document_id, generation, section)
            public_sections.append(public)
        item = {**manifest(doc), "representation": representation, "sections": public_sections}
        if doc.get("imageStatus") in ("queued", "processing", "failed"):
            notes.append(
                f"{document_id}: complete extracted text is available, but image enrichment is "
                f"{item['imageStatus']}; this read does not include all retained images."
            )
        if doc.get("sourceImageCount", 0) > doc.get("imageCount", 0):
            notes.append(
                f"{document_id}: this read includes indexed image sections; additional stored source "
                + (
                    "images and captions are available through selected-page image reads."
                    if doc.get("fileType") == "pdf"
                    else "images are outside this read. Direct reads of unindexed Office pictures are not supported; "
                    "reprocess with a higher image-description cap or export to PDF for page-image reads."
                )
            )
        # Count the serialized document, including identifiers, locations and headings.
        item["estimatedTokens"] = len(tokenizer.encode(json.dumps(item, ensure_ascii=False)))
        item["tokenizer"] = tokenizer.name
        if doc.get("imageCount"):
            notes.append(
                f"{document_id}: kept images include searchable descriptions and expiring image links. "
                "The consuming platform chooses whether to supply visual inputs to its model."
            )
        if doc.get("imagesSkipped"):
            notes.append(f"{document_id}: {doc['imagesSkipped']} visuals were skipped by size, format or cap rules.")
        items.append(item)

    # All-or-nothing, including across documents. Do not return an apparently complete
    # batch after a replacement, deletion, or metadata edit during the read.
    for original in records:
        current = await searcher.documents.get(library_id, original["id"], raw_response_hook=cost)
        if (
            current is None
            or current.get("status") == "deleting"
            or current.get("activeGeneration") != original["activeGeneration"]
            or current.get("metadataVersion", 0) != original.get("metadataVersion", 0)
            or current.get("contentRevision", 0) != original.get("contentRevision", 0)
        ):
            raise ApiError(409, "document_changed", "a document changed during the read; discover and retry")
    latest = await searcher.libraries.get(library_id, raw_response_hook=cost)
    check_search_allowed(library_id, latest, agent_id)
    for item in items:
        item["complete"] = True
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    response = {
        "library": {"libraryId": library_id, "name": library.get("name")},
        "documents": items,
        "complete": True,
        "disposition": "complete",
        "estimateScope": "full_response",
        "estimatedTokens": 0,
        "tokenizer": tokenizer.name,
        "maxTokens": max_tokens,
        "notes": notes,
        "usage": {"requestCharge": round(cost.request_charge, 2), "totalMs": elapsed},
        "nextAction": None,
    }
    # Count the complete envelope once off the event loop. A small allowance covers the
    # estimate's own digits and final latency value without repeatedly tokenizing files.
    response["estimatedTokens"] = await asyncio.to_thread(
        lambda: len(tokenizer.encode(json.dumps(response, ensure_ascii=False))) + 64
    )
    tokens = response["estimatedTokens"]
    fits = tokens <= max_tokens
    if not include_text or not fits:
        response.update(
            complete=False,
            disposition="inspected" if not include_text else "budget_exceeded",
            nextAction="Split the set or increase maxTokens within the model's remaining capacity. "
            "If a document cannot fit, explain the limitation; do not claim a full read.",
        )
        for item in items:
            item["complete"] = False
            del item["sections"]
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    response["usage"]["totalMs"] = elapsed
    log.info(
        "library_read",
        extra={
            "libraryId": library_id,
            "agentId": agent_id,
            "documentCount": len(items),
            "estimatedTokens": tokens,
            "contentReturned": include_text and fits,
            "requestCharge": round(cost.request_charge, 2),
            "totalMs": elapsed,
        },
    )
    record_metric(
        "read",
        library_id,
        agentId=agent_id,
        disposition=response["disposition"],
        resultCount=len(items),
        estimatedTokens=tokens,
        contentReturned=include_text and fits,
        **response["usage"],
    )
    return response
