"""
test_cards_only.py — the box rebuilds an episode's quote cards by itself (2026-10-13).

E34's cards stopped on a cover title (fixed by TITLE-02). The way to rebuild them was scripts/new-cards.sh, a Mac
path that broke on the OVH box (it reads .env as the deploy user). John: no manual fallback. So:
  reprocess_episode.py --episode 34 --cards-only
queues a Phase 2 that downloads, checks the titles (step 5c), builds the card set (step 12b) and stops: no FAQ,
blog, reels, thank-you, calendar or schedule change. No links needed.
"""

import ast
import os
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO))
STATE = Path(tempfile.mkdtemp(prefix="cards-only-"))
os.environ["RUN_LOG_PATH"] = str(STATE / "run_log.jsonl")
os.environ["STATE_DIR"] = str(STATE)

for _name in (
    "rockstar.clients.calendar_client",
    "rockstar.clients.drive_client",
    "rockstar.clients.gmail_client",
    "rockstar.clients.publisher_email",
    "rockstar.content.blog_publish",
    "rockstar.ops.workspace",
    "rockstar.service.db",
):
    _swap.setdefault(_name, types.ModuleType(_name))
sys.modules["rockstar.clients.gmail_client"].send_pipeline_alert = lambda to, subject, body: {}
if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    _swap.put("anthropic", _a)

from rockstar.jobs import publish_finalize as pf  # noqa: E402


def _cli(*argv):
    from rockstar.tools import reprocess_episode as rp

    calls = []
    rp.db = types.SimpleNamespace(enqueue=lambda kind, ep, payload: calls.append(payload) or 7)
    sys.argv = ["reprocess_episode.py", *argv]
    try:
        rp.main()
        return 0, calls
    except SystemExit as e:
        return e.code, calls


def test_the_cli_queues_a_cards_only_run_without_links():
    code, calls = _cli("--episode", "34", "--cards-only")
    assert code == 0 and calls[0]["cards_only"] is True and calls[0]["reels_only"] is False, (code, calls)
    code, calls = _cli("--episode", "34", "--cards-only", "--reels-only")
    assert code == 2 and calls == [], "two narrow runs at once is a mistake, not a merge"
    code, calls = _cli("--episode", "34")
    assert code == 2 and calls == [], "a full reprocess still needs the links"
    code, calls = _cli("--episode", "34", "--apple", "a", "--spotify", "s")
    assert code == 0 and calls[0]["cards_only"] is False, calls


def test_phase_2_refuses_cards_only_with_reels_only():
    try:
        pf._run_pipeline(1, {"trigger": "reprocess", "episode_number": 99, "cards_only": True, "reels_only": True})
        raise AssertionError("no refusal")
    except pf.PermanentFailure as exc:
        assert "separate runs" in str(exc), exc


def _cards_only_block():
    src = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_produce_content")
    block = next(n for n in ast.walk(fn) if isinstance(n, ast.If) and ast.unparse(n.test) == "cards_only")
    return src, block


def test_a_cards_only_run_checks_the_titles_builds_the_cards_and_stops():
    src, block = _cards_only_block()
    body = "\n".join(ast.unparse(n) for n in block.body)
    assert body.index("_fix_quote_titles(") < body.index("card_step.build_cards("), "titles first: the cards print them"
    assert isinstance(block.body[-1], ast.Return), "it stops after the cards"
    for other in (
        "_compose_and_upload_faq",
        "render_reel",
        "rockstar.content.thank_you",
        "create_pipeline_done_marker",
        "rockstar.clients.calendar_client",
        "_write_json",
    ):
        assert other not in body, f"a cards-only run touches nothing else ({other})"
    seg = ast.get_source_segment(src, block)
    assert src.index(seg) < src.index('run_ledger.step("5b", "clip_ceiling")'), "before any Phase 2 spend"
    assert src.index("tile_local = work_dir") < src.index(seg), "after the images are downloaded"


def test_the_full_run_uses_the_same_title_step():
    src = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    full = src[src.index('run_ledger.step("5b", "clip_ceiling")') : src.index('run_ledger.step("6", "faq_blog")')]
    assert "_fix_quote_titles(" in full and "quote_title.ensure(" not in full, "one title step, two callers"


# ── a cards-only run, played against fakes (Toby, #74 T1) ─────────────────────
SCHEDULE = {
    "episodes": [
        {
            "episode_number": 99,
            "episode_code": "E99",
            "season_episode_code": "S1E99",
            "slug": "jane",
            "drive_folder_id": "f99",
            "episode_title": "Rest Is Part of the Work | An Invented Episode",
            "guest_name": "Jane Invented",
            "guest_email": "",
            "status": "done",
        }
    ]
}
QUOTES = {
    "quotes": [
        {
            "id": "q01",
            "speaker": "Jane Invented",
            "heading": "Rest Belongs in the Workday",
            "text": "Rest is not a reward, it is part of the work I do every single day.",
        }
    ]
}
SUMMARY = "Rest Is Part of the Work | An Invented Episode\n\nHeather sits down with Jane Invented to talk about rest.\n"


def _play(files):
    """Run a cards-only job against fake Drive, Gmail and card step; return what each was asked to do."""
    import json

    (STATE / "publication_schedule.json").write_text(json.dumps(SCHEDULE))
    seen = {"downloads": [], "uploads": [], "alerts": [], "cards": [], "writes": [], "standups": []}
    drive = types.ModuleType("drive_client")
    drive.list_folder = lambda fid: [dict(f) for f in files]
    drive.find_episode_folder = lambda code: None

    def download(fid, dest):
        seen["downloads"].append(fid)
        Path(dest).write_text({"q": json.dumps(QUOTES), "s": SUMMARY}.get(fid, "image"))
        return Path(dest)

    drive.download_file = download
    drive.upload_file = lambda *a, **k: seen["uploads"].append(a) or {"id": "n"}
    drive.trash_file = lambda fid: seen["uploads"].append(("trash", fid))
    pf.drive_client = drive
    pf.gmail_client.send_pipeline_alert = lambda to, subject, body: seen["alerts"].append(subject) or {}
    pf.workspace.make_work_dir = lambda prefix: Path(tempfile.mkdtemp(prefix=prefix))
    real_write = pf._write_json
    pf._write_json = lambda path, data: seen["writes"].append(str(path)) or real_write(path, data)
    pf._prepend_standup = lambda code, msg: seen["standups"].append(msg)
    pf.card_step.build_cards = lambda **kw: seen["cards"].append(kw) or "built: 1 slides, 1 covers, 1 captions"
    try:
        pf._run_pipeline(1, {"trigger": "reprocess", "episode_number": 99, "cards_only": True})
        seen["result"] = "done"
    except pf.PermanentFailure as exc:
        seen["result"] = f"failed: {exc}"
    finally:
        pf._write_json = real_write
    seen["schedule_unchanged"] = json.loads((STATE / "publication_schedule.json").read_text()) == SCHEDULE
    return seen


IMAGES = [
    {"id": "t", "name": "E99-tile-jane.jpg", "mimeType": "image/jpeg"},
    {"id": "r", "name": "E99-reel-jane.jpg", "mimeType": "image/jpeg"},
]
DOCS = [
    {"id": "q", "name": "quotes.json", "mimeType": "application/json"},
    {"id": "s", "name": "apple_summary.md", "mimeType": "text/markdown"},
    {"id": "x", "name": "transcript_fast.txt", "mimeType": "text/plain"},
    {"id": "a", "name": "E99-jane.m4a", "mimeType": "audio/x-m4a"},
]


def test_a_cards_only_run_builds_the_cards_once_and_writes_nothing_else():
    seen = _play(IMAGES + DOCS)
    assert seen["result"] == "done", seen
    assert len(seen["cards"]) == 1 and seen["cards"][0]["episode_number"] == 99, seen["cards"]
    assert "a" not in seen["downloads"], "the audio is never downloaded"
    assert seen["writes"] == [] and seen["schedule_unchanged"], seen["writes"]
    assert seen["uploads"] == [] and seen["alerts"] == [], "a passing title saves nothing; no alert"


def test_a_cards_only_run_with_no_images_changes_nothing():
    seen = _play(DOCS)
    assert seen["result"].startswith("failed") and "Nothing was changed" in seen["result"], seen["result"]
    assert seen["writes"] == [] and seen["schedule_unchanged"], "never parked as waiting_for_visuals"
    assert seen["alerts"] == [] and seen["cards"] == [], seen


def test_a_cards_only_run_whose_cards_fail_fails_the_job():
    real = pf.card_step.build_cards
    seen = _play(IMAGES + DOCS)  # first, to install the fakes
    pf.card_step.build_cards = lambda **kw: "failed: the render stage failed"
    import json

    (STATE / "publication_schedule.json").write_text(json.dumps(SCHEDULE))
    try:
        pf._run_pipeline(1, {"trigger": "reprocess", "episode_number": 99, "cards_only": True})
        raise AssertionError("a failed card set must fail the job")
    except pf.PermanentFailure as exc:
        assert "the render stage failed" in str(exc), exc
    finally:
        pf.card_step.build_cards = real
    assert seen["result"] == "done"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ok  ", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("  FAIL", name, f"{type(exc).__name__}: {exc}")
    sys.exit(1 if failed else 0)
