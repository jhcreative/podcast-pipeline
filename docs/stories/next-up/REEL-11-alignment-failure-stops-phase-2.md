# REEL-11 — A forced-alignment failure stops Phase 2 instead of making reels on guessed timing

**Owner:** Bill (pipeline) · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** Medium-high: nothing has failed this way on record, but when it does the defect ships quietly · **Status:** **Proposed** (Angela, 2026-10-12)
**Created:** 2026-10-12 · **Raised by:** OBS-02 Q2. John: **yes**, alignment failure stops Phase 2.
**Lane:** after OBS-02. It touches the same lines (`publish_finalize.py:998–1020`).

## Why

When `align_episode` fails, step 7a-0 catches it, alerts, writes a standup warning and carries on. Reel boundaries then come
from interpolated segment timing. Those are the REEL-05 defects (truncated or trailing clip ends) with no ADR-0004
excision. The code's own standup line says *"Re-run with alignment before publishing the reels."* The reels it then makes
are known to be defective, and they still go to Drive for Heather to post.

John's ruling, 2026-10-12: a Phase 2 that can't align stops. It doesn't produce reels we already know are wrong.

## Acceptance criteria

1. **Alignment failure is a stop.** In 7a-0, a failed `align_episode` (non-zero exit, or success with no output file)
   raises `PermanentFailure`. The interpolated-timing fallback is removed. Nothing after step 7a-0 runs: no clips, no reels,
   no cards, no thank-you draft.
2. **One alert that names the fix.** The subject is `E<N>: forced alignment failed — Phase 2 stopped, no reels made`.
   The body gives the cause (the last stderr lines) and the re-run command
   (`tools/reprocess_episode.py --episode N`). It replaces today's "reels on degraded timing" alert, which goes.
3. **What's already done stays.** The FAQ and the blog **draft** (step 6) are already written when 7a-0 runs. They
   stay as drafts, and the alert says they exist, so nobody thinks Phase 2 did nothing.
4. **An OOM in alignment is the same stop**, and CLIP-01a's no-retry rule applies. (Alignment ran 280–388 s per attempt
   last night without failing, so this is defence, not a live case.)
5. **OBS-02 follows it.** 7a-0 leaves OBS-02's anomaly list. The step records `permanent_failure` through the existing
   end-of-job path, and OBS-02's guard-test allowlist drops it.
6. **Tests:** the alignment fake fails, so the job ends `PermanentFailure` at 7a-0, with exactly one alert, no
   `extract_clips` call and no reel upload. It fails against today's code. A second test: alignment "succeeds" with
   no output file, giving the same result.
7. **Local gate green**, with the existing exclusions. There's no live proof: the next real run's 7a-0 `ok` shows the happy path
   is unchanged, and the tests carry the stop.

## Out of scope

- Making alignment itself more robust (retrying a transient fault, a smaller model). If it starts failing in practice,
  that's its own story, with the evidence.
- A `reels_only` reprocess without alignment. Today there's no such path, and this story doesn't add one.
