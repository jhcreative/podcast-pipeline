"""
CARD-05 / ADR-0015: the IBM Plex faces every card renders in, vendored and self-hosted.

The WOFF2 files live in BrandAssets/fonts/ibm-plex/ (SIL OFL, pinned release, checksums in
its README). Pages are built with set_content, whose origin is about:blank, so the faces
travel as data: URIs inside the page's own CSS -- the same reason the card background does.
Nothing is fetched from Google Fonts or anywhere else at render time.

Chromium fetches an @font-face only when laid-out text uses it, so document.fonts.ready
can resolve while a face is still missing and the first measurement comes back in a
fallback face. load(page) loads every face explicitly; call it after every set_content
that measures or captures. The CARD-05 proof's first run measured Helvetica before
this existed, and fontcheck caught it.
"""

import base64
import os

from rockstar import config

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(str(config.CODE_ROOT), "BrandAssets", "fonts", "ibm-plex")

SANS_FAMILY = "CardPlexSans"
COND_FAMILY = "CardPlexSansCondensed"
SANS = f'"{SANS_FAMILY}"'  # CSS font-family values
COND = f'"{COND_FAMILY}"'

FACES = [  # (family, weight, file)
    (SANS_FAMILY, 400, "IBMPlexSans-Regular.woff2"),
    (SANS_FAMILY, 500, "IBMPlexSans-Medium.woff2"),
    (SANS_FAMILY, 600, "IBMPlexSans-SemiBold.woff2"),
    (SANS_FAMILY, 700, "IBMPlexSans-Bold.woff2"),
    (COND_FAMILY, 600, "IBMPlexSansCondensed-SemiBold.woff2"),
    (COND_FAMILY, 700, "IBMPlexSansCondensed-Bold.woff2"),
]


def _css():
    rules = []
    for fam, weight, name in FACES:
        data = base64.b64encode(open(os.path.join(FONT_DIR, name), "rb").read()).decode()
        rules.append(
            f'@font-face {{ font-family:"{fam}"; font-weight:{weight}; '
            f'font-style:normal; src:url(data:font/woff2;base64,{data}) format("woff2"); }}'
        )
    return "\n".join(rules)


CSS = _css()

_LOAD_JS = "() => Promise.all([" + ", ".join(f"document.fonts.load('{w} 64px \"{fam}\"')" for fam, w, _ in FACES) + "])"


def load(page):
    """Load every Plex face into the current page before anything is measured."""
    page.evaluate(_LOAD_JS)


def fontcheck(page):
    """Prove each Plex face resolved: each must measure differently from the fallback
    at its weight. A face that silently fell back produces a card that is subtly off-brand
    and, worse, mis-measured -- every fit decision would be wrong."""
    page.set_content(f"<html><head><style>{CSS}</style></head><body></body></html>")
    load(page)
    ok = True
    for fam, weight, name in FACES:
        w = page.evaluate(
            """([fam, wt]) => { const m = (f) => { const s = document.createElement('span');
                 s.textContent = 'Handgloves perimenopause 1234';
                 s.style.cssText = 'position:absolute;white-space:nowrap;font-size:64px;'
                   + `font-weight:${wt};font-family:${f}`;
                 document.body.appendChild(s); const r = s.getBoundingClientRect().width; s.remove(); return r; };
               return [m(`"${fam}", Helvetica`), m('Helvetica')]; }""",
            [fam, weight],
        )
        if abs(w[0] - w[1]) < 1:
            print(f"  FAIL  {name} did not resolve (measured as Helvetica, {w[0]:.1f}px)")
            ok = False
    # A width test can't catch one bad weight -- the browser borrows the nearest Plex
    # weight. Ask the font set itself which faces actually loaded.
    loaded = page.evaluate("""() => [...document.fonts].filter(f => f.status === 'loaded')
                                        .map(f => f.family.replace(/"/g, '') + ':' + f.weight)""")
    for fam, weight, name in FACES:
        if f"{fam}:{weight}" not in loaded:
            print(f"  FAIL  {name} did not load (font set reports {sorted(loaded)})")
            ok = False
    if ok:
        print(f"  ok    IBM Plex: {len(FACES)} faces resolved from {os.path.basename(FONT_DIR)}/")
    return ok
