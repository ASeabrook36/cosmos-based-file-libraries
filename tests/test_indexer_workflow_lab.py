"""Checks for reproducible workflow materialization, separate from live Azure evidence."""

import copy

import pytest

from scripts.indexer_workflow_lab import (
    baseline_definition,
    builtin_write_definition,
    layout_definition,
    processing_definition,
)


def template():
    return {
        "triggers": {"blob": {"type": "ServiceProvider", "inputs": {"name": "#workflowname#"}}},
        "actions": {
            "Project": {"type": "Select", "inputs": {"select": {"id": "@{guid()}"}}},
            "Loop": {
                "type": "Foreach",
                "foreach": "@outputs('Project')",
                "actions": {
                    "Write": {"type": "ApiConnection", "inputs": {"body": "@items('Loop')"}},
                },
            },
        },
        "outputs": {},
    }


def test_baseline_preserves_upstream_processing_and_does_not_mutate_source():
    source = template()
    before = copy.deepcopy(source)
    deployed = baseline_definition(source, "baseline")
    assert source == before
    expected = copy.deepcopy(source)
    expected["triggers"]["blob"]["inputs"]["name"] = "baseline"
    assert processing_definition(deployed) == expected
    assert deployed["actions"]["Project"]["inputs"]["select"]["id"] == "@{guid()}"


def test_redaction_is_not_added_to_control_actions_that_the_runtime_rejects():
    deployed = baseline_definition(template(), "baseline")
    loop = deployed["actions"]["Loop"]
    assert "runtimeConfiguration" not in loop
    assert loop["actions"]["Write"]["runtimeConfiguration"]["secureData"]["properties"] == ["inputs", "outputs"]
    assert deployed["actions"]["Project"]["runtimeConfiguration"]["secureData"]


@pytest.mark.parametrize("name", ["../other", "a/b", "", "Bad Name"])
def test_workflow_name_cannot_escape_package_directory(name):
    with pytest.raises(ValueError, match="workflow name"):
        baseline_definition(template(), name)


def ingestion_template():
    return {
        "actions": {
            "Read_blob_content": {"type": "ServiceProvider", "runAfter": {}},
            "Analyze_Document_probe": {
                "type": "ApiConnection",
                "inputs": {"path": "models/prebuilt-read:analyze", "queries": {"api-version": "2024-11-30"}},
                "runAfter": {"Read_blob_content": ["Succeeded"]},
            },
            "Chunk_text": {
                "type": "ChunkText",
                "inputs": {"text": "old", "TokenSize": 5000},
                "runAfter": {"Analyze_Document_probe": ["Succeeded"]},
            },
            "Get_multiple_embeddings": {"type": "ServiceProvider", "inputs": {"parameters": {"input": "chunks"}}},
            "Select_embeddings_and_map_to_CosmosDB": {"type": "Select", "inputs": {"select": {"id": "@{guid()}"}}},
            "For_each": {"type": "Foreach", "actions": {"Legacy_write": {"type": "ApiConnection"}}},
        },
        "triggers": {},
        "outputs": {},
    }


def test_builtin_bridge_preserves_random_ids_and_microsoft_extraction_chunk_embedding():
    source = ingestion_template()
    deployed = builtin_write_definition(source, "bridge")
    cleaned = processing_definition(deployed)
    for name in ["Analyze_Document_probe", "Chunk_text", "Get_multiple_embeddings"]:
        assert cleaned["actions"][name] == source["actions"][name]
    projection = deployed["actions"]["Select_embeddings_and_map_to_CosmosDB"]["inputs"]["select"]
    assert projection["id"] == "@{guid()}"
    assert "parameters(" in projection["libraryId"]
    write = deployed["actions"]["For_each"]["actions"]["Write_item"]["inputs"]
    assert write["serviceProviderConfiguration"]["operationId"] == "CreateOrUpdateDocument"
    assert "partitionKey" not in write["parameters"]  # scalar override cannot encode a hierarchical key


def test_layout_checkpoint_precedes_chunking_and_refuses_changed_immutable_source():
    source = ingestion_template()
    deployed = layout_definition(source, "layout-a")
    actions = deployed["actions"]
    branch = actions["Cached_or_analyze"]
    assert "Artifact_exists" in branch["runAfter"]
    assert (
        branch["actions"]["Validate_immutable_source"]["else"]["actions"]["Reject_changed_revision"]["inputs"][
            "runError"
        ]["code"]
        == "source_revision_changed"
    )
    fresh = branch["else"]["actions"]
    assert "prebuilt-layout" in fresh["Analyze_Document_probe"]["inputs"]["path"]
    assert fresh["Freeze_new"]["inputs"]["analysis"] == "@body('Analyze_Document_probe')"
    assert not fresh["Save_new"]["inputs"]["parameters"]["overrideIfExists"]
    assert actions["Chunk_text"]["runAfter"] == {"Effective_extraction": ["Succeeded"]}
    projection = actions["Select_embeddings_and_map_to_CosmosDB"]["inputs"]["select"]
    assert "guid()" not in projection["id"]
    assert projection["sourceArtifact"]
    assert "generation" in projection and "chunkIndex" in projection
    assert source["actions"]["Chunk_text"]["inputs"]["text"] == "old"


def test_compose_uses_secure_inputs_and_termination_has_no_unsupported_secure_flags():
    source = template()
    source["actions"]["Freeze"] = {"type": "Compose", "inputs": "source"}
    source["actions"]["Stop"] = {"type": "Terminate", "inputs": {"runStatus": "Failed"}}
    deployed = baseline_definition(source, "baseline")
    assert deployed["actions"]["Freeze"]["runtimeConfiguration"]["secureData"]["properties"] == ["inputs"]
    assert "runtimeConfiguration" not in deployed["actions"]["Stop"]


def test_connection_validation_catches_runtime_parameter_mistake_and_refuses_key_auth():
    from scripts.indexer_workflow_lab import validate_connections

    connection = {
        "serviceProvider": {"id": "/serviceProviders/AzureCosmosDB"},
        "parameterValues": {
            "accountURI": "https://example.invalid",
            "authProvider": {"Type": "ManagedServiceIdentity"},
        },
    }
    validate_connections({"serviceProviderConnections": {"cosmos": connection}})
    wrong = copy.deepcopy(connection)
    wrong["parameterValues"]["accountEndpoint"] = wrong["parameterValues"].pop("accountURI")
    with pytest.raises(ValueError, match="accountURI"):
        validate_connections({"serviceProviderConnections": {"cosmos": wrong}})
    wrong = copy.deepcopy(connection)
    wrong["parameterValues"]["authProvider"]["Type"] = "Key"
    with pytest.raises(ValueError, match="managed identity"):
        validate_connections({"serviceProviderConnections": {"cosmos": wrong}})
