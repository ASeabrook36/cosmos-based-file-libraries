"""Per-workload throttle governor.

One ``Governor`` per workload (for example: embeddings, image descriptions, Cosmos writes)
bounds concurrency and, optionally, a per-minute budget in the workload's own cost units.
``429``/``503`` answers are recorded with ``record_throttle``: the workload pauses for at
least ``Retry-After`` (or a jittered backoff), and after ``cooldown_after_throttles``
consecutive throttles it enters cooldown, a pause of at least ``cooldown_seconds`` *and* a
halved concurrency limit. A further halving needs another full run of consecutive throttles,
so the reduction tracks sustained pressure rather than the retries of one unlucky request.
The limit climbs back by one after every ``recover_after_successes`` consecutive successes,
never above ``max_concurrency``.

Every throttle, cooldown and recovery is reported through ``on_event`` (if given), so it is
possible to tell which workload ingestion is bottlenecked on.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

from .http import backoff_seconds

T = TypeVar("T")


@dataclass
class Governor:
    name: str
    max_concurrency: int
    budget_per_minute: int | None = None
    cooldown_after_throttles: int = 3
    cooldown_seconds: float = 60.0
    recover_after_successes: int = 50
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    throttled: int = 0
    requests: int = 0
    spent: int = 0
    cooldowns: int = 0
    concurrency_limit: int = field(init=False)
    _in_flight: int = field(default=0, init=False)
    _condition: asyncio.Condition = field(init=False)
    _window: list[tuple[float, int]] = field(default_factory=list, init=False)
    _consecutive: int = field(default=0, init=False)
    _successes: int = field(default=0, init=False)
    _paused_until: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.concurrency_limit = self.max_concurrency
        self._condition = asyncio.Condition()

    @property
    def in_cooldown(self) -> bool:
        return self.clock() < self._paused_until

    async def _wait_budget(self, cost: int) -> None:
        while True:
            now = self.clock()
            if now < self._paused_until:
                await self.sleep(self._paused_until - now)
                continue
            if self.budget_per_minute is None:
                return
            self._window = [(t, c) for t, c in self._window if now - t < 60.0]
            if sum(c for _, c in self._window) + cost <= self.budget_per_minute:
                self._window.append((now, cost))
                return
            await self.sleep(60.0 - (now - self._window[0][0]))

    async def run(self, cost: int, call: Callable[[], Awaitable[T]]) -> T:
        async with self._condition:
            await self._condition.wait_for(lambda: self._in_flight < self.concurrency_limit)
            self._in_flight += 1
        try:
            await self._wait_budget(cost)
            self.requests += 1
            self.spent += cost
            return await call()
        finally:
            async with self._condition:
                self._in_flight -= 1
                self._condition.notify_all()

    def record_throttle(self, retry_after: float | None, **context: Any) -> float:
        self.throttled += 1
        self._consecutive += 1
        self._successes = 0
        wait = retry_after if retry_after is not None else backoff_seconds(self._consecutive)
        consecutive = self._consecutive
        entered_cooldown = consecutive >= self.cooldown_after_throttles
        if entered_cooldown:
            self.cooldowns += 1
            wait = max(wait, self.cooldown_seconds)
            self.concurrency_limit = max(1, self.concurrency_limit // 2)
            self._consecutive = 0
        self._paused_until = max(self._paused_until, self.clock() + wait)
        self._emit("governor.throttle", waitSeconds=round(wait, 3), consecutive=consecutive, cooldown=entered_cooldown,
                   concurrencyLimit=self.concurrency_limit, **context)
        return wait

    def record_success(self) -> None:
        self._consecutive = 0
        if self.concurrency_limit >= self.max_concurrency:
            return
        self._successes += 1
        if self._successes >= self.recover_after_successes:
            self._successes = 0
            self.concurrency_limit += 1
            self._emit("governor.recovered", concurrencyLimit=self.concurrency_limit)

    def snapshot(self) -> dict[str, Any]:
        return {"workload": self.name, "requests": self.requests, "throttled": self.throttled, "cooldowns": self.cooldowns,
                "concurrencyLimit": self.concurrency_limit, "maxConcurrency": self.max_concurrency, "spent": self.spent,
                "inCooldown": self.in_cooldown}

    def _emit(self, name: str, **fields: Any) -> None:
        if self.on_event is not None:
            self.on_event(name, {"workload": self.name, **fields})
