# REEL-10 — Zero reels after a render must stop Phase 2, not replace the Drive reels with an empty folder

**Owner:** Bill · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — Angela, 2026-10-10, found in the OBS-01 re-review (Jen)
**Created:** 2026-10-10

## Why

In `jobs/publish_finalize.py`, if Step 10 renders **zero** reels for an episode that has quotes, nothing stops the run:
- Step 10.5 has nothing to check;
- Step 12 then **deletes the existing Drive `reels/` folder and creates an empty one**.

So a reprocess that fails to render can wipe reels that were good. The comment around Step 12 promises the
opposite. OBS-01 now records this case as an `anomaly` (not a skip), so it's visible in the ledger. It is still
not prevented.

## Acceptance criteria

1. Quotes are present and zero reels rendered → `PermanentFailure` before Step 12, with an alert naming the
   episode. The Drive `reels/` folder is untouched. *Test:* a fake render returning nothing; Drive's
   delete and create are never called.
2. A `reels_only` reprocess with zero renders behaves the same way.
3. No quotes, so nothing to render: unchanged.
