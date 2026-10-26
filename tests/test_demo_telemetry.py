import json
from types import SimpleNamespace

import pytest

from fli_demo.agent import Citation, SearchTrace, Turn, azure_chat, run_turn
from fli_demo.telemetry import metrics, save_metrics
from tests.test_demo import AGENT, FakeIndex, scripted


def test_turn_collects_actual_usage_and_provider_timings():
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="Answer", tool_calls=[]))],
                    usage=SimpleNamespace(
                        model_dump=lambda: {
                            "prompt_tokens": 30,
                            "completion_tokens": 12,
                            "total_tokens": 42,
                            "prompt_tokens_details": {"cached_tokens": 20},
                            "completion_tokens_details": {"reasoning_tokens": 7},
                        }
                    ),
                )
            )
        )
    )
    reply = azure_chat("endpoint", "deployment", client=client)([], [])
    assert reply.usage["total_tokens"] == 42 and reply.timing["providerMs"] >= 0
    turn = run_turn(scripted(reply), FakeIndex(), AGENT, [], "question")
    data = metrics(turn)
    assert data["promptTokens"] == 30 and data["completionTokens"] == 12 and data["totalTokens"] == 42
    assert data["modelCalls"][0]["cachedPromptTokens"] == 20
    assert data["modelCalls"][0]["reasoningTokens"] == 7
    assert data["totalMs"] >= 0


def test_usage_is_cumulative_and_unknown_counts_are_not_reported_as_zero():
    turn = Turn(
        "answer",
        [],
        [],
        model_calls=[
            {"usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}},
            {"usage": {"prompt_tokens": 150, "completion_tokens": 20, "total_tokens": 170}},
        ],
    )
    assert metrics(turn)["totalTokens"] == 280
    turn.model_calls.append({"usage": {}})
    assert metrics(turn)["totalTokens"] is None


def test_metrics_exclude_source_content_and_environment_identifiers():
    trace = SearchTrace(
        "private-tool",
        "private-library",
        "private-library-name",
        "private-question",
        25,
        3.5,
        2,
        "results",
        [Citation("private-file", {"page": 1})],
        error="private-endpoint",
        filters={"author": "private-author"},
    )
    turn = Turn("private-answer", [trace], [{"content": "private-source-text"}], total_ms=100)
    data = metrics(turn)
    assert "private-" not in json.dumps(data)
    assert data["retrievalRequestCharge"] == 3.5 and data["retrievalMs"] == 25
    assert data["tools"][0]["error"] is True


def test_optional_metrics_sink_writes_content_free_json_outside_repository(tmp_path, monkeypatch):
    target = tmp_path / "metrics.jsonl"
    monkeypatch.setenv("DEMO_METRICS_FILE", str(target))
    data = metrics(Turn("answer", [], []))
    save_metrics(data)
    assert json.loads(target.read_text())["tools"] == []
    monkeypatch.setenv("DEMO_METRICS_FILE", "records/local-metrics.jsonl")
    with pytest.raises(ValueError, match="outside"):
        save_metrics(data)
