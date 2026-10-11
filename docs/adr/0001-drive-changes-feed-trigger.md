# ADR 0001 — Drive new-file detection via the changes feed, not folder watches

- **Status:** Accepted
- **Date:** 2026-06-12
- **Deciders:** John (owner), Bill (backend), Toby (integration/infra)
- **Context incident:** E28-everett.m4a stranded in `recordings_inbox` 2026-06-10

## Context

The pipeline detected new audio by registering `files().watch(fileId=<folder>)`
on `recordings_inbox`, and a second one on `Episodes/`. The renewal job logged
both as "renewed OK" every night.

On 2026-06-10, John dropped `E28-everett.m4a` into `recordings_inbox` at 14:03 UTC.
No ROUND_1 job ran; the file sat untouched. Investigation showed the webhook
received **zero** notifications after the 04:00 watch-registration `sync` ping.

Root cause: **`files.watch` on a folder notifies on changes to the folder
*resource itself*** (rename, trash, share) — **not** on a child file being added
inside it. Drive acks the watch with one `sync` message at registration and then
stays silent on real drops. The watch "renewing OK" measured registration, never
delivery. The auto-trigger had almost certainly never worked on a real drop;
prior episodes were processed via manual `reprocess_episode.py` / `db.enqueue`.

A separate, unrelated breakage was found in passing: the Gmail Pub/Sub watch
fails nightly because topic `gmail-notifications` no longer exists. Tracked
separately (see Open items); not addressed here.

## Decision

Replace both per-folder `files.watch` channels with **one account-wide
`changes.watch`** on the Drive changes feed, plus a **persisted page-token
cursor** owned by the webhook.

- The push notification is only a "go look" ping. On receipt the webhook drains
  `changes.list(pageToken=<cursor>)`, routes each changed file by parent folder
  (inbox → ROUND_1; episode folder → resume PUBLISH_FINALIZE), and advances the
  cursor.
- The **cursor is the source of truth**, not the notification. If the watch
  lapses or a ping is dropped, the next drain still returns everything since the
  cursor — a drop can no longer be silently lost.
- `renew_watches.py` arms one `drive-changes` channel daily and *seeds* the
  cursor on first run; it never overwrites an existing cursor (that would skip
  changes since the last drain).

## Consequences

- **Positive:** correct new-file detection; self-healing across watch gaps; one
  channel instead of two; the same fix closes the identical latent bug on the
  Episodes watch.
- **Cost:** the changes feed reports *all* Drive activity for the account, so the
  webhook does one `changes.list` per ping and filters by parent. For a
  dedicated pipeline account this is negligible.
- **Cursor file** `state/drive_changes_token.json` is now critical state. If it
  is lost, the webhook re-baselines to "now" and acks (one drain's worth of
  changes is not replayable). Backup with the rest of `state/`.
- Endpoints `/webhook/drive/inbox` and `/webhook/drive/episode` are retired in
  favour of `/webhook/drive/changes`.

## Follow-ups (not in this change)

1. **Gmail Pub/Sub topic** `gmail-notifications` is deleted — recreate topic +
   subscription, then `register_watch()`. PUBLISH_FINALIZE's Gmail trigger is
   dead until then (Drive-resume path still works).
2. **Daily drain safety net:** have `renew_watches` drain the feed once per run
   so even a no-ping day catches a stranded drop. Deferred to avoid duplicating
   the webhook's routing; revisit if a gap recurs.
