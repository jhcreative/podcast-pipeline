"""
test_rerun_brand_team.py — rockstar/tools/rerun_brand_team.py picks quotes through the SAME check as Round 1 (2026-10-12).

It ran no verbatim or length check after QUOTE-01 and CLIP-01c, and queued Phase 2 by itself. Now:
- a re-run that leaves no quote standing changes nothing on Drive;
- the quotes it uploads carry the check's report (tape_check, lengths);
- Phase 2 is queued only with --enqueue.
Drive, the model and the job queue are fakes; quote_gate, quote_tape, clip_limits and content_gates are real.
Every name and transcript here is invented.
"""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
STATE = Path(tempfile.mkdtemp(prefix="rerun-bt-"))
os.environ["STATE_DIR"] = str(STATE)
os.environ["RUN_LOG_PATH"] = str(STATE / "run_log.jsonl")
os.environ.setdefault("BOX_LIMITS_FILE", str(REPO / "tests" / "fixtures" / "box-limits.env"))
os.environ.setdefault("BOX_LIMITS_TEST", "1")
(STATE / "publication_schedule.json").write_text(
    json.dumps(
        {
            "episodes": [
                {
                    "episode_number": 99,
                    "episode_code": "E99",
                    "drive_folder_id": "folder-99",
                    "guest_name": "Jane Invented",
                    "slug": "jane",
                }
            ]
        }
    )
)

SEGMENTS = {
    "segments": [
        {"start": 0, "end": 10, "speaker": "SPEAKER_00", "text": "Welcome back, today my guest is Jane Invented."},
        {
            "start": 10,
            "end": 20,
            "speaker": "SPEAKER_01",
            "text": "Every night I put the phone in another room and the evening slows down.",
        },
        {
            "start": 20,
            "end": 30,
            "speaker": "SPEAKER_01",
            "text": "Rest is not a reward, it is part of the work I do every single day.",
        },
    ]
}

uploads, enqueued, trashed = [], [], []
drive = types.ModuleType("drive_client")
drive.list_folder = lambda fid: [
    {"id": "t-txt", "name": "transcript_fast.txt", "mimeType": "text/plain"},
    {"id": "t-json", "name": "transcript_fast.json", "mimeType": "application/json"},
    {"id": "old-quotes", "name": "quotes.json", "mimeType": "application/json"},
]
drive.trash_file = lambda fid: trashed.append(fid)


def _download(fid, dest):
    dest = Path(dest)
    dest.write_text("transcript text" if fid == "t-txt" else json.dumps(SEGMENTS))
    return dest


drive.download_file = _download
drive.upload_file = lambda path, folder, name, mime: uploads.append(json.loads(Path(path).read_text()))
db = types.ModuleType("db")
db.enqueue = lambda *a, **k: enqueued.append(a) or 7
_swap.put("rockstar.clients.drive_client", drive)
_swap.put("rockstar.service.db", db)
if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    _swap.put("anthropic", _a)

from rockstar.clients import anthropic_client  # noqa: E402

sys.path.insert(0, str(REPO))
from rockstar.tools import rerun_brand_team  # noqa: E402

Q = lambda qid, text: {"id": qid, "speaker": "Jane Invented", "text": text, "heading": "A Heading"}  # noqa: E731


def _run(chain_quotes, argv=(), restore=lambda *a, **k: [], skipped=()):
    uploads.clear()
    enqueued.clear()
    trashed.clear()
    seen = {}

    def chain(transcript_text, episode_metadata, report=None):
        seen.update(episode_metadata)
        if report is not None:
            report["skipped"] = list(skipped)
        return [dict(q) for q in chain_quotes]

    anthropic_client.run_brand_team_chain = chain
    anthropic_client.restore_or_swap = restore
    sys.argv = ["rerun_brand_team.py", "--episode", "99", *argv]
    try:
        rerun_brand_team.main()
        return 0, seen
    except SystemExit as exc:
        return exc.code, seen


def test_a_rewritten_quote_is_checked_and_nothing_changes_when_none_survive():
    code, _ = _run([Q("q01", "Magnesium cures insomnia for everyone, as research proves")])
    assert code == 1 and uploads == [] and enqueued == [] and trashed == [], (code, uploads, enqueued, trashed)


def test_a_skipped_reviewer_never_replaces_a_reviewed_set():
    """Toby, 2026-10-12: a Lisa (or Joe) skip means claims and disclaimers nobody reviewed. Drive stays as it was."""
    for member in ("brand_team_lisa", "brand_team_dr_joe"):
        code, _ = _run(
            [Q("q01", "Rest is not a reward, it is part of the work I do every single day")], skipped=[member]
        )
        assert code == 1 and uploads == [] and trashed == [], (member, code, uploads, trashed)


def test_the_old_set_goes_to_the_trash_not_a_permanent_delete():
    code, _ = _run([Q("q01", "Rest is not a reward, it is part of the work I do every single day")])
    assert trashed == ["old-quotes"] and len(uploads) == 1, (trashed, uploads)
    assert "files().delete" not in (REPO / "rockstar" / "tools" / "rerun_brand_team.py").read_text()


def test_the_uploaded_quotes_carry_the_check_and_phase2_waits():
    code, meta = _run(
        [
            Q("q01", "Every night I put the phone in another room and the evening slows down"),
            Q("q02", "Magnesium cures insomnia for everyone"),
        ]
    )
    assert code in (0, None) and len(uploads) == 1, (code, uploads)
    data = uploads[0]
    assert [q["id"] for q in data["quotes"]] == ["q01"], data  # the rewrite was dropped
    assert data["quotes"][0]["tape_check"]["verdict"] == "verbatim", data
    assert data["brand_team"]["verbatim"]["dropped"][0]["id"] == "q02", data
    from rockstar.gates import clip_limits

    want = clip_limits.load()
    assert (meta["quote_ceiling_seconds"], meta["long_form_quote_ceiling_seconds"]) == (want.standard_s, want.long_s), (
        meta
    )
    assert enqueued == [], "Phase 2 waits for a person's read unless --enqueue"


def test_enqueue_only_when_asked():
    code, _ = _run([Q("q01", "Rest is not a reward, it is part of the work I do every single day")], ["--enqueue"])
    assert len(uploads) == 1 and len(enqueued) == 1, (uploads, enqueued)


def test_no_limits_file_no_selection():
    saved = os.environ.pop("BOX_LIMITS_TEST")
    try:
        code, _ = _run([Q("q01", "Rest is not a reward, it is part of the work I do every single day")])
    finally:
        os.environ["BOX_LIMITS_TEST"] = saved
    assert code == 1 and uploads == [], (code, uploads)


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
