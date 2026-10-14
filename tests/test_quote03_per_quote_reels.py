"""
test_quote03_per_quote_reels.py — a reels-only reprocess can name its quotes; only their reels are rendered and replaced.

QUOTE-03 (the corrected-quote backfill) re-renders the reels of the quotes it restores, and only those: E32's reel_01
must not be regenerated when only q04 changed. Before this, `--reels-only` rendered every reel and step 12 deleted every
reel_*.mp4 in Drive's reels/. Here the real _produce_content runs reels-only through a fake Drive and recorded scripts.
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
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix="quote03-"))
os.environ["RUN_LOG_PATH"] = str(TMP / "run_log.jsonl")

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
    ALERTS.append(subject) or {}
)
if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    _swap.put("anthropic", _a)

from rockstar.jobs import publish_finalize as pf  # noqa: E402
from rockstar.ops import run_ledger  # noqa: E402

pf._prepend_standup = lambda ep, text: None

# Headings pass TITLE-02's title rules, so step 5c changes nothing and this suite stays about QUOTE-03.
QUOTES = {
    "episode": {"number": 99},
    "quotes": [
        {
            "id": "q01",
            "index": 1,
            "text": "first",
            "heading": "Why the Evening Slows Down",
            "speaker": "Jane Invented",
            "topic_slug": "first-one",
        },
        {
            "id": "q02",
            "index": 2,
            "text": "second",
            "heading": "Morning Walks Changed It",
            "speaker": "Heather Reilly Hiemstra",
            "topic_slug": "second-new",
        },
        {
            "id": "q03",
            "index": 3,
            "text": "third",
            "heading": "Rest Is Part of the Work",
            "speaker": "Jane Invented",
            "topic_slug": "third-one",
        },
    ],
}


def _raises(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


# ── the option ────────────────────────────────────────────────────────────────


def test_quote_ids_are_parsed_strictly():
    from rockstar.tools import reprocess_episode as rp

    assert rp.parse_quote_ids("q04, Q01,q04") == ["q01", "q04"]
    assert rp.parse_quote_ids("q1") == [] and rp.parse_quote_ids("q01;rm -rf") == [] and rp.parse_quote_ids("") == []


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


def test_the_cli_carries_the_quotes_and_refuses_them_without_reels_only():
    code, calls = _cli("--episode", "32", "--reels-only", "--quotes", "q04,q01")
    assert code == 0 and calls[0]["quotes"] == ["q01", "q04"] and calls[0]["reels_only"] is True, (code, calls)
    code, calls = _cli("--episode", "32", "--apple", "a", "--spotify", "s", "--quotes", "q01")
    assert code == 2 and calls == [], (code, calls)
    code, calls = _cli("--episode", "32", "--reels-only", "--quotes", "four")
    assert code == 2 and calls == [], (code, calls)
    code, calls = _cli("--episode", "32", "--reels-only")
    assert code == 0 and "quotes" not in calls[0], calls  # unchanged without --quotes


def test_phase2_refuses_quotes_on_a_full_run():
    exc = _raises(
        lambda: pf._run_pipeline(
            1, {"trigger": "reprocess", "episode_number": 99, "apple_url": "a", "spotify_url": "s", "quotes": ["q01"]}
        )
    )
    assert type(exc).__name__ == "PermanentFailure" and "reels_only" in str(exc), repr(exc)


# ── the helpers ───────────────────────────────────────────────────────────────


def test_select_quotes_keeps_only_the_named_ones_and_every_other_key():
    p = TMP / "quotes.json"
    p.write_text(json.dumps(QUOTES))
    kept = pf._select_quotes(p, ["q02"], "E99")
    data = json.loads(p.read_text())
    assert [q["id"] for q in kept] == ["q02"] and data["episode"] == {"number": 99}, data
    p.write_text(json.dumps(QUOTES))
    exc = _raises(lambda: pf._select_quotes(p, ["q09"], "E99"))
    assert type(exc).__name__ == "PermanentFailure" and "q09" in str(exc), repr(exc)


def test_only_the_named_reels_are_replaced_whatever_their_old_slug():
    files = [
        {"id": "1", "name": "reel_01_first-one.mp4"},
        {"id": "2", "name": "reel_02_the-invented-line.mp4"},
        {"id": "3", "name": "reel_02_the-invented-line_instagram.txt"},
        {"id": "4", "name": "reel_12_x.mp4"},
    ]
    assert [f["id"] for f in pf._reels_to_replace(files, ["02"])] == ["2"]
    assert [f["id"] for f in pf._reels_to_replace(files, None)] == ["1", "2", "4"]  # today's behaviour, unchanged
    assert pf._reel_numbers([{"id": "q04"}, {"id": "q01", "index": 1}]) == ["04", "01"]


def test_a_named_quote_without_an_index_gets_it_from_its_id():
    p = TMP / "noindex.json"
    p.write_text(json.dumps({"quotes": [{"id": "q04", "text": "x"}, {"id": "q01", "text": "y"}]}))
    assert [q["index"] for q in pf._select_quotes(p, ["q04"], "E99")] == [4]


def test_a_subset_never_gets_a_co_guests_photo():
    """Two guests, one image named for one of them. A full run refuses the other guest's quote ('drop an image');
    a subset holding only that quote must refuse too, not hand it the named guest's photo (REEL-08)."""
    from rockstar.gates.content_gates import GateFailure

    folder = [{"id": "img", "name": "E99-sam-reel.jpg", "mimeType": "image/jpeg"}]
    p = TMP / "coguest.json"
    subset = {"quotes": [{"id": "q01", "index": 1, "speaker": "Jane Invented"}]}

    p.write_text(json.dumps(subset))
    exc = _raises(
        lambda: pf._assign_reel_backgrounds(
            p, folder, {"img": TMP / "sam.jpg"}, "E99", all_speakers=["Jane Invented", "Sam Madeup"], named=True
        )
    )
    assert isinstance(exc, GateFailure), repr(exc)

    p.write_text(json.dumps(subset))  # the defect it guards against: without the episode's speakers, Sam's photo
    pf._assign_reel_backgrounds(p, folder, {"img": TMP / "sam.jpg"}, "E99")
    assert json.loads(p.read_text())["quotes"][0]["background_path"].endswith("sam.jpg")


def test_a_named_non_candidate_quote_gets_its_own_background():
    folder = [{"id": "img", "name": "E99-jane-reel.jpg", "mimeType": "image/jpeg"}]
    p = TMP / "noncand.json"
    p.write_text(
        json.dumps({"quotes": [{"id": "q03", "index": 3, "speaker": "Jane Invented", "reel_candidate": False}]})
    )
    pf._assign_reel_backgrounds(p, folder, {"img": TMP / "jane.jpg"}, "E99", all_speakers=["Jane Invented"], named=True)
    assert json.loads(p.read_text())["quotes"][0]["background_path"].endswith("jane.jpg")


def test_every_caption_file_of_a_rerendered_reel_is_called_stale():
    """Step 11's real names: reel_NN_captions.md, one per reel (Toby: the first version matched names it never writes)."""
    files = [
        {"name": "reel_01_captions.md"},
        {"name": "reel_02_captions.md"},
        {"name": "reel_03_captions.md"},
        {"name": "reel_02_the-invented-line.mp4"},
        {"name": "reel_12_captions.md"},
    ]
    assert pf._stale_captions(files, ["02", "03"]) == ["reel_02_captions.md", "reel_03_captions.md"]
    assert pf._stale_captions(files, []) == []


def test_the_rerun_command_keeps_the_quotes():
    cmd = pf._rerun_command(32, "", "", "", True, ["q01", "q04"])
    assert cmd.endswith("--episode 32 --reels-only --quotes q01,q04"), cmd


# ── the real Phase 2, reels-only, one quote ───────────────────────────────────


def test_a_per_quote_run_renders_and_replaces_only_that_reel():
    work = Path(tempfile.mkdtemp(dir=TMP))
    contents = {
        "t-txt": "x\n",
        "q": json.dumps(QUOTES),
        "a": "# How Sleep Changes After Forty\n\nAbout Jane Invented\n",
        "t-json": json.dumps({"segments": []}),
        "tile": "x",
        "reel": "x",
        "audio": "x",
    }
    folder = [
        {"id": "t-txt", "name": "transcript_fast.txt"},
        {"id": "q", "name": "quotes.json"},
        {"id": "a", "name": "apple_summary.md"},
        {"id": "t-json", "name": "transcript_fast.json"},
        {"id": "audio", "name": "E99-jane.m4a"},
        {"id": "reels-folder", "name": "reels", "mimeType": "application/vnd.google-apps.folder"},
    ]
    in_drive = [
        {"id": "d1", "name": "reel_01_first-one.mp4"},
        {"id": "d2", "name": "reel_02_the-invented-line.mp4"},
        {"id": "d3", "name": "reel_03_third-one.mp4"},
        {"id": "d4", "name": "reel_02_captions.md"},
    ]
    deleted, uploaded, renders = [], [], []
    drive = sys.modules["rockstar.clients.drive_client"]
    drive.list_folder = lambda fid: in_drive if fid == "reels-folder" else []
    drive.download_file = lambda fid, path: Path(path).write_text(contents[fid])
    drive.upload_file = lambda path, folder_id, name, mime: uploaded.append(name)
    disc = types.ModuleType("googleapiclient.discovery")
    files_api = types.SimpleNamespace(
        delete=lambda fileId: types.SimpleNamespace(execute=lambda: deleted.append(fileId))
    )
    disc.build = lambda *a, **k: types.SimpleNamespace(files=lambda: files_api)
    _swap.put("googleapiclient", types.ModuleType("googleapiclient"))
    _swap.put("googleapiclient.discovery", disc)
    auth = types.ModuleType("google_auth")
    auth.drive_credentials = lambda: None
    _swap.put("rockstar.clients.google_auth", auth)

    def run_script(cmd, work_dir, label):  # each script's effect, faked; render_reel writes the reel it was asked for
        cmd = [str(c) for c in cmd]
        if label == "align_episode":
            Path(cmd[cmd.index("--out") + 1]).write_text("{}")
        elif label == "extract_quote_clips":
            (work / "clips").mkdir(exist_ok=True)
            (work / "clips" / "clips_manifest.json").write_text(json.dumps({"clips": []}))
        elif label == "render_reel":
            nn = cmd[cmd.index("--reel") + 1] if "--reel" in cmd else "all"
            renders.append(nn)
            slug = {"01": "first-one", "02": "second-new", "03": "third-one"}
            for n in [nn] if nn != "all" else ["01", "02", "03"]:
                (work / "reels" / f"reel_{n}_{slug[n]}.mp4").write_text("mp4")

    real = (pf._run_script, pf._force_align, pf._assign_reel_backgrounds)
    pf._run_script = run_script
    pf._force_align = lambda work_dir, *a, **k: (
        (work_dir / "transcript_aligned.json").write_text("{}") or (work_dir / "transcript_aligned.json")
    )
    pf._assign_reel_backgrounds = lambda *a, **k: None
    run_ledger.bind(job_id=152, episode="E99", run_kind="eval")
    run_ledger.begin("publish_finalize")
    try:
        exc = _raises(
            lambda: pf._produce_content(
                work_dir=work,
                episode_code="E99",
                episode_number=99,
                episode_title="How Sleep Changes After Forty",
                season_ep_code="S1E99",
                slug="e99",
                episode_folder_id="F99",
                episode_folder_url="u",
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
                only_quotes=["q02"],
            )
        )
    finally:
        pf._run_script, pf._force_align, pf._assign_reel_backgrounds = real
        run_ledger.end()
    # It may stop after step 12 on a fake it doesn't need (schedule, marker); what counts is what reached Drive.
    # q02 is the host's: the guest-featured gate judged the whole episode, so a host-only subset still renders.
    assert "guest isn" not in repr(exc), repr(exc)
    assert renders == ["02"], renders
    assert deleted == ["d2"], deleted  # reel_02's old mp4 only: not reel_01, not reel_03, not its caption file
    assert uploaded[:1] == ["reel_02_second-new.mp4"], (uploaded, repr(exc))
    steps = [json.loads(line) for line in (TMP / "run_log.jsonl").read_text().splitlines()]
    s12 = [e for e in steps if e.get("event") == "step" and e.get("step") == "12" and e.get("job_id") == 152]
    assert s12 and (s12[-1]["outcome"], s12[-1]["detail"]) == ("anomaly", "stale captions left in reels/"), s12


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
