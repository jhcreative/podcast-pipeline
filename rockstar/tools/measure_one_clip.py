#!/usr/bin/env python3
"""
measure_one_clip.py — one WhisperX run, exactly as transcribe_clips.sh runs it, and its peak memory (CLIP-01b AC 1).

Runs on the box, inside a memory-capped systemd unit that scripts/clip_measure_driver.sh starts as the worker's user.
Prints ONE JSON line:
  {"seconds", "batch", "peak_mb", "cgroup_peak_mb", "exit", "oom", "wall_s"}
- peak_mb: the largest descendant's peak RSS (ru_maxrss of children): WhisperX itself, the number the verdict uses.
- cgroup_peak_mb: the unit's memory.peak, page cache (the model file) included: context, it overstates.
- oom: the unit's memory.events oom_kill grew (evidence), so an exit that isn't an OOM is never called one.
WhisperX's own output stays on the box in <out_dir>/whisperx.log (its last lines), never in the evidence.

  python3 measure_one_clip.py <clip.m4a> <seconds> <batch> <whisperx> <out_dir>

WHISPERX_ARGS must match transcribe_clips.sh's WHISPERX_CMD apart from the batch size; a test compares the two.
"""

import json
import resource
import subprocess
import sys
import time
from pathlib import Path

WHISPERX_ARGS = ["--model", "large-v3", "--language", "en", "--output_format", "json", "--compute_type", "int8"]


def _cgroup_file(name: str) -> Path | None:
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                return Path("/sys/fs/cgroup") / line[3:].lstrip("/") / name
    except OSError:
        pass
    return None


def _read(name: str, key: str | None = None) -> int | None:
    path = _cgroup_file(name)
    try:
        text = path.read_text() if path else ""
        if key is None:
            return int(text.strip())
        for line in text.splitlines():
            k, _, v = line.partition(" ")
            if k == key:
                return int(v)
    except (OSError, ValueError):
        pass
    return None


def main(argv: list[str]) -> int:
    clip, seconds, batch, whisperx, out_dir = argv
    oom_before = _read("memory.events", "oom_kill")
    started = time.monotonic()
    r = subprocess.run(
        [whisperx, clip, *WHISPERX_ARGS, "--output_dir", out_dir, "--batch_size", batch],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    Path(out_dir, "whisperx.log").write_text("\n".join((r.stderr or "").splitlines()[-20:]) + "\n")
    # ru_maxrss of terminated children: the WhisperX process. KB on Linux, bytes on macOS.
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    oom_after = _read("memory.events", "oom_kill")
    cgroup_peak = _read("memory.peak")
    print(
        json.dumps(
            {
                "seconds": int(seconds),
                "batch": int(batch),
                "peak_mb": round(peak / (1048576 if sys.platform == "darwin" else 1024)),
                "cgroup_peak_mb": round(cgroup_peak / 1048576) if cgroup_peak is not None else None,
                "exit": r.returncode,
                "oom": (oom_after > oom_before) if None not in (oom_before, oom_after) else None,
                "wall_s": round(time.monotonic() - started),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
