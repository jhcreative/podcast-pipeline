"""
test_clip01a_dead_job.py — a job that runs out of memory stops once and says so; a job that runs out of retries says so once.

E13, 2026-10-12: WhisperX ran the box out of memory in step 8 five times. Python saw only `exit -15` (systemd stopping
the unit), so each attempt was retried, rewrote the FAQ and the draft, and died in the same place; after the fifth,
the job went dead with no alert. Here the cgroup's memory.events is a temp file the fake scripts bump, the scripts kill
themselves with SIGTERM as systemd did, Gmail is a recorder, and the worker's database is a fake. oom_watch,
run_ledger, the two script runners and the worker loop are the real code.
"""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix="clip01a-"))
os.environ["RUN_LOG_PATH"] = str(TMP / "run_log.jsonl")
EVENTS = TMP / "memory.events"
os.environ["ROCKSTAR_MEMORY_EVENTS"] = str(EVENTS)

# The Google-backed modules are stand-ins (as in test_trig02): nothing here reaches Google, and the suite runs
# without the Google client libraries. Gmail records every alert.
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

from rockstar.jobs import publish_finalize, round_1  # noqa: E402
from rockstar.ops import (
    oom_watch,  # noqa: E402
    run_ledger,  # noqa: E402
)
from rockstar.service import worker  # noqa: E402


def _events(oom_kill: int) -> None:
    EVENTS.write_text(f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\noom_group_kill 0\n")


def _work_dir(clip_seconds: float = 174.5) -> Path:
    """A work dir with E13's shape: quote 3's clip in the manifest, at its real length."""
    work = Path(tempfile.mkdtemp(dir=TMP))
    (work / "clips").mkdir()
    (work / "clips" / "clips_manifest.json").write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "quote_index": 3,
                        "clip_filename": "quote_03_human-study.m4a",
                        "clip_start_ms": 1_324_500,
                        "clip_end_ms": 1_324_500 + int(clip_seconds * 1000),
                    }
                ]
            }
        )
    )
    return work


def _script(oom: bool) -> Path:
    """A stand-in for transcribe_clips.sh: names the clip it starts, then dies as E13's did (SIGTERM, exit -15).
    With oom=True it first bumps the cgroup's oom_kill count, as the kernel did."""
    s = Path(tempfile.mkdtemp(dir=TMP)) / "fake_transcribe.sh"
    bump = f"printf 'oom_kill 1\\n' > '{EVENTS}'\n" if oom else ""
    s.write_text(
        "#!/bin/bash\necho 'INFO: Transcribing 1 clip(s)'\necho '  [3] quote_03_human-study.m4a...'\n"
        f"{bump}kill -TERM $$\n"
    )
    return s


def _step8(episode="E99"):
    run_ledger.bind(job_id=147, episode=episode, run_kind="eval")
    run_ledger.begin("publish_finalize")
    run_ledger.step("8", "transcribe_clips")


def _raises(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


# ── oom_watch: evidence, never a guess ────────────────────────────────────────


def test_count_is_read_from_memory_events():
    _events(4)
    assert oom_watch.oom_kills() == 4


def test_count_is_unknown_without_the_file():
    EVENTS.unlink(missing_ok=True)
    assert oom_watch.oom_kills() is None
    assert oom_watch.ran_out(None) is False


def test_ran_out_only_when_the_count_grew():
    _events(1)
    assert oom_watch.ran_out(1) is False
    assert oom_watch.ran_out(0) is True


def test_clip_in_progress_names_the_clip_and_its_length():
    work = _work_dir()
    out = "INFO: Transcribing 5 clip(s)\n  [1] quote_01_x.m4a...\n  [3] quote_03_human-study.m4a...\n"
    assert (
        oom_watch.clip_in_progress(out, work / "clips" / "clips_manifest.json")
        == "quote 3 (quote_03_human-study.m4a, 174.5 s)"
    )
    assert oom_watch.clip_in_progress("no clip lines", work / "clips" / "clips_manifest.json") == ""


# ── AC 12: an OOM is not retried, and it says where ───────────────────────────


def test_phase2_oom_is_permanent_with_one_alert_naming_step_clip_length():
    _events(0)
    ALERTS.clear()
    _step8()
    work = _work_dir()
    exc = _raises(lambda: publish_finalize._run_script(["bash", _script(oom=True)], work, "transcribe_clips"))
    assert type(exc).__name__ == "PermanentFailure", repr(exc)
    assert "out of memory in step 8 transcribe_clips on quote 3" in str(exc) and "174.5 s" in str(exc), exc
    assert len(ALERTS) == 1, ALERTS
    subject, body = ALERTS[0]
    assert subject.startswith("E99: Phase 2 stopped") and "174.5 s" in subject, subject
    assert "exit -15" in body and "Not retried" in body, body


def test_phase2_failure_without_oom_evidence_still_retries():
    _events(0)
    ALERTS.clear()
    _step8()
    exc = _raises(lambda: publish_finalize._run_script(["bash", _script(oom=False)], _work_dir(), "transcribe_clips"))
    assert isinstance(exc, RuntimeError) and type(exc).__name__ != "PermanentFailure" and "exit -15" in str(exc), repr(
        exc
    )
    assert ALERTS == []


def test_off_the_box_an_exit_minus_15_is_not_called_an_oom():
    EVENTS.unlink(missing_ok=True)
    ALERTS.clear()
    _step8()
    exc = _raises(lambda: publish_finalize._run_script(["bash", _script(oom=False)], _work_dir(), "transcribe_clips"))
    assert isinstance(exc, RuntimeError) and type(exc).__name__ != "PermanentFailure", repr(exc)  # retried, not stopped
    assert ALERTS == []


def test_round1_oom_alerts_once_and_writes_the_standup():
    _events(0)
    ALERTS.clear()
    standups = []
    real = round_1._prepend_standup
    round_1._prepend_standup = lambda ep, err: standups.append((ep, err))
    try:
        run_ledger.bind(job_id=146, episode="E98", run_kind="eval")
        run_ledger.begin("round_1")
        run_ledger.step("5", "transcribe_fast")
        exc = _raises(lambda: round_1._run_script(["bash", _script(oom=True)], _work_dir(), "transcribe_fast"))
    finally:
        round_1._prepend_standup = real
    assert type(exc).__name__ == "PermanentFailure" and "step 5 transcribe_fast" in str(exc), repr(exc)
    assert len(ALERTS) == 1 and ALERTS[0][0].startswith("E98: Round 1 stopped"), ALERTS
    assert standups == [("E98", "out of memory in step 5 transcribe_fast")], standups


# ── the worker: one alert when retries run out, none before ──────────────────


class _FakeDb:
    def __init__(self):
        self.calls = []

    def mark_done(self, job_id):
        self.calls.append(("done", job_id))

    def mark_failed(self, job_id, error):
        self.calls.append(("failed", job_id))

    def reschedule(self, job_id, attempt, next_run, error):
        self.calls.append(("reschedule", job_id, attempt))


def _run_attempts(handler, attempts):
    """Run the real worker loop body on job 147 for the given attempt numbers, with a fake handler and db."""
    fake_db, real_db, real_handler = _FakeDb(), worker.db, worker._run_handler
    worker.db, worker._run_handler = fake_db, handler
    try:
        for attempt in attempts:
            worker._run_one(
                {"id": 147, "job_type": "PUBLISH_FINALIZE", "episode_id": "TBD", "attempt": attempt, "payload": "{}"}
            )
    finally:
        worker.db, worker._run_handler = real_db, real_handler
    return fake_db.calls


def _fails_in_step8(job_type, job, payload, episode_id):
    run_ledger.bind(episode="E99")  # the job resolves its code; the queue row still says "TBD"
    run_ledger.step("8", "transcribe_clips")
    raise RuntimeError("transcribe_clips failed (exit 3): whisperx")


def test_five_failed_attempts_send_exactly_one_alert_naming_the_step():
    ALERTS.clear()
    calls = _run_attempts(_fails_in_step8, range(5))
    assert [c[0] for c in calls] == ["reschedule"] * 4 + ["failed"], calls
    assert len(ALERTS) == 1, ALERTS
    subject, body = ALERTS[0]
    assert subject == "E99: PUBLISH_FINALIZE failed for good after 5 attempts — step 8 transcribe_clips", subject
    assert "Attempts: 5" in body and "whisperx" in body, body


def test_a_retry_that_succeeds_sends_no_alert():
    ALERTS.clear()
    state = {"n": 0}

    def flaky(job_type, job, payload, episode_id):
        state["n"] += 1
        if state["n"] == 1:
            _fails_in_step8(job_type, job, payload, episode_id)

    calls = _run_attempts(flaky, range(2))
    assert [c[0] for c in calls] == ["reschedule", "done"], calls
    assert ALERTS == []


def test_an_oom_through_the_worker_fails_once_with_one_alert():
    """E13's whole path: the job's step runs out of memory → no reschedule, one alert (the job's), not five FAQs."""
    _events(0)
    ALERTS.clear()

    def oom_in_step8(job_type, job, payload, episode_id):
        run_ledger.bind(episode="E99")
        run_ledger.step("8", "transcribe_clips")
        publish_finalize._run_script(["bash", _script(oom=True)], _work_dir(), "transcribe_clips")

    calls = _run_attempts(oom_in_step8, [0])
    assert calls == [("failed", 147)], calls
    assert len(ALERTS) == 1 and "out of memory in step 8 transcribe_clips" in ALERTS[0][0], ALERTS


def test_phase2_run_passes_an_oom_through_with_no_second_alert():
    _events(0)
    ALERTS.clear()
    real = publish_finalize._run_pipeline

    def pipeline(job_id, payload):
        _step8()
        publish_finalize._run_script(["bash", _script(oom=True)], _work_dir(), "transcribe_clips")

    publish_finalize._run_pipeline = pipeline
    try:
        exc = _raises(lambda: publish_finalize.run(147, "TBD", {}))
    finally:
        publish_finalize._run_pipeline = real
    assert type(exc).__name__ == "PermanentFailure" and len(ALERTS) == 1, (repr(exc), ALERTS)


def test_round1_run_passes_an_oom_through_with_one_standup_and_one_alert():
    _events(0)
    ALERTS.clear()
    standups = []
    real_pipeline, real_standup = round_1._run_pipeline, round_1._prepend_standup
    round_1._prepend_standup = lambda ep, err: standups.append((ep, err))

    def pipeline(job_id, payload):
        run_ledger.bind(job_id=146, episode="E98", run_kind="eval")
        run_ledger.begin("round_1")
        run_ledger.step("5", "transcribe_fast")
        round_1._run_script(["bash", _script(oom=True)], _work_dir(), "transcribe_fast")

    round_1._run_pipeline = pipeline
    try:
        exc = _raises(lambda: round_1.run(146, "TBD", {}))
    finally:
        round_1._run_pipeline, round_1._prepend_standup = real_pipeline, real_standup
    assert type(exc).__name__ == "PermanentFailure", repr(exc)
    assert len(ALERTS) == 1 and len(standups) == 1, (ALERTS, standups)


def test_no_catch_around_a_script_swallows_a_permanent_failure():
    """7a-0 wraps alignment in `except Exception` (alert and continue). Without a PermanentFailure re-raise ahead
    of it, an OOM in alignment would alert twice and carry on with guessed timing while systemd stops the unit."""
    import ast

    for path in (REPO / "rockstar" / "jobs" / "publish_finalize.py", REPO / "rockstar" / "jobs" / "round_1.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            runs_script = any(
                isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_run_script"
                for b in node.body
                for n in ast.walk(b)
            )
            if not runs_script:
                continue
            for h in node.handlers:
                caught = ast.unparse(h.type) if h.type else "BaseException"
                if caught == "PermanentFailure":
                    break  # it is handled (re-raised) before any broad catch
                if caught in ("Exception", "BaseException"):
                    assert any(isinstance(n, ast.Raise) for n in ast.walk(h)), (
                        f"{path.name}:{node.lineno} catches {caught} around _run_script before PermanentFailure"
                    )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
