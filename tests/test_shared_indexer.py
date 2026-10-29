"""Offline shared-workflow contracts; no claims about deployed connector behavior."""

import copy

from scripts.shared_indexer_workflow import definition
from tests.test_indexer_workflow_lab import ingestion_template


def microsoft_shape():
    value = ingestion_template()
    value["actions"]["Chunk_text"]["inputs"].update(chunkingStrategy="TokenSize", PageOverlapLength=200)
    value["actions"]["Read_blob_content"]["inputs"] = {
        "parameters": {},
        "serviceProviderConfiguration": {
            "connectionName": "blob-connection_shared",
            "operationId": "readBlob",
            "serviceProviderId": "/serviceProviders/AzureBlob",
        },
    }
    value["actions"]["Select_embeddings_and_map_to_CosmosDB"]["inputs"]["select"].update(
        {
            "@{parameters('CDB_Text_shared')}": "@body('Chunk_text')['value'][item()]",
            "@{parameters('CDB_Vector_shared')}": "@body('Get_multiple_embeddings')['embeddings'][item()]",
            "totalChunks": "@{length(body('Chunk_text')['value'])}",
        }
    )
    return value


def test_one_shared_workflow_uses_trusted_dynamic_scope_and_no_cosmos_or_blob_trigger():
    original = microsoft_shape()
    frozen = copy.deepcopy(original)
    result = definition(original)
    assert original == frozen
    assert result["triggers"]["request"]["type"] == "Request"
    actions = result["actions"]
    assert "For_each" not in actions
    assert actions["Accepted"]["inputs"]["statusCode"] == 202
    assert actions["Read_blob_content"]["inputs"]["parameters"]["blobName"] == "@triggerBody()?['job']?['sourcePath']"
    processing = actions["Produce_representation"]["else"]["actions"]
    assert processing["Chunk_text"]["inputs"]["TokenSize"].endswith("['targetTokens']")
    assert processing["Chunk_text"]["inputs"]["PageOverlapLength"].endswith("['overlapTokens']")
    assert processing["Chunk_text"]["inputs"]["chunkingStrategy"] == "TokenSize"
    projection = processing["Select_embeddings_and_map_to_CosmosDB"]["inputs"]["select"]
    assert projection["embedding"] == "@body('Get_multiple_embeddings')['embeddings'][item()]"
    assert projection["totalChunks"] == "@length(body('Chunk_text')?['value'])"
    emitted = processing["Compose_result"]["inputs"]
    assert emitted["sourceBase64"] == "@outputs('Effective_extraction')?['sourceBase64']"
    assert emitted["embedding"]["modelRevision"] == "@parameters('EmbeddingModelRevision')"
    assert emitted["usage"] == {}
    save = processing["Save_result"]
    assert save["inputs"]["parameters"]["overrideIfExists"] is False
    assert save["runtimeConfiguration"]["secureData"]["properties"] == ["inputs", "outputs"]


def test_controlled_custom_extraction_branch_never_calls_embedding_or_chunking():
    result = definition(microsoft_shape())
    extract_only = result["actions"]["Produce_representation"]["actions"]
    assert set(extract_only) == {"Compose_extraction_result", "Save_extraction_result"}
    assert extract_only["Compose_extraction_result"]["inputs"]["schema"] == "fli.indexer-extraction.v1"
    assert extract_only["Compose_extraction_result"]["inputs"]["rows"] == []
