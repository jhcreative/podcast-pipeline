# TY-01 — Replace the E29 guest thank-you draft (transcript garbage → clean template)

**Owner:** Bill · **PO:** Angela · **Priority:** High (guest-facing, do first)
**Status:** Ready · **Created:** 2026-07-06 · **Raised by:** John

---

## Why

The E29 guest thank-you draft in Gmail contains the composing agent's **transcript**
— `"I'll verify the URLs, then create the draft server-side… Step 1… <tool_call>…
| Field |"` — instead of the email. If Heather sends it, the guest (Dr. Corinne
Maltravers) receives a wall of model reasoning. This is a straight regression against
the standardized template (`prompts/thank_you.md`, locked 2026-06-16) and violates
PQ-5 (honest systems).

Root cause is the pipeline itself — Step 13 composes the thank-you through the LLM
(`anthropic_client.compose_thank_you`, `publish_finalize.py:932`) when the template
is a deterministic fill. That structural fix is **TY-02**. This story is the
*immediate* content repair for E29 only.

## Facts (E29)

- **Guest first name:** Corinne (Dr. Corinne Maltravers — from the 06-26 carry-forward
  Apple title-verify, not the transcript).
- **Title:** `Don't Wait for the Stop | Optimize Your Hormones Before Perimenopause`
- **guest_email:** empty → draft to Heather, subject prefixed `[FILL IN GUEST EMAIL]`.
- **Audible:** fixed show URL `https://www.amazon.com/GenX-Hits-Menopause/dp/B0DQ8YXF3D`.
- **Apple / Spotify / blog URLs:** pull from the **box's reconciled `publication_schedule.json`**
  (E29 was written back `status=done` + URLs on 06-26). Do **NOT** trust the URLs in
  John's pasted transcript — they came from a model fabricating tool responses.
  Title-verify Apple via WebFetch before the draft cites it (publisher has pasted wrong
  Apple URLs before).

## Acceptance criteria

1. **Bad draft deleted** — the transcript-garbage E29 draft is removed **server-side**
   (`gmail_client` drafts delete via the Gmail API). Not the claude.ai Gmail MCP — it
   has no delete/update and rewrites URLs.
2. **Clean draft created server-side** via `gmail_client.create_draft`, body
   **byte-identical** to the `thank_you.md` output block with E29 slots filled.
3. **Subject** exactly `[FILL IN GUEST EMAIL] Your episode is live, Corinne`, **to**
   Heather.
4. **Raw-verified** — `drafts().get(format="raw")` shows: the exact template text; **no**
   `<tool_call>` / `Step ` / markdown table pipes / any transcript markers; **no**
   `google.com/url` redirects.
5. **URLs live** — Apple + Spotify + blog resolve and title-match E29 before the draft
   is created.

## Suggested approach

Once TY-02 lands `create_thank_you_draft.py --episode N`, this is one command against
the box. Until then, a one-off server-side fill using the reconciled schedule values is
fine — but do it in Python via `gmail_client.create_draft`, never by hand-composing in
a chat (that is exactly what produced the regression).
