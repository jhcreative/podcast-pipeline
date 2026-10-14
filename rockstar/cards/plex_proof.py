"""
CARD-05 proof sheet: the quote cards set in IBM Plex, before the format is locked.

John picks one setting from this sheet (ADR-0015); only then is the type fixed and the
renderer rebuilt around it. This is a proof, not the renderer: slides split greedily
at sentence, then clause, boundaries, measured in Chromium, which is enough to judge
size, centring and rhythm. The production split (rebalancing, no orphans, the verbatim
check) stays render_cards.Fitter's.

    python -m rockstar.cards.plex_proof        # -> quote_cards/_proof/plex/ + plex_proof_sheet.png

What is being proofed (CARD-05):
  - IBM Plex Sans for every slide face, vendored from BrandAssets/fonts/ibm-plex/ as
    @font-face data URIs (never Google Fonts);
  - the quote vertically centred between the top margin and the fixed citation;
  - three sizes, A / B / C, on one 8px spacing scale;
  - the footer lifted 20px over a purple band, as on the covers;
  - the cover's name and title in IBM Plex Sans Condensed.
"""

import base64
import io
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from rockstar import config
from rockstar.cards import card_format as fmt
from rockstar.cards import cover_format as cf
from rockstar.cards import (
    cover_image,  # noqa: E402
    plex_fonts,  # noqa: E402
    quote_source,  # noqa: E402
)
from rockstar.cards.render_cards import _typographic

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = str(config.CODE_ROOT)


OUT = os.path.join(HERE, "_proof", "plex")

# The faces come from plex_fonts, the module the renderer uses (ADR-0015).
FONTS = plex_fonts.CSS
SANS, COND = plex_fonts.SANS, plex_fonts.COND


# One spacing scale, base 8: 24 / 40 / 64 / 104.
MARGIN = 104  # sides and top, as the cards today
FOOTER_PAD = 20  # the cover's lift (ADR-0014), now on slides too
PANEL_TOP = fmt.PANEL_TOP - FOOTER_PAD  # 900
CITE_GAP = 40  # citation sits this far above the footer
QUOTE_CITE_GAP = 64  # the quote zone ends this far above the citation

SETTINGS = {  # quote size / line-height; citation 24, tracking 0
    "A": dict(px=46, line=1.45),
    "B": dict(px=52, line=1.42),
    "C": dict(px=58, line=1.38),
}


def _png_uri(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _background():
    """background.png's light field above, its own footer strip lifted 20px, purple band below."""
    bg = Image.open(os.path.join(HERE, "background.png")).convert("RGB")
    card = Image.new("RGB", (fmt.CANVAS, fmt.CANVAS), cf.FIELD)
    card.paste(bg.crop((0, 0, fmt.CANVAS, PANEL_TOP)), (0, 0))
    card.paste(bg.crop((0, fmt.PANEL_TOP, fmt.CANVAS, fmt.CANVAS)), (0, PANEL_TOP))
    card.paste(cf.FIELD, (0, PANEL_TOP + fmt.PANEL_H, fmt.CANVAS, fmt.CANVAS))
    return _png_uri(card)


BG = _background()

SLIDE = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{fonts}
* {{ margin:0; padding:0; box-sizing:border-box; }}
.card {{ position:relative; width:1080px; height:1080px; background:url("{bg}") 0 0/1080px 1080px; }}
.zone {{ position:absolute; left:{m}px; right:{m}px; top:{m}px; height:{zone_h}px;
         display:flex; flex-direction:column; justify-content:center; z-index:0; }}
.quote {{ position:relative; font-family:{sans}; font-weight:400; font-size:{px}px;
          line-height:{line}; color:{ink}; text-wrap:pretty; }}
.mark {{ position:absolute; left:-64px; top:-96px; font-family:{sans}; font-weight:700;
         font-size:300px; line-height:1; color:{ink}; opacity:0.16; z-index:-1; }}
.quote .txt {{ position:relative; }}
.cite {{ position:absolute; right:{m}px; bottom:{cite_bottom}px; font-family:{sans};
         font-weight:600; font-size:24px; line-height:1.25; letter-spacing:0.01em; color:{ink}; }}
.panel {{ position:absolute; left:{logo_right}px; top:{panel_top}px; width:{panel_w}px;
          height:{panel_h}px; display:flex; align-items:center; justify-content:flex-end;
          padding:14px 44px 14px 24px; }}
.ep {{ font-family:{sans}; font-weight:500; font-size:24px; line-height:1.3;
       letter-spacing:0.02em; color:#fff; text-align:right; }}
</style></head><body><div class="card">
<div class="zone"><p class="quote"><span class="mark">“</span><span class="txt">{quote}</span></p></div>
<p class="cite">{cite}</p>
<div class="panel"><span class="ep">{ep}</span></div>
</div></body></html>"""


def slide_html(setting, quote, cite, ep):
    s = SETTINGS[setting]
    cite_bottom = fmt.CANVAS - PANEL_TOP + CITE_GAP
    cite_top = fmt.CANVAS - cite_bottom - 30
    return SLIDE.format(
        fonts=FONTS,
        bg=BG,
        m=MARGIN,
        zone_h=cite_top - QUOTE_CITE_GAP - MARGIN,
        sans=SANS,
        px=s["px"],
        line=s["line"],
        ink=fmt.INK,
        cite_bottom=cite_bottom,
        logo_right=fmt.LOGO_RIGHT,
        panel_top=PANEL_TOP,
        panel_w=fmt.CANVAS - fmt.LOGO_RIGHT,
        panel_h=fmt.PANEL_H,
        quote=quote,
        cite=cite,
        ep=ep,
    )


def fits(page, setting, text, cite, ep):
    page.set_content(slide_html(setting, text, cite, ep))
    plex_fonts.load(page)
    return page.evaluate("""() => { const z = document.querySelector('.zone');
        const q = document.querySelector('.quote'); return q.offsetHeight <= z.clientHeight; }""")


def split(page, setting, text, cite, ep):
    units = re.split(r"(?<=[.!?…])\s+", text.strip())
    out, cur = [], []
    for u in units:
        trial = " ".join(cur + [u])
        if not cur or fits(page, setting, trial, cite, ep):
            cur.append(u)
        else:
            out.append(" ".join(cur))
            cur = [u]
    out.append(" ".join(cur))
    return out


def fontcheck(page):
    return plex_fonts.fontcheck(page)


def cover(page, ep_dir, ep_num, q, speakers, title, ep_line):
    """The ADR-0014 cover with its faces swapped to Plex Sans Condensed / Plex Sans."""
    image, _ = cover_image.pick_image(q["speaker"], os.listdir(ep_dir), speakers)
    path = image if os.path.isabs(image) else os.path.join(ep_dir, image)
    photo, _ = cover_image.photo_data_uri(path, cover_image.kind_of(path))
    bg = Image.open(os.path.join(HERE, "background.png")).convert("RGB")
    strip = bg.crop((0, fmt.PANEL_TOP, fmt.CANVAS, fmt.CANVAS))
    strip.paste(cf.FIELD, (0, 0, fmt.CANVAS, 3))
    strip.paste(cf.FIELD, (0, 0, 3, fmt.PANEL_H))
    html = cf.build_cover_html(
        photo, _png_uri(strip), cover_image.display_name(q["speaker"]), _typographic(title), _typographic(ep_line)
    )
    html = (
        html.replace(cf.NAME_FACE, COND)
        .replace(cf.TITLE_FACE, COND)
        .replace(fmt.PANEL_FACE, SANS)
        .replace("<style>", "<style>" + FONTS, 1)
    )
    page.set_content(html)
    plex_fonts.load(page)
    w = page.evaluate("""() => [document.querySelector('.name').scrollWidth,
                                document.querySelector('.namebox').clientWidth]""")
    p = os.path.join(OUT, f"cover_E{ep_num}_{q['id']}.png")
    page.locator(".card").screenshot(path=p)
    return p, w


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))
    e29 = quote_source.find_episode_dir(REPO, 29)
    qs29 = quote_source.load_quotes(e29)
    short, long_ = min(qs29, key=lambda q: len(q["text"])), max(qs29, key=lambda q: len(q["text"]))
    picks = [("E29", short), ("E29", long_)]
    ep_line = "E29 · Don’t Wait for the Stop | Optimize Hormones Before Perimenopause"
    rows = {}
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page(viewport={"width": 1080, "height": 1080})
        print("Font resolution:")
        if not fontcheck(page):
            sys.exit("Plex did not resolve -- refusing to proof in a fallback face")
        for setting in SETTINGS:
            row = []
            for tag, q in picks:
                text = _typographic(q["text"])
                parts = split(page, setting, text, q["speaker"], ep_line)
                for i, part in enumerate(parts, 1):
                    line = ep_line + (f" · {i}/{len(parts)}" if len(parts) > 1 else "")
                    page.set_content(slide_html(setting, part, q["speaker"], line))
                    plex_fonts.load(page)
                    p = os.path.join(OUT, f"{setting}_{tag}_{q['id']}_{i}of{len(parts)}.png")
                    page.locator(".card").screenshot(path=p)
                    row.append(p)
                print(
                    f"  {setting} {SETTINGS[setting]['px']}px  {tag} {q['id']} "
                    f"{len(q['text'])} ch -> {len(parts)} slide(s)"
                )
            rows[setting] = row
        speakers = [q["speaker"] for q in qs29]
        titles = __import__("cover_titles").load(e29)["titles"]
        cov, w = cover(page, e29, 29, qs29[0], speakers, titles[qs29[0]["id"]], ep_line)
        print(f"  cover name {w[0]}px in a {w[1]}px box")
        e28 = os.path.join(REPO, "Episodes", "_new_cards", "E28-everett")
        rows["covers"] = [cov]
        if os.path.isdir(e28):
            q28 = quote_source.load_quotes(e28)
            t28 = __import__("cover_titles").load(e28)["titles"]
            host = next(q for q in q28 if cover_image.is_host(q["speaker"]))
            c2, w2 = cover(
                page,
                e28,
                28,
                host,
                [q["speaker"] for q in q28],
                t28[host["id"]],
                "E28 · No Stone Unturned | Bone Density at Every Age · ft. Everett Calloran",
            )
            rows["covers"].append(c2)
            print(f"  cover (host) name {w2[0]}px in a {w2[1]}px box")
    avenir = sorted(
        os.path.join(e29, "cards", f)
        for f in os.listdir(os.path.join(e29, "cards"))
        if re.match(rf"E29_({short['id']}|{long_['id']})_\d+of\d+\.png$", f)
    )
    rows = {"today (Avenir)": avenir, **rows}
    t, label = 300, 230
    width = label + t * max(len(r) for r in rows.values())
    sheet = Image.new("RGB", (width, t * len(rows)), "white")
    d = ImageDraw.Draw(sheet)
    try:
        lf = ImageFont.truetype("/System/Library/Fonts/Avenir Next.ttc", 22)
    except OSError:
        lf = ImageFont.load_default()
    for r, (name, paths) in enumerate(rows.items()):
        caption = name if name not in SETTINGS else f"{name}  {SETTINGS[name]['px']}px"
        d.text((12, r * t + t // 2 - 12), caption, fill="#291c3b", font=lf)
        for c, p in enumerate(paths):
            sheet.paste(Image.open(p).convert("RGB").resize((t, t)), (label + c * t, r * t))
    out = os.path.join(OUT, "plex_proof_sheet.png")
    sheet.save(out)
    print(f"\nsheet -> {out}")


if __name__ == "__main__":
    main()
