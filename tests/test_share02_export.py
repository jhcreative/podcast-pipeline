"""
test_share02_export.py — SHARE-02: the public export is allowlisted, redacted, and refuses to commit on any gate.

Run: python3 -B tests/test_share02_export.py   (plain asserts, no pytest needed)

Acceptance criteria → tests:
  AC-1 a fixture tree holding one denylisted name: exit non-zero, the file named, no commit
                                                                → test_denylisted_name_blocks_the_commit
  (and) the same term inside a binary, as bytes or UTF-16, is caught too    → test_binary_hits_are_caught
  (and) a redaction-map row turns a hit into a pass; only allowlisted paths leave; the overlay lands
                                                                → test_redaction_allowlist_and_overlay
  (and) the inputs are honest: an empty denylist, a missing one, uncommitted work, a non-empty dest all stop it
                                                                → test_inputs_that_prove_nothing_stop_it
  (and) a binary nobody looked at blocks the commit (G5)        → test_unreviewed_binary_blocks_the_commit
  (and) the agents' public names apply as whole words, and an internal name that survives blocks it (G8)
                                                                → test_public_names_whole_words_and_g8
  (and) G4 catches a redaction-map term in a file the redaction skipped → test_g4_scans_redaction_terms_…
  (and) G1 flags a stray file; G3 fails on a missing scanner and on findings, printing no secret; G6 fails without
       SEC-01; a skipped exported test fails G2      → test_g1_…, test_g3_…, test_g6_and_a_skipped_test_…
  (and) word: entries, kept phrases, spaced-out variants → test_word_entries_kept_phrases_and_squeezed_variants
  (and) a clean source commits once, with SWAPPED.md and the overlay from HEAD; a .gitignore that would drop a gated
       file stops it                                → test_swapped_overlay_from_head_…, test_gitignore_cannot_…
All fixture names are invented; the real denylist is private and never read here.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPORT = REPO / "rockstar" / "tools" / "share_export.py"
_TMP: list[Path] = []
GUEST = "Zephyrine Quillfeather"  # invented; stands in for a real guest's name


def _tmp(prefix: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _TMP.append(d)
    return d


def _source(files: dict[str, bytes | str], allow: str = "*\n!secret/*\n") -> Path:
    """A committed git repo with share/ALLOWLIST and the given files."""
    src = _tmp("share02-src-")
    files = {"share/ALLOWLIST": allow, **files}
    for rel, body in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(body if isinstance(body, bytes) else body.encode())
    git = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-qm", "fixture")
    return src


def _lists(deny: str = f"# guests\n{GUEST}\n", redact: str = "") -> tuple[Path, Path]:
    d = _tmp("share02-private-")
    (d / "deny.txt").write_text(deny)
    (d / "redact.tsv").write_text(redact)
    return d / "deny.txt", d / "redact.tsv"


def _export(
    src: Path, deny: Path, redact: Path, dest: Path | None = None, env: dict | None = None, skip_tests: bool = True
) -> tuple[subprocess.CompletedProcess, Path]:
    dest = dest or _tmp("share02-dest-") / "out"
    r = subprocess.run(
        [sys.executable, str(EXPORT), str(dest), "--source", str(src), "--denylist", str(deny), "--redact", str(redact)]
        + (["--skip-tests"] if skip_tests else []),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    return r, dest


def _scanners(gitleaks_exit: int = 0, trufflehog_out: str = "", missing: str = "") -> dict:
    """A PATH with stand-in scanners (so these tests don't depend on the real tools being installed)."""
    bin_ = _tmp("share02-bin-")
    tools = {
        "gitleaks": f'#!/bin/sh\n[ "$1" = version ] && echo 8.0-fake && exit 0\nexit {gitleaks_exit}\n',
        "trufflehog": '#!/bin/sh\n[ "$1" = --version ] && echo fake && exit 0\n'
        + (f"printf '%s\\n' '{trufflehog_out}'\nexit 183\n" if trufflehog_out else "exit 0\n"),
    }
    for name, body in tools.items():
        if name != missing:
            (bin_ / name).write_text(body)
            (bin_ / name).chmod(0o755)
    keep = [d for d in os.environ["PATH"].split(":") if not any((Path(d) / t).exists() for t in tools)]
    return {**os.environ, "PATH": ":".join([str(bin_), *keep])}


def test_denylisted_name_blocks_the_commit():
    src = _source({"notes/episode.md": f"Our guest {GUEST} talked about sleep.\n", "ok.py": "x = 1\n"})
    r, dest = _export(src, *_lists())
    assert r.returncode == 1, r.stdout + r.stderr
    assert "G4 notes/episode.md contains denylist line 2" in r.stderr, r.stderr
    assert GUEST not in r.stdout + r.stderr, "a gate names the denylist line, never the term"
    assert "NO commit" in r.stderr and not (dest / ".git").exists(), "no commit when a gate is red"
    assert "1 hits" in r.stdout and "G4 ok.py" not in r.stderr, r.stdout


def test_binary_hits_are_caught():
    src = _source(
        {
            "a.bin": b"\x00\x01" + GUEST.upper().encode() + b"\x00",
            "b.bin": b"\x00\xff" + GUEST.encode("utf-16-le") + b"\x00",
        }
    )
    r, _ = _export(src, *_lists())
    assert "G4 a.bin contains denylist line 2" in r.stderr, "case-insensitive, in a binary: " + r.stderr
    assert "G4 b.bin contains denylist line 2" in r.stderr, "UTF-16, as fonts and some images store text: " + r.stderr


def test_a_binary_coincidence_is_waived_only_by_its_exact_bytes_path_and_line():
    """SHARE-03: a font's designer credits share a guest's first name. share/G4-WAIVED pins sha256 + path + list line;
    other bytes, another line, or a text file, and the hit stands. The waiver is printed, never silent."""
    import hashlib

    body = b"\x00\x01credits: " + GUEST.encode() + b"\x00"
    digest = hashlib.sha256(body).hexdigest()
    tk = hashlib.sha256(GUEST.lower().encode()).hexdigest()[:12]
    waiver = f"{digest}  font.ttf  denylist:2:{tk}  A-Person 2026-10-12  designer credit, not the guest\n"
    text = f"{GUEST}\n".encode()
    text_waiver = (
        f"{hashlib.sha256(text).hexdigest()}  notes.md  denylist:2:{tk}  A-Person 2026-10-12  tries a text file\n"
    )
    src = _source({"font.ttf": body, "share/G4-WAIVED": waiver + text_waiver, "notes.md": text})
    r, _ = _export(src, *_lists())
    assert "G4 font.ttf" not in r.stderr and "G4 waived (share/G4-WAIVED): font.ttf denylist line 2" in r.stdout, (
        r.stdout
    )
    assert "G4 notes.md contains denylist line 2" in r.stderr, "a text file is never waived: " + r.stderr
    other = _source({"font.ttf": body + b"\x00", "share/G4-WAIVED": waiver})  # new bytes: the waiver is void
    r, _ = _export(other, *_lists())
    assert "G4 font.ttf contains denylist line 2" in r.stderr, r.stderr
    line3 = _source({"font.ttf": body, "share/G4-WAIVED": waiver})  # the term moved to another line
    r, _ = _export(line3, *_lists(deny=f"# guests\n# moved\n{GUEST}\n"))
    assert "G4 font.ttf contains denylist line 3" in r.stderr, r.stderr
    both = b"\x00credits: " + GUEST.encode() + b", Other Name\x00"  # the SAME bytes in both runs
    w2 = f"{hashlib.sha256(both).hexdigest()}  font.ttf  denylist:2:{tk}  A-Person 2026-10-12  designer credit\n"
    r, _ = _export(_source({"font.ttf": both, "share/G4-WAIVED": w2}), *_lists())
    assert "G4 font.ttf" not in r.stderr, "the waiver holds for the term it names: " + r.stderr
    r, _ = _export(_source({"font.ttf": both, "share/G4-WAIVED": w2}), *_lists(deny="# guests\nOther Name\n"))
    assert "G4 font.ttf contains denylist line 2" in r.stderr, "line 2 now holds another name: no waiver: " + r.stderr
    latin = _source(
        {
            "notes.bin": b"\x00" + GUEST.encode() + b"\x00",
            "share/G4-WAIVED": f"{hashlib.sha256(b'\x00' + GUEST.encode() + b'\x00').hexdigest()}  notes.bin  "
            f"denylist:2:{tk}  A-Person 2026-10-12  not a font\n",
        }
    )
    r, _ = _export(latin, *_lists())
    assert "G4 notes.bin contains denylist line 2" in r.stderr, "only fonts, images, audio and video are waivable"


def test_redaction_allowlist_and_overlay():
    src = _source(
        {
            "notes/episode.md": f"Our guest {GUEST} talked about sleep.\n",
            "secret/box.md": "the box's address\n",
            "share/overlay/README.md": "# for a reviewer\n",
        }
    )
    r, dest = _export(src, *_lists(redact=f"{GUEST}\tAvery Example\n"))
    assert "G4 denylist: 1 terms + 1 redaction-map terms over" in r.stdout and " 0 hits" in r.stdout, r.stdout
    assert (dest / "notes/episode.md").read_text() == "Our guest Avery Example talked about sleep.\n"
    assert not (dest / "secret").exists(), "a path the allowlist excludes never leaves"
    assert (dest / "README.md").read_text() == "# for a reviewer\n", "the overlay lands at the root"
    assert "G1 allowlist:" in r.stdout and "0 outside it" in r.stdout, r.stdout
    assert r.returncode == 1 and "G2 skipped" in r.stderr, "--skip-tests never commits"


def test_inputs_that_prove_nothing_stop_it():
    src = _source({"ok.py": "x = 1\n"})
    deny, redact = _lists(deny="# nothing but comments\n")
    r, _ = _export(src, deny, redact)
    assert r.returncode == 1 and "UNVERIFIED" in r.stderr, r.stderr
    r, _ = _export(src, deny.parent / "absent.txt", redact)
    assert r.returncode == 1 and "denylist not found" in r.stderr, r.stderr
    (src / "ok.py").write_text("x = 2\n")
    r, _ = _export(src, *_lists())
    assert r.returncode == 1 and "uncommitted changes" in r.stderr, r.stderr
    subprocess.run(["git", "-C", str(src), "checkout", "--", "ok.py"], check=True)
    dest = _tmp("share02-dest-")
    (dest / "leftover.txt").write_text("x")
    r, _ = _export(src, *_lists(), dest=dest)
    assert r.returncode == 1 and "is not empty" in r.stderr, r.stderr


def test_unreviewed_binary_blocks_the_commit():
    src = _source({"font.woff2": b"\x00wOF2 not text\xff"})
    r, _ = _export(src, *_lists())
    assert "G5 font.woff2 has not been reviewed by a person" in r.stderr, r.stderr


def test_public_names_whole_words_and_g8():
    # Invented agent names: this file is itself exported, and real ones would be renamed in it.
    src = _source(
        {
            "share/PUBLIC_NAMES": "Quill\tVesper\n",
            "notes/team.md": "Quill reviewed it. Quillon Kirk wrote it. Quill's call. Quills.\n",
            "share/overlay/NOTES.md": "Thanks to Quill.\n",
        }
    )
    r, dest = _export(src, *_lists())
    assert (dest / "notes/team.md").read_text() == "Vesper reviewed it. Quillon Kirk wrote it. Vesper's call. Quills.\n"
    assert "G8 NOTES.md still names the agent 'Quill'" in r.stderr, "overlay text is checked too: " + r.stderr
    assert "G8 notes/team.md" not in r.stderr, r.stderr


def test_g4_scans_redaction_terms_in_files_redaction_skips():
    """A name only on the redaction map, in a file the redaction can't read (Latin-1, so not UTF-8): the map never
    rewrote it, and G4 must still stop it (Toby, PR #40)."""
    other = "Marigold Fenwhistle"  # invented; on the map, not on the denylist
    src = _source({"n.md": f"caf\xe9 notes \u2014 {other}\n".encode("latin-1", "replace")})
    r, dest = _export(src, *_lists(redact=f"{other}\tAvery Example\n"))
    assert other in (dest / "n.md").read_bytes().decode("latin-1"), "the redaction did skip it (the premise)"
    assert "G4 n.md contains redaction-map line 1" in r.stderr, r.stderr
    assert r.returncode == 1 and not (dest / ".git").exists()


def test_g1_flags_a_file_outside_the_allowlist():
    sys.path.insert(0, str(REPO))
    from rockstar.tools import share_export

    dest = _tmp("share02-g1-")
    (dest / "ok.py").write_text("x\n")
    (dest / "stray.log").write_text("x\n")
    rules = [(True, "*.py")]
    assert share_export.g1(dest, rules, laid=[]) == ["G1 stray.log is not allowlisted"]
    assert share_export.g1(dest, rules, laid=["stray.log"]) == [], "an overlay file is the one exemption"


def test_g3_missing_scanner_and_findings_never_pass():
    src = _source({"ok.py": "x = 1\n"})
    r, _ = _export(src, *_lists(), env=_scanners(missing="gitleaks"))
    assert "G3 gitleaks is not installed" in r.stderr, r.stderr
    secret = "sk-FAKE-0123456789abcdef"
    finding = json.dumps(
        {
            "DetectorName": "FakeKey",
            "Raw": secret,
            "SourceMetadata": {"Data": {"Filesystem": {"file": "ok.py", "line": 1}}},
        }
    )
    r, _ = _export(src, *_lists(), env=_scanners(gitleaks_exit=1, trufflehog_out=finding))
    assert "G3 gitleaks reported findings" in r.stderr and "G3 trufflehog reported findings" in r.stderr, r.stderr
    assert "FakeKey in ok.py:1" in r.stderr and secret not in r.stdout + r.stderr, "detector and place, never the key"


def test_g6_and_a_skipped_test_block_the_commit():
    sys.path.insert(0, str(REPO))
    from rockstar.tools import share_export

    dest = _tmp("share02-g6-")
    assert share_export.g6(dest) == ["G6 rockstar/service/webhook_auth.py (SEC-01) is not in the export"]
    (dest / "rockstar" / "service").mkdir(parents=True)
    (dest / "rockstar/service/webhook_auth.py").write_text("x = 1\n")
    assert share_export.g6(dest) == []
    (dest / "test_s.py").write_text("import pytest\n\ndef test_a():\n    pytest.skip('no')\n")
    deny, _ = _lists()
    got = share_export.g2(dest, deny)
    assert got and "a test was skipped" in got[0], got


def test_word_entries_kept_phrases_and_squeezed_variants():
    src = _source(
        {
            "share/PUBLIC_NAMES": "Quill\tVesper\nQuill Townsend\tQuill Townsend\n",
            "a.md": "Quill Townsend played. Quill reviewed.\n",
            "b.md": "in Canada, not a name\n",
            "c.md": "she said zephy rine quill feather\n",
        }
    )
    r, dest = _export(src, *_lists(deny="word:ada\nzephyrine quillfeather\n"))
    assert (dest / "a.md").read_text() == "Quill Townsend played. Vesper reviewed.\n", "a kept phrase survives"
    assert "G8 a.md" not in r.stderr, "the kept phrase isn't an internal name"
    assert "G4 b.md" not in r.stderr, "a word: entry never matches inside a longer word"
    assert "G4 c.md contains denylist line 2" in r.stderr, "a spaced-out spelling is caught: " + r.stderr
    src2 = _source({"d.md": "Ada said hi\n"})
    r, _ = _export(src2, *_lists(deny="word:ada\n"))
    assert "G4 d.md contains denylist line 1" in r.stderr, r.stderr


def test_swapped_overlay_from_head_and_a_clean_commit():
    src = _source(
        {
            "code.py": f"GUEST = '{GUEST}'\n",
            "same.py": "x = 1\n",
            "rockstar/service/webhook_auth.py": "y = 2\n",
            "tests/test_ok.py": "def test_ok():\n    assert True\n",
            "share/overlay/README.md": "# hi\n",
            ".gitignore": "*.log\n",
        },
        allow="*\n!share/*\n",
    )
    (src / "share" / "overlay" / "debug.log").write_text("ignored, never committed\n")
    r, dest = _export(src, *_lists(redact=f"{GUEST}\tAvery Example\n"), env=_scanners(), skip_tests=False)
    assert r.returncode == 0 and "every gate passed" in r.stdout, r.stdout[-800:] + r.stderr[-800:]
    assert not (dest / "debug.log").exists(), "the overlay comes from HEAD, not the disk"
    swapped = (dest / "SWAPPED.md").read_text()
    assert "| `code.py` | 1 |" in swapped and "same.py" not in swapped, swapped
    log = subprocess.run(["git", "-C", str(dest), "log", "--format=%an <%ae>"], capture_output=True, text=True)
    assert log.stdout.strip() == "John Hiemstra <2340330+jhcreative@users.noreply.github.com>", log.stdout


def test_gitignore_cannot_drop_a_gated_file():
    src = _source(
        {
            "keep.py": "x = 1\n",
            "rockstar/service/webhook_auth.py": "y = 2\n",
            "tests/test_ok.py": "def test_ok():\n    assert True\n",
            ".gitignore": "*.log\n",
        }
    )
    # tracked though ignored (git add -f): it is exported and gated, and the export's .gitignore would drop it
    (src / "notes.log").write_text("gated, then ignored\n")
    git = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True)  # noqa: E731
    git("add", "-f", "notes.log")
    git("-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-qm", "force")
    r, dest = _export(src, *_lists(), env=_scanners(), skip_tests=False)
    assert r.returncode == 1 and "git would commit" in r.stderr and "notes.log" in r.stderr, r.stderr[-600:]
    assert not (dest / ".git").exists(), "no commit, and no half-made repo left behind"


def main() -> int:
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
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
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
