import base64
import copy
import json

import pytest

from fli.blobs import MemoryBlobs
from fli.store import MemoryDocumentStore
from scripts.indexer_publication_lab import full_text_artifact, publish, validate_output


def fixture():
    source = b"original bytes"
    artifact = {
        "schema": "indexer-lab.extraction.v1",
        "sourceName": "doc/1.pdf",
        "sourceBase64": base64.b64encode(source).decode(),
        "analysis": {"analyzeResult": {"modelId": "prebuilt-layout", "content": "complete source"}},
    }
    rows = [
        {
            "id": "doc:1:0",
            "libraryId": "lib",
            "documentId": "doc",
            "generation": 1,
            "sourceRevision": 1,
            "sourceArtifact": "lib/doc/1/extraction.json",
            "workflowVariant": "layout",
            "contentKind": "text",
            "chunkIndex": 0,
            "totalChunks": "1",
            "text": "complete source",
        }
    ]
    return source, artifact, rows


@pytest.mark.parametrize(
    "field,value",
    [
        ("libraryId", "other"),
        ("generation", 2),
        ("id", "random"),
        ("sourceArtifact", "other/source"),
        ("chunkIndex", 9),
    ],
)
def test_publication_rejects_wrong_scope_revision_or_incomplete_rows(field, value):
    source, artifact, rows = fixture()
    rows[0][field] = value
    with pytest.raises(ValueError):
        validate_output(artifact, rows, library_id="lib", document_id="doc", generation=1, source=source)


def test_frozen_original_bytes_must_match_and_full_text_is_never_clipped():
    source, artifact, rows = fixture()
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_output(artifact, rows, library_id="lib", document_id="doc", generation=1, source=b"replacement")
    result = validate_output(artifact, rows, library_id="lib", document_id="doc", generation=1, source=source)
    result = {**result, "content": "Unicode café\n" * 10000 + "final paragraph"}
    data, descriptor = full_text_artifact(result, "lib", "doc", 1)
    assert json.loads(data)[0]["text"] == result["content"]
    assert descriptor["generation"] == 1 and descriptor["tokenCount"] > 10000


async def test_publication_is_idempotent_and_does_not_resurrect_deleting_document():
    source, artifact, rows = fixture()
    docs, blobs = MemoryDocumentStore(), MemoryBlobs()
    kwargs = dict(
        library_id="lib", document_id="doc", generation=1, source=source, file_name="test.pdf", expected_previous=None
    )
    first = await publish(docs, blobs, artifact, rows, **kwargs)
    second = await publish(docs, blobs, artifact, rows, **kwargs)
    assert first["fullText"] == second["fullText"]
    deleting = copy.deepcopy(second)
    deleting["status"] = "deleting"
    await docs.replace(deleting)
    with pytest.raises(ValueError, match="deleting"):
        await publish(docs, blobs, artifact, rows, **kwargs)
