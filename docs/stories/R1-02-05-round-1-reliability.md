# R1-02…R1-05 — Round 1 output you can trust (found on E32, 2026-10-01)

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** High · **Size:** M
**Status:** Built 2026-10-01 (branch `fix/r1-reliability`) · **Raised by:** Angela, on E32's Round 1 (job 126)

John, 2026-10-01: *"just fix this code from filed stories so we can call it reliable … grind until we've proven
this out … today!"*

## What E32's Round 1 actually produced

| # | Defect | Effect |
|---|---|---|
| **R1-02** | `apple_summary.md` opened with the model's notes ("Looking at the transcript, I can see…", "No Brand Team quotes were supplied…", `---`) above the title, and ran "Conversations include", the bullets and "About" together | The blog parser drops paragraph 1 as the title, so the notes would have become the post's opening |
| **R1-03** | `compose_apple_summary` read `quote_text_clean`/`topic`; the Brand Team writes `text`/`heading` (E27+) | 5 empty quote lines were sent, so the summary was written blind |
| **R1-03** | Dr. Joe's clinical review opened ```` ```json ```` and never closed it (or was cut at `max_tokens=4096`) | Not parsed, then silently skipped. Lisa's note says "No Dr. Joe flags" about a review that never ran |
| **R1-04** | The host was "Heather Riley Helmsdrum" (transcription: "Heather Riley, Heems Drum"); "a 2025 time capsule" | Wrong name on a public page; wrong year |
| **R1-05** | `upload_file` always creates; a re-run left duplicate `apple_summary.md` / `quotes.json` in the episode root | Phase 2 read whichever the listing put first |

## Fixes

- **`summary_norm.py`, new** (R1-02, R1-04):
  - drops everything above the "Gen X Hits Menopause" title line, and bare rules;
  - gives each part its own paragraph;
  - canonicalizes the host's name;
  - **proves the result with `blog_post.parse_apple_summary`**, the same parser Phase 2 uses.
  
  It runs in Round 1 right after the summary is composed, so a summary Phase 2 would reject fails Phase 1 before
  Drive, calendar, counter or schedule are touched (GateFailure → PermanentFailure, no retry spend). It never
  rewrites a sentence.
- **`anthropic_client.py`** (R1-03):
  - the summary reads `text`/`heading`, and an empty quote is refused before any model spend;
  - `_strip_json_fences` handles an opening fence with no closing one;
  - the Brand Team budget is 8192 tokens; a non-JSON step, or JSON with no `quotes` array, is retried once;
  - a step still bad after the retry is named in `report["skipped"]`. Round 1 writes that report into
    `quotes.json` (`brand_team`) and logs it at ERROR.
- **`jobs/round_1.py`** (R1-04, R1-05):
  - the transcript is host-canonicalized before every model call (the raw file in Drive is unchanged);
  - metadata carries `host` and `processed_date`;
  - quote speakers are canonicalized;
  - `_archive_existing_artifacts` moves same-named root artifacts (and their Google-Doc conversions) into
    `_superseded-<UTC>` before a re-run uploads. That's an archive Phase 2 already skips; nothing is deleted.
- **`scripts/deploy.sh`:** FILES gains `summary_norm.py`, `guest_form.py` and `calendar_client.py`.
  `test_deploy_files_cover_round_1_and_phase_2_imports` fails if any local import of Round 1 or Phase 2 is
  missing from FILES.

## Tests

- **`test_r1_reliability.py`:** 23 cases, including E32's real Round-1 output as a fixture.
  - **Red on `main`:** 9 of 21 fail with the new modules supplied (the unclosed fence, empty quote lines, a silent
    skip, no calendar switch, FILES missing modules).
  - **Green on the fix:** 23/23.
- **`test_title01_gen_gate.py`:** its fake summary is now realistic and `guest_form` is stubbed (no network); 9/9.
- **Every other suite** is unchanged and green.

## Proof on the box (AC)

- **AC-1:** deploy: `local==git==deploy OK`; the new modules import under the worker's environment.
- **AC-2:** a real Round 1 on real audio produces:
  - an `apple_summary.md` whose first line is the title, with no notes;
  - the canonical host name;
  - a `quotes.json` with `brand_team.skipped == []`;
  - quote texts in the summary request;
  - no calendar event;
  - for a guest who filled out the form, `guest_name` and `guest_email` on the schedule, and the bio seeded.
