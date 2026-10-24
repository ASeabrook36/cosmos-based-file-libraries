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
