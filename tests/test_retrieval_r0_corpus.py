"""Integrity and privacy gates for source-based benchmark reconstruction."""

import copy
import hashlib

import pytest
from docx import Document

from scripts.retrieval_r0_corpus import REPO, normalize, private_path, validate_pack, verify


def corpus(tmp_path):
    path = tmp_path / "fixture.docx"
    document = Document()
    document.add_paragraph("Source\u00a0assertion")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Year"
    table.cell(0, 1).text = "2031"
    table.cell(1, 0).text = "Option"
    document.save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "documents": [
            {
                "id": "d01",
                "fileName": path.name,
                "split": "development",
                "sha256": digest,
                "sizeBytes": path.stat().st_size,
            }
        ]
    }
    regions = [
        {
            "id": "d01:p0",
            "kind": "paragraph",
            "index": 0,
            "sourceSha256": digest,
            "normalizedTextSha256": hashlib.sha256(b"Source assertion").hexdigest(),
        },
        {
            "id": "d01:t0:r1:c1",
            "kind": "table_cell",
            "table": 0,
            "row": 1,
            "column": 1,
            "sourceSha256": digest,
            "normalizedTextSha256": hashlib.sha256(b"").hexdigest(),
            "expectedBlank": True,
        },
    ]
    tasks = [
        {
            "case": "one",
            "split": "development",
            "sourceFacts": [{"sourceId": "d01", "source": path.name, "regions": regions}],
        }
    ]
    return manifest, tasks


def test_reconstructs_original_coordinates_and_preserves_blankness(tmp_path):
    manifest, tasks = corpus(tmp_path)
    gold = verify(manifest, tasks, tmp_path)
    assert gold["regions"]["d01:p0"]["expectedText"] == "Source\u00a0assertion"
    assert gold["regions"]["d01:t0:r1:c1"]["expectedText"] == ""
    assert gold["regions"]["d01:p0"]["normalizedText"] == "Source assertion"


def test_changed_original_or_region_is_refused(tmp_path):
    manifest, tasks = corpus(tmp_path)
    tasks[0]["sourceFacts"][0]["regions"][0]["normalizedTextSha256"] = "0" * 64
    with pytest.raises(ValueError, match="source region changed"):
        verify(manifest, tasks, tmp_path)
    with (tmp_path / "fixture.docx").open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="original bytes changed"):
        verify(manifest, tasks, tmp_path)


def test_split_leakage_conflicting_regions_and_traversal_are_refused(tmp_path):
    manifest, tasks = corpus(tmp_path)
    wrong = copy.deepcopy(tasks)
    wrong[0]["split"] = "heldout"
    with pytest.raises(ValueError, match="split mismatch"):
        validate_pack(manifest, wrong)
    wrong = copy.deepcopy(tasks)
    duplicate = copy.deepcopy(wrong[0]["sourceFacts"][0]["regions"][0])
    duplicate["index"] = 1
    wrong[0]["sourceFacts"][0]["regions"].append(duplicate)
    with pytest.raises(ValueError, match="conflicting"):
        validate_pack(manifest, wrong)
    manifest["documents"][0]["fileName"] = "../fixture.docx"
    with pytest.raises(ValueError, match="plain filenames"):
        validate_pack(manifest, tasks)


def test_checkout_and_symlink_destination_are_refused(tmp_path):
    with pytest.raises(ValueError, match="outside the repository"):
        private_path(REPO / "private-gold.json")
    link = tmp_path / "link"
    link.symlink_to(REPO, target_is_directory=True)
    with pytest.raises(ValueError, match="outside the repository"):
        private_path(link / "private-gold.json")
    other_checkout = tmp_path / "another-worktree"
    other_checkout.mkdir()
    (other_checkout / ".git").write_text("gitdir: external-metadata")
    with pytest.raises(ValueError, match="outside the repository"):
        private_path(other_checkout / "private-gold.json")


def test_negative_coordinate_and_false_blank_label_are_refused(tmp_path):
    manifest, tasks = corpus(tmp_path)
    region = tasks[0]["sourceFacts"][0]["regions"][0]
    region["index"] = -1
    with pytest.raises(ValueError, match="nonnegative"):
        verify(manifest, tasks, tmp_path)
    region["index"] = 0
    region["expectedBlank"] = True
    with pytest.raises(ValueError, match="blankness"):
        verify(manifest, tasks, tmp_path)


def test_normalization_does_not_hide_case_or_punctuation():
    assert normalize("A\u00a0Ｂ\n C") == "A B C"
    assert normalize("Yes.") != normalize("yes")
