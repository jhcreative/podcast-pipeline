# ADR 0005 — Idempotent enqueue via a dedup-key partial unique index

- **Status:** Accepted
- **Date:** 2026-06-23
- **Deciders:** John (owner); Angela facilitating; Bill (backend, impl); Toby (review/infra); Jen (tests)
- **Story:** PUB-01 — Idempotent publisher-email trigger
- **Builds on:** ADR-0002 (publisher email as a drifting contract). The reply-guard
  there stopped *replies in the thread* from firing; this stops the *original*
  notification from re-firing.

## Context

One publisher "Episode is Live" email produced **8 / 3 / 11** `PUBLISH_FINALIZE`
jobs (message_ids `19e7018dabe1d91f`, `19ed1689f95595b1`, `19edd73ebafa7312`).
Three facts compose into a duplicate-enqueue loop:

1. **Blind search, not a delta.** `gmail_webhook` ignores the Pub/Sub `historyId`
   and runs `list_unread_messages("from:(publisher) is:unread")` on every push.
   It asks "is there an unread publisher email *now*", not "what changed".
2. **Nothing marks the email read.** No `modify`/`removeLabel` call exists; the
   email stays `is:unread` after its job completes.
3. **`db.enqueue` was a blind INSERT** — no dedup key, no constraint.

Pub/Sub is at-least-once *and* Gmail fires one notification per mailbox change, so
each push re-finds the still-unread email and enqueues again, until newer unread
mail pushes it out of the `max_results=5` window.

Blast radius is cost and queue hygiene, not content: downstream is guarded by
`.pipeline_done`, so no duplicate artifacts ship. What we pay for is redundant
Claude work (FAQ + blog generation × N) and a job history littered with confusing
duplicate `failed` rows.

## Decision

Make `db.enqueue` idempotent on the **identity of the trigger**, enforced in the
database, not in application code.

- **Dedup key = the trigger's stable identity.** A new nullable `jobs.dedup_key`
  column. For the Gmail trigger it is the Gmail `message_id`. It is the *trigger*
  identity, not the episode — a legitimate reprocess of the same episode through a
  different trigger is unaffected (criterion A4).
- **Constraint lives in the DB, via a *partial* UNIQUE index:**
  `CREATE UNIQUE INDEX … ON jobs (dedup_key) WHERE dedup_key IS NOT NULL`.
  Two concurrent webhook calls with the same key cannot both insert (A3). The
  `WHERE` clause exempts the entire pre-existing history (all `dedup_key` NULL),
  so the index is safe to add to the live `jobs.db` without touching one old row.
- **`ON CONFLICT … DO NOTHING`, then read back the surviving row.** A duplicate
  INSERT becomes a no-op instead of raising `IntegrityError`; `enqueue` then
  selects the row already owning the key and returns its id, so the caller always
  gets a real job id to log and act on (A1, D).
- **NULL keys keep today's behaviour.** `dedup_key=None` (ROUND_1, manual
  reprocess, drive-resume) is the default; those callers are unchanged and every
  call still inserts a fresh row (criterion 8). The signature change is additive —
  a new trailing optional param — so no existing call site needed editing.
- **Migration runs at startup, before the schema script.** `init_db()` calls
  `_migrate()` first (additive `ALTER TABLE … ADD COLUMN` guarded by a
  `PRAGMA table_info` check), then `executescript(_SCHEMA)` to create the partial
  index. Ordering is load-bearing: `CREATE TABLE IF NOT EXISTS` will not add a
  column to a table that already exists, and the index references the column — so
  the column must exist before the index is built.

### The SQLite gotcha worth recording

To upsert against a **partial** unique index, the `ON CONFLICT` conflict target
must repeat the index's `WHERE` predicate:

```sql
ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
```

A bare `ON CONFLICT (dedup_key)` fails at parse time —
*"ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint"* —
because SQLite has no *total* unique constraint on the column to match; the only
unique guarantee is the partial index, and you name a partial index by restating
its predicate. Verified against the deployed SQLite (lib 3.51.0). This is easy to
get subtly wrong (it looks redundant) and silently absent from most upsert
examples, so it is captured here so the next person does not rediscover it.

### Mark-read (criterion B) is belt-and-suspenders, not the guarantee

Criterion B tombstones the trigger (mark the email read so it stops matching
`is:unread`) to shrink the redelivery window. It is explicitly **not** the
correctness mechanism: criterion A (the DB constraint) holds even if mark-read
never runs (A2). Mark-read is therefore required to be **non-fatal** — the job
enqueues regardless, and a mark-read failure is logged, never propagated. Putting
correctness in the database and hygiene in Gmail keeps the guarantee independent
of a second fallible API call. (Mark-read is staged as a `TODO` in
`webhook_service.py` for the next session; criterion A ships first and stands on
its own.)

## Consequences

- **+** A publisher email enqueues exactly one job no matter how many
  notifications it generates. Redundant Claude cost and duplicate `failed` rows
  stop. The guarantee survives Pub/Sub redelivery independent of email read-state.
- **+** Migration is additive and reversible-by-omission: existing history is
  untouched and exempt; a brand-new DB builds the current shape whole.
- **+** Zero call-site churn — the optional param defaults to today's behaviour.
- **−** A suppressed INSERT still consumes an AUTOINCREMENT rowid, so job ids show
  small gaps after a duplicate. Cosmetic; ids stay unique and monotonic.
- **−** Dedup is keyed on `message_id`. If the publisher ever sent a genuinely new
  notification reusing a prior `message_id` (Gmail does not), it would be
  suppressed. Not a real risk for Gmail message ids.
- **Deploy dependency:** `db.py` had to be **added to `scripts/deploy.sh`'s FILES
  list** — it was absent, so the migration could not reach the server. Because it
  is a `.py`, the deploy correctly restarts both long-running services, and the
  migration runs on `init_db()` at startup. Without that line the column would
  never be created on the server even with the code pushed.

## Follow-ups (not in this change)

1. **`history.list` delta — the real fix, deferred.** The root cause is that the
   webhook does a blind `is:unread` search instead of consuming the Pub/Sub
   `historyId` and asking Gmail "what changed since the last cursor". That is the
   correct long-term idiom (mirrors the Drive changes-feed cursor in ADR-0001) and
   would remove the redelivery loop at the source rather than absorbing it at the
   DB. Deliberately **out of scope** for PUB-01: dedup + mark-read close the bleeding
   today; the delta rewrite is its own story.
2. **Back-fill the historical duplicate `failed` rows.** A separate ops cleanup —
   not touched here.
