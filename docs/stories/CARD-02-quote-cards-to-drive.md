# CARD-02 — Deliver rendered quote cards to the episode's Drive folder

**Owner:** Bill (impl) · **Tests:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **DONE — verified live 2026-07-27** (E29, E30, E31)
**Created:** 2026-07-25 · **Raised by:** Angela (during CARD-01)

---

## Why

CARD-01 renders cards to `Episodes/E{N}-*/cards/` on the Mac. Heather posts from Drive,
so without this the last step is a manual drag.

## Decision (John, 2026-07-25)

**Clear the folder before every upload.** `drive_client.upload_file` creates rather than
replaces, so a re-run would otherwise leave two copies of every slide side by side — and
the second run's corrections would sit next to the first run's defects with nothing to
tell them apart.

Clearing is a **trash**, not a delete: `drive_client.trash_file` sets `trashed=true`, so
a wrong run is recoverable from the Drive UI for 30 days.

## Implemented

- `drive_client.trash_file(file_id)` — new.
- **`quote_cards/card_upload.py`** — its own module. Rendering needs Playwright and the
  macOS system fonts; uploading needs Google credentials. Those live on *different
  hosts* — cards are made on the Mac and pushed from the box, which has no Playwright —
  so a module requiring both could only run where neither job happens. `render_cards.py`
  re-exports `upload_cards`, so `--upload` still works wherever credentials exist.
- Finds the episode folder by `E{N}-` prefix, finds or creates `cards/`, trashes what is
  already there (naming each file), then uploads the whole local folder — captions
  included, `_`-prefixed scratch excluded.
- Upload is **opt-in**. Without it nothing leaves the Mac.
- Local files are never touched — a failed upload leaves everything on disk.

## Tests — `test_card02_upload.py`, 7 passing

`drive_client` replaced with a recording fake — the one place a stub is right here,
since the real path trashes files in a live folder and the ordering guarantee is what a
live test cannot safely explore. Asserted: **every trash precedes every upload**; the
folder is created when absent and nothing is trashed then; the whole local folder
uploads while `_`-prefixed scratch and dotfiles stay local; png/txt mime types; lookup
by the hyphenated `E29-` prefix (bare `E3` would match E30–E39); a missing Drive folder
fails loudly; and an upload that raises leaves every local file in place.

**Known consequence, surfaced by the failure test:** a mid-upload failure leaves the
Drive folder *cleared but not repopulated*, because the clear necessarily happens first.
Local files are intact and re-running restores the folder, so this is recoverable — but
between the failure and the re-run, Drive shows an empty `cards/`.

## Live run — 2026-07-27 (all acceptance criteria met)

Run **from the box**, where `GCP_SA_KEY_PATH` lives; card sets staged up from the Mac.

| Episode | Result |
|---|---|
| E29 | `cards/` created, 15 files (10 slides + 5 captions) |
| E30 | `cards/` created, 18 files (13 + 5) |
| E31 | `cards/` created, 21 files (16 + 5) |

**No duplicates on re-run.** E29 run again: 15 stale trashed, 15 uploaded, folder ends at
**15 files, 0 duplicates**. This is the path that matters, and the first three runs did
*not* exercise it — no `cards/` folder existed, so they took the create branch and never
called `trash_file`.

**A failure leaves everything intact — demonstrated in production, not in a fixture.**
The first re-run attempt raised `AttributeError: module 'drive_client' has no attribute
'trash_file'` and failed **at the trash step, before mutating anything**: Drive still
held exactly 15 files with no duplicates, and every local file was untouched.

## Deploy — the drift this turned from harmless into blocking

`drive_client.py` is a **deployed** file and `trash_file` existed only in `main`. So a
**first** upload to an episode succeeded (create branch, never calls it) while every
**re-upload failed** — and re-uploading a corrected set after a typo fix is the ordinary
case, not an edge case. That is what changed the deploy calculus recorded on 2026-07-26.

Deployed 2026-07-27 with John's go-ahead, queue verified idle first (0 pending, 0
running; last job finished 2026-07-26 03:02, both services active). Four files moved:
`drive_client.py`, `anthropic_client.py`, `scripts/deploy.sh`, and
`prompts/quote_card_captions.md` — which was absent from the box entirely.
`deploy.sh --check` beforehand confirmed those four and nothing else had drifted, so
nothing unexpected rode along.

Result: **35/35 files OK, `local==git==deploy OK`**, both services restarted and active.

Re-verified end to end **on the deployed code with no `PYTHONPATH` shadowing**: E29
re-uploaded, 15 trashed, 15 uploaded, 15 files, 0 duplicates. The earlier verification
had only proved the repo's code, not the box's.

## Note

Captions ship carrying `[ADD @HANDLE]`. John fills the handle at publish time — his
explicit call, so it does not block production.
