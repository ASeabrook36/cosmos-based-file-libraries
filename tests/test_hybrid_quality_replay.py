"""The confirmation driver cannot silently change its corpus, wire or execution."""

import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from fli.ingestion import profile
from scripts import hybrid_quality_api as api
from scripts import hybrid_quality_replay as h
from scripts.evaluate_agent_retrieval import snapshot


class Index:
    def __init__(self, arm):
        self.arm = arm

    def get_library(self, library):
        return {"id": library, "name": "Neutral isolated library"}

    def list_documents(self, library):
        return [{"id": "document", "activeGeneration": 1}]

    def tools(self, library, agent):
        return [{"function": {"name": "read_documents_library_frozen", "description": "Read original source."}}]


@pytest.fixture
def pack(tmp_path, monkeypatch):
    config = {
        "model": {"deployment": "fixed", "model": "gpt-6.1-sol", "version": "2026-09-29"},
        "agent": {"id": "agent", "name": "Agent", "instructions": h.AGENT_INSTRUCTIONS},
        "libraryId": "evaluation-library",
        "indexUrls": {"vector": "http://127.0.0.1:8124", "hybrid": "http://127.0.0.1:8125"},
    }
    text = "Original condition and limit."
    region = {
        "id": "r",
        "kind": "paragraph",
        "index": 3,
        "sourceSha256": "f" * 64,
        "normalizedTextSha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    fact = {"sourceId": "source", "source": "original.docx", "regions": [region]}
    tasks = [
        {
            "case": f"q-{i}",
            "question": f"Question {i}",
            "split": "confirmation",
            "families": ["factual"],
            "answerable": i != 39,
            "sourceFacts": [fact] if i != 39 else [],
            "requiredFactIds": ["fact"] if i != 39 else [],
        }
        for i in range(40)
    ]
    manifest = {"documents": [{"id": "source", "fileName": "original.docx", "sha256": "f" * 64}]}
    gold = {
        "regions": {
            "r": {
                "sourceId": "source",
                "fileName": "original.docx",
                "expectedText": text,
                "locator": {"kind": "paragraph", "index": 3},
            }
        },
        "facts": {"fact": {"sourceRegionIds": ["r"], "expectedFact": text}},
        "tasks": {},
    }
    for name, value in (("tasks", tasks), ("manifest", manifest), ("gold", gold)):
        h.save(tmp_path / f"{name}.json", value)
    plan = {
        "schema": "fli.hybrid-quality.plan.v1",
        "baseCommit": h.BASE,
        "arms": list(h.ARMS),
        "repeats": 2,
        "evidenceView": "compact",
        "selectorMode": "handles",
        "sourceStrategy": "microsoft-indexer-layout-v1",
        "answering": h.ANSWERING,
        "direct": h.DIRECT,
        "cases": [t["case"] for t in tasks],
        **{f"{n}Sha256": h.sha(tmp_path / f"{n}.json") for n in ("tasks", "manifest", "gold")},
    }
    h.save(tmp_path / "plan.json", plan)
    now = datetime.now(UTC).isoformat()
    runtimes = {}
    for ordinal, arm in enumerate(h.ARMS):
        binding = dict.fromkeys(h.BINDING_KEYS, "isolated-test-setting")
        binding.update(
            AOAI_ENDPOINT="https://models.example.test",
            SEARCH_MODE=arm,
            AOAI_EMBEDDING_MODEL_REVISION="1",
            AOAI_EMBEDDING_DIMENSIONS="3072",
            SEARCH_DEFAULT_TOP="8",
            SEARCH_MAX_TOP="20",
        )
        runtimes[arm] = {
            "schema": "fli.hybrid-quality.api-runtime.v1",
            "capturedAt": now,
            "indexUrl": config["indexUrls"][arm],
            "apiSourceSha256": h.api_hashes(),
            "measurementCodeSha256": {"code": "frozen"},
            "launcherSha256": h.sha(Path(api.__file__)),
            "settingsBinding": binding,
            "settingsBindingSha256": h.digest(binding),
            "effectiveSettings": {**h.SETTINGS, "searchMode": arm},
            "authenticationMode": "dev",
            "environment": "local",
            "processId": 123 + ordinal,
            "ownEnvironmentAndImportVerified": True,
            "sourceRoot": str(tmp_path / "source"),
            "pythonPrefix": str(tmp_path / "source/.venv"),
            "gitRevision": "frozen-code",
            "declaredCodeRevision": "frozen-code",
            "storedRowsSha256": h.digest([{"id": "chunk", "_etag": "fixed", "contentHash": "source-body"}]),
        }
    deployment = {
        "capturedAt": now,
        "account": {"id": "/accounts/isolated", "properties": {"endpoint": binding["AOAI_ENDPOINT"]}},
        "deployment": {
            "id": "/accounts/isolated/deployments/fixed",
            "sku": {"name": "GlobalStandard", "capacity": 200},
            "properties": {
                "model": {"format": "OpenAI", "name": "gpt-6.1-sol", "version": "2026-09-29"},
                "versionUpgradeOption": "NoAutoUpgrade",
                "provisioningState": "Succeeded",
            },
        },
    }
    agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
    embedding = copy.deepcopy(deployment)
    embedding["deployment"]["id"] = "/accounts/isolated/deployments/" + binding["AOAI_EMBEDDING_DEPLOYMENT"]
    embedding["deployment"]["properties"]["model"].update(name="text-embedding-3-large", version="1")
    h.save(tmp_path / "query-cache-private.json", {"deploymentBinding": h.embedding_binding(embedding, runtimes)})
    baseline = snapshot(Index("vector"), agent)
    direct_source = {
        **baseline["libraries"][config["libraryId"]],
        "chunks": [{"id": "chunk", "_etag": "fixed", "contentHash": "source-body"}],
    }
    for name in ("source-before.json", "source-after.json"):
        h.save(tmp_path / name, direct_source)
    raw = tmp_path / "direct-raw.json"
    raw.write_text('{"measured":true}\n')
    direct = {
        "status": "complete",
        "finishedAt": now,
        "planSha256": h.sha(tmp_path / "plan.json"),
        "manifestSha256": plan["manifestSha256"],
        "tasksSha256": plan["tasksSha256"],
        "rawArtifactPaths": {"direct": str(raw)},
        "rawArtifactsSha256": {"direct": h.sha(raw)},
        "sourceSnapshotSha256": h.digest(direct_source),
        "settingsBindingSha256": h.digest({k: v for k, v in binding.items() if k != "SEARCH_MODE"}),
        "codeSha256": h.api_hashes(),
        "rows": [
            {"case": t["case"], "arm": arm, "initialRegions": [], "expandedRegions": []}
            for t in tasks
            for arm in h.ARMS
        ],
    }
    for name in ("source-before.json", "source-after.json", "query-cache-private.json"):
        direct["rawArtifactPaths"][name] = str(tmp_path / name)
        direct["rawArtifactsSha256"][name] = h.sha(tmp_path / name)
    h.save(tmp_path / "direct.json", direct)
    out = tmp_path / "run"
    monkeypatch.setattr(h, "hashes", lambda: {"code": "frozen"})
    authorization = {
        "schema": "fli.hybrid-quality.execution-authorization.v1",
        "status": "accepted",
        "reviewedAt": now,
        "directReceiptSha256": h.sha(tmp_path / "direct.json"),
        "planSha256": h.sha(tmp_path / "plan.json"),
        "sourceSnapshotSha256": h.digest(baseline),
        "runtimeBindingsSha256": h.digest(h.runtime_bindings(runtimes, config)),
        "codeSha256": h.digest(h.hashes()),
        "corpusIndependentlyReviewed": True,
        "sourceLibraryBoundariesVerified": True,
        "hybridRanksMeaningfullyVerified": True,
        "outputDirectory": str(out),
        "executionLedger": str(tmp_path / "claimed.json"),
    }
    monkeypatch.setenv("AOAI_ENDPOINT", binding["AOAI_ENDPOINT"])
    return {
        "directory": tmp_path,
        "config": config,
        "runtimes": runtimes,
        "deployment": deployment,
        "embedding": embedding,
        "authorization": authorization,
        "out": out,
        "tasks": tasks,
        "gold": gold,
        "baseline": baseline,
    }


def execute(pack):
    directory = pack["directory"]
    return h.run(
        directory / "plan.json",
        directory / "manifest.json",
        directory / "tasks.json",
        pack["config"],
        pack["deployment"],
        pack["runtimes"],
        directory / "direct.json",
        pack["authorization"],
        pack["out"],
        embedding=pack["embedding"],
    )


def services(monkeypatch, seen):
    monkeypatch.setattr(h, "make_index", lambda config, arm: Index(arm))
    monkeypatch.setattr(h, "validate_snapshot", lambda *args: None)
    monkeypatch.setattr(h, "default_chat_factory", lambda *args: lambda *args: None)

    def replay(index, chat, agent, cases, output, budget):
        assert budget == 200000 and len(cases) == 1 and set(cases[0]) == {"case", "question"}
        case = cases[0]
        seen.append((index.arm, case["case"]))
        output.mkdir()
        baseline = snapshot(index, agent)
        tools = baseline["libraries"][agent["libraryIds"][0]]["tools"]
        messages = [{"role": "system", "content": h.AGENT_INSTRUCTIONS}, {"role": "user", "content": case["question"]}]
        chat(messages, tools)
        reply = {
            "mode": "full",
            "complete": True,
            "documents": [{"fileName": "original.docx", "sections": [{"text": "Original condition and limit."}]}],
            "usage": {"requestCharge": 17},
            "telemetryId": "private",
            "score": 0.1,
        }
        messages.append({"role": "tool", "content": json.dumps(reply)})
        chat(messages, tools)
        rows = [
            {
                **case,
                "turn": 1,
                "answer": "Original condition and limit.",
                "toolReplies": [reply],
                "metrics": {"modelCalls": [{"promptTokens": 10}, {"promptTokens": 20}]},
            }
        ]
        for name, value in (
            ("run-status", {"status": "complete", "completedTurns": 1, "budget": budget}),
            ("cases", cases),
            ("source-snapshot", baseline),
            ("snapshot-check", {"unchanged": True, "before": baseline, "after": baseline}),
            ("results", rows),
        ):
            h.save(output / f"{name}.json", value)
        return rows

    monkeypatch.setattr(h, "replay", replay)


def final_receipts(pack):
    after, runtime = copy.deepcopy(pack["deployment"]), copy.deepcopy(pack["runtimes"])
    now = datetime.now(UTC).isoformat()
    after["capturedAt"] = now
    for value in runtime.values():
        value["capturedAt"] = now
    embedding = copy.deepcopy(pack["embedding"])
    embedding["capturedAt"] = now
    return after, runtime, embedding


def test_full_matrix_is_interleaved_source_only_and_claimed_once(pack, monkeypatch):
    seen = []
    services(monkeypatch, seen)
    # Execution needs only a hash binding; the private answers can be unavailable.
    (pack["directory"] / "gold.json").unlink()
    assert execute(pack)["completedTurns"] == 160
    expected = h.schedule(pack["tasks"])
    assert seen == [(m["arm"], m["case"]) for m in expected]
    assert seen[:4] == [("vector", "q-0"), ("hybrid", "q-0"), ("vector", "q-1"), ("hybrid", "q-1")]
    assert seen[80:82] == [("hybrid", "q-0"), ("vector", "q-0")]
    assert h.finalize(pack["out"], *final_receipts(pack))["status"] == "complete"
    h.finalized(pack["out"])
    pack["out"] = pack["directory"] / "second-run"
    pack["authorization"]["outputDirectory"] = str(pack["out"])
    with pytest.raises(FileExistsError):
        execute(pack)
    assert len(seen) == 160
    assert h.read(pack["out"] / "run-status.json")["confirmationExecuted"] is False


@pytest.mark.parametrize("damage", ["model", "prompt", "top", "endpoint", "code", "stale", "mode", "nonmode", "import"])
def test_runtime_and_model_control_drift_precedes_model_calls(pack, monkeypatch, damage):
    runtime = pack["runtimes"]["hybrid"]
    if damage == "model":
        pack["deployment"]["deployment"]["properties"]["model"]["version"] = "changed"
    elif damage == "prompt":
        pack["config"]["agent"]["instructions"] += "Hint"
    elif damage == "top":
        runtime["effectiveSettings"]["searchDefaultTop"] = 9
    elif damage == "endpoint":
        monkeypatch.setenv("AOAI_ENDPOINT", "https://different.invalid")
    elif damage == "code":
        runtime["apiSourceSha256"] = {}
    elif damage == "stale":
        runtime["capturedAt"] = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    elif damage in {"mode", "nonmode"}:
        runtime["settingsBinding"]["SEARCH_MODE" if damage == "mode" else "BLOB_CONTAINER"] = "changed"
        runtime["settingsBindingSha256"] = h.digest(runtime["settingsBinding"])
    else:
        runtime["ownEnvironmentAndImportVerified"] = False
    monkeypatch.setattr(h, "make_index", lambda *args: pytest.fail("must fail before source access"))
    with pytest.raises(ValueError):
        execute(pack)


@pytest.mark.parametrize("damage", ["missing-row", "wrong-corpus", "raw-change", "review", "snapshot", "same-ledger"])
def test_direct_completeness_and_independent_acceptance_gate(pack, monkeypatch, damage):
    services(monkeypatch, [])
    direct_path = pack["directory"] / "direct.json"
    direct = h.read(direct_path)
    if damage == "missing-row":
        direct["rows"].pop()
    elif damage == "wrong-corpus":
        direct["planSha256"] = "changed"
    elif damage == "raw-change":
        (pack["directory"] / "direct-raw.json").write_text("changed")
    elif damage == "review":
        pack["authorization"]["corpusIndependentlyReviewed"] = False
    elif damage == "snapshot":
        pack["authorization"]["sourceSnapshotSha256"] = "changed"
    else:
        pack["authorization"]["executionLedger"] = str(pack["out"] / "ledger.json")
    h.save(direct_path, direct)
    pack["authorization"]["directReceiptSha256"] = h.sha(direct_path)
    monkeypatch.setattr(h, "default_chat_factory", lambda *args: pytest.fail("must not call model"))
    with pytest.raises(ValueError):
        execute(pack)


def test_partial_run_remains_claimed_with_private_receipts(pack, monkeypatch):
    services(monkeypatch, [])

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(h, "replay", interrupt)
    with pytest.raises(KeyboardInterrupt):
        execute(pack)
    assert h.read(pack["out"] / "run-status.json")["status"] == "interrupted"
    assert (pack["directory"] / "claimed.json").exists()
    with pytest.raises((ValueError, FileNotFoundError)):
        h.finalize(pack["out"], *final_receipts(pack))


def test_stale_post_run_and_empty_finalization_inventory_fail(pack, monkeypatch):
    services(monkeypatch, [])
    execute(pack)
    with pytest.raises(ValueError, match="post-run"):
        h.finalize(pack["out"], pack["deployment"], pack["runtimes"], pack["embedding"])
    h.finalize(pack["out"], *final_receipts(pack))
    final_path = pack["out"] / "finalization.json"
    value = h.read(final_path)
    value["artifactsSha256"] = {}
    h.save(final_path, value)
    with pytest.raises(ValueError, match="inventory"):
        h.finalized(pack["out"])


@pytest.mark.parametrize("damage", ["message", "tool", "system", "question", "missing-wire"])
def test_actual_wire_cannot_be_replaced_by_status_boolean(pack, monkeypatch, damage):
    services(monkeypatch, [])
    execute(pack)
    directory = pack["out"] / "wire-r1-q-0-vector"
    path = directory / "wire-002.json"
    value = h.read(path)
    if damage == "missing-wire":
        path.unlink()
    else:
        if damage == "message":
            value["messages"][-1]["content"] = "{}"
        elif damage == "tool":
            value["tools"] = []
        elif damage == "system":
            value["messages"][0]["content"] += "Secret answer hint"
        else:
            value["messages"][1]["content"] = "Different question"
        h.save(path, value)
        # Even a matching local checksum cannot replace source binding/prompt checks.
        receipt = h.read(directory / "wire-receipts.json")
        receipt[-1]["wireSha256"] = h.digest(value["messages"])
        h.save(directory / "wire-receipts.json", receipt)
    with pytest.raises((ValueError, FileNotFoundError)):
        h.finalize(pack["out"], *final_receipts(pack))


def test_blinded_review_and_paired_scores_separate_retrieval_from_full_reads(pack, monkeypatch):
    services(monkeypatch, [])
    execute(pack)
    h.finalize(pack["out"], *final_receipts(pack))
    packets = pack["directory"] / "review"
    assert h.review_packets(pack["out"], pack["directory"] / "gold.json", packets)["packets"] == 160
    rows = []
    for path in sorted(packets.glob("*.json")):
        packet = h.read(path)
        assert not {"arm", "repeat", "usage", "score", "telemetryId"} & set(packet)
        assert all(not {"usage", "score", "telemetryId"} & set(item) for item in packet["evidence"])
        row = packet["reviewTemplate"]
        row.update(
            taskCompletion=True,
            factualSupport=True,
            citationAccuracy=True,
            appropriateAbstention=None if packet["answerable"] else True,
            sourceRelationships=None,
            deliveredRegionIds=["r"] if packet["answerable"] else [],
            supportedFactIds=["fact"] if packet["answerable"] else [],
            fullReadRegionIds=["r"] if packet["answerable"] else [],
        )
        rows.append(row)
    score = h.score(pack["out"], pack["directory"] / "gold.json", rows)
    assert score["uniqueQuestions"] == 40 and score["reviewedTurns"] == 160
    assert score["families"]["all"]["uniqueQuestions"] == 40
    assert score["families"]["all"]["pairedRepeats"] == 80
    assert score["families"]["all"]["comparisons"]["requiredRegionRecall"] == {"tie": 78, "unavailable": 2}
    assert all(r["directExpandedRegions"] == [] for r in score["rows"])
    assert all(r["fullReadRegionsBeyondDirectExpanded"] == ["r"] for r in score["rows"] if r["answerable"])
    assert all(r["fullReadRegionsBeyondAgentSearch"] == ["r"] for r in score["rows"] if r["answerable"])
    assert all(r["fullReadOnlyRegions"] == ["r"] for r in score["rows"] if r["answerable"])
    assert all(r["requiredRegionRecall"] is None for r in score["rows"] if not r["answerable"])
    assert all(r["usage"]["totalTokens"] is None for r in score["rows"])
    assert "Original condition" not in json.dumps(score)
    with pytest.raises(ValueError, match="exactly once"):
        h.score(pack["out"], pack["directory"] / "gold.json", rows[:-1])
    changed = copy.deepcopy(rows)
    next(r for r in changed if r["fullReadRegionIds"])["searchRegionIds"] = ["r"]
    with pytest.raises(ValueError, match="source channel"):
        h.score(pack["out"], pack["directory"] / "gold.json", changed)
    changed_results = pack["out"] / "r1-q-0-vector/results.json"
    result = h.read(changed_results)
    result[0]["answer"] = "Post-hoc replacement answer"
    h.save(changed_results, result)
    with pytest.raises(ValueError, match="artifact changed"):
        h.finalized(pack["out"])


def test_source_labels_must_remain_frozen_and_absence_has_no_fake_coverage(pack):
    directory = pack["directory"]
    plan, manifest, tasks = h.inputs(*(directory / f"{n}.json" for n in ("plan", "manifest", "tasks")))
    h.gold_check(directory / "gold.json", plan, manifest, tasks)
    gold = copy.deepcopy(pack["gold"])
    gold["regions"]["r"]["expectedText"] = "Changed answer"
    h.save(directory / "gold.json", gold)
    with pytest.raises(ValueError, match="gold changed"):
        h.gold_check(directory / "gold.json", plan, manifest, tasks)
    task = tasks[-1]
    review = {
        **dict.fromkeys(h.QUALITY),
        "appropriateAbstention": True,
        "deliveredRegionIds": ["r"],
        "supportedFactIds": [],
        "fullReadRegionIds": [],
        "searchRegionIds": [],
        "selectedReadRegionIds": [],
        "blankRegionIdsVerified": [],
    }
    with pytest.raises(ValueError, match="frozen labels"):
        h.validate_review(review, task, pack["gold"])


@pytest.mark.parametrize("damage", ["common-settings", "api-code", "stored-rows", "embedding-provider"])
def test_direct_to_replay_frozen_dependencies_cannot_drift(pack, monkeypatch, damage):
    services(monkeypatch, [])
    path = pack["directory"] / "direct.json"
    direct = h.read(path)
    if damage == "common-settings":
        direct["settingsBindingSha256"] = "changed"
    elif damage == "api-code":
        direct["codeSha256"] = {}
    elif damage == "embedding-provider":
        pack["embedding"]["deployment"]["sku"]["capacity"] = 300
    else:
        for runtime in pack["runtimes"].values():
            runtime["storedRowsSha256"] = "a" * 64
        pack["authorization"]["runtimeBindingsSha256"] = h.digest(h.runtime_bindings(pack["runtimes"], pack["config"]))
    h.save(path, direct)
    pack["authorization"]["directReceiptSha256"] = h.sha(path)
    monkeypatch.setattr(h, "default_chat_factory", lambda *args: pytest.fail("must not start answering"))
    with pytest.raises(ValueError):
        execute(pack)


def test_post_answering_embedding_upgrade_is_not_accepted(pack, monkeypatch):
    services(monkeypatch, [])
    execute(pack)
    after, runtime, embedding = final_receipts(pack)
    embedding["deployment"]["properties"]["model"]["version"] = "2"
    with pytest.raises(ValueError):
        h.finalize(pack["out"], after, runtime, embedding)


@pytest.mark.parametrize("damage", [None, "job-source", "job-generation", "metadata", "pending-generation"])
def test_source_snapshot_requires_original_metadata_and_active_provenance(pack, damage):
    recipe = profile("microsoft-indexer-layout-v1")
    recipe["embedding"]["modelRevision"] = "1"
    document = {
        "id": "document",
        "fileName": "original.docx",
        "sha256": "f" * 64,
        "status": "ready",
        "activeGeneration": 1,
        "pendingGeneration": None,
        "author": "Original publisher",
        "ingestionJob": {"profile": copy.deepcopy(recipe), "generation": 1, "sourceSha256": "f" * 64},
    }
    manifest = {
        "documents": [
            {
                "id": "source",
                "fileName": "original.docx",
                "sha256": "f" * 64,
                "metadata": {"author": "Original publisher"},
            }
        ]
    }
    source = {
        "libraries": {pack["config"]["libraryId"]: {"library": {"ingestionProfile": recipe}, "documents": [document]}}
    }
    if damage == "job-source":
        document["ingestionJob"]["sourceSha256"] = "changed"
    elif damage == "job-generation":
        document["ingestionJob"]["generation"] = 2
    elif damage == "metadata":
        document["author"] = "Changed"
    elif damage == "pending-generation":
        document["pendingGeneration"] = 2
    if damage is None:
        h.validate_snapshot(source, pack["config"], manifest)
    else:
        with pytest.raises(ValueError):
            h.validate_snapshot(source, pack["config"], manifest)


@pytest.mark.parametrize("damage", [None, "auth", "remote", "loaded-api", "loaded-helper", "launcher", "mode"])
def test_runtime_capture_is_authenticated_local_and_describes_loaded_code(pack, monkeypatch, damage):
    binding = pack["runtimes"]["vector"]["settingsBinding"]
    settings = SimpleNamespace(
        **{k.lower(): v for k, v in binding.items()},
        dev_api_key="test-key",
        auth_mode="dev",
        environment="local",
        ingestion_code_revision="frozen-code",
    )
    settings.search_default_top, settings.search_max_top, settings.aoai_embedding_dimensions = 8, 20, 3072
    app = FastAPI()
    app.state.settings = settings
    app.state.searcher = SimpleNamespace(mode="vector")
    loaded = {
        "apiSourceSha256": h.api_hashes(),
        "measurementCodeSha256": h.hashes(),
        "launcherSha256": h.sha(Path(api.__file__)),
        "gitRevision": "frozen-code",
        "sourceRoot": str(pack["directory"]),
        "pythonPrefix": str(pack["directory"] / ".venv"),
        "ownEnvironmentAndImportVerified": True,
    }
    monkeypatch.setattr(api, "hashes", h.hashes)
    monkeypatch.setattr(api, "code_revision", lambda repo: "frozen-code")
    captured = []

    async def source(*args):
        captured.append(True)
        return {"chunks": [{"id": "stored", "_etag": "exact"}]}

    monkeypatch.setattr(api, "capture_source", source)
    api.add_capture_route(app, pack["config"], "vector", loaded)
    if damage == "loaded-api":
        monkeypatch.setattr(api, "api_hashes", lambda: {})
    elif damage == "loaded-helper":
        monkeypatch.setattr(api, "hashes", lambda: {})
    elif damage == "launcher":
        monkeypatch.setattr(api, "sha", lambda path: "changed")
    elif damage == "mode":
        app.state.searcher.mode = "hybrid"

    async def request():
        transport = httpx.ASGITransport(
            app=app, client=("198.51.100.9" if damage == "remote" else "127.0.0.1", 123), raise_app_exceptions=False
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(
                "/__hybrid_quality/runtime",
                headers={"Authorization": "Bearer " + ("wrong" if damage == "auth" else "test-key")},
            )

    response = asyncio.run(request())
    if damage is None:
        assert response.status_code == 200 and captured == [True]
        assert response.json()["apiSourceSha256"] == loaded["apiSourceSha256"]
        assert response.json()["storedRowsSha256"] == h.digest([{"id": "stored", "_etag": "exact"}])
    else:
        assert response.status_code == (401 if damage == "auth" else 500)
        assert captured == []
