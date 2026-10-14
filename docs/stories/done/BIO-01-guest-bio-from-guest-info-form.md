# BIO-01 — Seed the guest bio from the "Podcast Guest Info" form

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** High (every episode) · **Size:** M
**Status:** Built 2026-10-01 (branch `fix/r1-reliability`) · **Created:** 2026-10-01 · **Raised by:** John

---

## Why

John, 2026-10-01: *"before we were getting bio from the Drive doc … the Google drive form doc that guests fill
out"*.

**Today the pipeline never reads the form.** Round 1 builds `episode_metadata` from the episode number, season,
file name and slug only (`jobs/round_1.py:439-447`). `prompts/apple_summary.md` §Bio sourcing says: *"If episode
metadata includes a guest bio, use it as the seed … If no bio is provided, write a clearly marked placeholder:
[BIO PENDING — supplied manually by Heather/John before publish]"*. So the bio has always been pasted in by hand
from the guest's form answers into `apple_summary.md`. `blog_post.py:266-315` then reads it from there for the
post's "About ⟨guest⟩" section.

This makes a manual step automatic, and it removes a way for the blog's bio to come out as a placeholder or an
invented one.

## The source (read 2026-10-01, metadata only, no answers opened)

- **Form:** "Podcast Guest Info", `FORM_ID_EXAMPLE`, owner `admin@example.test`,
  last edited **2026-09-20**.
- **Answers:** a linked Sheet, "Podcast Guest Info (Responses)", `SHEET_ID_EXAMPLE`,
  in the same folder. Last modified **2026-06-11**.
- **Access:** the pipeline signs in to Google as `DRIVE_USER` through domain-wide delegation, with the full Drive
  scope (`google_auth.py:26`, `:59-66`). If that user can read the Sheet, it can export it as CSV with no new
  scope and no new dependency.

## Proposed behaviour

1. In Round 1, before the Brand Team chain (step 10), export the answers Sheet as CSV through the existing Drive
   client.
2. Find the guest's row: **the newest submission whose name matches the episode's guest.** How to match is RTS-2.
3. If exactly one row matches, add `guest_bio` to `episode_metadata`, as the guest wrote it, trimmed and
   length-capped. The prompt already says to seed from it and polish lightly, keeping the guest's own wording.
4. If no row or several rows match, or the export fails, write **no** `guest_bio`. The prompt then writes
   `BIO PENDING` as it does today, and the Round-1 alert names the reason ("no form answer for <name>", or
   "2 answers for <name>"). **We never invent a bio for a real person.**
5. Never log the bio text or any other answer from the form. Log only the row's timestamp and which match rule
   applied.

## Ready to Start: open questions for John

- **RTS-1, the live destination: ANSWERED (John, 2026-10-01).** John's working view is this Sheet, opened from
  the form (`urp=linked`), on tab **`gid=815049215`**. The pipeline reads that tab by gid, never "the first
  tab". **Still open:** the Sheet's Drive modified time is 06-11, so check whether that guest has submitted the
  form. If she hasn't, E32 takes the BIO PENDING path and John pastes the bio by hand, as today.
- **RTS-2, how do we know which row belongs to which episode?** When Round 1 runs, the guest name is `TBD`
  (`round_1.py:573`, *"Guest name TBD — fill in before Round 2"*). Options:
  - (a) move the bio lookup to **Round 2**, after the name is known, and patch `apple_summary.md`'s About
    section there;
  - (b) add an "episode / recording date" question to the form;
  - (c) John picks the row by hand.

  (a) needs no change to the form. Angela leans (a).
- **RTS-3, which column holds the bio?** Name the question title or column header exactly. Bill reads the
  headers only.
- **RTS-4, `DRIVE_USER` can read the Sheet.** Oliver proves this with a header-only read from the box.

## Acceptance criteria (draft, final after RTS)

- **AC-1** (tests go red first, Jen): fixture CSVs give one match → `guest_bio` seeded; no match → no
  `guest_bio` and a named reason in the alert; two matches → the same; export error → the same, and Round 1
  continues.
- **AC-2:** the bio text never appears in logs, alerts or job error fields (a sentinel test, as in BLOG-02's
  C-4).
- **AC-3:** with a seeded bio, `apple_summary.md`'s About section is built from the guest's own words (Robert's
  voice-fidelity standard). A golden test covers it on one real past guest, with that guest's consent or a
  redacted fixture.
- **AC-4:** the blog gate (`content_gates.assert_blog_post_wellformed`) still refuses a post whose About section
  is a `BIO PENDING` placeholder. Jen confirms whether it does today; if not, that is a separate fix filed under
  BLOG-02.

## Not in scope

Changing the form itself (unless RTS-2 picks (b)), or writing anything back to the form or the Sheet.

## Built (2026-10-01): the RTS answers, and how

- **RTS-1:** the Sheet, tab gid `815049215` (John).
- **RTS-3:** columns `' Your name '` and `'Bio/Introduction '`, plus `Email Address`, `Timestamp` and `Your primary
  website`. They are matched by normalized header text, never by position, and a missing name or bio column is
  an error. Headers were read with the pipeline's own credentials: 32 answers, and no answer text was read.
- **RTS-4:** the Sheets API is **not enabled** on the pipeline's GCP project (403 SERVICE_DISABLED). An authorized
  CSV export of the tab through the existing Drive delegation works (HTTP 200, `text/csv`). No new API or scope.
- **RTS-2: resolved without changing the form.** Round 1 doesn't know the guest's name (it was `TBD`), so the
  match runs the other way round: the guest is the form respondent whose **full name the host says in the first
  5 minutes**. Rules:
  - names are normalized and honorifics dropped;
  - re-submissions collapse to the newest;
  - exactly one person must match, otherwise it's a logged miss;
  - one-word names are never matched.
- **On a match** Round 1:
  - adds `guest_name` and `guest_bio` to `episode_metadata`, which the prompt already uses as the bio seed;
  - writes `guest_name` and `guest_email` onto the schedule entry, so the thank-you draft is addressed to the
    real guest.
- **On a miss or an outage:** the reason is written to the schedule notes and the documented no-bio path runs.
- **Privacy:** answer text is never logged (sentinel test).
- `guest_form.py` is in deploy FILES.
- **Tests:** `test_form_*` in `test_r1_reliability.py` (7 cases).
