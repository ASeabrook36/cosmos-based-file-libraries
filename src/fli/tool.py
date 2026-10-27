"""The function-calling tool for one library (spec §6.4).

The tool offers ``query`` and the filters this library can use, read from its ``tagVocabulary``
and ``filterValues`` (``fli.vocabulary``). A filter is offered only when it can narrow the
search: tags, authors and the time windows when some document has a value, file types and
content kinds when the library holds more than one. Tag and author values are an ``enum`` of the
real ones, so the agent picks values instead of inventing them; above ``MAX_ENUM`` values the
enum is dropped and the description lists the ``LISTED`` most used.

``uploadedBetween`` stays an API filter only, as in the spec's example tool. The library id is
never a parameter: the platform binds the tool to one library and calls ``POST …/search`` with it
in the path (CLAUDE.md rule 3).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PREFIX = "search_library_"
MAX_NAME = 64  # function names: 1-64 of [A-Za-z0-9_-]
MAX_ENUM = 100
LISTED = 50

WINDOW = {
    "type": "object",
    "properties": {"from": {"type": "string", "format": "date"}, "to": {"type": "string", "format": "date"}},
}


def tool_name(library_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", library_name.lower()).strip("_") or "library"
    return (PREFIX + slug)[:MAX_NAME].rstrip("_")


def most_used(counts: Mapping[str, int]) -> list[str]:
    return sorted(counts, key=lambda value: (-counts[value], value))


def _list_filter(counts: Mapping[str, int], description: str) -> tuple[dict[str, Any], bool]:
    """An array parameter; an ``enum`` of the real values unless there are too many. Returns (schema, enumerated)."""
    items: dict[str, Any] = {"type": "string"}
    enumerated = len(counts) <= MAX_ENUM
    if enumerated:
        items["enum"] = sorted(counts)
    return {"type": "array", "items": items, "description": description}, enumerated


def tool_definition(library: Mapping[str, Any]) -> dict[str, Any]:
    vocabulary: Mapping[str, Mapping[str, int]] = library.get("tagVocabulary") or {}
    values: Mapping[str, Any] = library.get("filterValues") or {}
    total = (library.get("stats") or {}).get("documentCount", 0)
    properties: dict[str, Any] = {"query": {"type": "string", "description": "What to look for, in natural language."}}
    offered: list[str] = []

    tags = {f"{key}={value}": n for key, counted in vocabulary.items() for value, n in counted.items()}
    if tags:
        properties["tags"], enumerated = _list_filter(tags, "Tags written key=value. Every listed tag must match.")
        if enumerated:
            by_key = ", ".join(f"{key}=({', '.join(most_used(vocabulary[key]))})" for key in sorted(vocabulary))
            offered.append(f"tags {by_key}")
        else:
            offered.append(f"tags (the {LISTED} most used: {', '.join(most_used(tags)[:LISTED])})")
    if authors := values.get("authors") or {}:
        properties["authors"], enumerated = _list_filter(authors, "Any listed author matches.")
        if enumerated:
            offered.append(f"authors ({', '.join(most_used(authors))})")
        else:
            offered.append(f"authors (the {LISTED} most used: {', '.join(most_used(authors)[:LISTED])})")
    if period := values.get("period"):
        properties["period"] = {**WINDOW, "description": "What a document is about: its period overlaps this window."}
        offered.append(f"documents cover periods {period['from']} to {period['to']}")
    missing = values.get("missing") or {}
    dated = bool(values) and total - missing.get("authoredAt", total) > 0
    if dated:
        properties["authoredBetween"] = {**WINDOW, "description": "When a document was written."}
    if len(file_types := values.get("fileTypes") or {}) > 1:
        properties["fileTypes"], _ = _list_filter(file_types, "Any listed file type matches.")
        offered.append(f"file types {', '.join(sorted(file_types))}")
    if len(kinds := values.get("contentKinds") or {}) > 1:
        properties["contentKinds"], _ = _list_filter(kinds, "text passages, image descriptions, or both.")

    description = library.get("description", "")
    if (library.get("stats") or {}).get("imageCount", 0) or (library.get("stats") or {}).get(
        "imageProcessingDocuments", 0
    ):
        description += (
            "\nSearches text and the meanings of images together, including chart labels, quantities and trends. "
            "For a vaguely remembered chart, describe its subject and appearance in query. "
            "Image results carry descriptions, nearby source text, documentId/location and an image with a short-lived "
            "read URL and expiresAt. Show the image and cite its explanation; read the parent file if needed. "
            "Use a relevant figure inline when it helps explain the answer, even without an explicit image request. "
            "Reuse images and captions already returned; a separate image lookup is unnecessary for those. "
            "Include the associated source caption by default; omit it when the situation or user calls for that. "
            "New PDF images are cropped figures (kind=pdf_figure), with source captions and page/figure provenance. "
            "Legacy full-page images use kind=pdf_page. "
            "Results are candidates, not all relevant answers. Only image.caption is a source caption; "
            "heading is section context, and layout figure IDs are not printed figure numbers. "
            "If text identifies a relevant PDF picture that was not returned, use get_images for that page, "
            "including when semantic image search found none or the indexing cap omitted it. "
            "For a picture remembered with a passage, match the passage's document/page; "
            "do not substitute a same-subject image from a different page. "
            "Descriptions are generated evidence and can misread values; inspect the visual when precision matters."
        )
    if offered:
        description += f"\n\nFilters available in this library: {'; '.join(offered)}."
    if period and dated:
        description += " Use 'period' for what a document is about, 'authoredBetween' for when it was written."
    return {
        "type": "function",
        "function": {
            "name": tool_name(library.get("name", "")),
            "description": description.strip(),
            "parameters": {"type": "object", "properties": properties, "required": ["query"]},
        },
    }


def tool_definitions(library: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Standard function schemas; a REST or MCP host binds each to this library."""
    search = tool_definition(library)
    search["function"]["description"] += (
        "\nReturns selected passages, never proof of whole-file or all-files coverage. "
        "Use documentId from results to read a whole file; for 'all files by author/tag', enumerate the catalogue."
    )
    slug = tool_name(library.get("name", "")).removeprefix(PREFIX)
    filters = {
        k: v for k, v in search["function"]["parameters"]["properties"].items() if k not in ("query", "contentKinds")
    }
    for key, description in (
        ("authors", "Any listed author matches exactly."),
        ("tags", "All key=value tags must match."),
        ("fileTypes", "Any listed normalized file type matches."),
    ):
        # The catalogue includes queued/failed files and sees metadata edits immediately.
        # Cached searchable-only vocabulary is a hint, never an exhaustive enum here.
        known = (filters.get(key) or {}).get("items", {}).get("enum", [])
        hint = f" Recently observed values include: {', '.join(known[:LISTED])}." if known else ""
        filters[key] = {"type": "array", "items": {"type": "string"}, "description": description + hint}
    for key in ("period", "authoredBetween", "uploadedBetween"):
        filters.setdefault(key, {**WINDOW})
    common = library.get("description") or library.get("name") or "File library"
    catalogue = {
        "type": "function",
        "function": {
            "name": ("find_documents_library_" + slug)[:MAX_NAME].rstrip("_"),
            "description": common + "\nFind files by name or metadata without loading their contents. "
            "fileNames matches any exact name ignoring case; duplicate names remain distinct documents. "
            "fileNameContains matches a name fragment. Selectors combine with AND. "
            "For 'all matching files', follow nextAfter using after with unchanged selectors until hasMore=false, "
            "including unreadable files in your coverage accounting. Inspect metadata, generated opening descriptions "
            "and estimatedTokens before full reads. Null estimates mean unknown. "
            "For a vaguely described topic, search passages first to identify candidate documentIds; "
            "semantic top hits are not an exhaustive file set. Clarify if multiple plausible files remain.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "filters": {"type": "object", "additionalProperties": False, "properties": filters},
                    "fileNames": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                    "fileNameContains": {"type": "string"},
                    "after": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
        },
    }
    read = {
        "type": "function",
        "function": {
            "name": ("read_documents_library_" + slug)[:MAX_NAME].rstrip("_"),
            "description": common + "\nRead the complete extracted text of one or more identified files. "
            "Use for explicit whole-file requests, full summaries, critiques, comparisons, or tasks requiring "
            "global context. Prefer passage search for localized facts. Best-answer quality takes priority: "
            "combine full reads with passage searches from other files in the same turn as needed, "
            "without routine user confirmation. "
            "Weigh input tokens, latency and attention cost; do not substitute snippets for a requested whole read. "
            "Select documentIds and generations from catalogue/search; never invent IDs. "
            "includeText=false inspects exact serialized size without returning content. "
            "maxTokens is your available input budget (default 32000, service maximum 200000); "
            "reserve room for instructions, history, other files and the answer. "
            "Returns every selected file in full or no text with budget_exceeded. Split batches or increase "
            "the budget only if the model has capacity. No silent truncation. Token estimates name their tokenizer. "
            "Kept image sections include descriptions and expiring image links for display or visual model input. "
            "Skipped visuals are disclosed; extraction does not guarantee every figure/attachment. "
            "Only say files were read in full when complete=true; disclose unreadable files and incomplete sets.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "documents": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "documentId": {"type": "string"},
                                "generation": {"type": "integer", "minimum": 1},
                            },
                            "required": ["documentId"],
                        },
                    },
                    "includeText": {"type": "boolean"},
                    "maxTokens": {"type": "integer", "minimum": 1, "maximum": 200000},
                },
                "required": ["documents"],
            },
        },
    }
    images = {
        "type": "function",
        "function": {
            "name": ("get_images_library_" + slug)[:MAX_NAME].rstrip("_"),
            "description": common + "\nRead original figure crops and page text from 1–3 identified PDF pages. "
            "Use after search locates a requested picture's page, including pages omitted by the ingestion image cap. "
            "A page request does not require reading the whole document. Select the matching figure, inspect it "
            "and show it inline when requested; do not substitute other retrieved candidates. "
            "Pass documentId/generation from search or catalogue. This returns source figures, not new semantic "
            "search results. Up to 10 figures per request; omissions are explicit. "
            "Only image.caption is an associated source caption; heading is contextual section text, "
            "never a figure title. "
            "Layout figure IDs are extraction locators, not printed figure numbers. "
            "These results cover the selected PDF pages, not the entire file. Cached figures are reused; "
            "image and caption metadata are read together from ingestion storage; "
            "no page analysis occurs at retrieval.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "documentId": {"type": "string"},
                    "generation": {"type": "integer", "minimum": 1},
                    "pages": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {"type": "integer", "minimum": 1, "maximum": 2000},
                    },
                },
                "required": ["documentId", "pages"],
            },
        },
    }
    return [search, catalogue, read, images]
