"""The canary's orchestration end to end, against fakes.

This proves the script runs and writes a well-formed record without leaking text. It says
nothing about Cosmos: the fakes encode our assumptions about the SDK, and only a run against
a deployed account tests those. The fake embedder hashes text into vectors, which is fine for
plumbing and meaningless for search quality (CLAUDE.md, Testing).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.canary as canary

REPO = Path(__file__).resolve().parent.parent


def fake_vector(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    raw = [((seed[i % 32] * (i + 1)) % 251) / 251 - 0.5 for i in range(3072)]
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm for v in raw]


class FakeEmbedder:
    def __init__(self, **kwargs):
        self.usage = SimpleNamespace(prompt_tokens=0, throttled=0)

    async def embed(self, texts):
        self.usage.prompt_tokens += sum(len(t.split()) for t in texts)
        return [fake_vector(t) for t in texts]


class FakeCredential:
    async def get_token(self, scope):
        return SimpleNamespace(token="t", expires_on=10**10)

    async def close(self):
        pass


class FakeContainer:
    def __init__(self, name: str, store: dict):
        self.name = name
        self.items = store.setdefault(name, {})
        self.queries: list[str] = []

    async def read(self):
        return json.loads((REPO / "cosmos" / f"{self.name}.container.json").read_text()) | {"_rid": "x"}

    async def upsert_item(self, body, response_hook=None):
        self.items[body["id"]] = body
        if response_hook:
            response_hook({"x-ms-request-charge": "91"}, body)
        return body

    async def read_item(self, item, partition_key):
        return self.items[item]

    async def delete_item(self, item, partition_key):
        del self.items[item]

    def query_items(self, sql, parameters=None, partition_key=None, populate_query_metrics=None, response_hook=None):
        self.queries.append(sql)
        params = {p["name"]: p["value"] for p in parameters or []}
        rows = [i for i in self.items.values() if i.get("libraryId") == params["@libraryId"]]

        async def gen():
            if "VectorDistance" in sql:
                assert re.match(r"SELECT TOP \d+ ", sql) and "c.libraryId = @libraryId" in sql
                assert " c.embedding," not in sql
                query = params["@queryVector"]
                top = int(sql.split()[2])
                scored = sorted(rows, key=lambda r: -sum(a * b for a, b in zip(r["embedding"], query, strict=True)))
                exact = ", true," in sql
                if response_hook:
                    retrieved = len(rows) if exact or "'float32'" in sql else 7
                    metrics = f"retrievedDocumentCount={retrieved};outputDocumentCount={top}"
                    charge = "4000" if exact else "12.5"
                    response_hook({"x-ms-request-charge": charge, "x-ms-documentdb-query-metrics": metrics}, None)
                for r in scored[:top]:
                    yield {"id": r["id"], "score": 0.5}
            elif "c.id = @id" in sql:
                for r in rows:
                    if r["id"] == params["@id"]:
                        yield {"id": r["id"]}
            else:
                for r in rows:
                    yield {"id": r["id"], "documentId": r.get("documentId")}

        return gen()


class FakeDatabase:
    def __init__(self, store):
        self.containers = {n: FakeContainer(n, store) for n in canary.CONTAINERS}

    def get_container_client(self, name):
        return self.containers[name]


class FakeCosmosClient:
    last: FakeCosmosClient | None = None

    def __init__(self, endpoint, credential):
        self.store: dict = {}
        self.database = FakeDatabase(self.store)
        FakeCosmosClient.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_database_client(self, name):
        return self.database


@pytest.fixture
def fakes(monkeypatch):
    import azure.cosmos.aio
    import azure.identity.aio

    import inherited.embedding

    monkeypatch.setattr(azure.cosmos.aio, "CosmosClient", FakeCosmosClient)
    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(inherited.embedding, "AzureOpenAIEmbedder", FakeEmbedder)
    monkeypatch.setattr(canary, "MIN_CHUNKS", 50)  # the repo's own docs: real text, about 80 chunks
    for name, value in {
        "COSMOS_ENDPOINT": "https://fake.documents.azure.com:443/",
        "AOAI_ENDPOINT": "https://fake.openai.azure.com/",
        "AOAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("keep", [False, True])
def test_canary_runs_end_to_end_and_records_metrics_only(fakes, tmp_path, keep):
    argv = ["--corpus", str(REPO / "docs"), "--settle-seconds", "0", "--records-dir", str(tmp_path)]
    assert canary.main([*argv, "--keep"] if keep else argv) == 0

    [path] = tmp_path.glob("canary-*.json")
    record = json.loads(path.read_text())
    assert record["passed"] is True, [c for c in record["checks"] if not c["passed"]]
    assert {c["name"] for c in record["checks"]} >= {
        "container_definitions_match",
        "crud_chunks",
        "index_engaged_charge",
        "index_engaged_retrieved",
        "vector_recall_at_10",
        "hybrid_recall_at_10",
    }
    assert record["float32Trap"]["scanned"] is True
    assert len(record["searches"]["vector"]) == len(record["searches"]["hybrid"]) == canary.QUERIES_PER_KIND
    assert record["storedVector"]["dimensions"] == 3072

    # No chunk text, query text or file names reach the committed record (CLAUDE.md rule 12).
    raw = path.read_text()
    chunks = FakeCosmosClient.last.store["chunks"]
    sample = next(iter(chunks.values()))["text"] if chunks else None
    for doc in sorted((REPO / "docs").glob("*.md")):
        assert doc.name not in raw
    for line in (REPO / "docs" / "spec.md").read_text().splitlines():
        if len(line) > 60:
            assert line not in raw
    if sample:
        assert sample not in raw

    store = FakeCosmosClient.last.store
    assert bool(store["chunks"]) is keep and bool(store["libraries"]) is keep
    assert record["cleanedUp"] is not keep


def test_hybrid_queries_use_rrf_and_vector_queries_declare_float16(fakes, tmp_path):
    canary.main(["--corpus", str(REPO / "docs"), "--settle-seconds", "0", "--records-dir", str(tmp_path)])
    sent = FakeCosmosClient.last.database.containers["chunks"].queries
    vector = [q for q in sent if "VectorDistance" in q]
    assert sum("ORDER BY RANK RRF(" in q for q in vector) == 2 * canary.QUERIES_PER_KIND  # hybrid + exact hybrid
    assert sum("'float32'" in q for q in vector) == 1  # only the documented trap
    assert all("'float16'" in q for q in vector if "'float32'" not in q)


def test_count_only_reads_the_corpus_without_azure(monkeypatch, capsys):
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.setattr(canary, "MIN_CHUNKS", 50)
    assert canary.main(["--corpus", str(REPO / "docs"), "--count-only"]) == 0
    monkeypatch.setattr(canary, "MIN_CHUNKS", 10_000)
    assert canary.main(["--corpus", str(REPO / "docs"), "--count-only"]) == 1
    assert "need at least 10000" in capsys.readouterr().err
