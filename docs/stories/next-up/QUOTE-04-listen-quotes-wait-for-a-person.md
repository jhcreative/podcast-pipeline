# QUOTE-04 — A `listen` quote waits for a person before it becomes a reel or a card

**Owner:** Bill · **Tests:** Jen · **Review:** Toby · **Words:** Lisa · **PO:** Angela
**Priority:** High. **Must land before Phase 2 renders a `listen` quote unattended** (Toby's re-review): that is the next real
episode with visuals, the E6 and E13 re-runs. Until it lands, John or Heather reads the standup's "listen before posting" list
before dropping the visuals.
**Status:** **Proposed** (Angela, 2026-10-12), not built
**Raised by:** Toby's cold review of QUOTE-01 slice 1 (2026-10-12), finding 3: *"LISTEN quotes go to Phase 2 without a person
ruling on them."*
**Parent:** QUOTE-01. Slice 1's gate drops `not_verbatim` quotes and keeps `listen` ones, marked. Today the standup line is the
only place a `listen` shows up.

## The gap

Round 1's gate (step 10b) keeps a quote with `tape_check.verdict == "listen"`: a join, an unruled mishear, a cut that left out a
negation, mixed speakers, or words under another speaker's label. Each needs a person's ear. Phase 2 starts when the visuals are
dropped and renders every kept quote as a reel and a card. Nothing stops a dropped negation from being posted unheard.

## Also in scope: a long_form quote waits for its approval (Toby's CLIP-01c review)

John's ruling (2026-10-12): a quote over the standard ceiling may run long *"only if it's really a good quote"*, which
means Lisa's written `long_form_reason` **and** a person's approval. CLIP-01c enforces the reason. The approval is
only the standup line "kept whole as long_form (approve before posting)". It's the same gap as `listen`: a long_form
reel renders and goes to Drive with nobody having approved it. The same ruling record covers both: a long_form reel
renders only after a recorded "keep".

## A known limit this story also closes: the speaker anchor

QUOTE-01's speaker check trusts a name with a label only when all its quotes sit under that one label and no other name
shares it. It can't catch a guest with one quote, or a guest whose every quote is the host's words: with no anchor for the
host's label, one label per name proves nothing. **The anchor:** the label of the segment where the host names herself
(canonical name), or a label recorded at intake. A quote under the host's anchored label but given to the guest is
`not_verbatim`.

## Acceptance criteria (draft, for John)

1. Phase 2 renders reels and cards only for quotes whose `tape_check.verdict` is `verbatim`, or `listen` with a recorded ruling.
   A `long_form` quote needs a recorded ruling too, whatever its verdict.
2. A ruling is written to a file (the ear-corrections record QUOTE-02 uses), never in a prompt: quote id, who listened, the date,
   and keep or drop.
3. A held quote is named in Phase 2's standup line and its internal alert. The rest of the episode carries on.
4. A quotes.json with no `tape_check` (an episode from before QUOTE-01) is treated as it is today, and the standup line says so.
5. The speaker anchor above. A guest quote under the host's anchored label is `not_verbatim`, and a guest with one quote is
   checked against it.
6. Tests: a `listen` quote without a ruling makes no reel and no card. With a "keep" ruling it renders. With a "drop" ruling it
   doesn't, and the drop is recorded.

## Open questions

| # | Question | Recommendation | Answer |
|---|---|---|---|
| Q1 | Hold the quote (Phase 2 waits) or skip it (Phase 2 runs on without it)? | Skip it and name it: the rest of the episode isn't held up, and the quote's reel can be rendered later | |
| Q2 | Who rules: John, Heather, or either? | Either, with the name recorded | |
