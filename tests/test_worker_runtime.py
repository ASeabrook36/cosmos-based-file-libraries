"""Settling messages and running lanes (spec §7.1)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from fli.worker import MAX_DELIVERY_COUNT, attempt_number, run_lanes, settle
from fli.worker.handlers import BadMessage


class FakeWorker:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.failed = error, []

    async def handle(self, message):
        if self.error:
            raise self.error

    async def fail(self, message, reason):
        self.failed.append((message, reason))


MESSAGE = json.dumps({"type": "ingest", "libraryId": "L", "documentId": "D", "generation": 1}).encode()


async def test_success_completes():
    assert (await settle(FakeWorker(), MESSAGE, 0)).action == "complete"


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"\xff\xfe"])
async def test_unreadable_bodies_dead_letter_at_once(body):
    outcome = await settle(FakeWorker(), body, 0)
    assert outcome.action == "dead_letter" and outcome.reason.startswith("bad_message:")


async def test_bad_messages_dead_letter_at_once_without_marking_anything():
    worker = FakeWorker(BadMessage("unknown type"))
    outcome = await settle(worker, MESSAGE, 0)
    assert (outcome.action, outcome.reason) == ("dead_letter", "bad_message:BadMessage") and not worker.failed


async def test_errors_are_retried_until_the_final_delivery_then_marked_and_dead_lettered():
    worker = FakeWorker(RuntimeError("cosmos unavailable"))
    actions = [(await settle(worker, MESSAGE, count)).action for count in range(MAX_DELIVERY_COUNT)]
    assert actions == ["abandon"] * (MAX_DELIVERY_COUNT - 1) + ["dead_letter"]
    assert worker.failed == [(json.loads(MESSAGE), "processing_error:RuntimeError")]


async def test_a_failure_to_mark_still_dead_letters():
    worker = FakeWorker(RuntimeError("x"))

    async def broken(message, reason):
        raise RuntimeError("documents unavailable too")

    worker.fail = broken
    assert (await settle(worker, MESSAGE, MAX_DELIVERY_COUNT - 1)).action == "dead_letter"


def test_attempt_numbers_count_from_one():
    assert [attempt_number(c) for c in (None, 0, 4)] == [1, 1, 5]


def test_max_delivery_count_matches_the_queue_definition():
    bicep = (Path(__file__).resolve().parent.parent / "infra" / "main.bicep").read_text()
    assert int(re.search(r"maxDeliveryCount:\s*(\d+)", bicep).group(1)) == MAX_DELIVERY_COUNT


async def test_lanes_run_in_parallel_up_to_the_limit_and_stop():
    stopping = asyncio.Event()
    state = {"now": 0, "peak": 0, "sessions": 0}

    async def take_session():
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        state["sessions"] += 1
        if state["sessions"] >= 20:
            stopping.set()
        return True

    await asyncio.wait_for(run_lanes(3, take_session, stopping), timeout=5)
    assert state["peak"] == 3


async def test_a_lane_survives_an_error():
    stopping = asyncio.Event()
    calls = {"n": 0}

    async def take_session():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("session lock lost")
        stopping.set()
        return True

    await asyncio.wait_for(run_lanes(1, take_session, stopping), timeout=5)
    assert calls["n"] == 2
