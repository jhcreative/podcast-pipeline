"""
drive_reset.py — an episode reset's Drive half, by the ids in a frozen inventory (scripts/reset-episode.sh).

First written for the E13 reset (Jen, 2026-10-12); now any episode, with a quarantine. Rewritten after Toby's review
(2026-10-12): no Drive name ever crosses ssh or reaches a shell. This module picks its own targets from fixed rules, by
id, and the reset script passes it only fixed words (the inventory's path, a scratch path, the episode's label).

  python drive_reset.py plan        INVENTORY            the targets, as JSON (runs anywhere: no Drive call)
  python drive_reset.py describe    INVENTORY LOCAL LABEL JOBS   the same, for a person, and how Phase 2 will start
  python drive_reset.py fetch-audio INVENTORY OUT       download the recording by its frozen id to OUT; md5 checked
  python drive_reset.py reset       INVENTORY LABEL [--check]  trash the outputs and the folder's audio copy, then
                                                        QUARANTINE
                                                        reels/ and cards/ into <episode>/_DO-NOT-POST/<date>-<EP>-redo/

The rules: the recording is the one <EP>-*.m4a at the folder's top level; the outputs are OUTPUTS at the top level;
reels/ and cards/ are quarantined (moved, kept as evidence, QUOTE-03 Q2). A name that appears twice stops the plan
(Drive allows duplicates: one copy would hide the other).
Before any write, every target is re-checked against the freeze: a file's md5 and modifiedTime; a folder's direct
children (one level: a move keeps everything inside it); not trashed; parents exactly its frozen parent. If anything
differs, nothing is written. Then, if a Drive change could start Phase 2 on this episode (its schedule entry still
awaiting Phase 2), it stops too. A failure partway prints what was done, what wasn't, and the undo.
Trash, not delete: 30 days to undo. A quarantine is a move: undo by moving back.
"""

import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FOLDER = "application/vnd.google-apps.folder"
QUARANTINE = "_DO-NOT-POST"
OUTPUTS = (
    "apple_summary.md",
    "blog_post.json",
    "boundaries.json",
    "faq.md",
    "quotes.json",
    "transcript_fast.json",
    "transcript_fast.txt",
    "thank_you.md",
    ".pipeline_done",
)
QUARANTINED = ("reels", "cards")
AUDIO_TYPES = (".m4a", ".mp3", ".wav", ".aac", ".flac")


def load(inv_path):
    return json.loads(Path(inv_path).read_text(encoding="utf-8"))


def safe(name: str) -> str:
    """A Drive name for a terminal: control characters (an ANSI escape could rewrite the screen) shown as '?'."""
    return "".join(c if c.isprintable() else "?" for c in str(name))


def plan(inv: dict, label: str | None = None) -> dict:
    """The targets, by id, from fixed rules. Raises ValueError (with every reason) if the plan isn't safe to run."""
    ep, root = inv["episode"].lower(), inv["folder"]
    top = [e for e in inv["items"] if e["path"].count("/") == 1]
    problems = []
    seen: dict[str, int] = {}
    for e in top:  # the top level: what's trashed, moved or read by the re-run (inside a moved folder, both copies go)
        seen[e["path"]] = seen.get(e["path"], 0) + 1
    dupes = sorted(p for p, n in seen.items() if n > 1)
    if dupes:
        problems.append(f"names in the folder twice (one copy would hide the other): {dupes}")
    audio = [e for e in top if e["name"].lower().startswith(ep + "-") and e["name"].lower().endswith(".m4a")]
    others = [e["name"] for e in top if e["name"].lower().endswith(AUDIO_TYPES) and e not in audio]
    if len(audio) != 1:
        problems.append(
            f"expected one {inv['episode']}-*.m4a at the top level, found {[e['name'] for e in audio]}"
            + (f"; other audio: {others}" if others else "")
        )
    if problems:
        raise ValueError("; ".join(problems))

    def pick(es):
        return [{"id": e["id"], "path": e["path"], "folder": e["mimeType"] == FOLDER} for e in es]

    return {
        "episode": inv["episode"],
        "folder": root["name"],
        "folder_id": root["id"],
        "audio": {
            "id": audio[0]["id"],
            "path": audio[0]["path"],
            "name": audio[0]["name"],
            "md5": audio[0].get("md5Checksum"),
        },
        "trash": pick([e for e in top if e["name"] in OUTPUTS] + audio),
        "quarantine": pick([e for e in top if e["mimeType"] == FOLDER and e["name"] in QUARANTINED]),
        "label": label or f"{datetime.now(timezone.utc):%Y-%m-%d}-{inv['episode']}-redo",
    }


def _children(svc, folder_id):
    out, token = [], None
    while True:
        r = (
            svc.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name)",
                pageSize=200,
                pageToken=token,
            )
            .execute()
        )
        out += r.get("files", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def check(inv: dict, targets: list[dict]) -> list[str]:
    """Why any target differs from the freeze (empty: all match)."""
    from rockstar.clients import drive_client

    svc = drive_client._svc()
    by_id = {e["id"]: e for e in inv["items"]}
    problems = []
    for t in targets:
        e = by_id[t["id"]]
        now = svc.files().get(fileId=e["id"], fields="id, md5Checksum, modifiedTime, trashed, parents").execute()
        diffs = []
        if t["folder"]:
            frozen = {x["id"] for x in inv["items"] if x["path"].rsplit("/", 1)[0] == e["path"]}
            if {c["id"] for c in _children(svc, e["id"])} != frozen:
                diffs.append("its contents changed")
        else:
            diffs += [k for k in ("md5Checksum", "modifiedTime") if now.get(k) != e.get(k)]
        if now.get("trashed"):
            diffs.append("already trashed")
        if (now.get("parents") or []) != [inv["folder"]["id"]]:
            diffs.append("not where it was (or in another folder too)")
        if diffs:
            problems.append(f"{safe(e['path'])}: changed since the freeze ({', '.join(diffs)})")
    return problems


def phase2_could_start(episode: str) -> str:
    """'' or why a Drive change in this episode's folder could enqueue Phase 2 (drive_changes' own guard)."""
    try:
        from rockstar.clients import drive_changes

        schedule = json.loads(drive_changes._PUB_SCHEDULE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 -- can't tell, so say so: a person decides
        return f"can't read the schedule to rule it out ({type(exc).__name__})"
    for ep in schedule.get("episodes", []):
        if (
            f"E{ep.get('episode_number')}" == episode
            and ep.get("status") in drive_changes._PHASE2_READY_STATUSES
            and not ep.get("pipeline_done")
        ):
            return (
                f"its schedule entry is '{ep.get('status')}' and not pipeline_done: a Drive change here starts Phase 2"
            )
    return ""


def phase2_start(jobs_rows_path: str, episode: str, inv: dict | None = None) -> str:
    """How the redo's Phase 2 will start. The jobs table's dedup index is unique across every row ever, and the visuals
    trigger's key is visuals:E<N>:<tile id>:<reel id>: if the first run used the tile and reel the reset keeps, the
    same images can't enqueue again (Toby: match the key to the images in the folder now, not just any key)."""
    try:
        rows = json.loads(Path(jobs_rows_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown (no frozen job rows): after Round 1, start Phase 2 by hand if it hasn't started"
    keys = {str(r.get("dedup_key")) for r in rows if str(r.get("dedup_key") or "").startswith(f"visuals:{episode}:")}
    kept = None
    if inv is not None:
        top = [e for e in inv["items"] if e["path"].count("/") == 1]

        def img(kind):
            return next(
                (
                    e["id"]
                    for e in top
                    if f"-{kind}" in e["name"].lower()
                    and e["name"].lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                ),
                None,
            )

        tile, reel = img("tile"), img("reel")
        kept = f"visuals:{episode}:{tile}:{reel}" if tile and reel else None
    if kept and kept in keys:
        return (
            "it will NOT start by itself: the tile and reel kept in the folder already enqueued Phase 2 once. "
            "After Round 1, review apple_summary.md, then start it"
        )
    if keys and kept is None:
        return (
            "it may NOT start by itself (a visuals key exists; the folder's images couldn't be matched to it). "
            "After Round 1, review apple_summary.md, then start it if it hasn't"
        )
    return (
        "the visuals trigger can start it by itself once Round 1 marks the episode ready (the images in the "
        "folder haven't enqueued it before): review apple_summary.md before then"
    )


def fetch_audio(inv_path, out):
    from googleapiclient.http import MediaIoBaseDownload

    from rockstar.clients import drive_client

    p = plan(load(inv_path))
    req = drive_client._svc().files().get_media(fileId=p["audio"]["id"])
    h, n = hashlib.md5(), 0
    with open(out, "wb") as fh:
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
            chunk = buf.getvalue()
            fh.write(chunk)
            h.update(chunk)
            n += len(chunk)
            buf.seek(0)
            buf.truncate()
    if h.hexdigest() != p["audio"]["md5"]:
        print(f"RESET STOP: the recording downloaded with md5 {h.hexdigest()}, frozen {p['audio']['md5']}")
        return 2
    print(f"fetched the recording: {n} bytes, md5 matches the freeze")
    return 0


def reset(inv_path, check_only=False, label=None):
    from rockstar.clients import drive_client

    inv = load(inv_path)
    p = plan(inv, label)
    problems = check(inv, p["trash"] + p["quarantine"])
    why = phase2_could_start(p["episode"])
    if why:
        problems.append(f"{p['episode']}: {why}")
    if problems:
        print("RESET STOP: nothing changed.")
        for x in problems:
            print("  " + x)
        return 2
    where = f"{safe(p['folder'])}/{QUARANTINE}/{p['label']}"
    print(
        f"all {len(p['trash']) + len(p['quarantine'])} targets match the freeze; trash {len(p['trash'])}, "
        f"quarantine {len(p['quarantine'])} to {where}" + ("; --check: nothing changed" if check_only else "")
    )
    if check_only:
        return 0
    done: list[str] = []
    try:
        for t in p["trash"]:
            drive_client.trash_file(t["id"])
            done.append(f"trashed {safe(t['path'])} ({t['id']})")
            print("  " + done[-1])
        if p["quarantine"]:
            svc = drive_client._svc()
            top = next(
                (c for c in _children(svc, p["folder_id"]) if c["name"] == QUARANTINE), None
            ) or drive_client.create_folder(QUARANTINE, p["folder_id"])
            dest = next(
                (c for c in _children(svc, top["id"]) if c["name"] == p["label"]), None
            ) or drive_client.create_folder(p["label"], top["id"])
            drive_client.upload_text(
                "DO NOT POST anything in this folder.\n\n"
                f"Made by a pipeline run of {p['episode']} that is being redone "
                f"from scratch ({p['label']}). Under ST-10 and QUOTE-01 its quotes may not be what the speaker said.\n"
                f"Moved here, not trashed, as evidence:\n"
                + "".join(f"  - {safe(t['path'])}\n" for t in p["quarantine"]),
                "README.txt",
                dest["id"],
            )
            for t in p["quarantine"]:
                drive_client.move_file(t["id"], dest["id"])
                done.append(f"quarantined {safe(t['path'])} ({t['id']})")
                print("  " + done[-1])
    except Exception as exc:  # noqa: BLE001 -- say exactly where it stopped, then stop
        print(f"RESET PARTIAL: stopped by {type(exc).__name__}: {exc}")
        print("  done:" + "".join(f"\n    {d}" for d in done) if done else "  done: nothing")
        print("  undo: restore the trashed ids from Drive's Trash; move quarantined ids back to the episode folder.")
        return 3
    left = sorted(safe(c["name"]) for c in _children(drive_client._svc(), p["folder_id"]))
    print(f"left in {safe(p['folder'])}: {left}")
    return 0


def main(argv):
    cmd, rest = argv[0], [a for a in argv[1:] if a != "--check"]
    try:
        if cmd == "plan":
            print(json.dumps(plan(load(rest[0]))))
            return 0
        if (
            cmd == "describe"
        ):  # describe INVENTORY LOCAL_AUDIO LABEL JOBS_ROWS: the plan for a person (printed, never run)
            p = plan(load(rest[0]), rest[2])
            print(f"   recording:  {safe(p['audio']['path'])}  ->  {rest[1]}")
            print("   trash:      " + ", ".join(safe(t["path"]) for t in p["trash"]))
            print(
                "   quarantine: "
                + (", ".join(safe(t["path"]) for t in p["quarantine"]) or "(no reels/ or cards/)")
                + f"  ->  {safe(p['folder'])}/{QUARANTINE}/{p['label']}/"
            )
            print(f"   phase 2:    {phase2_start(rest[3], p['episode'], load(rest[0]))}")
            return 0
        if cmd == "phase2":  # phase2 JOBS_ROWS EP [INVENTORY]
            print(phase2_start(rest[0], rest[1], load(rest[2]) if len(rest) > 2 else None))
            return 0
        if cmd == "fetch-audio":
            return fetch_audio(rest[0], rest[1])
        if cmd == "reset":  # reset INVENTORY LABEL [--check]
            if len(rest) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}-E\d+-redo", rest[1]):
                print("RESET STOP: a label like 2026-10-12-E6-redo is required")
                return 2
            return reset(rest[0], "--check" in argv, rest[1])
    except ValueError as exc:
        print(f"RESET STOP: {exc}")
        return 2
    print(f"unknown command {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
