"""Tamper resistance and counterfactual limits of the offline admission receipt."""

import json

import pytest

from fli.chunking import default_tokenizer
from scripts.qualify_context_envelope import bound, continuation, sha


def captured(tmp_path, *, prompt=55000):
    run = "r1-invented-sol-vector"
    wire = {"messages": [{"role": "system", "content": "Use sources."}, {"role": "user", "content": "q"}], "tools": []}
    function = {"type": "function_call", "call_id": "call", "name": "read_documents_library_bound", "arguments": "{}"}
    payload = {"mode": "selected", "complete": True, "documents": [{"sections": [{"text": "条件\n" * 1500}]}]}
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call", "type": "function", "function": {"name": function["name"], "arguments": "{}"}}],
    }
    tool = {
        "role": "tool",
        "tool_call_id": "call",
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }
    tokenizer = default_tokenizer()

    def size(rows):
        return len(tokenizer.encode(json.dumps({"messages": rows, "tools": []})))

    guard = prompt + size(wire["messages"] + [assistant, tool]) - size(wire["messages"]) + 8000
    values = {
        f"{run}/results.json": [{"metrics": {"modelCalls": [{}]}, "toolReplies": [payload]}],
        f"wire-{run}/wire-001.json": wire,
        "provider/response-00001.json": {"output": [function], "usage": {"input_tokens": prompt}},
        "provider/request-00001.json": {"input": [{"role": "system", "content": "Use sources."}]},
    }
    for name, value in values.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    diagnostic = {
        "run": run,
        "case": "invented",
        "cell": "sol-vector",
        "lastActualResponseAttempt": 1,
        "reconstructedGuardTotal": guard,
        "inputsSha256": {name: sha(tmp_path / name) for name in values},
    }
    return diagnostic


def test_offline_receipt_rejects_whole_source_candidate_without_claiming_answer_recovery(tmp_path):
    result = continuation(tmp_path, captured(tmp_path))
    assert result["historicalGuard"] > 64000 and result["resultingLocalGuard"] <= 64000
    assert result["wouldPermitNextProviderCall"] and result["priorReceivedMessagesPreserved"]
    assert result["candidateDecisions"][0]["wholePayloadRefused"]
    assert not result["newAnswerQualityMeasured"]


def test_insufficient_minimum_reply_capacity_remains_a_stop(tmp_path):
    result = continuation(tmp_path, captured(tmp_path, prompt=55990))
    assert not result["minimumReplyPreflightPassed"] and not result["wouldFetchAnyPendingSource"]
    assert not result["wouldPermitNextProviderCall"] and result["candidateDecisions"] == []


@pytest.mark.parametrize("name", ["provider/request-00001.json", "provider/response-00001.json"])
def test_provider_artifacts_cannot_be_omitted_from_hash_binding(tmp_path, name):
    diagnostic = captured(tmp_path)
    del diagnostic["inputsSha256"][name]
    with pytest.raises(ValueError, match="unbound wire/provider"):
        continuation(tmp_path, diagnostic)


def test_changed_source_reply_is_not_silently_replayed(tmp_path):
    diagnostic = captured(tmp_path)
    (tmp_path / diagnostic["run"] / "results.json").write_text("[]")
    with pytest.raises(ValueError, match="frozen evidence hash changed"):
        continuation(tmp_path, diagnostic)


def test_historical_guard_must_reproduce_exactly(tmp_path):
    diagnostic = captured(tmp_path)
    diagnostic["reconstructedGuardTotal"] += 1
    with pytest.raises(ValueError, match="historical guard reconstruction"):
        continuation(tmp_path, diagnostic)


def test_paths_cannot_escape_frozen_run_directory(tmp_path):
    with pytest.raises(ValueError, match="escaped run"):
        bound(tmp_path, "../foreign.json", "irrelevant")
