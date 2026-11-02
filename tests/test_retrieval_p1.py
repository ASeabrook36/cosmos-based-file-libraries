"""P1 comparison attribution, fixed controls and evidence receipts without cloud."""

import copy
import json

import pytest

from fli.reference_format import model_messages, pack
from fli.selector_hints import tool_hints
from scripts import retrieval_p1 as p1
from scripts.evaluate_agent_retrieval import snapshot
from scripts.retrieval_controls import AGENT_INSTRUCTIONS


class Index:
    def __init__(self, cell):
        self.cell = cell

    def get_library(self, library):
        return {"id": library, "name": "Frozen pilot"}

    def list_documents(self, library):
        return [{"id": "document", "activeGeneration": 1}]

    def tools(self, library, agent):
        definitions = [{"function": {"name": "read_documents_library_frozen", "description": "Read source."}}]
        return tool_hints(definitions) if self.cell == "selectors-only" else definitions


def configuration():
    return {
        "model": {"deployment": "fixed", "model": "gpt-6.1-sol", "version": "2026-09-29"},
        "agent": {"id": "agent", "name": "Agent", "instructions": AGENT_INSTRUCTIONS},
        "libraryId": "new-pilot",
        "indexUrl": "http://127.0.0.1:8122",
        "searchMode": "vector",
    }


def test_p1_schedule_separates_changes_uses_same_sources_and_preserves_questions(tmp_path, monkeypatch):
    manifest, tasks = p1.frozen_pack(p1.REPO / "docs/testing/retrieval-r0")
    seen = []
    monkeypatch.setattr(p1, "make_index", lambda config, cell: Index(cell))
    monkeypatch.setattr(p1, "bound_deployment", lambda *args: {"binding": "same"})
    monkeypatch.setattr(p1, "validate_sources", lambda *args: None)
    monkeypatch.setattr(p1, "default_chat_factory", lambda *args: object())

    def replay(index, chat, agent, cases, output, budget):
        assert budget == 200000
        assert all(c["split"] == "development" for c in cases)
        seen.append(index.cell)
        output.mkdir()
        before = snapshot(index, agent)
        rows = [{"case": c["case"], "turn": 1, "question": c["question"]} for c in cases]
        for name, value in [
            ("run-status", {"status": "complete", "completedTurns": len(rows), "budget": budget}),
            ("cases", cases),
            ("source-snapshot", before),
            ("snapshot-check", {"unchanged": True, "before": before, "after": before}),
            ("results", rows),
        ]:
            p1.save(output / f"{name}.json", value)
        return rows

    monkeypatch.setattr(p1, "replay", replay)
    config = configuration()
    result = p1.run(config, {}, tmp_path / "run")
    assert seen == ["baseline", "format-only", "selectors-only", "selectors-only", "format-only", "baseline"]
    assert result["completedTurns"] == 18 and result["status"] == "pending_deployment_recheck"
    controls = p1.read(tmp_path / "run/controls.json")
    assert controls["contextAllowancePerRun"] == 200000 and controls["agent"]["instructions"] == AGENT_INSTRUCTIONS
    assert controls["cases"] == [c for c in tasks if c["case"] in {"r0-01", "r0-11", "r0-17"}]
    assert p1.finalize(tmp_path / "run", {})["status"] == "complete"


@pytest.mark.parametrize("change", ["prompt", "query", "model"])
def test_control_mutations_fail_before_source_calls(tmp_path, monkeypatch, change):
    config = configuration()
    if change == "prompt":
        config["agent"]["instructions"] = "changed"
    elif change == "query":
        config["searchMode"] = "hybrid"
    else:
        config["model"]["version"] = "changed"
    monkeypatch.setattr(p1, "make_index", lambda *args: pytest.fail("must not access sources"))
    with pytest.raises(ValueError):
        p1.run(config, {}, tmp_path / "run")


def test_wire_recording_checks_actual_format_and_keeps_only_private_capture(tmp_path):
    source = {"mode": "selected", "documents": [{"text": "Exact original"}], "complete": False}
    plain = [{"role": "tool", "content": json.dumps(source)}]
    observed = []
    chat = p1.RecordedChat(lambda messages, tools: observed.append(copy.deepcopy(messages)), tmp_path, "format-only")
    chat(model_messages(plain), [])
    assert json.loads(observed[0][0]["content"]) == pack(source)
    record = p1.read(tmp_path / "wire-receipts.json")[0]
    assert record["referenceToolMessages"] == 1
    assert "Exact original" not in json.dumps(record)
    with pytest.raises(ValueError, match="format differs"):
        chat(plain, [])
