# REMODEL-01 — what the 2026-10-14 remodel left undone, on purpose

**Status:** Proposed · **Created:** 2026-10-14 · **Raised by:** the remodel's own plan, and Toby and Orly's reviews
**Log:** `docs/carry-forward/2026-10-13-remodel.md` (subjects "deviations from the plan" and "reviews")

Each item is small and independent. None blocks the remodel's deploy.

| # | Item | Why it matters | Where it came from |
|---|---|---|---|
| 1 | TypedDicts for Quote, ScheduleEntry, JobPayload (`rockstar/types.py`) | quotes and schedule entries move as untyped dicts; the tests build them by hand | DX review, Bill #6 |
| 2 | print → logging in `publish_finalize` and `blog_publish` (11 prints) | the box's evidence is split between journald and stdout | DX review, Bill #3 |
| 3 | Rename test files from story ids to behaviour (`test_trig03_links_only.py` → what it proves) | a story id tells a new reader nothing; the functions are already named well | DX review, Jim #4 |
| 4 | `// @ts-check` + typedefs in `reels/render_reel.js` and `lookup_quote_timestamps.js` | 2,400 untyped lines; type checks at zero dependencies | DX review, Jim #1 |
| 5 | Tests on ordinary pytest fixtures instead of one process per file | `tests/_swap.py` keeps the per-file fakes honest; injected clients would let them go | plan item 6 |
| 6 | Wrap the tests' long lines (E501 is ignored in `tests/*`) | the gate holds everything else there | the ruff commit |
| 7 | A deploy that removes files dropped from git (or an archive step) | nothing deletes a module that left the repo, so dead code can pile up on the box | Toby's review |
| 8 | Fold the four ExecStart drop-ins into the unit files, archive the old flat modules: **by 2026-10-28** | the flat modules are a rollback that grows stale with every fix after the merge | Orly's review, runbook step 8 |
| 9 | `render_reel.js` is not deterministic: the same inputs give reels up to 0.33 s apart | reels can't be compared by bytes; a noise source for any eval that measures them | the parity harness |
| 10 | The worker polls SQLite every 10 s; the README says event-driven | V-6; don't overclaim | DX review, Bill #7 |
| 11 | Step 16's two branches: the reprocess chain is inline, the new-episode chain is `_auto_chain` | uneven; one function for both reads better | Toby's review |
