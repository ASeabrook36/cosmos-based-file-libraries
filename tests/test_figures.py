"""Figure crops, attributed captions, text separation and restartable layout batches."""

import copy

import pytest

from fli.blobs import MemoryBlobs
from fli.extraction import OcrError
from fli.figures import extract_pdf_figures, text_sections
from tests.layout_fakes import FakeLayout, paragraph, region, result


async def test_figures_use_crop_bytes_and_keep_captions_in_both_evidence_channels():
    layout, blobs = FakeLayout(), MemoryBlobs()
    extracted = await extract_pdf_figures(b"%PDF-source", 2, layout, blobs, "text/", "images/")
    [visual] = extracted.visuals
    assert visual.kind == "pdf_figure" and visual.location == {"page": 1, "figure": "1.1"}
    assert (visual.width, visual.height) == (640, 400)
    assert visual.caption == "Figure 1: GDP growth between 2020 and 2025."
    assert visual.caption in visual.context
    assert visual.caption_location == [region(1, (1, 6.1, 7, 6.5))]
    assert visual.meta["caption"] == visual.caption
    assert visual.meta["regions"][0]["unit"] == "inch"
    assert visual.meta["regions"][0]["pageWidth"] == 8.5
    body = "\n".join(s.text for s in extracted.sections)
    assert visual.caption in body and "productivity" in body
    assert "GDP growth 2020-2025" not in body  # graphic-internal label
    assert await blobs.download(visual.blob_path) == visual.data
    assert layout.figure_calls == ["1.1"] and extracted.skipped == 0


async def test_cap_counts_figures_without_dropping_later_page_text_or_fetching_extra_crops():
    layout, blobs = FakeLayout(), MemoryBlobs()
    original = layout.analyze_pages

    async def repeated(data, first, last):
        layout.figure_page = first
        return await original(data, first, last)

    layout.analyze_pages = repeated
    extracted = await extract_pdf_figures(b"source", 5, layout, blobs, "text/", "images/", batch_pages=2, cap=1)
    assert layout.calls == [(1, 2), (3, 4), (5, 5)]
    assert layout.figure_calls == ["1.1"] and extracted.skipped == 2
    assert "expansion on page 5" in "\n".join(s.text for s in extracted.sections)
    assert len(extracted.visuals) == 1
    replay = await extract_pdf_figures(b"source", 5, layout, blobs, "text/", "images/", batch_pages=2, cap=1)
    assert replay.batches_analyzed == 0 and replay.batches_reused == 3
    assert len(layout.calls) == 3 and len(layout.figure_calls) == 1
    assert replay.visuals[0].meta == extracted.visuals[0].meta
    assert replay.visuals[0].data == extracted.visuals[0].data


async def test_restart_after_later_layout_failure_reuses_successful_batches():
    layout, blobs = FakeLayout(), MemoryBlobs()
    original = layout.analyze_pages

    async def interrupted(data, first, last):
        if first > 2:
            raise OcrError("layout_temporarily_unavailable", transient=True)
        return await original(data, first, last)

    layout.analyze_pages = interrupted
    with pytest.raises(OcrError):
        await extract_pdf_figures(b"source", 4, layout, blobs, "text/", "images/", batch_pages=2)
    assert len(layout.calls) == 1
    layout.analyze_pages = original
    extraction = await extract_pdf_figures(b"source", 4, layout, blobs, "text/", "images/", batch_pages=2)
    assert layout.calls == [(1, 2), (3, 4)]
    assert extraction.batches_reused == 1 and extraction.batches_analyzed == 1
    assert layout.figure_calls == ["1.1"]


async def test_layout_page_bound_is_checked_before_analyze():
    layout = FakeLayout()
    layout.max_pages = 2
    with pytest.raises(OcrError, match="layout_page_limit_exceeded") as exc:
        await extract_pdf_figures(b"source", 3, layout, MemoryBlobs(), "text/", "images/")
    assert not exc.value.transient and not layout.calls


async def test_changed_source_or_configuration_invalidates_layout_checkpoint():
    layout, blobs = FakeLayout(), MemoryBlobs()
    for data, cap in [(b"first source", 50), (b"second source", 50), (b"second source", 1)]:
        await extract_pdf_figures(data, 1, layout, blobs, "text/", "images/", cap=cap)
    assert len(layout.calls) == 3


def test_caption_missing_from_paragraphs_and_tables_preserve_reading_order_without_duplicates():
    layout = result(1, 1)
    figure = layout["figures"][0]
    layout["paragraphs"].pop()  # caption exists only on figure
    figure["caption"]["elements"] = []
    table_cells = [paragraph("Year", 200), paragraph("Growth", 210), paragraph("2025", 220), paragraph("3%", 230)]
    start = len(layout["paragraphs"])
    layout["paragraphs"].extend(table_cells)
    layout["tables"] = [
        {
            "boundingRegions": [region(1, (1, 8, 7, 9))],
            "spans": [{"offset": 200, "length": 35}],
            "cells": [
                {
                    "rowIndex": i // 2,
                    "columnIndex": i % 2,
                    "content": p["content"],
                    "elements": [f"/paragraphs/{start + i}"],
                }
                for i, p in enumerate(table_cells)
            ],
            "caption": paragraph("Table 1: Annual values", 190),
        }
    ]
    text = "\n".join(s.text for s in text_sections(layout))
    assert text.count(figure["caption"]["content"]) == 1
    assert "Year | Growth\n2025 | 3%" in text
    assert text.index("productivity") < text.index("Figure 1") < text.index("Table 1") < text.index("Year | Growth")
    assert text.count("2025") == 2  # figure caption and table, no duplicated cells


def test_layout_context_references_do_not_remove_body_text_outside_figure():
    layout = result(1, 1)
    layout["figures"][0]["elements"] += ["/paragraphs/0"]
    assert "productivity" in "\n".join(s.text for s in text_sections(layout))
    original = copy.deepcopy(layout)
    text_sections(layout)
    assert layout == original


def test_unlinked_caption_can_be_recovered_from_aligned_attributed_text_below_figure():
    from fli.figures import figure_caption

    layout = result(1, 1, caption=False)
    caption = paragraph(
        "A comparison of human and automated tasks (Adapted from Example, 2025).", 150, box=(1, 6.1, 7, 6.4)
    )
    layout["paragraphs"].append(caption)
    recovered = figure_caption(layout, layout["figures"][0])
    assert recovered["content"] == caption["content"] and recovered["association"] == "adjacent_inferred"
    assert recovered["boundingRegions"] == caption["boundingRegions"]
    assert caption["content"] in "\n".join(s.text for s in text_sections(layout))


@pytest.mark.parametrize(
    "text,role,box,page",
    [
        ("Ordinary explanatory prose beneath the figure.", None, (1, 6.1, 7, 6.4), 1),
        ("Figure 2: A section heading", "sectionHeading", (1, 6.1, 7, 6.4), 1),
        ("Source: A distant paragraph", None, (1, 9, 7, 9.4), 1),
        ("Source: Another column", None, (7.5, 6.1, 8.4, 6.4), 1),
        ("Figure 2: A preceding page caption", None, (1, 6.1, 7, 6.4), 2),
    ],
)
def test_nearby_prose_headers_wrong_columns_and_other_pages_are_not_promoted_to_captions(text, role, box, page):
    from fli.figures import figure_caption

    layout = result(1, 1, caption=False)
    layout["paragraphs"].append(paragraph(text, 150, page=page, box=box, role=role))
    assert not figure_caption(layout, layout["figures"][0])


def test_layout_caption_takes_precedence_and_ambiguous_nearest_associations_are_not_guessed():
    from fli.figures import figure_caption

    layout = result(1, 1)
    assert figure_caption(layout, layout["figures"][0])["association"] == "layout"
    layout["figures"][0].pop("caption")
    layout["paragraphs"][-1] = paragraph("Source: Shared ambiguous attribution", 150, box=(1, 6.1, 7, 6.4))
    layout["figures"].append({**layout["figures"][0], "id": "another-figure"})
    assert not figure_caption(layout, layout["figures"][0])
