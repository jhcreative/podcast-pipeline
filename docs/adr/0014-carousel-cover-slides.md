# ADR-0014 — Quote carousels open on a cover: photo, name, gated title, the cards' footer

**Status:** ACCEPTED 2026-10-08 (John: "looks good proceed", after four layout passes on
the rendered covers).
**Deciders:** John (owner), Angela (PO), Joni (design), Lisa (words), Toby (architecture), Jen (gates)
**Related:** ADR-0012 (quote cards), ADR-0013 (captions: model writes, code gates),
ADR-0009 (title precedence), REEL-08 (speaker images) · **Story:** CARD-04

## Context

The five quote carousels per episode open on a text slide, which does not read as an
Instagram thumbnail or a first impression (John, 2026-10-08). The episode already has a
photo of every speaker inside its tile and reel images, but both are finished designs
with the name and the wordmark baked in.

## Decision

1. **A cover is slide 1 of every carousel.** `E{N}_{qid}_0cover.png`; the quote slides
   are unchanged (proven byte-identical).
2. **Layout** (John, 2026-10-08, third pass): a **square photo pinned top right**
   (560px, the largest square inside the photograph, not a tight face crop); the
   speaker's **name to its left, right-justified** against it with 32px padding, gold
   (`#b7ad78`, off the lockup) Avenir Next Condensed Demi Bold, **split after the first
   name** ("Heather" / "Reilly Hiemstra"), the first name 49px and the rest 41px, a gold
   rule under it; the **title below it all, centred**, white Avenir Next Condensed Bold
   68px; the cards' own 160px footer cut from `background.png`, lifted 20px over a
   purple band so viewer apps don't crop the lockup or the episode line.
   The same 20px tops the card: photo and name sit at y 20..580, which frames the title
   tighter in y 580..900. Purple field `#2d193c`
   (the panel as rendered, so field and footer read as one). Condensed is the lockup's cut.
   *Superseded the same day:* (1) name and title in a column left of a full-height photo
   cover-cropped to 492 × 920, which cropped faces tight; (2) a 600px photo with a 36px
   one-size name and a 59px title -- John asked for both 15% larger and the split name,
   and the photo gave up 40px so "VASHTI-MERCER" (328px) fits beside it.
3. **Text sits inside Instagram's 3:4 grid crop** (x 144..936 of 1080), so the thumbnail
   shows the whole title and name; the photo keeps x 520..945 in view.
4. **Only the photograph is used**: the longest run of non-purple rows, and on a tile
   the part right of the name panel. **Reel first, tile fallback** (revised 2026-10-08:
   the E30/E31 tiles have no seam between their text and photo, so the cut carried
   text; every reel has a clean photo band), BrandAssets host image for Heather. The host is matched by **surname** (E27's guest is also a Heather).
5. **The model drafts titles, code gates them** (ADR-0013's division), into
   `cover_titles.json` beside `quotes.json`, which a person may edit and which a
   redraft never overwrites unasked (PP-11).
6. **Type is fixed** (ADR-0012); a title that does not fit fails, it is never shrunk.
7. **The backfill is add-only**, the opposite contract to CARD-02's clear-and-repopulate:
   existing slides and captions may already be posted or hand-fixed.

## Alternatives considered

- **The tile, whole, as the cover.** Zero layout work, but it names the guest twice
  over the cover's own name and title, and says nothing about the carousel.
- **Full-bleed photo with the title over a scrim.** More dramatic; rejected because the
  title's legibility would depend on each photo, and the grid crop cuts a centred
  full-width title.
- **Title written by code from the quote's first clause.** Deterministic, but a clause
  is a restatement, which the gate exists to stop.
- **Type sized to fill the column.** Rejected for the cards' reason: one size is the brand.

## Consequences

- A new episode now needs `make_cover_titles.py` before `render_cards.py`; the renderer
  refuses without it, so a forgotten step is loud, not silent.
- The title spans 792px, so long scientific words no longer bound it; the name box
  (344px) bounds names, which break only between words; a surname wider than that
  fails the render rather than shrinking.
- If the type size changes, every cover is stale. Re-render is deterministic.
