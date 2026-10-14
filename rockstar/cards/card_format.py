"""
Quote-card format -- the layout constants and the HTML the renderer builds.

ADR-0015 (CARD-05, 2026-10-08) supersedes ADR-0012's type and anchoring:
  - every face is IBM Plex, vendored (plex_fonts.py); the quote is Plex Sans Regular
    52px / 1.42 (setting B, John's pick from the proof sheet);
  - the quote is VERTICALLY CENTRED in its zone, between the top margin and the fixed
    citation. The citation and the footer are now the steady elements on a swipe;
  - the footer is lifted 40px over a purple band, as on the covers (ADR-0014).

ADR-0012's other rules stand: type size is FIXED and content flows across as many
slides as it needs (report-writer collection canon §2). Nothing is scaled, clipped or
shrunk to fit. If a quote does not fit, it gets another slide.

Canvas is 1080x1080. The supplied template (background.png) carries the brand panel
(bottom 160px, #291c3b, logo lockup in its left 413px); lifted_background() moves that
panel up FOOTER_PAD px and paints the band beneath. This module never draws chrome the
template provides -- it only places type.
"""

import base64
import io

from rockstar.cards import plex_fonts

CANVAS = 1080
PANEL_TOP = 920  # the panel's top in the supplied template
PANEL_H = CANVAS - PANEL_TOP
LOGO_RIGHT = 413  # right edge of the logo lockup inside the panel
FOOTER_PAD = 40  # the footer sits this far above the bottom edge: 20 in ADR-0015,
# 40 on John's cover pass the same day (shared with the covers)
FOOTER_TOP = PANEL_TOP - FOOTER_PAD  # 880
BAND = "#2d193c"  # the panel purple as the template renders it

INK = "#291c3b"  # quote + citation ink, on the light field
PANEL_INK = "#ffffff"  # episode line, on the purple panel

QUOTE_FACE = plex_fonts.SANS
CITE_FACE = plex_fonts.SANS
PANEL_FACE = plex_fonts.SANS

# --- Fixed elements (one spacing scale, base 8: 24 / 40 / 64 / 104) ---
CITE_BOTTOM = 40  # the citation sits this far above the footer
CITE_PX = 24
CITE_LINE = 1.25
QUOTE_CITE_GAP = 64  # the quote zone ends this far above the citation's top

# --- Opening-mark watermark: Plex's “, behind the type, travelling with the quote ---
MARK_SIZE_WATERMARK = 300
MARK_OPACITY_WATERMARK = 0.16
MARK_LEFT_WATERMARK = -64  # relative to the quote's left edge
MARK_TOP_WATERMARK = -96  # relative to the quote's top: partly behind its first lines

SETTINGS = {
    # B is canon (John, 2026-10-08, ADR-0015). A and C are the proof's alternatives,
    # kept so the proof can be re-run; the episode path never uses them.
    "A": dict(quote_px=46, line=1.45, track=0.0, margin=104, cite_px=CITE_PX),
    "B": dict(quote_px=52, line=1.42, track=0.0, margin=104, cite_px=CITE_PX),
    "C": dict(quote_px=58, line=1.38, track=0.0, margin=104, cite_px=CITE_PX),
}
CANON = "B"


def _setting(setting):
    return SETTINGS[setting] if isinstance(setting, str) else setting


def cite_top():
    """The citation's top edge (one line). Fixed on every slide."""
    return FOOTER_TOP - CITE_BOTTOM - round(CITE_PX * CITE_LINE)


def zone(setting):
    """(top, height) of the box the quote is centred in."""
    top = _setting(setting)["margin"]
    return top, cite_top() - QUOTE_CITE_GAP - top


def quote_top(setting, mark_mode=None):
    """The zone's top. Kept for callers of ADR-0012's fixed anchor; with centring the
    quote's first line is at or below this."""
    return zone(setting)[0]


def lifted_background(path):
    """background.png with its footer lifted FOOTER_PAD px over a purple band, as a
    data: URI (set_content pages cannot load file://)."""
    from PIL import Image

    bg = Image.open(path).convert("RGB")
    card = Image.new("RGB", (CANVAS, CANVAS), BAND)
    card.paste(bg.crop((0, 0, CANVAS, FOOTER_TOP)), (0, 0))
    card.paste(bg.crop((0, PANEL_TOP, CANVAS, CANVAS)), (0, FOOTER_TOP))
    card.paste(BAND, (0, FOOTER_TOP + PANEL_H, CANVAS, CANVAS))
    buf = io.BytesIO()
    card.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  {fonts}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{canvas}px; height:{canvas}px; }}
  .card {{
    position:relative; width:{canvas}px; height:{canvas}px;
    background:url("{bg}") no-repeat 0 0/{canvas}px {canvas}px;
  }}
  /* The quote is centred in this box; the box, not the quote, is the fixed element. */
  .zone {{
    position:absolute; left:{margin}px; right:{margin}px; top:{zone_top}px; height:{zone_h}px;
    display:flex; flex-direction:column; justify-content:center; z-index:0;
  }}
  .quote {{
    position:relative;
    font-family:{quote_face}; font-weight:400;
    font-size:{quote_px}px; line-height:{line}; letter-spacing:{track}em;
    color:{ink}; text-wrap:pretty;
  }}
  .quote .txt {{ position:relative; }}
  .mark {{
    position:absolute; left:{mark_left}px; top:{mark_top}px; z-index:-1;
    font-family:{quote_face}; font-weight:700; font-size:{mark_size}px; line-height:1;
    color:{ink}; opacity:{mark_opacity};
  }}
  /* Fixed, right-justified, above the lifted footer. */
  .cite {{
    position:absolute; right:{margin}px; top:{cite_top}px;
    text-align:right; white-space:nowrap;
    font-family:{cite_face}; font-weight:600;
    font-size:{cite_px}px; line-height:{cite_line}; letter-spacing:0.01em;
    color:{ink};
  }}
  /* Episode line in the (lifted) panel, right of the lockup. The show name is NOT
     repeated here -- the lockup already says it. */
  .panel {{
    position:absolute; left:{logo_right}px; top:{footer_top}px;
    width:{panel_w}px; height:{panel_h}px;
    display:flex; align-items:center; justify-content:flex-end;
    padding:14px 44px 14px 24px;
  }}
  .ep {{
    font-family:{panel_face}; font-weight:500;
    font-size:24px; line-height:1.3; letter-spacing:0.02em;
    color:{panel_ink}; text-align:right;
  }}
</style></head><body>
  <div class="card">
    <div class="zone"><p class="quote">{mark_block}<span class="txt">{quote}</span></p></div>
    {cite_block}
    <div class="panel"><span class="ep">{episode}</span></div>
  </div>
</body></html>"""


def build_html(bg_url, setting, quote_text, citation, episode_line, mark_mode="watermark", show_mark=True):
    s = _setting(setting)
    ztop, zh = zone(s)
    cite_block = f'<p class="cite">{citation}</p>' if citation else ""
    mark_block = '<span class="mark">“</span>' if show_mark and mark_mode == "watermark" else ""
    return PAGE.format(
        fonts=plex_fonts.CSS,
        canvas=CANVAS,
        bg=bg_url,
        margin=s["margin"],
        zone_top=ztop,
        zone_h=zh,
        quote_face=QUOTE_FACE,
        quote_px=s["quote_px"],
        line=s["line"],
        track=s["track"],
        ink=INK,
        mark_left=MARK_LEFT_WATERMARK,
        mark_top=MARK_TOP_WATERMARK,
        mark_size=MARK_SIZE_WATERMARK,
        mark_opacity=MARK_OPACITY_WATERMARK,
        cite_top=cite_top(),
        cite_face=CITE_FACE,
        cite_px=s["cite_px"],
        cite_line=CITE_LINE,
        logo_right=LOGO_RIGHT,
        footer_top=FOOTER_TOP,
        panel_w=CANVAS - LOGO_RIGHT,
        panel_h=PANEL_H,
        panel_face=PANEL_FACE,
        panel_ink=PANEL_INK,
        quote=quote_text,
        cite_block=cite_block,
        mark_block=mark_block,
        episode=episode_line,
    )
