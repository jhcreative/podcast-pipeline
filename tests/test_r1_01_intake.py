"""
test_r1_01_intake.py — R1-01: one definition of "episode audio", and a loud failure when audio is stranded.
Also: an old episode the pipeline never saw gets its real season (E19 → S1E19, not S2E19).

Run: python3 -B test_r1_01_intake.py   (plain asserts, no pytest needed)

AC-1 one predicate, every gate          → test_matrix_*, test_one_extension_list
AC-2 stranded audio fails loud + alerts → test_stranded_*  (the REAL round_1._run_pipeline, title01's harness)
AC-3 bare "E30.m4a" policy = (a) accept, safe slug → test_matrix_jen, test_slug_never_untitled
SEASON old episode, no schedule entry   → test_season_*
"""

import sys
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))

import test_title01_gen_gate as t1  # noqa: E402  (its Round-1 harness: real _run_pipeline, I/O stubbed)

from rockstar.content import episode_files as ef  # noqa: E402

# Jen's matrix from the story: filename → processable?
MATRIX = {
    "E30-perimenopause-jane.m4a": True,
    "E30_perimenopause_jane.m4a": True,
    "E30.m4a": True,  # policy (a): accepted, safe slug
    "E30 perimenopause jane.m4a": True,  # space separator accepted
    "E30-.m4a": True,  # separator + empty slug: safe slug
    "random-voice-memo.m4a": False,  # no E-number → loud
    "E30-guest.mp3": False,  # audio transcription can't read → loud
    "E300x.m4a": False,
    "Episode 30.m4a": False,
}


def test_matrix_jen():
    bad = {n: ef.is_processable(n) for n, want in MATRIX.items() if ef.is_processable(n) != want}
    assert not bad, bad


def test_matrix_non_audio_ignored():
    assert not ef.is_audio("E30-notes.txt") and ef.problem("E30-notes.txt") != ""  # problem() only used for audio
    assert ef.is_audio("random-voice-memo.m4a") and ef.is_audio("x.bin", "audio/mpeg")


def test_problem_hints_are_actionable():
    assert "rename it to 'E33-<guest>.m4a'" in ef.problem("random-voice-memo.m4a", 33)
    assert "export it as '.m4a'" in ef.problem("E30-guest.mp3")
    assert ef.problem("E30-guest.m4a") == ""


def test_one_extension_list():
    import ast

    for mod in ("rockstar/clients/drive_changes.py", "rockstar/clients/drive_client.py", "rockstar/jobs/round_1.py"):
        src = (REPO / mod).read_text()
        tree = ast.parse(src)
        literal_sets = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "_AUDIO_EXTENSIONS" for t in n.targets)
            and isinstance(n.value, (ast.Set, ast.Call))
        ]
        assert not literal_sets, f"{mod} defines its own _AUDIO_EXTENSIONS; import episode_files instead"


def test_slug_never_untitled():
    r1 = _r1()
    assert r1._derive_slug("E30.m4a") == "episode"
    assert r1._derive_slug("E30-perimenopause-jane.m4a") == "perimenopause-jane"


def test_season_current_and_previous():
    r1 = _r1()
    counter = {"next_episode_number": 33, "next_season_episode": 8, "current_season": 2}
    assert r1._season_for(32, counter) == (2, 7)  # E32 is S2E7 (as the pipeline logged it)
    assert r1._season_for(26, counter) == (2, 1)
    assert r1._season_for(25, counter) == (1, 25)
    assert r1._season_for(19, counter) == (1, 19)


def test_season_refuses_to_guess_two_seasons_back():
    r1 = _r1()
    try:
        r1._season_for(5, {"next_episode_number": 60, "next_season_episode": 3, "current_season": 3})
    except r1.PermanentFailure:
        return
    raise AssertionError("an episode two seasons back must be refused, not guessed")


# ── AC-2 on the real pipeline ─────────────────────────────────────────────────
def _r1():
    state = t1._fresh_state()
    r1, _ = t1._load_round1_with_stubs(state, {"title": "A | B", "hook": "A", "topic": "B"})
    return r1


def _run_with_inbox(names):
    state = t1._fresh_state()
    r1, drive = t1._load_round1_with_stubs(state, {"title": "A | B", "hook": "A", "topic": "B"})
    t1._patch_scripts(r1)
    drive.list_audio_in_inbox = lambda: [{"id": f"id-{i}", "name": n} for i, n in enumerate(names)]
    drive.find_episode_folder = lambda code: None
    alerts = []
    gm = types.ModuleType("gmail_client")
    gm.send_pipeline_alert = lambda **k: alerts.append(k)
    _swap.put("rockstar.clients.gmail_client", gm)
    try:
        r1._run_pipeline(job_id=1, payload={})
    except r1.PermanentFailure as exc:
        return "failed", str(exc), alerts, state
    return "done", "", alerts, state


def test_stranded_unnamed_audio_fails_loud():
    status, err, alerts, _ = _run_with_inbox(["random-voice-memo.m4a"])
    assert status == "failed", "a job that strands inbox audio must not finish as done"
    assert "random-voice-memo.m4a" in err and "rename it to 'E30-<guest>.m4a'" in err, err
    assert len(alerts) == 1 and "random-voice-memo.m4a" in alerts[0]["body"], alerts


def test_stranded_wrong_format_fails_loud():
    status, err, alerts, _ = _run_with_inbox(["E30-guest.mp3"])
    assert status == "failed" and "export it as '.m4a'" in err, err
    assert alerts, "an alert must be sent"


def test_stranded_waiting_later_episode_fails_loud():
    status, err, alerts, _ = _run_with_inbox(["E31-next-guest.m4a"])  # counter is at E30
    assert status == "failed" and "E31-next-guest.m4a" in err and "waiting" in err, err


def test_empty_inbox_is_a_clean_no_op():
    status, err, alerts, _ = _run_with_inbox([])
    assert status == "done" and not alerts, (status, err, alerts)


def test_bare_e_number_processes():
    status, err, alerts, state = _run_with_inbox(["E30.m4a"])
    assert status == "done", err
    sched = t1._read_schedule(state)
    assert sched["episodes"] and sched["episodes"][0]["slug"] == "episode", sched


def test_reprocess_chains_to_the_next_old_episode():
    state = t1._fresh_state()
    r1, drive = t1._load_round1_with_stubs(state, {"title": "A | B", "hook": "A", "topic": "B"})
    t1._patch_scripts(r1)
    inbox = [{"id": "e6", "name": "E6-Tamsin.m4a"}, {"id": "e13", "name": "E13-heather-john.m4a"}]
    drive.list_audio_in_inbox = lambda: list(inbox)
    drive.move_file = lambda fid, dest: inbox.remove(next(f for f in inbox if f["id"] == fid))
    drive.find_episode_folder = lambda code: None
    enq = []
    r1.db.enqueue = lambda *a, **k: enq.append((a, k)) or 77
    r1._run_pipeline(job_id=1, payload={})
    assert [f["name"] for f in inbox] == ["E13-heather-john.m4a"], inbox  # E6 processed and moved
    assert len(enq) == 1 and enq[0][0][0] == "ROUND_1", enq  # E13 chained, not stranded
    sched = t1._read_schedule(state)
    e6 = [e for e in sched["episodes"] if e["episode_number"] == 6]
    assert e6 and e6[0]["season"] == 1 and e6[0]["season_episode"] == 6, sched


def test_reprocess_reopens_a_finished_entry_for_the_visuals_trigger():
    """E6/E13, 2026-10-11: a back-catalogue episode with an old schedule entry (no slug, pipeline_done) ran Round 1,
    but the entry kept slug '' and pipeline_done=True, so dropping the visuals never started Phase 2 (TRIG-02 matches
    the folder's slug and skips a finished entry). Round 1 re-run means Phase 2 is wanted again."""
    import json

    state = t1._fresh_state()
    old = {
        "episode_number": 6,
        "season": 1,
        "season_episode": 6,
        "episode_code": "E6",
        "slug": "",
        "folder_name": "E6",
        "status": "done",
        "pipeline_done": True,
        "apple_url": "https://podcasts.apple.com/x?i=1",
        "spotify_url": "https://open.spotify.com/episode/y",
    }
    (state / "publication_schedule.json").write_text(json.dumps({"episodes": [old]}), encoding="utf-8")
    r1, drive = t1._load_round1_with_stubs(state, {"title": "A | B", "hook": "A", "topic": "B"})
    t1._patch_scripts(r1)
    drive.list_audio_in_inbox = lambda: [{"id": "e6", "name": "E6-Tamsin.m4a"}]
    drive.find_episode_folder = lambda code: None
    r1._run_pipeline(job_id=1, payload={})
    (e6,) = [e for e in t1._read_schedule(state)["episodes"] if e["episode_number"] == 6]
    assert e6["slug"] == "tamsin" and e6["folder_name"] == "E6-tamsin", e6  # the folder TRIG-02 will see
    assert e6["pipeline_done"] is False and e6["status"] == "reprocessed", e6
    assert e6["apple_url"] == old["apple_url"] and e6["spotify_url"] == old["spotify_url"], e6  # known links kept


def _reprocess_with_folder(folder):
    import json

    state = t1._fresh_state()
    old = {
        "episode_number": 6,
        "season": 1,
        "season_episode": 6,
        "episode_code": "E6",
        "slug": "",
        "folder_name": "E6",
        "status": "done",
        "pipeline_done": True,
    }
    (state / "publication_schedule.json").write_text(json.dumps({"episodes": [old]}), encoding="utf-8")
    r1, drive = t1._load_round1_with_stubs(state, {"title": "A | B", "hook": "A", "topic": "B"})
    t1._patch_scripts(r1)
    drive.list_audio_in_inbox = lambda: [{"id": "e6", "name": "E6-Tamsin.m4a"}]
    drive.find_episode_folder = lambda code: folder
    r1._run_pipeline(job_id=1, payload={})
    (e6,) = [e for e in t1._read_schedule(state)["episodes"] if e["episode_number"] == 6]
    return e6


def test_reprocess_into_a_reused_folder_records_the_folder_as_drive_has_it():
    """Toby's cold review of #42: a reused folder keeps its own name, which need not match the audio's."""
    for name in ("E6", "E6-tamsin-smith"):
        e6 = _reprocess_with_folder({"id": "f6", "name": name})
        assert e6["folder_name"] == name and e6["drive_folder_id"] == "f6", e6
        assert e6["slug"] == ef.folder_slug(name), e6  # exactly what the visuals trigger will compute
        assert e6["pipeline_done"] is False, e6


def test_episode_folder_match_is_exact():
    assert ef.is_episode_folder("E1", "E1") and ef.is_episode_folder("E1-guest", "E1")
    assert ef.is_episode_folder("E6-jane", "E6-")  # card_upload passes "E6-"
    assert not ef.is_episode_folder("E13-heather-john", "E1")
    assert not ef.is_episode_folder("E16-x", "E1") and not ef.is_episode_folder("E61-x", "E6-")
    assert ef.folder_slug("E6-jane") == "jane" and ef.folder_slug("E13-Heather-John") == "heather-john"
    assert ef.folder_slug("E6") == "e6"


def test_drive_finds_only_the_exact_episode_folder():
    """Drive's `contains 'E1'` returns E13's folder first; find_episode_folder must skip it."""
    import importlib
    import os

    os.environ.setdefault("DRIVE_EPISODES_FOLDER_ID", "parent")
    _swap.pop("rockstar.clients.drive_client", None)
    for mod in (
        "googleapiclient",
        "googleapiclient.discovery",
        "googleapiclient.errors",
        "googleapiclient.http",
        "rockstar.clients.google_auth",
    ):
        if mod not in sys.modules:
            m = types.ModuleType(mod)
            m.build = m.HttpError = m.MediaFileUpload = m.MediaIoBaseDownload = m.drive_credentials = object
            _swap.put(mod, m)
    dc = importlib.import_module("rockstar.clients.drive_client")
    listed = [{"id": "f13", "name": "E13-heather-john"}, {"id": "f1", "name": "E1-guest"}]
    files = types.SimpleNamespace(list=lambda **k: types.SimpleNamespace(execute=lambda: {"files": listed}))
    dc._svc = lambda: types.SimpleNamespace(files=lambda: files)
    assert dc.find_episode_folder("E1")["id"] == "f1"
    listed[:] = [{"id": "f13", "name": "E13-heather-john"}]
    assert dc.find_episode_folder("E1") is None
    _swap.pop("rockstar.clients.drive_client", None)


def test_target_matches_by_number_not_prefix():
    status, err, alerts, state = _run_with_inbox(["E301-x.m4a", "E30-real.m4a"])  # counter at E30
    sched = t1._read_schedule(state)
    assert status == "done" and sched["episodes"][0]["original_filename"] == "E30-real.m4a", (status, err, sched)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    print("R1-01 intake + season — test run")
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
