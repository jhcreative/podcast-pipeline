# CAP-01 — Assert caption rules (≤4 hashtags/platform + idempotent IG music credit)

**Owner:** Jen · **PO:** Angela · **Priority:** Low (fast-follow to ADR-0007)
**Status:** Ready · **Created:** 2026-06-25 · **Raised by:** Toby (PR #1 review)

---

## Why

ADR-0007 made two caption-format rules live (PR #1, `712c452`):

1. **Hashtags capped at 3–4 on all platforms** — a *soft*, model-guided rule in
   `prompts/seo_captions.md`. Nothing fails if the model emits 5+.
2. **IG music credit appended in code** — `publish_finalize.py` Step 11, idempotent,
   exact string `🎵 from #uppbeat https://uppbeat.io/t/real-daze/show-me`
   (env `IG_MUSIC_CREDIT`).

Toby verified the credit-append contract by hand during review (append-once,
idempotent, empty-safe, whitespace-safe) but it is **not** in the automated suite,
and the hashtag rule has no assertion at all. PP-4 says AI features get the same
rigor as infrastructure — this closes that gap.

## Acceptance criteria

1. **Hashtag count** — for a generated caption set, every platform string
   (instagram/linkedin/youtube) contains **3–4 content hashtags**, excluding the
   `#uppbeat` token inside the music credit. (Tolerance-matrix / golden-output check.)
2. **Credit append — present & exact** — the rendered IG caption ends with the exact
   `IG_MUSIC_CREDIT` string; LinkedIn and YouTube never contain it.
3. **Credit append — idempotent** — running the append over a caption that already
   carries the credit does not duplicate it.
4. **Credit append — empty-safe** — an empty/`None` Instagram value produces no credit
   line and does not raise.
5. **Env override** — setting `IG_MUSIC_CREDIT` changes the appended string.

## Suggested approach

The append logic is currently inline in `_produce_content` (Step 11). Consider
extracting it to a tiny pure helper (e.g. `_with_music_credit(ig: str) -> str`) so
criteria 2–5 are unit-testable without standing up the whole job. Toby flagged the
extraction as optional during review; it pays for itself the moment this test exists.

## Out of scope

The hashtag rule is soft by design (ADR-0007) — this story asserts the count on
generated output; it does not add a hard gate that fails the pipeline on a 5-tag
caption. If a hard gate is ever wanted, that's a separate decision for Angela + Toby.
