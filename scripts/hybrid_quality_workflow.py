"""Read and bind the actual mounted Microsoft workflow before confirmation ingestion.

Only coordinator-invoked, bounded ARM GETs are permitted. Configuration, mounted
files and resource identifiers remain in the private output directory. This does
not invoke a workflow, upload sources, provision or mutate a resource.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fli.config import Settings  # noqa: E402
from scripts.hybrid_quality_scope import binding  # noqa: E402
from scripts.retrieval_controls import deployment_binding, private_path  # noqa: E402

PROFILE = "microsoft-indexer-layout-v1"
BASELINE_RECIPE_SHA256 = "923ffe2b909e96bdaf26d29b930dd02198ffa878d8a101f6529a0c4504e33b37"
BASELINE_CONFIG_SHA256 = "721e20e80180e9ba0b585d2c5c67de63b5b07f59455b84c318af18bacf9079ce"
PACKAGE_FILES = ("host.json", "connections.json", "parameters.json", "shared/workflow.json", "recovery/workflow.json")
WORKER_FIELDS = ("image", "env", "command", "args")
CHECKS = (
    "workerReadyRevisionMatchesTemplate",
    "workerReadyRevisionHealthy",
    "workerImageUsesImmutableDigest",
    "workerConfigurationUnchanged",
    "workerDataTargetsMatchSettings",
    "workerWorkflowTargetMatchesSettings",
    "workerEmbeddingMatchesSettings",
    "workflowEndpointBelongsToCapturedHost",
    "workflowEasyAuthEnabled",
    "workflowRequiresAuthentication",
    "mountedFilesMatchReviewedPackage",
    "mountedFilesMatchPriorM1",
    "mountedEmbeddingBindingMatchesActualDeployment",
    "mountedBlobTargetMatchesSettings",
    "mountedManagedIdentityProviders",
    "mountedDIConnectionMatchesLive",
    "invokedTriggerIsPOSTOnly",
    "profileRecipeUnchanged",
    "actualEmbeddingDeploymentUnchanged",
)


def now():
    return datetime.now(UTC).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    """Match M1's compact JSON hashes; distinct from structure_lab.digest."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read(path):
    return json.loads(private_path(path).read_text())


def ref(path):
    path = private_path(path)
    return {"path": str(path), "sha256": sha(path)}


def save(path, value):
    with private_path(path).open("x") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def route(endpoint):
    parsed = urlsplit(endpoint)
    pieces = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parse_qs(parsed.query, keep_blank_values=True) not in ({}, {"api-version": ["2020-05-01-preview"]})
        or parsed.fragment
        or parsed.username
        or parsed.password
        or pieces != ["api", "recovery", "triggers", "request", "invoke"]
    ):
        raise ValueError("expected the unchanged recovery workflow endpoint")
    return "recovery"


def audit(*, env, worker, revision, host, auth, mounted, package, previous, embedding, old_embedding, di, old_worker):
    """Pure content checks; fresh network captures are supplied by the coordinator reader."""
    container = worker["properties"]["template"]["containers"][0]
    rcontainer = revision["properties"]["template"]["containers"][0]
    wenv = {row["name"]: row.get("value") for row in container["env"]}
    worker_settings = Settings.from_env(wenv)
    workflow_route = route(wenv["MICROSOFT_WORKFLOW_ENDPOINT"])
    fields = (*WORKER_FIELDS, "volumeMounts")
    params = {key: value.get("value") for key, value in mounted["parameters.json"].items()}
    providers = mounted["connections.json"]["serviceProviderConnections"]
    aoai = providers["aoai-connection_shared"]["parameterValues"]
    blobs = providers["blob-connection_shared"]["parameterValues"]
    managed = mounted["connections.json"]["managedApiConnections"]["formrecognizer_shared"]
    definition = mounted[workflow_route + "/workflow.json"]["definition"]
    model = embedding["deployment"]["properties"]["model"]
    fields_hash = {name: digest(value) for name, value in mounted.items()}
    checked_model = deployment_binding(
        embedding,
        configured_endpoint=env["AOAI_ENDPOINT"],
        configured_deployment=env["AOAI_EMBEDDING_DEPLOYMENT"],
        model="text-embedding-3-large",
        revision="1",
    )
    checks = {
        "workerReadyRevisionMatchesTemplate": all(container.get(k) == rcontainer.get(k) for k in fields)
        and all(
            container.get("resources", {}).get(k) == rcontainer.get("resources", {}).get(k) for k in ("cpu", "memory")
        ),
        "workerReadyRevisionHealthy": worker["properties"].get("provisioningState") == "Succeeded"
        and worker["properties"].get("runningStatus") == "Running"
        and revision["properties"].get("provisioningState") == "Provisioned"
        and revision["properties"].get("active") is True
        and revision["properties"].get("healthState") == "Healthy",
        "workerImageUsesImmutableDigest": "@sha256:" in container["image"],
        "workerConfigurationUnchanged": all(
            container.get(k) == old_worker["properties"]["template"]["containers"][0].get(k)
            for k in (*fields, "resources")
        ),
        "workerDataTargetsMatchSettings": all(
            wenv.get(k) == env.get(k)
            for k in (
                "COSMOS_ENDPOINT",
                "COSMOS_DATABASE",
                "BLOB_ACCOUNT_URL",
                "BLOB_CONTAINER",
                "SERVICEBUS_NAMESPACE",
                "SERVICEBUS_QUEUE",
            )
        ),
        "workerWorkflowTargetMatchesSettings": all(
            wenv.get(k) == env.get(k) for k in ("MICROSOFT_WORKFLOW_ENDPOINT", "MICROSOFT_WORKFLOW_AUDIENCE")
        ),
        "workerEmbeddingMatchesSettings": all(
            str(wenv.get(k)) == str(env.get(k))
            for k in (
                "AOAI_ENDPOINT",
                "AOAI_EMBEDDING_DEPLOYMENT",
                "AOAI_EMBEDDING_MODEL_REVISION",
            )
        )
        and worker_settings.aoai_embedding_dimensions == int(env["AOAI_EMBEDDING_DIMENSIONS"]) == 3072,
        "workflowEndpointBelongsToCapturedHost": urlsplit(wenv["MICROSOFT_WORKFLOW_ENDPOINT"]).hostname
        in host["properties"].get("enabledHostNames", []),
        "workflowEasyAuthEnabled": auth["properties"]["platform"].get("enabled") is True,
        "workflowRequiresAuthentication": auth["properties"]["globalValidation"].get("requireAuthentication") is True,
        "mountedFilesMatchReviewedPackage": mounted == package,
        "mountedFilesMatchPriorM1": fields_hash == previous["workflowMountedFilesSha256"],
        "mountedEmbeddingBindingMatchesActualDeployment": params.get("EmbeddingModel") == model["name"]
        and str(params.get("EmbeddingModelRevision"))
        == model["version"]
        == str(wenv.get("AOAI_EMBEDDING_MODEL_REVISION"))
        and params.get("AOAI_Emb_Dep_shared")
        == embedding["deployment"]["name"]
        == wenv.get("AOAI_EMBEDDING_DEPLOYMENT")
        and params.get("EmbeddingDimensions") == 3072
        and aoai.get("openAIEndpoint", "").rstrip("/") == wenv.get("AOAI_ENDPOINT", "").rstrip("/"),
        "mountedBlobTargetMatchesSettings": blobs.get("blobStorageEndpoint", "").rstrip("/")
        == env["BLOB_ACCOUNT_URL"].rstrip("/")
        and params.get("RecoverySourceContainer") == env["BLOB_CONTAINER"]
        and definition["actions"]["Read_blob_content"]["inputs"]["parameters"]["containerName"]
        == "@parameters('RecoverySourceContainer')",
        "mountedManagedIdentityProviders": bool(providers)
        and all(
            p["parameterValues"].get("authProvider", {}).get("Type") == "ManagedServiceIdentity"
            for p in providers.values()
        ),
        "mountedDIConnectionMatchesLive": managed["connection"]["id"].lower() == di["id"].lower()
        and managed["connectionRuntimeUrl"] == di["properties"]["connectionRuntimeUrl"]
        and managed["api"]["id"] == di["properties"]["api"]["id"],
        "invokedTriggerIsPOSTOnly": definition["triggers"]["request"]["inputs"].get("method") == "POST",
        "profileRecipeUnchanged": sha(REPO / "src/fli/ingestion.py") == BASELINE_RECIPE_SHA256
        and sha(REPO / "src/fli/config.py") == BASELINE_CONFIG_SHA256,
        "actualEmbeddingDeploymentUnchanged": checked_model["model"]
        == old_embedding["deployment"]["properties"]["model"]
        and checked_model["sku"] == old_embedding["deployment"]["sku"]
        and embedding["deployment"]["id"].lower() == old_embedding["deployment"]["id"].lower(),
    }
    assert set(checks) == set(CHECKS)
    return checks, fields_hash, checked_model


def run(config_path, output):
    config_path, output = private_path(config_path), private_path(output)
    output.mkdir(mode=0o700, exist_ok=False)
    config = read(config_path)
    inputs = {name: private_path(path) for name, path in config["inputs"].items()}
    input_refs = [ref(config_path), *[ref(path) for path in inputs.values()]]
    env = read(inputs["environment"])
    os.environ.update(env)
    settings = Settings.from_env()
    old_worker = read(inputs["workerBefore"])
    old_embedding = read(inputs["embeddingBefore"])
    host_before = read(inputs["workflowHostBefore"])["body"]
    di_before = read(inputs["workflowDIBefore"])["body"]
    previous = read(inputs["priorBinding"])
    models = read(inputs["modelBindings"])
    expected_package_hash = sha(inputs["expectedPackage"])
    if expected_package_hash != read(inputs["packageReceipt"])["packageSha256"]:
        raise ValueError("reviewed package archive hash changed")
    with zipfile.ZipFile(inputs["expectedPackage"]) as archive:
        if set(archive.namelist()) != set(PACKAGE_FILES) or any(
            info.file_size > 10_000_000 for info in archive.infolist()
        ):
            raise ValueError("unexpected package inventory or member size")
        package = {name: json.loads(archive.read(name)) for name in PACKAGE_FILES}
    started = now()
    clock_start = time.monotonic()
    raw_refs = []
    save(
        output / "execution.json",
        {
            "startedAt": started,
            "producerSourceSha256": sha(__file__),
            "inputBindings": input_refs,
            "managementGetLimit": 12,
            "overallDeadlineSeconds": 300,
            "perGetTimeoutSeconds": 20,
            "httpRetries": 0,
            "readOnly": True,
        },
    )
    token_result = subprocess.run(
        [
            "az",
            "account",
            "get-access-token",
            "--subscription",
            config["subscription"],
            "--resource",
            "https://management.azure.com/",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if token_result.returncode:
        raise ValueError("management token unavailable; credential output not retained")
    token = json.loads(token_result.stdout)
    if token["subscription"].lower() != config["subscription"].lower():
        raise ValueError("credential subscription mismatch")
    count = 0
    with httpx.Client(
        timeout=httpx.Timeout(20, connect=10), follow_redirects=False, transport=httpx.HTTPTransport(retries=0)
    ) as client:

        def get(label, url):
            nonlocal count
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "management.azure.com"
                or not parsed.path.lower().startswith("/subscriptions/" + config["subscription"].lower() + "/")
            ):
                raise ValueError("unexpected ARM target")
            remaining = 300 - (time.monotonic() - clock_start)
            if count >= 12 or remaining <= 0:
                raise ValueError("bounded capture exhausted")
            count += 1
            response = client.get(
                url, headers={"Authorization": "Bearer " + token["accessToken"]}, timeout=min(20, remaining)
            )
            body_path = output / (label + ".body")
            with body_path.open("xb") as handle:
                handle.write(response.content)
            raw_refs.append(ref(body_path))
            value = response.json()
            row = {
                "capturedAt": now(),
                "method": "GET",
                "url": url,
                "status": response.status_code,
                "body": value,
                "responseBodySha256": sha(body_path),
                "responseRequestId": response.headers.get("x-ms-request-id"),
            }
            receipt_path = output / (label + ".json")
            save(receipt_path, row)
            raw_refs.append(ref(receipt_path))
            response.raise_for_status()
            return value

        worker_id = old_worker["id"]
        worker = get("worker", "https://management.azure.com" + worker_id + "?api-version=2024-03-01")
        revision = get(
            "worker-revision",
            "https://management.azure.com"
            + worker_id
            + "/revisions/"
            + worker["properties"]["latestReadyRevisionName"]
            + "?api-version=2024-03-01",
        )
        embedding_url = models["embedding"]["url"]
        account = get("embedding-account", embedding_url.split("/deployments/")[0] + "?api-version=2024-10-01")
        deployment = get("embedding-deployment", embedding_url)
        embedding = {"capturedAt": now(), "account": account, "deployment": deployment}
        embedding_path = output / "embedding-envelope-private.json"
        save(embedding_path, embedding)
        raw_refs.append(ref(embedding_path))
        host_id = host_before["id"]
        host = get("workflow-host", "https://management.azure.com" + host_id + "?api-version=2023-12-01")
        auth = get(
            "workflow-auth", "https://management.azure.com" + host_id + "/config/authsettingsV2?api-version=2022-09-01"
        )
        mounted, mounted_refs = {}, {}
        for name in PACKAGE_FILES:
            label = "mounted-" + name.replace("/", "-").replace(".json", "")
            mounted[name] = get(
                label,
                "https://management.azure.com"
                + host_id
                + "/hostruntime/admin/vfs/site/wwwroot/"
                + name
                + "?api-version=2024-04-01",
            )
            # Exact served bytes are JSON and can be independently parsed by the gate.
            mounted_refs[name] = ref(output / (label + ".body"))
        managed = mounted["connections.json"]["managedApiConnections"]["formrecognizer_shared"]
        if managed["connection"]["id"].lower() != di_before["id"].lower():
            raise ValueError("mounted DI connection target changed")
        di = get(
            "workflow-di-connection",
            "https://management.azure.com" + managed["connection"]["id"] + "?api-version=2016-06-01",
        )
    checks, mounted_hashes, model_binding = audit(
        env=env,
        worker=worker,
        revision=revision,
        host=host,
        auth=auth,
        mounted=mounted,
        package=package,
        previous=previous,
        embedding=embedding,
        old_embedding=old_embedding,
        di=di,
        old_worker=old_worker,
    )
    container = worker["properties"]["template"]["containers"][0]
    receipt = {
        "schema": "fli.hybrid-quality.workflow-binding.v1",
        "status": "complete" if all(checks.values()) else "failed",
        "readOnly": True,
        "capturedAt": started,
        "finishedAt": now(),
        "managementGets": count,
        "checks": checks,
        "producerSourceSha256": sha(__file__),
        "settingsBinding": binding(settings),
        "profileId": PROFILE,
        "profileRecipeSha256": sha(REPO / "src/fli/ingestion.py"),
        "configurationSourceSha256": sha(REPO / "src/fli/config.py"),
        "workerConfigSha256": digest({key: container.get(key) for key in WORKER_FIELDS}),
        "workerReadyRevision": worker["properties"]["latestReadyRevisionName"],
        "workflowEndpointSha256": hashlib.sha256(settings.microsoft_workflow_endpoint.encode()).hexdigest(),
        "workflowMountedFilesSha256": mounted_hashes,
        "workflowMountedPackageSha256": digest(mounted_hashes),
        "expectedPackageSha256": expected_package_hash,
        "expectedPackageReceipt": ref(inputs["expectedPackage"]),
        "priorM1Receipt": ref(inputs["priorBinding"]),
        "embeddingBinding": model_binding,
        "mountedFiles": mounted_refs,
        "workerReceipt": ref(output / "worker.json"),
        "workerRevisionReceipt": ref(output / "worker-revision.json"),
        "embeddingReceipt": ref(embedding_path),
        "rawReceipts": raw_refs,
        "inputBindings": input_refs,
        "limitations": [
            "Mounted files were freshly reopened; canonical JSON equality binds all reviewed package members. "
            "Exact served byte hashes are retained separately from the ZIP byte hash.",
            "Document Intelligence implementation is managed. Mounted action/model/API parameters are bound, "
            "not a frozen provider implementation build.",
            "Worker immutable image and ready-revision configuration are bound; "
            "executing container bytes were not inspected.",
            "Authentication configuration is checked. This reader does not invoke the workflow "
            "or establish fresh runtime denial behavior.",
            "Captures are sequential rather than a transactional multi-service snapshot.",
        ],
    }
    save(output / "workflow-binding-private.json", receipt)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "readOnly": True,
                "managementGets": count,
                "checksPassed": sum(checks.values()),
                "checksTotal": len(checks),
                "failedChecks": [k for k, v in checks.items() if not v],
                "workflowMountedPackageSha256": receipt["workflowMountedPackageSha256"],
            }
        )
    )
    return 0 if receipt["status"] == "complete" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        return run(args.config, args.output)
    except Exception as error:
        output = private_path(args.output)
        if output.is_dir() and not (output / "failure-private.json").exists():
            save(
                output / "failure-private.json", {"at": now(), "errorType": type(error).__name__, "message": str(error)}
            )
        print(json.dumps({"status": "failed", "errorType": type(error).__name__, "readOnly": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
