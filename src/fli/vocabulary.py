"""What a library knows about its own filters (spec §4.1 ``tagVocabulary`` and ``filterValues``).

Recomputed from the library's documents after every ingest, deletion and retag (spec §7.2 step
11). The tool definition (§6.4) offers only the filters that have values here, and search
coverage (§9.2) reads it to say what a filter left out. Neither reads documents per search.

Only searchable documents count: those with an active generation that are not ``deleting``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

MISSING_FIELDS = ("period", "author", "authoredAt")


def searchable(documents: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [d for d in documents if d.get("activeGeneration") is not None and d.get("status") != "deleting"]


def tag_vocabulary(documents: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """``{key: {value: documentCount}}`` from the documents' facets."""
    vocabulary: dict[str, Counter[str]] = {}
    for d in documents:
        for facet in d.get("facets", []):
            key, _, value = facet.partition("=")
            vocabulary.setdefault(key, Counter())[value] += 1
    return {k: dict(v) for k, v in vocabulary.items()}


def filter_values(documents: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Authors, file types and content kinds with document counts; the period range; what is missing."""
    authors: Counter[str] = Counter()
    file_types: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    missing: Counter[str] = Counter({field: 0 for field in MISSING_FIELDS})
    starts: list[str] = []
    ends: list[str] = []
    for d in documents:
        if d.get("author"):
            authors[d["author"]] += 1
        else:
            missing["author"] += 1
        if d.get("fileType"):
            file_types[d["fileType"]] += 1
        images = d.get("imageCount", 0) or 0
        if (d.get("chunkCount", 0) or 0) > images:
            kinds["text"] += 1
        if images:
            kinds["image"] += 1
        if d.get("periodStart") and d.get("periodEnd"):
            starts.append(d["periodStart"])
            ends.append(d["periodEnd"])
        else:
            missing["period"] += 1
        if not d.get("authoredAt"):
            missing["authoredAt"] += 1
    return {
        "authors": dict(authors),
        "fileTypes": dict(file_types),
        "contentKinds": dict(kinds),
        "period": {"from": min(starts), "to": max(ends)} if starts else None,
        "missing": dict(missing),
    }
