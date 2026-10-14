# CARD-04 — Every quote carousel opens on a cover slide

**Owner:** Bill (impl) · **Tests:** Jen · **Review:** Toby · **Design:** Joni · **Words:** Lisa · **PO:** Angela
**Priority:** High · **Status:** **BUILT 2026-10-08, layout approved by John** — AC1–AC6
verified locally and in a full rehearsal against a fake box; **AC7 (the live backfill) is
John's run**, see below. Open: whether new episodes' quote slides take the cover's 20px
footer lift (the slides already in Drive stay as they are).
**Created:** 2026-10-08 · **Raised by:** John · **ADR:** 0014

---

## Why

John, 2026-10-08: *"these are working great but need a cover image bc text only doesnt
read in insta thumbz or first impression."* Five carousels per episode open on a text
slide. In the profile grid and the feed's first glance, text alone does not stop a scroll;
a face, a name and a title do.

## Decisions (John, 2026-10-08)

| Question | Answer |
|---|---|
| How many covers | **One per carousel**, five per episode, each slide 1 |
| Who writes the title | **The model drafts, code gates**; a sidecar a person can edit |
| Which image | ~~Tile first~~ → **the speaker's reel image, tile as fallback** (revised the same day: the E30/E31 tiles in Drive have no seam between text and photo); Heather's host image |
| Backfill scope | **Whatever Drive has**; add the covers, never redo the originals |

## Acceptance criteria

1. **A cover per carousel.** `E{N}_{qid}_0cover.png`, 1080×1080, beside the quote slides in
   `cards/`. `0cover` sorts before `1ofN`, so Drive lists it first.
2. **The cover shows** the speaker's photograph (from the tile or reel, the photo only, never
   the design's own text panel or purple bands), the speaker's name (the host's canonical),
   the carousel's title, the podcast lockup small in the cards' own footer, and the episode
   line. Brand purple field, Avenir Next (Condensed) type.
3. **The title is gated** before render: 2–8 words, ≤ 44 characters; no `?!…`, emoji, `&`,
   URL, hashtag or handle; no claims words (CONTENT-CANON §4), no bait (§2), no candour
   signal (PQ-5b), no first person; never five consecutive words of the quote.
4. **Fixed type.** Title 68px, centred below the photo, ≤ 3 lines; name beside the photo,
   first name 49px on its own line, the rest 41px. Anything that does not fit fails the
   render with its cause. Nothing shrinks (ADR-0012). Layout revised twice by John on
   2026-10-08 (square photo top right, name right-justified on its left and split after
   the first name, title centred below, type +15%); see ADR-0014.
5. **Future episodes get covers in the normal run.** `render_cards.py --episode N` refuses
   to start without `cover_titles.json` (so a coverless set never looks finished), then
   renders slides and covers; `--upload` pushes both.
6. **The backfill is add-only.** `card_upload.py --covers-only` uploads only `*_0cover.png`
   into an existing `cards/`; never trashes, replaces or creates; skips a cover already there.
7. **Backfilled in Drive** for every episode whose `cards/` exists. ← John's run.

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 2 | 25 covers rendered from local data (E23, E24, E25, E27, E29), contact sheet reviewed; E30/E31 rendered in rehearsal with stand-in photos |
| 2 | `test_photo_box_*` (synthetic tile + reel), `test_pick_*` (6 cases incl. E27's guest Heather, two guests, missing image) |
| 3 | `test_title_gates` (19 named failures; 4 plain titles not over-caught), `test_titles_refuse_a_moved_quote`; live catch: E31 q01's first draft copied the quote and was rejected |
| 4 | `test_render_*` in real Chromium: a cover renders; an unfittable title fails |
| 5 | Integration run of `render_cards.py --episode 29` in a scratch copy: 10 slides **byte-identical to `main`'s output** + 5 covers; without the sidecar it stops before any slide |
| 6 | `test_add_covers_*` (incl. exact folder, no lone covers); rehearsal: 20 uploads, re-run 0, zero trash/create calls; post-review rehearsal: 12 uploads, 8 lone covers held back |
| — | `test_card01/02/03`, `test_reel08` and the rest pass; `test_compose` (needs a live key) and `test_hf01` (path guard rejects the worktree location; passes in `~/RockstarPodcast`) are unrelated |

## Cold review (DOD-3, 2026-10-08)

Nothing severe. The add-only guarantee, every `rm` path and bash 3.2 behavior were checked,
and a title full of shell metacharacters survived ssh. Fixed and tested:

1. A guest with no image of their own could inherit the host's lone reel image → `_names_host` guard.
2. `find_episode_folder("E3-")` is a `name contains` query that can return E30 → exact `^E{n}-` match before any write.
3. A cover could be pushed for a carousel that isn't in Drive → a cover only joins existing `E{n}_{qid}_*of*.png` slides.
4. A stale local sidecar could put titles on moved quotes → per-quote fingerprints in `cover_titles.json`; a mismatch refuses.
5. `E30_Graham_tile.jpg` was never fetched (`\b` treats `_` as a word character) → fixed regex.
6. The gates over-caught "How Your Brain Works", "Treat Sleep…" and "Part I", and under-caught "Healing", "Cured", "Hacks",
   "Secrets" and "..." → pattern gates; curly and straight apostrophes are now equal in the restatement check.
7. A title that isn't a string crashed the retry path → it is now rejected as a named failure.
8. `render_cards` checks every cover image before any slide renders.
9. Box scratch cleanup now runs on every exit (`trap`).

## Operating notes

- **A new episode now needs its titles before `render_cards.py`.** `make_cover_titles.py` needs the model key, which
  lives on the box. On the Mac, write `cover_titles.json` by hand or draft on the box (as the backfill does).
- **Add-only means a pushed cover is never replaced.** To correct one, delete it in Drive, then re-run `--push`.
- Titles reach the box through `printf %q`; root's shell there is bash, which reads `$'…'`.

## The backfill (AC7)

```bash
cd ~/RockstarPodcast-card04
scripts/backfill-covers.sh           # Drive list → fetch → titles → render; writes nothing to Drive
open Episodes/_cover_backfill/_sheet.jpg
scripts/backfill-covers.sh --push    # adds the covers; re-runnable
```

Titles already drafted (2026-10-08, in session, gated) for E23, E24, E25, E27, E29, E30,
E31 sit in each local `Episodes/E{N}-*/cover_titles.json`; the wrapper keeps them. Any
other episode Drive turns up gets a model draft on the box. E23's `apple_summary.md` has
no title line: `TITLE_E23="Red Light and Blue: Our Midlife Clean Energy Future"`.

## New episodes with no cards yet (2026-10-08)

```bash
scripts/new-cards.sh 25 28 33          # fetch from Drive, draft titles + captions on the box, render here
open Episodes/_new_cards/E*/_sheet.jpg  # and read the captions in each cards/
scripts/new-cards.sh --push 25 28 33   # uploads exactly what was reviewed; nothing redrafted
```

The box's own prompts are used (Heather tunes them, D-5). A staged `cover_titles.json` or
captions survive a re-run, so edit them in place. The push refuses any episode whose Drive
`cards/` already has files. Rehearsed against a fake box: 3 sets built and pushed, E29
refused at fetch, a second push refused, zero trash calls.

## Found along the way (not in scope)

- **REEL-08 hands E27's guest the host's photo.** `episode_files.background_for` treats
  any speaker whose first name is "heather" as the host. E27's guest is Heather
  Vashti-Mercer, so a re-rendered E27 reel would get `reel_background_host.jpg`. The
  cover code matches the host by surname; the reel rule should too. Needs its own story.
- **E30's transcript spells the host "Heather Reilly-Hemstra".** The cover prints the
  canonical name; the quote slides print the transcript's.
