"""Source-preserving granular navigation, independent of ingestion orchestration.

This versioned enrichment uses original DOCX structure and lossless Markdown ATX
blocks. Source atoms and their navigation catalog share one immutable artifact.
Its passage mapping establishes a containing source atom, never invented offsets.
"""

from __future__ import annotations

import copy
import hashlib
import io
import re

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from fli.checkpoints import digest
from fli.extraction import Section
from fli.source_fidelity import original_blocks, render
from fli.structure_lab import markdown_document

PROFILE = "custom-granular-v1"
SCHEMA = "fli.granular-source.v1"
LIMITATIONS = [
    "DOCX coverage is supported main-body paragraphs and tables, not complete Office fidelity or rendered pages.",
    "Authored DOCX outline levels and Markdown ATX headings establish hierarchy; visual formatting does not.",
    "DOCX list identities/depth and numbering definitions are preserved; printed ordinal markers remain unresolved.",
    "Fields retain cached visible text only; unsupported revisions, footnotes, text boxes and nested tables fail.",
    "Markdown selection uses exact ATX source blocks, not inferred paragraph, table-cell or page coordinates.",
    "Passage mappings identify containing source atoms; normalized chunk text has no claimed exact character offsets.",
]


def require(condition, message):
    if not condition:
        raise ValueError("granular source: " + message)


def _binding(work, source):
    require(work.get("profile", {}).get("id") == PROFILE, "profile binding unavailable")
    require(type(work.get("generation")) is int and work["generation"] > 0, "generation binding unavailable")
    require(work.get("sourceSha256") == hashlib.sha256(source).hexdigest(), "source binding differs")
    return {
        "schema": SCHEMA,
        "sourceSha256": work["sourceSha256"],
        "jobSha256": digest(work),
        "generation": work["generation"],
    }


def _heading(element, document):
    paragraph = Paragraph(element, document)
    candidates = [("paragraph", paragraph._p.pPr)]
    style, seen = paragraph.style, set()
    while style is not None:
        require(style.style_id not in seen, "cyclic paragraph style ancestry")
        seen.add(style.style_id)
        candidates.append(("style:" + style.style_id, style.element.pPr))
        style = style.base_style
    defaults = document.styles.element.find(qn("w:docDefaults"))
    if defaults is not None:
        props = defaults.find(qn("w:pPrDefault"))
        candidates.append(("document-default", None if props is None else props.find(qn("w:pPr"))))
    for origin, props in candidates:
        levels = [] if props is None else props.findall(qn("w:outlineLvl"))
        require(len(levels) <= 1, "ambiguous authored outline level")
        if levels:
            value = levels[0].get(qn("w:val"))
            require(isinstance(value, str) and re.fullmatch(r"[0-9]", value), "invalid authored outline level")
            return {"level": int(value) + 1 if value != "9" else None, "origin": origin}
    return None


def _numbering_catalog(atoms):
    definitions = {}
    for atom in atoms:
        number = atom["paragraphs"][0]["numbering"]
        if number and number["status"] != "suppressed":
            definition = {
                "numId": number["properties"]["numId"],
                "abstractXml": number.pop("abstract"),
                "instanceXml": number.pop("instance"),
            }
            reference = digest(definition)
            definitions[reference] = definition
            number["definitionSha256"] = reference
    return definitions


def _docx_atoms(source):
    blocks, _ = original_blocks(source)
    document = Document(io.BytesIO(source))
    headings = {i: _heading(p, document) for i, p in enumerate(document.element.body.iter(qn("w:p")), 1)}
    atoms = []
    for block in blocks:
        for paragraph in block["paragraphs"]:
            value = copy.deepcopy(paragraph)
            value["authoredHeading"] = headings[value["ordinal"]]
            atoms.append({"coordinate": copy.deepcopy(block["coordinate"]), "paragraphs": [value]})
        for continuation in block.get("mergeContinuations", []):
            for paragraph in continuation["paragraphs"]:
                value = copy.deepcopy(paragraph)
                value["authoredHeading"] = headings[value["ordinal"]]
                atoms.append(
                    {
                        "coordinate": copy.deepcopy(block["coordinate"]),
                        "paragraphs": [value],
                        "mergeContinuationRow": continuation["row"],
                    }
                )
    atoms.sort(key=lambda a: a["paragraphs"][0]["ordinal"])
    return atoms, _numbering_catalog(atoms)


def _node(identifier, kind, parent, title, containing, **fields):
    return {
        "id": identifier,
        "kind": kind,
        "parentId": parent,
        "title": title,
        "containingSectionId": containing,
        "sectionIndexes": [],
        **fields,
    }


def _docx_model(atoms, title):
    """Derive the complete catalog/locations from retained original atom metadata."""
    nodes = [_node("n0", "document", None, title, None)]
    by_id, stack = {"n0": nodes[0]}, [(0, nodes[0])]
    locations, headings = [], []
    ordinals = [atom["paragraphs"][0]["ordinal"] for atom in atoms]
    require(all(type(ordinal) is int for ordinal in ordinals), "invalid paragraph ordinal")
    require(ordinals == list(range(1, len(atoms) + 1)), "paragraph inventory is not complete and ordered")
    table_owners, cell_coordinates = {}, {}
    for index, atom in enumerate(atoms):
        paragraph, coordinate = atom["paragraphs"][0], atom["coordinate"]
        ordinal, text = paragraph["ordinal"], paragraph["text"]
        authored = paragraph.get("authoredHeading")
        if authored is not None:
            require(
                isinstance(authored, dict)
                and (
                    authored.get("level") is None or type(authored.get("level")) is int and 1 <= authored["level"] <= 9
                )
                and isinstance(authored.get("origin"), str),
                "invalid retained authored heading",
            )
        if coordinate.get("kind") == "paragraph":
            require(coordinate == {"kind": "paragraph", "paragraph": ordinal}, "paragraph coordinate differs")
            if authored and authored["level"] is not None and text.strip():
                depth = authored["level"]
                while stack[-1][0] >= depth:
                    stack.pop()
                node = _node(
                    f"h{ordinal}",
                    "section",
                    stack[-1][1]["id"],
                    text,
                    stack[-1][1]["id"],
                    headingLevel=depth,
                    headingOrigin=authored["origin"],
                    headingParagraph=ordinal,
                )
                nodes.append(node)
                by_id[node["id"]] = node
                stack.append((depth, node))
            parent = stack[-1][1]["id"]
        else:
            require(coordinate.get("kind") == "tableCell", "unsupported source coordinate")
            require(
                all(type(coordinate.get(k)) is int and coordinate[k] >= 0 for k in ("table", "row", "column"))
                and all(type(coordinate.get(k)) is int and coordinate[k] > 0 for k in ("rowSpan", "columnSpan"))
                and type(coordinate.get("headerRow")) is bool,
                "invalid table cell coordinate",
            )
            table_id = f"t{coordinate['table']}"
            owner = stack[-1][1]["id"]
            require(table_owners.get(table_id, owner) == owner, "table crosses authored sections")
            table_owners[table_id] = owner
            if table_id not in by_id:
                node = _node(table_id, "table", owner, f"Table {coordinate['table'] + 1}", owner)
                node["sourceCoordinate"] = {"kind": "table", "table": coordinate["table"]}
                nodes.append(node)
                by_id[table_id] = node
            parent = f"{table_id}:r{coordinate['row']}:c{coordinate['column']}"
            require(cell_coordinates.get(parent, coordinate) == coordinate, "cell topology varies across paragraphs")
            cell_coordinates[parent] = coordinate
            if parent not in by_id:
                node = _node(
                    parent,
                    "tableCell",
                    table_id,
                    f"Table {coordinate['table'] + 1}, row {coordinate['row'] + 1}, column {coordinate['column'] + 1}",
                    owner,
                    sourceCoordinate=coordinate,
                )
                nodes.append(node)
                by_id[parent] = node
            if "mergeContinuationRow" in atom:
                row = atom["mergeContinuationRow"]
                require(
                    type(row) is int
                    and coordinate["row"] < row < coordinate["row"] + coordinate["rowSpan"]
                    and not text,
                    "invalid merged continuation paragraph",
                )
        owner = stack[-1][1]
        identifier = f"p{ordinal}"
        original = {**coordinate, "paragraph": ordinal}
        if "mergeContinuationRow" in atom:
            original["mergeContinuationRow"] = atom["mergeContinuationRow"]
        node = _node(identifier, "paragraph", parent, f"Paragraph {ordinal}", owner["id"], sourceCoordinate=original)
        node["sectionIndexes"] = [index]
        nodes.append(node)
        by_id[identifier] = node
        locations.append(
            {
                "sourceAtomId": identifier,
                "nodeId": owner["id"],
                "parentNodeId": owner["parentId"],
                "paragraph": ordinal,
                "paragraphEnd": ordinal,
                "paragraphOrdinals": [ordinal],
                "originalCoordinate": original,
            }
        )
        headings.append(owner["title"] if owner["id"] != "n0" else None)
    return nodes, locations, headings


def _markdown_model(text, document_id, title):
    structure = markdown_document(document_id, title, text)
    blocks = {block["id"]: block for block in structure["blocks"]}
    nodes, sections = [], []
    for original in structure["nodes"]:
        root = original["parentId"] is None
        node = _node(
            original["id"],
            "document" if root else original["level"],
            original["parentId"],
            title if root else original["title"],
            original["parentId"],
        )
        if not root:
            block = blocks[original["blockIds"][0]]
            match = re.match(r"^ {0,3}(#{1,6})[ \t]+", block["text"])
            require(match is not None, "ATX heading source unavailable")
            node.update(headingLevel=len(match[1]), headingOrigin="markdown-atx")
        nodes.append(node)
        for bid in original["blockIds"]:
            block = blocks[bid]
            coordinate = {"kind": "sourceBlock", "charStart": block["start"], "charEnd": block["end"]}
            leaf = _node(
                bid,
                "sourceBlock",
                node["id"],
                f"Source block {len(sections) + 1}",
                node["id"],
                sourceCoordinate=coordinate,
            )
            leaf["sectionIndexes"] = [len(sections)]
            nodes.append(leaf)
            sections.append(
                {
                    "text": block["text"],
                    "heading": None if root else node["title"],
                    "contentKind": "text",
                    "location": {
                        "sourceAtomId": bid,
                        "nodeId": node["id"],
                        "parentNodeId": node["parentId"],
                        "charStart": block["start"],
                        "charEnd": block["end"],
                        "originalCoordinate": coordinate,
                    },
                    "sourceStructure": {
                        "scope": "markdown-utf8-text",
                        "coordinate": coordinate,
                        "sourceText": block["text"],
                    },
                }
            )
    return nodes, sections


def build_source(source: bytes, work: dict, file_name: str, file_type: str):
    binding = _binding(work, source)
    if file_type == "docx":
        atoms, definitions = _docx_atoms(source)
        nodes, locations, headings = _docx_model(atoms, file_name)
        sections = [
            {
                "text": render(atom),
                "heading": heading,
                "contentKind": "text",
                "location": location,
                "sourceStructure": {**binding, "scope": "original-main-body", **atom},
            }
            for atom, location, heading in zip(atoms, locations, headings, strict=True)
        ]
    elif file_type == "md":
        nodes, sections = _markdown_model(source.decode("utf-8-sig"), work["documentId"], file_name)
        definitions = {}
        for section in sections:
            section["sourceStructure"].update(binding)
    else:
        raise ValueError("granular source: supported formats are DOCX and Markdown")
    sections[0]["sourceStructure"]["numberingDefinitions"] = definitions
    sections[0]["sourceNavigation"] = {
        **binding,
        "fileType": file_type,
        "documentId": work["documentId"],
        "nodes": nodes,
        "sourceAtomCount": len(sections),
        "mappingPrecision": "containing_source_atom",
    }
    coverage = {
        "sourceStructure": SCHEMA,
        "sourceStructureScope": "docx-main-body" if file_type == "docx" else "markdown-utf8-atx",
        "sourceStructureLimitations": list(LIMITATIONS),
        "authoredHierarchy": "original-outline-levels" if file_type == "docx" else "markdown-atx",
        "directPassageToSourceMapping": "verified_containing_source_atom",
        "sourceStructureRendering": "XML tags/attributes describe DOCX structure; enclosed text is source text. "
        "Markdown source blocks retain exact decoded text. List printed markers remain unresolved.",
        "sourceAtoms": len(sections),
        "sourceParagraphs": len(sections) if file_type == "docx" else None,
        "sourceTableCells": sum(n["kind"] == "tableCell" for n in nodes),
        "visuals": "unavailable",
        "generatedSummaries": "unavailable",
    }
    return sections, coverage


def navigation(sections):
    require(
        isinstance(sections, list) and sections and all(isinstance(section, dict) for section in sections),
        "source atoms unavailable",
    )
    tree = sections[0].get("sourceNavigation")
    require(
        isinstance(tree, dict) and tree.get("schema") == SCHEMA and isinstance(tree.get("nodes"), list),
        "navigation unavailable",
    )
    return tree


def node_indexes(tree, node_ids, subtree=True):
    require(
        isinstance(node_ids, list) and node_ids and all(isinstance(n, str) for n in node_ids), "select source nodes"
    )
    nodes = tree["nodes"]
    by_id = {n["id"]: n for n in nodes}
    require(len(by_id) == len(nodes) and set(node_ids) <= by_id.keys(), "source node unavailable or duplicate")
    chosen, pending, indexes = set(), list(node_ids), set()
    children = {}
    for node in nodes:
        children.setdefault(node["parentId"], []).append(node)
    while pending:
        identifier = pending.pop()
        if identifier in chosen:
            continue
        chosen.add(identifier)
        indexes.update(by_id[identifier]["sectionIndexes"])
        pending.extend(
            child["id"]
            for child in children.get(identifier, [])
            if subtree or child["kind"] not in {"section", "chapter"}
        )
    return sorted(indexes)


def plain_sections(sections):
    return [
        Section(
            section.get("heading"),
            section["sourceStructure"]["paragraphs"][0]["text"]
            if section["sourceStructure"]["scope"] == "original-main-body"
            else section["sourceStructure"]["sourceText"],
            copy.deepcopy(section["location"]),
        )
        for section in sections
    ]


def validate_sections(sections, document):
    require(isinstance(document, dict), "document binding unavailable")
    work = document.get("ingestionJob")
    require(isinstance(work, dict), "job binding unavailable")
    recipe = work.get("profile")
    require(isinstance(recipe, dict) and recipe.get("id") == PROFILE, "document profile differs")
    require(work.get("documentId") == document.get("id"), "document identity differs")
    require(work.get("libraryId") == document.get("libraryId"), "library identity differs")
    require(
        type(document.get("activeGeneration")) is int and document["activeGeneration"] > 0,
        "active generation unavailable",
    )
    require(
        isinstance(document.get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", document["sha256"]),
        "source identity unavailable",
    )
    require(work.get("generation") == document.get("activeGeneration"), "job generation differs")
    require(work.get("sourceSha256") == document.get("sha256"), "job source differs")
    expected = {
        "schema": SCHEMA,
        "generation": document["activeGeneration"],
        "sourceSha256": document["sha256"],
        "jobSha256": digest(work),
    }
    tree = navigation(sections)
    require(all(tree.get(k) == v for k, v in expected.items()), "navigation binding differs")
    require(
        tree.get("fileType") == document.get("fileType") and tree.get("documentId") == document["id"],
        "navigation source identity differs",
    )
    require(
        tree.get("sourceAtomCount") == len(sections) and tree.get("mappingPrecision") == "containing_source_atom",
        "atom inventory differs",
    )
    require(not any("sourceNavigation" in s for s in sections[1:]), "duplicate navigation catalog")
    first_structure = sections[0].get("sourceStructure")
    require(isinstance(first_structure, dict), "source structure unavailable")
    definitions = first_structure.get("numberingDefinitions")
    require(
        isinstance(definitions, dict)
        and all(isinstance(v, dict) and type(v.get("numId")) is int and digest(v) == k for k, v in definitions.items()),
        "numbering catalog unavailable or corrupt",
    )
    used, atoms = set(), []
    for index, section in enumerate(sections):
        value = section.get("sourceStructure")
        require(
            isinstance(value, dict) and all(value.get(k) == v for k, v in expected.items()), "source binding differs"
        )
        require(section.get("contentKind") == "text", "unsupported source atom kind")
        require(isinstance(section.get("text"), str), "source text unavailable")
        require(isinstance(value.get("coordinate"), dict), "source coordinate unavailable")
        require(index == 0 or "numberingDefinitions" not in value, "duplicate numbering catalog")
        if tree["fileType"] == "docx":
            paragraphs = value.get("paragraphs")
            require(
                value.get("scope") == "original-main-body" and isinstance(paragraphs, list) and len(paragraphs) == 1,
                "paragraph atom unavailable",
            )
            paragraph = paragraphs[0]
            require(isinstance(paragraph, dict), "paragraph structure unavailable")
            runs = paragraph.get("runs")
            require(
                isinstance(paragraph.get("text"), str)
                and isinstance(runs, list)
                and all(isinstance(run, str) for run in runs)
                and "".join(runs) == paragraph["text"],
                "original runs differ",
            )
            number = paragraph["numbering"]
            require(number is None or isinstance(number, dict), "numbering structure unavailable")
            if number is not None:
                require(
                    number.get("status") in {"suppressed", "unresolved_preserved_properties"}
                    and isinstance(number.get("properties"), dict),
                    "numbering properties unavailable",
                )
            if number is not None and number["status"] != "suppressed":
                reference = number.get("definitionSha256")
                require(
                    isinstance(reference, str)
                    and reference in definitions
                    and definitions[reference]["numId"] == number["properties"]["numId"],
                    "numbering definition differs",
                )
                used.add(reference)
            atoms.append({k: value[k] for k in ("coordinate", "paragraphs", "mergeContinuationRow") if k in value})
        else:
            require(tree["fileType"] == "md" and value.get("scope") == "markdown-utf8-text", "unsupported source scope")
            require(section["text"] == value.get("sourceText"), "Markdown source differs")
    require(used == set(definitions), "unused numbering definition")
    if tree["fileType"] == "docx":
        nodes, locations, headings = _docx_model(atoms, document["fileName"])
        require(
            all(section["text"] == render(section["sourceStructure"]) for section in sections),
            "rendered source differs",
        )
        require(
            all(
                s.get("location") == loc and s.get("heading") == heading
                for s, loc, heading in zip(sections, locations, headings, strict=True)
            ),
            "source coordinates or heading ownership differ",
        )
    else:
        nodes, canonical = _markdown_model("".join(s["text"] for s in sections), document["id"], document["fileName"])
        require(len(canonical) == len(sections), "Markdown block inventory differs")
        require(
            all(
                a.get(k) == b.get(k)
                for a, b in zip(sections, canonical, strict=True)
                for k in ("text", "heading", "location")
            ),
            "Markdown source coordinates differ",
        )
        require(
            all(
                a["sourceStructure"].get("coordinate") == b["sourceStructure"]["coordinate"]
                for a, b in zip(sections, canonical, strict=True)
            ),
            "Markdown structure coordinate differs",
        )
    require(tree["nodes"] == nodes, "navigation differs from retained source structure")
    require(node_indexes(tree, ["n0"]) == list(range(len(sections))), "navigation coverage is incomplete")
