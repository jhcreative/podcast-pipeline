# ADR 0003 — Reel clip boundaries frame a complete thought

- **Status:** Accepted — **AMENDED 2026-07-21: this ADR was INERT IN PRODUCTION from its
  acceptance date until 2026-07-21.** The implementation landed and was tested, but the
  server path could not reach it: `publish_finalize` never passed `--aligned`, and
  `scripts/align_episode.py` — which produces the word-aligned transcript the whole
  code path is gated on — was absent from `scripts/deploy.sh` FILES and therefore did
  not exist on the server at all. Anything rendered before 2026-07-21 (through E29) was
  framed by the pre-ADR code. Fixed and verified in production by REEL-05 (`16a464d`);
  see DEPLOY-01 for why a wrong manifest was indistinguishable from a right one.
- **Date:** 2026-06-18
- **Deciders:** John (owner, editorial calibration); Angela facilitating; Jim (impl), Toby (approach), Jen (tests)
- **Supersedes the assumptions baked into:** `scripts/lookup_quote_timestamps.js` (`findTimestamps`, `buildTimeline`) and the fixed lead-in/tail in `scripts/render_reel.js`

## Context

Reviewing E27's reels, John flagged that the audio clips don't land on whole
thoughts:

- **Reels 1 & 4** cut the speaker off *before* the final thought resolves (truncated tail).
- **Reel 1** also *opens* on the tail of the previous sentence (mangled in-point).
- **Reel 2** runs *past* the resolution into the next breath (trailing tail).

Root cause is that the clipper approximates boundaries three ways over:

1. **Interpolated timing.** `buildTimeline` (`lookup_quote_timestamps.js:178`) spreads a
   WhisperX *segment's* duration evenly across its words (`perWord = dur / words.length`) —
   a word's timestamp is a guess within the segment, not its real onset.
2. **The end is *counted*, not *found*.** `findTimestamps` locates where the quote starts,
   then walks forward exactly `baseLen` words (`endI = bestI + baseLen - 1`, line 229) and
   stops. It never searches for the quote's actual last word.
3. **Blind padding.** `render_reel.js` adds a flat 1.5s lead-in / 0.5s tail regardless of
   what audio sits there.

The brand team *cleans* quotes — removes host interjections, fillers, and transcription
errors — so the editorial `text` no longer word-aligns to the raw transcript. When the
spoken span has *more* words than the cleaned text, the forward count undershoots and the
thought is guillotined (reels 1, 4). When it has *fewer* (e.g. a removed host interjection,
per q02's `lisa_note`), the count overshoots and the clip trails (reel 2). The lead-in then
drags in whatever preceded the matched start (reel 1's open).

The buffers made this good-enough in the early reels; at the current quality bar it reads as
editorial roughness.

## Decision

**Editorial standard — complete-thought framing.** Every reel opens on the first word of a
complete thought and closes on the last word of a complete thought. It *resolves*: it never
truncates a clause, and never trails past the landing into the next thought.

**Tie-break (John):** preserve the full thought, accept a tiny tail — *complete over crisp*.
At an ambiguous final boundary, round **outward** (keep the whole final word/sentence plus a
small natural tail), never inward. A few hundred ms of breath after the resolution is
acceptable; clipping mid-clause is not. Trailing into the *next* thought is also not.

**Mechanics — stop counting, start finding** (Jim's algorithm, Toby reviews the approach):

- **Out-point** anchors to the *located* end of the intended thought's last significant word
  — found by aligning the tail of `text` against the timeline, not `bestI + baseLen`.
- **In-point** anchors to the located start of the first significant word; the lead-in is
  **clamped** so the clip can never begin before the preceding sentence/word boundary (no
  prior-thought bleed).
- **Trailing guard:** the out-point never extends past the onset of the next thought after
  the intended final word.
- **Interjection tolerance:** a host interjection cleaned out of `text` (present in the raw
  span) must not corrupt the end boundary — the end still lands on the guest's intended final
  word. (Note: the *clip audio* may still contain the interjection; physically excising
  mid-quote speech is a separate, harder change and is out of scope here — flag, don't solve.)
- Boundaries should derive from the sharpest available word timing; interpolated
  segment-averaged timing is the current floor and a known source of error.

**Tests are the contract.** Jen's fixtures encode the failure matrix: cleaned-shorter-than-
spoken (undershoot), cleaned-longer-than-spoken / interjection (overshoot), and prior-thought
bleed at the start. E27 q01/q02/q04 are the named proof cases.

## Consequences

- **+** Reels land on complete thoughts by construction, every episode — not by per-episode
  hand-tuning. The standard is the searchable answer to "why does the clip start/end here."
- **+** E27's three flagged reels are fixed by the same change that fixes the class.
- **−** Mid-quote interjection audio still rides along until a multi-segment cut capability is
  built (separate ticket). Reel 2's *end* is corrected; its *middle* may still carry the
  host's words.
- **−** Boundary-finding against cleaned text is inherently fuzzy; the outward-rounding
  tie-break is the deliberate safety margin.
- **Follow-up (not in this change):** the brand team emitting explicit per-quote signals
  (`speaker_role`, and optionally first/last-sentence anchors) would remove the remaining
  guesswork — tracked with the speaker_role follow-up.
