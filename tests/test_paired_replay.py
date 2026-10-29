"""Matched replay stays in the existing harness and refuses invalid comparison inputs."""

import copy
import json

import pytest

from scripts import evaluate_agent_retrieval as evaluation


class Index:
    def __init__(self):
        self.rows = {
            key: [
                {
                    "id": key + "-doc",
                    "sourceId": "common",
                    "sha256": "source-hash",
                    "status": "ready",
                    "activeGeneration": 1,
                    "contentRevision": 2,
                }
            ]
            for key in ["custom", "microsoft"]
        }

    def get_experiment(self, experiment_id):
        return {"members": {key: {"libraryId": key} for key in self.rows}}

    def list_documents(self, library):
        return copy.deepcopy(self.rows[library])

    def get_library(self, library):
        return {"name": "Matched reports", "description": "Reports"}


CONTROLS = {
    "modelRevision": "test",
    "searchMode": "vector",
    "backgroundLoad": "stable isolated",
    "cacheState": "unknown",
}


def test_alternating_runs_have_one_library_and_fresh_chat_each_time(tmp_path, monkeypatch):
    calls, chats = [], []

    def replay(index, chat, agent, cases, output, budget):
        calls.append((chat, agent, cases, budget))

    def factory():
        chat = object()
        chats.append(chat)
        return chat

    monkeypatch.setattr(evaluation, "replay", replay)
    metadata = evaluation.replay_pair(
        Index(),
        factory,
        {"libraryIds": ["irrelevant"], "instructions": "same"},
        "exp",
        [
            {
                "case": "rainfall",
                "question": "What changed?",
                "sourceFacts": [{"sourceId": "common", "fact": "salinity fell"}],
            }
        ],
        tmp_path,
        repeats=2,
        index_ready=True,
        run_controls=CONTROLS,
    )
    assert [call[1]["libraryIds"] for call in calls] == [["custom"], ["microsoft"], ["microsoft"], ["custom"]]
    assert len({id(chat) for chat in chats}) == 4
    assert metadata["sourceUnchanged"] is True
    review = json.loads((tmp_path / "quality-review.json").read_text())
    assert review[0]["reviewStatus"] == "unreviewed" and review[0]["taskSuccess"] is None


@pytest.mark.parametrize("damage", ["not-ready", "bytes", "metadata", "pending"])
def test_unmatched_or_unready_sources_cannot_be_compared(tmp_path, damage):
    index = Index()
    if damage == "bytes":
        index.rows["microsoft"][0]["sha256"] = "different"
    elif damage == "metadata":
        index.rows["microsoft"][0]["facets"] = ["region=other"]
    elif damage == "pending":
        index.rows["microsoft"][0]["pendingGeneration"] = 2
    with pytest.raises(ValueError):
        evaluation.replay_pair(
            index, object, {}, "exp", [], tmp_path, index_ready=damage != "not-ready", run_controls=CONTROLS
        )


def test_existing_replay_resets_context_between_cases_but_preserves_questions_within_case(tmp_path, monkeypatch):
    from types import SimpleNamespace

    histories = []

    def run(chat, index, agent, history, question, **kwargs):
        histories.append(copy.deepcopy(history))
        return SimpleNamespace(answer="answer", searches=[], messages=[])

    monkeypatch.setattr(evaluation, "run_turn", run)
    monkeypatch.setattr(evaluation, "metrics", lambda turn: {"measured": True})
    evaluation.replay(
        Index(),
        object(),
        {"libraryIds": ["custom"]},
        [{"case": "a", "questions": ["first", "follow-up"]}, {"case": "b", "question": "independent"}],
        tmp_path,
    )
    assert len(histories[0]) == 0 and len(histories[1]) == 2 and len(histories[2]) == 0
