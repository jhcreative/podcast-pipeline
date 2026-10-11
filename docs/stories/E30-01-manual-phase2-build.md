# E30-01 — Build Phase 2 for E30 by hand (it has been live for a while)

**Owner:** Bill · **Ops:** Oliver · **PO:** Angela · **Verification:** Jen
**Priority:** HIGH — business-visible; the episode is public with no downstream artifacts
**Status:** Ready · **Created:** 2026-07-21 · **Raised by:** John

---

## Why

E30 completed Phase 1 and went live on the platforms, but the publisher's "Episode is
Live" email never came (see TRIG-01 — it is not coming again, for any episode). Phase 2
therefore never ran. E30 has been public "for a while" with no blog post, no reels, no
thank-you, and a stale `publication_schedule.json` entry.

E30 is also the **first episode to exercise the deterministic v2 thank-you** (TY-02 /
ADR-0010), which shipped 2026-07-06 and has not yet been validated on a real episode. This
story is that validation.

## Scope

One-time recovery of E30. The durable fix is TRIG-01. **This story must not grow into
TRIG-01** — if the recovery reveals a systemic gap, it becomes a note on TRIG-01, not
extra work here.

## E30 — identified and verified 2026-07-21

Located from the platforms, per John's instruction to find them ourselves.

| Field | Value |
|---|---|
| Title | **Feed Your Head \| Gut Microbiome, Hormones & Midlife Vitality** |
| Published | 2026-07-09 (live 12 days at story creation) |
| Episode number | `<itunes:episode>30</itunes:episode>` — **explicit in the feed**, not inferred |
| Audio file | `GHM_30.mp3` |
| Apple | `https://podcasts.apple.com/us/podcast/feed-your-head-gut-microbiome-hormones-midlife-vitality/id1785708777?i=1000700000030` |
| Spotify | `https://open.spotify.com/episode/0ExampleEpisode30xxxxx` |
| Show collectionId | `1785708777` · Spotify show `3CIrqvlAWVsin0ZwFzykv6` |
| Feed | `https://publisher.example.test/feed/genx-hits-menopause` |

Both URLs returned 200 anonymously; the Spotify title was confirmed via the public oEmbed
endpoint as an exact match. **Re-verify at run time** — these were checked on 2026-07-21 and
criterion 1 requires verification immediately before use, not a copy from this table.

**Caveat for anyone reusing this method:** Apple's API returns `episodeNumber: null` for
*every* episode of this show. The number exists only in the RSS feed. Do not build
identification on Apple's numbering.

## Acceptance criteria

1. **The live URLs are re-verified at run time, not assumed.** Title-verified by fetch
   (HTTP 200 + title matches) immediately before anything cites them. A URL that does not
   resolve or does not title-match halts the story — we ask John rather than proceeding on a
   guess (PQ-5, BLOG-01 lesson).
   *Testable:* the fetched titles are recorded in the completion note before Phase 2 runs.

2. **Numbering is reconciled against platform truth before processing.** The pipeline's
   internal episode number and the platforms' published numbering have disagreed before
   (E29: schedule "Optimize Hormones" vs. platforms "Optimizing Your Hormones"). Confirm
   the episode we call E30 is the episode at those URLs — **by title, not by number**.
   *Testable:* the platform title and the schedule title are both quoted in the completion
   note; any mismatch is raised to John as a decision, not silently normalized.

3. **Phase 2 runs through the sanctioned recovery path.**
   `reprocess_episode.py --episode 30 --apple <url> --spotify <url>`, **without**
   `--reels-only`. No ad-hoc scripting, no LLM in the thank-you path.

4. **`publication_schedule.json` is hand-reconciled afterward, with a backup.** The
   reprocess path skips the schedule write-back (known gap, 07-06 carry-forward). Back up
   to `publication_schedule.json.bak-e30-2026-07-21` before editing.
   *Testable:* backup exists on the box; post-edit entry has the verified URLs, the correct
   blog slug, and a status reflecting reality.

5. **The v2 thank-you is validated on a real episode.** The E30 guest draft must be:
   gate-clean (`assert_thank_you_wellformed` passes), v2 wording, subject
   `Hi <First> ~ podcast episode & links are live!`, correct apostrophe-safe blog slug
   (BLOG-01), **no duplicated signature block**, and **DRAFT only — never sent.** Sending
   is John's, always.
   *Testable:* the raw server-side draft is fetched back and inspected — not the client
   render, and not a claim that it was created. If it is wrong, we fix the template, not
   the draft by hand.

6. **The blog URL resolves.** The derived blog post URL returns 200 and title-matches
   before it appears in any artifact. (This is TY-03's gap, hit manually here.)

7. **A defect found during recovery becomes a story, not a silent patch.** Anything
   surprising gets filed under `docs/stories/` and referenced in the carry-forward.

## Notes

- **Nothing is sent to anyone.** Every outbound artifact is a draft for John's review. The
  guest thank-you in particular is DRAFT-only, permanently.
- Oliver confirms service health on the box before and after; Bill owns the run.
- E30's reels are in scope for Phase 2 as normal — but the **E28 reel defects (REEL-05) are
  not yet fixed**, so E30's reels may carry the same tail/caption issues. Review them
  against the same bar and note it; do not block E30's recovery on the reel fix.
- Related: TRIG-01 (why this happened), TY-02/ADR-0010, BLOG-01, TY-03.
