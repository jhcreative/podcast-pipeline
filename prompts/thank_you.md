# Thank-You Email — Standard Template (deterministic fill)
## Rockstar Blends Podcast · thank_you.md / Gmail draft

⚠️ NEVER AUTO-SENT. The output becomes a Gmail DRAFT for Heather to review and send
herself. This rule is immutable.

The post-publish guest thank-you is a STANDARD TEMPLATE with deterministic fills — not
an AI-composed letter. Do NOT invent a greeting, a personal intro, or a reflection:
the personalization is the guest first name in the SUBJECT only. Fill the slots below;
change nothing else.

⚠️ Create the draft SERVER-SIDE via `gmail_client.create_draft` — NOT the claude.ai
Gmail MCP, which rewrites every URL through `google.com/url?q=…` redirects (visible cruft,
and it can't delete/update). Verify with `drafts().get(format="raw")` that no
`google.com/url` appears.

---

## Inputs (slots)

- `{{guest_first_name}}` — used in the subject only
- `{{episode_title}}` — the title as it reads on the platforms
- `{{apple_url}}`  — verified live; reconstruct corrupted Apple `?i=` IDs first
- `{{spotify_url}}` — verified live
- `{{blog_url}}` — the rockstarblends.com episode page (confirm it resolves)
- Audible is the FIXED show URL (same every episode):
  `https://www.amazon.com/GenX-Hits-Menopause/dp/B0DQ8YXF3D`

---

## Exact output (plain text — fill slots, change nothing else)

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

---

## Rules

- The subject is `Hi {{guest_first_name}} ~ podcast episode & links are live!` — the guest
  first name is the only personalization; there is no greeting line in the body. Heather
  adds any personal touch herself when she reviews the draft.
- NO sign-off and NO signature in the template. The "Heather & John Hiemstra /
  rockstarblends.com" closing is Heather's Gmail signature, appended automatically at send
  time — putting it in the template would double it. (The old "Rock on, Heather" sign-off
  was removed for this reason, 2026-07-06 — John confirmed.)
- Audible line is always the fixed show URL. Keep the "Our blog:" line.
- No claims about reach or numbers. Say "is live" — never "just went live" (the template
  is reused for backlog episodes that went live earlier).
- If the guest email address is unknown, address the draft to Heather with the subject
  prefixed `[FILL IN GUEST EMAIL]` so she sets the recipient before sending.
