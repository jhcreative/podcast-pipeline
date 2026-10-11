# MEM-01 — Job work dirs were in RAM by accident

**Owner:** Toby (architecture) · **Ops:** Oliver · **PO:** Angela
**Priority:** was HIGH · **Status:** **PART 1 DONE 2026-07-21 · PART 2 ACCEPTED-AS-IS (John)**
**Created:** 2026-07-21 · **Raised by:** John

---

## Why

On the server `/tmp` is a **tmpfs** — a RAM-backed filesystem, 1.9 GB on a 3.8 GB box —
while `/` has 59 GB free. Every job downloaded its episode audio, extracted WAV clips and
rendered MP4s into `/tmp`, holding them in RAM at exactly the moment WhisperX wanted
1.7–2.6 GB of that same RAM.

**Nothing accumulated.** Both jobs create their dir with `tempfile.mkdtemp()` and remove it
with `shutil.rmtree` in `finally` (`publish_finalize.py:547`, `round_1.py:613`); at
investigation time `/tmp` held 25 MB, all of it a manual download. The cost was the
*transient peak*, not stale data. (Angela's initial framing implied accumulation and was
wrong — corrected on the day.)

It was never a decision:

- `tempfile.mkdtemp()` was called with **no `dir=`**, so it fell back to TMPDIR/`/tmp`
  regardless of configuration.
- The systemd units set `WORK_DIR_BASE=/tmp`, which *looked* like the governing knob — but
  only the bash transcribe scripts ever read it. The Python jobs ignored it entirely.

So the one setting that appeared to control scratch location controlled half the pipeline,
and pointed that half at RAM.

## Evidence

`journalctl`, six OOM kills, every one `whisperx`, twice taking `rockstar-worker.service`
down with it:

```
Jun 03 23:23  Killed process 192914 (whisperx)  anon-rss: 2,579,972 kB
Jun 12 18:06  Killed process 293187 (whisperx)  anon-rss: 1,991,040 kB
Jun 23 21:28  Killed process 461628 (whisperx)  anon-rss: 1,898,800 kB
rockstar-worker.service: Failed with result 'oom-kill'
```

Measured on the day: a full-episode forced alignment pass peaks at **1.79 GB RSS**
(E28, 40 min audio, 4:21 wall, RTF 0.108×) — inside the band where WhisperX has been killed
before. The box carries a 2 GB swapfile, which is likely why recent runs survived.

## Part 1 — DONE (shipped `0d8ed56`, deployed 2026-07-21)

New `workspace.py` owns the answer; both jobs call `workspace.make_work_dir()`; systemd
units repointed to `WORK_DIR_BASE=/var/tmp/rockstar-work` (disk). Verified in production on
E30 job 93: `work dir /var/tmp/rockstar-work/rockstar-E30-pf-0c110kym`.

Cleanup is deliberately left where it was — the jobs already do it correctly in `finally`,
and moving it into `workspace.py` would spread one responsibility across two files.

## Part 2 — ACCEPTED AS-IS (John, 2026-07-21)

**Part 1 does not fix the OOM risk and was never going to.** WhisperX is the dominant term
at 1.7–2.6 GB on a 3.8 GB box; moving a few hundred MB of scratch off RAM removes needless
pressure, not the cause. Closing that needs one of:

- bounded concurrency (never let alignment and clip transcription overlap — today they are
  sequential by construction, but nothing *enforces* it),
- a smaller alignment/transcription footprint (batch size, compute type), or
- a bigger box.

John's call: **accept the current risk.** The alignment pass is now a per-episode cost we
did not previously pay, so if OOMs resume, this is the first place to look. The failure is
loud — `publish_finalize` alerts and writes a standup entry when alignment fails, and the
episode still publishes on degraded timing rather than stranding.

## Notes

- Any future override of `WORK_DIR_BASE` **must** be disk-backed. Pointing it at a tmpfs
  silently re-creates this exact problem.
- Related: REEL-05 (added the alignment pass that made the headroom question urgent),
  DEPLOY-01 (same silent-failure family).
