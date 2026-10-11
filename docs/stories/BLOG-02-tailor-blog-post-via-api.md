# BLOG-02 — Phase 2 writes the episode post to Tailor as a draft, in plain code (no model)

**Owner:** Bill (impl) · **Tests:** Jen · **Security:** Orly · **Ops/box:** Oliver · **Review/deploy:** Toby · **PO:** Angela
**Priority:** HIGH — blocks E32 · **Status:** **02a BUILT** (branch `feat/blog-02a`, Jen review → Toby) · 02b/02c wait on RTS-3
**Created:** 2026-09-22 · **Raised by:** John

---

## Why

rockstarblends.com moved from BigCommerce + DropInBlog to Tailor on 2026-09-21 (now rsb-991,
v0.9.91). Phase 2 still produces the old artifact:

- **Step 6** (`jobs/publish_finalize.py` → `anthropic_client.compose_blog_page`) has the **model**
  write `blog_page.html` in DropInBlog markup (`prompts/blog_page.md` v4.0 — Froala classes, a
  hand-counted TOC, icons on `dropinblog.net`). A person pastes it in. Tailor has no paste target
  for that markup, and the migration dropped every `dropinblog.net` reference (migration-spec AC-4).
- **Step 5** derives the post URL from `_BLOG_BASE_URL = …/the-blog` (`:71`) plus a slug guess
  (`_derive_blog_url`, `:272`). `/the-blog/*` now 301s. A derived URL is also a guess, and a wrong
  guess already slipped out once (BLOG-01: the `don-t` slug 404'd, and the dead link went into
  E29's schedule and thank-you draft).

John's rulings, 2026-09-22:

1. **Automate through the API.** Phase 2 creates the post on the live instance.
2. **The format is what's on :8627 (content truth, rsb-991) after the hand edits.**
3. **The model is out of the blog step.** Every input is already structured; same principle
   as TY-02 / ADR-0010.
4. **Match the content 1:1 for now.** No new sections, product cards, citations or embeds.

## The target — read from :8627, 2026-09-22 (read-only)

29 posts tagged `podcast`. Every post has three listen buttons and a hero image. **E27, E28,
E30 and E31 share one exact 16-block layout**, and that is the template. (E29 has no Share
section. E26 and earlier are older DropInBlog shapes and are not models.)

Reference post: `/api/pages/blog/bliss-is-the-system-the-ecs-estrogen-perimenopause-connection` (E31).

| # | Block | Content | Source |
|---|---|---|---|
| 0 | `section`, empty h2 | Author intro paragraph: *In **"⟨title⟩"**, host [Heather Reilly Hiemstra](/about) sits down with **⟨guest⟩** — ⟨descriptor⟩ — for a conversation about ⟨topics⟩.* followed by 3 `cta` nodes: `Listen on Spotify` / `Listen on Apple` / `Listen on Audible` | title + guest + descriptor + topics: apple_summary; URLs: Phase-2 trigger; Audible: fixed |
| 1 | `toc` | `title: "On this page"` | fixed |
| 2 | `section`, empty h2 | Hook paragraphs → "Conversations include:" → `ul` (each `li` = **bold lead** + body) → tune-in CTA | apple_summary, verbatim |
| 3 | `section`, h2 `About ⟨guest⟩` | Bio paragraphs (+ "Learn more" link if present) | apple_summary About, verbatim |
| 4 | `section`, h2 `Episode Highlights`, content = one `h3` | First quote heading | quotes.json[0].heading (F-1) |
| 5 | `blockquote` | `quote` = "“…”" (curly quotes included), `attribution` = speaker first name (with honorific as in E31 "Dr. Leyla"), `background: plain` | quotes.json[0] |
| 6…(2n+3) | per remaining quote: `section` (empty h2, content = one `h3`) + `blockquote` | | quotes.json[1..] |
| next | `section`, h2 `Share this episode` | fixed share sentence | constant (Resolved, OPEN-3) |
| last | `faq`, `title: "FAQs"` | `question` × 3–5: `prompt` + `answer` paragraphs | faq.md |

Page fields: `parent_slug: blog`, `content_mode: blocks`, `layout: child`,
`hero_layout: simple_text`, `hero_media_id` = the uploaded episode tile (`card_media_id` is derived from it — see 02b facts), tag
`podcast`, `title` = `⟨Hook⟩ | ⟨Topic⟩ | GenX Hits Menopause E⟨N⟩`, `meta_description` (Resolved, OPEN-2).

## Acceptance criteria

**AC-1 — Deterministic build, no model.** A pure function
`build_post(apple_summary, quotes, faq, meta) → PageCreate JSON` produces the table above.
`compose_blog_page` is retired from Phase 2, and `prompts/blog_page.md` is marked superseded (kept for
history, like `compose_thank_you`). Same inputs → byte-identical JSON.

**AC-2 — Golden parity against E31 and E30.** Given E31's and E30's pipeline inputs, `build_post` output
equals the :8627 block JSON **exactly** for blocks 1–(last), and for block 0 **except** the descriptor and
topics clause (the live intro was model-written). Every remaining difference is listed in the test as a
named, accepted delta. Fixtures are inline or committed, never read from gitignored `Episodes/` (the
card-suite rule).

**AC-3 — Fails before it writes.** A gate raises **before any API call** if: block count ≠ 2n+6 for n
quotes, any listen URL is a placeholder or non-https, the title fails `is_real_title`, any text node
contains model/transcript markers (TY-02's marker set), or a quote is not verbatim `quote_text_clean`.
A gate failure = `send_pipeline_alert`, never a half-written post.

**AC-4 — Creates a draft, never publishes.** The media upload (tile) → `POST /api/pages` →
tag/hero/card set, all under one login. Resulting `status` = draft. **Nothing in the pipeline sets
`published`.** Heather reviews and publishes in the editor, the same draft-only rule as guest email.

**AC-5 — Idempotent.** A re-run (reprocess, PUB-01 retry) with the post already present **updates that
draft in place**, never makes a second post and never touches a **published** post (a published post →
alert + skip; the human copy wins, ADR-0008 spirit). A second run leaves exactly one post and one tile in
the media library. *(Amended 2026-09-24, Toby F-1 — scripts defer to hand edits:)* it updates the draft
only while the draft is **unchanged since the pipeline's last write** — a fingerprint of the fields it
writes, taken from Tailor's own response, kept in the ownership record. A draft edited by hand since then
(including between a worker retry's attempts), or one whose record carries no fingerprint, is **alert +
skip**, exactly like a published post.

**AC-6 — The URL comes from the API, not a guess.** `blog_post_url` = `https://rockstarblends.com/blog/`
+ the slug the create call **returned**. `_derive_blog_url` is only used to **propose** the slug. That
URL flows to the schedule write-back, thank-you draft and captions exactly as today. `_BLOG_BASE_URL`
default → `/blog`.

**AC-7 — Least-privilege credential.** The pipeline logs in as a dedicated account on the lowest role
that can create a post and upload media (`author`, per Tailor ADR-0027, if that's enough; Orly confirms).
The credential lives only in the box env, never in the repo, a log line or a Drive artifact. Orly signs
off before the first live write.

**AC-8 — Retest on a disposable instance, then E32.** Full Phase 2 via
`reprocess_episode.py --episode 31` against a **disposable** Tailor instance built from a fresh
:8627 export (**never** production and never :8627, the live content lane; memory rule "no test writes
on live client content"). Pass = the draft renders with 16 blocks and a working TOC, FAQ structured
data appears once published on the disposable instance, and the second run leaves exactly one post. Only
then does `BLOG_API_BASE` point at production for E32.

**AC-9 — Drive keeps a record.** The built JSON is uploaded to the episode's Drive folder as
`blog_post.json` (replacing `blog_page.html`), so the review surface and the audit trail stay where
they've always been.

## Resolved — John, 2026-09-22

- **OPEN-1 → pre-send check.** Order unchanged. `create_thank_you_draft.py` (and the Phase-2 thank-you
  step's alert) checks that the blog URL returns **200** before it reports the draft ready; a non-200
  URL gets a named warning ("post not published yet"). The draft is still created either way, because
  Heather publishes the post before she sends the email.
- **OPEN-2 → first sentence of the hook**, capped at 160 characters on a word boundary with no ellipsis.
  Heather edits it on review.
- **OPEN-3 → a fixed sentence.** Written from the five live share lines (E25, E27, E28, E30, E31):
  > Know someone who needs this conversation right now? Send them this episode — and tag us
  > @genxhitsmenopause so we can amplify right back.

  It's one constant in code; Heather can rewrite it per post in the editor.
- **OPEN-4 → accepted.** Descriptor = the About section's first sentence after the guest's name; topics
  = the Conversations-bullet leads, comma-joined, the last pair joined with "and". AC-2 records the
  E31/E30 wording diff as an accepted delta.

## Findings from 02a and John's rulings — 2026-09-22

**F-1 — the highlight h3 had no source.** The story sourced each h3 from
`quotes.json[i].topic`, but the Brand Team never wrote `topic`. Step 7a later sets
`topic = topic_slug`, a 3-word kebab for clip filenames. The live h3s were written by the old
model blog step. **Ruling:** the Brand Team writes a `heading` per quote (a content surface
John already reviews in quotes.json). `build_post` reads `quote["heading"]`.
- Spec, in prompts/brand_team_marcus.md and carried through Lisa's schema: Title Case,
  ≤60 characters, names the idea, not the speaker.
- `content_gates.assert_quote_headings` refuses a headless set in **Phase 1** (round_1,
  right after the Brand Team, before the title, counter and upload; also rerun_brand_team.py,
  before the old quotes.json is deleted). The blog gate refuses an empty h3 in Phase 2 as
  a backstop.
- E31 and earlier have no `heading`. The E30/E31 golden fixtures carry the live h3s as
  `heading`, noted in each fixture.

**F-2 — the bullet lead had no delimiter after a hand edit.** The `text/plain` export of
the edited apple_summary Doc dropped the bold that ends each bullet's lead. **Ruling:** fix
it at the download, not in John's editing. `drive_client.download_file` exports a Doc
named `*.md` as `text/markdown`, which keeps the bold. See ADR-0008 addendum 2026-09-22 for
the escape handling and for why `.md` only (a JSON/transcript Doc keeps `text/plain`).
- E31's Doc has a real slip: `**The Bliss Molecule You're Running Low** On`. It is an
  accepted delta, not auto-repaired.

**FAQ quotes — decided by the data.** faq.md has straight quotes. The live FAQs are curly
everywhere except two E31 answers that were edited by hand on live. With curling, E30's five
answers and E31's first two match live exactly, so the build curls FAQ text like everything else.

**Golden result (02a).** All 16 blocks, the FAQ included, plus the page fields equal :8627
except the named, reasoned deltas in `test_blog02_build_post.ACCEPTED_DELTAS`: 14 for E31
(mostly hand edits on live after the inputs: the title, the tune-in, two FAQ answers, a
deleted FAQ Q5; plus the rulings and the Doc's bold slip) and 4 for E30 (the rulings, and
the Doc bolding "Go Unpro").

## 02b scope — additions from what 02a found in Tailor v0.9.91 (read-only, schemas + :8627 GETs)

- **Create is two calls.** `PageCreate` (`POST /api/pages`) accepts title, slug,
  parent_slug, tag_slugs, layout, hero_layout, hero_media_id and meta_description, but **not**
  `blocks`, `content_mode` or `status`. Those are `PageUpdate` (`PUT /api/pages/blog/{slug}`)
  fields; author-materia.py does the same POST → PUT. `build_post` returns the whole post as
  one dict, and the 02b client splits it. Neither call sends `status`: a created page is a
  draft (AC-4).
- **`card_media_id` is derived, not written.** It is a read projection that Tailor resolves
  from the thumbnail → hero → gallery → first image. Setting `hero_media_id` = the tile sets
  the card. No write field exists.
- **Slug cap is 64** (`PageCreate.slug max_length=64`). E31's live slug is 62 characters.
  `blog_post.propose_slug` caps on a hyphen boundary.
- **Live slugs have no `-genx-hits-menopause-eN` suffix** (E30:
  `feed-your-head-gut-microbiome-hormones-midlife-vitality`). `_derive_blog_url` still
  appends one. AC-6's proposal must use `blog_post.propose_slug` (one rule), and
  `_derive_blog_url` should delegate to it when `_BLOG_BASE_URL` moves to `/blog`.
- Both golden posts validate against Tailor's own `PageCreate`/`PageUpdate` Pydantic models,
  and the blocks round-trip unchanged. That was a throwaway check, not a committed test,
  because the repos stay separate.
- Carry-overs already owned by 02b: retire `compose_blog_page` (and `_DROPINBLOG_CDN` and the
  placeholder injection), `_BLOG_BASE_URL` → `/blog`, tile upload → `hero_media_id`,
  idempotency and the published-skip (AC-5), `blog_post.json` to Drive (AC-9).
- **Deploy note:** `prompts/blog_page.md` now carries a SUPERSEDED banner, and
  `_load_prompt` sends it to the model with the rest of the file. 02a must ship with 02b, not
  ahead of it.

## Reviews and rulings — 2026-09-22/23 (Jen, Orly, Oliver; John)

**Jen (tests):** 02a applied her review in `3463ab8` + `5ee7471`. All 25 of her mutations go red (M10 re-anchored after
A-1 removed its old target). DOD-1 is met locally. The repo has no CI, so **Toby names what counts as the pipeline.**

**Orly (AC-7):** ruling at `docs/security/2026-09-22-orly-blog02-pipeline-credential.md`: **`author` PASS WITH
CONDITIONS, `editor` FAIL** (an editor login reaches customer orders, coupons, refunds, redirects, and publishing or
deleting Heather's pages). **Conditions C-1..C-14 are binding on 02b.** Key facts:
- `author` **can** publish its own pages, so "never publishes" is a code guarantee: request bodies never carry
  `status` or `published_at`, and a test proves it.
- `author` **cannot upload media** (`routers/media.py:331`, `:378-385`), so it can't upload the tile.
- One session per account: log in and out on every run, and allow only one blog write at a time. Nobody uses the
  account in a browser.
- The password goes in a worker-only root file `/etc/rockstar-pipeline/tailor-blog.env`, **not** the shared `.env`.
- Posts need `under_module_pillar: true` (`/blog` is a module pillar).
- An unexpected slug collision means alert and stop, never adopt the existing page.

**Oliver (box + disposable instance):** `rsb-991-blogtest` on :8691, built from a fresh :8627 export (21:56Z). E31 is
field-for-field equal to :8627. Record: `rockstar-blends/docs/rsb-991-blogtest-record.md`. Box is idle, and the
deployed code is `c4668d4`. 02c blockers:
1. `blog_post.py` is missing from `deploy.sh` FILES, so the worker would crash on import.
2. `deploy.sh` ships whatever is checked out, so deploy only from a clean `main`.
3. The box `.env` sets `BLOG_BASE_URL=/the-blog`, which overrides the new code default. Back up the `.env` under a
   fresh timestamp first. Three keys are duplicated.

**John, 2026-09-23:**
- **Byline and identity:** the pipeline account's display name is **Heather Reilly Hiemstra**, so every author and
  byline detail stays exactly as on existing posts, and the intro links to `/about`. Account email:
  `blog-pipeline@example.test` (Cloudflare-routed to a mailbox John reads). Role: `author`. John creates it
  following Orly's steps.
  **Confirmed necessary on v0.9.94 (Bill, 2026-09-24):** an `author` cannot set `pages.byline` (403
  CONTENT_PRIVILEGE, `routers/pages.py:1432`); the byline resolves from the owner account's name
  (`:720-759`), and names can't be edited after creation. So the account's name IS the byline: First "Heather",
  Last "Reilly Hiemstra". The pipeline never sends `byline`; it reads it back and warns in the alert on a mismatch.
- **Heather's production role is `editor` or above**, so she can edit and publish pipeline drafts. Confirmed.
- **E32 = Imogen Farrelly.** The July Round 1 ran on old code and was never submitted. **E32 restarts from scratch
  from her audio** when we're ready, so it gets `heading` from Phase 1 like every episode after it.
- **The tile is the Cover Image, not the hero (John, 2026-09-23):** *"in blog post templates above the fold, the
  featured image is set as 'Cover Image' in Post tab, not as element in Content tab."* Cover Image =
  `thumbnail_media_id` (its own 4:3 `card` crop; `workbench.$slug.content.tsx:341-345`). New posts set
  `thumbnail_media_id` = tile, leave `hero_media_id` **null**, `hero_layout: simple_text`. This supersedes the
  "Page fields" line in the target table (`hero_media_id` = `card_media_id` = tile), which described the migrated
  posts: on :8627 all recent podcast posts carry the tile in `hero_media_id` with Cover Image empty. That is a named
  golden delta (reason: this ruling). Moving the 29 existing posts is a content-lane question, not BLOG-02's.
  How the tile reaches Cover Image when `author` can't upload is **still open** (see RTS).

**AC-8 revised (Angela, 2026-09-22):** a full `reprocess_episode.py --episode 31` would render and upload reels, create
a thank-you draft, and write the disposable post's URL into **E31's real schedule entry**, and the box can't reach
:8691 anyway. So the retest runs **only the blog step, from the Mac**, against :8691, on the E31 fixture inputs
(which carry the live h3s as `heading`). Pass = one draft with 16 blocks; the TOC works; FAQPage appears once it is
published on the disposable instance; a second run leaves exactly one post; a published post is skipped with an
alert. The first full end-to-end run is E32. That is safe because the output is a draft Heather reviews.

## Ready to Start (RTS) — as of 2026-09-22

- RTS-1 problem + rulings: ✅ John, above.
- RTS-2 testable ACs: ✅ OPEN-1..4 resolved by John 2026-09-22.
- RTS-3 dependencies: ✅ Orly ruled (author, C-1..C-14); ✅ Oliver built :8691 and checked the box; ✅ Tailor contract
  read (POST then PUT, `under_module_pillar`, 64-char slug). ✅ **Cover Image path decided (John, 2026-09-23): fully automatic via Tailor
  BACKLOG-0211** (an author may upload to a page it owns; next Tailor lane; picked up in another window). Until
  it's on production the pipeline runs in **manual-cover mode**: it creates the draft with Cover Image empty and the
  alert tells Heather to set it, with a link to the tile on Drive. It switches to automatic when the upload
  returns 201 instead of 403. The alert names the mode. **Dependency:** BACKLOG-0206 (og:image falls back to the
  cover) must ship no later than 0211, or cover-only posts share with no image.
- RTS-4 sized: likely L. Split: **02a** build + golden tests (no network) — built 2026-09-22 → **02b** API
  write + idempotency on the disposable instance → **02c** credential + deploy + E32.
- RTS-5 owner: ✅ Bill.

REEL-07 (committed, unpushed) ships in the same deploy so the worker restarts once.

## Evidence and status — 2026-09-24 (Angela)

**Status:** 02a + 02b BUILT and reviewed. Toby APPROVED WITH CONDITIONS (merge: M-1 this section, M-2 systemd
drop-in, M-3 `compose_blog_page` deleted). Deploy gated on Orly's 02b sign-off, John's production account, and
Oliver's runbook (in Toby's review). F-1 (retries must not overwrite Heather's draft edits) fixed before E32.

**ACs amended by rulings:** AC-4 (Cover Image = `thumbnail_media_id`, not the hero; the pipeline never sends
`status`/`published_at`/`byline`; manual-cover mode on Tailor < 0.9.94). AC-5 (the pipeline's own ownership record;
a published post is skipped with an alert; a slug it doesn't own means alert + stop; per F-1, a draft hand-edited
since our last write also means alert + skip). AC-8 (blog step only, from the Mac, on a disposable instance, same
content with a slug suffix, never renaming or deleting existing posts).

**Tests (no CI; Toby's accepted pipeline):** the named suites run under `python3 -B` (see the PR), plus
`node --test scripts/render_reel.test.mjs`.
- **Jen, 02a (09-22):** 26 mutations; after fixes all red.
- **Jen, 02b round 1 (09-24, at 2f03a8c):** 52 mutations, 14 survived. DOD-1 NOT MET. Found F-1..F-12, including
  the stale-session 404 (High).
- **02b round 2 (09-24, at 8fa2c69, Jen's harness):** 66 mutations, 64 red, 2 accepted. M8b is backstopped by the
  pre-PUT published check. X4 is backstopped by the PUT's 404 → BlogStop and by the `/me` confirmation. The reasons
  are in `test_blog02b_tailor_write.py` under "Accepted survivors".
- The AC→test map is the header of `test_blog02b_tailor_write.py`.

**AC-8 live retest — rsb-995-blogtest (:8695), Tailor v0.9.94, content from :8693, E31 inputs**
(record: `rockstar-blends/docs/rsb-995-blogtest-record.md`):

| Run | Code | What | Result |
|---|---|---|---|
| 1 | 35dcea9 | E31 at its live slug | exit 3: collision, **not adopted**, no write, logout |
| 2 | f7a288c | slug `…-32` | **created** draft: 16 blocks, cover automatic, byline Heather Reilly Hiemstra |
| 3 | f7a288c | repeat | **updated in place**, same id, no second upload |
| — | — | John reviewed and published `-32` in the workbench | — |
| 4 | 8fa2c69 | `-32` again | **skipped_published**, alert, no PUT/upload |
| 5 | 8fa2c69 | slug `…-33`, fresh state | **created**; the 404 confirmed via `/me` (F-1 fix) |
| 6 | 8fa2c69 | repeat | **updated**; cover media checked (200) and reused |

Oliver's public checks on the published `-32`, all PASS: 16 blocks server-rendered, TOC matches headings; exactly one
FAQPage (5 Q); BlogPosting author Heather Reilly Hiemstra, image = cover; og:image = cover, og:type article,
og:url canonical; cover above the fold, no hero; listen CTAs correct; listed on the podcast index.

**Still open:** AC-7 (the production `author` account, John), C-12/C-14 (Oliver, before E32), and Orly's 02b sign-off.
