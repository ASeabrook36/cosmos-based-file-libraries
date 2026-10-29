"""Build a shared request-driven Indexer workflow from the pinned Microsoft template.

Offline transformation only. Uses the existing lab's source checkpointing and
Microsoft acquisition/layout/chunk/embedding stages; removes every Cosmos writer.
Deploy behind Entra authentication restricted to the controller managed identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

from scripts.indexer_workflow_lab import (
    TEMPLATE_SHA256,
    baseline_definition,
    layout_definition,
    secure_actions,
    validate_connections,
)
from scripts.microsoft_reuse_lab import private_directory

VERSION = "shared-indexer-v1"
NAME = "shared"


def definition(template: dict) -> dict:
    value = layout_definition(baseline_definition(template, NAME), NAME)
    # Normal generation-local paths make replacement/deletion cleanup cover all output.
    artifact = (
        "@concat(triggerBody()?['job']?['libraryId'],'/',triggerBody()?['job']?['documentId'],'/text/',"
        "string(triggerBody()?['job']?['generation']),'/microsoft-extraction.json')"
    )
    old_artifact = value["actions"]["Artifact_exists"]["inputs"]["parameters"]["blobName"]
    source_name = (
        "concat(triggerBody()?['job']?['documentId'],'/',string(triggerBody()?['job']?['generation']),'.source')"
    )

    def rewrite(item):
        if isinstance(item, str):
            if item == old_artifact:
                return artifact
            return item.replace("parameters('Lab_Library_shared')", "triggerBody()?['job']?['libraryId']").replace(
                "triggerBody()?['name']", source_name
            )
        if isinstance(item, list):
            return [rewrite(x) for x in item]
        if isinstance(item, dict):
            return {k: rewrite(v) for k, v in item.items()}
        return item

    value = rewrite(value)
    value["triggers"] = {
        "request": {
            "type": "Request",
            "kind": "Http",
            "inputs": {
                "method": "POST",
                "schema": {
                    "type": "object",
                    "required": ["job", "resultPath"],
                    "properties": {"job": {"type": "object"}, "resultPath": {"type": "string"}},
                },
            },
        }
    }
    actions = value["actions"]
    read = actions["Read_blob_content"]
    read["inputs"]["parameters"] = {
        "containerName": "@parameters('SourceContainer')",
        "blobName": "@triggerBody()?['job']?['sourcePath']",
    }
    read["runAfter"] = {"Accepted": ["Succeeded"]}
    actions["Accepted"] = {"type": "Response", "kind": "Http", "inputs": {"statusCode": 202}}
    # Result rows remain private staging output; only the Python controller can publish.
    actions.pop("For_each")
    projection_name = "Select_embeddings_and_map_to_CosmosDB"
    projection = actions[projection_name]["inputs"]["select"]
    # Existing upstream projection carries the exact Microsoft text/vector expressions.
    upstream = template["actions"][projection_name]["inputs"]["select"]
    text_key, vector_key = "@{parameters('CDB_Text_shared')}", "@{parameters('CDB_Vector_shared')}"
    if text_key not in projection or vector_key not in projection or "totalChunks" not in upstream:
        raise ValueError("pinned Microsoft projection shape changed")
    projection["text"] = projection.pop(text_key)
    projection["embedding"] = projection.pop(vector_key)
    projection["totalChunks"] = "@length(body('Chunk_text')?['value'])"
    # Preserve all unknown upstream fields privately. Validation strips them on publication.
    result = {
        "schema": "fli.indexer-result.v1",
        "workflowVersion": VERSION,
        "job": "@triggerBody()?['job']",
        "sourceBase64": "@outputs('Effective_extraction')?['sourceBase64']",
        "embedding": {
            "model": "@parameters('EmbeddingModel')",
            "modelRevision": "@parameters('EmbeddingModelRevision')",
            "dimensions": "@parameters('EmbeddingDimensions')",
        },
        "analysis": "@outputs('Effective_extraction')?['analysis']",
        "rows": f"@body('{projection_name}')",
        # No estimate is substituted when the connector doesn't expose usage.
        "usage": {},
    }
    actions["Save_result"] = {
        "type": "ServiceProvider",
        "runAfter": {projection_name: ["Succeeded"]},
        "inputs": {
            "parameters": {
                "containerName": "@parameters('SourceContainer')",
                "blobName": "@triggerBody()?['resultPath']",
                "content": result,
                "overrideIfExists": False,
            },
            "serviceProviderConfiguration": read["inputs"]["serviceProviderConfiguration"].copy()
            | {"operationId": "uploadBlob"},
        },
    }
    # Connector upload expects serialized JSON, rather than an object or base64 guess.
    actions["Compose_result"] = {"type": "Compose", "inputs": result, "runAfter": {projection_name: ["Succeeded"]}}
    actions["Save_result"]["inputs"]["parameters"]["content"] = "@string(outputs('Compose_result'))"
    actions["Save_result"]["runAfter"] = {"Compose_result": ["Succeeded"]}

    def containers(nodes):
        for action in nodes.values():
            inputs = action.get("inputs", {})
            parameters = inputs.get("parameters", {}) if isinstance(inputs, dict) else {}
            if parameters.get("containerName") == "artifacts":
                parameters["containerName"] = "@parameters('SourceContainer')"
            if "actions" in action:
                containers(action["actions"])
            if "else" in action:
                containers(action["else"].get("actions", {}))

    containers(actions)
    chunk = actions["Chunk_text"]["inputs"]
    # Preserve actual upstream casing/settings shape; fail closed if it changes.
    if chunk.get("chunkingStrategy") != "TokenSize" or "TokenSize" not in chunk or "PageOverlapLength" not in chunk:
        raise ValueError("pinned Microsoft chunk settings changed")
    for key in list(chunk):
        if key.lower() == "tokensize":
            chunk[key] = "@triggerBody()?['job']?['profile']?['chunking']?['targetTokens']"
        elif key.lower() in {"pageoverlaplength"}:
            chunk[key] = "@triggerBody()?['job']?['profile']?['chunking']?['overlapTokens']"
    value.setdefault("parameters", {}).update(
        {
            "EmbeddingModel": {"type": "String", "defaultValue": "text-embedding-3-large"},
            "EmbeddingModelRevision": {"type": "String"},
            "EmbeddingDimensions": {"type": "Int", "defaultValue": 3072},
        }
    )
    value.setdefault("parameters", {})["SourceContainer"] = {"type": "String", "defaultValue": "files"}
    # Controlled component runs share the identical extractor/model/version, but the
    # Python side requests extraction only and applies its own 500/50 chunk boundaries.
    extraction = {**result, "schema": "fli.indexer-extraction.v1", "rows": [], "embedding": None}
    extraction_save = json.loads(json.dumps(actions["Save_result"]))
    extraction_save["inputs"]["parameters"]["content"] = "@string(outputs('Compose_extraction_result'))"
    extraction_save["runAfter"] = {"Compose_extraction_result": ["Succeeded"]}
    processing = {
        key: actions.pop(key)
        for key in ("Chunk_text", "Get_multiple_embeddings", projection_name, "Compose_result", "Save_result")
    }
    processing["Chunk_text"]["runAfter"] = {}
    actions["Produce_representation"] = {
        "type": "If",
        "expression": "@equals(triggerBody()?['job']?['profile']?['extractionOnly'],true)",
        "runAfter": {"Effective_extraction": ["Succeeded"]},
        "actions": {
            "Compose_extraction_result": {"type": "Compose", "inputs": extraction, "runAfter": {}},
            "Save_extraction_result": extraction_save,
        },
        "else": {"actions": processing},
    }
    secure_actions(actions)
    return value


def package(source: bytes, config: dict, destination: Path) -> dict:
    if hashlib.sha256(source).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Microsoft template digest differs from pinned source")
    validate_connections(config["connections"])
    value = definition(json.loads(source))
    parameters = config["parameters"]
    references = set(re.findall(r"parameters\('([^']+)'\)", json.dumps(value)))
    defaults = {key for key, item in value.get("parameters", {}).items() if "defaultValue" in item}
    if references - parameters.keys() - defaults:
        raise ValueError("runtime parameters do not bind the shared workflow")
    if not parameters.get("EmbeddingModelRevision", {}).get("value"):
        raise ValueError("EmbeddingModelRevision must identify the configured model version")
    providers = config["connections"].get("serviceProviderConnections", {})
    managed = config["connections"].get("managedApiConnections", {})
    if not {"blob-connection_shared", "aoai-connection_shared"} <= providers.keys():
        raise ValueError("Blob and OpenAI managed-identity connections required")
    if "formrecognizer_shared" not in managed:
        raise ValueError("Document Intelligence managed-identity connection required")
    private_directory(destination.parent)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "host.json",
            json.dumps(
                {
                    "version": "2.0",
                    "extensionBundle": {
                        "id": "Microsoft.Azure.Functions.ExtensionBundle.Workflows",
                        "version": "[1.*, 2.0.0)",
                    },
                }
            ),
        )
        archive.writestr("connections.json", json.dumps(config["connections"]))
        archive.writestr("parameters.json", json.dumps(parameters))
        archive.writestr("shared/workflow.json", json.dumps({"kind": "Stateful", "definition": value}))
    destination.chmod(0o600)
    return {
        "schema": "fli.shared-indexer-package.v1",
        "upstreamSha256": TEMPLATE_SHA256,
        "workflowVersion": VERSION,
        "packageSha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, help="private parameters/connections JSON; output a ZIP")
    args = parser.parse_args()
    source = args.template.read_bytes()
    if hashlib.sha256(source).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Microsoft template digest differs from pinned source")
    if args.runtime_config:
        if args.runtime_config.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError("runtime configuration must remain outside the repository")
        package(source, json.loads(args.runtime_config.read_text()), args.output)
        return
    private_directory(args.output.parent)
    args.output.write_text(json.dumps({"definition": definition(json.loads(source)), "kind": "Stateful"}, indent=2))


if __name__ == "__main__":
    main()
