"""Navigation control tests; synthetic vectors are never experimental evidence."""

import copy
import json

import pytest

from fli.structure_lab import digest
from fli.structure_representation import VARIANTS
from scripts import retrieval_r2_navigation as nav
from scripts.retrieval_r0_corpus import sha256


def test_navigation_plan_follows_passage_decision_and_freezes_candidate_budget(tmp_path, monkeypatch):
    source = {"digest": "source"}
    monkeypatch.setattr(nav, "load_source", lambda _: source)
    source_dir, output = tmp_path / "source", tmp_path / "output"
    source_dir.mkdir()
    output.mkdir()
    (source_dir / "r2-evaluation.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "sourceArtifactDigest": "source",
                "candidatePassedPredeclaredGate": False,
            }
        )
    )
    (source_dir / "r2-vectors.json").write_text(json.dumps({"arraySha256": "unchanged"}))
    nav.plan(source_dir, output)
    plan = json.loads((output / "navigation-plan.json").read_text())
    assert plan["selectedRepresentation"] == VARIANTS[0]
    assert plan["passageVectorArraySha256"] == "unchanged"
    assert plan["heldoutExecuted"] is False
    assert set(plan["sourceIds"]) == {"d06", "d07", "d08"}
    assert all(sum(arm.values()) == 8 for arm in plan["arms"].values())
    assert plan["evidenceOnlyTokenAllowance"] == 200000
    with pytest.raises(ValueError, match="fresh"):
        nav.plan(source_dir, output)


def test_navigation_inputs_reject_changed_passage_decision_or_vector_bytes(tmp_path, monkeypatch):
    source_dir, output = tmp_path / "source", tmp_path / "output"
    source_dir.mkdir()
    output.mkdir()
    source = {
        "digest": "source",
        "bundle": {"documents": [{"id": key} for key in nav.SOURCES]},
        "tasks": [{"case": key} for key in nav.CASES],
    }
    monkeypatch.setattr(nav, "load_source", lambda _: source)
    decision = {"status": "complete", "sourceArtifactDigest": "source", "candidatePassedPredeclaredGate": True}
    (source_dir / "r2-evaluation.json").write_text(json.dumps(decision))
    raw = b"frozen vector fixture"
    (source_dir / "r2-vectors.npy").write_bytes(raw)
    vectors = {"arrayFile": "r2-vectors.npy", "arraySha256": sha256(raw)}
    vectors["digest"] = digest(vectors)
    (source_dir / "r2-vectors.json").write_text(json.dumps(vectors))
    nav.plan(source_dir, output)
    assert nav.inputs(source_dir, output)[0]["selectedRepresentation"] == VARIANTS[1]
    changed = copy.deepcopy(decision) | {"candidatePassedPredeclaredGate": False}
    (source_dir / "r2-evaluation.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="decision changed"):
        nav.inputs(source_dir, output)
    (source_dir / "r2-evaluation.json").write_text(json.dumps(decision))
    (source_dir / "r2-vectors.npy").write_bytes(b"reembedded")
    with pytest.raises(ValueError, match="vector bytes changed"):
        nav.inputs(source_dir, output)


def test_named_file_control_uses_question_mentions_without_gold_and_records_choice():
    docs = [
        {
            "id": key,
            "title": name,
            "sourceSha256": key,
            "sourceDigest": key,
            "blocks": [{"id": "b0", "text": "Original source"}],
        }
        for key, name in (("d06", "ea-policy.docx"), ("d07", "ea-annex-1.docx"))
    ]
    hits, choice = nav.named_document_hits("Compare EA-ANNEX-1.DOCX with ea-policy.docx", docs)
    assert choice == {"matchedDocuments": 2, "selectedDocumentId": "d07", "tieAtFirstPosition": False}
    assert hits[0]["documentId"] == "d07"
    assert hits[0]["sourceSpans"] == [{"blockId": "b0", "start": 0, "end": 15}]
    assert nav.named_document_hits("Unspecified file", docs)[0] == []


async def test_summary_plumbing_covers_source_caches_and_measures_actual_omission(tmp_path, monkeypatch):
    """Fake providers exercise the unrun cloud path, never claim summary quality."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import azure.identity.aio

    import fli.embedding
    from fli.embedding import EmbeddingUsage
    from fli_demo.agent import Reply

    documents = [
        {
            "id": key,
            "title": key + ".docx",
            "genre": "unknown",
            "canonicalTreeDigest": key,
            "sourceDigest": digest("Whole source.\n"),
            "sourceSha256": key,
            "blocks": [{"id": "b0", "text": "Whole source.\n"}],
        }
        for key in nav.SOURCES
    ]
    monkeypatch.setattr(nav, "inputs", lambda *_: ({}, {"digest": "source"}, {}, documents, []))
    monkeypatch.setenv("AOAI_ENDPOINT", "https://example.test")
    monkeypatch.setenv("AOAI_CHAT_DEPLOYMENT", "summary")
    monkeypatch.setenv("AOAI_EMBEDDING_DEPLOYMENT", "embed")
    calls = []

    def chat(messages, tools):
        calls.append(messages)
        assert not tools and "Whole source." in messages[-1]["content"]
        return Reply(
            json.dumps({"summary": "Discovery hint."}),
            [],
            {"prompt_tokens": 20, "completion_tokens": 4},
            {"totalMs": 1},
        )

    monkeypatch.setattr(nav, "default_chat_factory", lambda *_: chat)

    class Credential:
        async def close(self):
            pass

    class Embedder:
        def __init__(self, *args):
            self.usage = EmbeddingUsage()

        async def embed(self, texts):
            self.usage.requests += 1
            return [[0.1] * 3072 for _ in texts]

    monkeypatch.setattr(azure.identity.aio, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(fli.embedding, "AzureOpenAIEmbedder", Embedder)
    paths = {}
    for name, model, revision in (("summary", "gpt-6.1-sol", "2026-09-29"), ("embed", "text-embedding-3-large", "1")):
        receipt = {
            "capturedAt": datetime.now(UTC).isoformat(),
            "account": {"id": "/accounts/test", "properties": {"endpoint": "https://example.test"}},
            "deployment": {
                "id": "/accounts/test/deployments/" + name,
                "sku": {"name": "GlobalStandard", "capacity": 1},
                "properties": {
                    "model": {"format": "OpenAI", "name": model, "version": revision},
                    "provisioningState": "Succeeded",
                    "versionUpgradeOption": "NoAutoUpgrade",
                },
            },
        }
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(receipt))
    await nav.freeze(
        SimpleNamespace(model_receipt=paths["summary"], embedding_receipt=paths["embed"]), tmp_path, tmp_path
    )
    artifact = json.loads((tmp_path / "navigation-frozen.json").read_text())
    assert len(calls) == artifact["summaryCreationCalls"] == 3
    assert artifact["unchangedRefreshAdditionalModelCalls"] == 0
    assert len(artifact["summarySourceInputs"]) == 3
    assert all(row["sourceBlocks"] == 1 and row["sourceCharacters"] == 14 for row in artifact["summarySourceInputs"])
    assert artifact["omission"]["removedSummaryRows"] == 1
    assert artifact["omission"]["remainingSummaryRows"] == 2
    assert artifact["omission"]["retainedSummaryVectorsUnchanged"]
    assert (tmp_path / "navigation-omission-private.json").exists()
