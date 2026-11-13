"""Original structure and deterministic navigation, without cloud or model claims."""

import copy
import hashlib
import io

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from fli.chunking import Chunker, default_tokenizer
from fli.granular_source import (
    PROFILE,
    SCHEMA,
    build_source,
    navigation,
    node_indexes,
    plain_sections,
    validate_sections,
)
from fli.source_fidelity import original_blocks


def xml(name, **attributes):
    node = OxmlElement("w:" + name)
    for key, value in attributes.items():
        node.set(qn("w:" + key), str(value))
    return node


def numbered(document, paragraph, level=0):
    numbering = document.part.numbering_part.element
    if not any(n.get(qn("w:numId")) == "99" for n in numbering.findall(qn("w:num"))):
        abstract = xml("abstractNum", abstractNumId=99)
        for depth in (0, 1):
            definition = xml("lvl", ilvl=depth)
            definition.extend([xml("start", val=1), xml("numFmt", val="decimal"), xml("lvlText", val="%1.")])
            abstract.append(definition)
        instance = xml("num", numId=99)
        instance.append(xml("abstractNumId", val=99))
        numbering.extend([abstract, instance])
    paragraph._p.get_or_add_pPr().get_or_add_numPr().extend([xml("numId", val=99), xml("ilvl", val=level)])


def bytes_for(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def bind(source, file_type="docx"):
    work = {
        "libraryId": "library",
        "documentId": "document",
        "generation": 3,
        "sourceSha256": hashlib.sha256(source).hexdigest(),
        "profile": {"id": PROFILE, "version": 1},
    }
    record = {
        "id": "document",
        "libraryId": "library",
        "fileName": "Policy." + file_type,
        "fileType": file_type,
        "sha256": work["sourceSha256"],
        "activeGeneration": 3,
        "ingestionJob": work,
    }
    sections, coverage = build_source(source, work, record["fileName"], file_type)
    return sections, coverage, record


def source_document():
    document = Document()
    document.add_paragraph("Preamble & its scope.")
    document.add_heading("Requirements", level=1)
    document.add_paragraph("First instruction.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Topic"
    table.cell(0, 1).text = "Conditions"
    numbered(document, table.cell(0, 1).add_paragraph("First list item."))
    numbered(document, table.cell(0, 1).add_paragraph("Nested item."), 1)
    table.cell(0, 1).add_paragraph("Only when approved.")
    table.cell(1, 0).text = ""
    table.cell(1, 1).text = "Body cell styled as heading"
    table.cell(1, 1).paragraphs[0].style = document.styles["Heading 1"]
    table.rows[0]._tr.get_or_add_trPr().append(xml("tblHeader"))
    derived = document.styles.add_style("Detailed requirement", WD_STYLE_TYPE.PARAGRAPH)
    derived.base_style = document.styles["Heading 2"]
    document.add_paragraph("Exceptions", style=derived)
    document.add_paragraph("Second instruction.")
    suppressed = document.add_paragraph("Not another section", style=derived)
    suppressed._p.get_or_add_pPr().append(xml("outlineLvl", val=9))
    document.add_heading("Closing", level=1)
    document.add_paragraph("Final instruction.")
    return document


def test_authored_hierarchy_preserves_paragraphs_cells_lists_and_selects_own_section_or_subtree():
    source = bytes_for(source_document())
    sections, coverage, record = bind(source)
    validate_sections(sections, record)
    tree = navigation(sections)
    by_title = {n["title"]: n for n in tree["nodes"] if n["kind"] == "section"}
    assert set(by_title) == {"Requirements", "Exceptions", "Closing"}
    assert by_title["Exceptions"]["parentId"] == by_title["Requirements"]["id"]
    assert by_title["Exceptions"]["headingLevel"] == 2
    assert by_title["Exceptions"]["headingOrigin"] == "style:Heading2"
    own = node_indexes(tree, [by_title["Requirements"]["id"]], subtree=False)
    subtree = node_indexes(tree, [by_title["Requirements"]["id"]])
    own_text = "\n".join(plain_sections(sections)[i].text for i in own)
    subtree_text = "\n".join(plain_sections(sections)[i].text for i in subtree)
    assert "Only when approved." in own_text and "Second instruction." not in own_text
    assert "Second instruction." in subtree_text and "Final instruction." not in subtree_text
    assert len(own) < len(subtree) < len(sections)
    assert node_indexes(tree, ["n0"]) == list(range(len(sections)))
    assert node_indexes(tree, ["n0"], subtree=False) == [0]
    assert node_indexes(tree, [by_title["Requirements"]["id"], "t0:r0:c1"]) == subtree
    cell = node_indexes(tree, ["t0:r0:c1"])
    assert [plain_sections(sections)[i].text for i in cell] == [
        "Conditions",
        "First list item.",
        "Nested item.",
        "Only when approved.",
    ]
    qualifier = sections[cell[-1]]
    assert qualifier["sourceStructure"]["paragraphs"][0]["numbering"] is None
    assert sections[cell[2]]["sourceStructure"]["paragraphs"][0]["numbering"]["properties"]["ilvl"] == 1
    assert 'marker="unresolved"' in sections[cell[2]]["text"]
    assert "<p>Only when approved.</p>" in qualifier["text"]
    assert coverage["sourceStructure"] == SCHEMA
    assert coverage["sourceTableCells"] == 4
    assert coverage["visuals"] == "unavailable"
    assert all("page" not in s["location"] for s in sections)
    original, _ = original_blocks(source)
    expected = [p["text"] for block in original for p in block["paragraphs"]]
    assert [s.text for s in plain_sections(sections)] == expected
    assert len(sections) == len(list(Document(io.BytesIO(source)).element.body.iter(qn("w:p"))))
    assert len(sections[0]["sourceStructure"]["numberingDefinitions"]) == 1


def test_each_paragraph_can_be_selected_and_carries_exact_parent_and_cell_relationships():
    sections, _, record = bind(bytes_for(source_document()))
    tree = navigation(sections)
    for index, section in enumerate(sections):
        atom = section["location"]["sourceAtomId"]
        assert node_indexes(tree, [atom]) == [index]
        node = next(n for n in tree["nodes"] if n["id"] == atom)
        assert node["containingSectionId"] == section["location"]["nodeId"]
        assert node["sourceCoordinate"] == section["location"]["originalCoordinate"]
        assert section["sourceStructure"]["sourceSha256"] == record["sha256"]
    blank = next(s for s in sections if s["sourceStructure"]["paragraphs"][0]["text"] == "")
    assert blank["text"].endswith("<p></p>\n</table-cell>")
    assert blank["location"]["originalCoordinate"]["column"] == 0


def test_merge_continuations_and_cell_topology_are_preserved_in_original_paragraph_order():
    document = Document()
    document.add_heading("Merged records", 1)
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "First"
    table.cell(0, 0).merge(table.cell(1, 0))
    table.cell(0, 1).text = "Across"
    table.cell(0, 1).merge(table.cell(0, 2))
    table.cell(2, 2).text = "Last"
    source = bytes_for(document)
    sections, _, record = bind(source)
    validate_sections(sections, record)
    assert [s["location"]["paragraph"] for s in sections] == list(range(1, len(sections) + 1))
    tree = navigation(sections)
    vertical = node_indexes(tree, ["t0:r0:c0"])
    assert len(vertical) == 2
    assert [plain_sections(sections)[i].text for i in vertical] == ["First", ""]
    assert sections[vertical[1]]["sourceStructure"]["mergeContinuationRow"] == 1
    assert all(sections[i]["location"]["originalCoordinate"]["rowSpan"] == 2 for i in vertical)
    horizontal = node_indexes(tree, ["t0:r0:c1"])
    assert sections[horizontal[0]]["location"]["originalCoordinate"]["columnSpan"] == 2


def test_chunker_mapping_is_containing_source_atom_without_claiming_normalized_text_offsets():
    sections, coverage, _ = bind(bytes_for(source_document()))
    chunker = Chunker(default_tokenizer(), target=500, overlap=50)
    plain = plain_sections(sections)
    chunks = chunker.chunk(plain)
    by_id = {s.location["sourceAtomId"]: s for s in plain}
    for chunk in chunks:
        original = by_id[chunk.location["sourceAtomId"]]
        assert chunk.location == original.location
        assert chunk.heading == original.heading
        assert "charStart" not in chunk.location and "charEnd" not in chunk.location
        assert "<table-cell" not in chunk.text and "<list-item" not in chunk.text
    assert len(chunks) == sum(bool(s.text.strip()) for s in plain)
    assert coverage["directPassageToSourceMapping"] == "verified_containing_source_atom"


def test_lossless_markdown_atx_preserves_unicode_fences_and_authored_chapter_labels():
    source = (
        "Preamble 🌳\r\n\r\n# Chapter One\r\nFirst.\r\n\r\n## Detail\n"
        "```md\n# Not a heading\n```\n\nFinal.\n# Closing\nEnd.\n"
    )
    sections, coverage, record = bind(source.encode(), "md")
    validate_sections(sections, record)
    assert "".join(s.text for s in plain_sections(sections)) == source
    tree = navigation(sections)
    authored = [n for n in tree["nodes"] if n["kind"] in {"section", "chapter"}]
    assert [n["title"] for n in authored] == ["Chapter One", "Detail", "Closing"]
    assert authored[0]["kind"] == "chapter"
    assert authored[1]["parentId"] == authored[0]["id"]
    assert len(node_indexes(tree, [authored[0]["id"]], subtree=False)) == 1
    assert len(node_indexes(tree, [authored[0]["id"]])) == 2
    assert coverage["sourceStructureScope"] == "markdown-utf8-atx"
    for section in sections:
        location = section["location"]
        assert source[location["charStart"] : location["charEnd"]] == section["text"]
        assert "paragraph" not in location and "page" not in location
    assert all(n["kind"] != "tableCell" for n in tree["nodes"])


@pytest.mark.parametrize(
    "mutation",
    [
        "profile",
        "library",
        "job",
        "source",
        "generation",
        "file_type",
        "name",
        "missing_catalog",
        "duplicate_catalog",
        "tree_cycle",
        "tree_missing",
        "tree_duplicate",
        "leaf_index",
        "leaf_parent",
        "atom_id",
        "coordinate",
        "render",
        "runs",
        "numbering",
        "numbering_unused",
        "paragraph_order",
        "heading_owner",
        "heading_title",
    ],
)
def test_tampered_source_and_navigation_fail_closed(mutation):
    sections, _, record = bind(bytes_for(source_document()))
    if mutation == "profile":
        record["ingestionJob"]["profile"]["id"] = "current-v1"
    elif mutation == "library":
        record["libraryId"] = "another-library"
    elif mutation == "job":
        record["ingestionJob"]["metadataVersion"] = 1
    elif mutation == "source":
        record["sha256"] = "f" * 64
    elif mutation == "generation":
        record["activeGeneration"] = 4
    elif mutation == "file_type":
        record["fileType"] = "md"
    elif mutation == "name":
        record["fileName"] = "Other.docx"
    elif mutation == "missing_catalog":
        del sections[0]["sourceNavigation"]
    elif mutation == "duplicate_catalog":
        sections[1]["sourceNavigation"] = copy.deepcopy(sections[0]["sourceNavigation"])
    elif mutation.startswith("tree") or mutation in {"leaf_index", "leaf_parent", "heading_title"}:
        nodes = sections[0]["sourceNavigation"]["nodes"]
        if mutation == "tree_cycle":
            nodes[0]["parentId"] = nodes[-1]["id"]
        elif mutation == "tree_missing":
            nodes.pop()
        elif mutation == "tree_duplicate":
            nodes.append(copy.deepcopy(nodes[-1]))
        elif mutation == "leaf_index":
            nodes[-1]["sectionIndexes"] = [0]
        elif mutation == "leaf_parent":
            nodes[-1]["parentId"] = "n0"
        else:
            next(n for n in nodes if n["kind"] == "section")["title"] = "Fabricated"
    elif mutation == "atom_id":
        sections[1]["location"]["sourceAtomId"] = "p999"
    elif mutation == "coordinate":
        sections[1]["sourceStructure"]["coordinate"]["paragraph"] = 999
    elif mutation == "render":
        sections[-1]["text"] += "Fabricated"
    elif mutation == "runs":
        sections[-1]["sourceStructure"]["paragraphs"][0]["runs"] = ["Fabricated"]
    elif mutation == "numbering":
        sections[0]["sourceStructure"]["numberingDefinitions"] = {}
    elif mutation == "numbering_unused":
        from fli.checkpoints import digest

        extra = {"numId": 777, "abstractXml": "", "instanceXml": ""}
        sections[0]["sourceStructure"]["numberingDefinitions"][digest(extra)] = extra
    elif mutation == "paragraph_order":
        sections[-1], sections[-2] = sections[-2], sections[-1]
    else:
        sections[-1]["heading"] = "Unrelated"
    with pytest.raises((ValueError, KeyError, TypeError)):
        validate_sections(sections, record)


@pytest.mark.parametrize("bad", ["-1", "10", "unknown"])
def test_invalid_outline_level_is_rejected(bad):
    doc = Document()
    p = doc.add_paragraph("Authored level")
    p._p.get_or_add_pPr().append(xml("outlineLvl", val=bad))
    with pytest.raises(ValueError, match="outline level"):
        bind(bytes_for(doc))


def test_visual_style_does_not_invent_heading_or_chapter_semantics():
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("Chapter 1 in bold").bold = True
    doc.add_paragraph("Ordinary body.")
    sections, _, record = bind(bytes_for(doc))
    validate_sections(sections, record)
    assert {node["kind"] for node in navigation(sections)["nodes"]} == {"document", "paragraph"}


def test_wrong_build_identity_unsupported_format_and_unknown_nodes_refuse():
    source = bytes_for(source_document())
    sections, _, record = bind(source)
    wrong = copy.deepcopy(record["ingestionJob"])
    wrong["sourceSha256"] = "f" * 64
    with pytest.raises(ValueError, match="source binding"):
        build_source(source, wrong, "Policy.docx", "docx")
    with pytest.raises(ValueError, match="supported formats"):
        build_source(source, record["ingestionJob"], "Policy.pdf", "pdf")
    with pytest.raises(ValueError, match="source node"):
        node_indexes(navigation(sections), ["unknown"])


def test_markdown_coordinate_and_source_changes_refuse():
    sections, _, record = bind(b"# Heading\n\nText.\n## Nested\nMore.\n", "md")
    damaged = copy.deepcopy(sections)
    damaged[0]["location"]["charEnd"] += 1
    with pytest.raises(ValueError, match="coordinates"):
        validate_sections(damaged, record)
    damaged = copy.deepcopy(sections)
    damaged[0]["text"] += "Different"
    with pytest.raises(ValueError, match="Markdown source"):
        validate_sections(damaged, record)


@pytest.mark.parametrize("replacement", [None, []])
@pytest.mark.parametrize(
    "path",
    [
        (0,),
        (0, "sourceStructure"),
        (0, "sourceStructure", "paragraphs"),
        (0, "sourceStructure", "paragraphs", 0),
        (0, "sourceStructure", "paragraphs", 0, "runs"),
        (0, "sourceStructure", "coordinate"),
        (0, "sourceStructure", "numberingDefinitions"),
        (0, "sourceNavigation"),
        (0, "sourceNavigation", "nodes"),
        (0, "sourceNavigation", "nodes", 0),
    ],
)
def test_malformed_json_containers_refuse_as_validation_errors(path, replacement):
    sections, _, record = bind(bytes_for(source_document()))
    container = sections
    for part in path[:-1]:
        container = container[part]
    container[path[-1]] = replacement
    with pytest.raises(ValueError):
        validate_sections(sections, record)


@pytest.mark.parametrize("replacement", [None, []])
@pytest.mark.parametrize("path", [("ingestionJob",), ("ingestionJob", "profile")])
def test_malformed_identity_containers_refuse_as_validation_errors(path, replacement):
    sections, _, record = bind(bytes_for(source_document()))
    container = record
    for part in path[:-1]:
        container = container[part]
    container[path[-1]] = replacement
    with pytest.raises(ValueError):
        validate_sections(sections, record)
