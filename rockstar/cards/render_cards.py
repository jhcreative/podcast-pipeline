"""
Render square (1080x1080) Instagram quote cards from an episode's quotes.json.

Set in IBM Plex, vendored in BrandAssets/fonts/ibm-plex/ (ADR-0015), so the same renderer
runs on the Mac and on the box. (Until CARD-05 it was Mac-only: American Typewriter and
Avenir Next are macOS faces -- ADR-0012 §6, superseded.)

Three rules govern the output, the first two inherited from the report-writer
collection canon:

  1. Type size is fixed. A quote that does not fit gets ANOTHER SLIDE. Nothing is
     scaled, tracked-in, or clipped to fit. Slides are the variable; type is not.
  2. The renderer proves it. Every slide is measured in the browser after layout;
     content past its box is a hard failure, not a silent crop. This is REEL-05's
     lesson applied up front -- verify the edit, not just the file.
  3. The quote is vertically centred in a fixed zone; the citation and the lifted
     footer sit at the same y on every slide, so a swipe has steady elements
     (ADR-0015, superseding ADR-0012's fixed top anchor).

Usage:
    python -m rockstar.cards.render_cards --fontcheck
    python -m rockstar.cards.render_cards --proof                    # format proof, E29 quotes
    python -m rockstar.cards.render_cards --episode 31 --title "Hook | Topic"   # slides + covers (CARD-04)
    python -m rockstar.cards.render_cards --episode 31 --title "Hook | Topic" --upload

Requires: playwright + chromium  (pip install playwright && playwright install chromium)
--upload additionally requires Google credentials (GCP_SA_KEY_PATH), which the server
has and a bare Mac checkout does not.
"""

import argparse
import os
import re
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

from rockstar import config
from rockstar.cards import card_format as fmt
from rockstar.cards import cover_image, plex_fonts, quote_source
from rockstar.cards.card_upload import upload_cards  # noqa: F401  (re-exported for --upload)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = str(config.CODE_ROOT)
# The background travels as a data: URI, embedded in the CSS. Pages are loaded via
# set_content, whose origin is about:blank -- Chromium refuses file:// subresources
# from there, so a path (bare or file://) silently loads nothing and the card renders
# on white with no error. Embedding removes the origin question entirely.
_BG_PATH = os.path.join(HERE, "background.png")
BG = fmt.lifted_background(_BG_PATH)  # the template's footer lifted 20px (ADR-0015)

# Sentence boundary. Keeps the terminator with the sentence it ends.
_SENT = re.compile(r"(?<=[.!?…])\s+")
# Clause fallbacks, tried in order, only when one sentence alone overflows a slide.
_CLAUSE = [re.compile(r"(?<=—)\s+"), re.compile(r"(?<=[;:])\s+"), re.compile(r"(?<=,)\s+")]


def _typographic(text):
    """Straight quotes are a transcription artifact, not the speaker's words.
    Convert to typographic marks before layout -- on a typewriter face at 50px a
    straight apostrophe is conspicuous. Applied once, before splitting, so the
    verbatim check compares against the text that is actually rendered."""
    text = re.sub(r"(?<=\w)'(?=\w)", "’", text)  # don't, you're
    text = re.sub(r"(?<=\w)'(?=\s|$)", "’", text)  # plural possessive
    text = re.sub(r"(?<!\w)'", "‘", text)  # opening single
    out, open_dq = [], True
    for ch in text:
        if ch == '"':
            out.append("“" if open_dq else "”")
            open_dq = not open_dq
        else:
            out.append(ch)
    return "".join(out)


def _rehang_dash(parts):
    """A slide must not end on a dangling em dash. The dash belongs to the thought
    it introduces, so it moves to the head of the next slide. Concatenation is
    unchanged, so the verbatim invariant still holds."""
    out = []
    for p in parts:
        p = p.strip()
        if out and out[-1].endswith("—"):
            out[-1] = out[-1][:-1].rstrip()
            p = "— " + p
        out.append(p)
    return [p for p in out if p]


def _episode_line(ep_num, title, part, total):
    """Panel line: episode number and the episode's own title. The show name is
    deliberately absent -- the logo lockup two inches to the left already says it,
    and repeating it spends the panel's only free space on nothing."""
    base = f"E{ep_num} · {title}" if title else f"E{ep_num}"
    return f"{base} · {part}/{total}" if total > 1 else base


def _assert_chrome(path):
    """Prove the brand template actually painted. A background that fails to load
    produces a clean, plausible card on white -- no error anywhere. Sample the
    panel and the field instead of trusting that the URL resolved."""
    px = Image.open(path).convert("RGB").load()
    panel = px[20, fmt.FOOTER_TOP + 20]
    band = px[fmt.CANVAS // 2, fmt.CANVAS - fmt.FOOTER_PAD // 2]
    field = px[fmt.CANVAS - 20, 20]
    if not (band[0] < 90 and band[2] < 110 and band[2] > band[1]):
        raise SystemExit(f"FAIL {os.path.basename(path)}: band under the footer not painted (sampled {band})")
    if not (panel[0] < 90 and panel[2] < 110 and panel[2] > panel[1]):
        raise SystemExit(
            f"FAIL {os.path.basename(path)}: brand panel not painted (sampled {panel}, expected ~{fmt.INK})"
        )
    if field[0] < 150:
        raise SystemExit(f"FAIL {os.path.basename(path)}: field not painted (sampled {field})")


# What every fit decision reads: the quote's laid-out height against its centring
# zone (the zone ends QUOTE_CITE_GAP above the fixed citation), and the episode line
# against the footer panel.
_MEASURE_JS = """() => {
  const z = document.querySelector('.zone');
  const q = document.querySelector('.quote');
  const c = document.querySelector('.cite');
  // The citation never wraps; a name too long for the line must fail, not run off the card.
  const citeFits = !c || c.getBoundingClientRect().left >= z.getBoundingClientRect().left;
  const e = document.querySelector('.ep');
  const p = document.querySelector('.panel');
  const ps = getComputedStyle(p);
  const panelAvail = p.clientHeight - parseFloat(ps.paddingTop) - parseFloat(ps.paddingBottom);
  return {fits: q.offsetHeight <= z.clientHeight, citeFits, content: q.offsetHeight,
          avail: z.clientHeight, panelFits: e.scrollHeight <= panelAvail,
          panelH: e.scrollHeight, panelAvail};
}"""


class Fitter:
    """Measures real laid-out text in Chromium. The browser is the authority on
    what fits -- character counts are an estimate, and an estimate that is wrong
    by one line is a clipped quote."""

    def __init__(self, page, setting, citation, mark_mode):
        self.page = page
        self.setting = setting
        self.citation = citation
        self.mark_mode = mark_mode
        self._cache = {}  # rebalancing re-measures the same candidates often

    def _measure(self, text, show_mark=False):
        html = fmt.build_html(BG, self.setting, text, self.citation, "E00 · title", self.mark_mode, show_mark)
        self.page.set_content(html)
        plex_fonts.load(self.page)
        return self.page.evaluate(_MEASURE_JS)

    def _cached(self, text):
        if text not in self._cache:
            m = self._measure(text)
            # Fill = how much of the centring zone this text occupies.
            self._cache[text] = (m["fits"], m["content"] / max(1, m["avail"]))
        return self._cache[text]

    def fits(self, text):
        return self._cached(text)[0]

    def fill(self, text):
        return self._cached(text)[1]

    def split(self, text):
        """Pack at the largest natural boundary that fits, then rebalance. Returns
        a list of slide texts whose concatenation is the original quote, verbatim."""
        units = self._explode_overlong(_SENT.split(text.strip()))
        groups, cur = [], []
        for u in units:
            if not cur or self.fits(" ".join(cur + [u])):
                cur.append(u)
            else:
                groups.append(cur)
                cur = [u]
        if cur:
            groups.append(cur)
        return [" ".join(g) for g in self._rebalance(groups)]

    def _rebalance(self, groups):
        """Greedy packing fills each slide to the brim and leaves the remainder on
        the last one, which can orphan a single short sentence on its own slide --
        the card equivalent of an orphaned heading, which the collection canon
        forbids. Push units backward while every slide still fits and the emptiest
        slide gets fuller. Slide count never changes, so nothing is shrunk."""
        if len(groups) < 2:
            return groups
        moved = True
        while moved:
            moved = False
            for i in range(len(groups) - 1):
                if len(groups[i]) < 2:
                    continue
                head, tail = groups[i][:-1], [groups[i][-1]] + groups[i + 1]
                if not self.fits(" ".join(tail)):
                    continue
                before = min(self.fill(" ".join(groups[i])), self.fill(" ".join(groups[i + 1])))
                after = min(self.fill(" ".join(head)), self.fill(" ".join(tail)))
                if after > before:
                    groups[i], groups[i + 1] = head, tail
                    moved = True
        return groups

    def _explode_overlong(self, units):
        """Any single unit that cannot fit alone is broken further -- at clause
        boundaries first, and only as a last resort between words. A quote is
        never truncated to make it fit."""
        out = []
        for u in units:
            if self.fits(u):
                out.append(u)
                continue
            parts = [u]
            for rx in _CLAUSE:
                parts = _rehang_dash([p for part in parts for p in rx.split(part)])
                if all(self.fits(p) for p in parts):
                    break
            if not all(self.fits(p) for p in parts):
                parts = self._wordwrap(u)
            out.extend(parts)
        return out

    def _wordwrap(self, text):
        words, parts, cur = text.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if not cur or self.fits(trial):
                cur = trial
            else:
                parts.append(cur)
                cur = w
        if cur:
            parts.append(cur)
        return parts


def fontcheck(page):
    """Prove every IBM Plex face resolved (plex_fonts.fontcheck). A face that falls
    back to Helvetica produces a card that is subtly off-brand -- and mis-measured."""
    return plex_fonts.fontcheck(page)


def render_slides(page, setting, quote, ep_num, title, out_dir, prefix, mark_mode="watermark"):
    """Split one quote into slides and write a PNG per slide. Returns paths."""
    text = _typographic(quote["text"].strip())
    # The host's name is canonical, however the transcript spelled it (E28: "Heather
    # Riley-Hemstra"); a guest's is the transcript's, reconciled by BIO-02.
    cite = cover_image.display_name(quote["speaker"])
    f = Fitter(page, setting, cite, mark_mode)
    slides = f.split(text)
    # Verbatim invariant: what we render is exactly what the brand team selected.
    joined = " ".join(s.strip() for s in slides)
    if re.sub(r"\s+", " ", joined) != re.sub(r"\s+", " ", text):
        raise SystemExit(f"FAIL {prefix}: split altered the quote text")
    paths = []
    for i, s in enumerate(slides, 1):
        # The watermark is brand furniture and sits on every slide. The punctuation
        # placements are an *opening* mark, so they appear on the first slide only.
        show_mark = (mark_mode == "watermark") or (i == 1)
        ep_line = _typographic(_episode_line(ep_num, title, i, len(slides)))  # as the cover's
        html = fmt.build_html(BG, setting, s, cite, ep_line, mark_mode, show_mark)
        page.set_content(html)
        plex_fonts.load(page)
        m = page.evaluate(_MEASURE_JS)
        if not m["citeFits"]:
            raise SystemExit(
                f"FAIL {prefix} slide {i}: citation {cite!r} is wider than the "
                f"type field -- the speaker name needs shortening in quotes.json"
            )
        if not m["fits"]:
            raise SystemExit(
                f"FAIL {prefix} slide {i}: quote needs {m['content']}px in a "
                f"{m['avail']}px zone -- add a slide, do not shrink"
            )
        if m["panelH"] > m["panelAvail"]:
            raise SystemExit(
                f"FAIL {prefix} slide {i}: episode line needs "
                f"{m['panelH']}px in a {m['panelAvail']}px panel -- the "
                f"title is too long for the footer. Shorten the title "
                f"passed to --title; do not shrink the panel type."
            )
        p = os.path.join(out_dir, f"{prefix}_{i}of{len(slides)}.png")
        page.locator(".card").screenshot(path=p)
        _assert_chrome(p)
        paths.append(p)
    return paths


def _load_quotes(ep_num):
    """Normalized across both quotes.json schema generations — see quote_source."""
    ep_dir = quote_source.find_episode_dir(REPO, ep_num)
    return os.path.basename(ep_dir), quote_source.load_quotes(ep_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fontcheck", action="store_true")
    ap.add_argument("--proof", action="store_true")
    ap.add_argument("--episode", type=int)
    ap.add_argument("--title", default="", help="published episode title for the footer")
    ap.add_argument("--dir", help="episode folder (default: Episodes/E<N>-*)")
    ap.add_argument("--setting", default=fmt.CANON)
    # "watermark" is canon (ADR-0012, kept by ADR-0015); "none" exists for comparison.
    ap.add_argument("--mark", default="watermark", choices=("watermark", "none"))
    ap.add_argument("--upload", action="store_true", help="clear and repopulate the episode's Drive cards/ folder")
    a = ap.parse_args()

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": fmt.CANVAS, "height": fmt.CANVAS}, device_scale_factor=1)
        page.set_content("<html><body></body></html>")

        print("Font resolution:")
        if not fontcheck(page):
            sys.exit("fonts did not resolve -- refusing to render off-brand cards")
        if a.fontcheck:
            return

        if a.proof:
            _, qs = _load_quotes(29)
            shortest = min(qs, key=lambda q: len(q["text"]))
            longest = max(qs, key=lambda q: len(q["text"]))
            # Its own folder: _proof/ also holds final/ and plex/, which this must not touch.
            out = os.path.join(HERE, "_proof", "format")
            os.makedirs(out, exist_ok=True)
            for f in os.listdir(out):
                if os.path.isfile(os.path.join(out, f)):
                    os.remove(os.path.join(out, f))
            # Two real title lengths. The long one is E30's verified published title
            # -- the worst case the footer actually has to survive.
            titles = {
                "longtitle": "Feed Your Head | Gut Microbiome, Hormones & Midlife Vitality",
                "shorttitle": "Feed Your Head",
            }
            print(f"\nProof, setting {a.setting}, quote centred, citation right-justified")
            for mark in ("watermark", "none"):  # ADR-0015: the mark is a watermark or absent
                for tkey, title in titles.items():
                    for label, q in (("short", shortest), ("long", longest)):
                        prefix = f"{mark}_{tkey}_{label}"
                        ps = render_slides(page, a.setting, q, 30, title, out, prefix, mark)
                        print(f"  {prefix:32s} {len(ps)} slide(s)")
            return

        if a.episode:
            if a.dir:  # Phase 2's card step: a staged Drive copy
                ep_dir = os.path.abspath(a.dir)
                folder, qs = os.path.basename(ep_dir), quote_source.load_quotes(ep_dir)
            else:
                folder, qs = _load_quotes(a.episode)
                ep_dir = os.path.join(REPO, "Episodes", folder)
            out = os.path.join(ep_dir, "cards")
            # CARD-04: every carousel opens on a cover. Check its titles BEFORE any
            # slide renders, so a missing sidecar stops the run here rather than
            # leaving a folder of coverless carousels that looks finished.
            from rockstar.cards import cover_titles, render_covers

            cover_titles.titles_for(ep_dir, a.episode, qs)
            from rockstar.cards import cover_image

            names = os.listdir(ep_dir)
            for q in qs:
                cover_image.pick_image(q["speaker"], names, [x["speaker"] for x in qs])
            if not render_covers.cover_fontcheck(page):
                sys.exit("Avenir Next Condensed did not resolve -- refusing to render")
            os.makedirs(out, exist_ok=True)
            rendered = []
            for q in qs:
                ps = render_slides(page, a.setting, q, a.episode, a.title, out, f"E{a.episode}_{q['id']}", a.mark)
                rendered.extend(ps)
                print(f"  {q['id']}  {len(q['text']):3d} ch -> {len(ps)} slide(s)")
            print("\nCovers (CARD-04):")
            covers = render_covers.render_episode_covers(page, a.episode, a.title, ep_dir=ep_dir, out_dir=out)
            print(f"\n{len(qs)} posts, {len(rendered)} slides + {len(covers)} covers -> {out}")
            if a.upload:
                print("\nUpload:")
                upload_cards(a.episode, out)
            return

        ap.print_help()


if __name__ == "__main__":
    main()
