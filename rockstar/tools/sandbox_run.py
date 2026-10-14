#!/usr/bin/env python3
"""sandbox_run.py — run one episode's Round 1 and Phase 2 on this machine, with every outside service faked.

Why: to prove a change to the pipeline's code does the same thing as the code before it (the 2026-10-13 remodel),
on real episodes, without touching Drive, Gmail, Tailor, Calendar or the box. It is self-contained: it imports
nothing from the checkout it lives in, and `--code` names the checkout under test, so one harness drives both.

What is real: the jobs, the gates, the prompts, the worker's dispatch, the SQLite queue, the Node and bash scripts,
ffmpeg, WhisperX on the quote clips and the forced alignment, and the card renderer (Chromium).

What is faked, and how:
  Drive        a folder tree under OUT/drive (inbox/, episodes/<folder>/); ids are paths
  Gmail        drafts and alerts are written to OUT/gmail/*.json; nothing is sent
  Tailor       the test suite's FakeTailor (tests/test_blog02b_tailor_write.py) behind tailor_client.HttpTransport
  Calendar     off (EDITORIAL_CALENDAR_EVENT=off) and faked anyway
  Guest form   guest_form.fetch_rows returns --guest-csv (the three guests' rows, private)
  Model        anthropic.Anthropic: `live` calls the API; `record` calls it for a request the
               cassette lacks and saves the reply
               (a known request is replayed, so a re-run extends the cassette);
               `replay` answers from the cassette and FAILS on any request it has no reply for: a changed
               prompt or request is a parity break, never a quiet new call
  Transcribe   Round 1's transcribe_fast.sh is answered from the snapshot's transcripts (both arms, stated)
  Scripts      every other subprocess runs for real, or, with --script-cache, is answered from the cache when its
               normalized argv, the script's bytes and every input file in the work dir hash the same. A hit IS
               the proof the step's inputs and code were identical; a miss runs it for real and says so.

Out: OUT/manifest.json — every artifact the run produced (Drive files, drafts, blog pages, state, ledger events),
hashed after masking run-specific values (timestamps, dates, job ids, work-dir paths). `--compare A B` diffs two.

Usage:
  sandbox_run.py --code DIR --snapshot DIR --episode E6 --out DIR --model record --cassette FILE \
      --script-cache DIR --guest-csv FILE --state DIR [--layout flat|package]
  sandbox_run.py --compare OUT_A OUT_B
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

# ── layouts: where the code under test keeps each piece ───────────────────────────────────────────────────────────

LAYOUTS = {
    "flat": {
        "modules": {
            "drive_client": "drive_client",
            "gmail_client": "gmail_client",
            "calendar_client": "calendar_client",
            "google_auth": "google_auth",
            "guest_form": "guest_form",
            "tailor_client": "tailor_client",
            "workspace": "workspace",
            "worker": "worker",
            "db": "db",
            "drive_changes": "drive_changes",
        },
        "scripts_env": {"SCRIPTS_DIR": "scripts"},
        "fake_tailor": "tests/test_blog02b_tailor_write.py",
    },
    "package": {
        "modules": {
            "drive_client": "rockstar.clients.drive_client",
            "gmail_client": "rockstar.clients.gmail_client",
            "calendar_client": "rockstar.clients.calendar_client",
            "google_auth": "rockstar.clients.google_auth",
            "guest_form": "rockstar.content.guest_form",
            "tailor_client": "rockstar.clients.tailor_client",
            "workspace": "rockstar.ops.workspace",
            "worker": "rockstar.service.worker",
            "db": "rockstar.service.db",
            "drive_changes": "rockstar.clients.drive_changes",
        },
        "scripts_env": {},  # none: config's own defaults find scripts/ and reels/, as on the box (Toby's review)
        "fake_tailor": "tests/test_blog02b_tailor_write.py",
    },
}

_AUDIO = {".m4a", ".mp4", ".mp3", ".wav"}
_REAL_RUN = subprocess.run


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Drive ─────────────────────────────────────────────────────────────────────────────────────────────────────────


class FakeDrive:
    """Drive as a folder tree. A file's id is its path relative to the root; a folder's mimeType is Drive's."""

    FOLDER = "application/vnd.google-apps.folder"

    def __init__(self, root: Path):
        self.root = root
        (root / "inbox").mkdir(parents=True, exist_ok=True)
        (root / "episodes").mkdir(parents=True, exist_ok=True)
        self.calls: list[list] = []

    def _p(self, fid: str) -> Path:
        return self.root / fid

    def _id(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def _entry(self, p: Path) -> dict:
        d = {"id": self._id(p), "name": p.name}
        if p.is_dir():
            d["mimeType"] = self.FOLDER
        return d

    def module(self) -> types.ModuleType:
        m = types.ModuleType("drive_client")
        d = self

        def log(name):
            def wrap(fn):
                @functools.wraps(fn)
                def inner(*a, **k):
                    d.calls.append([name, [str(x) for x in a]])
                    return fn(*a, **k)

                return inner

            return wrap

        @log("list_audio_in_inbox")
        def list_audio_in_inbox():
            return [d._entry(p) for p in sorted((d.root / "inbox").iterdir()) if p.suffix.lower() in _AUDIO]

        @log("find_episode_folder")
        def find_episode_folder(episode_code):
            for p in sorted((d.root / "episodes").iterdir()):
                code = episode_code.rstrip("-_ ")  # card_upload asks for "E34-"; Round 1 for "E34"
                if p.is_dir() and re.match(rf"^{re.escape(code)}(?:[-_ ]|$)", p.name, re.IGNORECASE):
                    return d._entry(p)
            return None

        @log("list_folder")
        def list_folder(folder_id):
            p = d._p(folder_id)
            return [d._entry(c) for c in sorted(p.iterdir()) if not c.name.startswith(".")] if p.is_dir() else []

        @log("create_folder")
        def create_folder(name, parent_folder_id=None):
            parent = d._p(parent_folder_id) if parent_folder_id else d.root / "episodes"
            p = parent / name
            p.mkdir(parents=True, exist_ok=True)
            return d._entry(p)

        @log("download_file")
        def download_file(file_id, dest):
            shutil.copyfile(d._p(file_id), dest)

        @log("move_file")
        def move_file(file_id, folder_id):
            src = d._p(file_id)
            dst = d._p(folder_id) / src.name
            shutil.move(src, dst)
            return d._entry(dst)

        @log("upload_file")
        def upload_file(path, folder_id, name=None, mime_type=None, *a, **k):
            dst = d._p(folder_id) / (name or Path(path).name)
            shutil.copyfile(path, dst)
            return d._entry(dst)

        @log("upload_text")
        def upload_text(text, folder_id, name, *a, **k):
            dst = d._p(folder_id) / name
            dst.write_text(text, encoding="utf-8")
            return d._entry(dst)

        @log("trash_file")
        def trash_file(file_id):
            p = d._p(file_id)
            trash = d.root / ".trash"
            trash.mkdir(exist_ok=True)
            if p.exists():
                shutil.move(p, trash / f"{len(list(trash.iterdir())):03d}-{p.name}")

        @log("has_pipeline_done_marker")
        def has_pipeline_done_marker(folder_id):
            return (d._p(folder_id) / ".pipeline_done").exists()

        @log("create_pipeline_done_marker")
        def create_pipeline_done_marker(folder_id):
            (d._p(folder_id) / ".pipeline_done").write_text("")

        @log("get_folder_name")
        def get_folder_name(folder_id):
            return d._p(folder_id).name

        def export_mime_for(name):  # pure: the real rule, no I/O
            return "text/markdown" if name.endswith(".md") else "text/plain"

        for fn in (
            list_audio_in_inbox,
            find_episode_folder,
            list_folder,
            create_folder,
            download_file,
            move_file,
            upload_file,
            upload_text,
            trash_file,
            has_pipeline_done_marker,
            create_pipeline_done_marker,
            get_folder_name,
            export_mime_for,
        ):
            setattr(m, fn.__name__, fn)

        def missing(name):
            raise AttributeError(
                f"sandbox: drive_client.{name} is not faked — add it to FakeDrive, never let it reach Google"
            )

        m.__getattr__ = missing
        return m

    def discovery(self) -> types.ModuleType:
        """googleapiclient.discovery: publish_finalize deletes Drive files through the API directly (3 places)."""
        d = self

        class Files:
            def delete(self, fileId):
                def execute():
                    d.calls.append(["api.files.delete", [fileId]])
                    p = d._p(fileId)
                    if p.is_dir():
                        shutil.rmtree(p)
                    elif p.exists():
                        p.unlink()

                return types.SimpleNamespace(execute=execute)

        m = types.ModuleType("googleapiclient.discovery")
        m.build = lambda *a, **k: types.SimpleNamespace(files=lambda: Files())
        return m


# ── Gmail, Calendar, Google auth ──────────────────────────────────────────────────────────────────────────────────


def fake_gmail(out: Path) -> types.ModuleType:
    out.mkdir(parents=True, exist_ok=True)
    m = types.ModuleType("gmail_client")
    seq = {"n": 0}

    def _write(kind, **fields):
        seq["n"] += 1
        (out / f"{seq['n']:03d}-{kind}.json").write_text(json.dumps(fields, indent=2, ensure_ascii=False))
        return seq["n"]

    def create_draft(to, subject, body, *a, **k):
        n = _write("draft", to=to, subject=subject, body=body)
        return {"id": f"draft-{n}", "message": {"id": f"msg-{n}", "threadId": f"t-{n}"}}

    def send_pipeline_alert(to, subject, body, *a, **k):
        _write("alert", to=to, subject=subject, body=body)

    def update_draft(draft_id, *a, **k):
        _write("draft-update", draft_id=draft_id, args=[str(x) for x in a], kwargs={k2: str(v) for k2, v in k.items()})

    class DraftGone(Exception):
        pass

    m.create_draft, m.send_pipeline_alert, m.update_draft, m.DraftGone = (
        create_draft,
        send_pipeline_alert,
        update_draft,
        DraftGone,
    )
    m.draft_state = lambda draft_id, *a, **k: {"id": draft_id, "body_sha": None}
    m.get_draft_body = lambda *a, **k: ""

    def missing(name):
        raise AttributeError(f"sandbox: gmail_client.{name} is not faked — never let it reach Google")

    m.__getattr__ = missing
    return m


def fake_calendar() -> types.ModuleType:
    m = types.ModuleType("calendar_client")

    def refuse(*a, **k):
        raise RuntimeError("sandbox: the calendar is off (CAL-OFF); a call means the switch was ignored")

    m.create_editorial_event = m.mark_episode_live = refuse
    return m


def fake_google_auth() -> types.ModuleType:
    m = types.ModuleType("google_auth")
    m.drive_credentials = m.gmail_credentials = m.calendar_credentials = lambda *a, **k: None
    return m


# ── the model ─────────────────────────────────────────────────────────────────────────────────────────────────────


def request_key(kwargs: dict) -> str:
    return sha(json.dumps(kwargs, sort_keys=True, ensure_ascii=False, default=str).encode())


def install_model(mode: str, cassette: Path, real_anthropic) -> dict:
    """Swap anthropic.Anthropic. Returns the stats dict the run reports (calls, hits, recorded)."""
    stats = {"mode": mode, "calls": 0, "recorded": 0, "replayed": 0, "keys": []}
    tape = json.loads(cassette.read_text()) if (mode in ("replay", "record") and cassette.exists()) else {}
    seen: dict[str, int] = {}
    from anthropic.types import Message

    class Messages:
        def __init__(self, real):
            self._real = real

        def create(self, **kwargs):
            stats["calls"] += 1
            k = request_key(kwargs)
            n = seen.get(k, 0)
            seen[k] = n + 1
            slot = f"{k}#{n}"
            stats["keys"].append(slot)
            if mode == "record" and slot in tape:  # record EXTENDS a cassette: a known request is replayed
                stats["replayed"] += 1
                return Message.model_validate(tape[slot])
            if mode == "replay":
                if slot not in tape:
                    raise RuntimeError(
                        f"sandbox replay: no recorded reply for request {slot} "
                        f"(model={kwargs.get('model')}, max_tokens={kwargs.get('max_tokens')}): "
                        f"the request differs from the recorded run — a parity break"
                    )
                stats["replayed"] += 1
                return Message.model_validate(tape[slot])
            resp = self._real.messages.create(**kwargs)
            if mode == "record":
                tape[slot] = resp.model_dump(mode="json")
                stats["recorded"] += 1
                cassette.parent.mkdir(parents=True, exist_ok=True)
                cassette.write_text(json.dumps(tape, indent=1, ensure_ascii=False))
            return resp

    class Anthropic:
        def __init__(self, *a, **k):
            real = real_anthropic.Anthropic(*a, **k) if mode != "replay" else None
            self.messages = Messages(real)

    fake = types.ModuleType("anthropic")
    for name in dir(real_anthropic):  # exception classes, types: the code may catch anthropic.APIError etc.
        if not name.startswith("__"):
            setattr(fake, name, getattr(real_anthropic, name))
    fake.Anthropic = Anthropic
    sys.modules["anthropic"] = fake
    return stats


# ── subprocesses ──────────────────────────────────────────────────────────────────────────────────────────────────


class Scripts:
    """subprocess.run, recorded, answered from the snapshot (transcribe_fast) or a content-addressed cache."""

    def __init__(self, code: Path, work_base: Path, snapshot_folder: Path, cache: Path | None, log: list):
        self.code, self.work_base, self.snap, self.cache, self.log = code, work_base, snapshot_folder, cache, log
        # Every script a step may call (a .js requiring another, a sourced .sh, align_episode.py): a change to any of
        # them changes every key, so a cache hit can't hide a changed callee (Toby's review).
        sib = [p for d in ("scripts", "reels") for p in sorted((code / d).glob("*")) if p.is_file()]
        self.siblings = sha("".join(file_sha(p) for p in sib).encode())

    def _inputs(self, cwd: Path) -> str:
        """The step's inputs, comparable across two checkouts: each work-dir file's bytes, with the checkout and the
        work dir's own path replaced (quotes.json carries absolute paths)."""
        out = {}
        for p in sorted(cwd.rglob("*")):
            if not p.is_file():
                continue
            data = p.read_bytes()
            if p.suffix in {".json", ".txt", ".md", ".srt", ".vtt", ".csv"}:
                text = data.decode("utf-8", "replace").replace(str(self.code), "<CODE>")
                for rx, rep in _MASKS:  # a run's own timestamps (transcript_aligned.json's generated_at) aren't inputs
                    text = rx.sub(rep, text)
                data = text.replace(str(self.work_base), "<WORK>").encode()
            out[str(p.relative_to(cwd))] = sha(data)
        return sha(json.dumps(out, sort_keys=True).encode())

    def norm(self, s: str) -> str:
        s = s.replace(str(self.work_base), "<WORK>").replace(str(self.code), "<CODE>")
        return s

    def _script(self, argv: list[str]) -> Path | None:
        if "-m" in argv[:3] and argv.index("-m") + 1 < len(argv):  # python -m rockstar.cards.render_cards
            mod = self.code / (argv[argv.index("-m") + 1].replace(".", "/") + ".py")
            return mod if mod.is_file() else None
        for a in argv[:3]:
            p = Path(a)
            if p.suffix in {".sh", ".js", ".mjs", ".py"} and p.is_file():
                return p
        return None

    def _state(self, cwd: Path) -> dict[str, str]:
        if not cwd.is_dir():
            return {}
        return {str(p.relative_to(cwd)): file_sha(p) for p in sorted(cwd.rglob("*")) if p.is_file()}

    def run(self, cmd, *a, **k):
        argv = [str(c) for c in cmd]
        cwd = Path(k.get("cwd") or os.getcwd())
        script = self._script(argv)
        entry = {
            "argv": [self.norm(x) for x in argv],
            "script": script.name if script else None,
            "script_sha": file_sha(script) if script else None,
        }
        if script and script.name == "transcribe_fast.sh":
            return self._seed_transcripts(argv, entry)
        if not (cwd.is_dir() and str(cwd).startswith(str(self.work_base))):
            # the card stages run from the checkout on a folder in the work dir (--dir): hash that folder as the input
            dirs = [Path(x) for x in argv if x.startswith(str(self.work_base)) and Path(x).is_dir()]
            if dirs:
                entry["inputs"] = sha("".join(self._inputs(d) for d in dirs).encode())
            entry["how"] = "real (outside the work dir, uncached)"
            self.log.append(entry)
            return _REAL_RUN(cmd, *a, **k)
        before = self._state(cwd)
        entry["inputs"] = self._inputs(cwd)
        key = sha(json.dumps([entry["argv"], entry["script_sha"], self.siblings, before], sort_keys=True).encode())
        entry["key"] = key
        slot = self.cache / key if self.cache else None
        if slot and (slot / "result.json").is_file():
            res = json.loads((slot / "result.json").read_text())
            for rel in res["files"]:
                dst = cwd / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(slot / "files" / rel, dst)
            entry["how"] = "cache hit (same argv, script bytes and inputs)"
            self.log.append(entry)
            return subprocess.CompletedProcess(argv, res["returncode"], res["stdout"], res["stderr"])
        r = _REAL_RUN(cmd, *a, **k)
        after = self._state(cwd)
        changed = [p for p, h in after.items() if before.get(p) != h]
        entry["how"] = "real (cache miss)" if self.cache else "real"
        entry["returncode"] = r.returncode
        self.log.append(entry)
        if slot and r.returncode == 0:
            (slot / "files").mkdir(parents=True, exist_ok=True)
            for rel in changed:
                dst = slot / "files" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cwd / rel, dst)
            out = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode("utf-8", "replace")
            err = r.stderr if isinstance(r.stderr, str) else (r.stderr or b"").decode("utf-8", "replace")
            (slot / "result.json").write_text(
                json.dumps({"returncode": r.returncode, "stdout": out, "stderr": err, "files": changed})
            )
        return r

    def _seed_transcripts(self, argv, entry):
        work = Path(argv[2])
        (work / "transcripts").mkdir(parents=True, exist_ok=True)
        for name in ("transcript_fast.json", "transcript_fast.txt"):
            shutil.copyfile(self.snap / name, work / "transcripts" / name)
        entry["how"] = "seeded from the snapshot's transcripts (WhisperX not run, both arms)"
        self.log.append(entry)
        return subprocess.CompletedProcess(argv, 0, "seeded\n", "")


# ── child processes: the card step runs its stages as their own Python processes ───────────────────────────────

_SHIM = """# sitecustomize: written by sandbox_run.py. Loaded by every Python child the sandbox starts; it acts only
# when SANDBOX_HARNESS is set and the child runs on the harness's own venv (the card step's scripts; WhisperX has
# its own venv and is left alone), and gives that child the same
# fake Drive and the same model record/replay as the parent.
import os, sys
if os.environ.get("SANDBOX_HARNESS") and os.environ.get("SANDBOX_PREFIX") == sys.prefix:
    import importlib.util
    _s = importlib.util.spec_from_file_location("_sandbox_harness", os.environ["SANDBOX_HARNESS"])
    _h = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(_h)
    _h.install_child_fakes()
"""


def install_child_fakes() -> None:
    """In a card-step child: the fake Drive (same root) and the model on its own cassette (<cassette>.child.json),
    so the parent's in-memory tape never overwrites the child's records."""
    from pathlib import Path as _P

    drive = FakeDrive(_P(os.environ["SANDBOX_DRIVE_ROOT"]))
    for name in os.environ["SANDBOX_FAKE_MODULES"].split(","):
        sys.modules[name] = drive.module()
    auth = fake_google_auth()
    sys.modules[os.environ["SANDBOX_AUTH_MODULE"]] = auth
    sys.modules["rockstar.clients.google_auth"] = auth
    import anthropic as real_anthropic

    install_model(os.environ["SANDBOX_MODEL"], _P(os.environ["SANDBOX_CASSETTE"] + ".child.json"), real_anthropic)


# ── the manifest ──────────────────────────────────────────────────────────────────────────────────────────────────

_MASKS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b"), "<TS>"),
    (re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"), "<DATE>"),
    (re.compile(r"\b20\d{6}T\d{6}Z\b"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID>"),  # fake Tailor ids
]
_EVENT_DROP = {
    "ts",
    "t",
    "time",
    "latency_ms",
    "duration_ms",
    "dur_ms",
    "elapsed_s",
    "seconds",
    "wall_s",
    "job_id",
    "started_at",
    "finished_at",
    "pid",
    "rss_mb",
    "peak_rss_mb",
}


def mask(text: str, run: Path) -> str:
    text = text.replace(str(run), "<RUN>")
    for rx, rep in _MASKS:
        text = rx.sub(rep, text)
    return text


def is_text(p: Path) -> bool:
    return p.suffix.lower() in {".md", ".txt", ".json", ".jsonl", ".html", ".csv", ".srt", ".vtt", ".env"}


def mp4_shape(p: Path) -> str:
    """What a reel must keep between two renders of the same inputs: its streams, size, frame count and length."""
    probe = shutil.which("ffprobe") or "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
    out = _REAL_RUN(
        [
            probe,
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=codec_name,width,height,nb_read_frames:format=duration",
            "-of",
            "json",
            str(p),
        ],
        capture_output=True,
        text=True,
    )
    info = json.loads(out.stdout or "{}")
    streams = sorted(
        (s.get("codec_name"), s.get("width"), s.get("height"), s.get("nb_read_frames")) for s in info.get("streams", [])
    )
    return f"mp4 {float(info.get('format', {}).get('duration', 0)):.2f}s {streams}"


def build_manifest(run: Path) -> dict:
    files = {}
    for area in ("drive", "gmail", "tailor", "state"):
        base = run / area
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name in {"jobs.db", "jobs.db-wal", "jobs.db-shm"} or "/.trash/" in str(p):
                continue
            rel = str(p.relative_to(run))
            if is_text(p):
                files[rel] = sha(mask(p.read_text(encoding="utf-8", errors="replace"), run).encode())
            elif p.suffix == ".mp4":
                files[rel] = mp4_shape(p)  # the renderer isn't byte-deterministic run to run (control, 2026-10-14)
            else:
                files[rel] = file_sha(p)
    events = []
    log = run / "run_log.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            e = json.loads(line)
            events.append(
                {k: (mask(v, run) if isinstance(v, str) else v) for k, v in sorted(e.items()) if k not in _EVENT_DROP}
            )
    scripts = json.loads((run / "scripts.json").read_text()) if (run / "scripts.json").exists() else []
    # The SDK probes the platform (uname, file) on its first live call: the environment, not the pipeline.
    scripts = [x for x in scripts if x["script"] or not str(x.get("how", "")).startswith("real (outside")]
    outcomes = json.loads((run / "outcomes.json").read_text())["jobs"] if (run / "outcomes.json").exists() else []
    trash = (
        sorted(p.name.split("-", 1)[1] for p in (run / "drive" / ".trash").glob("*"))
        if (run / "drive" / ".trash").exists()
        else []
    )
    calls = (
        [c[0] for c in json.loads((run / "drive_calls.json").read_text())]
        if (run / "drive_calls.json").exists()
        else []
    )
    return {
        "outcomes": outcomes,
        "trashed": trash,
        "drive_calls": calls,
        "files": files,
        "events": events,
        "scripts": [
            {"argv": s["argv"], "script": s["script"], "script_sha": s["script_sha"], "inputs": s.get("inputs")}
            for s in scripts
        ],
    }


REEL_TOLERANCE_S = 0.5  # render_reel.js: the same inputs rendered twice differ by up to 0.33 s (measured 2026-10-14)


def _same_reel(x: str, y: str) -> bool:
    """Two reels match when their streams, codecs and size match and their lengths are within the render's own
    run-to-run variance. Their bytes never match: the renderer isn't deterministic."""
    mx, my = re.match(r"mp4 ([\d.]+)s (.*)", x), re.match(r"mp4 ([\d.]+)s (.*)", y)
    if not (mx and my):
        return False

    def kinds(streams):  # codec and size only: frame counts follow the length
        return re.sub(r", '\d+'\)", ")", streams)

    return kinds(mx.group(2)) == kinds(my.group(2)) and abs(float(mx.group(1)) - float(my.group(1))) <= REEL_TOLERANCE_S


def compare(a: Path, b: Path) -> int:
    ma, mb = (json.loads((x / "manifest.json").read_text()) for x in (a, b))
    problems = []
    for key in ("outcomes", "trashed", "drive_calls"):
        if ma.get(key) != mb.get(key):
            problems.append(f"{key}: A={json.dumps(ma.get(key))[:300]}\n           B={json.dumps(mb.get(key))[:300]}")
    fa, fb = ma["files"], mb["files"]
    variance = []
    for k in sorted(set(fa) | set(fb)):
        if k.endswith(".mp4") and k in fa and k in fb and _same_reel(fa[k], fb[k]):
            if fa[k] != fb[k]:
                variance.append(k.rsplit("/", 1)[-1])
            continue
        if fa.get(k) != fb.get(k):
            problems.append(
                f"file  {k}: {'missing in B' if k not in fb else 'missing in A' if k not in fa else 'differs'}"
            )
    if ma["events"] != mb["events"]:
        for i, (x, y) in enumerate(zip(ma["events"], mb["events"], strict=False)):
            if x != y:
                problems.append(f"event #{i}: A={json.dumps(x)[:300]}\n           B={json.dumps(y)[:300]}")
                break
        if len(ma["events"]) != len(mb["events"]):
            problems.append(f"events: A has {len(ma['events'])}, B has {len(mb['events'])}")

    def shape(entry):
        argv = " ".join(entry["argv"])
        argv = re.sub(r"-m rockstar\.cards\.(\w+)", r"<S>/\1.py", argv)
        argv = re.sub(r"<CODE>/(?:quote_cards|rockstar/cards)/(\w+\.py)", r"<S>/\1", argv)
        argv = re.sub(r"/(scripts|bin|reels)/", "/<S>/", argv)
        argv = re.sub(r"\S*/python3?(\.\d+)?\b", "<PY>", argv)
        return (entry["script"], entry["script_sha"], argv, entry.get("inputs"))

    sa = [shape(x) for x in ma["scripts"]]
    sb = [shape(x) for x in mb["scripts"]]
    if sa != sb:
        for i, (x, y) in enumerate(zip(sa, sb, strict=False)):
            if x != y:
                if (x[0], x[2]) != (y[0], y[2]):
                    what = "argv differs"
                elif x[3] != y[3]:
                    what = "INPUTS differ"
                else:
                    what = "bytes differ (same argv, same inputs)"
                problems.append(f"script #{i} {x[0]}: {what}\n            A={x}\n            B={y}")
        if len(sa) != len(sb):
            problems.append(f"scripts: A ran {len(sa)}, B ran {len(sb)}")
    print(f"A {a}\nB {b}")
    print(
        f"files {len(fa)} vs {len(fb)} · events {len(ma['events'])} vs {len(mb['events'])} · "
        f"scripts {len(sa)} vs {len(sb)}"
    )
    if problems:
        print(f"PARITY: {len(problems)} difference(s)")
        for p in problems:
            print("  " + p)
        return 1
    if variance:
        print(f"reels within the renderer's run-to-run variance (±{REEL_TOLERANCE_S}s): {', '.join(variance)}")
    print("PARITY: identical (files, ledger events, script argv and bytes; reels by shape)")
    return 0


# ── the run ───────────────────────────────────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--compare", nargs=2, type=Path)
    ap.add_argument("--remanifest", type=Path, help="rebuild OUT/manifest.json from a finished run (new masks)")
    ap.add_argument("--code", type=Path)
    ap.add_argument("--layout", choices=sorted(LAYOUTS), default="flat")
    ap.add_argument("--snapshot", type=Path, help="the episode's Drive folder snapshot")
    ap.add_argument("--episode")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--model", choices=["live", "record", "replay"])
    ap.add_argument("--cassette", type=Path)
    ap.add_argument("--script-cache", type=Path)
    ap.add_argument("--guest-csv", type=Path)
    ap.add_argument("--state", type=Path, help="episode_counter.json + publication_schedule.json to start from")
    ap.add_argument("--box-limits", type=Path)
    ap.add_argument("--work-base", type=Path, help="a fixed path, the same for both arms")
    ap.add_argument(
        "--env-file",
        type=Path,
        default=Path.home() / "RockstarPodcast/scripts/.env",
        help="ANTHROPIC_API_KEY for record/live (read, never printed)",
    )
    ap.add_argument("--venv", type=Path, default=Path.home() / "RockstarPodcast/scripts/whisperx_env")
    args = ap.parse_args()
    if args.compare:
        return compare(*args.compare)
    if args.remanifest:
        (args.remanifest / "manifest.json").write_text(
            json.dumps(build_manifest(args.remanifest), indent=1, sort_keys=True)
        )
        return 0

    code, out, layout = args.code.resolve(), args.out.resolve(), LAYOUTS[args.layout]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work_base = args.work_base.resolve()
    if work_base.exists():
        shutil.rmtree(work_base)
    work_base.mkdir(parents=True)
    state = out / "state"
    state.mkdir()
    for name in ("episode_counter.json", "publication_schedule.json"):
        shutil.copyfile(args.state / name, state / name)

    env = {
        "STATE_DIR": str(state),
        "DB_PATH": str(state / "jobs.db"),
        "RUN_LOG_PATH": str(out / "run_log.jsonl"),
        # flat code defaults to /opt/…/prompts; the package finds its own (config.prompts_dir), as on the box
        "PROMPTS_DIR": str(code / "prompts") if args.layout == "flat" else "",
        "BRAND_ASSETS_DIR": str(code / "BrandAssets"),
        "WORK_DIR_BASE": str(work_base),
        "VENV_PATH": str(args.venv),
        "BOX_LIMITS_FILE": str(args.box_limits),
        "BOX_LIMITS_TEST": "1",
        "DRIVE_RECORDINGS_INBOX_FOLDER_ID": "inbox",
        "DRIVE_EPISODES_FOLDER_ID": "episodes",
        "DRIVE_ROOT_FOLDER_ID": ".",
        "PIPELINE_ALERT_EMAIL": "alerts@example.invalid",
        "EDITORIAL_CALENDAR_EVENT": "off",
        "GMAIL_USER": "pipeline@example.invalid",
        "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
    }
    env.update({k: str(code / v) for k, v in layout["scripts_env"].items()})
    os.environ.update({k: v for k, v in env.items() if v})
    if args.layout == "package":  # nothing inherited may stand in for config's defaults
        for k in ("PROMPTS_DIR", "SCRIPTS_DIR", "REELS_DIR"):
            os.environ.pop(k, None)
    os.environ.pop("CREDENTIALS_DIRECTORY", None)
    if args.model == "replay":
        os.environ["ANTHROPIC_API_KEY"] = "sandbox-replay"  # never used: replay answers from the cassette
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        for line in args.env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"')

    # Drive: the audio in the inbox, the visuals in a fresh episode folder (as Heather drops them).
    drive = FakeDrive(out / "drive")
    audio = next(p for p in args.snapshot.iterdir() if p.suffix.lower() in _AUDIO)
    shutil.copyfile(audio, drive.root / "inbox" / audio.name)
    folder = drive.root / "episodes" / args.snapshot.name
    folder.mkdir()
    for p in args.snapshot.iterdir():
        if p.suffix.lower() in {".jpg", ".jpeg", ".png"} and ("-tile-" in p.name or "-reel-" in p.name):
            shutil.copyfile(p, folder / p.name)

    import anthropic as real_anthropic  # the venv's SDK, kept for live and record

    model_stats = install_model(args.model, args.cassette, real_anthropic)
    mods = layout["modules"]
    fakes = {
        mods["drive_client"]: drive.module(),
        mods["gmail_client"]: fake_gmail(out / "gmail"),
        mods["calendar_client"]: fake_calendar(),
        mods["google_auth"]: fake_google_auth(),
        "googleapiclient": types.ModuleType("googleapiclient"),
        "googleapiclient.discovery": drive.discovery(),
    }
    sys.modules.update(fakes)
    if args.layout == "flat":
        sys.modules.update({k: v for k, v in {"drive_client": fakes[mods["drive_client"]]}.items()})
    sys.path.insert(0, str(code))
    os.chdir(code)

    shim = out / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(_SHIM)
    os.environ.update(
        {
            "PYTHONPATH": str(shim),
            "SANDBOX_HARNESS": str(Path(__file__).resolve()),
            "SANDBOX_PREFIX": sys.prefix,
            "SANDBOX_DRIVE_ROOT": str(drive.root),
            "SANDBOX_FAKE_MODULES": ",".join(sorted({"drive_client", mods["drive_client"]})),
            "SANDBOX_AUTH_MODULE": mods["google_auth"],
            "SANDBOX_MODEL": args.model,
            "SANDBOX_CASSETTE": str(args.cassette),
        }
    )

    script_log: list = []
    scripts = Scripts(code, work_base, args.snapshot, args.script_cache, script_log)
    subprocess.run = scripts.run

    guest_form = importlib.import_module(mods["guest_form"])
    rows = guest_form.parse_csv(args.guest_csv.read_text(encoding="utf-8"))
    guest_form.fetch_rows = lambda: rows

    workspace = importlib.import_module(mods["workspace"])
    counter = {"n": 0}

    def fixed_work_dir(prefix):  # a fixed name, the same for both arms (outputs that embed paths compare)
        counter["n"] += 1
        p = work_base / f"{prefix}{counter['n']}"
        p.mkdir(parents=True)
        return p

    workspace.make_work_dir = fixed_work_dir

    tailor_client = importlib.import_module(mods["tailor_client"])
    spec = importlib.util.spec_from_file_location("sandbox_fake_tailor", code / layout["fake_tailor"])
    ft_mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(code / "tests"))  # the suite's own helpers (_swap) import from there
    spec.loader.exec_module(ft_mod)
    fake_tailor = ft_mod.FakeTailor()
    # the fake's own account: the pipeline logs in exactly as it does on the box, against the fake
    os.environ["TAILOR_BLOG_EMAIL"], os.environ["TAILOR_BLOG_PASSWORD"] = ft_mod.EMAIL, ft_mod.PASSWORD
    tailor_client.HttpTransport = lambda *a, **k: fake_tailor

    db = importlib.import_module(mods["db"])
    worker = importlib.import_module(mods["worker"])
    db.init_db()

    # Round 1, as the Drive poll would enqueue it; then every job it chains, through the worker's own dispatch.
    db.enqueue("ROUND_1", "TBD", {"trigger": "drive_sandbox", "file": audio.name})
    outcomes = []
    for _ in range(6):
        job = db.claim_next()
        if job is None:
            break
        try:
            worker._dispatch(job["job_type"], job)
            db.mark_done(job["id"])
            outcomes.append({"job": job["job_type"], "outcome": "done"})
        except BaseException as exc:  # noqa: BLE001 — a failure is a result to compare, not a crash
            db.mark_failed(job["id"], f"{type(exc).__name__}: {exc}")
            outcomes.append(
                {"job": job["job_type"], "outcome": "failed", "error": mask(f"{type(exc).__name__}: {exc}", out)}
            )
            if job["job_type"] == "ROUND_1":
                break
    if outcomes and outcomes[0]["outcome"] == "done" and not any(o["job"] == "PUBLISH_FINALIZE" for o in outcomes):
        # Phase 2 starts the way it does on the box when the visuals are in the folder: the Drive drain's own
        # trigger (TRIG-02), which reads the schedule entry Round 1 wrote. Nothing is enqueued by hand.
        drive_changes = importlib.import_module(mods["drive_changes"])
        started = drive_changes._maybe_resume_publish_finalize(drive._id(folder))
        if not started:
            outcomes.append(
                {
                    "job": "PUBLISH_FINALIZE",
                    "outcome": "failed",
                    "how": "visuals trigger",
                    "error": "the visuals trigger did not start Phase 2",
                }
            )
        else:
            job = db.claim_next()
            try:
                worker._dispatch(job["job_type"], job)
                db.mark_done(job["id"])
                outcomes.append({"job": job["job_type"], "outcome": "done", "how": "visuals trigger"})
            except BaseException as exc:  # noqa: BLE001
                db.mark_failed(job["id"], f"{type(exc).__name__}: {exc}")
                outcomes.append(
                    {
                        "job": job["job_type"],
                        "outcome": "failed",
                        "how": "visuals trigger",
                        "error": mask(f"{type(exc).__name__}: {exc}", out),
                    }
                )

    tailor_dir = out / "tailor"
    tailor_dir.mkdir()
    for slug, page in sorted(fake_tailor.pages.items()):
        body = {k: v for k, v in page.items() if k not in {"id", "created_at", "updated_at", "owner_id"}}
        (tailor_dir / f"{slug}.json").write_text(
            json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        )
    (tailor_dir / "media.json").write_text(
        json.dumps(
            [{k: v for k, v in m.items() if k != "id"} if isinstance(m, dict) else str(m) for m in fake_tailor.media],
            indent=1,
            default=str,
        )
    )
    (out / "scripts.json").write_text(json.dumps(script_log, indent=1))
    (out / "drive_calls.json").write_text(json.dumps(drive.calls, indent=1))
    (out / "outcomes.json").write_text(
        json.dumps({"jobs": outcomes, "model": {k: v for k, v in model_stats.items() if k != "keys"}}, indent=2)
    )
    (out / "manifest.json").write_text(json.dumps(build_manifest(out), indent=1, sort_keys=True))
    print(
        json.dumps(
            {
                "episode": args.episode,
                "jobs": outcomes,
                "model": {k: v for k, v in model_stats.items() if k != "keys"},
                "scripts": [f"{s['script']}: {s['how']}" for s in script_log],
            },
            indent=2,
        )
    )
    return 0 if outcomes and all(o["outcome"] == "done" for o in outcomes) else 3


if __name__ == "__main__":
    sys.exit(main())
