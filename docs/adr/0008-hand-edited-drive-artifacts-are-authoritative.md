# ADR 0008 — Hand-edited Drive artifacts are authoritative; download exports Google Docs

- **Status:** Accepted
- **Date:** 2026-06-26
- **Deciders:** John (owner — the workflow requirement); Bill (backend — impl);
  Toby (review/deploy); Jen (tests — DL-01 follow-up)
- **Story:** DL-01 — Doc-tolerant download
- **Triggered by:** E29. The publisher sent **no** "Episode is Live" email, so Phase 2
  was kicked off manually. It failed because `apple_summary.md` had been hand-edited in
  Drive — which silently converts it to a Google Doc — and `download_file` does a binary
  `get_media`, which 403s on Editors files.

## Context

The workflow has a deliberate hand-edit checkpoint: Phase 1 (`round_1`) produces draft
artifacts in the episode's Drive folder, **John reviews and edits them there** before the
episode goes to the publisher, and Phase 2 (`publish_finalize`) consumes them to make the
blog, FAQ, captions, reels, and thank-you. John's requirement, in his words: *"I edit
apple_summary before uploading to publisher, then expect that hand edit to be source of
truth for Phase 2 artifacts."*

The obstacle was mechanical, not conceptual: editing a `.md`/`.txt`/`.json` in Drive's UI
converts it to a Google-Apps-Editors file (Doc/Sheet), which has no binary content. Every
`download_file` of such a file 403s with *"Only files with binary content can be downloaded.
Use Export with Docs Editors files."* So the hand-edited version wasn't just non-authoritative
— it broke the pipeline outright.

## Decision

**The hand-edited Drive version of an artifact is the source of truth, and the single shared
download path makes that work.** `drive_client.download_file` now checks the file's mime
first: if it is a Google-Apps-Editors type it exports to text (`document → text/plain`,
`spreadsheet → text/csv`, `presentation → text/plain`) instead of streaming binary; otherwise
it streams as before. A UTF-8 BOM that Google prepends on export is stripped so downstream
`json.loads` / first-line parsing doesn't choke.

Fixing it in `download_file` — not at each call site — means **every** artifact Phase 2 reads
(apple_summary, transcript, quotes.json) is hand-edit-tolerant in one place (DRY). Audio and
images are never Editors types, so they take the unchanged binary path; the only cost is one
cheap metadata round-trip per download.

## Consequences

- Hand-editing `apple_summary.md` in Drive now flows straight into the blog/FAQ/thank-you
  with no manual re-upload. The E29 manual fix (export Doc → re-upload as binary) is no
  longer needed going forward.
- **Known boundary — the reel *title* is not sourced from `apple_summary`.** Reels render
  `episode.title` from `publication_schedule.json` (per ADR-0006/TITLE-01). So editing the
  title *inside apple_summary* will change the blog/FAQ/thank-you but **not** the reel title
  card — that still comes from the schedule. If we want one hand-edit surface to drive the
  reel title too, that's a separate precedence decision (Angela + Toby), not this ADR.
- **JSON-as-Doc caveat:** if `quotes.json` is opened/edited as a Google Doc, the export is
  best-effort text — Docs may smart-quote or reflow it and break `json.loads`. The right way
  to hand-edit JSON is as a plain-text file; this ADR keeps the pipeline from 403-ing but
  does not guarantee a mangled Doc re-parses.
- Tests owed: **DL-01** (Jen) — export-on-Editors + BOM strip + binary-path-unchanged.

## Addendum — 2026-09-22: a `.md` Doc exports as `text/markdown` (BLOG-02 F-2)

- **Deciders:** John (ruling); Angela (PO — proved the export); Bill (impl); Toby (review)
- **Story:** BLOG-02 (02a), finding F-2

**What broke.** BLOG-02 builds the blog post in code from `apple_summary.md`. Each
Conversations bullet is "**Lead** body", and the bold is the only boundary between the
lead (the bullet's heading) and its body. John edits the summary in Drive, so it is a Doc,
and the `text/plain` export above threw the bold away: E30 came back as "The Microbiome
Footprint What your…" with one space between them. Code cannot tell where that lead ends.
The build refuses it rather than guess.

**Decision.** In `download_file`, a Google Doc whose **name ends in `.md`** exports as
`text/markdown`, which keeps the bold (`**Why Men and Women Don't Share the Same ECS**`).
The choice lives in the pure `drive_client.export_mime_for(mime, name)`.

**The one deliberate narrowing of the ruling.** The ruling said "Google Docs export as
text/markdown". This applies it to `.md` names only. The markdown export backslash-escapes
characters (`\-`, `\*`, `\.`, `\[` …). A `quotes.json` or `transcript_fast.txt` that Drive
converted to a Doc was never markdown, so escaping would corrupt it: JSON would stop
parsing, and the escapes would end up in the transcript the Brand Team reads. Those keep
`text/plain`, and Sheets keep `text/csv`. If John wants markdown for another artifact,
extend `_MARKDOWN_NAMES`.

**What the markdown export changes downstream, and where it is handled.**
- Escapes: `blog_post.unescape_markdown` undoes them. An escaped `\*bliss\*` becomes
  `*bliss*`, which the post renders as an em mark, the way the live post already shows it.
- The title line arrives as `## Hook | Topic`, and can carry bold or escapes.
  `publish_finalize._title_from_apple_summary` (ADR-0009) runs the line through
  `blog_post.markdown_to_text`: escapes are undone and emphasis markers dropped, with **no**
  typographic change, so reels, the thank-you and the slug see the same characters as before.
- "About ⟨guest⟩" arrives bold. The blog parser matches it on its plain text.
- "Conversations include" still has no colon. The parser never required one.
- Every other consumer of `apple_summary_text` passes it to the model (FAQ, captions, and the
  old blog compose until 02b). Markdown is at least as readable to the model as plain text.
- A real hand-edit slip carries through faithfully: E31's Doc has
  `**The Bliss Molecule You're Running Low** On`. The bold closes one word early, so "On"
  lands in the bullet body. It is **not** auto-repaired. The export tells the truth about the
  Doc, and the fix is in the Doc (BLOG-02 golden test, accepted delta).

**Tests:** `test_blog02_build_post.py` covers `test_export_mime_for`,
`test_title_from_markdown_summary`, `test_unescape_markdown` and
`test_plain_text_export_refused`. The E30/E31 fixtures are the real markdown exports.
