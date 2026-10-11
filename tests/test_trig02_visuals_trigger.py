"""
test_trig02_visuals_trigger.py — TRIG-02: the tile + reel images in the episode folder start Phase 2.

Run: python3 -B test_trig02_visuals_trigger.py   (plain asserts, no pytest needed)

Why (John, 2026-10-01): the publisher no longer sends "Your Episode is Live" (ADR-0011) and Example Publisher has no API.
Phase 2 now starts when the reviewed episode's folder holds both images. The episode links don't exist yet, so
every listen link carries the SHOW page until John pastes the episode links.

  AC-1 both images, episode awaiting Phase 2 → exactly one PUBLISH_FINALIZE(trigger=visuals), deduped
  AC-2 one image, done/pipeline_done, wrong folder, images only in a subfolder → nothing
  AC-3 show links pass the blog's listen-URL gate (they are real links on the allowed hosts)
  AC-4 the record says links are pending; giving real links clears it
  AC-5 the long-standing resume trigger name "drive_changes_resume" is recognised by Phase 2
"""

import ast
import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
STATE = Path(tempfile.mkdtemp(prefix="trig02-"))
os.environ["STATE_DIR"] = str(STATE)

import episode_files  # noqa: E402

for name in ("anthropic",):
    if name not in sys.modules:
        m = types.ModuleType(name)
        m.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
        sys.modules[name] = m


def _drive_changes(files, folder_name="E32-linnea"):
    db = types.ModuleType("db")
    db.calls = []
    db.enqueue = lambda **k: db.calls.append(k) or 900 + len(db.calls)
    drive = types.ModuleType("drive_client")
    drive.get_folder_name = lambda fid: folder_name
    drive.list_folder = lambda fid: list(files)
    sys.modules["db"], sys.modules["drive_client"] = db, drive
    sys.modules.pop("drive_changes", None)
    import drive_changes
    return drive_changes, db


def _schedule(**over):
    ep = {"episode_number": 32, "episode_code": "E32", "slug": "linnea", "drive_folder_id": "F32",
          "status": "reprocessed", "pipeline_done": False}
    ep.update(over)
    (STATE / "publication_schedule.json").write_text(json.dumps({"episodes": [ep]}))


TILE = {"id": "t1", "name": "E32-linnea-tile.jpg", "mimeType": "image/jpeg"}
REEL = {"id": "r1", "name": "E32-linnea-reel.png", "mimeType": "image/png"}


def test_find_visual_rules():
    files = [{"id": "x", "name": "E32-tile.jpg", "mimeType": "application/vnd.google-apps.folder"},
             {"id": "y", "name": "E32-linnea-tile.pdf", "mimeType": "application/pdf"}, TILE]
    assert episode_files.find_visual(files, "tile")["id"] == "t1"
    assert episode_files.find_visual([REEL], "tile") is None
    assert episode_files.find_visual([{"id": "z", "name": "tile.jpg"}], "tile") is None  # needs E<N>


def test_both_images_enqueue_once_with_visuals_trigger():
    _schedule()
    dc, db = _drive_changes([TILE, REEL, {"id": "a", "name": "apple_summary.md"}])
    assert dc._maybe_resume_publish_finalize("F32") is True
    assert len(db.calls) == 1, db.calls
    call = db.calls[0]
    assert call["job_type"] == "PUBLISH_FINALIZE" and call["payload"]["trigger"] == "visuals"
    assert call["payload"]["episode_number"] == 32
    assert call["dedup_key"] == "visuals:E32:t1:r1", call["dedup_key"]


def test_one_image_waits():
    _schedule()
    dc, db = _drive_changes([TILE])
    assert dc._maybe_resume_publish_finalize("F32") is False and not db.calls


def test_not_awaiting_does_not_fire():
    for over in ({"status": "done"}, {"pipeline_done": True}):
        _schedule(**over)
        dc, db = _drive_changes([TILE, REEL])
        assert dc._maybe_resume_publish_finalize("F32") is False and not db.calls, over


def test_other_folder_does_not_fire():
    _schedule()
    dc, db = _drive_changes([TILE, REEL])
    assert dc._maybe_resume_publish_finalize("SOME-OTHER-FOLDER") is False and not db.calls
    dc, db = _drive_changes([TILE, REEL], folder_name="_superseded-2026-10-01T1613Z")
    assert dc._maybe_resume_publish_finalize("F32") is False and not db.calls


def _pf():
    for name in ("calendar_client", "drive_client", "gmail_client", "publisher_email", "db",
                 "blog_publish", "workspace"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules.pop("jobs.publish_finalize", None)
    import jobs.publish_finalize as pf
    return pf


def test_show_links_pass_the_blog_gate():
    pf = _pf()
    import content_gates
    assert content_gates._listen_url_problem("Listen on Apple", pf._SHOW_APPLE_URL) is None
    assert content_gates._listen_url_problem("Listen on Spotify", pf._SHOW_SPOTIFY_URL) is None


def test_record_says_links_pending_until_real_links():
    pf = _pf()
    ep = {}
    pf._apply_phase2_completion(ep, apple_url=pf._SHOW_APPLE_URL, spotify_url=pf._SHOW_SPOTIFY_URL,
                                mm_url="", blog_post_url="https://rockstarblends.com/blog/x", message_id="",
                                today="2026-10-01",
                                links_pending=pf._links_pending(pf._SHOW_APPLE_URL, pf._SHOW_SPOTIFY_URL))
    assert ep["episode_links_pending"] is True and "episode links pending" in ep["notes"]
    pf._apply_phase2_completion(ep, apple_url="https://podcasts.apple.com/us/podcast/x/id1785708777?i=1",
                                spotify_url="https://open.spotify.com/episode/abc", mm_url="",
                                blog_post_url="https://rockstarblends.com/blog/x", message_id="", today="2026-10-02",
                                links_pending=pf._links_pending("https://podcasts.apple.com/us/podcast/x/id1785708777?i=1",
                                                                "https://open.spotify.com/episode/abc"))
    assert ep["episode_links_pending"] is False


def test_phase2_recognises_both_resume_names_and_visuals():
    src = (REPO / "jobs" / "publish_finalize.py").read_text()
    assert 'trigger == "visuals"' in src
    assert 'trigger in ("drive_resume", "drive_changes_resume")' in src


def test_one_visual_rule():
    tree = ast.parse((REPO / "jobs" / "publish_finalize.py").read_text())
    literal = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "_VISUAL_RE" for t in n.targets) and isinstance(n.value, ast.Dict)]
    assert not literal, "publish_finalize must use episode_files.VISUAL_RE, the rule the trigger uses"


def test_guest_gate_with_unknown_name():
    import content_gates
    from content_gates import GateFailure
    quotes = [{"speaker": "Linnea Ashgrovenly"}, {"speaker": "Heather Reilly Hiemstra"}]
    for placeholder in ("Guest", "", "TBD"):
        content_gates.assert_guest_featured(quotes, placeholder, "E32")  # guest featured: passes
    try:
        content_gates.assert_guest_featured([{"speaker": "Heather Reilly Hiemstra"}], "Guest", "E32")
    except GateFailure:
        pass
    else:
        raise AssertionError("all-host quotes must still be refused when the name is unknown")
    content_gates.assert_guest_featured(quotes, "Linnea Ashgrovenly", "E32")  # named path unchanged


def test_guest_name_from_reviewed_summary():
    pf = _pf()
    import blog_post as _bp
    pf.blog_post = _bp
    import content_gates
    e13 = ("# Gen X Hits Menopause · S1E13 · A | B\n\nHook.\n\nConversations include\n\n- **L** b\n\nTune.\n\n"
           "About Heather Reilly Hiemstra\n\nBio.\n")
    name = pf._guest_from_summary("", e13, "E13")
    assert name == "Heather Reilly Hiemstra", name
    content_gates.assert_guest_featured([{"speaker": "Heather Reilly Hiemstra"}] * 5, name, "E13")  # host-only: allowed
    e6 = e13.replace("About Heather Reilly Hiemstra", "About Tamsin Kestrelmore, MD")
    assert pf._guest_from_summary("Guest", e6, "E6") == "Tamsin Kestrelmore, MD"
    assert pf._guest_from_summary("Linnea Ashgrovenly", e6, "E32") == "Linnea Ashgrovenly"     # a real schedule name wins
    assert pf._guest_from_summary("", "not a summary", "E1") == ""                 # unparseable: unchanged


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    print("TRIG-02 visuals trigger — test run")
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
