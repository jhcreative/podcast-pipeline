# SEC-01 — The webhook service fails closed: `/status` behind auth, Pub/Sub verified

**Owner:** Bill (impl) · **Security:** Orly · **Box:** Oliver · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** **High** — ahead of every other story in this set · **Status:** **Proposed** — Angela, 2026-10-10, from Orly's sharing review; **first slot with PROMPT-01 Part 1 and TRIG-03 (John, 2026-10-10)**; Q1 open
**Created:** 2026-10-10 · **Raised by:** Orly (sharing review), Toby (story audit) · **Blocks:** DASH-01, SHARE-02

---

## Why

Found while reviewing the code for sharing, confirmed in code by Orly and Toby independently. Nobody has probed
the live host, so exposure today is **unverified**.

| # | Finding | Where | Orly |
|---|---|---|---|
| 1 | `/status` has no auth and returns `SELECT * FROM jobs LIMIT 20`: `episode_id`, `payload`, `error` text | `webhook_service.py:118`, `db.py:323` | High while live |
| 2 | The `Caddyfile` proxies all of `pipeline.example.test` to `:8000`, no path filter | `Caddyfile` | (enables 1) |
| 3 | `/webhook/gmail` "auth" compares a subscription name the caller puts in the body; Pub/Sub's OIDC JWT is not verified, so anyone can trigger a Gmail query | `webhook_service.py:94-106` | Medium |
| 4 | Drive token and Pub/Sub checks are skipped when their env var is unset (dev-mode path) | `webhook_service.py:86`, `:94` | Medium |
| 5 | Token compared with `!=`, not `hmac.compare_digest` | `webhook_service.py:86` | Low |

A reviewer reading this code in an interview will spot 1 and 3 within minutes. Fix it before it's shown, and before
DASH-01 adds anything new to that host.

## Scope

1. Orly confirms or rules out finding 1 on the live host — **one read-only GET, on John's go**, output pasted here.
2. `/status`: Q1 decides between bound to localhost (Caddy never routes it) and a bearer secret.
3. `/webhook/gmail` verifies the Pub/Sub push JWT (issuer, audience, service-account email).
4. Every auth check fails **closed** when its secret is unset. The service refuses to start without them, except under
   an explicit `PIPELINE_DEV=1` that a test proves the systemd unit never sets.
5. `hmac.compare_digest` for every shared-secret comparison.

## Acceptance criteria

1. `GET /status` without credentials returns 401 or is unreachable through Caddy. *Test:* TestClient, no header → 401.
2. A Gmail push with a missing, expired or wrong-audience JWT returns 401 and enqueues nothing. *Test:* signed fixture
   tokens (good, wrong `aud`, expired).
3. With `DRIVE_WEBHOOK_TOKEN` or the Pub/Sub settings unset and `PIPELINE_DEV` unset, the service exits non-zero at
   start-up and names the missing setting.
4. `grep -n '!= .*token\|token .*!=' webhook_service.py` returns nothing; a test asserts `compare_digest` is used.
5. After deploy, the live GET from scope 1 returns 401 (or 404 through Caddy). Output and date pasted here.

## Decisions (open — John)

| # | Question | Recommendation |
|---|---|---|
| Q1 | `/status`: localhost only, or a secret? | **Answered (John, 2026-10-10): localhost only.** Original recommendation: **Localhost only.** John reads it over ssh; nothing outside the box needs it. Fewest moving parts. |

## Shipping it (John runs each line; agents don't touch the box)

```bash
cd ~/RockstarPodcast
bash scripts/deploy.sh --check
bash scripts/deploy.sh
curl -s -o /dev/null -w '%{http_code}\n' https://pipeline.example.test/status   # expect 401 or 404
```

---

## Live check — Orly, 2026-10-10T18:55:52Z (John's go: "go")

One read-only `GET https://pipeline.example.test/status`, no credentials. Only the status line and the response's shape
were printed; the body was deleted unread.

```
http=200 bytes=7171  content-type: application/json
shape: {'queue': dict, 'recent': list[20]}
payload non-empty in 20 of 20 rows · error non-empty in 1 of 20 · episode_id non-empty in 20 of 20
```

**Finding 1 is CONFIRMED live: High.** Anyone on the internet can read the last 20 job rows, payloads and error
text today.

**Interim mitigation, until SEC-01 ships (John runs it; a box change):** one line in the box's Caddy site block,
ahead of the proxy, then a reload: `respond /status 404`. Check afterwards with the `curl` in *Shipping it* (expect 404).
The webhooks keep working; only `/status` stops answering from outside.

---

## Built — Bill, 2026-10-10 (branch `feat/sec-01`)

**What changed:**
- **`webhook_auth.py`** (new; one home for the rules; in deploy FILES, which PROMPT-01's guard demanded on the
  first run):
  - **`status_allowed`:** a loopback peer **and** no proxy forwarding header. uvicorn listens on `127.0.0.1`, so
    every outside request arrives through Caddy, whose own connection is loopback too. Caddy always adds
    `X-Forwarded-For`; a `curl` on the box doesn't.
  - **`verify_pubsub_push`:** google-auth `verify_token` (signature against Google's certs, expiry, `aud`), then
    the issuer is Google and `email` is our push service account with `email_verified`.
  - **`secret_matches`:** `hmac.compare_digest`. An unset secret matches nothing.
  - **`missing_settings`:** the four required settings.
- **`webhook_service.py`:**
  - `/status` → 404 unless `status_allowed`.
  - The Gmail push needs a verified token and an exact subscription name.
  - The Drive token is compared in constant time.
  - **Start-up refuses** while a required setting is unset, naming it, unless `PIPELINE_DEV=1`.
  - The silent "dev mode" skips are gone.
- **`Caddyfile`:** `respond /status 404`, the second layer. The box's Caddy config is managed by hand, so John
  applies it.
- **`.env.example`:** `PUBSUB_PUSH_AUDIENCE`, `PUBSUB_PUSH_SA_EMAIL`.

**Evidence, 2026-10-10:**
- `python3 -B test_sec01_webhook_auth.py` → **10 passed, 0 failed**.
  - **Real RS256 tokens**, signed with a throwaway key and checked by google-auth's own `verify_token`. Only the
    certificate fetch is faked.
    - The good token passes.
    - Each refusal fails for its own reason: wrong `aud` and expired → `InvalidValue`; another key → signature
      `MalformedError`; another service account, an unverified email, a non-Google issuer → named.
  - **The real `webhook_service`, under test_pub01's stubs:**
    - `/status` through the proxy → 404; on the box → the status.
    - A push with no token or a bad token → 401, with nothing enqueued.
    - Start-up refuses, naming each missing setting in turn.
  - No systemd unit sets `PIPELINE_DEV`.
- `test_pub01` 5 of 5: its fake request gained `.headers`, and the stub follows the renamed check.
- The rest of the suite matches `main`.

## Rollout — the order matters (first run of this path: Oliver names the risk)

The service now **refuses to start** without the four settings, and refuses every Gmail push without a token. Done
in the wrong order, that takes the webhook down, and Gmail pushes then queue in Pub/Sub, which retries for up to 7
days. The 10-minute Drive poll keeps Drive covered either way.

1. **Read-only check on the box** (below): which of the four settings are set.
2. **Google Cloud Console** → Pub/Sub → Subscriptions → the Gmail push subscription → **Edit** → *Enable
   authentication*:
   - Pick a service account; its email becomes `PUBSUB_PUSH_SA_EMAIL`.
   - Set the audience to `https://pipeline.example.test/webhook/gmail`.
   - **Prerequisites:** whoever saves the change needs `iam.serviceAccounts.actAs` on that service account. On a
     project created before 2021-04-08, the Pub/Sub service agent
     (`service-<project-number>@gcp-sa-pubsub.iam.gserviceaccount.com`) also needs
     `roles/iam.serviceAccountTokenCreator` on it. Without that, pushes arrive with no token or stop.
   - Today's code ignores the token, so this step is safe on its own.
3. **Box `.env`:**
   - Add `PUBSUB_PUSH_AUDIENCE` and `PUBSUB_PUSH_SA_EMAIL`.
   - If `WEBHOOK_SHARED_SECRET` is unset, set it. Step 5 re-arms the Drive watch with it straight away; until then,
     Drive pushes get 401 and the 10-minute poll covers them.
4. **Box Caddy:** add `respond /status 404` to the site block and run `systemctl reload caddy`.
5. **Deploy** (`deploy.sh`), then:
   - `systemctl start rockstar-renew-watches.service`, which re-arms the Drive watch with the token now rather than
     at the 04:00 UTC renewal.
   - `curl -s -o /dev/null -w '%{http_code}\n' https://pipeline.example.test/status` → `404`.
   - `systemctl is-active rockstar-webhook` → `active`.
   - `journalctl -u rockstar-webhook --since -1h | grep -c 'push refused'` → `0` after the next Gmail push. Don't wait
     hours for one: also check the subscription's *unacked messages* in the console, which should not climb.
6. **Rollback.** The primary path: revert the merge on `main` and run `deploy.sh`. That ships the previous
   `webhook_service.py`, and the service runs as it did before this PR.
   - **Last resort only:** `PIPELINE_DEV=1` in `.env`. It switches off **both** the Drive and the Gmail checks, which
     is weaker than before this PR, and it is the one setting the tests say must never be on the box.
   - If John uses it, it carries a removal date in this story and comes out with the fix.

**Box check for step 1** (from the Mac; prints set or unset and the length, never a secret):

```bash
ssh root@203.0.113.10 'for k in WEBHOOK_SHARED_SECRET GMAIL_PUBSUB_SUBSCRIPTION PUBSUB_PUSH_AUDIENCE PUBSUB_PUSH_SA_EMAIL; do v=$(grep -E "^$k=" /opt/rockstar-pipeline/.env | tail -1 | cut -d= -f2-); if [ -n "$v" ]; then echo "$k: set (${#v} chars)"; else echo "$k: UNSET"; fi; done; grep -E "^GMAIL_PUBSUB_SUBSCRIPTION=" /opt/rockstar-pipeline/.env; caddy version'
```

**Cold review of PR #32 (DOD-3), 2026-10-10: Toby and Orly approved with changes. All addressed:**
- **Rollback:** the merge is reverted and redeployed; `PIPELINE_DEV` is a dated last resort that names what it
  opens (step 6).
- **Clock skew:** `clock_skew_in_seconds=30`.
- **Pub/Sub prerequisites** (`actAs`, the service agent's token-creator role) are in step 2.
- **Drive watch** re-armed straight after the deploy; the unacked count and the `push refused` count are checks in
  step 5.
- **Tests added:** a good push accepted end to end (it enqueues); a good token for another subscription → 401; Drive
  token missing, empty or wrong → 401, right → 200; certs unreachable → 503; skew of 10 s passes, 120 s doesn't.
- **Certs unreachable → 503** (`CertsUnavailable`): our fault, not the caller's 401.
- **Settings stripped**, as `missing_settings` strips them.
- **Left as is:** an unparseable body still gets a 200 before auth, with nothing enqueued, as Pub/Sub expects.
  `RestartPreventExitStatus=3` is not added: the restart loop keeps naming the missing setting in the journal.

**Evidence:** `test_sec01_webhook_auth.py` → **14 passed**. With the skew set back to 0, the skew test fails
(13 of 14).

**Re-review of `848e458`: approve with changes. Done:**
- A wrong subscription name is now logged as `push refused`, so the rollout's count catches it.
- `renew_watches.py` strips the secret, as the service does.
- The certs fetch times out at 10 s, not google-auth's 120 s, since it runs inside the async handler. A test asserts
  the timeout is passed.

**Merge note (required, from the re-review):** merged with `feat/trig-03`, `git` reports no conflict, but
`test_trig03_links_only.py::test_webhook_refuses_an_unauthenticated_publish_email` still patches
`_assert_pubsub_subscription` and fails (30 of 31). Whichever PR merges second changes that line to
`webhook_service._assert_pubsub_push = lambda authorization, env: None` and reruns all three suites before merging.

**Left as is:** a 200 from the certs URL with a body that isn't JSON (a captive page) is logged as 401 rather than
503. Pub/Sub retries either way. **Backlog:** cache Google's certs, rather than fetching them on every push.

### Rollout step 1 — box check, John, 2026-10-10

```
WEBHOOK_SHARED_SECRET: set (43 chars)
GMAIL_PUBSUB_SUBSCRIPTION: set (14 chars)   value: gmail-push-sub
PUBSUB_PUSH_AUDIENCE: UNSET
PUBSUB_PUSH_SA_EMAIL: UNSET
caddy v2.11.3
```

- **Drive is ready.** The secret is already set, so the Drive watch is armed with it today. After the deploy, Drive
  pushes keep working, and re-arming the watch in step 5 is a formality.
- **Gmail needs steps 2 and 3:** enable authentication on the subscription `gmail-push-sub`, then add the two new
  settings. Deploying before then stops the service from starting, as designed.
- **Caddy v2.11.3** sets `X-Forwarded-For` on every proxied request, and with no `trusted_proxies` it replaces any
  value a client sends, so the `/status` rule holds.

### Rollout step 2 — done (Claude in John's Chrome, on John's go, 2026-10-10)

`gmail-push-sub` in project **`example-gcp-project`**. `.env.example`'s `rockstar-pipeline` is the display name,
not the project ID.

| Setting | Before | After (read back from the saved form) |
|---|---|---|
| Push endpoint | `https://pipeline.example.test/webhook/gmail` | unchanged |
| Enable authentication | off | **on** |
| Service account | — | `pipeline-sa@example-gcp-project.iam.gserviceaccount.example.test`, the project's only service account |
| Audience | — | `https://pipeline.example.test/webhook/gmail` |

- The console saved it without an IAM error: "Subscription successfully edited". Today's code ignores the token, so
  Gmail delivery is unchanged until the deploy.
- **Noted, not changed:** the subscription **expires after 31 days of inactivity**. Gmail pushes on every mailbox
  change, so it stays active in practice. It is still a quiet way for the trigger to disappear.
  Recommendation: "Never expire". John decides.
- The console shows "Your free trial is over"; Pub/Sub at this volume sits in the always-free tier.

**Step 3, for John:** add to `/opt/rockstar-pipeline/.env`:

```
PUBSUB_PUSH_AUDIENCE=https://pipeline.example.test/webhook/gmail
PUBSUB_PUSH_SA_EMAIL=pipeline-sa@example-gcp-project.iam.gserviceaccount.example.test
```

### Rollout steps 3–5d — done (John, 2026-10-10)

- **Step 3:** the first attempt never reached the file. `.env` was still dated Oct 1, with no `PUBSUB_PUSH` lines. The
  two lines were then appended with an idempotent command (backup, owner and mode kept), and the check read `set`.
- **Steps 4 and 5:** Caddy `respond /status 404` added (with a backup, and validated before the reload); the deploy
  shipped PROMPT-01 and SEC-01 together; the Drive watch re-armed; services active. **John reports 4a–5d complete.**
- **AC-5, the live check from outside, 2026-10-10T22:33:19Z (Claude, read-only):**

```
GET  /status                     404
GET  /health                     200
POST /webhook/gmail, no token    401
```

  It was 200 with 20 job rows at 18:55 the same day.
- **Still to see (5e):** after the next Gmail push, `journalctl -u rockstar-webhook --since -1h | grep -c 'push refused'`
  should give 0. That is the first real push carrying the OIDC token, the one path no test can prove.

**2026-10-10, John's go:** `gmail-push-sub`'s expiration period changed from "31 days of inactivity" to **Never expire**,
and read back from the saved form. The Gmail trigger can no longer disappear because the mailbox went quiet. Done in
the Chrome profile that holds the project-owner account; the others lack access to `example-gcp-project`.
