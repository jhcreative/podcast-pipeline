"""
CARD-04: render the cover slide (slide 1) of each of an episode's five quote carousels.

Set in vendored IBM Plex (ADR-0015), so it renders the same on the Mac and on the box.

    python -m rockstar.cards.render_covers --episode 29 --title "Don't Wait for the Stop | ..."
    python -m rockstar.cards.render_covers --episode 30 --title "..." --dir /tmp/stage/E30-graham

render_cards.py --episode renders the covers too (after the slides), so a new
episode needs no separate run; this entry point is for the backfill and re-renders.

Writes Episodes/E<N>-*/cards/E<N>_<qid>_0cover.png beside the quote slides. "0cover"
sorts before "1ofN", so Drive lists the cover first and the carousel opens on it.

Inputs, all from the episode folder:
  quotes.json        the five quotes (speaker + text)
  cover_titles.json  the gated carousel titles (make_cover_titles.py drafts them)
  *-tile.* / *-reel.* the speaker's image (cover_image.pick_image)

Gates, all hard failures: the condensed faces resolved; the title fits the column in
at most TITLE_MAX_LINES lines at its fixed size; the name fits beside the photo; the episode
line fits the panel; the PNG proves the photo, the field and the footer painted.
"""

import argparse
import base64
import io
import os
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

from rockstar import config
from rockstar.cards import card_format as fmt
from rockstar.cards import cover_format as cf
from rockstar.cards import (
    cover_image,  # noqa: E402
    cover_titles,  # noqa: E402
    plex_fonts,  # noqa: E402
    quote_source,  # noqa: E402
)
from rockstar.cards.render_cards import _episode_line, _typographic, fontcheck

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = str(config.CODE_ROOT)


def _footer_uri():
    """The cards' own footer: the bottom PANEL_H px of background.png."""
    bg = Image.open(os.path.join(HERE, "background.png")).convert("RGB")
    strip = bg.crop((0, fmt.PANEL_TOP, fmt.CANVAS, fmt.CANVAS))
    # Its top 3 rows (and left 3 columns) are the anti-aliased edge against the slides' light field; on the
    # cover's purple field they draw a seam. Paint them field purple.
    strip.paste(cf.FIELD, (0, 0, fmt.CANVAS, 3))
    strip.paste(cf.FIELD, (0, 0, 3, fmt.PANEL_H))  # and the same edge down its left side
    buf = io.BytesIO()
    strip.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


FOOTER = _footer_uri()


def cover_fontcheck(page):
    """The condensed cut must resolve, or the name and title set in a fallback face.
    Every Plex face is proven together (plex_fonts.fontcheck)."""
    return plex_fonts.fontcheck(page)


def _assert_painted(path):
    """Sample the PNG: the photo is not brand purple, the text column and footer are.
    A photo that fails to load leaves a purple card that looks deliberate."""
    px = Image.open(path).convert("RGB").load()

    def purple(p):
        return p[0] < 90 and p[2] < 110 and p[2] > p[1]

    if not purple(px[60, 60]):
        raise SystemExit(f"FAIL {os.path.basename(path)}: field not painted ({px[60, 60]})")
    if not purple(px[20, cf.PANEL_TOP + 20]):
        raise SystemExit(f"FAIL {os.path.basename(path)}: footer not painted")
    if not purple(px[fmt.CANVAS // 2, fmt.CANVAS - cf.FOOTER_PAD // 2]):
        raise SystemExit(f"FAIL {os.path.basename(path)}: bottom padding not painted")
    samples = [
        px[x, y] for x in range(cf.PHOTO_LEFT + 30, fmt.CANVAS, 60) for y in range(cf.TOP_PAD + 30, cf.PHOTO_BOTTOM, 60)
    ]
    if sum(1 for p in samples if purple(p)) > len(samples) * 0.5:
        raise SystemExit(f"FAIL {os.path.basename(path)}: photo did not paint (the photo area is mostly brand purple)")


def render_cover(page, ep_dir, ep_num, quote, speakers, title, ep_title, out_dir, names=None):
    """Render one cover. Returns (path, image_rule)."""
    names = names if names is not None else os.listdir(ep_dir)
    image, rule = cover_image.pick_image(quote["speaker"], names, speakers)
    image_path = image if os.path.isabs(image) else os.path.join(ep_dir, image)
    photo, _ = cover_image.photo_data_uri(image_path, cover_image.kind_of(image_path))
    name = cover_image.display_name(quote["speaker"])
    html = cf.build_cover_html(
        photo, FOOTER, name, _typographic(title), _typographic(_episode_line(ep_num, ep_title, 1, 1))
    )
    page.set_content(html)
    plex_fonts.load(page)
    page.wait_for_load_state("load")
    m = page.evaluate(
        """() => {
          const t = document.querySelector('.title'), n = document.querySelector('.name');
          const tb = document.querySelector('.titlebox'), nb = document.querySelector('.namebox');
          const e = document.querySelector('.ep');
          const p = document.querySelector('.panel'), ps = getComputedStyle(p);
          const lh = parseFloat(getComputedStyle(t).lineHeight);
          const nlh = parseFloat(getComputedStyle(n).lineHeight);
          return {titleLines: Math.round(t.scrollHeight / lh), titleW: t.scrollWidth,
                  titleBoxW: tb.clientWidth, titleH: t.scrollHeight, titleBoxH: tb.clientHeight,
                  nameLines: Math.round(n.scrollHeight / nlh), nameW: n.scrollWidth,
                  nameBoxW: nb.clientWidth, nameH: nb.scrollHeight, nameBoxH: nb.clientHeight,
                  panelH: e.scrollHeight,
                  panelAvail: p.clientHeight - parseFloat(ps.paddingTop) - parseFloat(ps.paddingBottom)};
        }"""
    )
    tag = f"E{ep_num}_{quote['id']} cover"
    if m["titleLines"] > cf.TITLE_MAX_LINES or m["titleW"] > m["titleBoxW"] or m["titleH"] > m["titleBoxH"]:
        raise SystemExit(
            f"FAIL {tag}: title needs {m['titleLines']} lines "
            f"(max {cf.TITLE_MAX_LINES}) or overruns its box -- shorten "
            f"it in cover_titles.json; the type does not shrink"
        )
    if m["nameW"] > m["nameBoxW"] or m["nameH"] > m["nameBoxH"]:
        raise SystemExit(
            f"FAIL {tag}: speaker name {name!r} is {m['nameW']}px wide in a {m['nameBoxW']}px box beside the photo"
        )
    if m["panelH"] > m["panelAvail"]:
        raise SystemExit(f"FAIL {tag}: episode line too long for the footer -- shorten --title")
    path = os.path.join(out_dir, f"E{ep_num}_{quote['id']}_0cover.png")
    page.locator(".card").screenshot(path=path)
    _assert_painted(path)
    return path, rule


def render_episode_covers(page, ep_num, ep_title, ep_dir=None, out_dir=None):
    ep_dir = ep_dir or quote_source.find_episode_dir(REPO, ep_num)
    quotes = quote_source.load_quotes(ep_dir)
    titles = cover_titles.titles_for(ep_dir, ep_num, quotes)
    out_dir = out_dir or os.path.join(ep_dir, "cards")
    os.makedirs(out_dir, exist_ok=True)
    speakers = [q["speaker"] for q in quotes]
    done = []
    for q in quotes:
        path, rule = render_cover(page, ep_dir, ep_num, q, speakers, titles[q["id"]], ep_title, out_dir)
        print(f"  {q['id']}  {rule:13s} {titles[q['id']]!r}")
        done.append(path)
    return done


def open_page(pw):
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": fmt.CANVAS, "height": fmt.CANVAS}, device_scale_factor=1)
    page.set_content("<html><body></body></html>")
    print("Font resolution:")
    if not (fontcheck(page) and cover_fontcheck(page)):
        sys.exit("fonts did not resolve -- refusing to render off-brand covers")
    return page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--title", required=True, help="published episode title for the footer")
    ap.add_argument(
        "--dir", help="episode folder (default: Episodes/E<N>-*); the backfill points this at a staged Drive copy"
    )
    ap.add_argument("--out", help="write here instead of the episode's cards/")
    a = ap.parse_args()
    with sync_playwright() as pw:
        page = open_page(pw)
        paths = render_episode_covers(page, a.episode, a.title, ep_dir=a.dir, out_dir=a.out)
        print(f"\n{len(paths)} cover(s) -> {os.path.dirname(paths[0])}")


if __name__ == "__main__":
    main()
