"""The generation check (spec §8 step 6).

A document's chunks carry the generation that wrote them. Search may return only chunks of
the document's ``activeGeneration`` while the document is ``ready``. This hides replaced and
deleted content the moment the document record changes, before any background purge runs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def is_visible(chunk: Mapping[str, Any], document: Mapping[str, Any] | None) -> bool:
    return (
        document is not None
        and document.get("status") == "ready"
        and chunk.get("generation") == document.get("activeGeneration")
    )


def filter_visible(
    chunks: Iterable[Mapping[str, Any]], documents: Mapping[str, Mapping[str, Any]], limit: int
) -> list[Mapping[str, Any]]:
    """Keep chunks whose document is ``ready`` at the chunk's generation, in order, up to ``limit``.

    ``documents`` maps documentId to its record; a missing record hides the chunk.
    """
    kept: list[Mapping[str, Any]] = []
    for chunk in chunks:
        if is_visible(chunk, documents.get(chunk.get("documentId", ""))):
            kept.append(chunk)
            if len(kept) == limit:
                break
    return kept
