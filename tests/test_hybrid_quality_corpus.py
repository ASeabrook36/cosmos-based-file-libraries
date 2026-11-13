"""Original-source integrity, hidden Word text, absence and privacy gates."""

import copy
import json
from urllib.parse import unquote, urlsplit

import pytest
from docx import Document
from docx.oxml import OxmlElement

from scripts import hybrid_quality_corpus as corpus
from scripts.retrieval_r0_corpus import sha256


def fixture_pack(tmp_path):
    doc = Document()
    paragraph = doc.add_paragraph("Approval needs ")
    control = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "three conditions"
    run.append(text)
    content.append(run)
    control.append(content)
    paragraph._p.append(control)
    paragraph.add_run(" before work.")
    doc.add_paragraph("")
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Line one\nLine two"
    path = tmp_path / "original.docx"
    doc.save(path)
    source_hash = sha256(path.read_bytes())
    source = {
        "id": "source",
        "fileName": path.name,
        "split": "confirmation",
        "sha256": source_hash,
        "sizeBytes": path.stat().st_size,
    }
    body = "Approval needs three conditions before work."
    region = {
        "id": "source:p0",
        "kind": "paragraph",
        "index": 0,
        "sourceSha256": source_hash,
        "normalizedTextSha256": sha256(body.encode()),
        "expectedBlank": False,
    }
    fact = {
        "sourceRegionIds": [region["id"]],
        "anchorSpans": [
            {"regionId": region["id"], "start": 0, "end": len(body), "normalizedTextSha256": sha256(body.encode())}
        ],
    }
    task = {
        "case": "one",
        "question": "What is needed before work?",
        "split": "confirmation",
        "families": ["concept"],
        "answerable": True,
        "sourceFacts": [{"sourceId": "source", "source": path.name, "regions": [region]}],
        "requiredRegionIds": [region["id"]],
        "requiredFactIds": ["fact"],
        "expectedSourceIds": ["source"],
    }
    return {"documents": [source], "facts": {"fact": fact}}, [task]


def test_nested_content_control_text_is_required_original_evidence(tmp_path):
    manifest, tasks = fixture_pack(tmp_path)
    # python-docx omits these words; accepting its empty/partial text would corrupt recall.
    assert "three conditions" not in Document(tmp_path / "original.docx").paragraphs[0].text
    gold = corpus.verify(manifest, tasks, tmp_path)
    assert gold["facts"]["fact"]["requiredAnchorTexts"] == ["Approval needs three conditions before work."]
    assert gold["tasks"]["one"]["expectedAnswer"] == gold["facts"]["fact"]["expectedFact"]
    assert (
        corpus._complete_region_text(
            Document(tmp_path / "original.docx"),
            {
                "kind": "table_cell",
                "table": 0,
                "row": 0,
                "column": 0,
            },
        )
        == "Line one Line two"
    )


def test_source_bytes_and_original_coordinates_cannot_drift(tmp_path):
    manifest, tasks = fixture_pack(tmp_path)
    tampered = copy.deepcopy(tasks)
    tampered[0]["sourceFacts"][0]["regions"][0]["index"] = 1
    with pytest.raises(ValueError, match="empty-string"):
        corpus.verify(manifest, tampered, tmp_path)
    with (tmp_path / "original.docx").open("ab") as handle:
        handle.write(b"changed original")
    with pytest.raises(ValueError, match="original bytes changed"):
        corpus.verify(manifest, tasks, tmp_path)


@pytest.mark.parametrize("mutation", ["empty-region", "empty-anchor", "anchor-bounds", "anchor-hash", "orphan-region"])
def test_no_empty_match_or_unbound_fact_can_score_support(tmp_path, mutation):
    manifest, tasks = fixture_pack(tmp_path)
    anchor = manifest["facts"]["fact"]["anchorSpans"][0]
    if mutation == "empty-region":
        tasks[0]["sourceFacts"][0]["regions"][0]["normalizedTextSha256"] = sha256(b"")
    elif mutation == "empty-anchor":
        anchor["end"] = 0
    elif mutation == "anchor-bounds":
        anchor["end"] += 100
    elif mutation == "anchor-hash":
        anchor["normalizedTextSha256"] = "0" * 64
    else:
        tasks[0]["requiredRegionIds"] = []
    with pytest.raises(ValueError):
        corpus.verify(manifest, tasks, tmp_path)


def test_absence_has_explicit_abstention_and_no_fabricated_recall_denominator(tmp_path):
    manifest, tasks = fixture_pack(tmp_path)
    missing = {
        "case": "missing",
        "question": "Who signed the completed contract?",
        "split": "confirmation",
        "families": ["name", "unanswerable"],
        "answerable": False,
        "sourceFacts": [],
        "requiredRegionIds": [],
        "requiredFactIds": [],
        "expectedSourceIds": [],
        "missingAnswerDefinition": "No completed signature is supplied; state the information is unavailable.",
    }
    tasks.append(missing)
    gold = corpus.verify(manifest, tasks, tmp_path)
    assert gold["tasks"]["missing"]["requiredFactIds"] == []
    assert "expectedAnswer" not in gold["tasks"]["missing"]
    missing["requiredFactIds"] = ["fact"]
    with pytest.raises(ValueError, match="no recall denominator"):
        corpus.verify(manifest, tasks, tmp_path)


def test_source_traversal_and_confirmation_split_leakage_are_refused(tmp_path):
    manifest, tasks = fixture_pack(tmp_path)
    manifest["documents"][0]["fileName"] = "../original.docx"
    with pytest.raises(ValueError, match="plain filenames"):
        corpus.validate_pack(manifest, tasks)
    manifest, tasks = fixture_pack(tmp_path)
    tasks[0]["split"] = "development"
    with pytest.raises(ValueError, match="confirmation"):
        corpus.validate_pack(manifest, tasks)


def test_public_frozen_pack_is_new_complete_and_source_hash_bound():
    manifest, tasks = corpus.load_pack(corpus.PACK / "manifest.json", corpus.PACK / "tasks.json")
    old = json.loads((corpus.r0.REPO / "docs/testing/retrieval-r0/manifest.json").read_text())
    assert not {d["sha256"] for d in old["documents"]} & {d["sha256"] for d in manifest["documents"]}
    assert len(tasks) == 48
    assert sum(t["answerable"] for t in tasks) == 40
    assert len(manifest["documents"]) == 8
    assert manifest["sourceTokenEstimate"] >= 140_000
    assert manifest["actualMicrosoftChunks"] is None  # Local estimates never become cloud facts.
    assert all("expectedFact" not in value for value in manifest["facts"].values())
    assert all("expectedAnswer" not in value for value in tasks)
    assert len(manifest["goldSha256"]) == 64
    from fli.api.documents import UploadMetadata

    for source in manifest["documents"]:
        assert source["fileName"] == unquote(urlsplit(source["provenance"]["downloadUrl"]).path.rsplit("/", 1)[-1])
        UploadMetadata.model_validate(source["metadata"])
        tags = {tag["key"]: tag["value"] for tag in source["metadata"]["tags"]}
        assert tags["document-title"] == source["provenance"]["title"]


def test_modified_task_bytes_and_conflicting_fact_identity_fail(tmp_path):
    manifest, tasks = fixture_pack(tmp_path)
    manifest["tasksSha256"] = sha256(corpus.serialized(tasks))
    (tmp_path / "manifest.json").write_bytes(corpus.serialized(manifest))
    (tmp_path / "tasks.json").write_bytes(corpus.serialized(tasks) + b" ")
    with pytest.raises(ValueError, match="task pack differs"):
        corpus.load_pack(tmp_path / "manifest.json", tmp_path / "tasks.json")
    tasks[0]["requiredFactIds"] = ["fact", "fact"]
    with pytest.raises(ValueError, match="duplicate required fact"):
        corpus.validate_pack(manifest, tasks)
