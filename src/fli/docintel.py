"""Azure AI Document Intelligence ``prebuilt-read``: the bounded OCR fallback for scanned
PDFs and images. The layout subclass uses ``prebuilt-layout`` with figure output.

    POST {endpoint}/documentintelligence/documentModels/prebuilt-read:analyze
         ?api-version=2024-11-30&pages=1-{maxPages}      {"base64Source": "..."}
      -> 202, Operation-Location: .../analyzeResults/{resultId}
    GET  Operation-Location                               poll until succeeded | failed
      -> analyzeResult.content (text), analyzeResult.pages (pages analysed)

Bounds. Documents larger than ``max_bytes`` are refused before any call; PDFs longer than
``max_pages`` are refused by the extractor before any call; ``pages=1-{max_pages}`` caps cost
for inputs whose page count is not known locally. Polling stops at ``timeout_seconds``.

Failure mapping (``OcrError.transient`` means "retry the queue message"):

    429, 408, 5xx, transport, poll timeout, failed-with-server-error   transient
    401, 403, 404 on the endpoint                                     transient: a deployment
                                                                      fault dead-letters after
                                                                      the retry limit rather
                                                                      than marking every
                                                                      document failed
    other 4xx, failed-with-content-error                              the document is recorded
                                                                      failed with the code

Authentication is an Entra token for ``https://cognitiveservices.azure.com/.default`` from a
managed identity holding Cognitive Services User on the resource; no key is used. The poll
URL is only followed when it is on the configured endpoint, so the bearer token is never
sent to a host named by a response header.

Request/response shapes follow the Document Intelligence v4.0 (2024-11-30) REST reference.
Layout figure/caption segmentation has also been probed on live raster and vector PDFs;
see docs/image-retrieval.md for the verification boundary.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .extraction import OcrError
from .governor import Governor
from .http import parse_retry_after

API_VERSION = "2024-11-30"
SCOPE = "https://cognitiveservices.azure.com/.default"
SERVER_ERROR_CODES = {"InternalServerError", "ServiceUnavailable", "Timeout", "InternalServiceError"}


class DocumentIntelligenceReader:
    def __init__(
        self,
        *,
        endpoint: str,
        token_provider: Callable[[], Awaitable[str]],
        http: httpx.AsyncClient,
        max_pages: int,
        max_bytes: int,
        timeout_seconds: float = 180.0,
        api_version: str = API_VERSION,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_pages < 1 or max_bytes < 1:
            raise ValueError("OCR bounds must be positive")
        self.endpoint = endpoint.rstrip("/")
        self._token = token_provider
        self._http = http
        self.max_pages = max_pages
        self.max_bytes = max_bytes
        self.timeout = timeout_seconds
        self.api_version = api_version
        self._sleep = sleep
        self._clock = clock

    async def read(self, data: bytes, content_type: str) -> tuple[str, int]:
        result, _ = await self.analyze_operation(data, "prebuilt-read", {"pages": f"1-{self.max_pages}"})
        return str(result.get("content") or ""), len(result.get("pages") or [])

    async def analyze_operation(self, data: bytes, model: str, params: dict) -> tuple[dict, str]:
        if len(data) > self.max_bytes:
            raise OcrError("ocr_input_too_large", transient=False)
        deadline = self._clock() + self.timeout
        url = f"{self.endpoint}/documentintelligence/documentModels/{model}:analyze"
        params = {"api-version": self.api_version, **params}
        response = await self._send(
            "POST", url, params=params, json={"base64Source": base64.b64encode(data).decode("ascii")}
        )
        if response.status_code != 202:
            raise _http_error(response, "analyze")
        location = response.headers.get("Operation-Location")
        if not location or not location.startswith(self.endpoint + "/"):
            raise OcrError("ocr_bad_operation_location", transient=True)  # never follow a poll URL off our own endpoint
        while True:
            wait = parse_retry_after(response.headers.get("Retry-After"))
            await self._sleep(min(max(wait if wait is not None else 1.0, 0.5), 10.0))
            if self._clock() > deadline:
                raise OcrError("ocr_timeout", transient=True)
            response = await self._send("GET", location)
            if response.status_code != 200:
                raise _http_error(response, "poll")
            body = _json(response)
            status = body.get("status")
            if status == "succeeded":
                result = body.get("analyzeResult") or {}
                return result, location
            if status == "failed":
                code = str(((body.get("error") or {}).get("code")) or "unknown")
                raise OcrError(f"ocr_failed:{code}", transient=code in SERVER_ERROR_CODES)
            if status not in ("notStarted", "running"):
                raise OcrError(f"ocr_unexpected_status:{status}", transient=True)

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        try:
            return await self._http.request(method, url, headers=headers, timeout=60.0, **kwargs)
        except httpx.TransportError as exc:
            raise OcrError(f"ocr_transport:{type(exc).__name__}", transient=True) from exc


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _http_error(response: httpx.Response, stage: str) -> OcrError:
    status = response.status_code
    code = str(((_json(response).get("error") or {}).get("code")) or status)
    transient = status in (401, 403, 404, 408, 429) or status >= 500
    return OcrError(
        f"ocr_{stage}_{status}:{code}",
        transient=transient,
        retry_after=parse_retry_after(response.headers.get("Retry-After")),
    )


class DocumentIntelligenceLayout(DocumentIntelligenceReader):
    """Layout-defined figure regions/captions; never classify a whole page as a figure locally."""

    def __init__(self, *, governor: Governor | None = None, **kwargs):
        super().__init__(**kwargs)
        self.governor = governor or Governor("layout", max_concurrency=2)

    async def _bounded(self, cost: int, call):
        async def attempt():
            try:
                result = await call()
            except OcrError as exc:
                if re.search(r"_(429|503):", exc.code):
                    self.governor.record_throttle(exc.retry_after)
                raise
            self.governor.record_success()
            return result

        return await self.governor.run(cost, attempt)

    @property
    def cache_identity(self) -> str:
        return f"{self.endpoint}|prebuilt-layout|{self.api_version}|figures-v1"

    async def analyze_pages(self, data: bytes, first: int, last: int) -> tuple[dict, str]:
        if not 1 <= first <= last <= self.max_pages:
            raise OcrError("layout_page_limit_exceeded", transient=False)
        result, operation = await self._bounded(
            last - first + 1,
            lambda: self.analyze_operation(
                data,
                "prebuilt-layout",
                {"pages": f"{first}-{last}", "output": "figures", "stringIndexType": "unicodeCodePoint"},
            ),
        )
        pages = [p.get("pageNumber") for p in result.get("pages") or []]
        if pages != list(range(first, last + 1)):
            raise OcrError("layout_incomplete_pages", transient=True)
        return result, operation

    async def figure(self, operation: str, figure_id: str) -> bytes:
        return await self._bounded(0, lambda: self._figure(operation, figure_id))

    async def _figure(self, operation: str, figure_id: str) -> bytes:
        from urllib.parse import quote

        base = operation.split("?", 1)[0]
        if not base.startswith(self.endpoint + "/documentintelligence/documentModels/prebuilt-layout/analyzeResults/"):
            raise OcrError("layout_bad_operation_location", transient=True)
        if not isinstance(figure_id, str) or not figure_id or len(figure_id) > 200:
            raise OcrError("layout_bad_figure_id", transient=True)
        response = await self._send(
            "GET", base + "/figures/" + quote(figure_id, safe=""), params={"api-version": self.api_version}
        )
        if response.status_code != 200:
            raise _http_error(response, "figure")
        if not response.content.startswith(b"\x89PNG\r\n\x1a\n") or len(response.content) > 25_000_000:
            raise OcrError("layout_invalid_figure", transient=True)
        return response.content
