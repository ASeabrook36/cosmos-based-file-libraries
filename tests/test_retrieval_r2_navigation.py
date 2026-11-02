"""Navigation control tests; synthetic vectors are never experimental evidence."""

import copy
import json

import pytest

from fli.structure_lab import digest
from fli.structure_representation import VARIANTS
from scripts import retrieval_r2_navigation as nav
from scripts.retrieval_r0_corpus import sha256


def test_navigation_plan_follows_passage_decision_and_freezes_candidate_budget(tmp_path, monkeypatch):
    source = {"digest": "source"}
    monkeypatch.setattr(nav, "load_source", lambda _: source)
    source_dir, output = tmp_path / "source", tmp_path / "output"
    source_dir.mkdir()
    output.mkdir()
    (source_dir / "r2-evaluation.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "sourceArtifactDigest": "source",
                "candidatePassedPredeclaredGate": False,
            }
        )
    )
    (source_dir / "r2-vectors.json").write_text(json.dumps({"arraySha256": "unchanged"}))
    nav.plan(source_dir, output)
    plan = json.loads((output / "navigation-plan.json").read_text())
    assert plan["selectedRepresentation"] == VARIANTS[0]
    assert plan["passageVectorArraySha256"] == "unchanged"
    assert plan["heldoutExecuted"] is False
    assert set(plan["sourceIds"]) == {"d06", "d07", "d08"}
    assert all(sum(arm.values()) == 8 for arm in plan["arms"].values())
    assert plan["evidenceOnlyTokenAllowance"] == 200000
    with pytest.raises(ValueError, match="fresh"):
        nav.plan(source_dir, output)


def test_navigation_inputs_reject_changed_passage_decision_or_vector_bytes(tmp_path, monkeypatch):
    source_dir, output = tmp_path / "source", tmp_path / "output"
    source_dir.mkdir()
    output.mkdir()
    source = {
        "digest": "source",
        "bundle": {"documents": [{"id": key} for key in nav.SOURCES]},
        "tasks": [{"case": key} for key in nav.CASES],
    }
    monkeypatch.setattr(nav, "load_source", lambda _: source)
    decision = {"status": "complete", "sourceArtifactDigest": "source", "candidatePassedPredeclaredGate": True}
    (source_dir / "r2-evaluation.json").write_text(json.dumps(decision))
    raw = b"frozen vector fixture"
    (source_dir / "r2-vectors.npy").write_bytes(raw)
    vectors = {"arrayFile": "r2-vectors.npy", "arraySha256": sha256(raw)}
    vectors["digest"] = digest(vectors)
    (source_dir / "r2-vectors.json").write_text(json.dumps(vectors))
    nav.plan(source_dir, output)
    assert nav.inputs(source_dir, output)[0]["selectedRepresentation"] == VARIANTS[1]
    changed = copy.deepcopy(decision) | {"candidatePassedPredeclaredGate": False}
    (source_dir / "r2-evaluation.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="decision changed"):
        nav.inputs(source_dir, output)
    (source_dir / "r2-evaluation.json").write_text(json.dumps(decision))
    (source_dir / "r2-vectors.npy").write_bytes(b"reembedded")
    with pytest.raises(ValueError, match="vector bytes changed"):
        nav.inputs(source_dir, output)
