"""
test_obs02_caught_failures.py — a failure the pipeline catches and carries on from is never recorded "ok" (OBS-02).

E6, 2026-10-12: the ledger says `12b quote_cards ok`; no cards were built. card_step alerts and carries on by design
(CARD-05), and the step closed "ok" when the next one started. Here every catch-and-carry-on site records an anomaly,
held to its step's real end; a failed alert send is recorded; and a guard test refuses the next silent catch.
The Google-backed modules are stand-ins (as in test_trig02); run_ledger and the job code are real.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix="obs02-"))
LEDGER = TMP / "run_log.jsonl"
os.environ["RUN_LOG_PATH"] = str(LEDGER)

for _name in (
    "rockstar.clients.calendar_client",
    "rockstar.clients.drive_client",
    "rockstar.clients.gmail_client",
    "rockstar.clients.publisher_email",
    "rockstar.content.blog_publish",
    "rockstar.ops.workspace",
):
    _swap.put(_name, types.ModuleType(_name))
if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    _swap.put("anthropic", _a)

from rockstar.jobs import publish_finalize as pf  # noqa: E402
from rockstar.ops import run_ledger  # noqa: E402


def _boom(*a, **k):
    raise RuntimeError("simulated outage")


def _steps():
    if not LEDGER.exists():
        return []
    return [e for e in map(json.loads, LEDGER.read_text().splitlines()) if e.get("event") == "step"]


def _in_step(step, name, fn):
    """Run fn inside a step, start the next one (which closes it, as the pipeline does), return the step's event."""
    LEDGER.unlink(missing_ok=True)
    run_ledger.bind(job_id=6, episode="E99", run_kind="eval")
    run_ledger.begin("publish_finalize")
    run_ledger.step(step, name)
    fn()
    run_ledger.step("99", "next")
    run_ledger.end()
    return next(e for e in _steps() if e["step"] == step)


# ── AC 1, 2: the ledger holds a degraded outcome to the step's real end ───────


def test_an_anomaly_is_held_until_the_step_ends():
    import time

    def work():
        run_ledger.anomaly("subfolder not listed")
        time.sleep(0.05)  # the rest of the step (step 4's downloads) still runs

    e = _in_step("4", "download", work)
    assert (e["outcome"], e["detail"]) == ("anomaly", "subfolder not listed"), e
    assert e["duration_ms"] >= 50, e  # whole, not cut at the anomaly


def test_the_first_reason_wins_and_a_failure_outranks_it():
    def two():
        run_ledger.anomaly("subfolder not listed")
        run_ledger.anomaly("Drive file not deleted")

    assert _in_step("4", "download", two)["detail"] == "subfolder not listed"

    LEDGER.unlink(missing_ok=True)
    run_ledger.begin("publish_finalize")
    run_ledger.step("8", "transcribe_clips")
    run_ledger.anomaly("subfolder not listed")
    run_ledger.end(RuntimeError("whisperx"))
    (e,) = _steps()
    assert (e["outcome"], e["detail"]) == ("error", "RuntimeError"), e


def test_a_step_that_ends_skipped_after_an_anomaly_is_an_anomaly():
    def work():
        run_ledger.anomaly("calendar not updated")
        run_ledger.skipped("nothing to do")

    assert _in_step("14", "calendar", work)["outcome"] == "anomaly"


def test_a_clean_step_is_still_ok():
    assert _in_step("12", "upload", lambda: None)["outcome"] == "ok"


# ── AC 3, 4: each catch-and-carry-on site records its anomaly ─────────────────


def test_12b_failed_cards_are_an_anomaly_naming_the_stage():
    e = _in_step("12b", "quote_cards", lambda: pf._record_cards_status("failed: the render stage failed"))
    assert (e["outcome"], e["detail"]) == ("anomaly", "cards not built: render"), e
    e = _in_step("12b", "quote_cards", lambda: pf._record_cards_status("failed: the cover titles stage failed"))
    assert e["detail"] == "cards not built: cover titles", e
    e = _in_step("12b", "quote_cards", lambda: pf._record_cards_status("failed: an unexpected error"))
    assert e["detail"] == "cards not built", e
    e = _in_step("12b", "quote_cards", lambda: pf._record_cards_status("built: 10 slides, 2 covers, 2 captions"))
    assert e["outcome"] == "ok", e
    e = _in_step("12b", "quote_cards", lambda: pf._record_cards_status("skipped: cards/ not empty"))
    assert e["outcome"] == "skipped", e


def test_4_an_unlisted_subfolder_is_an_anomaly():
    folder = [{"id": "s1", "name": "visuals", "mimeType": "application/vnd.google-apps.folder"}]
    e = _in_step("4", "download", lambda: pf._gather_files(folder, _boom))
    assert (e["outcome"], e["detail"]) == ("anomaly", "subfolder not listed"), e


def test_4_starts_before_the_subfolder_listing():
    """Toby's blocker: _gather_files ran under step 3, so its anomaly landed on 3 visuals. In the real pipeline the
    step-4 heading must come first."""
    src = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)]
    step4 = min(n.lineno for n in calls if ast.unparse(n).startswith("run_ledger.step('4'"))
    gather = min(n.lineno for n in calls if ast.unparse(n.func) == "_gather_files")
    assert step4 < gather, (step4, gather)


def test_13_an_unfingerprinted_draft_is_an_anomaly():
    sys.modules["rockstar.clients.gmail_client"].draft_state = _boom
    e = _in_step("13", "thank_you", lambda: pf._record_ty_draft(99, {"id": "d1"}))
    assert (e["outcome"], e["detail"]) == ("anomaly", "thank-you not fingerprinted"), e


def test_14_a_failed_calendar_update_is_an_anomaly():
    sys.modules["rockstar.clients.calendar_client"].mark_episode_live = _boom
    e = _in_step("14", "calendar", lambda: pf._mark_calendar_live("ev1", "a", "s", "E99"))
    assert (e["outcome"], e["detail"]) == ("anomaly", "calendar not updated"), e


def test_a_guest_name_missing_from_the_summary_is_an_anomaly():
    real = pf.blog_post.parse_apple_summary
    pf.blog_post.parse_apple_summary = _boom
    try:
        e = _in_step("4", "download", lambda: pf._guest_from_summary("Guest", "summary", "E99"))
    finally:
        pf.blog_post.parse_apple_summary = real
    assert (e["outcome"], e["detail"]) == ("anomaly", "guest name not in summary"), e


def test_a_drive_file_not_deleted_is_an_anomaly():
    disc = types.ModuleType("googleapiclient.discovery")
    files = types.SimpleNamespace(delete=lambda fileId: types.SimpleNamespace(execute=_boom))
    disc.build = lambda *a, **k: types.SimpleNamespace(files=lambda: files)
    _swap.put("googleapiclient", types.ModuleType("googleapiclient"))
    _swap.put("googleapiclient.discovery", disc)
    auth = types.ModuleType("google_auth")
    auth.drive_credentials = lambda: None
    _swap.put("rockstar.clients.google_auth", auth)
    e = _in_step("12", "upload", lambda: pf._drive_delete_all_by_name([{"id": "f1", "name": "reels"}], "reels"))
    assert (e["outcome"], e["detail"]) == ("anomaly", "Drive file not deleted"), e


# ── AC 5: an alert that never went out is recorded ────────────────────────────


def test_a_failed_alert_is_recorded_without_its_text_and_the_job_carries_on():
    LEDGER.unlink(missing_ok=True)
    sys.modules["rockstar.clients.gmail_client"].send_pipeline_alert = _boom
    pf._alert("E99: quote cards not built (the render stage failed)", "a body that must not be recorded")
    (e,) = [e for e in map(json.loads, LEDGER.read_text().splitlines()) if e["event"] == "alert_failed"]
    assert (e["site"], e["error"]) == ("publish_finalize._alert", "RuntimeError"), e
    assert "render" not in json.dumps(e) and "body" not in json.dumps(e), e  # a hash, never the subject or body
    assert e["subject_sha"] == run_ledger.sha256("E99: quote cards not built (the render stage failed)")


def test_a_failed_alert_names_the_step_it_was_about():
    """E6's question: did 12b's card alert go out? The record names the step."""
    LEDGER.unlink(missing_ok=True)
    sys.modules["rockstar.clients.gmail_client"].send_pipeline_alert = _boom
    run_ledger.begin("publish_finalize")
    run_ledger.step("12b", "quote_cards")
    pf._alert("E99: quote cards not built (the render stage failed)", "body")
    run_ledger.end()
    (e,) = [e for e in map(json.loads, LEDGER.read_text().splitlines()) if e["event"] == "alert_failed"]
    assert e["step"] == "12b quote_cards", e


def test_failed_oom_and_dead_job_alerts_are_recorded():
    """CLIP-01a added two sends with their own catches (Round 1's OOM alert, the worker's dead-job alert)."""
    from rockstar.jobs import round_1
    from rockstar.service import worker

    LEDGER.unlink(missing_ok=True)
    sys.modules["rockstar.clients.gmail_client"].send_pipeline_alert = _boom
    episode = run_ledger.episode() or "TBD"
    step = run_ledger.failed_step() or "(outside a step)"
    round_1._alert_oom("out of memory in step 5 transcribe_fast", -15)
    worker._alert_dead_job({"id": 147, "job_type": "PUBLISH_FINALIZE", "episode_id": "TBD"}, 5, "RuntimeError: x")
    got = [
        (e["site"], e["subject_sha"])
        for e in map(json.loads, LEDGER.read_text().splitlines())
        if e["event"] == "alert_failed"
    ]
    assert [g[0] for g in got] == ["round_1.oom", "worker.dead_job"], got
    # the hashes are of the subjects actually sent, so each can be matched to the alert that never went out
    assert got[0][1] == run_ledger.sha256(f"{episode}: Round 1 stopped — out of memory in step 5 transcribe_fast"), got
    assert got[1][1] == run_ledger.sha256(
        f"{episode}: PUBLISH_FINALIZE failed for good after 5 attempts — step {step}"
    ), got


# ── AC 6: the next silent catch fails this test ───────────────────────────────

# (file, enclosing function, exception caught) → why it may carry on without recording. Matched by COUNT: a second
# silent `except Exception` in an allowlisted function is a new catch, and it fails (Toby, OBS-02 review).
ALLOWED = {
    (
        "card_step.py",
        "_playwright_platform_override",
        "OSError",
    ): "a platform probe, not a failure: None means no override",
    ("card_step.py", "build_cards", "_StageFailed"): "returns 'failed: …'; the caller records it at 12b",
    ("card_step.py", "build_cards", "Exception"): "returns 'failed: …'; the caller records it at 12b",
    (
        "publish_finalize.py",
        "_swap_ty_draft",
        "gmail_client.DraftGone",
    ): "an expected state (sent or deleted), told in the swap alert",
    ("round_1.py", "run", "Exception"): "a best-effort episode label inside a handler that re-raises",
}
RECORDS = {"anomaly", "skipped", "alert_failed"}
_NESTED = (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(handler):
    """The handler's own body: never a nested handler's, function's or class's, which would lend it their raise or
    their record (7a-0's alert-send catch would cover for 7a-0's own)."""
    stack = list(handler.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _NESTED))


def _records(node) -> bool:
    f = getattr(node, "func", None)
    return (
        isinstance(node, ast.Call)
        and isinstance(f, ast.Attribute)
        and f.attr in RECORDS
        and isinstance(f.value, ast.Name)
        and f.value.id == "run_ledger"
    )


def _silent_catches():
    """Every handler in jobs/ (functions, classes and module level) that neither raises nor records.
    Known limits: a raise in one branch (`if x: raise`) counts as a raise, and so does a raise caught again inside
    the handler's own body (`except: try: raise X / except X: pass`). Both are rare; neither exists in jobs/ today."""
    found = []
    for path in sorted((REPO / "rockstar" / "jobs").glob("*.py")):
        tree = ast.parse(path.read_text())
        owner = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                owner[child] = parent
        for h in ast.walk(tree):
            if not isinstance(h, ast.ExceptHandler):
                continue
            own = list(_own_nodes(h))
            if any(isinstance(n, ast.Raise) for n in own) or any(_records(n) for n in own):
                continue
            fn = owner.get(h)
            while fn is not None and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                fn = owner.get(fn)
            found.append(
                (
                    path.name,
                    fn.name if fn is not None else "<module>",
                    ast.unparse(h.type) if h.type else "BaseException",
                    h.lineno,
                )
            )
    return found


def test_every_catch_in_jobs_records_or_reraises_or_is_allowed():
    from collections import Counter

    seen = Counter(c[:3] for c in _silent_catches())
    extra = {k: n - (1 if k in ALLOWED else 0) for k, n in seen.items() if n > (1 if k in ALLOWED else 0)}
    where = [f"{f}:{line} {fn}() except {exc}" for f, fn, exc, line in _silent_catches() if (f, fn, exc) in extra]
    assert not extra, "a caught failure the ledger would record as ok:\n  " + "\n  ".join(where)


def test_the_guard_sees_through_a_nested_record():
    """Toby's OBS-02 probe, kept after REEL-11 removed 7a-0's catch: an outer catch that records nothing must not
    borrow the alert_failed of an alert-send catch nested inside it."""
    src = """
def step():
    try:
        work()
    except Exception as exc:
        log(exc)
        try:
            send()
        except Exception as alert_exc:
            run_ledger.alert_failed("site", "subject", alert_exc)
"""
    outer = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ExceptHandler) and n.name == "exc")
    own = list(_own_nodes(outer))
    assert not any(_records(n) or isinstance(n, ast.Raise) for n in own)


def test_the_allowlist_has_no_stale_lines():
    present = {c[:3] for c in _silent_catches()}
    stale = [k for k in ALLOWED if k not in present]
    assert not stale, f"allowlist lines that match no catch (remove them): {stale}"


# ── AC 7: the summary counts anomalies up top ─────────────────────────────────


def test_ledger_summary_counts_anomalies_and_lists_failed_alerts():
    _in_step("12b", "quote_cards", lambda: pf._record_cards_status("failed: the render stage failed"))
    run_ledger.alert_failed("publish_finalize._alert", "E99: x", RuntimeError("503"))
    out = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "rockstar.tools.ledger_summary",
            "E99",
            "--ledger",
            str(LEDGER),
            "--kind",
            "eval",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "(1 anomaly)" in out.stdout and "cards not built: render" in out.stdout, out.stdout
    assert "Alerts that never went out — 1" in out.stdout, out.stdout


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
