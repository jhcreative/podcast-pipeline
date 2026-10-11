# EVAL-02 — Leave Sonnet 4.6 on the numbers: measure current models per call site

**Decision owner:** Toby (AI and standards) · **Harness:** Jen · **Impl:** Bill · **Words:** Lisa · **PO:** Angela
**Priority:** Medium. **Due before 2027-01** (see *Why*) · **Status:** **Proposed** — drafted 2026-10-10 by Claude at John's request, for Angela's RTS pass; not Ready to Start (Q1–Q3 open)
**Created:** 2026-10-10 · **Raised by:** John · **Needs:** OBS-01 (cost, latency, `prompt_sha`), EVAL-01 (rubric, regression set, calibrated judge) · **Produces:** ADR-0016

---

## Why

Decision D-5 (2026-05-21) pinned every call to `claude-sonnet-4-6` (`anthropic_client.py:4`,
`_MODEL` at `:50`). Anthropic's own docs, checked 2026-10-10:

| Model | API ID | Input / output, $ per MTok | Cache read | Status |
|---|---|---|---|---|
| Sonnet 4.6 (today) | `claude-sonnet-4-6` | $3 / $15 | $0.30 | Active; tentative retirement *"not sooner than February 17, 2027"* |
| Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | $0.10 | Current |
| Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 (prompts up to 100K tokens) | $0.01 | Current; "high-volume, latency-sensitive" |
| Opus 5.5 | `claude-opus-5-5` | $4 / $20 | $0.20 | Current; docs call it the recommended starting point |

Sources: platform.claude.com/docs/en/about-claude/pricing,
platform.claude.com/docs/en/models/overview, platform.claude.com/docs/en/about-claude/model-deprecations.
Re-check before running; this table is a dated copy, not a source of truth.

What this means:

1. **The pinned model is now the older and the more expensive one.** The current Sonnet costs less on
   every line, and its cache reads are a third of the price. The pipeline relies on cache reads for
   brand-team calls 2–4.
2. **There is a deadline, but not yet a forced one.** Retirement is "not sooner than" Feb 2027. Decide
   with time to spare, not in a hurry.
3. **Swapping blind is the real risk.** The pipeline's robustness was tuned on 4.6: the `max_tokens`
   headroom (R1-03), the JSON-fence recovery, and the title JSON parse. The current models think
   adaptively and have different default effort levels. An unmeasured swap could bring back the
   R1-03 failure class.
4. **Cost is probably not what decides this.** At one episode a week, model spend is likely small
   next to Heather's editing time. OBS-01 will say for sure. That is why EVAL-01's human-edit rate
   is the main metric here, and price is the tiebreaker.

## Scope

- Run the EVAL-01 regression set, prompts unchanged (same `prompt_sha`), **per call site**: brand team
  (×4), title, apple summary, FAQ, card captions, cover titles, SEO captions.
- **Candidates (Q1):** baseline `claude-sonnet-4-6`; `claude-sonnet-5-5` on every call site;
  `claude-haiku-5-5` on the narrow call sites where code already assembles everything fixed (cover
  titles, card captions, SEO captions; ADR-0007, ADR-0013).
- **The judge is `claude-opus-5-5`** and is never a candidate (EVAL-01 AC-6).
- **Offline runs use the Batch API** (50% off input and output, per the pricing page). The eval is not
  latency-sensitive, so it should cost half.
- Read model limits from the Models API at run time (`max_input_tokens`, `max_tokens`) rather than
  hard-coding them, as the docs recommend.
- **Out of scope:** prompt rewrites for the new models. Compare like for like first; tuning is a
  follow-up story if a candidate is close.

## Acceptance criteria

1. For each call site and candidate, three runs report: the rubric score with its spread, the gate
   pass rate, `max_tokens` hits, JSON-parse failures, cost per episode and latency.
2. The comparison rule from EVAL-00 applies: a gap the paired CI can't separate from 0 is "no detectable
   difference" (not "no difference"), reported with the minimum detectable effect for the n used.
3. **ADR-0016** records the model for each call site with the numbers, or keeps 4.6 with the reason.
   Toby decides; John signs off.
4. If the decision changes anything, it is a config change with a one-line rollback, and the OBS-01
   `PRICES` table gains the new rows.
5. The first live episode after the switch is compared with the eval's prediction (the human-edit rate
   from EVAL-01). If they disagree, that goes in the ADR.

## Decisions (open — John, Toby)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Candidate list | As above. Add Opus 5.5 as a brand-team candidate only if Sonnet 5.5 scores below baseline there. |
| Q2 | One model everywhere, or per call site? | **One model unless the gap is large.** Every split is another thing to explain in a review. Haiku earns a split only where its quality is within the spread. |
| Q3 | When? | After EVAL-01 has calibrated its judge; **aim for a decision by January 2027**, well ahead of the earliest retirement date. |

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 2 | `evals/results/` files, one per candidate × `prompt_sha`, linked here |
| 3 | ADR-0016 |
| 4 | Diff of the config change and the rollback line |
| 5 | The episode's `human_edit` lines next to the eval's predicted range |

---

## RTS pass — Angela, 2026-10-10 (with Toby, Jen)

**Price table re-checked against the live pricing page, 2026-10-10:** correct as written. Sonnet 5.5 cache reads are
$0.10 (0.05× input, footnote 2 on the page); Sonnet 4.6's are $0.30 (0.1×). Batch is 50% off input and output.
Caveat for the ADR: 5.5 thinks adaptively, so cheaper per token is not yet cheaper per episode. OBS-01 shows which.

**Code facts (Toby):** `run_dr_joe.py` pins `claude-sonnet-4-6` separately (`:97`, `:141`), and SHARE-01 removes the
second definition before any switch. No call sets `temperature`; the default is 1.0. Record it in the baseline.

**Amendments adopted (Jen):**
- AC-6: the primary metric and decision rule per call site are written in the ADR-0016 draft **before the first
  run** (pre-registration; ten sites × criteria × candidates make chance "wins" likely).
- AC-7: call sites without a rubric (brand team, FAQ, captions) are decided on reliability metrics only, and the ADR
  says so.
- AC-8: latency comes from production ledgers, never batch runs; results are split pre/post each candidate's training
  cutoff; the run aborts at `--budget-usd`, and eval spend carries `run_kind: eval`.
- AC-2 now uses EVAL-00's paired bootstrap rule in place of "spread".
- **Judge family bias:** Opus 5.5 judging Sonnet 5.5 risks family preference that favours the challenger. A second
  judge on a sample, or Heather's ratings, cross-check it.
- **Cost, Jen's estimate (not data):** 23 site-configs × 20 episodes × 3 runs = 1,380 generations, about $45 with
  batch; judging about $55–110 at one call per output, past $300 per-criterion. Budget cap required.

**Gate status:** RTS-3 needs OBS-01, EVAL-00, EVAL-01 (calibrated judge) · due before 2027-01.

## Measured cycles before any change (John, 2026-10-10)

> *"we should run a few cycles before we change models so we can measure with the new tools, then project backwards"*

- **The cycles.** Once OBS-01 and EVAL-01 A (the draft snapshot) are live, the pipeline runs **6 live episodes
  unchanged**: Sonnet 4.6, today's prompts, today's request structure. Six is Jen's floor for a live human-edit
  baseline (6–8); cost needs fewer. John may set a different number.
- **Project backwards.** The backfill method (OBS-01) is run on those same 6 episodes and compared with what was
  measured. The gap, given as a mean and a range over 6 episodes rather than 1, is the estimate's stated error. Only
  then is it applied to E23–E33.
- **Nothing changes during the cycles.** No model switch (EVAL-02), no prompt change (PROMPT-01 Part 2 is
  byte-identical by its AC-4, so it may land), no request-structure change (the CACHE-01 fix waits; its *measurement*
  is the cycles themselves). A change mid-cycle restarts the count, and the plan says so on the day it happens.
- **AC-9:** ADR-0016 is not decided, and no call site's model changes, before the 6 measured cycles are complete. The
  ADR cites their ledger lines.
