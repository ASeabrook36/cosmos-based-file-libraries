"""Content-free demo telemetry. The platform owns model-usage accounting in production."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fli_demo.agent import Turn


def metrics(turn: Turn) -> dict[str, Any]:
    calls = []
    for call in turn.model_calls:
        usage, timing = call.get("usage") or {}, call.get("timing") or {}
        calls.append(
            {
                "purpose": call.get("purpose") if call.get("purpose") in {"answer", "tool_request"} else None,
                "promptTokens": usage.get("prompt_tokens"),
                "completionTokens": usage.get("completion_tokens"),
                "totalTokens": usage.get("total_tokens"),
                "cachedPromptTokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                "reasoningTokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "totalMs": timing.get("totalMs"),
                "providerMs": timing.get("providerMs"),
                "rateLimitWaitMs": timing.get("rateLimitWaitMs"),
                "rateLimitRetries": timing.get("rateLimitRetries"),
                "finishReason": timing.get("finishReason")
                if timing.get("finishReason") in {"stop", "length", "tool_calls", "content_filter"}
                else None,
            }
        )

    def total(key):
        values = [c[key] for c in calls]
        return sum(values) if values and all(v is not None for v in values) else None

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "totalMs": turn.total_ms,
        "modelCalls": calls,
        "promptTokens": total("promptTokens"),
        "completionTokens": total("completionTokens"),
        "totalTokens": total("totalTokens"),
        "tools": [
            {
                "operation": t.operation,
                "latencyMs": t.latency_ms,
                "requestCharge": t.request_charge,
                "resultCount": t.result_count,
                "estimatedTokens": t.estimated_tokens,
                "complete": t.complete,
                "error": t.error is not None,
            }
            for t in turn.searches
        ],
        "retrievalRequestCharge": sum(t.request_charge for t in turn.searches if t.request_charge is not None),
        "retrievalMs": sum(t.latency_ms for t in turn.searches),
        "providerMs": total("providerMs"),
        "rateLimitWaitMs": total("rateLimitWaitMs"),
        "rateLimitRetries": total("rateLimitRetries"),
        "retrievalChargeComplete": all(t.request_charge is not None for t in turn.searches),
    }


def save_metrics(data: dict[str, Any]) -> None:
    """Optional local JSONL sink; refuse a path inside the reference repository."""
    target = os.environ.get("DEMO_METRICS_FILE")
    if not target:
        return
    path = Path(target).expanduser().resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("DEMO_METRICS_FILE must be outside the reference repository")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(data, ensure_ascii=False) + "\n")
