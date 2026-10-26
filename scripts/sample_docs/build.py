"""Generate the sample documents for hand-testing uploads and retrieval.

    .venv/bin/python -m scripts.sample_docs.build [output_dir]

Writes 12 files of each supported type, an edge-cases folder, ANSWER_KEY.md and manifest.json. Every subject is
different and every fact is invented, so a correct answer to a question in the key proves retrieval worked.
"""

from __future__ import annotations

import csv
import datetime as dt
import email.utils
import json
import shutil
import sys
from email.message import EmailMessage
from pathlib import Path

from . import images
from .content_decks_mail import DECKS, EMAILS
from .content_pdf_docx import DOCX, PDF
from .content_tables import CSV, XLSX
from .content_text_types import HTML, MD, TXT
from .render import to_docx, to_html, to_md, to_pdf, to_txt

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "sample-docs"
manifest: list[dict] = []


def record(kind: str, path: Path, title: str, author: str, date: str, q: str, a: str, note: str = "") -> None:
    manifest.append(
        {
            "type": kind,
            "file": f"{path.parent.name}/{path.name}",
            "title": title,
            "author": author,
            "date": date,
            "question": q,
            "answer": a,
            "note": note,
        }
    )


def stamp(date: str) -> dt.datetime:
    return dt.datetime.fromisoformat(date).replace(hour=10, tzinfo=dt.UTC)


def text_docs(out: Path, tmp_images: Path) -> None:
    plans = [
        ("pdf", PDF, ".pdf"),
        ("docx", DOCX, ".docx"),
        ("md", MD, ".md"),
        ("html", HTML, ".html"),
        ("txt", TXT, ".txt"),
    ]
    for kind, docs, ext in plans:
        folder = out / kind
        folder.mkdir(parents=True)
        for d in docs:
            p = folder / f"{d.slug}{ext}"
            note = ""
            if kind == "pdf":
                to_pdf(d, p)
            elif kind == "docx":
                pic = tmp_images / "03-allotment-site-plan.png" if d.slug.startswith("01-allotment") else None
                to_docx(d, p, pic)
                note = (
                    "Contains an embedded image (the allotment site plan) for the image-ingestion phase." if pic else ""
                )
            else:
                p.write_text({"md": to_md, "html": to_html, "txt": to_txt}[kind](d) + "\n", encoding="utf-8")
            record(kind, p, d.title, d.author, d.date, d.q, d.a, note)


def decks(out: Path, tmp_images: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    folder = out / "pptx"
    folder.mkdir()
    for slug, title, author, date, slides, q, a in DECKS:
        prs = Presentation()
        prs.core_properties.author = author
        prs.core_properties.title = title
        prs.core_properties.created = stamp(date)
        prs.core_properties.modified = stamp(date)
        s = prs.slides.add_slide(prs.slide_layouts[0])
        s.shapes.title.text = title
        s.placeholders[1].text = f"{author}, {date}"
        for st, bullets, notes in slides:
            s = prs.slides.add_slide(prs.slide_layouts[1])
            s.shapes.title.text = st
            tf = s.placeholders[1].text_frame
            tf.text = bullets[0]
            for b in bullets[1:]:
                tf.add_paragraph().text = b
            s.notes_slide.notes_text_frame.text = notes
        note = ""
        if slug.startswith("12-wetland"):
            s = prs.slides.add_slide(prs.slide_layouts[5])
            s.shapes.title.text = "Visiting the fen"
            s.shapes.add_picture(
                str(tmp_images / "10-heron-loop-trail-sign.gif"), Inches(1), Inches(1.6), width=Inches(8)
            )
            note = (
                "Final slide holds an image (a trail sign: Heron Loop, full circuit 6.4 km) for "
                "the image-ingestion phase."
            )
        p = folder / f"{slug}.pptx"
        prs.save(p)
        record("pptx", p, title, author, date, q, a, (note + " Answers may also come from speaker notes.").strip())


def sheets(out: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    folder = out / "xlsx"
    folder.mkdir()
    for slug, title, author, date, data, q, a in XLSX:
        wb = Workbook()
        wb.remove(wb.active)
        wb.properties.creator = author
        wb.properties.title = title
        wb.properties.created = stamp(date).replace(tzinfo=None)
        wb.properties.modified = stamp(date).replace(tzinfo=None)
        for name, rows in data.items():
            ws = wb.create_sheet(name)
            for r in rows:
                ws.append(r)
            for c in ws[1]:
                c.font = Font(bold=True)
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(len(str(c.value)) for c in col) + 3
        p = folder / f"{slug}.xlsx"
        wb.save(p)
        record("xlsx", p, title, author, date, q, a)


def csvs(out: Path) -> None:
    folder = out / "csv"
    folder.mkdir()
    for slug, author, date, rows, q, a in CSV:
        p = folder / f"{slug}.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        record(
            "csv",
            p,
            slug.split("-", 1)[1].replace("-", " ").title(),
            author,
            date,
            q,
            a,
            "CSV has no author or date metadata.",
        )


def emails(out: Path) -> None:
    folder = out / "eml"
    folder.mkdir()
    for slug, sender, to, date, subject, body, att, q, a in EMAILS:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = sender, to, subject
        parsed = dt.datetime.strptime(date, "%Y-%m-%d %H:%M %z")
        m["Date"] = email.utils.format_datetime(parsed)
        m["Message-ID"] = f"<{slug}@sample.example>"
        m.set_content(body)
        if att:
            m.add_attachment(att[1].encode(), maintype="text", subtype="plain", filename=att[0])
        p = folder / f"{slug}.eml"
        p.write_bytes(bytes(m))
        record(
            "eml",
            p,
            subject,
            sender.split("<")[0].strip(),
            date[:10],
            q,
            a,
            f"Attachment {att[0]} is listed, not processed." if att else "",
        )


def pictures(out: Path) -> None:
    images.save_all(out / "images")
    for name, _fn, author, date, q, a in images.IMAGES:
        record(
            "image",
            out / "images" / name,
            name.rsplit(".", 1)[0],
            author,
            date,
            q,
            a,
            "Phase 3B (images). Answer comes from the vision description.",
        )


def edge_cases(out: Path) -> None:
    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    folder = out / "edge-cases"
    folder.mkdir()
    src = out / "pdf" / "01-wexcombe-lighthouse-keepers-log.pdf"
    cases = []

    Image.open(out / "images" / "05-fish-bar-receipt.jpg").save(folder / "scanned-no-text-layer.pdf")
    cases.append(
        (
            "scanned-no-text-layer.pdf",
            "PDF with no text layer (a picture of a receipt)",
            "Falls back to Document Intelligence OCR. Ask: what was the total? (17.80)",
        )
    )

    w = PdfWriter(clone_from=PdfReader(src))
    w.encrypt(user_password="opensesame", owner_password="owner")
    with (folder / "password-protected.pdf").open("wb") as f:
        w.write(f)
    cases.append(
        (
            "password-protected.pdf",
            "Password-protected PDF (the lighthouse log)",
            "Should fail with statusDetail password_protected. The password is opensesame.",
        )
    )

    shutil.copy(src, folder / "duplicate-of-lighthouse-log.pdf")
    cases.append(
        (
            "duplicate-of-lighthouse-log.pdf",
            "Byte-identical copy of pdf/01",
            "Same sha256: checks duplicate handling. Content is the same so searches return it twice.",
        )
    )

    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 2048
    (folder / "legacy-word-97.doc").write_bytes(ole)
    (folder / "legacy-excel-97.xls").write_bytes(ole)
    cases.append(("legacy-word-97.doc", "Legacy .doc (OLE header only)", "Should fail with unsupported_file_type"))
    cases.append(("legacy-excel-97.xls", "Legacy .xls (OLE header only)", "Should fail with unsupported_file_type"))

    (folder / "empty.txt").write_bytes(b"")
    cases.append(("empty.txt", "Zero-byte text file", "Should be rejected or fail cleanly, not hang"))

    (folder / "corrupt.docx").write_bytes(b"PK\x03\x04 this is not really a zip archive")
    cases.append(("corrupt.docx", "Corrupt .docx", "Should fail with parser_error"))

    (folder / "mislabelled.pdf").write_text("This is plain text with a .pdf extension.\n")
    cases.append(("mislabelled.pdf", "Plain text named .pdf", "Checks type detection against the extension"))

    (folder / "unknown-type.xyz").write_text("Unknown extension.\n")
    cases.append(("unknown-type.xyz", "Unknown extension", "Should be rejected as unsupported_file_type"))

    for f, t, n in cases:
        manifest.append(
            {
                "type": "edge-case",
                "file": f"edge-cases/{f}",
                "title": t,
                "author": "",
                "date": "",
                "question": "",
                "answer": "",
                "note": n,
            }
        )


def answer_key(out: Path) -> None:
    lines = [
        "# Answer key",
        "",
        (
            "Every subject and fact in these files is invented, so a correct answer means "
            "retrieval worked, not that the model already knew it."
        ),
        "Ask the question with only the library selected; the answer should cite the file named.",
        "",
    ]
    kinds = ["pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "html", "eml", "image"]
    for k in kinds:
        items = [m for m in manifest if m["type"] == k]
        lines += [
            f"## {k.upper()} ({len(items)})",
            "",
            "| File | Author | Date | Question | Expected answer |",
            "|---|---|---|---|---|",
        ]
        for m in items:
            lines.append(
                f"| `{m['file'].split('/')[1]}` | {m['author']} | {m['date']} | {m['question']} | {m['answer']} |"
            )
        notes = [
            f"- `{m['file'].split('/')[1]}`: {m['note']}"
            for m in items
            if m["note"] and m["note"] != "CSV has no author or date metadata."
        ]
        lines += [""] + notes + [""]
    lines += ["## Edge cases", "", "| File | What it is | Expected |", "|---|---|---|"]
    lines += [
        f"| `{m['file'].split('/')[1]}` | {m['title']} | {m['note']} |" for m in manifest if m["type"] == "edge-case"
    ]
    lines += [
        "",
        "## Ideas for checking filters and retrieval",
        "",
        (
            "- Period filter: try 2023 only, then 2026 only; PDFs, DOCX and PPTX carry real "
            "created dates, so results should shrink accordingly."
        ),
        "- Author filter: pick any name in the tables above. Each author appears once or twice.",
        (
            "- Type filter: ask a question whose answer is in a CSV, with `fileTypes` set to "
            "`pdf` only; the right answer must NOT come back."
        ),
        (
            "- Near neighbours to tell apart: the three baking documents (sourdough PDF, "
            "pastry DOCX, bakery deck, night-bakery TXT) and the two bee documents (beekeeping deck, apiary mentions)."
        ),
        (
            "- Wording check: ask without the file's own vocabulary (e.g. 'who runs the choir "
            "solos' rather than 'Solo column')."
        ),
        "",
    ]
    (out / "ANSWER_KEY.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> None:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUT
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    pictures(out)
    text_docs(out, out / "images")
    decks(out, out / "images")
    sheets(out)
    csvs(out)
    emails(out)
    edge_cases(out)
    answer_key(out)
    n = sum(1 for m in manifest if m["type"] != "edge-case")
    print(f"wrote {n} files plus edge cases to {out}")


if __name__ == "__main__":
    main()
