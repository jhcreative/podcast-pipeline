"""
CARD-04: the cover slide's layout -- slide 1 of every quote carousel.

Why a cover (John, 2026-10-08): a text-only first slide does not read as a thumbnail
or as a first impression. The cover leads with a face, names the speaker, and titles
the carousel; the quote slides follow unchanged.

Layout, 1080x1080 (John, 2026-10-08, second pass: "square images without tight crops
on faces, pinned to upper right, with name on their left ... right justified against
image with padding, then title below it all ... centered, footer is great"):

    +-------------------------+---------------+
    |                    40px purple          |
    |            FIRST NAME ->|   square      |
    |             LAST NAME  -|   photograph  |   560 x 560, pinned right, y 40..600
    +-------------------------+---------------+
    |            Carousel title, centred      |   field below the photo
    +-----------------------------------------+
    | lockup              E29 · episode title |   panel: 160px, the cards' own footer
    +-----------------------------------------+
    |                                         |   40px purple, clear of app crops
    +-----------------------------------------+

The photo is the largest square inside the speaker's photograph (cover_image.square_box),
so the face keeps its shoulders and background instead of being cropped tight. The
footer is the bottom 160px of the cards' background.png, pixel-identical to every quote
slide that follows. Brand purple fills the field.

Instagram's profile grid shows a square post as a centred 3:4 crop (x 135..945), so the
name and title stay inside x 144..936, and the photo keeps x 520..945 in view.

Type is fixed (the cards' canon, ADR-0012): a title that does not fit fails the render
with its cause; nothing shrinks. Faces are IBM Plex Sans Condensed and Plex Sans
(ADR-0015), vendored; until CARD-05 they were Avenir Next Condensed.
"""

import card_format as fmt
import plex_fonts

CANVAS = fmt.CANVAS
# Purple under the footer, so the lockup and episode line sit clear of the bottom edge
# that viewer apps crop; the footer keeps its 160px and moves up. 20px (John,
# 2026-10-08), then 40 the same day ("another 20px each"). One value with the slides
# (card_format.FOOTER_PAD), so the footer never moves on a swipe.
FOOTER_PAD = fmt.FOOTER_PAD
PANEL_TOP = fmt.PANEL_TOP - FOOTER_PAD   # 880: the footer is y 880..1040
# The same padding at the top: the photo sits at y 40..600 and the title in a tighter
# band, y 600..880.
TOP_PAD = FOOTER_PAD
PHOTO = 560              # the square photo's side, pinned top right; 560 leaves the
                         # name box 344px, and "VASHTI-MERCER" needs 328 at LAST_PX
PHOTO_LEFT = CANVAS - PHOTO
PHOTO_BOTTOM = TOP_PAD + PHOTO
SAFE_LEFT = 144          # inside the 3:4 grid crop (135px) with a little air
SAFE_RIGHT = CANVAS - SAFE_LEFT
NAME_GAP = 32            # padding between the right-justified name and the photo

FIELD = "#2d193c"        # the panel purple as background.png renders it, so the
                         # field and the footer read as one surface
GOLD = "#b7ad78"         # sampled off the lockup's "GENX HITS"
WHITE = "#ffffff"

# ADR-0015: IBM Plex Sans Condensed (vendored) replaces Avenir Next Condensed; the
# uppercase name, its tracking and the SemiBold / Bold weights carry over unchanged.
NAME_FACE = plex_fonts.COND
TITLE_FACE = plex_fonts.COND
# John, 2026-10-08, third pass: name and title another 15%; the name breaks after the
# first name ("Heather" / "Reilly Hiemstra") and the first name is 20% larger.
LAST_PX = 41             # 36 x 1.15
FIRST_PX = 49            # LAST_PX x 1.2
TITLE_PX = 68            # 59 x 1.15
TITLE_LINE = 1.06
TITLE_MAX_LINES = 3
_HONORIFICS = {"dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "prof", "prof.", "rev", "rev."}

PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  {fonts}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{canvas}px; height:{canvas}px; }}
  .card {{ position:relative; width:{canvas}px; height:{canvas}px; background:{field}; }}
  .photo {{
    position:absolute; left:{photo_left}px; top:{top_pad}px; width:{photo}px; height:{photo}px;
    background:url("{photo_url}") no-repeat center/cover;
  }}
  /* Beside the photo: the name, right-justified against it, set low so the gold rule's
     bottom edge sits on the photo's bottom edge (John, 2026-10-08). */
  .namebox {{
    position:absolute; left:{safe_left}px; top:{top_pad}px;
    width:{name_w}px; height:{photo}px;
    display:flex; flex-direction:column; justify-content:flex-end; align-items:flex-end;
  }}
  .name {{
    font-family:{name_face}; font-weight:600; line-height:1.12;
    letter-spacing:0.06em; text-transform:uppercase; color:{gold}; text-align:right;
  }}
  .first {{ display:block; font-size:{first_px}px; }}
  .last {{ display:block; font-size:{last_px}px; }}
  .rule {{ width:64px; height:4px; background:{gold}; margin-top:22px; }}
  /* Below it all: the title, centred in the field between the photo and the footer. */
  .titlebox {{
    position:absolute; left:{safe_left}px; top:{photo_bottom}px;
    width:{title_w}px; height:{title_h}px;
    display:flex; align-items:center; justify-content:center;
  }}
  .title {{
    font-family:{title_face}; font-weight:700; font-size:{title_px}px;
    line-height:{title_line}; letter-spacing:0.005em; color:{white};
    text-align:center; text-wrap:balance;
  }}
  .footer {{
    position:absolute; left:0; top:{panel_top}px; width:{canvas}px; height:{panel_h}px;
    background:url("{footer}") no-repeat 0 0/{canvas}px {panel_h}px;
  }}
  .panel {{
    position:absolute; left:{logo_right}px; top:{panel_top}px;
    width:{panel_w}px; height:{panel_h}px;
    display:flex; align-items:center; justify-content:flex-end;
    padding:14px 44px 14px 24px;
  }}
  .ep {{
    font-family:{panel_face}; font-weight:500;
    font-size:24px; line-height:1.3; letter-spacing:0.02em;   /* = the slides' (card_format) */
    color:{white}; text-align:right;
  }}
</style></head><body>
  <div class="card">
    <div class="photo"></div>
    <div class="namebox"><p class="name">{name}</p><div class="rule"></div></div>
    <div class="titlebox"><p class="title">{title}</p></div>
    <div class="footer"></div>
    <div class="panel"><span class="ep">{episode}</span></div>
  </div>
</body></html>"""


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def split_name(name):
    """(first line, rest): the first name, with any honorific before it, then the rest.
    "Dr. Corinne Maltravers" -> ("Dr. Corinne", "Maltravers")."""
    words = name.split()
    i = 0
    while i < len(words) - 1 and words[i].lower() in _HONORIFICS:
        i += 1
    return " ".join(words[:i + 1]), " ".join(words[i + 1:])


def _name_html(name):
    """First name on its own line, larger; the rest below. Names break between words
    only: "Brennick-Hale" and "Vashti-Mercer" are one surname each, and the browser
    would otherwise break them at the hyphen."""
    def words(s):
        return " ".join(f'<span style="white-space:nowrap">{_esc(w)}</span>' for w in s.split())
    first, rest = split_name(name)
    html = f'<span class="first">{words(first)}</span>'
    return html + (f'<span class="last">{words(rest)}</span>' if rest else "")


def build_cover_html(photo_url, footer_url, name, title, episode_line):
    return PAGE.format(
        fonts=plex_fonts.CSS, canvas=CANVAS, field=FIELD, photo=PHOTO, photo_left=PHOTO_LEFT,
        top_pad=TOP_PAD, photo_bottom=PHOTO_BOTTOM,
        photo_url=photo_url, footer=footer_url,
        panel_top=PANEL_TOP, panel_h=fmt.PANEL_H,
        safe_left=SAFE_LEFT, name_w=PHOTO_LEFT - NAME_GAP - SAFE_LEFT,
        title_w=SAFE_RIGHT - SAFE_LEFT, title_h=PANEL_TOP - PHOTO_BOTTOM,
        name_face=NAME_FACE, first_px=FIRST_PX, last_px=LAST_PX, gold=GOLD,
        title_face=TITLE_FACE, title_px=TITLE_PX, title_line=TITLE_LINE, white=WHITE,
        logo_right=fmt.LOGO_RIGHT, panel_w=CANVAS - fmt.LOGO_RIGHT,
        panel_face=fmt.PANEL_FACE,
        name=_name_html(name), title=_esc(title), episode=_esc(episode_line),
    )
