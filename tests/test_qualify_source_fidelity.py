"""Offline qualification provenance and immutable-input controls with invented text."""

import base64
import copy
import hashlib
import json

import pytest

from fli.checkpoints import digest
from fli.ingestion import job, profile
from scripts.qualify_source_fidelity import execute
from scripts.source_structure_probe import sha
from tests.test_modular_ingestion import output
from tests.test_source_fidelity import analysis, fixture, save


def envelope(tmp_path):
    source = save(fixture())
    recipe = profile("microsoft-indexer-layout-v1")
    recipe["embedding"]["modelRevision"] = "test-revision"
    request = job("library", "document", 1, {"sha256": sha(source), "blobPath": "original"}, recipe, 0)
    result = output(request)
    result["sourceBase64"] = base64.b64encode(source).decode()
    result["analysis"] = {"analyzeResult": analysis()}
    extraction = {
        "sourceBase64": result["sourceBase64"],
        "analysis": result["analysis"],
        "analysisOutput": {"body": result["analysis"]},
    }
    sections = [{"text": result["analysis"]["analyzeResult"]["content"], "contentKind": "text", "location": {}}]
    values = {
        "original": source,
        "result": json.dumps(result).encode(),
        "extraction": json.dumps(extraction).encode(),
        "sections": json.dumps(sections).encode(),
    }
    files = {}
    for name, data in values.items():
        path = tmp_path / name
        path.write_bytes(data)
        files[name] = {"path": str(path), "sha256": sha(data)}
    inputs = {
        "schema": "fli.source-structure-probe.inputs.v1",
        "sourceId": "invented-case",
        "files": files,
        "locator": {"table": 0, "row": 0, "column": 1},
        "normalizedTextSha256": hashlib.sha256(b"Conditions First Nested Only when approved.").hexdigest(),
    }
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(inputs))
    return path, inputs, request, values


def test_qualification_rebinds_execution_identity_and_keeps_originals_immutable(tmp_path):
    path, inputs, old_job, values = envelope(tmp_path)
    destination = tmp_path / "output.json"
    public = execute(path, destination)
    stored = json.loads(destination.read_text())
    new_job = stored["counterfactualJob"]
    unsigned = copy.deepcopy(new_job)
    unsigned.pop("executionId")
    assert new_job["executionId"] == digest(unsigned) and new_job["executionId"] != old_job["executionId"]
    assert public["counterfactualExecutionIdChanged"] and public["vectorsUnchanged"]
    assert public["cloudCalls"] == public["modelCalls"] == 0
    assert public["observations"]["targetFinalParagraphUnnumbered"]
    assert str(tmp_path) not in json.dumps(public)
    for name, data in values.items():
        assert (tmp_path / name).read_bytes() == data
    with pytest.raises(FileExistsError):
        execute(path, destination)


def test_qualification_rejects_changed_input_before_writing_result(tmp_path):
    path, _, _, _ = envelope(tmp_path)
    (tmp_path / "original").write_bytes(b"changed")
    with pytest.raises(ValueError, match="input hash changed"):
        execute(path, tmp_path / "output.json")
    assert not (tmp_path / "output.json").exists()
