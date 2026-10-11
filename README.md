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
| How is a production LLM system measured? | `run_ledger.py` (every model call's tokens and cost, every step's time and outcome) · `scripts/ledger_summary.py` · `docs/stories/OBS-01-…` |
| How would you know a prompt or model change made it better? | `docs/eval/test-strategy.md` · `evals/` (the metric, the paired comparison rule, the baseline) · `docs/stories/EVAL-00-…`, `EVAL-01-…`, `EVAL-02-…` |
| Where does the model stop and the code start? | `content_gates.py`, `thank_you.py`, `blog_post.py` · ADR-0007, ADR-0010, ADR-0013 in `docs/adr/` |
| How do you test without real guests? | `fixtures_gen/` (seeded synthetic episodes with known defects) · `docs/stories/FIX-01-…` |
| Do the tests catch anything? | A gate's test plants the defect the gate guards against. The cold reviews ran mutants (remove the guard, check its test fails); each story's review table records them. |
| What happens when it goes wrong? | `docs/stories/TRIG-03-…` (a late email re-ran a whole phase) · `docs/stories/SEC-01-…` (a webhook that failed open) · `docs/stories/CACHE-01-…` (a prompt cache that could never hit) |
| How does it reach production? | `scripts/deploy.sh` (only from a clean `main` that matches the remote; a file manifest proved complete; byte parity checked on the box) · `scripts/check_manifest.py` |

`docs/plans/2026-10-10-measurement-eval-showcase.md` is the plan this repo was cleaned up under: what the reviews
found, in what order it was fixed, and what is still open.

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
Google Drive / Gmail ──push──► webhook_service.py ──enqueue──► SQLite job queue (db.py)
                                                                    │
                                                         worker.py (systemd)
                                         ┌──────────────────────────┴───────────────────────────┐
                                  jobs/round_1.py                                 jobs/publish_finalize.py
                     transcribe (WhisperX) · brand team (Claude)          blog (CMS API) · reels (ffmpeg + Node)
                       show notes · calendar · schedule               cards (Chromium + IBM Plex, jobs/card_step.py)
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
(the publisher's email domains, for instance, so `publisher_email.py` recognises `example.test` senders here).
`SWAPPED.md` lists every file that differs from the private source and by how many lines; the export script writes it.

## What isn't here, and why

This is an export of a private working repo, not the repo itself. `scripts/share_export.py` builds it from an
allowlist (`share/ALLOWLIST` in the private repo), with fresh history, and refuses to commit unless every gate
passes: only allowlisted paths; the tests pass; gitleaks and trufflehog find nothing; a private denylist of guest
names, mailboxes and infrastructure identifiers finds nothing, binaries included; every image was looked at by a
person, with its metadata stripped.

- **Guests' words.** The real fixtures were transcripts and quotes from real guests. They are replaced by synthetic
  episodes from `fixtures_gen/`, and the tests' assertions are unchanged.
- **Rockstar's audio, artwork and the show's logo.** The card background here is a plain stand-in.
- **The operations side:** the proxy config, run-books, security sign-offs and session notes. (The systemd units
  and their credential-isolation drop-ins are here: they hold designs, no values.) Host names and addresses that remain in the docs are placeholders (`pipeline.example.test`,
  `203.0.113.10`).
- **Agent names.** The agents appear under their public names.

## Licence

MIT (`LICENSE`), for the code and the docs. IBM Plex is under the SIL Open Font License
(`BrandAssets/fonts/ibm-plex/LICENSE.txt`). The show's name, *Gen X Hits Menopause*, and Rockstar Blends belong to
Rockstar Blends, and appear here with its permission; the licence doesn't extend to them.
