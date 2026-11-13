"""Source-derived navigation and compact-only refinement, through the real API."""

import hashlib
import io
import json

import pytest
from docx import Document

from fli_demo.agent import Reply, ToolCall, run_turn, source_documents
from tests.test_granular_inspection import api_read
from tests.test_granular_navigation import QUALIFIER, child_named, hierarchy, outline, read, ready, text_of


def docx_with_text(text):
    document = Document()
    document.add_heading("Equipment", level=1)
    document.add_paragraph(text)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize("text", ["Inspect A & B <protocol> before use.", "🙂" * 170, "", "x" * 160, "x" * 161])
async def test_docx_preview_is_exact_bounded_original_text_and_never_source_evidence(text):
    env = await ready(source=docx_with_text(text))
    heading = child_named(env, None, "Equipment")
    result = read(env, heading["outlineSelector"])
    entries = result["documents"][0]["outline"]
    assert entries[0]["paragraphRole"] == "authored_heading"
    assert entries[0]["navigationPreview"]["prefix"] == "Equipment"
    assert entries[1]["navigationPreview"] == {
        "prefix": text[:160],
        "characters": min(len(text), 160),
        "sourceCharacters": len(text),
        "truncated": len(text) > 160,
    }
    assert "paragraphRole" not in entries[1]
    assert "text" not in entries[1] and "citation" not in entries[1]
    assert source_documents(result) == set()
    assert any("even when truncated=false" in note for note in result["notes"])
    selected = read(env, entries[1]["readSelector"])
    source = selected["documents"][0]["sections"][0]
    assert source["citation"] == "procedure.docx (source paragraph 2)"
    assert source["text"] and selected["selectionComplete"]
    assert len(selected["documents"][0]["sections"]) == 1
    assert source_documents(selected) == {env.doc["id"]}


async def test_markdown_navigation_preview_preserves_exact_block_prefix_and_character_coordinates():
    source = "# Equipment\n\n" + "Keep original whitespace.  \n" * 12
    env = await ready(source=source.encode(), name="equipment.md")
    heading = child_named(env, None, "Equipment")
    entry = outline(env, heading)["outline"][0]
    assert entry["navigationKind"] == "sourceBlock"
    actual = read(env, entry["readSelector"])["documents"][0]["sections"][0]
    assert entry["navigationPreview"]["prefix"] == actual["text"][:160]
    assert entry["navigationPreview"]["sourceCharacters"] == len(actual["text"])
    assert source[actual["location"]["charStart"] : actual["location"]["charEnd"]] == actual["text"]
    assert "source characters" in actual["citation"] and "page" not in actual["location"]


@pytest.mark.parametrize("view", ["raw", "compact", "references"])
@pytest.mark.parametrize("inspection", [True, False])
async def test_navigation_prefixes_are_removed_on_inspection_or_atomic_budget_refusal(view, inspection):
    secret = "Unpublished equipment serial 99371. " * 8
    env = await ready(source=docx_with_text(secret))
    heading = child_named(env, None, "Equipment")
    response = api_read(
        env,
        {**heading["outlineSelector"], "includeText": not inspection, "maxTokens": 200000 if inspection else 1},
        view=view,
    )
    assert response.status_code == 200
    payload = response.json()
    assert '"navigationPreview"' not in response.text and "Unpublished equipment serial" not in response.text
    assert '"prefix"' not in response.text
    if view == "raw":
        assert payload["disposition"] == ("inspected" if inspection else "budget_exceeded")
    else:
        assert not payload["attachments"]


async def test_authored_ancestry_scope_and_complete_container_reads_are_explicit():
    env = await ready()
    operating, eligibility, table, cell, qualifier = hierarchy(env)
    context = outline(env, eligibility)["outlineContext"]
    assert context == {
        "nodeId": eligibility["navigationNodeId"],
        "kind": "section",
        "title": "Eligibility",
        "authoredPath": [
            {"nodeId": operating["navigationNodeId"], "title": "Operating procedure"},
            {"nodeId": eligibility["navigationNodeId"], "title": "Eligibility"},
        ],
    }
    assert eligibility["readScope"] == "own_content_excluding_authored_subsections"
    assert qualifier["readScope"] == "complete_source_atom"
    assert table["readScope"] == cell["readScope"] == "complete_source_subtree"
    result = read(env, cell["readSelector"])
    selection = result["documents"][0]["selectionSummary"]
    assert selection["sourceAtomCount"] == 4 and selection["selectionUnit"] == "requested_nodes"
    assert selection["nodes"][0]["readScope"] == "complete_source_subtree"
    assert selection["nodes"][0]["authoredPath"] == context["authoredPath"]
    assert result["selectionComplete"] and not result["documentComplete"]
    repeated = read(
        env,
        {"mode": "selected", "documents": [{**cell["readSelector"]["documents"][0], "nodeScope": "subtree"}]},
    )
    assert result["documents"][0]["sections"] == repeated["documents"][0]["sections"]
    assert any("does not expand a complete selection" in note for note in result["notes"])
    # Selecting four atoms does not claim the whole cell was selected.
    selected_atoms = read(
        env,
        {
            "mode": "selected",
            "documents": [{**env.pin, "sectionIds": [s["sectionId"] for s in result["documents"][0]["sections"]]}],
        },
    )
    assert (
        selected_atoms["documents"][0]["selectionSummary"]["selectionUnit"] == "source_atoms_not_container_completeness"
    )
    assert selected_atoms["documents"][0]["selectionSummary"]["nodes"] == []


async def test_compact_deduplicates_navigation_but_raw_and_stored_provenance_remain_intact():
    env = await ready()
    path = env.doc["fullText"]["blobPath"]
    before = hashlib.sha256(await env.s.blobs.download(path)).hexdigest()
    _, _, _, cell, qualifier = hierarchy(env)
    result = read(env, qualifier["readSelector"])
    document = result["documents"][0]
    row = document["sections"][0]
    assert "passageToSourceMapping" not in document and "passageMappingVerification" not in document
    assert "sourceLocationPrecision" not in document["readCapabilities"]
    assert "fullSelector" in document and "outlineSelector" in document
    for key in ("documentSelector", "fullSelector", "outlineSelector", "subtreeSelector", "readCapabilities"):
        assert key not in row
    assert row["parentSelector"] == cell["readSelector"]
    assert row["sectionId"] != row["containingSectionId"]
    assert document["fullSelector"]["documents"] == [env.pin]
    raw = api_read(env, {**qualifier["readSelector"], "maxTokens": 200000}, view="raw").json()
    original = raw["documents"][0]["sections"][0]
    assert original["text"] == row["text"] and original["location"] == row["location"]
    assert original["sourceStructure"]["paragraphs"][0]["text"] == QUALIFIER
    assert original["sourceStructure"]["sourceSha256"] == env.doc["sha256"]
    assert original["fullSelector"] == document["fullSelector"]
    assert "outlineSelector" in original and "subtreeSelector" in original
    assert hashlib.sha256(await env.s.blobs.download(path)).hexdigest() == before


async def test_normal_agent_uses_source_preview_to_copy_a_paragraph_selector_before_answering():
    env = await ready()
    calls = 0

    def chat(messages, tools):
        nonlocal calls
        calls += 1
        name = next(
            tool["function"]["name"] for tool in tools if tool["function"]["name"].startswith("read_documents_")
        )

        def invoke(selector):
            return Reply(None, [ToolCall(str(calls), name, json.dumps(selector))])

        if calls == 1:
            return invoke({"mode": "outline", "documents": [env.pin]})
        evidence = json.loads(messages[-1]["content"])
        if calls in {2, 3}:
            target = "Operating procedure" if calls == 2 else "Eligibility"
            entry = next(row for row in evidence["documents"][0]["outline"] if row["title"] == target)
            assert not source_documents(evidence)
            return invoke(entry["outlineSelector"])
        if calls == 4:
            assert not source_documents(evidence)
            entry = next(
                row
                for row in evidence["documents"][0]["outline"]
                if "submission" in row.get("navigationPreview", {}).get("prefix", "")
            )
            assert entry["navigationKind"] == "paragraph"
            return invoke(entry["readSelector"])
        assert calls == 5 and source_documents(evidence) == {env.doc["id"]}
        assert "A submission needs approval." in text_of(evidence)
        assert len(evidence["documents"][0]["sections"]) == 1
        citation = evidence["documents"][0]["sections"][0]["citation"]
        return Reply(f"A submission needs approval. ({citation})")

    turn = run_turn(
        chat,
        env.index,
        {"id": "reader", "libraryIds": [env.lib]},
        [],
        "Use the outline to find the submission requirement, read that paragraph and cite it.",
        context_token_budget=200000,
        image_inputs=False,
    )
    assert calls == 5 and len(turn.searches) == 4
    assert all(trace.error is None for trace in turn.searches)
