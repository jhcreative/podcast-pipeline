# CASE-01 — The case study: measuring a production LLM pipeline, one page, every number sourced

**Owner:** John (voice) · **Numbers:** Jen (claims ledger) · **Words:** Robert (voice), Lisa (final gate) · **Form:** Joni · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — Angela, 2026-10-10; written in two passes (see Scope); Q1 open
**Created:** 2026-10-10 · **Raised by:** John ("proof of concept and interview reflection") · **Needs:** OBS-01 (pass 1), EVAL-01/00/03 (pass 2), SHARE-02 (link)

---

## Why

The work only helps John in an interview if he can show it in five minutes and defend every number for an hour. A
case study does both, if every claim is sourced and the limits are on the page. It is also the reflection: what we
believed, what the measurement showed, what we changed.

The strongest story is likely already in hand: we believed the transcript cache saved money, and it can't as built
(CACHE-01). Instrumenting found it. That's the thesis — **measure before you optimize** — in one honest example.

## Scope

1. **Pass 1, after OBS-01 + CACHE-01 (about 2–3 weeks):**
   - cost per episode and per call site, measured
   - where the time goes
   - the cache finding, before and after
   - the pipeline's design choices: event-driven, hand edits win, deterministic gates, no autosend
2. **Pass 2, after the baseline and calibration (about 4–6 weeks):**
   - the eval harness
   - measured run-to-run noise
   - judge agreement with Heather, with its CI and n
   - the human-edit metric's first numbers
   - EVAL-02's result, whatever it is, including "no detectable difference"
3. **The claims ledger** (`docs/case-study/claims.md`): each claim → its evidence file, its date, its n. Jen's
   allowed/not-allowed list (below) is the starting rule.
4. **Reflection section:** what we got wrong and how we found it (the cache; the four prompts deploy never shipped; the
   status endpoint), in the Fail → Fix form. Benefits, not flex.
5. Format: one US-Letter page in the JHC house report format (`product-ops/report-writer`), plus the DASH-01 report as
   the appendix. Saved locally; never published to claude.ai (PP-9).

## What may and may not be claimed (Jen, PQ-5)

| After | May say | May not say |
|---|---|---|
| OBS-01 | "An episode costs $X in model spend, measured; here's where the time goes; the cache does / doesn't hit." | Any estimate for E23–E33. |
| Baseline | "A harness with deterministic gates, a judge, and measured run noise on 20 real episodes." | "The writing is good." |
| Calibration | "The judge agrees with the host X% (CI, n) per criterion." | "Calibrated" or "validated" on 10 items. |
| EVAL-02 | "On this show, a 20-episode paired comparison found [a difference with CI / no detectable difference]." | "Model A is better"; any generalization beyond this show; a "win" inside the CI. |
| Human edits | "Heather's edit share went from A to B over n episodes." | "Quality improved" (her trust and workload confound it); any cause, unless only one thing changed. |

## Acceptance criteria

1. Every number on the page has a row in `claims.md` linking a ledger, a results file or a test, with a date and an n.
2. Every claim is on Jen's may-say list, or has been added to the list by Jen.
3. No guest-identifying data anywhere; G4's denylist scan passes on the page and its appendix.
4. Lisa passes the words (RTP-3, PQ-5b); Joni passes the form; John approves the final.
5. It links the SHARE-02 repo, or states the code is available on request.

## Decisions (open — John)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Pass 1 now (cost and cache only), or wait for the full eval? | **Pass 1 as soon as OBS-01 has one real episode.** A real, small, sourced result beats a large promised one, and it's interview-ready in weeks. |

**John, 2026-10-10 (OBS-01 Q2): backfilled costs.**
- **May say:** "Estimated at $X per episode on Sonnet 4.6 (token-counted inputs, approximate outputs), within Y% of the
  first measured episode."
- **May not say:** any estimate presented as a measurement, or a trend that mixes the two.
