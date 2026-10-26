"""Identifiers and blob paths (spec §4.2, §4.3).

A chunk's id is ``{documentId}:{generation}:{chunkIndex}``. It is a pure function of its
inputs, so a retried ingest upserts the same items instead of adding duplicates.

Each generation's original has its own path, ``{libraryId}/{documentId}/original/{generation}/{fileName}``,
so a replacement never overwrites the original of the generation search still returns
(review finding 7). Originals stored before Phase 3 sit directly under ``original/`` and are
generation 1. Images extracted from a generation (spec §7.5) follow the same layout under
``images/{generation}/``, so the purge removes both by one rule.
"""

from __future__ import annotations

import re
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


def chunk_id(document_id: str, generation: int, chunk_index: int) -> str:
    if generation < 1 or chunk_index < 0:
        raise ValueError("generation starts at 1 and chunk indexes at 0")
    return f"{document_id}:{generation}:{chunk_index}"


def safe_file_name(file_name: str) -> str:
    """The last path segment of an uploaded name, without control characters.

    Uploaded names come from users. A name such as ``../../other/x.pdf`` must never move a
    blob out of its document's prefix: HTTP clients resolve dot segments in URLs.
    """
    name = re.split(r"[\\/]", file_name)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().strip(".")
    return name[:255] or "file"


def original_prefix(library_id: str, document_id: str) -> str:
    return f"{library_id}/{document_id}/original/"


def original_blob_path(library_id: str, document_id: str, generation: int, file_name: str) -> str:
    if generation < 1:
        raise ValueError("generation starts at 1")
    return f"{original_prefix(library_id, document_id)}{generation}/{safe_file_name(file_name)}"


def image_prefix(library_id: str, document_id: str) -> str:
    return f"{library_id}/{document_id}/images/"


def text_prefix(library_id: str, document_id: str) -> str:
    return f"{library_id}/{document_id}/text/"


def text_blob_path(library_id: str, document_id: str, generation: int) -> str:
    if generation < 1:
        raise ValueError("generation starts at 1")
    return f"{text_prefix(library_id, document_id)}{generation}/sections.json"


def image_blob_path(library_id: str, document_id: str, generation: int, image_id: str, ext: str) -> str:
    if generation < 1:
        raise ValueError("generation starts at 1")
    if not re.fullmatch(r"[A-Za-z0-9-]+", image_id) or not re.fullmatch(r"[a-z0-9]{1,5}", ext):
        raise ValueError("image ids and extensions are generated, never taken from the file")
    return f"{image_prefix(library_id, document_id)}{generation}/{image_id}.{ext}"


def blob_generation(blob_path: str) -> int:
    """The generation an original, image or text artifact belongs to; 1 for legacy originals.

    Only originals stored before Phase 3 lack one.
    """
    for folder in ("/original/", "/images/", "/text/"):
        if folder in blob_path:
            _, _, rest = blob_path.partition(folder)
            head, nested, _ = rest.partition("/")
            return int(head) if nested and head.isdigit() else 1
    raise ValueError("not an original, image or text artifact path")
