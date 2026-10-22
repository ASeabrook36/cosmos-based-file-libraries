"""Text extraction: native parsers first, OCR only as a bounded fallback.

Native extraction uses python-docx, python-pptx, openpyxl and pypdf. Azure Document
Intelligence (``prebuilt-read``) is used only for PDFs and images with no usable text layer,
and only within a page bound, because it is billed per page.

Content that arrived intact but cannot be parsed as plaintext is classified rather than
retried, because retrying cannot change the answer:

* rights-protected (RMS/IRM) Office or PDF → ``encrypted``;
* password-protected Office or PDF → ``categorical`` with ``password_protected``;
* unparseable for a reason not recognised → ``encrypted`` (treated as unreadable).

Transient failures (HTTP 5xx, timeouts) never reach this classification: they raise and the
queue message is retried. The rights-protection markers follow the MS-OFFCRYPTO data-space
names (``DRMEncryptedDataSpace`` / ``StrongEncryptionDataSpace``) and the PDF
``/MicrosoftIRMServices`` security handler; confirm them against real protected files early.

Every section carries a ``location`` (page, slide or sheet) so search results can cite where
a passage came from (spec §4.3, ``location``).

Not handled here yet (spec §7.3, §7.5): image extraction from DOCX/PPTX, EML/MSG, and author
and date metadata. Standalone images should go to the image pipeline (spec §7.5), not to the
OCR path below, which is kept for scanned pages.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .enums import ExtractionMethod

EXTRACTION_VERSION = "extract-py-v1"
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")

Outcome = Literal["ok", "encrypted", "categorical", "unsupported", "needs_ocr", "failed"]


@dataclass(frozen=True)
class Section:
    heading: str | None
    text: str
    location: dict[str, Any] | None = None


@dataclass
class Extracted:
    outcome: Outcome
    sections: list[Section] = field(default_factory=list)
    method: ExtractionMethod = ExtractionMethod.NATIVE_TEXT
    ocr_applied: bool = False
    page_count: int | None = None
    categorical_reason: str | None = None
    unsupported_reason: str | None = None
    failure_reason: str | None = None
    usage_units: int | None = None
    extraction_version: str = EXTRACTION_VERSION

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.sections if s.text)


class OcrError(Exception):
    """An OCR call that did not produce text. ``transient`` failures (throttling, timeouts,
    5xx) retry the queue message; the rest record the document as failed with ``code``."""

    def __init__(self, code: str, *, transient: bool, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.retry_after = retry_after


class OcrClient(Protocol):
    max_pages: int  # documents known to be longer are refused before any call

    async def read(self, data: bytes, content_type: str) -> tuple[str, int]:
        """Return (text, pages analysed). Raises OcrError."""


def _utf16(marker: str) -> bytes:
    return marker.encode("utf-16-le")


def classify_unreadable(data: bytes) -> tuple[Outcome, str | None]:
    """Classify content that arrived but is not plaintext-parseable."""
    head = data[:8]
    if head == OLE_MAGIC:
        if _utf16("DRMEncrypted") in data or b"DRMEncrypted" in data:
            return "encrypted", None
        if _utf16("StrongEncryption") in data or _utf16("EncryptionInfo") in data:
            return "categorical", "password_protected"
        return "encrypted", None
    if data[:5] == b"%PDF-":
        if b"/MicrosoftIRMServices" in data:
            return "encrypted", None
        if b"/Encrypt" in data:
            return "categorical", "password_protected"
    return "encrypted", None


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _docx(data: bytes) -> list[Section]:
    import docx

    document = docx.Document(io.BytesIO(data))
    sections: list[Section] = []
    heading: str | None = None
    buffer: list[str] = []
    for para in document.paragraphs:
        style = (para.style.name if para.style is not None else "") or ""
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("Heading") or style == "Title":
            if buffer:
                sections.append(Section(heading, "\n".join(buffer)))
                buffer = []
            heading = text
        else:
            buffer.append(text)
    if buffer:
        sections.append(Section(heading, "\n".join(buffer)))
    for table in document.tables:
        rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        if any(r.strip(" |") for r in rows):
            sections.append(Section(None, "\n".join(rows)))
    return sections


def _pptx(data: bytes) -> list[Section]:
    from pptx import Presentation

    deck = Presentation(io.BytesIO(data))
    sections: list[Section] = []
    for number, slide in enumerate(deck.slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None and slide.shapes.title.has_text_frame else None
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape is not slide.shapes.title:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(t)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                texts.append(f"Speaker notes: {notes}")
        if title or texts:
            sections.append(Section(title or f"Slide {number}", "\n".join(texts), {"slide": number}))
    return sections


def _xlsx(data: bytes) -> list[Section]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sections: list[Section] = []
    for sheet in workbook.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                rows.append(" | ".join(cells).rstrip(" |"))
        if rows:
            sections.append(Section(sheet.title, "\n".join(rows), {"sheet": sheet.title}))
    return sections


def _pdf(data: bytes) -> tuple[list[Section], int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise PermissionError("pdf requires a password")
        except Exception as exc:  # noqa: BLE001
            raise PermissionError("pdf requires a password") from exc
    sections = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            sections.append(Section(None, text, {"page": number}))
    return sections, len(reader.pages)


_MD_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def _markdown(text: str) -> list[Section]:
    sections: list[Section] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = _MD_HEADING.match(line)
        if match:
            if any(b.strip() for b in buffer):
                sections.append(Section(heading, "\n".join(buffer).strip()))
            heading, buffer = match.group(1).strip(), []
        else:
            buffer.append(line)
    if any(b.strip() for b in buffer):
        sections.append(Section(heading, "\n".join(buffer).strip()))
    return sections


def _html(text: str) -> list[Section]:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    body = "\n".join(line for line in lines if line)
    return [Section(None, body)] if body else []


TEXT_TYPES = {"txt", "md", "markdown", "csv", "json", "log"}
IMAGE_TYPES = {"png", "jpg", "jpeg", "tif", "tiff", "bmp"}


async def extract(data: bytes, *, filename: str, content_type: str = "", ocr: OcrClient | None = None) -> Extracted:
    ext = _ext(filename)
    try:
        if ext in ("docx", "pptx", "xlsx"):
            if not zipfile.is_zipfile(io.BytesIO(data)):
                outcome, reason = classify_unreadable(data)
                return Extracted(outcome, categorical_reason=reason, method=ExtractionMethod.UNSUPPORTED)
            sections = {"docx": _docx, "pptx": _pptx, "xlsx": _xlsx}[ext](data)
            return Extracted("ok", sections)
        if ext == "pdf":
            if b"/MicrosoftIRMServices" in data[:1_000_000]:
                return Extracted("encrypted", method=ExtractionMethod.UNSUPPORTED)
            try:
                sections, pages = _pdf(data)
            except PermissionError:
                return Extracted("categorical", categorical_reason="password_protected", method=ExtractionMethod.UNSUPPORTED)
            if sections:
                return Extracted("ok", sections, page_count=pages)
            return await _ocr_fallback(data, "application/pdf", ocr, pages)
        if ext in IMAGE_TYPES:
            return await _ocr_fallback(data, content_type or f"image/{ext}", ocr, 1)
        if ext in TEXT_TYPES or content_type.startswith("text/plain") or content_type == "text/markdown":
            text = data.decode("utf-8", errors="replace")
            return Extracted("ok", _markdown(text) if ext in ("md", "markdown") else ([Section(None, text.strip())] if text.strip() else []))
        if ext in ("html", "htm", "aspx") or content_type.startswith("text/html"):
            return Extracted("ok", _html(data.decode("utf-8", errors="replace")))
        if data[:8] == OLE_MAGIC:
            outcome, reason = classify_unreadable(data)
            if outcome != "encrypted" or reason:
                return Extracted(outcome, categorical_reason=reason, method=ExtractionMethod.UNSUPPORTED)
        return Extracted("unsupported", unsupported_reason="unsupported_file_type", method=ExtractionMethod.UNSUPPORTED)
    except OcrError:
        raise  # a transient OCR failure retries; it is never classified as unreadable content
    except Exception as exc:  # noqa: BLE001 — a parser crash on successfully-read content
        outcome, reason = classify_unreadable(data)
        return Extracted(outcome, categorical_reason=reason, failure_reason=f"parser:{type(exc).__name__}", method=ExtractionMethod.UNSUPPORTED)


async def _ocr_fallback(data: bytes, content_type: str, ocr: OcrClient | None, pages: int | None) -> Extracted:
    if ocr is None:
        return Extracted("needs_ocr", page_count=pages, unsupported_reason="no_text_layer_and_ocr_disabled", method=ExtractionMethod.UNSUPPORTED)
    if pages is not None and pages > ocr.max_pages:  # bounded fallback: never a partial read of a longer document
        return Extracted("unsupported", page_count=pages, unsupported_reason="ocr_page_limit_exceeded", method=ExtractionMethod.UNSUPPORTED)
    try:
        text, analysed = await ocr.read(data, content_type)
    except OcrError as exc:
        if exc.transient:
            raise
        return Extracted("failed", page_count=pages, unsupported_reason=exc.code, failure_reason=f"ocr:{exc.code}",
                         method=ExtractionMethod.DOCUMENT_INTELLIGENCE_READ, ocr_applied=True)
    if not text.strip():
        return Extracted("unsupported", page_count=analysed, unsupported_reason="ocr_returned_no_text",
                         method=ExtractionMethod.DOCUMENT_INTELLIGENCE_READ, ocr_applied=True, usage_units=analysed)
    return Extracted("ok", [Section(None, text.strip())], method=ExtractionMethod.DOCUMENT_INTELLIGENCE_READ, ocr_applied=True,
                     page_count=analysed, usage_units=analysed)
