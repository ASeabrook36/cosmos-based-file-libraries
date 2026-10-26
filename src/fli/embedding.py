"""Azure OpenAI embeddings, used by ingestion for chunks and by search for the query vector.

Requests are batched; ``429`` and ``5xx`` honour ``Retry-After`` and otherwise back off with
jitter, up to a bounded number of attempts, then fail. Authentication is an Entra bearer
token from a caller-supplied async token provider (managed identity in deployed
environments); no key is used. The returned dimension is checked against the configured one,
because the Cosmos vector policy's dimension is fixed at container creation (spec §4).

Search embeds the query with this client and then converts it with
``fli.query.to_float16_query`` before querying the float16 index (spec §8 step 3).

Verification boundary: tested against a fake transport whose response shape follows the
Azure OpenAI embeddings REST reference (``data[].embedding``, ``data[].index``,
``usage.prompt_tokens``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from .governor import Governor
from .http import backoff_seconds, parse_retry_after

TokenProvider = Callable[[], Awaitable[str]]


class EmbeddingError(RuntimeError):
    def __init__(self, message: str, *, throttled: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.throttled = throttled
        self.retry_after = retry_after


class Embedder(Protocol):
    model: str
    dimensions: int
    version: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class EmbeddingUsage:
    requests: int = 0
    prompt_tokens: int = 0
    throttled: int = 0
    retries: int = 0
    tokens_reported: bool = False


_ACTIVE_USAGE: ContextVar[EmbeddingUsage | None] = ContextVar("embedding_usage", default=None)


@contextmanager
def measure_embeddings():
    usage = EmbeddingUsage()
    token = _ACTIVE_USAGE.set(usage)
    try:
        yield usage
    finally:
        _ACTIVE_USAGE.reset(token)


@dataclass
class AzureOpenAIEmbedder:
    endpoint: str
    deployment: str
    token_provider: TokenProvider
    http: httpx.AsyncClient
    model: str = "text-embedding-3-large"
    dimensions: int = 3072
    version: str = "text-embedding-3-large"
    api_version: str = "2024-10-21"
    batch_size: int = 16
    max_attempts: int = 5
    max_wait_seconds: float = 60.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    usage: EmbeddingUsage = field(default_factory=EmbeddingUsage)
    governor: Governor | None = None  # ingestion paces embeddings as a workload; search passes none

    def _count(self, field: str, value: int) -> None:
        setattr(self.usage, field, getattr(self.usage, field) + value)
        if (active := _ACTIVE_USAGE.get()) is not None:
            setattr(active, field, getattr(active, field) + value)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(await self._embed_batch(texts[start : start + self.batch_size]))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        url = f"{self.endpoint.rstrip('/')}/openai/deployments/{self.deployment}/embeddings"
        for attempt in range(self.max_attempts):
            token = await self.token_provider()
            self._count("requests", 1)

            async def post(token: str = token) -> httpx.Response:  # bound now, not at call time
                return await self.http.post(
                    url,
                    params={"api-version": self.api_version},
                    headers={"Authorization": f"Bearer {token}"},
                    json={"input": batch, "dimensions": self.dimensions},
                    timeout=30.0,
                )

            try:
                response = await (self.governor.run(len(batch), post) if self.governor else post())
            except httpx.TransportError as exc:
                if attempt + 1 == self.max_attempts:
                    raise EmbeddingError(f"embedding transport failure: {type(exc).__name__}") from exc
                self._count("retries", 1)
                await self.sleep(backoff_seconds(attempt))
                continue
            if response.status_code == 200:
                if self.governor:
                    self.governor.record_success()
                body = response.json()
                self._count("prompt_tokens", int(body.get("usage", {}).get("prompt_tokens", 0)))
                if "prompt_tokens" in body.get("usage", {}):
                    self.usage.tokens_reported = True
                    if (active := _ACTIVE_USAGE.get()) is not None:
                        active.tokens_reported = True
                data = sorted(body["data"], key=lambda d: d["index"])
                if len(data) != len(batch):
                    raise EmbeddingError("embedding response cardinality mismatch")
                vectors = [list(map(float, d["embedding"])) for d in data]
                if any(len(v) != self.dimensions for v in vectors):
                    raise EmbeddingError("embedding dimensions differ from the configured dimensions")
                return vectors
            if response.status_code == 429 or response.status_code >= 500:
                if response.status_code == 429:
                    self._count("throttled", 1)
                wait = parse_retry_after(response.headers.get("Retry-After"))
                if attempt + 1 == self.max_attempts:
                    raise EmbeddingError(
                        f"embedding failed after {self.max_attempts} attempts",
                        throttled=response.status_code == 429,
                        retry_after=wait,
                    )
                self._count("retries", 1)
                if self.governor:  # the governor's pause is enforced on the next run(); do not wait twice
                    self.governor.record_throttle(
                        wait, status=response.status_code, resourceType="embeddings", requestClass="POST"
                    )
                    continue
                await self.sleep(min(self.max_wait_seconds, wait if wait is not None else backoff_seconds(attempt)))
                continue
            raise EmbeddingError(f"embedding request rejected with HTTP {response.status_code}")
        raise EmbeddingError("embedding attempts exhausted")
