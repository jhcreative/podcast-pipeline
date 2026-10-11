# DEPLOY-01 — The server runs code we don't deploy, and nothing tells us

**Owner:** Toby (architecture) · **Ops:** Oliver · **Tests:** Jen · **PO:** Angela
**Priority:** HIGH — promoted from "Open / recommended" after REEL-05
**Status:** **PARTIALLY DONE — GUARD STILL OPEN (John, keep on the to-do list).**
Manifest corrected and `--check` no longer abandons runs silently (`0d8ed56`). The
automated guard — criteria 1, 2, 3, 5 — is NOT built. · **Created:** 2026-07-21

---

## Why

A deploy-FILES preflight guard has been an open recommendation since the 2026-06-26
carry-forward. It was reasonable to defer it while it was theoretical. **It is no longer
theoretical.** REEL-05 found:

- **`scripts/align_episode.py` is not in `scripts/deploy.sh` FILES** — so it does not exist
  on the server. It is what produces `transcript_aligned.json`, without which the entire
  ADR-0003/ADR-0004 code path is dead. Two accepted ADRs, fully specified, tested, reviewed
  and "shipped," have **never executed in production.**
- **`scripts/inject_word_timestamps.js` is not in FILES either** — yet `publish_finalize.py:796`
  *invokes it on every episode.* The server is executing a script our deploy process does not
  manage. We cannot say what version is running. It is also the direct source of the phantom-
  caption defect.

The precedent was already there: commit `037c1f2` — "fix(deploy): include transcribe scripts
in deploy.sh FILES." Same bug, same file, fixed by hand, and nothing was added to stop the
next instance. This is the third time a hand-maintained manifest has silently diverged.

The failure mode is the worst kind we have: **it looks like success.** Tests pass, review
passes, deploy reports 30 files with sha256 parity — parity against a *manifest that is
itself wrong*. Oliver's deploy verification is honest and thorough and could not have caught
this, because it verifies the wrong invariant.

This is the same family as R1-01 and TRIG-01: **we keep building systems whose absence of a
signal is indistinguishable from success** (PQ-5).

## Acceptance criteria

1. **A preflight fails the deploy when a referenced file is not in the manifest.** Static
   scan of the deployed Python/JS for module imports, `subprocess`/`spawn` invocations, and
   prompt/asset path literals; every resolved repo-relative path must appear in `deploy.sh`
   FILES. Missing ⇒ **non-zero exit, deploy blocked** — not a warning.
   *Testable:* a fixture referencing an unlisted file fails; the current tree fails until
   `align_episode.py` and `inject_word_timestamps.js` are added; the corrected tree passes.

2. **The reverse direction is reported too.** Files in FILES that nothing references are
   listed as a warning (dead weight — DOD-4). Warning, not a block.

3. **Post-deploy, the server's inventory is verified against the manifest.** Today's sha256
   check proves *listed* files match. Add: no pipeline-referenced file is **absent** on the
   box. *Testable:* remove a file server-side in staging ⇒ verification fails loudly.

4. **The two known-missing files are added and a real episode proves the path is live.**
   Adding them to FILES is necessary but **not sufficient** — REEL-05's Gate A also requires
   `publish_finalize.py` to actually pass `--aligned`. Deploying `align_episode.py` without
   wiring it changes nothing. *Testable:* an episode processed after this change shows
   `usingAligned === true` in its logs. **That log line is the acceptance evidence.**

5. **The guard runs in CI, not only at deploy time.** A PR that references an unlisted file
   fails before it merges (DOD-1, DOD-3).

## Notes

- Scope is the manifest-vs-reality invariant. **Do not** rewrite `deploy.sh` into a
  packaging system — the hand-rolled manifest is a deliberate choice and stays. We are adding
  the check that makes it honest.
- Toby owns the "how"; this story only fixes that a wrong manifest is currently
  indistinguishable from a right one.
- Related: REEL-05 (found it), R1-01 (same silent-failure class), commit `037c1f2` (the
  first instance, patched without a guard), 2026-06-26 carry-forward (where it was first
  recommended).
