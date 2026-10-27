"""Text extraction: native parsers first, OCR only as a bounded fallback.

Native extraction uses python-docx, python-pptx, openpyxl and pypdf. Azure Document
Intelligence (``prebuilt-read``) is used only for PDFs and images with no usable text layer,
and only within a page bound, because it is billed per page.

Content that arrived intact but cannot be parsed as plaintext is classified rather than
retried, because retrying cannot change the answer:

* rights-protected (RMS/IRM) Office or PDF → ``encrypted``;
* password-protected Office or PDF → ``categorical`` with ``password_protected``;
* an Office container (OLE) with no marker recognised → ``encrypted``: encrypted Office files
  are OLE-wrapped, and nothing else should arrive as one under a modern extension;
* anything else unparseable (a damaged file, plain text under a ``.pdf`` name) → ``failed``,
  reported as ``parser_error``. Calling it encrypted sent admins looking for a password
  (``evidence.md`` E33).

Transient failures (HTTP 5xx, timeouts) never reach this classification: they raise and the
queue message is retried. The rights-protection markers follow the MS-OFFCRYPTO data-space
names (``DRMEncryptedDataSpace`` / ``StrongEncryptionDataSpace``) and the PDF
``/MicrosoftIRMServices`` security handler; confirm them against real protected files early.

Every section carries a ``location`` (page, slide or sheet) so search results can cite where
a passage came from (spec §4.3, ``location``).

Author and authoring date come from document properties (Office, PDF) or message headers
(EML) and are returned beside the sections.

Image extraction and descriptions live in ``fli.images`` and are coordinated by the worker,
including standalone images and DOCX/PPTX pictures. PDF worker ingestion uses
``fli.figures`` for layout-separated text, captions and figure crops; the native/OCR path
below is retained for extraction callers. MSG remains unsupported.
"""

from __future__ import annotations

import asyncio
import csv
import email
import email.policy
import email.utils
import io
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Literal, Protocol

from .enums import ExtractionMethod

EXTRACTION_VERSION = "extract-py-v2"
# A spreadsheet is cut into sections of this many rows so that a chunk can cite its rows (spec §4.3).
ROWS_PER_SECTION = 25
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
    author: str | None = None
    authored_at: datetime | None = None
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
    return "failed", None  # no sign of protection: the file is damaged or not what its name says


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _metadata(read: Callable[[], tuple[str | None, datetime | None]]) -> tuple[str | None, datetime | None]:
    """Author and date are optional: a malformed property block must never fail the document."""
    try:
        return read()
    except Exception:  # noqa: BLE001
        return None, None


def _clean(value: object) -> str | None:
    text = str(value).strip() if value else ""
    return text or None


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _docx(data: bytes) -> tuple[list[Section], tuple[str | None, datetime | None]]:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(io.BytesIO(data))
    props = document.core_properties
    meta = _metadata(lambda: (_clean(props.author), _utc(props.created)))
    sections: list[Section] = []
    heading: str | None = None
    buffer: list[str] = []
    first_paragraph = None
    paragraphs = {element: i for i, element in enumerate(document.element.body.iter(qn("w:p")), 1)}
    # Walk the body in document order so a table stays under the heading it appears beneath.
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            para = Paragraph(child, document)
            style = (para.style.name if para.style is not None else "") or ""
            text = para.text.strip()
            if not text:
                continue
            if style.startswith("Heading") or style == "Title":
                if buffer:
                    sections.append(Section(heading, "\n".join(buffer), {"paragraph": first_paragraph}))
                    buffer = []
                    first_paragraph = None
                heading = text
            else:
                if not buffer:
                    first_paragraph = paragraphs[child]
                buffer.append(text)
        elif child.tag.endswith("}tbl"):
            rows = []
            for row in Table(child, document).rows:
                cells: list[str] = []
                seen: set[int] = set()
                for cell in row.cells:  # a merged cell is returned once per grid column it spans
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    text = cell.text.strip()
                    if text:
                        cells.append(text)
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                if not buffer:
                    first_paragraph = next((paragraphs[p] for p in child.iter(qn("w:p"))), None)
                buffer.extend(rows)
    if buffer:
        sections.append(Section(heading, "\n".join(buffer), {"paragraph": first_paragraph}))
    return sections, meta


def _pptx(data: bytes) -> tuple[list[Section], tuple[str | None, datetime | None]]:
    from pptx import Presentation

    deck = Presentation(io.BytesIO(data))
    meta = _metadata(lambda: (_clean(deck.core_properties.author), _utc(deck.core_properties.created)))
    sections: list[Section] = []
    for number, slide in enumerate(deck.slides, start=1):
        title = (
            slide.shapes.title.text.strip()
            if slide.shapes.title is not None and slide.shapes.title.has_text_frame
            else None
        )
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
    return sections, meta


def _rows_to_sections(name: str, rows: list[list[str]]) -> list[Section]:
    """Render rows as ``Header: value`` lines, ROWS_PER_SECTION at a time (spec §7.3).

    The first non-empty row is the header. Every line names its column, so a chunk is
    readable on its own and the header is effectively repeated in every chunk. Row numbers
    are the sheet's own (1-based, blank rows counted), so a citation finds the row.
    """
    numbered = [(n, r) for n, r in enumerate(rows, start=1) if any(c.strip() for c in r)]
    if len(numbered) < 2:
        return [Section(name, " | ".join(numbered[0][1]), {"sheet": name})] if numbered else []
    header = [h.strip() or f"column {i + 1}" for i, h in enumerate(numbered[0][1])]
    sections: list[Section] = []
    body = numbered[1:]
    for start in range(0, len(body), ROWS_PER_SECTION):
        group = body[start : start + ROWS_PER_SECTION]
        lines = []
        for number, row in group:
            cells = [
                f"{header[i] if i < len(header) else f'column {i + 1}'}: {c.strip()}"
                for i, c in enumerate(row)
                if c.strip()
            ]
            lines.append(f"Row {number}. " + "; ".join(cells))
        location = {"sheet": name, "rowStart": group[0][0], "rowEnd": group[-1][0]}
        sections.append(Section(name, "\n".join(lines), location))
    return sections


def _xlsx(data: bytes) -> tuple[list[Section], tuple[str | None, datetime | None]]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sections: list[Section] = []
    for sheet in workbook.worksheets:
        rows = [["" if v is None else str(v) for v in row] for row in sheet.iter_rows(values_only=True)]
        sections.extend(_rows_to_sections(sheet.title, rows))
    props = workbook.properties
    return sections, _metadata(lambda: (_clean(props.creator), _utc(props.created)))


def _csv(text: str, filename: str) -> list[Section]:
    name = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "data"
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return _rows_to_sections(name, list(csv.reader(io.StringIO(text.lstrip("\ufeff")), dialect)))


_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:([Zz+-])(\d{2})?'?(\d{2})?)?")


def _pdf_date(raw: object) -> datetime | None:
    """Parse a PDF date, ``D:YYYYMMDDHHmmSSOHH'mm`` (every part after the year optional), to UTC."""
    match = _PDF_DATE.match(str(raw or ""))
    if not match:
        return None
    groups = match.groups()
    parts = [int(g) if g else d for g, d in zip(groups[:6], (0, 1, 1, 0, 0, 0), strict=True)]
    sign, hours, minutes = groups[6], int(groups[7] or 0), int(groups[8] or 0)
    offset = timedelta(hours=hours, minutes=minutes) * (-1 if sign == "-" else 1)
    try:
        return datetime(*parts, tzinfo=timezone(offset) if sign in ("+", "-") else UTC).astimezone(UTC)
    except ValueError:
        return None


def _pdf(data: bytes, read_text: bool = True) -> tuple[list[Section], int, tuple[str | None, datetime | None]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise PermissionError("pdf requires a password")
        except Exception as exc:  # noqa: BLE001
            raise PermissionError("pdf requires a password") from exc
    sections = []
    for number, page in enumerate(reader.pages if read_text else [], start=1):
        text = (page.extract_text() or "").strip()
        if text:
            sections.append(Section(None, text, {"page": number}))

    def read_meta() -> tuple[str | None, datetime | None]:
        info = reader.metadata
        return (_clean(info.author), _pdf_date(info.get("/CreationDate"))) if info else (None, None)

    meta = _metadata(read_meta)
    return sections, len(reader.pages), meta


def inspect_pdf(data: bytes) -> Extracted:
    """Validate protection/structure and read metadata without duplicating layout OCR."""
    if b"/MicrosoftIRMServices" in data[:1_000_000]:
        return Extracted("encrypted", method=ExtractionMethod.UNSUPPORTED)
    try:
        _, pages, (author, authored_at) = _pdf(data, read_text=False)
        return Extracted("ok", page_count=pages, author=author, authored_at=authored_at)
    except PermissionError:
        return Extracted("categorical", categorical_reason="password_protected", method=ExtractionMethod.UNSUPPORTED)
    except Exception as exc:
        outcome, reason = classify_unreadable(data)
        return Extracted(
            outcome,
            categorical_reason=reason,
            failure_reason=f"parser:{type(exc).__name__}",
            method=ExtractionMethod.UNSUPPORTED,
        )


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


class _HtmlSections(HTMLParser):
    """Collects visible text, starting a new section at each heading (h1-h6)."""

    # Not "head": a page that never closes <head> would lose its whole body.
    _SKIP = {"script", "style", "noscript", "template", "title"}
    _BREAK = {"p", "div", "li", "br", "tr", "section", "article", "table", "ul", "ol", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = []
        self.heading: str | None = None
        self._buffer: list[str] = []
        self._skip = 0
        self._in_heading = False
        self._heading_text: list[str] = []

    def _flush(self) -> None:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in "".join(self._buffer).splitlines()]
        body = "\n".join(line for line in lines if line)
        if body:
            self.sections.append(Section(self.heading, body))
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._in_heading, self._heading_text = True, []
        elif tag in self._BREAK:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif re.fullmatch(r"h[1-6]", tag) and self._in_heading:
            self._in_heading = False
            self.heading = re.sub(r"\s+", " ", "".join(self._heading_text)).strip() or self.heading
        elif tag in self._BREAK:
            self._buffer.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        (self._heading_text if self._in_heading else self._buffer).append(data)


def _html(text: str) -> list[Section]:
    parser = _HtmlSections()
    parser.feed(text)
    parser.close()
    parser._flush()
    return parser.sections


def _eml(data: bytes) -> tuple[list[Section], tuple[str | None, datetime | None]]:
    message = email.message_from_bytes(data, policy=email.policy.default)

    def read_meta() -> tuple[str | None, datetime | None]:
        sender, date = message.get("From"), message.get("Date")
        name, address = email.utils.parseaddr(str(sender)) if sender else ("", "")
        try:
            parsed = email.utils.parsedate_to_datetime(str(date)) if date else None
        except (TypeError, ValueError):
            parsed = None
        return _clean(name or address), _utc(parsed)

    author, authored_at = _metadata(read_meta)
    lines = [
        f"{label}: {message[key]}"
        for label, key in (("From", "From"), ("To", "To"), ("Cc", "Cc"), ("Date", "Date"))
        if message[key]
    ]
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        try:
            content = body_part.get_content()
        except (LookupError, UnicodeError):  # unknown or wrong declared charset
            content = (body_part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if body_part.get_content_type() == "text/html":
            content = "\n".join(s.text for s in _html(content))
        body = str(content).strip()
    attachments = [p.get_filename() for p in message.iter_attachments() if p.get_filename()]
    if attachments:
        lines.append("Attachments: " + ", ".join(attachments))
    text = "\n".join(lines) + ("\n\n" + body if body else "")
    subject = _clean(message["Subject"])
    return ([Section(subject, text)] if text.strip() else []), (author, authored_at)


TEXT_TYPES = {"txt", "md", "markdown", "json", "log"}
LEGACY_OFFICE = {"doc", "ppt", "xls"}  # unsupported in v1 (spec §7.3), whatever content type is declared
IMAGE_TYPES = {"png", "jpg", "jpeg", "tif", "tiff", "bmp"}


async def extract(data: bytes, *, filename: str, content_type: str = "", ocr: OcrClient | None = None) -> Extracted:
    ext = _ext(filename)
    try:
        if ext in ("docx", "pptx", "xlsx"):
            if not zipfile.is_zipfile(io.BytesIO(data)):
                outcome, reason = classify_unreadable(data)
                return Extracted(outcome, categorical_reason=reason, method=ExtractionMethod.UNSUPPORTED)
            # Parsers are synchronous and can run for many seconds: keep the event loop free
            # (queue lock renewal and other lanes run on it).
            parse = {"docx": _docx, "pptx": _pptx, "xlsx": _xlsx}[ext]
            sections, (author, authored_at) = await asyncio.to_thread(parse, data)
            return Extracted("ok", sections, author=author, authored_at=authored_at)
        if ext == "pdf":
            if b"/MicrosoftIRMServices" in data[:1_000_000]:
                return Extracted("encrypted", method=ExtractionMethod.UNSUPPORTED)
            try:
                sections, pages, (author, authored_at) = await asyncio.to_thread(_pdf, data)
            except PermissionError:
                return Extracted(
                    "categorical", categorical_reason="password_protected", method=ExtractionMethod.UNSUPPORTED
                )
            if sections:
                return Extracted("ok", sections, page_count=pages, author=author, authored_at=authored_at)
            return await _ocr_fallback(data, "application/pdf", ocr, pages)
        if ext in IMAGE_TYPES:
            return await _ocr_fallback(data, content_type or f"image/{ext}", ocr, 1)
        if ext in LEGACY_OFFICE:
            return Extracted(
                "unsupported", unsupported_reason="unsupported_file_type", method=ExtractionMethod.UNSUPPORTED
            )
        if ext == "eml" or content_type == "message/rfc822":
            sections, (author, authored_at) = await asyncio.to_thread(_eml, data)
            return Extracted("ok", sections, author=author, authored_at=authored_at)
        if ext == "csv":
            return Extracted("ok", await asyncio.to_thread(_csv, data.decode("utf-8", errors="replace"), filename))
        if ext in TEXT_TYPES or content_type.startswith("text/plain") or content_type == "text/markdown":
            text = data.decode("utf-8", errors="replace")
            return Extracted(
                "ok",
                _markdown(text)
                if ext in ("md", "markdown")
                else ([Section(None, text.strip())] if text.strip() else []),
            )
        if ext in ("html", "htm", "aspx") or content_type.startswith("text/html"):
            return Extracted("ok", await asyncio.to_thread(_html, data.decode("utf-8", errors="replace")))
        if data[:8] == OLE_MAGIC:
            outcome, reason = classify_unreadable(data)
            if outcome != "encrypted" or reason:
                return Extracted(outcome, categorical_reason=reason, method=ExtractionMethod.UNSUPPORTED)
        return Extracted("unsupported", unsupported_reason="unsupported_file_type", method=ExtractionMethod.UNSUPPORTED)
    except OcrError:
        raise  # a transient OCR failure retries; it is never classified as unreadable content
    except Exception as exc:  # noqa: BLE001 — a parser crash on successfully-read content
        if ext not in ("docx", "pptx", "xlsx", "pdf"):  # text formats cannot be encrypted containers
            return Extracted(
                "failed", failure_reason=f"parser:{type(exc).__name__}", method=ExtractionMethod.UNSUPPORTED
            )
        outcome, reason = classify_unreadable(data)
        return Extracted(
            outcome,
            categorical_reason=reason,
            failure_reason=f"parser:{type(exc).__name__}",
            method=ExtractionMethod.UNSUPPORTED,
        )


async def _ocr_fallback(data: bytes, content_type: str, ocr: OcrClient | None, pages: int | None) -> Extracted:
    if ocr is None:
        return Extracted(
            "needs_ocr",
            page_count=pages,
            unsupported_reason="no_text_layer_and_ocr_disabled",
            method=ExtractionMethod.UNSUPPORTED,
        )
    if pages is not None and pages > ocr.max_pages:  # bounded fallback: never a partial read of a longer document
        return Extracted(
            "unsupported",
            page_count=pages,
            unsupported_reason="ocr_page_limit_exceeded",
            method=ExtractionMethod.UNSUPPORTED,
        )
    try:
        text, analysed = await ocr.read(data, content_type)
    except OcrError as exc:
        if exc.transient:
            raise
        return Extracted(
            "failed",
            page_count=pages,
            unsupported_reason=exc.code,
            failure_reason=f"ocr:{exc.code}",
            method=ExtractionMethod.DOCUMENT_INTELLIGENCE_READ,
            ocr_applied=True,
        )
    if not text.strip():
        return Extracted(
            "unsupported",
            page_count=analysed,
            unsupported_reason="ocr_returned_no_text",
            method=ExtractionMethod.DOCUMENT_INTELLIGENCE_READ,
            ocr_applied=True,
            usage_units=analysed,
        )
    return Extracted(
        "ok",
        [Section(None, text.strip())],
        method=ExtractionMethod.DOCUMENT_INTELLIGENCE_READ,
        ocr_applied=True,
        page_count=analysed,
        usage_units=analysed,
    )
