# OBS-02 — A failure the pipeline catches and carries on from is never recorded "ok"

**Owner:** Bill (pipeline) · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** High: every eval number and DASH-01 chart that reads step outcomes depends on it (PQ-5a) · **Status:** **Proposed** (Angela, 2026-10-12)
**Created:** 2026-10-12 · **Raised by:** eval round 1 (carry-forward 2026-10-12, impediment #3)
**Lane:** with CLIP-01a and OBS-03, its own PR.

## Why

E6's ledger says `12b quote_cards ok 16.9s`. The cards were not built: Chromium was installed for root and the worker
runs as `rockstar` (fixed by #44). The step reported the failure and the ledger dropped it:

1. `run_ledger.StepClock.start()` closes the running step as **`ok`** whenever the next step starts
   (`run_ledger.py:246–248`). A step is `ok` unless the job raised an error or something marked it otherwise.
2. `card_step.build_cards()` **never raises, by design** (CARD-05: alert and continue). It returns
   `"failed: the render stage failed"` (`jobs/card_step.py:147–161`).
3. The caller checks only for a skip (`jobs/publish_finalize.py:1260–1261`). A failure falls through, step 13 starts, and
   12b closes as `ok`.

The tool to record it already exists: step 10.5 calls `run_ledger.anomaly("no reels rendered")`
(`publish_finalize.py:1159`). 12b was never given the same call.

**It isn't only 12b.** Angela read every place in the two jobs where a failure is caught and the job carries on.
Five more close their step `ok` today:

| Step | Where | What fails | What the ledger says | Why it matters |
|---|---|---|---|---|
| 12b quote_cards | `publish_finalize.py:1252–1262` | the card build (any stage) | `ok` | E6, last night |
| **7a-0 forced_alignment** | `publish_finalize.py:998–1020` | the alignment | `ok` | **The reels are then framed on interpolated timing** (the REEL-05 defect class). The worst of the five: a degraded reel looks like a normal run in every chart. |
| 4 download | `publish_finalize.py:747–748` | listing a subfolder | `ok` (a warning in the log) | Files in that subfolder are silently missing from the run |
| 13 thank_you | `publish_finalize.py:1504–1506` | fingerprinting the draft | `ok` (a warning in the log) | A late publisher email will then leave the draft alone (TRIG-03), so its links stay pending |
| 14 calendar | `publish_finalize.py:1346–1348` | updating the event to LIVE | `ok` | Dormant while CAL-OFF holds; live again if the calendar comes back on |
| any step | `_alert()`, `publish_finalize.py:1677–1682` | **sending an alert** | nothing at all | An alert that never went out leaves no trace in the ledger. That's why we can't tell from the record whether E6's card alert reached John. |

Round 1 wraps its pipeline so any error becomes `PermanentFailure` (`round_1.py:210–222`). Angela found no
false-`ok` path there, apart from the alert send at `round_1.py:281–283`, which is the same case as `_alert()` above.

## Acceptance criteria

### A. The ledger has an outcome for "done, but not as asked"

1. **A degraded outcome a step can record before it ends.** A step that catches a failure and carries on calls
   `run_ledger.anomaly(reason)`, and that step closes as `anomaly`. If the call comes **before** the rest of the step's work
   (as in 7a-0, where the quote lookup runs after the fallback), the outcome is held until the step's real end, so its
   duration stays whole. *(Today's `anomaly()` closes the step on the spot. Bill chooses between holding it and a new name,
   such as `degraded()`; the test is what counts.)*
2. **Reasons are fixed strings from our code**, never data (the rule `run_ledger` already states). Each one names the
   stage: `cards not built: render`, `alignment failed: reels on interpolated timing`, `subfolder not listed`,
   `thank-you not fingerprinted`, `calendar not updated`.

### B. Every caught failure in the table is recorded

3. **12b:** `cards_status` starting with `failed` records `anomaly` with its stage. *Test:* a failing render stage gives
   `12b anomaly "cards not built: render"`. **It fails against today's code.**
4. **7a-0, 4, 13, 14:** each records its anomaly (7a-0 only until REEL-11 makes it a stop, Q2) at the catch shown in the table. One test each, each failing against
   today's code.
5. **A failed alert send is recorded.** `_alert()` (and Round 1's send) records a ledger event `alert_failed` with the
   subject's fixed prefix and the exception type, never the body. *Test:* the Gmail fake raises; the ledger has one
   `alert_failed`, and the job's outcome is unchanged.

### C. It stays fixed

6. **A guard test.** Jen adds a test that finds every `except` in `jobs/` whose block neither re-raises nor calls
   `run_ledger.anomaly`/`skipped`/the new outcome, and fails if one appears outside a short, commented allowlist (such as
   `_alert`'s own catch once AC 5 is in). This stops the next alert-and-continue step from reintroducing a false `ok`.
7. **`ledger_summary.py` prints anomalies** with their reasons, and its header counts them (`Steps — 40 (1 anomaly)`), so
   a reader sees one without scanning 40 rows.

### D. Proof

8. **Local gate green**, with the existing exclusions. The 2 `google.auth` failures are named, not counted.
9. **Live proof:** the next real Phase 2 run's ledger has no `ok` that the job's log contradicts. Jen checks every
   caught-failure line in the log against its step's outcome, and the check is a file in the run's OBS-03 statement.
   (If nothing degrades on that run, the tests carry the proof, and the story says so.)

## Out of scope (named so nobody loses them)

- **Correcting E6's old ledger line.** The ledger is append-only. The eval record (`WALKTHROUGH.md`) already notes that 12b
  failed, and that's E6's correction.
- **Changing alert-and-continue itself.** CARD-05's ruling stands: one failed step doesn't take down reels, blog and
  thank-you. This story makes the failure visible; it doesn't make it fatal.
- **The stop-and-raise paths** (gates, `PermanentFailure`): they already record their outcome.

## Open questions

| # | Question | Angela's recommendation | Answer |
|---|---|---|---|
| Q1 | Should a run with an anomaly still be marked `done`, and still drop `.pipeline_done`? | Yes. The job did finish. The anomaly shows in the ledger, the OBS-03 statement's top line, and the standup. Withholding `done` would re-run the job, and that's the expensive path. | **Yes** (John, 2026-10-12) |
| Q2 | Does an alignment failure (7a-0) stay alert-and-continue, given the reels it produces are known-defective? | Not this story's call. It's named here because OBS-02 makes it visible. If John wants it to stop Phase 2 instead, that's a REEL story. | **Yes: stop Phase 2** (John, 2026-10-12). Filed as **REEL-11**. Until it ships, OBS-02's 7a-0 anomaly (AC 4) is the interim record. When REEL-11 lands, 7a-0 leaves the anomaly list and becomes a stop path that records its own outcome, and AC 6's allowlist drops it. |
