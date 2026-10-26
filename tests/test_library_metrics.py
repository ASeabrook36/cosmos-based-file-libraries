import asyncio
import json

import httpx
import pytest

from fli.embedding import AzureOpenAIEmbedder, measure_embeddings
from fli.metrics import read_events, record, summarize
from tests.fakes import api
from tests.test_worker_handlers import ctx, doc, drain, upload  # noqa: F401


def test_journal_is_scoped_bounded_and_drops_content_fields(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("FLI_METRICS_FILE", str(path))
    record(
        "search",
        "a",
        agentId="agent",
        totalMs=20,
        requestCharge=4,
        resultCount=0,
        disposition="empty",
        embeddingTokens=3,
        query="private query",
        text="private text",
        fileName="private filename",
    )
    record("read", "b", totalMs=10, requestCharge=2)
    assert "private" not in path.read_text()
    events = read_events("a")["events"]
    assert len(events) == 1 and "libraryId" not in events[0]
    result = summarize(events)
    assert result["requestCharge"] == 4 and result["emptySearches"] == 1 and result["queryEmbeddingTokens"] == 3
    with path.open("a") as sink:
        sink.write("incomplete\n1\n")
    assert len(read_events("a")["events"]) == 1
    monkeypatch.setattr("fli.metrics.MAX_EVENTS", 1)
    record("read", "a", totalMs=40, requestCharge=1, contentReturned=False, disposition="budget_exceeded")
    history = read_events("a")
    assert history["bounded"] and len(history["events"]) == 1
    assert summarize(history["events"])["budgetRefusals"] == 1


async def test_library_metrics_include_inventory_and_protect_personal_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("FLI_METRICS_FILE", str(tmp_path / "events.jsonl"))
    client, _ = api()
    lib = client.post("/v1/libraries", json={"kind": "personal", "name": "Private", "boundAgentId": "owner"}).json()[
        "id"
    ]
    record("search", lib, agentId="owner", totalMs=15, requestCharge=3, resultCount=2, disposition="thin")
    assert client.get(f"/v1/libraries/{lib}/metrics", headers={"X-Agent-Id": "other"}).status_code == 403
    response = client.get(f"/v1/libraries/{lib}/metrics", headers={"X-Agent-Id": "owner"})
    assert response.status_code == 200
    data = response.json()
    assert data["inventory"]["documents"] == 0 and data["summary"]["thinSearches"] == 1
    assert "events.jsonl" not in response.text


async def test_real_api_calls_populate_library_history_and_errors_without_content(ctx, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FLI_METRICS_FILE", str(tmp_path / "events.jsonl"))
    client, s, lib, worker = ctx
    document = upload(client, lib)
    await drain(s, worker)
    headers = {"X-Agent-Id": "agent"}
    client.post(f"/v1/libraries/{lib}/search", headers=headers, json={"query": "private question"})
    client.post(f"/v1/libraries/{lib}/documents:discover", headers=headers, json={})
    client.post(
        f"/v1/libraries/{lib}/documents:read",
        headers=headers,
        json={"documents": [{"documentId": document}], "maxTokens": 1},
    )
    client.post(f"/v1/libraries/{lib}/documents:read", headers=headers, json={"documents": [{"documentId": "missing"}]})
    data = client.get(f"/v1/libraries/{lib}/metrics", headers=headers).json()
    assert data["inventory"]["chunks"] > 0 and data["inventory"]["measuredDocuments"] == 1
    assert data["summary"]["searches"] == 1 and data["summary"]["budgetRefusals"] == 1
    assert data["summary"]["errors"] == 1 and not data["summary"]["chargeComplete"]
    assert "private question" not in json.dumps(data)
    m = doc(client, lib, document)["processingMetrics"]
    assert m["embeddingTokens"] is None and m["chunkTokens"] > 0 and m["chunkWriteCharge"] is None
    assert m["totalMs"] >= m["embeddingMs"] >= 0


async def test_concurrent_embedding_measurements_are_attributed_per_call():
    async def token():
        return "token"

    async def handler(request):
        inputs = json.loads(request.content)["input"]
        await asyncio.sleep(0)
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": len(inputs[0])}, "data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        embedder = AzureOpenAIEmbedder("https://example.openai.azure.com", "embedding", token, http, dimensions=3)

        async def measured(text):
            with measure_embeddings() as usage:
                await embedder.embed([text])
            return usage

        a, b = await asyncio.gather(measured("abc"), measured("abcdefg"))
    assert (a.prompt_tokens, b.prompt_tokens) == (3, 7)
    assert a.requests == b.requests == 1 and a.tokens_reported and b.tokens_reported
    assert embedder.usage.prompt_tokens == 10


def test_invalid_metrics_sink_does_not_break_data_operations(monkeypatch):
    monkeypatch.setenv("FLI_METRICS_FILE", "records/metrics.jsonl")
    record("search", "library", query="private")
    with pytest.raises(ValueError, match="outside"):
        read_events("library")
