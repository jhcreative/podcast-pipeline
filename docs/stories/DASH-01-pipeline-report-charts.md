# DASH-01 — The pipeline report: cost, time, failures and writing quality, as charts

**Spec:** Jen (telemetry and dashboard design, Tufte) · **Impl:** Bill (report script) · **Design review:** Joni · **Security:** Orly · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — drafted 2026-10-10 by Claude at John's request, for Angela's RTS pass; not Ready to Start (Q1–Q2 open)
**Created:** 2026-10-10 · **Raised by:** John · **Needs:** OBS-01 (charts 1–4), EVAL-01 (chart 5), EVAL-02 (chart 6)

---

## Why

OBS-01 and EVAL-01 produce numbers. Numbers left in a JSONL file get read by no one. Each chart below
answers one question John or Heather actually asks. The report is also the artifact John can put in
front of a hiring manager, so it has to be honest first and handsome second.

## The charts (one question each)

| # | Question | Form (Jen's call; these are starting points) | Source |
|---|---|---|---|
| 1 | What does an episode cost, and which call spends it? | One row per episode; a horizontal bar split by call site; the total labelled at the end; no legend if direct labels fit | OBS-01 `model_call` |
| 2 | Where does the time go? | Duration per step, per episode, as dots on a shared axis; the long pole stands out without colour | OBS-01 `step` |
| 3 | Is the transcript cache working? | Cache-read share on brand-team calls 2–4, per episode. Expected near-full; one line, one reference band | OBS-01 `model_call` |
| 4 | What went wrong? | Per episode, a strip of retries, gate failures (named), permanent failures and skips (with reasons) | OBS-01 `step` |
| 5 | Is the writing getting closer to Heather's? | Share of each artifact changed between draft and final, over episodes; a falling line is the goal | EVAL-01 `human_edit` |
| 6 | Which model or prompt version should we run? | Dot plot per criterion: baseline vs candidates, with the run spread as a range | EVAL-01 / EVAL-02 results |

## How it's built

- `scripts/pipeline_report.py` reads `state/run_log.jsonl` and `evals/results/`, and writes **one
  self-contained HTML file**.
- Charts are **hand-drawn inline SVG** generated in Python: no chart library, no CDN, no web fonts
  linked. IBM Plex is already vendored (`BrandAssets/fonts/ibm-plex/`, CARD-05) and is embedded the
  same way the cards embed it. Every dependency earns its place.
- **Missing data says so.** An episode from before OBS-01 shows "no data", never a zero. A zero is a
  claim.
- Charts 1–4 ship first. 5 and 6 appear when their sources exist.

## Where it lives (Q1, and an Orly item found while writing this)

The repo's `Caddyfile` proxies all of `pipeline.example.test` to the webhook service. The webhooks
check a shared secret, but **`/status` (`webhook_service.py:118`) has no auth check** and returns the
last 20 job rows, including error text. If the box's live config matches the repo, that's publicly
readable today. **This is unverified:** the box isn't a git checkout, and nobody has probed the live
host. Orly confirms it either way before anything new is served from that host.

## Acceptance criteria

1. Built from a fixture ledger in a test, all six charts render (or show "no data" for 5 and 6).
   Chart 1's per-episode totals equal the ledger sum to the cent (Jen asserts).
2. No transcript text, quote text, guest e-mail or credential appears in the HTML (the sentinel pattern
   from OBS-01 AC-6).
3. The file opens offline and makes no network request (checked in the test by scanning for `http`
   in `src=` and `href=`).
4. Joni's review covers type, grid, direct labels, and no chartjunk.
5. Wherever Q1 decides the report lives, it is not publicly readable. Orly signs off.
6. John approves a screenshot for the case study, and it shows no guest-identifying data.

## Decisions (open — John)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Where does the report live? | **The episode's shared desk: a `reports/` folder in Drive**, uploaded after each Phase 2 run. It matches how everything else reaches Heather, and it needs no new public surface. |
| Q2 | Rebuild after every episode, or on demand? | **After every Phase 2**, as a non-blocking last step: a failure alerts and never fails the job. |

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 2, 3 | `test_dash01_report.py` against a fixture ledger and eval results |
| 4, 6 | Joni's review note and John's approval, dated, linked here |
| 5 | Orly's review in `docs/security/` |

---

## RTS pass — Angela, 2026-10-10 (with Orly, Jen)

**The `/status` finding is now SEC-01** (High, ahead of this story). Orly confirmed it in code, and found two more:
the Gmail webhook doesn't verify Pub/Sub's JWT, and the auth checks fail open when their secret is unset. DASH-01's
AC-5 depends on SEC-01.

**Amendments adopted (Jen):**
- AC-7: every report has a footer: ledger lines read, date range, code sha, and n per chart.
- AC-8: chart 5 shows points only, no trend line below 8 episodes; prompt and model changes are marked on the axis.
- AC-9: chart 6 plots the paired-difference CI (EVAL-00), not raw ranges.
- AC-10: the report builds from the EVAL-03 replay fixture in under 2 minutes, offline. That is the version shown in
  interviews.

**Order:** charts 1–4 need only OBS-01 and can be built alongside EVAL-01 and EVAL-02; John's order lists DASH-01 last,
and 5–6 do wait for their sources.

**John, 2026-10-10 (OBS-01 Q2):** chart 1 may show E23–E33 cost **estimates**: hollow marks, labelled "estimate,
Sonnet 4.6", with the measured-vs-estimate gap from OBS-01 AC-12 in the note. "No data" still means no data. An
estimate never fills a gap silently.
