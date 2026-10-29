"""Opt-in validation/publication helpers for frozen Microsoft workflow output.

These helpers do not extract, chunk, embed or serve a new public API. A lab driver
can inject the existing API with real isolated Cosmos/Blob stores after validation.
"""

from __future__ import annotations

import base64
import hashlib

from fli.checkpoints import encoded
from fli.chunking import default_tokenizer


def validate_output(
    artifact: dict, rows: list[dict], *, library_id: str, document_id: str, generation: int, source: bytes
) -> dict:
    source_name = artifact.get("sourceName", "")
    if artifact.get("schema") != "indexer-lab.extraction.v1":
        raise ValueError("unknown frozen extraction schema")
    try:
        frozen_source = base64.b64decode(artifact["sourceBase64"], validate=True)
    except (KeyError, ValueError) as exc:
        raise ValueError("invalid original-source binding") from exc
    if frozen_source != source or not source_name.startswith(f"{document_id}/{generation}."):
        raise ValueError("original-source binding mismatch")
    result = artifact["analysis"]["analyzeResult"]
    if result.get("modelId") != "prebuilt-layout" or not isinstance(result.get("content"), str):
        raise ValueError("expected source layout response")
    if not rows:
        raise ValueError("no completed source rows")
    path = f"{library_id}/{document_id}/{generation}/extraction.json"
    indexes = []
    for row in rows:
        if (
            row.get("libraryId") != library_id
            or row.get("documentId") != document_id
            or type(row.get("generation")) is not int
            or row["generation"] != generation
            or row.get("sourceRevision") != generation
            or row.get("sourceArtifact") != path
            or row.get("workflowVariant") != "layout"
            or row.get("contentKind") != "text"
            or type(row.get("chunkIndex")) is not int
            or not isinstance(row.get("text"), str)
        ):
            raise ValueError("workflow row scope/provenance mismatch")
        index = row["chunkIndex"]
        if row["id"] != f"{document_id}:{generation}:{index}" or int(row.get("totalChunks", -1)) != len(rows):
            raise ValueError("workflow row identity/count mismatch")
        indexes.append(index)
    if sorted(indexes) != list(range(len(rows))):
        raise ValueError("source row set is incomplete or duplicated")
    return result


def full_text_artifact(result: dict, library_id: str, document_id: str, generation: int) -> tuple[bytes, dict]:
    # Preserve every character in the provider's complete content. Logical section
    # reads are not inferred from this compatibility artifact; the full graph stays separate.
    data = encoded([{"heading": None, "text": result["content"], "location": {}}])
    tokenizer = default_tokenizer()
    descriptor = {
        "blobPath": f"{library_id}/{document_id}/text/{generation}/sections.json",
        "generation": generation,
        "sectionCount": 1,
        "tokenizer": tokenizer.name,
        "tokenCount": len(tokenizer.encode(data.decode())),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return data, descriptor


async def publish(
    documents,
    blobs,
    artifact: dict,
    rows: list[dict],
    *,
    library_id: str,
    document_id: str,
    generation: int,
    source: bytes,
    file_name: str,
    expected_previous: int | None,
    source_images_omitted: int | None = None,
) -> dict:
    result = validate_output(
        artifact, rows, library_id=library_id, document_id=document_id, generation=generation, source=source
    )
    omitted = len(result.get("figures", [])) if source_images_omitted is None else source_images_omitted
    if type(omitted) is not int or omitted < 0:
        raise ValueError("invalid source-image coverage")
    data, full = full_text_artifact(result, library_id, document_id, generation)
    if not await blobs.create(full["blobPath"], data, "application/json"):
        if hashlib.sha256(await blobs.download(full["blobPath"])).hexdigest() != full["sha256"]:
            raise ValueError("immutable full-source artifact changed")
    current = await documents.get(library_id, document_id)
    if current and current.get("status") == "deleting":
        raise ValueError("document is deleting")
    if current and current.get("activeGeneration") == generation:
        if current.get("fullText") != full or current.get("sha256") != hashlib.sha256(source).hexdigest():
            raise ValueError("published source revision conflicts")
        return current
    if (current or {}).get("activeGeneration") != expected_previous:
        raise ValueError("publication pointer changed")
    record = {
        **(current or {}),
        "id": document_id,
        "documentId": document_id,
        "libraryId": library_id,
        "fileName": file_name,
        "fileType": file_name.rsplit(".", 1)[-1],
        "sizeBytes": len(source),
        "sha256": hashlib.sha256(source).hexdigest(),
        "status": "ready",
        "generation": generation,
        "activeGeneration": generation,
        "pendingGeneration": None,
        "contentRevision": 1,
        "metadataVersion": 0,
        "publishedChunkCount": len(rows),
        "chunkCount": len(rows),
        "fullText": full,
        "imageCount": 0,
        "sourceImageCount": 0,
        "imagesSkipped": omitted,
        "sourceImagesSkipped": omitted,
        "imageStatus": "failed" if omitted else "none",
        "imagePendingCount": 0,
        "facets": [],
        "tags": [],
        "author": None,
        "authoredAt": None,
        "extraction": {"method": "logicapps-prebuilt-layout", "pageCount": len(result.get("pages", []))},
        "structureArtifact": {
            "blobPath": f"{library_id}/{document_id}/{generation}/extraction.json",
            "generation": generation,
        },
    }
    return await documents.replace(record) if current else await documents.create(record)
