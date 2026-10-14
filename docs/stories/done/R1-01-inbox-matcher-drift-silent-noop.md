# R1-01 — ROUND_1 must fail loud when the inbox holds an audio file it won't process

**Owner:** Bill · **Tests:** Jen · **PO:** Angela · **Priority:** Medium (the *silent* failure is the High part)
**Status:** Built 2026-10-01 (branch `fix/r1-01-intake`) · **Created:** 2026-07-06 · **Raised by:** Angela (E30 stranding incident)

---

## Why

E30 sat unprocessed in the recordings inbox for ~2 hours (uploaded 17:38 UTC, noticed
21:35 UTC) with **no alert, no standup entry, and a green `done` job**. Root cause is a
disagreement between two gates that both decide "is this an episode file":

| Gate | Where | Definition | Verdict on `E30.m4a` |
|------|-------|------------|----------------------|
| **Enqueue** | `drive_changes.py:155` / `drive_client.list_audio_in_inbox:87` | audio *extension* only | ✅ accepts → enqueues ROUND_1 |
| **Process** | `round_1.py:63` `_EXX_PREFIX_RE = ^E\d+[-_]` | requires a `-`/`_` **after** the number | ❌ rejects → "nothing to do" |

The file was named `E30.m4a` — number butted straight against the extension, no separator.
The enqueue side happily created ROUND_1 job #88; the process side re-read the inbox, found
nothing matching its stricter pattern, logged *"inbox is empty or has no E30-prefixed file"*,
and `return`ed cleanly. Clean return → no exception → no `_prepend_standup`, no
`send_pipeline_alert`. **The pipeline reported success while doing nothing.**

This is the **third** instance of the silent-stranding class (E28 dead-push 2026-06-10,
E29 no-trigger 2026-06-25, E30 matcher-drift 2026-07-06). The durable-design contract for
this pipeline is explicit: input drift we don't control must **fail safe and alert, never
silently no-op** (PQ-5). A job that accepted a trigger and then found nothing to do is an
*anomaly*, not a success.

## Acceptance criteria

1. **The two gates share one definition of "episode audio file."** The predicate ROUND_1
   uses to select the target file is the same one (or a strict superset of) the predicate
   `drive_changes` uses to enqueue. No file can pass enqueue and silently fail process.
   *Testable:* a single `is_episode_audio(name, mime)` helper is the sole authority, imported
   by both call sites; a test asserts the enqueue set ⊆ the process set for the fixture matrix.

2. **A ROUND_1 that finds no processable file for its target, while unprocessed audio IS
   present in the inbox, fails loud — never returns `done` silently.** It writes a standup
   entry AND fires `send_pipeline_alert` naming the offending filename(s) and the exact
   remediation ("rename `E30.m4a` → `E30-<slug>.m4a`"). *Testable:* seed the inbox with a
   convention-violating audio file, run ROUND_1, assert (a) an alert was sent, (b) a standup
   block was prepended, (c) the job status reflects the anomaly — not a clean `done`.

3. **Decide and record the bare-`E{N}` policy (ADR).** Either (a) accept `E<N>` with no
   separator and derive a safe slug fallback (not `untitled` — `_derive_slug('E30.m4a')`
   returns `'untitled'` today, which poisons the Drive folder name and schedule slug), or
   (b) keep the strict convention and rely on criterion 2's loud alert to get it renamed.
   Bill + Toby choose; Angela records the ADR. *Whichever is chosen, criterion 2 still
   holds* — a genuinely unnamed/garbage file must still alert, not vanish.

## Jen — filename-shape tolerance matrix

Mirror the publisher-drift matrix (sender × subject) with an intake matrix. At minimum:

| filename | today | must become |
|----------|-------|-------------|
| `E30-perimenopause-jane.m4a` | ✅ processes | ✅ processes (unchanged) |
| `E30_perimenopause_jane.m4a` | ✅ processes | ✅ processes (unchanged) |
| `E30.m4a` | ❌ silent no-op | ✅ process w/ safe slug **or** ❌ **loud** alert (per ADR) |
| `E30 perimenopause jane.m4a` (space sep) | ❌ silent no-op | process or loud alert |
| `E30-.m4a` (separator, empty slug) | slug edge | safe slug or loud alert |
| `random-voice-memo.m4a` (no E-code) | ❌ silent no-op | ❌ **loud** alert (audio present, unnamed) |
| `E30-notes.txt` (non-audio) | ignored (correct) | ignored (correct) — no alert |

## Notes

- Scope is the *intake* gate only. Don't touch the reprocess-numbering logic (round_1.py:243)
  or the title gate — those are correct and separately tested.
- The alert must be *actionable*, not just "something's wrong": name the file and the exact
  rename. The operator fix for E30 was a 10-second rename — the cost was entirely that no one
  was told for two hours.
- Immediate E30 unblock (2026-07-06) was handled out-of-band: rename in Drive UI →
  10-min drive-poll re-enqueues. This story is the durable fix so it can't recur silently.

## ADR, 2026-10-01: the bare `E{N}` policy is (a), lenient intake + loud on everything else

**Proposed by Angela on Bill's implementation. Toby to confirm at review.**

- **Decision.** `episode_files.py` is the single authority. An episode file is an E-number followed by `-`,
  `_`, a space, the extension dot or nothing (so `E30-…`, `E30_…`, `E30 …` and `E30.m4a` all qualify), **and**
  `.m4a`/`.mp4`, the only formats `transcribe_fast.sh` reads. An empty slug becomes `episode`, never `untitled`.
- **Loud on everything else.** Enqueue still fires on any audio, from the same shared extension list, so a
  wrongly named or wrongly formatted file wakes Round 1 up. Round 1 then raises `PermanentFailure` (standup
  entry) and sends `send_pipeline_alert` to `PIPELINE_ALERT_EMAIL`. The alert names each file and the exact fix:
  "rename it to `E33-<guest>.m4a`" or "export it as `.m4a`". A later episode waiting behind a missing one alerts
  too. Only a truly empty inbox is a clean no-op.
- **Why (a).** The back catalogue is about to be dropped in by hand (John, 2026-10-01). Accepting the obvious
  names costs nothing, and the loud path covers the rest.

## Also fixed here: the season of an episode the pipeline has never seen

An old episode with no schedule entry got "current season, in-season number = E-number", so E19 became
**S2E19**. `_season_for` now derives it from the counter (season start = `next_episode_number −
next_season_episode + 1`, so E26 for S2). One season back is Season 1, numbered as its E-number. Anything older
is refused rather than guessed.

## Tests

`test_r1_01_intake.py`: 12 cases, including Jen's matrix and the real `_run_pipeline` through title01's
harness.
- **Red on `main`:** 8 of 12 fail. Stranded audio finishes `done`; `E30.m4a` is skipped; the slug is
  `untitled`; there are three extension lists; and there's no season derivation.
- **Green on the branch:** 12/12.
