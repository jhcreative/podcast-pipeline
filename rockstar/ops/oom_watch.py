"""
oom_watch.py — did the kernel's OOM killer take a process in this service while a step ran? (CLIP-01a)

E13, 2026-10-12: WhisperX ran the box out of memory in step 8 five times. Python saw only `exit -15` — systemd's
SIGTERM as it stopped the whole unit — so every attempt looked like an ordinary error, was retried, and rewrote the
FAQ and the draft before dying in the same place. The OOM itself showed only in systemd's journal.

The evidence we read instead of guessing from the exit code: cgroup v2's `memory.events` for the service's own
cgroup. Its `oom_kill` line counts processes in that cgroup killed by any OOM killer, the kernel's global one
included. Read it before a step and after a failure; if it grew, the step ran out of memory, and a retry on the same
input will run out again.

Off the box (a laptop, cgroup v1, no permission) the count is None: unknown, never 0, so nothing is called an OOM
without evidence.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Tests point this at a fake memory.events; on the box it is found from /proc/self/cgroup.
_EVENTS_ENV = "ROCKSTAR_MEMORY_EVENTS"


def _events_path() -> Path | None:
    if os.environ.get(_EVENTS_ENV):
        return Path(os.environ[_EVENTS_ENV])
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            # cgroup v2 has a single line "0::/system.slice/rockstar-worker.service"
            if line.startswith("0::"):
                return Path("/sys/fs/cgroup") / line[3:].lstrip("/") / "memory.events"
    except OSError:
        pass
    return None


def oom_kills() -> int | None:
    """This cgroup's OOM-kill count so far, or None when it can't be read."""
    path = _events_path()
    if path is None:
        return None
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "oom_kill":
                return int(value)
    except (OSError, ValueError):
        pass
    return None


def ran_out(before: int | None) -> bool:
    """True only on evidence: both readings exist and the count grew."""
    after = oom_kills()
    return before is not None and after is not None and after > before


_CLIP_LINE = re.compile(r"^\s*\[(\d+)\]\s+(\S+)\.\.\.\s*$", re.MULTILINE)


def clip_in_progress(stdout: str, manifest: Path) -> str:
    """For transcribe_clips: the clip it had started when it died, with its length from the manifest.

    Holds while systemd's OOMPolicy=stop (the default) stops the whole unit, so the script dies with WhisperX on the
    clip that ran out. With OOMPolicy=continue or a MemoryMax=, transcribe_clips.sh would count the failure and go on
    to the next clip, and the last line printed would name the wrong one: change this with either setting.

    The script prints "  [N] quote_NN_slug.m4a..." before each clip, so the last such line is the one that ran out.
    Returns "" when stdout names no clip, and leaves the length out when the manifest can't give it."""
    found = _CLIP_LINE.findall(stdout or "")
    if not found:
        return ""
    index, filename = found[-1]
    try:
        for clip in json.loads(manifest.read_text())["clips"]:
            if clip.get("clip_filename") == filename:
                seconds = (clip["clip_end_ms"] - clip["clip_start_ms"]) / 1000
                return f"quote {index} ({filename}, {seconds:.1f} s)"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return f"quote {index} ({filename})"
