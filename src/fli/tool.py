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
    properties["document"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "documentId": {"type": "string"},
            "generation": {"type": "integer", "minimum": 1},
            "contentRevision": {"type": "integer", "minimum": 0},
        },
        "required": ["documentId"],
        "description": "Scope evidence search to a returned document. Keep query about the information "
        "needed, without repeating filenames.",
    }
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
            "If text identifies a relevant PDF picture that was not returned, use inspect_images for that page, "
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
    hints = search["function"]["description"].partition("\n\nFilters available in this library:")[2]
    search["function"]["description"] = (
        (library.get("description") or library.get("name") or "File library")
        + (
            "\nSearch ranked evidence passages and generated image descriptions, with multiple hits per file. "
            "Default searches text and images together. Use document selector for an identified file; query the "
            "information needed without repeating filenames or web operators. Only filter explicit constraints, "
            "not guessed topic tags. Search is not file enumeration or full-file coverage. "
            "Results have snapshot-bound evidenceIds: expand them with read_documents mode=selected for exact source "
            "context and associated figure candidates. Use outline to locate missing themes in long reports. "
            "Descriptions may misread values: inspect_images with chosen imageIds for precise visual comparisons. "
            "Show relevant returned figures immediately when requested, rather than offering to fetch them later. "
            "Cite filenames and source locations, never internal layout figure locators. "
        )
        + (" Filters available: " + hints if hints else "")
    )
    slug = tool_name(library.get("name", "")).removeprefix(PREFIX)
    filters = {
        k: v
        for k, v in search["function"]["parameters"]["properties"].items()
        if k not in ("query", "contentKinds", "document")
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
            "For a vaguely described file, set mode=semantic_candidates with query to find "
            "bounded document candidates; "
            "semantic top hits are not an exhaustive file set. Clarify if multiple plausible files remain.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["catalogue", "semantic_candidates"],
                        "description": "Catalogue: exact names/metadata with paging. semantic_candidates: query "
                        "required, bounded non-exhaustive file selection, no name/paging selectors.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Natural description of the file needed; only for semantic_candidates.",
                    },
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
            "description": common + "\nRead identified files: mode full (default), outline, or selected. "
            "Full returns every stored section in the selected files or no content if over maxTokens; complete=true "
            "alone confirms a full read. Never replace an explicit whole-file request with snippets. "
            "For focused facts/research in a large file, use document-scoped search then mode=selected with "
            "evidenceIds, sectionIds, or source pages/slides/paragraphs. Selected returns exact stored extraction "
            "regions plus figure candidates, selectionComplete and documentComplete=false. DOCX paragraph lookup "
            "expands the containing stored section, not an exact layout boundary. "
            "For research synthesis in a long report, use mode=outline to locate relevant themes and studies; "
            "page with afterSection/sectionLimit while hasMore. Outline gives sectionIds, headings, locations and "
            "token sizes, no body text. Read relevant sections to verify claims and qualifications. "
            "Use returned IDs/generations/revisions; stale references are refused. All modes enforce maxTokens, "
            "default 32000, maximum 200000, without silent clipping. includeText=false inspects response size only. "
            "After a refusal narrow scope or disclose the limit; don't retry unchanged. "
            "Combine full files and supporting passages as needed without routine permission.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {"type": "string", "enum": ["full", "outline", "selected"]},
                    "afterSection": {"type": "integer", "minimum": 0},
                    "sectionLimit": {"type": "integer", "minimum": 1, "maximum": 200},
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
                                "contentRevision": {"type": "integer", "minimum": 0},
                                **{
                                    key: {"type": "array", "maxItems": 20, "items": {"type": kind}}
                                    for key, kind in (
                                        ("sectionIds", "string"),
                                        ("evidenceIds", "string"),
                                        ("pages", "integer"),
                                        ("slides", "integer"),
                                        ("paragraphs", "integer"),
                                    )
                                },
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
            "name": ("inspect_images_library_" + slug)[:MAX_NAME].rstrip("_"),
            "description": common + "\nInspect selected returned imageIds across PDF, Word, PowerPoint and "
            "standalone images, or 1–3 PDF pages (choose IDs or pages). Returns up to 10 stored figures, source "
            "captions/absence, exact body context where retained and generated descriptions where available. "
            "No new analysis, model-description calls or ingestion writes. Works for retained assets beyond the "
            "semantic description cap. Use for visual/spatial comparisons or precise readings, then show selected "
            "relevant pictures inline. Native inputs are supplied only when supported and budgeted; delivery is "
            "reported explicitly. Only image.caption is a source caption, adjacent_inferred is uncertain attachment; "
            "headings/nearby body are context. Internal layout locators are not printed figure numbers. "
            "Source-region proximity makes images candidates, not proof they support a claim. "
            "These are selected images/source regions, never whole-file coverage. Omissions are explicit.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "documentId": {"type": "string"},
                    "generation": {"type": "integer", "minimum": 1},
                    "contentRevision": {"type": "integer", "minimum": 0},
                    "imageIds": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string"}},
                    "pages": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {"type": "integer", "minimum": 1, "maximum": 2000},
                    },
                },
                "required": ["documentId"],
            },
        },
    }
    return [search, catalogue, read, images]
