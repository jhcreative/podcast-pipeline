# TY-02 — Take the model out of the thank-you path (deterministic fill + gate)

**Owner:** Bill (impl) · **Tests:** Jen · **Review/deploy:** Toby · **PO:** Angela
**Priority:** High (before this week's episode) · **Status:** Ready
**Created:** 2026-07-06 · **Raised by:** John

---

## Why

`prompts/thank_you.md` is explicit: the guest thank-you is a **STANDARD TEMPLATE with
deterministic fills — not an AI-composed letter… change nothing else.** Yet Step 13 of
`publish_finalize.py` (line 932) round-trips it through the LLM
(`anthropic_client.compose_thank_you`, `anthropic_client.py:503` — a full
`client.messages.create`). Every input that call needs — guest name, title, Apple,
Spotify, blog URL — is already a known deterministic value at the call site. The model
has nothing to decide, and a generation call **can** drift. On E29 it did: the model
emitted its own reasoning/tool-call narration and that blob became the draft body
(see TY-01).

This is John's deterministic-scaffolding rule applied literally: deterministic paths
are code, agents enter only on a genuine content surface. A template fill is not one.

## Acceptance criteria

1. **No model in the path.** Step 13 fills the `thank_you.md` output block in Python
   from slot values. `anthropic_client.compose_thank_you` is **retired** (removed, or
   left dead with a pointer comment). For a fixed set of slots the body is
   **byte-identical** to the template.
2. **Content gate.** `content_gates.assert_thank_you_wellformed(body)` (new, alongside
   the existing title/guest gates) **raises `GateFailure`** — before `create_draft`
   writes — when the body does not start with the fixed first line
   (`Thank you so much for joining us on GenX Hits Menopause!` — **v2 copy 2026-07-06,
   ends with `!`**) **or** contains any
   transcript/model marker: `<tool_call>`, `tool_response`, `Step `, a code fence
   (```` ``` ````), or a markdown **table row** (a line with ≥2 pipes). The E29
   transcript is a fixture that must be rejected. NOTE (2026-07-06, Angela): a single
   ` | ` in a *title* is legitimate — E29's own title carries one — so the gate matches
   table rows, not any pipe. Approved per Bill's finding; recorded in ADR-0010.
3. **Subject shape.** Exactly `Hi {first_name} ~ podcast episode & links are live!`
   (**v2 copy 2026-07-06**); the `[FILL IN GUEST EMAIL] ` prefix is added **only** when
   `guest_email` is empty. First name is derived deterministically from `guest_name`
   (handle `Dr.`/title prefixes and multi-word names — e.g. `Dr. Corinne Maltravers` →
   `Corinne`).

### Copy revision v2 — 2026-07-06 (John, authoritative)

The template body/subject is revised to the wording below. **No signature/sign-off in
the template** — the "Heather & John Hiemstra / rockstarblends.com" block is Heather's
Gmail signature, baked in at send time; do NOT replicate it in the template (would
double it). Body ends at "Thanks again for being part of the show!". Exact block Bill
fills byte-for-byte (`{{slots}}`):

```
Subject: Hi {{guest_first_name}} ~ podcast episode & links are live!

Thank you so much for joining us on GenX Hits Menopause!

Our episode together "{{episode_title}}" is live ~ here are links you can share:

- Apple Podcasts: {{apple_url}}

- Spotify: {{spotify_url}}

- Audible: https://www.amazon.com/GenX-Hits-Menopause/dp/B0DQ8YXF3D

- Our blog: {{blog_url}}

We'll be sharing audio clips from our conversation on Instagram:
@genxhitsmenopause, @rockstar.blends, and @heatherreillyalchemist and we will tag you as collaborator.

Please share your social posts and tag us above; we'll amplify right back so the episode reaches as many folks as possible.

Thanks again for being part of the show!
```

Changes vs v1: subject → `Hi {first} ~ podcast episode & links are live!`; first line ends
`!`; "Your episode" → "Our episode together"; Instagram line uses a colon + "we will tag
you as collaborator" (singular); closing ends `!`; **"Rock on, Heather" sign-off removed**
(Gmail signature replaces it). Golden test + gate first-line constant update to match.
4. **First-class CLI.** `create_thank_you_draft.py --episode N` runs **only** the
   deterministic draft step — reads the reconciled schedule + verified URLs, fills the
   template, creates the server-side draft, and raw-verifies it (AC2 gate + no
   `google.com/url`). This is the sanctioned way to (re)create a thank-you so no one
   hand-composes it in a chat again. TY-01 uses it.
5. **reels-only unchanged.** The reels-only reprocess path still skips the thank-you
   draft + calendar (`publish_finalize.py:923-931`).
6. **DRAFT-only preserved.** Still `create_draft`, never send. `send_pipeline_alert`
   remains the only auto-send.

## Tests (Jen)

- **Golden equality** — filled template == expected bytes for a representative episode.
- **Gate rejects cruft** — the actual E29 transcript, a code-fenced body, a table body,
  and a body missing the first line each raise `GateFailure`; a clean body passes.
- **Subject matrix** — with/without `guest_email`; first-name derivation across
  `Dr. Corinne Maltravers`, `Heather` (host-name guest), single-token, hyphenated.

## Post-review fast-follow (Toby, 2026-07-06 — non-gating)

- **Anchor the `Step ` marker.** The gate checks `Step ` as a whole-body substring, and
  the episode title renders into the body — so a legitimate future title like
  *"Step Into Menopause"* would trip the gate and `PermanentFailure` that episode's draft.
  E29 is safe, so it doesn't gate this week. Change to a line-anchored `^Step \d` (or
  equivalent) so only transcript step-headers match. Small, do it in the next touch.

## Notes

- Shipping this should record **ADR-0010 (deterministic thank-you fill)**, superseding
  the "Compose … (Claude)" design in the Step 13 header comment
  (`publish_finalize.py:28`).
- Confirm `create_thank_you_draft.py` (and any new module) is added to `deploy.sh`
  FILES — the deploy-omission pattern has bitten three times (PP-6).
