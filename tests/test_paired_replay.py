"""Matched replay stays in the existing harness and refuses invalid comparison inputs."""

import copy
import json
from types import SimpleNamespace

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


def test_controlled_replay_rejects_legacy_mislabeled_overlap(tmp_path):
    from fli.ingestion import profile

    index = Index()
    index.get_experiment = lambda _: {
        "comparison": "controlled-component",
        "members": {key: {"libraryId": key} for key in index.rows},
    }
    recipes = {
        key: profile(f"{key}-structured-v1" if key == "custom" else "microsoft-indexer-layout-v1", controlled=True)
        for key in index.rows
    }
    recipes["microsoft"]["chunking"].pop("overlapCharacters")
    index.get_library = lambda key: {"name": "Matched", "ingestionProfile": recipes[key]}
    with pytest.raises(ValueError, match="explicit zero overlap"):
        evaluation.replay_pair(index, object, {}, "exp", [], tmp_path, index_ready=True, run_controls=CONTROLS)


def test_overlap_labels_preserve_legacy_recipe_but_correct_its_units():
    from fli.ingestion import chunking_description, profile

    recipe = profile("microsoft-indexer-layout-v1")
    assert recipe["version"] == 2
    assert recipe["chunking"]["overlapTokens"] is None
    assert "200 characters" in chunking_description(recipe)
    recipe["chunking"].pop("overlapCharacters")
    recipe["chunking"]["overlapTokens"] = 200
    assert "200 characters" in chunking_description(recipe)
    assert "legacy" in chunking_description(recipe)


def test_functional_smoke_does_not_claim_index_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "replay", lambda *args: None)
    result = evaluation.replay_pair(
        Index(),
        object,
        {},
        "exp",
        [],
        tmp_path,
        repeats=1,
        run_controls=CONTROLS,
        measurement_mode="functional-smoke",
    )
    assert result["indexReadiness"] == "not-confirmed"
    assert result["measurementMode"] == "functional-smoke"


@pytest.mark.parametrize("paired", [False, True])
def test_replay_refuses_existing_output_even_if_previous_run_failed(tmp_path, paired):
    previous = tmp_path / "run-status.json"
    previous.write_text('{"status": "failed"}')
    with pytest.raises(ValueError, match="must be empty"):
        if paired:
            evaluation.replay_pair(Index(), object, {}, "exp", [], tmp_path, index_ready=True, run_controls=CONTROLS)
        else:
            evaluation.replay(Index(), object(), {"libraryIds": ["custom"]}, [], tmp_path)
    assert previous.read_text() == '{"status": "failed"}'


@pytest.mark.parametrize("change", ["metadata", "library-description", "tool-schema"])
def test_baseline_replay_detects_changed_metadata_and_model_context(tmp_path, monkeypatch, change):
    index = Index()
    context = {"description": "Reports", "tools": [{"name": "find", "description": "Find reports"}]}
    index.get_library = lambda _: {"name": "Matched reports", "description": context["description"]}
    index.tools = lambda *_: copy.deepcopy(context["tools"])

    def run(*args, **kwargs):
        if change == "metadata":
            index.rows["custom"][0]["facets"] = ["corpus=changed"]
        elif change == "library-description":
            context["description"] = "A materially different tool scope"
        else:
            context["tools"][0]["description"] = "A materially different instruction"
        return SimpleNamespace(answer="answer", searches=[], messages=[])

    monkeypatch.setattr(evaluation, "run_turn", run)
    monkeypatch.setattr(evaluation, "metrics", lambda _: {})
    with pytest.raises(RuntimeError, match="comparison is confounded"):
        evaluation.replay(
            index, object(), {"libraryIds": ["custom"]}, [{"case": "one", "question": "question"}], tmp_path
        )
    assert json.loads((tmp_path / "snapshot-check.json").read_text())["unchanged"] is False
    assert json.loads((tmp_path / "run-status.json").read_text())["status"] == "failed"


def test_replay_preserves_completed_turns_and_marks_failed_attempt_usage_unavailable(tmp_path, monkeypatch):
    def run(chat, index, agent, history, question, **kwargs):
        if question == "fails":
            raise RuntimeError("provider error that must not be copied to the status artifact")
        return SimpleNamespace(answer="answer", searches=[], messages=[])

    monkeypatch.setattr(evaluation, "run_turn", run)
    monkeypatch.setattr(evaluation, "metrics", lambda _: {"totalTokens": 12})
    with pytest.raises(RuntimeError):
        evaluation.replay(
            Index(), object(), {"libraryIds": ["custom"]}, [{"case": "one", "questions": ["works", "fails"]}], tmp_path
        )
    assert len(json.loads((tmp_path / "results.json").read_text())) == 1
    status = json.loads((tmp_path / "run-status.json").read_text())
    assert status["completedTurns"] == 1 and "unavailable" in status["usage"]
    assert "provider error" not in json.dumps(status)


def test_replay_freezes_cases_and_strips_attachment_credentials_from_tool_replies(tmp_path, monkeypatch):
    reply = {"mode": "evidence_search", "results": [{"image": {"url": "private-url", "expiresAt": "later"}}]}
    monkeypatch.setattr(
        evaluation,
        "run_turn",
        lambda *a, **kw: SimpleNamespace(
            answer="answer", searches=[], messages=[{"role": "tool", "content": json.dumps(reply)}]
        ),
    )
    monkeypatch.setattr(evaluation, "metrics", lambda _: {})
    cases = [{"case": "one", "question": "question"}]
    result = evaluation.replay(Index(), object(), {"libraryIds": ["custom"]}, cases, tmp_path)
    assert result[0]["toolReplies"][0]["results"][0]["image"] == {}
    assert json.loads((tmp_path / "cases.json").read_text()) == cases
    assert json.loads((tmp_path / "source-snapshot.json").read_text())["libraries"]["custom"]["documents"]
    assert json.loads((tmp_path / "run-status.json").read_text())["status"] == "complete"


def test_automatic_indicators_distinguish_word_matches_from_body_delivery_and_correctness():
    case = {"indicators": {"answerPatterns": ["later"], "requiredSourceFiles": ["one.docx", "two.docx"]}}
    replies = [
        {"mode": "catalogue", "documents": [{"fileName": "one.docx", "description": "later of dates"}]},
        {"mode": "outline", "documents": [{"fileName": "two.docx", "sections": [{"text": "later of dates"}]}]},
        {
            "mode": "evidence_search",
            "results": [
                {"fileName": "one.docx", "text": "later of dates", "textSource": "generated_visual_description"},
                {"fileName": "two.docx", "text": "source body", "textSource": "extracted_source_text"},
            ],
        },
    ]
    result = evaluation.indicators(case, 1, "The later date is not specified in one.docx.", replies)
    assert result["answerPatterns"][0]["present"] is True
    assert result["sourceFileNameMentioned"]["one.docx"] is True
    assert result["originalBodyFilesDelivered"] == ["two.docx"]
    assert result["requiredBodyDeliveryObserved"] is False
    assert result["citationCorrectness"] is None and result["taskSuccess"] is None


def test_indicators_allow_catalogue_only_cases_and_turn_specific_source_requirements():
    case = {"indicators": {"evidenceRequirement": "catalogue", "expectedFiles": ["one.docx"]}}
    reply = {"mode": "catalogue", "documents": [{"fileName": "one.docx", "readable": True}]}
    result = evaluation.indicators(case, 1, "one.docx is readable", [reply])
    assert result["expectedCatalogueFilesObserved"] is True and result["requiredBodyDeliveryObserved"] is None
    case = {"indicators": {"perTurn": [{"requiredSourceFiles": ["one.docx"]}, {"requiredSourceFiles": ["two.docx"]}]}}
    result = evaluation.indicators(case, 2, "two.docx", [reply])
    assert result["requiredSourceFiles"] == ["two.docx"] and result["requiredBodyDeliveryObserved"] is False


@pytest.mark.parametrize("mode", ["full", "outline"])
def test_recovered_passages_count_as_body_even_when_parent_read_was_refused(mode):
    case = {"indicators": {"requiredSourceFiles": ["one.docx", "two.docx"]}}
    reply = {
        "mode": mode,
        "disposition": "budget_exceeded",
        "automaticRecovery": "document_scoped_passages",
        "results": [{"fileName": "one.docx", "text": "Recovered original body"}],
        "documents": [{"fileName": "two.docx", "sections": [{"text": "Refused read"}]}],
    }
    result = evaluation.indicators(case, 1, "one.docx", [reply])
    assert result["originalBodyFilesDelivered"] == ["one.docx"]
    assert result["requiredBodyDeliveryObserved"] is False
