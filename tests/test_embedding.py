"""Azure OpenAI embedding client against a fake transport (shapes per the Azure OpenAI
embeddings REST reference)."""

from __future__ import annotations

import httpx
import pytest

from fli.embedding import AzureOpenAIEmbedder, EmbeddingError


def make(handler, **kwargs):
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def token() -> str:
        return "aad-token"

    embedder = AzureOpenAIEmbedder(
        endpoint="https://aoai.example.invalid",
        deployment="emb",
        token_provider=token,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        dimensions=3,
        sleep=sleep,
        **kwargs,
    )
    return embedder, sleeps


def ok_response(request: httpx.Request) -> httpx.Response:
    import json

    inputs = json.loads(request.content)["input"]
    data = [{"index": i, "embedding": [float(i), 0.0, 1.0]} for i in range(len(inputs))][::-1]  # out of order
    return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 7}})


async def test_batches_and_orders_by_index():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer aad-token"
        assert request.url.params["api-version"]
        return ok_response(request)

    embedder, _ = make(handler, batch_size=2)
    vectors = await embedder.embed(["a", "b", "c"])
    assert len(calls) == 2 and vectors[1][0] == 1.0 and vectors[2][0] == 0.0
    assert embedder.usage.prompt_tokens == 14


async def test_honours_retry_after_then_succeeds():
    responses = [httpx.Response(429, headers={"Retry-After": "2"}), None]

    def handler(request):
        r = responses.pop(0)
        return r if r is not None else ok_response(request)

    embedder, sleeps = make(handler)
    assert len(await embedder.embed(["a"])) == 1
    assert sleeps == [2.0] and embedder.usage.throttled == 1


async def test_gives_up_after_bounded_attempts():
    embedder, sleeps = make(lambda request: httpx.Response(503), max_attempts=3)
    with pytest.raises(EmbeddingError):
        await embedder.embed(["a"])
    assert len(sleeps) == 2


async def test_client_errors_are_not_retried_and_dimension_mismatch_fails():
    embedder, sleeps = make(lambda request: httpx.Response(400))
    with pytest.raises(EmbeddingError):
        await embedder.embed(["a"])
    assert sleeps == []
    bad, _ = make(lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}], "usage": {}}))
    with pytest.raises(EmbeddingError, match="dimensions"):
        await bad.embed(["a"])
