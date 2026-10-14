# REEL-06 — The brand team's `reel_candidate` verdict is written and never read

**Owner:** Jim (reel pipeline) · **Tests:** Jen · **Architecture:** Toby · **PO:** Angela
**Priority:** Medium — we publish clips an editor explicitly excluded
**Status:** Ready · **Created:** 2026-07-21 · **Raised by:** Angela (found during REEL-05)

---

## Why

The Brand Team marks each quote with `reel_candidate`. On E28, Lisa set q05 to `false` with
her reasoning recorded in `lisa_note`:

> *"reel_candidate set to false: at current length this quote needs visual support that works
> better in blog/highlights context than as a standalone Reel clip. Recommend Heather review
> before any paid promotion use."*

**`reel_05_don-get-feeling.mp4` was rendered and published anyway.** E30 has the same shape —
q05 is `reel_candidate: false` and job 93 reported *"all 5 reels passed Instagram spec."*

The cause is one line in `render_reel.js:1032`:

```js
const reelsToRender = args.reel === 'all'
  ? quotesData.quotes          // ← every quote, unfiltered
  : quotesData.quotes.filter(q => String(q.index).padStart(2, '0') === args.reel.padStart(2, '0'));
```

`grep -rn "reel_candidate"` across `scripts/`, `jobs/` and `anthropic_client.py` returns **only
the two brand-team prompt files that write it.** There is no consumer anywhere. The same is
true of **`lisa_approved`** — also written by the prompts, also read by nothing.

So the pipeline asks its editor for a verdict on every quote, records that verdict in
`quotes.json`, ships the verdict to Drive, and then renders everything regardless. This is the
REEL-05 lesson in a different costume: **we verified reels were well-formed videos and never
that they were the reels the editor asked for.** Here we don't even ask — we discard the answer.

The consequence is editorial, not technical: a quote the editor judged unfit to stand alone
goes out as a standalone clip, on a channel where it may be promoted.

## Acceptance criteria

1. **A quote with `reel_candidate: false` is not rendered in an `--reel all` run.**
   *Testable:* fixture with 5 quotes, one `false` ⇒ 4 reels rendered, and the excluded index
   never appears in the output dir.

2. **Absent means render (back-compat).** Only an *explicit* `false` excludes. Older
   `quotes.json` files predate the field and must be unaffected.
   *Testable:* fixture with the field missing ⇒ rendered.

3. **Exclusions are reported, not silent.** Each skipped quote logs its index, topic and — when
   present — its `lisa_note`, so the reason the editor gave is visible in the run output. A
   filter that quietly drops work is how we got here.
   *Testable:* the log line naming the excluded index is asserted.

4. **An explicit `--reel NN` still renders that reel, with a warning.** A human naming a
   specific reel is overriding deliberately; the tool should obey and say so. The flag governs
   the *automated* selection, not a human's explicit instruction.
   *Testable:* `--reel 05` on a `reel_candidate: false` quote renders and warns.

5. **The post-filter count is checked against `minReelCount`.** The existing range check
   (`render_reel.js:321`) runs on the *loaded* quotes, before filtering — so an episode where
   the editor excluded three quotes could silently produce two reels. Warn after filtering, on
   the number actually being rendered.
   *Testable:* fixture where filtering drops below `minReelCount` ⇒ warning emitted.

6. **`lisa_approved` is resolved, not left dangling.** It is also written-and-never-read. Either
   give it a consumer or delete it from the prompts — a field the editor is asked to fill and
   nothing consumes is a lie about how the system works (DOD-4, PQ-5). **Deliberately NOT
   auto-wired in this story:** unlike `reel_candidate`, we have no observed case of it being
   `false`, and inferring a veto from an unexercised field risks dropping reels for the wrong
   reason. Decision goes to John and Toby; this story only names it.

## Notes

- Scope is **selection**, not framing. Do not touch boundary logic (ADR-0003) or excision
  (ADR-0004).
- Expected effect: E28 and E30 both render **4** reels instead of 5 — inside the
  `minReelCount: 3 … maxReelCount: 5` range, so no range warning.
- **E30 is affected right now.** Job 94 rendered 5 reels including the excluded q05. Whether to
  re-run E30 after this fix, or to delete `reel_05` from its Drive folder by hand, is John's
  call — the other four reels are unaffected either way.
- Related: REEL-05 (found it), and the same family as DEPLOY-01 / R1-01 — *a signal exists, and
  nothing consumes it.*
