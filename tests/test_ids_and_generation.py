from __future__ import annotations

import pytest

from fli.errors import ApiError, library_not_found
from fli.generation import filter_visible, is_visible
from fli.ids import chunk_id, original_blob_path


def test_chunk_ids_are_deterministic_and_encode_generation_and_index():
    assert chunk_id("d1", 2, 7) == chunk_id("d1", 2, 7) == "d1:2:7"
    assert len({chunk_id("d1", g, i) for g in (1, 2) for i in range(3)}) == 6  # no collisions across generations


@pytest.mark.parametrize("generation,index", [(0, 0), (1, -1)])
def test_chunk_id_refuses_impossible_positions(generation, index):
    with pytest.raises(ValueError):
        chunk_id("d1", generation, index)


def test_blob_path_layout():
    assert original_blob_path("L", "D", "a b.pdf") == "L/D/original/a b.pdf"


def chunk(doc: str, generation: int, index: int = 0) -> dict:
    return {"id": chunk_id(doc, generation, index), "documentId": doc, "generation": generation}


def test_generation_check_drops_replaced_deleted_and_unfinished_documents():
    documents = {
        "replaced": {"status": "ready", "activeGeneration": 2},
        "deleted": {"status": "deleting", "activeGeneration": 1},
        "processing": {"status": "processing", "activeGeneration": 1, "pendingGeneration": 2},
        "current": {"status": "ready", "activeGeneration": 1},
    }
    chunks = [
        chunk("replaced", 1),  # old generation, still stored until the purge runs
        chunk("replaced", 2),
        chunk("deleted", 1),
        chunk("processing", 2),  # being written: not visible until commit
        chunk("current", 1),
        chunk("ghost", 1),  # no document record at all
    ]
    kept = filter_visible(chunks, documents, limit=10)
    assert [c["id"] for c in kept] == ["replaced:2:0", "current:1:0"]


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
