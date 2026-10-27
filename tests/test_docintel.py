"""Document Intelligence prebuilt-read client and the extractor's bounded OCR fallback,
against a fake transport."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from fli.docintel import DocumentIntelligenceReader
from fli.enums import ExtractionMethod
from fli.extraction import OcrError, extract
from tests.test_extraction_and_chunking import blank_pdf

ENDPOINT = "https://docintel.example.invalid"
RESULT = f"{ENDPOINT}/documentintelligence/documentModels/prebuilt-read/analyzeResults/r1?api-version=2024-11-30"


class FakeDocIntel:
    def __init__(
        self, *, analyze: list[httpx.Response] | None = None, polls: list[httpx.Response] | None = None
    ) -> None:
        self.analyze = analyze or [httpx.Response(202, headers={"Operation-Location": RESULT})]
        self.polls = polls or [succeeded("scanned orion text", 2)]
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer di-token"
        if request.method == "POST":
            return self.analyze.pop(0)
        return self.polls.pop(0)


def succeeded(text: str, pages: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "succeeded",
            "analyzeResult": {
                "apiVersion": "2024-11-30",
                "modelId": "prebuilt-read",
                "content": text,
                "pages": [{"pageNumber": i + 1} for i in range(pages)],
            },
        },
    )


def reader(fake: FakeDocIntel, *, max_pages: int = 5, max_bytes: int = 1_000_000, timeout: float = 30.0):
    clock, sleeps = {"t": 0.0}, []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    async def token() -> str:
        return "di-token"

    r = DocumentIntelligenceReader(
        endpoint=ENDPOINT,
        token_provider=token,
        http=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
        max_pages=max_pages,
        max_bytes=max_bytes,
        timeout_seconds=timeout,
        sleep=sleep,
        clock=lambda: clock["t"],
    )
    return r, sleeps


async def test_analyze_then_poll_returns_text_and_pages_analysed():
    fake = FakeDocIntel(
        polls=[
            httpx.Response(200, headers={"Retry-After": "2"}, json={"status": "running"}),
            succeeded("scanned orion text", 2),
        ]
    )
    r, sleeps = reader(fake)
    text, pages = await r.read(b"%PDF-bytes", "application/pdf")
    assert (text, pages) == ("scanned orion text", 2)
    post = fake.requests[0]
    assert post.url.path == "/documentintelligence/documentModels/prebuilt-read:analyze"
    assert post.url.params["api-version"] == "2024-11-30" and post.url.params["pages"] == "1-5"
    assert base64.b64decode(json.loads(post.content)["base64Source"]) == b"%PDF-bytes"
    assert [str(q.url) for q in fake.requests[1:]] == [RESULT, RESULT]
    assert sleeps[1] == 2.0, "the poll's Retry-After is honoured"


async def test_oversized_input_is_refused_before_any_call():
    fake = FakeDocIntel()
    r, _ = reader(fake, max_bytes=4)
    with pytest.raises(OcrError) as raised:
        await r.read(b"12345", "image/png")
    assert raised.value.code == "ocr_input_too_large" and not raised.value.transient and fake.requests == []


@pytest.mark.parametrize(
    ("response", "transient"),
    [
        (httpx.Response(429, headers={"Retry-After": "7"}), True),
        (httpx.Response(503), True),
        (
            httpx.Response(403, json={"error": {"code": "PermissionDenied"}}),
            True,
        ),  # deployment fault: dead-letters, never stamps the item
        (
            httpx.Response(400, json={"error": {"code": "InvalidRequest", "innererror": {"code": "InvalidContent"}}}),
            False,
        ),
    ],
)
async def test_analyze_errors_map_to_retry_or_item_failure(response, transient):
    r, _ = reader(FakeDocIntel(analyze=[response]))
    with pytest.raises(OcrError) as raised:
        await r.read(b"x", "application/pdf")
    assert raised.value.transient is transient
    if response.status_code == 429:
        assert raised.value.retry_after == 7


@pytest.mark.parametrize(("code", "transient"), [("InvalidContent", False), ("InternalServerError", True)])
async def test_a_failed_operation_maps_by_error_code(code, transient):
    r, _ = reader(FakeDocIntel(polls=[httpx.Response(200, json={"status": "failed", "error": {"code": code}})]))
    with pytest.raises(OcrError) as raised:
        await r.read(b"x", "application/pdf")
    assert raised.value.code == f"ocr_failed:{code}" and raised.value.transient is transient


async def test_polling_stops_at_the_timeout():
    r, _ = reader(FakeDocIntel(polls=[httpx.Response(200, json={"status": "running"}) for _ in range(20)]), timeout=5.0)
    with pytest.raises(OcrError) as raised:
        await r.read(b"x", "application/pdf")
    assert raised.value.code == "ocr_timeout" and raised.value.transient


async def test_a_poll_location_off_the_endpoint_is_never_followed():
    """The bearer token must not be sent to a host named by a response header."""
    fake = FakeDocIntel(
        analyze=[httpx.Response(202, headers={"Operation-Location": "https://elsewhere.example.invalid/steal"})]
    )
    r, _ = reader(fake)
    with pytest.raises(OcrError):
        await r.read(b"x", "application/pdf")
    assert len(fake.requests) == 1


async def test_extractor_refuses_a_pdf_longer_than_the_bound_without_calling():
    calls = []

    class Ocr:
        max_pages = 1

        async def read(self, data, content_type):  # noqa: ANN001
            calls.append(1)
            return "x", 1

    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    two_page = buf.getvalue()
    result = await extract(two_page, filename="scan.pdf", ocr=Ocr())
    assert result.outcome == "unsupported" and result.unsupported_reason == "ocr_page_limit_exceeded" and calls == []


async def test_a_transient_ocr_failure_is_never_classified_as_unreadable_content():
    """Before this, the extractor's catch-all turned any OCR exception into an 'encrypted' verdict."""

    class Throttled:
        max_pages = 5

        async def read(self, data, content_type):  # noqa: ANN001
            raise OcrError("ocr_analyze_429:429", transient=True, retry_after=3)

    with pytest.raises(OcrError):
        await extract(blank_pdf(), filename="scan.pdf", ocr=Throttled())


async def test_a_permanent_ocr_failure_records_the_item_failed_with_its_code():
    class Broken:
        max_pages = 5

        async def read(self, data, content_type):  # noqa: ANN001
            raise OcrError("ocr_failed:InvalidContent", transient=False)

    result = await extract(blank_pdf(), filename="scan.pdf", ocr=Broken())
    assert result.outcome == "failed" and result.failure_reason == "ocr:ocr_failed:InvalidContent"
    assert result.ocr_applied and result.method == ExtractionMethod.DOCUMENT_INTELLIGENCE_READ


# Layout figure extraction uses the service's figure segmentation, not PDF objects.
LAYOUT_RESULT = RESULT.replace("prebuilt-read", "prebuilt-layout")


def layout_reader(handler):
    from fli.docintel import DocumentIntelligenceLayout

    async def token():
        return "di-token"

    async def no_sleep(seconds):
        pass

    return DocumentIntelligenceLayout(
        endpoint=ENDPOINT,
        token_provider=token,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_pages=2000,
        max_bytes=1_000_000,
        sleep=no_sleep,
    )


async def test_layout_requests_crops_and_unicode_offsets_for_exact_requested_pages():
    from tests.layout_fakes import png

    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"Operation-Location": LAYOUT_RESULT})
        if "/figures/" in request.url.path:
            return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})
        return httpx.Response(
            200,
            json={
                "status": "succeeded",
                "analyzeResult": {
                    "pages": [{"pageNumber": 21}, {"pageNumber": 22}],
                    "figures": [{"id": "opaque-id"}],
                },
            },
        )

    reader = layout_reader(handler)
    result, operation = await reader.analyze_pages(b"%PDF", 21, 22)
    assert requests[0].url.params["pages"] == "21-22"
    assert requests[0].url.params["output"] == "figures"
    assert requests[0].url.params["stringIndexType"] == "unicodeCodePoint"
    assert result["figures"][0]["id"] == "opaque-id"
    assert await reader.figure(operation, "opaque-id") == png()
    assert requests[-1].url.path.endswith("/analyzeResults/r1/figures/opaque-id")
    assert requests[-1].headers["Authorization"] == "Bearer di-token"
    assert reader.governor.spent == 2 and reader.governor.requests == 2


async def test_layout_rejects_incomplete_page_result_instead_of_publishing_partial_text():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, headers={"Operation-Location": LAYOUT_RESULT})
        return succeeded("only one page", 1)

    with pytest.raises(OcrError, match="layout_incomplete_pages") as raised:
        await layout_reader(handler).analyze_pages(b"source", 1, 2)
    assert raised.value.transient


@pytest.mark.parametrize(
    "operation",
    [
        "https://elsewhere.example.invalid/steal",
        ENDPOINT + "/documentintelligence/documentModels/prebuilt-read/analyzeResults/r1",
    ],
)
async def test_figure_download_never_sends_credentials_off_the_layout_result_path(operation):
    requests = []
    reader = layout_reader(lambda request: requests.append(request))
    with pytest.raises(OcrError, match="layout_bad_operation_location"):
        await reader.figure(operation, "1.1")
    assert requests == []


async def test_layout_throttle_updates_shared_governor_and_preserves_retry_after():
    reader = layout_reader(lambda request: httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(OcrError) as raised:
        await reader.analyze_pages(b"source", 1, 1)
    assert raised.value.retry_after == 7
    assert reader.governor.throttled == 1 and reader.governor.in_cooldown


async def test_layout_binds_analysis_concurrency_across_worker_lanes():
    import asyncio

    reader = layout_reader(lambda request: None)
    active = peak = 0

    async def analysis(data, model, params):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"pages": [{"pageNumber": 1}]}, LAYOUT_RESULT

    reader.analyze_operation = analysis
    await asyncio.gather(*(reader.analyze_pages(b"source", 1, 1) for _ in range(6)))
    assert peak == 2
