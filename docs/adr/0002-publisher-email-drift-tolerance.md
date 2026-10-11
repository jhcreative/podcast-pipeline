# ADR 0002 — Treat the publisher "Episode is Live" email as a drifting contract

- **Status:** Accepted
- **Date:** 2026-06-16
- **Deciders:** John (owner); Angela facilitating; Bill + Toby (impl/architecture), Orly (fail-safe), Jen (tests)
- **Supersedes the assumptions baked into:** `webhook_service.py` Gmail handler, `jobs/publish_finalize.py` Step 1

## Context

Phase 2 (`publish_finalize`) is triggered by the publisher's "Your Episode is Live"
email. On 2026-06-16, processing E28 silently failed and the team found the publisher
had changed the integration contract underneath us in **three** independent ways at once:

1. **Sender changed.** Legacy Publisher Media now sends via **Example Publisher** (`hello@publisher.example.test`).
   The trigger only matched `@legacy-publisher.example.test`, so the live email was ignored — no job,
   no error. (Separately, a `editor@legacy-publisher.example.test` *reply* in the thread DID match the
   trigger and spawned `PUBLISH_FINALIZE` jobs that PermanentFailed on a bio email — the
   trigger fired on replies, not just the original notification.)
2. **Episode number dropped.** Subjects went from `Your Episode is Live | EP 28 …` to
   `Your Episode is Live | No Stone Unturned | …`. We identified episodes *by that number*,
   so identification broke entirely.
3. **Body is HTML-only.** Listen links now live in button `href`s with no `text/plain`
   part, and the Apple episode id is corrupted in the template (`?i=1000…` → `?i<ctrl>00…`).
   URL extraction (text-only, exact-URL) found nothing.

This is the second publisher-format incident (cf. the E24 "HenX" subject typo, the E25/E2
mangled Apple ids). The pattern is clear: **the contract drifts, and each drift is a silent
outage.** John's directive: support old and new forms together; make the next change a
config/fixture edit, not a code hunt.

## Decision

Centralize every assumption about the publisher email in **`publisher_email.py`**, consumed
by both the webhook (enqueue decision) and the job (parse + identify):

- **Sender allowlist, env-driven.** Default = `legacy-publisher.example.test, legacy-publisher.example.org,
  publisher.example.test`; override/extend via `PUBLISHER_SENDER_DOMAINS`. Adding the next vendor is
  one env edit + one test row.
- **Reply guard.** Trigger only on an *original* notification — reject `Re:`/`Fwd:` subjects
  and any message carrying an `In-Reply-To` header.
- **No episode number required.** Identify by: (1) explicit `EP NN` if present (back-compat)
  → (2) title/hook match against the schedule → (3) the single awaiting-publish episode.
  **Zero or ambiguous ⇒ return None ⇒ caller fails safe (alert, never guess)** — a
  wrong-episode publish is worse than a held one (Orly, PQ-5).
- **HTML-aware extraction + Apple-id repair.** Parse `text` and `html`; reconstruct the
  11-digit→13-digit Apple id; verify Apple/Spotify by fetch before any artifact cites them.
- **Binary-over-Docs in Drive.** `publish_finalize` file-map now prefers a downloadable
  binary over a same-named Google-Docs file (Drive auto-converts uploaded `.md`/`.txt`),
  which is what 403'd the first E28 reprocess.
- **Tests are the contract.** `test_publisher_email.py` encodes the sender × subject ×
  URL-shape × identification matrix. New drift lands here first.

## Consequences

- **+** Both Legacy Publisher and Example Publisher senders work; the no-number subject works; the
  HTML-only body works; reply storms stop. Future drift is a config/fixture change.
- **+** Failure is now loud and safe (PermanentFailure with a clear reason; ambiguity halts
  rather than guesses).
- **−** Title-match identification can theoretically mis-match if two live-but-unpublished
  episodes share a hook; mitigated by the full-title-first rule and the single-candidate
  fallback returning None on ambiguity.
- **Follow-up (not in this change):** a periodic reconcile of `publication_schedule.json`
  against Drive truth (the schedule drifts stale; E26 carried duplicate/`waiting_for_visuals`
  entries despite being fully produced). Tracked separately.
