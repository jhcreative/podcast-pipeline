# ADR 0011 — Phase 2 is triggered manually until a genuine push mechanism exists

- **Status:** Accepted
- **Date:** 2026-07-21
- **Deciders:** John (owner, hard constraint); Angela facilitating; Bill + Toby (implementation surface)
- **Supersedes the trigger assumption in:** ADR 0002 (publisher email as a drifting contract)
- **Story:** TRIG-01

## Context

Phase 2 (`publish_finalize`) had exactly one trigger: the publisher's "Your Episode
is Live" email, arriving via the Gmail push webhook. ADR 0002 hardened us against
that email *changing shape* — sender drift, dropped episode numbers, HTML-only
bodies. It cannot help when the email **stops arriving at all**, which is now the
case: Legacy Publisher publishes through Example Publisher and the notification is no longer
sent. E30 sat live for 12 days with no downstream artifacts as a result.

**John's constraint is explicit and hard: the trigger must be event-driven. No
polling.** Asked what fallback he would accept if no true push existed, he chose
"WebSub only — real push or nothing."

## What we investigated

**The show's RSS feed advertises no WebSub hub.** `https://publisher.example.test/feed/genx-hits-menopause`
carries exactly one atom link:

```xml
<atom:link href="https://publisher.example.test/feed/genx-hits-menopause" rel="self" type="application/rss+xml" />
```

No `rel="hub"` anywhere in the 246 KB body, and none in the HTTP headers. Example Publisher
self-hosts the feed on its own Cloudflare-fronted domain, so there is no third-party
host whose hub support we could inherit.

Supporting findings, recorded because they will matter to whoever revisits this:

- Hosts that **do** advertise hubs: Captivate, Spotify for Creators, Fireside,
  Podigee, Audioboom, Simplecast. Hosts that **don't**: Libsyn, Megaphone, Acast,
  Blubrry, Podbean, Podetize, **Example Publisher**. The split is indie/SMB vs. enterprise
  ad-tech. Nearly every hub-advertising host points at the same free Google App
  Engine instance — the compliant path is thinner than it looks.
- **Livewire's Podping→WebSub bridge** is free and genuinely push (~20s latency,
  verified live), but only covers hosts that emit Podpings — the same indie set.
  It would become available if the show ever moved hosts.
- **Apple and Spotify offer no publish webhook.** The feed is the only signal.
- The feed carries **no platform URLs at all** — zero occurrences of `spotify` or
  `podcasts.apple.com`; every item's `<link>` is the same Legacy Publisher author
  page. So even a working feed trigger would be notification-only; Apple/Spotify
  URLs must still be resolved out-of-band.
- Apple's API returns `episodeNumber: null` for **every** episode of this show. The
  number exists only in the RSS `<itunes:episode>` tag.
- The feed migrated hosts mid-show: items E26+ use `example-publisher-ep-<uuid>` GUIDs, E25 and
  older still carry `Buzzsprout-<id>` GUIDs. Any GUID-keyed dedupe must tolerate both.

**Example Publisher offers nothing.** John logged into the dashboard on 2026-07-21 and checked:
there is no publish webhook, no outbound integration, no API. This was the last open
question and it is now closed — the decision below rests on a checked dashboard, not on
an absence of evidence.

## Decision

**Phase 2 is triggered manually until a genuine push mechanism is found.**
`reprocess_episode.py --episode N --apple <url> --spotify <url>` is the sanctioned
kickoff. No automated trigger is built.

### Options rejected, and why

- **ETag-conditional feed polling (~15 min).** Technically clean — the feed returns a
  proper `ETag` and conditional GET verifiably returns `304` with an empty body, so it
  would be genuinely cheap. **Rejected: it is polling.** It is recorded here with its
  merits intact because the temptation to adopt it "just because it's efficient" is
  exactly how a stated constraint erodes.
- **Superfeedr.** Free at our scale (under 10 feeds/month; we have one), HMAC-signed,
  no infrastructure. **Rejected: their own documentation says "Superfeedr still polls
  the feeds, up to every minute."** It is polling with a vendor's name on it. Adopting
  it and calling the result event-driven would be a PQ-5 violation in our own records.
- **Zapier / Make / IFTTT "new podcast episode" triggers.** Push-shaped from our side,
  polling underneath. Rejected for the same reason, more so — they hide it better.
- **Asking Legacy Publisher to move hosts** to a hub-advertising platform. Not rejected
  on merit; it is their feed and their decision, and a large ask to solve our problem.

## Consequences

- **−** Every episode needs a human to notice it went live and run one command. That is
  a real, recurring cost and the honest price of the constraint.
- **−** The silent-stranding class (E28 dead-push, E29 no-trigger, E30 matcher-drift and
  now no-trigger-at-all) is not closed. It is converted from "silently doesn't happen"
  to "obviously doesn't happen," which is strictly better but not the durable fix.
- **+** Nothing in the system claims to be automated when it isn't. No poll loop we
  didn't decide to write; no vendor's poll described as push.
- **+** The URL-sourcing work is done regardless: iTunes Lookup by `collectionId` plus
  Spotify's unauthenticated oEmbed endpoint both resolve and title-verify without
  credentials. Whatever trigger eventually arrives, that half is solved.

## Revisit when

All avenues available to us today are closed: Example Publisher offers no webhook or API (checked),
the feed advertises no hub, and neither Apple nor Spotify pushes. This decision is
therefore final until one of the following changes:

1. **Example Publisher ships webhooks.** Worth asking Legacy Publisher to request it — we are the
   party who needs it and they own the vendor relationship.
2. The feed starts advertising a hub, or the show moves to a host that does — at which
   point TRIG-01b's acceptance criteria (already written) apply unchanged, including
   the WebSub security requirements Orly must sign off on: serve the `hub.challenge`
   echo as `application/octet-stream` with `X-Content-Type-Options: nosniff` (it is a
   reflected-XSS vector otherwise), verify `X-Hub-Signature` over the **body only**,
   expect SHA-1 signatures in the wild, and handle a delivered body that may be the
   full feed or only new items.

---

## Addendum — 2026-10-10: the publisher email is back; both triggers are live (TRIG-03)

This addendum is dated, not a rewrite: the decision above stands as recorded.

**The email resumed in October 2026.** "Your Episode is Live" from Legacy Publisher arrived for E32 (2026-10-06)
and E33 (2026-10-08), and the Gmail path processed both. Phase 2 therefore has two push triggers again, and they
can arrive in either order. Read from the box, both orders caused trouble:

- **E32** (job 141): the email started a full Phase 2 again and stopped at a renamed post.
- **E33** (job 142): the email ran all of Phase 2 again, making a second thank-you draft and regenerating the
  reel captions.

**Routing, decided in the worker against the schedule when the job runs:**

| Episode state when the email's job runs | Action |
|---|---|
| Phase 2 not run, images missing | store `pending_mm_context` and wait (unchanged) |
| Phase 2 not run, images present | full Phase 2 with the real links (unchanged) |
| `pipeline_done`, `episode_links_pending` | **links only**: the blog draft's show-page listen buttons and our untouched thank-you draft get the episode links; no model call; one alert |
| `pipeline_done`, links already real | no-op: a log line and a standup line |

**The email is trusted only on Google's verdict**: the topmost `Authentication-Results` from `mx.google.com`,
with `dmarc=pass` for the From domain or an aligned `dkim=pass`. The From domain must match the allowlist
exactly. Legacy Publisher publishes DMARC `p=NONE`, so the From line alone proves nothing. The links must be this
show's episode links. See TRIG-03, and Orly's RTS-6 review in it.
