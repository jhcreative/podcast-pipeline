# podcast-pipeline

A production LLM pipeline, built to be measured. It turns each recorded episode of ***Gen X Hits Menopause***
(the Rockstar Blends podcast, hosted by Heather Reilly Hiemstra) into everything the episode needs to go out: show
notes, a blog post, 9:16 reels, quote-card carousels, captions and a thank-you draft for the guest. Claude writes
the prose. Code writes everything fixed, gates check the model's output, and a person approves and sends.

It has run in production since May 2026. Rockstar Blends agreed to its name and show appearing here.

Built by **John Hiemstra** (design, architecture and every production decision), working with a team of AI agents
whose decisions and reviews are on the record in `docs/`.

## What to look at

| If you're asking… | Read |
|---|---|
| How is a production LLM system measured? | `rockstar/ops/run_ledger.py` (every model call's tokens and cost, every step's time and outcome) · `rockstar/tools/ledger_summary.py` · `docs/stories/next-up/OBS-01-run-ledger-steps-and-model-calls.md` |
| How would you know a prompt or model change made it better? | `docs/eval/test-strategy.md` · `evals/` (the metric, the paired comparison rule, the baseline) · `docs/stories/next-up/EVAL-00-eval-test-strategy-baseline-and-metrics.md`, `EVAL-01-…`, `EVAL-02-…` |
| Where does the model stop and the code start? | `rockstar/gates/content_gates.py`, `rockstar/content/thank_you.py`, `rockstar/content/blog_post.py` · ADR-0007, ADR-0010, ADR-0013 in `docs/adr/` |
| How do you test without real guests? | `fixtures_gen/` (seeded synthetic episodes with known defects) · `docs/stories/next-up/FIX-01-seeded-synthetic-episode-generator.md` |
| Do the tests catch anything? | A gate's test plants the defect the gate guards against. The cold reviews ran mutants (remove the guard, check its test fails); each story's review table records them. |
| What happens when it goes wrong? | `docs/stories/next-up/TRIG-03-late-publisher-email-swaps-links-only.md` (a late email re-ran a whole phase) · `docs/stories/next-up/SEC-01-webhook-service-fails-closed.md` (a webhook that failed open) · `docs/stories/next-up/CACHE-01-brand-team-transcript-cache-never-hits.md` (a prompt cache that could never hit) |
| How do you change a pipeline's structure without changing what it does? | `rockstar/tools/sandbox_run.py` (record the model once, replay it through old code and new, compare every artifact and each step's inputs) · `scripts/check.sh` · `docs/backlog/REMODEL-01-follow-ups.md` (what was left undone, on purpose) |
| How does it reach production? | `scripts/deploy.sh` (only from a clean `main` that matches the remote; the code listed from git, `deploy.sh --list`; byte parity checked on the box) |

`docs/plans/2026-10-10-measurement-eval-showcase.md` is the plan this repo was cleaned up under: what the reviews
found, in what order it was fixed, and what is still open.

## What changed since the first export

This is the second export (2026-10-14). The first went out on 2026-10-11. Four changes since then, each with what
prompted it:

- **The model was editing guests' words inside quote marks.** Measurement showed it: the clinical-review step
  "corrected" quotes for accuracy, so the words in quote marks were no longer the guest's. A quote is now never edited.
  - The reviewer can only flag a quote (`prompts/brand_team_dr_joe.md`). A correction goes beside the quote, as small
    print, or the quote is swapped or dropped.
  - Code checks every quote against the transcript: `rockstar/gates/quote_tape.py` places each one on the tape, and
    `rockstar/gates/quote_gate.py` turns that into a verdict.
  - A quote that isn't the speaker's words gets one restore-or-swap pass, then it's dropped. Joins, mishears and
    doubtful speakers are listed for a person.
  - Tests: `tests/test_quote_tape.py`, `tests/test_quote01_gate.py`.
- **One quote clip ran the box out of memory, five times in a row.** Transcription memory grew with clip length at
  the batch size the box used.
  - The box's limit is now measured, not guessed (`scripts/measure_clip_limit.sh`, `rockstar/tools/clip_limit.py`),
    and the measured batch size is the one used.
  - Each quote has a length ceiling derived from the reel's own timing and the box's limit (`rockstar/gates/clip_limits.py`). It's
    enforced when quotes are chosen, and checked again before any money is spent on the episode (`CLIP-01` in
    `docs/stories/`).
  - An out-of-memory kill is read from the kernel's own count and stops the job with one alert, with no retry loop.
- **A caught failure was being logged as "ok".** The ledger now records it as an anomaly (`OBS-02`). A failed alignment
  stops the run instead of carrying on with known-bad timing (`REEL-11`).
- **The repo itself was reorganized, and proven to behave the same.** A review found 30 modules flat at the root, a
  linter nothing ran, and a deploy list kept by hand.
  - The code is now the `rockstar` package (`clients`, `gates`, `content`, `jobs`, `cards`, `service`, `ops`,
    `tools`); the Node reel renderers are in `reels/`. One local gate, `scripts/check.sh`, runs lint, format, every
    Python and Node test and an import of every module, and the deploy refuses to ship without it.
  - The deploy lists the code from git, so a new module can't be left off a hand-kept list.
  - Proof: `rockstar/tools/sandbox_run.py` runs real episodes through both pipeline phases with every outside
    service faked and the model's replies recorded once and replayed. Old code and new produced the same blog draft,
    show notes, quotes, captions, cards, thank-you and ledger, step by step.
  - It also found that the reel renderer isn't deterministic: the same inputs rendered twice differ in length by up
    to a third of a second. Reels are compared by shape, and that's on the list (`docs/backlog/REMODEL-01-…`).

The stories that name guests or quote their words stay private. The fixes, their tests and the reviews are here.

## Run the tests

```bash
git clone <this repo> && cd podcast-pipeline
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install chromium   # the card renderer's browser: the card tests measure real fit
.venv/bin/pytest
```

Each test file runs in its own process, against fakes for Gmail, Drive, the CMS and the model. Nothing reaches a
real service. `pytest -m live` runs the one file that calls the real Anthropic API (it needs `ANTHROPIC_API_KEY`).

One file is skipped here, and says so: `tests/test_fix01_no_real_names.py` proves the synthetic generator never
produces a real guest's name. The list of real names is private, so the check runs before every export instead.

## How it's built

```
Google Drive / Gmail ──push──► rockstar/service/webhook_service.py ──enqueue──► SQLite job queue (rockstar/service/db.py)
                                                                    │
                                                         rockstar/service/worker.py (systemd)
                                         ┌──────────────────────────┴───────────────────────────┐
                                  rockstar/jobs/round_1.py                                 rockstar/jobs/publish_finalize.py
                     transcribe (WhisperX) · brand team (Claude)          blog (CMS API) · reels (ffmpeg + Node)
                       show notes · calendar · schedule               cards (Chromium + IBM Plex, rockstar/jobs/card_step.py)
                                                                       thank-you draft (Gmail) · calendar · schedule
```

- **Event-driven.** Drive change notifications and Gmail push start the jobs; one 10-minute timer drains Drive's
  change feed in case a notification went missing. The publisher is never polled.
- **A hand edit always wins.** Every artifact lands in the episode's Drive folder, and a person's edit there is
  authoritative over anything the pipeline wrote (ADR-0008).
- **Guests get drafts, never sends.** The thank-you is filled from a fixed template and saved as a Gmail draft.
- **When something can't be done safely, it stops that one thing** and sends an alert that names the fix.

## How this differs from what runs in production

The logic, the steps and their order are the code running in production. Where a real name, mailbox, domain, ID
or address appeared, in code, comments, prompts or tests, it was swapped for an invented or `example.test` value
(the publisher's email domains, for instance, so `rockstar/clients/publisher_email.py` recognises `example.test` senders here).
`SWAPPED.md` lists every file that differs from the private source and by how many lines; the export script writes it.

## What isn't here, and why

This is an export of a private working repo, not the repo itself. `rockstar/tools/share_export.py` builds it from an
allowlist (`share/ALLOWLIST` in the private repo), with fresh history, and refuses to commit unless every gate
passes: only allowlisted paths; the tests pass; gitleaks and trufflehog find nothing; a private denylist of guest
names, mailboxes and infrastructure identifiers finds nothing, binaries included; every image was looked at by a
person, with its metadata stripped.

- **Guests' words.** The real fixtures were transcripts and quotes from real guests. They are replaced by synthetic
  episodes from `fixtures_gen/`, and the tests' assertions are unchanged.
- **Rockstar's audio, artwork and the show's logo.** The card background, the reel's host background and closing
  stills are plain stand-ins, and the theme music is a synthetic chord with its own beat grid. The reels and cards
  render in IBM Plex (SIL OFL, included).
- **The operations side:** the proxy config, run-books, security sign-offs and session notes. (The systemd units
  and their credential-isolation drop-ins are here: they hold designs, no values.) Host names and addresses that remain in the docs are placeholders (`pipeline.example.test`,
  `203.0.113.10`).
- **Agent names.** The agents appear under their public names.

## Licence

MIT (`LICENSE`), for the code and the docs. IBM Plex is under the SIL Open Font License
(`BrandAssets/fonts/ibm-plex/LICENSE.txt`). The show's name, *Gen X Hits Menopause*, and Rockstar Blends belong to
Rockstar Blends, and appear here with its permission; the licence doesn't extend to them.
