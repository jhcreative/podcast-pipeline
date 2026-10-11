# TY-03 — `create_thank_you_draft.py` should verify URLs resolve, not just non-empty

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** Low (follow-up)
**Status:** Ready · **Created:** 2026-07-06 · **Raised by:** Bill (TY-01/BLOG-01)

---

## Why

`create_thank_you_draft.py`'s `_require` guard checks that each URL slot is **non-empty**
— not that it **resolves**. BLOG-01 was exactly this failure mode: the stored E29
`blog_post_url` was non-empty but 404'd, so the CLI would have cited a dead link with no
complaint. The gate (`assert_thank_you_wellformed`) checks *shape*, not *liveness*. An
artifact citing a URL that doesn't resolve is a PQ-5 honest-systems gap.

## Acceptance criteria

1. Before creating the draft, the CLI HTTP-checks each cited URL (Apple, Spotify, blog)
   resolves (200) **and** title-matches the episode — the same verify the pipeline already
   does elsewhere (publisher URL title-verify). Audible is the fixed show URL; a lighter
   check is fine.
2. On a non-resolving or title-mismatched URL the CLI **fails loudly** (never creates the
   draft) with which URL failed — consistent with fail-safe-not-guess (PQ-5).
3. Offline/dry-run mode still works without network (skip liveness, keep shape checks).

## Notes

- Keep it in the CLI, not the pipeline's Step 13 — the automated path derives URLs from
  already-verified sources; this closes the *manual re-create* gap where a human passes or
  a stale schedule supplies a bad URL. Reuse the existing title-verify helper; don't add a
  second HTTP client.
