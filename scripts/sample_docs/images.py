# ruff: noqa: E501
"""Sample images, drawn with Pillow. Every subject and figure here is invented. Text is large so OCR and vision both work."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 800
INK, PAPER = (30, 34, 44), (250, 248, 242)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Resolve font names through Pillow; no development-machine font paths.
    names = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def canvas(bg=PAPER, seed: int = 0) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Paper with faint grain, so files are not trivially compressible and look photographed rather than rendered."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H), bg)
    px = img.load()
    for _ in range(40000):
        x, y = rnd.randrange(W), rnd.randrange(H)
        d = rnd.randint(-9, 9)
        px[x, y] = tuple(max(0, min(255, c + d)) for c in bg)
    return img, ImageDraw.Draw(img)


def title(d, text, sub=None):
    d.text((60, 36), text, font=font(46, True), fill=INK)
    if sub:
        d.text((60, 96), sub, font=font(26), fill=(90, 96, 110))


def bar_chart():
    img, d = canvas(seed=1)
    title(d, "Monthly rainfall, Glen Orrin 2025", "Millimetres per month, station GO-3")
    data = [
        ("Jan", 142),
        ("Feb", 118),
        ("Mar", 96),
        ("Apr", 71),
        ("May", 64),
        ("Jun", 58),
        ("Jul", 77),
        ("Aug", 109),
        ("Sep", 131),
        ("Oct", 188),
        ("Nov", 204),
        ("Dec", 171),
    ]
    x0, _y0, base, scale = 110, 170, 700, 2.4
    for i in range(0, 241, 40):
        y = base - i * scale
        d.line([(x0, y), (1140, y)], fill=(210, 210, 205), width=1)
        d.text((60, y - 12), str(i), font=font(20), fill=(90, 96, 110))
    for i, (m, v) in enumerate(data):
        x = x0 + 20 + i * 84
        d.rectangle([x, base - v * scale, x + 56, base], fill=(46, 111, 175) if v != 204 else (200, 72, 52))
        d.text((x + 8, base + 8), m, font=font(24), fill=INK)
        d.text((x + 2, base - v * scale - 28), str(v), font=font(22, True), fill=INK)
    return img


def line_chart():
    img, d = canvas(seed=2)
    title(d, "Sourdough starter rise over 8 hours", "Height of starter in jar, fed at 8:00, kept at 24 C")
    pts = [(0, 0), (1, 6), (2, 14), (3, 25), (4, 39), (5, 52), (6, 61), (7, 58), (8, 49)]
    x0, base, sx, sy = 120, 700, 125, 8.4
    d.line([(x0, 170), (x0, base), (1130, base)], fill=INK, width=3)
    for h, _v in pts:
        d.text((x0 + h * sx - 8, base + 12), f"{h}h", font=font(22), fill=INK)
    xy = [(x0 + h * sx, base - v * sy) for h, v in pts]
    d.line(xy, fill=(46, 140, 90), width=6)
    for (x, y), (_, v) in zip(xy, pts, strict=True):
        d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=(46, 140, 90))
        d.text((x - 14, y - 38), str(v), font=font(22, True), fill=INK)
    d.text((x0 + 6 * sx - 150, base - 61 * sy - 90), "Peak: 61 mm at 6 hours", font=font(30, True), fill=(200, 72, 52))
    return img


def plot_map():
    img, d = canvas(seed=3)
    title(d, "Greywell Community Allotments", "Site plan: 12 plots, water standpipes marked with W")
    n = 0
    for r in range(3):
        for c in range(4):
            n += 1
            x, y = 90 + c * 260, 170 + r * 190
            fill = (190, 218, 170) if n not in (5, 8) else (232, 196, 120)
            d.rectangle([x, y, x + 220, y + 150], fill=fill, outline=INK, width=3)
            d.text((x + 12, y + 8), f"Plot {n}", font=font(30, True), fill=INK)
    d.text((100, 260), "W", font=font(34, True), fill=(46, 111, 175))
    d.text((1130, 640), "W", font=font(34, True), fill=(46, 111, 175))
    d.text(
        (60, 745),
        "Amber plots 5 and 8: waiting list. Shed and compost bins at the south gate.",
        font=font(26),
        fill=INK,
    )
    return img


def whiteboard():
    img, d = canvas(bg=(238, 241, 240), seed=4)
    title(d, "Sprint 14 retro", "Photo of whiteboard, Thursday")
    d.line([(600, 150), (600, 740)], fill=INK, width=5)
    d.text((140, 160), "WENT WELL", font=font(40, True), fill=(40, 120, 70))
    d.text((690, 160), "TO IMPROVE", font=font(40, True), fill=(190, 60, 50))
    for i, t in enumerate(["Demo to the council", "Pairing on the importer", "Zero hotfixes"]):
        d.rectangle([110, 240 + i * 120, 540, 330 + i * 120], fill=(255, 245, 150), outline=INK)
        d.text((125, 262 + i * 120), t, font=font(30), fill=INK)
    for i, t in enumerate(["Flaky staging deploys", "Standups run to 25 min", "Docs out of date"]):
        d.rectangle([660, 240 + i * 120, 1090, 330 + i * 120], fill=(255, 190, 200), outline=INK)
        d.text((675, 262 + i * 120), t, font=font(30), fill=INK)
    return img.rotate(1.5, fillcolor=(238, 241, 240)).filter(ImageFilter.GaussianBlur(0.6))


def receipt():
    img, d = canvas(bg=(252, 252, 250), seed=5)
    lines = [
        ("THE HARBOUR FISH BAR", 44, True),
        ("14 Quay Street, Eyemouth", 28, False),
        ("Tel 01890 555 0171", 28, False),
        ("", 20, False),
        ("Date: 08/08/2025   19:42", 30, False),
        ("", 14, False),
        ("1 x Haddock & chips        9.80", 32, False),
        ("1 x Mushy peas             1.60", 32, False),
        ("2 x Battered sausage      5.00", 32, False),
        ("1 x Irn-Bru can            1.40", 32, False),
        ("-" * 34, 28, False),
        ("TOTAL                    17.80", 38, True),
        ("Card payment  VISA ****4471", 28, False),
        ("", 14, False),
        ("Thank you - see you soon!", 32, False),
    ]
    y = 40
    for t, s, b in lines:
        d.text(
            (300, y),
            t,
            font=ImageFont.truetype(
                "/System/Library/Fonts/Supplemental/Courier New Bold.ttf"
                if b
                else "/System/Library/Fonts/Supplemental/Courier New.ttf",
                s,
            ),
            fill=(40, 40, 40),
        )
        y += s + 14
    return img.rotate(-2.2, fillcolor=(120, 112, 100), expand=False).filter(ImageFilter.GaussianBlur(0.7))


def poster():
    img, d = canvas(bg=(255, 244, 214), seed=6)
    d.rectangle([30, 30, W - 30, H - 30], outline=(200, 72, 52), width=10)
    d.text((300, 70), "LOST CAT", font=font(130, True), fill=(200, 72, 52))
    d.text((120, 250), "MARMALADE", font=font(90, True), fill=INK)
    d.text((120, 380), "Ginger and white, very fluffy, answers to a tin opener.", font=font(36), fill=INK)
    d.text((120, 440), "Last seen on Pelham Road near the chip shop on 3 May.", font=font(36), fill=INK)
    d.text((120, 520), "REWARD: 50 pounds", font=font(60, True), fill=(40, 120, 70))
    d.text((120, 640), "Call Freya on 07700 900 318", font=font(54, True), fill=INK)
    return img


def org_chart():
    img, d = canvas(seed=7)
    title(d, "Fennimore Theatre Company", "Who does what")

    def box(cx, y, t1, t2, col):
        d.rounded_rectangle([cx - 150, y, cx + 150, y + 100], 12, fill=col, outline=INK, width=3)
        d.text((cx - 138, y + 14), t1, font=font(26, True), fill=INK)
        d.text((cx - 138, y + 54), t2, font=font(24), fill=INK)

    box(600, 160, "Artistic Director", "Odile Marchetti", (200, 220, 245))
    for cx, t1, t2 in [
        (220, "Stage Manager", "Rhodri Penn"),
        (600, "Head of Wardrobe", "Aoife Delaney"),
        (980, "Box Office", "Nuru Mohamud"),
    ]:
        d.line([(600, 260), (600, 310), (cx, 310), (cx, 360)], fill=INK, width=3)
        box(cx, 360, t1, t2, (214, 235, 214))
    for cx, t1, t2 in [
        (220, "Lighting", "Ivar Strand"),
        (600, "Cutters (3)", "Team"),
        (980, "Volunteers", "40 ushers"),
    ]:
        d.line([(cx, 460), (cx, 540)], fill=INK, width=3)
        box(cx, 540, t1, t2, (250, 230, 200))
    return img


def pie_chart():
    img, d = canvas(seed=9)
    title(d, "Where the town council budget goes", "Total precept: 1.2 million pounds, 2025/26")
    parts = [
        ("Parks and play areas", 31, (60, 150, 90)),
        ("Street lighting", 22, (240, 190, 60)),
        ("Grants to clubs", 17, (70, 120, 190)),
        ("Admin", 15, (160, 160, 170)),
        ("Market hall repairs", 15, (200, 90, 70)),
    ]
    start = -90.0
    for _name, pct, col in parts:
        end = start + pct * 3.6
        d.pieslice([80, 170, 580, 670], start, end, fill=col, outline=PAPER, width=3)
        start = end
    for i, (name, pct, col) in enumerate(parts):
        d.rectangle([660, 230 + i * 80, 700, 270 + i * 80], fill=col)
        d.text((720, 230 + i * 80), f"{name}: {pct}%", font=font(34), fill=INK)
    return img


def trail_sign():
    img, d = canvas(bg=(120, 90, 60), seed=10)
    d.rectangle([60, 60, W - 60, H - 60], fill=(70, 120, 80), outline=(240, 235, 220), width=8)
    d.text((120, 100), "HERON LOOP", font=font(110, True), fill=(250, 248, 240))
    for i, t in enumerate(
        [
            "Reedbed hide ........ 1.2 km",
            "Otter bridge ....... 2.9 km",
            "Full circuit ...... 6.4 km",
            "Car park ........... 0.3 km",
        ]
    ):
        d.text((130, 280 + i * 100), t, font=font(52, True), fill=(250, 248, 240))
    d.text((130, 700), "Dogs on leads Mar to Jul", font=font(34), fill=(255, 220, 120))
    return img


def flowchart():
    img, d = canvas(seed=11)
    title(d, "Can I compost it?", "Decision tree")

    def box(x, y, t, col):
        d.rounded_rectangle([x, y, x + 300, y + 90], 14, fill=col, outline=INK, width=3)
        d.text((x + 14, y + 26), t, font=font(28, True), fill=INK)

    box(450, 150, "Is it plant-based?", (200, 220, 245))
    box(130, 340, "Is it cooked or oily?", (200, 220, 245))
    d.line([(450, 195), (280, 195), (280, 340)], fill=INK, width=3)
    d.text((300, 200), "yes", font=font(26), fill=INK)
    box(800, 340, "Landfill or hot bin", (250, 200, 200))
    d.line([(750, 195), (950, 195), (950, 340)], fill=INK, width=3)
    d.text((830, 200), "no", font=font(26), fill=INK)
    box(130, 540, "Green bin (collection)", (214, 235, 214))
    d.line([(280, 430), (280, 540)], fill=INK, width=3)
    d.text((300, 470), "yes", font=font(26), fill=INK)
    box(540, 540, "Heap: browns + greens", (214, 235, 214))
    d.line([(430, 385), (690, 385), (690, 540)], fill=INK, width=3)
    d.text((470, 350), "no", font=font(26), fill=INK)
    d.text((540, 660), "Aim for 2 parts brown to 1 part green", font=font(28), fill=INK)
    return img


def ferry_board():
    img, d = canvas(bg=(15, 25, 50), seed=12)
    d.text((60, 36), "INVERLORN FERRY - DEPARTURES", font=font(52, True), fill=(250, 214, 80))
    rows = [
        ("07:30", "Isle of Maren", "On time"),
        ("09:15", "Isle of Maren", "On time"),
        ("11:00", "Skarra", "Delayed 20 min"),
        ("13:45", "Isle of Maren", "On time"),
        ("16:20", "Skarra", "Cancelled - high winds"),
    ]
    for i, (t, dest, st) in enumerate(rows):
        y = 170 + i * 110
        d.text((70, y), t, font=font(60, True), fill=(250, 250, 250))
        d.text((300, y), dest, font=font(60), fill=(250, 250, 250))
        d.text(
            (760, y + 8),
            st,
            font=font(46, True),
            fill=(250, 100, 90) if ("Cancel" in st or "Delay" in st) else (110, 230, 140),
        )
    return img


def recipe_card():
    img, d = canvas(bg=(250, 240, 220), seed=8)
    d.rectangle([40, 40, W - 40, H - 40], outline=(150, 90, 60), width=6)
    d.text((100, 70), "Granny Morag's Shortbread", font=font(64, True), fill=(120, 60, 40))
    for i, t in enumerate(
        [
            "8 oz plain flour",
            "6 oz butter",
            "4 oz caster sugar",
            "Pinch of salt",
            "Bake at 150 C for 45 minutes",
            "Prick with a fork, dust with sugar while warm",
            "Cut into fingers after 5 minutes",
        ]
    ):
        d.text((110, 190 + i * 76), "- " + t, font=font(42), fill=INK)
    return img.rotate(-1.2, fillcolor=(190, 175, 150)).filter(ImageFilter.GaussianBlur(0.5))


# (slug with extension, builder, author, date, question, answer)
IMAGES = [
    (
        "01-rainfall-bar-chart.png",
        bar_chart,
        "Glen Orrin Weather Group",
        "2026-01-05",
        "Which month had the highest rainfall in Glen Orrin in 2025, and how many millimetres?",
        "November, 204 mm.",
    ),
    (
        "02-starter-rise-line-chart.png",
        line_chart,
        "Kitchen Science Club",
        "2025-04-12",
        "At what hour did the sourdough starter peak, and how tall was it?",
        "At 6 hours, 61 mm.",
    ),
    (
        "03-allotment-site-plan.png",
        plot_map,
        "Greywell Allotment Society",
        "2024-09-08",
        "Which allotment plots are on the waiting list?",
        "Plots 5 and 8 (amber).",
    ),
    (
        "04-retro-whiteboard.jpg",
        whiteboard,
        "Dev Team",
        "2025-11-20",
        "What did the team list under 'to improve' about standups?",
        "Standups run to 25 minutes.",
    ),
    (
        "05-fish-bar-receipt.jpg",
        receipt,
        "Scanned receipt",
        "2025-08-08",
        "What was the total on the Harbour Fish Bar receipt?",
        "17.80 (pounds).",
    ),
    (
        "06-lost-cat-poster.png",
        poster,
        "Freya Lindgren",
        "2026-05-04",
        "What is the lost cat's name and how much is the reward?",
        "Marmalade; 50 pounds.",
    ),
    (
        "07-theatre-org-chart.png",
        org_chart,
        "Fennimore Theatre",
        "2024-06-30",
        "Who is the Stage Manager of the Fennimore Theatre Company?",
        "Rhodri Penn.",
    ),
    (
        "08-shortbread-recipe-card.jpg",
        recipe_card,
        "Family archive",
        "2023-12-24",
        "At what temperature and for how long is Granny Morag's shortbread baked?",
        "150 C for 45 minutes.",
    ),
    (
        "09-council-budget-pie.bmp",
        pie_chart,
        "Town Clerk",
        "2025-04-01",
        "What share of the council budget goes to parks and play areas?",
        "31%.",
    ),
    (
        "10-heron-loop-trail-sign.gif",
        trail_sign,
        "Wetlands Trust",
        "2025-03-15",
        "How long is the full circuit of the Heron Loop?",
        "6.4 km.",
    ),
    (
        "11-compost-flowchart.webp",
        flowchart,
        "Green Streets Project",
        "2024-04-22",
        "What ratio of browns to greens does the compost flowchart recommend?",
        "2 parts brown to 1 part green.",
    ),
    (
        "12-ferry-departures-board.tiff",
        ferry_board,
        "Inverlorn Harbour",
        "2026-02-17",
        "Which ferry departure was cancelled and why?",
        "The 16:20 to Skarra, because of high winds.",
    ),
]


def save_all(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, fn, *_ in IMAGES:
        img = fn()
        kwargs = {"quality": 88} if name.endswith(".jpg") else {}
        if name.endswith(".gif"):
            img = img.quantize(256)
        img.save(out / name, **kwargs)
