"""Layout-defined PDF figures and source captions, checkpointed in bounded page batches.

The layout service owns segmentation. No PDF raster object or drawing primitive is
promoted to a figure by itself, and no full-page image fallback is used.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from fli.blobs import BlobStore
from fli.checkpoints import digest, freeze, load
from fli.extraction import OcrError, Section
from fli.images import Visual, normalize_visuals


class LayoutReader(Protocol):
    max_pages: int
    cache_identity: str

    async def analyze_pages(self, data: bytes, first: int, last: int) -> tuple[dict, str]: ...
    async def figure(self, operation: str, figure_id: str) -> bytes: ...


def _paragraph_ids(elements) -> set[int]:
    return {int(m[1]) for value in elements or [] if (m := re.fullmatch(r"/paragraphs/(\d+)", value))}


def _offset(item: dict) -> int:
    return min((s.get("offset", 0) for s in item.get("spans") or []), default=0)


def _page(item: dict) -> int | None:
    regions = item.get("boundingRegions") or []
    return regions[0].get("pageNumber") if regions else None


def _bounds(region):
    polygon = region.get("polygon") or []
    if len(polygon) < 8:
        return None
    return min(polygon[0::2]), min(polygon[1::2]), max(polygon[0::2]), max(polygon[1::2])


def figure_caption(result: dict, figure: dict) -> dict:
    """Keep layout captions; conservatively recover labelled/attributed adjacent text.

    Proximity alone never turns ordinary prose or a heading into a caption.
    An inferred association is returned explicitly for consumers to qualify it.
    """
    direct = figure.get("caption") or {}
    if direct.get("content", "").strip():
        return {**direct, "association": "layout"}
    labelled = re.compile(r"^(?:figure|fig\.?|chart|map|plate|illustration)\s*(?:[:.]|[A-Z]?\d+|[IVXLC]+\b)", re.I)
    attributed = re.compile(r"\((?:adapted|reproduced|modified)\s+from\b|\bSources?\s*:", re.I)
    linked = set().union(
        *(_paragraph_ids((f.get("caption") or {}).get("elements")) for f in result.get("figures") or [])
    )
    candidates = []
    for region in figure.get("boundingRegions") or []:
        bounds = _bounds(region)
        if not bounds:
            continue
        x0, y0, x1, y1 = bounds
        page = next((p for p in result.get("pages") or [] if p.get("pageNumber") == region.get("pageNumber")), {})
        height = page.get("height") or 0
        if not height or x1 <= x0:
            continue
        for index, paragraph in enumerate(result.get("paragraphs") or []):
            text = paragraph.get("content", "").strip()
            if (
                index in linked
                or not text
                or len(text) > 1000
                or paragraph.get("role") in ("title", "sectionHeading", "pageHeader", "pageFooter", "pageNumber")
            ):
                continue
            label = labelled.search(text)
            if not label and not attributed.search(text):
                continue
            for box in paragraph.get("boundingRegions") or []:
                b = _bounds(box)
                if not b or box.get("pageNumber") != region.get("pageNumber"):
                    continue
                a0, b0, a1, b1 = b
                gap = b0 - y1 if b0 >= y1 else y0 - b1 if label and b1 <= y0 else -1
                overlap = min(x1, a1) - max(x0, a0)
                if (
                    0 <= gap <= height * 0.045
                    and 0 < b1 - b0 <= height * 0.085
                    and a1 > a0
                    and overlap / min(x1 - x0, a1 - a0) >= 0.65
                ):
                    # A nearby caption belongs to the closest aligned figure, not
                    # every figure sharing a page or column.
                    closer = False
                    for other in result.get("figures") or []:
                        if other is figure:
                            continue
                        for r in other.get("boundingRegions") or []:
                            q = _bounds(r)
                            if not q or r.get("pageNumber") != box.get("pageNumber"):
                                continue
                            other_gap = b0 - q[3] if b0 >= q[3] else q[1] - b1 if label and b1 <= q[1] else -1
                            if 0 <= other_gap <= gap and min(q[2], a1) > max(q[0], a0):
                                closer = True
                    if not closer:
                        candidates.append((gap, index, paragraph))
    if not candidates:
        return {}
    _, index, paragraph = min(candidates, key=lambda item: (item[0], item[1]))
    return {**paragraph, "elements": [f"/paragraphs/{index}"], "association": "adjacent_inferred"}


def _inside(paragraph: dict, regions: list[dict]) -> bool:
    for p in paragraph.get("boundingRegions") or []:
        polygon = p.get("polygon") or []
        if len(polygon) < 8:
            continue
        x, y = sum(polygon[0::2]) / (len(polygon) / 2), sum(polygon[1::2]) / (len(polygon) / 2)
        for r in regions:
            q = r.get("polygon") or []
            if r.get("pageNumber") == p.get("pageNumber") and len(q) >= 8:
                if min(q[0::2]) <= x <= max(q[0::2]) and min(q[1::2]) <= y <= max(q[1::2]):
                    return True
    return False


def text_sections(result: dict) -> list[Section]:
    """Preserve body/caption text and tables; graphic-internal labels stay with the figure."""
    paragraphs = result.get("paragraphs") or []
    figures, tables = result.get("figures") or [], result.get("tables") or []
    caption_ids, table_ids = set(), set()
    regions, table_regions = [], []
    for f in figures:
        caption_ids |= _paragraph_ids((f.get("caption") or {}).get("elements"))
        for footnote in f.get("footnotes") or []:
            caption_ids |= _paragraph_ids(footnote.get("elements"))
        regions.extend(f.get("boundingRegions") or [])
    for table in tables:
        caption_ids |= _paragraph_ids((table.get("caption") or {}).get("elements"))
        table_regions.extend(table.get("boundingRegions") or [])
        table_ids |= _paragraph_ids(table.get("elements"))
        for cell in table.get("cells") or []:
            table_ids |= _paragraph_ids(cell.get("elements"))
    events = []
    for i, paragraph in enumerate(paragraphs):
        if i not in caption_ids and (i in table_ids or _inside(paragraph, regions + table_regions)):
            continue
        if paragraph.get("content", "").strip():
            events.append((_offset(paragraph), _page(paragraph), paragraph.get("role"), paragraph["content"]))
    # Captions need not be duplicated in the service's paragraphs array.
    existing = {" ".join(event[3].split()) for event in events}
    for figure in figures + tables:
        for caption in [figure.get("caption"), *(figure.get("footnotes") or [])]:
            if caption and caption.get("content") and " ".join(caption["content"].split()) not in existing:
                events.append((_offset(caption), _page(caption), "caption", caption["content"]))
                existing.add(" ".join(caption["content"].split()))
    for table in tables:
        rows = {}
        for cell in table.get("cells") or []:
            rows.setdefault(cell.get("rowIndex", 0), []).append((cell.get("columnIndex", 0), cell.get("content", "")))
        text = "\n".join(" | ".join(text for _, text in sorted(cells)) for _, cells in sorted(rows.items()))
        if text:
            events.append((_offset(table), _page(table), "table", text))
    events.sort(key=lambda e: e[0])
    sections, heading, buffer, current_page = [], None, [], None

    def flush():
        if buffer:
            sections.append(Section(heading, "\n\n".join(buffer), {"page": current_page}))
            buffer.clear()

    for _, page, role, text in events:
        if page != current_page or role in ("title", "sectionHeading"):
            flush()
            current_page = page
        if role in ("title", "sectionHeading"):
            heading = text
        buffer.append(text)
    flush()
    return sections


@dataclass
class PdfFigures:
    sections: list[Section]
    visuals: list[Visual]
    skipped: int
    page_count: int
    batches_analyzed: int
    batches_reused: int
    skipped_pages: dict[int, int] = field(default_factory=dict)


def _visual_fields(visual: Visual) -> dict:
    return {
        name: getattr(visual, name)
        for name in (
            "heading",
            "location",
            "context",
            "kind",
            "width",
            "height",
            "caption",
            "caption_association",
            "caption_location",
            "regions",
        )
    }


async def extract_pdf_figures(
    data: bytes,
    page_count: int,
    reader: LayoutReader,
    blobs: BlobStore,
    text_prefix: str,
    image_prefix: str,
    *,
    cap=50,
    min_pixels=16384,
    min_bytes=0,
    batch_pages=20,
    first_page=1,
    last_page=None,
    load_pixels=True,
) -> PdfFigures:
    if page_count > reader.max_pages:
        raise OcrError("layout_page_limit_exceeded", transient=False)
    last_page = page_count if last_page is None else last_page
    if not 1 <= first_page <= last_page <= page_count or batch_pages < 1:
        raise OcrError("layout_invalid_page_range", transient=False)
    key = digest(
        {
            "version": "pdf-source-assets-v1",
            "source": hashlib.sha256(data).hexdigest(),
            "reader": reader.cache_identity,
            "cap": cap,
            "minPixels": min_pixels,
            "minBytes": min_bytes,
            "batchPages": batch_pages,
            **({"pageRange": [first_page, last_page]} if first_page != 1 or last_page != page_count else {}),
        }
    )
    sections, visuals, skipped, analyzed, reused = [], [], 0, 0, 0
    skipped_pages = {}
    for first in range(first_page, last_page + 1, batch_pages):
        last = min(last_page, first + batch_pages - 1)
        path = f"{text_prefix}layout-{key}-{first}-{last}.json"
        batch = await load(blobs, path)
        if batch is None:
            result, operation = await reader.analyze_pages(data, first, last)
            body = await asyncio.to_thread(text_sections, result)
            selected, omitted, omitted_pages = [], 0, {}
            page_metadata = {p["pageNumber"]: p for p in result.get("pages") or []}
            for figure in result.get("figures") or []:
                source_page = _page(figure) or 0
                if len(visuals) + len(selected) >= cap:
                    omitted += 1
                    omitted_pages[source_page] = omitted_pages.get(source_page, 0) + 1
                    continue
                regions = [
                    {
                        **r,
                        "unit": page_metadata.get(r["pageNumber"], {}).get("unit"),
                        "pageWidth": page_metadata.get(r["pageNumber"], {}).get("width"),
                        "pageHeight": page_metadata.get(r["pageNumber"], {}).get("height"),
                    }
                    for r in figure.get("boundingRegions") or []
                ]
                if not regions or not figure.get("id"):
                    raise OcrError("layout_incomplete_figure", transient=True)
                page = regions[0]["pageNumber"]
                caption = figure_caption(result, figure)
                context = "\n\n".join(s.text for s in body if (s.location or {}).get("page") == page)[:2000]
                heading = next((s.heading for s in body if (s.location or {}).get("page") == page and s.heading), None)
                visual = Visual(
                    await reader.figure(operation, figure["id"]),
                    heading,
                    {"page": page, "figure": figure["id"]},
                    context,
                    "pdf_figure",
                    caption=caption.get("content"),
                    caption_association=caption.get("association"),
                    caption_location=caption.get("boundingRegions") or [],
                    regions=regions,
                )
                kept, rejected = await asyncio.to_thread(
                    normalize_visuals, [visual], min_pixels=min_pixels, min_bytes=min_bytes, cap=1
                )
                omitted += rejected
                if rejected:
                    omitted_pages[source_page] = omitted_pages.get(source_page, 0) + rejected
                if not kept:
                    continue
                pixels = hashlib.sha256(visual.data).hexdigest()
                image_path = image_prefix + "figure-" + digest([figure["id"], pixels]) + ".png"
                await blobs.create(image_path, visual.data, "image/png")
                selected.append({"blobPath": image_path, **_visual_fields(visual)})
            batch = await freeze(
                blobs,
                path,
                {
                    "sections": [{"heading": s.heading, "text": s.text, "location": s.location} for s in body],
                    "visuals": selected,
                    "skipped": omitted,
                    "skippedPages": omitted_pages,
                },
            )
            analyzed += 1
        else:
            reused += 1
        sections.extend(Section(**s) for s in batch["sections"])
        for item in batch["visuals"]:
            visual = Visual(
                await blobs.download(item["blobPath"]) if load_pixels else b"",
                **{k: v for k, v in item.items() if k != "blobPath"},
                blob_path=item["blobPath"],
            )
            visuals.append(visual)
        skipped += batch["skipped"]
        for page, count in (batch.get("skippedPages") or {}).items():
            skipped_pages[int(page)] = skipped_pages.get(int(page), 0) + count
    return PdfFigures(sections, visuals, skipped, page_count, analyzed, reused, skipped_pages)
