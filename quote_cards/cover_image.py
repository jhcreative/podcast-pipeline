"""
CARD-04: which picture goes on a carousel's cover, and the photograph inside it.

The episode's tile and reel images are finished designs, not photos: the tile is a
light name panel beside the photo over the purple wordmark, the reel is a purple
header (lockup + name) over the photo over purple. Put either on a cover whole and the
cover says the guest's name three times. So the cover takes only the photograph:
the largest run of rows that are not brand purple, and, on a tile, the part right of
the name panel.

Which image: the speaker's REEL image, the TILE as the fallback, and for Heather the
host image in BrandAssets when the episode has none of her own. (John first chose
tile-first, 2026-10-08, on the assumption the tile's photo cuts out cleanly. The E30/E31
tiles in Drive set the name text and the photo on one continuous grey, with no seam to
cut at, so their crops carried text; every reel has a clean full-width photo band.) The speaker is matched by name the way REEL-08 matches reel backgrounds,
with one difference: the host is recognised by her FULL name, because E27's guest is
also a Heather and a first-name rule hands a guest the host's photo.

Nothing is guessed. A speaker with no matching image is a hard failure that names the
file to drop.
"""

from __future__ import annotations

import base64
import difflib
import io
import os
import re
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST_IMAGE = os.path.join(REPO, "BrandAssets", "reel_background_host.jpg")
sys.path.insert(0, REPO)                 # host_identity lives at the repo root
import host_identity                     # noqa: E402  REEL-09: the one host rule
HOST_NAME = host_identity.HOST_NAME

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
_HONORIFICS = {"dr", "doctor", "mr", "mrs", "ms", "prof", "rev", "md", "phd", "nd"}
# Brand purple as it actually renders in the supplied artwork (#2d193c measured off
# background.png's panel; the canon ink is #291c3b). A pixel this close is chrome.
_PURPLE = (45, 25, 60)
_PURPLE_DIST = 48
_PURPLE_ROW = 0.55       # a row this purple is a band (header, wordmark, footer)


def _tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


def _name_words(speaker):
    return [w for w in _tokens(speaker) if w not in _HONORIFICS]


def is_host(speaker):
    """The host, however the transcript spelled her -- host_identity's rule (REEL-09)."""
    return host_identity.is_host(speaker)


def display_name(speaker):
    """The name printed on the cover and the slide citation: the host's canonical form,
    a guest's as the transcript has it (BIO-02 reconciles it with the guest form)."""
    return host_identity.display_name(speaker)


def _matches(image_name, speaker, all_speakers):
    """An image names this speaker by a surname word, or by first name when no other
    speaker shares it."""
    toks = set(_tokens(os.path.splitext(image_name)[0]))
    words = _name_words(speaker)
    if not words:
        return False
    first, rest = words[0], [w for w in words[1:] if len(w) >= 3]
    if any(w in toks for w in rest):
        return True
    # Distinct people: all_speakers repeats a speaker once per quote.
    people = {display_name(s) for s in all_speakers}
    shared = sum(1 for s in people if (_name_words(s) or [""])[0] == first) > 1
    return first in toks and not shared


def _names_host(image_name, speaker):
    """A filename that is the host's picture: her surname, or her first name when the
    speaker is not also a Heather. Guards the single-image rule, which would otherwise
    hand a guest the host's photo when the host has no quote in the episode."""
    toks = set(_tokens(os.path.splitext(image_name)[0]))
    if toks & {"reilly", "hiemstra"}:
        return True
    return "heather" in toks and (_name_words(speaker) or [""])[0] != "heather"


def pick_image(speaker, names, all_speakers):
    """(filename or HOST_IMAGE path, rule) for this speaker, or raises SystemExit.

    `names` are the episode folder's filenames. Order: reel, then tile. Within a
    kind: an image that names the speaker; else the one image of that kind when it
    names no other speaker. The host falls back to the BrandAssets host image.
    """
    others = [s for s in all_speakers if display_name(s) != display_name(speaker)]
    for kind in ("reel", "tile"):
        imgs = sorted(n for n in names
                      if n.lower().endswith(_IMAGE_EXT) and kind in _tokens(n)
                      and re.match(r"^E\d+", n, re.I))
        named = [n for n in imgs if _matches(n, speaker, all_speakers)]
        if len(named) == 1:
            return named[0], f"{kind}-named"
        if len(named) > 1:
            raise SystemExit(f"{len(named)} {kind} images name {speaker!r}: "
                             f"{named} -- keep one")
        if is_host(speaker):
            continue                      # never hand the host a guest's single image
        unclaimed = [n for n in imgs if not any(_matches(n, o, all_speakers) for o in others)
                     and not _names_host(n, speaker)]
        if len(imgs) == 1 and unclaimed:
            return imgs[0], f"{kind}-single"
    if is_host(speaker):
        return HOST_IMAGE, "host-default"
    first = (_name_words(speaker) or ["name"])[0]
    raise SystemExit(f"no tile or reel image for {speaker!r}: drop one named like "
                     f"'E<N>-{first.capitalize()}-tile.jpg' in the episode folder")


def _purple_frac(row):
    n = 0
    for r, g, b in row:
        if (r - _PURPLE[0]) ** 2 + (g - _PURPLE[1]) ** 2 + (b - _PURPLE[2]) ** 2 <= _PURPLE_DIST ** 2:
            n += 1
    return n / len(row)


def photo_box(img, kind):
    """The photograph's box (l, t, r, b) inside a tile or reel design, in img's pixels.

    Rows: the longest run whose purple fraction is under _PURPLE_ROW. Columns, tile
    only: the name panel's right edge, the sharpest change in column colour between
    30% and 62% of the width -- and only when what lies left of it is a flat, light,
    unsaturated panel. A reel photo can sit on a light grey backdrop (the host
    image does), so the column rule never runs on a reel.
    """
    W, H = img.size
    w = 240
    h = max(1, round(H * w / W))
    s = img.convert("RGB").resize((w, h), Image.BILINEAR)
    px = s.load()
    rows = [_purple_frac([px[x, y] for x in range(w)]) < _PURPLE_ROW for y in range(h)]
    best, start = (0, 0), None
    for y, ok in enumerate(rows + [False]):
        if ok and start is None:
            start = y
        elif not ok and start is not None:
            if y - start > best[1] - best[0]:
                best = (start, y)
            start = None
    t, b = best
    if b - t < h * 0.25:
        raise SystemExit(f"could not find the photograph in this {kind} image "
                         f"(longest non-purple band is {b - t}/{h} rows)")
    l, r = 0, w
    if kind == "tile":
        def colmean(x):
            vals = [px[x, y] for y in range(t, b)]
            return [sum(v[i] for v in vals) / len(vals) for i in range(3)]
        means = [colmean(x) for x in range(w)]
        lo, hi = int(w * 0.30), int(w * 0.62)
        def jump(x):
            a = [sum(means[i][c] for i in range(x - 3, x)) / 3 for c in range(3)]
            z = [sum(means[i][c] for i in range(x, x + 3)) / 3 for c in range(3)]
            return sum(abs(a[c] - z[c]) for c in range(3))
        x = max(range(lo, hi), key=jump)
        left = [px[i, j] for i in range(0, x, 2) for j in range(t, b, 2)]
        light = sum(1 for p in left if min(p) > 150 and max(p) - min(p) < 30) / len(left)
        if light > 0.6:
            l = x + 1                     # one column in, clear of the seam
    sx = W / w
    return (round(l * sx), round(t * sx), round(r * sx), round(b * sx))


def square_box(box, top_bias=0.15):
    """The largest square inside the photograph (John, 2026-10-08: square, not a tight
    crop on the face). Centred across; a tall photo keeps its upper part, where the
    head is, by trimming 15% of the excess from the top and the rest from the bottom."""
    l, t, r, b = box
    w, h = r - l, b - t
    side = min(w, h)
    x = l + (w - side) // 2
    y = t + round((h - side) * top_bias)
    return (x, y, x + side, y + side)


def photo_data_uri(path, kind, max_px=1400):
    """The photograph, cropped out of its design and embedded as a data: URI (the
    card pages load via set_content, which cannot reach file:// -- see render_cards)."""
    img = Image.open(path)
    img = img.convert("RGB")
    box = square_box(photo_box(img, kind))
    photo = img.crop(box)
    photo.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    photo.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), box


def kind_of(path):
    if os.path.abspath(path) == os.path.abspath(HOST_IMAGE):
        return "reel"
    return "tile" if "tile" in _tokens(os.path.basename(path)) else "reel"
