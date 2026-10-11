# EVAL-01 — Measure the writing: how much Heather changes it, and a held-out regression set

**Owner:** Jen (eval design and harness) · **Impl:** Bill (draft snapshot, the Step 4 hook) · **Rubric:** Heather with Lisa · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — drafted 2026-10-10 by Claude at John's request, for Angela's RTS pass; not Ready to Start (Q1–Q4 open; Q5 answered)
**Created:** 2026-10-10 · **Raised by:** John · **Needs:** OBS-01 (ledger, `prompt_sha`) · **Feeds:** EVAL-02, DASH-01 chart 5

---

## Why

The tests prove the code, and the gates stop junk. Nothing measures whether the writing is good.
`content_gates.py` catches a placeholder title, a missing guest or a malformed blog post. It can't tell
strong show notes from flat ones, so a prompt change today is judged by eye on whichever episode comes
next.

**The pipeline already makes labeled data and throws it away.** ADR-0008 says a hand edit always wins.
`round_1` writes the model's `apple_summary.md` (`round_1.py:552-563`), Heather and John edit it in
Drive, and Phase 2 Step 4 downloads whatever is in Drive at that point. So every episode produces a
pair: what the model wrote, and what Heather published. The size of that edit is the most honest
quality number this pipeline can have, because it is Heather's own judgment, given for free on every
episode. Nobody records it.

The second half is a **regression set**: frozen past episodes, scored the same way every time, so
prompt changes and model changes (EVAL-02) are measured before they ship, not after.

## Scope

**A. The human-edit metric (live, every episode)**

1. `round_1` snapshots each model-written artifact **as written**, before any human touch, to
   `state/drafts/E{N}/` with its `prompt_sha` and model (from OBS-01).
2. After its download, Phase 2 Step 4 compares each snapshot with the Drive version and writes an
   `event: "human_edit"` ledger line per artifact: share of characters changed, lines changed, and
   whether the title changed.
2a. The blog gets the same line once the post is published: `blog_post.json` (in Drive) against the live
   Tailor post. Read-only, with the BLOG-02 credential rules (C-4, C-9) unchanged.
2b. **No backfill.** 20 of the Drive `apple_summary` files are plain `.md` files, not Google Docs, and Drive
   keeps old versions of plain files for only about 30 days. The original drafts of past episodes are
   mostly gone. The metric starts with the next episode, and that is why the snapshot in step 1 exists.

**B. The regression set (offline, run on demand)**

3. **Everything starts with the transcript.** The input is `transcript_fast.txt`, the exact file
   `round_1` feeds the model (`round_1.py:482`), plus `quotes.json` where a call needs it. Source of
   truth is the episode's folder in the shared Drive (`My Drive/RockstarPodcast/Episodes/`), read on the
   Mac through Google Drive for desktop. The repo's local `Episodes/` is a partial working copy and is
   **not** the source.
   Inventory, names only, 2026-10-10: `transcript_fast.txt` exists for **20 episodes** (E6, E13, E17–E23,
   E25–E35). Leave out `EXX-<guest>` and `lymphatic-drainage-1` (test runs) and `E16-<guest>` (empty).
   E24 keeps its transcript in a `transcripts/` subfolder; check it before including.
   The pre-pipeline transcripts (E5, E7, E8, the E13 Doc, `Podcast Audio & Transcriptions/`) are in other
   formats and stay out.
4. `scripts/eval-fetch.sh` copies each manifest episode's inputs and references from the Drive-for-desktop
   path (an env var, never a hard-coded account path) into `evals/fixtures/`, which is **gitignored**.
   Transcripts and guest content never enter git. Reading those files may download them from the cloud;
   that's expected and read-only.
4a. **References are the published finals, not `voice_corpus/gold_*`.** The gold titles are pasted into
   the prompts (`prompts/title.md:32`, `prompts/apple_summary.md:26`), so scoring against them would
   reward copying what the model was shown. That's eval leakage. Where the final lives:
   - **Blog (E32 onward):** the draft is `blog_post.json` in Drive, exactly what was sent to Tailor. The
     final is the post as published on rockstarblends.com, read back from Tailor. This is the cleanest
     pair the pipeline has. E17–E31 used the retired model-written `blog_page.html`; that era is
     history, not a reference.
   - **Show notes and title:** the final is Drive's `apple_summary.md`, which Heather edits in place
     (John, 2026-10-10). The Drive file is the reference.
   - **Captions:** Heather posts by hand, so the final lives on Instagram, not Drive. Out of scope until
     edits happen in Drive first.
5. Scoring has three parts, all recorded:
   - **Deterministic checks**, reusing `content_gates.py`: the same gates as production, one definition.
   - **An LLM judge** against a short rubric. The judge prompt is versioned by sha.
   - **Cost and latency**, from the OBS-01 ledger. An eval run has a price too.
6. `python3 evals/run_eval.py --model <id> --runs 3` writes `evals/results/<date>-<model>-<prompt_sha>.json`:
   a score per episode, artifact and criterion, the gate pass rate, cost, and the spread across runs.

## Acceptance criteria

1. Every model-written artifact in `round_1` is snapshotted before upload, and the snapshot survives
   the work directory being cleaned up.
2. Step 4 writes one `human_edit` line per artifact. **A metric failure never fails the job** (same rule
   as OBS-01 AC-5).
3. The regression set runs offline from `evals/fixtures/`: no live Drive, Gmail or Tailor calls during a run. `eval-fetch.sh` is the only step that touches Drive.
4. **The judge is calibrated before it gates anything.** Heather rates at least 10 items on the same
   rubric, and the report shows how often the judge agrees with her. Below the threshold (Q3), the
   judge is advisory only.
5. **Noise is measured.** Each configuration runs 3 times, and the report shows the spread. A difference
   smaller than the spread is reported as *no detectable difference* (EVAL-00's wording), never as a win.
6. The judge never scores its own model's output (self-preference bias). The judge model is fixed and
   recorded per run.
7. `git ls-files` shows no transcript, quote audio or guest text under `evals/`. A test asserts it.

## Decisions (open — John, Heather)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Which artifacts first? | **Answered (John, 2026-10-10): title and show notes first.** Original recommendation: **Title and show notes** (`apple_summary.md`): the most edited and the most visible. FAQ and captions later. |
| Q2 | Rubric shape? | **Answered (John, 2026-10-10): 4–6 criteria.** Original recommendation: **4–6 criteria, each pass/fail or 1–3**, written by Heather and Lisa. Judges are more reliable on narrow binary questions than on a 1–10 scale. |
| Q3 | Judge agreement threshold for gating? | Start advisory. Gate only once agreement with Heather is high enough that she'd trust it; she sets the bar. |
| Q4 | Heather's time for the calibration set? | **Answered (John, 2026-10-10): yes**, as amended: about 90 minutes for 30 items, plus 5 re-rated a week later. |
| Q5 | ~~Where does Heather's final show-notes text live?~~ | **Answered (John, 2026-10-10): edited in place in Drive's `apple_summary.md`.** So the Drive file that Step 4 downloads is the final reference, and the step 2 comparison captures every edit. |

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 2 | `test_eval01_snapshot.py`: a fake `round_1` writes artifacts, Drive returns an edited copy, the `human_edit` line carries the expected numbers; a failing metric leaves the job `done` |
| 3, 5, 6, 7 | `test_eval01_harness.py`: a fake model with a seeded spread, the noise rule applied, the judge-equals-candidate guard, the `git ls-files` assertion |
| 4 | The calibration report, dated, in `evals/results/`, linked here |

---

## RTS pass — Angela, 2026-10-10 (with Toby, Bill, Jen)

**Corrections from the code audit:**
- The model doesn't receive `transcript_fast.txt` as-is: `round_1.py:486` runs `summary_norm.canonicalize_host` on it
  first. The eval applies the same step to every fixture, or it measures a different input.
- The show notes pass through `normalize_apple_summary` (`round_1.py:560`) before they're written. The snapshot is
  taken **after** normalization (what Heather saw), and the raw model output is kept beside it (EVAL-00).
- `_MODEL` is a module constant. The harness needs `model` as a parameter of the `compose_*` functions; the harness
  calls those directly on fixtures, never the jobs (which need WhisperX and Drive).

**Amendments adopted (Jen; John: "I want what Jen wants"):**
- **New dependency: EVAL-00** (metric definitions, comparison rule, test strategy, baseline). Nothing in Part B starts
  before it.
- AC-1 adds: the `human_edit` metric is the one defined in `evals/metric.py` (EVAL-00), and each line carries
  `reviewed: bool`.
- **AC-5 replaced:** differences are the mean paired per-episode difference with a 95% bootstrap CI; "no detectable difference"
  whenever the CI contains 0.
- **AC-4 replaced:** the report shows per-criterion raw agreement (Wilson CI) and Cohen's kappa; the judge gates
  nothing below 30 rated items and Heather's threshold. Q4 becomes about 90 minutes of Heather's time for 30 items,
  plus 5 re-rated a week later.
- AC-6 widened:
  - A judge from the same model family as a candidate is cross-checked on a sample, by a second judge or by Heather's ratings.
  - Any pairwise judgment runs in both orders and counts only consistent verdicts.
  - Mean output length is reported per candidate.
- AC-8: eval outputs pass through `summary_norm` and the same `content_gates.py` as production.
- AC-9: Heather's finals are edits of 4.6 drafts, so similarity-to-final favours the incumbent. Model choice uses
  **reference-free** rubric scores; similarity-to-final is reported, never decisive.
- **Ship Part A (the snapshot) early.** Every week it isn't live loses a labelled pair. The live baseline needs about
  6–8 episodes with no prompt or model change.

**Gate status:** RTS-1 pass after the amendments · RTS-3 needs OBS-01 and EVAL-00 · RTS-4 via EVAL-00 · RTS-6: Orly
reviews `eval-fetch.sh` and the gitignore assertion at design time (guest transcripts on disk).

## John's answers, 2026-10-10 (Q1, Q2) and the anchor set (PROMPT-01 Q1)

- **Q1:** title and show notes first. **Q2:** 4–6 criteria, each pass/fail or 1–3.
- **Anchors:** the published finals of **E22–E35** are the accepted great examples (John). The May gold files are retired.

**Leakage guard (Angela, Jen's rule). E22–E35 overlaps the regression set.** The set holds E22, E23 and E25–E35, which
is 13 of its 20 episodes. An episode can't be both an example of "good" shown to the judge and an item the judge
scores. If it is, the judge is grading the answer key.
- **Amendment:** pick **3 anchor episodes** from E22–E35, with Heather choosing the clearest examples. They are shown
  to the judge and used in the rubric, and are **removed from the scored set**. The other 10 stay as scored items, with
  their finals as references.
- Every results file lists the anchor episodes, and a test asserts that no anchor appears among the scored items.
- E24 still needs its transcript checked (it sits in a `transcripts/` subfolder). E13 stays in the scored set and is
  the public replay episode (EVAL-03).
- **AC-10:** the anchor and scored sets are disjoint, and `run_eval.py` refuses to run when they overlap.
