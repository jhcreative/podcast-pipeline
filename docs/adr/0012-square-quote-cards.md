# ADR-0012 — Square quote cards are a fixed-type, slide-flowing, locally-rendered format

**Status:** ACCEPTED 2026-07-25 (John)
**Deciders:** John (owner), Angela (PO), Toby (architecture), Joni (design), Jen (gates)
**Supersedes:** nothing · **Related:** ADR-0003/0004 (reel framing), ADR-0009 (title
precedence), ADR-0010 (deterministic thank-you fill), ADR-0011 (manual Phase-2 kickoff)

## Context

Every episode already produces five brand-team-selected quotes (`quotes.json`) which
feed the reels. The same five quotes are wanted as **square Instagram assets** — five
posts per episode, each expanding to a carousel when the quote is long. John supplied a
single background template carrying the brand panel and logo lockup, and specified the
type: quotes in `#291c3b` American Typewriter Condensed, citation in smaller Avenir
Demi, episode info in white Avenir inside the panel.

The explicit design frame was the report-writer's page-fit model: *fixed type, consistent
family/size/leading/tracking as a branding exercise, content flowed across pages.*

## Decision

**1. The report collection canon governs the format.** Type size is fixed; a quote that
does not fit gets another slide. Scaling, tracking-in, margin-shaving, and clipping are
off-canon here for the same reason they are off-canon in the reports — they hide or
distort the content. Splits go sentence → clause → word, and the concatenated slides are
asserted equal to the original quote.

**2. Type is locked at 50px / 1.38 / 0.006em, margin 104px** (proof setting B), citation
Avenir Next Demi Bold 24px. *There is no Demi weight in the Avenir family itself* —
Avenir ships Light/Book/Roman/Medium/Heavy/Black; Demi Bold exists only in Avenir Next.
Avenir Next Demi Bold was chosen deliberately over Avenir Medium.

**3. Anchors are fixed across slides.** Quote top at 104px, citation right-justified at
58px above the panel, opening mark at 40/44 — identical on every slide. A carousel is
swiped; anything that moves between slides reads as instability.

**4. The opening mark is an oversized watermark** — 300px `“` at 20% opacity, behind the
type, on every slide, sitting partially behind the opening lines. Because it sits behind
rather than above, it costs zero capacity.

**5. The episode line omits the show name.** `E{N} · {published title}` (+ `i/n`). The
lockup already says the show; the panel's free space goes to the episode's own title.
Title source is ADR-0009 — `apple_summary.md` wins.

**6. Rendering is local-only and deterministic.** `quote_cards/render_cards.py` on the
Mac. No model in the path (ADR-0010's lesson). No Phase-2 hook.

**7. Verification is editorial, not just structural.** Three runtime gates: the Apple
faces are proven to have resolved, the rendered PNG is pixel-sampled to prove the brand
chrome painted, and the laid-out quote is measured against the citation anchor. This is
REEL-05's lesson applied before shipping rather than after — *we verified reels were
well-formed videos and never that they were well-formed edits.*

## Alternatives considered

**Server-side rendering inside `publish_finalize`** — cards would generate automatically
alongside reels, with no remembered step. Rejected: American Typewriter Condensed and
Avenir Next are macOS system fonts and cannot be licensed onto the Hetzner box.
Substituting licensed webfonts would automate the pipeline at the cost of the specified
type. Brand consistency with the reels was judged worth more than the automation.

**Local CLI plus a Phase-2 "cards not rendered" alert** — offered and declined by John.
The residual risk is named here rather than argued: a forgotten card run is silent, and
silent misses are this pipeline's established failure class (E28 dead push, E29 no
trigger, E30 inbox matcher). If cards are missed twice, revisit.

**A type-size ladder keyed to quote length** — short quotes would fill their card more
evenly. Rejected: one size is the branding rule, and the ladder trades the rule for
symptom relief.

**Solid opening mark above the quote (150px / 104px)** — more emphatic as punctuation,
and correct typographically on the first slide only. Rejected on measured cost: to keep
the top anchor fixed it must reserve its space on every slide, which cost one to two
lines of capacity everywhere and turned E29's 291-character quote into a two-slide post
with a mostly empty second slide.

**4:5 canvas (1080×1350)** — more vertical feed space and typically more reach. Rejected
for now: a taller field changes every split, making it a second set to render and verify.
Reopen if reach data argues for it.

## Consequences

- Cards for a new episode require a deliberate local run; nothing in the pipeline
  reminds anyone. Accepted with eyes open (see above).
- A published title long enough to overflow the two-line panel **fails the render**
  rather than shrinking the type. The fix is a shorter title in `--title`, not a
  smaller font.
- The back catalogue can be rendered at any time; the format is deterministic, so
  re-running an episode reproduces byte-comparable cards.
- If the type size is ever changed, every card ever produced is stale. That is the
  reason it was proofed on real quotes before being locked.
