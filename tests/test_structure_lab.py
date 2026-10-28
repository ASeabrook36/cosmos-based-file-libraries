"""Functional safeguards, not evidence of model or retrieval quality."""

import asyncio
import json
from pathlib import Path

import pytest

from fli.chunking import ApproxTokenizer
from fli.proposals import ChatReply
from fli.query import partition_key_for
from fli.structure_lab import (
    CONTAINERS,
    Summarizer,
    digest,
    markdown_document,
    prepare,
    render_query,
    rows_for,
    source_read,
    target_container,
)


class Model:
    def __init__(self):
        self.inputs = []

    async def complete_json(self, messages):
        data = json.loads(messages[-1]["content"])
        self.inputs.append(data)
        return ChatReply({"summary": "Discovery hint " + digest(data)[:16]}, 20, 5)


def test_lossless_heading_tree_and_fences_and_unknown_genre():
    source = "Opening\r\n# Chapter One\r\nBody\r\n### Results\r\nA\n```md\n# not a node\n```\n# End\nZ"
    doc = markdown_document("a", "Paper", source, "research_paper")
    assert "".join(b["text"] for b in doc["blocks"]) == source
    assert [n["parentId"] for n in doc["nodes"]] == [None, "n0", "n1", "n0"]
    assert [n["level"] for n in doc["nodes"]] == ["document", "chapter", "section", "section"]
    assert doc["nodes"][2]["role"] == "results"
    for b in doc["blocks"]:
        assert source[b["start"] : b["end"]] == b["text"]
    assert markdown_document("a", "Unknown", "# Results\nA")["nodes"][1]["role"] == "unknown"


def test_source_reads_subtrees_once_in_order_and_never_partially():
    doc = markdown_document("a", "Paper", "Intro\n# One\nA\n## Two\nB\n# Three\nC")
    doc["summaries"] = {"n1": {"text": "This is invented generated discovery text"}}
    tokenizer = ApproxTokenizer()
    result = source_read(doc, ["n2", "n1"], tokenizer, 10000)
    assert "".join(b["text"] for b in result["evidence"]["blocks"]) == "# One\nA\n## Two\nB\n"
    assert source_read(doc, ["n1"], tokenizer, 1)["evidence"] is None
    with pytest.raises(ValueError):
        source_read(doc, ["missing"], tokenizer, 10000)


def test_incremental_add_edit_delete_and_model_version_invalidation():
    async def scenario():
        model = Model()
        s = Summarizer(model, ApproxTokenizer(), "model-revision-1")
        one = markdown_document("a", "Paper", "# Results\nA\n# Methods\nB", "research_paper")
        first = await prepare([one], s, "lab", "Owner wording")
        n = s.calls
        assert (await prepare([one], s, "lab", "Owner wording")) == first
        assert s.calls == n
        two = markdown_document("b", "Letter", "# Body\nNew topic", "correspondence")
        added = await prepare([one, two], s, "lab", "Owner wording")
        assert s.calls - n == 3  # new body + document + library; all old document summaries reused
        assert added["ownerDescription"] == first["ownerDescription"]
        assert added["contentsOverview"]["inventoryDigest"] != first["contentsOverview"]["inventoryDigest"]
        n = s.calls
        edited = markdown_document("a", "Paper", "# Results\nChanged\n# Methods\nB", "research_paper")
        await prepare([edited, two], s, "lab")
        assert s.calls - n == 3  # changed section, document and library; sibling and second doc reused
        await prepare([two], s, "lab")
        assert model.inputs[-1]["context"]["documentCount"] == 1
        assert len(model.inputs[-1]["parts"]) == 1
        n = s.calls
        empty = await prepare([], s, "lab", "Owner wording")
        assert s.calls == n
        assert empty["ownerDescription"] == "Owner wording"
        assert empty["documents"] == [] and "no documents" in empty["contentsOverview"]["text"]
        newer = Summarizer(model, s.tokenizer, "model-revision-2", cache=s.cache)
        await prepare([one], newer, "lab")
        assert newer.calls == 4

    asyncio.run(scenario())


def test_long_sections_cover_all_source_through_bounded_reductions():
    async def scenario():
        model = Model()
        s = Summarizer(model, ApproxTokenizer(), "model", input_tokens=100, summary_tokens=20)
        parts = [" ".join(f"word{i}" for i in range(400))]
        await s.reduce({"level": "section"}, parts, "dep")
        seen = " ".join(p for call in model.inputs for p in call["parts"])
        assert all(f"word{i}" in seen for i in range(400))
        assert all(sum(len(s.tokenizer.encode(p)) for p in call["parts"]) <= 100 for call in model.inputs)
        assert s.calls > 1

    asyncio.run(scenario())


def test_bad_model_output_is_not_cached_or_published():
    class BadModel:
        async def complete_json(self, messages):
            return ChatReply({"summary": "too long " * 50})

    s = Summarizer(BadModel(), ApproxTokenizer(), "bad", input_tokens=100, summary_tokens=20)
    with pytest.raises(ValueError, match="exceeds budget"):
        asyncio.run(s.reduce({}, ["source"], "dep"))
    assert s.cache == {}


def test_overlong_output_retries_without_clipping_and_accounts_for_both_calls():
    class RetryModel:
        calls = 0

        async def complete_json(self, messages):
            self.calls += 1
            text = "too long " * 50 if self.calls == 1 else "Short qualified discovery hint."
            return ChatReply({"summary": text}, 30, 10)

    s = Summarizer(RetryModel(), ApproxTokenizer(), "retry", input_tokens=100, summary_tokens=20)
    assert asyncio.run(s.reduce({}, ["original source"], "dependency")) == "Short qualified discovery hint."
    assert (s.calls, s.prompt_tokens, s.completion_tokens) == (2, 60, 20)
    assert list(s.cache.values()) == ["Short qualified discovery hint."]


def test_hash_in_a_heading_is_preserved_unless_it_is_a_markdown_closing_marker():
    doc = markdown_document("a", "Languages", "# C#\nText\n# Python ###\nMore text")
    assert [n["title"] for n in doc["nodes"][1:]] == ["C#", "Python"]


def test_rows_are_typed_and_each_result_resolves_to_original_source():
    s = Summarizer(Model(), ApproxTokenizer(), "model")
    doc = markdown_document("a", "Paper", "# Methods\nMeasured original text", "research_paper")
    bundle = asyncio.run(prepare([doc], s, "lab"))
    rows = rows_for(bundle, s.tokenizer)
    assert len({r["id"] for r in rows}) == len(rows)
    assert {r["recordKind"] for r in rows} == {"summary", "passage"}
    for row in rows:
        assert row["generated"] == (row["recordKind"] == "summary")
        assert source_read(doc, [row["nodeId"]], s.tokenizer, 10000)["complete"]
    assert "Measured original text" in next(r["text"] for r in rows if r["level"] == "passage")


@pytest.mark.parametrize("level", ["passage", "section", "chapter", "document"])
def test_query_is_library_bound_typed_and_float16(level):
    q = render_query("lib", [0.123456, 1e-9], level, document_id="doc")
    assert q.sql.startswith("SELECT TOP 10 ")
    assert q.sql.count("c.libraryId = @libraryId") == 1
    assert "c.documentId = @documentId" in q.sql
    assert "c.recordKind = @recordKind" in q.sql
    assert "embedding" not in q.sql.split(" FROM ")[0]
    assert "'dataType': 'float16'" in q.sql
    assert q.parameters[1]["value"][1] == 0
    assert partition_key_for(q.sql, "lib") == ["lib"]
    hybrid = render_query("lib", [0.1], level, query_text="original words")
    assert partition_key_for(hybrid.sql, "lib") is None
    assert target_container("shared", level) == "lab-shared"
    assert target_container("split", level) == ("lab-passages" if level == "passage" else "lab-summaries")
    assert ", true," in render_query("lib", [0.1], level, exact=True).sql


@pytest.mark.parametrize("kwargs", [{"top": True}, {"top": 0}, {"top": 41}, {"query_text": "q", "exact": True}])
def test_invalid_search_is_rejected(kwargs):
    with pytest.raises(ValueError):
        render_query("lib", [0.1], "passage", **kwargs)


def test_layouts_hold_vector_policy_constant_and_index_queried_fields():
    root = Path(__file__).resolve().parents[1] / "cosmos"
    base = json.loads((root / "chunks.container.json").read_text())
    for name in CONTAINERS:
        body = json.loads((root / f"{name}.container.json").read_text())
        assert body["id"] == name
        assert body["partitionKey"] == base["partitionKey"]
        assert body["vectorEmbeddingPolicy"] == base["vectorEmbeddingPolicy"]
        assert body["indexingPolicy"]["vectorIndexes"] == base["indexingPolicy"]["vectorIndexes"]
        paths = {p["path"] for p in body["indexingPolicy"]["includedPaths"]}
        assert {"/libraryId/?", "/documentId/?", "/level/?", "/recordKind/?"} <= paths
        assert "/id/?" not in paths
