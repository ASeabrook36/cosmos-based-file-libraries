"""Contract checks for the opt-in compatibility probes, independent of Azure."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.microsoft_reuse_lab import (
    ROOT,
    assess,
    inspect_template,
    load_cases,
    private_directory,
    resume,
    save,
    section_diagnostic,
    structure_metrics,
)


def test_private_runtime_refuses_repository_and_public_directory(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        private_directory(ROOT / "runtime-reuse")
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        private_directory(tmp_path)
    tmp_path.chmod(0o700)
    assert private_directory(tmp_path) == tmp_path.resolve()
    # Resolving symlinks is necessary before the containment check.
    (tmp_path / "repo").symlink_to(ROOT, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        private_directory(tmp_path / "repo")


def test_private_receipts_are_atomic_and_restrictive(tmp_path):
    target = tmp_path / "receipt.json"
    save(target, {"status": "started"})
    save(target, {"status": "succeeded"})
    assert json.loads(target.read_text()) == {"status": "succeeded"}
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_graph_metrics_distinguish_absent_structure_from_present_but_broken_structure():
    assert structure_metrics({"content": "plain"})["sections"] == 0
    result = {
        "content": "chapter and subsection",
        "sections": [
            {"elements": ["/sections/1", "/figures/0"]},
            {"elements": ["/paragraphs/0", "/tables/0", "/paragraphs/99", "invalid"]},
        ],
        "paragraphs": [{"content": "subsection"}],
        "figures": [{"caption": {"content": "caption"}, "elements": ["/paragraphs/0"]}],
        "tables": [{}],
    }
    metrics = structure_metrics(result)
    assert metrics["nestedSectionReferences"] == 1
    assert metrics["sectionElementReferences"] == 6
    assert metrics["unresolvedSectionReferences"] == 2
    assert metrics["figuresWithCaption"] == 1
    assert metrics["figuresWithElements"] == 1


def test_template_inspection_exposes_lossy_input_projection_and_id_strategy():
    # Minimal interface fixture, not a copy of the upstream implementation.
    definition = {
        "actions": {
            "Select_embeddings_and_map_to_CosmosDB": {"inputs": {"select": {"id": "@{guid()}", "text": "chunk"}}},
            "Analyze_Document_probe": {"inputs": {"path": "prebuilt-read", "queries": {"api-version": "2024-11-30"}}},
            "Chunk_text": {"inputs": {"text": "analyzeResult.content", "TokenSize": 5000}},
        },
        "triggers": {},
        "outputs": {},
    }
    report = inspect_template({"definition": definition})
    assert report["randomIds"]
    assert not report["preservesElementGraph"]
    assert report["chunkInput"] == "analyzeResult.content"
    assert report["chunking"] == {"TokenSize": 5000}


def manifest(tmp_path: Path, documents: list) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"documents": documents}))
    return path


def test_rejects_traversal_labels_duplicate_labels_and_unsupported_inputs(tmp_path):
    source = tmp_path / "test.txt"
    source.write_text("source")
    with pytest.raises(ValueError, match="safe"):
        load_cases(manifest(tmp_path, [{"label": "../leak", "path": "test.txt"}]), 12)
    with pytest.raises(ValueError, match="supported"):
        load_cases(manifest(tmp_path, [{"label": "text", "path": "test.txt"}]), 12)
    source = tmp_path / "test.png"
    source.write_bytes(b"fixture")
    cases = [{"label": "same", "path": "test.png"}] * 2
    with pytest.raises(ValueError, match="unique"):
        load_cases(manifest(tmp_path, cases), 12)


def test_oversized_pdf_is_refused_instead_of_prefix_read(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    writer.write(tmp_path / "three.pdf")
    path = manifest(tmp_path, [{"label": "three", "path": "three.pdf"}])
    with pytest.raises(ValueError, match="never silently"):
        load_cases(path, 2)
    assert len(load_cases(path, 3)) == 1


def test_resume_rechecks_new_probes_without_network_and_rejects_response_changes(tmp_path):
    path = tmp_path / "response.json"
    save(path, {"content": "Exact source fact"})
    receipt = {"status": "succeeded", "responseSha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert resume(receipt, path, ["exact SOURCE fact"])["acceptancePassed"]
    assert not resume(receipt, path, ["absent fact"])["acceptancePassed"]
    save(path, {"content": "changed"})
    with pytest.raises(ValueError, match="integrity"):
        resume(receipt, path, [])


def test_request_success_does_not_imply_probe_or_graph_acceptance():
    assert not assess({"status": "succeeded"}, {"content": "text"}, ["missing"])["acceptancePassed"]
    bad_graph = {"content": "text", "sections": [{"elements": ["/sections/3"]}]}
    assert not assess({"status": "succeeded"}, bad_graph, ["text"])["acceptancePassed"]


def test_manifest_refuses_unbounded_document_counts(tmp_path):
    with pytest.raises(ValueError, match="one to six"):
        load_cases(manifest(tmp_path, []), 12)
    with pytest.raises(ValueError, match="one to six"):
        load_cases(manifest(tmp_path, [{}] * 7), 12)


def test_provider_section_probe_exposes_omitted_source_and_bad_spans_without_repair():
    result = {
        "content": "Chapter 1 body limitations",
        "paragraphs": [{"content": "Chapter 1"}],
        "sections": [{"elements": ["/paragraphs/0"], "spans": [{"offset": 0, "length": 14}]}],
    }
    task = {"label": "coverage", "heading": "Chapter 1", "include": ["body", "limitations"]}
    row = section_diagnostic(result, task)
    assert row["resolved"] and not row["passed"]
    assert row["included"] == [True, False]
    result["sections"][0]["spans"][0]["length"] = 999
    assert not section_diagnostic(result, task)["resolved"]
    result["sections"] = []
    assert not section_diagnostic(result, task)["resolved"]


def test_resume_checks_figure_bytes_as_well_as_response(tmp_path):
    path = tmp_path / "probe-response.json"
    save(path, {"content": "source"})
    data = b"image"
    image = tmp_path / "probe-figure-0.png"
    image.write_bytes(data)
    receipt = {
        "responseSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "figureAssets": [{"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    }
    assert resume(receipt, path, ["source"])["acceptancePassed"]
    image.write_bytes(b"other")
    with pytest.raises(ValueError, match="figure integrity"):
        resume(receipt, path, ["source"])
