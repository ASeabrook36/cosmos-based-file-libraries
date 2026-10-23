from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fli.tokens import CachedToken


class Credential:
    def __init__(self) -> None:
        self.calls = 0

    async def get_token(self, scope):
        self.calls += 1
        await asyncio.sleep(0)
        return SimpleNamespace(token=f"t{self.calls}", expires_on=3600)


async def test_tokens_are_reused_until_near_expiry_and_refreshed_once():
    clock = {"t": 0.0}
    credential = Credential()
    token = CachedToken(credential, "scope", clock=lambda: clock["t"])
    assert await asyncio.gather(*(token() for _ in range(10))) == ["t1"] * 10
    assert credential.calls == 1
    clock["t"] = 3299.0
    assert await token() == "t1"
    clock["t"] = 3300.0  # five minutes before expiry
    assert await token() == "t2" and credential.calls == 2
