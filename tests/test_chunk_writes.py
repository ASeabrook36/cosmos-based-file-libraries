"""The worker-wide write cap and 429 handling (spec §7.2 step 8, evidence E10)."""

from __future__ import annotations

import asyncio

import pytest

from fli.chunks import capped, retry_after_seconds
from fli.governor import Governor


class Throttled(Exception):
    status_code = 429

    def __init__(self, ms: str | None = "250") -> None:
        super().__init__("429")
        self.headers = {"x-ms-retry-after-ms": ms} if ms is not None else {}


class Fatal(Exception):
    status_code = 400


def governor(limit: int = 2) -> tuple[Governor, list[float]]:
    slept: list[float] = []
    clock = {"t": 0.0}

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds
        await asyncio.sleep(0)

    return Governor("cosmos_writes", max_concurrency=limit, clock=lambda: clock["t"], sleep=sleep), slept


async def test_concurrency_is_capped_across_all_callers():
    gov, _ = governor(2)
    state = {"now": 0, "peak": 0}

    async def write():
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.001)
        state["now"] -= 1

    # many "documents" writing at once still share two slots
    await asyncio.gather(*(capped(gov, write) for _ in range(20)))
    assert state["peak"] == 2 and gov.requests == 20


async def test_a_429_after_the_sdk_waits_the_requested_time_and_retries():
    gov, slept = governor()
    attempts = {"n": 0}

    async def write():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Throttled("1500")
        return "ok"

    assert await capped(gov, write) == "ok"
    assert attempts["n"] == 3 and gov.throttled == 2 and slept[:2] == [1.5, 1.5]


async def test_repeated_throttling_halves_the_cap():
    gov, _ = governor(2)
    attempts = {"n": 0}

    async def write():
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise Throttled()

    await capped(gov, write)
    assert gov.cooldowns == 1 and gov.concurrency_limit == 1


async def test_throttling_beyond_the_attempt_limit_raises_for_the_queue_to_retry():
    gov, _ = governor()

    async def write():
        raise Throttled()

    with pytest.raises(Throttled):
        await capped(gov, write, max_attempts=4)
    assert gov.requests == 4


async def test_other_errors_are_not_retried():
    gov, _ = governor()
    calls = {"n": 0}

    async def write():
        calls["n"] += 1
        raise Fatal()

    with pytest.raises(Fatal):
        await capped(gov, write)
    assert calls["n"] == 1


@pytest.mark.parametrize("ms,expected", [("250", 0.25), (None, None), ("junk", None)])
def test_retry_after_header(ms, expected):
    assert retry_after_seconds(Throttled(ms)) == expected
