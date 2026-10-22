"""The generation check (spec §8 step 6).

A document's chunks carry the generation that wrote them. Search may return only chunks of
the document's ``activeGeneration``, and nothing of a document that is ``deleting``. This hides
replaced and deleted content the moment the document record changes, before any background
purge runs.

``status`` is otherwise ignored (evidence.md E26). While a replacement is queued, processing or
has failed, the previous generation is still active and stays searchable. ``activeGeneration``
is ``None`` until the first generation commits, so a first upload is never half-visible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def is_visible(chunk: Mapping[str, Any], document: Mapping[str, Any] | None) -> bool:
    return (
        document is not None
        and document.get("status") != "deleting"
        and document.get("activeGeneration") is not None
        and chunk.get("generation") == document.get("activeGeneration")
    )


def filter_visible(
    chunks: Iterable[Mapping[str, Any]], documents: Mapping[str, Mapping[str, Any]], limit: int
) -> list[Mapping[str, Any]]:
    """Keep chunks of their document's active generation, in order, up to ``limit``.

    ``documents`` maps documentId to its record; a missing record hides the chunk.
    """
    kept: list[Mapping[str, Any]] = []
    for chunk in chunks:
        if is_visible(chunk, documents.get(chunk.get("documentId", ""))):
            kept.append(chunk)
            if len(kept) == limit:
                break
    return kept
