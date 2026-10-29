"""Build six small synthetic compatibility cases in private storage, never demo libraries.

Requires the optional reportlab package for PDF fixtures. These are plumbing cases,
not a representative quality benchmark. Existing Office fixture bytes are unchanged.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from scripts.microsoft_reuse_lab import ROOT, private_directory, save

SELECTIONS = {
    "nested-report": [
        {
            "label": "chapter-one-coverage",
            "heading": "Chapter 1 Marsh drainage",
            "include": ["eleven hours", "Synthetic observations do not establish causation"],
            "exclude": ["nineteen parts per thousand"],
        },
        {
            "label": "chapter-two-coverage",
            "heading": "Chapter 2 Salinity and vegetation",
            "include": ["nineteen parts per thousand", "Synthetic observations do not establish causation"],
            "exclude": ["Standing water lasted eighteen hours"],
        },
    ],
    "play": [{"label": "act-two-coverage", "heading": "Act II", "include": ["fund another year"]}],
    "opinion": [{"label": "dissent-coverage", "heading": "Dissent", "include": ["validate timestamps"]}],
}


def build(work: Path) -> None:
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Image as PdfImage
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()

    def pdf(name: str, story: list) -> None:
        SimpleDocTemplate(str(work / name), title="Synthetic Microsoft reuse compatibility fixture", invariant=1).build(
            story
        )

    chart = Image.new("RGB", (800, 300), "white")
    draw = ImageDraw.Draw(chart)
    draw.rectangle((130, 70, 310, 250), fill="#365f91")
    draw.rectangle((450, 140, 630, 250), fill="#76933c")
    draw.text((130, 260), "Blocked: 18 hours", fill="black", font=ImageFont.load_default(size=22))
    draw.text((450, 260), "Reopened: 11 hours", fill="black", font=ImageFont.load_default(size=22))
    chart.save(work / "drainage.png")
    story = []
    chapters = [
        (
            "Chapter 1 Marsh drainage",
            "1.1 Methods",
            "Loggers sampled every fifteen minutes for eight weeks.",
            "1.2 Results",
            "Standing water lasted eighteen hours in blocked plots and eleven hours in reopened plots.",
        ),
        (
            "Chapter 2 Salinity and vegetation",
            "2.1 Methods",
            "Weekly pore-water samples measured salt exposure.",
            "2.2 Results",
            "Median salinity increased from twelve to nineteen parts per thousand.",
        ),
    ]
    for i, (title, mh, method, rh, result) in enumerate(chapters):
        if i:
            story.append(PageBreak())
        story += [
            Paragraph(title, styles["Heading1"]),
            Paragraph(mh, styles["Heading2"]),
            Paragraph(method, styles["BodyText"]),
            Paragraph(rh, styles["Heading2"]),
            Paragraph(result, styles["BodyText"]),
            Spacer(1, 18),
        ]
        if i == 0:
            story += [
                PdfImage(str(work / "drainage.png"), width=440, height=165),
                Paragraph("Figure 1 Standing-water duration in the two plot groups.", styles["BodyText"]),
            ]
        story += [
            Paragraph("Limitations", styles["Heading2"]),
            Paragraph("Synthetic observations do not establish causation or annual effects.", styles["BodyText"]),
        ]
    pdf("nested-report.pdf", story)
    for label, source in [("paper", "research"), ("play", "play"), ("opinion", "opinion")]:
        story = []
        for block in (ROOT / f"tests/fixtures/structure-lab/{source}.md").read_text().strip().split("\n\n"):
            level = "Heading2" if block.startswith("## ") else "Heading1" if block.startswith("# ") else "BodyText"
            text = " ".join(block.lstrip("# ").splitlines())
            story += [Paragraph(text, styles[level]), Spacer(1, 6)]
        pdf(f"{label}.pdf", story)
    scan = Image.new("RGB", (1200, 700), "white")
    draw = ImageDraw.Draw(scan)
    font = ImageFont.load_default(size=32)
    for y, text in enumerate(
        [
            "Marsh monitoring field record",
            "Sensor MARSH-42 was offline for two days.",
            "The team retained the missing intervals.",
            "No replacement values were invented.",
        ]
    ):
        draw.text((60, 70 + y * 100), text, fill="black", font=font)
    scan.save(work / "scan.png")
    pdf("scan.pdf", [PdfImage(str(work / "scan.png"), width=480, height=280)])
    shutil.copyfile(
        ROOT / "tests/fixtures/retrieval-review/01-allotment-seasonal-planting-guide.docx", work / "office.docx"
    )
    probes = {
        "nested-report": ["Chapter 2 Salinity and vegetation", "nineteen parts per thousand", "Figure 1"],
        "office": ["Early spring", "Summer succession", "20 March"],
        "scan": ["MARSH-42", "No replacement values were invented"],
        "paper": ["six paired plots", "nineteen parts per thousand"],
        "play": ["Act II", "fund another year of monitoring"],
        "opinion": ["Dissent", "identify missing intervals"],
    }
    save(
        work / "manifest.json",
        {
            "documents": [
                {
                    "label": label,
                    "path": label + (".docx" if label == "office" else ".pdf"),
                    "probes": values,
                    "selections": SELECTIONS.get(label, []),
                }
                for label, values in probes.items()
            ]
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    build(private_directory(args.work_dir))
    print("Built six synthetic extraction cases; content remains private.")


if __name__ == "__main__":
    main()
