# REEL-05 — E28's two tail defects, and the reason ADR-0003/0004 never fixed them

**Owner:** Jim (reel pipeline) · **Architecture:** Toby · **Tests:** Jen · **Ops:** Oliver · **PO:** Angela
**Priority:** HIGH — the defect is visible in published assets and the governing ADRs are inert
**Status:** **DONE 2026-07-21.** Parts A, B and C complete. Two corrected E28 reels
reviewed and approved by John ("🔥"). Code shipped `16a464d` + `157497c`, deployed and
verified in production. · **Created:** 2026-07-21

---

## What John reported

- **E28 reel 1** — clip crops abruptly at the end while the guest is still making the point.
- **E28 reel 2** — the opposite: the guest finishes the thought, we don't stop in the gap,
  a phantom **"And…"** from the *next* thought appears on screen, and *then* the reel cuts.

Goal: two corrected reels, **and** the learnings folded back into the reel rules so this
class stops recurring.

## The finding that reframes this story

Both defects trace to one structural fact: **the ADR-0003 (complete-thought framing) and
ADR-0004 (mid-quote excision) implementations are unreachable on the server path that
renders our reels.** They are correct code that production never executes. Two independent
gates:

**Gate A — no word-aligned transcript exists on the server.**
`jobs/publish_finalize.py:744` invokes the boundary finder with `--transcript
transcript_fast.json` and never `--aligned`. `grep -n "aligned" jobs/publish_finalize.py`
returns zero hits. `scripts/align_episode.py` — the script that *produces*
`transcript_aligned.json` — **is not in `scripts/deploy.sh`'s FILES list, so it does not
exist on the server at all.** With `usingAligned === false`, the entire
`reconcileCleanedWords → findExciseSpans → smoothCadence` block is skipped
(`lookup_quote_timestamps.js:1055`), `words[]` is never built from cleaned text,
`audio_segments` is never emitted, and ADR-0004's excision never runs.

**Gate B — `words[]` is then overwritten with RAW words.**
`publish_finalize.py:793` runs `inject_word_timestamps.js`, which does
`quote.words = filtered` from per-clip WhisperX output. **That script is also missing from
`deploy.sh` FILES** while being invoked by deployed code — the server copy is unmanaged.

**Corollary specific to E28:** a reprocess downloads the existing Drive `quotes.json`,
which already carries timestamps, and `lookup_quote_timestamps.js:1031` skips any quote
that has them. **E28's clip boundaries were never recomputed by the ADR-0003 finder.** A
bare `reprocess_episode.py --episode 28 --reels-only` would reproduce both defects exactly.

This is a **process failure, not just a code defect**, and it is the important part of this
story. We ran the full gate — spec, tests, Toby's review, deploy — on work that could not
execute. See DEPLOY-01.

## Root cause — defect 2 (the phantom "And")

Two causes compound. `smoothCadence` is **ruled out** (it never runs on this path, and it
rewrites timings without ever adding a token).

**Primary — an off-by-one at the tail.** `inject_word_timestamps.js:112-117`:

```js
const windowStart = quote.transcript_start_ms - 100;
const windowEnd   = quote.transcript_end_ms   + 500;
const filtered = absoluteWords.filter(w => w.start_ms >= windowStart && w.end_ms <= windowEnd);
quote.words = filtered;
```

The comment says "100ms slop"; the **tail slop is 500 ms** — five times the head — and it
is a *word-inclusion* filter, not a time clamp. `end_ms <= windowEnd` admits any
next-thought word short enough to *finish* within 500 ms. That is a precise fingerprint
for monosyllabic conjunctions — **"And", "So", "But", "Then"** — and excludes longer words,
which is exactly why this reads as rare and word-specific rather than as systematic
overshoot. `extract_quote_clips.sh:62` cuts 1000 ms past the end, so the next word is
always available to be transcribed and admitted.

**Enabling co-factor — unguarded render tail.** `render_reel.js:65` adds a flat
`tailMs: 500` with no guard. ADR-0003's trailing guard clamps `transcript_end_ms` to the
next word's onset (`lookup_quote_timestamps.js:474-479`) and render then adds 500 ms *past
that same onset*. Worse: on the interpolated timeline the server actually uses,
`timeline[i].end === timeline[i+1].start` by construction, so the guard's `endMs > guardMs`
test **can never be true — it is a structural no-op in production.**

**This is already shipped and was never reported.** `Episodes/E23-red-light-blue/quotes.json`
q03 ends at 1411000 ms and admits the word **`"So"`** at +331 ms. Three E24 quotes show the
same shape. E28 is not the first occurrence — it is the first one anybody noticed.

*Confidence: HIGH on mechanism (verified in code, reproduced in three committed artifacts on
the identical path). MEDIUM-HIGH on E28's specific instance — E28's `quotes.json` is not in
this repo and must be pulled from Drive to confirm the literal `"And"` and its onset.*

## Root cause — defect 1 (truncated tail)

1. **The ADR-0003 finder never ran for E28** (`lookup_quote_timestamps.js:1031` skip).
2. **The timeline is the interpolated floor** — `buildTimeline` spreads `perWord = dur /
   words.length` across a segment; the source's own comment calls these "±0.5–1s guesses."
   A 500 ms flat tail cannot cover a >500 ms interpolation undershoot.
3. **The counting fallback is still live.** `lookup_quote_timestamps.js:441-444` restores
   `endIdx = bestI + baseLen - 1` — the exact pre-ADR-0003 undershoot formula — whenever the
   4-word tail anchor misses. Against interpolated, punctuation-stripped text, anchor misses
   are likely on any heavily-cleaned quote, which is precisely the case ADR-0003 targeted.
4. **The trailing guard is one-sided** — it can only shorten, never lengthen. It cannot
   rescue an undershoot.

## The gap that let both ship

**Nothing anywhere asserts that the last rendered caption word equals the last word of the
cleaned quote text.** No comparison of caption end to audio end. `verify_reel_spec.js`
asserts 19 things — codec, pixel format, color primaries, dimensions, frame rate, sample
rate, faststart, 5–120 s duration — **all container and encoding, zero editorial.** A reel
with a phantom word or a guillotined clause passes every gate we have.

The tests are blind by construction: `test_caption_reconcile.js` only exercises the aligned
path, which *cannot* produce a phantom word and *does not run on the server*.
`inject_word_timestamps.js` — the actual source of the defect — **has no test file at all.**

## Acceptance criteria

**Part A — the two reels (John's immediate ask)**

1. E28's assets are pulled from Drive (`quotes.json`, `E28-<Firstname>.m4a`,
   `E28-<firstname>-reel.jpg`) and the defect is **reproduced locally and confirmed by eye**
   before any fix — including reading E28's actual `words[]` tail to confirm the literal
   `"And"` and its measured onset. *No fix is written against an inferred defect.*
2. Two corrected reels are produced and **John reviews them before anything reaches Drive.**
   Complete-thought framing per ADR-0003: reel 1 resolves its clause; reel 2 stops in the
   gap with no next-thought word visible **or audible** (ADR-0003 forbids trailing into the
   next thought — check by ear, not just by frame).
3. Which reel indices correspond to John's "reel 1" and "reel 2" is **confirmed with John**,
   not guessed — filenames embed `topic_slug`, not review order.
4. Replacing the published reels in Drive is a **separate, explicit go from John.**

**Part B — the durable fix**

5. **The tail slop stops admitting next-thought words.** Either clamp `words[]` to the
   quote's cleaned token count, or make the tail filter a time clamp rather than a
   word-inclusion filter. Head and tail slop asymmetry (100 vs 500) is justified in a
   comment or removed.
6. **The render tail is guarded.** `transcript_end_ms + tailMs` may never cross the onset of
   the next spoken word. The guard must work on the *interpolated* timeline — the current one
   is a no-op there, which is how it passed review.
7. **One source of truth for lead-in/tail.** The constant is currently duplicated in four
   places with **two different tail values** (500 ms in `render_reel.js`/`lookup`, 1000 ms in
   `extract_quote_clips.sh`), coupled only by a comment saying "keep these in step." PQ-4.
8. **A content-level invariant is asserted and gated.** `verify_reel_spec.js` (or a sibling)
   asserts: last caption word == last word of cleaned text; no caption begins after the
   audio's last spoken word; caption end ≤ clip end. **This is the learning John asked for**
   — the reason two generations of this defect shipped is that we verify containers and not
   content. A reel failing this gate does not ship.
9. **`inject_word_timestamps.js` gets a test file.** It has none and it caused this.
10. **The counting fallback is made loud.** When `endIdx` falls back to `bestI + baseLen - 1`,
    that quote is flagged for review rather than silently rendered — it is the known
    undershoot path (PQ-5).

**Part C — record it**

11. ADR-0003 and ADR-0004 get a **status amendment** recording that they were inert in
    production from their acceptance dates until this fix, and why. Anyone reading them
    today would reasonably believe reels have framed complete thoughts since 2026-06-18.
    They haven't. Leaving that uncorrected is a PQ-5 problem in our own record.

## Notes

- `--reel NN` (`render_reel.js:126`) is the only per-reel selector; `reprocess_episode.py
  --reels-only` is **all-or-nothing** and never passes it.
- `--dry-run` prints the caption page table without encoding — **the phantom word is visible
  there before spending any encode time.** Use it for the reproduction step.
- Do **not** use `rerun_brand_team.py` — it regenerates quote *selection* through the Claude
  chain and would replace John's approved E28 quotes.
- A hand-edit of `quotes.json` is legitimate under ADR-0008 and survives reprocess (because
  of the `:1031` skip) — acceptable as the Part A shortcut, **never** as the Part B fix.
- `BrandAssets/audio/opener.m4a` is absent locally; `render_reel.js:363` silently
  synthesizes an 80 Hz hum in its place. Confirm before judging a local render by ear.
- E30's reels (E30-01) will carry these same defects until Part B lands.

---

## Completion note — 2026-07-21

**Part A.** Reproduced on the box against E28's real assets, not locally and not inferred.
One finding corrected the original analysis: E28's Drive `quotes.json` carries
`transcript_start_ms/end_ms = 0` on all five quotes and no `words[]`, so the
`lookup_quote_timestamps.js:1031` skip did **not** apply and the ADR-0003 finder *did* run.
Defect 1's cause is therefore causes 2 and 3 (interpolated timeline + live counting
fallback), not the skip. Reproduce-before-fixing is what caught that.

Rendered with forced alignment for the first time:

```
timeline source = word-aligned (6919 words, wav2vec2-en-default)
q1 leadIn=781ms   q2 leadIn=20ms   q3 leadIn=80ms   q4 leadIn=20ms   q5 leadIn=100ms
q2 ↳ EXCISE 1 span(s), 4.3s removed → 2 audio segment(s)
```

Every one of those lead-ins was structurally pinned at 0 before. q02's excision removed
4.3s of cleaned-out speech ("so you know that s the good news you know is that your skull
is remodeling and") that had been playing with no caption over it.

Caption tails now match the cleaned text exactly — `…there's a fragility fracture.` and
`…going to get healthy.` No phantom word. **John reviewed and approved both ("🔥").**

**Part B.** Shipped in `16a464d`: alignment wired into `publish_finalize`, Gate B closed via
`words_source: 'aligned'`, and the phantom word fixed structurally — caption tokens now come
from the cleaned text through the LCS reconciler, so a cleaned-out or next-thought word
cannot reach the screen regardless of window tuning. New `test_inject_word_timestamps.js`
(13 checks) covers the phantom next-word, cleaned-out filler, Gate B preservation,
late-landing tail, token fidelity and monotonic timing — the file had no tests and caused
the defect.

`157497c` additionally replaced the opening/closing 80 Hz structural tone with true silence.
The theme music was always the real audio layer; because the mix is
`amix=inputs=2:normalize=0` the tone was summed *under* it, putting unasked-for low-end in
every reel.

**Criterion 4 — NOT done, deliberately.** The published `reels/` folder still holds the
original defective MP4s. The corrected pair lives in
`reels-REVIEW-2026-07-21-reel05-fix/` alongside it. John closed E28 without ordering the
swap; replacing published social assets stays an explicit, separate decision. Re-rendering
and replacing is one `reprocess_episode.py --episode 28 --reels-only` run whenever he wants
it — and it would now produce *correct* reels, where before today the same command would
have faithfully reproduced both defects.

**Part C.** DONE — ADR-0003 and ADR-0004 now carry a status amendment recording that they were
inert in production from acceptance until 2026-07-21, and why.
