"""Versioned compositions and trusted provider boundary, independent of enrichment.

Profiles are immutable recipes. A generation snapshots its recipe and source; queue
arguments never choose a provider. Generated discovery text is never source evidence.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from fli.checkpoints import digest
from fli.chunking import Chunk
from fli.config import EMBEDDING_MODEL

ProfileId = Literal["current-v1", "custom-structured-v1", "microsoft-indexer-layout-v1"]
PROFILE_IDS = ("current-v1", "custom-structured-v1", "microsoft-indexer-layout-v1")


def profile(profile_id: str = "current-v1", *, controlled: bool = False) -> dict:
    if profile_id not in PROFILE_IDS:
        raise ValueError("unknown ingestion profile")
    microsoft = profile_id == "microsoft-indexer-layout-v1"
    structured = profile_id == "custom-structured-v1"
    return {
        "id": profile_id,
        "version": 2 if microsoft or controlled else 1,
        "codeRevision": None,
        "provider": "microsoft-indexer" if microsoft else "python",
        "extraction": "prebuilt-layout" if microsoft or controlled else "native-and-layout",
        "extractionOnly": controlled and not microsoft,
        "chunking": {
            "algorithm": "token-window" if microsoft else "section-sentence",
            "targetTokens": 500 if controlled or not microsoft else 5000,
            "overlapTokens": 0 if controlled else None if microsoft else 50,
            "overlapCharacters": (0 if controlled else 200) if microsoft else None,
            "emailSingleChunkMax": 700 if not microsoft and not structured and not controlled else None,
        },
        "enrichment": {
            "adapter": "source-structure-v1" if structured and not controlled else None,
            "generatedSummaries": False,
        },
        "embedding": {"model": EMBEDDING_MODEL, "dimensions": 3072, "modelRevision": None},
        "workflowVersion": "shared-indexer-v2" if microsoft or controlled else None,
        "capabilities": {
            "passages": True,
            "completeProviderText": True,
            "authoredHierarchy": "markdown-atx-and-docx-headings" if structured and not controlled else False,
            "generatedSummaries": False,
            "visuals": not microsoft and not controlled,
        },
        "limitations": (
            [
                "Controlled profile uses Microsoft layout extraction with Python chunking/embedding; "
                "hierarchy, proposals and visuals are disabled to isolate chunk boundaries."
            ]
            if controlled and not microsoft
            else (
                [
                    "No figure crops or native Office visuals; authored chapters are not inferred.",
                    "One canonical provider-text section; provider usage may be unavailable.",
                ]
                if microsoft
                else [
                    "Structure adapters support Markdown ATX and DOCX main-body Heading styles; "
                    "other formats fail explicitly.",
                    "Generated summaries and semantic hierarchy search are not implemented.",
                ]
                if structured
                else []
            )
        ),
    }


def snapshot(library: dict) -> dict:
    return copy.deepcopy(library.get("ingestionProfile") or profile())


def job(library_id: str, document_id: str, generation: int, source: dict, recipe: dict, metadata_version: int) -> dict:
    body = {
        "schema": "fli.ingestion-job.v1",
        "libraryId": library_id,
        "documentId": document_id,
        "generation": generation,
        "sourcePath": source["blobPath"],
        "sourceSha256": source["sha256"],
        "profile": copy.deepcopy(recipe),
        "metadataVersion": metadata_version,
    }
    return {**body, "executionId": digest(body)}


@dataclass
class ProviderResult:
    pieces: list[Chunk]
    sections: list[dict]
    vectors: list[list[float]]
    extraction: dict
    usage: dict
    coverage: dict


class IngestionProvider(Protocol):
    async def prepare(self, work: dict, source: bytes) -> dict: ...


def validate_microsoft(output: dict, work: dict, source: bytes, dimensions: int) -> ProviderResult:
    """Validate the complete result before any writes. Never trust workflow row scope."""
    if (
        output.get("schema") != "fli.indexer-result.v1"
        or output.get("workflowVersion") != work["profile"].get("workflowVersion")
        or digest(output.get("job")) != digest(work)
        or hashlib.sha256(source).hexdigest() != work["sourceSha256"]
        or output.get("embedding") != work["profile"]["embedding"]
    ):
        raise ValueError("provider source/job/model binding mismatch")
    try:
        provider_source = base64.b64decode(output["sourceBase64"], validate=True)
    except (KeyError, ValueError) as exc:
        raise ValueError("provider original-source binding unavailable") from exc
    if provider_source != source:
        raise ValueError("provider original-source binding mismatch")
    if not work["profile"]["embedding"]["modelRevision"]:
        raise ValueError("embedding model revision must be configured for Microsoft ingestion")
    analysis = output.get("analysis", {}).get("analyzeResult", {})
    if analysis.get("modelId") != "prebuilt-layout" or not isinstance(analysis.get("content"), str):
        raise ValueError("complete provider text required")
    rows = output.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("empty provider result")
    rows = sorted(rows, key=lambda r: r["chunkIndex"])
    pieces, vectors = [], []
    for index, row in enumerate(rows):
        vector = row.get("embedding")
        if (
            type(row.get("chunkIndex")) is not int
            or row["chunkIndex"] != index
            or type(row.get("totalChunks")) is not int
            or row.get("totalChunks") != len(rows)
            or not isinstance(row.get("text"), str)
            or not row["text"].strip()
            or not isinstance(vector, list)
            or len(vector) != dimensions
            or any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > 65504 for v in vector)
            or not any(vector)
        ):
            raise ValueError("incomplete rows or incompatible vectors")
        # Scope/location fields are deliberately discarded; the provider produced no exact locations.
        pieces.append(Chunk(index, row["text"], 0, None, {}))
        vectors.append(vector)
    usage = output.get("usage") or {}
    allowed = ("embeddingTokens", "embeddingRequests", "embeddingRetries", "embeddingThrottles", "embeddingMs")
    for key in allowed:
        value = usage.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError("invalid provider usage")
    return ProviderResult(
        pieces,
        [{"text": analysis["content"], "heading": None, "location": {}, "contentKind": "text"}],
        vectors,
        {"method": "logicapps-prebuilt-layout", "pageCount": len(analysis.get("pages", []))},
        {key: usage.get(key) for key in allowed},
        {
            "visuals": "unavailable",
            "authoredHierarchy": "unavailable",
            "generatedSummaries": "unavailable",
            "providerFigureCount": len(analysis.get("figures", [])),
        },
    )


def result_plan(result: ProviderResult, work: dict, document: dict, count) -> dict:
    pieces = [asdict(p) | {"token_count": count(p.text)} for p in result.pieces]
    return {
        "version": 1,
        "generation": work["generation"],
        "sourceSha256": work["sourceSha256"],
        "job": work,
        "pieces": pieces,
        "vectors": result.vectors,
        "providerUsage": result.usage,
        "sections": result.sections,
        "imageRecords": {},
        "proposals": None,
        "accept": False,
        "meta": {key: document.get(key) for key in ("author", "periodStart", "periodEnd", "facets")},
        "author": None,
        "authoredAt": None,
        "imageCount": 0,
        "imagesSkipped": result.coverage["providerFigureCount"],
        "visuals": [],
        "textChunkCount": len(pieces),
        "chunkingVersion": "microsoft-token-window-v1",
        "extraction": result.extraction,
        "coverage": result.coverage,
    }


def validate_layout_extraction(output: dict, work: dict, source: bytes) -> dict:
    if (
        output.get("schema") != "fli.indexer-extraction.v1"
        or output.get("workflowVersion") != work["profile"].get("workflowVersion")
        or output.get("job") != work
        or hashlib.sha256(source).hexdigest() != work["sourceSha256"]
    ):
        raise ValueError("extraction source/job binding mismatch")
    try:
        original = base64.b64decode(output["sourceBase64"], validate=True)
    except (KeyError, ValueError) as exc:
        raise ValueError("extraction source binding unavailable") from exc
    analysis = output.get("analysis", {}).get("analyzeResult", {})
    if (
        original != source
        or analysis.get("modelId") != "prebuilt-layout"
        or not isinstance(analysis.get("content"), str)
    ):
        raise ValueError("complete layout extraction/source required")
    return analysis


def chunking_description(recipe: dict) -> str:
    """Display real units, including immutable recipes created before the unit fix."""
    chunking = recipe["chunking"]
    microsoft = recipe["provider"] == "microsoft-indexer"
    if microsoft:
        overlap = chunking.get("overlapCharacters", chunking.get("overlapTokens"))
        unit = "characters"
        correction = " (legacy field mislabeled tokens)" if "overlapCharacters" not in chunking else ""
    else:
        overlap, unit, correction = chunking.get("overlapTokens"), "tokens", ""
    return f"{chunking['targetTokens']} tokens / {overlap} {unit} overlap{correction}"
