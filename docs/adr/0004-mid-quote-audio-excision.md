# ADR 0004 — Mid-quote audio excision (cut cleaned-out speech from the clip)

- **Status:** Accepted — **AMENDED 2026-07-21: this ADR was INERT IN PRODUCTION from its
  acceptance date until 2026-07-21.** The implementation landed and was tested, but the
  server path could not reach it: `publish_finalize` never passed `--aligned`, and
  `scripts/align_episode.py` — which produces the word-aligned transcript the whole
  code path is gated on — was absent from `scripts/deploy.sh` FILES and therefore did
  not exist on the server at all. Anything rendered before 2026-07-21 (through E29) was
  framed by the pre-ADR code. Fixed and verified in production by REEL-05 (`16a464d`);
  see DEPLOY-01 for why a wrong manifest was indistinguishable from a right one.
- **Date:** 2026-06-23
- **Deciders:** John (owner); Angela facilitating; Jim (impl), Toby (assembly-contract review), Jen (tests)
- **Builds on:** ADR 0003 (complete-thought framing) — this delivers the "mid-quote excision" capability 0003 explicitly deferred.

## Context

ADR 0003 made reel clips frame a complete thought and made the karaoke reveal the
brand-team's *cleaned* quote text on real word timing. But the audio clip is a single
continuous cut `[start, end]`, so any speech the brand team **cleaned out of the text but
that still sits inside that span** keeps playing with no caption to show. `smoothCadence`
(ADR 0003 follow-up) bridges *small* cleaned-out gaps by easing the next word in early, but
it deliberately refuses to bridge large ones (capped at 2.5s lead) because floating a caption
6–10s ahead of its voice is a worse lie than a pause.

Two large residuals remain on E27:
- **q02 ~6.2s** — the *host's interjection* ("…Heather were so young…"). Host speech in a
  guest reel, uncaptioned.
- **q04 ~10.5s** — a guest clause Lisa cut as "ambient, not payoff," still spoken in the audio.

These are not caption bugs; they are cleaned-out speech still in the audio. The honest fix is
to remove that audio.

## Decision

Excise large cleaned-out spans from the clip audio and re-stitch, so the audio matches the
captioned thought.

- **What gets excised:** contiguous runs of raw (cleaned-out) words inside a quote's
  `[start, end]` whose duration exceeds a threshold (**default ~2.0s**). This catches the
  host interjection and dropped clauses. **Small fillers/repeats stay** — removing every
  "um/like" makes speech sound clipped and unnatural, and `smoothCadence` already handles
  their captions. Threshold is a named, tunable constant.
- **Seam (Toby to review):** the intelligence lives in `lookup_quote_timestamps.js`, which
  already owns the cleaned-vs-raw alignment. Per quote it emits (1) `audio_segments` — the
  ordered **kept** sub-spans in original episode time — and (2) `words[]` already remapped to
  the **post-excision continuous local timeline**. `render_reel.js` stays dumb: it builds the
  clip audio by concatenating the given segments (with a short click-free crossfade) instead
  of one `-ss/-to` cut, then proceeds unchanged — captions from the remapped `words[]`, music
  bed mixed over the **probed** assembled duration, beat-snapped closing recomputed.
- **Invariants:** the outer complete-thought boundaries (ADR 0003) are unchanged — excision
  only removes *interior* spans. No clicks/pops at stitch points (crossfade ~20–40ms or
  silence/zero-crossing-aligned cuts). Clip durations shrink (expected, tighter is better);
  `verify_reel_spec.js` must still pass.

## Consequences

- **+** Both residual gaps vanish; captions are continuous against the audio.
- **+** The host's voice is removed from guest reels (independently desirable).
- **+** Reels get tighter/punchier; less dead weight.
- **+** Resolves the cleaned-text-over-uncleaned-audio mismatch at its source, not by caption
  trickery.
- **−** Each stitch is a micro-discontinuity in natural speech; mitigated by crossfade and by
  the threshold (only big structural cuts, never micro-fillers).
- **−** Highest-risk change in this line of work: stitch artifacts and sync drift must be
  verified by ear and by frame, not assumed.
- **Open:** if a future quote's *only* clean rendering would require excising so much that the
  remaining audio sounds disjointed, that's a signal to re-pick the quote (brand-team
  selection), not to excise harder.
