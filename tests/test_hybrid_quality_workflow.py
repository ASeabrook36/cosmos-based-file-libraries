"""Content-binding denials for actual mounted workflow captures; no cloud access."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from scripts import hybrid_quality_workflow as workflow
from scripts.hybrid_quality_workflow import CHECKS, audit, digest, route


@pytest.fixture
def frozen_recipe_fingerprints(monkeypatch):
    # These cloud captures are synthetic. Control their matching local source
    # fingerprints too; a new opt-in implementation is not the frozen deployment.
    hashes = {
        workflow.REPO / "src/fli/ingestion.py": workflow.BASELINE_RECIPE_SHA256,
        workflow.REPO / "src/fli/config.py": workflow.BASELINE_CONFIG_SHA256,
    }
    actual_sha = workflow.sha

    def observed_sha(path):
        return hashes[path] if path in hashes else actual_sha(path)

    monkeypatch.setattr(workflow, "sha", observed_sha)
    return hashes


@pytest.fixture
def captures(frozen_recipe_fingerprints):
    env = {
        "ENVIRONMENT": "prod",
        "MICROSOFT_WORKFLOW_ENDPOINT": "https://workflow.example.invalid/api/recovery/triggers/request/invoke",
        "MICROSOFT_WORKFLOW_AUDIENCE": "example-audience",
        "AOAI_ENDPOINT": "https://model.example.invalid",
        "AOAI_EMBEDDING_DEPLOYMENT": "embedding",
        "AOAI_EMBEDDING_DIMENSIONS": "3072",
        "AOAI_EMBEDDING_MODEL_REVISION": "1",
        "COSMOS_ENDPOINT": "https://cosmos.example.invalid",
        "COSMOS_DATABASE": "example-database",
        "BLOB_ACCOUNT_URL": "https://blob.example.invalid",
        "BLOB_CONTAINER": "example-container",
        "SERVICEBUS_NAMESPACE": "queue.example.invalid",
        "SERVICEBUS_QUEUE": "example-queue",
    }
    container = {
        "image": "example-image@sha256:" + "a" * 64,
        "env": [{"name": key, "value": value} for key, value in env.items()],
        "resources": {"cpu": 1, "memory": "2Gi"},
    }
    worker = {
        "properties": {
            "template": {"containers": [container]},
            "provisioningState": "Succeeded",
            "runningStatus": "Running",
        }
    }
    revision = deepcopy(worker)
    revision["properties"].update(provisioningState="Provisioned", active=True, healthState="Healthy")
    connection = {
        "connection": {"id": "/example/connection"},
        "connectionRuntimeUrl": "https://connection.example.invalid",
        "api": {"id": "/example/api"},
    }
    mounted = {
        "host.json": {},
        "parameters.json": {
            key: {"value": value}
            for key, value in {
                "EmbeddingModel": "text-embedding-3-large",
                "EmbeddingModelRevision": "1",
                "AOAI_Emb_Dep_shared": "embedding",
                "EmbeddingDimensions": 3072,
                "RecoverySourceContainer": "example-container",
            }.items()
        },
        "connections.json": {
            "serviceProviderConnections": {
                "aoai-connection_shared": {
                    "parameterValues": {
                        "openAIEndpoint": env["AOAI_ENDPOINT"],
                        "authProvider": {"Type": "ManagedServiceIdentity"},
                    }
                },
                "blob-connection_shared": {
                    "parameterValues": {
                        "blobStorageEndpoint": env["BLOB_ACCOUNT_URL"],
                        "authProvider": {"Type": "ManagedServiceIdentity"},
                    }
                },
            },
            "managedApiConnections": {"formrecognizer_shared": connection},
        },
        "shared/workflow.json": {},
        "recovery/workflow.json": {
            "definition": {
                "triggers": {"request": {"inputs": {"method": "POST"}}},
                "actions": {
                    "Read_blob_content": {
                        "inputs": {"parameters": {"containerName": "@parameters('RecoverySourceContainer')"}}
                    }
                },
            }
        },
    }
    account_id = "/subscriptions/example/resourceGroups/example/providers/Microsoft.CognitiveServices/accounts/example"
    embedding = {
        "capturedAt": datetime.now(UTC).isoformat(),
        "account": {"id": account_id, "properties": {"endpoint": env["AOAI_ENDPOINT"]}},
        "deployment": {
            "id": account_id + "/deployments/embedding",
            "name": "embedding",
            "sku": {"name": "GlobalStandard", "capacity": 150},
            "properties": {
                "model": {"format": "OpenAI", "name": "text-embedding-3-large", "version": "1"},
                "versionUpgradeOption": "NoAutoUpgrade",
                "provisioningState": "Succeeded",
            },
        },
    }
    return {
        "env": env,
        "worker": worker,
        "revision": revision,
        "old_worker": deepcopy(worker),
        "host": {"properties": {"enabledHostNames": ["workflow.example.invalid"]}},
        "auth": {"properties": {"platform": {"enabled": True}, "globalValidation": {"requireAuthentication": True}}},
        "mounted": mounted,
        "package": deepcopy(mounted),
        "previous": {"workflowMountedFilesSha256": {key: digest(value) for key, value in mounted.items()}},
        "embedding": embedding,
        "old_embedding": deepcopy(embedding),
        "di": {
            "id": "/example/connection",
            "properties": {key: connection[key] for key in ("connectionRuntimeUrl", "api")},
        },
    }


def test_matching_actual_captures_pass(captures):
    checks, mounted, model = audit(**captures)
    assert set(checks) == set(CHECKS)
    assert all(checks.values())
    assert len(mounted) == 5
    assert model["model"]["version"] == "1"


@pytest.mark.parametrize("name", ["ingestion.py", "config.py"])
def test_changed_recipe_or_configuration_cannot_inherit_frozen_deployment(captures, frozen_recipe_fingerprints, name):
    frozen_recipe_fingerprints[workflow.REPO / "src/fli" / name] = "0" * 64
    checks, _, _ = audit(**captures)
    assert not checks["profileRecipeUnchanged"]


def test_mounted_parameter_drift_cannot_inherit_package_receipt(captures):
    captures["mounted"]["parameters.json"]["RecoverySourceContainer"]["value"] = "changed"
    checks, _, _ = audit(**captures)
    assert not checks["mountedFilesMatchReviewedPackage"]
    assert not checks["mountedFilesMatchPriorM1"]
    assert not checks["mountedBlobTargetMatchesSettings"]


def test_ready_revision_and_worker_target_must_match(captures):
    captures["revision"]["properties"]["template"]["containers"][0]["image"] = "changed"
    for row in captures["worker"]["properties"]["template"]["containers"][0]["env"]:
        if row["name"] == "COSMOS_ENDPOINT":
            row["value"] = "changed"
    checks, _, _ = audit(**captures)
    assert not checks["workerReadyRevisionMatchesTemplate"]
    assert not checks["workerConfigurationUnchanged"]
    assert not checks["workerDataTargetsMatchSettings"]


def test_actual_deployment_revision_and_upgrade_policy_are_enforced(captures):
    captures["embedding"]["deployment"]["properties"]["versionUpgradeOption"] = "OnceNewDefaultVersionAvailable"
    with pytest.raises(ValueError, match="frozen controls"):
        audit(**captures)


def test_worker_dimension_default_uses_frozen_settings(captures):
    for target in ("worker", "revision", "old_worker"):
        container = captures[target]["properties"]["template"]["containers"][0]
        container["env"] = [row for row in container["env"] if row["name"] != "AOAI_EMBEDDING_DIMENSIONS"]
    checks, _, _ = audit(**captures)
    assert all(checks.values())


def test_inactive_revision_is_not_ready(captures):
    captures["revision"]["properties"]["active"] = False
    checks, _, _ = audit(**captures)
    assert not checks["workerReadyRevisionHealthy"]


def test_authentication_and_di_connection_are_actual_checks(captures):
    captures["auth"]["properties"]["globalValidation"]["requireAuthentication"] = False
    captures["di"]["properties"]["connectionRuntimeUrl"] = "https://changed.example.invalid"
    checks, _, _ = audit(**captures)
    assert not checks["workflowRequiresAuthentication"]
    assert not checks["mountedDIConnectionMatchesLive"]


@pytest.mark.parametrize("suffix", ["?sig=secret", "/extra", "#fragment"])
def test_unexpected_workflow_route_is_rejected(suffix):
    with pytest.raises(ValueError, match="unchanged recovery"):
        route("https://workflow.example.invalid/api/recovery/triggers/request/invoke" + suffix)
