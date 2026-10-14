# TRIG-01 — Phase 2 needs an event-driven "episode is live" trigger that isn't email

**Owner:** Bill · **Architecture:** Toby · **Tests:** Jen · **Security:** Orly · **PO:** Angela
**Priority:** HIGH — this is the pipeline's only remaining hand-crank
**Status:** **CLOSED — ACCEPTED AS-IS 2026-07-21 (John).** Spike done; no genuine push
mechanism exists: the feed advertises no WebSub hub AND Example Publisher offers no webhook or API
(John checked the dashboard 2026-07-21). Phase 2 is a manual CLI kickoff.
Decision + rejected options recorded in **ADR-0011**. TRIG-01b acceptance criteria are
retained below, unbuilt, for whenever a push path appears. · **Created:** 2026-07-21
**Raised by:** John — "Phase 2 kickoff trigger (email) is no longer provided by our partner"

---

## Why

Phase 2 (`publish_finalize`) has exactly one trigger: the publisher's "Your Episode is
Live" email, arriving via the Gmail push webhook. ADR-0002 hardened us against that email
*changing shape* — sender drift, dropped episode numbers, HTML-only bodies. It cannot help
us when the email **stops arriving at all**, which is the current state: Legacy Publisher
now publishes through **Example Publisher**, and the notification is no longer sent.

The consequence is that every episode from E30 forward strands after Phase 1. Phase 1 on
Hetzner is working well (John, 2026-07-21) — the break is entirely at the Phase 1 → Phase 2
seam.

This is also the **fourth** incident in the silent-stranding class (E28 dead-push 06-10,
E29 no-trigger 06-25, E30 matcher-drift 07-06, and now the trigger's permanent removal).
See R1-01. The class is the same every time: *we depend on a signal we don't control, and
its absence looks identical to "nothing to do."*

## Constraint from the owner — no polling

**John's directive is explicit and it is a hard constraint, not a preference: the trigger
must be event-driven. NO POLLING.** Asked what fallback he'd accept if no true push
mechanism exists, John chose **"WebSub only — real push or nothing."**

That means:

- A hub POSTing us on publish is acceptable. A loop asking "is it live yet?" is not.
- **A Zapier/Make "new podcast episode" trigger is NOT acceptable** even though it looks
  event-driven from our side — those bridges poll the feed internally. Presenting one as
  push would be exactly the kind of flattering-but-false framing PQ-5 forbids.
- **If no genuine push mechanism is available, we do not quietly add a poll.** We escalate
  to John and Phase 2 stays a manual CLI kickoff. Reporting "we couldn't find real push"
  is a valid, honest outcome of this story.

This also aligns with the standing engineering value: event-driven over schedule-driven,
because unnecessary compute is a real cost.

---

## TRIG-01a — SPIKE (time-boxed, must complete before TRIG-01b is estimated)

Deliverable is a written recommendation with evidence, not code. Answer:

1. **Example Publisher webhooks.** We have Example Publisher login credentials (John, confirmed 2026-07-21).
   Log in and determine whether Example Publisher offers an outbound webhook on episode publish, or
   any API. Document: event names, payload shape, auth model (HMAC? bearer?), retry
   semantics. **If the setting doesn't exist, say so plainly** — do not describe a feature
   we wish existed.
2. **WebSub on the show's RSS feed.** Fetch the feed. Does `<channel>` advertise
   `<atom:link rel="hub" href="…"/>`? Quote the exact line, or state definitively that it
   is absent. Identify which host actually serves the feed — that determines hub support.
3. **If no hub is advertised:** can we get one? (Some hosts enable WebSub on request; a
   hosted subscriber service can bridge.) Cost and dependency named honestly.
4. **URL sourcing.** Whatever fires the trigger, Phase 2 needs the live Apple and Spotify
   URLs. Determine the authoritative source (feed enclosure/GUID → iTunes Lookup →
   Spotify Web API) and the realistic lag between publish and availability.
5. **Recommendation** ranking the viable options by (a) genuine push vs. disguised polling,
   (b) implementation cost, (c) reliability — with a clear first choice, or an explicit
   "no push path exists, recommend manual kickoff."

**Exit condition:** Toby and John agree on the mechanism. Angela records **ADR-0011 —
Phase 2 trigger after the publisher email**, including the rejected options and *why*
(especially why any polling bridge was rejected), per PP-5.

---

## TRIG-01b — BUILD (acceptance criteria)

Written now so the spike knows what it must enable. Criteria hold regardless of which
mechanism wins.

1. **A new inbound endpoint on the existing webhook service.** The trigger arrives at
   `webhook_service.py` alongside `/webhook/drive/changes` and `/webhook/gmail`, reusing
   the established shared-secret/verification pattern — not a new service.
   *Testable:* endpoint exists, is covered by the service's health/status surface, and is
   in `scripts/deploy.sh` FILES.

2. **The payload is verified before it is trusted.** WebSub requires the `hub.challenge`
   GET handshake for subscription confirmation and `X-Hub-Signature` verification on
   content distribution; a Example Publisher webhook requires whatever signature it offers. An
   unverified or badly-signed POST is rejected with an honest status code and logged —
   never processed. **Orly signs off on this criterion** (DOD-3, BP-6): this is a public,
   unauthenticated-by-default internet endpoint that kicks off a job.
   *Testable:* fixtures for valid signature, invalid signature, missing signature, and
   replayed payload; only the valid one enqueues.

3. **Subscription lifecycle is maintained without a cron.** WebSub leases expire (typically
   ≤10 days). Renewal must be driven by the lease's own expiry — renew on receipt of the
   hub's lease-seconds, self-scheduling from the event — not by a periodic "check if we
   need to renew" sweep. *If the only honest way to hold a subscription is a scheduled
   renewal, that is a finding to bring to John, not a decision to make quietly.*
   *Testable:* a lease-expiry fixture proves renewal is triggered by the lease, and the
   current subscription state is visible on `/status`.

4. **A trigger identifies the episode or fails loud.** Reuse ADR-0002's identification
   ladder (explicit number → title/hook match against the schedule → single awaiting-publish
   episode; **zero or ambiguous ⇒ None ⇒ alert, never guess**). A feed ping that we cannot
   resolve to exactly one awaiting episode must alert with the feed item's title and GUID.
   *Testable:* the ADR-0002 identification matrix, re-run against feed-item inputs.

5. **URLs are verified before any artifact cites them.** Apple and Spotify URLs are
   title-verified by fetch before Phase 2 uses them — the existing rule, unchanged. A feed
   ping that arrives before the platform URLs are available must **hold and retry on a
   later event, or fail loud** — not cite a URL it hasn't confirmed (PQ-5; this is the
   BLOG-01 lesson).
   *Testable:* fixture where the Apple URL 404s at trigger time; assert no artifact is
   produced and an alert fires.

6. **Duplicate pushes are idempotent.** Hubs retry; a feed refresh can re-announce existing
   items. A second push for an already-finalized episode must not produce a second run.
   Reuse the PUB-01 dedup key (ADR-0005).
   *Testable:* same payload twice ⇒ one job.

7. **The old email path is retired deliberately, not left rotting.** Either keep the Gmail
   trigger as a still-supported alternate (documented, tested) or remove it — a recorded
   decision either way. Dead-but-live trigger code is a DOD-4 violation and a foot-gun.

8. **Loud on absence.** If no trigger has arrived for an episode that Phase 1 completed
   more than N hours ago, we alert. This is the **no-email watchdog** from the 06-26
   carry-forward, and TRIG-01 is the right time to build it — a push-only trigger with no
   absence detection is a single point of silent failure, which is the exact class of bug
   we keep paying for. Note for Toby: a watchdog is inherently time-based; it is a
   *safety net*, not the trigger, and John's no-polling rule targets the trigger. **Confirm
   this reading with John before building it** rather than assuming the exemption.

## Notes

- `publisher_email.py` stays. Its identification and URL-repair logic is the reusable part;
  only the *transport* changes. Do not fork that logic into a new module — PQ-4.
- Credentials for Example Publisher go in the secrets store, never in the repo. Orly reviews.
- Related: R1-01 (silent-stranding class), ADR-0002 (drifting publisher contract),
  ADR-0005 (idempotent enqueue).
