"""Invented layout-service responses; no page/object classification is simulated."""

import copy
import io

from PIL import Image, ImageDraw


def png():
    image = Image.new("RGB", (640, 400), "white")
    ImageDraw.Draw(image).line([(30, 350), (300, 200), (600, 40)], fill="blue", width=5)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def region(page=1, box=(1, 2, 7, 6)):
    x0, y0, x1, y1 = box
    return {"pageNumber": page, "polygon": [x0, y0, x1, y0, x1, y1, x0, y1]}


def paragraph(text, offset, *, page=1, box=(1, 7, 7, 8), role=None):
    return {
        "content": text,
        "spans": [{"offset": offset, "length": len(text)}],
        "boundingRegions": [region(page, box)],
        "role": role,
    }


def result(first=1, last=2, *, figure_page=1, caption=True, body=True):
    paragraphs = []
    if body:
        for page in range(first, last + 1):
            paragraphs.append(
                paragraph(f"Investment and productivity explain expansion on page {page}.", page * 100, page=page)
            )
    figures = []
    if first <= figure_page <= last:
        item = {"id": f"{figure_page}.1", "boundingRegions": [region(figure_page)], "elements": []}
        paragraphs.append(paragraph("GDP growth 2020-2025", figure_page * 100 + 10, page=figure_page, box=(2, 3, 5, 4)))
        if caption:
            item["caption"] = paragraph(
                "Figure 1: GDP growth between 2020 and 2025.",
                figure_page * 100 + 20,
                page=figure_page,
                box=(1, 6.1, 7, 6.5),
            )
            item["caption"]["elements"] = [f"/paragraphs/{len(paragraphs)}"]
            paragraphs.append(copy.deepcopy(item["caption"]))
        figures.append(item)
    return {
        "pages": [{"pageNumber": p, "width": 8.5, "height": 11, "unit": "inch"} for p in range(first, last + 1)],
        "paragraphs": paragraphs,
        "figures": figures,
    }


class FakeLayout:
    max_pages = 2000
    cache_identity = "invented-layout-v1"

    def __init__(self, *, figure_page=1, caption=True, body=True):
        self.figure_page, self.caption, self.body = figure_page, caption, body
        self.calls, self.figure_calls = [], []
        self.crop = png()

    async def analyze_pages(self, data, first, last):
        self.calls.append((first, last))
        return result(first, last, figure_page=self.figure_page, caption=self.caption, body=self.body), "operation"

    async def figure(self, operation, figure_id):
        self.figure_calls.append(figure_id)
        return self.crop
