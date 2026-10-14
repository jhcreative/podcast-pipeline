"""
test_reel11_alignment_stops.py — a forced-alignment failure stops Phase 2 instead of making reels on guessed timing.

Step 7a-0 used to catch an align_episode failure, alert, and carry on: the reels were then framed on interpolated
timing, the REEL-05 defects (truncated or trailing ends), and still went to Drive. John, 2026-10-12: a Phase 2 that
can't align stops. Here the WhisperX venv's python is a fake that fails each way the story names; Gmail records the
alerts; _force_align, _run_script, run_ledger and oom_watch are the real code.
"""

import ast
import os
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix="reel11-"))
os.environ["RUN_LOG_PATH"] = str(TMP / "run_log.jsonl")
EVENTS = TMP / "memory.events"
os.environ["ROCKSTAR_MEMORY_EVENTS"] = str(EVENTS)

ALERTS = []
for _name in (
    "rockstar.clients.calendar_client",
    "rockstar.clients.drive_client",
    "rockstar.clients.gmail_client",
    "rockstar.clients.publisher_email",
    "rockstar.content.blog_publish",
    "rockstar.ops.workspace",
):
    _swap.put(_name, types.ModuleType(_name))
sys.modules["rockstar.clients.gmail_client"].send_pipeline_alert = lambda to, subject, body: (
    ALERTS.append((subject, body)) or {}
)
if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    _swap.put("anthropic", _a)

import json  # noqa: E402

from rockstar.jobs import publish_finalize as pf  # noqa: E402
from rockstar.ops import run_ledger  # noqa: E402

STANDUPS = []
pf._prepend_standup = lambda ep, text: STANDUPS.append((ep, text))
APPLE, SPOTIFY = "https://podcasts.example.test/e99", "https://open.example.test/e99"


def _venv(body: str) -> None:
    """A fake WhisperX venv whose python runs `body` as bash (the real align_episode.py is never reached)."""
    venv = Path(tempfile.mkdtemp(dir=TMP))
    (venv / "bin").mkdir()
    py = venv / "bin" / "python"
    py.write_text("#!/bin/bash\n" + body + "\n")
    py.chmod(0o755)
    os.environ["VENV_PATH"] = str(venv)


def _align():
    """Run 7a-0 as the pipeline does: inside its step, then close the job's clock with what happened."""
    ALERTS.clear()
    STANDUPS.clear()
    (TMP / "run_log.jsonl").unlink(missing_ok=True)
    work = Path(tempfile.mkdtemp(dir=TMP))
    run_ledger.bind(job_id=150, episode="E99", run_kind="eval")
    run_ledger.begin("publish_finalize")
    run_ledger.step("7a-0", "forced_alignment")
    try:
        out = pf._force_align(
            work, "E99", work / "transcript_fast.json", rerun=pf._rerun_command(99, APPLE, SPOTIFY, "", False)
        )
    except Exception as exc:  # noqa: BLE001
        run_ledger.end(exc)
        return exc, work
    run_ledger.end()
    return out, work


def _step_7a0():
    lines = (TMP / "run_log.jsonl").read_text().splitlines()
    return next(e for e in map(json.loads, lines) if e.get("event") == "step" and e["step"] == "7a-0")


def _stopped(exc):
    assert type(exc).__name__ == "PermanentFailure", repr(exc)
    assert len(ALERTS) == 1, ALERTS
    subject, body = ALERTS[0]
    assert subject == "E99: forced alignment failed — Phase 2 stopped, no reels made", subject
    assert "the FAQ and the blog draft" in body, body  # AC 3: what's already done is named
    assert (
        f"venv/bin/python -m rockstar.tools.reprocess_episode --episode 99 --apple {APPLE} --spotify {SPOTIFY}" in body
    ), body
    assert len(STANDUPS) == 1 and "Phase 2 stopped" in STANDUPS[0][1], STANDUPS
    assert _step_7a0()["outcome"] == "permanent_failure", _step_7a0()
    return body


# ── AC 1, 2, 3: every alignment failure is one stop with one alert ────────────


def test_a_failing_alignment_stops_phase_2_with_its_last_stderr_lines():
    _venv(
        "echo 'loading wav2vec2' >&2\necho 'Traceback (most recent call last):' >&2\n"
        "echo 'RuntimeError: CUDA-less build cannot load the model' >&2\nexit 1"
    )
    exc, _ = _align()
    body = _stopped(exc)
    assert "RuntimeError: CUDA-less build cannot load the model" in body, body  # the line that matters, quoted


def test_success_with_no_output_file_is_the_same_stop():
    _venv("exit 0")
    exc, _ = _align()
    body = _stopped(exc)
    assert "wrote no output" in body, body


def test_a_missing_venv_is_the_same_stop():
    os.environ["VENV_PATH"] = str(TMP / "no-such-venv")
    exc, _ = _align()
    _stopped(exc)


def test_a_good_alignment_returns_its_file_and_sends_nothing():
    _venv('out=""; while [ $# -gt 0 ]; do [ "$1" = "--out" ] && out="$2"; shift; done; echo "{}" > "$out"')
    out, work = _align()
    assert out == work / "transcript_aligned.json" and out.is_file(), out
    assert ALERTS == [] and STANDUPS == [], (ALERTS, STANDUPS)
    assert _step_7a0()["outcome"] == "ok"


# ── the re-run command (Toby's review): same mode, same links, .env first, every link shell-quoted ──


def test_the_rerun_command_repeats_the_run_as_asked():
    full = pf._rerun_command(99, APPLE, SPOTIFY, "https://mm.example.test/e99", False)
    assert full.startswith("cd /opt/rockstar-pipeline && set -a && source .env && set +a && "), full
    assert full.endswith(f"--episode 99 --apple {APPLE} --spotify {SPOTIFY} --mm https://mm.example.test/e99"), full
    assert "--mm" not in pf._rerun_command(99, APPLE, SPOTIFY, "", False)
    reels = pf._rerun_command(99, APPLE, SPOTIFY, "https://mm.example.test/e99", True)
    assert reels.endswith("--episode 99 --reels-only") and "--apple" not in reels, reels  # PP-8: never a full rerun


def test_a_missing_link_is_named_not_passed_empty():
    cmd = pf._rerun_command(99, "", SPOTIFY, "", False)
    assert "--apple <apple-url>" in cmd and "(fill in --apple first)" in cmd and "''" not in cmd, cmd


def test_a_hostile_link_is_quoted_not_run():
    import shlex

    evil = 'https://x.example.test/"$(touch /tmp/pwned)"`id`'
    cmd = pf._rerun_command(99, evil, SPOTIFY, "", False)
    tail = cmd.split("set +a && ", 1)[1]
    assert shlex.split(tail)[shlex.split(tail).index("--apple") + 1] == evil, cmd  # one literal word, nothing expanded


def test_a_reels_only_stop_does_not_claim_a_faq_or_draft():
    _venv("echo 'boom' >&2\nexit 1")
    ALERTS.clear()
    STANDUPS.clear()
    work = Path(tempfile.mkdtemp(dir=TMP))
    run_ledger.begin("publish_finalize")
    run_ledger.step("7a-0", "forced_alignment")
    try:
        pf._force_align(
            work, "E99", work / "transcript_fast.json", rerun=pf._rerun_command(99, "", "", "", True), reels_only=True
        )
    except Exception:  # noqa: BLE001
        pass
    run_ledger.end()
    ((subject, body),) = ALERTS
    assert "FAQ" not in body and "--reels-only" in body, body
    assert "FAQ" not in STANDUPS[0][1], STANDUPS


# ── AC 6: the real _produce_content stops at 7a-0 ─────────────────────────────


def test_produce_content_stops_at_7a0_with_one_alert_and_no_clips():
    """Drives the real _produce_content (reels-only, so no model call) through a fake Drive until 7a-0 fails."""
    _venv("echo 'boom' >&2\nexit 1")
    ALERTS.clear()
    STANDUPS.clear()
    (TMP / "run_log.jsonl").unlink(missing_ok=True)
    contents = {
        "t-txt": "HOST: hello.\n",
        "q": json.dumps({"quotes": [{"id": "q01", "text": "hello", "heading": "Hello"}]}),
        "a": "# How Sleep Changes After Forty\n\nAbout Jane Invented\n",
        "t-json": json.dumps({"segments": []}),
        "tile": "x",
        "reel": "x",
        "audio": "x",
    }
    folder = [
        {"id": "t-txt", "name": "transcript_fast.txt", "mimeType": "text/plain"},
        {"id": "q", "name": "quotes.json", "mimeType": "application/json"},
        {"id": "a", "name": "apple_summary.md", "mimeType": "text/markdown"},
        {"id": "t-json", "name": "transcript_fast.json", "mimeType": "application/json"},
        {"id": "audio", "name": "E99-jane.m4a", "mimeType": "audio/mp4"},
    ]
    drive = sys.modules["rockstar.clients.drive_client"]
    drive.list_folder = lambda fid: []
    drive.download_file = lambda fid, path: Path(path).write_text(contents[fid])
    scripts = []
    real_run = pf._run_script

    def run_script(cmd, work_dir, label):
        scripts.append(label)
        return real_run(cmd, work_dir, label)

    pf._run_script = run_script
    run_ledger.bind(job_id=151, episode="E99", run_kind="eval")
    run_ledger.begin("publish_finalize")
    try:
        exc = None
        try:
            pf._produce_content(
                work_dir=Path(tempfile.mkdtemp(dir=TMP)),
                episode_code="E99",
                episode_number=99,
                episode_title="How Sleep Changes After Forty",
                season_ep_code="S1E99",
                slug="e99",
                episode_folder_id="F99",
                episode_folder_url="https://drive.example.test/F99",
                folder_files=folder,
                tile_file={"id": "tile", "name": "E99-tile.jpg"},
                reel_file={"id": "reel", "name": "E99-reel.jpg"},
                calendar_event_id="",
                guest_name="Jane Invented",
                guest_email="",
                apple_url="",
                spotify_url="",
                mm_url="",
                message_id="",
                pub_schedule={},
                trigger="reprocess",
                reels_only=True,
            )
        except Exception as e:  # noqa: BLE001
            exc = e
        run_ledger.end(exc)
    finally:
        pf._run_script = real_run
    assert type(exc).__name__ == "PermanentFailure", repr(exc)
    assert scripts == ["align_episode"], scripts  # no lookup, no extract_quote_clips, nothing after 7a-0
    assert len(ALERTS) == 1 and "Phase 2 stopped" in ALERTS[0][0], ALERTS
    steps = [e for e in map(json.loads, (TMP / "run_log.jsonl").read_text().splitlines()) if e.get("event") == "step"]
    assert (steps[-1]["step"], steps[-1]["outcome"]) == ("7a-0", "permanent_failure"), steps


# ── AC 4: an OOM in alignment is the same stop, alerted once (CLIP-01a's alert) ──


def test_an_oom_in_alignment_stops_with_one_alert():
    EVENTS.write_text("oom_kill 0\n")
    _venv(f"printf 'oom_kill 1\\n' > '{EVENTS}'\nkill -TERM $$")
    exc, _ = _align()
    EVENTS.unlink()
    assert type(exc).__name__ == "PermanentFailure", repr(exc)
    assert len(ALERTS) == 1 and "out of memory in step 7a-0 forced_alignment" in ALERTS[0][0], ALERTS


# ── AC 1: nothing after 7a-0 runs ─────────────────────────────────────────────


def test_nothing_catches_the_stop_between_7a0_and_step_7():
    """_force_align's PermanentFailure must leave Phase 2 before step 7 (extract_clips) starts: in _produce_reels the
    call sits in no try and comes before the step-7 heading, and _produce_content calls _produce_reels in no try."""
    tree = ast.parse((REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text())

    def fn(name):
        return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)

    def call_to(scope, name):
        return next(n for n in ast.walk(scope) if isinstance(n, ast.Call) and ast.unparse(n.func) == name)

    def outside_every_try(scope, call):
        for t in (n for n in ast.walk(scope) if isinstance(n, ast.Try)):
            inside = any(n is call for b in t.body for n in ast.walk(b))
            assert not inside, f"{ast.unparse(call.func)} is inside the try at line {t.lineno}"

    reels = fn("_produce_reels")
    align = call_to(reels, "_force_align")
    step7 = next(
        n for n in ast.walk(reels) if isinstance(n, ast.Call) and ast.unparse(n).startswith("run_ledger.step('7'")
    )
    assert align.lineno < step7.lineno, (align.lineno, step7.lineno)
    outside_every_try(reels, align)
    content = fn("_produce_content")
    outside_every_try(content, call_to(content, "_produce_reels"))


def test_the_degraded_path_is_gone():
    src = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    assert "reels on degraded timing" not in src and "aligned_json_path = None" not in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
