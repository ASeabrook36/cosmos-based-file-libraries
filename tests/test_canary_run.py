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


def throttled():
    from azure.cosmos.exceptions import CosmosHttpResponseError

    exc = CosmosHttpResponseError(status_code=429, message="Request rate is large")
    exc.headers = {"x-ms-retry-after-ms": "1"}
    return exc


def raw_response(headers: dict) -> SimpleNamespace:
    return SimpleNamespace(http_response=SimpleNamespace(headers=headers))


class FakeContainer:
    """Throttles every 7th write and every 5th search once, as serverless does under load (E10).

    The vector index engages only after ``scans_before_index`` indexed searches have scanned,
    like an asynchronous DiskANN build. A hybrid search answers in two HTTP responses, one per
    component, and only the raw hook sees both.
    """

    scans_before_index = 2

    def __init__(self, name: str, store: dict):
        self.name = name
        self.items = store.setdefault(name, {})
        self.queries: list[str] = []
        self.calls = 0
        self.indexed_searches = 0
        self.prefix_routed = 0

    async def read(self):
        return json.loads((REPO / "cosmos" / f"{self.name}.container.json").read_text()) | {"_rid": "x"}

    async def upsert_item(self, body, response_hook=None):
        self.calls += 1
        if self.calls % 7 == 0:
            raise throttled()
        self.items[body["id"]] = body
        if response_hook:
            response_hook({"x-ms-request-charge": "91"}, body)
        return body

    async def read_item(self, item, partition_key):
        return self.items[item]

    async def delete_item(self, item, partition_key):
        del self.items[item]

    raw_hook_works = True

    def query_items(self, sql, parameters=None, partition_key=None, raw_response_hook=None, response_hook=None, **kw):
        if not self.raw_hook_works:
            raw_response_hook = None
        params = {p["name"]: p["value"] for p in parameters or []}
        rows = [i for i in self.items.values() if i.get("libraryId") == params["@libraryId"]]

        self.calls += 1
        throttle_this = "VectorDistance" in sql and self.calls % 5 == 0
        exact = ", true," in sql
        hybrid = "RRF(" in sql
        indexed = "VectorDistance" in sql and not exact and not hybrid and "'float32'" not in sql
        if not throttle_this:
            self.queries.append(sql)  # queries that ran, not retried attempts
            self.indexed_searches += indexed
            self.prefix_routed += partition_key is not None and "VectorDistance" in sql
        scanning = not indexed or self.indexed_searches <= self.scans_before_index

        async def gen():
            if throttle_this:
                raise throttled()
            if "VectorDistance" in sql:
                assert re.match(r"SELECT TOP \d+ ", sql) and "c.libraryId = @libraryId" in sql
                assert " c.embedding," not in sql
                query = params["@queryVector"]
                top = int(sql.split()[2])
                scored = sorted(rows, key=lambda r: -sum(a * b for a, b in zip(r["embedding"], query, strict=True)))
                retrieved = len(rows) if scanning and not hybrid else 7
                metrics = f"retrievedDocumentCount={retrieved};outputDocumentCount={top}"
                charge = "4000" if exact else ("2" if hybrid else ("114" if scanning else "12.5"))
                if raw_response_hook:
                    for _ in range(2 if hybrid else 1):
                        raw_response_hook(
                            raw_response({"x-ms-request-charge": charge, "x-ms-documentdb-query-metrics": metrics})
                        )
                if response_hook:  # like the SDK: hybrid components never reach it
                    response_hook({"x-ms-request-charge": charge, "x-ms-documentdb-query-metrics": metrics}, None)
                # Like Cosmos: a RANK-ordered query routed by partition key comes back unranked (E27).
                ordered = rows if hybrid and partition_key is not None else scored
                for r in ordered[:top]:
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
        self.store = store
        self.containers = {n: FakeContainer(n, store) for n in canary.CONTAINERS}

    def get_container_client(self, name):
        if name not in self.containers:
            self.containers[name] = FakeContainer(name, self.store)
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

    import fli.embedding

    monkeypatch.setattr(azure.cosmos.aio, "CosmosClient", FakeCosmosClient)
    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(fli.embedding, "AzureOpenAIEmbedder", FakeEmbedder)
    monkeypatch.setattr(canary, "MIN_CHUNKS", 50)  # the repo's own docs: real text, about 80 chunks
    monkeypatch.setattr(canary, "INDEX_POLL_SECONDS", 0)
    for name, value in {
        "COSMOS_ENDPOINT": "https://fake.documents.azure.com:443/",
        "AOAI_ENDPOINT": "https://fake.openai.azure.com/",
        "AOAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("keep", [False, True])
def test_canary_runs_end_to_end_and_records_metrics_only(fakes, tmp_path, keep):
    argv = ["--corpus", str(REPO / "docs"), "--index-wait-minutes", "5", "--records-dir", str(tmp_path)]
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
    }
    assert "hybrid_recall_at_10" not in {c["name"] for c in record["checks"]}
    assert record["searches"]["hybridVsBruteForceVariant"]["queries"] == canary.QUERIES_PER_KIND
    assert "endpoint" not in record["account"] and "fake.documents.azure.com" not in path.read_text()
    assert record["float32Trap"]["scanned"] is True
    assert len(record["searches"]["vector"]) == len(record["searches"]["hybrid"]) == canary.QUERIES_PER_KIND
    assert record["storedVector"]["dimensions"] == 3072
    assert record["load"]["throttledRetries"] > 0 and record["searches"]["throttledRetries"] > 0
    wait = record["indexWait"]
    assert wait["engaged"] is True and len(wait["polls"]) == 2  # poll 1 scans twice, poll 2 engages
    assert wait["polls"][0]["prefix"]["retrieved"] == wait["polls"][0]["noPrefix"]["retrieved"] > 7
    assert {m["http_responses"] for m in record["searches"]["hybrid"]} == {2}
    assert {m["request_charge"] for m in record["searches"]["hybrid"]} == {4.0}

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
    canary.main(["--corpus", str(REPO / "docs"), "--index-wait-minutes", "5", "--records-dir", str(tmp_path)])
    sent = FakeCosmosClient.last.database.containers["chunks"].queries
    vector = [q for q in sent if "VectorDistance" in q]
    assert sum("ORDER BY RANK RRF(" in q for q in vector) == 2 * canary.QUERIES_PER_KIND  # hybrid + exact hybrid
    assert sum("'float32'" in q for q in vector) == 1  # only the documented trap
    assert all("'float16'" in q for q in vector if "'float32'" not in q)


def test_an_index_that_never_engages_fails_and_records_the_scan(fakes, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeContainer, "scans_before_index", 10**9)
    argv = ["--corpus", str(REPO / "docs"), "--index-wait-minutes", "0", "--records-dir", str(tmp_path)]
    assert canary.main(argv) == 1
    [path] = tmp_path.glob("canary-*.json")
    record = json.loads(path.read_text())
    checks = {c["name"]: c for c in record["checks"]}
    assert not checks["index_engaged_charge"]["passed"] and not checks["index_engaged_retrieved"]["passed"]
    assert record["indexWait"]["engaged"] is False and len(record["indexWait"]["polls"]) == 1
    assert record["cleanedUp"] is True


def test_count_only_reads_the_corpus_without_azure(monkeypatch, capsys):
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.setattr(canary, "MIN_CHUNKS", 50)
    assert canary.main(["--corpus", str(REPO / "docs"), "--count-only"]) == 0
    monkeypatch.setattr(canary, "MIN_CHUNKS", 10_000)
    assert canary.main(["--corpus", str(REPO / "docs"), "--count-only"]) == 1
    assert "need at least 10000" in capsys.readouterr().err


def test_falls_back_to_response_hook_and_says_so(fakes, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeContainer, "raw_hook_works", False)
    argv = ["--corpus", str(REPO / "docs"), "--index-wait-minutes", "5", "--records-dir", str(tmp_path)]
    assert canary.main(argv) == 0
    [path] = tmp_path.glob("canary-*.json")
    hybrid = json.loads(path.read_text())["searches"]["hybrid"]
    assert {m["measured_by"] for m in hybrid} == {"response_hook"} and {m["http_responses"] for m in hybrid} == {1}


def test_the_canary_fails_if_hybrid_searches_are_routed_by_partition_key(fakes, tmp_path, monkeypatch):
    monkeypatch.setattr(canary, "partition_key_for", lambda sql, library_id: [library_id])
    argv = ["--corpus", str(REPO / "docs"), "--index-wait-minutes", "5", "--records-dir", str(tmp_path)]
    assert canary.main(argv) != 0
    [path] = tmp_path.glob("canary-*.json")
    checks = {c["name"]: c for c in json.loads(path.read_text())["checks"]}
    assert checks["hybrid_ranks"]["passed"] is False


@pytest.mark.parametrize("index", ["lab-shared", "lab-passages", "lab-summaries"])
def test_experimental_index_target_receipt_and_cleanup(fakes, tmp_path, index):
    assert (
        canary.main(
            [
                "--corpus",
                str(REPO / "docs"),
                "--index-container",
                index,
                "--index-wait-minutes",
                "5",
                "--records-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    [path] = tmp_path.glob("canary-*.json")
    record = json.loads(path.read_text())
    assert record["passed"] and record["indexContainer"] == index
    assert record["targetFingerprint"] == canary.target_fingerprint(
        "https://fake.documents.azure.com:443/", "fli", index, "x"
    )
    store = FakeCosmosClient.last.store
    assert not store["chunks"]
    assert not store[index] and not store["documents"] and not store["libraries"]
    assert record["targetFingerprint"] != canary.target_fingerprint(
        "https://fake.documents.azure.com:443/", "fli", index, "recreated"
    )
