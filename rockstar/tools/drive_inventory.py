"""drive_inventory.py — READ-ONLY inventory of one episode's Drive folder, for a freeze (scripts/freeze-episode.sh).

First written for the E13 baseline freeze (Jen, 2026-10-12); now any episode. Runs on the box (Drive credentials live
there), with /opt/rockstar-pipeline on PYTHONPATH and its .env loaded; the freeze script copies it up and removes it.
Calls only files.list, files.get, revisions.list, get_media and export. It writes nothing to Drive.

Writes into OUT_DIR:
  inventory.json    every file and folder under the episode folder: id, path, mime, created/modified time,
                    size, md5, last modifying user, and each file's revision list
  files/<path>      the current content of each file, except audio/video (recorded by md5 only).
                    Google Docs are exported (markdown for *.md names, plain text otherwise).
Usage: python -m rockstar.tools.drive_inventory EP OUT_DIR      (EP like E6: matches E6 and E6-…, never E60)
"""

import io
import json
import re
import sys
from pathlib import Path

from googleapiclient.http import MediaIoBaseDownload

from rockstar.clients import drive_client

FOLDER = "application/vnd.google-apps.folder"
SKIP_CONTENT = ("audio/", "video/")
FILE_FIELDS = (
    "id, name, mimeType, createdTime, modifiedTime, size, md5Checksum, trashed, "
    "lastModifyingUser(emailAddress, displayName)"
)


def children(svc, folder_id):
    out, token = [], None
    while True:
        r = (
            svc.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields=f"nextPageToken, files({FILE_FIELDS})",
                pageSize=200,
                pageToken=token,
            )
            .execute()
        )
        out += r.get("files", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def revisions(svc, file_id):
    try:
        r = (
            svc.revisions()
            .list(
                fileId=file_id,
                pageSize=200,
                fields="revisions(id, modifiedTime, size, md5Checksum, keepForever, "
                "lastModifyingUser(emailAddress, displayName))",
            )
            .execute()
        )
        return r.get("revisions", [])
    except Exception as e:  # noqa: BLE001 -- recorded, never fatal: the inventory must finish
        return [{"error": f"{type(e).__name__}: {e}"[:300]}]


def save_content(svc, f, dest: Path):
    mime = f["mimeType"]
    if mime.startswith(SKIP_CONTENT):
        return "skipped (audio/video: md5 recorded)"
    if mime.startswith("application/vnd.google-apps."):
        export = drive_client.export_mime_for(mime, f["name"]) or "text/plain"
        req = svc.files().export_media(fileId=f["id"], mimeType=export)
    else:
        req = svc.files().get_media(fileId=f["id"])
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    return f"saved ({len(buf.getvalue())} bytes)"


def main():
    episode, out = sys.argv[1], Path(sys.argv[2])
    svc = drive_client._svc()
    parent = drive_client._env("DRIVE_EPISODES_FOLDER_ID")
    exact = re.compile(rf"^{re.escape(episode)}(?!\d)")  # E13, E13-…, never E130 / E1
    folders = [f for f in children(svc, parent) if f["mimeType"] == FOLDER and exact.match(f["name"])]
    if len(folders) != 1:
        print(f"FREEZE STOP: expected one {episode} folder, found {[f['name'] for f in folders]}")
        return 2
    root = folders[0]
    items, stack = [], [(root, root["name"])]
    while stack:
        folder, path = stack.pop()
        for f in children(svc, folder["id"]):
            p = f"{path}/{f['name']}"
            entry = {**f, "path": p}
            # Drive allows "/" and ".." in a name: such a name is recorded, never used as a path (Toby, 2026-10-12)
            unsafe = "/" in f["name"] or f["name"] in (".", "..") or "\0" in f["name"]
            dest = (out / "files" / p).resolve()
            if unsafe or not dest.is_relative_to((out / "files").resolve()):
                entry["unsafe_name"] = True
                entry["content"] = "skipped (unsafe name: recorded, not written)"
                if f["mimeType"] != FOLDER:
                    entry["revisions"] = revisions(svc, f["id"])
            elif f["mimeType"] == FOLDER:
                stack.append((f, p))
            else:
                entry["revisions"] = revisions(svc, f["id"])
                entry["content"] = save_content(svc, f, dest)
            items.append(entry)
    inv = {
        "episode": episode,
        "folder": {k: root[k] for k in ("id", "name", "createdTime", "modifiedTime")},
        "items": sorted(items, key=lambda e: e["path"]),
    }
    (out / "inventory.json").write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
    files = [e for e in items if e["mimeType"] != FOLDER]
    print(
        f"drive inventory: folder {root['name']} created {root['createdTime']} — "
        f"{len(files)} files, {len(items) - len(files)} subfolders"
    )
    for e in sorted(files, key=lambda e: e["path"]):
        print(f"  {e['modifiedTime']}  created {e['createdTime']}  revs {len(e['revisions']):>2}  {e['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
