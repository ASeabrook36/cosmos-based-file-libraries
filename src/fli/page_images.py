"""Read stored source images and their ingested captions from identified PDF pages."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from fli.errors import ApiError
from fli.image_assets import load_assets
from fli.metrics import record as record_metric
from fli.permissions import check_search_allowed
from fli.search import QueryCost

MAX_PAGES = 3
MAX_FIGURES = 10


async def read_page_images(searcher, blobs, library_id, agent_id, document_id, generation, pages):
    started, cost = time.perf_counter(), QueryCost()
    library = await searcher.libraries.get(library_id, raw_response_hook=cost)
    check_search_allowed(library_id, library, agent_id)
    doc = await searcher.documents.get(library_id, document_id, raw_response_hook=cost)
    if doc is None or doc.get("status") == "deleting":
        raise ApiError(404, "document_not_found", "the requested file does not exist in this library")
    active = doc.get("activeGeneration")
    if active is None:
        raise ApiError(409, "document_not_ready", "the requested file has no active generation")
    if generation is not None and generation != active:
        raise ApiError(409, "document_changed", "the requested generation is no longer active")
    if (
        not 1 <= len(pages) <= MAX_PAGES
        or len(set(pages)) != len(pages)
        or any(type(p) is not int or p < 1 for p in pages)
    ):
        raise ApiError(400, "invalid_request", "select 1–3 distinct positive page numbers")
    if doc.get("fileType") != "pdf":
        raise ApiError(400, "page_images_unsupported", "page-image lookup currently supports PDF files")
    if max(pages) > ((doc.get("extraction") or {}).get("pageCount") or 0):
        raise ApiError(400, "invalid_page", "a requested page is outside the PDF")
    try:
        manifest = await load_assets(blobs, library_id, doc)
    except Exception as exc:
        raise ApiError(
            409, "document_images_unavailable", "stored image/caption assets are unavailable; reprocess this document"
        ) from exc
    full = doc.get("fullText") or {}
    try:
        raw = await blobs.download(full["blobPath"])
        if hashlib.sha256(raw).hexdigest() != full["sha256"]:
            raise ValueError("invalid extracted text digest")
        sections = await asyncio.to_thread(json.loads, raw)
        if not isinstance(sections, list) or len(sections) != full["sectionCount"]:
            raise ValueError("incomplete extracted text")
        page_sections = [
            s
            for s in sections
            if (s.get("location") or {}).get("page") in pages and s.get("contentKind", "text") != "image"
        ]
    except Exception as exc:
        raise ApiError(409, "document_text_unavailable", "stored page text is unavailable; reprocess") from exc
    selected = [a for a in manifest["assets"] if a.get("location", {}).get("page") in pages]
    omitted = sum(
        count for page, count in manifest.get("skippedPages", {}).items() if int(page) in pages or int(page) == 0
    )
    omitted += max(0, len(selected) - MAX_FIGURES)
    results = []
    for asset in selected[:MAX_FIGURES]:
        results.append(
            {
                "imageId": asset["imageId"],
                "documentId": document_id,
                "generation": active,
                "fileName": doc["fileName"],
                "contentKind": "image",
                "location": asset["location"],
                "heading": asset.get("heading"),
                "text": "Stored source figure and source-caption metadata from ingestion.",
                "image": await searcher.image(library_id, document_id, active, asset),
            }
        )
    current = await searcher.documents.get(library_id, document_id, raw_response_hook=cost)
    if (
        current is None
        or current.get("status") == "deleting"
        or current.get("activeGeneration") != active
        or current.get("metadataVersion", 0) != doc.get("metadataVersion", 0)
        or current.get("imageAssets") != doc.get("imageAssets")
    ):
        raise ApiError(409, "document_changed", "the file changed during figure retrieval; retrieve again")
    check_search_allowed(library_id, await searcher.libraries.get(library_id, raw_response_hook=cost), agent_id)
    usage = {
        "requestCharge": round(cost.request_charge, 2),
        "totalMs": round((time.perf_counter() - started) * 1000, 1),
    }
    record_metric("images", library_id, agentId=agent_id, resultCount=len(results), **usage)
    notes = [
        "Source figures and captions were paired and stored during ingestion; this read "
        "performs no layout or model calls.",
        "These are page candidates, not automatically selected answers. Headings are section context, not captions.",
        "A null caption means none was identified during ingestion. Adjacent-inferred associations are labelled.",
    ]
    if omitted:
        notes.append(f"{omitted} source figures on the selected pages were omitted by ingestion or request limits.")
    return {
        "library": {"libraryId": library_id, "name": library["name"]},
        "documentId": document_id,
        "generation": active,
        "fileName": doc["fileName"],
        "pages": sorted(pages),
        "scope": "stored_pdf_page_images",
        "complete": not omitted,
        "pageSections": page_sections,
        "results": results,
        "imagesSkipped": omitted,
        "notes": notes,
        "usage": usage,
    }
