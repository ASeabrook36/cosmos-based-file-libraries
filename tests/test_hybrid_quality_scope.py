"""Owned-scope safety tests: no network, credentials, provisioning or Azure calls."""

import copy
import hashlib
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from scripts import hybrid_quality_scope as scope


class Store:
    def __init__(self, rows):
        self.rows = rows

    async def get(self, library_id):
        return self.rows.get(library_id)

    async def list(self, library_id, **_kwargs):
        return [row for row in self.rows.values() if row["libraryId"] == library_id]


class Blobs:
    async def list_prefix(self, prefix):
        assert prefix.endswith("/")
        return []


class Client:
    def __init__(self, owner, app, source, ambiguous=None):
        self.owner, self.app, self.source = owner, app, source
        self.calls, self.ambiguous = [], ambiguous

    async def request(self, method, path, **kwargs):
        durable = json.loads(self.owner.path.read_text())
        self.calls.append((method, path))
        if path == "/v1/libraries":
            assert durable["createAttempted"] is True
            if self.ambiguous == "create":
                raise TimeoutError("response lost")
            body = kwargs["json"]
            record = {**body, "id": str(uuid.uuid4()), "ingestionProfile": {"id": scope.PROFILE}}
            self.app.state.libraries.rows[record["id"]] = record
            return httpx.Response(201, json=record)
        if method == "POST":
            assert durable["sources"]["s1"]["attempted"] is True
            if self.ambiguous == "upload":
                raise TimeoutError("response lost")
            source = self.source
            doc_id = str(uuid.uuid4())
            row = {
                "id": doc_id,
                "libraryId": self.owner.owned(),
                "fileName": source["fileName"],
                "sha256": source["sha256"],
                "sizeBytes": source["sizeBytes"],
                "status": "ready",
                "generation": 1,
                "activeGeneration": 1,
                "pendingGeneration": None,
                "metadataVersion": 0,
                "author": source["metadata"]["author"],
                "tags": [{**tag, "source": "user"} for tag in source["metadata"]["tags"]],
                "ingestionJob": {"profile": {"id": scope.PROFILE}},
                "chunkCount": 4,
                "processingMetrics": {"embeddingTokens": None, "executionId": "private-runtime-id"},
            }
            self.app.state.documents.rows[doc_id] = row
            return httpx.Response(202, json={"documentId": doc_id})
        assert method == "DELETE" and durable["deleteAttempted"] is True
        self.app.state.documents.rows.clear()
        self.app.state.libraries.rows.clear()
        return httpx.Response(202)


def prepared(tmp_path, ambiguous=None):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    data = b"fixture source bytes"
    (source_dir / "Original.DOCX").write_bytes(data)
    source = {
        "id": "s1",
        "fileName": "Original.DOCX",
        "sha256": hashlib.sha256(data).hexdigest(),
        "sizeBytes": len(data),
        "metadata": {"author": "Public author", "tags": [{"key": "title", "value": "Title"}]},
    }
    manifest = {"documents": [source], "tasksSha256": "frozen-task-hash"}
    root = tmp_path / "runtime"
    owner = scope.Scope(root, manifest, {"BINDING": "private-endpoint"}, ["protected-library"], "manifest-hash")
    app = SimpleNamespace(state=SimpleNamespace(libraries=Store({}), documents=Store({}), blobs=Blobs()))
    return owner, app, Client(owner, app, source, ambiguous), source_dir


async def test_create_upload_are_durably_recorded_and_resume_does_not_duplicate(tmp_path):
    owner, app, client, source_dir = prepared(tmp_path)
    result = await owner.ingest(app, client, source_dir)
    assert result["readyDocuments"] == 1 and result["publishedChunkCount"] == 4
    assert result["sources"][0]["providerUsage"]["embeddingTokens"] is None
    assert "private-runtime-id" not in json.dumps(result)
    resumed = scope.Scope(
        owner.root, owner.manifest, owner.contract["settingsBinding"], ["protected-library"], "manifest-hash"
    )
    await resumed.ingest(app, client, source_dir)
    assert len(client.calls) == 2
    assert (owner.root / "evaluation-library.json").exists()
    assert owner.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("step", ["create", "upload"])
async def test_lost_mutation_response_stops_retries_instead_of_duplicate_resources(tmp_path, step):
    owner, app, client, source_dir = prepared(tmp_path, ambiguous=step)
    with pytest.raises(TimeoutError):
        await owner.ingest(app, client, source_dir)
    count = len(client.calls)
    with pytest.raises(ValueError, match="ambiguous"):
        await owner.ingest(app, client, source_dir)
    assert len(client.calls) == count


async def test_protected_or_metadata_changed_scope_cannot_be_deleted(tmp_path):
    owner, app, client, source_dir = prepared(tmp_path)
    await owner.ingest(app, client, source_dir)
    owner.protected.add(owner.owned())
    with pytest.raises(ValueError, match="protected"):
        await owner.cleanup(app, client, late_recheck=0)
    owner.protected = {"protected-library"}
    next(iter(app.state.documents.rows.values()))["tags"][0]["source"] = "generated"
    with pytest.raises(ValueError, match="metadata differs"):
        await owner.cleanup(app, client, late_recheck=0)
    assert all(method != "DELETE" for method, _ in client.calls)


async def test_cleanup_waits_for_ready_and_verifies_late_artifacts(tmp_path, monkeypatch):
    owner, app, client, source_dir = prepared(tmp_path)
    with pytest.raises(ValueError, match="completed ready"):
        await owner.cleanup(app, client, late_recheck=0)
    await owner.ingest(app, client, source_dir)
    calls = []

    async def chunks(_app, library_id):
        assert library_id == owner.owned()
        calls.append(library_id)
        return [] if len(calls) == 1 else [{"id": "late-owned-artifact"}]

    monkeypatch.setattr(scope, "library_chunks", chunks)
    with pytest.raises(ValueError, match="late artifact"):
        await owner.cleanup(app, client, late_recheck=0)
    assert not owner.state.get("cleanupComplete")
    assert len(calls) == 2
    assert client.calls[-1] == ("DELETE", "/v1/libraries/" + owner.owned())


async def test_complete_cleanup_checks_owned_scope_only_and_reports_live_absence(tmp_path, monkeypatch):
    owner, app, client, source_dir = prepared(tmp_path)
    await owner.ingest(app, client, source_dir)

    async def chunks(_app, library_id):
        assert library_id == owner.owned()
        return []

    monkeypatch.setattr(scope, "library_chunks", chunks)
    result = await owner.cleanup(app, client, late_recheck=0)
    assert result["lateRecheckPassed"] is True
    assert result["protectedLibrariesMutated"] == 0
    assert owner.state["cleanupComplete"] is True
    assert owner.owned() not in json.dumps(result)


def test_changed_runtime_binding_and_manifest_are_refused(tmp_path):
    owner, _app, _client, _source_dir = prepared(tmp_path)
    for arguments in [
        ({"BINDING": "another-endpoint"}, "manifest-hash"),
        (owner.contract["settingsBinding"], "changed"),
    ]:
        with pytest.raises(ValueError, match="ledger binding"):
            scope.Scope(owner.root, owner.manifest, arguments[0], ["protected-library"], arguments[1])


@pytest.mark.parametrize("fresh", [False, True])
def test_preflight_requires_passing_bound_canary_and_matching_controls(tmp_path, fresh):
    canary_path = tmp_path / "canary.json"
    canary = {"passed": True, "checks": [{"passed": True}], "targetFingerprint": "target"}
    canary_path.write_text(json.dumps(canary))
    preflight = {
        "inheritedCanaryPassed": not fresh,
        "freshCanaryPassed": fresh,
        "incarnationAndPoliciesMatch": True,
        "newContainerCreated": fresh,
        "scaleResourcesAccessed": False,
        "canarySha256": scope.sha(canary_path),
        "targetFingerprint": "target",
    }
    settings = SimpleNamespace(
        environment="local",
        auth_mode="dev",
        aoai_embedding_dimensions=3072,
        aoai_embedding_model_revision="1",
        search_default_top=8,
        search_max_top=20,
    )
    scope.checked_preflight(settings, preflight, canary_path)
    wrong = copy.deepcopy(preflight)
    wrong["targetFingerprint"] = "other"
    with pytest.raises(ValueError, match="canary binding"):
        scope.checked_preflight(settings, wrong, canary_path)


@pytest.mark.parametrize(
    "damage",
    [
        "inherited-label",
        "missing-fresh-pass",
        "legacy-hash-only",
        "wrong-receipt",
        "failed-canary",
        "empty-checks",
        "failed-check",
        "different-incarnation",
        "policies-unverified",
        "scaling-access",
        "not-boolean",
    ],
)
def test_fresh_container_requires_its_own_unambiguous_passing_canary(tmp_path, damage):
    path = tmp_path / "canary.json"
    canary = {"passed": True, "checks": [{"passed": True}], "targetFingerprint": "fresh-target"}
    settings = SimpleNamespace(
        environment="local",
        auth_mode="dev",
        aoai_embedding_dimensions=3072,
        aoai_embedding_model_revision="1",
        search_default_top=8,
        search_max_top=20,
    )
    preflight = {
        "newContainerCreated": True,
        "freshCanaryPassed": True,
        "inheritedCanaryPassed": False,
        "incarnationAndPoliciesMatch": True,
        "scaleResourcesAccessed": False,
        "targetFingerprint": "fresh-target",
    }
    if damage == "inherited-label":
        preflight["inheritedCanaryPassed"] = True
    elif damage == "missing-fresh-pass":
        preflight.pop("freshCanaryPassed")
    elif damage == "failed-canary":
        canary["passed"] = False
    elif damage == "empty-checks":
        canary["checks"] = []
    elif damage == "failed-check":
        canary["checks"][0]["passed"] = False
    elif damage == "different-incarnation":
        preflight["targetFingerprint"] = "old-target"
    elif damage == "policies-unverified":
        preflight["incarnationAndPoliciesMatch"] = False
    elif damage == "scaling-access":
        preflight["scaleResourcesAccessed"] = True
    elif damage == "not-boolean":
        preflight["newContainerCreated"] = 1
    path.write_text(json.dumps(canary))
    preflight["canarySha256"] = scope.sha(path)
    if damage == "wrong-receipt":
        path.write_text(json.dumps({**canary, "changed": True}))
    elif damage == "legacy-hash-only":
        preflight["inheritedCanarySha256"] = preflight.pop("canarySha256")
    with pytest.raises(ValueError, match="canary binding"):
        scope.checked_preflight(settings, preflight, path)


@pytest.mark.parametrize("damage", [None, "incarnation", "libraries-policy", "documents-policy", "chunks-policy"])
async def test_live_incarnation_and_all_actual_policies_still_gate_new_containers(damage):
    class Container:
        def __init__(self, name):
            self.actual = json.loads((scope.REPO / "cosmos" / f"{name}.container.json").read_text())
            self.actual["_rid"] = "fresh-" + name

        async def read(self):
            return self.actual

    containers = {name: Container(name) for name in ("libraries", "documents", "chunks")}
    settings = SimpleNamespace(cosmos_endpoint="https://isolated.example.test", cosmos_database="evaluation")
    preflight = {
        "newContainerCreated": True,
        "targetFingerprint": scope.target_fingerprint(
            settings.cosmos_endpoint, settings.cosmos_database, "chunks", containers["chunks"].actual["_rid"]
        ),
    }
    app = SimpleNamespace(
        state=SimpleNamespace(
            searcher=SimpleNamespace(chunks=SimpleNamespace(_c=containers["chunks"])),
            libraries=SimpleNamespace(_c=SimpleNamespace(_c=containers["libraries"])),
            documents=SimpleNamespace(_c=SimpleNamespace(_c=containers["documents"])),
        )
    )
    if damage == "incarnation":
        containers["chunks"].actual["_rid"] = "recreated-chunks"
    elif damage:
        containers[damage.removesuffix("-policy")].actual["indexingPolicy"]["indexingMode"] = "none"
    if damage is None:
        await scope.check_incarnation(app, settings, preflight)
    else:
        with pytest.raises(ValueError, match="incarnation differs" if damage == "incarnation" else "policy differs"):
            await scope.check_incarnation(app, settings, preflight)


def test_operation_lock_prevents_parallel_create_attempts(tmp_path):
    with scope.locked(tmp_path):
        with pytest.raises(BlockingIOError), scope.locked(tmp_path):
            pass


async def test_scope_rehearsal_uses_real_api_and_worker_with_original_metadata(tmp_path, monkeypatch):
    from fli.chunking import ApproxTokenizer, Chunker
    from fli.worker.handlers import Worker
    from tests.fakes import Services, api
    from tests.test_modular_ingestion import MicrosoftFake
    from tests.test_worker_handlers import drain

    owner, _app, _client, source_dir = prepared(tmp_path)
    services = Services()
    test_api, _ = api(services, AOAI_EMBEDDING_MODEL_REVISION="1")
    worker = Worker(
        services.libraries,
        services.documents,
        services.chunks,
        services.blobs,
        services.queue,
        services.embedder,
        Chunker(ApproxTokenizer()),
        microsoft=MicrosoftFake(),
        stale_read_pause=0,
        embedding_model_revision="1",
    )

    class Rehearsal(scope.Scope):
        async def request(self, *args, **kwargs):
            result = await super().request(*args, **kwargs)
            await drain(services, worker)
            return result

    owner = Rehearsal(
        owner.root, owner.manifest, owner.contract["settingsBinding"], ["protected-library"], "manifest-hash"
    )

    async def chunks(_app, library_id):
        return [{"id": row["id"]} for (library, _, _), row in services.chunks.items.items() if library == library_id][
            :1
        ]

    monkeypatch.setattr(scope, "library_chunks", chunks)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_api.app), base_url="http://local", headers={"Authorization": "Bearer k"}
    ) as client:
        result = await owner.ingest(test_api.app, client, source_dir)
        assert result["readyDocuments"] == 1 and result["publishedChunkCount"] > 0
        receipt = await owner.cleanup(test_api.app, client, late_recheck=0)
    assert receipt["lateRecheckPassed"]
    assert not await services.documents.list(owner.owned(), status=None, facet=None)
    assert not services.chunks.items and not services.blobs.blobs
