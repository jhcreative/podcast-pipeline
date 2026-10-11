# ADR 0010 — Deterministic thank-you fill (take the model out of the path)

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** John (owner — raised it); Bill (backend — impl); Toby (review/deploy); Jen (tests — TY-02); Angela (PO)
- **Story:** TY-02 — deterministic thank-you fill + gate (root-cause fix for the TY-01 regression)
- **Supersedes:** the "Compose … (Claude)" design of Step 13 in `jobs/publish_finalize.py`
  (the thank-you leg only). Blog/FAQ/captions still compose via the model — those have a
  genuine content surface; the thank-you does not.

## Context

`prompts/thank_you.md` states it plainly: the guest thank-you is a **STANDARD TEMPLATE with
deterministic fills — not an AI-composed letter … change nothing else.** Yet Step 13 of
`publish_finalize.py` round-tripped it through the LLM (`anthropic_client.compose_thank_you`,
a full `client.messages.create`). Every input that call needed — guest name, title, Apple,
Spotify, blog URL — is already a known deterministic value at the call site. The model had
nothing to decide, and a generation call **can** drift. On E29 it did: the model emitted its
own reasoning/tool-call narration ("Step 1…", `<tool_call>…`, a faked `| Field |` table) and
that blob became the draft body (TY-01). If Heather had sent it, the guest (Dr. Corinne
Maltravers) would have received a wall of model reasoning — a straight regression against the
locked template and a PQ-5 (honest systems) violation.

This is John's deterministic-scaffolding rule applied literally: deterministic paths are
code; agents enter only on a genuine content surface. A template fill is not one.

## Decision

1. **No model in the path.** Step 13 fills the `thank_you.md` output block in Python
   (`thank_you.build_draft`), reading the template's **own bytes** and substituting slots.
   For a fixed slot set the body is byte-identical to the template (golden test).
   `anthropic_client.compose_thank_you` is **retired** (removed; a pointer comment remains).

2. **Content gate before the draft.** `content_gates.assert_thank_you_wellformed(body)` raises
   `GateFailure` **before** `gmail_client.create_draft` writes, when the body does not start
   with the template's fixed first line, or contains a transcript/model marker
   (`<tool_call>`, `tool_response`, `Step `, a ``` code fence) or a **markdown table row**.
   The E29 transcript is a rejected fixture. A deterministic fill can't produce cruft; the
   gate defends against a future non-deterministic path or a bad hand-paste.

3. **First-class CLI.** `create_thank_you_draft.py --episode N` runs ONLY the deterministic
   draft step — read reconciled schedule → fill → gate → create server-side draft →
   raw-verify (gate passes on the stored body + no `google.com/url`). This is the sanctioned
   way to (re)create a thank-you; no one hand-composes one in a chat again. TY-01 uses it.

4. **DRAFT-only preserved.** Still `create_draft`, never send. `send_pipeline_alert` remains
   the only auto-send. reels-only reprocess still skips the thank-you draft.

## The one deliberate deviation from the TY-02 wording

TY-02 AC2 lists "a markdown table pipe (` | `)" as a reject marker. Taken literally — reject
**any** single pipe — the gate would reject the real E29 body, because E29's own title carries
one ` | ` separator ("Don't Wait for the Stop **|** Optimize Your Hormones Before
Perimenopause") and the title is rendered into the body. That also makes TY-01's own AC4
("no markdown table pipes") impossible for E29. The two stories cannot both be satisfied under
the literal reading.

Resolution: the gate rejects a **table ROW** — a line with two or more pipes (`| Field | Value |`,
the E29 garbage shape) — **not** a single inline ` | ` separator. Episode titles are
"Hook | Topic" (exactly one pipe by convention), so a real title passes and a faked table
fails. A dedicated test asserts the E29 clean body (title with one pipe) passes the gate. If a
title ever legitimately needs two pipes, revisit this line — it is the only spot the character
count matters.

## Update — v2 copy (2026-07-06, John)

After Toby's GO, John revised the thank-you *copy* (not the mechanism): new subject
`Hi {first} ~ podcast episode & links are live!`, first line now ends `!`
(`…GenX Hits Menopause!`), "Our episode together …", blank lines between links,
"collaborator" (singular), no "Rock on, Heather" sign-off (Heather's Gmail signature is
appended at send time — keeping it in the template would double it). The change is
entirely template bytes + the one gate first-line constant + the golden expected bytes;
the deterministic-fill architecture above is unchanged. Because the fill is template-driven
(`thank_you.py` reads `prompts/thank_you.md`), only the gate constant and the tests needed
code edits — the subject/body followed the template automatically.

## Consequences

- The thank-you draft is now byte-deterministic and un-driftable. The E29 class of regression
  cannot recur from the pipeline.
- `create_thank_you_draft.py` + `thank_you.py` are added to `scripts/deploy.sh` FILES (the
  deploy-omission pattern has bitten three times — PP-6).
- The template file `prompts/thank_you.md` stays the single source of truth Heather tunes; the
  fill reads it rather than duplicating it. Its fixed first line is also hard-coded in
  `content_gates` as an independent invariant — if the template's opening sentence changes,
  update both (a deliberate two-place change, caught by the golden test).
