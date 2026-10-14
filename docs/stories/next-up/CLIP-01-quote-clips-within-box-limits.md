# CLIP-01 — Phase 1 picks only quotes the box can transcribe; a dead job says so once

**Owner:** Bill (pipeline), Oliver (box measurement) · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** High: blocks the E13 re-run, and E34 runs into the same limit · **Status:** **Proposed** (Angela, 2026-10-12)
**Created:** 2026-10-12 · **Raised by:** John, after eval round 1 (carry-forward 2026-10-12, impediments #1 and #2)

## Split into three (John: *"I'm good with splitting the work"*, 2026-10-12)

Each AC keeps its number below, so nothing is reworded or lost. Each part ships as its own PR on John's go (OPS-5)
and passes its own local gate (AC 14 applies to all three).

| Part | ACs | What | Depends on | Owner |
|---|---|---|---|---|
| **CLIP-01a: a dead job says so once** | 12, 13, 14 | An OOM fails once with its reason, read from evidence. Exhausted retries send one alert. | Nothing. **Built; Toby approved with conditions, met (2026-10-12).** | Bill · Jen · Toby |
| **CLIP-01b: measure the box's limit** | 1, 2, 3, 14 | `measure_clip_limit.sh` → `box-limits.env` with its evidence. The batch size is chosen by measurement. No file, no run, one alert. Q2 and Q4 close here. | In progress: the Mac repeat runs (AC 2). | Oliver · Jen |
| **CLIP-01c: enforce the ceiling, Phase 1 and Phase 2** | 4–11, 14, 15, 16 | `min(editorial max, CLIP_MAX_SECONDS)`. Lisa is told the ceiling, the quotes are measured with the shared matcher, then one trim-or-swap pass and a drop. Phase 2 checks the limit before spending money. Live proof is the E13 re-run. | 01b's limits file on the box | Bill · Jen · Toby · Lisa |

- **Mac vs box.** The Mac's numbers pick a batch size to *try*. Only the run on the box sets `CLIP_MAX_SECONDS`.
- **Order.** 01a and 01b run in parallel. 01c starts once 01b's file exists on the box.
- **E34.** 01a makes a failure loud and cheap, but only 01c makes E34 succeed. It's John's call whether to wait for 01c or to run E34 once 01a is live, knowing the risk.
- **Sibling, not folded in:** impediment #3 (the ledger says `quote_cards` "ok" when the cards failed) becomes **OBS-02**. It's a small story in 01a's lane with its own PR.

## Why

E13 failed Phase 2 five times, 03:17 → 06:36 on 2026-10-12, and every attempt failed the same way:
- **Where:** step 8 `transcribe_clips.sh` (WhisperX large-v3, `--batch_size 8`), each time on quote 3, a **174.5 s** clip.
- **How:** the kernel OOM killer stepped in at a peak of 3.4 GB memory plus 1.7 GB swap. systemd restarted the worker.
- **The clips:** E13's run 28, 80, 175, 107 and 27 s. The script's header assumes "~30-60s" per clip, and every E6 clip was under 30 s.
- **The cause, measured (AC 2):** WhisperX splits audio into chunks of up to 30 s and transcribes up to `batch_size` chunks together. At batch 8, memory grows by about 31 MB per second of clip. At batch 1 or 2, it stays flat at the models' fixed cost, about 2.6–3.2 GB. Round 1's `transcribe_fast.sh` survives full episodes only because it uses `base`.

What made it worse:
- Each retry was certain to fail, and each rewrote the FAQ and the draft first: five FAQs, $0.32.
- When the retries ran out, nobody was told. Gmail held nothing for E13 except five "blog draft ready" mails.

John's rulings, 2026-10-12:
- *"we shoudl fix the clip json code in Phase 1 to respect limts set by box hardware"*
- **90 s is the absolute maximum** (a finished reel), *"which is fine, but lets make sure that agrees with hardware"*. Measured: at batch 8 it doesn't (see AC 2); at batch 2, length stops mattering and the fixed cost decides.
- Scope: memory-safe transcription **and** the cap, an alert on exhausted retries, and no retry of an OOM.

The principle: **the box's measured limit is a Phase 1 input.** Phase 1 doesn't approve a quote unless the box can transcribe it.
Phase 2 still guards the limit (quotes.json can be hand-edited), but it should never be the place we find out.

## Acceptance criteria

### A. The box's limit is measured, not guessed

1. **The measurement script.** `scripts/measure_clip_limit.sh` runs on the box as the worker's user.
   - It transcribes speech clips of 30, 60, 90, 120 and 180 s using exactly `transcribe_clips.sh`'s settings (model, compute type, batch size).
   - It records the peak RSS for each one.
   - It writes `CLIP_MAX_SECONDS` to a box-side limits file, with its evidence: date, total RAM, swap, settings, and peak RSS per length.
   - The limit is the longest length whose peak stays under a stated headroom of the box's RAM (Oliver proposes the headroom, John approves). Swap doesn't count as room.
   - The run prints the limit and saves its full output to a file next to it (the manual rule from 2026-10-12).
2. **The batch size is chosen by measurement.**
   - **First pass, 2026-10-12** (Toby). Mac, real E24 speech, large-v3 int8, one run each. The box has 3,113 MB available when idle.

     | Clip | Batch | Peak RSS | Wall time |
     |---|---|---|---|
     | 90 s | 8 | 3,476 MB | 61 s |
     | 175 s | 8 | 6,096 MB | 145 s |
     | 90 s | 2 | 3,198 MB | 57 s |
     | 175 s | 2 | 2,657 MB | 107 s |

     - At batch 8, memory grows by about 31 MB per second of clip. The box's E13 death at about 5.1 GB fits that line.
     - At batch 2, the 175 s clip used *less* than the 90 s one. So the batch size is what makes memory grow with length, and at batch 2 the remaining cost is mostly the fixed price of the models.
     - That fixed cost, about 2.7–3.2 GB on this one run each, sits right at the box's free memory. That's too close to call a pass.
   - **Repeat runs, 2026-10-12** (Toby). Three runs each, same clips and settings, peak RSS in MB:

     | Clip | Batch 1 (runs 1/2/3) | Batch 2 (runs 1/2/3) |
     |---|---|---|
     | 30 s | 2,669 / 3,233 / 2,851 | 2,625 / 2,739 / 2,964 |
     | 77 s | 2,612 / 2,881 / 2,936 | 2,823 / 2,644 / 2,665 |
     | 175 s | 2,806 / 2,567 / 2,557 | 2,702 / 3,134 / 2,933 |

     - **At batch 1 or 2, length doesn't move memory.** The 175 s clip is no higher than the 30 s one. The spread between runs, about 2.56–3.23 GB, is noise, and it's as wide at 30 s as at 175 s.
     - **Batch 1 buys nothing over batch 2**, in memory or time (175 s: about 116 s at batch 1, 114 s at batch 2). **Batch 2 is the candidate for the box.**
     - **The fixed cost is the problem now.** The highest single run (3,233 MB) is above the box's 3,113 MB available when idle. Even at batch 2, a 30 s clip could push the box into swap. Batch 8 never fit; E6 got through on the low side of the noise.
   - **Still owed before the limit is set:** the real number from `measure_clip_limit.sh` on the box. The Mac is a stand-in. If the box shows the same fixed cost, the options are:
     - free the transcription model before the alignment pass, so the two models aren't held at once;
     - more memory on the box;
     - a smaller transcription model for the clips (a quality question for Jen's eval).

     The choice is John's.
   - When the batch size changes, the header comment in `transcribe_clips.sh` says why. Its "~30-60s" assumption goes.
3. **No limits file, no guess.** Phase 1 and Phase 2 refuse to start the clip steps if the limits file is missing or unreadable. Each sends one alert that names the file and gives the command to create it.

### B. Phase 1 enforces the ceiling (the fix John asked for)

4. **The ceiling** is `min(editorial quote max, CLIP_MAX_SECONDS)`.
   - **90 s is the absolute maximum for a finished reel** (John, 2026-10-12). It's the Reels-tab cap, and `render_reel_PRD.md` 3g already says 20–90 s.
   - A reel is the quote **plus** about 10 s of frame, all from `render_reel.js` `RENDER_CONFIG`: opening 2.5 s, lead-in 1.5 s, tail 0.5 s, close 5.5 s. A beat-snapped close can add up to one bar.
   - So the **editorial quote max is 90 s minus that frame, worst case**. It is computed from `RENDER_CONFIG` and the theme's bar length, not typed in, so a longer opener lowers it on its own.
   - *Test:* a quote exactly at the max renders a reel of 90 s or less, and one 0.5 s longer is refused.
   - The 90 s lives in one place in code and is cited in the reels canon.
   - **One definition of length.** The ceiling caps the **quote** window (`transcript_end_ms − transcript_start_ms`). The clip is the quote plus the padding `extract_quote_clips.sh` adds (2.5 s on E13: window 172.0 s, clip 174.5 s). Phase 2's check (AC 10) derives the clip ceiling in code from that padding, read from the extract script's config and not typed in.
   - *Test:* a quote at exactly the max passes both Phase 1 and Phase 2.
   - `verify_reel_spec.js`'s `maxDurationSec: 120` disagrees with the 90 s and is brought into line. Its "margin for spec drift" comment goes.
5. **Lisa is told before she picks.**
   - `prompts/brand_team_lisa.md` states the ceiling in seconds, filled at run time, never hard-coded in the prompt.
   - Most quotes then come in under it without a second pass.
6. **Measured right after the brand team.**
   - In `jobs/round_1.py`, right after the heading gate, each quote's start and end are found against `transcript_fast.json` using **`quote_tape.py`**, the one tape check QUOTE-01 uses too (John, 2026-10-12). Its runs give each quote's start, end and length. That gives one rule rather than two copies that can drift.
   - The timestamps go into quotes.json, so Phase 2's lookup finds them already set (it already skips quotes that have them).
7. **Over the ceiling.** Lisa gets **one** trim-or-swap pass that names each long quote and its measured length. *(Q1)*
   - If a quote is still over after that pass, it is dropped.
   - The drop is recorded in quotes.json's `brand_team` report and in the standup line.
   - *Test:* a fixture transcript with a 175 s quote ends with no quote over the ceiling and one recorded drop.
8. **Unmatched quote.**
   - A quote the matcher can't place has no length, so Phase 1 can't prove it fits. It gets the same trim-or-swap pass as an over-ceiling quote, and a drop if it still can't be placed.
   - Phase 1 never passes on a 0 timestamp.
9. **All quotes dropped.** If no quote is left, Phase 1 raises `PermanentFailure` with an alert, before Drive, calendar, counter or schedule are touched. This matches the heading gate.

### C. Phase 2 guards the same limit

10. **Long clip.**
    - `transcribe_clips.sh` checks each clip's length from the manifest against the same ceiling **before** loading WhisperX.
    - A clip over the ceiling is a `PermanentFailure` with one alert: episode, quote index, length, ceiling.
    - The FAQ and the draft are not rewritten.
    - *Test:* a hand-edited quotes.json with a 175 s quote fails in step 8 with that message, and WhisperX is never started.
11. **Order.** The clip-length check runs before `6 faq_blog` **and** before `7a-0 forced_alignment`.
    - Phase 1's timestamps (AC 6) are already in quotes.json, so Phase 2 can check them without aligning first.
    - If a quote has no Phase 1 timestamps (a hand-edited quotes.json), the story records where the check sits instead.
    - *Why:* each E13 attempt spent $0.064 and rewrote the draft in `faq_blog` (about 28 s), then spent about 385 s in alignment, then died in step 8. Five attempts cost about 53 min of box time and $0.32 (ledger, 2026-10-12).

### D. A dead job is never silent

12. **OOM is not retried.**
    - An OOM in a pipeline step becomes `PermanentFailure` with the reason: step, clip, length.
    - The reason is read from evidence (the unit cgroup's `memory.events` `oom_kill` count, or the step's own signal), not guessed from the exit code alone.
    - *Test:* a faked OOM marks the job failed once, with no retries scheduled.
    - *Evidence, confirmed on E13:* Python saw `exit -15` (systemd's SIGTERM as it stopped the unit), and the ledger recorded `error (RuntimeError)`. The OOM shows only in systemd's journal: `The kernel OOM killer killed some processes in this unit` and `Failed with result 'oom-kill'`. Reading the exit code alone misses it.
    - The whole worker died each time, not just the job. That makes Oliver's `MemoryMax=` story the next one after 01a (out of scope here).
    - **Scope, as built (Toby's review, 2026-10-12):** in Phase 2 the reason names step, clip and length. Round 1 has no clips, so its reason names the step only.
    - **The clip named is the last clip the script started.** That holds while systemd's default `OOMPolicy=stop` stops the whole unit. With `OOMPolicy=continue` or a `MemoryMax=`, `transcribe_clips.sh` would go on to the next clip, and the alert would name the wrong one. Whoever changes either setting changes the attribution too (it's noted in `oom_watch.py`).
13. **Exhausted retries send one alert.**
    - When `worker.py` marks a job permanently failed after its last attempt, it sends **one** internal alert: episode, job type, step, attempts, last error.
    - It uses the existing internal-alert path. Internal alerts are the only auto-send, and guest mail stays DRAFT-only.
    - *Test:* five failing attempts produce exactly one alert. A job that fails once and then succeeds produces none.

### E. Proof

14. **Local gate green**, with the full test run and the existing exclusions. The 2 `google.auth` failures (impediment #5) are named, not counted as passes.
15. **Live proof on E13** (John runs the box): deploy, `measure_clip_limit.sh`, then E13's re-run. *(Q3)*
    - Every **remaining** E13 quote is at or under the ceiling, and any drop is recorded (AC 7).
    - Expect two or three trims, not one. On the 2026-10-12 windows, q3 (172 s) and q4 (104 s) are over and q2 (77 s) is on the line. A full re-run may pick different quotes, but this speaker's long answers make long quotes likely again.
    - Step 8 completes, and Phase 2 reaches reels, cards and the thank-you DRAFT.
    - The worker log shows no OOM.
16. **The ledger** records the ceiling used and each quote's length for that run, so the eval sessions can read it from a file.

## As built: CLIP-01c (2026-10-12, branch `feat/clip-01c`)

**John's rulings, 2026-10-12:**
- *"we should now be able to handle up to 180 seconds - but only if it's really a good quote"* → **two tiers.** A standard
  reel is ≤ 90 s. A `long_form` quote, kept whole with Lisa's written `long_form_reason`, makes a reel ≤ 180 s (under
  3 minutes), and the standup names it for John or Heather to approve before posting. A bare `long_form` flag counts for
  nothing and is cleared.
- **Measure by segment span, the safe reading.** The fast transcript has no word times; its segments run ~24 s
  (median, 9 episodes). On the 10 quotes with real aligned windows (E13, E29) the span was never more than 1.5 s short
  of the alignment, but it can read up to ~50 s long. A word-position estimate read up to 29 s short, so it was dropped.
  The cost, accepted: some quotes that would fit get Lisa's pass.
- The clip Phase 2 cuts is the whole window, gaps between joins included (E13 q03: 172 s aligned, 38 s of spoken runs),
  so the span runs first word to last, never the summed runs.

**The numbers, all derived** (`clip_limits.py`): frame 12.22 s (render_reel.js: opening 2.5 + lead-in 1.5 + tail 0.5 +
closing 5.5 + one theme bar 2.22); clip padding 2.5 s (extract_quote_clips.sh); caps 90 / 180 s (verify_reel_spec.js
SPEC, the one place); box 180 s at batch 2 (state/box-limits.env). Each ceiling also leaves the 1.5 s span shortfall:
**standard quote ≤ 76 s, long_form ≤ 166 s**; the box (176 s) never binds today.

**Where each AC landed:**
- AC 2: `transcribe_clips.sh` takes `CLIP_BATCH_SIZE` from the limits file (it ran at batch 8, which the 180 s limit was
  never measured at). Its "~30-60 s" header goes.
- AC 3: Round 1 (before the brand team), Phase 2 (step 5b) and `transcribe_clips.sh` (exit 4) refuse without the file,
  each naming the command.
- AC 5: the ceilings reach Lisa and Marcus as episode metadata (`quote_ceiling_seconds`,
  `long_form_quote_ceiling_seconds`), never typed into a prompt.
- **AC 6, changed:** the length lives in `tape_check` (`start_ms`, `end_ms`, `span_s`, `ceiling_s`), not in
  `transcript_start_ms`/`end_ms`. QUOTE-01 slice 1 keeps those unwritten on purpose, since Phase 2's forced alignment is
  word-level and more exact than a segment-level placement.
- AC 7 and 8: Round 1 step 10b, in the same single Lisa pass as QUOTE-01. An unplaced quote is `not_verbatim`, gets that
  pass, then is dropped; it never passes on a zero length.
- AC 10 and 11: Phase 2 checks three times:
  - **5b**, before `faq_blog` and alignment, on Phase 1's span (a quote without one is named and checked next);
  - **step 7**, on the aligned window, against the reel and the box;
  - **step 8**, on each cut clip, before WhisperX. `transcribe_clips.sh` refuses too (exit 5), for a run by hand.

  Each is a `PermanentFailure` with one alert.
- AC 16: the ledger gets `quote_lengths` (Round 1) and `clip_ceiling` (Phase 2) events.

## Out of scope (named so nobody loses them)

- A `MemoryMax=` on `rockstar-worker.service`, so an OOM kills only the job and not the unit. Oliver is to propose it as its own story.
- **What 01a can't see** (Toby, finding 3). If the worker itself is the OOM victim, or systemd-oomd kills the cgroup (oomd doesn't raise `oom_kill`), no Python code runs. There's no alert, and the job stays `running`. `reclaim_stale` then returns it to pending **without counting an attempt**, so it could retry with no limit. The fix: an `OnFailure=`/`ExecStopPost=` alert, and `reclaim_stale` counting the attempt. It goes with Oliver's `MemoryMax=` story, and OBS-03 AC 6 writes the record.
- CACHE-01 and the eval checklist (Jen). Impediment #3 is OBS-02, a sibling (see the split).

## Open questions

| # | Question | Angela's recommendation | Answer |
|---|---|---|---|
| Q1 | A quote over the ceiling: does Lisa get one trim-or-swap pass with a drop after it, or do we drop it outright? | One pass, then drop. Costs one Lisa call, keeps the set whole. | **Yes** (John, 2026-10-12) |
| Q2 | Where does the limits file live on the box? | `/opt/rockstar-pipeline/state/box-limits.env`. It's box state and isn't deployed, so `deploy.sh` never overwrites it. | John: "idk". Left to Oliver and Toby; the recommendation stands unless they object. |
| Q3 | E13: a full Round 1 re-run (new quotes, about $1) or a hand trim of its quotes.json? | Re-run. It's the live proof of AC 6–8. | **Re-run** (John, 2026-10-12) |
| Q4 | RAM headroom for the limit (AC 1) | Oliver proposes it after measuring. The box has 3,809 MB total and 3,113 MB available when idle, with 2 GB swap, 2 vCPU and `MemoryMax=infinity` (John's read, 2026-10-12). | |
