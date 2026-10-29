"""Optional source-structure adapters, separate from provider orchestration and vectors.

This first adapter consumes exact UTF-8 Markdown source. It generates no summaries.
The structure lab remains the authority for ATX ancestry and lossless block spans.
"""

from __future__ import annotations

from fli.extraction import Section
from fli.structure_lab import markdown_document


def markdown_structure(data: bytes, document_id: str, file_name: str) -> tuple[list[Section], dict]:
    structure = markdown_document(document_id, file_name, data.decode("utf-8-sig"))
    blocks = {block["id"]: block for block in structure["blocks"]}
    sections = [
        Section(
            node["title"] if node["parentId"] else None,
            blocks[block_id]["text"],
            {
                "charStart": blocks[block_id]["start"],
                "charEnd": blocks[block_id]["end"],
                "nodeId": node["id"],
                "parentNodeId": node["parentId"],
            },
        )
        for node in structure["nodes"]
        for block_id in node["blockIds"]
    ]
    return sections, structure


def docx_structure(data: bytes, document_id: str, file_name: str) -> tuple[list[Section], dict]:
    """Native main-body paragraphs/tables with authored Heading 1..9 ancestry.

    Unicode spans address the canonical body extraction, not ZIP/XML byte offsets.
    Headers, footnotes, comments, text boxes and revisions are outside this adapter.
    Existing native figure extraction/enrichment stays separate.
    """
    import io
    import re

    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    from fli.structure_lab import digest

    document = Document(io.BytesIO(data))
    root = {"id": "n0", "parentId": None, "title": file_name, "level": "document", "role": "unknown", "blockIds": []}
    nodes, blocks, sections = [root], [], []
    stack, owner = [(0, root)], root
    paragraphs = {element: index for index, element in enumerate(document.element.body.iter(qn("w:p")), 1)}
    offset = 0
    for child in document.element.body.iterchildren():
        text, start, end = None, None, None
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text + "\n"
            start = end = paragraphs[child]
            style = paragraph.style.name if paragraph.style is not None else ""
            heading = re.fullmatch(r"Heading ([1-9])", style or "")
            if heading and paragraph.text.strip():
                depth = int(heading[1])
                while stack[-1][0] >= depth:
                    stack.pop()
                owner = {
                    "id": f"n{len(nodes)}",
                    "parentId": stack[-1][1]["id"],
                    "title": paragraph.text,
                    "level": "section",
                    "role": "unknown",
                    "blockIds": [],
                }
                nodes.append(owner)
                stack.append((depth, owner))
        elif child.tag == qn("w:tbl"):
            rows = []
            for row in Table(child, document).rows:
                cells, seen = [], set()
                for cell in row.cells:
                    if id(cell._tc) not in seen:
                        seen.add(id(cell._tc))
                        cells.append(cell.text)
                rows.append(" | ".join(cells))
            text = "\n".join(rows) + "\n"
            spans = [paragraphs[p] for p in child.iter(qn("w:p"))]
            start, end = (min(spans), max(spans)) if spans else (None, None)
        if text is None:
            continue
        block_id = f"b{len(blocks)}"
        blocks.append({"id": block_id, "text": text, "start": offset, "end": offset + len(text)})
        owner["blockIds"].append(block_id)
        location = {
            "charStart": offset,
            "charEnd": offset + len(text),
            "nodeId": owner["id"],
            "parentNodeId": owner["parentId"],
        }
        if start is not None:
            location.update(paragraph=start, paragraphEnd=end)
        sections.append(Section(owner["title"] if owner["parentId"] else None, text, location))
        offset += len(text)
    if not any(section.text.strip() for section in sections):
        raise ValueError("empty native body")
    structure = {
        "id": document_id,
        "title": file_name,
        "genre": "unknown",
        "genreOrigin": "unknown",
        "sourceDigest": digest("".join(block["text"] for block in blocks)),
        "adapter": "docx-heading-body-v1",
        "nodes": nodes,
        "blocks": blocks,
        "limitations": ["Main body only; no authored hierarchy inferred from visual formatting."],
    }
    return sections, structure


def source_structure(data: bytes, document_id: str, file_name: str, file_type: str):
    adapter = {"md": markdown_structure, "docx": docx_structure}.get(file_type)
    if adapter is None:
        raise ValueError("unsupported structural format")
    return adapter(data, document_id, file_name)
