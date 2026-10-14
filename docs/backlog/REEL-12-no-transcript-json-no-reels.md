# REEL-12 — Without transcript_fast.json, Phase 2 makes no reels on zero timestamps

**Owner:** Bill · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** Medium: no failure on record, but it ships the same defect as REEL-11, unseen · **Status:** **Proposed** (Angela, 2026-10-12)
**Created:** 2026-10-12 · **Raised by:** Toby, in the REEL-11 cold review (finding 5)

## Why

REEL-11 made a failed forced alignment stop Phase 2: reels on guessed timing are the REEL-05 defects. One path
still skips the question entirely. When `transcript_fast.json` isn't in the episode's Drive folder, step 7a logs a
warning, records `skipped`, and skips both the quote lookup and 7a-0 (`jobs/publish_finalize.py`, the `else`
branch after `_force_align`). Step 7 then cuts clips for quotes whose timestamps are still 0, and
`extract_quote_clips.sh` only warns. The result is reels with no real timing at all, which is arguably worse than
the interpolated timing REEL-11 refused.

## Acceptance criteria

1. **A missing `transcript_fast.json` stops Phase 2 before step 7**, the same way REEL-11 does: one standup line,
   one alert naming the missing file and the re-run command (`_rerun_command`, the same mode and links), then
   `PermanentFailure`. In a full run the alert says the FAQ and blog draft are kept as drafts; in a reels-only run it
   doesn't.
2. **Exception, if John wants one (Q1):** quotes that already carry Phase 1 timestamps (CLIP-01c AC 6) don't need the
   lookup. With every quote timed, Phase 2 may skip 7a and still run 7a-0 for word timing, if alignment can run from
   the timed quotes alone. Bill says whether it can.
3. **Tests:** a folder without `transcript_fast.json` ends `PermanentFailure` at 7a, with one alert and no
   `extract_quote_clips` call (drive `_produce_content` as REEL-11's end-to-end test does). It fails against today's code.
4. **Local gate green**, with the existing exclusions.

## Open questions

| # | Question | Angela's recommendation | Answer |
|---|---|---|---|
| Q1 | Once CLIP-01c puts timestamps in quotes.json in Phase 1, may a run without `transcript_fast.json` go on? | Not in this story. Stop now; revisit when CLIP-01c lands. | |
