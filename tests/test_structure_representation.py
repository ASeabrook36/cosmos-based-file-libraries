"""Controlled-representation contracts; invented fixtures are not quality evidence."""

import copy
import io

import pytest
from docx import Document

from fli.chunking import TiktokenTokenizer
from fli.structural_enrichment import docx_structure
from fli.structure_lab import digest
from fli.structure_representation import VARIANTS, docx_document, passage_rows, source_relevance, verify_rows
from scripts.retrieval_r0_corpus import normalize, sha256
from scripts.retrieval_r2 import deployment_identity, exact_rank, verify_gold


def fixture():
    doc = Document()
    doc.add_heading("Policy", 1)
    doc.add_paragraph("First rule.")
    doc.add_paragraph("Second condition.")
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).merge(table.cell(0, 2))
    table.cell(1, 0).text = "Amount"
    table.cell(1, 1).text = "12"
    doc.add_heading("Exceptions", 2)
    doc.add_paragraph("Only on weekdays.")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_docx_bridge_keeps_extraction_fixed_and_maps_original_not_xml_paragraphs():
    data = fixture()
    doc = docx_document(data, "d", "policy.docx")
    _, canonical = docx_structure(data, "d", "policy.docx")
    assert doc["canonicalTreeDigest"] == digest(canonical)
    assert [b["text"] for b in doc["blocks"]] == [b["text"] for b in canonical["blocks"]]
    # The fourth top-level paragraph follows six visible table cells. Its original
    # zero-based location must remain 3, not the adapter's XML paragraph number.
    assert doc["blocks"][-2]["originalRegions"][0]["locator"] == {"kind": "paragraph", "index": 3}
    table = next(b for b in doc["blocks"] if b["kind"] == "table")
    cells = table["tableGrid"]["cells"]
    assert cells[1]["start"] == cells[2]["start"] and cells[1]["end"] == cells[2]["end"]
    assert table["text"] == "Name | \nAmount | 12 | \n"


def test_coalescing_changes_only_packing_and_preserves_small_blocks_tables_and_headings():
    doc = docx_document(fixture(), "d", "policy.docx")
    tok = TiktokenTokenizer()
    granular, coalesced = [passage_rows(doc, "lib", name, tok) for name in VARIANTS]
    assert len(coalesced) < len(granular)
    assert all(r["tokenCount"] <= 500 for r in coalesced + granular)
    table_id = next(b["id"] for b in doc["blocks"] if b["kind"] == "table")
    for rows in (granular, coalesced):
        table_rows = [r for r in rows if any(s["blockId"] == table_id for s in r["sourceSpans"])]
        assert len(table_rows) == 1
        assert {s["blockId"] for s in table_rows[0]["sourceSpans"]} == {table_id}
        assert set(r["nodeId"] for r in rows) == {"n1", "n2"}
        verify_rows(doc, rows)
    assert {tuple(s.values()) for r in granular for s in r["sourceSpans"]} == {
        tuple(s.values()) for r in coalesced for s in r["sourceSpans"]
    }


def test_long_unicode_blocks_reconstruct_exactly_and_never_overlap():
    original = Document()
    original.add_heading("Long", 1)
    original.add_paragraph("日本語 🌳 Ελληνικά " * 700)
    output = io.BytesIO()
    original.save(output)
    document = docx_document(output.getvalue(), "d", "long.docx")
    tokenizer = TiktokenTokenizer()
    for variant in VARIANTS:
        rows = passage_rows(document, "lib", variant, tokenizer)
        assert all(r["tokenCount"] <= 500 for r in rows)
        verify_rows(document, rows)
        bad = copy.deepcopy(rows)
        bad[0]["text"] += "invented fact"
        with pytest.raises(ValueError, match="exact source"):
            verify_rows(document, bad)
        with pytest.raises(ValueError, match="overlap"):
            verify_rows(document, rows + rows)
        with pytest.raises(ValueError, match="omitted"):
            verify_rows(document, rows[:-1])


def test_gold_and_blank_table_delivery_use_original_coordinates_not_substrings():
    doc = docx_document(fixture(), "d", "policy.docx")
    tokenizer = TiktokenTokenizer()
    rows = passage_rows(doc, "lib", VARIANTS[0], tokenizer)
    region = {
        "id": "d-t0-r0-c1",
        "kind": "table_cell",
        "table": 0,
        "row": 0,
        "column": 1,
        "sourceSha256": doc["sourceSha256"],
        "normalizedTextSha256": sha256(normalize("").encode()),
    }
    task = {"case": "case", "sourceFacts": [{"sourceId": "d", "regions": [region]}]}
    assert verify_gold([doc], [task]) == 1
    assert source_relevance(task, {"d": doc}, [rows[0]], tokenizer)["deliveredRegions"] == 0
    table = next(b for b in doc["blocks"] if b["kind"] == "table")
    hit = next(r for r in rows if r["sourceSpans"][0]["blockId"] == table["id"])
    assert source_relevance(task, {"d": doc}, [hit], tokenizer)["allGoldDelivered"]
    bad = copy.deepcopy(task)
    bad["sourceFacts"][0]["regions"][0]["normalizedTextSha256"] = sha256(b"filled")
    with pytest.raises(ValueError, match="gold"):
        verify_gold([doc], [bad])
    stale = hit | {"sourceSha256": "stale"}
    with pytest.raises(ValueError, match="stale"):
        source_relevance(task, {"d": doc}, [stale], tokenizer)


def test_exact_reference_uses_cosine_and_stable_ties_and_rejects_invalid_vectors():
    rows = [{"id": name} for name in ("b", "a", "c")]
    vectors = {"a": [1.0, 0.0], "b": [2.0, 0.0], "c": [0.0, 1.0]}
    assert [r["id"] for r in exact_rank(rows, vectors, [0.5, 0.0], 2)] == ["a", "b"]
    with pytest.raises(ValueError, match="zero"):
        exact_rank(rows, vectors, [0.0, 0.0])


def test_embedding_cache_binding_requires_actual_pinned_model_and_deployment():
    from datetime import UTC, datetime

    account = {"id": "/accounts/test", "properties": {"endpoint": "https://example.test"}}
    deployment = {
        "id": "/accounts/test/deployments/embed",
        "name": "embed",
        "sku": {"name": "GlobalStandard", "capacity": 1},
        "properties": {
            "model": {"format": "OpenAI", "name": "text-embedding-3-large", "version": "1"},
            "provisioningState": "Succeeded",
            "versionUpgradeOption": "NoAutoUpgrade",
        },
    }
    receipt = {"capturedAt": datetime.now(UTC).isoformat(), "account": account, "deployment": deployment}
    first = deployment_identity(receipt, "https://example.test", "embed")
    assert len(first) == 64
    with pytest.raises(ValueError, match="endpoint"):
        deployment_identity(receipt, "https://another.test", "embed")
    with pytest.raises(ValueError, match="deployment"):
        deployment_identity(receipt, "https://example.test", "other")
    receipt["deployment"]["properties"]["versionUpgradeOption"] = "OnceNewDefaultVersionAvailable"
    with pytest.raises(ValueError, match="settings"):
        deployment_identity(receipt, "https://example.test", "embed")


def test_binary_vector_cache_keeps_completed_batches_and_rejects_conflicts(tmp_path):
    from scripts.structure_lab import VectorCache

    path = tmp_path / "vectors.sqlite3"
    cache = VectorCache(path)
    cache.put_batch({"a": [0.1] * 3072})
    cache.close()
    cache = VectorCache(path)
    assert "a" in cache and len(cache) == 1
    assert cache["a"] == [0.1] * 3072
    with pytest.raises(ValueError, match="conflict"):
        cache.put_batch({"b": [0.2] * 3072, "a": [0.3] * 3072})
    assert "b" not in cache and cache["a"] == [0.1] * 3072
    with pytest.raises(ValueError, match="invalid"):
        cache.put_batch({"c": [float("nan")] * 3072})
    cache.close()


def test_selection_cannot_hide_lost_region_with_an_equal_count_replacement():
    from scripts.retrieval_r2 import selection_grade

    rows = [
        {"case": "case", "variant": v, "deliveredRegionIds": ids, "evidenceContextLimited": False}
        for v, ids in zip(VARIANTS, (["a", "b"], ["b", "c"]), strict=True)
    ]
    grade = selection_grade(rows, {VARIANTS[0]: 10, VARIANTS[1]: 5})
    assert not grade["candidatePassedPredeclaredGate"]
    assert grade["lostRegionIdsByCase"] == {"case": ["a"]}
    assert grade["gainedRegionIdsByCase"] == {"case": ["c"]}
    rows[1]["deliveredRegionIds"] = ["a", "b", "c"]
    assert selection_grade(rows, {VARIANTS[0]: 10, VARIANTS[1]: 5})["candidatePassedPredeclaredGate"]
    rows[1]["evidenceContextLimited"] = True
    assert not selection_grade(rows, {VARIANTS[0]: 10, VARIANTS[1]: 5})["candidatePassedPredeclaredGate"]
    with pytest.raises(ValueError, match="missing"):
        selection_grade(rows[:1], {VARIANTS[0]: 10, VARIANTS[1]: 5})
