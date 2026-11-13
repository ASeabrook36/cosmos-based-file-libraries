"""Bounded continuation gates and reuse of already-published owned sources.

All services here are local substitutes. No Azure/model calls are made.
"""

import asyncio
import copy
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fli_demo.client import IndexClient
from scripts import granular_live as live
from scripts import granular_live_resume as resume
from scripts.granular_live_safety import OwnedLedger
from scripts.qualify_granular_navigation import LIMITS, fixtures
from tests.fakes import AUTH, Services
from tests.test_granular_live import local_service, normalize_stored_records


def write(path, value):
    path.write_text(json.dumps(value))


def prior_receipts(root):
    started = datetime(2026, 11, 4, 19, 0, tzinfo=UTC)
    embeddings = [
        {
            "logicalCall": 1,
            "localInputTokens": 98,
            "physicalRequests": 1,
            "retries": 0,
            "throttles": 0,
            "unmeteredAttempts": 0,
            "reportedPromptTokens": 98,
            "status": "response",
        }
    ]
    records = {
        "controls.json": {"sourceSha256": {"src/example.py": "a" * 64}, "limits": LIMITS},
        "execution-result.json": {
            "status": "failed",
            "checks": [],
            "cleanup": {"passed": False},
            "embedding": embeddings,
        },
        "ownership-final.json": {
            "libraries": {str(i): {} for i in range(3)},
            "documents": {str(i): {} for i in range(3)},
            "generations": [[str(i), 1] for i in range(3)],
            "operations": [
                {
                    "id": "op",
                    "key": "document:0",
                    "kind": "document-replace",
                    "status": "unknown",
                    "bodySha256": "b" * 64,
                }
            ],
        },
        "reconciliation-proof.json": {"semanticEquality": True, "matchedExactOriginalProposedSha256": "b" * 64},
        "embedding-attempts.json": embeddings,
        "execution-started.json": {"startedAt": started.isoformat()},
    }
    for name, value in records.items():
        write(root / name, value)
    events = root / "ownership" / "events"
    events.mkdir(parents=True)
    write(events / "000106.json", {"at": (started + timedelta(seconds=120.1)).isoformat()})
    return records, started


def save_receipts(root, records):
    for name, value in records.items():
        write(root / name, value)


def test_continuation_keeps_all_prior_counter_receipts_and_time_reservation(tmp_path):
    records, _ = prior_receipts(tmp_path)
    controls, ownership, operation, embedding, consumed = resume.continuation_controls(tmp_path)
    assert controls == records["controls.json"]
    assert ownership == records["ownership-final.json"]
    assert operation == ownership["operations"][0]
    assert embedding == records["embedding-attempts.json"]
    assert consumed == 181 and LIMITS["executionSeconds"] - consumed == 1619


@pytest.mark.parametrize(
    "damage",
    [
        "completed",
        "checks",
        "models",
        "cleanup-passed",
        "unproven",
        "libraries",
        "documents",
        "generations",
        "multiple-unknown",
        "wrong-operation",
        "proposal-hash",
        "unfinished-embedding",
        "counter-receipt-drift",
        "too-many-calls",
        "sequence",
        "tokens",
        "physical",
        "negative",
        "bool-counter",
        "retries",
        "throttles",
        "provider-negative",
        "unknown-attempts",
        "empty-calls",
        "backward-time",
        "exhausted-time",
        "naive-time",
    ],
)
def test_continuation_rejects_changed_failure_scope_or_consumed_bounds(tmp_path, damage):
    records, started = prior_receipts(tmp_path)
    result, owned, proof = (
        records[name] for name in ("execution-result.json", "ownership-final.json", "reconciliation-proof.json")
    )
    rows = records["embedding-attempts.json"]
    if damage == "completed":
        result["status"] = "complete"
    elif damage == "checks":
        result["checks"] = ["source-publication"]
    elif damage == "models":
        (tmp_path / "models").mkdir()
    elif damage == "cleanup-passed":
        result["cleanup"]["passed"] = True
    elif damage == "unproven":
        proof["semanticEquality"] = False
    elif damage in {"libraries", "documents"}:
        owned[damage]["extra"] = {}
    elif damage == "generations":
        owned["generations"].append(["0", 2])
    elif damage == "multiple-unknown":
        owned["operations"].append(copy.deepcopy(owned["operations"][0]))
    elif damage == "wrong-operation":
        owned["operations"][0]["kind"] = "blob-create"
    elif damage == "proposal-hash":
        proof["matchedExactOriginalProposedSha256"] = "c" * 64
    elif damage == "unfinished-embedding":
        rows[0]["status"] = "started"
    elif damage == "counter-receipt-drift":
        result["embedding"] = []
    elif damage == "too-many-calls":
        rows.extend({**rows[0], "logicalCall": i} for i in range(2, LIMITS["logicalEmbeddingRequests"] + 2))
    elif damage == "empty-calls":
        rows.clear()
    elif damage == "sequence":
        rows[0]["logicalCall"] = 2
    elif damage == "tokens":
        rows[0]["localInputTokens"] = LIMITS["sourceEmbeddingTokens"] + 1
    elif damage == "physical":
        rows[0]["physicalRequests"] = LIMITS["embeddingAttemptsEach"] + 1
    elif damage == "negative":
        rows[0]["localInputTokens"] = -1
    elif damage == "bool-counter":
        rows[0]["physicalRequests"] = True
    elif damage == "retries":
        rows[0]["retries"] = 1
    elif damage == "throttles":
        rows[0]["throttles"] = 2
    elif damage == "provider-negative":
        rows[0]["reportedPromptTokens"] = -1
    elif damage == "unknown-attempts":
        rows[0]["unmeteredAttempts"] = 1
    elif damage == "backward-time":
        write(tmp_path / "ownership/events/000106.json", {"at": (started - timedelta(seconds=30)).isoformat()})
    elif damage == "exhausted-time":
        write(tmp_path / "ownership/events/000106.json", {"at": (started + timedelta(seconds=1800)).isoformat()})
    elif damage == "naive-time":
        records["execution-started.json"]["startedAt"] = started.replace(tzinfo=None).isoformat()
    save_receipts(tmp_path, records)
    with pytest.raises(ValueError):
        resume.continuation_controls(tmp_path)


def test_continuation_accepts_exact_counter_ceilings_without_reset(tmp_path):
    records, _ = prior_receipts(tmp_path)
    rows = [
        {
            "logicalCall": i,
            "localInputTokens": 2500,
            "physicalRequests": 3,
            "retries": 2,
            "throttles": 2,
            "unmeteredAttempts": 2,
            "reportedPromptTokens": 2500,
            "status": "response",
        }
        for i in range(1, 21)
    ]
    records["embedding-attempts.json"] = rows
    records["execution-result.json"]["embedding"] = rows
    save_receipts(tmp_path, records)
    inherited = resume.continuation_controls(tmp_path)[3]
    assert len(inherited) == 20
    assert sum(r["localInputTokens"] for r in inherited) == 50000
    assert sum(r["physicalRequests"] for r in inherited) == 60
    assert sum(r["unmeteredAttempts"] for r in inherited) == 40


async def test_runtime_source_change_refuses_before_continuation_creation_or_services(tmp_path, monkeypatch):
    prior_receipts(tmp_path)
    monkeypatch.setattr(live, "code_hashes", lambda: {"src/example.py": "changed"})
    with pytest.raises(ValueError, match="cannot change source-navigation runtime"):
        await resume.resume(tmp_path, tmp_path.parent / (tmp_path.name + "-archive"))
    assert not (tmp_path / "continuation-2").exists()


async def test_existing_source_continuation_reuses_exact_resources_and_published_generation(tmp_path, monkeypatch):
    original_sleep = asyncio.sleep

    async def skip_warmup(seconds, *args, **kwargs):
        return await original_sleep(0 if seconds == 30 else seconds, *args, **kwargs)

    monkeypatch.setattr(live.asyncio, "sleep", skip_warmup)
    inputs = tmp_path / "fixtures"
    inputs.mkdir()
    for name, data in fixtures().items():
        (inputs / name).write_bytes(data)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    stores = Services()
    normalize_stored_records(stores)
    with OwnedLedger.create(
        tmp_path / "ledger", binding={"local": True}, protected_library_ids=["protected"], live=False
    ) as ledger:
        service, independent = local_service(stores, ledger), local_service(stores, ledger)
        async with live.loopback(service.app) as url:
            async with httpx.AsyncClient(base_url=url, headers={**AUTH, "X-Agent-Id": "owner"}, timeout=30) as client:
                with httpx.Client(base_url=url, timeout=30) as sync_client:
                    index = IndexClient(url, "k", http=sync_client)
                    initial = live.Qualification(
                        first, inputs, ledger, service, independent, client, index, time.monotonic() + 90
                    )
                    shared, personal, foreign = (
                        await initial.create("shared"),
                        await initial.create("personal"),
                        await initial.create("shared"),
                    )
                    docx, markdown = (
                        await initial.upload(shared, "source.docx"),
                        await initial.upload(shared, "source.md"),
                    )
                    lifecycle = await initial.upload(personal, "source.docx")
                    await service.worker.ingest(shared, docx, 1)
                    original = await service.documents.get(shared, docx)
                    assert original["status"] == "ready" and stores.embedder.calls == 1
                    first_chunks = {
                        identity for lib, doc, identity in stores.chunks.items if (lib, doc) == (shared, docx)
                    }
                    before = ledger.snapshot()
                    service.queue.cancel_pending("local substitute for independently reviewed first publication")
                    for library, document in ((shared, docx), (shared, markdown), (personal, lifecycle)):
                        await service.queue.send(
                            {"type": "ingest", "libraryId": library, "documentId": document, "generation": 1},
                            session_id=library,
                        )
                    followup = live.Qualification(
                        second, inputs, ledger, service, independent, client, index, time.monotonic() + 90
                    )
                    existing = {
                        "shared": shared,
                        "personal": personal,
                        "foreign": foreign,
                        "docx": docx,
                        "markdown": markdown,
                        "lifecycle": lifecycle,
                    }
                    bindings = await followup.mechanics(existing)
                    current = await service.documents.get(shared, docx)
                    assert current["fullText"] == original["fullText"]
                    assert {
                        identity for lib, doc, identity in stores.chunks.items if (lib, doc) == (shared, docx)
                    } == first_chunks
                    assert bindings["docx"]["documentId"] == docx and bindings["markdown"]["documentId"] == markdown
                    after = ledger.snapshot()
                    assert set(after["libraries"]) == set(before["libraries"]) and len(after["libraries"]) == 3
                    assert set(after["documents"]) == set(before["documents"]) and len(after["documents"]) == 3
                    assert len(before["generations"]) == 3 and len(after["generations"]) == 4
                    assert len(after["chunks"]) <= LIMITS["publishedChunks"]
                    assert ledger.summary()["unresolvedOperations"] == ledger.summary()["unfinishedMessages"] == 0
