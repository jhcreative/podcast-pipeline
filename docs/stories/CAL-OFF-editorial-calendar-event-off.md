# CAL-OFF — Turn the Round-1 editorial calendar event off (behind a switch)

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** Medium · **Size:** S
**Status:** Built 2026-10-01 (branch `fix/r1-reliability`) · **Created:** 2026-10-01 · **Raised by:** John

---

## Why

John, 2026-10-01: *"we can remove the calendar stuff for now - not helpful yet"*.

Round 1 step 13 creates a Google Calendar event, "E{N} editorial review — {Guest}"
(`jobs/round_1.py:519-535`, `calendar_client.create_editorial_event`), and nobody is using it yet. Worse, its
checklist says things that are no longer true:

- *"Add guest bio in this event description"*: nothing reads the event description. The bio reaches the
  post through `apple_summary.md` (see BIO-01).
- *"Approve blog_page.html draft"*: this is BLOG-02 finding F-1. The deployed `calendar_client.py` is the
  May version, because `calendar_client.py` is not in `deploy.sh` FILES.

An event that sets people the wrong tasks is worse than no event.

## What already holds (read 2026-10-01)

- `publish_finalize` reads `calendar_event_id` from the schedule entry (`:471`). When the id is empty, step 14
  is already skipped with a warning (`:1134-1147`), and a failed update never blocks finalization. **Nothing
  downstream needs the event.**
- On a reprocess, Round 1 reuses an existing id (`:520-526`) and writes it back to the schedule entry (`:554`,
  `:575`).

## Change

1. Add a setting `EDITORIAL_CALENDAR_EVENT` (default **off**). It is read in Round 1 the same way as the
   pipeline's other `.env` settings.
2. When it is off, Round 1 makes **no Calendar API call**, logs
   `round_1: editorial calendar event disabled (EDITORIAL_CALENDAR_EVENT=off)`, and writes
   `calendar_event_id: ""`. On a reprocess, an id already in the schedule is **kept as it is**: we neither delete
   the event nor overwrite the id.
3. Change publish_finalize step 14's log level for an empty id from warning to info **when the setting is off**,
   because an empty id is then expected, not a fault.
4. Leave `calendar_client.py` and the scope in place, so switching it back on is one line in the `.env`.
5. Add `calendar_client.py` to `deploy.sh` FILES anyway (this closes BLOG-02 F-1). The box's copy is then a
   known copy.

## Acceptance criteria

- **AC-1** (tests go red first, Jen): with the setting off, Round 1 makes no call to `create_editorial_event`
  and writes an empty `calendar_event_id`. With it on, the event is created exactly as today.
- **AC-2:** on a reprocess with the setting off, an existing `calendar_event_id` stays unchanged and no event
  is created or deleted.
- **AC-3:** publish_finalize with an empty id completes every other step. Step 14 logs at info and makes no
  Calendar call.
- **AC-4:** `deploy.sh --check` lists `calendar_client.py`. After deploy, it reports
  `local==git==deploy OK`.
- **AC-5:** deploy only when no job is pending or running, and after E32 has finished its whole run. A deploy
  restarts the worker and would kill a running job.

## Not in scope

Deleting existing calendar events, or removing the Calendar scope. Both are a later decision if the event never
comes back.

## Built (2026-10-01)

- `jobs/round_1.py`: `calendar_event_enabled()` (default **off**). The only `create_editorial_event` call sits
  behind it. With the switch off, an id already on the schedule is kept.
- `jobs/publish_finalize.py`: an empty id with the switch off logs at info.
- `scripts/deploy.sh` FILES: `calendar_client.py` (closes BLOG-02 F-1).
- Tests (`test_r1_reliability.py`): `test_calendar_off_by_default_and_switchable` and
  `test_calendar_create_only_behind_the_switch`. `test_title01_gen_gate.py` sets the switch **on** to keep its
  event-on coverage.
