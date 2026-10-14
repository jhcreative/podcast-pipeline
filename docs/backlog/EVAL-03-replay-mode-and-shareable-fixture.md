# EVAL-03 — Replay mode and one shareable episode: the demo that runs anywhere

**Owner:** Jen (harness) · **Impl:** Bill · **Consent:** Heather, John · **Security:** Orly · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — Angela, 2026-10-10, from Jen's review; Q1 open
**Created:** 2026-10-10 · **Raised by:** Jen · **Needs:** OBS-01, EVAL-01 B · **Feeds:** SHARE-02, CASE-01

---

## Why

The regression set lives in `evals/fixtures/`, which is gitignored guest data. That is right for privacy, but it means
John can't show the eval to anyone outside the house: no Drive, no box, no guest transcripts. A demo needs two things:

- **Record/replay.** The harness records every real API response once (a "cassette"); replay rebuilds results and the
  DASH-01 report offline, in minutes, at $0. The numbers shown are real numbers from a real run, not regenerated.
- **One episode we may publish.** E13 (Heather and John; John, 2026-10-10), with both their consent on record. Synthetic transcripts stay in
  unit tests (FIX-01 generates them); they are never evidence.

## Scope

1. `run_eval.py --record cassettes/<run>/` stores request hash → response (content, `usage`, `stop_reason`, `model`).
2. `run_eval.py --replay cassettes/<run>/` serves only from the cassette. A request that isn't in it fails loudly,
   naming its hash; it never falls through to the network.
3. The public fixture: one consented episode's transcript, quotes and published finals, under `evals/public/`, with
   its consent record named in `evals/public/CONSENT.md` (who, what, date; the record itself stays private).
4. Cassettes from the public episode only are committed; all others stay gitignored.

## Acceptance criteria

1. Replay makes zero network calls. *Test:* sockets blocked; the run passes.
2. Replay of the public cassette, from a fresh clone, rebuilds the results file and the report in under 2 minutes.
3. Replay is byte-stable: two replays produce identical results files.
4. A request missing from the cassette exits non-zero and names its hash.
5. Sentinel test: no guest name from the private manifest appears anywhere in `git ls-files` content outside
   `evals/public/`.
6. `CONSENT.md` names the consent's date and form before any file under `evals/public/` is committed.

## Decisions (open — John, Heather)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Which episode is the public one? | **Answered (John, 2026-10-10): E13** (`E13-heather-john`; no third party, so consent is John's and Heather's, recorded in `CONSENT.md`). Original recommendation: **a Heather-solo episode** if one exists in the 20 — no third-party consent needed. Otherwise the guest most likely to say yes, asked in writing by Heather. |
