"""Generation-local, write-once Blob checkpoints. They follow normal generation purges."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from azure.core.exceptions import ResourceNotFoundError

from fli.blobs import BlobStore


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


async def load(blobs: BlobStore, path: str) -> dict | None:
    try:
        data = await blobs.download(path)
    except (KeyError, ResourceNotFoundError):
        return None
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid ingestion checkpoint")
    return value


async def freeze(blobs: BlobStore, path: str, value: dict) -> dict:
    if await blobs.create(path, encoded(value), "application/json"):
        return value
    winner = await load(blobs, path)
    if winner is None:
        # A deletion/purge raced with this attempt. Retry; never invent a winner.
        raise RuntimeError("ingestion checkpoint disappeared")
    return winner
