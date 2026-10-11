# Eval test strategy — RockstarPodcast (EVAL-00, RTS-4)

**Owner:** Jen · **Review:** Toby · **Written:** 2026-10-10, before any eval number exists.

## What the evaluation measures

| Question | Instrument | Defined in |
|---|---|---|
| What does an episode cost, and where does the time go? | OBS-01 run ledger (`model_call` and `step` events) | `run_ledger.py` |
| How much does Heather change what the model wrote? | Human-edit share: word-level Levenshtein ÷ max(word counts), after normalisation | `evals/metric.py` |
| Is a change (prompt, model) better, worse or the same? | Paired per-episode difference; two 95% CIs (bootstrap percentile and Student-t); "no detectable difference" unless BOTH exclude 0; advisory under 10 episodes; the metric's direction always stated | `evals/stats.py` |
| Can the LLM judge be trusted? | Per criterion: raw agreement with its Wilson CI, plus Cohen's kappa, against Heather's ratings | `evals/stats.py` |
| What exactly was compared? | A frozen baseline that names every input that could change a result | `evals/baseline.py` |

## How each is tested

- **Golden cases** (`test_eval00_definitions.py`):
  - identical = 0; a no-edit round trip = 0; whitespace-only = 0;
  - one word in N = 1/N exactly; a rewrite ≥ 0.9 and flagged;
  - a known effect's CI excludes 0, and no effect's contains it, with a fixed seed;
  - Wilson 8/10 ≈ 0.49–0.94; a textbook kappa = 0.4.
- **The ledger** (`test_obs01_ledger.py`): fake clients with canned usage, a cost worked out by hand, and a sentinel
  for C-4.
- **Every eval output** passes through the same `summary_norm` and `content_gates` as production (EVAL-01 AC-8).

## Why two intervals

The cold review simulated the percentile bootstrap alone, at 20 episodes with no real effect. It contained 0
only 92.3% of the time, so luck would pass as a win more often than the 95% claims. Requiring the Student-t
interval to exclude 0 as well brings it to **95.8%** (383 of 400 seeded trials). A test pins at least 94% over 200
trials.

## What the rule can and can't see

The verdict reads "no **detectable** difference", never "no difference". At 20 episodes the rule is in effect a
paired t-test: false positives ≈ 4.9%, and a real effect of 0.5 SD (of the per-episode differences) is found only
58% of the time (re-review simulation, 1,000 trials). Before trusting a "no detectable difference", check it
against the effect the comparison could have seen.

**Minimum detectable effect** (80% power, α = 0.05 two-sided, in SDs of the per-episode differences ≈ (t₀.₉₇₅ +
t₀.₈₀) / √n):

| Episodes | Smallest effect found 80% of the time |
|---|---|
| 10 | ≈ 0.99 SD |
| 20 | ≈ 0.66 SD |
| 30 | ≈ 0.53 SD |

In plain terms: with this show's 20 transcripts, only a clear difference shows up. Small improvements are not
claims this set can make (CASE-01's may-not-say list).

## The judge's ceiling, and what similarity can't decide

- **Heather re-rates 5 items a week after her first ratings.** Her agreement with herself is the ceiling the judge
  is held to: no judge is asked to agree with her more than she agrees with herself.
- **The human-edit share is a similarity-to-final measure.** Heather's finals are edits of the incumbent model's
  drafts, so it favours the incumbent. It reports; it never decides a model choice (EVAL-01 AC-9). EVAL-02 decides
  on reference-free rubric scores.

## Not tested (deliberately), and why

- **The real API's behaviour.** Tests use fakes and recorded cassettes. Model drift is what the regression set
  measures; a unit test can't.
- **The Drive export round trip** beyond the null control. Converting to Google Docs and back could change bytes;
  EVAL-01's null control (a no-edit round trip = 0) checks it on real files once, not on every run.
- **The judge's correctness** beyond calibration against Heather. Agreement is measured, not assumed. Below 30
  rated items the judge is advisory only.
- **Cassette staleness.** A recorded response is true of the day it was recorded; replay proves the harness, not
  today's model.
- **The visual quality of the charts** (DASH-01). Joni reviews them by eye; tests check numbers and leakage only.

## Pre-registration (EVAL-02)

One primary metric and decision rule per call site is written into the ADR-0016 draft **before** the first
comparison run. Ten call sites × criteria × candidates make some chance "wins" likely.

Signed: Jen, 2026-10-10.
