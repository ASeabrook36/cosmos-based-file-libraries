"""Immutable, generation-bound source image/caption manifests written during ingestion."""

import hashlib
import json

from fli.checkpoints import encoded
from fli.ids import text_prefix


async def store_assets(blobs, library_id, document_id, generation, source_sha256, assets, skipped, skipped_pages):
    payload = {
        "version": 1,
        "generation": generation,
        "sourceSha256": source_sha256,
        "assets": assets,
        "skipped": skipped,
        "skippedPages": skipped_pages,
    }
    data = encoded(payload)
    path = f"{text_prefix(library_id, document_id)}{generation}/image-assets.json"
    if not await blobs.create(path, data, "application/json"):
        if await blobs.download(path) != data:
            raise ValueError("immutable image assets conflict with generation preparation")
    return {
        "blobPath": path,
        "generation": generation,
        "sourceSha256": source_sha256,
        "sha256": hashlib.sha256(data).hexdigest(),
        "assetCount": len(assets),
        "skipped": skipped,
    }


async def load_assets(blobs, library_id, document):
    descriptor = document.get("imageAssets") or {}
    generation = document.get("activeGeneration")
    if descriptor.get("generation") != generation or descriptor.get("sourceSha256") != document.get("sha256"):
        raise ValueError("source image/caption assets require reprocessing")
    path = f"{text_prefix(library_id, document['id'])}{generation}/image-assets.json"
    if descriptor.get("blobPath") != path:
        raise ValueError("invalid source image manifest path")
    data = await blobs.download(path)
    if hashlib.sha256(data).hexdigest() != descriptor.get("sha256"):
        raise ValueError("invalid source image manifest digest")
    payload = json.loads(data)
    if (
        payload.get("version") != 1
        or payload.get("generation") != generation
        or payload.get("sourceSha256") != document.get("sha256")
        or not isinstance(payload.get("assets"), list)
        or len(payload["assets"]) != descriptor.get("assetCount")
        or payload.get("skipped") != descriptor.get("skipped")
    ):
        raise ValueError("incomplete source image manifest")
    return payload
