# CARD-03 — An Instagram caption for every quote-card post

**Owner:** Bill (impl) · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **DONE — all acceptance criteria verified
2026-07-27** (E31 real bio, E24 pending bio, both run live on the box).
**Created:** 2026-07-25 · **Raised by:** John

---

## Why

The cards ship as images with no copy. Heather would otherwise write five captions per
episode by hand, from a bio that already exists in `apple_summary.md`.

## Acceptance criteria

1. **One `.txt` per post**, written beside that post's PNGs in `Episodes/E{N}-*/cards/`.
2. **Fixed shape**, assembled in code:
   ```
   {opening — model, specific to this post's quote}

   {guest intro — model, once per episode, from the About section}

   Connect with {Guest Name} at [ADD @HANDLE]
   Listen to E{N} on Apple Podcasts & Spotify — link in bio.

   {3–4 hashtags — model, chosen against this post's quote}
   ```
3. **Guest intro comes only from `About <Guest>`** in `apple_summary.md`, sharpened —
   never extended. No credential appears that the source does not already carry.
4. **A pending bio produces `[BIO PENDING — add before posting]`**, never a composed one.
5. **Gates, all hard failures:**
   - hashtags outside 3–4 (ADR-0007)
   - no show tag present
   - malformed hashtags
   - any URL in the caption
   - any music/audio credit (a silent card must not claim the reel's licence)
   - connect line with neither a handle nor the placeholder
   - opening line that restates the quote verbatim
6. **`--dry-run`** exercises assembly and every gate with a marked stub, no API key.

## Evidence (2026-07-25)

- `--dry-run` on **E29** (new schema, no `apple_summary.md`): 5 captions, guest name
  correctly falls back to the sole quote speaker, bio gap marked, all gates pass.
- `--dry-run` on **E25** (old schema, real bio): About parser returns `Bryony Castellane`
  and a non-pending bio; 5 captions, all gates pass.
- About parser checked against both real `apple_summary.md` files in the repo, including
  **E24's genuine pending-bio case** — correctly detected as pending.
- **Schema drift found and fixed:** `quotes.json` exists in two generations
  (`index`+`quote_text_clean` vs `id`+`text`). New `quote_source.py` reads either; both
  the renderer and the caption writer use it. E25 now renders cards too — it could not
  before.

## Tests (2026-07-27) — `test_card03_captions.py`, 17 passing

About parser (heading level and case tolerance per ADR-0008, stops at the next rule,
returns None rather than guessing when absent), pending-bio detection against both real
`apple_summary` shapes, a golden byte-equality check on the assembled caption, and all
seven gates — hashtag count both directions, missing show tag, malformed tags, URL,
music credit, blank connect line, and an opening that restates the quote. A filled-in
real handle is asserted to pass, so the placeholder gate does not block John after he
edits it. Two more (19 total) cover `novel_terms`: E31's borrowed episode topics are
surfaced, and a faithful paraphrase stays quiet.

Fixtures are inline rather than read from `Episodes/`, which is gitignored — a test
needing artifacts a fresh clone lacks is a test that quietly stops running.

## Live run — E31, 2026-07-27 (AC1 ✅, AC3 ✅)

Run **on the Hetzner box**, where `ANTHROPIC_API_KEY` already lives, so no production
secret reached the Mac. `make_captions.py` is pure text — no fonts, no Playwright — so
it runs there unchanged. The repo's `anthropic_client.py` was shadowed from a temp dir
rather than deployed (the box still runs the deployed build).

Five captions, 107–121 words each, 4 hashtags each, no URLs, no music credit, all seven
gates passing. Output pulled back to `Episodes/E31-leyla/cards/`.

### The finding: the model extended the bio, and no gate could see it

First run wrote *"…female-specific biology — **including estrogen, the menstrual cycle,
and perimenopause**"*. All three are true of the **episode**; none is in the bio, which
says "female-specific physiology" and names **endometriosis**. No credential was
inflated — but the rule is *sharpen, don't extend*, and it extended on the first real
run against a real person's biography.

Two changes:
1. **Prompt** — explicit: do not name a condition, hormone, mechanism or body system the
   bio does not name, *not even one the episode is plainly about*. Borrowing the
   episode's subject matter into the biography reads as accurate and is not.
2. **`novel_terms()`** — an advisory review line printing every content word in the
   intro absent from the About section. Not a hard gate: paraphrase legitimately
   introduces words, so failing would block good copy. It names what to look at.

After the prompt fix the same run flagged **one** word (`leading`, from the source's
irregular "she **led** two landmark projects" — a known false positive of prefix
matching) and the borrowed topics were gone. Every claim in the final intro traces to
the bio.

## Heading-less bios — E29, 2026-07-27

E29 reported a **pending bio while a real one sat in the file**. Its `apple_summary.md`
is hand-edited in Drive: the Docs export renders the horizontal rule as a row of
underscores and the `About <Guest>` heading is gone entirely — the bio is simply the
tail of the document.

ADR-0008 makes hand-edited summaries authoritative, so the parser has to read what a
human typed. `extract_about` now takes a `guest_hint` (the sole quote speaker) and, when
no heading exists, falls back to the **last paragraph that begins with the guest's
name**, taking it to the end and dropping divider rows. Requiring the paragraph to open
with the name keeps it from grabbing the hook intro, which mentions the guest but opens
on Heather.

Without this, E29's captions would have shipped a `[BIO PENDING]` gap for no reason.
Covered by two tests, including one asserting the hook intro is not captured.

## Pending-bio run — E24, 2026-07-27 (AC2 ✅)

E24's guest never filed an intake form, so `About Rosalind Teague` reads
`[BIO PENDING — supplied manually…]`. Run live on the box:

- The **model returned the literal string `'BIO_PENDING'`** — verified by calling
  `compose_card_captions` directly and printing the raw field. It did not compose a bio
  from the quotes, the title, or general knowledge.
- The full run then emitted five captions carrying `[BIO PENDING — add before posting]`
  and printed the ACTION line.

**Two independent defenses, confirmed separately.** The first full run proved only the
*code* path, because `make_captions` overrides the intro whenever `bio_is_pending(about)`
is true — regardless of what the model says. That belt-and-braces is worth keeping, but
it also masks the model's behaviour, so the raw call had to be inspected on its own to
close this AC honestly.

E24 is **old-schema** (`index` + `quote_text_clean`), so this run also exercised
`quote_source` normalization through the live path.
