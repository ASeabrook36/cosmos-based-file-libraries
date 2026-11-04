"""Prospective enclosing-transcript accounting for the opt-in agent budget.

This prices the exact local guard representation, not a provider tokenizer. Actual
input meters supersede earlier estimates; opaque reasoning and new native images
remain explicit reservations. Nothing here changes measured usage counters.
"""

from __future__ import annotations

import json
from collections.abc import Callable

MODES = ("legacy-v1", "envelope-v2")
OUTPUT_RESERVE = 8_000
SYNTHESIS_PROMPT = {
    "role": "user",
    "content": "Tool-round limit reached. Answer from content actually received. "
    "Explicitly disclose any unfinished full-document reads or catalogue pages; "
    "never claim exhaustive coverage.",
}


def refusal_message(call_id: str) -> dict:
    """Bounded no-body reply, also reserved for every pending batched tool call."""
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(
            {
                "disposition": "budget_exceeded",
                "error": "remaining_context_budget_exceeded",
                "contextContentWithheld": True,
                "complete": False,
                "message": "No content delivered. Narrow the selection or disclose incomplete coverage.",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


class EnvelopeContext:
    """Use one estimator for pre-call guards and complete candidate admission.

    Anchor local growth at the last provider-reported input. Do not subtract
    locally removed bytes from that actual observation. If a later reply lacks
    input usage, preserve all not-yet-superseded reasoning reservations.
    """

    def __init__(self, tokenizer, tools: list[dict], allowance: int, image_tokens: int, project: Callable):
        if type(allowance) is not int or allowance < 12_000 or type(image_tokens) is not int or image_tokens < 1:
            raise ValueError("invalid context allowance or image reservation")
        self.tokenizer, self.tools = tokenizer, tools
        self.allowance, self.image_tokens, self.project = allowance, image_tokens, project
        self.accounted_local = self.accounted_prompt = None
        self.carryover = 0

    def local_size(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        images = sum(
            part.get("type") == "image_url"
            for message in messages
            if isinstance(message.get("content"), list)
            for part in message["content"]
        )
        wire = {"messages": self.project(messages), "tools": self.tools if tools is None else tools}
        return len(self.tokenizer.encode(json.dumps(wire))) + images * self.image_tokens

    def size(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        current = self.local_size(messages, tools)
        visible = (
            current if self.accounted_prompt is None else self.accounted_prompt + max(0, current - self.accounted_local)
        )
        return visible + self.carryover

    def fits(self, messages: list[dict], tools: list[dict] | None = None) -> bool:
        return self.size(messages, tools) + OUTPUT_RESERVE <= self.allowance

    def remaining(self, messages: list[dict]) -> int:
        return max(0, self.allowance - self.size(messages) - OUTPUT_RESERVE)

    def observe(self, sent_local: int, usage: dict, carryover: int) -> None:
        if type(sent_local) is not int or sent_local < 0 or type(carryover) is not int or carryover < 0:
            raise ValueError("invalid context accounting observation")
        prompt = usage.get("prompt_tokens")
        if prompt is not None:
            if type(prompt) is not int or prompt <= 0:
                raise ValueError("invalid provider input-token observation")
            self.accounted_local, self.accounted_prompt = sent_local, prompt
            self.carryover = carryover
        else:
            self.carryover += carryover
