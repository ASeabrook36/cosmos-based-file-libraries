"""Local, fail-closed authorization of a fresh owned confirmation ingestion.

Reads hash-bound private evidence only; no network, provisioning, or source upload.
Historical status/cleanup do not depend on a fresh restart receipt.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from scripts.retrieval_controls import deployment_binding, private_path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW_CHECKS = {
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
}
PACKAGE_FILES = {"host.json", "connections.json", "parameters.json", "shared/workflow.json", "recovery/workflow.json"}
WORKER_FIELDS = ("image", "env", "command", "args")
CLEANUP_TRUE = {
    "ownershipRequired",
    "protectedScopeExcluded",
    "archiveBeforeFence",
    "allObservedWritersTerminal",
    "immutableGenerationPlans",
    "currentStateMatchesArchives",
    "allCandidateReadsBeforeDelete",
    "etagDeletes",
    "independent404BeforeBlobDelete",
    "stopOnUnknown",
    "retainArtifactsOnIncomplete",
    "retentionUnchanged",
}
CLEANUP_FALSE = {"automaticRetry", "provision", "providerResubmission", "sharedQueueDrain"}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_hash(value):
    """Canonical JSON contract used by the M1 mounted-file review."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def hash_value(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def read(path):
    return json.loads(private_path(path).read_text())


def timestamp(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, "restart timestamp requires a timezone")
    return result


def fresh(value, current, max_age):
    result = timestamp(value)
    require(0 <= (current - result).total_seconds() <= max_age, "restart evidence is stale or in the future")
    return result


def artifact(reference):
    require(isinstance(reference, dict) and hash_value(reference.get("sha256")), "artifact hash required")
    path = private_path(reference["path"])
    require(path.is_file() and sha(path) == reference["sha256"], "restart artifact bytes changed")
    return path


def artifacts(references):
    require(isinstance(references, list) and bool(references), "raw restart artifacts required")
    return [artifact(reference) for reference in references]


def requires_fresh_ingest(state, manifest):
    """Do not authorize duplicate or ambiguous writes, even with fresh evidence."""
    require(not state.get("deleteAttempted"), "cleanup already started; ingestion cannot resume")
    if "library" not in state:
        require(not state.get("createAttempted"), "ambiguous library creation; do not create a duplicate")
        return True
    for source in manifest["documents"]:
        item = state["sources"].get(source["id"], {})
        require(item.get("documentId") or not item.get("attempted"), "ambiguous source upload; do not repeat it")
    return any(not state["sources"].get(source["id"], {}).get("documentId") for source in manifest["documents"])


def model(reference, settings, current, max_age):
    receipt = read(artifact(reference))
    captured = fresh(receipt["capturedAt"], current, max_age)
    return deployment_binding(
        receipt,
        configured_endpoint=settings.aoai_endpoint,
        configured_deployment=settings.aoai_embedding_deployment,
        model="text-embedding-3-large",
        revision="1",
        now=captured,
    )


def common(receipt, binding, fingerprint):
    require(receipt.get("settingsBinding") == binding, "restart settings binding differs")
    require(receipt.get("targetFingerprint") == fingerprint, "restart target incarnation differs")


def smoke_inventory(reference, *, library_id, binding, fingerprint, protected, source_hashes):
    ledger = read(artifact(reference))
    require(
        ledger.get("schema") == "fli.hybrid-quality.smoke-ownership.v1"
        and ledger.get("libraryId") == library_id
        and ledger.get("protectedLibraryIds") == sorted(protected)
        and ledger.get("noAsyncWriters") is True,
        "smoke ownership ledger differs",
    )
    common(ledger, binding, fingerprint)
    rows = ledger["rows"]
    require(14 <= len(rows) <= 25, "bounded real smoke row inventory required")
    by_container = {name: [] for name in ("libraries", "documents", "chunks")}
    keys = []
    for item in rows:
        container, body = item["container"], item["body"]
        require(container in by_container, "unknown smoke container")
        require(
            body.get("libraryId") == library_id and body.get("smokeOwner") == ledger.get("runId"), "foreign smoke row"
        )
        by_container[container].append(body)
        keys.append((container, body["id"], body.get("documentId")))
    require(len(keys) == len(set(keys)), "duplicate owned smoke row")
    require(
        len(by_container["libraries"]) == 1
        and by_container["libraries"][0]["id"] == library_id
        and 1 <= len(by_container["documents"]) <= 12
        and len(by_container["chunks"]) == 12,
        "smoke must contain one library, source documents and twelve real chunks",
    )
    doc_ids = {row["id"] for row in by_container["documents"]}
    require(
        {row.get("sha256") for row in by_container["documents"]} == set(source_hashes),
        "smoke documents do not bind the original source bytes",
    )
    require(
        all(
            row.get("documentId") in doc_ids
            and row.get("generation") == 1
            and bool(row.get("text"))
            and len(row.get("embedding", [])) == 3072
            for row in by_container["chunks"]
        ),
        "smoke chunks do not bind actual first-generation source documents and vectors",
    )
    require(
        len(ledger.get("attemptedIds", [])) == len(rows)
        and set(ledger["attemptedIds"]) == {item["body"]["id"] for item in rows},
        "smoke attempted row inventory differs",
    )
    return len(rows)


def health_check(
    receipt,
    *,
    settings,
    binding,
    fingerprint,
    canary_sha,
    protected,
    forbidden_sources,
    evaluation_library,
    current,
    max_age,
):
    require(
        receipt.get("schema") == "fli.hybrid-quality.restart-health.v1" and receipt.get("status") == "passed",
        "complete current smoke evidence required",
    )
    started = fresh(receipt["startedAt"], current, max_age)
    completed = fresh(receipt["completedAt"], current, max_age)
    finalized = fresh(receipt["finalizedAt"], current, max_age)
    require(started <= completed <= finalized, "smoke timestamps reversed")
    common(receipt, binding, fingerprint)
    require(receipt.get("canarySha256") == canary_sha, "smoke canary binding differs")
    require(receipt.get("protectedLibraryIds") == sorted(protected), "smoke protected inventory differs")
    lib = receipt.get("libraryId")
    require(
        str(uuid.UUID(lib)) == lib and lib not in protected and lib != evaluation_library,
        "smoke scope must be separately owned",
    )
    sources = receipt.get("sourceHashes")
    require(
        isinstance(sources, list)
        and bool(sources)
        and all(hash_value(item) for item in sources)
        and set(sources).isdisjoint(forbidden_sources)
        and receipt.get("sourceDisjointFromBenchmark") is True,
        "smoke must use disjoint real sources",
    )
    require(
        receipt.get("producerSourceSha256") == sha(REPO / "scripts/hybrid_quality_smoke.py"),
        "smoke producer code differs",
    )
    artifacts(receipt.get("rawArtifacts"))
    source_paths = artifacts(receipt.get("sourceArtifacts"))
    require(set(sources) == {sha(path) for path in source_paths}, "smoke sources are not hash-bound originals")
    row_count = smoke_inventory(
        receipt["ownershipReceipt"],
        library_id=lib,
        binding=binding,
        fingerprint=fingerprint,
        protected=protected,
        source_hashes=sources,
    )
    checks = receipt["checks"]
    require(
        checks.get("writes") == {"expectedRows": row_count, "completedRows": row_count},
        "smoke real-chunk writes incomplete",
    )
    require(
        checks.get("pointReads") == {"expectedRows": row_count, "matchingRows": row_count},
        "smoke point reads incomplete",
    )
    require(checks.get("scopedQuery") == {"expectedChunks": 12, "returnedChunks": 12}, "smoke scoped query incomplete")
    for key, route in (("vectorQueries", "library-prefix"), ("hybridQueries", "no-partition-key")):
        rows = checks.get(key, [])
        require(
            len(rows) == 2
            and {row.get("queryId") for row in rows} == {"smoke-1", "smoke-2"}
            and all(
                row.get("resultCount") == 3 and row.get("foreignRows") == 0 and row.get("route") == route
                for row in rows
            ),
            "smoke ranking or route evidence incomplete",
        )
    require(checks.get("hybridListsDiffer") is True, "smoke hybrid ranking not established")
    cleanup = checks.get("cleanup", {})
    require(
        all(
            cleanup.get(key) == row_count
            for key in ("expectedRows", "deletedRows", "point404Count", "latePoint404Count")
        )
        and cleanup.get("scopedRows") == 0
        and cleanup.get("lateScopedRows") == 0
        and 30 <= cleanup.get("lateRecheckSeconds", 0) <= 60,
        "smoke cleanup and late absence required",
    )
    before = model(receipt["embeddingBeforeReceipt"], settings, current, max_age)
    after = model(receipt["embeddingAfterReceipt"], settings, current, max_age)
    before_raw = read(artifact(receipt["embeddingBeforeReceipt"]))
    after_raw = read(artifact(receipt["embeddingAfterReceipt"]))
    require(
        receipt["embeddingBeforeReceipt"]["sha256"] != receipt["embeddingAfterReceipt"]["sha256"]
        and timestamp(before_raw["capturedAt"]) <= started
        and completed <= timestamp(after_raw["capturedAt"]) <= finalized
        and receipt.get("embeddingDimensions") == 3072,
        "smoke needs distinct model readbacks bracketing its writes and cleanup",
    )
    require(before == after == receipt.get("embeddingBinding"), "smoke embedding deployment changed")
    return before


def workflow_check(receipt, *, settings, binding, expected_embedding, current, max_age):
    require(
        receipt.get("schema") == "fli.hybrid-quality.workflow-binding.v1"
        and receipt.get("status") == "complete"
        and receipt.get("readOnly") is True,
        "complete mounted workflow binding required",
    )
    started = fresh(receipt["capturedAt"], current, max_age)
    require(started <= fresh(receipt["finishedAt"], current, max_age), "workflow timestamps reversed")
    require(receipt.get("settingsBinding") == binding, "workflow settings binding differs")
    require(
        receipt.get("producerSourceSha256") == sha(REPO / "scripts/hybrid_quality_workflow.py"),
        "workflow producer code differs",
    )
    require(
        receipt.get("profileId") == "microsoft-indexer-layout-v1"
        and receipt.get("profileRecipeSha256") == sha(REPO / "src/fli/ingestion.py")
        and receipt.get("configurationSourceSha256") == sha(REPO / "src/fli/config.py"),
        "workflow recipe changed",
    )
    require(
        set(receipt.get("checks", {})) == WORKFLOW_CHECKS
        and all(value is True for value in receipt["checks"].values()),
        "workflow checks incomplete",
    )
    for key in (
        "workerConfigSha256",
        "workflowMountedPackageSha256",
        "expectedPackageSha256",
    ):
        require(hash_value(receipt.get(key)), "workflow actual content hash required")
    require(bool(receipt.get("workerReadyRevision")), "ready worker revision required")
    require(
        receipt.get("workflowEndpointSha256")
        == hashlib.sha256(settings.microsoft_workflow_endpoint.encode()).hexdigest(),
        "workflow endpoint binding differs",
    )
    artifacts(receipt.get("rawReceipts"))
    artifacts(receipt.get("inputBindings"))
    require(set(receipt.get("mountedFiles", {})) == PACKAGE_FILES, "mounted workflow file inventory differs")
    mounted = {name: read(artifact(reference)) for name, reference in receipt["mountedFiles"].items()}
    mounted_hashes = {name: canonical_hash(value) for name, value in mounted.items()}
    require(
        receipt.get("workflowMountedFilesSha256") == mounted_hashes
        and receipt.get("workflowMountedPackageSha256") == canonical_hash(mounted_hashes),
        "actual mounted workflow content differs",
    )
    package_path = artifact(receipt["expectedPackageReceipt"])
    require(sha(package_path) == receipt["expectedPackageSha256"], "reviewed workflow package differs")
    with zipfile.ZipFile(package_path) as archive:
        require(
            set(archive.namelist()) == PACKAGE_FILES
            and len(archive.infolist()) == len(PACKAGE_FILES)
            and all(item.file_size <= 10_000_000 for item in archive.infolist()),
            "workflow package inventory differs",
        )
        package = {name: json.loads(archive.read(name)) for name in PACKAGE_FILES}
    require(package == mounted, "mounted workflow differs from reviewed package")
    worker_raw = read(artifact(receipt["workerReceipt"]))
    require(worker_raw.get("status") == 200 and worker_raw.get("method") == "GET", "actual worker GET required")
    worker = worker_raw["body"]["properties"]
    container = worker["template"]["containers"][0]
    require(
        receipt["workerConfigSha256"] == canonical_hash({key: container.get(key) for key in WORKER_FIELDS})
        and receipt["workerReadyRevision"] == worker["latestReadyRevisionName"],
        "actual worker content differs",
    )
    revision = read(artifact(receipt["workerRevisionReceipt"]))
    require(revision.get("status") == 200 and revision.get("method") == "GET", "actual worker revision GET required")
    actual_model = model(receipt["embeddingReceipt"], settings, current, max_age)
    require(actual_model == expected_embedding == receipt.get("embeddingBinding"), "workflow embedding differs")


def cleanup_check(plan, *, root, binding, fingerprint, protected, manifest_sha, current):
    require(plan.get("schema") == "fli.hybrid-quality.partial-cleanup.v1", "partial ingestion cleanup plan required")
    require(timestamp(plan["declaredAt"]) <= current, "cleanup plan must precede ingestion")
    require(
        private_path(plan["evaluationStateDir"]) == root
        and private_path(plan["ownershipLedger"]) == root / "ownership.json",
        "cleanup ownership ledger differs",
    )
    common(plan, binding, fingerprint)
    require(
        plan.get("protectedLibraryIds") == sorted(protected)
        and plan.get("manifestSha256") == manifest_sha
        and type(plan.get("maximumEvaluationLibraries")) is int
        and plan["maximumEvaluationLibraries"] == 1,
        "cleanup scope contract differs",
    )
    policy = plan.get("policy", {})
    require(
        all(policy.get(key) is True for key in CLEANUP_TRUE)
        and all(policy.get(key) is False for key in CLEANUP_FALSE)
        and 30 <= policy.get("lateRecheckSeconds", 0) <= 60,
        "partial cleanup safety policy incomplete",
    )
    for file in ("scripts/hybrid_quality_scope.py", "scripts/hybrid_quality_failed_scope.py"):
        require(plan.get("helpers", {}).get(file) == sha(REPO / file), "cleanup helper code differs")


def validate_restart(
    path,
    *,
    root,
    settings,
    binding,
    preflight,
    canary_path,
    protected,
    manifest,
    manifest_sha,
    evaluation_library=None,
    max_age=3600,
    current=None,
):
    """Validate raw receipt bindings; return a ledger entry to persist before mutation."""
    require(type(max_age) is int and 60 <= max_age <= 3600, "restart freshness must be 60 to 3600 seconds")
    require(path is not None, "new ingestion requires --restart-preflight")
    current = current or datetime.now(UTC)
    path, root = private_path(path), private_path(root)
    receipt = read(path)
    require(receipt.get("schema") == "fli.hybrid-quality.restart.v1", "restart preflight schema differs")
    require(private_path(receipt["evaluationStateDir"]) == root, "restart receipt belongs to another ingestion ledger")
    completed = fresh(receipt["completedAt"], current, max_age)
    common(receipt, binding, preflight["targetFingerprint"])
    require(receipt.get("protectedLibraryIds") == sorted(protected), "restart protected inventory differs")
    require(receipt.get("canarySha256") == sha(canary_path), "restart canary differs")
    health = read(artifact(receipt["healthReceipt"]))
    workflow = read(artifact(receipt["workflowReceipt"]))
    cleanup = read(artifact(receipt["partialCleanupPlan"]))
    expected_model = health_check(
        health,
        settings=settings,
        binding=binding,
        fingerprint=preflight["targetFingerprint"],
        canary_sha=sha(canary_path),
        protected=protected,
        forbidden_sources={row["sha256"] for row in manifest["documents"]},
        evaluation_library=evaluation_library,
        current=current,
        max_age=max_age,
    )
    workflow_check(
        workflow,
        settings=settings,
        binding=binding,
        expected_embedding=expected_model,
        current=current,
        max_age=max_age,
    )
    cleanup_check(
        cleanup,
        root=root,
        binding=binding,
        fingerprint=preflight["targetFingerprint"],
        protected=protected,
        manifest_sha=manifest_sha,
        current=current,
    )
    require(
        completed >= max(timestamp(health["finalizedAt"]), timestamp(workflow["finishedAt"])),
        "restart finalized before its evidence",
    )
    return {
        "path": str(path),
        "sha256": sha(path),
        "acceptedAt": current.isoformat(),
        "maxAgeSeconds": max_age,
        "healthSha256": receipt["healthReceipt"]["sha256"],
        "workflowSha256": receipt["workflowReceipt"]["sha256"],
        "cleanupPlanSha256": receipt["partialCleanupPlan"]["sha256"],
    }
