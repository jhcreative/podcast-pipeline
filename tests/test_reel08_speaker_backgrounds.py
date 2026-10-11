"""
test_reel08_speaker_backgrounds.py — REEL-08: each reel's background is its own speaker's image; Heather as the
featured guest gets no thank-you draft.

Run: python3 -B test_reel08_speaker_backgrounds.py   (plain asserts, no pytest needed)

John, 2026-10-01: "we will have episodes where Heather is featured guest, so handling for that is key including
matching reel backgrounds to correct speaker" · naming "E32-reel-name" · Heather-as-guest thank-you: "skip it".
"""

import json
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))

import episode_files as ef  # noqa: E402


def _f(name, fid=None):
    return {"id": fid or name, "name": name, "mimeType": "image/jpeg"}


def test_johns_naming_order_and_the_other_order():
    reels = [_f("E13-reel-john.jpg"), _f("E13-heather-reel.jpg")]
    spk = ["John Hiemstra", "Heather Reilly Hiemstra"]
    assert ef.background_for("John Hiemstra", reels, spk) == (reels[0], "named")
    assert ef.background_for("Heather Reilly Hiemstra", reels, spk) == (reels[1], "named")


def test_single_image_episode_unchanged():  # E32 / E6 today
    reels = [_f("E32-reel-linnea.jpg")]
    spk = ["Linnea Ashgrovenly", "Heather Reilly Hiemstra"]
    assert ef.background_for("Linnea Ashgrovenly", reels, spk)[1] == "named"
    assert ef.background_for("Heather Reilly Hiemstra", reels, spk) == (None, "host-default")
    unnamed = [_f("E40-reel.jpg")]
    assert ef.background_for("Dr. Jane Roe", unnamed, ["Dr. Jane Roe"]) == (unnamed[0], "single")


def test_third_speaker_never_borrows_someone_elses_image():
    reels = [_f("E50-reel-tamsin.jpg")]
    spk = ["Tamsin Kestrelmore MD", "John Hiemstra", "Heather Reilly Hiemstra"]
    f, rule = ef.background_for("John Hiemstra", reels, spk)
    assert f is None and "E<N>-john-reel.jpg" in rule.replace("'", ""), rule


def test_heather_as_guest_with_her_own_image_and_interviewer():
    reels = [_f("E60-reel-heather.jpg"), _f("E60-reel-john.jpg")]
    spk = ["Heather Reilly Hiemstra", "John Hiemstra"]
    assert ef.background_for("Heather Reilly Hiemstra", reels, spk)[0]["name"] == "E60-reel-heather.jpg"
    assert ef.background_for("John Hiemstra", reels, spk)[0]["name"] == "E60-reel-john.jpg"


def test_honorific_and_ambiguity():
    reels = [_f("E6-reel-tamsin.jpg")]
    assert ef.background_for("Dr. Tamsin Kestrelmore", reels, ["Dr. Tamsin Kestrelmore"])[1] == "named"
    two = [_f("E6-reel-tamsin.jpg", "a"), _f("E6-tamsin-reel-2.jpg", "b")]
    f, rule = ef.background_for("Tamsin Kestrelmore", two, ["Tamsin Kestrelmore"])
    assert f is None and "keep one" in rule


def _pf():
    if "anthropic" not in sys.modules:
        a = types.ModuleType("anthropic")
        a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
        sys.modules["anthropic"] = a
    for name in ("calendar_client", "drive_client", "gmail_client", "publisher_email", "db", "blog_publish",
                 "workspace"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules.pop("jobs.publish_finalize", None)
    import jobs.publish_finalize as pf
    return pf


def test_phase2_writes_background_paths_and_stops_on_a_mismatch():
    pf = _pf()
    tmp = Path(tempfile.mkdtemp())
    reels = [_f("E13-reel-john.jpg", "j"), _f("E13-reel-heather.jpg", "h")]
    locals_ = {"j": tmp / "E13-reel-john.jpg", "h": tmp / "E13-reel-heather.jpg"}
    qp = tmp / "quotes.json"
    qp.write_text(json.dumps({"quotes": [{"id": "q1", "speaker": "John Hiemstra"},
                                         {"id": "q2", "speaker": "Heather Reilly Hiemstra"},
                                         {"id": "q3", "speaker": "Heather Reilly Hiemstra", "reel_candidate": False}]}))
    pf._assign_reel_backgrounds(qp, reels, locals_, "E13")
    q = {x["id"]: x for x in json.loads(qp.read_text())["quotes"]}
    assert q["q1"]["background_path"].endswith("E13-reel-john.jpg")
    assert q["q2"]["background_path"].endswith("E13-reel-heather.jpg")
    assert "background_path" not in q["q3"]  # not a reel: untouched
    qp.write_text(json.dumps({"quotes": [{"id": "q1", "speaker": "Guest Two"}, {"id": "q2", "speaker": "John Hiemstra"}]}))
    from content_gates import GateFailure
    try:
        pf._assign_reel_backgrounds(qp, [_f("E13-reel-john.jpg", "j")], locals_, "E13")
    except GateFailure as e:
        assert "Guest Two" in str(e) or "guest" in str(e).lower(), e
        return
    raise AssertionError("a speaker with no image of their own must stop Phase 2 before rendering")


def test_heather_as_guest_skips_thank_you():
    pf = _pf()
    assert pf._guest_is_host("Heather Reilly Hiemstra") and pf._guest_is_host("Heather")
    assert not pf._guest_is_host("Heather Smith") and not pf._guest_is_host("Linnea Ashgrovenly")
    src = (REPO / "jobs" / "publish_finalize.py").read_text()
    skip = src.index("if _guest_is_host(guest_name):")
    assert skip < src.index("ty = thank_you.build_draft(") < src.index("# ── Step 14"), "thank-you must sit inside the skip"


def test_render_reel_honours_background_path():
    js = (REPO / "scripts" / "render_reel.js").read_text()
    assert "quote.background_path\n      || (quote.speaker_role === 'host' ? hostBackgroundPath : args.background)" in js


def test_gate_refusal_alerts_once_and_is_permanent():
    pf = _pf()
    from content_gates import GateFailure
    sent = []
    pf._alert = lambda subject, body: sent.append((subject, body))
    def boom(job_id, payload):
        raise GateFailure("E13: reel backgrounds don't match the speakers. q2: no reel image for John")
    pf._run_pipeline = boom
    try:
        pf.run(1, "x", {"trigger": "visuals", "episode_number": 13})
    except pf.PermanentFailure:
        assert len(sent) == 1 and sent[0][0].startswith("E13: Phase 2 stopped") and "John" in sent[0][1], sent
        return
    raise AssertionError("a gate refusal must be permanent (no 5x retry)")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    print("REEL-08 speaker backgrounds — test run")
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
