"""Driver checks use stand-ins for plumbing, never for retrieval-quality evidence."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from fli.chunking import TiktokenTokenizer
from fli.structure_lab import CONTAINERS, VERSION, digest, markdown_document, split_source
from scripts import structure_lab as lab


def test_private_artifacts_digest_and_library_boundary(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        lab.private_directory(lab.REPO / "private-runtime")
    artifact = {"bundle": {"version": VERSION, "libraryId": "structure-lab-" + "a" * 32}, "rows": []}
    artifact["digest"] = digest(artifact)
    lab.save(tmp_path / "frozen.json", artifact)
    assert lab.artifact_at(tmp_path, "frozen.json") == artifact
    with pytest.raises(ValueError):
        lab.artifact_at(tmp_path, "../frozen.json")
    artifact["bundle"]["libraryId"] = "application-library"
    lab.save(tmp_path / "frozen.json", artifact)
    with pytest.raises(ValueError, match="changed"):
        lab.artifact_at(tmp_path, "frozen.json")
    artifact.pop("digest")
    artifact["digest"] = digest(artifact)
    lab.save(tmp_path / "frozen.json", artifact)
    with pytest.raises(ValueError, match="isolated"):
        lab.artifact_at(tmp_path, "frozen.json")


def test_unicode_source_is_not_corrupted_at_token_boundaries():
    source = "树木 🌳 Ελληνικά 日本語\n" * 100
    tokenizer = TiktokenTokenizer()
    pieces = split_source(source, tokenizer, 29)
    assert "".join(pieces) == source
    assert all(len(tokenizer.encode(p)) <= 29 for p in pieces)


def test_vector_cache_reuses_identical_text_and_invalidates_model_revision(tmp_path):
    class Embedder:
        calls = 0

        async def embed(self, texts):
            self.calls += 1
            return [[0.1] * 3072 for _ in texts]

    async def run():
        embedder = Embedder()
        cache = lab.Cache(tmp_path / "cache.json")
        first = await lab.cached_vectors(["one", "one", "two"], embedder, cache, "model-a")
        assert embedder.calls == 1
        assert len(cache) == 2 and len(first) == 3
        second = await lab.cached_vectors(["one", "two"], embedder, lab.Cache(cache.path), "model-a")
        assert embedder.calls == 1 and second == [first[0], first[2]]
        await lab.cached_vectors(["one"], embedder, cache, "model-b")
        assert embedder.calls == 2

    asyncio.run(run())


def test_canary_receipts_bind_to_current_target_and_definition(tmp_path):
    class Container:
        def __init__(self, name):
            self.name = name

        async def read(self):
            return json.loads((lab.REPO / "cosmos" / f"{self.name}.container.json").read_text()) | {"_rid": "current"}

    database = SimpleNamespace(get_container_client=Container)
    for name in CONTAINERS:
        receipt = {
            "passed": True,
            "indexContainer": name,
            "checks": [{"passed": True}],
            "targetFingerprint": lab.target_fingerprint("endpoint", "db", name, "current"),
        }
        lab.save(tmp_path / f"canary-{name}.json", receipt)
    assert set(asyncio.run(lab.verified_containers(database, "endpoint", "db", tmp_path))) == set(CONTAINERS)
    with pytest.raises(ValueError, match="receipt"):
        asyncio.run(lab.verified_containers(database, "different-environment", "db", tmp_path))
    receipt["passed"] = False
    lab.save(tmp_path / f"canary-{name}.json", receipt)
    with pytest.raises(ValueError, match="receipt"):
        asyncio.run(lab.verified_containers(database, "endpoint", "db", tmp_path))


def test_comparison_uses_identical_queries_alternates_order_and_expands_source(monkeypatch):
    doc = markdown_document("paper", "Title", "# Methods\nSource measurement.")
    row = {"id": "r", "documentId": "paper", "nodeId": "n1", "level": "section"}
    artifact = {
        "digest": "snapshot",
        "bundle": {"libraryId": "one-library", "documents": [doc]},
        "rows": [row],
        "queries": [
            {"text": "method", "level": "section", "vector": [0.2], "top": 1, "relevantDocumentIds": ["paper"]}
        ],
    }
    sent = []

    async def query(container, sql, parameters, library_id, measurement, throttle, *, scored):
        sent.append((container, sql, parameters))
        assert library_id == "one-library"
        measurement.result_ids = ["r"]
        measurement.request_charge = 5
        return measurement

    monkeypatch.setattr(lab, "run_query", query)
    report = asyncio.run(lab.evaluate(artifact, {n: n for n in CONTAINERS}, 2, hybrid=True))
    assert len(report["measurements"]) == 8
    assert [sent[i][0] for i in (0, 3, 6, 9)] == ["lab-shared", "lab-summaries", "lab-summaries", "lab-shared"]
    assert sent[0][1:] == sent[3][1:]
    for m in report["measurements"]:
        assert m["expandedSourceTokens"] > 0
        assert m["labelledDocumentRecall"] == 1
        assert m["annRecall"] == (1 if m["kind"] == "vector" else None)
    assert "Source measurement" not in json.dumps(report)
    assert "unassessed" in report["performanceConclusion"]
