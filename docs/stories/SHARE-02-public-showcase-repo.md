# SHARE-02 — A public showcase repo, built from an allowlist with fresh history

**Owner:** Toby (export) · **Security:** Orly (gates G1–G7) · **Impl:** Bill · **Consent:** John, Heather (Rockstar) · **PO:** Angela
**Priority:** Medium · **Status:** **In progress** — started 2026-10-11 (John: "start SHARE-02"); Q1–Q3 answered
**Created:** 2026-10-10 · **Raised by:** John · **Needs:** SEC-01, SHARE-01, EVAL-03, FIX-01 · **Feeds:** CASE-01

---

## Why

This repo cannot go public, and it shouldn't be rewritten to make it so. Orly's review of all 173 commits (2026-10-10):

- **Secrets: clean.** Zero hits for every key pattern, in history and at HEAD (hand pattern scan; gitleaks and
  trufflehog are not installed, so a tool scan is still owed).
- **Guest content at HEAD (High):**
  - real brand-team output in `tests/fixtures/blog02/E30`, `E31` and `r1rel/E32`
  - 12 real guest-quote cards in `quote_cards/_proof/final/`
  - three third parties' real email addresses in code, tests and ADR-0002
- **Infra details (Medium):**
  - the box IP, 58 times in history
  - `ssh root@…` in 7 files
  - the host name, the GCP project and service account, a Drive folder ID
  - the systemd credential layout
- **Licensing (Medium):**
  - `BrandAssets/fonts/primary.ttc` has no licence
  - `theme.mp3` and the closing images belong to Rockstar

The safe form is a **new repository**: an allowlisted export of this one, with synthetic fixtures and one fresh
commit. The private repo stays the working repo and isn't touched.

## Scope

1. `share/ALLOWLIST` (checked in here): the paths that may leave.
   - **In:** code, prompts, the ADRs that teach, redacted stories, `evals/public/`.
   - **Out unless rewritten:** `tests/fixtures/*`, `quote_cards/_proof/`, `BrandAssets/`, `voice_corpus/`,
     `docs/carry-forward/`, `systemd/`, `Caddyfile` and `.env.example`.
2. `scripts/share_export.py <dest>` (Python, not the `share-export.sh` first named: the redaction and gates are text work): copies the allowlist, applies a redaction map (host → `pipeline.example.test`,
   IP → `203.0.113.10`, mailboxes → `@example.test`), and creates one commit. Runs every gate; any gate red, no commit.
3. Synthetic fixtures from **FIX-01** (invented guests, `example.test` addresses, made-up quotes) replace the real ones in the export,
   and the exported tests still pass.
4. The export's README is written for a reviewer, and links CASE-01.

## Gates (Orly's; every one must pass on the export, every time)

| Gate | Check |
|---|---|
| G1 | Only allowlisted paths are present (the script diffs the tree against `ALLOWLIST`) |
| G2 | Fixtures are synthetic, and the exported tests pass |
| G3 | gitleaks **and** trufflehog over the export's full history: zero findings |
| G4 | Sentinel denylist, kept private (every guest name, John's and Heather's mailboxes, client domains, the IP, the host, the GCP and Drive/calendar IDs), zero hits across the tree and history, binaries included (`strings` over images and fonts) |
| G5 | Every image and binary viewed by a person; EXIF/XMP stripped |
| G6 | SEC-01 shipped, or a plainly worded "known issue" note in the export |
| G7 | Orly clears the export; Rockstar consents to its name and show appearing; **John gives the go to publish** (OPS-5) |
| G8 | No internal agent name survives: the public names (`share/PUBLIC_NAMES`, from jhc-agency's post engine; John, 2026-10-08) are applied as whole words, and any left stops it. Added 2026-10-11 |

## Acceptance criteria

1. `share-export.sh` on a fixture tree containing one denylisted name exits non-zero, names the file, and creates no
   commit (PQ-5a: a gate proves it can fail).
2. On the real tree, every gate prints its evidence (paths checked, counts, tool versions) and passes.
3. A fresh clone of the export runs `pytest` green in one command (SHARE-01 AC-1, on the export).
4. Orly's clearance is filed in `docs/security/` and linked here, dated.
5. Nothing is pushed to a public remote without John's go, recorded here with the date.

## Built (2026-10-11, branch `feat/share-02`)

- **`share/ALLOWLIST`**: code, tests (fixtures synthetic), prompts, ADRs, stories, eval docs, the plan, IBM Plex, the four
  systemd units and timers, `.env.example` (names only). Private: Rockstar's audio and artwork, guest-quote proofs, the
  voice corpus, ops notes and the reel handoff/PRD (they quote a guest's bio), credential drop-ins, Caddyfile, security
  sign-offs, carry-forward.
- **`scripts/share_export.py`**: exports what git has at HEAD (refuses uncommitted work), redacts with the private map plus
  the public agent names (whole words), lays `share/overlay/` on top (the reviewer README, a stand-in card background:
  the real one carries the show's logo artwork), strips image metadata, runs G1–G6 and G8, and commits only when all pass.
  A gate names the file and the denylist line number, never the term. G2 runs the exported tests with the denylist, so
  the private check runs; a skipped test fails G2.
- **Private inputs (Orly, RTS-6):** `~/jhc/secrets/rockstar-podcast/share-denylist.txt` (77 terms, 7 categories) and
  `share-redact.tsv` (123 rows). Pre-scan over 204 files: 530 hits before redaction, 0 after.
- **What the name swap alone didn't fix (Orly), and the fix:** a guest's bio in card tests, `make_captions.py` and the
  reel handoff/PRD (rewritten; the two docs excluded); episode-level Apple/Spotify links, which lead back to the real
  guest (placeholder ids; the show id stays); three tests that broke under redaction (invented names at source); the
  real-names test's hard-coded domains (now from the denylist).
- **`tests/test_fix01_no_real_names.py`** (FIX-01 AC-3, split out): marked `private`; skipped, never passed, where the
  denylist is absent.
- **Tests:** `tests/test_share02_export.py`, 6 tests (AC-1 included); each gate's mutant fails its test; both scanners
  fail on a planted fake key.

## G7

- **Orly: CLEARED, 2026-10-11:**
  [`docs/security/2026-10-11-orly-share02-g7-clearance.md`](../security/2026-10-11-orly-share02-g7-clearance.md)
  (private). The export pushed is the one the newest clearance there names, by commit. This story is itself exported,
  so it can't name that commit without changing it; any re-export needs a new G7.
- **Rockstar's consent:** John for Heather, 2026-10-11 (Q2, Q7).
- **John's go to publish:** _pending_ (AC-5).

## Decisions (open — John, Heather)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Public repo, or private with named reviewers? | **Answered (John, 2026-10-10): public showcase.** Original recommendation: **public showcase** from the export. Read access to the real repo shares 173 commits of history, and a clone can't be taken back. |
| Q2 | Does Rockstar's name stay in it? | **Answered (John, 2026-10-11): yes, Heather consents.** The show's name and Heather as host stay; everything else is redacted. |
| Q3 | Under whose GitHub account? | **Answered (John, 2026-10-11): his personal account, repo `podcast-pipeline`**, so it stays with him (V-4). |
| Q4 | Licence? | **Answered (John, 2026-10-11): MIT**, for code and docs; the show's name and Rockstar stay Rockstar's (README). |
| Q6 | Must the public code be byte-identical to production? | **Answered (John, 2026-10-11): disclose the swaps.** The export writes `SWAPPED.md` (every file whose text differs from the private source, by line count), and the README says the logic is production's with identifying values replaced. |
| Q7 | Heather's health history in three prompts (age at her cancer diagnosis, plant-medicine history, "cancer survivor") | **Answered (John, 2026-10-11): she's fine with it public.** It stays in the export. |
| Q5 | Commit author address? | **Answered (John, 2026-10-11): his GitHub no-reply**, `2340330+jhcreative@users.noreply.github.com`. `jhcreative` is his personal GitHub account (a User, his name, johnhiemstra.com), so the repo is `jhcreative/podcast-pipeline`. |
