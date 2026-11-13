"""Opt-in DOCX source blocks, independently of the provider's passage vectors.

Only validated original/main-body mappings are called source structure. Unsupported
constructs fail closed. Provider headers/footers remain separately labelled evidence.
No layout, numbering label or semantic qualifier relationship is inferred.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
import unicodedata

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml import etree

from fli.checkpoints import digest

PROFILE = "microsoft-indexer-source-fidelity-v1"
SCHEMA = "fli.docx-source-blocks.v1"
EXTERNAL_ROLES = {"pageHeader": "PageHeader", "pageFooter": "PageFooter", "pageNumber": "PageNumber"}
LIMITATIONS = [
    "Original structure covers DOCX main-body paragraphs and tables only, not complete original-document structure.",
    "Headers/footers/page numbers are separately labelled provider-only evidence without original-region mapping.",
    "Numbering definitions are preserved; printed ordinal markers and semantic qualifier scope are not inferred.",
    "Footnotes, endnotes, revisions, text boxes, nested tables and visuals are unsupported.",
    "Fields retain their cached visible text and field properties; no field evaluation is performed.",
    "Existing passage vectors and locations are unchanged; expand search through outline section selectors.",
]


def require(condition, message):
    if not condition:
        raise ValueError("source fidelity: " + message)


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def local(tag):
    return tag.rsplit("}", 1)[-1]


def xml_value(node):
    """Lossless attribute/child model for numbering; no XML strings in evidence."""
    return {
        "name": local(node.tag),
        "qualifiedName": node.tag,
        "attributes": {local(k): v for k, v in node.attrib.items()},
        "qualifiedAttributes": dict(node.attrib),
        "text": node.text,
        "tail": node.tail,
        "children": [xml_value(child) for child in node],
    }


def numbering(paragraph, document):
    properties, origins = {}, {}
    levels = [("paragraph", paragraph._p.pPr)]
    style, seen = paragraph.style, set()
    while style is not None:
        require(style.style_id not in seen, "cyclic paragraph style ancestry")
        seen.add(style.style_id)
        levels.append(("style:" + style.style_id, style.element.pPr))
        style = style.base_style
    for origin, props in levels:
        num = None if props is None else props.numPr
        if num is not None:
            for key in ("numId", "ilvl") if origin == "paragraph" else ("numId",):
                value = getattr(num, key)
                if key not in properties and value is not None:
                    properties[key], origins[key] = int(value.val), origin
    if not properties or properties.get("numId") == 0:
        return None if not properties else {"properties": properties, "origins": origins, "status": "suppressed"}
    require("numId" in properties, "list level has no effective numbering identity")
    root = document.part.numbering_part.element
    instances = [n for n in root if n.tag == qn("w:num") and n.get(qn("w:numId")) == str(properties["numId"])]
    require(len(instances) == 1, "numbering instance missing or ambiguous")
    instance = instances[0]
    ids = instance.findall(qn("w:abstractNumId"))
    require(len(ids) == 1, "abstract numbering identity unavailable")
    abstract_id = ids[0].get(qn("w:val"))
    abstracts = [n for n in root if n.tag == qn("w:abstractNum") and n.get(qn("w:abstractNumId")) == abstract_id]
    require(len(abstracts) == 1, "abstract numbering definition missing or ambiguous")
    if "ilvl" not in properties:
        # Style numPr.ilvl is ignored by Word. The abstract level's pStyle link
        # selects the level instead; conflicting style ilvl cannot override it.
        style_ids = [origin.removeprefix("style:") for origin, _ in levels if origin.startswith("style:")]
        matches = [
            level
            for style_id in style_ids
            for level in abstracts[0].findall(qn("w:lvl"))
            if (link := level.find(qn("w:pStyle"))) is not None and link.get(qn("w:val")) == style_id
        ]
        require(len(matches) <= 1, "ambiguous style-linked numbering level")
        if matches:
            properties["ilvl"] = int(matches[0].get(qn("w:ilvl")))
            origins["ilvl"] = "abstract-pStyle"
        elif origins["numId"] == "paragraph":
            properties["ilvl"], origins["ilvl"] = 0, "word-default"
        else:
            # Effective level is not established; retain explicit unknown status.
            properties["ilvl"], origins["ilvl"] = None, "unresolved-style-level"
    require(properties["ilvl"] is None or 0 <= properties["ilvl"] <= 8, "invalid numbering level")
    return {
        "properties": properties,
        "origins": origins,
        "status": "unresolved_preserved_properties",
        "abstract": etree.tostring(abstracts[0], encoding="unicode", with_tail=False),
        "instance": etree.tostring(instance, encoding="unicode", with_tail=False),
    }


def paragraph_data(element, document, ordinal):
    unsupported = {
        "del",
        "ins",
        "moveFrom",
        "moveTo",
        "txbxContent",
        "footnoteReference",
        "endnoteReference",
        "altChunk",
    }
    require(not any(local(x.tag) in unsupported for x in element.iter()), "unsupported paragraph construct")
    runs, fields, field_stack = [], [], []
    for run in element.iter(qn("w:r")):
        pieces = []
        for node in run:
            kind = local(node.tag)
            require(node.tag == qn("w:" + kind), "unsupported run namespace")
            if kind == "t":
                pieces.append(node.text or "")
            elif kind == "tab":
                pieces.append("\t")
            elif kind in {"br", "cr"}:
                require(node.get(qn("w:type"), "textWrapping") == "textWrapping", "non-text run break")
                pieces.append("\n")
            elif kind == "noBreakHyphen":
                pieces.append("\u2011")
            elif kind == "softHyphen":
                pieces.append("\u00ad")
            elif kind == "fldChar":
                field_kind = node.get(qn("w:fldCharType"))
                fields.append(xml_value(node))
                if field_kind == "begin":
                    field_stack.append(False)
                elif field_kind == "separate":
                    require(field_stack and not field_stack[-1], "malformed cached field boundary")
                    field_stack[-1] = True
                elif field_kind == "end":
                    require(field_stack, "unbalanced cached field")
                    field_stack.pop()
                else:
                    require(False, "unsupported field boundary")
            elif kind == "instrText":
                require(field_stack and not field_stack[-1], "unbound field instruction")
                fields.append(xml_value(node))
            elif kind not in {"rPr", "lastRenderedPageBreak", "drawing", "pict"}:
                require(False, "unsupported run element")
        runs.append("".join(pieces))
    require(not field_stack, "field crosses paragraph boundary")
    fields.extend(xml_value(node) for node in element.iter(qn("w:fldSimple")))
    return {
        "ordinal": ordinal,
        "text": "".join(runs),
        "runs": runs,
        "numbering": numbering(Paragraph(element, document), document),
        **({"fields": fields, "fieldStatus": "cached-display-only"} if fields else {}),
    }


def body_children(element):
    for child in element:
        if child.tag == qn("w:sdt"):
            contents = child.findall(qn("w:sdtContent"))
            require(len(contents) == 1, "content control has no unique content")
            yield from body_children(contents[0])
        elif child.tag in {qn("w:p"), qn("w:tbl")}:
            yield child
        else:
            require(
                child.tag
                in {qn("w:" + name) for name in ("sectPr", "tcPr", "bookmarkStart", "bookmarkEnd", "proofErr")},
                "unsupported body/cell structure",
            )


def original_blocks(source):
    document = Document(io.BytesIO(source))
    defaults = document.styles.element.find(qn("w:docDefaults"))
    require(defaults is None or not list(defaults.iter(qn("w:numPr"))), "default-style numbering unsupported")
    require(
        not any(
            s.get(qn("w:type")) == "table" and list(s.iter(qn("w:numPr")))
            for s in document.styles.element.findall(qn("w:style"))
        ),
        "table-style numbering unsupported",
    )
    require(not document.element.body.findall(".//" + qn("w:tbl") + "/.//" + qn("w:tbl")), "nested tables unsupported")
    ordinals = {p: i for i, p in enumerate(document.element.body.iter(qn("w:p")), 1)}
    blocks, tables = [], []
    for child in body_children(document.element.body):
        if child.tag == qn("w:p"):
            blocks.append(
                {
                    "coordinate": {"kind": "paragraph", "paragraph": ordinals[child]},
                    "paragraphs": [paragraph_data(child, document, ordinals[child])],
                }
            )
            continue
        table_id = len(tables)
        rows = child.findall(qn("w:tr"))
        grid = child.find(qn("w:tblGrid"))
        require(grid is not None and len(grid) > 0, "table grid unavailable")
        width, cells, active = len(grid), [], {}
        for r, row in enumerate(rows):
            props = row.find(qn("w:trPr"))
            if props is not None:
                require(
                    all(props.find(qn("w:" + n)) is None for n in ("gridBefore", "gridAfter")),
                    "omitted table grid cells unsupported",
                )
            header_prop = props.find(qn("w:tblHeader")) if props is not None else None
            require(
                header_prop is None or header_prop.get(qn("w:val"), "1") in {"0", "1", "false", "true", "off", "on"},
                "invalid header-row boolean",
            )
            header = header_prop is not None and header_prop.get(qn("w:val"), "1") not in {"0", "false", "off"}
            column, next_active = 0, {}
            for tc in row.findall(qn("w:tc")):
                pr = tc.find(qn("w:tcPr"))
                span = pr.find(qn("w:gridSpan")) if pr is not None else None
                count = int(span.get(qn("w:val"))) if span is not None else 1
                require(count > 0 and column + count <= width, "table cell crosses grid")
                merge = pr.find(qn("w:vMerge")) if pr is not None else None
                paragraphs = list(body_children(tc))
                require(all(p.tag == qn("w:p") for p in paragraphs), "nested table unsupported")
                ps = [paragraph_data(p, document, ordinals[p]) for p in paragraphs]
                if merge is not None and merge.get(qn("w:val"), "continue") == "continue":
                    anchors = [active.get(c) for c in range(column, column + count)]
                    require(
                        anchors and anchors[0] is not None and all(a is anchors[0] for a in anchors),
                        "vertical merge has no unique anchor",
                    )
                    anchor = anchors[0]
                    require(
                        anchor["columnSpan"] == count and not any(p["text"] for p in ps),
                        "vertical continuation contains source text or mismatched width",
                    )
                    anchor["rowSpan"] += 1
                    anchor.setdefault("continuations", []).append({"row": r, "paragraphs": ps})
                    for c in range(column, column + count):
                        next_active[c] = anchor
                else:
                    require(merge is None or merge.get(qn("w:val")) == "restart", "invalid vertical merge")
                    cell = {
                        "row": r,
                        "column": column,
                        "rowSpan": 1,
                        "columnSpan": count,
                        "headerRow": header,
                        "paragraphs": ps,
                    }
                    cells.append(cell)
                    if merge is not None:
                        for c in range(column, column + count):
                            next_active[c] = cell
                column += count
            require(column == width, "incomplete table grid")
            active = next_active
        table = {"table": table_id, "rowCount": len(rows), "columnCount": width, "cells": cells}
        tables.append(table)
        for cell in cells:
            blocks.append(
                {
                    "coordinate": {
                        "kind": "tableCell",
                        "table": table_id,
                        **{k: cell[k] for k in ("row", "column", "rowSpan", "columnSpan", "headerRow")},
                    },
                    "paragraphs": cell["paragraphs"],
                    **({"mergeContinuations": cell["continuations"]} if cell.get("continuations") else {}),
                }
            )
    require(blocks and any(p["text"].strip() for b in blocks for p in b["paragraphs"]), "empty source body")
    return blocks, tables


class Spans:
    def __init__(self, content, units):
        require(isinstance(content, str), "provider text missing")
        require(units in {"unicodeCodePoint", "utf16CodeUnit"}, "unsupported source span units")
        self.content, self.units = content, units
        self.boundaries = {0: 0}
        offset = 0
        for i, char in enumerate(content, 1):
            offset += 2 if units == "utf16CodeUnit" and ord(char) > 0xFFFF else 1
            self.boundaries[offset] = i

    def bounds(self, item, *, empty=False):
        spans = item.get("spans")
        require(isinstance(spans, list) and len(spans) == 1, "single provider span required")
        start, length = spans[0].get("offset"), spans[0].get("length")
        require(
            type(start) is int and type(length) is int and start >= 0 and length >= (0 if empty else 1),
            "invalid provider span",
        )
        require(
            start in self.boundaries and start + length in self.boundaries, "span outside text or inside UTF16 pair"
        )
        return self.boundaries[start], self.boundaries[start + length]


def align(blocks, tables, analysis):
    spans = Spans(analysis.get("content"), analysis.get("stringIndexType"))
    require(analysis.get("contentFormat") == "markdown", "expected provider markdown")
    paragraphs = []
    for ordinal, value in enumerate(analysis.get("paragraphs", [])):
        start, end = spans.bounds(value)
        text = value.get("content")
        require(isinstance(text, str), "paragraph text unavailable")
        addressed = spans.content[start:end]
        role = value.get("role")
        wrapped = f'<!-- {EXTERNAL_ROLES[role]}="{text}" -->' if role in EXTERNAL_ROLES else None
        require(addressed == text or addressed == wrapped, "paragraph span does not address exact text")
        paragraphs.append(
            {
                "ordinal": ordinal,
                "start": start,
                "end": end,
                "text": text,
                "role": role,
                "sourceSpan": value["spans"][0],
            }
        )
    require(paragraphs == sorted(paragraphs, key=lambda p: p["start"]), "paragraphs out of source order")
    require(
        all(a["end"] <= b["start"] for a, b in zip(paragraphs, paragraphs[1:], strict=False)), "overlapping paragraphs"
    )
    provider_tables = analysis.get("tables", [])
    require(len(provider_tables) == len(tables), "table inventories differ")
    assigned, table_ranges = set(), []
    for table, provider in zip(tables, provider_tables, strict=True):
        require(
            (provider.get("rowCount"), provider.get("columnCount")) == (table["rowCount"], table["columnCount"]),
            "table dimensions differ",
        )
        tstart, tend = spans.bounds(provider)
        table_ranges.append((tstart, tend))
        by_coordinate = {}
        for cell in provider.get("cells", []):
            key = (cell.get("rowIndex"), cell.get("columnIndex"))
            require(key not in by_coordinate, "duplicate provider cell coordinate")
            by_coordinate[key] = cell
        require(len(by_coordinate) == len(table["cells"]), "cell inventories differ")
        cell_ranges = []
        for cell in table["cells"]:
            value = by_coordinate.get((cell["row"], cell["column"]))
            require(value is not None, "original cell mapping unavailable")
            require(
                (value.get("rowSpan", 1), value.get("columnSpan", 1)) == (cell["rowSpan"], cell["columnSpan"]),
                "merged-cell spans differ",
            )
            start, end = spans.bounds(value, empty=True)
            require(tstart <= start <= end <= tend, "cell outside table")
            cell_ranges.append((start, end))
            require(
                normalize(value.get("content", "")) == normalize(" ".join(p["text"] for p in cell["paragraphs"])),
                "original and provider cell text differ",
            )
            candidates = []
            for p in paragraphs:
                if p["start"] < end and p["end"] > start:
                    require(
                        start <= p["start"] < p["end"] <= end and p["role"] not in EXTERNAL_ROLES,
                        "paragraph crosses cell or has excluded role",
                    )
                    candidates.append(p)
            bind_paragraphs(cell["paragraphs"], candidates, assigned, spans.units)
            cell["providerKind"] = value.get("kind", "content")
        require(
            sorted(cell_ranges) == cell_ranges
            and all(a[1] <= b[0] for a, b in zip(cell_ranges, cell_ranges[1:], strict=False)),
            "provider cell spans overlap or reorder",
        )
    require(
        table_ranges == sorted(table_ranges)
        and all(a[1] <= b[0] for a, b in zip(table_ranges, table_ranges[1:], strict=False)),
        "provider table spans overlap or reorder",
    )
    outside = [p for p in paragraphs if p["ordinal"] not in assigned and p["role"] not in EXTERNAL_ROLES]
    native = [p for b in blocks if b["coordinate"]["kind"] == "paragraph" for p in b["paragraphs"]]
    bind_paragraphs(native, outside, assigned, spans.units)
    require(
        len(assigned) + sum(p["role"] in EXTERNAL_ROLES for p in paragraphs) == len(paragraphs),
        "unmapped provider paragraphs",
    )
    mapped = [p["provider"]["ordinal"] for b in blocks for p in b["paragraphs"] if "provider" in p]
    require(mapped == sorted(mapped), "original/provider global order differs")
    # No unannotated source words may disappear during reconstruction. Known
    # structural markup is allowed, but arbitrary tags or residual prose are not.
    residual, previous = [], 0
    for p in paragraphs:
        residual.append(spans.content[previous : p["start"]])
        previous = p["end"]
    residual.append(spans.content[previous:])
    rest = "".join(residual)
    rest = re.sub(r"</?(?:table|thead|tbody|tr|td|th)(?:\s+(?:rowspan|colspan)=\"[1-9][0-9]*\")*\s*>", "", rest)
    rest = re.sub(r"(?m)^ {0,3}#{1,6}[ \t]+", "", rest)
    require(not rest.strip(), "unannotated provider content or unsupported markup")
    return paragraphs, spans


def bind_paragraphs(native, provider, assigned, units):
    nonempty = [p for p in native if normalize(p["text"])]
    require(len(nonempty) == len(provider), "paragraph inventories differ")
    for original, match in zip(nonempty, provider, strict=True):
        require(normalize(original["text"]) == normalize(match["text"]), "paragraph mapping is not exact in order")
        require(match["ordinal"] not in assigned, "provider paragraph assigned twice")
        assigned.add(match["ordinal"])
        original["provider"] = {"ordinal": match["ordinal"], "span": match["sourceSpan"], "stringIndexType": units}
    for original in native:
        if not normalize(original["text"]):
            original["mappingStatus"] = "original_blank_paragraph"


def render(block):
    rendered = []
    for paragraph in block["paragraphs"]:
        num = paragraph["numbering"]
        text = html.escape(paragraph["text"], quote=False)
        if num and num["status"] != "suppressed":
            props = num["properties"]
            rendered.append(
                f'<list-item numId="{props["numId"]}" level="{props["ilvl"]}" marker="unresolved">'
                + text
                + "</list-item>"
            )
        else:
            rendered.append("<p>" + text + "</p>")
    body = "\n".join(rendered)
    location = block["coordinate"]
    if location["kind"] == "tableCell":
        body = (
            f'<table-cell table="{location["table"]}" row="{location["row"]}" column="{location["column"]}" '
            f'rowSpan="{location["rowSpan"]}" columnSpan="{location["columnSpan"]}">\n{body}\n</table-cell>'
        )
    return body


def block_location(block):
    ordinals = [p["ordinal"] for p in block["paragraphs"]]
    ordinals += [p["ordinal"] for c in block.get("mergeContinuations", []) for p in c["paragraphs"]]
    return {
        "paragraph": min(ordinals),
        "paragraphEnd": max(ordinals),
        "paragraphOrdinals": sorted(ordinals),
        "originalCoordinate": block["coordinate"],
    }


def source_sections(source, analysis, work):
    """Return a complete *versioned representation*, not complete original fidelity."""
    require(type(work.get("generation")) is int and work["generation"] > 0, "generation binding unavailable")
    require(work.get("sourceSha256") == hashlib.sha256(source).hexdigest(), "original source binding changed")
    blocks, tables = original_blocks(source)
    paragraphs, spans = align(blocks, tables, analysis)
    definitions = {}
    for block in blocks:
        all_paragraphs = block["paragraphs"] + [p for c in block.get("mergeContinuations", []) for p in c["paragraphs"]]
        for paragraph in all_paragraphs:
            number = paragraph["numbering"]
            if number and number["status"] != "suppressed":
                definition = {
                    "numId": number["properties"]["numId"],
                    "abstractXml": number.pop("abstract"),
                    "instanceXml": number.pop("instance"),
                }
                reference = digest(definition)
                definitions[reference] = definition
                number["definitionSha256"] = reference
    binding = {
        "schema": SCHEMA,
        "sourceSha256": hashlib.sha256(source).hexdigest(),
        "jobSha256": digest(work),
        "generation": work["generation"],
        "providerContentSha256": hashlib.sha256(spans.content.encode()).hexdigest(),
    }
    sections = []
    for block in blocks:
        location = block_location(block)
        sections.append(
            {
                "text": render(block),
                "heading": None,
                "contentKind": "text",
                "location": location,
                "sourceStructure": {**binding, "scope": "original-main-body", **block},
            }
        )
    # Provider-only extras are included at their original extraction positions, but
    # never acquire fabricated original coordinates or list/source relationships.
    ordered = []
    for index, section in enumerate(sections):
        mapped = [p["provider"]["ordinal"] for p in section["sourceStructure"]["paragraphs"] if "provider" in p]
        if mapped:
            position = min(mapped)
        else:
            # Blank native paragraphs stay in native order relative to mapped siblings.
            following = next(
                (p["provider"]["ordinal"] for b in blocks[index + 1 :] for p in b["paragraphs"] if "provider" in p),
                len(paragraphs),
            )
            position = following - 0.5
        ordered.append((position, index, section))
    for p in paragraphs:
        if p["role"] in EXTERNAL_ROLES:
            ordered.append(
                (
                    p["ordinal"],
                    -1,
                    {
                        "text": spans.content[p["start"] : p["end"]],
                        "heading": None,
                        "contentKind": "text",
                        "location": {},
                        "sourceStructure": {
                            **binding,
                            "scope": "provider-only",
                            "role": p["role"],
                            "originalMapping": "unavailable",
                            "providerSpan": p["sourceSpan"],
                            "stringIndexType": spans.units,
                        },
                    },
                )
            )
    result = [section for _, _, section in sorted(ordered)]
    result[0]["sourceStructure"]["numberingDefinitions"] = definitions
    return result, {
        "sourceStructure": SCHEMA,
        "sourceStructureScope": "docx-main-body-with-provider-only-extras",
        "sourceStructureLimitations": LIMITATIONS,
        "sourceParagraphs": sum(len(b["paragraphs"]) for b in blocks),
        "mergedContinuationParagraphs": sum(
            len(c["paragraphs"]) for b in blocks for c in b.get("mergeContinuations", [])
        ),
        "sourceTableCells": sum(len(t["cells"]) for t in tables),
        "providerOnlyParagraphs": sum(p["role"] in EXTERNAL_ROLES for p in paragraphs),
        "directPassageToSourceMapping": "unavailable",
        "sourceStructureRendering": "XML tags/attributes describe structure; enclosed text is source text. "
        "Numbered list items retain list identity/depth with unresolved printed markers.",
    }


def validate_sections(sections, document):
    """Validate the embedded binding on every delivery path after the Blob hash check."""
    require(document.get("ingestionJob", {}).get("profile", {}).get("id") == PROFILE, "source profile binding differs")
    expected = {
        "schema": SCHEMA,
        "generation": document["activeGeneration"],
        "sourceSha256": document["sha256"],
        "jobSha256": digest(document["ingestionJob"]),
    }
    hashes, used_definitions = set(), set()
    definitions = sections[0].get("sourceStructure", {}).get("numberingDefinitions") if sections else None
    require(
        isinstance(definitions, dict) and all(digest(v) == k for k, v in definitions.items()),
        "numbering definition catalog unavailable or corrupt",
    )
    for section in sections:
        value = section.get("sourceStructure")
        require(
            isinstance(value, dict) and all(value.get(k) == v for k, v in expected.items()),
            "source artifact binding differs",
        )
        content_hash = value.get("providerContentSha256")
        require(
            isinstance(content_hash, str) and re.fullmatch(r"[a-f0-9]{64}", content_hash),
            "provider binding unavailable",
        )
        hashes.add(content_hash)
        if value.get("scope") == "original-main-body":
            require(section["text"] == render(value), "rendered source differs from original blocks")
            require(all("".join(p["runs"]) == p["text"] for p in value["paragraphs"]), "original runs differ")
            for p in value["paragraphs"] + [p for c in value.get("mergeContinuations", []) for p in c["paragraphs"]]:
                number = p["numbering"]
                if number and number["status"] != "suppressed":
                    reference = number.get("definitionSha256")
                    require(reference in definitions, "numbering definition missing")
                    require(
                        definitions[reference]["numId"] == number["properties"]["numId"],
                        "numbering identity differs from definition",
                    )
                    used_definitions.add(reference)
            require(
                section.get("location") == block_location(value),
                "original location differs from embedded blocks",
            )
        else:
            require(
                value.get("scope") == "provider-only"
                and value.get("role") in EXTERNAL_ROLES
                and value.get("originalMapping") == "unavailable",
                "unknown source scope",
            )
    require(len(hashes) == 1, "provider source varies within artifact")
    require(set(definitions) == used_definitions, "unused numbering definition")


def evidence_structure(value):
    """Model view: preserve meaning/order, keep duplicate runs/definitions in audit storage."""
    # Source hash/schema/rendering contract are document-level fields. Original
    # coordinates remain in section.location. Do not repeat them for each cell.
    public = {k: value[k] for k in ("scope", "role", "originalMapping") if k in value}
    if "paragraphs" in value:
        if any(p["numbering"] and p["numbering"]["status"] != "suppressed" for p in value["paragraphs"]):
            public["numberingMarkers"] = "unresolved_preserved_properties"
        if any("fields" in p for p in value["paragraphs"]):
            public["fields"] = "cached-display-only"
        if "mergeContinuations" in value:
            public["mergeContinuations"] = [
                {"row": row["row"], "paragraphOrdinals": [p["ordinal"] for p in row["paragraphs"]]}
                for row in value["mergeContinuations"]
            ]
    return public
