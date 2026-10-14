"""
tests/conftest.py — `pytest` runs every test file in ITS OWN process (SHARE-01).

The suites here are plain-assert scripts: several replace pipeline modules in sys.modules with fakes (Gmail, Drive,
Tailor, the model) for the length of the file. Imported into one pytest process, those fakes leak from one file
into the next. So each file is collected as a single item that runs `python -B <file>` with a clean interpreter, and
passes when the script exits 0. The trade-off, stated: a failure is reported per file, and the file's own output
(printed on failure) names the function and assertion.

`pytest`            every offline file
`pytest -m live`    the files marked live (they call the real API; ANTHROPIC_API_KEY must be set)

A file marked `private` needs Orly's private denylist (DENYLIST_PATH, or the secrets store). Where it's absent (the
public export) the file is SKIPPED with that reason — reported as a skip, never as a pass (SHARE-02).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# CLIP-01c: Round 1 and Phase 2 refuse to run without the box's measured clip limit. Every file gets the new box's
# (CLIP-01b, 2026-10-12) unless it sets its own; tests/test_clip01c_ceiling.py proves the refusal without it.
os.environ.setdefault("BOX_LIMITS_FILE", str(Path(__file__).parent / "fixtures" / "box-limits.env"))
os.environ.setdefault("BOX_LIMITS_TEST", "1")  # BOX_LIMITS_FILE counts only with this set

_DENYLIST = (os.environ.get("DENYLIST_PATH"), Path.home() / "jhc/secrets/rockstar-podcast/share-denylist.txt")


def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".py" and file_path.name.startswith("test_"):
        return ScriptFile.from_parent(parent, path=file_path)
    return None


class ScriptFile(pytest.File):
    def collect(self):
        item = ScriptItem.from_parent(self, name=self.path.stem)
        text = self.path.read_text(encoding="utf-8")
        if "pytestmark = pytest.mark.live" in text:
            item.add_marker(pytest.mark.live)
        if "pytestmark = pytest.mark.private" in text and not any(p and Path(p).is_file() for p in _DENYLIST):
            item.add_marker(
                pytest.mark.skip(
                    reason="needs the private denylist (DENYLIST_PATH); the export runs it before every commit"
                )
            )
        yield item


class ScriptItem(pytest.Item):
    def runtest(self):
        proc = subprocess.run(
            [sys.executable, "-B", str(self.path)],
            capture_output=True,
            text=True,
            cwd=self.path.parent.parent,
            timeout=900,
        )
        self._output = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise ScriptFailed(proc.returncode)
        # A suite that skipped its render tests says so ("NOT a pass") and exits 0; under pytest that is a failure,
        # or a missing browser would read as green (cold review of PR #38).
        for marker in _NOT_A_PASS:
            if marker in self._output:
                raise ScriptFailed(f"0, but it printed {marker!r} — a skipped test is not a pass")

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptFailed):
            tail = "\n".join(self._output.splitlines()[-60:])
            return f"{self.path.name} exited {excinfo.value.args[0]}:\n{tail}"
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script: {self.path.name}"


_NOT_A_PASS = ("NOT a pass", "!! playwright absent")


class ScriptFailed(Exception):
    pass
