"""Direct source-quality measurements fail closed; local fakes make no ranking claim."""

import copy
import json
from types import SimpleNamespace

import pytest

from fli.ingestion import profile
from fli.presentation import compact_response
from fli.query import SearchFilters, lexical_terms, render_search, to_float16_query
from fli.structure_lab import digest
from scripts import hybrid_quality_direct as h


def save(path, value):
    path.write_text(json.dumps(value))


def query(arm):
    return render_search("owned", [0.25] * 3072, ["frozen"] if arm == "hybrid" else [], SearchFilters(), 16)


def test_query_audit_records_real_terms_and_rank_routing():
    assert lexical_terms("ISO-9001 EIR EIR CAFÉ " + " ".join(f"x{i}" for i in range(20))) == [
        "iso",
        "9001",
        "eir",
        "caf",
        *[f"x{i}" for i in range(12)],
    ]
    assert h.audit()["maximumLexicalTerms"] == 16
    assert h.audit()["fetchAtTop8"] == 16
    assert "RRF" in query("hybrid").sql
    assert "ORDER BY RANK" not in query("vector").sql


async def test_query_cache_has_no_fallback_or_mutation():
    task = {"case": "case", "question": "frozen"}
    vector = [2**-20, 0.25] + [0.0] * 3070
    cache = {
        "queries": {
            "case": {
                "questionSha256": h.question_sha("frozen"),
                "vector": vector,
                "float16Sha256": digest(to_float16_query(vector)),
            }
        }
    }
    embedder = h.FrozenEmbedder([task], cache)
    result = await embedder.embed(["frozen"])
    result[0][1] = 99
    assert (await embedder.embed(["frozen"]))[0][1] == 0.25
    assert to_float16_query((await embedder.embed(["frozen"]))[0])[0] == 0
    with pytest.raises(ValueError, match="unfrozen"):
        await embedder.embed(["changed"])
    cache["queries"]["case"]["vector"][1] = 0.5
    with pytest.raises(ValueError, match="fingerprint"):
        h.FrozenEmbedder([task], cache)


def test_sdk_wrapper_enforces_scope_route_and_preserves_partial_diagnostics():
    class Container:
        def query_items(self, sql, **kwargs):
            kwargs["raw_response_hook"](
                SimpleNamespace(
                    http_response=SimpleNamespace(
                        headers={
                            "x-ms-request-charge": "3.5",
                            "x-ms-documentdb-partitionkeyrangeid": "private-range",
                            "x-ms-retry-after-ms": "10",
                        },
                        status_code=429,
                    )
                )
            )
            raise RuntimeError("provider failed after response")

    container = h.RecordingContainer(Container(), "owned")
    ranked = query("hybrid")
    with pytest.raises(ValueError, match="route"):
        container.query_items(ranked.sql, parameters=ranked.parameters, partition_key=["owned"])
    assert not container.observations
    with pytest.raises(RuntimeError, match="provider failed"):
        container.query_items(ranked.sql, parameters=ranked.parameters)
    summary = h.diagnostics_summary(container.observations)
    assert summary["knownChunkResponseRequestCharge"] == 3.5
    assert summary["visibleThrottleResponses"] == 1
    assert summary["visiblePartitionContactCount"] == 1
    assert summary["totalRetries"] is None
    assert "private-range" not in json.dumps(summary)
    other = copy.deepcopy(ranked.parameters)
    next(p for p in other if p["name"] == "@libraryId")["value"] = "other"
    with pytest.raises(ValueError, match="escaped"):
        container.query_items(ranked.sql, parameters=other)


@pytest.fixture
def receipt(tmp_path):
    source_hash = "a" * 64
    manifest = {"documents": [{"id": "source", "fileName": "example.docx", "sha256": source_hash}]}
    region = {
        "id": "region",
        "kind": "paragraph",
        "index": 1,
        "sourceSha256": source_hash,
        "normalizedTextSha256": h.question_sha("Required evidence"),
    }
    tasks = [
        {
            "case": f"case-{i}",
            "question": f"Frozen question {i}",
            "split": "confirmation",
            "answerable": bool(i),
            "families": ["concept"],
            "sourceFacts": [{"sourceId": "source", "source": "example.docx", "regions": [region]}] if i else [],
            "requiredFactIds": ["fact"] if i else [],
        }
        for i in range(40)
    ]
    gold = {
        "regions": {
            "region": {
                "sourceId": "source",
                "fileName": "example.docx",
                "locator": {"kind": "paragraph", "index": 1},
                "expectedText": "Required evidence",
            }
        },
        "facts": {"fact": {"sourceRegionIds": ["region"], "expectedFact": "Required evidence"}},
    }
    for name, value in (("manifest", manifest), ("tasks", tasks), ("gold", gold)):
        save(tmp_path / f"{name}.json", value)
    plan = {
        "arms": list(h.ARMS),
        "direct": h.DIRECT,
        "queryAudit": h.audit(),
        **{name + "Sha256": h.sha(tmp_path / f"{name}.json") for name in ("manifest", "tasks", "gold")},
    }
    recipe = profile("microsoft-indexer-layout-v1")
    recipe["embedding"]["modelRevision"] = "1"
    snapshot = {
        "library": {"ingestionProfile": recipe},
        "tools": [],
        "documents": [
            {
                "id": "document",
                "status": "ready",
                "activeGeneration": 1,
                "sha256": source_hash,
                "fileName": "example.docx",
                "ingestionJob": {"profile": recipe, "generation": 1, "sourceSha256": source_hash},
            }
        ],
        "chunks": [
            {
                "id": "chunk",
                "documentId": "document",
                "generation": 1,
                "contentHash": "hash",
                "_etag": "immutable",
                "embeddingModel": "text-embedding-3-large",
            }
        ],
    }
    cache = {
        "queries": {
            t["case"]: {
                "questionSha256": h.question_sha(t["question"]),
                "vector": [0.25] * 3072,
                "float16Sha256": digest([0.25] * 3072),
            }
            for t in tasks
        }
    }
    captures, reviews = [], []
    for t in tasks:
        for arm in h.ARMS:
            raw = {
                "results": [
                    {
                        "rank": 1,
                        "documentId": "document",
                        "generation": 1,
                        "text": "Required evidence",
                        "fileName": "example.docx",
                        "neighbors": [{"text": "Neighbor evidence"}],
                    }
                ]
            }
            captures.append(
                {
                    "case": t["case"],
                    "arm": arm,
                    "status": "complete",
                    "totalMs": 1,
                    "querySha256": h.question_sha(t["question"]),
                    "queryVectorSha256": digest([0.25] * 3072),
                    "rawResponse": raw,
                    "wire": compact_response(raw, 200000, selectors="handles"),
                    "sdkObservations": [{"sql": query(arm).sql, "responses": []}],
                }
            )
            reviews.append(
                {
                    "case": t["case"],
                    "arm": arm,
                    "initialEvidenceReviewed": True,
                    "expandedEvidenceReviewed": True,
                    "initialRegions": ["region"] if t["answerable"] else [],
                    "expandedRegions": ["region"] if t["answerable"] else [],
                    "initialFacts": ["fact"] if t["answerable"] else [],
                    "expandedFacts": ["fact"] if t["answerable"] else [],
                    "initialUsefulHitRanks": [],
                    "initialIrrelevantHitRanks": [],
                    "initialUnjudgedHitRanks": [1],
                }
            )
    root = tmp_path / "capture"
    root.mkdir()
    config = {"sourceMap": {"document": "source"}}
    for name, value in (
        ("plan.json", plan),
        ("query-cache-private.json", cache),
        ("config-private.json", config),
        ("settings-private.json", {}),
        ("preflight-private.json", {}),
        ("source-before.json", snapshot),
        ("source-after.json", snapshot),
        ("captures-private.json", captures),
    ):
        save(root / name, value)
    controls = {
        "codeSha256": h.code_hashes(),
        "manifestSha256": plan["manifestSha256"],
        "tasksSha256": plan["tasksSha256"],
        **{
            key: h.sha(root / name)
            for key, name in (
                ("planSha256", "plan.json"),
                ("queryCacheSha256", "query-cache-private.json"),
                ("configSha256", "config-private.json"),
                ("preflightSha256", "preflight-private.json"),
            )
        },
    }
    save(root / "controls.json", controls)
    save(
        root / "capture-receipt.json",
        {
            "status": "captured",
            "finishedAt": "2026-11-02T00:00:00+00:00",
            "sourceSnapshotSha256": digest(snapshot),
            **controls,
            "artifactsSha256": {name: h.sha(root / name) for name in h.CAPTURE_FILES},
        },
    )
    save(tmp_path / "reviews.json", reviews)
    return tmp_path, root, reviews


def run_score(receipt):
    path, root, _ = receipt
    return h.score(
        root,
        path / "manifest.json",
        path / "tasks.json",
        path / "gold.json",
        path / "reviews.json",
        path / "scored.json",
    )


def test_scoring_keeps_unanswerable_and_unjudged_precision_unavailable(receipt):
    path, _, _ = receipt
    assert run_score(receipt)["status"] == "complete"
    scored = h.read(path / "scored.json")
    assert len(scored["rows"]) == 80
    absent, answerable = scored["rows"][0], scored["rows"][2]
    assert absent["initialRegionRecall"] is None
    assert absent["initialFactRecall"] is None
    assert absent["initialUsefulHitPrecision"] is None
    assert absent["labeledEvidenceHitPrecision"] is None
    assert answerable["initialRegionRecall"] == 1
    assert answerable["labeledEvidenceHitPrecision"] == 1
    assert answerable["initialUsefulHitPrecision"] is None
    assert answerable["initialUnjudgedHitCount"] == 1
    assert "Required evidence" not in json.dumps(scored)


def test_string_diagnostic_does_not_override_manual_source_fact_judgment(receipt):
    path, _, reviews = receipt
    reviews[2].update(initialRegions=[], expandedRegions=[], initialFacts=[], expandedFacts=[])
    save(path / "reviews.json", reviews)
    run_score(receipt)
    row = h.read(path / "scored.json")["rows"][2]
    assert row["initialRegionRecall"] == 0
    assert row["initialFactRecall"] == 0
    assert row["diagnostics"]["initialTextMatchRegionIds"] == ["region"]


@pytest.mark.parametrize(
    "mutation", ["raw", "snapshot", "query", "code", "missing-review", "unlabeled-hit", "foreign-label"]
)
def test_changed_or_incomplete_evidence_is_rejected(receipt, mutation):
    path, root, reviews = receipt
    if mutation in {"raw", "snapshot", "query", "code"}:
        name = {
            "raw": "captures-private.json",
            "snapshot": "source-after.json",
            "query": "query-cache-private.json",
            "code": "controls.json",
        }[mutation]
        (root / name).write_text("{}")
    elif mutation == "missing-review":
        save(path / "reviews.json", reviews[:-1])
    elif mutation == "unlabeled-hit":
        reviews[0]["initialUnjudgedHitRanks"] = []
        save(path / "reviews.json", reviews)
    else:
        reviews[0]["initialRegions"] = ["not-required"]
        save(path / "reviews.json", reviews)
    with pytest.raises((ValueError, KeyError)):
        run_score(receipt)


def test_delivery_requires_compact_wire_same_as_raw_and_correct_generation(receipt):
    path, root, _ = receipt
    row = h.read(root / "captures-private.json")[0]
    task = h.read(path / "tasks.json")[0]
    snapshot, cache = h.read(root / "source-before.json"), h.read(root / "query-cache-private.json")
    assert h.validate_delivery(row, task, snapshot, cache) == "vector"
    row["wire"]["evidence"]["results"][0]["text"] = "different"
    with pytest.raises(ValueError, match="delivered"):
        h.validate_delivery(row, task, snapshot, cache)
    row["rawResponse"]["results"][0]["generation"] = 2
    row["wire"] = compact_response(row["rawResponse"], 200000, selectors="handles")
    with pytest.raises(ValueError, match="invisible"):
        h.validate_delivery(row, task, snapshot, cache)
