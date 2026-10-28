"""Unselected image candidates must not become an answer gallery."""

import ast
import re
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from fli_demo.agent import compact, plain_dollars


def renderer():
    source = Path(__file__).resolve().parents[1] / "demo/pages/3_Chat.py"
    parsed = ast.parse(source.read_text())
    function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "show_answer")
    shown, labels = [], []
    st = SimpleNamespace(
        session_state={"image_bytes": {"chosen": b"chosen pixels", "other": b"other pixels"}},
        image=lambda data, **kwargs: shown.append(data),
        write=lambda text: labels.append(text),
        caption=lambda text: labels.append(text),
        expander=lambda text: nullcontext(),
    )
    namespace = {"st": st, "re": re, "plain_dollars": plain_dollars}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["show_answer"], shown, labels


def traces():
    return [
        SimpleNamespace(
            images=[{"imageId": key, "url": "unused", "caption": key + " source"} for key in ("chosen", "other")]
        )
    ]


def test_only_explicitly_selected_returned_images_are_displayed():
    render, shown, labels = renderer()
    render("Here it is: ![matching figure](fli-image:chosen)", traces())
    assert shown == [b"chosen pixels"]
    assert "other source" in " ".join(labels)


def test_omitting_inline_markers_does_not_present_all_candidates_as_answers():
    render, shown, labels = renderer()
    render("I found an excerpt.", traces())
    assert not shown and "Retrieval candidates may be unrelated" in " ".join(labels)


def test_image_search_separates_section_context_from_caption_and_title():
    payload = compact(
        {
            "coverage": {"disposition": "results"},
            "results": [
                {
                    "chunkId": "image",
                    "fileName": "source.pdf",
                    "location": {"page": 4},
                    "text": "Image from source.pdf, page 4, under 'Previous section heading': "
                    "Actual figure description",
                    "heading": "Previous section heading",
                    "image": {"url": "unused", "caption": None},
                }
            ],
        }
    )
    row = payload["results"][0]
    assert row["heading"] is None and row["sectionContext"] is None
    assert row["contextHeading"] == "Previous section heading"
    assert "not_figure_title" in row["headingAssociation"]
    assert row["image"]["caption"] is None
    assert row["text"] == "Actual figure description"


@pytest.mark.parametrize("text_leads", [False, True])
def test_search_previews_respect_overall_ranking_and_leave_room_for_target_page_lookup(text_leads):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from tests.test_demo import AGENT, FakeIndex, scripted

    index = FakeIndex()
    original = index.search

    def candidates(*args, **kwargs):
        payload = original(*args, **kwargs)
        base = payload["results"][0]
        payload["results"] = [
            dict(
                base,
                chunkId=f"candidate-{n}",
                imageId=f"candidate-{n}",
                contentKind="image",
                image={"url": f"https://example.invalid/{n}.png"},
            )
            for n in range(8)
        ]
        if text_leads:
            payload["results"].insert(0, base)
        return payload

    index.search = candidates
    chat = scripted(
        Reply(None, [ToolCall("search", "search_library_finance", '{"query":"picture"}')]),
        Reply("A candidate: ![Candidate](fli-image:candidate-0)"),
    )
    turn = run_turn(chat, index, AGENT, [], "Find a remembered picture", context_token_budget=48000)
    assert len(turn.searches[0].images) == 8
    assert sum(
        part.get("type") == "image_url"
        for m in turn.messages
        if isinstance(m.get("content"), list)
        for part in m["content"]
    ) == (0 if text_leads else 1)


def test_associated_source_caption_is_displayed_with_its_figure_and_provenance():
    render, shown, labels = renderer()
    trace = traces()
    trace[0].images[0].update(sourceCaption="Figure 1: Task choices.", captionAssociation="adjacent_inferred")
    # Inspect actual caption passed to Streamlit, alongside the selected pixels.
    captured = []
    render.__globals__["st"].image = lambda data, **kwargs: captured.append(kwargs["caption"])
    render("![matching figure](fli-image:chosen)", trace)
    assert captured == ["Nearby caption (inferred): Figure 1: Task choices. — chosen source"]


def test_untitled_image_does_not_expose_its_internal_locator_as_a_printed_figure_number():
    payload = compact(
        {
            "coverage": {"disposition": "results"},
            "results": [
                {
                    "chunkId": "image",
                    "fileName": "source.pdf",
                    "location": {"page": 4, "figure": "4.1"},
                    "text": "Image from source.pdf, page 4, figure 4.1, under '': Actual diagram description",
                    "heading": None,
                    "image": {"url": "unused", "caption": None},
                }
            ],
        }
    )
    row = payload["results"][0]
    assert row["text"] == "Actual diagram description" and row["location"] == {"page": 4}


def test_agent_can_omit_the_displayed_source_caption_without_hiding_provenance():
    render, shown, labels = renderer()
    trace = traces()
    trace[0].images[0].update(sourceCaption="Figure 1: Task choices.", captionAssociation="layout")
    captions = []
    render.__globals__["st"].image = lambda data, **kwargs: captions.append(kwargs["caption"])
    render("![Source figure](fli-image:chosen?caption=omit)", trace)
    assert captions == ["chosen source"]
