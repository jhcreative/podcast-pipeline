# TITLE-04 — Tests for apple_summary title precedence

**Owner:** Jen · **PO:** Angela · **Priority:** Medium (fast-follow to ADR-0009)
**Status:** Ready · **Created:** 2026-06-26 · **Raised by:** Bill (feat/apple-summary-title-wins)

---

## Why

ADR-0009 made the apple_summary title win over the schedule title for Phase 2. Bill verified
`_title_from_apple_summary` against synthetic shapes and E29's real summary, but it's not in
the suite. The title drives the reel card, blog title, and blog URL — worth locking down.

## Acceptance criteria

`publish_finalize._title_from_apple_summary(text)`:
1. **Structured H1** — `"Gen X Hits Menopause · S2E4 · Hook | Topic · ft. Guest"` → `"Hook | Topic"` (index 2, not the `ft. Guest` byline).
2. **Hand-edited** — first non-empty line returned verbatim when there are no ` · ` segments; a leading `#` is stripped; the title's own ` | ` / ` — ` separators are preserved.
3. **Whitespace** — leading blank lines skipped; empty/whitespace-only text → `None`.

Override behavior in `_produce_content` (mock the Drive reads):
4. **Summary wins** — a real summary title different from the schedule title is the one used for blog URL + reel metadata; the override is logged.
5. **Fallback** — when the summary yields no real title (`None` or placeholder), the schedule title is kept.
6. **Gate on final** — `_assert_real_title` is enforced on the resolved title; a TBD schedule title + real summary title proceeds, while TBD/placeholder in *both* fails as `PermanentFailure`.

`content_gates`:
7. `is_real_title` true/false matches `assert_real_title` raising (shared predicate; `"Pendulum"` not matched on `"pending"`).

## Notes

Pure unit tests — no live Drive. Integration is already proven against production (E29 summary parses to its rendered title).
