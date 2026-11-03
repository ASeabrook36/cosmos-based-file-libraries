"""Fresh-health gates reject stale, mismatched, partial, and tampered evidence."""

import copy
import hashlib
import json
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import hybrid_quality_restart as gate

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return {"path": str(path), "sha256": gate.sha(path)}


@pytest.fixture
def restart(tmp_path, monkeypatch):
    repository = tmp_path / "producer-code"
    monkeypatch.setattr(gate, "REPO", repository)
    for file in (
        "scripts/hybrid_quality_smoke.py",
        "scripts/hybrid_quality_workflow.py",
        "src/fli/ingestion.py",
        "src/fli/config.py",
        "scripts/hybrid_quality_scope.py",
        "scripts/hybrid_quality_failed_scope.py",
    ):
        path = repository / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file)
    root = tmp_path / "private"
    root.mkdir()
    state = root / "owned"
    binding = {"private": "same-target"}
    protected = ["protected-library"]
    settings = SimpleNamespace(
        aoai_endpoint="https://model.invalid",
        aoai_embedding_deployment="embedding",
        microsoft_workflow_endpoint="https://workflow.invalid/invoke",
    )
    timestamp = (NOW - timedelta(seconds=10)).isoformat()
    envelope = {
        "capturedAt": timestamp,
        "account": {"id": "/accounts/model", "properties": {"endpoint": settings.aoai_endpoint}},
        "deployment": {
            "id": "/accounts/model/deployments/embedding",
            "sku": {"name": "GlobalStandard", "capacity": 150},
            "properties": {
                "model": {"format": "OpenAI", "name": "text-embedding-3-large", "version": "1"},
                "versionUpgradeOption": "NoAutoUpgrade",
                "provisioningState": "Succeeded",
            },
        },
    }
    model_ref = put(root, "model.json", envelope)
    model_binding = gate.model(model_ref, settings, NOW, 3600)
    after_envelope = copy.deepcopy(envelope)
    after_envelope["capturedAt"] = (NOW - timedelta(seconds=5)).isoformat()
    after_model_ref = put(root, "model-after.json", after_envelope)
    canary = put(root, "canary.json", {"passed": True})
    raw = put(root, "raw.json", {"private": "raw-operation-evidence"})
    source = put(root, "source.json", {"original": "disjoint real-source fixture"})
    library_id = "a54ac957-4ca4-461b-9f5f-37f04b0ad219"
    rows = [
        {"container": "libraries", "body": {"id": library_id}},
        {"container": "documents", "body": {"id": "document", "sha256": source["sha256"]}},
    ]
    rows += [
        {
            "container": "chunks",
            "body": {
                "id": f"chunk-{i}",
                "documentId": "document",
                "generation": 1,
                "text": "fixture source text",
                "embedding": [0.001] * 3072,
            },
        }
        for i in range(12)
    ]
    for item in rows:
        item["body"].update(libraryId=library_id, smokeOwner="smoke-run")
    ownership = {
        "schema": "fli.hybrid-quality.smoke-ownership.v1",
        "runId": "smoke-run",
        "libraryId": library_id,
        "settingsBinding": binding,
        "targetFingerprint": "fingerprint",
        "protectedLibraryIds": protected,
        "noAsyncWriters": True,
        "rows": rows,
        "attemptedIds": [item["body"]["id"] for item in rows],
    }
    ownership_ref = put(root, "smoke-ownership.json", ownership)
    health = {
        "schema": "fli.hybrid-quality.restart-health.v1",
        "status": "passed",
        "startedAt": timestamp,
        "completedAt": timestamp,
        "finalizedAt": after_envelope["capturedAt"],
        "targetFingerprint": "fingerprint",
        "settingsBinding": binding,
        "canarySha256": canary["sha256"],
        "libraryId": library_id,
        "protectedLibraryIds": protected,
        "sourceHashes": [source["sha256"]],
        "sourceDisjointFromBenchmark": True,
        "producerSourceSha256": gate.sha(repository / "scripts/hybrid_quality_smoke.py"),
        "rawArtifacts": [raw],
        "sourceArtifacts": [source],
        "ownershipReceipt": ownership_ref,
        "embeddingBinding": model_binding,
        "embeddingDimensions": 3072,
        "embeddingBeforeReceipt": model_ref,
        "embeddingAfterReceipt": after_model_ref,
        "checks": {
            "writes": {"expectedRows": 14, "completedRows": 14},
            "pointReads": {"expectedRows": 14, "matchingRows": 14},
            "scopedQuery": {"expectedChunks": 12, "returnedChunks": 12},
            "vectorQueries": [
                {"queryId": f"smoke-{i}", "resultCount": 3, "foreignRows": 0, "route": "library-prefix"} for i in (1, 2)
            ],
            "hybridQueries": [
                {"queryId": f"smoke-{i}", "resultCount": 3, "foreignRows": 0, "route": "no-partition-key"}
                for i in (1, 2)
            ],
            "hybridListsDiffer": True,
            "cleanup": {
                "expectedRows": 14,
                "deletedRows": 14,
                "point404Count": 14,
                "scopedRows": 0,
                "latePoint404Count": 14,
                "lateScopedRows": 0,
                "lateRecheckSeconds": 30,
            },
        },
    }
    mounted = {name: {"fixture": name} for name in gate.PACKAGE_FILES}
    mounted_refs = {name: put(root, "mounted/" + name, content) for name, content in mounted.items()}
    mounted_hashes = {name: gate.canonical_hash(value) for name, value in mounted.items()}
    package = root / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for name, value in mounted.items():
            archive.writestr(name, json.dumps(value))
    worker_container = {"image": "registry.invalid/image@sha256:immutable", "env": [], "command": [], "args": []}
    worker_body = {"properties": {"template": {"containers": [worker_container]}, "latestReadyRevisionName": "ready"}}
    worker_ref = put(root, "worker.json", {"method": "GET", "status": 200, "body": worker_body})
    workflow = {
        "schema": "fli.hybrid-quality.workflow-binding.v1",
        "status": "complete",
        "readOnly": True,
        "capturedAt": timestamp,
        "finishedAt": timestamp,
        "settingsBinding": binding,
        "producerSourceSha256": gate.sha(repository / "scripts/hybrid_quality_workflow.py"),
        "profileId": "microsoft-indexer-layout-v1",
        "profileRecipeSha256": gate.sha(repository / "src/fli/ingestion.py"),
        "configurationSourceSha256": gate.sha(repository / "src/fli/config.py"),
        "checks": dict.fromkeys(gate.WORKFLOW_CHECKS, True),
        "workerConfigSha256": gate.canonical_hash(worker_container),
        "workflowMountedFilesSha256": mounted_hashes,
        "workflowMountedPackageSha256": gate.canonical_hash(mounted_hashes),
        "expectedPackageSha256": gate.sha(package),
        "expectedPackageReceipt": {"path": str(package), "sha256": gate.sha(package)},
        "workerReadyRevision": "ready",
        "workflowEndpointSha256": digest(settings.microsoft_workflow_endpoint.encode()),
        "rawReceipts": [raw],
        "inputBindings": [raw],
        "mountedFiles": mounted_refs,
        "workerReceipt": worker_ref,
        "workerRevisionReceipt": worker_ref,
        "embeddingReceipt": model_ref,
        "embeddingBinding": model_binding,
    }
    cleanup = {
        "schema": "fli.hybrid-quality.partial-cleanup.v1",
        "declaredAt": timestamp,
        "evaluationStateDir": str(state),
        "ownershipLedger": str(state / "ownership.json"),
        "settingsBinding": binding,
        "targetFingerprint": "fingerprint",
        "protectedLibraryIds": protected,
        "manifestSha256": digest(b"manifest"),
        "maximumEvaluationLibraries": 1,
        "policy": {
            **dict.fromkeys(gate.CLEANUP_TRUE, True),
            **dict.fromkeys(gate.CLEANUP_FALSE, False),
            "lateRecheckSeconds": 30,
        },
        "helpers": {
            file: gate.sha(repository / file)
            for file in ("scripts/hybrid_quality_scope.py", "scripts/hybrid_quality_failed_scope.py")
        },
    }
    outer = {
        "schema": "fli.hybrid-quality.restart.v1",
        "evaluationStateDir": str(state),
        "completedAt": after_envelope["capturedAt"],
        "settingsBinding": binding,
        "targetFingerprint": "fingerprint",
        "canarySha256": canary["sha256"],
        "protectedLibraryIds": protected,
    }
    values = {"healthReceipt": health, "workflowReceipt": workflow, "partialCleanupPlan": cleanup}
    args = dict(
        root=state,
        settings=settings,
        binding=binding,
        preflight={"targetFingerprint": "fingerprint"},
        canary_path=canary["path"],
        protected=protected,
        manifest={"documents": [{"id": "source", "sha256": digest(b"benchmark")}]},
        manifest_sha=cleanup["manifestSha256"],
        current=NOW,
    )

    def write():
        for key, value in values.items():
            outer[key] = put(root, key + ".json", value)
        return put(root, "restart.json", outer)["path"]

    return SimpleNamespace(
        root=root, values=values, outer=outer, args=args, write=write, raw=raw, envelope=envelope, repository=repository
    )


def test_valid_restart_reopens_all_evidence_and_returns_durable_binding(restart):
    path = restart.write()
    result = gate.validate_restart(path, **restart.args)
    assert result["sha256"] == gate.sha(path)
    assert result["acceptedAt"] == NOW.isoformat()
    assert result["healthSha256"] == restart.outer["healthReceipt"]["sha256"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "stale",
        "future",
        "changed-target",
        "other-ledger",
        "changed-raw",
        "benchmark-source",
        "protected-smoke",
        "partial-write",
        "foreign-query",
        "wrong-hybrid-route",
        "no-hybrid-ranking",
        "late-cleanup",
        "unbound-model",
        "unbound-workflow",
        "changed-producer",
        "changed-recipe",
        "missing-mounted-file",
        "missing-cleanup",
        "automatic-retry",
        "multiple-libraries",
        "stale-model",
        "future-cleanup",
        "different-candidate",
        "loose-age",
        "finalized-too-early",
    ],
)
def test_invalid_restart_cannot_authorize_ingest(restart, damage):
    health = restart.values["healthReceipt"]
    workflow = restart.values["workflowReceipt"]
    cleanup = restart.values["partialCleanupPlan"]
    if damage == "stale":
        health["startedAt"] = (NOW - timedelta(hours=2)).isoformat()
    elif damage == "future":
        restart.outer["completedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif damage == "changed-target":
        health["targetFingerprint"] = "different-incarnation"
    elif damage == "other-ledger":
        restart.outer["evaluationStateDir"] += "-second"
    elif damage == "benchmark-source":
        restart.args["manifest"]["documents"][0]["sha256"] = health["sourceHashes"][0]
    elif damage == "protected-smoke":
        health["libraryId"] = restart.args["protected"][0]
    elif damage == "partial-write":
        health["checks"]["writes"]["completedRows"] = 11
    elif damage == "foreign-query":
        health["checks"]["vectorQueries"][0]["foreignRows"] = 1
    elif damage == "wrong-hybrid-route":
        health["checks"]["hybridQueries"][0]["route"] = "library-prefix"
    elif damage == "no-hybrid-ranking":
        health["checks"]["hybridListsDiffer"] = False
    elif damage == "late-cleanup":
        health["checks"]["cleanup"]["lateScopedRows"] = 1
    elif damage == "unbound-model":
        health["embeddingBinding"] = {}
    elif damage == "unbound-workflow":
        workflow["checks"]["mountedFilesMatchReviewedPackage"] = False
    elif damage == "changed-producer":
        workflow["producerSourceSha256"] = digest(b"unreviewed")
    elif damage == "changed-recipe":
        workflow["profileRecipeSha256"] = digest(b"changed-ingestion")
    elif damage == "missing-mounted-file":
        workflow["mountedFiles"] = []
    elif damage == "missing-cleanup":
        del cleanup["policy"]["allObservedWritersTerminal"]
    elif damage == "automatic-retry":
        cleanup["policy"]["automaticRetry"] = True
    elif damage == "multiple-libraries":
        cleanup["maximumEvaluationLibraries"] = 2
    elif damage == "future-cleanup":
        cleanup["declaredAt"] = (NOW + timedelta(seconds=10)).isoformat()
    elif damage == "different-candidate":
        restart.args["evaluation_library"] = health["libraryId"]
    elif damage == "loose-age":
        restart.args["max_age"] = 3601
    elif damage == "finalized-too-early":
        restart.outer["completedAt"] = (NOW - timedelta(seconds=20)).isoformat()
    elif damage == "stale-model":
        stale = copy.deepcopy(restart.envelope)
        stale["capturedAt"] = (NOW - timedelta(hours=2)).isoformat()
        health["embeddingAfterReceipt"] = put(restart.root, "stale-model.json", stale)
    path = restart.write()
    if damage == "changed-raw":
        from pathlib import Path

        Path(restart.raw["path"]).write_text("changed")
    with pytest.raises(ValueError):
        gate.validate_restart(None if damage == "missing" else path, **restart.args)


def test_new_or_partial_ingestion_requires_fresh_evidence_but_pure_polling_does_not():
    manifest = {"documents": [{"id": "one"}, {"id": "two"}]}
    state = {"sources": {}}
    assert gate.requires_fresh_ingest(state, manifest)
    state.update(library={"id": "owned"}, createAttempted=True)
    state["sources"]["one"] = {"attempted": True, "documentId": "first"}
    assert gate.requires_fresh_ingest(state, manifest)
    state["sources"]["two"] = {"attempted": True, "documentId": "second"}
    assert not gate.requires_fresh_ingest(state, manifest)


@pytest.mark.parametrize(
    "state",
    [
        {"sources": {}, "createAttempted": True},
        {"sources": {"one": {"attempted": True}}, "library": {"id": "owned"}},
        {"sources": {}, "deleteAttempted": True},
    ],
)
def test_fresh_evidence_cannot_authorize_ambiguous_retry_or_deleted_scope(state):
    with pytest.raises(ValueError):
        gate.requires_fresh_ingest(state, {"documents": [{"id": "one"}]})


@pytest.mark.parametrize(
    "damage",
    [
        "foreign-owner",
        "duplicate-row",
        "wrong-count",
        "missing-generation",
        "changed-mounted-file",
        "mounted-map-hash",
        "reused-model-readback",
        "post-model-too-early",
    ],
)
def test_resealed_structured_evidence_must_still_match_actual_inventory_and_content(restart, damage):
    health = restart.values["healthReceipt"]
    workflow = restart.values["workflowReceipt"]
    ledger = gate.read(health["ownershipReceipt"]["path"])
    if damage == "foreign-owner":
        ledger["rows"][0]["body"]["smokeOwner"] = "someone-else"
    elif damage == "duplicate-row":
        ledger["rows"][-1] = copy.deepcopy(ledger["rows"][-2])
    elif damage == "wrong-count":
        health["checks"]["writes"] = {"expectedRows": 12, "completedRows": 12}
    elif damage == "missing-generation":
        del ledger["rows"][-1]["body"]["generation"]
    elif damage == "changed-mounted-file":
        value = {"altered": "actual mounted parameters"}
        workflow["mountedFiles"]["parameters.json"] = put(restart.root, "changed-mounted.json", value)
        workflow["workflowMountedFilesSha256"]["parameters.json"] = gate.canonical_hash(value)
        workflow["workflowMountedPackageSha256"] = gate.canonical_hash(workflow["workflowMountedFilesSha256"])
    elif damage == "mounted-map-hash":
        workflow["workflowMountedFilesSha256"]["parameters.json"] = digest(b"different")
    elif damage == "reused-model-readback":
        health["embeddingAfterReceipt"] = health["embeddingBeforeReceipt"]
    elif damage == "post-model-too-early":
        value = copy.deepcopy(restart.envelope)
        value["capturedAt"] = (NOW - timedelta(seconds=20)).isoformat()
        health["embeddingAfterReceipt"] = put(restart.root, "early-post-model.json", value)
    health["ownershipReceipt"] = put(restart.root, "smoke-ownership.json", ledger)
    with pytest.raises(ValueError):
        gate.validate_restart(restart.write(), **restart.args)
