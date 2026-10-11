# TITLE-01 — Phase-1 title generation + shared placeholder gate

**Owner:** Bill (Backend Lead) · **PO:** Angela · **Priority:** HIGH (blocks Phase 2 reels)
**Status:** Ready to Start · **Created:** 2026-06-23

---

## Story

**As** the pipeline,
**I want** `round_1` (Phase 1) to reliably **generate** a real episode title and **refuse to
complete** without one,
**so that** Phase 2 (reels) — which cannot run without a title — is never blocked, and no
episode silently advances with a `TBD` placeholder.

## Context — what happened (E29, 2026-06-23)

E29 completed `round_1` with `episode_title="TBD"`, bumped the counter to E30, and wrote a
`drafting` schedule entry — yet the model had produced a perfectly good title. Root cause:

- The title is **scraped from prose**, not generated. `round_1.py:466-475` takes
  `apple_summary_text.split("\n")[0]`, splits on `" · "`, and uses `parts[2]`.
- The model emitted a **pre-write note above the H1**:
  ```
  *Pre-write note: Brand Team quotes are blank ...*
  ---
  # Gen X Hits Menopause · S2E4 · Don't Wait for the Stop | Hormone Optimization ...
  ```
  `split("\n")[0]` grabbed the *note* (no `·`), so it fell back to the `episode_title = "TBD"`
  default at `round_1.py:470` — and **there is no gate in round_1**, so the job completed.
- The hard gate `_assert_real_title` (commit `c9f1443`) exists **only in
  `publish_finalize.py` (Phase 5)** — too late: Phase 2 reels are already blocked by then,
  and its remedy is *manual* ("set the real title in publication_schedule.json").

**Decision (John, 2026-06-23):** Phase 1 must **generate** the title (dedicated, structured —
not a brittle prose-scrape), AND the placeholder gate must be **shared** and enforced in
Phase 1. → this story.

## RTS gate (Angela)

- **RTS-1 Testable:** yes — observable in title output + job pass/fail + schedule state.
- **RTS-2 Contract:** Bill defines the title-generation function's return shape (structured)
  and the shared-gate module's interface before coding.
- **RTS-3 Dependencies:** `jobs/round_1.py`, `jobs/publish_finalize.py`, `anthropic_client.py`,
  prompts; the existing `_assert_real_title` / `_PLACEHOLDER_TITLE_RE` to be extracted.
- **RTS-4 Test strategy:** Jen — preamble-resilience, gate-blocks-TBD, no-half-complete.
- **RTS-5 Branch:** clean; work in an **isolated worktree off `main`** (the PUB-01 working
  tree is uncommitted — do NOT entangle the two; no file overlap expected).

## Acceptance criteria

**A. Dedicated title generation (Phase 1).**
1. `round_1` obtains the title from a **dedicated generation step** that returns the title as
   **structured data** (e.g. a `compose_title` returning `{hook, topic, title, ...}` or a
   JSON field), NOT by parsing the Apple-summary H1's line 0.
2. Generation is **resilient to model preamble/notes** — any leading commentary before the
   title content does not corrupt the result. (The E29 pre-write-note case must produce the
   correct title.)
3. The title follows the house format (`Hook | Topic`, guest-featured per the brand rule);
   value true to voice (BP-4).

**B. Shared placeholder gate, enforced in Phase 1.**
4. Extract `_PLACEHOLDER_TITLE_RE` + `_assert_real_title` (and consider `_assert_guest_featured`)
   into a **single shared module** imported by BOTH `round_1.py` and `publish_finalize.py`
   (PQ-4 DRY — one definition, two callers).
5. `round_1` calls the title gate **before** it does anything irreversible — i.e. before the
   counter bump (step 14), schedule write (step 15), and ideally before the calendar event
   (step 13). On a missing/placeholder title it **raises (fails the job loudly)** rather than
   completing.

**C. No half-complete state.**
6. When the gate fails, `round_1` must NOT advance the episode counter and must NOT leave a
   `drafting` schedule entry that looks complete (PQ-5 — honest systems; a failed title is a
   failed job, visibly). A corrected re-run then completes cleanly and idempotently.

**D. Back-compat / reprocess.**
7. The `reprocess` path still works (reuses calendar event, doesn't bump counter) and is
   subject to the same gate.

## Out of scope (separate tickets)

- The "Brand Team quotes are blank" note in `compose_apple_summary` despite 5 quotes existing
  — suggests quotes may not be reaching the summary compose. **Flag as TITLE-02 for triage;
  do not fix here.**
- Auto-capturing `guest_name` in Phase 1 (currently null until manual) — nice-to-have; note
  it, don't build it unless trivial alongside A.

## Decision record

Bill + Toby capture the title-generation contract (structured return, gate-in-Phase-1,
shared module) in **ADR-0006** (PP-5). Reference E29 as the motivating failure.

## Definition of Done

- DOD-1 Jen's tests pass. DOD-2 contract documented. DOD-3 Toby review.
- DOD-5 generation + gate have explicit, honest failure handling (raise, don't default).
- DOD-7 every criterion mapped to a test. ADR-0006 merged.

## Test notes for Jen

- **Preamble resilience:** feed a summary/model-output with leading notes before the title →
  correct title extracted (the E29 reproduction).
- **Gate blocks TBD:** title resolves to TBD/empty/`pending`/`placeholder` → `round_1` raises,
  counter NOT bumped, no `drafting` entry written.
- **Happy path:** real title → completes, schedule + calendar carry the real title.
- **Shared gate parity:** the same input fails identically in round_1 and publish_finalize
  (one definition, two callers).
- Isolated state only — never touch live `/opt/rockstar-pipeline/state`.
