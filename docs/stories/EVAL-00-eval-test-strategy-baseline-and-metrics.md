# EVAL-00 — Eval test strategy, frozen baseline and metric definitions

**Owner:** Jen · **Impl:** Bill (`evals/metric.py`, `baseline.json`) · **Review:** Toby · **PO:** Angela
**Priority:** Medium — **Ready to Start before EVAL-01 builds anything** (RTS-4) · **Status:** **Proposed** — Angela, 2026-10-10, from Jen's review
**Created:** 2026-10-10 · **Raised by:** Jen · **Gates:** EVAL-01, EVAL-02, DASH-01 charts 5–6, CACHE-01

---

## Why

EVAL-01 and EVAL-02 say "score", "spread" and "no difference" without defining any of them. With 20 episodes, an
undefined statistic is how a measurement project ends up reporting luck as a win (PQ-5). RTS-4 also needs a written
test strategy that says what is deliberately **not** tested. This story writes the definitions down before any number
exists, so nobody picks a metric after seeing the results.

## Scope

1. **`docs/eval/test-strategy.md`.** What is tested, how, and what is deliberately not tested:
   - Real API behaviour (we test fakes and replay cassettes).
   - The Drive export round trip beyond the null control in item 3.
   - The judge's correctness beyond calibration.
   - Cassette staleness as models drift.
   - The visual quality of the charts (Joni reviews by eye).
2. **`baseline.json` schema and one recorded baseline**: the model, `response.model`, the expanded `prompt_sha` per call
   site, `request_template_sha`, the deploy manifest hash, the `summary_norm` version, `temperature` (today unset =
   1.0, recorded as such), the judge model and sha, and the fixture manifest hash.
3. **The human-edit metric, in `evals/metric.py`, with golden cases.**
   - Normalize first: NFC, LF, trailing whitespace, collapsed blank lines; Markdown markers stay.
   - **Primary:** word-level Levenshtein ÷ max(word counts). Character-level is secondary. Not difflib's ratio.
   - Report per artifact, plus the absolute number of words edited.
   - `reviewed: bool` from the Drive revision (author and time), so "accepted" and "never opened" are not both 0.
   - Rewrites (≥ 0.9) are their own category, not just a large number.
   - **Which title:** `compose_title` (calendar and schedule) and the `apple_summary.md` H1 are different titles. Name both.
   - The snapshot is taken **after `normalize_apple_summary`** (what Heather saw), with the raw model output kept beside it.
4. **The comparison rule.** The unit is the episode. A difference is the mean paired per-episode difference
   (candidate − baseline, each the mean of its 3 runs) with a 95% bootstrap CI over episodes (10,000 resamples).
   **"No difference" whenever the CI contains 0.** Three runs measure within-episode noise only.
5. **Judge calibration statistics.** Per criterion: raw agreement with its Wilson 95% CI, and Cohen's kappa. Ten items
   is a smoke test; **gating needs ≥ 30 items** and Heather's threshold. Heather re-rates 5 items a week later; her
   consistency is the ceiling the judge is held to.
6. **Pre-registration.** One primary metric and decision rule per call site, written into the ADR-0016 draft before
   EVAL-02's first run.

## Acceptance criteria

1. `evals/metric.py` golden tests pass:
   - identical text = 0
   - a no-edit round trip through the normalizer = 0
   - a one-word change in N words = 1/N exactly
   - a full rewrite ≥ 0.9
   - whitespace-only change = 0
2. The bootstrap function, on a fixture with a known effect and one with none, returns a CI excluding 0 and a CI
   containing 0 respectively, with a fixed seed so the test is deterministic.
3. `baseline.json` validates against its schema, and a field-for-field test fails if any listed field is missing.
4. `docs/eval/test-strategy.md` exists, lists the not-tested items above, and Jen signs it (RTS-4).
5. The calibration report template prints n, raw agreement, Wilson CI and kappa per criterion, and prints
   **"advisory — n < 30"** when n is below 30.

## Decisions

None open; these are Jen's calls as eval owner. Toby reviews.

---

## Built — Jen with Bill, 2026-10-10 (branch `feat/eval-00`)

- **`evals/metric.py`:** the human-edit metric, exactly as §3 defines it.
  - **Normalise first:** NFC, LF line endings, trailing whitespace removed, runs of blank lines collapsed. Markdown
    markers are kept, so changing a heading is an edit.
  - **The headline:** word-level Levenshtein ÷ max(word counts); character-level is reported but never the
    headline.
  - **Rewrites:** a share of 0.9 or more is flagged `rewrite`.
  - **`reviewed`:** passed in by the caller, never inferred.
- **`evals/stats.py`:**
  - `paired_bootstrap`: the unit is the episode, using only episodes present in both; 10,000 resamples with a fixed
    seed; "no detectable difference" whenever the CI contains 0 (wording settled in the re-review).
  - `wilson`, `cohen_kappa` (None when undefined), and `calibration_report`, which says "advisory — n < 30" below
    30 items.
- **`evals/baseline.py`:** 14 required fields, each with its reason. `validate()` names every missing field and
  refuses anchors that are also scored (EVAL-01 AC-10).
- **`docs/eval/test-strategy.md`** (RTS-4): what is measured, how it's tested, what is deliberately not tested and
  why, and pre-registration for EVAL-02. Signed.
- Stdlib only. The box doesn't run `evals/`; when EVAL-01 A's Step 4 imports `evals.metric`, the PROMPT-01 guard
  will require it in FILES.

**Evidence:**
- `python3 -B test_eval00_definitions.py` → **11 passed**.
  - **Golden cases:** identical = 0; a CRLF / trailing-space / blank-line round trip = 0; whitespace-only = 0; one
    word in 17 = 1/17 exactly; a rewrite ≥ 0.9 and flagged; a heading change counts.
  - **The bootstrap:** a known effect gives "candidate lower" with the CI below 0; noise gives "no detectable difference";
    identical on a rerun.
  - **Statistics:** Wilson 8/10 = 0.49–0.94, Jen's figure from the review; a textbook kappa = 0.4.
- **Mutation checks:** a verdict that always says "no difference" fails 1 test, and dropping normalisation fails 1.

**Cold review of PR #36, 2026-10-10: approve with changes. Done:**
- **The interval (the statistician's condition).** The percentile bootstrap alone covered only 92.3% at n = 20.
  Now the verdict needs the bootstrap **and** a Student-t interval both to exclude 0: 95.8% null coverage, pinned
  by a test (≥ 94% over 200 seeded trials).
  - Under 10 episodes the verdict is advisory.
  - `lower_is_better` is required, and the result states the direction ("candidate better" or "worse", never
    "lower").
- **The normaliser tests can now fail:**
  - an exported round trip (CRLF, trailing spaces, blank runs) = 0 at both word and character level;
  - NFC vs NFD = 0;
  - `char_share` golden cases, where whitespace inside a line counts at character level, by design and documented;
  - empty draft or final; deleting NFC fails a test.
- **`validate` checks types:** lists of non-empty strings, a positive integer for runs, non-empty sha strings, a
  non-empty scored set.
- **`rewrite` uses the reported, rounded share** (boundary test 17999/20000 → 0.9 → rewrite).
- **Bad input is refused,** with a named error: `wilson` with successes > n, calibration on unequal or empty lists,
  episodes with no runs.
- **Speed:** a shared prefix and suffix are trimmed before the edit distance; exact, and fast on lightly edited text.
- **Text fixed:** the docstring now names the right test file, and "1/17" replaces "1/19".
- **The test strategy** gains the two-interval rationale, Heather's re-rate as the judge's ceiling, and
  similarity-to-final never deciding (EVAL-01 AC-9).

**Not done, said plainly: AC-3 is partial.** The schema and validator ship; **the recorded baseline does not.** A
baseline needs OBS-01's ledger, with `response_model`, the template shas and real call sites, from live episodes.
It is recorded after the 6 measured cycles (EVAL-02's rule).

**Evidence:** `test_eval00_definitions.py` → **17 passed** in 2.8 s. **Mutation checks:** no NFC → fails; a verdict
that always says "no difference" → fails; no normalisation → fails.

**Re-review of `e28a462`: approve with changes from both reviewers. Done:**
- **The interval rule is now pinned:** a test asserts the verdict interval contains both the t and the bootstrap
  intervals, so dropping the union fails it. The t table is spot-checked at df 4, 19 and 29 (exact), 45 (the
  conservative row) and 200.
- **Above df 120 the table uses 1.980,** not z = 1.960, which was below the true value and broke the rule's own
  "conservative" promise.
- **The wording is now "no detectable difference",** and the test strategy gives the minimum detectable effect at
  10, 20 and 30 episodes. With 20 episodes, only clear effects show; that limit is stated, not hidden.
- **`runs_per_episode=True` is refused** (bool is an int in Python).

`test_eval00_definitions.py` → **18 passed**.

**Final review of `4d8ec28`: approve with changes. Done:**
- **The old wording is gone wherever it drives a decision.** The strategy's summary table, EVAL-01 (×2), EVAL-02 and
  this story now say "no detectable difference". Historical lines that describe past runs are kept as they were.
  **CACHE-01 AC-4 becomes a non-inferiority test:** the margin is written before the run, because an underpowered
  "no detectable difference" can't stand in for equivalence.
- **Correcting my claim:** the containment test pins only **half** the union. On its seed, the bootstrap interval
  sits inside the t interval, so a t-only mutant survives. It does pin that the bootstrap-only rule is gone, which
  was the review's finding. The rule is a t-test in practice, and the strategy says so.
