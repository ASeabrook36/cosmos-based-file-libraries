"""Materialize the pinned Microsoft Indexer into a PRIVATE deployment package.

The baseline preserves upstream processing semantics. Parameter substitution and
secure run-history settings are the only changes. This opt-in tool never deploys
resources or changes the normal application.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import zipfile
from pathlib import Path

from scripts.microsoft_reuse_lab import ROOT, private_directory, save

TEMPLATE_SHA256 = "52a2ff79422ea461cb38c1ebc4e2b17351b893a87c1a15b8ea46b3eb6b386e55"


def secure_actions(actions: dict) -> None:
    for action in actions.values():
        # Control actions reject secureData; redact the data-bearing child actions.
        if action["type"].lower() not in {"foreach", "if", "scope", "switch", "until", "terminate"}:
            # Compose implicitly secures its output when inputs are secured; explicit outputs is rejected.
            properties = ["inputs"] if action["type"].lower() == "compose" else ["inputs", "outputs"]
            action.setdefault("runtimeConfiguration", {})["secureData"] = {"properties": properties}
        if "actions" in action:
            secure_actions(action["actions"])
        if "else" in action:
            secure_actions(action["else"].get("actions", {}))


def baseline_definition(template: dict, workflow_name: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", workflow_name):
        raise ValueError("invalid workflow name")
    definition = json.loads(json.dumps(template).replace("#workflowname#", workflow_name))
    secure_actions(definition["actions"])
    return definition


def processing_definition(definition: dict) -> dict:
    """Compare processing logic independently of operational run-history protection."""
    result = copy.deepcopy(definition)

    def clean(actions: dict) -> None:
        for action in actions.values():
            runtime = action.get("runtimeConfiguration", {})
            runtime.pop("secureData", None)
            if not runtime:
                action.pop("runtimeConfiguration", None)
            if "actions" in action:
                clean(action["actions"])
            if "else" in action:
                clean(action["else"].get("actions", {}))

    clean(result["actions"])
    return result


def builtin_write_definition(definition: dict, workflow_name: str) -> dict:
    """Minimal transport bridge; retain read/chunk/embed and random upstream IDs."""
    result = copy.deepcopy(definition)
    actions = result["actions"]
    projection = actions["Select_embeddings_and_map_to_CosmosDB"]["inputs"]["select"]
    projection.update(
        {
            "libraryId": f"@parameters('Lab_Library_{workflow_name}')",
            "documentId": "@triggerBody()?['name']",
            "generation": 1,
            "chunkIndex": "@item()",
            "contentKind": "text",
            "workflowVariant": "builtin-write",
        }
    )
    actions["For_each"]["actions"] = {
        "Write_item": {
            "type": "ServiceProvider",
            "inputs": {
                "parameters": {
                    "databaseId": f"@parameters('CosmosDB_Database_{workflow_name}')",
                    "containerId": f"@parameters('CosmosDB_Collection_{workflow_name}')",
                    "item": "@items('For_each')",
                    # Let the modern SDK derive every hierarchical component from the item.
                    # The connector's string partitionKey parameter encodes a single scalar.
                    "isUpsert": True,
                },
                "serviceProviderConfiguration": {
                    "connectionName": f"cosmos-builtin_{workflow_name}",
                    "operationId": "CreateOrUpdateDocument",
                    "serviceProviderId": "/serviceProviders/AzureCosmosDB",
                },
                # Bound the lab write probe; retry experiments explicitly replay the workflow.
                "retryPolicy": {"type": "none"},
            },
        },
    }
    secure_actions(result["actions"])
    return result


def layout_definition(definition: dict, workflow_name: str) -> dict:
    """Versioned lab adapter: preserve full extraction, reuse it on retry, bind rows.

    Source names must be server-owned document-id/revision.extension paths. Publication
    is a separate adapter operation after all rows and the immutable artifact exist.
    """
    result = builtin_write_definition(definition, workflow_name)
    actions = result["actions"]
    analysis_name = next(name for name in actions if name.startswith("Analyze_Document"))
    analysis = actions.pop(analysis_name)
    analysis["inputs"]["path"] = analysis["inputs"]["path"].replace("prebuilt-read", "prebuilt-layout")
    analysis["inputs"]["queries"]["stringIndexType"] = "unicodeCodePoint"
    analysis["runAfter"] = {}
    artifact = (
        f"@concat(parameters('Lab_Library_{workflow_name}'),'/',"
        "first(split(triggerBody()?['name'],'/')),'/',"
        "first(split(last(split(triggerBody()?['name'],'/')),'.')),'/extraction.json')"
    )

    def blob(operation: str, parameters: dict, after: dict | None = None) -> dict:
        return {
            "type": "ServiceProvider",
            "inputs": {
                "parameters": parameters,
                "serviceProviderConfiguration": {
                    "connectionName": f"blob-connection_{workflow_name}",
                    "operationId": operation,
                    "serviceProviderId": "/serviceProviders/AzureBlob",
                },
            },
            "runAfter": after or {},
        }

    actions["Artifact_exists"] = blob(
        "blobExists", {"containerName": "artifacts", "blobName": artifact}, {"Read_blob_content": ["Succeeded"]}
    )
    actions["Cached_or_analyze"] = {
        "type": "If",
        "expression": "@equals(body('Artifact_exists')?['isBlobExists'],true)",
        "runAfter": {"Artifact_exists": ["Succeeded"]},
        "actions": {
            "Read_cached": blob("readBlob", {"containerName": "artifacts", "blobName": artifact}),
            "Decode_cached": {
                "type": "Compose",
                "inputs": "@json(body('Read_cached')?['content'])",
                "runAfter": {"Read_cached": ["Succeeded"]},
            },
            "Validate_immutable_source": {
                "type": "If",
                "expression": (
                    "@equals(outputs('Decode_cached')?['sourceBase64'],base64(body('Read_blob_content')?['content']))"
                ),
                "runAfter": {"Decode_cached": ["Succeeded"]},
                "actions": {},
                "else": {
                    "actions": {
                        "Reject_changed_revision": {
                            "type": "Terminate",
                            "inputs": {
                                "runStatus": "Failed",
                                "runError": {
                                    "code": "source_revision_changed",
                                    "message": "An immutable source revision changed.",
                                },
                            },
                        }
                    }
                },
            },
        },
        "else": {
            "actions": {
                analysis_name: analysis,
                "Freeze_new": {
                    "type": "Compose",
                    "inputs": {
                        "schema": "indexer-lab.extraction.v1",
                        "sourceBase64": "@base64(body('Read_blob_content')?['content'])",
                        "sourceName": "@triggerBody()?['name']",
                        "analysis": f"@body('{analysis_name}')",
                        "analysisOutput": f"@outputs('{analysis_name}')",
                    },
                    "runAfter": {analysis_name: ["Succeeded"]},
                },
                "Save_new": blob(
                    "uploadBlob",
                    {
                        "containerName": "artifacts",
                        "blobName": artifact,
                        "content": "@string(outputs('Freeze_new'))",
                        "overrideIfExists": False,
                    },
                    {"Freeze_new": ["Succeeded"]},
                ),
            }
        },
    }
    actions["Effective_extraction"] = {
        "type": "Compose",
        "inputs": (
            "@if(equals(body('Artifact_exists')?['isBlobExists'],true),outputs('Decode_cached'),outputs('Freeze_new'))"
        ),
        "runAfter": {"Cached_or_analyze": ["Succeeded"]},
    }
    actions["Chunk_text"]["inputs"]["text"] = (
        "@outputs('Effective_extraction')?['analysis']?['analyzeResult']?['content']"
    )
    actions["Chunk_text"]["runAfter"] = {"Effective_extraction": ["Succeeded"]}
    projection = actions["Select_embeddings_and_map_to_CosmosDB"]["inputs"]["select"]
    projection.update(
        {
            "id": (
                "@concat(first(split(triggerBody()?['name'],'/')),':',"
                "first(split(last(split(triggerBody()?['name'],'/')),'.')),':',string(item()))"
            ),
            "documentId": "@first(split(triggerBody()?['name'],'/'))",
            "generation": "@int(first(split(last(split(triggerBody()?['name'],'/')),'.')))",
            "sourceRevision": "@int(first(split(last(split(triggerBody()?['name'],'/')),'.')))",
            "sourceArtifact": artifact,
            "workflowVariant": "layout",
            "fileName": "@triggerBody()?['name']",
            "fileType": "@last(split(triggerBody()?['name'],'.'))",
            "heading": None,
            "location": {},
        }
    )
    secure_actions(actions)
    return result


def validate_connections(connections: dict) -> None:
    for connection in connections.get("serviceProviderConnections", {}).values():
        values = connection.get("parameterValues", {})
        if values.get("authProvider", {}).get("Type") != "ManagedServiceIdentity":
            raise ValueError("lab service-provider connections require managed identity")
        if connection["serviceProvider"]["id"] == "/serviceProviders/AzureCosmosDB" and not values.get("accountURI"):
            raise ValueError("Cosmos built-in managed identity requires accountURI")
    for connection in connections.get("managedApiConnections", {}).values():
        if connection.get("authentication", {}).get("type") != "ManagedServiceIdentity":
            raise ValueError("lab managed API connections require managed identity")


def package(source: bytes, config: dict, destination: Path) -> dict:
    if hashlib.sha256(source).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("upstream template checksum mismatch")
    validate_connections(config["connections"])
    name = config["workflowName"]
    template = json.loads(source)
    definition = baseline_definition(template, name)
    expected = json.loads(source.decode().replace("#workflowname#", name))
    if processing_definition(definition) != expected:
        raise ValueError("baseline processing definition changed")
    variant = config.get("variant", "baseline")
    if variant == "builtin-write":
        definition = builtin_write_definition(definition, name)
    elif variant == "layout":
        definition = layout_definition(definition, name)
    elif variant != "baseline":
        raise ValueError("unknown workflow variant")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(
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
        output.writestr("connections.json", json.dumps(config["connections"]))
        output.writestr("parameters.json", json.dumps(config["parameters"]))
        output.writestr(f"{name}/workflow.json", json.dumps({"kind": "Stateful", "definition": definition}))
    destination.chmod(0o600)
    return {"variant": variant, "upstreamSha256": TEMPLATE_SHA256, "processingMatchesUpstream": variant == "baseline"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    work = private_directory(args.work_dir)
    if args.runtime_config.resolve().is_relative_to(ROOT):
        parser.error("runtime connection settings must be outside the repository")
    config = json.loads(args.runtime_config.read_text())
    receipt = package(args.template.read_bytes(), config, work / "baseline.zip")
    save(work / "package-receipt.json", receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
