"""Static checks on cosmos/*.json, the only container definitions (CLAUDE.md rules 9 and 10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

COSMOS = Path(__file__).resolve().parent.parent / "cosmos"
NAMES = ("libraries", "documents", "chunks", "indexer-lab")


def load(name: str) -> dict:
    return json.loads((COSMOS / f"{name}.container.json").read_text())


@pytest.mark.parametrize("name", NAMES)
def test_explicit_indexing_with_key_paths_included_and_never_id(name):
    body = load(name)
    policy = body["indexingPolicy"]
    included = [p["path"] for p in policy["includedPaths"]]
    assert "/id/?" not in included  # E15: accepted at create, then the create fails silently
    assert {"path": "/*"} in policy["excludedPaths"]
    for key_path in body["partitionKey"]["paths"]:
        assert f"{key_path}/?" in included
    for vector_index in policy.get("vectorIndexes", []):
        for shard_path in vector_index.get("vectorIndexShardKey", []):
            assert f"{shard_path}/?" in included


def test_chunks_vector_settings_are_the_specified_ones():
    body = load("chunks")
    assert body["partitionKey"] == {"paths": ["/libraryId", "/documentId"], "kind": "MultiHash", "version": 2}
    assert body["vectorEmbeddingPolicy"]["vectorEmbeddings"] == [
        {"path": "/embedding", "dataType": "float16", "distanceFunction": "cosine", "dimensions": 3072}
    ]
    assert body["indexingPolicy"]["vectorIndexes"] == [
        {
            "path": "/embedding",
            "type": "diskANN",
            "quantizerType": "spherical",
            "indexingSearchListSize": 300,
            "vectorIndexShardKey": ["/libraryId"],
        }
    ]
    assert {"path": "/embedding/*"} in body["indexingPolicy"]["excludedPaths"]
    assert body["indexingPolicy"]["fullTextIndexes"] == [{"path": "/text"}]


def test_query_module_dtype_matches_the_stored_dtype():
    from fli.query import STORED_DTYPE

    assert STORED_DTYPE == load("chunks")["vectorEmbeddingPolicy"]["vectorEmbeddings"][0]["dataType"]


@pytest.mark.parametrize("name", NAMES)
def test_file_id_matches_container_name(name):
    assert load(name)["id"] == name
