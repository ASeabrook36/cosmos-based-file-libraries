"""Offline, source-bound diagnosis of a DOCX table cell versus archived layout output.

No cloud calls, model calls, application mutation or semantic grading. Raw source
text is written only to a new private output file; stdout is an allowlisted receipt.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from scripts.retrieval_r0_corpus import normalize


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private_path(path):
    resolved = Path(path).resolve()
    require(not any((p / ".git").exists() for p in (resolved, *resolved.parents)), "private path is inside Git")
    return resolved


def checked(reference):
    path = private_path(reference["path"])
    require(path.is_file() and path.stat().st_size <= 50_000_000, "input missing or too large")
    data = path.read_bytes()
    require(sha(data) == reference["sha256"], "input hash changed")
    return data


def xml_text(element):
    parts = []
    for node in element.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(node.text or "")
        elif tag in {"p", "tab", "br", "cr"}:
            parts.append(" ")
    return "".join(parts).strip()


def number_properties(paragraph):
    """Retain numbering properties without inventing rendered ordinal labels."""
    levels = [("paragraph", paragraph._p.pPr)]
    style, seen = paragraph.style, set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        levels.append(("style", style.element.pPr))
        style = style.base_style
    result = {}
    for origin, props in levels:
        number = None if props is None else props.numPr
        if number is None:
            continue
        for key in ("numId", "ilvl"):
            value = getattr(number, key)
            if key not in result and value is not None:
                result[key] = int(value.val)
                result[key + "Origin"] = origin
    if result.get("numId") == 0:
        return None
    return result or None


class CellParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "th"}:
            cell = {"text": [], "tags": Counter()}
            self.cells.append(cell)
            self.stack.append(cell)
        for cell in self.stack:
            if tag not in {"td", "th"}:
                cell["tags"][tag] += 1
            if tag in {"p", "br", "li", "div"}:
                cell["text"].append(" ")

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            if self.stack:
                self.stack.pop()
        elif tag in {"p", "li", "div"}:
            for cell in self.stack:
                cell["text"].append(" ")

    def handle_data(self, data):
        for cell in self.stack:
            cell["text"].append(data)


def matching_html_cells(content, expected):
    parser = CellParser()
    parser.feed(content)
    parser.close()
    return [cell for cell in parser.cells if normalize("".join(cell["text"])) == expected]


def span_bounds(item):
    spans = item.get("spans")
    require(isinstance(spans, list) and len(spans) == 1, "single bounded source span required")
    start, length = spans[0]["offset"], spans[0]["length"]
    require(type(start) is int and start >= 0 and type(length) is int and length > 0, "invalid source span")
    return start, start + length


def probe(original, extraction, result, sections, locator, expected_text_sha256):
    require(set(locator) == {"table", "row", "column"}, "exact table coordinate required")
    require(all(type(v) is int and v >= 0 for v in locator.values()), "nonnegative table coordinates required")
    document = Document(io.BytesIO(original))
    cell = document.tables[locator["table"]].rows[locator["row"]].cells[locator["column"]]
    require(len(list(cell._tc.iter(qn("w:tbl")))) == 0, "nested table requires a separate mapping")
    native = [
        {"ordinal": i, "text": xml_text(p._p), "numbering": number_properties(p)} for i, p in enumerate(cell.paragraphs)
    ]
    expected = normalize(xml_text(cell._tc))
    require(sha(expected.encode()) == expected_text_sha256, "original region text hash changed")
    require(base64.b64decode(extraction["sourceBase64"], validate=True) == original, "extraction source differs")
    require(base64.b64decode(result["sourceBase64"], validate=True) == original, "result source differs")
    analysis = extraction["analysis"]["analyzeResult"]
    require(extraction["analysis"] == extraction["analysisOutput"]["body"], "raw analysis copies differ")
    require(result["analysis"] == extraction["analysis"], "workflow result changed analysis")
    require(analysis["stringIndexType"] == "unicodeCodePoint", "unsupported source span units")
    content = analysis["content"]
    require(analysis["contentFormat"] == "markdown", "expected archived markdown content")
    require(
        len(sections) == 1 and sections[0]["text"] == content and sections[0].get("contentKind") == "text",
        "published canonical source differs",
    )
    matches = matching_html_cells(content, expected)
    require(len(matches) == 1, "target rendered cell is missing or ambiguous")
    matching_annotations = [
        c for table in analysis["tables"] for c in table["cells"] if normalize(c["content"]) == expected
    ]
    require(len(matching_annotations) == 1, "target annotated cell is missing or ambiguous")
    annotation = matching_annotations[0]
    start, end = span_bounds(annotation)
    require(end <= len(content), "cell span outside content")
    mapped = []
    for ordinal, paragraph in enumerate(analysis["paragraphs"]):
        pstart, pend = span_bounds(paragraph)
        if pstart < end and pend > start:
            require(start <= pstart < pend <= end, "paragraph crosses target cell boundary")
            require(content[pstart:pend] == paragraph["content"], "paragraph span does not address exact source text")
            mapped.append({"providerOrdinal": ordinal, "start": pstart, "end": pend, "text": paragraph["content"]})
    require(mapped == sorted(mapped, key=lambda p: p["start"]), "provider paragraphs out of source order")
    require(
        all(a["end"] <= b["start"] for a, b in zip(mapped, mapped[1:], strict=False)), "overlapping provider paragraphs"
    )
    require(len(mapped) == len(native), "paragraph inventories differ")
    require(
        all(normalize(a["text"]) == normalize(b["text"]) for a, b in zip(native, mapped, strict=True)),
        "paragraph text cannot map one-to-one to original",
    )
    require(normalize(" ".join(p["text"] for p in mapped)) == expected, "mapped source text incomplete")
    tags = matches[0]["tags"]
    list_count = sum(bool(p["numbering"]) for p in native)
    boundary_tags = sum(tags[tag] for tag in ("p", "li", "br", "div", "ol", "ul"))
    summary = {
        "originalParagraphs": len(native),
        "originalNumberedParagraphs": list_count,
        "originalUnnumberedParagraphs": len(native) - list_count,
        "numberedParagraphLevels": sorted(
            {p["numbering"]["ilvl"] for p in native if p["numbering"] and "ilvl" in p["numbering"]}
        ),
        "providerParagraphsMappedExactlyInOrder": len(mapped),
        "providerParagraphSpansAddressExactText": True,
        "providerCellNormalizedTextMatchesOriginal": True,
        "providerCellInternalBoundaryTagCount": boundary_tags,
        "providerCellListTagCount": tags["li"] + tags["ol"] + tags["ul"],
        "publishedSections": len(sections),
        "publishedCanonicalTextEqualsProviderContent": True,
        "publishedSectionHasNodeId": bool(sections[0].get("location", {}).get("nodeId")),
        "paragraphBoundaryLossReproduced": len(native) > 1 and boundary_tags == 0,
        "publishedCellHasNoListTags": list_count > 0 and tags["li"] + tags["ol"] + tags["ul"] == 0,
        "semanticQualifierScopeAutomaticallyGraded": False,
    }
    return summary, {"originalParagraphs": native, "providerParagraphs": mapped, "sourceCellAnnotation": annotation}


def execute(inputs_path, output_path):
    envelope = json.loads(private_path(inputs_path).read_text())
    require(envelope["schema"] == "fli.source-structure-probe.inputs.v1", "unknown input schema")
    source_id = envelope["sourceId"]
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", source_id) is not None, "public source label required")
    data = {name: checked(envelope["files"][name]) for name in ("original", "extraction", "result", "sections")}
    summary, detail = probe(
        data["original"],
        json.loads(data["extraction"]),
        json.loads(data["result"]),
        json.loads(data["sections"]),
        envelope["locator"],
        envelope["normalizedTextSha256"],
    )
    public = {
        "schema": "fli.source-structure-probe.receipt.v1",
        "status": "complete",
        "sourceId": source_id,
        "locator": envelope["locator"],
        "normalizedTextSha256": envelope["normalizedTextSha256"],
        "inputSha256": {name: sha(value) for name, value in data.items()},
        "probeSourceSha256": sha(Path(__file__).read_bytes()),
        "observations": summary,
        "cloudCalls": 0,
        "modelCalls": 0,
        "applicationChanges": False,
        "limits": [
            "One already-consumed DOCX cell; no generalized extraction-quality claim.",
            "Text equality and paragraph mapping do not establish semantic qualifier scope.",
            "Original numbering properties retained privately, not expanded into guessed ordinal labels.",
            "No repair applied to provider text, application representation or frozen comparison.",
        ],
    }
    output = private_path(output_path)
    with output.open("x") as file:
        json.dump({"public": public, "privateDetail": detail}, file, indent=2)
        file.write("\n")
    output.chmod(0o600)
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(execute(args.inputs, args.output), indent=2))


if __name__ == "__main__":
    main()
