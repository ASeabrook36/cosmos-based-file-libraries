import base64
import copy
import io
import json

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from scripts.source_structure_probe import checked, matching_html_cells, normalize, private_path, probe, sha


def fixture(*, preserve_boundaries=False):
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    paragraphs = [
        "An event includes the following circumstances.",
        "The first numbered circumstance.",
        "A nested numbered exclusion.",
        "This final unnumbered condition applies to the event as a whole.",
    ]
    cell.paragraphs[0].text = paragraphs[0]
    for i, text in enumerate(paragraphs[1:], 1):
        paragraph = cell.add_paragraph(text)
        if i < 3:
            num = OxmlElement("w:numPr")
            for tag, value in (("ilvl", i - 1), ("numId", i)):
                item = OxmlElement("w:" + tag)
                item.set(qn("w:val"), str(value))
                num.append(item)
            paragraph._p.get_or_add_pPr().append(num)
    output = io.BytesIO()
    document.save(output)
    original = output.getvalue()
    inside = "".join(f"<p>{p}</p>" for p in paragraphs) if preserve_boundaries else " ".join(paragraphs)
    content = "<table><tr><td>" + inside + "</td></tr></table>"
    mapping = [{"content": p, "spans": [{"offset": content.index(p), "length": len(p)}]} for p in paragraphs]
    start = content.index(paragraphs[0])
    end = content.index(paragraphs[-1]) + len(paragraphs[-1])
    analysis = {
        "analyzeResult": {
            "stringIndexType": "unicodeCodePoint",
            "contentFormat": "markdown",
            "content": content,
            "paragraphs": mapping,
            "tables": [
                {"cells": [{"content": " ".join(paragraphs), "spans": [{"offset": start, "length": end - start}]}]}
            ],
        }
    }
    encoded = base64.b64encode(original).decode()
    extraction = {"sourceBase64": encoded, "analysis": analysis, "analysisOutput": {"body": analysis}}
    result = {"sourceBase64": encoded, "analysis": analysis}
    sections = [{"contentKind": "text", "text": content, "location": {}}]
    locator = {"table": 0, "row": 0, "column": 0}
    return original, extraction, result, sections, locator, sha(normalize(" ".join(paragraphs)).encode())


def test_same_words_can_lose_list_and_paragraph_structure():
    public, private = probe(*fixture())
    assert public["providerCellNormalizedTextMatchesOriginal"]
    assert public["paragraphBoundaryLossReproduced"]
    assert public["originalParagraphs"] == 4
    assert public["originalNumberedParagraphs"] == 2
    assert public["numberedParagraphLevels"] == [0, 1]
    assert public["providerParagraphsMappedExactlyInOrder"] == 4
    assert private["originalParagraphs"][-1]["numbering"] is None
    assert private["originalParagraphs"][-2]["numbering"]["ilvl"] == 1
    assert public["semanticQualifierScopeAutomaticallyGraded"] is False


def test_preserved_paragraphs_do_not_report_boundary_loss_or_claim_list_repair():
    public, _ = probe(*fixture(preserve_boundaries=True))
    assert public["providerCellInternalBoundaryTagCount"] == 4
    assert public["paragraphBoundaryLossReproduced"] is False
    assert public["publishedCellHasNoListTags"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda args: args[1].update(sourceBase64=base64.b64encode(b"different source").decode()),
        lambda args: args[2].update(sourceBase64=base64.b64encode(b"different source").decode()),
        lambda args: args[3][0].update(text="changed publication"),
        lambda args: args[3].append(copy.deepcopy(args[3][0])),
        lambda args: args[1]["analysis"]["analyzeResult"].update(stringIndexType="utf16CodeUnit"),
        lambda args: args[1]["analysis"]["analyzeResult"]["paragraphs"].reverse(),
        lambda args: args[1]["analysis"]["analyzeResult"]["paragraphs"][1]["spans"][0].update(offset=1),
        lambda args: args[1]["analysis"]["analyzeResult"]["paragraphs"].pop(),
        lambda args: args[1]["analysis"]["analyzeResult"]["paragraphs"][1].update(content="different words"),
        lambda args: args[4].update(row=-1),
        lambda args: args[4].update(extra="unsupported"),
        lambda args: args.__setitem__(5, "0" * 64),
    ],
)
def test_changed_or_ambiguous_evidence_fails_closed(mutation):
    args = list(fixture())
    mutation(args)
    with pytest.raises((ValueError, IndexError)):
        probe(*args)


def test_html_cell_matching_retains_internal_boundary_tags():
    cells = matching_html_cells("<table><td><p>A</p><ol><li>B</li></ol><p>C</p></td></table>", "A B C")
    assert len(cells) == 1
    assert cells[0]["tags"] == {"p": 2, "ol": 1, "li": 1}
    assert (
        matching_html_cells("<td>A</td><td>A</td>", "A") and len(matching_html_cells("<td>A</td><td>A</td>", "A")) == 2
    )


def test_input_hash_binding_and_private_output_guard(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"stable": True}))
    ref = {"path": str(source), "sha256": sha(source.read_bytes())}
    assert checked(ref) == source.read_bytes()
    source.write_text("changed")
    with pytest.raises(ValueError, match="hash"):
        checked(ref)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("worktree pointer")
    with pytest.raises(ValueError, match="inside Git"):
        private_path(checkout / "raw.json")
