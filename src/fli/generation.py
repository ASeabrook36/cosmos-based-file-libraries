"""The generation check (spec §8 step 6).

A document's chunks carry the generation that wrote them. Search may return only chunks of
the document's ``activeGeneration``, and nothing of a document that is ``deleting``. This hides
replaced and deleted content the moment the document record changes, before any background
purge runs.

``status`` is otherwise ignored (evidence.md E26). While a replacement is queued, processing or
has failed, the previous generation is still active and stays searchable. ``activeGeneration``
is ``None`` until the first generation commits, so a first upload is never half-visible.

``generation`` on the record is the newest generation ever assigned. It only increases, so a
generation number is never reused, and a generation below it that is neither active nor
pending can never become visible again. That holds even when the record was read from a
replica that lags (evidence.md E28): such a read can only under-report the newest generation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def latest_generation(document: Mapping[str, Any]) -> int:
    """The newest generation assigned. Records written before Phase 3 lack the field: they had one, generation 1."""
    known = (document.get("generation"), document.get("activeGeneration"), document.get("pendingGeneration"), 1)
    return max(g for g in known if g is not None)


def is_obsolete(generation: int, document: Mapping[str, Any]) -> bool:
    """True when chunks or an original of ``generation`` can be deleted for good.

    Neither active nor pending, and not newer than the newest generation this read knows: a
    stale read may miss a newer generation, so what lies above it is left for a later purge.
    """
    keep = {document.get("activeGeneration"), document.get("pendingGeneration")}
    return generation not in keep and generation <= latest_generation(document)


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
