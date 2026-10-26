"""Library inventory and a bounded, content-free retrieval/ingestion history."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request

from fli.auth import require_caller
from fli.metrics import read_events, summarize
from fli.permissions import check_search_allowed

router = APIRouter(prefix="/v1/libraries/{library_id}", dependencies=[Depends(require_caller)])


@router.get("/metrics")
async def library_metrics(
    library_id: str, request: Request, x_agent_id: Annotated[str | None, Header()] = None
) -> dict[str, Any]:
    library = await request.app.state.libraries.get(library_id)
    check_search_allowed(library_id, library, x_agent_id)
    docs = await request.app.state.documents.list(library_id, status=None, facet=None)
    docs = [d for d in docs if d.get("status") != "deleting"]
    active = [d for d in docs if d.get("activeGeneration") is not None]
    rows = []
    for doc in docs:
        measured = doc.get("processingMetrics") or {}
        rows.append(
            {
                "documentId": doc["id"],
                "fileName": doc.get("fileName"),
                "status": doc["status"],
                "generation": doc.get("activeGeneration"),
                "chunkCount": doc.get("chunkCount", 0),
                "extractedTextTokens": (doc.get("fullText") or {}).get("tokenCount"),
                "chunkingVersion": (doc.get("extraction") or {}).get("chunkingVersion"),
                "processingMetrics": measured or None,
            }
        )
    try:
        history = await asyncio.to_thread(read_events, library_id)
    except (OSError, ValueError):
        history = {"enabled": False, "unavailable": True, "events": [], "bounded": False}
    latest = await request.app.state.libraries.get(library_id)
    check_search_allowed(library_id, latest, x_agent_id)
    return {
        "library": {"libraryId": library_id, "name": library["name"]},
        "inventory": {
            "documents": len(docs),
            "activeDocuments": len(active),
            "chunks": sum(d.get("chunkCount", 0) for d in active),
            "images": sum(d.get("imageCount", 0) for d in active),
            "embeddingModel": library.get("embeddingModel"),
            "embeddingDimensions": library.get("embeddingDimensions"),
            "measuredDocuments": sum(bool(d.get("processingMetrics")) for d in active),
        },
        "documents": rows,
        "history": history,
        "summary": summarize(history["events"]),
        "notes": [
            "Inventory describes current active generations. Older files may have no captured processing metrics.",
            "History is a bounded prototype journal from telemetry-enabled processes, "
            "not lifetime usage or Azure billing.",
            "Chunk-write RU excludes document/library metadata, Blob, queue and proposal costs. "
            "Failed attempts may be absent.",
            "Retrieval RU covers reported API calls; monitoring, tool-definition and UI metadata reads are excluded.",
            "Model answer tokens belong to a chat turn and are not allocated artificially across libraries.",
        ],
    }
