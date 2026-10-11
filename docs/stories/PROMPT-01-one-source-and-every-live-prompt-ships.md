# PROMPT-01 — Every live prompt ships, and every example has one source

**Owner:** Bill (impl) · **Box:** Oliver · **Review:** Toby · **Words:** Heather (owner of the prompts) with Lisa · **Tests:** Jen · **PO:** Angela
**Priority:** **High** for Part 1 (deploy gap), Medium for the rest · **Status:** **Proposed** — drafted 2026-10-10 by Claude at John's request, for Angela's RTS pass; not Ready to Start (Q1–Q3 open)
**Created:** 2026-10-10 · **Raised by:** John · **Follows:** DEPLOY-01 (same failure class) · **Feeds:** OBS-01 (`prompt_sha`), EVAL-01 (rubric anchors)

---

## Why

Three findings from a read-only audit, 2026-10-10.

**1. Four live prompts are not in `scripts/deploy.sh` FILES.** The deployed code loads them, but deploy
never ships them and `--check` never compares them.

| Prompt | Loaded by | Last changed in repo |
|---|---|---|
| `prompts/apple_summary.md` | `anthropic_client.py:404` (title and show notes) | 2026-06-01 `08c07d2` |
| `prompts/faq.md` | `anthropic_client.py:466` | 2026-06-01 `9b1df54` |
| `prompts/brand_team_robert.md` | `_BRAND_TEAM_MEMBERS` (`anthropic_client.py:55`) | 2026-05-26 `b52ecc1` |
| `prompts/brand_team_dr_joe.md` | `_BRAND_TEAM_MEMBERS` | 2026-05-26 `b52ecc1` |

The risk is **latent, not live**: none of the four has changed since early June, so the box probably holds
matching copies placed some other way. That is unverified. The next edit Heather makes to the show-notes
prompt would stay on the laptop while `deploy.sh --check` reports green. That is DEPLOY-01's exact failure
class ("parity against a manifest"). DEPLOY-01's guard didn't catch it because it resolves `prompts/…` path
literals, while `_load_prompt("apple_summary.md")` passes a bare filename and the brand-team names are
assembled from a list. Toby confirms whether the guard was built and missed these, or was never built.

**2. The examples are copied, not referenced.**
- **Gold titles exist in three places:** `voice_corpus/gold_titles/titles.txt`, `prompts/title.md:32` and
  `prompts/apple_summary.md:26`. They're identical today. Editing `titles.txt` changes nothing live.
- **The gold thank-you is stale.** The live template is `prompts/thank_you.md`, the file Heather tunes and
  `thank_you.py` reads. `voice_corpus/gold_thank_yous/episode-live-thanks.template.md` has a different
  subject line and a different body. It's an orphan that looks authoritative.
- **`prompts/blog_page.md` is dead.** Its only caller, `compose_blog_page`, was removed on 2026-09-24
  (BLOG-02b). `blog_post.py:5` still calls it "now superseded".
- **The gold show notes (5) and articles (3) are read by nothing.** No code loads them, and no prompt
  contains them.

**3. Measurement depends on it.** OBS-01 tags every call with `prompt_sha`. If examples are pasted copies,
changing an example doesn't change what the model sees, and the sha can't tell a real prompt change from a
non-change. One source means one sha that reflects reality.

## Scope

**Part 1 — every live prompt ships (High)**

1. Add the four prompts to FILES.
2. Make the guard see them: every name passed to `_load_prompt`, plus every `_BRAND_TEAM_MEMBERS` entry
   + `.md`, must be in FILES. Missing means a non-zero exit and a blocked deploy. Simplest form:
   **every `prompts/*.md` is either in FILES or listed in `RETIRED_PROMPTS`**.
3. **Before the first deploy overwrites them, compare the box copies.** If a box copy differs from the
   repo, the box copy is what has been live, and it may hold an edit made there. Show the diff to John and
   Heather and decide which wins before shipping. Never overwrite blind.

**Part 2 — one source for examples**

4. Gold titles live only in `titles.txt`. Prompts pull it in through an include line that `_load_prompt`
   expands. The syntax must not collide with the `{{slot}}` fills in `thank_you.md`; Bill picks it, for
   example `<<include voice_corpus/gold_titles/titles.txt>>`. `titles.txt` joins FILES, and OBS-01's
   `prompt_sha` is taken **after** expansion.
5. Retire the stale and the dead: replace `gold_thank_yous/episode-live-thanks.template.md` with a one-line
   README pointing to `prompts/thank_you.md` (git keeps the history); delete `prompts/blog_page.md` and fix
   the `blog_post.py:5` docstring.
6. Gold show notes and articles: Q1 decides.

**Part 3 — keep it that way**

7. A test fails if any `prompts/*.md` is neither loaded by code nor listed as retired (orphan guard), or if
   3 or more consecutive lines of a `voice_corpus/` file appear literally in a prompt (copy guard).

**Out of scope:** changing what any prompt says (that's Heather's), and `gold_outreach/` (not pipeline
material; Q3).

## Acceptance criteria

1. `deploy.sh --check` lists all four prompts, and a deploy with any loaded prompt missing from FILES exits
   non-zero.
2. The box-vs-repo comparison for the four is recorded here before the first overwrite, with the decision
   on any difference.
3. Editing `titles.txt` changes the prompt the model receives, and changes `prompt_sha`, in both the title
   and the summary call. *Test:* load both prompts, edit the fixture `titles.txt`, assert both changed.
4. The rendered `title.md` and `apple_summary.md` prompts are byte-identical to today's after the include
   swap. It's a refactor; the model sees nothing new.
5. The orphan and copy guards pass on the cleaned tree, and fail on a fixture that re-pastes the titles.
6. `prompts/blog_page.md` is gone, and nothing references it.

## Decisions (open — John, Heather)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Gold show notes (5) and articles (3), all from May: are they still the bar? And their job? | **Answered (John, 2026-10-10): the May gold show notes and articles are retired; the published finals of E22–E35 are the accepted great examples.** See the John's answers section below. Original recommendation: If Heather says yes, make them **EVAL-01 rubric anchors**: examples of "good" for the rubric and the judge, never scoring answers. If not, retire them, and the rubric gets new anchors from recent episodes she likes. |
| Q2 | Should the show-notes prompt also include a gold show-notes example? | Not in this story. That would change model behaviour; measure it through EVAL-01 first. |
| Q3 | `gold_outreach/cold-pitch.template.md`: whose is it? | Leave it in place and ask Heather. It isn't pipeline material. |

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 5 | `test_prompt01_manifest.py`: the guard against the real `deploy.sh` and `anthropic_client.py`; the orphan and copy guards against fixtures |
| 3, 4 | Same file: before/after rendered-prompt bytes, and `prompt_sha` change on a fixture edit |
| 2 | The box comparison output, pasted here, dated |

## Shipping it (John runs each line; agents don't touch the box)

```bash
cd ~/RockstarPodcast
shasum -a 256 prompts/{apple_summary,faq,brand_team_robert,brand_team_dr_joe}.md
# on the box:
sha256sum /opt/rockstar-pipeline/prompts/{apple_summary,faq,brand_team_robert,brand_team_dr_joe}.md
# any mismatch: copy the box file down, diff it, and decide with Heather before deploying
bash scripts/deploy.sh --check
bash scripts/deploy.sh
```

---

## RTS pass — Angela, 2026-10-10 (with Toby, Bill, Jen)

**Claims verified by Toby and Bill:** all CONFIRMED: the four prompts missing from FILES (commits and dates as
stated), `_load_prompt` bare names (`anthropic_client.py:195`, `:404`, `:466`), gold titles identical in three places,
the stale thank-you (subject lines differ), `blog_post.py:5`. **DEPLOY-01's guard was never built** (its own status
line, and `deploy.sh`). It wasn't built and then missed.

**More of the same class (Toby):** `worker.py`, `google_auth.py`, `jobs/__init__.py`, `jobs/round_2.py` and
`watches/__init__.py` are also absent from FILES, and the systemd units are unmanaged. Part 1 scope 2 widens: **every
first-party `.py` imported by a deployed module is in FILES**, and the box comparison in scope 3 covers these too.

**Amendments adopted (Jen):**
- AC-7: a missing or unreadable include **raises**; it never expands to an empty string. *Test.*
- AC-8: AC-2's box comparison is complete **before any EVAL baseline is recorded**; otherwise the baseline measures
  prompts that were never live.

**Order (John, 2026-10-10):** Part 1 ships first, with TRIG-03 — both are live risks. Parts 2–3 follow before EVAL-01's
baseline.

**Gate status:** RTS-1 pass after the amendments · RTS-3 pass for Part 1 (no dependencies) · RTS-4 pass (Jen's
Verification table) · Q1–Q3 wait on John and Heather; Part 1 doesn't need them.

## John's answers, 2026-10-10

**Q1: the published finals of E22–E35 are the accepted great examples.** The May gold show notes (5) and articles (3)
are retired in Part 2: a README in each folder points to the new anchors, and git keeps the history (PP-8).
**Who uses the anchors, and how, is EVAL-01's job** (rubric anchors, see the leakage note there). This story only
retires the old ones and records where the new ones live: Drive's `apple_summary.md` for each episode, fetched by
`eval-fetch.sh`, never committed.

---

## Part 1 — built (Bill, 2026-10-10, branch `feat/prompt-01-part1`)

**What changed:**
- **`scripts/check_manifest.py`** (new, stdlib only) checks FILES against the code, under four rules:
  - every `prompts/*.md` is listed or retired
  - every loaded prompt exists and is listed
  - every systemd entry point is listed
  - every first-party import of a deployed `.py` is listed
- **It prints evidence for each rule.** Exit 1 means gaps, and each one is named. Exit 2 means *unverified*: an empty
  input is never reported as a pass.
- **What it doesn't cover, stated in its docstring:** files reached through subprocess or Node spawn, and asset path
  literals. Both stay with DEPLOY-01.
- **`deploy.sh`:**
  - runs the guard first, in both deploy and `--check`
  - FILES gains the four prompts plus `worker.py`, `google_auth.py`, `jobs/__init__.py` and `jobs/round_2.py`
    (eight files)
- **Scope 3 is a gate, not a note.** `NEWLY_MANAGED` lists the eight. Before any file is copied, a deploy compares each
  one with the box and **refuses to overwrite a box copy that differs**. It names the files and the way to resolve
  them. Absent or identical on the box: no stop. A file leaves the list in a commit that records the decision.
- **Correction to the RTS pass:** `watches/__init__.py` is not needed. `renew_watches.py` runs as a script, and
  nothing imports `watches` as a package.

**Evidence (DOD-1), 2026-10-10, base `main` 57b2cbe:**
- On unchanged `main`, `python3 -I scripts/check_manifest.py` → **exit 1, 18 gaps**: the four prompts (rules 1 and 2),
  `worker.py` (rule 3), and `google_auth.py`, `jobs/__init__.py` and `jobs/round_2.py` (rule 4).
- On this branch → **exit 0**: 74 FILES entries, 12 prompts, 10 loaded, 4 systemd entry points, 43 deployed `.py` scanned.
- `python3 -B test_prompt01_manifest.py` → **12 passed, 0 failed**:
  - the real tree
  - one fixture per gap kind, each failing with its gap named
  - third-party imports ignored
  - an empty manifest reported as UNVERIFIED
  - the guard and the box gate both run before any `scp`
- **Rest of the suite** (`.venv-pipeline`, Python 3.12.3): 15 of 19 offline files green, the same as Toby's cold
  baseline plus this file.
  - The other four are unchanged from `main`: card01, card04, card05 and reel09 fail on `No module named 'PIL'`
    (SHARE-01).
  - `test_hf01` passes on `main` and fails 2 of 3 in this worktree. The cause is `transcribe_fast.sh:56`'s safety
    rule, which refuses any folder outside `~/RockstarPodcast`. The fix belongs to SHARE-01: the test uses a
    `/tmp/rockstar-*` fixture.

**AC status:**
- **AC-1: met** (tests above).
- **AC-2: waits on John's box run.** `deploy.sh --check` prints OK, DIFF or MISSING for each of the eight, read-only.
  Paste it here; any DIFF is resolved before the deploy, which the gate enforces.
- **AC-5:** Part 1's half is met. The orphan and copy guards are Part 3.

**Shipping it (John runs each line; agents don't touch the box). After the merge:**
1. `bash scripts/deploy.sh --check` (read-only). Expect the guard OK, then DIFF or MISSING for the newly listed files.
2. For each DIFF: `scp root@<box>:/opt/rockstar-pipeline/<file> /tmp/`, then `diff` it against the repo. Decide with
   Heather for a prompt, and record the decision here.
3. `bash scripts/deploy.sh`

**Cold review (DOD-3), 2026-10-10: Toby and Orly, approve with changes. All nine findings fixed:**

| # | Finding | Fix |
|---|---|---|
| 1 | **High:** the box gate read a failed ssh as "absent" and passed (reproduced with a fake ssh) | `scripts/box_gate.sh`: one ssh call, one explicit `SUM`/`ABSENT`/`ERROR` line per file; ssh failure, a checksum error or a short answer is a FAIL |
| 2 | The gate would block every later legitimate edit | A box copy is "ours" when it matches **any committed version** of the file; only a copy edited outside this repo stops the deploy |
| 3 | The gate was untested; tests matched strings | Gate tests run the real script against a fake `ssh` (absent, same, an older committed version, edited on the box, ssh down, checksum error, short answer, missing local file); ordering tests ignore comment lines |
| 4 | Unreadable forms skipped without a word | Relative imports, unreadable dynamic imports, non-literal `_load_prompt` outside the brand-team loop, a non-literal `_BRAND_TEAM_MEMBERS` → UNVERIFIED; an `ExecStart*` naming python or uvicorn that parses to nothing → gap; `-m pkg.mod`, `pkg.mod:attr` and continuation lines read |
| 5 | Missing local file in the gate list → unclear crash | Checked first, named |
| 6 | A checker crash exited 1 ("gaps") | Any exception exits 2 (UNVERIFIED) |
| 7 | Guard read `$HOME/RockstarPodcast` regardless of which `deploy.sh` ran | `deploy.sh` refuses to run from any other copy of the repo |
| 8 | Box systemd units are unmanaged | Stated in the checker's "Not covered" |
| 9 | Test temp dirs left behind | Removed at the end of the run |

**Evidence:**
- `python3 -B test_prompt01_manifest.py` → **20 passed, 0 failed**.
- **The ssh test can fail.** With the old fail-open behaviour put back in a copy of the gate, `test_gate_stops_when_ssh_fails` fails (19 of 20). With the fix restored, 20 of 20.
- **The real tree** → exit 0, with the same counts as above.

**Cold re-review of `4d52f47`: approve with changes, nothing blocking. All three fixed:**
1. **Orly:** a failed `git show` (a commit that deleted the file) hashed as empty and matched an empty box copy. It is
   now skipped through `pipefail`, and hashed straight from the pipe.
2. **Toby:** inside the brand-team loop, only `f"{<member>}.md"` is accepted; any other argument is UNVERIFIED.
3. **Toby:** the repo-copy refusal is now tested. `deploy.sh`, run from a copy, refuses before any ssh or scp, with
   `CDPATH` set. It uses a `CDPATH=`-safe `cd`.

**Evidence:**
- `python3 -B test_prompt01_manifest.py` → **22 passed, 0 failed**.
- The new deleted-commit test fails against the old behaviour (21 of 22).

---

## AC-2 — box comparison before the first overwrite (John ran it, 2026-10-10, `main` 9779b2c)

`bash scripts/deploy.sh --check`, read-only:

- **Manifest guard:** OK, with the same counts as the build (74 FILES, 12 prompts and 1 retired, 10 loaded, 4 entry points, 43 `.py` scanned).
- **All eight newly managed files are `OK`**, byte-identical on the box: `prompts/apple_summary.md`, `prompts/faq.md`,
  `prompts/brand_team_robert.md`, `prompts/brand_team_dr_joe.md`, `worker.py`, `google_auth.py`, `jobs/__init__.py` and
  `jobs/round_2.py`.
- **Every other listed file is `OK`, except `scripts/deploy.sh`:** `DIFF` (local `eac5b099339e…`, box `1e97b8b4732a…`).
  That difference is expected, since this PR changed `deploy.sh`. The box copy is never run; deploys run on the Mac.
- The check ended `FAIL: checksum mismatch` because of that one `DIFF`, as designed.

**Decision:** none needed. No box copy differs from the repo, so nothing was edited on the box and the risk stayed
latent, as the story said. The first deploy ships only `scripts/deploy.sh`, with no `.py`, so no service restarts. The
box gate will report SAME for all eight.

**Part 1 AC status:** AC-1 is met; AC-2 is met (above); AC-5's Part 1 half is met. **Part 1 is done** once John runs
`bash scripts/deploy.sh` and the `--check` that follows reports every file `OK`.
