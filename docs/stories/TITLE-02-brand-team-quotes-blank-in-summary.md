# TITLE-02 — Brand Team quotes blank in Apple summary despite quotes existing (TRIAGE)

**Owner:** TBD · **PO:** Angela · **Priority:** TBD (triage)
**Status:** Needs triage · **Created:** 2026-06-23 · **Raised by:** Bill (during TITLE-01)

---

## Symptom

During E29 (2026-06-23), `compose_apple_summary` emitted a pre-write note —
`*Pre-write note: Brand Team quotes are blank ...*` — even though the Brand Team
chain had produced **5 quotes** (`quotes.json` was populated). The model believed
it had no quotes to work from.

## Suspicion (unverified — this is a triage flag, not a diagnosis)

The quotes may not be reaching the summary compose. Candidate areas:
- the shape passed into `anthropic_client.compose_apple_summary(quotes=...)` vs.
  what the `apple_summary.md` prompt expects to read;
- whether quotes are serialized into the user message at all in that call;
- a key-name mismatch (`quote_text` vs `text`, `speaker` vs `name`).

## Why it was NOT fixed in TITLE-01

TITLE-01 (out-of-scope note) addressed the *title* failure E29 surfaced. The
title is now generated from a dedicated structured step that does not depend on
the summary at all, so the title is no longer exposed to this bug. But the
summary-quotes gap is a separate content-quality issue with its own blast radius
(weaker summaries) and belongs in its own ticket.

## Suggested first step

Reproduce with a known-good `quotes.json` and log the exact user message
`compose_apple_summary` sends to the model — confirm whether the quotes are in
the payload before blaming the prompt.
