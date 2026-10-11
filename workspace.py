"""
workspace.py — one home for "where does per-job scratch work live".

WHY THIS MODULE EXISTS
----------------------
Every pipeline job downloads an episode's audio, transcripts, quote clips and
rendered reels into a temp directory, works there, and deletes it in `finally`.
The cleanup was never the problem — it works, and the server carries no stale
episode data.

The problem was WHERE that directory landed. On the box, `/tmp` is a **tmpfs**:
a RAM-backed filesystem, 1.9 GB on a 3.8 GB machine. Meanwhile `/` has 59 GB
free. Every job was therefore holding its audio, its extracted WAV clips and its
rendered MP4s **in RAM**, at exactly the moment WhisperX wanted 1.7–2.6 GB of
that same RAM.

That was not a decision — it was a default nobody had looked at:

  • `tempfile.mkdtemp(prefix=...)` was called with **no `dir=`**, so it fell back
    to TMPDIR/`/tmp` regardless of configuration.
  • The systemd units set `WORK_DIR_BASE=/tmp`, which *looked* like the knob that
    controlled this — but only the bash transcribe scripts ever read it. The
    Python jobs ignored it entirely.

So the one setting that appeared to govern scratch location governed half the
pipeline, and pointed the half it did govern at RAM.

Evidence this mattered (journalctl, 2026-07-21): six OOM kills, every one of them
`whisperx`, twice taking `rockstar-worker.service` down with it —
anon-rss 1.90 GB, 1.99 GB, 2.58 GB. WhisperX is the dominant term and this module
does not fix that. What it fixes is spending scarce RAM on files that belong on a
disk that is 82 % empty. At a 1.79 GB working-set peak, a few hundred MB of
avoidable pressure is not a rounding error.

CONTRACT
--------
`work_dir_base()` returns a disk-backed directory that exists. `WORK_DIR_BASE`
overrides it (same variable the bash scripts read, so both halves of the pipeline
now agree). The default is `/var/tmp/rockstar-work` — on Ubuntu `/var/tmp` lives
on the root filesystem, unlike `/tmp`.

Callers use `tempfile.mkdtemp(prefix=..., dir=work_dir_base())` and keep their
existing `shutil.rmtree` in `finally`. This module deliberately does NOT own
cleanup: the jobs already do that correctly and moving it here would spread one
responsibility across two files.
"""

import os
import tempfile
from pathlib import Path

# /var/tmp is disk-backed on Ubuntu; /tmp on this box is a 1.9 GB tmpfs. Any
# override must also be disk-backed — pointing this at a tmpfs re-creates the
# exact problem the module was written to remove.
_DEFAULT_WORK_DIR_BASE = "/var/tmp/rockstar-work"


def work_dir_base() -> Path:
    """
    Return the directory per-job scratch dirs are created under, creating it if
    needed. Honors WORK_DIR_BASE so the Python jobs and the bash transcribe
    scripts resolve to the same place.
    """
    base = Path(os.environ.get("WORK_DIR_BASE") or _DEFAULT_WORK_DIR_BASE)
    base.mkdir(parents=True, exist_ok=True)
    return base


def make_work_dir(prefix: str) -> Path:
    """
    Create a per-job scratch directory on disk. The caller owns its lifetime and
    is responsible for removing it (the jobs do this in `finally`).
    """
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(work_dir_base())))
