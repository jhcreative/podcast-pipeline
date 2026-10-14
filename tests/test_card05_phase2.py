"""
test_card05_phase2.py — CARD-05 Phase 2 card step (rockstar/jobs/card_step.py).

Run: python3 test_card05_phase2.py   (plain asserts, no pytest needed)

  AC5 a full set lands in cards/, standup says so        → test_builds_and_uploads
  AC5 an existing cards/ is never touched                → test_skips_existing_cards
  AC6 a failure alerts once and Phase 2 carries on       → test_failure_alerts_and_returns

The success test drives the REAL scripts (titles, captions, render in Chromium, upload)
in subprocesses, as Phase 2 does. Only the two outside services are faked: the Anthropic
SDK and drive_client, each a small module put first on PYTHONPATH. Without playwright the
render test SKIPS LOUDLY rather than passing quietly (REEL-05).
"""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

from PIL import Image

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE))

from rockstar.jobs import card_step  # noqa: E402

FOLDER = "application/vnd.google-apps.folder"
QUOTES = {
    "quotes": [
        {
            "id": "q01",
            "speaker": "Dr. Ann Ames",
            "text": "A normal lab report does not always mean you feel well. Ask what well looks like for you.",
        },
        {
            "id": "q02",
            "speaker": "Heather Reilly Hiemstra",
            "text": "We used to live in tribes. The aunties are still there if you look for them.",
        },
    ]
}
SUMMARY = (
    "Feed Your Head | A Test Title\n\nHeather sits down with Dr. Ann Ames.\n\n"
    "About Dr. Ann Ames\n\nDr. Ann Ames is a physician in Tampa who has practised "
    "functional medicine for twenty years and teaches at the university.\n"
)

FAKE_ANTHROPIC = """
import json, re
class _M:
    def __init__(self, t): self.content = [type("C", (), {"text": t})()]
class _Msgs:
    def create(self, system="", messages=(), **kw):
        ids = re.findall(r"id: (q\\d+)", messages[0]["content"])
        if "Cover Titles" in system:
            return _M(json.dumps({"titles": {q: f"Test Cover Title {q[-1]}" for q in ids}}))
        return _M(json.dumps({"guest_intro": "Dr. Ann Ames is a physician in Tampa.",
            "posts": {q: {"opening": f"An opening line for {q}.",
                          "hashtags": ["#GenXHitsMenopause", "#Menopause", "#Midlife"]} for q in ids}}))
class Anthropic:
    def __init__(self, **kw): self.messages = _Msgs()
"""

FAKE_DRIVE = """
import json, os, shutil
from pathlib import Path
ROOT = os.environ["FAKE_DRIVE_ROOT"]
def _log(*a): open(os.path.join(ROOT, "calls.log"), "a").write(" ".join(map(str, a)) + "\\n")
def find_episode_folder(prefix):
    for n in sorted(os.listdir(ROOT)):
        if n.startswith(prefix): return {"id": os.path.join(ROOT, n), "name": n}
def list_folder(fid):
    return [{"id": os.path.join(fid, n), "name": n,
             "mimeType": "application/vnd.google-apps.folder" if os.path.isdir(os.path.join(fid, n)) else "x"}
            for n in sorted(os.listdir(fid))]
def create_folder(name, parent):
    _log("CREATE", name); d = os.path.join(parent, name); os.makedirs(d); return {"id": d, "name": name}
def upload_file(p, folder, mime_type=""):
    _log("upload", Path(p).name); shutil.copy(p, folder); return {"id": os.path.join(folder, Path(p).name)}
def trash_file(fid):
    if os.path.basename(fid) == "_upload_incomplete.txt":   # the upload's own in-flight marker
        _log("marker-cleared"); os.remove(fid); return
    _log("TRASH", fid); raise RuntimeError("must not trash")
"""


class Drive(types.SimpleNamespace):
    """In-process drive_client stand-in for the existing-cards check."""

    def __init__(self, root):
        super().__init__(root=root)

    def list_folder(self, fid):
        return [
            {"id": os.path.join(fid, n), "name": n, "mimeType": FOLDER if os.path.isdir(os.path.join(fid, n)) else "x"}
            for n in sorted(os.listdir(fid))
        ]


def _episode(tmp):
    work = Path(tmp) / "work"
    work.mkdir()
    (work / "quotes.json").write_text(json.dumps(QUOTES))
    (work / "apple_summary.md").write_text(SUMMARY)
    img = Image.new("RGB", (600, 1066), (45, 25, 60))
    img.paste((200, 205, 200), (0, 130, 600, 746))
    img.save(work / "E99-reel-ann.jpg")
    drive = Path(tmp) / "drive"
    (drive / "E99-test").mkdir(parents=True)
    return work, drive


def _call(work, drive, alerts, standups):
    return card_step.build_cards(
        work_dir=work,
        episode_code="E99",
        episode_number=99,
        slug="test",
        episode_title="Feed Your Head | A Test Title",
        quotes_path=work / "quotes.json",
        apple_summary_path=work / "apple_summary.md",
        images=[work / "E99-reel-ann.jpg"],
        episode_folder_id=str(drive / "E99-test"),
        drive_client=Drive(str(drive)),
        alert=lambda s, b: alerts.append((s, b)),
        standup=lambda c, m: standups.append(m),
    )


def test_skips_existing_cards():
    with tempfile.TemporaryDirectory() as tmp:
        work, drive = _episode(tmp)
        (drive / "E99-test" / "cards").mkdir()
        (drive / "E99-test" / "cards" / "E99_q01_1of1.png").write_text("x")
        alerts, standups = [], []
        status = _call(work, drive, alerts, standups)
    assert status.startswith("skipped") and not alerts, status
    print("  ok  an existing cards/ is left alone, no alert")


def test_failure_alerts_and_returns():
    with tempfile.TemporaryDirectory() as tmp:
        work, drive = _episode(tmp)
        os.remove(work / "quotes.json")  # copy fails -> unexpected error path
        (work / "quotes.json").mkdir()
        alerts, standups = [], []
        status = _call(work, drive, alerts, standups)
    assert status.startswith("failed"), status
    assert len(alerts) == 1 and "quote cards not built" in alerts[0][0], alerts
    assert "-m rockstar.tools.reprocess_episode --episode 99 --cards-only" in alerts[0][1] and not standups
    print("  ok  a failure sends one alert with the hand-run fix, and returns")


def test_builds_and_uploads():
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  SKIP  render path: playwright not installed -- NOT a pass")
        return
    with tempfile.TemporaryDirectory() as tmp:
        work, drive = _episode(tmp)
        fakes = Path(tmp) / "fakes"
        fakes.mkdir()
        (fakes / "anthropic.py").write_text(FAKE_ANTHROPIC)
        # The fake Drive client goes in as a module registered at start-up: a folder on PYTHONPATH can't stand in
        # for one module of the real `rockstar` package (the package wins), sitecustomize can.
        (fakes / "_fake_drive.py").write_text(FAKE_DRIVE)
        (fakes / "sitecustomize.py").write_text(
            "import sys\nimport _fake_drive\nsys.modules['rockstar.clients.drive_client'] = _fake_drive\n"
        )
        # Only the fakes go on the path; card_step must add the repo root itself, as the
        # box's worker gives its subprocesses no PYTHONPATH.
        env = {
            "PYTHONPATH": str(fakes),
            "ANTHROPIC_API_KEY": "fake",
            "PROMPTS_DIR": str(WORKTREE / "prompts"),
            "FAKE_DRIVE_ROOT": str(drive),
        }
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            alerts, standups = [], []
            status = _call(work, drive, alerts, standups)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        assert status.startswith("built"), (status, alerts)
        cards = sorted(os.listdir(drive / "E99-test" / "cards"))
        assert sum(n.endswith("_0cover.png") for n in cards) == 2, cards
        assert sum(n.endswith("_caption.txt") for n in cards) == 2, cards
        assert any(n.endswith("1of1.png") for n in cards), cards
        log = (drive / "calls.log").read_text()
        assert "TRASH" not in log and log.count("CREATE") == 1, log
        assert "marker-cleared" in log and "_upload_incomplete.txt" not in cards, (log, cards)
        assert standups and "ready to review" in standups[0]
    print(f"  ok  full set built and uploaded: {status}")


def test_partial_upload_is_redone_not_skipped():
    """Cold review #2: a cut-short upload leaves the in-flight marker; it must not be
    mistaken for a finished set, or every retry would skip forever."""
    with tempfile.TemporaryDirectory() as tmp:
        work, drive = _episode(tmp)
        cards = drive / "E99-test" / "cards"
        cards.mkdir()
        (cards / "E99_q01_1of1.png").write_text("x")
        (cards / "_upload_incomplete.txt").write_text("x")
        assert card_step._existing_cards(Drive(str(drive)), str(drive / "E99-test")) == 0
        sys.path.insert(0, str(WORKTREE))
        from rockstar.cards import card_upload

        assert card_upload.is_partial(["a.png", card_upload.INCOMPLETE])
    print("  ok  a partial upload counts as no set: the retry redoes it")


def test_skip_tells_the_standup():
    with tempfile.TemporaryDirectory() as tmp:
        work, drive = _episode(tmp)
        (drive / "E99-test" / "cards").mkdir()
        (drive / "E99-test" / "cards" / "E99_q01_1of1.png").write_text("x")
        alerts, standups = [], []
        _call(work, drive, alerts, standups)
    assert standups and "-m rockstar.tools.reprocess_episode --episode 99 --cards-only" in standups[0], standups
    print("  ok  a skipped rebuild says so in the standup, with the re-render command")


def test_playwright_platform_override_on_newer_ubuntu():
    """The box is Ubuntu 26.04; Playwright 1.60 only knows up to 24.04, at launch too."""
    with tempfile.TemporaryDirectory() as tmp:

        def rel(text):
            p = Path(tmp) / "os-release"
            p.write_text(text)
            return card_step._playwright_platform_override(str(p))

        assert rel('ID=ubuntu\nVERSION_ID="26.04"\n') == "ubuntu24.04-x64"
        assert rel('ID=ubuntu\nVERSION_ID="24.04"\n') is None
        assert rel('ID=debian\nVERSION_ID="13"\n') is None
        assert card_step._playwright_platform_override(str(Path(tmp) / "missing")) is None
    print("  ok  Ubuntu 26.04 -> the 24.04 Chromium build at launch; 24.04, Debian, macOS untouched")


def test_render_uses_the_shared_browser_folder():
    """E6, 2026-10-11: Chromium was installed as root into /root/.cache; the worker (User=rockstar) found
    none. A browser folder in the app dir is passed to every stage; without one, Playwright's default stays."""
    import subprocess

    seen = []
    real_run, real_browsers = subprocess.run, card_step.BROWSERS
    saved = os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)  # a developer's own setting must not decide the test
    subprocess.run = lambda cmd, **k: (
        seen.append(k["env"]) or types.SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            card_step.BROWSERS = Path(tmp) / "ms-playwright"
            card_step._run("render", ["x.py"], Path(tmp))
            assert "PLAYWRIGHT_BROWSERS_PATH" not in seen[-1], seen[-1]
            card_step.BROWSERS.mkdir()
            card_step._run("render", ["x.py"], Path(tmp))
            assert seen[-1]["PLAYWRIGHT_BROWSERS_PATH"] == str(card_step.BROWSERS), seen[-1]
    finally:
        subprocess.run, card_step.BROWSERS = real_run, real_browsers
        if saved is not None:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = saved
    print("  ok  the render stage is pointed at the shared browser folder when it exists")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)} tests passed")
