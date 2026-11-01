"""Opt-in Azure Responses transport for controlled agent-model comparisons.

The normal demo's Chat Completions transport is unchanged. Both comparison arms
use this same mapping, explicit reasoning effort and completion limit. Provider
state is disabled. Encrypted reasoning is held only in memory and replayed only
while its exact originating message prefix and assistant reply remain present.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from typing import Any

from fli.http import parse_retry_after
from fli_demo.agent import Chat, Reply, ToolCall

log = logging.getLogger("fli_demo.responses")


class ResponsesProtocolError(RuntimeError):
    """A content-free failure retaining provider usage for private run receipts."""

    def __init__(self, message: str, usage: dict, timing: dict):
        super().__init__(message)
        self.usage, self.timing = usage, timing


def _plain(value: Any) -> dict:
    return value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else copy.deepcopy(value)


def _usage(value: dict | None) -> dict:
    """Map measured Responses counters to the existing telemetry contract."""
    value = value or {}
    names = {
        "input_tokens": "prompt_tokens",
        "output_tokens": "completion_tokens",
        "total_tokens": "total_tokens",
        "input_tokens_details": "prompt_tokens_details",
        "output_tokens_details": "completion_tokens_details",
    }
    return {target: value[source] for source, target in names.items() if source in value}


def _message(message: dict) -> list[dict]:
    role = message["role"]
    if role == "tool":
        return [{"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]}]
    if role not in {"system", "developer", "user", "assistant"}:
        raise ValueError("Unsupported Responses message role")
    result = []
    content = message.get("content")
    if isinstance(content, list):
        converted = []
        for part in content:
            if part["type"] == "text":
                converted.append({"type": "input_text", "text": part["text"]})
            elif part["type"] == "image_url":
                image = part["image_url"]
                converted.append(
                    {"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "auto")}
                )
            else:
                raise ValueError("Unsupported Responses content part")
        result.append({"role": role, "content": converted})
    elif content is not None:
        result.append({"role": role, "content": content})
    for call in message.get("tool_calls") or []:
        if role != "assistant" or call.get("type") != "function":
            raise ValueError("Unsupported Responses tool call")
        result.append({"type": "function_call", "call_id": call["id"], **call["function"]})
    return result


def _tools(tools: list[dict]) -> list[dict]:
    result = []
    for tool in tools:
        if tool.get("type") != "function":
            raise ValueError("Only existing function tools are supported")
        function = copy.deepcopy(tool["function"])
        # Responses otherwise defaults to strict schemas; the existing schemas
        # intentionally contain optional selectors and retain their old semantics.
        function.setdefault("strict", False)
        result.append({"type": "function", **function})
    return result


def azure_responses_chat(
    endpoint: str,
    deployment: str,
    *,
    reasoning_effort: str = "medium",
    max_output_tokens: int = 8_000,
    client: Any | None = None,
    max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> Chat:
    """Return the existing agent Chat interface, backed by stateless Responses.

    ``contextCarryoverTokensEstimate`` reserves newly emitted reasoning before the
    next measured prompt supersedes the estimate. It must be included by the agent
    budget guard; measured input/output counters are never inflated to reserve it.
    No encrypted reasoning, source text, signed image URL or response ID is logged.
    """
    from openai import OpenAI, RateLimitError

    if type(max_output_tokens) is not int or max_output_tokens < 1 or type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_output_tokens and max_attempts must be positive integers")
    if reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Choose an explicit supported reasoning effort")
    if client is None:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        client = OpenAI(
            base_url=endpoint.rstrip("/") + "/openai/v1/",
            api_key=get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"),
            max_retries=0,
        )

    # Entries bind raw output (including opaque reasoning and message phase) to
    # exactly the public assistant message the caller receives. They are never a
    # provider-side conversation and cannot resurrect a trimmed/changed prefix.
    cache: list[dict] = []

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        if cache:
            last = cache[-1]
            expected = [*last["prefix"], last["assistant"]]
            if messages[: len(expected)] != expected:
                cache.clear()
        by_position = {len(entry["prefix"]): entry for entry in cache}
        inputs = []
        for position, message in enumerate(messages):
            cached = by_position.get(position)
            inputs.extend(copy.deepcopy(cached["output"]) if cached else _message(message))
        request = {
            "model": deployment,
            "input": inputs,
            "tools": _tools(tools),
            "reasoning": {"effort": reasoning_effort},
            "max_output_tokens": max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "truncation": "disabled",
        }
        started = time.perf_counter()
        provider_ms = wait_ms = 0.0
        retries = 0
        for attempt in range(max_attempts):
            request_started = time.perf_counter()
            try:
                response = _plain(client.responses.create(**request))
                provider_ms += (time.perf_counter() - request_started) * 1000
                break
            except RateLimitError as exc:
                provider_ms += (time.perf_counter() - request_started) * 1000
                if attempt + 1 == max_attempts:
                    raise
                headers = exc.response.headers
                wait = parse_retry_after(headers.get("retry-after"))
                if wait is None:
                    milliseconds = headers.get("retry-after-ms") or headers.get("x-ms-retry-after-ms")
                    seconds = parse_retry_after(milliseconds)
                    wait = seconds / 1000 if seconds is not None else 60.0
                log.warning("responses_rate_limited", extra={"attempt": attempt + 1, "retrySeconds": wait})
                retries += 1
                sleep_started = time.perf_counter()
                while wait > 0:
                    pause = min(wait, 60.0)
                    sleep(pause)
                    wait -= pause
                wait_ms += (time.perf_counter() - sleep_started) * 1000
        usage = _usage(response.get("usage"))
        status = response.get("status")
        timing = {
            "api": "responses",
            # Azure can return its environment-specific deployment alias here.
            # The private management-plane receipt supplies canonical version.
            "responseModel": None if response.get("model") == deployment else response.get("model"),
            "responseStatus": status,
            "totalMs": round((time.perf_counter() - started) * 1000, 1),
            "providerMs": round(provider_ms, 1),
            "rateLimitWaitMs": round(wait_ms, 1),
            "rateLimitRetries": retries,
        }
        if status != "completed":
            timing["finishReason"] = (response.get("incomplete_details") or {}).get("reason") or status
            raise ResponsesProtocolError(
                "Responses did not complete; partial text/tools were not accepted", usage, timing
            )
        output = response.get("output") or []
        content, calls, reasoning = [], [], []
        refused = False
        for item in output:
            kind = item.get("type")
            if kind == "reasoning":
                reasoning.append(item)
            elif kind == "function_call":
                if item.get("status", "completed") != "completed":
                    raise ResponsesProtocolError("Responses returned an incomplete tool call", usage, timing)
                calls.append(ToolCall(item["call_id"], item["name"], item["arguments"]))
            elif kind == "message":
                for part in item.get("content") or []:
                    if part["type"] == "output_text":
                        content.append(part["text"])
                    elif part["type"] == "refusal":
                        refused = True
                        content.append(part["refusal"])
                    else:
                        raise ResponsesProtocolError("Responses returned an unsupported output part", usage, timing)
            else:
                raise ResponsesProtocolError("Responses returned an unsupported output item", usage, timing)
        reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        if reasoning and (
            type(reasoning_tokens) is not int
            or reasoning_tokens < 0
            or any(not item.get("encrypted_content") for item in reasoning)
        ):
            raise ResponsesProtocolError(
                "Responses reasoning continuity or token accounting is unavailable", usage, timing
            )
        if not content and not calls:
            raise ResponsesProtocolError("Responses returned no answer or tool call", usage, timing)
        message_items = [item for item in output if item.get("type") == "message"]
        if not calls and message_items and all(item.get("phase") == "commentary" for item in message_items):
            raise ResponsesProtocolError("Responses returned commentary without an answer or tool call", usage, timing)
        timing["contextCarryoverTokensEstimate"] = reasoning_tokens if reasoning else 0
        timing["finishReason"] = "refusal" if refused else "tool_calls" if calls else "stop"
        answer = "\n".join(content) if content else None
        assistant = {"role": "assistant", "content": answer}
        if calls:
            assistant["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in calls
            ]
        cache.append({"prefix": copy.deepcopy(messages), "assistant": assistant, "output": copy.deepcopy(output)})
        return Reply(answer, calls, usage, timing)

    return chat
