"""Read-only, private preflight for the bounded granular qualification."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path

from fli.config import Settings
from fli.structure_lab import digest
from scripts.canary import definition_mismatches, target_fingerprint
from scripts.hybrid_quality_scope import save
from scripts.qualify_granular_navigation import REPO, require, sha
from scripts.retrieval_controls import private_path


async def protected_snapshot(database, blobs, library_ids=None):
    """Whole existing library inventory; chunk projections always bind one library."""
    libraries = database.get_container_client("libraries")
    if library_ids is None:
        rows = [r async for r in libraries.query_items("SELECT TOP 1000 * FROM c")]
        require(len(rows) < 1000, "library inventory exceeded bounded census")
    else:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        rows = []
        for identity in library_ids:
            try:
                rows.append(await libraries.read_item(identity, partition_key=identity))
            except CosmosResourceNotFoundError:
                rows.append({"id": identity, "absent": True})
    result = {}
    for library in rows:
        identity = library["id"]
        params = [{"name": "@libraryId", "value": identity}]
        documents = [
            r
            async for r in database.get_container_client("documents").query_items(
                "SELECT TOP 1000 * FROM c WHERE c.libraryId = @libraryId",
                parameters=params,
                partition_key=identity,
            )
        ]
        chunks = [
            r
            async for r in database.get_container_client("chunks").query_items(
                "SELECT TOP 10000 c.id, c.documentId, c.generation, c.contentHash, c._etag FROM c "
                "WHERE c.libraryId = @libraryId",
                parameters=params,
                partition_key=[identity],
            )
        ]
        require(len(documents) < 1000 and len(chunks) < 10000, "protected census ceiling")
        paths = []
        async for item in blobs.list_blobs(name_starts_with=identity + "/"):
            require(len(paths) < 10000, "protected blob census ceiling")
            paths.append({"name": item.name, "etag": item.etag, "size": item.size})
        result[identity] = {
            "librarySha256": digest(library),
            "documentsSha256": digest(sorted(documents, key=lambda x: x["id"])),
            "chunksSha256": digest(sorted(chunks, key=lambda x: (x["documentId"], x["id"]))),
            "blobsSha256": digest(sorted(paths, key=lambda x: x["name"])),
            "documentCount": len(documents),
            "chunkCount": len(chunks),
            "blobCount": len(paths),
        }
    return result


def management_get(url, output):
    result = subprocess.run(
        ["az", "rest", "--method", "get", "--url", url, "-o", "json"], capture_output=True, text=True, timeout=60
    )
    if result.returncode:
        save(output.with_suffix(".error.json"), {"exit": result.returncode, "stderr": result.stderr})
        raise RuntimeError("Azure read-only management request failed; private error retained")
    value = json.loads(result.stdout)
    save(output, value)
    return value


async def preflight(root, account_seed, canary_path):
    root = private_path(root)
    require(not (root / "preflight.json").exists(), "preflight receipt already exists")
    env = json.loads((root / "environment.json").read_text())
    os.environ.update(env)
    settings = Settings.from_env()
    require(settings.environment == "local" and settings.auth_mode == "dev", "local authorization required")
    require(settings.search_mode == "vector", "vector-only qualification required")
    seed = json.loads(private_path(account_seed).read_text())
    base = "https://management.azure.com" + seed["id"]
    account = management_get(base + "?api-version=2024-10-01", root / "account.json")
    require(
        account["properties"]["endpoint"].rstrip("/") == settings.aoai_endpoint.rstrip("/"), "model account differs"
    )
    deployments = management_get(base + "/deployments?api-version=2024-10-01", root / "deployments.json")
    subscription = seed["id"].split("/")[2]
    management_get(
        "https://management.azure.com/subscriptions/"
        + subscription
        + "/providers/Microsoft.CognitiveServices/locations/"
        + account["location"]
        + "/usages?api-version=2024-10-01",
        root / "quota.json",
    )
    embedding = next(d for d in deployments["value"] if d["name"] == settings.aoai_embedding_deployment)
    require(embedding["properties"]["provisioningState"] == "Succeeded", "embedding deployment not ready")
    require(embedding["properties"]["model"]["name"] == "text-embedding-3-large", "embedding model differs")
    require(
        embedding["properties"]["model"]["version"] == settings.aoai_embedding_model_revision,
        "embedding revision differs",
    )
    canary = json.loads(private_path(canary_path).read_text())
    inherited = json.loads((root / "inherited-preflight.json").read_text())
    require(sha(canary_path) == inherited["canarySha256"] and canary["passed"], "inherited canary binding differs")
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient

    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential())
        await credential.get_token("https://cognitiveservices.azure.com/.default")
        cosmos = await stack.enter_async_context(CosmosClient(settings.cosmos_endpoint, credential=credential))
        database = cosmos.get_database_client(settings.cosmos_database)
        blob_service = await stack.enter_async_context(
            BlobServiceClient(settings.blob_account_url, credential=credential)
        )
        blobs = blob_service.get_container_client(settings.blob_container)
        await blobs.get_container_properties()
        policies = {}
        for name in ("libraries", "documents", "chunks"):
            actual = await database.get_container_client(name).read()
            expected = json.loads((REPO / "cosmos" / f"{name}.container.json").read_text())
            require(not definition_mismatches(expected, actual), "current container policy mismatch: " + name)
            policies[name] = actual
        fingerprint = target_fingerprint(
            settings.cosmos_endpoint, settings.cosmos_database, "chunks", policies["chunks"]["_rid"]
        )
        require(fingerprint == canary["targetFingerprint"], "existing canary incarnation differs")
        save(root / "policies.json", policies)
        protected = await protected_snapshot(database, blobs)
        require(protected, "protected inventory unexpectedly empty")
        save(root / "protected-before.json", protected)
    receipt = {
        "schema": "fli.granular.live-preflight.v1",
        "capturedAt": datetime.now(UTC).isoformat(),
        "passed": True,
        "newResources": False,
        "canarySha256": sha(canary_path),
        "targetFingerprint": fingerprint,
        "environmentSha256": sha(root / "environment.json"),
        "protectedSha256": sha(root / "protected-before.json"),
        "modelDeploymentsSha256": sha(root / "deployments.json"),
        "embedding": embedding,
        "health": "current authenticated management, container policy/incarnation, scoped reads and blob census passed",
        "mutations": 0,
        "modelCalls": 0,
    }
    save(root / "preflight.json", receipt)
    return {
        "passed": True,
        "protectedLibraries": len(protected),
        "protectedDocuments": sum(x["documentCount"] for x in protected.values()),
        "models": [
            {"model": d["properties"]["model"], "sku": d["sku"], "state": d["properties"]["provisioningState"]}
            for d in deployments["value"]
        ],
        "mutations": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--account-seed", type=Path, required=True)
    parser.add_argument("--canary", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.WARNING)

    async def bounded():
        async with asyncio.timeout(300):
            return await preflight(args.state, args.account_seed, args.canary)

    print(json.dumps(asyncio.run(bounded()), indent=2))


if __name__ == "__main__":
    main()
