"""Model, transport and context comparisons preserve independent experimental controls."""

import copy
import json

import pytest

from scripts import compare_agent_models as comparison


def config():
    return {
        "models": {
            "mini": {"deployment": "mini-test", "model": "gpt-5-mini", "version": "fixed-mini"},
            "strong": {"deployment": "strong-test", "model": "gpt-6.1-sol", "version": "fixed-strong"},
        },
        "libraries": {role: role + "-id" for role in comparison.ORIGINAL + comparison.HELDOUT},
        "agent": {"id": "review-agent", "name": "Reviewer", "instructions": "Read and cite original evidence."},
    }


CASES = [{"case": "deadline", "question": "Deadline?"}, {"case": "compare-options", "question": "Compare?"}]
HELDOUT = [{"case": "heldout-form-comparison", "question": "Form fields?"}]


class Index:
    def __init__(self):
        self.generation = 1
        self.status = "ready"

    def get_library(self, library):
        return {"name": "Documents", "description": "Original reports"}

    def list_documents(self, library):
        corpus = "heldout" if library.startswith("heldout") else "original"
        return [
            {
                "id": library + "-doc",
                "fileName": corpus + ".docx",
                "sha256": corpus + "-bytes",
                "status": self.status,
                "activeGeneration": self.generation,
            }
        ]

    def tools(self, library, agent):
        return {"definition": "same retrieval tools", "library": library}


def test_matrix_has_separate_bridge_model_and_context_stages():
    runs = comparison.schedule()
    assert len(runs) == 40
    assert len({row["id"] for row in runs}) == 40
    assert sum(1 if row["pack"] == "heldout" else 2 for row in runs) == 68
    assert all(row["stage"] == "transport-bridge" for row in runs[:4])
    assert all(row["stage"] == "model" for row in runs[4:-4])
    assert all(row["stage"] == "context" for row in runs[-4:])
    assert all(row["budget"] == 24000 for row in runs[:-4])
    assert all(row["budget"] == 64000 and row["model"] == "mini" for row in runs[-4:])
    original = [r for r in runs if r["stage"] == "model" and r["library"] == "current"]
    assert [row["model"] for row in original] == ["mini", "strong", "strong", "mini"]
    for row in runs[-4:]:
        assert any(
            other["stage"] == "model"
            and other["model"] == row["model"]
            and other["library"] == row["library"]
            and other["repeat"] == row["repeat"]
            for other in runs
        )


def test_shared_agent_questions_fresh_clients_and_frozen_manifest(tmp_path):
    calls, chats = [], []
    original_config = config()
    before_config = copy.deepcopy(original_config)

    def factory(model, transport):
        chat = object()
        chats.append((chat, model, transport))
        return chat

    def replay(index, chat, agent, cases, output, budget):
        calls.append((agent, copy.deepcopy(cases), budget, chat))
        # Even an incidental mutation in a delegated caller cannot change later cases.
        cases[0]["question"] = "changed only in this invocation"
        return [None] * len(cases)

    result = comparison.compare(
        Index(),
        original_config,
        CASES,
        HELDOUT,
        tmp_path,
        chat_factory=factory,
        replay_fn=replay,
        code_hash_fn=lambda: {"agent.py": "fixed"},
    )
    assert result == {"status": "complete", "completedTurns": 68, "unchanged": True}
    assert original_config == before_config
    assert len({id(row[0]) for row in chats}) == 40
    assert all(len(row[0]["libraryIds"]) == 1 for row in calls)
    assert all(row[0]["instructions"] == before_config["agent"]["instructions"] for row in calls)
    assert all(row[1][0]["question"] in {"Deadline?", "Form fields?"} for row in calls)
    controls = json.loads((tmp_path / "controls.json").read_text())
    assert controls["plannedTurns"] == 68
    assert controls["codeSha256"] == {"agent.py": "fixed"}
    assert controls["cases"]["original"] == CASES
    assert controls["config"] == before_config
    review = json.loads((tmp_path / "quality-review.json").read_text())
    assert len(review) == 68 and all(row["taskSuccess"] is None for row in review)


@pytest.mark.parametrize("change", ["source", "code"])
def test_drift_between_runs_stops_comparison_without_erasing_evidence(tmp_path, change):
    index = Index()
    code = {"agent.py": "before"}

    def replay(index, chat, agent, cases, output, budget):
        output.mkdir()
        (output / "results.json").write_text('[{"answer":"retained"}]')
        if change == "source":
            index.generation += 1
        else:
            code["agent.py"] = "after"
        return [None, None]

    with pytest.raises(RuntimeError, match="confounded"):
        comparison.compare(
            index,
            config(),
            CASES,
            HELDOUT,
            tmp_path,
            chat_factory=lambda *_: object(),
            replay_fn=replay,
            code_hash_fn=lambda: dict(code),
        )
    status = json.loads((tmp_path / "run-status.json").read_text())
    assert status["status"] == "failed" and status["completedTurns"] == 2
    assert len(list(tmp_path.glob("*/results.json"))) == 1
    assert json.loads((tmp_path / "controls.json").read_text())["codeSha256"] == {"agent.py": "before"}


@pytest.mark.parametrize("error", [RuntimeError("offline"), KeyboardInterrupt()])
def test_partial_run_failure_or_interrupt_is_preserved(tmp_path, error):
    def replay(index, chat, agent, cases, output, budget):
        output.mkdir()
        (output / "run-status.json").write_text('{"completedTurns":1}')
        (output / "results.json").write_text('[{"answer":"retained"}]')
        raise error

    with pytest.raises(type(error)):
        comparison.compare(
            Index(),
            config(),
            CASES,
            HELDOUT,
            tmp_path,
            chat_factory=lambda *_: object(),
            replay_fn=replay,
            code_hash_fn=lambda: {},
        )
    status = json.loads((tmp_path / "run-status.json").read_text())
    assert status["completedTurns"] == 1
    assert status["status"] == ("interrupted" if isinstance(error, KeyboardInterrupt) else "failed")
    assert len(list(tmp_path.glob("*/results.json"))) == 1
    with pytest.raises(ValueError, match="empty"):
        comparison.compare(Index(), config(), CASES, HELDOUT, tmp_path)


@pytest.mark.parametrize("damage", ["version", "duplicates", "secret", "agent", "source"])
def test_missing_controls_or_unready_sources_are_rejected(tmp_path, damage):
    settings, index = config(), Index()
    if damage == "version":
        del settings["models"]["mini"]["version"]
    elif damage == "duplicates":
        settings["libraries"]["current"] = settings["libraries"]["complete-custom"]
    elif damage == "secret":
        settings["apiKey"] = "do not store"
    elif damage == "agent":
        settings["agent"].pop("instructions")
    else:
        index.status = "processing"
    with pytest.raises(ValueError):
        comparison.compare(index, settings, CASES, HELDOUT, tmp_path, code_hash_fn=lambda: {})
