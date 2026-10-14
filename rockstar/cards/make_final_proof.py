"""
Build the final proof pack for the locked quote-card format.

Everything here is regenerated from the canon -- nothing is hand-assembled -- so
re-running it after a format change produces an honest new proof.

    python -m rockstar.cards.make_final_proof

Writes quote_cards/_proof/final/:
    01_spec_annotated.png   the geometry, drawn on a real card
    02_contact_E29.png      all 10 slides of a real episode, in post order
    03_stress_longtitle.png a 60-character published title in the footer
    <the individual PNGs>
    README.md
"""

import glob
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

from rockstar import config
from rockstar.cards import card_format as fmt

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = str(config.CODE_ROOT)
OUT = os.path.join(HERE, "_proof", "final")
CARDS = os.path.join(REPO, "Episodes", "E29-corinne", "cards")
# The real published title, read from the server's publication_schedule.json
# (2026-07-27). An earlier proof carried a reconstruction.
E29_TITLE = "Don’t Wait for the Stop | Optimize Hormones Before Perimenopause"
E30_TITLE = "Feed Your Head | Gut Microbiome, Hormones & Midlife Vitality"

GUIDE = (198, 32, 96)  # annotation colour, deliberately not a brand colour


def _font(size, face="Avenir Next.ttc"):
    path = f"/System/Library/Fonts/Supplemental/{face}"
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def annotate(src, dst):
    """Draw the format's fixed geometry over a real rendered card.

    Callouts are numbered on the card and described in a legend beside it, rather
    than labelled in place -- an annotation that covers the thing it points at is
    worse than no annotation."""
    card = Image.open(src).convert("RGB")
    LEG = 720
    im = Image.new("RGB", (fmt.CANVAS + LEG, fmt.CANVAS), "white")
    im.paste(card, (0, 0))
    d = ImageDraw.Draw(im)
    f = _font(19)
    s = fmt.SETTINGS[fmt.CANON]
    m = s["margin"]
    cite_y = fmt.cite_top()  # ADR-0015: the citation's fixed top
    ztop, zh = fmt.zone(s)  # the quote is centred in this zone

    def dot(n, x, y):
        d.ellipse([x - 17, y - 17, x + 17, y + 17], fill=GUIDE)
        w = d.textlength(str(n), font=f)
        d.text((x - w / 2, y - 11), str(n), fill=(255, 255, 255), font=f)

    ft = fmt.FOOTER_TOP
    d.rectangle([m, ztop, fmt.CANVAS - m, ztop + zh], outline=GUIDE, width=2)
    d.line([(0, cite_y), (fmt.CANVAS, cite_y)], fill=GUIDE, width=2)
    d.line([(0, ft), (fmt.CANVAS, ft)], fill=GUIDE, width=2)
    d.line([(0, ft + fmt.PANEL_H), (fmt.CANVAS, ft + fmt.PANEL_H)], fill=GUIDE, width=2)
    d.line([(fmt.LOGO_RIGHT, ft), (fmt.LOGO_RIGHT, ft + fmt.PANEL_H)], fill=GUIDE, width=2)

    legend = [
        (
            1,
            (fmt.CANVAS - m - 40, ztop),
            f"Quote zone \u2014 y {ztop}..{ztop + zh}; the quote is centred in it",
            f"IBM Plex Sans Regular {s['quote_px']}px / lh {s['line']} / #291c3b",
        ),
        (2, (m, 700), f"Margin \u2014 {m}px, both sides of the type field", ""),
        (
            3,
            (m + 60, cite_y),
            f"Citation \u2014 fixed, {fmt.CITE_BOTTOM}px above the footer, right-justified",
            "IBM Plex Sans SemiBold 24px / #291c3b \u00b7 on every slide",
        ),
        (
            4,
            (fmt.CANVAS - m - 40, ft),
            f"Footer \u2014 {fmt.PANEL_H}px, lifted {fmt.FOOTER_PAD}px over a purple band",
            "Episode line: IBM Plex Sans Medium 24px / #ffffff, right-aligned, max two lines",
        ),
        (
            5,
            (fmt.LOGO_RIGHT, ft + fmt.PANEL_H - 24),
            f"Logo lockup ends at {fmt.LOGO_RIGHT}px \u2014 free panel space begins here",
            "",
        ),
        (
            6,
            (m + 150, ztop + 40),
            f"Opening mark \u2014 {fmt.MARK_SIZE_WATERMARK}px at {int(fmt.MARK_OPACITY_WATERMARK * 100)}% opacity",
            "Travels with the quote, behind its first lines \u00b7 costs no capacity",
        ),
    ]
    x0, y = fmt.CANVAS + 34, 58
    d.text((x0, 22), "Fixed geometry \u2014 every measurement is canon", fill=(20, 20, 20), font=_font(22))
    for n, (cx, cy), line1, line2 in legend:
        dot(n, cx, cy)
        d.ellipse([x0, y, x0 + 30, y + 30], fill=GUIDE)
        w = d.textlength(str(n), font=f)
        d.text((x0 + 15 - w / 2, y + 5), str(n), fill=(255, 255, 255), font=f)
        d.text((x0 + 48, y + 3), line1, fill=(20, 20, 20), font=f)
        if line2:
            d.text((x0 + 48, y + 29), line2, fill=(110, 110, 110), font=_font(17))
        y += 78 if line2 else 56
    d.text((x0, y + 20), "Slide count is the only variable \u2014 type never changes size.", fill=GUIDE, font=_font(20))
    im.save(dst)


def contact(paths, dst, thumb=470, cols=5, title=""):
    f = _font(18)
    rows = (len(paths) + cols - 1) // cols
    pad, lbl, head = 18, 26, 44 if title else 0
    W = pad + cols * (thumb + pad)
    H = head + pad + rows * (thumb + pad + lbl)
    sheet = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(sheet)
    if title:
        d.text((pad, 14), title, fill=(20, 20, 20), font=_font(22))
    for i, p in enumerate(paths):
        r, c = divmod(i, cols)
        x = pad + c * (thumb + pad)
        y = head + pad + r * (thumb + pad + lbl)
        d.text((x, y), os.path.basename(p), fill=(60, 60, 60), font=f)
        sheet.paste(Image.open(p).resize((thumb, thumb), Image.LANCZOS), (x, y + lbl - 4))
    sheet.save(dst)


def main():
    if not glob.glob(os.path.join(CARDS, "E29_*.png")):
        sys.exit(
            f"no rendered cards in {CARDS} -- run:\n"
            f'  python -m rockstar.cards.render_cards --episode 29 --title "{E29_TITLE}"'
        )
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    cards = sorted(glob.glob(os.path.join(CARDS, "E29_*.png")))
    for p in cards:
        shutil.copy(p, OUT)

    first = sorted(glob.glob(os.path.join(OUT, "E29_q01_1of*.png")))[0]  # count varies with type
    annotate(first, os.path.join(OUT, "01_spec_annotated.png"))
    contact(
        cards,
        os.path.join(OUT, "02_contact_E29.png"),
        title=f"E29 — five posts, {len(cards)} PNGs, canon format (post order left to right, slides in sequence)",
    )

    # Stress case: the longest real published title we have, in the footer.
    subprocess.run(
        [sys.executable, "-m", "rockstar.cards.render_cards", "--episode", "29", "--title", E30_TITLE],
        cwd=REPO,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    stress = sorted(glob.glob(os.path.join(CARDS, "E29_q02_*.png")))
    contact(
        stress,
        os.path.join(OUT, "03_stress_longtitle.png"),
        cols=3,
        title=f"Footer stress test — {len(E30_TITLE)}-character published title (wraps to two right-aligned lines)",
    )
    # Restore the episode's own cards so the folder is not left holding the stress run.
    subprocess.run(
        [sys.executable, "-m", "rockstar.cards.render_cards", "--episode", "29", "--title", E29_TITLE],
        cwd=REPO,
        check=True,
        stdout=subprocess.DEVNULL,
    )

    open(os.path.join(OUT, "README.md"), "w").write(README)
    print(f"final proof -> {OUT}")
    for f in sorted(os.listdir(OUT)):
        print("  ", f)


README = """# Quote cards — final proof

Everything below is rendered by `render_cards.py` at canon settings (ADR-0012,
`QUOTE-CARD-FORMAT.md`). Regenerate with `python -m rockstar.cards.make_final_proof`.

| File | What it shows |
|---|---|
| `01_spec_annotated.png` | Every fixed measurement drawn on a real card |
| `02_contact_E29.png` | A whole episode: 5 posts, 10 slides, in post order |
| `03_stress_longtitle.png` | A 60-character published title in the footer |
| `E29_q*.png` | The individual 1080x1080 slides, as delivered |

## What to expect per episode

Five posts, one per selected quote. Each post is 1-N slides depending only on the
length of the quote; type never changes size. E29's five quotes (291-599 characters)
produced 2 / 3 / 2 / 2 / 1 slides = **10 images**.

Filenames are `E{N}_q{id}_{i}of{n}.png` — post identity and slide order are in
the name, so they upload and schedule in the right sequence.

## The command

    python -m rockstar.cards.render_cards --episode 31 --title "Hook | Topic"

`--title` is the **published** title (ADR-0009: apple_summary wins over the schedule).
The render fails loudly rather than shrinking type if that title cannot fit the footer
in two lines.

## What fails the render, on purpose

- A quote that will not fit at canon type — the fix is another slide, never a smaller size
- A published title too long for the panel — the fix is a shorter title
- Either Apple face failing to resolve — a Helvetica fallback is refused, not rendered
- The brand background failing to paint — every finished PNG is pixel-sampled
"""


if __name__ == "__main__":
    main()
