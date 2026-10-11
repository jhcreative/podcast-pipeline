# ADR-0013 — Quote-card captions: model writes three parts, code writes the rest

**Status:** ACCEPTED 2026-07-25 (John)
**Deciders:** John (owner), Angela (PO), Toby (design call), Jen (gates)
**Related:** ADR-0007 (caption rules), ADR-0010 (deterministic thank-you), ADR-0012
(quote cards), ADR-0002 (drift tolerance), ADR-0008 (hand-edited artifacts authoritative)
**Story:** CARD-03

## Context

The quote cards (ADR-0012) ship as images with no accompanying copy. John asked for an
Instagram caption per post: the guest introduced compellingly from the `About <Guest>`
section of `apple_summary.md`, a connect line naming the guest with a manually-filled
social handle, a listen line, and relevant hashtags — deposited alongside the cards.

## Decision

**1. The model writes exactly three things:** the guest introduction (once per episode),
each post's opening line, and each post's hashtags. Everything else — the connect line,
the listen line, the layout — is assembled in Python.

The split is not stylistic. The connect line carries a real person's name and the listen
line is the show's standing CTA; both are fixed copy. ADR-0010 established what happens
when fixed copy is routed through a model, and ADR-0007 already drew this exact line for
the Uppbeat credit: *the part that must be exact is made exact by construction.*

**2. Captions are per-post, not per-episode.** Each opens on its own quote's idea, then
carries the shared guest block. Five identical captions in a row read as automated.

**3. Hashtags inherit ADR-0007 unchanged** — exactly 3–4, one show tag plus 2–3 topical,
chosen against that post's quote from the existing pools in `seo_captions.md`.

**4. No music credit, ever.** The Uppbeat attribution belongs to the reels, whose theme
track is licensed. A quote card is a silent image; crediting a track it does not play
would be a false attribution — the opposite of what ADR-0007 was protecting. Asserted in
code, not merely omitted from the prompt.

**5. No URLs.** Captions here never carry platform links; the listen line says "link in
bio". Also asserted — a model-written URL can be subtly wrong, and the publisher has
supplied wrong Apple URLs before.

**6. A missing bio is a marked gap, never an invented one.** `About <Guest>` is
legitimately a placeholder for some episodes — E24's guest never filed an intake form,
so the section reads `[BIO PENDING — supplied manually…]`. When the source is pending,
the caption carries `[BIO PENDING — add before posting]` and the run says so. The prompt
instructs the model to return the literal `BIO_PENDING` rather than compose from the
quotes or from general knowledge. **Inventing credentials for a real person is the one
failure here that cannot be walked back after posting.**

**7. The social handle is a placeholder by design** — `[ADD @HANDLE]`, filled by hand.
We do not have a reliable handle source, and guessing one risks tagging a stranger.

## Consequences

- Captions need an API key at run time; `--dry-run` exercises assembly and every gate
  with a marked stub, so the shape is verifiable without one.
- Two ACTION lines print after every run: fill the handle, and fill the bio if pending.
  A caption posted with `[ADD @HANDLE]` visible is embarrassing but obvious — which is
  why the placeholder is loud rather than blank.
- The hashtag assertion is the generation-time check **CAP-01** asked for on the reel
  captions and never got. It exists here; the reel path still relies on the prompt alone.
- Uploading is owned by `render_cards.py --upload`, which pushes the whole `cards/`
  folder. Two tools each clearing the folder before pushing only their own files would
  have had the second run trash the first's output.
- Because the clear necessarily precedes the uploads, **a mid-upload failure leaves the
  Drive folder cleared but not repopulated.** Local files are untouched and re-running
  `--upload` restores it, so this is recoverable — but it is a real intermediate state,
  not a theoretical one, and it is asserted in `test_card02_upload.py` rather than
  discovered later.

## Amendment — 2026-07-27, after the first live run

Decision 6 said a missing bio must never be invented. E31's first real run surfaced the
softer neighbouring failure: the bio was **present**, nothing was inflated, and the model
still **extended** it — adding "estrogen, the menstrual cycle, and perimenopause" to a bio
that says "female-specific physiology" and names endometriosis. True of the episode, not
of the biography.

Two responses, and deliberately not a hard gate:

- The prompt now forbids naming a condition, hormone, mechanism or body system the bio
  does not name, *even when the episode is plainly about it*.
- `make_captions.novel_terms()` prints every content word in the intro that is absent
  from the About section, as a REVIEW line. **Advisory by design** — paraphrase
  legitimately introduces words, so a hard failure would block good copy, and a check
  that cries wolf gets ignored. It tells a human where to look instead of pretending to
  judge. Known false positive: irregular verb forms (`led` / `leading`).

The general shape is worth stating plainly, because it will recur: **the structural gates
prove a caption is well-formed; they cannot prove it is true.** Truth about a real person
is defended by the prompt, the review line, and a human read — in that order.

## Also fixed in passing

`quotes.json` exists in **two schema generations** — old episodes (E23–E25) carry
`index` + `quote_text_clean`; newer ones (E27+) carry `id` + `text`. Both the renderer
and the caption writer now read either, per ADR-0002's rule that drift is supported
rather than chosen against. Without this, every back-catalogue episode would have failed
with a `KeyError` partway through writing a set.
