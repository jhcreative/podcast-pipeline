"""
test_clip01b_measure.py — measuring the box's clip limit: the limit rule, the WhisperX flags, and a rehearsal of the
box-side driver (CLIP-01b AC 1, 2).

The driver normally runs on the box under systemd. Here systemd-run, ffmpeg, pgrep, nproc, chown and the job queue are
stand-ins on PATH, and /proc/meminfo is a fake, so the driver's own loop, its stop rule and its "killed" record run for
real. clip_limit.py and measure_one_clip.py are the real code.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix="clip01b-"))

from rockstar.tools import (
    clip_limit,  # noqa: E402
    measure_one_clip,  # noqa: E402
)

BOX = {"mem_total_mb": 3809, "mem_available_mb": 3113, "swap_mb": 2047, "measured_at": "2026-10-12T20:00:00Z"}


def _run(batch, seconds, peak, exit=0):
    return {"batch": batch, "seconds": seconds, "peak_mb": peak, "exit": exit, "wall_s": 1}


# ── the limit rule ────────────────────────────────────────────────────────────


def test_the_limit_is_the_longest_length_with_every_shorter_one_fitting():
    runs = [_run(2, 30, 2600), _run(2, 60, 2650), _run(2, 90, 2700), _run(2, 120, 2950), _run(2, 180, 2700)]
    # budget 3113 - 600 = 2513: nothing fits. With 300 reserve, 2813: 30-90 fit, 120 doesn't, so 180 can't count.
    assert clip_limit.limit(runs, 2, 3113 - 600) == 0
    assert clip_limit.limit(runs, 2, 3113 - 300) == 90


def test_a_killed_or_unreported_run_never_fits():
    assert not clip_limit.fits(_run(2, 30, 1000, exit=-9), 3000)
    assert not clip_limit.fits({"batch": 2, "seconds": 30, "peak_mb": None, "exit": "killed"}, 3000)


def test_the_proposal_picks_the_batch_with_the_longest_limit():
    runs = [_run(8, 30, 2700), _run(2, 30, 2600), _run(2, 60, 2650), _run(2, 90, 2700)]
    p = clip_limit.proposal(BOX, runs, reserve_mb=300)
    assert (p["batch"], p["clip_max_seconds"], p["by_batch"]) == (2, 90, {2: 90, 8: 30}), p
    env = clip_limit.env_text(BOX, p, "x.jsonl")
    assert "CLIP_MAX_SECONDS=90\n" in env and "CLIP_BATCH_SIZE=2\n" in env and "CLIP_RESERVE_MB=300\n" in env, env


def test_nothing_fits_is_said_not_hidden():
    p = clip_limit.proposal(BOX, [_run(2, 30, 3100)], reserve_mb=600)
    assert p["clip_max_seconds"] == 0, p


def test_a_failure_that_is_not_memory_blocks_the_proposal():
    """Toby: WhisperX output went to /dev/null, so a missing token or a cache miss read as 'out of memory'."""
    runs = [_run(2, 30, 2600), {"batch": 2, "seconds": 60, "peak_mb": 400, "exit": 1, "oom": False}]
    p = clip_limit.proposal(BOX, runs, reserve_mb=300)
    assert p["broken"] == [(2, 60)], p
    assert clip_limit.verdict(runs[1], p["budget_mb"]).startswith("FAILED, not memory"), clip_limit.verdict(runs[1], 0)
    oom = {"batch": 2, "seconds": 180, "peak_mb": 2800, "exit": -9, "oom": True}
    assert clip_limit.verdict(oom, 2800) == "killed: out of memory" and not clip_limit.broken(oom)


def test_env_refuses_while_a_run_failed_for_another_reason():
    ev = TMP / "broken.jsonl"
    ev.write_text(
        json.dumps({"box": BOX})
        + "\n"
        + json.dumps({"batch": 2, "seconds": 30, "peak_mb": 1, "exit": 2, "oom": False})
        + "\n"
    )
    r = subprocess.run(
        [sys.executable, str(REPO / "rockstar" / "tools" / "clip_limit.py"), str(ev), "--reserve-mb", "300", "--env"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2 and "REFUSED" in r.stderr and "CLIP_MAX_SECONDS" not in r.stdout, (r.returncode, r.stdout)


def test_each_run_is_capped_named_timed_and_killed_first():
    src = (REPO / "scripts" / "clip_measure_driver.sh").read_text()
    for prop in (
        'MemoryMax="${cap}M"',
        "MemorySwapMax=0",
        "OOMPolicy=continue",
        "OOMScoreAdjust=1000",
        'RuntimeMaxSec="$RUN_MAX_SEC"',
        '--unit="clip-measure-run-${batch}-${seconds}"',
        'EnvironmentFile=-"$APP/.env"',
    ):  # optional: a box before its cutover has no .env yet (HOST-01)
        assert prop in src, prop
    assert "cap=$((avail - CAP_MARGIN_MB))" in src


# ── AC 2: the measurement is WhisperX exactly as transcribe_clips.sh runs it ──


def test_the_measured_flags_are_transcribe_clips_flags():
    src = (REPO / "scripts" / "transcribe_clips.sh").read_text()
    block = re.search(r"WHISPERX_CMD=\((.*?)\)", src, re.S).group(1)
    words = [w for w in block.split() if w not in ("whisperx", '"$CLIP_PATH"')]
    flags = dict(zip(words[::2], words[1::2], strict=False))
    flags.pop("--output_dir")
    flags.pop("--batch_size")  # the one thing measured on purpose
    measured = dict(zip(measure_one_clip.WHISPERX_ARGS[::2], measure_one_clip.WHISPERX_ARGS[1::2], strict=False))
    assert measured == flags, (measured, flags)


def test_measure_one_clip_reports_one_json_line():
    fake = TMP / "whisperx_ok"
    fake.write_text("#!/bin/bash\nexit 0\n")
    fake.chmod(0o755)
    out = subprocess.run(
        [
            sys.executable,
            str(REPO / "rockstar" / "tools" / "measure_one_clip.py"),
            "clip.m4a",
            "30",
            "2",
            str(fake),
            str(TMP),
        ],
        capture_output=True,
        text=True,
    )
    row = json.loads(out.stdout.strip())
    assert (row["seconds"], row["batch"], row["exit"]) == (30, 2, 0) and isinstance(row["peak_mb"], int), row
    assert "oom" in row and "cgroup_peak_mb" in row and (TMP / "whisperx.log").exists(), row


# ── the driver, rehearsed ─────────────────────────────────────────────────────


def _stage(whisperx_busy_after: int | None = None):
    """A fake box: a WhisperX venv whose python is this python, stand-in tools on PATH. The 180 s run's whole unit is
    'killed' (no report). pgrep finds a WhisperX once `whisperx_busy_after` runs have happened."""
    root = Path(tempfile.mkdtemp(dir=TMP))
    app, work, bin_ = root / "app", root / "work", root / "bin"
    for d in (app / "venv" / "bin", app / "scripts" / "whisperx_env" / "bin", app / "state", work, bin_):
        d.mkdir(parents=True)
    counter = root / "runs"
    counter.write_text("0")
    (app / "venv" / "bin" / "python").write_text("#!/bin/bash\necho 0\n")
    busy = (
        "exit 1\n"
        if whisperx_busy_after is None
        else f'[ "$(cat {counter})" -ge {whisperx_busy_after} ] && exit 0\nexit 1\n'
    )
    os.symlink(sys.executable, app / "scripts" / "whisperx_env" / "bin" / "python")
    (app / "scripts" / "whisperx_env" / "bin" / "whisperx").write_text("#!/bin/bash\nexit 0\n")
    (work / "source.m4a").write_text("audio")
    (work / "measure_one_clip.py").write_text((REPO / "rockstar" / "tools" / "measure_one_clip.py").read_text())
    meminfo = root / "meminfo"
    meminfo.write_text("MemTotal: 3900416 kB\nMemAvailable: 3187712 kB\nSwapTotal: 2096128 kB\n")
    tools = {
        # drop systemd-run's own options, then run the command; the 180 s scope dies before reporting
        "systemd-run": f"n=$(cat {counter}); echo $((n+1)) > {counter}\n"
        'while [ "${1#-}" != "$1" ]; do case "$1" in -p) shift 2;; *) shift;; esac; done\n'
        'case "$*" in *clip_180*) exit 1;; esac\nexec "$@"\n',
        "ffmpeg": 'cp "${@: -4:1}" "${@: -1}"\n',
        "pgrep": busy,
        "nproc": "echo 2\n",
        "chown": "exit 0\n",
    }
    for name, body in tools.items():
        (bin_ / name).write_text("#!/bin/bash\n" + body)
        (bin_ / name).chmod(0o755)
    for p in (app / "venv" / "bin" / "python", app / "scripts" / "whisperx_env" / "bin" / "whisperx"):
        p.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "MEMINFO": str(meminfo)}
    return app, work, env


def _drive(app, work, env, runs):
    return subprocess.run(
        [
            "bash",
            str(REPO / "scripts" / "clip_measure_driver.sh"),
            str(work),
            str(app),
            os.environ.get("USER", "nobody"),
            runs,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_the_driver_records_the_box_every_run_and_a_killed_scope():
    app, work, env = _stage()
    r = _drive(app, work, env, "2:30 2:180 8:30")
    assert r.returncode == 0, r.stderr
    rows = [json.loads(line) for line in (work / "evidence.jsonl").read_text().splitlines()]
    assert rows[0]["box"]["mem_available_mb"] == 3113 and rows[0]["box"]["cpus"] == 2, rows[0]
    assert rows[0]["box"]["cap_mb"] == 3113 - 300, rows[0]
    assert [(x["batch"], x["seconds"], x["exit"]) for x in rows[1:]] == [(2, 30, 0), (2, 180, "killed"), (8, 30, 0)], (
        rows
    )
    assert "DONE" in (work / "run.log").read_text()
    box, runs = clip_limit.load(work / "evidence.jsonl")  # the evidence reads back into the limit rule
    assert len(runs) == 3 and box["mem_total_mb"] == 3809


def test_the_driver_stops_when_another_whisperx_appears():
    app, work, env = _stage(whisperx_busy_after=1)
    r = _drive(app, work, env, "2:30 2:60 2:90")
    assert r.returncode == 3, (r.returncode, r.stdout)
    assert "STOPPED: WhisperX is already running" in (work / "run.log").read_text()
    rows = [line for line in (work / "evidence.jsonl").read_text().splitlines() if '"seconds"' in line]
    assert len(rows) == 1, rows


def _mac(queue: str = "0", server: str = "root@fake-box", worker_active: bool = True):
    """The Mac script against a fake box: ssh runs its command here in bash, scp copies locally, systemctl and
    systemd-run log what they're asked (systemd-run also runs the driver, so --fetch has evidence to read)."""
    root = Path(tempfile.mkdtemp(dir=TMP))
    app, box_dir, bin_, home, log = root / "app", root / "var-tmp", root / "bin", root / "home", root / "calls.log"
    for d in (app / "venv" / "bin", app / "state", app / "scripts" / "whisperx_env" / "bin", bin_, home):
        d.mkdir(parents=True)
    # the real python against a real jobs.db, so the queue check's nested quoting through ssh runs for real
    os.symlink(sys.executable, app / "venv" / "bin" / "python")
    import sqlite3

    db = sqlite3.connect(app / "state" / "jobs.db")
    db.execute("create table jobs (id integer primary key, status text)")
    db.executemany("insert into jobs (status) values (?)", [("done",), ("failed",)] + [("pending",)] * int(queue))
    db.commit()
    db.close()
    os.symlink(sys.executable, app / "scripts" / "whisperx_env" / "bin" / "python")
    (app / "scripts" / "whisperx_env" / "bin" / "whisperx").write_text("#!/bin/bash\nexit 0\n")
    meminfo = root / "meminfo"
    meminfo.write_text("MemTotal: 3900416 kB\nMemAvailable: 3187712 kB\nSwapTotal: 2096128 kB\n")
    tools = {
        "ssh": 'while [ "${1#-}" != "$1" ]; do shift 2; done; shift; exec bash -c "$*"\n',
        "scp": 'args=(); for a in "$@"; do case "$a" in -q) ;; -o) skip=1 ;; *) if [ -n "$skip" ]; then skip=; else args+=("${a#*:}"); fi ;; esac; done\n'
        'n=${#args[@]}; cp "${args[@]:0:n-1}" "${args[n-1]}"\n',
        "systemctl": f'echo "systemctl $*" >> {log}\n'
        + (
            'case "$*" in *is-active*rockstar-worker*|*is-enabled*rockstar-worker*) echo active; exit 0;; esac\n'
            if worker_active
            else ""
        )
        + 'case "$1" in is-active|is-enabled) exit 3;; esac\n',
        "sudo": '[ "$1" = -n ] && shift; exec "$@"\n',
        "systemd-run": f'echo "systemd-run $*" >> {log}\nwhile [ "${{1#-}}" != "$1" ]; do case "$1" in -p) shift 2;; *) shift;; esac; done\n'
        f'MEMINFO={meminfo} "$@" >/dev/null 2>&1; true\n',
        "pgrep": "exit 1\n",
        "nproc": "echo 2\n",
        "chown": "exit 0\n",
        "du": 'echo "3.0G\t$2"\n',
        "df": 'echo hdr; echo "x 1 1 99999999 1% /"\n',
        "install": 'cp "${@: -2:1}" "${@: -1}"\n',
    }
    for name, body in tools.items():
        (bin_ / name).write_text("#!/bin/bash\n" + body)
        (bin_ / name).chmod(0o755)
    (app / "scripts" / "whisperx_env" / "bin" / "whisperx").chmod(0o755)
    src = root / "speech.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "800",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
    )
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "HOME": str(home),
        "BOX_SERVER_OVERRIDE": server,
        "BOX_APP_OVERRIDE": str(app),
        "BOX_DIR_OVERRIDE": str(box_dir),
        "CLIP_SOURCE": str(src),
    }

    def run(*a):
        return subprocess.run(
            ["bash", str(REPO / "scripts" / "measure_clip_limit.sh"), *a], capture_output=True, text=True, env=env
        )

    return run, log, app, box_dir, home


def test_start_pauses_the_worker_only_after_the_files_are_up_and_brings_it_back():
    run, log, app, box_dir, home = _mac()
    r = run("--start")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text().splitlines()
    stop = next(i for i, c in enumerate(calls) if c == "systemctl stop rockstar-worker")
    start = next(i for i, c in enumerate(calls) if c.startswith("systemd-run --unit=clip-measure "))
    assert stop < start, calls
    assert "ExecStopPost=/bin/systemctl start rockstar-worker" in calls[start], calls[start]
    assert (box_dir / "source.m4a").exists() and (box_dir / "clip_measure_driver.sh").exists()
    r = run("--fetch")
    assert r.returncode == 0 and "PROPOSED: CLIP_MAX_SECONDS=" in r.stdout and "DONE" in r.stdout, r.stdout + r.stderr
    saved = list((home / "RockstarPodcast-evaldata" / "box-limits").glob("*-evidence.jsonl"))
    assert saved, "the evidence is saved on the Mac"
    r = run("--write", "--reserve-mb", "300")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CLIP_MAX_SECONDS=" in (app / "state" / "box-limits.env").read_text()
    assert not box_dir.exists(), "the speech clip and outputs are removed after --write"


def test_start_refuses_a_busy_queue_and_never_touches_the_worker():
    run, log, *_ = _mac(queue="2")
    r = run("--start")
    assert r.returncode != 0 and "REFUSED: 2 pipeline job(s)" in r.stdout, r.stdout + r.stderr
    assert "systemctl stop rockstar-worker" not in (log.read_text() if log.exists() else "")


def test_stop_restarts_the_worker():
    run, log, *_ = _mac()
    r = run("--stop")
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert any(c.startswith("systemctl stop clip-measure clip-measure-run-*") for c in calls), calls
    assert "systemctl start rockstar-worker" in calls, calls


def test_on_a_box_before_its_cutover_the_worker_is_never_started():
    """HOST-01: the OVH box, as deploy@ through sudo, its worker installed but off. Measuring must not start it: two
    boxes would share the same Drive and Gmail feeds."""
    run, log, app, box_dir, home = _mac(server="deploy@fake-new-box", worker_active=False)
    r = run("--start", "--new-box")
    assert r.returncode == 0 and "stays off" in r.stdout, r.stdout + r.stderr
    calls = log.read_text()
    assert "systemctl start rockstar-worker" not in calls and "ExecStopPost" not in calls, calls
    assert "systemctl stop rockstar-worker" not in calls, calls
    run("--stop", "--new-box")
    assert "systemctl start rockstar-worker" not in log.read_text(), log.read_text()


def test_both_scripts_parse():
    for s in ("measure_clip_limit.sh", "clip_measure_driver.sh"):
        subprocess.run(["bash", "-n", str(REPO / "scripts" / s)], check=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
