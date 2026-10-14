# CARD-01 — Square quote cards from the five selected quotes

**Owner:** Bill (impl) · **Tests:** Jen · **Review:** Toby · **Design:** Joni · **PO:** Angela
**Priority:** Medium (wanted for E31's publication) · **Status:** DONE (format + renderer) —
delivery to Drive open, see CARD-02
**Created:** 2026-07-25 · **Raised by:** John

---

## Why

Every episode already selects five quotes for the reels. The same five carry the episode
into the feed as static assets at a fraction of the render cost — no transcription, no
clip extraction, no word alignment. Nothing consumed them until now.

John's framing was explicit: this is the report-writer's page-fit idea applied to a
square canvas — one consistent family, size, leading and tracking as a **branding
exercise**, with long quotes flowing onto additional slides rather than being shrunk.

## Acceptance criteria

1. **Five posts per episode**, one per quote, each 1–N slides at 1080×1080 PNG.
2. **Fixed type.** 50px American Typewriter Condensed / lh 1.38 / 0.006em, `#291c3b`,
   104px margin. No size varies by quote length, ever.
3. **Flow, don't shrink.** A quote that overruns gets another slide, split at the
   largest natural boundary that fits (sentence → clause → word). The concatenation of
   the slides equals the original quote, asserted at render time. No slide is left an
   orphan: after splitting, units rebalance backward at a constant slide count.
4. **Fixed anchors.** Quote top and right-justified citation sit at the same y on
   every slide of every post.
5. **Panel line** is `E{N} · {published title}` (+ ` · i/n` when multi-slide), white
   Avenir Next, right-aligned, max two lines. Show name absent.
6. **Watermark mark** — 300px `“` at 20%, behind the type, every slide.
7. **Three runtime gates, all fail loud:**
   - Apple faces proven resolved (a Helvetica fallback is refused, not rendered).
   - Rendered PNG pixel-sampled to prove the brand panel and field painted.
   - Laid-out quote measured against the citation anchor; overflow raises with the
     slide named. Panel overflow raises naming the title as the cause.
8. **No model in the path.** Splitting is deterministic (ADR-0010's lesson).

## Evidence (2026-07-25)

Proof rendered against E29's real quotes — shortest 291 ch, longest 599 ch — at three
candidate settings, plus three mark treatments and two title lengths including E30's
verified 60-character published title. Artifacts in `quote_cards/_proof/`.

At canon settings: shortest quote → 1 slide, longest → 3 slides. Setting A (58px) was
disqualified by its own output, splitting a 291-character quote in two.

Full episode run — `--episode 29` → **5 posts, 10 slides** (2/3/2/2/1), all gates
passing, in `Episodes/E29-corinne/cards/`. Contact sheet: `_E29_all.png`.

Three real defects were caught during the proof and fixed:
- **Orphan slide.** Greedy packing left q03's second slide carrying one short sentence.
  Rebalancing pushes units backward at constant slide count.
- **Background silently absent.** Chromium refuses `file://` subresources from a
  `set_content` page, so the first run produced clean, plausible, entirely unbranded
  cards with no error. Background now travels as a `data:` URI, and every PNG is
  pixel-sampled.
- **Straight apostrophes** from the transcript rendered as typewriter primes.
  Typographic conversion now runs before layout.

## Tests (2026-07-27) — `test_card01_cards.py`, 13 passing

Schema normalization (both generations, plus hard failures on missing text, missing
speaker, duplicate ids), typographic conversion, em-dash rehang, and three split
invariants measured in real Chromium: concatenation is verbatim, every slide fits, a
short quote stays one slide. Rebalancing verified on E29 q03's shape — emptiest slide
**10% → 40%** at unchanged slide count.

The browser-backed tests **skip loudly** if playwright is absent rather than passing
quietly. A green run that silently skipped the fit guarantees is the REEL-05 failure
shape exactly.

**Finding, now canon (§2):** a short *final* slide is not automatically a defect. E29
q02 measures 50% / 91% / 20% and is correct — the middle sentence alone fills 91% of a
column, both alternative groupings overflow, and the closing slide holds one complete
sentence. Fragments are orphans; whole sentences are not. The asserted invariant is that
every slide ends on a complete sentence unless that sentence alone could not fit.

## First real episode — E31, 2026-07-27

`quotes.json` and `apple_summary.md` pulled read-only from E31's Drive folder. Rendered
with the published title *"Bliss Is the System | The ECS, Estrogen & the Perimenopause
Connection"* — **70 characters, longer than the 60-char stress case**, and it fits the
two-line panel.

E31's quotes run 451–870 characters against E29's 291–599, giving **16 slides across 5
posts** (3/4/4/2/3). The format absorbed roughly 60% more text per quote without any
change: more slides, same type.

## Not in scope (see CARD-02)

Upload to the episode's Drive folder. Cards land in `Episodes/E{N}-*/cards/` locally
and are moved by hand for now.

## Notes

- Local-render-only is a licensing consequence, not a preference — see ADR-0012.
- John declined a Phase-2 reminder for un-rendered cards. Residual risk recorded in
  ADR-0012; revisit if cards are missed twice.
