# Measurement, eval and showcase — plan (Angela, 2026-10-10)

**Goal (John, 2026-10-10):** make this pipeline the proof of concept for measuring and evaluating a production LLM
system — something John can show and defend in an interview and reflect on — and clean the code up so it can be
shared.

**Team on it:** Angela (PO), Jen (eval and test owner), Bill (implementation), Toby (standards, review, export),
Orly (security, sharing gates), Oliver (box), Joni (report form), Lisa and Robert (case-study words). Heather owns the
prompts, the rubric and the calibration ratings. John decides, and runs everything on the box.

**How this was put together:** three read-only reviews on 2026-10-10 — Toby and Bill (code-for-sharing audit and a
claim-by-claim check of the five new stories), Orly (secrets, personal data and infra exposure across all 173 commits),
Jen (eval gaps). No code changed, nothing touched the box or the live host.

## Order

John's order, from the planning session: *"PROMPT-01 Part 1 and TRIG-03 first, since both are live risks. Then
OBS-01, EVAL-01, EVAL-02 and DASH-01."* Jen's testing work goes in where it gates (John: *"I want what Jen wants"*).

| # | Story | Why here | Owner |
|---|---|---|---|
| 1 | **PROMPT-01 Part 1** | Live risk: four loaded prompts (and five modules) aren't in deploy FILES; the next prompt edit would stay on the laptop | Bill, Oliver |
| 1 | **TRIG-03** | Live risk: a late publisher email re-runs all of Phase 2 | Bill |
| 1 | **SEC-01** | Live risk if the host matches the repo: `/status` is world-readable, Gmail webhook unverified (Q1 below) | Bill, Orly |
| 2 | **OBS-01** | The ledger everything else reads; amended with `run_kind`, `request_template_sha`, cache-miss check | Bill, Jen |
| 2 | **EVAL-00** | Jen's test strategy, metric definitions, comparison rule and baseline schema; written before any number exists (RTS-4) | Jen |
| 3 | **CACHE-01** | Measured across the 6 cycles; the fix ships after cycle 6 | Bill |
| 3 | **EVAL-01 A** | The draft snapshot. Ship early: each week without it loses a draft→final pair | Bill, Jen |
| 3 | **PROMPT-01 Parts 2–3** | One source for examples; must land before the baseline | Bill |
| 4 | **EVAL-01 B** + **EVAL-03** | Regression set and 4.6 baseline; replay mode and the one shareable episode | Jen, Bill |
| 4½ | **6 measured cycles** | John: run a few cycles before changing models, measure with the new tools, then project backwards. 6 live episodes unchanged on 4.6; the backfill is calibrated on them; the CACHE-01 fix and any model change wait until after | Bill, Jen |
| 5 | **EVAL-02** | Model choice per call site, pre-registered; decision by January 2027 | Toby, Jen |
| 5 | **DASH-01** | Charts 1–4 can start once OBS-01 has data; 5–6 when their sources exist | Bill, Jen, Joni |
| — | **SHARE-01** | Clean-up a stranger can run (install, one test command, layout). After PROMPT-01's guard, so moves are caught | Toby, Bill |
| — | **FIX-01** | Seeded synthetic episodes with known properties; replaces real-guest fixtures; property tests for the gates. Built alongside SHARE-02 | Jen, Bill, Orly |
| — | **SHARE-02** | Public showcase repo from an allowlist, fresh history, Orly's gates G1–G7 | Toby, Orly |
| — | **CASE-01** | Pass 1 after OBS-01 + CACHE-01 (~2–3 weeks); pass 2 after calibration (~4–6 weeks) | John, Jen |

**Smallest honest first demo (Jen):** one real episode's OBS-01 ledger (cost per call site, cache-read share, step
times) plus the replay harness on the shareable episode, showing run-to-run noise on fixed input. About 2–3 weeks.
Every number in it is actual.

## What the reviews found that the five stories didn't have

- **The transcript cache can't hit as built** (Toby, confirmed in code by Angela). Each brand-team call has a different
  `system=`, which sits ahead of the transcript in the cache prefix. → CACHE-01, and CASE-01's lead example.
- **The webhook service fails open** (Orly): `/status` unauthenticated, Gmail push not JWT-verified, checks skipped
  when a secret is unset. → SEC-01.
- **The repo can't go public**: real guest quotes in test fixtures and proof cards, three third parties' addresses,
  the box IP 58 times in history. Secrets are clean. → SHARE-02 (new repo, not a rewrite).
- **No one can run the whole suite**: no requirements file; 14 of 20 test files green cold, 4 fail on a missing
  Pillow. → SHARE-01.
- **The eval had no definitions**: no metric formula, no comparison rule, n=10 calibration, no baseline, no demo path.
  → EVAL-00, EVAL-03, and amendments to EVAL-01/02, DASH-01.
- Story corrections (input passes through `canonicalize_host`; the snapshot point; step names 11.5/11.6/7a;
  `run_log.jsonl` is failure-only today; `run_dr_joe.py` not live) are recorded in each story's RTS-pass section.

## Open questions for John — first five, in priority order

Every story keeps its own Q table; these five unblock the most work.

| # | Question | Recommendation | Answer |
|---|---|---|---|
| 1 | SEC-01 joins PROMPT-01 Part 1 and TRIG-03 in the first slot? | **Yes.** It's the same class — a live risk — and it's small. Orly does one read-only GET on your go to confirm exposure. | **Yes** (John, 2026-10-10) |
| 2 | OBS-01 Q1: ledger as JSONL on the box, no hosted tracer? | **JSONL.** The event shape maps to OpenTelemetry if a tracer is ever wanted. | **Plain JSONL** (John) |
| 3 | Which episode becomes the public, shareable one (EVAL-03 Q1)? | **A Heather-solo episode** if one exists; otherwise one guest, asked in writing by Heather. | **E13** (`E13-heather-john`: no third party; consent is John's and Heather's) |
| 4 | Heather's time: ~90 minutes to rate 30 items, plus 5 re-rated a week later (EVAL-01 Q4 as amended)? | **Ask her now.** Calibration is on the critical path to any quality claim. | **Yes** (John) |
| 5 | Sharing form: a new public showcase repo plus the case study (SHARE-02 Q1)? | **Yes.** The current repo stays private and untouched. | **Yes** (John) |

## Open questions — second batch, in priority order (2026-10-10)

| # | Question | Recommendation | Answer |
|---|---|---|---|
| 6 | SEC-01 Q1: `/status` localhost-only, or behind a secret? (Now confirmed world-readable, 200 with 20 job rows) | **Localhost only**, and the one-line Caddy block tonight as the stopgap | **Agree: localhost only** (John) |
| 7 | EVAL-01 Q1: which artifacts are scored first? | **Title and show notes** (`apple_summary.md`); FAQ and captions later | **Agree** (John) |
| 8 | EVAL-01 Q2: rubric shape? | **4–6 criteria, each pass/fail or 1–3**, drafted by Heather with Lisa | **4–6** (John) |
| 9 | PROMPT-01 Q1: are the May gold show notes (5) and articles (3) still Heather's bar? | If yes, they become **rubric anchors** (examples of good, never answers); if not, retire them and anchor on recent episodes she likes | **The published finals of E22–E35 are the accepted great examples** (John). May gold retired. Leakage guard: 3 anchors, removed from the scored set (EVAL-01) |
| 10 | OBS-01 Q2: backfill estimated costs for E23–E33? | **No.** Start clean at the next episode; an estimate is not data | **Yes, labelled as an estimate from the old Sonnet** (John). Separate file, token-counted inputs, never summed with measured cost (OBS-01) |

Later: SHARE-01 Q1–Q2 (move files, type hints); SHARE-02 Q2–Q3 (Rockstar's name, whose GitHub); CASE-01 Q1;
EVAL-01 Q3 (Heather sets the judge threshold after calibration); EVAL-02 Q1–Q3; PROMPT-01 Q2–Q3.

## Gates, as they stand

None of the fourteen stories is Ready to Start yet: each has open questions. The five reviewed today carry an
RTS-pass section saying which gate waits on what. **TRIG-03 has not had its RTS pass yet** — it's next on my list, as
it shares the first slot. PROMPT-01 Part 1 is closest: its open questions don't block it. All of these files are drafts and
untracked; nothing is committed.
