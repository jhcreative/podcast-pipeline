# TRIG-03 — A late "Your Episode is Live" email swaps in the real links, and nothing else

**Owner:** Bill (impl) · **Tests:** Jen · **Review:** Toby · **Security:** Orly (Gmail draft scope) · **PO:** Angela
**Priority:** High (a guest could get the wrong thank-you) · **Status:** **Proposed** — drafted 2026-10-10 by Claude at John's request, for Angela's RTS pass; not Ready to Start (Q1–Q3 open)
**Created:** 2026-10-10 · **Raised by:** John · **Amends:** ADR-0011, TRIG-02 · **Uses:** OBS-01 (proof of zero model calls)

---

## Why

**The publisher email is back.** ADR-0011 (2026-07-21) and TRIG-02 (2026-10-01) were written because the publisher
stopped sending "Your Episode is Live". It is arriving again, from `editor@legacy-publisher.example.test` (already on
the allowlist, `publisher_email.py:34`), and the Gmail path is processing it. Checked 2026-10-10:

| Episode | Email arrived (UTC) | `blog_post.json` written to Drive | Links in the draft |
|---|---|---|---|
| E32 | 2026-10-06 22:05:35 | 22:06:38 (+63 s) | Episode links (Spotify `/episode/76Iod…`, Apple `?i=1000700000032`) |
| E33 | 2026-10-08 17:21:41 | 17:23:04 (+83 s) | Episode links (Spotify `/episode/0TPlC…`, Apple `?i=1000700000033`) |

So Phase 2 has **two push triggers**, and they can arrive in either order:

- **Email first, then images:** works today. The email path stores the links in `pending_mm_context`
  (`publish_finalize.py:617`), and the image trigger reads them (`drive_changes.py:118`).
- **Images first, then email:** the run uses the show-page links (`episode_links_pending: true`), and the
  later email **runs all of Phase 2 again**. The email path has no check for an episode that has already
  finished. Traced 2026-10-10, that re-run:
  - **Thank-you:** creates a **second Gmail draft**. `gmail_client` can create drafts but not update or
    delete them, so the old show-link draft stays and Heather could send either one. Highest risk.
  - **Reels:** deletes the whole Drive `reels/` folder and re-renders it; the model regenerates the
    captions, overwriting any caption edits made in Drive.
  - **Blog:** rewrites an untouched draft (good). A hand-edited draft is skipped with an alert
    (`skipped_edited`), and a published post is skipped (AC-5), so the show links stay on it.
  - **Model calls and render time** are spent again for no new content.
  - **Already safe:** cards (skipped when `cards/` isn't empty), and duplicate emails (deduplicated by
    Gmail message id).

All the late email needs to do is put the two real links where the show-page links went.

## Scope

1. **Route by state.** When a publisher email arrives, the email path decides by the episode's schedule
   entry:

   | Episode state | Action |
   |---|---|
   | Phase 2 not run, images missing | Unchanged: store `pending_mm_context` and wait |
   | Phase 2 not run, images present | Unchanged: full Phase 2 with the real links |
   | `pipeline_done` and `episode_links_pending` | **New: `links_only` run** (below) |
   | `pipeline_done` and links already real | **No-op:** a log line and a standup line, nothing written |

2. **A `links_only` run** (same pattern as today's `reels_only`). It verifies the two links with the existing
   publisher title check, then:
   - **Blog draft in Tailor:** swap **only** the listen-button URLs that still equal the show-page URLs
     (`_SHOW_APPLE_URL` / `_SHOW_SPOTIFY_URL`). Nothing else in the post changes (Q1). If the post is
     **published**, it is not touched (AC-5); the alert lists both links to paste by hand.
   - **Thank-you:** rebuild `thank_you.md` with the real links (deterministic, ADR-0010) and **replace** the
     Gmail draft: delete the recorded show-link draft and create the new one. If the recorded draft is gone
     (sent or deleted), create nothing and alert (Q2). This needs `gmail_client` to gain `delete_draft`, and
     the pipeline to record the draft id when it first creates the draft.
   - **Schedule:** set `apple_url` and `spotify_url`, set `episode_links_pending` to false, and fill the
     calendar event's links if it carries them.
   - **Not touched:** reels, captions, cards, FAQ, show notes. **No model calls.**
   - **One alert:** "E<N>: real episode links in — blog draft and thank-you updated", or exactly what still
     needs pasting.
3. **The record matches reality.** An addendum to ADR-0011 (dated, not a rewrite): the email resumed in
   October 2026, both triggers are live, and here's the routing table. The README's Phase 2 paragraph names
   both triggers. TRIG-02 gets a pointer to this story.

**Out of scope:** changing the publisher allowlist; the 10-minute drain; anything about reels or captions
beyond not re-running them.

## Acceptance criteria

1. With images first then email (fixture), the email produces a `links_only` run, never a full Phase 2:
   no reel render, no caption write, no FAQ, and **zero `model_call` events** in the OBS-01 ledger.
2. A Tailor draft's two show-page listen URLs are replaced by the episode URLs, and every other block is
   byte-identical before and after. A listen URL someone already changed by hand is left alone.
3. A published post is not touched; the alert names both links.
4. Exactly **one** thank-you Gmail draft exists for the guest afterwards, and it carries the episode links.
   If the recorded draft was already sent or deleted, no new draft is created and the alert says so.
5. The schedule entry ends with real URLs and `episode_links_pending: false`.
6. An email for an episode whose links are already real changes nothing and logs it.
7. Both orders, email then images and images then email, end in the same state: one blog draft, one
   thank-you draft, real links, `pipeline_done: true`.
8. ADR-0011 has the addendum; the README names both triggers.

## Decisions (open — John)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Blog draft: swap only the two listen URLs, even if Heather has edited other parts of the draft? | **Answered (John, 2026-10-10): yes.** Swap a URL only where it still equals the show-page URL. That can't overwrite a human choice, and it fixes the one thing the pipeline knows is a placeholder. |
| Q2 | Thank-you draft already sent with show links: anything more? | **Answered (John, 2026-10-10): no new draft, no send.** Alert Heather with the episode links in case she wants to follow up herself. |
| Q3 | Should a late email also refresh the calendar event? | Yes, if the event carries the links; otherwise leave it. |

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 6, 7 | `test_trig03_links_only.py`: both trigger orders against fakes for Drive, Gmail, Tailor and the model; the OBS-01 ledger shows no `model_call` |
| 2, 3 | Same file: a Tailor fake holding a draft with the show links, one with a hand-changed link, and a published post; block bytes compared |
| 4 | Same file: a Gmail fake with a recorded draft; afterwards exactly one draft per guest. Plus a "draft already sent" fixture |
| 5 | Schedule entry before and after |
| 8 | The ADR-0011 addendum and README diff, linked here |

## Shipping it (John runs each line; agents don't touch the box)

```bash
cd ~/RockstarPodcast
bash scripts/deploy.sh --check
bash scripts/deploy.sh
# first real case: an episode whose images land before the publisher email.
# Confirm one thank-you draft in Gmail, real links on the Tailor draft, and the alert.
```

---

## RTS pass — Angela, 2026-10-10 (claims checked in code; Orly's items from her sharing review)

**Claims checked:**
- **WRONG — `gmail_client` already has `delete_draft`** (`gmail_client.py:281`, added for the E29 clean-up), and the
  Gmail scopes include `gmail.modify` (`google_auth.py:29`), which covers deleting drafts. Scope 2 shrinks: no new
  client call is needed.
- **CONFIRMED — the draft id isn't recorded.** `create_draft`'s return value is discarded (`jobs/publish_finalize.py:1258`).
  Recording it stays in scope.
- **CONFIRMED** — a full Phase 2 deletes and recreates the `reels/` folder (`publish_finalize.py:1180-1184`); `reels_only`
  is the existing narrower pattern (`:424-447`, `:1152`); the show-page constants are at `:113-114`; `pending_mm_context`
  is read at `drive_changes.py:118` and written at `publish_finalize.py:617`.
- **Q3 is mostly moot.** Calendar events are off by default (`EDITORIAL_CALENDAR_EVENT=off`, CAL-OFF): with the switch
  off, `links_only` makes no Calendar calls.
- **UNVERIFIED by me** — the E32/E33 arrival times (box data) and Gmail message-id deduplication. Bill confirms both
  before the build.

**Finding 1 — replacing the thank-you draft can delete Heather's words (PP-11). Highest risk in the story.**
Scope 2 deletes the recorded draft and creates a new one. If Heather has opened that draft and added a personal note,
deleting it erases her edit. That is the failure PP-11 exists for: the human's hand outranks the script.
- **Amended scope:** when the draft is first created, record its id **and a fingerprint of its body** (the
  `_authored.py` `may_write`/`stamp` pattern from rockstar-blends). `links_only` replaces the draft only when the
  current body still matches the fingerprint. If it doesn't match, leave the draft alone and alert Heather with the
  two links to paste in herself.
- **AC-9:** a fixture draft whose body was edited after creation is not deleted or replaced. The alert names the two
  links, and the edited body is byte-identical afterwards.
- Drafts created before this ships have no fingerprint, so they get the same treatment: never replaced, alert only.

**Finding 2 — the sender check trusts a spoofable header (Orly; pre-existing, but this story widens what the email
can do).**
- `is_publisher_sender` checks whether the From header *contains* `@legacy-publisher.example.test`
  (`publisher_email.py:42-45`). `x@legacy-publisher.example.test.example.net` passes, and so does a forged From. Nothing reads
  DKIM or SPF.
- The link regexes limit the damage: only `podcasts.apple.com` and `open.spotify.com/episode/` URLs are taken
  (`publisher_email.py:79-80`). So the worst case is the wrong episode's links in a draft, not an attacker's site.
- Today that worst case needs a full Phase 2 run. After this story, the same email triggers a cheap, automatic
  rewrite of the blog draft and the thank-you draft.
- **AC-10:** the sender's domain is matched exactly on the parsed address (`email.utils.parseaddr`; the domain
  equals an allowlisted domain or ends with `.` + it), and the message's `Authentication-Results` shows `dkim=pass`
  for that domain. Otherwise the email is ignored and an alert names it. *Tests:* a lookalike domain, a forged From
  without DKIM, and the real fixture.
- SEC-01's Pub/Sub JWT check is the other half: an unverified push can start this path at all.

**Dependency fix (RTS-3).** AC-1 asks for "zero `model_call` events in the OBS-01 ledger", but TRIG-03 ships **before**
OBS-01 (John's order).
- **AC-1 amended:** zero model calls, proven with a fake Anthropic client that counts calls and fails on any call.
- The ledger check becomes a follow-up line in OBS-01's verification, once OBS-01 exists.

**Sharing note:** the story names a third party's real address. It stays in this private repo, and SHARE-02's
redaction map covers it.

**Gate status:**
- RTS-1: pass after AC-1, AC-9 and AC-10.
- RTS-2: pass. The routing table is the contract.
- RTS-3: pass once AC-1 is amended. SEC-01 ships in the same slot.
- RTS-4: Jen's table, plus the edited-draft and spoofed-sender fixtures.
- RTS-6: Orly reviews the draft-replacement and sender rules before the build, since they touch a guest's email.
- RTS-5: start from `main` on a feature branch.

**Open (John):**
- Q1 and Q2: the recommendations stand.
- Q3: answered by CAL-OFF.
- **Q4 (new):** a thank-you draft Heather has edited gets left alone, with an alert carrying the two links.
  **Answered (John, 2026-10-10): yes.**

---

## RTS-6 design review — Orly, 2026-10-10: **cleared with amendments**

**Thank-you draft (A, High)**
- **Record.** On the schedule entry, store `ty_draft: {draft_id, message_id, sha256, created_at}`, written right after
  the create and before the alerts.
- **What is hashed:** `get_draft_body()` as read back after the create, plus To and Subject. Never what we sent
  (the blog F-1 rule).
- **"Untouched"** means all three hold:
  - the draft's `message.id` is unchanged (any save, in the UI or the API, gives a new one);
  - the hash matches;
  - the stored MIME is still a single text/plain part.
  The single-part test closes the one false-"unedited" path: an added attachment, or an HTML rewrite by Gmail's
  composer, can leave the plain part unchanged.
- **Replace in place** with `drafts.update` on the recorded id (a new `update_draft` wrapper), never delete then
  create, and never create then delete:
  - delete-then-create can leave zero drafts;
  - create-then-delete can leave two, the bug this story fixes.
  `gmail.modify` covers it, so no new scope.
- **Check timing.** Check immediately before the update. Gmail has no conditional write; the few seconds of risk are
  accepted.
- **Write nothing and alert** when the draft is gone (404), edited, or carries no fingerprint (every draft created
  before this ships).
- **The alert may carry:** the episode code, the two URLs, the recipient (to the internal alert address only) and the
  reason. Never the draft body or the publisher email's body.
- **AC-4 amended:** replaced in place via `drafts.update`, and the draft id is unchanged afterwards.
- **AC-9 amended:** add fixtures for a new message id with an identical body, and for an added attachment. Both are
  left alone.

**Sender (B, High; this story makes a forged email do more)**
- **AC-10 amended:**
  - **Domain.** Parse the From address with `parseaddr`; its domain must **exactly** equal an allowlisted domain. No
    subdomain clause: a vendor subdomain is added to the env list by name.
  - **Header.** Read only the **topmost** `Authentication-Results`, and only if its authserv-id is `mx.google.com`.
    Ignore every other copy.
  - **Pass** on either `dmarc=pass` with `header.from` equal to the From domain, or `dkim=pass` with `header.d` (or
    the `header.i` domain) aligned to it (relaxed: equal or a subdomain).
  - **Fail closed** on anything else, including `dmarc=none` without aligned DKIM. ARC is not trusted.
  - **One function**, called in both the webhook and the job.
- **Blocker before the build:** a redacted fixture of the real `From` and `Authentication-Results` lines of the E32
  and E33 emails. The rule must pass the real mail, and nobody knows yet whether Example Publisher signs with an aligned DKIM.
  John captures it on the box (command below).

**Blog swap (C, High)**
- **AC-2 amended:**
  - GET, then change only the `href` of the Apple and Spotify `cta` blocks, and only where it equals the show URL
    exactly.
  - The PUT body is **`blocks` only**: `tag_slugs` is a full-set replace and must not be sent.
  - C-9, C-5, C-1 and C-7 apply, under the blog lock.
- **Fingerprint rule** (the gap my RTS pass missed):
  - If the record's fingerprint did **not** match before the swap, **do not re-stamp** afterwards. Re-stamping would
    adopt Heather's edits as the pipeline's, and the next full run would overwrite them.
  - Re-stamp only when the fingerprint matched before the swap.
- **AC-11 (new), URL validation and duplicates.** Before any write, both URLs must match:
  - Apple: `^https://podcasts\.apple\.com/[a-z]{2}/podcast/[^/?#]+/id1785708777\?i=1000\d{9}$`, with the show id
    pinned to ours;
  - Spotify: `^https://open\.spotify\.com/episode/[A-Za-z0-9]{22}$`, with `?si=` stripped first.
  If either fails, or `normalize_apple_url` couldn't repair the Apple id, alert and write nothing. An Apple `?i=` id or
  Spotify id that another schedule entry already holds is also refused.
- **Correction (Orly's open question):** there is no "existing publisher title check" on the links. The story's
  Scope 2 claim was wrong; AC-11 is the link check.

**Other (D)**
- **AC-12 (new):**
  - The route is decided in the **worker**, against the schedule as read when the job runs, so it is serialised behind
    any visuals run.
  - A links-only episode match needs the title, or an EP number that agrees with the title; if they disagree, stop.
  - A retry after a partial run ends in the same state: the update is in place, and the schedule is written last.
- No new credentials or scopes. With CAL-OFF, no Calendar calls.

**Orly reviews the diff against these rules before John's deploy go.**

### Capture for the B blocker (John runs on the box; read-only, prints headers only)

```bash
systemd-run --wait --pipe --collect -p User=rockstar -p Group=rockstar -p WorkingDirectory=/opt/rockstar-pipeline -p EnvironmentFile=/opt/rockstar-pipeline/.env /opt/rockstar-pipeline/venv/bin/python -B - <<'PY'
import json, re, gmail_client
s = json.load(open("state/publication_schedule.json"))
for e in s["episodes"]:
    mid = e.get("mission_matters_email_id")
    if e.get("episode_number") not in (32, 33) or not mid:
        continue
    hs = gmail_client.get_message(mid)["payload"]["headers"]
    pick = lambda n: [h["value"] for h in hs if h["name"].lower() == n]
    red = lambda v: re.sub(r"[\w.+-]+@", "***@", re.sub(r"\bb=[^;]+", "b=…", v))
    print("==", e["episode_code"])
    print("From:", red(pick("from")[0]))
    ar = pick("authentication-results")
    print("Authentication-Results count:", len(ar))
    for i, v in enumerate(ar):
        print(f"AR[{i}]:", red(" ".join(v.split())))
    for v in pick("dkim-signature"):
        print("DKIM-Signature d/s:", re.findall(r"\b[ds]=([^;\s]+)", v))
PY
```

### Header capture — John ran it on the box, 2026-10-10 (blocker B cleared)

- Saved, redacted, as `tests/fixtures/trig03/auth_headers.json`: 4 real `pass` cases, plus 6 synthetic `refuse`
  cases (forged From, a planted lower header, a lookalike domain, a header not stamped by Google, unaligned DKIM,
  no header at all).
- **The E32 and E33 publish emails pass Orly's rule.** The topmost header is `mx.google.com`, DKIM passes for
  `legacy-publisher.example.test`, and `dmarc=pass header.from=legacy-publisher.example.test`. Example Publisher passes too (DKIM `publisher.example.test`,
  DMARC pass, policy quarantine). A newsletter forwarded through Cloudflare Email Routing carries two headers; Google's
  is on top, and it passes.

**Findings:**
- **Legacy Publisher' DMARC policy is `p=NONE`.** A forged From is still delivered, only marked `dmarc=fail`, so
  reading Google's verdict is what stops it. The From line can't.
- **Most mail from the allowlisted domain is not a publish notice:** newsletters, pitches, "Episode 32 Release"
  threads. The subject check (`is_publish_notification`) stays alongside the sender check.
- **E32's publisher email id was never stored on the schedule; E33's was.** E32's email arrived on 2026-10-06 at
  22:05 UTC and did not complete the path that records it. Bill reads E32's job rows and journal before building the
  routing, since this may be the images-first case itself.
- **Operations note:** a script run from `/tmp` by `systemd-run` needs `/opt/rockstar-pipeline` on `sys.path`, and long
  pasted lines break on the box. Copying the script up with `scp` worked; it goes into the SHARE-01 runbook clean-up.

### E32 and E33, read from the box — 2026-10-10 (John ran it; jobs.db opened read-only)

| Job | Created (UTC) | Trigger | Result |
|---|---|---|---|
| 139 (E33) | 10-06 21:30 | visuals | done 21:47, with the show links |
| 141 (E32) | 10-06 22:06 | gmail | **failed**: `PermanentFailure: … our recorded post /blog/… is gone (renamed or deleted in the editor)` |
| 142 (E33) | 10-08 17:22 | gmail | done 17:40, a **full Phase 2 re-run** |

- **E32 is the images-first case, stopped loudly.**
  - Phase 2 ran on 2026-10-01 with the show links. The email started a full Phase 2 again, which wrote
    `blog_post.json`, after composing the FAQ with the model, then stopped at the blog step because the post had
    been renamed in the editor.
  - The schedule still says `episode_links_pending: true`, with the show links, and no email id stored.
  - Nothing put the real links on the post or the thank-you.
  - The story's *Why* table ("links in the draft: episode links") describes `blog_post.json`, not what reached Tailor.
- **E33 is the images-first case, completed.** The full re-run made the second thank-you draft, re-rendered the
  reels and regenerated their captions: the harm this story describes, live.
- **New fixtures:**
  - E32's shape: a links-only run against a post that is gone stops with an alert and composes nothing.
  - E33's shape: images, then email, ends with exactly one draft.
- **Operations (John and Heather, today):**
  - Delete E33's Oct 6 show-link thank-you draft, if it is unsent.
  - Check E32's renamed post and its thank-you for the show links.

---

## Built — Bill, 2026-10-10 (branch `feat/trig-03`, merged with `main` at eb466cb)

**What changed:**
- **`publisher_email.py`:**
  - `sender_domain` / `is_publisher_sender`: an exact domain match.
  - `authenticated_sender(headers)`: the topmost `mx.google.com` header, with comments (ARC's) stripped;
    `dmarc=pass` for the From domain, or an aligned `dkim=pass`; fails closed with the reason.
  - `episode_url_problems`, `clean_spotify_url`, `episode_ids` (AC-11, Apple show id pinned).
- **`webhook_service.py`:** a publish-looking email that fails `authenticated_sender` is never enqueued. It is
  named in one alert and marked read, so it cannot re-alert on every push.
- **`blog_publish.swap_listen_links`:**
  - The same lock and author login as the full blog write, using our own record (C-7), with the status read again
    right before the write (C-9).
  - It changes only `cta` hrefs that exactly equal a show URL.
  - It stops if the blocks change between the read and the write.
  - It leaves a published post or a non-blocks draft alone.
  - It re-stamps the fingerprint only when the draft was still ours.
  - **Correction to Orly's C rule, from Tailor's schema** (`templates/backend/schemas/pages.py` `PageUpdate`):
    `title`, `html`, `js` and `scss` are *required* on PUT, so a blocks-only body is a 422. The PUT is
    `{title: as it stands, html/js/scss: "", blocks}`. Every other field is omitted, and Tailor leaves omitted
    fields unchanged (`model_fields_set`). `html` "" is only safe in blocks mode, hence the mode guard.
- **`gmail_client.py`:** `update_draft` (`drafts.update`, in place) and `draft_state`, which raises
  `DraftGone` on a 404.
- **`jobs/publish_finalize.py`:**
  - Step 13 records `ty_draft {draft_id, message_id, sha256, single_text_plain}` as Gmail stored it. A failure
    to record never fails the job.
  - The email path uses `authenticated_sender`.
  - **The route is decided in the worker, right after Step 2.** A finished episode with links still pending goes to
    `_links_only`; one with real links is a no-op. A finished episode never reaches Step 3 again.
  - **`_links_only`, in order:**
    1. Stop if the email names another episode's title (AC-12).
    2. AC-11 links and duplicate ids.
    3. The blog swap.
    4. The thank-you draft, updated in place only if untouched. Its record is written straight after the update,
       so a retry finds it still ours.
    5. The schedule, written **last**. `episode_links_pending` stays true while any manual step is left.
    6. One alert, plus a standup line.
- **ADR-0011:** a dated addendum: both triggers are live, the routing table, the sender rule. The README names
  both triggers.

**Evidence (DOD-1), 2026-10-10, `.venv-pipeline` (Python 3.12.3):**
- `python3 -B test_trig03_links_only.py` → **24 passed, 0 failed**:
  - sender: the real box headers pass and all six forgeries are refused; aligned and unaligned DKIM; ARC comments
    ignored; an exact domain
  - links
  - the blog swap against test_blog02b's FakeTailor, which enforces Tailor's required PUT fields and author rules:
    only the two buttons change; every other block is byte-identical; no re-stamp after a hand edit, so a later full
    run still says `skipped_edited`; a published post, a post gone, an edit between read and write, HTML mode
  - links-only runs with a model and a Drive that fail the test if touched:
    - E33's shape: one draft, same id
    - no-op
    - an edited draft, a re-saved draft, an attachment, a draft gone, no record
    - E32's shape
    - links refused
    - another title named
    - a forged sender in the job
    - a retry after a Gmail 503
  - the Step 13 record
  - the webhook refusal
- **Mutation checks:**
  - With the routing disabled (the old full re-run), the E33 test fails at "Drive touched: drive_client.list_folder"
    (16 of 24).
  - With re-stamping forced, the hand-edit test fails (23 of 24).
  - Both restored: 24 of 24.
- **Whole suite:**
  - Green: test_prompt01 22 of 22, test_pub01 5 of 5 (one stub added for the new predicate), blog02b 64, trig02 11,
    r1 25 and 14, and the rest as before.
  - Unchanged from `main`: card01, card04, card05 and reel09 (no PIL), and hf01 (worktree path).
- `scripts/check_manifest.py` → OK. No new module, so FILES is unchanged.

**Open:** a cold review (Toby), then Orly's diff review against her RTS-6 rules, before John's go. On the box, the
first real case is the next email that arrives after the images.

**Cold review of PR #31 (DOD-3), 2026-10-10: Toby approved with changes; Orly blocked on finding 1. All ten
addressed:**

| # | Finding | Fix |
|---|---|---|
| 1 | **High:** a quoted local part, which the sender controls and Google copies into its header, could smuggle a `dkim=pass` clause; two `dmarc` clauses were read as a vote | `_ar_clauses` refuses a header with a quote, a backslash or unbalanced parentheses. **Exactly one dmarc verdict decides**, and two is ambiguous. With no dmarc clause, every aligned DKIM result must pass. |
| 2 | AC-12: an EP number alone was enough | The email must name **this** episode's title, or nothing is written and the alert carries the links |
| 3 | A retry after an update that succeeded read as a hand edit | If the draft already says exactly what we would write, it is ours: it is recorded, not flagged. A 404 on either GET in `draft_state` is `DraftGone`. |
| 4 | The Gmail wrappers were untested; no email-then-images test; one test matched strings | `draft_state` and `update_draft` are run for real against a fake API object (404, multipart, the update body). Email first, then images, waits without the links-only route. A visuals run on a finished episode still goes to Step 3, shown by behaviour. |
| 5 | Routing keyed on the trigger's name | Routes on `from_email`, set only in the email branch |
| 6 | The no-op trusted the flag | Links-only when the flag **or** the stored links are still show pages |
| 7 | `thank_you.md` in Drive is not rebuilt | Left as is, on purpose (Drive untouched); the alert says so |
| 8 | The recipient can change silently | The alert names the change, from Heather to the guest |
| 9 | Webhook: alert, then mark read, could re-alert | Mark read first; alert only when the mark succeeded |
| 10 | The mode guard let a missing field through | `content_mode == "blocks"` exactly. test_blog02b's FakeTailor now returns `content_mode` as Tailor does (default `"code"`). |

**Evidence:**
- `test_trig03_links_only.py` → **31 passed**.
- **Mutation checks:**
  - No ambiguity guards → the quoted-clause probe passes (30 of 31).
  - No own-title check → the EP-only test fails (30 of 31).
  - Earlier checks still stand: no routing → 16 of 24; forced re-stamp → the hand-edit test fails.
- `test_blog02b` 64 of 64 with the more faithful fake.
- The rest of the suite matches `main`. The manifest is OK.

**Open, needs data from the box (AC-12):** we don't yet know whether a real Legacy Publisher email names the
episode's title. If it doesn't, every late email stops safely with "doesn't name the episode", and the links get
pasted by hand. That is safe, but it does nothing. John runs the read-only check below before the deploy go.

**Re-review of `58e0ed6`, 2026-10-10: Orly lifted her block. Her 23 crafted headers and 9 crafted From lines all
failed, and the real fixtures still pass. Approve with changes. Done:**
- **A refused email whose alert fails goes back to unread** (`gmail_client.mark_unread`), so the next push finds it
  and alerts. It is never lost silently.
- **The parenthesis guards now have probes only they catch:** a close paren first, and a trailing open paren.
- **Fails closed:** a property repeated inside a clause; more than one From address or From header.
- **A page with no `content_mode` is left alone,** now tested.
- **Noted, acceptable:** a finished episode stored before TRIG-02, with empty links, can now take the links-only
  route. A published post is skipped, and only validated links are recorded.

**Evidence:**
- `test_trig03_links_only.py` → **34 passed**.
- **Mutation checks:**
  - No depth guard → fails.
  - No repeated-key guard → fails.
  - No mark-unread → fails.
  - Each run is 33 of 34.
- The rest of the suite matches `main`.

**Before the deploy go (Toby, Orly):** the AC-12 box check, which asks whether the real email names the title.
**At merge** with SEC-01: `test_webhook_*` here patch `_assert_pubsub_subscription`; after SEC-01 they patch
`_assert_pubsub_push = lambda authorization, env: None`.

### AC-12 — the rule settled on real data (John, 2026-10-10)

Box check (read-only, booleans only) of the two real "Your Episode is Live" emails against the schedule:

| Email | Full title | Title's first half | Distinctive title words | Guest's full name |
|---|---|---|---|---|
| E32 | no | yes | 5 of 6 | yes |
| E33 | no | **no** | **3 of 8** | **no** |

E33 was published under reworded wording, and its email names neither the first half of the title nor the guest.
Under the strict rule it would have stopped every time. **John chose title words:**

**AC-12 (final):**
- The EP number matches.
- **At least 3 of the episode's distinctive title words** are in the email (words over 4 letters, excluding the
  show's own name and generic email words), or all of them when the title has fewer.
- **No other episode scores as high.** A tie stops the run.
- Either failure writes nothing and sends an alert carrying the links.

**Evidence:**
- `test_trig03_links_only.py` → **36 passed**. The default fixture is now E33's real title with a 3-of-8 email,
  and passes; E32's shape passes; 2 words stop; a rival that scores as high stops; the show's name never counts.
- **Mutation checks:** a threshold of 1 → 2 tests fail; no rival check → 1 fails.
- The rest of the suite matches `main`.

**Orly, cold look at `8231b06`: approved.**
- **Residual risk:** an email with the wrong EP number *and* 3 or more of the wrong episode's title words, with no
  tie. The outcome is real links in one of our own untouched drafts, seen by a human before anything publishes.
  Acceptable.
- **Ties** (two-part episodes, reworded titles) stop safely, and the alert carries the links.
- **No document-frequency filter:** a shared word lifts every episode's score equally.
- **Taken from her optional hardening:** every AC-12 alert, and the success alert, names the matched words, ours and
  each rival's. A tie is then visible without a box trip.
