# BLOG-01 — `_derive_blog_url` mis-slugs apostrophe titles (404 blog links)

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** High (bundled with TY-02, per John 2026-07-06)
**Status:** Ready · **Created:** 2026-07-06 · **Raised by:** Bill (during TY-01)

---

## Why

`_derive_blog_url` (publish_finalize) turns an apostrophe into a hyphen:
`Don't` → `don-t`. The live rockstarblends.com post **drops** the apostrophe entirely:
`dont`. So the derived/stored blog URL 404s for any apostrophe title. Verified for E29
via WebFetch (Bill, 2026-07-06):

- Stored (schedule): `…/don-t-wait-…` → **404**
- Live: `…/dont-wait-…` → **200**, title-matches E29

Impact: any episode whose title contains `Don't / Can't / Won't / It's / …` gets a dead
blog link in the reconciled schedule — and therefore in the guest thank-you draft, the
blog reference, anywhere the URL is cited. It silently blocked TY-01. This is a PQ-5
honest-systems defect: an artifact cites a link that doesn't resolve.

## Acceptance criteria

1. **Correct slug rule** — `_derive_blog_url` matches rockstarblends.com's actual
   slugging: apostrophes are **removed**, not hyphenated. (Confirm the full rule against
   several live posts, not just E29 — there may be other punctuation cases.)
2. **E29 backfill** — the stored `blog_post_url` for E29 in the reconciled
   `publication_schedule.json` is corrected to the live `dont-wait` slug (back up first).
3. **Tests (Jen)** — `Don't Wait…` → `dont-wait…`; a plain title is unchanged; any other
   punctuation cases found in AC1 are covered.

## Notes

- Related: TY-02's `create_thank_you_draft.py` `_require` guard checks non-empty, not
  resolvability, so it cannot catch a 404 URL. If we want the CLI to *guarantee* live
  links, it needs an HTTP title-verify step (Bill flagged; out of TY-02 scope — decide
  under this story or a follow-up).
