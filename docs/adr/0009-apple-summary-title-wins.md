# ADR 0009 — apple_summary title wins for reels and blog (human review outranks the schedule)

- **Status:** Accepted
- **Date:** 2026-06-26
- **Deciders:** John (owner — the decision); Bill (backend — impl); Toby (review/deploy); Jen (tests — TITLE-04)
- **Story:** TITLE-04 — apple_summary title precedence
- **Amends:** ADR-0006 (Phase-1 title generation + shared gate). 0006 still stands for
  Phase 1 (generate a real title into the schedule, gated). This ADR changes which title
  *Phase 2 renders* when the two disagree.

## Context

`apple_summary.md` is the **human-review surface**: John reviews and edits it in Drive
before the episode goes to the publisher. Until now the reel title card and blog title came
from `publication_schedule.json` (the Phase-1-generated title), and apple_summary's title was
only consulted as a `"TBD"` fallback. So a hand-edited title in the summary did **not** reach
the reels — surprising, because the summary is exactly where a human signs off on wording.

John's call: *"apple_summary wins for reels and blog post title because that's human review."*
A human-reviewed title should outrank a machine-generated one.

A complication the old code couldn't handle: the summary takes two shapes. The
pipeline-generated H1 is structured —
`"Gen X Hits Menopause · S2E4 · Hook | Topic · ft. Guest"` (title at ` · ` index 2). When John
hand-edits, he rewrites it as a clean **title line + prose**, dropping the show/code/guest
segments. The old parser only understood the structured form (`split(" · ")`, need ≥3 parts),
so a hand-edited title silently fell through.

## Decision

Phase 2 resolves the title from `apple_summary.md` and that title **overrides** the schedule
title for everything it produces — reel cards, blog title, blog URL slug, captions context,
thank-you. A new `_title_from_apple_summary` handles both shapes: structured H1 → segment
index 2; otherwise the first non-empty line (stripped of a leading `#`). The schedule title is
the fallback only when the summary yields no real title (`content_gates.is_real_title`).

The placeholder gate moved accordingly: it no longer hard-fails on the *schedule* title up
front (that would block a TBD schedule entry the human already fixed in the summary). It runs
on the **final, resolved** title — still before any render or upload, so junk never ships.

## Consequences

- Editing the title in `apple_summary.md` now drives the reels and blog. One human-review
  surface, as intended.
- For episodes John doesn't hand-edit, the structured H1 parses to the same title Phase 1 put
  in the schedule — no behavior change. Overrides are logged when the two differ.
- E29 did **not** need re-rendering: its summary parses to the title already on its reels.
- `content_gates` gained `is_real_title()` (the non-raising predicate behind `assert_real_title`)
  so callers can branch without catching `GateFailure` (PQ-4 DRY).
- **Boundary:** a hand-edited title containing ` · ` would be misparsed as the structured form.
  John separates with ` | ` / ` — `, never ` · `, so this is acceptable; noted, not guarded.
- Tests owed: **TITLE-04** (Jen) — both parse shapes, override-vs-fallback, gate-on-final-title.
