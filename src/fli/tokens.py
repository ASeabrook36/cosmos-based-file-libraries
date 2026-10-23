"""Cached Entra tokens for the REST clients that take a token provider (embeddings, OCR).

The Azure SDK clients cache their own tokens; these calls do not. Locally,
``DefaultAzureCredential`` falls through to the Azure CLI, which starts an ``az`` process for
every token and does not cache: one process per embedding batch. A token is reused until five
minutes before it expires.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

REFRESH_MARGIN_SECONDS = 300


class CachedToken:
    def __init__(self, credential: Any, scope: str, clock: Callable[[], float] = time.time) -> None:
        self._credential = credential
        self._scope = scope
        self._clock = clock
        self._token: str | None = None
        self._expires_on = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        if self._token is None or self._clock() >= self._expires_on - REFRESH_MARGIN_SECONDS:
            async with self._lock:  # one refresh, however many callers are waiting
                if self._token is None or self._clock() >= self._expires_on - REFRESH_MARGIN_SECONDS:
                    access = await self._credential.get_token(self._scope)
                    self._token, self._expires_on = access.token, float(access.expires_on)
        return self._token


TokenProvider = Callable[[], Awaitable[str]]
