# REEL-08 — Each reel's background is its speaker's image; Heather as the featured guest

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** High · **Status:** Built 2026-10-01

John, 2026-10-01: *"we will have episodes where Heather is featured guest, so handling for that is key including
matching reel backgrounds to correct speaker"*. Naming: *"E32-reel-name"*. Heather-as-guest thank-you: *"skip it"*.

## Rules (`episode_files.background_for`)

1. **A reel image whose filename names the speaker's first name.** `E13-reel-john.jpg` and `E13-john-reel.jpg`
   both work, because filename words are compared, not their order. Honorifics are ignored ("Dr. Avery" →
   `avery`). Two images naming the same person stop the run ("keep one").
2. **Heather with no image of her own** → `BrandAssets/reel_background_host.jpg` (today's behaviour).
3. **Exactly one reel image that names no other speaker** → used for everyone else (E32 and E6 today: unchanged).
4. **Otherwise Phase 2 stops before rendering**, with an alert naming the speaker and the file to drop. A reel
   showing the wrong person is worse than a held one. A third speaker never borrows another speaker's image.

Phase 2 writes `background_path` onto each reel quote (step 9c). `render_reel.js` uses it first and falls back
to the old host/episode choice.

## Heather as the featured guest

- The reviewed summary's "About Heather Reilly Hiemstra" fills `guest_name` (#17), so the guest gate passes a
  host-only quote set.
- **The thank-you draft is skipped.** The "paste the episode links" alert still goes out.
- A different guest named Heather ("Heather Smith") is not treated as the host.

## Also fixed

**A Phase 2 gate refusal now alerts once and stops.** It used to be an "unhandled error": the worker retried it
5× silently, recomposing the FAQ each time, and nobody was told. Now: one alert, "E<N>: Phase 2 stopped — fix
and re-drop an image", with the gate's reason, then `PermanentFailure`.

## Tests

`test_reel08_speaker_backgrounds.py`: 9 cases. **Red on `main`**, **green 9/9**. All Python suites, plus
`render_reel.test.mjs`, `test_speaker_role.js`, `test_reel_selection.js` and `test_clip_boundaries.js`, are
green.
