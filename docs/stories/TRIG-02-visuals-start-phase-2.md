# TRIG-02 — The tile + reel images start Phase 2 (the publisher e-mail is gone)

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** High · **Size:** M
**Status:** Built 2026-10-01 · **Raised by:** John · **Follows:** ADR-0011 ("there is no publisher email any more")

John, 2026-10-01: *"they dont do the email any more and example-publisher doesnt have API … change trigger to images
arriving in same directory - the only thing missing then in draft would be the episode links … the manual work
to publish is still greatly reduced"*.

## The flow now

1. Round 1 (audio in the inbox) → John reviews `apple_summary.md` and `quotes.json`.
2. **John drops `E<N>-…-tile.<jpg|png|webp>` and `E<N>-…-reel.<…>` into the episode folder root.** That is the
   "reviewed, go" signal. If they are dropped before the review, nothing fires until an image is touched again.
3. The Drive change drain sees both images on an episode still awaiting Phase 2 and enqueues
   `PUBLISH_FINALIZE(trigger="visuals")`. It's deduped on episode + both image ids: replacing an image re-runs,
   while repeated Drive notifications don't.
4. Phase 2 runs in full: FAQ, the blog DRAFT on rockstarblends.com, reels plus captions to Drive, and the
   thank-you Gmail DRAFT.
   - The episode links don't exist yet, so **every listen link is the SHOW page**:
     `podcasts.apple.com/us/podcast/genx-hits-menopause/id1785708777` and
     `open.spotify.com/show/3CIrqvlAWVsin0ZwFzykv6`.
   - Both are real, working links on the hosts the blog gate allows. A forgotten swap still lands a listener on
     the show, never on a dead button.
   - The links can be overridden with `SHOW_APPLE_URL` / `SHOW_SPOTIFY_URL`.
5. Alert "**E<N>: drafted — paste the episode links**", listing the Tailor post's two listen buttons, the
   thank-you draft and the reel captions. The schedule records `episode_links_pending: true`.

## Also fixed (found while building this)

- **The reel gate refused every episode without a guest name.** `publish_finalize` falls back to `"Guest"`,
  and `assert_guest_featured` matched speakers against the word "guest", so E32's (and the back catalogue's)
  reels would have been refused. With no real name, the rule is now checked by the host: at least one quote must
  be someone other than Heather. Red on `main`, with the exact refusal text.
- **The resume trigger name mismatch.** `drive_changes` enqueued `"drive_changes_resume"`, but Phase 2 only
  knew `"drive_resume"`, so resumes fell into the e-mail branch. Both names are recognised now.
- **One visual rule.** `episode_files.VISUAL_RE` / `find_visual` are used by both the trigger and Phase 2, and
  folders never match.

## Tests

`test_trig02_visuals_trigger.py`: 10 cases.
- **Red on `main`:** 6 of 10.
- **Green:** 10/10.

All 15 suites are green. `_apply_phase2_completion` stays pure (`test_sched01` executes it in isolation), so
`links_pending` is passed in by the caller.

## The back catalogue

Those episodes are already live, but they run through the same flow: Round 1, review, then drop the images. The
links will be the show pages until John pastes each episode's own. `reprocess_episode.py --episode N --apple …
--spotify …` stays available to run Phase 2 with the real links in one go.
