# GenX Hits Menopause — Square Quote Card Format · CANON
Single source of truth for how episode quote cards are composed, split, rendered, and
verified. Locked by John 2026-07-25. Sibling of the JHC report collection format
(`skills/product-ops/writer-brand-kit/COLLECTION-FORMAT.md`), from which rules 1 and 2
are inherited directly.

## 1. The atom
One PNG = one **slide** = one 1080×1080 card. One quote = one **post** = 1–N slides,
swiped as an Instagram carousel. Five selected quotes per episode = five posts.

Slides are self-describing: every slide carries the speaker's name and the episode
line, so a slide screenshotted alone is still attributed.

**Every post opens on a cover (§12, CARD-04).** The quote slides follow it unchanged.

## 2. Pagination & fit — CANON
**Type size is fixed. A quote that does not fit gets another slide.**
- Splits happen at the largest natural boundary that fits: sentence → clause
  (`—`, `;`/`:`, `,`) → between words as a last resort.
- **OFF-CANON (never do this):** shrinking the type, tightening tracking, reducing
  margins, or clipping to force a quote onto fewer slides. These are the same hacks
  the report canon forbids, for the same reason — they hide or distort content.
- A slide never ends on a dangling em dash; the dash moves to the head of the next
  slide, where the thought it introduces lives.
- **No orphan slides.** Greedy packing fills each slide to the brim and dumps the
  remainder on the last one, which can leave a single short *fragment* alone on a slide —
  the card equivalent of the orphaned heading the report canon forbids. After splitting,
  units are pushed backward while every slide still fits and the emptiest slide gets
  fuller. The slide count never changes, so nothing is shrunk to achieve balance.
- **A short final slide is not automatically a defect.** When one sentence alone nearly
  fills a column, no order-preserving arrangement can even the set out, and breaking that
  sentence at a clause would put a fragment on screen where a clean sentence boundary
  exists. **Fragments are orphans; complete sentences are not.** E29 q02 measures roughly
  50% / 91% / 20% and is correct: the closing slide holds one whole sentence, and both
  alternative groupings overflow. The invariant that holds is *every slide ends on a
  complete sentence unless that sentence alone could not fit* — asserted in
  `test_card01_cards.py`, not left to judgement.
- **The renderer proves the fit.** Every slide is measured in Chromium after layout.
  Quote overrunning the citation anchor, or an episode line overrunning the panel, is
  a hard failure with a named cause — never a silent crop.

## 3. Canvas & supplied chrome
`background.png`, 1080×1080, derived from `static template.jpg` (2701×2700, normalized
to 2700 then downsampled).

| Element | Geometry |
|---|---|
| Canvas | 1080 × 1080 |
| Brand panel | bottom 160px, full-bleed, `#291c3b` |
| Logo lockup | inside the panel, left 413px |
| Field | the 920px above the panel |

The renderer never draws chrome the background already provides.

## 4. Type contract (ADR-0015, 2026-10-08)
| Role | Face | Size | Colour |
|---|---|---|---|
| Quote | IBM Plex Sans Regular | **52px / line-height 1.42** (setting B, John's pick) | `#291c3b` |
| Citation | IBM Plex Sans SemiBold | 24px / 1.25, tracking 0.01em | `#291c3b` |
| Episode line | IBM Plex Sans Medium | 24px / line-height 1.3, tracking 0.02em | `#ffffff` |
| Opening mark | IBM Plex Sans Bold `“` | 300px at 16% opacity | `#291c3b` |

Margin 104px. The faces are **vendored** in `BrandAssets/fonts/ibm-plex/` (SIL OFL, pinned,
checksums in its README) and load as `@font-face` data URIs through `plex_fonts.py`; nothing
calls Google Fonts. Chromium fetches a face only when text uses it, so `plex_fonts.load(page)`
runs after every `set_content` that measures or captures, and `plex_fonts.fontcheck` fails a
render whose faces fell back. *Superseded:* American Typewriter Condensed 50px and Avenir Next
(ADR-0012); the back catalogue is re-rendered (CARD-05).

Straight apostrophes from the transcript are converted to typographic marks before
layout.

## 5. Anchors — CANON (ADR-0015)
- **The quote is vertically centred** in a fixed zone: from the 104px top margin to 64px above
  the citation. *Superseded:* ADR-0012's fixed quote top.
- **Citation** is right-justified at a fixed y, 40px above the footer, on every slide.
- **The footer is lifted 40px** (y 880..1040) over a purple band, as on the covers (§12), so it
  doesn't move between cover and slides.
- **Opening mark** travels with the quote: 64px left of it, 96px above its first line, drawn on
  **every** slide behind the type. It costs no capacity.

A carousel is swiped; the citation and the footer are the steady elements now.

## 6. Episode line
`E{N} · {published episode title}` — plus ` · {i}/{n}` when the post has more than
one slide. Wraps to at most two right-aligned lines inside the panel.

**The show name is not repeated here.** The logo lockup already says it; the panel's
only free space goes to the episode's own title.

Title source is ADR-0009: `apple_summary.md` wins over the schedule.

## 7. Render host (ADR-0015)
**Mac or box: the same renderer.** IBM Plex is vendored, so CARD-05's Phase 2 step renders on
the box exactly what `render_cards.py` renders on the Mac (visually; Chromium
versions may differ, so not byte parity). *Superseded:* ADR-0012's Mac-only rule, which existed
because American Typewriter and Avenir Next are macOS faces.

The background (with its lifted footer) is embedded as a `data:` URI. Pages load via
`set_content`, whose origin is `about:blank`; Chromium refuses `file://` subresources from
there. Every finished PNG is pixel-sampled to prove the field, the panel and the band painted.

## 8. Rejected, with their merits intact
- **Server-side render with substitute webfonts** — would run automatically inside
  Phase 2, but the cards would not be set in the specified faces. Brand consistency
  with the reels was worth more than the automation.
- **`above` / `compact` mark placement** — a solid opening mark above the quote. Must
  reserve its space on every slide to keep the anchor fixed, costing one to two lines
  of capacity everywhere; at 150px it turned E29's 291-character quote into a two-slide
  post with a three-quarters-empty second slide.
- **Type-size ladder by quote length** — would fill short cards more evenly. Rejected:
  one size is the branding rule.
- **Fixed quote top (ADR-0012)**: kept swipes steadier, but short quotes sat high with an empty
  lower half. John asked for centring (ADR-0015).
- **Plex Serif for quotes**: a more literary voice; John chose Plex Sans for readability.
- **4:5 canvas (1080×1350)** — more feed real estate and typically more reach, but a
  taller field changes every split, making it a second set to render and verify.
- **Phase-2 hook that flags un-rendered cards** — rejected as unnecessary ceremony;
  cards are a deliberate local step (see ADR-0012 for the residual risk).

## 9. The companion caption (ADR-0013)

Every post gets one `.txt`, written beside its PNGs in the same `cards/` folder:

```
{opening — specific to this post's quote, never a restatement of it}

{guest intro — from the About section of apple_summary.md, once per episode}

Connect with {Guest Name} at [ADD @HANDLE]
Listen to E{N} on Apple Podcasts & Spotify — link in bio.

{3–4 hashtags}
```

**The model writes three things** — the guest intro, each opening, each hashtag set.
**Code writes everything else.** The connect and listen lines are fixed copy carrying a
real person's name and the show's CTA; ADR-0010 is what happens when fixed copy goes
through a model.

Gated, all hard failures: hashtags outside 3–4 or missing a show tag (ADR-0007), any
URL, any music credit (a silent card must not claim the reel's licence), a connect line
with neither handle nor placeholder, an opening that restates the quote.

**A pending bio is marked, never invented.** `About <Guest>` is legitimately a
placeholder on some episodes; the caption then carries `[BIO PENDING — add before
posting]`. Inventing credentials for a real person is the one failure here that cannot
be walked back after posting.

## 10. Reading quotes.json

Two schema generations are live, because the back catalogue is re-rendered on demand:
old episodes (E23–E25) carry `index` + `quote_text_clean`, newer ones (E27+) carry `id`
+ `text`. `quote_source.load_quotes` reads either — ADR-0002's rule that drift is
supported, not chosen against.

## 11. Commands
```bash
python render_cards.py --fontcheck                          # prove the faces resolve
python render_cards.py --proof                              # re-run the format proof
python make_cover_titles.py --episode 31 --title "Hook | Topic"  # cover titles (§12) first
python render_cards.py --episode 31 --title "Hook | Topic"  # 5 posts + covers -> Episodes/E31-*/cards/
python make_captions.py --episode 31 --dry-run              # captions, no API key
python make_captions.py --episode 31
python render_cards.py --episode 31 --title "…" --upload    # push the whole folder
```
Captions before `--upload`: the upload clears the Drive folder and pushes everything
local, so anything added afterwards needs another `--upload`.

## 12. The cover slide (CARD-04, ADR-0014)

Slide 1 of every post: `E{N}_{qid}_0cover.png`, so it sorts first. A text-only first
slide does not read as a thumbnail (John, 2026-10-08).

| Element | Spec |
|---|---|
| Field | `#2d193c` (the panel purple as rendered), full card |
| Photo | **560 × 560 square, pinned right, 40px from the top** (x 520..1080, y 40..600): the largest square inside the photograph, not a tight face crop |
| Speaker name | left of the photo, **right-justified** 32px from it, set low so the gold rule's base is the photo's base; IBM Plex Sans Condensed SemiBold, uppercase, 0.06em, `#b7ad78`. **First name (with any honorific) on its own line at 49px; the rest below at 41px**; breaks only between words |
| Rule | 64 × 4px, `#b7ad78`, under the name, right-aligned |
| Title | **below it all, centred** in y 600..880 × x 144..936; IBM Plex Sans Condensed Bold **68px** / 1.06, white, balanced, ≤ 3 lines |
| Footer | the bottom 160px of `background.png` + the episode line, as on every slide, **lifted 40px** (y 880..1040) over a 40px purple band, clear of viewer-app crops |

**Photo:** the speaker's reel image, else tile, else (Heather) BrandAssets' host image;
only the photograph inside the design is used. **Title:** `cover_titles.json` beside
`quotes.json`; model-drafted, gated (cover_titles.py), hand edits win.

```bash
python make_cover_titles.py --episode 31 --title "…"        # on the box (model key)
python render_cards.py --episode 31 --title "…"             # slides + covers
python render_covers.py --episode 31 --title "…"            # covers only
venv/bin/python -m rockstar.tools.reprocess_episode --episode N --cards-only   # one episode, on the box
```
