# REEL-09 — One rule for "is this Heather the host?", by name, tolerant of misheard and hyphenated spellings

**Owner:** Bill · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **BUILT 2026-10-08** — `host_identity.py`; `test_reel09_host_identity.py` 6/6; suite 17/17
**Created:** 2026-10-08 · **Found by:** CARD-04 (cold review and the E28/E30 builds)

---

## Why

Three places decide whether a speaker is the host, and they disagree:

| Where | Rule | Wrong on |
|---|---|---|
| `episode_files.background_for` (REEL-08, reels) | first name is `heather` | **E27**: guest Lorna Vashti-Mercer is treated as the host, so a re-rendered E27 reel gets `reel_background_host.jpg` — Heather Reilly Hiemstra's photo behind another woman's words |
| `summary_norm.canonicalize_speaker` / `HOST_NAME_RE` (Round 1) | "Heather Riley/Reilly …" with a space-separated surname | **E30** "Heather Reilly-Hemstra", **E28** "Heather Riley-Hemstra": the hyphen defeats the regex, so the misspelling survives into `quotes.json` and onto slides |
| `quote_cards/cover_image.is_host` (CARD-04) | surname reilly, or ≈hiemstra | correct, but a fourth copy of the same idea |

A reel showing the wrong person is the failure REEL-08 exists to prevent; a misspelled host name on a
published card is the one the R1 canonicalizer exists to prevent.

## The canonical name (John, 2026-10-08)

**Heather Reilly Hiemstra**, no hyphen. Reilly is her legal middle name, Hiemstra her surname. That
is already `summary_norm.HOST_NAME`, the blog byline and the RSB site's form, so nothing is renamed.
The transcripts' misspellings, hyphenated ones included ("Reilly-Hemstra", "Riley-Hemstra"), must
all come out as exactly this form.

## Change

0. **One `HOST_NAME`** ("Heather Reilly Hiemstra"), in `summary_norm`; `blog_post` imports it rather than
   keeping its own copy.
1. **One primitive, `summary_norm.is_host(speaker)`**, beside `HOST_NAME`: the name has the first name
   Heather **and** a host surname (Reilly/Riley/Rielly…, or an H-word ending like Hiemstra), joined by a
   space **or a hyphen**. "Heather" alone stays the host (today's behaviour for a bare first name). Another
   surname (Vashti-Mercer) is never the host.
2. **`HOST_NAME_RE` accepts the hyphenated forms**, so Round 1 writes "Heather Reilly Hiemstra" into
   `quotes.json` in the first place.
3. **Every caller uses it:** `episode_files.background_for` (replacing `HOST_FIRST_NAME`),
   `publish_finalize._guest_is_host`, `quote_cards/cover_image.is_host` / `display_name`, and the slide
   citation in `render_cards.py`. No module keeps its own host rule.

## Acceptance criteria

1. `background_for("Lorna Vashti-Mercer", …)` never returns `host-default`; with E27's single unnamed
   reel image it returns that image (`single`).
2. `is_host` is true for "Heather Reilly Hiemstra", "Heather Reilly-Hemstra", "Heather Riley-Hemstra",
   "Heather Riley Heemstra", "Heather"; false for "Lorna Vashti-Mercer", "Heather Locklear".
3. `canonicalize_speaker` turns "Heather Reilly-Hemstra" and "Heather Riley-Hemstra" into
   "Heather Reilly Hiemstra"; no output anywhere hyphenates her name.
4. `test_reel08_speaker_backgrounds.py`, `test_r1_reliability.py` and `test_card04_covers.py` pass with the
   new cases added; no module defines its own host rule (grep for `HOST_FIRST_NAME` returns nothing).
5. Nothing already published changes: E27's reels were rendered before REEL-08 and are not re-rendered by
   this story. A future E27 reprocess picks the right image.

## Notes

- Deploy: `episode_files.py`, `summary_norm.py`, `jobs/publish_finalize.py` are in `deploy.sh`'s FILES;
  the worker restarts. Run `deploy.sh --check` first.
