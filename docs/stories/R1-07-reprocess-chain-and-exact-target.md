# R1-07 — Several old episodes dropped at once all get processed; the target is matched by number

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** High (back catalogue) · **Status:** Built 2026-10-01

**Found by** John dropping `E6-Avery.m4a` and `E13-heather-john.m4a` together (2026-10-01, job 130).

1. **Strand.** A Drive drain enqueues ONE Round 1 however many files land, and step 16's chain skipped
   reprocess mode. E13 would have sat in the inbox until an unrelated Drive change. Now a re-run that finishes
   with other old-episode audio still in the inbox enqueues the next Round 1 itself.
2. **Prefix match.** The target was found with `startswith("E1")`, which also matches `E13-…` (and target E30
   matched `E301-x.m4a`). It is now matched by `episode_files.episode_number(name) == ep_num`.

**Tests** (`test_r1_01_intake.py`, now 14 cases): `test_reprocess_chains_to_the_next_old_episode` (E6 then E13;
E6 lands as S1E6) and `test_target_matches_by_number_not_prefix`. **Red on `main`, 2 of 14; green 14/14.**

**Deploy:** after job 130 (E6) finishes. Then enqueue E13's Round 1 once by hand, because job 130 runs the old
code and can't chain to it.
