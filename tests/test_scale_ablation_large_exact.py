"""Offline oracle math, population binding and non-mutating reference repair."""

import copy
import hashlib
import json
from datetime import UTC, datetime

import numpy as np
import pytest

from scripts import scale_ablation_large_exact as exact


def save(path, value):
    path.write_text(json.dumps(value))


def fixture(tmp_path, monkeypatch):
    state = tmp_path / "private"
    state.mkdir()
    monkeypatch.setattr(exact, "DIMENSIONS", 3)
    monkeypatch.setattr(exact, "SIZES", dict.fromkeys(exact.SIZES, 12))
    labels = ["scale-p01", "scale-p10", *exact.NEW]
    plan = [(lib, f"{lib}-d{i // 10:04d}", i % 10) for lib in labels[:2] for i in range(12)]
    monkeypatch.setattr(exact, "PREFIX_ROWS", len(plan))
    monkeypatch.setattr(exact, "_prefix_plan", lambda: plan)
    prefix = b"".join(
        (json.dumps({"embedding": [1, (i + 1) / 100, ((i + 1) / 100) ** 2]}) + "\n").encode() for i in range(len(plan))
    )
    corpus = state / "corpus.ndjson"
    corpus.write_bytes(prefix)
    monkeypatch.setattr(exact, "PREFIX_BYTES", len(prefix))
    manifest = {
        "complete": True,
        "seed": 20261029,
        "preparationStartedAt": datetime.now(UTC).isoformat(),
        "hashes": {"originalPrefixSha256": hashlib.sha256(prefix).hexdigest()},
        "libraries": {},
    }
    for ordinal, lib in enumerate(exact.NEW):
        path = state / f"{lib}.ndjson"
        with path.open("w") as handle:
            for i in range(12):
                angle = (25 + ordinal * 12 + i) / 100
                body = {
                    "id": f"{lib}-d{i // 10:04d}:1:{i % 10}",
                    "libraryId": lib,
                    "embedding": [1, angle, angle * angle],
                    "text": "PRIVATE_TEXT_NOT_FOR_PUBLIC_SUMMARY",
                }
                handle.write(json.dumps(body) + "\n")
        manifest["libraries"][lib] = {"size": 12, "sha256": exact._sha(path), "payloadFile": str(path)}
    manifest_path = state / "manifest.json"
    save(manifest_path, manifest)
    config = {"prior": {"corpus": str(corpus)}, "inputManifest": str(manifest_path), "selected": list(exact.NEW)}
    save(state / "status.json", {"selectedLibraries": list(exact.NEW)})
    save(state / "question-vectors.json", {lib: [[1, 0, 0]] * 20 for lib in labels})
    save(state / "question-vector-hash.json", {"sha256": exact._sha(state / "question-vectors.json")})
    save(
        state / "binding.json",
        {
            "configSha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            "inputManifestSha256": exact._sha(manifest_path),
            "sourceFiles": {"scripts/scale_probe.py": exact._sha(exact.scale_probe.__file__)},
        },
    )
    return config, state


def raw_for(oracle, library="large-p025000", *, all_twelve=True):
    ids = [f"{library}-d{i // 10:04d}:1:{i % 10}" for i in reversed(range(12))]
    if not all_twelve:
        ids = list(reversed(oracle["references"][library][0]["result_ids"]))
    return {
        "arm": "unsplit",
        "library": library,
        "kind": "exact",
        "shape": "routed",
        "query": 0,
        "run": 0,
        "error": None,
        "request_charge": 100,
        "retrieved_document_count": 12,
        "milliseconds": 55.2,
        "http_responses": 2,
        "result_ids": ids,
        "result_bindings": [{"id": v, "documentId": v.split(":")[0], "generation": 1} for v in ids],
        "scores": [],
        "recordedAt": "2026-11-13T00:00:00Z",
        "private": "PRIVATE_BINDING",
    }


def test_streamed_oracle_matches_independent_dense_math(tmp_path, monkeypatch):
    config, state = fixture(tmp_path, monkeypatch)
    oracle = exact.build_oracle(config, state)
    assert oracle["safe"]["populationVectors"] == 72
    assert oracle["safe"]["libraryReferenceQuestions"] == 120
    assert len(oracle["references"]["global"]) == 1
    assert oracle["safe"]["minimumBoundaryMargin"] > exact.BOUNDARY_TOLERANCE
    lib = "large-p025000"
    bodies = [json.loads(v) for v in (state / f"{lib}.ndjson").read_text().splitlines()]
    matrix = np.asarray([v["embedding"] for v in bodies], dtype=np.float16).astype(np.float64)
    scores = 1 - matrix[:, 0] / np.sqrt(np.sum(matrix * matrix, axis=1))
    expected = [bodies[i]["id"] for i in np.argsort(scores)[:10]]
    assert oracle["references"][lib][0]["result_ids"] == expected
    encoded = json.dumps(oracle["safe"])
    assert "PRIVATE_" not in encoded and str(state) not in encoded and "result_ids" not in encoded
    assert exact.build_oracle(config, state) == oracle


def test_derive_checks_union_and_preserves_every_other_raw_field(tmp_path, monkeypatch):
    config, state = fixture(tmp_path, monkeypatch)
    oracle = exact.build_oracle(config, state)
    raw = raw_for(oracle)
    before = copy.deepcopy(raw)
    result = exact.derive_reference(raw, oracle)
    assert raw == before
    assert result["result_ids"] == oracle["references"][raw["library"]][0]["result_ids"]
    assert result["referenceDerivation"]["rawCandidateCount"] == 12
    assert all(result[key] == value for key, value in raw.items() if key not in {"result_ids", "result_bindings"})
    assert [v["id"] for v in result["result_bindings"]] == result["result_ids"]
    already_ten = exact.derive_reference(raw_for(oracle, all_twelve=False), oracle)
    assert already_ten["referenceDerivation"]["rawWasAlreadyExactTopTen"]


@pytest.mark.parametrize("bad", ["missing", "duplicate", "ownership", "error", "metric", "wrong-kind", "tie"])
def test_invalid_union_or_unresolved_boundary_stops(tmp_path, monkeypatch, bad):
    config, state = fixture(tmp_path, monkeypatch)
    oracle = exact.build_oracle(config, state)
    raw = raw_for(oracle)
    if bad == "missing":
        missing = oracle["references"][raw["library"]][0]["result_ids"][0]
        raw["result_ids"].remove(missing)
    elif bad == "duplicate":
        raw["result_ids"][-1] = raw["result_ids"][0]
    elif bad == "ownership":
        raw["result_ids"][-1] = "scale-p01-d0000:1:0"
    elif bad == "error":
        raw["error"] = "retained failure"
    elif bad == "metric":
        raw["milliseconds"] = None
    elif bad == "wrong-kind":
        raw["kind"] = "vector"
    else:
        oracle["references"][raw["library"]][0]["boundaryMargin"] = 0
        oracle["oracleSha256"] = exact._oracle_hash(oracle)
    with pytest.raises(exact.OracleError):
        exact.derive_reference(raw, oracle)


def test_original_oracle_cache_and_question_bindings_are_immutable(tmp_path, monkeypatch):
    config, state = fixture(tmp_path, monkeypatch)
    oracle = exact.build_oracle(config, state)
    tampered = copy.deepcopy(oracle)
    tampered["references"]["global"][0]["result_ids"].reverse()
    with pytest.raises(exact.OracleError, match="digest"):
        exact.validate_oracle(config, state, tampered)
    (state / "question-vectors.json").write_text("{}")
    with pytest.raises(exact.OracleError, match="query-vector"):
        exact.validate_oracle(config, state, oracle)


def test_numeric_input_sensitivity_is_reported_without_altering_f16_truth(tmp_path, monkeypatch):
    config, state = fixture(tmp_path, monkeypatch)
    oracle = exact.build_oracle(config, state)
    ref = oracle["references"]["large-p025000"][0]
    ref["numericVariants"]["originalFloat64"]["sameTopTenSet"] = False
    oracle["oracleSha256"] = exact._oracle_hash(oracle)
    value = exact.derive_reference(raw_for(oracle), oracle)
    assert not value["referenceDerivation"]["numericModelTopTenSetsAgree"]
