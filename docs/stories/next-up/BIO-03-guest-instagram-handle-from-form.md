# BIO-03 — The caption's connect line carries the guest's own Instagram handle

**Owner:** Bill · **Tests:** Jen · **Words:** Lisa · **PO:** Angela
**Priority:** Medium · **Status:** **READY 2026-10-08** · **Raised by:** John (*"I like your follow-up — file that story on the side"*)
**Related:** BIO-01 (the guest form is the source), CARD-03 (captions), CARD-05 (cards in Phase 2)

---

## Why

Every quote-card caption ends with `Connect with {Guest} at [ADD @HANDLE]`, and every run prints
*"ACTION: replace [ADD @HANDLE] with the guest's handle"*. That's five hand edits per episode
before Heather can post, and a placeholder that ships if one is missed. The guest already fills in
the "Podcast Guest Info" form (BIO-01), which asks for their name, email, bio and website, but not
their Instagram.

## Change

1. **The form asks for it.** John adds one question: *"Your Instagram handle (optional)"*. That's a
   form change, so it's John's; nothing here edits the form.
2. **`guest_form.py` reads it** as an optional column, `instagram`. A sheet without the column
   still works, because the question is new and older answers predate it. (Today a missing header is
   an error; this one is the documented exception.)
3. **The handle is normalized, never invented.** Accept `@name`, `name` or an `instagram.com/name`
   URL; reduce it to `@name`; check Instagram's rules (1–30 characters, letters, digits, `.` and
   `_`). Anything else, or an empty answer, keeps `[ADD @HANDLE]`, so a person still decides.
4. **`make_captions.py` fills the connect line** from the matched guest's handle, still in code and
   never by the model (ADR-0013: fixed copy carrying a real person's name stays out of the model).
   The ACTION line prints only for the captions that still hold the placeholder.
5. **The handle flows to Phase 2** the same way the bio does (BIO-01's match), so CARD-05's Step 12b
   captions get it with no extra step.

## Acceptance criteria

1. A form row with `@imogen.farrelly`, `imogen.farrelly` or `https://instagram.com/imogen.farrelly/` gives
   `Connect with Imogen Farrelly at @imogen.farrelly`.
2. An empty answer, an invalid handle (spaces, 31+ characters, other symbols) or no matched guest
   keeps `[ADD @HANDLE]`, and the ACTION line names those captions.
3. A sheet without the new column reads exactly as today: no error, placeholder kept.
4. The caption gate (`assert_caption_wellformed`) still passes with a handle in place of the
   placeholder; no model call ever sees or writes the handle.
5. PRIVACY (BIO-01's rule): the handle is personal data, so logs show counts and match rules, never
   the handle itself. `test_r1_reliability.py`'s sentinel test covers the new column.
6. Host-only episodes (Heather as the featured guest) use Rockstar's own handle, which is fixed copy
   John supplies once. Until then they keep the placeholder.

## Open question

| # | Question | Answer |
|---|---|---|
| Q1 | Rockstar's handle for host-only episodes (AC6)? | |
