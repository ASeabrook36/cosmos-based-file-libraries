from __future__ import annotations

import pytest

from fli.errors import ApiError, library_not_found
from fli.generation import filter_visible, is_obsolete, is_visible, latest_generation
from fli.ids import chunk_id, original_blob_path, original_generation


def test_chunk_ids_are_deterministic_and_encode_generation_and_index():
    assert chunk_id("d1", 2, 7) == chunk_id("d1", 2, 7) == "d1:2:7"
    assert len({chunk_id("d1", g, i) for g in (1, 2) for i in range(3)}) == 6  # no collisions across generations


@pytest.mark.parametrize("generation,index", [(0, 0), (1, -1)])
def test_chunk_id_refuses_impossible_positions(generation, index):
    with pytest.raises(ValueError):
        chunk_id("d1", generation, index)


def test_blob_path_layout():
    assert original_blob_path("L", "D", 1, "a b.pdf") == "L/D/original/1/a b.pdf"
    assert original_blob_path("L", "D", 12, "a b.pdf") == "L/D/original/12/a b.pdf"
    with pytest.raises(ValueError):
        original_blob_path("L", "D", 0, "a.pdf")


@pytest.mark.parametrize(
    "path,generation",
    [
        ("L/D/original/3/a b.pdf", 3),
        ("L/D/original/12/2024", 12),
        ("L/D/original/a b.pdf", 1),  # stored before Phase 3: no generation segment
        ("L/D/original/2024", 1),  # a legacy file named like a number is still legacy
    ],
)
def test_each_original_knows_its_generation(path, generation):
    assert original_generation(path) == generation


def test_the_newest_generation_survives_records_written_before_the_counter():
    assert latest_generation({"generation": 4, "activeGeneration": 2, "pendingGeneration": None}) == 4
    assert latest_generation({"activeGeneration": 1, "pendingGeneration": None}) == 1
    assert latest_generation({"activeGeneration": None, "pendingGeneration": None}) == 1  # a failed first upload


@pytest.mark.parametrize(
    "generation,obsolete",
    [(1, True), (2, False), (3, True), (4, False), (5, False)],
)
def test_only_generations_a_read_proves_dead_are_obsolete(generation, obsolete):
    # Active 2, pending 4, newest 4: 1 was replaced, 3 was superseded, 5 is newer than this read knows.
    document = {"generation": 4, "activeGeneration": 2, "pendingGeneration": 4}
    assert is_obsolete(generation, document) is obsolete


def test_a_failed_newest_generation_is_obsolete():
    assert is_obsolete(3, {"generation": 3, "activeGeneration": 2, "pendingGeneration": None})


def chunk(doc: str, generation: int, index: int = 0) -> dict:
    return {"id": chunk_id(doc, generation, index), "documentId": doc, "generation": generation}


def test_generation_check_drops_replaced_deleted_and_never_committed_documents():
    documents = {
        "replaced": {"status": "ready", "activeGeneration": 2},
        "deleted": {"status": "deleting", "activeGeneration": 1},
        "first-upload": {"status": "processing", "activeGeneration": None, "pendingGeneration": 1},
        "current": {"status": "ready", "activeGeneration": 1},
    }
    chunks = [
        chunk("replaced", 1),  # old generation, still stored until the purge runs
        chunk("replaced", 2),
        chunk("deleted", 1),
        chunk("first-upload", 1),  # half-written: nothing is active yet
        chunk("current", 1),
        chunk("ghost", 1),  # no document record at all
    ]
    kept = filter_visible(chunks, documents, limit=10)
    assert [c["id"] for c in kept] == ["replaced:2:0", "current:1:0"]


@pytest.mark.parametrize("status", ["queued", "processing", "failed"])
def test_the_old_version_stays_searchable_while_a_replacement_is_pending_or_after_it_fails(status):
    document = {"status": status, "activeGeneration": 1, "pendingGeneration": 2 if status != "failed" else None}
    assert is_visible(chunk("d", 1), document)
    assert not is_visible(chunk("d", 2), document)  # the new generation's chunks, partly written


def test_a_commit_switches_visibility_to_the_new_generation():
    after = {"status": "ready", "activeGeneration": 2, "pendingGeneration": None}
    assert is_visible(chunk("d", 2), after) and not is_visible(chunk("d", 1), after)


def test_limit_applies_after_dropping():
    documents = {"d": {"status": "ready", "activeGeneration": 1}}
    kept = filter_visible([chunk("x", 1), chunk("d", 1, 0), chunk("d", 1, 1), chunk("d", 1, 2)], documents, limit=2)
    assert [c["chunkIndex" if "chunkIndex" in c else "id"] for c in kept] == ["d:1:0", "d:1:1"]


def test_api_error_body_shape():
    err = library_not_found("L1")
    assert isinstance(err, ApiError) and err.status == 404
    assert err.body() == {"error": {"code": "library_not_found", "message": "library L1 does not exist"}}


@pytest.mark.parametrize(
    "name,expected",
    [
        ("../../other-lib/x.pdf", "x.pdf"),
        ("C:\\Users\\me\\report.docx", "report.docx"),
        ("..", "file"),
        ("a\x00b\nc.txt", "abc.txt"),
        ("Q4 review.pptx", "Q4 review.pptx"),
    ],
)
def test_uploaded_names_cannot_leave_the_document_prefix(name, expected):
    assert original_blob_path("L", "D", 1, name) == f"L/D/original/1/{expected}"
