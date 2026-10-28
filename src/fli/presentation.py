"""Versioned REST evidence/attachment presentation. No retrieval or permission decisions.

Only call after retrieval's authorization, integrity and lifecycle checks. Source strings
are never shortened or rewritten. Attachment URLs are bearer capabilities for the platform.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal

from fastapi import Query
from pydantic import BaseModel, Field

from fli.chunking import default_tokenizer

ResponseView = Annotated[Literal["raw", "compact"], Query()]
EvidenceBudget = Annotated[int | None, Query(ge=1, le=200_000)]
DEFAULT_EVIDENCE_TOKENS = 32_000
IMAGE_EVIDENCE_FIELDS = ("kind", "caption", "captionStatus", "captionAssociation")
OPERATIONAL = {
    "usage",
    "telemetryId",
    "tokenizer",
    "estimateScope",
    "maxTokens",
    "estimatedTokens",
    "documentTokenTokenizer",
    "chunkId",
    "imageRef",
    "imageMeta",
}


class CompactResponse(BaseModel):
    """Opt-in response envelope; raw responses retain their existing operation shape."""

    contract: Literal["fli.compact.v1"]
    evidence: dict[str, Any] = Field(description="Untrusted source evidence safe to serialize as model tool text.")
    attachments: list[dict[str, Any]] = Field(description="Platform-only images keyed by imageId, with expiring links.")
    metadata: dict[str, Any] = Field(description="Platform-only usage, telemetryId and evidence text-token budget.")


def evidence_json(evidence):
    """Canonical serialization used for the contract's text-token budget."""
    return json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))


def project(response):
    """Return lossless source evidence and separately held, snapshot-bound attachments."""
    attachments = {}

    def visit(value, parent=None):
        if isinstance(value, list):
            return [visit(item, parent) for item in value]
        if not isinstance(value, dict):
            return value
        context = {**(parent or {}), **value}
        out = {}
        for key, item in value.items():
            if key in OPERATIONAL:
                continue
            if key == "image" and isinstance(item, dict):
                image_id = value.get("imageId") or value.get("chunkId")
                if image_id and item.get("url"):
                    attachment = {
                        k: context.get(k)
                        for k in ("documentId", "generation", "contentRevision", "fileName", "location")
                    }
                    attachment.update(imageId=image_id, image=dict(item))
                    attachments[image_id] = attachment
                    out["imageId"] = image_id
                out[key] = {k: item[k] for k in IMAGE_EVIDENCE_FIELDS if k in item}
            elif key in {"location", "sourceScope"} and isinstance(item, dict):
                # Internal layout figure IDs are not printed source captions/locators.
                out[key] = {k: v for k, v in item.items() if k != "figure"}
            else:
                out[key] = visit(item, context)
        if "sectionId" in value and "text" not in value and "estimatedTokens" in value:
            out["sectionTokens"] = value["estimatedTokens"]
        # Catalogue size hints describe stored source, not the serialized response.
        if "documentId" in value and "estimatedTokens" in value and "sections" not in value:
            out.setdefault("documentTokens", value["estimatedTokens"])
        if value.get("image") and "chunkId" in value:
            # Search's generated wrapper repeats provenance and internal figure IDs.
            # Preserve the description verbatim; original body/neighbor/read text is untouched.
            marker = f", under '{value.get('heading') or ''}': "
            position = value.get("text", "").find(marker)
            if value.get("text", "").startswith("Image from ") and 0 <= position < 1000:
                out["text"] = value["text"][position + len(marker) :]
            heading = value.get("heading")
            nearby = value.get("text", "").partition("\nNearby source text: ")[2]
            out["heading"] = None
            out["sectionContext"] = heading if heading and heading in nearby else None
            if heading:
                out["contextHeading"] = heading
                out["headingAssociation"] = "ingestion_context_not_figure_title_or_section_membership"
        if "text" in value:
            image = value.get("contentKind") == "image" or bool(value.get("image"))
            out["textSource"] = (
                "generated_image_description"
                if image and value.get("descriptionSource") != "not_available"
                else "unavailable_image_description"
                if image
                else "extracted_source_text"
            )
            name = context.get("fileName")
            if name:
                location = out.get("location") or {}
                if "chunkIndex" in value and "location" not in value:
                    out["locationStatus"] = "neighbor_location_not_returned_do_not_inherit_hit_location"
                where = ", ".join(f"{k} {v}" for k, v in location.items() if k != "figure")
                out["citation"] = f"{name} ({where})" if where else name
        return out

    evidence = visit(response)
    # Flat convenience fields used by function-tool hosts; coverage remains authoritative.
    if (coverage := evidence.get("coverage")) and "results" in evidence:
        evidence.setdefault("disposition", coverage.get("disposition"))
        evidence.setdefault("notes", coverage.get("notes") or [])
        evidence.setdefault("filtersApplied", coverage.get("filtersApplied") or {})
    return evidence, list(attachments.values())


def compact_response(response, max_tokens=DEFAULT_EVIDENCE_TOKENS, include_content=True):
    evidence, attachments = project(response)
    tokenizer = default_tokenizer()
    required = len(tokenizer.encode(evidence_json(evidence)))
    fits = required <= max_tokens
    if not include_content or not fits:
        original = evidence
        # Refuse the entire batch, including attachments and outline entries. Control
        # messages may exceed tiny budgets; no source content is delivered in that case.
        evidence = {
            k: evidence[k]
            for k in ("library", "mode", "resultUnit", "documentScope", "scope", "pages", "requestedImageIds")
            if k in evidence
        }
        evidence.update(
            disposition="budget_exceeded" if include_content else "inspected",
            complete=False,
            selectionComplete=False,
            documentComplete=False,
            notes=[
                "No source bodies, outline entries, image candidates or attachments delivered. "
                "Only request/coverage and document manifests remain."
            ],
            availableActions=["narrow_selection", "outline", "paginate", "increase_budget"],
            nextAction="Select fewer documents/regions or a smaller search/catalogue/outline page, or increase "
            "the budget. No source text was clipped; do not claim complete coverage.",
        )
        evidence["notes"] = (original.get("notes") or []) + evidence["notes"]
        if "coverage" in original:
            evidence["coverage"] = original["coverage"]
        if "documents" in original:
            evidence["documents"] = [
                {
                    **{
                        k: v
                        for k, v in d.items()
                        if k not in {"sections", "outline", "candidateImages", "description", "descriptionSource"}
                    },
                    "complete": False,
                    "selectionComplete": False,
                    "documentComplete": False,
                }
                for d in original["documents"]
            ]
        attachments = []
    metadata = {k: response[k] for k in ("usage", "telemetryId") if k in response}
    if results := response.get("results"):
        metadata["references"] = [
            {k: r[k] for k in ("chunkId", "evidenceId", "documentId", "generation", "contentRevision") if k in r}
            for r in results
            if r.get("chunkId")
        ]
    if documents := response.get("documents"):
        metadata["documentEstimates"] = [
            {
                "documentId": d.get("documentId"),
                "tokens": d.get("documentTokens", d.get("estimatedTokens")),
                "tokenizer": d.get("documentTokenTokenizer", d.get("tokenizer")),
                "scope": "stored_document_hint",
            }
            for d in documents
        ]
    metadata["budget"] = {
        "scope": "evidence_json",
        "tokenizer": tokenizer.name,
        "maxTokens": max_tokens,
        "requiredTokens": required,
        "returnedTokens": len(tokenizer.encode(evidence_json(evidence))),
        "fits": fits,
        "nativeImageTokensIncluded": False,
    }
    return {"contract": "fli.compact.v1", "evidence": evidence, "attachments": attachments, "metadata": metadata}


async def respond(response, view, max_tokens=None):
    if view == "raw":
        return response
    return await asyncio.to_thread(compact_response, response, max_tokens or DEFAULT_EVIDENCE_TOKENS)
