"""Synthetic source contracts, never retrieval/answer-quality evidence."""

import copy
import hashlib
import io

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from fli.checkpoints import digest
from fli.ingestion import job, profile
from fli.source_fidelity import PROFILE, SCHEMA, Spans, numbering, original_blocks, source_sections, validate_sections


def element(name, **attrs):
    node = OxmlElement("w:" + name)
    for key, value in attrs.items():
        node.set(qn("w:" + key), str(value))
    return node


def numbered(document, paragraph, level=0, num_id=42):
    root = document.part.numbering_part.element
    if not any(n.get(qn("w:numId")) == str(num_id) for n in root.findall(qn("w:num"))):
        abstract = element("abstractNum", abstractNumId=num_id)
        for depth in (0, 4):
            lvl = element("lvl", ilvl=depth)
            lvl.extend([element("start", val=1), element("numFmt", val="decimal"), element("lvlText", val="%1.")])
            abstract.append(lvl)
        instance = element("num", numId=num_id)
        instance.append(element("abstractNumId", val=num_id))
        root.extend([abstract, instance])
    props = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    props.append(element("numId", val=num_id))
    props.append(element("ilvl", val=level))


def save(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def fixture(*, inside=False, repeated=False, unicode=False, merge=False):
    doc = Document()
    doc.add_paragraph("Policy 🌳" if unicode else "Policy")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Topic"
    cell = table.cell(0, 1)
    cell.text = "Conditions"
    numbered(doc, cell.add_paragraph("First"), 0)
    numbered(doc, cell.add_paragraph("First" if repeated else "Nested"), 4)
    final = cell.add_paragraph("Only when approved.")
    if inside:
        numbered(doc, final, 4)
    table.cell(1, 1).text = "End"
    if merge:
        table.cell(1, 0).merge(table.cell(1, 1))
    table.rows[0]._tr.get_or_add_trPr().append(element("tblHeader"))
    return doc


def analysis(*, repeated=False, unicode=False, merge=False, extra=False, units="unicodeCodePoint"):
    """An independently specified layout response for the invented source above."""
    content, paragraphs = "", []

    def units_length(value):
        return len(value.encode("utf-16-le")) // 2 if units == "utf16CodeUnit" else len(value)

    def append(value):
        nonlocal content
        start = units_length(content)
        content += value
        return {"offset": start, "length": units_length(value)}

    def para(value, role=None):
        if not value:
            return
        addressed = f'<!-- {role[0].upper() + role[1:]}="{value}" -->' if role else value
        item = {"content": value, "spans": [append(addressed)]}
        if role:
            item["role"] = role
        paragraphs.append(item)

    if extra:
        para("Provider header", "pageHeader")
        append("\n")
    para("Policy 🌳" if unicode else "Policy")
    append("\n")
    start = units_length(content)
    append("<table>\n")
    cells = []
    specifications = [
        (0, 0, ["Topic"], 1),
        (0, 1, ["Conditions", "First", "First" if repeated else "Nested", "Only when approved."], 1),
    ]
    specifications += [(1, 0, ["", "End"], 2)] if merge else [(1, 0, [""], 1), (1, 1, ["End"], 1)]
    for row, col, ps, colspan in specifications:
        append(f'<td colspan="{colspan}">')
        begin = units_length(content)
        for i, text in enumerate(ps):
            if i:
                append(" ")
            para(text)
        span = {"offset": begin, "length": units_length(content) - begin}
        cells.append(
            {
                "rowIndex": row,
                "columnIndex": col,
                "columnSpan": colspan,
                "kind": "columnHeader" if row == 0 else "content",
                "content": " ".join(ps),
                "spans": [span],
            }
        )
        append("</td>\n")
    append("</table>\n")
    table_span = {"offset": start, "length": units_length(content) - start}
    if extra:
        para("Provider footer", "pageFooter")
    return {
        "modelId": "prebuilt-layout",
        "contentFormat": "markdown",
        "stringIndexType": units,
        "content": content,
        "paragraphs": paragraphs,
        "tables": [{"rowCount": 2, "columnCount": 2, "cells": cells, "spans": [table_span]}],
        "pages": [{}],
    }


def work(source, *, generation=1):
    recipe = profile(PROFILE)
    recipe["embedding"]["modelRevision"] = "test-revision"
    return job(
        "library",
        "document",
        generation,
        {"sha256": hashlib.sha256(source).hexdigest(), "blobPath": "original"},
        recipe,
        0,
    )


def repair(**options):
    source = save(fixture(**{k: v for k, v in options.items() if k in {"inside", "repeated", "unicode", "merge"}}))
    return source_sections(source, analysis(**{k: v for k, v in options.items() if k != "inside"}), work(source))


def cell_section(sections):
    return next(
        s
        for s in sections
        if s["location"].get("originalCoordinate", {}).get("column") == 1
        and s["location"]["originalCoordinate"]["row"] == 0
    )


@pytest.mark.parametrize("inside", [False, True])
def test_qualifier_is_a_distinct_paragraph_with_exact_list_membership(inside):
    sections, coverage = repair(inside=inside)
    section = cell_section(sections)
    ps = section["sourceStructure"]["paragraphs"]
    assert len(ps) == 4 and ps[-1]["text"] == "Only when approved."
    assert (ps[-1]["numbering"] is not None) == inside
    assert ("<p>Only when approved.</p>" in section["text"]) != inside
    assert 'level="4" marker="unresolved"' in section["text"]
    assert coverage["directPassageToSourceMapping"] == "unavailable"
    assert coverage["sourceStructure"] == SCHEMA
    assert ps[1]["numbering"]["status"] == "unresolved_preserved_properties"


@pytest.mark.parametrize("units", ["unicodeCodePoint", "utf16CodeUnit"])
def test_unicode_units_repeated_paragraphs_and_provider_only_extras(units):
    sections, coverage = repair(unicode=True, repeated=True, extra=True, units=units)
    assert coverage["providerOnlyParagraphs"] == 2
    assert sections[0]["sourceStructure"]["scope"] == "provider-only"
    assert sections[-1]["sourceStructure"]["originalMapping"] == "unavailable"
    repeated = cell_section(sections)["sourceStructure"]["paragraphs"][1:3]
    assert repeated[0]["text"] == repeated[1]["text"]
    assert repeated[0]["provider"]["ordinal"] != repeated[1]["provider"]["ordinal"]
    assert {p["provider"]["stringIndexType"] for p in repeated} == {units}


@pytest.mark.parametrize("merge", [False, True])
def test_blank_and_merged_cells_and_headers_are_retained(merge):
    sections, _ = repair(merge=merge)
    cells = [s["sourceStructure"] for s in sections if s["sourceStructure"]["coordinate"]["kind"] == "tableCell"]
    assert len(cells) == (3 if merge else 4)
    assert cells[0]["coordinate"]["headerRow"]
    bottom = next(c for c in cells if c["coordinate"]["row"] == 1 and c["coordinate"]["column"] == 0)
    assert bottom["coordinate"]["columnSpan"] == (2 if merge else 1)
    if merge:
        assert bottom["paragraphs"][0]["text"] == "End"
    else:
        assert bottom["paragraphs"][0]["text"] == ""
        assert bottom["paragraphs"][0]["mappingStatus"] == "original_blank_paragraph"


@pytest.mark.parametrize(
    "damage",
    [
        "unit",
        "span-start",
        "span-end",
        "cross-cell",
        "duplicate-paragraph",
        "missing-paragraph",
        "reorder",
        "ambiguous-cell",
        "merge",
        "cell-text",
        "extra-body",
        "extra-unannotated",
        "table-overlap",
        "unknown-markup",
    ],
)
def test_untrusted_or_incomplete_provider_mappings_fail_closed(damage):
    source, value = save(fixture()), analysis()
    ps, table = value["paragraphs"], value["tables"][0]
    if damage == "unit":
        value["stringIndexType"] = "textElements"
    elif damage == "span-start":
        ps[1]["spans"][0]["offset"] = -1
    elif damage == "span-end":
        ps[1]["spans"][0]["length"] = len(value["content"]) + 1
    elif damage == "cross-cell":
        table["cells"][1]["spans"][0]["length"] = 1
    elif damage == "duplicate-paragraph":
        ps.insert(2, copy.deepcopy(ps[1]))
    elif damage == "missing-paragraph":
        ps.pop(2)
    elif damage == "reorder":
        ps[2], ps[3] = ps[3], ps[2]
    elif damage == "ambiguous-cell":
        table["cells"][1]["rowIndex"] = table["cells"][1]["columnIndex"] = 0
    elif damage == "merge":
        table["cells"][1]["columnSpan"] = 2
    elif damage == "cell-text":
        table["cells"][1]["content"] += " not original"
    elif damage == "extra-body":
        ps.append({"content": "Extra", "spans": [{"offset": len(value["content"]), "length": 5}]})
        value["content"] += "Extra"
    elif damage == "extra-unannotated":
        value["content"] += "Unannotated words"
    elif damage == "table-overlap":
        table["spans"][0]["length"] = len(value["content"]) + 1
    elif damage == "unknown-markup":
        value["content"] += "<untrusted>"
    with pytest.raises(ValueError, match="source fidelity"):
        source_sections(source, value, work(source))


def test_utf16_pair_boundaries_and_zero_length_semantics():
    spans = Spans("🌳x", "utf16CodeUnit")
    assert spans.bounds({"spans": [{"offset": 0, "length": 2}]}) == (0, 1)
    with pytest.raises(ValueError, match="UTF16"):
        spans.bounds({"spans": [{"offset": 1, "length": 1}]})
    with pytest.raises(ValueError, match="invalid provider span"):
        spans.bounds({"spans": [{"offset": 2, "length": 0}]})


def test_split_runs_content_controls_and_hyperlink_text_are_exact():
    doc = fixture()
    p = doc.tables[0].cell(0, 1).paragraphs[0]
    p.clear()
    p.add_run("Con")
    control = element("sdt")
    content = element("sdtContent")
    run = element("r")
    node = element("t")
    node.text = "dit"
    run.append(node)
    content.append(run)
    control.append(content)
    p._p.append(control)
    hyperlink = element("hyperlink")
    run = element("r")
    node = element("t")
    node.text = "ions"
    run.append(node)
    hyperlink.append(run)
    p._p.append(hyperlink)
    source = save(doc)
    sections, _ = source_sections(source, analysis(), work(source))
    assert cell_section(sections)["sourceStructure"]["paragraphs"][0]["runs"] == ["Con", "dit", "ions"]


def test_style_linked_level_wins_over_ignored_style_ilvl_and_numid_zero_suppresses():
    doc = Document()
    p = doc.add_paragraph("Item")
    numbered(doc, p)
    p._p.remove(p._p.pPr)
    style = doc.styles.add_style("TestList", WD_STYLE_TYPE.PARAGRAPH)
    num = style.element.get_or_add_pPr().get_or_add_numPr()
    num.extend([element("numId", val=42), element("ilvl", val=7)])
    abstract = next(n for n in doc.part.numbering_part.element if n.get(qn("w:abstractNumId")) == "42")
    abstract.findall(qn("w:lvl"))[1].append(element("pStyle", val=style.style_id))
    p.style = style
    result = numbering(p, doc)
    assert result["properties"]["ilvl"] == 4 and result["origins"]["ilvl"] == "abstract-pStyle"
    p._p.get_or_add_pPr().get_or_add_numPr().append(element("numId", val=0))
    assert numbering(p, doc)["status"] == "suppressed"


def test_numbering_restart_and_overrides_preserved_without_inventing_labels():
    doc = fixture()
    num = next(n for n in doc.part.numbering_part.element if n.get(qn("w:numId")) == "42")
    override = element("lvlOverride", ilvl=0)
    override.append(element("startOverride", val=7))
    num.append(override)
    abstract = next(n for n in doc.part.numbering_part.element if n.get(qn("w:abstractNumId")) == "42")
    abstract.findall(qn("w:lvl"))[1].append(element("lvlRestart", val=0))
    result = numbering(doc.tables[0].cell(0, 1).paragraphs[1], doc)
    assert etree.fromstring(result["instance"])[-1][0].get(qn("w:val")) == "7"
    assert etree.fromstring(result["abstract"])[-1][-1].tag == qn("w:lvlRestart")
    assert result["status"] == "unresolved_preserved_properties"


def test_cached_fields_are_retained_but_not_evaluated_and_unbalanced_rejected():
    doc = fixture()
    p = doc.paragraphs[0]
    p.clear()
    begin = p.add_run()
    begin._r.append(element("fldChar", fldCharType="begin"))
    code = p.add_run()
    instruction = element("instrText")
    instruction.text = " REF example "
    code._r.append(instruction)
    middle = p.add_run()
    middle._r.append(element("fldChar", fldCharType="separate"))
    p.add_run("Policy")
    end = p.add_run()
    end._r.append(element("fldChar", fldCharType="end"))
    source = save(doc)
    sections, _ = source_sections(source, analysis(), work(source))
    block = sections[0]["sourceStructure"]["paragraphs"][0]
    assert block["text"] == "Policy" and block["fieldStatus"] == "cached-display-only"
    assert any(n["text"] == " REF example " for n in block["fields"])
    end._r.remove(end._r[0])
    with pytest.raises(ValueError, match="field crosses"):
        original_blocks(save(doc))


@pytest.mark.parametrize("tag", ["ins", "del", "footnoteReference", "txbxContent"])
def test_unsupported_original_constructs_do_not_silently_flatten(tag):
    doc = fixture()
    doc.paragraphs[0]._p.append(element(tag))
    with pytest.raises(ValueError, match="unsupported"):
        original_blocks(save(doc))


def test_artifact_binds_source_job_generation_and_rendered_block_integrity():
    source = save(fixture())
    request = work(source)
    sections, _ = source_sections(source, analysis(), request)
    doc = {"activeGeneration": 1, "sha256": request["sourceSha256"], "ingestionJob": request}
    validate_sections(sections, doc)
    assert sections[0]["sourceStructure"]["jobSha256"] == digest(request)
    for key, value in [
        ("activeGeneration", 2),
        ("sha256", "wrong"),
        ("ingestionJob", {**request, "documentId": "foreign"}),
    ]:
        with pytest.raises(ValueError, match="binding"):
            validate_sections(sections, {**doc, key: value})
    changed = copy.deepcopy(sections)
    changed[0]["text"] += " invented"
    with pytest.raises(ValueError, match="rendered"):
        validate_sections(changed, doc)


def test_profile_is_opt_in_and_preserves_provider_embedding_and_chunk_recipe():
    original, candidate = profile("microsoft-indexer-layout-v1"), profile(PROFILE)
    for key in ("provider", "extraction", "chunking", "embedding", "workflowVersion"):
        assert original[key] == candidate[key]
    assert profile()["id"] == "current-v1"
    assert not candidate["capabilities"]["completeProviderText"]
    assert not candidate["capabilities"]["authoredHierarchy"]
    with pytest.raises(ValueError, match="frozen component"):
        profile(PROFILE, controlled=True)


def test_vertical_merge_retains_blank_continuation_without_claiming_intervening_paragraphs():
    from fli.reading import region_matches

    doc = fixture()
    doc.tables[0].cell(0, 0).merge(doc.tables[0].cell(1, 0))
    source, value = save(doc), analysis()
    value["tables"][0]["cells"][0]["rowSpan"] = 2
    value["tables"][0]["cells"].pop(2)
    sections, coverage = source_sections(source, value, work(source))
    anchor = sections[1]
    assert anchor["location"]["originalCoordinate"]["rowSpan"] == 2
    assert anchor["sourceStructure"]["mergeContinuations"][0]["paragraphs"][0]["text"] == ""
    ordinals = anchor["location"]["paragraphOrdinals"]
    assert len(ordinals) == 2 and coverage["mergedContinuationParagraphs"] == 1
    assert region_matches(anchor, {"paragraph": ordinals[-1]}, sections)
    assert not region_matches(anchor, {"paragraph": ordinals[0] + 1}, sections)


@pytest.mark.parametrize(
    "value,expected", [("0", False), ("false", False), ("off", False), ("1", True), ("true", True), ("on", True)]
)
def test_table_header_onoff_semantics(value, expected):
    doc = fixture()
    doc.tables[0].rows[0]._tr.trPr.find(qn("w:tblHeader")).set(qn("w:val"), value)
    blocks, _ = original_blocks(save(doc))
    assert blocks[1]["coordinate"]["headerRow"] is expected


def test_unsupported_default_or_table_style_numbering_is_not_proven_unnumbered():
    doc = fixture()
    defaults = doc.styles.element.find(qn("w:docDefaults"))
    defaults.append(element("numPr"))
    with pytest.raises(ValueError, match="default-style numbering"):
        original_blocks(save(doc))
    defaults.remove(defaults[-1])
    style = next(s for s in doc.styles.element.findall(qn("w:style")) if s.get(qn("w:type")) == "table")
    style.append(element("numPr"))
    with pytest.raises(ValueError, match="table-style numbering"):
        original_blocks(save(doc))


def test_numbering_namespaces_are_preserved_and_foreign_run_text_fails_closed():
    doc = fixture()
    abstract = next(n for n in doc.part.numbering_part.element if n.get(qn("w:abstractNumId")) == "42")
    abstract.set("{urn:extension}restartNumberingAfterBreak", "1")
    abstract.set("{urn:other}restartNumberingAfterBreak", "0")
    num = numbering(doc.tables[0].cell(0, 1).paragraphs[1], doc)
    parsed = etree.fromstring(num["abstract"])
    assert parsed.get("{urn:extension}restartNumberingAfterBreak") == "1"
    assert parsed.get("{urn:other}restartNumberingAfterBreak") == "0"
    run = doc.paragraphs[0].add_run()._r
    foreign = etree.Element("{urn:not-word}t")
    foreign.text = "Not Word text"
    run.append(foreign)
    with pytest.raises(ValueError, match="unsupported run namespace"):
        original_blocks(save(doc))


def test_numbering_catalog_hashes_and_reference_membership_are_checked():
    source = save(fixture())
    request = work(source)
    sections, _ = source_sections(source, analysis(), request)
    document = {"activeGeneration": 1, "sha256": request["sourceSha256"], "ingestionJob": request}
    bad = copy.deepcopy(sections)
    catalog = bad[0]["sourceStructure"]["numberingDefinitions"]
    next(iter(catalog.values()))["abstractXml"] += "changed"
    with pytest.raises(ValueError, match="catalog"):
        validate_sections(bad, document)
    bad = copy.deepcopy(sections)
    cell_section(bad)["sourceStructure"]["paragraphs"][1]["numbering"]["definitionSha256"] = "0" * 64
    with pytest.raises(ValueError, match="definition missing"):
        validate_sections(bad, document)
