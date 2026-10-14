# REEL-07 — the gapless snap makes one frame per boundary paint two caption states

**Owner:** Jim (reel pipeline) · **Architecture:** Toby · **Tests:** Jen · **Ops:** Oliver · **PO:** Angela
**Priority:** MEDIUM — visible in published reels, roughly one frame per word boundary; cosmetic but
it reads as a glitch and is unreproducible by eye · **Status:** **PROPOSED** · **Created:** 2026-09-21

---

## How it surfaced

Not from this repo. The Tailor team ported `render_reel.js`'s caption block into its demo-screencast
harness — John's call: the Tailor product videos drop the planned voice-over in favour of on-screen
text narration, and this pipeline already solves that problem. Jen found the defect while verifying
the port against a 65-second render, by sweeping every frame rather than watching the video.

**Measured: 1 frame out of 1,948, at t = 16.400s**, where the caption read `A new` painted over
`A new section.`

## The defect

Two correct decisions combine into a wrong one.

`buildAccumulatingWordPages()` makes word transitions gapless within a page — each entry's `endSec`
is set to the next entry's `startSec`, which is what kills the blank-frame blink between words:

```js
// scripts/render_reel.js:709-712
const pageEndIdx = result.length;
for (let i = pageStartIdx; i < pageEndIdx - 1; i++) {
  result[i].endSec = result[i + 1].startSec;
}
```

`buildDrawtextFilter()` then gates each state with a comparison that is inclusive at **both** ends:

```js
// scripts/render_reel.js:768
`enable=gte(t\\,${page.startSec.toFixed(3)})*lte(t\\,${page.endSec.toFixed(3)})`
```

So at exactly `t == result[i].endSec == result[i+1].startSec`, both states satisfy their `enable`
expression. ffmpeg draws both. The accumulating word-by-word build means state *i* is a strict
prefix of state *i+1*, so the shorter text lands on top of the longer one: the same words at the
same size with a different centre, one frame of doubled, offset glyphs.

It is invisible when scrubbing and invisible on a casual watch — one frame at 30fps is 33ms — which
is exactly why it survived into published assets. Every word boundary in every reel has it.

## The fix

Make the end bound exclusive. The interval becomes half-open `[start, end)`, which is what the
gapless snap already assumes: state *i* is meant to hand over to state *i+1* at that instant, not
share it.

```js
`enable=gte(t\\,${...startSec...})*lt(t\\,${...endSec...})`
```

The only cost is that the **last** entry of a page — which keeps its natural `endSec` rather than a
snapped one (`:707-708`) — ends one frame earlier than before. That entry is followed by the
deliberate wipe gap, so the frame is text-free either way.

## What done looks like

- `lt` in place of `lte` on the end bound at `:768`.
- A test that would have caught it: for a set of word timings, assert that **no instant enables two
  states**, swept at the real frame rate across every boundary. `render_reel.js` exports nothing
  today, so the caption builders need an export block — additive, no behaviour change to the CLI.
- Re-render one reel and confirm the boundary frame is clean.

## Notes

The Tailor port carries the fix already (`~/jhc/tailor-demo/demo/lib/captions.mjs`), along with a
15-case test suite including the frame-rate sweep. This story exists so the source does not keep the
defect, and so the next person to read the snap block finds the reason the bound is half-open.
