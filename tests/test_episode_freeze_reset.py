"""
test_episode_freeze_reset.py — freeze and reset an episode for a re-run from scratch (the E13 pattern, any episode).

A rehearsal: scripts/freeze-episode.sh and scripts/reset-episode.sh run for real against a fake box. ssh runs its
command here; the box's drive_client is a stand-in backed by a JSON "Drive", so rockstar/tools/drive_inventory.py and
rockstar/tools/drive_reset.py run their real code against it. Every name is invented (tests are exported).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
TMP = Path(tempfile.mkdtemp(prefix="epreset-"))

FAKE_DRIVE_CLIENT = r'''
"""A stand-in drive_client: Drive is a JSON file (DRIVE_STATE). Only what the freeze and reset tools call."""
import hashlib, json, os

PATH = os.environ["DRIVE_STATE"]
def _load(): return json.load(open(PATH))
def _save(d): json.dump(d, open(PATH, "w"), indent=1)
def _env(v): return os.environ[v]
def export_mime_for(m, n): return "text/plain"

class _Req:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()

class _Files:
    def get(self, fileId, fields=None):
        return _Req(lambda: dict(next(i for i in _load()["items"] if i["id"] == fileId)))
    def list(self, q, fields=None, pageSize=None, pageToken=None):
        parent = q.split("'")[1]
        return _Req(lambda: {"files": [i for i in _load()["items"] if parent in i["parents"] and not i["trashed"]]})
    def get_media(self, fileId): return {"content": next(i for i in _load()["items"] if i["id"] == fileId).get("content", "")}
    export_media = lambda self, fileId, mimeType: self.get_media(fileId)

class _Revs:
    def list(self, fileId, pageSize=None, fields=None): return _Req(lambda: {"revisions": [{"id": "1"}]})

class _Svc:
    def files(self): return _Files()
    def revisions(self): return _Revs()

def _svc(): return _Svc()

def _new(name, mime, parent, content=""):
    d = _load(); fid = f"id{len(d['items']) + 1}"
    d["items"].append({"id": fid, "name": name, "mimeType": mime, "parents": [parent], "trashed": False,
                       "content": content, "md5Checksum": hashlib.md5(content.encode()).hexdigest(),
                       "modifiedTime": "2026-10-12T00:00:00Z", "createdTime": "2026-10-12T00:00:00Z"})
    _save(d); return {"id": fid, "name": name}

def create_folder(name, parent): return _new(name, "application/vnd.google-apps.folder", parent)
def upload_text(content, filename, folder, mime_type="text/plain"): return _new(filename, mime_type, folder, content)

def _set(fid, **kw):
    d = _load()
    for i in d["items"]:
        if i["id"] == fid: i.update(kw)
    _save(d)

def move_file(fid, dest): _set(fid, parents=[dest])
def trash_file(fid): _set(fid, trashed=True)
'''

FAKE_HTTP = r"""
class MediaIoBaseDownload:
    def __init__(self, buf, req, chunksize=None): self.buf, self.req = buf, req
    def next_chunk(self):
        self.buf.write(self.req["content"].encode()); return None, True
"""

FAKE_DRIVE_CHANGES = r'''
"""A stand-in for drive_changes' Phase 2 guard: the schedule at SCHEDULE, the real statuses' shape."""
import os
from pathlib import Path
_PUB_SCHEDULE_PATH = Path(os.environ["SCHEDULE"])
_PHASE2_READY_STATUSES = ("live", "awaiting_visuals")
'''

FOLDER = "application/vnd.google-apps.folder"


def _drive():
    """E7, an invented episode: a recording, images, pipeline outputs, reels/ with a reel, cards/ with a slide."""
    import hashlib

    items = []

    def add(fid, name, parent, mime="text/plain", content="x"):
        items.append(
            {
                "id": fid,
                "name": name,
                "mimeType": mime,
                "parents": [parent],
                "trashed": False,
                "content": content,
                "md5Checksum": hashlib.md5(content.encode()).hexdigest() if mime != FOLDER else None,
                "modifiedTime": "2026-10-11T00:00:00Z",
                "createdTime": "2026-10-11T00:00:00Z",
            }
        )

    add("eps", "Episodes", "root", FOLDER)
    add("f7", "E7-jane", "eps", FOLDER)
    add("f70", "E70-other", "eps", FOLDER)  # E7 must never match E70
    add("a", "E7-jane.m4a", "f7", "audio/mp4", "the recording")
    add("t", "E7-tile-jane.jpg", "f7", "image/jpeg")
    add("t2", "E7-reel-jane.jpg", "f7", "image/jpeg")
    add("q", "quotes.json", "f7", "application/json", '{"quotes": []}')
    add("faq", "faq.md", "f7")
    add("ty", "thank_you.md", "f7")
    add("r", "reels", "f7", FOLDER)
    add("r1", "reel_01_x.mp4", "r", "video/mp4")
    add("c", "cards", "f7", FOLDER)
    add("c1", "E7_q01_slide.png", "c", "image/png")
    return {"items": items}


def _stage():
    root = Path(tempfile.mkdtemp(dir=TMP))
    app, bin_, home = root / "app", root / "bin", root / "home"
    for d in (
        app / "venv" / "bin",
        app / "state",
        app / "scripts",
        app / "googleapiclient",
        bin_,
        home / "RockstarPodcast",
    ):
        d.mkdir(parents=True)
    os.symlink(sys.executable, app / "venv" / "bin" / "python")
    (app / "rockstar" / "clients").mkdir(parents=True)  # the box's package layout since the 2026-10-14 remodel
    for init in (app / "rockstar" / "__init__.py", app / "rockstar" / "clients" / "__init__.py"):
        init.write_text("")
    (app / "rockstar/clients/drive_client.py").write_text(FAKE_DRIVE_CLIENT)
    (app / "rockstar/clients/drive_changes.py").write_text(FAKE_DRIVE_CHANGES)
    schedule = root / "schedule.json"
    schedule.write_text(json.dumps({"episodes": [{"episode_number": 7, "status": "done", "pipeline_done": True}]}))
    (app / "googleapiclient" / "__init__.py").write_text("")
    (app / "googleapiclient" / "http.py").write_text(FAKE_HTTP)
    (app / ".env").write_text("DRIVE_EPISODES_FOLDER_ID=eps\n")
    for f in ("publication_schedule.json", "blog_posts.json", "episode_counter.json", "run_log.jsonl"):
        (app / "state" / f).write_text("{}")
    import sqlite3

    db = sqlite3.connect(app / "state" / "jobs.db")
    db.execute("create table jobs (id integer primary key, episode_id text, payload text, status text, dedup_key text)")
    db.executemany(
        "insert into jobs (episode_id, payload, status, dedup_key) values (?, ?, ?, ?)",
        [
            ("E7", "{}", "done", "visuals:E7:t:t2"),
            ("TBD", '{"episode_number": 7}', "failed", None),
            ("E70", "{}", "done", None),
            ("x", "not json", "done", None),
        ],
    )
    db.commit()
    db.close()
    (app / "rockstar" / "tools").mkdir(parents=True, exist_ok=True)
    (app / "rockstar" / "tools" / "__init__.py").write_text("")
    (app / "rockstar" / "tools" / "ledger_summary.py").write_text("print('ledger for', __import__('sys').argv[1])\n")
    state = root / "drive.json"
    state.write_text(json.dumps(_drive()))
    tools = {
        "ssh": 'while [ "${1#-}" != "$1" ]; do shift 2; done; shift; exec bash -c "$*"\n',
        "scp": 'args=(); for a in "$@"; do case "$a" in -q) ;; *) args+=("${a#*:}") ;; esac; done\n'
        'n=${#args[@]}; cp "${args[@]:0:n-1}" "${args[n-1]}"\n',
        "journalctl": 'echo "worker journal"\n',
    }
    for name, body in tools.items():
        (bin_ / name).write_text("#!/bin/bash\n" + body)
        (bin_ / name).chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "HOME": str(home),
        "BOX_SERVER_OVERRIDE": "fake-box",
        "BOX_APP_OVERRIDE": str(app),
        "FREEZE_ROOT": str(root / "freeze"),
        "MAC_AUDIO_DIR": str(home / "RockstarPodcast"),
        "DRIVE_STATE": str(state),
        "SINCE": "2026-10-01 00:00",
        "SCHEDULE": str(schedule),
    }

    def run(script, *a):
        return subprocess.run(["bash", str(REPO / "scripts" / script), *a], capture_output=True, text=True, env=env)

    return run, state, root, home


def _edit(state, fn):
    d = json.loads(state.read_text())
    fn(d["items"])
    state.write_text(json.dumps(d))


def _items(state):
    return {i["id"]: i for i in json.loads(state.read_text())["items"]}


def test_freeze_then_check_then_reset():
    run, state, root, home = _stage()
    r = run("freeze-episode.sh", "E7")
    assert r.returncode == 0, r.stdout + r.stderr
    freeze = root / "freeze" / "E7"
    inv = json.loads((freeze / "drive" / "inventory.json").read_text())
    assert inv["folder"]["name"] == "E7-jane" and {e["path"] for e in inv["items"]} >= {"E7-jane/reels/reel_01_x.mp4"}
    jobs = json.loads((freeze / "jobs-rows.json").read_text())
    assert sorted(j["episode_id"] for j in jobs) == ["E7", "TBD"], jobs  # by code or by number; never E70
    assert (freeze / "SHA256SUMS").exists() and not os.access(freeze / "SHA256SUMS", os.W_OK)

    before = state.read_text()
    r = run("reset-episode.sh", "E7", "--check")
    assert r.returncode == 0 and "nothing changed" in r.stdout, r.stdout + r.stderr
    assert state.read_text() == before, "--check changed Drive"
    assert not (home / "RockstarPodcast" / "E7-jane.m4a").exists()

    r = run("reset-episode.sh", "E7")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "will NOT start by itself" in r.stdout and "-m rockstar.tools.reprocess_episode --episode 7" in r.stdout, (
        r.stdout
    )
    assert (home / "RockstarPodcast" / "E7-jane.m4a").read_text() == "the recording"
    items = _items(state)
    assert all(items[f]["trashed"] for f in ("q", "faq", "ty", "a")), "outputs and the audio copy trashed"
    assert not items["t"]["trashed"] and items["t"]["parents"] == ["f7"], "the images stay"
    quarantine = next(i for i in items.values() if i["name"] == "_DO-NOT-POST")
    label = next(i for i in items.values() if quarantine["id"] in i["parents"])
    assert label["name"].endswith("-E7-redo"), label
    assert items["r"]["parents"] == [label["id"]] and items["c"]["parents"] == [label["id"]], "reels/ and cards/ moved"
    assert not items["r"]["trashed"] and not items["r1"]["trashed"], "quarantined, not trashed"
    readme = next(i for i in items.values() if i["name"] == "README.txt")
    assert "DO NOT POST" in readme["content"] and "E7-jane/reels" in readme["content"]


def test_a_change_since_the_freeze_stops_everything():
    run, state, root, home = _stage()
    assert run("freeze-episode.sh", "E7").returncode == 0
    d = json.loads(state.read_text())
    next(i for i in d["items"] if i["id"] == "faq")["modifiedTime"] = "2026-10-12T09:00:00Z"  # someone edited faq.md
    state.write_text(json.dumps(d))
    before = state.read_text()
    r = run("reset-episode.sh", "E7")
    assert r.returncode != 0 and "changed since the freeze" in r.stdout, r.stdout + r.stderr
    assert state.read_text() == before, "a stopped reset changed Drive"
    assert not (home / "RockstarPodcast" / "E7-jane.m4a").exists()


def test_a_new_file_in_reels_stops_the_quarantine():
    run, state, root, home = _stage()
    assert run("freeze-episode.sh", "E7").returncode == 0
    d = json.loads(state.read_text())
    d["items"].append({**d["items"][-3], "id": "r2", "name": "reel_02_new.mp4"})  # a reel added after the freeze
    state.write_text(json.dumps(d))
    r = run("reset-episode.sh", "E7", "--check")
    assert r.returncode != 0 and "its contents changed" in r.stdout, r.stdout + r.stderr


def test_a_freeze_is_made_once_and_a_reset_needs_one():
    run, state, root, home = _stage()
    r = run("reset-episode.sh", "E7", "--check")
    assert r.returncode == 2 and "no frozen inventory" in r.stderr, r.stderr
    assert run("freeze-episode.sh", "E7").returncode == 0
    r = run("freeze-episode.sh", "E7")
    assert r.returncode == 2 and "already exists" in r.stderr, r.stderr


# ── Toby's review: names come from Drive, and anyone with the folder can set them ──


def test_a_hostile_recording_name_never_reaches_a_shell():
    """Two names a person with the folder could set: one with a path, one without. Either is refused; neither runs."""
    for name in ("E7-x';touch {canary};'.m4a", "E7-x';touch${{IFS}}pwned-canary;'.m4a"):  # no space: ${IFS}
        run, state, root, home = _stage()
        canary = root / "pwned"
        _edit(
            state,
            lambda items, name=name, canary=canary: next(i for i in items if i["id"] == "a").update(
                name=name.format(canary=canary)
            ),
        )
        assert run("freeze-episode.sh", "E7").returncode == 0
        before = state.read_text()
        for args in (("E7", "--check"), ("E7",)):
            r = run("reset-episode.sh", *args)
            assert r.returncode == 2 and "RESET STOP" in r.stdout + r.stderr, (name, r.stdout + r.stderr)
        assert state.read_text() == before
        hits = [p for d in (root, home, REPO, Path.cwd()) for p in d.rglob("pwned*")] + [canary] * canary.exists()
        assert not hits, f"a Drive name ran as a command: {hits}"


def test_a_name_twice_stops_the_plan():
    run, state, root, home = _stage()
    _edit(state, lambda items: items.append({**next(i for i in items if i["id"] == "q"), "id": "q2"}))
    assert run("freeze-episode.sh", "E7").returncode == 0
    r = run("reset-episode.sh", "E7", "--check")
    assert r.returncode == 2 and "twice" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_file_in_two_folders_is_not_moved_out_of_both():
    run, state, root, home = _stage()
    assert run("freeze-episode.sh", "E7").returncode == 0
    _edit(state, lambda items: next(i for i in items if i["id"] == "faq")["parents"].append("elsewhere"))
    before = state.read_text()
    r = run("reset-episode.sh", "E7")
    assert r.returncode != 0 and "another folder too" in r.stdout, r.stdout
    assert state.read_text() == before


def test_an_episode_still_waiting_on_phase_2_is_not_reset():
    run, state, root, home = _stage()
    (root / "schedule.json").write_text(
        json.dumps({"episodes": [{"episode_number": 7, "status": "live", "pipeline_done": False}]})
    )
    assert run("freeze-episode.sh", "E7").returncode == 0
    r = run("reset-episode.sh", "E7", "--check")
    assert r.returncode != 0 and "starts Phase 2" in r.stdout, r.stdout


def test_a_failure_between_trash_and_quarantine_says_what_was_done():
    run, state, root, home = _stage()
    assert run("freeze-episode.sh", "E7").returncode == 0
    client = next(root.glob("app/rockstar/clients/drive_client.py"))
    client.write_text(client.read_text() + "\ndef create_folder(name, parent):\n    raise RuntimeError('Drive 503')\n")
    r = run("reset-episode.sh", "E7")
    assert r.returncode != 0 and "RESET PARTIAL" in r.stdout and "trashed E7-jane/quotes.json" in r.stdout, r.stdout
    assert "undo:" in r.stdout


def test_the_freeze_never_writes_a_name_outside_its_folder():
    run, state, root, home = _stage()
    _edit(
        state,
        lambda items: items.append(
            {
                **next(i for i in items if i["id"] == "faq"),
                "id": "evil",
                "name": "../../../escaped.txt",
                "content": "escaped",
            }
        ),
    )
    r = run("freeze-episode.sh", "E7")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not list(root.rglob("escaped.txt")), list(root.rglob("escaped.txt"))
    inv = json.loads((root / "freeze" / "E7" / "drive" / "inventory.json").read_text())
    assert any(e.get("unsafe_name") for e in inv["items"])


def test_a_replaced_image_means_phase_2_can_start_by_itself():
    """The first run's key names the old reel image; a new one was dropped since, so the trigger is live again."""
    run, state, root, home = _stage()
    _edit(state, lambda items: next(i for i in items if i["id"] == "t2").update(id="t3"))
    assert run("freeze-episode.sh", "E7").returncode == 0
    r = run("reset-episode.sh", "E7", "--check")
    assert r.returncode == 0 and "can start it by itself" in r.stdout and "NOT start" not in r.stdout, r.stdout


def test_two_copies_inside_cards_do_not_block_the_reset():
    """CARD-02: upload_file creates, so cards/ can hold a slide twice; moving the folder keeps both."""
    run, state, root, home = _stage()
    _edit(state, lambda items: items.append({**next(i for i in items if i["id"] == "c1"), "id": "c2"}))
    assert run("freeze-episode.sh", "E7").returncode == 0
    r = run("reset-episode.sh", "E7", "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_name_with_an_escape_sequence_prints_cleaned():
    run, state, root, home = _stage()
    _edit(state, lambda items: next(i for i in items if i["id"] == "faq").update(name="faq\x1b[2J.md"))
    assert run("freeze-episode.sh", "E7").returncode == 0
    r = run("reset-episode.sh", "E7", "--check")
    assert "\x1b" not in r.stdout, repr(r.stdout[-300:])


def test_bad_episode_codes_are_refused():
    run, *_ = _stage()
    for bad in ("7", "E7; rm -rf /", "e7"):
        assert run("freeze-episode.sh", bad).returncode == 2
        assert run("reset-episode.sh", bad).returncode == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
