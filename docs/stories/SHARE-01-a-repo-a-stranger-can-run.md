# SHARE-01 — A repo a stranger can clone, install and test in one command

**Owner:** Toby (layout, standards) · **Impl:** Bill · **Box:** Oliver · **Tests:** Jen · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — Angela, 2026-10-10, from Toby and Bill's audit; Q1–Q2 open
**Created:** 2026-10-10 · **Raised by:** John ("clean up code for sharing") · **Feeds:** SHARE-02

---

## Why

Toby and Bill ran the suite cold on 2026-10-10 (a `git archive` copy, no key, no `.env`):

- **14 of 20 test files green**, about 210 assertions.
- card01, card04, card05 and reel09 fail on `No module named 'PIL'`.
- hf01 fails 2 of 3 on a guard that requires the repo to sit at `~/RockstarPodcast`. That's caused by where it ran, not by the code.
- `test_compose.py` makes live API calls and sits among the offline tests.
- **No interpreter on this Mac has every dependency**, so nobody can get a fully green run.

Beyond the tests, a reviewer opening the repo cold finds:

- no `requirements.txt` or `pyproject`
- 25 modules and 20 test files flat at the root
- a stale `INSTRUCTIONS.md` (May, v2.0) that calls live jobs "stubs"
- the box IP and `root` hard-coded in five scripts, and `$HOME/RockstarPodcast` in `deploy.sh:26`
- a retired job still dispatched (`worker.py:70` → `jobs/round_2.py`)
- a dead prompt, and no lint or type config

The code is better than its first impression. This story fixes the first impression.

**PP-8 applies throughout:** this is a cleanup, not a rewrite. Nothing finished is discarded.

- `run_dr_joe.py` is moved and parameterized, not deleted.
- The gold show notes and articles wait on PROMPT-01 Q1.
- The `quote_cards/_proof` PNGs are the format record and stay in this private repo.

## Scope

1. `requirements.txt` (pinned) and `requirements-dev.txt`: anthropic, fastapi, uvicorn, google-api-python-client,
   google-auth, Pillow, playwright, pytest, ruff. One venv runs everything.
2. `pyproject.toml`: pytest collects the existing `test_*.py` files unchanged; `test_compose.py` is marked `live` and
   skipped by default; ruff configured, **applied only to files this story touches** (no repo-wide reformat).
3. hf01's guard reads the repo root from `git rev-parse --show-toplevel`, not `~/RockstarPodcast`.
4. The box address comes from one config line (`scripts/box.conf`, gitignored, with a `.example`). `deploy.sh`
   resolves `LOCAL_ROOT` from the script's own path.
5. Retire `jobs/round_2.py` and its dispatch entry; delete `prompts/blog_page.md` if PROMPT-01 hasn't already.
6. `INSTRUCTIONS.md` is replaced by a pointer to README (git keeps the history).
7. **Layout (Q1).**
   - `tests/` for the tests
   - `tools/` for the manual CLIs: `run_dr_joe.py`, `rerun_brand_team.py`, `create_thank_you_draft.py`, `reprocess_episode.py`, `publish_blog_post.py`
   - `docs/reference/` for the 66 KB roster and `archive/`
   - `deploy.sh` FILES and the systemd paths are updated in the same commit, and DEPLOY-01's guard (PROMPT-01) proves nothing was dropped.
8. `README.md` gains "Run it": clone, venv, install, `pytest`, and what `-m live` needs.

## Acceptance criteria

1. From a fresh clone on a clean machine: `python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt &&
   .venv/bin/pytest` runs every offline test file and **all pass**. Command, count, date and sha pasted here (DOD-1).
2. `pytest -m live` is the only way `test_compose.py` runs, and it skips with a clear message when there's no key.
3. `grep -rn '91\.98\.' -- ':!docs'` and `grep -rn 'RockstarPodcast' scripts/` return nothing.
4. `deploy.sh --check` passes after the moves, and the box runs one episode end to end afterwards (John's go).
5. `ruff check` passes on every file this story changed.

## Decisions (open — John, Toby)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Move files (tests/, tools/), or leave the layout and add only install + runner? | **Move.** It is the first thing a reviewer sees. Cost: one deploy with the FILES guard on. Do it **after** PROMPT-01's guard ships, so the guard catches any miss. |
| Q2 | Type hints: add to the modules the eval touches, or leave? | **Only where touched** (`anthropic_client.py`, `evals/`). A full pass is churn with no reader. |

---

## Built — Toby with Bill, 2026-10-10 (branch `feat/share-01`; John: "Move files")

**What changed:**
- **`tests/`:** all 25 suites moved there, each finding the repo root explicitly.
- **`tests/conftest.py`:** `pytest` runs **each test file in its own process**. The suites swap pipeline modules for
  fakes, and in one shared process those fakes leak between files. The cost: one result per file, with the file's
  own output naming the failing assertion.
- **`pyproject.toml`:** pytest config (`live` marker; `test_compose.py` is the only live file) and ruff.
- **`requirements.txt` / `requirements-dev.txt`:** pinned to what this repo runs, with Playwright at 1.60.0 as the
  box uses.
- **`tools/`:** the six hand-run CLIs moved there, each with a one-line repo-root import path. Deploy FILES and every
  operator message point to `tools/…`. The runbook is updated.
- **`hf01`** runs from any checkout: the test gives `transcribe_fast.sh` a throwaway `HOME`. The script's
  "only under ~/RockstarPodcast" safety rule is **kept as it is**, deliberately.
- **The box's address** lives in `scripts/box.conf` (gitignored), with a `.example`, read through `scripts/box.sh` by
  `deploy.sh` and all four card scripts. The IP is gone from every script.
- **`deploy.sh` deploys the checkout it lives in, and only from `main`.** That replaces PR #29's "only from
  ~/RockstarPodcast" check: a worktree on a feature branch is still refused before any ssh, and that is tested.
- **Removed:**
  - `jobs/round_2.py` and its dispatch entry, so a ROUND_2 job is now an unknown job type;
  - `prompts/blog_page.md`, which stays in `RETIRED_PROMPTS` so it can't drift back into FILES;
  - `test_r1_reliability`'s stale exemption list.
- **Moved to `docs/reference/`:** the 66 KB roster, `archive/`, and the May `INSTRUCTIONS.md`, now a short pointer to
  the README.
- **README:** "Run the tests" (clone, venv, install, `pytest`), a map of the folders, `box.conf`, and the ledger
  summary.

**Evidence, 2026-10-10:**
- **AC-1:** a fresh venv (Python 3.12.3), then `pip install -r requirements-dev.txt && pytest`, gave **25 passed,
  1 deselected (live)** in about 14 s. That includes the four card and reel suites that never ran locally for want of
  Pillow. A planted failing file is reported with its file and line.
- **AC-2:** `pytest -m live --co` selects only `test_compose`.
- **AC-3:** no IP in any `.py`, `.sh` or `.js` outside `docs/`.
  - **Exceptions:** see the amended AC-3 below (the transcribe safety rule, three deployed defaults, comments).
  - The archived May reference in `docs/` still holds the IP; SHARE-02's allowlist keeps `docs/` out of the export.
- **AC-5:** `ruff check` passes on every file written this session.
  - The existing code carries **177 findings repo-wide** (`ruff check . --statistics`), **133** of them in the files
    this PR changed or moved. They are not reformatted, by rule: no repo-wide format.
  - A lint pass on them is its own story if wanted.
- **AC-4:** `deploy.sh --check` and one live episode wait for John's deploy.
- The manifest is OK.

**For John, before the next deploy:**
1. Create `scripts/box.conf` from the example in `~/RockstarPodcast`; without it, the deploy refuses and says why.
2. The deploy ships `tools/*.py`. The **old copies at the box's root** (`rerun_brand_team.py`, `reprocess_episode.py`,
   `create_thank_you_draft.py`) and `jobs/round_2.py` stay there, unused, until removed by hand.
3. **Use the new path:** `venv/bin/python tools/reprocess_episode.py …`.

**Retired (John, 2026-10-11): `reset_test_state.py`,** a May smoke-test reset. It failed every pending job, reset
the counter to **E26** and stripped schedule entries, with no arguments and no confirmation. It was never deployed;
git keeps it. Round 1's standup error no longer tells the operator to run it.

**Cold review of PR #38: Oliver and Orly blocked on 1 and 4; Toby approved with changes. All addressed:**

| # | Finding | Fix |
|---|---|---|
| 1 | **A `SERVER` set in the shell silently redirected the deploy** (reproduced: 76 ssh calls to a made-up host, with no box.conf) | `box.sh` always sets `SERVER` itself: from `box.conf`, or the explicit `BOX_SERVER_OVERRIDE`, which only the card scripts' rehearsal sets. Tested with an ambient `SERVER`. |
| 2 | **AC-1 on a clean machine:** without Playwright's browser, 3 card suites fail | README and AC-1: `playwright install chromium`. `conftest.py` now **fails** a file that prints "NOT a pass" / "!! playwright absent", so a skipped render test can't read as green. |
| 3 | `tools/publish_blog_post.py` resolved `state/` under `tools/` | It resolves to the repo root, so run 2 finds run 1's ownership record again (C-7). Usage line fixed. |
| 4 | **Any clone on main could deploy**, stale or dirty, with "not pushed" checked only afterwards | Before any ssh, `deploy.sh` requires `main`, a clean tree, and `HEAD == origin/main` after a fetch. |
| 5 | AC-3 said no `RockstarPodcast` under `scripts/` | **AC amended to name the exceptions** (see below). The defaults in `render_reel.js`, `extract_quote_clips.sh` and `render_closing_sequence.sh` ship to the box, and changing them would change box behaviour, so they're out of this story. `analyze_theme_music.sh` is Mac-only, so it now derives its default from its own checkout. |
| 6 | AC-2 said "skips"; with no key the live file fails | **AC amended:** with no key it fails loudly, naming `pytest -m live`. It asserts nothing about the output, which is fine for a smoke check. |
| 7 | AC-5 said "every file this story changed" | **AC amended:** files written this session are ruff-clean. Moved files keep their lint debt: 133 findings, reported. |
| 8 | Stale references | The README's test line is fixed. The BLOG-02 runbook's smoke check no longer imports `jobs.round_2` and expects 2 failure types. The prompts that mention `blog_page.html` are Heather's to edit. |
| 9 | Old copies at the box's root still run (Oliver) | On the deploy run-sheet below. |

**AC-3, amended.** No IP in any `.py`, `.sh` or `.js` outside `docs/`. `RockstarPodcast` remains **only** in:
- `transcribe_*.sh`, as the safety rule;
- the default paths in `render_reel.js`, `extract_quote_clips.sh` and `render_closing_sequence.sh`, a follow-up
  because they're deployed;
- comments.

**Deploy run-sheet for SHARE-01 (John runs it):**
```bash
cd ~/RockstarPodcast && git switch main && git pull --ff-only
cp scripts/box.conf.example scripts/box.conf   # then set SERVER="root@<the box>"
bash scripts/deploy.sh --check && bash scripts/deploy.sh
# remove the old root copies the move left on the box (unused, but they'd run stale code if typed by habit):
ssh root@<the box> 'cd /opt/rockstar-pipeline && rm -fv reprocess_episode.py rerun_brand_team.py create_thank_you_draft.py jobs/round_2.py'
```

**Re-review of `1d55f44`: Oliver and Orly blocked on 1, now fixed:**
- **`deploy.sh` unsets `BOX_SERVER_OVERRIDE` before reading `box.conf`,** so a deploy honours no override at all.
  - **The test:** a real clone of a bare origin (on main, clean, pushed) with `SERVER` and `BOX_SERVER_OVERRIDE` both
    exported.
  - **It must reach ssh:** the test fails if `deploy.sh` never gets that far.
  - **It proves the target:** every call goes to box.conf's host. With the `unset` removed, the test fails, naming
    the wrong host.
- **Every FILES entry must be tracked by git, before any ssh.** An untracked file's local bytes used to pass the
  clean check.
- **The card scripts' override is renamed `CARD04_REHEARSAL_SERVER`:** a name nobody exports by habit.
- **The end-of-script "not pushed" block is gone, because it could no longer fire:** git is proved before ssh, and
  the header says so (DOD-4).
- **`analyze_theme_music.sh`, which is Mac-only, derives its default from its own checkout.** The AC text is
  reconciled: the exception list, and both ruff counts with what each counts.
- **Run-sheet:** `rm -fv`, and the no-op is gone.

**Evidence:** `tests/test_prompt01_manifest.py` → **25 passed**. The override mutant fails.

**Oliver and Orly, re-review of `94beaa6`: the block is lifted** (every way of choosing another target tried; all
calls went to box.conf's host). Approve with changes. Done:
- **The untracked-FILES gate is tested.** A deploy-list file is untracked and committed, and the deploy is refused
  before any ssh. Weakening `--error-unmatch` fails the test.
- **The box.conf-without-SERVER case is tested.** With `SERVER` exported and no SERVER line in box.conf, the run
  stops with "SERVER is empty" and no ssh. Removing the reset fails the test.
- **`builtin source`** in `box.sh` and `deploy.sh`, so an exported function named `source` can't intercept the
  config read.

`tests/test_prompt01_manifest.py` → **25 passed**, and both new mutants fail.
