"""Identifiers and blob paths (spec §4.2, §4.3).

A chunk's id is ``{documentId}:{generation}:{chunkIndex}``. It is a pure function of its
inputs, so a retried ingest upserts the same items instead of adding duplicates.
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


def original_blob_path(library_id: str, document_id: str, file_name: str) -> str:
    return f"{library_id}/{document_id}/original/{safe_file_name(file_name)}"
