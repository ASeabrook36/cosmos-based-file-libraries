"""Retry helpers shared by the Azure REST clients (Azure OpenAI, Document Intelligence)."""

from __future__ import annotations

import email.utils
import random
from datetime import UTC, datetime


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait, from a ``Retry-After`` header.

    Delta-seconds is the expected form; an HTTP-date is accepted but should not be relied on.
    Missing or malformed values return ``None`` so the caller falls back to exponential
    backoff with jitter rather than retrying immediately.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return max(0.0, (parsed - now).total_seconds())


def backoff_seconds(attempt: int, *, base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None) -> float:
    """Full-jitter exponential backoff for when no usable ``Retry-After`` exists."""
    rng = rng or random.Random()
    return rng.uniform(0, min(cap, base * (2 ** max(0, attempt))))
