"""
test_card02_upload.py — CARD-02 clear-before-upload sequencing.

Run: python3 test_card02_upload.py   (plain asserts, no pytest needed)

Covers CARD-02 acceptance criteria, minus the live run:
  AC1 cards/ found or created, folder repopulated → test_creates_*, test_uploads_*
  AC3 clear-first, so re-runs do not duplicate     → test_trashes_before_uploading
  AC4 a failed upload leaves local files intact    → test_failure_leaves_local_intact

Targets `card_upload`, which is deliberately free of Playwright so it can run on
the box where the credentials live. `drive_client` is replaced with a recording fake. This is the one place in the card
tooling where a stub is the right call: the real function TRASHES files in a live
Drive folder, and the ordering guarantee — every trash before any upload — is
exactly what a live test cannot safely explore. The live run is CARD-02's remaining
acceptance criterion and is not replaced by this file.
"""

import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE))

CARDS_FOLDER = {"id": "cards-id", "name": "cards", "mimeType": "application/vnd.google-apps.folder"}
EPISODE_FOLDER = {"id": "ep-id", "name": "E29-thessaly"}


class FakeDrive(types.ModuleType):
    """Records the call sequence so ordering can be asserted, not assumed."""

    def __init__(self, cards_exists=True, upload_raises=False):
        super().__init__("drive_client")
        self.calls = []
        self.cards_exists = cards_exists
        self.upload_raises = upload_raises
        self.existing = [{"id": "old-1", "name": "E29_q01_1of2.png"}, {"id": "old-2", "name": "E29_q01_caption.txt"}]

    def find_episode_folder(self, prefix):
        self.calls.append(("find", prefix))
        return EPISODE_FOLDER

    def list_folder(self, folder_id):
        self.calls.append(("list", folder_id))
        if folder_id == EPISODE_FOLDER["id"]:
            return [CARDS_FOLDER] if self.cards_exists else []
        return list(self.existing)

    def create_folder(self, name, parent):
        self.calls.append(("create_folder", name, parent))
        return CARDS_FOLDER

    def trash_file(self, file_id):
        # Clearing the marker at the end is the upload finishing, not a stale-file trash.
        self.calls.append(("marker-cleared",) if file_id == "marker" else ("trash", file_id))

    def upload_file(self, local_path, folder_id, mime_type="application/octet-stream"):
        name = Path(local_path).name
        if name == "_upload_incomplete.txt":  # CARD-05: the in-flight marker
            self.calls.append(("marker", name))
            return {"id": "marker"}
        self.calls.append(("upload", name, mime_type))
        if self.upload_raises:
            raise RuntimeError("network died mid-upload")
        return {"id": "new"}


def _local_dir(tmp):
    """A realistic post-run folder: cards, captions, and local scratch."""
    d = Path(tmp) / "cards"
    d.mkdir()
    for name in (
        "E29_q01_1of2.png",
        "E29_q01_2of2.png",
        "E29_q01_caption.txt",
        "E29_q02_1of1.png",
        "E29_q02_caption.txt",
    ):
        (d / name).write_text("x")
    (d / "_E29_contact_sheet.png").write_text("scratch")  # local only
    (d / ".DS_Store").write_text("junk")
    return d


def _run(tmp, fake):
    _swap.put("rockstar.clients.drive_client", fake)
    from rockstar.cards import card_upload

    d = _local_dir(tmp)
    card_upload.upload_cards(29, str(d))
    return d


# ── tests ──────────────────────────────────────────────────────────────────────


def test_trashes_before_uploading():
    """The ordering guarantee. upload_file CREATES rather than replaces, so if a
    single upload landed before the clear, a re-run would leave two copies of that
    slide side by side — the second run's fix next to the first run's defect."""
    fake = FakeDrive()
    with tempfile.TemporaryDirectory() as tmp:
        _run(tmp, fake)
    kinds = [c[0] for c in fake.calls]
    assert "trash" in kinds and "upload" in kinds, kinds
    assert max(i for i, k in enumerate(kinds) if k == "trash") < min(i for i, k in enumerate(kinds) if k == "upload"), (
        kinds
    )
    assert kinds.count("trash") == 2, kinds
    print("  ok  every trash precedes every upload (2 stale files cleared)")


def test_creates_cards_folder_when_absent():
    fake = FakeDrive(cards_exists=False)
    with tempfile.TemporaryDirectory() as tmp:
        _run(tmp, fake)
    assert ("create_folder", "cards", EPISODE_FOLDER["id"]) in fake.calls, fake.calls
    assert not any(c[0] == "trash" for c in fake.calls), "nothing to clear in a new folder"
    print("  ok  creates cards/ when absent, and trashes nothing")


def test_uploads_whole_folder_excluding_scratch():
    """Captions and cards travel together — two tools each clearing the folder and
    pushing only their own files would have the second trash the first's work."""
    fake = FakeDrive()
    with tempfile.TemporaryDirectory() as tmp:
        _run(tmp, fake)
    names = [c[1] for c in fake.calls if c[0] == "upload"]
    assert sorted(names) == [
        "E29_q01_1of2.png",
        "E29_q01_2of2.png",
        "E29_q01_caption.txt",
        "E29_q02_1of1.png",
        "E29_q02_caption.txt",
    ], names
    assert not any(n.startswith("_") or n.startswith(".") for n in names), names
    print(f"  ok  uploads all {len(names)} deliverables; _scratch and dotfiles stay local")


def test_mime_types():
    fake = FakeDrive()
    with tempfile.TemporaryDirectory() as tmp:
        _run(tmp, fake)
    mimes = {c[1]: c[2] for c in fake.calls if c[0] == "upload"}
    assert mimes["E29_q01_1of2.png"] == "image/png", mimes
    assert mimes["E29_q01_caption.txt"] == "text/plain", mimes
    print("  ok  png and txt get their own mime types")


def test_finds_folder_by_hyphenated_prefix():
    """'E3' would match E30-E39; the hyphen is what makes E3 unambiguous."""
    fake = FakeDrive()
    with tempfile.TemporaryDirectory() as tmp:
        _run(tmp, fake)
    assert ("find", "E29-") in fake.calls, fake.calls
    print("  ok  episode folder looked up as 'E29-', not 'E29'")


def test_missing_episode_folder_is_a_hard_failure():
    fake = FakeDrive()
    fake.find_episode_folder = lambda prefix: None
    _swap.put("rockstar.clients.drive_client", fake)
    from rockstar.cards import card_upload

    with tempfile.TemporaryDirectory() as tmp:
        d = _local_dir(tmp)
        try:
            card_upload.upload_cards(29, str(d))
        except SystemExit as e:
            assert "nothing uploaded" in str(e), e
            assert len(list(d.iterdir())) == 7, "local files must survive"
            print("  ok  a missing Drive folder fails loudly, local files untouched")
            return
    raise AssertionError("expected a hard failure")


def test_failure_leaves_local_intact():
    """A dead network mid-upload must not cost the rendered work."""
    fake = FakeDrive(upload_raises=True)
    with tempfile.TemporaryDirectory() as tmp:
        _swap.put("rockstar.clients.drive_client", fake)
        from rockstar.cards import card_upload

        d = _local_dir(tmp)
        try:
            card_upload.upload_cards(29, str(d))
            raise AssertionError("expected the upload to raise")
        except RuntimeError:
            pass
        assert len(list(d.iterdir())) == 7, list(d.iterdir())
    print("  ok  upload failure leaves every local file in place")


# ── runner ─────────────────────────────────────────────────────────────────────


def main() -> int:
    tests = [
        test_trashes_before_uploading,
        test_creates_cards_folder_when_absent,
        test_uploads_whole_folder_excluding_scratch,
        test_mime_types,
        test_finds_folder_by_hyphenated_prefix,
        test_missing_episode_folder_is_a_hard_failure,
        test_failure_leaves_local_intact,
    ]
    failed = 0
    print("CARD-02 clear-before-upload sequencing — test run")
    print("NOTE: drive_client is faked. The LIVE run is still CARD-02's open AC.")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback

            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
