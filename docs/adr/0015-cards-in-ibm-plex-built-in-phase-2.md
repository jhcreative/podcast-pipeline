# ADR-0015 — Quote cards are set in vendored IBM Plex and built on the box in Phase 2

**Status:** ACCEPTED 2026-10-08. John picked **setting B** from the proof sheet
(`quote_cards/_proof/plex/plex_proof_sheet.png`): *"yep I'm in on B as well."*
**Deciders:** John (owner), Angela (PO), Toby (architecture), Joni (design), Oliver (box), Jen (gates)
**Supersedes:** ADR-0012 §6 (local-only rendering), §7 (Apple-face verification), and the
fixed top anchor of §3 for text slides. **Amends:** ADR-0014 (cover faces). **Story:** CARD-05

## Context

ADR-0012 kept the cards on the Mac because American Typewriter Condensed and Avenir Next are macOS
faces that can't be licensed onto the box. It chose that knowingly. Brand consistency with the reels
outranked automation. It named the cost: a forgotten card run is silent, and silent misses are this
pipeline's established failure class. CARD-04 then moved the per-episode work into one wrapper
(`new-cards.sh`). That wrapper still needs a person, a Mac and two runs.

John, 2026-10-08: build the cards in Phase 2, with open-licensed fonts: *"use our IBM Plex throughout
for readability, maintain uppercase and font-weight patterns and adjust text slides to be vertically
centered and easy to read in terms of font-size and whitespace rhythm."* IBM Plex Sans is already the
Rockstar site's text face, and it ships under the SIL Open Font License.

## Decision

1. **IBM Plex everywhere a card renders.** Quotes, citation and footer use IBM Plex Sans
   (Regular / SemiBold / Medium). The cover's name and title use IBM Plex Sans Condensed (SemiBold /
   Bold). Uppercase, tracking and the weight hierarchy carry over unchanged (CARD-05 table).
   Plex Sans for quotes is John's ruling (Q1), chosen over Plex Serif.
2. **Vendored, never linked.** WOFF2 files from `@ibm/plex-sans@1.1.0` and
   `@ibm/plex-sans-condensed@1.1.0` live in `BrandAssets/fonts/ibm-plex/`, with `LICENSE.txt`
   and checksums. They load as `@font-face` data URIs. Nothing calls Google Fonts.
   Chromium fetches a face only when text uses it, so the renderer loads each face explicitly
   before measuring. The font gate fails a render where any face fell back. (The proof's first run
   measured Helvetica before this was added, and the gate caught it.)
3. **Text slides are vertically centred.** The quote is centred between the 104px top margin and
   the fixed, right-aligned citation. ADR-0012 fixed the top anchor so that *"only the words change"*
   on a swipe. The citation and footer now carry that steadiness instead. The opening-mark watermark
   travels with the quote block and sits partly behind its first lines.
4. **One spacing scale** (base 8: 24 / 40 / 64 / 104), shared by slides and covers.
5. **The footer is lifted 40px** (20px at first; John took it to 40 on the covers the same day) on slides, as on covers (ADR-0014), so the footer doesn't jump when
   the carousel swipes from cover to slide.
6. **Type stays fixed and content flows** (ADR-0012 §1–2 unchanged). **The quote is IBM Plex Sans
   Regular 52px, line-height 1.42** (setting B, John's pick), chosen over A (46px / 1.45) and C (58px / 1.38). On the proof, E29's 599-character quote
   runs to 3 slides at A, B and C, and the 291-character one to 1, 1 and 2.
7. **Phase 2 builds the set** after the reels (Step 12). It alerts and continues on failure (Q3),
   never overwrites an existing `cards/`, and leaves the reels completely untouched (Q4).
8. **The back catalogue is re-rendered in Plex** (Q2) by a John-run replace step. Captions and cover
   titles are kept, and the old set goes to Drive's trash.

## Alternatives considered

- **Keep the Apple faces and Mac rendering** (ADR-0012 as it stands). This keeps the reels' type, but
  the cards still depend on a person remembering to run them on a Mac.
- **Plex Serif for quotes.** It keeps a quoted, literary voice. John chose Plex Sans for readability.
- **Fixed top anchor with Plex.** Swipes would stay steadier, but short quotes would sit high with an
  empty lower half. John asked for centring.
- **Google Fonts link at render time.** Rejected under the house rule (self-host fonts). It would
  also make the box's renders depend on a third party being reachable.

## Consequences

- The cards' type differs from the reels' captions until a separate decision; John chose that
  knowingly (Q4).
- Every Avenir set is stale. The re-render replaces them in Drive; carousels already posted stay as
  posted.
- The box needs Playwright and Chromium. Oliver sizes the box and sequences the card step after
  WhisperX.
- The proof's slide split is greedy. Production keeps render_cards.Fitter's rebalancing, no-orphan
  and verbatim checks.
