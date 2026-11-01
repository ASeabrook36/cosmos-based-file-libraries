"""Responses transport preserves the agent contract without hidden conversation state."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError

from fli_demo.agent import Reply, ToolCall, run_turn
from fli_demo.responses import ResponsesProtocolError, azure_responses_chat
from tests.test_demo import AGENT, FakeIndex, scripted, tool


def message(text="Answer", **extra):
    return {
        "type": "message",
        "id": "msg",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
        **extra,
    }


def call(identifier="c1"):
    return {
        "type": "function_call",
        "id": "fc_" + identifier,
        "call_id": identifier,
        "name": "search_library_finance",
        "arguments": '{"query":"policy"}',
        "status": "completed",
    }


def reasoning(identifier="rs1"):
    return {"type": "reasoning", "id": identifier, "summary": [], "encrypted_content": "opaque-" + identifier}


def response(*output, **extra):
    return {
        "status": "completed",
        "model": "pinned-model-version",
        "output": list(output),
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 60},
            "output_tokens_details": {"reasoning_tokens": 30},
        },
        **extra,
    }


def client_for(*responses):
    iterator, requests = iter(responses), []

    def create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        result = next(iterator)
        if isinstance(result, Exception):
            raise result
        return result

    return SimpleNamespace(responses=SimpleNamespace(create=create)), requests


def assistant(reply):
    result = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        result["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in reply.tool_calls
        ]
    return result


def test_mapping_preserves_optional_tools_multimodal_inputs_and_measured_usage():
    client, requests = client_for(response(message()))
    chat = azure_responses_chat("unused", "chosen", client=client)
    definitions = [tool("lookup")]
    original = copy.deepcopy(definitions)
    reply = chat(
        [
            {"role": "system", "content": "instructions"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read this source"},
                    {"type": "image_url", "image_url": {"url": "https://example.org/source.png", "detail": "high"}},
                ],
            },
        ],
        definitions,
    )
    request = requests[0]
    assert definitions == original
    assert request["tools"] == [{"type": "function", **original[0]["function"], "strict": False}]
    assert request["input"][1]["content"] == [
        {"type": "input_text", "text": "Read this source"},
        {"type": "input_image", "image_url": "https://example.org/source.png", "detail": "high"},
    ]
    assert request["reasoning"] == {"effort": "medium"}
    assert request["store"] is False and request["truncation"] == "disabled"
    assert request["max_output_tokens"] == 8000 and "previous_response_id" not in request
    assert reply.content == "Answer"
    assert reply.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "prompt_tokens_details": {"cached_tokens": 60},
        "completion_tokens_details": {"reasoning_tokens": 30},
    }
    assert reply.timing["responseModel"] == "pinned-model-version"
    assert reply.timing["contextCarryoverTokensEstimate"] == 0


def test_all_reasoning_and_function_items_roundtrip_with_phase_and_no_provider_state():
    first_output = [reasoning(), message("Looking up evidence", phase="commentary"), call()]
    second_output = [reasoning("rs2"), call("c2")]
    client, requests = client_for(response(*first_output), response(*second_output), response(message()))
    chat = azure_responses_chat("unused", "chosen", client=client)
    messages = [{"role": "user", "content": "Question"}]
    first = chat(messages, [tool("search_library_finance")])
    assert first.timing["contextCarryoverTokensEstimate"] == 30
    assert "opaque" not in json.dumps(first.usage) + json.dumps(first.timing)
    messages.extend([assistant(first), {"role": "tool", "tool_call_id": "c1", "content": "source text"}])
    second = chat(messages, [tool("search_library_finance")])
    assert requests[1]["input"] == [
        messages[0],
        *first_output,
        {"type": "function_call_output", "call_id": "c1", "output": "source text"},
    ]
    messages.extend([assistant(second), {"role": "tool", "tool_call_id": "c2", "content": "more source"}])
    chat(messages, [])
    assert [i["id"] for i in requests[2]["input"] if i.get("type") == "reasoning"] == ["rs1", "rs2"]
    assert requests[2]["tools"] == []
    assert all("previous_response_id" not in request for request in requests)


@pytest.mark.parametrize("change", ["new_case", "trim", "replace_source", "alter_answer"])
def test_changed_or_trimmed_history_never_restores_opaque_context(change):
    client, requests = client_for(
        response(reasoning(), call()), response(reasoning("rs2"), call("c2")), response(message())
    )
    chat = azure_responses_chat("unused", "chosen", client=client)
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}]
    first = chat(messages, [])
    messages.extend([assistant(first), {"role": "tool", "tool_call_id": "c1", "content": "old source"}])
    second = chat(messages, [])
    messages.extend([assistant(second), {"role": "tool", "tool_call_id": "c2", "content": "new source"}])
    if change == "new_case":
        messages = messages[:2]
    elif change == "trim":
        messages = messages[1:]
    elif change == "replace_source":
        messages[3]["content"] = "revised source"
    else:
        messages[2]["content"] = "revised assistant text"
    chat(messages, [])
    assert not any(i.get("type") == "reasoning" for i in requests[-1]["input"])
    assert "old source" not in json.dumps(requests[-1]) if change == "replace_source" else True


def test_preserves_refusal_without_turning_it_into_a_tool_or_empty_success():
    refusal = message()
    refusal["content"] = [{"type": "refusal", "refusal": "Cannot provide that answer."}]
    client, _ = client_for(response(refusal))
    reply = azure_responses_chat("unused", "chosen", client=client)([], [])
    assert reply.content == "Cannot provide that answer." and reply.tool_calls == []
    assert reply.timing["finishReason"] == "refusal"


def test_commentary_only_response_is_not_scored_as_a_final_answer():
    client, _ = client_for(response(message("I will look this up.", phase="commentary")))
    with pytest.raises(ResponsesProtocolError, match="commentary without an answer") as caught:
        azure_responses_chat("unused", "chosen", client=client)([], [])
    assert caught.value.usage["prompt_tokens"] == 100


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_partial_outputs_are_not_accepted_and_failure_retains_usage(status):
    client, _ = client_for(
        response(message("partial"), call(), status=status, incomplete_details={"reason": "max_output_tokens"})
    )
    with pytest.raises(ResponsesProtocolError) as caught:
        azure_responses_chat("unused", "chosen", client=client)([], [])
    assert caught.value.usage["prompt_tokens"] == 100
    assert caught.value.timing["responseStatus"] == status
    assert "partial" not in str(caught.value).lower().replace("partial text/tools", "")


@pytest.mark.parametrize("missing", ["encrypted_content", "reasoning_tokens"])
def test_missing_reasoning_continuity_or_budget_accounting_fails_explicitly(missing):
    item = response(reasoning(), call())
    if missing == "encrypted_content":
        del item["output"][0]["encrypted_content"]
    else:
        del item["usage"]["output_tokens_details"]["reasoning_tokens"]
    client, _ = client_for(item)
    with pytest.raises(ResponsesProtocolError, match="reasoning continuity or token accounting"):
        azure_responses_chat("unused", "chosen", client=client)([], [])


def test_unavailable_usage_is_not_reported_as_zero():
    client, _ = client_for(response(message(), usage=None))
    assert azure_responses_chat("unused", "chosen", client=client)([], []).usage == {}


@pytest.mark.parametrize(
    "headers,expected", [({"retry-after": "65"}, [60, 5]), ({"retry-after-ms": "1250"}, [1.25]), ({}, [60])]
)
def test_rate_limit_retry_preserves_identical_request_and_bounds_waits(headers, expected):
    error = RateLimitError(
        "limited",
        response=httpx.Response(429, headers=headers, request=httpx.Request("POST", "https://example.org")),
        body=None,
    )
    client, requests = client_for(error, response(message()))
    slept = []
    reply = azure_responses_chat("unused", "chosen", client=client, sleep=slept.append)([], [])
    assert slept == expected and requests[0] == requests[1]
    assert reply.timing["rateLimitRetries"] == 1


def test_retry_exhaustion_stops_after_three_attempts():
    error = RateLimitError(
        "limited",
        response=httpx.Response(
            429, headers={"retry-after": "1"}, request=httpx.Request("POST", "https://example.org")
        ),
        body=None,
    )
    client, requests = client_for(error, error, error)
    slept = []
    with pytest.raises(RateLimitError):
        azure_responses_chat("unused", "chosen", client=client, sleep=slept.append)([], [])
    assert len(requests) == 3 and slept == [1, 1]


def test_pending_reasoning_consumes_agent_budget_without_inflating_measured_usage():
    chat = scripted(
        Reply(
            None,
            [ToolCall("c1", "search_library_finance", '{"query":"policy"}')],
            {"prompt_tokens": 1000, "completion_tokens": 6000},
            {"contextCarryoverTokensEstimate": 5000},
        )
    )
    index = FakeIndex()
    turn = run_turn(chat, index, AGENT, [], "What does the policy say?", context_token_budget=12000)
    assert len(chat.seen) == 1
    assert turn.model_calls[0]["usage"]["prompt_tokens"] == 1000
    assert "context budget" in turn.answer


def test_next_measured_prompt_replaces_prior_reasoning_reserve():
    # If old carryover is added again after the next measured input, the 12k
    # guard prevents the third call. The actual second carryover is zero.
    chat = scripted(
        Reply(
            None,
            [ToolCall("c1", "search_library_finance", '{"query":"policy"}')],
            {"prompt_tokens": 500},
            {"contextCarryoverTokensEstimate": 2500},
        ),
        Reply(
            None,
            [ToolCall("c2", "search_library_finance", '{"query":"specific policy"}')],
            {"prompt_tokens": 2000},
            {"contextCarryoverTokensEstimate": 0},
        ),
        Reply("The supplied source says margin fell."),
    )
    turn = run_turn(chat, FakeIndex(), AGENT, [], "What does the policy say?", context_token_budget=12000)
    assert len(chat.seen) == 3 and turn.answer == "The supplied source says margin fell."
