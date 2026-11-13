"""Recovery proofs and exact mutation boundaries; synthetic data and fake SDKs only."""

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from fli.config import Settings
from fli.ingestion import job, profile
from scripts import hybrid_quality_failed_scope as r
from scripts.hybrid_quality_scope import binding

REPO = Path(__file__).resolve().parents[1]
LIB = "owned-library"
DOCS = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"]


def worker_repository(tmp_path):
    """Bind fake deployment evidence to its own immutable Git snapshot.

    A developer may be testing uncommitted worker edits. Such edits must still
    fail the real deployment guard; synthetic archives must not pretend that the
    development checkout's HEAD deployed those bytes. No production guard is mocked.
    """
    repository = tmp_path / "synthetic-worker"
    repository.mkdir()
    for name in r.WORKER_FILES:
        target = repository / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / name).read_bytes())
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    subprocess.run(["git", "add", "--", *r.WORKER_FILES], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Synthetic fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            "Bind synthetic worker bytes",
        ],
        cwd=repository,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    return repository, revision


def fixture(tmp_path, mutate=None):
    """Make a complete two-source archive; no live source body or environment IDs."""

    def save(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    sources, docs, blobs = [], [], {}
    recipe = profile("microsoft-indexer-layout-v1")
    recipe["embedding"]["modelRevision"] = "synthetic-revision"
    for index, doc_id in enumerate(DOCS):
        data = f"Synthetic source {index}.".encode()
        original = f"{LIB}/{doc_id}/original/1/Original.DOCX"
        source = {
            "id": f"s{index}",
            "fileName": "Original.DOCX",
            "sha256": hashlib.sha256(data).hexdigest(),
            "sizeBytes": len(data),
            "metadata": {"author": "Synthetic", "tags": [{"key": "title", "value": "Fixture"}]},
        }
        work = job(LIB, doc_id, 1, {"blobPath": original, "sha256": source["sha256"]}, recipe, 0)
        doc = {
            **source,
            "id": doc_id,
            "libraryId": LIB,
            "generation": 1,
            "metadataVersion": 0,
            "author": "Synthetic",
            "tags": [{"key": "title", "value": "Fixture", "source": "user"}],
            "status": "failed" if index == 0 else "deleting",
            "activeGeneration": None,
            "pendingGeneration": None if index == 0 else 1,
            "pendingFile": None,
            "ingestionJob": work,
        }
        sources.append(source)
        docs.append(doc)
        blobs[original] = data
        started = f"2026-01-01T00:0{index * 3}:00Z"
        start = {"attemptId": f"attempt{index}", "generation": 1, "stage": "ingest", "startedAt": started}
        finish = {
            **start,
            "finishedAt": f"2026-01-01T00:0{index * 3 + 1}:00Z",
            "status": "error" if index == 0 else "stopped",
        }
        if index == 0:
            finish.update(
                executionId=work["executionId"],
                sourceSha256=source["sha256"],
                profileId=recipe["id"],
                localEmbeddingUsage={},
                chunkWriteChargeComplete=False,
                providerResultUsage={},
                providerResultReused=False,
            )
            vector = [1.0] + [0.0] * 3071
            result = {
                "schema": "fli.indexer-result.v1",
                "workflowVersion": recipe["workflowVersion"],
                "job": work,
                "embedding": recipe["embedding"],
                "sourceBase64": base64.b64encode(data).decode(),
                "analysis": {"analyzeResult": {"modelId": "prebuilt-layout", "content": data.decode()}},
                "rows": [{"chunkIndex": 0, "totalChunks": 1, "text": data.decode(), "embedding": vector}],
            }
            plan = {
                "version": 1,
                "job": work,
                "generation": 1,
                "sourceSha256": source["sha256"],
                "chunkingVersion": "microsoft-token-window-v1",
                "imageRecords": {},
                "imageCount": 0,
                "textChunkCount": 1,
                "pieces": [{"index": 0, "text": data.decode(), "heading": None, "location": {}}],
                "vectors": [vector],
            }
            blobs[f"{LIB}/{doc_id}/text/1/plan.json"] = json.dumps(plan).encode()
            blobs[f"{LIB}/{doc_id}/text/1/microsoft-result.json"] = json.dumps(result).encode()
        blobs[f"{LIB}/{doc_id}/attempts/1-attempt{index}/start.json"] = json.dumps(start).encode()
        blobs[f"{LIB}/{doc_id}/attempts/1-attempt{index}/finish.json"] = json.dumps(finish).encode()
    library = {
        "id": LIB,
        "status": "deleting",
        "createdBy": "synthetic-run",
        "name": "Hybrid quality confirmation synthetic-run",
        "ingestionProfile": recipe,
        "updatedAt": "2026-01-01T00:02:00Z",
    }
    if mutate:
        mutate(docs, blobs, library)
    manifest = save("manifest.json", {"documents": sources})
    environment = {
        "ENVIRONMENT": "local",
        "COSMOS_ENDPOINT": "https://example.invalid",
        "COSMOS_DATABASE": "synthetic",
        "BLOB_ACCOUNT_URL": "https://example.invalid",
        "BLOB_CONTAINER": "synthetic",
        "SERVICEBUS_NAMESPACE": "synthetic",
        "SERVICEBUS_QUEUE": "synthetic",
    }
    env = save("env.json", environment)
    protected = save("protected.json", {"libraryIds": ["protected-library"]})
    contract = {
        "manifestSha256": r.sha(manifest),
        "protectedLibraryIds": ["protected-library"],
        "settingsBinding": binding(Settings.from_env(environment)),
    }
    owner = save(
        "owner.json",
        {
            "library": library,
            "createdByThisRun": True,
            "createAttempted": True,
            "contract": contract,
            "runId": "synthetic-run",
            "sources": {
                s["id"]: {"attempted": True, "documentId": d, "sourceSha256": s["sha256"]}
                for s, d in zip(sources, DOCS, strict=True)
            },
        },
    )
    repository, revision = worker_repository(tmp_path)
    container = {
        "image": "synthetic-image",
        "env": [{"name": k, "value": v} for k, v in {**environment, "FLI_CODE_REVISION": revision}.items()],
    }
    arm = save("worker.json", {"properties": {"template": {"containers": [container]}}})
    summary = save(
        "worker-summary.json",
        {
            "privateConfigurationSha256": hashlib.sha256(
                json.dumps(
                    {key: container.get(key) for key in ("image", "env", "command", "args")}, sort_keys=True
                ).encode()
            ).hexdigest(),
            "workerCodeRevision": revision,
            "existingWorkerConfigurationUnchanged": True,
        },
    )
    archive = tmp_path / "archive"
    archive.mkdir()
    rows = []
    for i, (path, data) in enumerate(blobs.items()):
        file = archive / f"{i}.blob"
        file.write_bytes(data)
        rows.append({"blobPath": path, "archiveFile": file.name, "bytes": len(data), "sha256": r.sha(file)})
    for name, value in {
        "blob-manifest-private.json": rows,
        "documents-after-private.json": docs,
        "documents-before-private.json": docs,
        "library-private.json": library,
        "blob-paths-after-private.json": list(blobs),
        "capture-receipt.json": {
            "libraryFenced": True,
            "pathsStableDuringCapture": True,
            "documentsStableDuringCapture": True,
            "startedAt": "2026-01-01T00:05:00Z",
            "completedAt": "2026-01-01T00:06:00Z",
            "archivedFiles": len(blobs),
        },
    }.items():
        save("archive/" + name, value)
    args = SimpleNamespace(
        repository=repository,
        output=tmp_path / "proof",
        env=env,
        manifest=manifest,
        ownership=owner,
        protected_scope=protected,
        worker_arm=arm,
        worker_summary=summary,
        worker_revision=revision,
        archive=[archive],
    )
    return args, docs, blobs


def test_proof_closes_planned_and_post_fence_empty_universes(tmp_path):
    args, _, _ = fixture(tmp_path)
    r.build(args)
    p = r.read(args.output / "proof-private.json")
    assert len(p["sources"]) == 2 and len(p["candidates"]) == 1 and len(p["attempts"]) == 2
    assert p["sources"][1]["postFenceEarlyStops"] == 1 and p["sources"][1]["candidateCount"] == 0
    assert p["builderSha256"] == r.sha(r.__file__)
    assert args.repository != REPO
    assert p["workerCodeSha256"] == {name: r.sha(args.repository / name) for name in r.WORKER_FILES}
    assert r.read(args.output / "build-summary.json")["cleanupExecuted"] is False


def test_real_deployment_guard_rejects_working_bytes_that_differ_from_recorded_git_revision(tmp_path):
    args, _, _ = fixture(tmp_path)
    path = args.repository / "src/fli/worker/handlers.py"
    path.write_bytes(path.read_bytes() + b"\n# Synthetic uncommitted deployment drift.\n")
    with pytest.raises(ValueError, match="deployed worker code differs from analyzed repository"):
        r.build(args)
    assert not (args.output / "proof-private.json").exists()
    summary = r.read(args.output / "build-summary.json")
    assert summary["status"] == "unknown" and not summary["cleanupExecuted"]


@pytest.mark.parametrize(
    "bad", ["unmatched-attempt", "provider-result-missing", "active-source", "unbound-provider", "unfenced", "bad-plan"]
)
def test_incomplete_or_ambiguous_publication_proof_stops_unknown(tmp_path, bad):
    def mutate(docs, blobs, library):
        prefix = f"{LIB}/{DOCS[0]}/"
        if bad == "unmatched-attempt":
            del blobs[prefix + "attempts/1-attempt0/finish.json"]
        elif bad == "provider-result-missing":
            del blobs[prefix + "text/1/microsoft-result.json"]
        elif bad == "active-source":
            docs[0]["status"] = "processing"
        elif bad == "unfenced":
            library["status"] = "active"
        elif bad == "unbound-provider":
            path = prefix + "attempts/1-attempt0/finish.json"
            value = json.loads(blobs[path])
            del value["chunkWriteChargeComplete"]
            blobs[path] = json.dumps(value).encode()
        else:
            path = prefix + "text/1/plan.json"
            value = json.loads(blobs[path])
            value["pieces"][0]["text"] = "Different synthetic text"
            blobs[path] = json.dumps(value).encode()

    args, _, _ = fixture(tmp_path, mutate)
    with pytest.raises(ValueError):
        r.build(args)
    assert r.read(args.output / "build-summary.json")["status"] == "unknown"
    assert not (args.output / "proof-private.json").exists()
    assert len(list((args.output / "events").glob("*.json"))) == 1


def test_archived_hash_tamper_and_foreign_scope_are_rejected(tmp_path):
    args, _, _ = fixture(tmp_path)
    directory = args.archive[0]
    assert r.load_archive(directory, LIB, set(DOCS))
    with pytest.raises(ValueError, match="unknown scope"):
        r.load_archive(directory, "another", set(DOCS))
    (directory / "0.blob").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        r.load_archive(directory, LIB, set(DOCS))


def test_event_errors_are_append_only(tmp_path):
    r.event(tmp_path, "failed", errorType="FirstError")
    r.event(tmp_path, "failed", errorType="SecondError")
    files = list((tmp_path / "events").glob("*.json"))
    assert len(files) == 2
    assert {r.read(p)["errorType"] for p in files} == {"FirstError", "SecondError"}


@pytest.mark.parametrize(
    "failure",
    [
        "builder-changed",
        "worker-code-changed",
        "archive-corrupt",
        "archive-missing",
        "archive-symlink",
        "escaped-candidate",
        "short-late-check",
        "chunk-precheck-500",
        "independent-500",
        "late-blob",
        None,
    ],
)
async def test_point_recovery_never_converts_failures_to_absence(tmp_path, monkeypatch, failure):
    import azure.cosmos.aio
    import azure.identity.aio

    from scripts import retrieval_i1_lifecycle

    build_args, original_docs, original_blobs = fixture(tmp_path)
    # Earlier and latest snapshots must both remain intact, including duplicate paths.
    earlier = tmp_path / "earlier-archive"
    shutil.copytree(build_args.archive[0], earlier)
    build_args.archive.insert(0, earlier)
    r.build(build_args)
    proof_path = build_args.output / "proof-private.json"
    p = r.read(proof_path)
    lib = p["libraryId"]
    docs = {d["id"]: d for d in original_docs}
    blobs = dict(original_blobs)
    library = copy.deepcopy(p["library"])
    calls = []
    remaining = {p["candidates"][0]["id"]: {**p["candidates"][0], "_etag": "expected-etag"}}
    blob_lists = 0
    api_opens = []
    if failure and failure.startswith("archive-"):
        archived = earlier / "0.blob"
        if failure == "archive-corrupt":
            archived.write_bytes(b"corrupted retained source")
        elif failure == "archive-missing":
            archived.unlink()
        else:
            target = tmp_path / "symlink-target.blob"
            target.write_bytes(archived.read_bytes())
            archived.unlink()
            archived.symlink_to(target)
    if failure == "builder-changed":
        p["builderSha256"] = "other"
    if failure == "worker-code-changed":
        path = build_args.repository / "src/fli/worker/handlers.py"
        path.write_bytes(path.read_bytes() + b"\n# Synthetic drift after proof acceptance.\n")
    if failure == "escaped-candidate":
        p["candidates"][0]["libraryId"] = "protected-library"
    proof_path.write_text(json.dumps(p))

    class Chunk:
        async def read_item(self, id, partition_key):
            assert partition_key == [lib, next(c["documentId"] for c in p["candidates"] if c["id"] == id)]
            if failure == "chunk-precheck-500":
                raise HttpResponseError(message="synthetic500", status_code=500)
            if id not in remaining:
                raise ResourceNotFoundError(message="missing")
            return remaining[id]

        async def delete_item(self, id, partition_key, **kwargs):
            assert kwargs["etag"] == "expected-etag" and partition_key[0] == lib
            calls.append(("chunk-delete", id))
            remaining.pop(id, None)

        def query_items(self, *args, **kwargs):
            raise AssertionError("SQL/feed forbidden")

    class Libraries:
        async def get(self, id):
            assert id == lib
            return library or None

        async def delete(self, id):
            assert id == lib
            calls.append(("library-delete", id))
            library.clear()

    class Documents:
        async def get(self, library_id, d):
            assert library_id == lib
            return docs.get(d)

        async def delete(self, library_id, d):
            assert library_id == lib and d in docs
            calls.append(("document-delete", d))
            docs.pop(d)

    class Blobs:
        async def list_prefix(self, prefix):
            nonlocal blob_lists
            assert prefix == lib + "/"
            blob_lists += 1
            if failure == "late-blob" and blob_lists >= 4:
                return [lib + "/late-provider-artifact"]
            return list(blobs)

        async def download(self, path):
            assert path in original_blobs
            return blobs[path]

        async def delete(self, path):
            assert path in original_blobs
            calls.append(("blob-delete", path))
            blobs.pop(path, None)

    app = SimpleNamespace(
        state=SimpleNamespace(
            libraries=Libraries(),
            documents=Documents(),
            blobs=Blobs(),
            searcher=SimpleNamespace(chunks=SimpleNamespace(_c=Chunk())),
        )
    )

    @asynccontextmanager
    async def api_instance(_settings):
        api_opens.append(True)
        yield app, None

    class Credential:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Cosmos:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get_database_client(self, name):
            return self

        def get_container_client(self, name):
            class Independent:
                async def read_item(self, id, partition_key):
                    if failure == "independent-500":
                        raise HttpResponseError(message="synthetic500", status_code=500)
                    if name == "chunks":
                        assert partition_key[0] == lib
                        if id in remaining:
                            return remaining[id]
                    elif name == "libraries":
                        assert id == lib and partition_key == lib
                        if library:
                            return library
                    else:
                        assert name == "documents" and partition_key == lib
                        if id in docs:
                            return docs[id]
                    raise ResourceNotFoundError(message="missing")

                def query_items(self, *args, **kwargs):
                    raise AssertionError("SQL/feed forbidden")

            return Independent()

    async def fake_sleep(seconds):
        assert seconds == 60

    monkeypatch.setattr(retrieval_i1_lifecycle, "api_instance", api_instance)
    monkeypatch.setattr(azure.cosmos.aio, "CosmosClient", Cosmos)
    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(r.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(r.os, "environ", os.environ.copy())
    args = SimpleNamespace(
        output=tmp_path / "result",
        proof=proof_path,
        proof_sha256=r.sha(proof_path),
        maximum_candidates=1,
        repository=build_args.repository,
        env=build_args.env,
        late_recheck=0 if failure == "short-late-check" else 60,
    )
    if failure:
        message = "analyzed worker code changed" if failure == "worker-code-changed" else None
        with pytest.raises((HttpResponseError, ValueError), match=message):
            await r.execute(args)
        assert r.read(args.output / "summary.json")["cleanupComplete"] is False
    else:
        await r.execute(args)
        assert r.read(args.output / "summary.json")["status"] == "complete"
    if failure and failure.startswith("archive-"):
        assert not api_opens and calls == [] and blobs == original_blobs and len(docs) == 2
    if failure in (
        "builder-changed",
        "worker-code-changed",
        "escaped-candidate",
        "short-late-check",
        "chunk-precheck-500",
    ):
        assert calls == [] and blobs == original_blobs and len(docs) == 2
    if failure == "worker-code-changed":
        assert not api_opens
    if failure == "independent-500":
        assert all(kind == "chunk-delete" for kind, _ in calls) and blobs == original_blobs and len(docs) == 2
    if failure is None:
        assert not remaining and not blobs and not docs and not library
    assert list((args.output / "events").glob("*.json"))
