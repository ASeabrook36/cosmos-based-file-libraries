"""Controlled DOCX passage packing for the existing opt-in structure lab.

This module is not imported by the API/worker. Canonical extraction is unchanged;
original DOCX locators are a verified sidecar, not a second source representation.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from collections import defaultdict

from .chunking import Tokenizer
from .structure_lab import digest, split_source

VARIANTS = ("granular-zero-overlap-v1", "coalesced-500-zero-overlap-v1")


def docx_document(data: bytes, document_id: str, file_name: str) -> dict:
    """Use the deployed extractor, then bind its blocks to original-body coordinates.

    Gold uses zero-based top-level paragraphs and visible table grid coordinates.
    The application adapter uses one-based XML paragraph extents, including cells.
    Never compare those integers. Every block must match the independent sidecar's
    canonical rendering exactly before any location is trusted.
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    from .structural_enrichment import docx_structure

    _, frozen = docx_structure(data, document_id, file_name)
    document = copy.deepcopy(frozen)
    original = Document(io.BytesIO(data))
    paragraph_index = table_index = block_index = 0
    for child in original.element.body.iterchildren():
        regions, cells = [], []
        if child.tag == qn("w:p"):
            text = Paragraph(child, original).text
            rendered = text + "\n"
            regions.append({"locator": {"kind": "paragraph", "index": paragraph_index}, "start": 0, "end": len(text)})
            paragraph_index += 1
            kind = "paragraph"
        elif child.tag == qn("w:tbl"):
            rendered = ""
            table = Table(child, original)
            for ri, row in enumerate(table.rows):
                seen = {}
                for ci, cell in enumerate(row.cells):
                    key = cell._tc
                    locator = {"kind": "table_cell", "table": table_index, "row": ri, "column": ci}
                    if key in seen:
                        start, end = seen[key]
                    else:
                        if seen:
                            rendered += " | "
                        start = len(rendered)
                        rendered += cell.text
                        end = len(rendered)
                        seen[key] = (start, end)
                    regions.append({"locator": locator, "start": start, "end": end})
                    cells.append({"row": ri, "column": ci, "start": start, "end": end})
                rendered += "\n"
            table_index += 1
            kind = "table"
        else:
            continue
        block = document["blocks"][block_index]
        if block["text"] != rendered:
            raise ValueError("original locator sidecar differs from canonical extraction")
        block["kind"] = kind
        block["originalRegions"] = regions
        if kind == "table":
            block["tableGrid"] = {"table": table_index - 1, "cells": cells}
        block_index += 1
    if block_index != len(document["blocks"]):
        raise ValueError("unmapped canonical source blocks")
    document["sourceSha256"] = hashlib.sha256(data).hexdigest()
    document["canonicalTreeDigest"] = digest(frozen)
    document["locatorContract"] = "original-docx-main-body-v1"
    return document


def _prefix(node: dict, tokenizer: Tokenizer) -> str:
    if node["parentId"] is None:
        return ""
    # Preserve UTF-8 at the prefix limit, including unusually styled headings.
    heading = split_source(node["title"], tokenizer, 64)[0]
    return heading + "\n"


def passage_rows(document: dict, library_id: str, variant: str, tokenizer: Tokenizer, target: int = 500) -> list[dict]:
    """Exact Unicode body spans, identical zero overlap and source-block expansion.

    Tables are never merged with prose. Oversized blocks are split identically in
    both candidates, retaining full table/source-block addresses for expansion.
    Empty blocks stay in the source artifact but receive no standalone vector.
    """
    if variant not in VARIANTS or target < 100:
        raise ValueError("supported representation and target >=100 required")
    owners = {bid: node for node in document["nodes"] for bid in node["blockIds"]}
    rows, pending = [], []
    pending_node = None

    def emit():
        nonlocal pending, pending_node
        if not pending:
            return
        prefix = _prefix(pending_node, tokenizer)
        text = prefix + "".join(part["text"] for part in pending)
        if not text.strip():
            pending = []
            return
        rows.append(
            {
                "id": f"{document['id']}:passage:{variant}:{len(rows)}",
                "libraryId": library_id,
                "documentId": document["id"],
                "generation": 1,
                "contentRevision": 1,
                "sourceSha256": document["sourceSha256"],
                "sourceDigest": document["sourceDigest"],
                "nodeId": pending_node["id"],
                "parentNodeId": pending_node["parentId"],
                "role": pending_node["role"],
                "genre": document["genre"],
                "recordKind": "passage",
                "level": "passage",
                "contentKind": "text",
                "generated": False,
                "text": text,
                "tokenCount": len(tokenizer.encode(text)),
                "headingPrefix": prefix,
                "sourceSpans": [{k: p[k] for k in ("blockId", "start", "end")} for p in pending],
                "chunkIndex": len(rows),
                "chunkingVersion": variant,
            }
        )
        pending, pending_node = [], None

    for block in document["blocks"]:
        node = owners[block["id"]]
        prefix = _prefix(node, tokenizer)
        # Reserve one token for BPE boundary variation, then verify final size.
        budget = target - len(tokenizer.encode(prefix)) - 1
        pieces = split_source(block["text"], tokenizer, budget)
        offset = 0
        for piece in pieces:
            atom = {"blockId": block["id"], "start": offset, "end": offset + len(piece), "text": piece}
            offset += len(piece)
            if not piece.strip():
                # Empty source is available from whole block/subtree reads. Do not
                # create heading-only vectors or quietly relabel them as passages.
                continue
            together = prefix + "".join(p["text"] for p in pending) + piece
            if pending and (
                variant == VARIANTS[0]
                or block["kind"] == "table"
                or pending_node["id"] != node["id"]
                or len(tokenizer.encode(together)) > target
            ):
                emit()
            pending.append(atom)
            pending_node = node
            if block["kind"] == "table" or variant == VARIANTS[0]:
                emit()
    emit()
    if any(r["tokenCount"] > target for r in rows):
        raise ValueError("serialized passage exceeds target")
    verify_rows(document, rows)
    return rows


def verify_rows(document: dict, rows: list[dict]) -> None:
    """Every reported span is independently checked against immutable source."""
    blocks = {b["id"]: b for b in document["blocks"]}
    seen = defaultdict(list)
    for row in rows:
        if row["documentId"] != document["id"] or row["sourceSha256"] != document["sourceSha256"]:
            raise ValueError("row source binding differs")
        text = []
        for span in row["sourceSpans"]:
            block = blocks[span["blockId"]]
            start, end = span["start"], span["end"]
            if not 0 <= start < end <= len(block["text"]):
                raise ValueError("invalid source extent")
            text.append(block["text"][start:end])
            seen[block["id"]].append((start, end))
        if row["headingPrefix"] + "".join(text) != row["text"]:
            raise ValueError("passage is not exact source plus declared heading")
    for bid, spans in seen.items():
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:], strict=False)):
            raise ValueError("zero-overlap control violated")
        covered = "".join(blocks[bid]["text"][start:end] for start, end in spans)
        if "".join(covered.split()) != "".join(blocks[bid]["text"].split()):
            raise ValueError("source content omitted")
    if any(block["text"].strip() and bid not in seen for bid, block in blocks.items()):
        raise ValueError("nonempty source block omitted")


def expanded_blocks(documents: dict[str, dict], hits: list[dict]) -> dict[str, list[dict]]:
    """Identical bounded-selector semantics in both passage candidates."""
    selected = defaultdict(set)
    for row in hits:
        document = documents[row["documentId"]]
        if row["sourceSha256"] != document["sourceSha256"] or row["sourceDigest"] != document["sourceDigest"]:
            raise ValueError("stale source row")
        selected[row["documentId"]].update(span["blockId"] for span in row["sourceSpans"])
    return {did: [b for b in documents[did]["blocks"] if b["id"] in bids] for did, bids in sorted(selected.items())}


def source_relevance(task: dict, documents: dict[str, dict], hits: list[dict], tokenizer: Tokenizer) -> dict:
    """Gold-to-original locator scoring, independent of candidate region labels.

    Blank cell support requires the complete canonical table block plus original
    grid sidecar. Passage substring presence alone never proves a blank value.
    This is source delivery, not answer quality or independent human relevance.
    """
    chosen = expanded_blocks(documents, hits)
    available = {}
    for did, blocks in chosen.items():
        available[did] = {json.dumps(r["locator"], sort_keys=True) for b in blocks for r in b["originalRegions"]}
    expected, delivered = [], []
    for fact in task["sourceFacts"]:
        doc = documents[fact["sourceId"]]
        for region in fact["regions"]:
            if region["sourceSha256"] != doc["sourceSha256"]:
                raise ValueError("gold binds different original bytes")
            key = json.dumps(
                {k: region[k] for k in ("kind", "index", "table", "row", "column") if k in region}, sort_keys=True
            )
            expected.append(region["id"])
            if key in available.get(fact["sourceId"], set()):
                delivered.append(region["id"])
    expected, delivered = sorted(set(expected)), sorted(set(delivered))
    evidence = [
        {"documentId": did, "sourceDigest": documents[did]["sourceDigest"], "blocks": blocks}
        for did, blocks in chosen.items()
    ]
    return {
        "case": task["case"],
        "expectedRegions": len(expected),
        "deliveredRegions": len(delivered),
        "coverage": len(delivered) / len(expected) if expected else None,
        "allGoldDelivered": bool(expected) and delivered == expected,
        "deliveredRegionIds": delivered,
        "missingRegionIds": sorted(set(expected) - set(delivered)),
        "expandedSourceBlocks": sum(len(blocks) for blocks in chosen.values()),
        "expandedSourceBodyTokensEstimate": sum(
            len(tokenizer.encode(b["text"])) for blocks in chosen.values() for b in blocks
        ),
        "expandedSourceJsonTokensEstimate": len(tokenizer.encode(json.dumps(evidence, ensure_ascii=False))),
    }
