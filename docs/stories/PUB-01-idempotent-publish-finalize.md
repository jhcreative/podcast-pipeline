# PUB-01 — Idempotent publisher-email trigger

**Owner:** Bill (Backend Lead) · **PO:** Angela · **Priority:** Medium (cost + queue-integrity; no content impact)
**Status:** Ready to Start · **Created:** 2026-06-23

---

## Story

**As** the pipeline operator,
**I want** a publisher "Episode is Live" email to enqueue exactly one `PUBLISH_FINALIZE`
job no matter how many Gmail notifications that email generates,
**so that** we stop paying for redundant Claude work and stop polluting the job queue
with duplicate jobs and misleading `failed` rows.

## Context — the bug

A single publisher email produced **8 / 3 / 11** `PUBLISH_FINALIZE` jobs (message_ids
`19e7018dabe1d91f` May 28, `19ed1689f95595b1` Jun 16, `19edd73ebafa7312` Jun 19).
Root cause is three facts that compose into a loop:

1. **Blind search, not a delta.** `gmail_webhook` ignores the Pub/Sub `historyId` and
   runs `list_unread_messages("from:(publisher) is:unread")` on every push
   (`webhook_service.py:209`). It asks "is there an unread publisher email *right now*",
   not "what changed".
2. **Nothing marks the email read.** No `modify`/`removeLabel`/mark-read call exists
   anywhere in the codebase; the `gmail.modify` scope is granted but unused. The email
   stays `is:unread` after its job completes.
3. **`db.enqueue` is a blind INSERT** (`db.py:88`) — no dedup key, no UNIQUE constraint.

So every Gmail notification (Pub/Sub is at-least-once, *and* Gmail fires one per mailbox
change) re-finds the still-unread email and enqueues another job, until newer unread mail
pushes it out of the `max_results=5` window.

> Not the same as the Jun 16 reply-guard (`webhook_service.py:222`), which stopped
> *replies in the thread* from firing. This is the *original* notification re-firing.

**Blast radius:** content is safe (downstream guarded by `.pipeline_done`); cost is
wasted Claude calls (FAQ + blog generation × N) and confusing queue history.

## RTS gate (Angela)

- **RTS-1 Testable:** yes — acceptance criteria below are observable in DB + Gmail state.
- **RTS-2 Contract:** new `db.enqueue` signature + schema migration; Bill defines before coding.
- **RTS-3 Dependencies:** `db.py`, `webhook_service.py`, `gmail_client.py`; live SQLite on server.
- **RTS-4 Test strategy:** Jen — redelivery simulation + migration test (see below).
- **RTS-5 Branch:** clean off `main`; suggested `fix/pub-01-idempotent-finalize`.

## Acceptance criteria

**A. Idempotent enqueue (the fix).**
1. Given a publisher `message_id` already has a `PUBLISH_FINALIZE` job, when the webhook
   fires again for that same `message_id`, then **no new row is inserted** and `enqueue`
   returns the existing job id.
2. Dedup survives Pub/Sub redelivery **regardless of the email's read-state** — i.e. A1
   holds even if mark-read (criterion B) never ran.
3. Dedup is enforced **in the database** (UNIQUE constraint/partial index on the dedup
   key), not only in application code — concurrent webhook calls cannot both insert.
4. Idempotency is scoped to the trigger identity (`message_id`), not the episode — a
   legitimate *reprocess* of the same episode via a different trigger is unaffected.

**B. Tombstone the trigger (hygiene).**
5. On the *first* successful enqueue for a message, the email is marked read (remove
   `UNREAD`) or labeled `pipeline-handled`, so it no longer matches the `is:unread` search.
6. Mark-read failure is **non-fatal**: the job still enqueues; the failure is logged.
   (Criterion A is the correctness guarantee; B is belt-and-suspenders.)

**C. Migration & back-compat.**
7. A migration adds the dedup-key column + index to the **existing** `jobs.db` on the
   server without dropping history; existing rows get `NULL` dedup keys and are exempt
   from the UNIQUE constraint (partial index `WHERE dedup_key IS NOT NULL`).
8. Non-gmail enqueues (ROUND_1, reprocess) pass `dedup_key=None` and behave exactly as today.

**D. Observability.**
9. A suppressed duplicate logs at INFO: `enqueue: duplicate trigger <message_id> → existing job <id>`.

## Out of scope (this story)

- The deeper `history.list` delta rewrite (blind-search → proper Gmail-push idiom).
  Record it as the long-term direction in the ADR; do **not** build it here.
- Back-filling/cleaning the historical duplicate `failed` rows (separate ops cleanup).

## Decision record

Bill + Toby capture the dedup-key design (column shape, conflict handling, mark-read
timing) in **ADR-0005** before merge (PP-5). Note `history.list` as the deferred option.

## Definition of Done

- DOD-1 Jen's tests pass (redelivery sim + migration).
- DOD-2 `db.enqueue` signature change documented; callers updated.
- DOD-3 Toby review.
- DOD-5 mark-read and DB-conflict paths have explicit, honest error handling.
- DOD-7 every criterion above mapped to a test (Angela ↔ Jen).
- ADR-0005 merged.

## Test notes for Jen

- Simulate N webhook invocations for one `message_id`; assert exactly one job row.
- Concurrency: two near-simultaneous enqueues of the same key → one row (DB constraint).
- Migration: run against a copy of the live `jobs.db`; assert history intact, index present,
  existing rows exempt.
- Mark-read failure injected → job still enqueues, warning logged.
