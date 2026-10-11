"""
drive_client.py — Google Drive API wrapper.

Thin wrappers around googleapis Drive v3.  No retry logic here — the worker
handles retries at the job level.  All operations are synchronous; the worker
process is single-threaded so async is not needed.

Key operations:
  - list_audio_in_inbox()        list m4a/mp3 files in recordings_inbox
  - download_file()              download a file to a local path
  - upload_file()                upload / create a file in a folder
  - create_folder()              create an Episodes/E{N}-{slug} subfolder
  - move_file()                  move inbox audio into its episode folder
  - has_pipeline_done_marker()   check .pipeline_done idempotency marker
  - create_pipeline_done_marker()
  - register_watch() / stop_watch()  Drive push-notification channels
"""

import io
import logging
import os
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from google_auth import drive_credentials

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────────

from episode_files import AUDIO_EXTENSIONS as _AUDIO_EXTENSIONS  # R1-01: one shared list

# Google-Apps-Editors files (Docs/Sheets/Slides) hold no binary content, so
# files().get_media() 403s on them with "Only files with binary content can be
# downloaded. Use Export with Docs Editors files." This matters because the moment
# John hand-edits an artifact in Drive (apple_summary.md, quotes.json, a transcript)
# Drive converts that uploaded text file into a Google Doc. We want the hand-edited
# version to BE the source of truth for Phase 2 — so download_file() detects the
# Editors mime and exports it to text instead of failing. Map each Editors type to
# the closest text export so a converted .md→Doc round-trips back to text, a
# converted .json/.csv→Sheet round-trips to CSV, etc.
_GOOGLE_EDITORS_PREFIX = "application/vnd.google-apps."
_GOOGLE_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_UTF8_BOM = b"\xef\xbb\xbf"

# A Doc whose NAME says markdown exports as markdown (ADR-0008 addendum, 2026-09-22).
# Plain-text export flattens John's formatting: the bold that delimits each
# Conversations bullet's lead in apple_summary.md came back as one run of text, and
# the blog post (BLOG-02) needs that boundary. text/markdown keeps it. Only *.md —
# a quotes.json or transcript that Drive converted to a Doc keeps text/plain, because
# the markdown export backslash-escapes characters (\-, \*, \[ …) and would corrupt
# JSON and transcript text that were never markdown to begin with.
_MARKDOWN_NAMES = (".md",)


def export_mime_for(google_mime: str, name: str) -> str | None:
    """The export mime for a Google-Editors file, or None if there is no text export.
    Pure (no Drive call) so the choice is testable."""
    if google_mime == "application/vnd.google-apps.document" and (name or "").lower().endswith(_MARKDOWN_NAMES):
        return "text/markdown"
    return _GOOGLE_EXPORT_MIME.get(google_mime)


def _env(var: str) -> str:
    val = os.environ.get(var, "")
    if not val:
        raise RuntimeError(f"{var} not set in environment")
    return val


def _svc():
    """Build a Drive v3 service client.  cache_discovery=False avoids a
    file-system cache that can become stale and cause confusing 404s."""
    return build("drive", "v3", credentials=drive_credentials(), cache_discovery=False)


# ── listing ────────────────────────────────────────────────────────────────────


def list_audio_in_inbox() -> list[dict]:
    """Return audio files in the recordings_inbox folder."""
    folder_id = _env("DRIVE_RECORDINGS_INBOX_FOLDER_ID")
    results = (
        _svc()
        .files()
        .list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType, size, createdTime)",
            pageSize=50,
        )
        .execute()
    )
    files = results.get("files", [])
    return [
        f
        for f in files
        if Path(f["name"]).suffix.lower() in _AUDIO_EXTENSIONS
        or "audio" in f.get("mimeType", "")
    ]


def list_folder(folder_id: str) -> list[dict]:
    """List all non-trashed files directly inside a folder."""
    results = (
        _svc()
        .files()
        .list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType, size)",
            pageSize=200,
        )
        .execute()
    )
    return results.get("files", [])


def find_episode_folder(episode_slug: str) -> dict | None:
    """
    Find an episode folder by slug prefix in the Episodes parent.
    Returns the folder resource or None if not found.
    """
    episodes_folder = _env("DRIVE_EPISODES_FOLDER_ID")
    results = (
        _svc()
        .files()
        .list(
            q=(
                f"'{episodes_folder}' in parents"
                f" and mimeType='application/vnd.google-apps.folder'"
                f" and name contains '{episode_slug}'"
                f" and trashed=false"
            ),
            fields="files(id, name)",
            pageSize=5,
        )
        .execute()
    )
    items = results.get("files", [])
    return items[0] if items else None


# ── create folder ──────────────────────────────────────────────────────────────


def create_folder(name: str, parent_folder_id: str) -> dict:
    """Create a subfolder inside parent_folder_id. Returns the new folder resource."""
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = _svc().files().create(body=metadata, fields="id, name").execute()
    logger.info("created folder %r id=%s parent=%s", name, folder["id"], parent_folder_id)
    return folder


# ── download ───────────────────────────────────────────────────────────────────


def download_file(file_id: str, dest_path: Path) -> Path:
    """
    Download a Drive file to dest_path using resumable chunked download.
    Returns dest_path on success.

    8 MB chunks are a sensible tradeoff: small enough to handle timeouts on
    large-v3 model downloads, large enough to avoid excessive round-trips for
    typical 200–500 MB episode audio.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    svc = _svc()

    # A hand-edit in Drive may have converted a text artifact into a Google
    # Doc/Sheet, which has no binary content. Check the mime first and export to
    # text in that case; otherwise stream the bytes normally. The extra metadata
    # call is one cheap round-trip on a pipeline that downloads ~6 files/episode,
    # and audio/images (never Editors types) take the unchanged get_media path.
    meta = svc.files().get(fileId=file_id, fields="mimeType, name").execute()
    mime = meta.get("mimeType", "")
    is_export = mime.startswith(_GOOGLE_EDITORS_PREFIX)
    if is_export:
        export_mime = export_mime_for(mime, meta.get("name", ""))
        if not export_mime:
            raise RuntimeError(
                f"download_file: {meta.get('name', file_id)} is a Google '{mime}' with no "
                f"text-export mapping — cannot download. Re-upload it as a binary file."
            )
        request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
        logger.info(
            "downloading %s as %s export (was hand-edited to %s)",
            meta.get("name", file_id), export_mime, mime,
        )
    else:
        request = svc.files().get_media(fileId=file_id)

    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.debug(
                    "download %s %.0f%%", file_id, status.progress() * 100
                )

    # Google's text export prepends a UTF-8 BOM; strip it so downstream
    # json.loads() / first-line parsing doesn't choke on a leading U+FEFF.
    if is_export:
        data = dest_path.read_bytes()
        if data.startswith(_UTF8_BOM):
            dest_path.write_bytes(data[len(_UTF8_BOM):])

    logger.info("downloaded %s → %s", file_id, dest_path)
    return dest_path


# ── upload ─────────────────────────────────────────────────────────────────────


def upload_file(
    local_path: Path,
    drive_folder_id: str,
    drive_filename: str | None = None,
    mime_type: str = "application/octet-stream",
) -> dict:
    """
    Upload local_path to drive_folder_id.  Creates the file; does not update
    an existing one.  Returns the created file resource.
    """
    name = drive_filename or local_path.name
    metadata = {"name": name, "parents": [drive_folder_id]}
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    result = (
        _svc()
        .files()
        .create(body=metadata, media_body=media, fields="id, name")
        .execute()
    )
    logger.info("uploaded %s → Drive %s id=%s", local_path, name, result["id"])
    return result


def upload_text(
    content: str,
    filename: str,
    drive_folder_id: str,
    mime_type: str = "text/plain",
) -> dict:
    """Upload a string as a Drive file.  Convenience wrapper around upload_file."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=Path(filename).suffix, delete=False
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        return upload_file(tmp_path, drive_folder_id, filename, mime_type)
    finally:
        tmp_path.unlink(missing_ok=True)


def update_file(file_id: str, local_path: Path, mime_type: str = "text/html") -> dict:
    """Replace the content of an existing Drive file."""
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    result = (
        _svc()
        .files()
        .update(fileId=file_id, media_body=media, fields="id, name")
        .execute()
    )
    logger.info("updated Drive file %s ← %s", file_id, local_path)
    return result


# ── move ───────────────────────────────────────────────────────────────────────


def move_file(file_id: str, dest_folder_id: str) -> None:
    """Move a file into dest_folder_id by updating its parents."""
    f = _svc().files().get(fileId=file_id, fields="parents").execute()
    current_parents = ",".join(f.get("parents", []))
    _svc().files().update(
        fileId=file_id,
        addParents=dest_folder_id,
        removeParents=current_parents,
        fields="id, parents",
    ).execute()
    logger.info("moved file %s → folder %s", file_id, dest_folder_id)


def trash_file(file_id: str) -> None:
    """Move a file to Drive's trash.

    Trash, not permanent delete: a wrong call here is recoverable from the Drive UI
    for 30 days, and nothing in this pipeline needs the storage back immediately.
    Used by the quote-card upload, which clears its own `cards/` folder before each
    run because upload_file CREATES rather than replaces (CARD-02).
    """
    _svc().files().update(fileId=file_id, body={"trashed": True}).execute()
    logger.info("trashed Drive file %s", file_id)


# ── idempotency marker ─────────────────────────────────────────────────────────


def has_pipeline_done_marker(episode_folder_id: str) -> bool:
    """
    Check for a .pipeline_done file inside an episode folder.
    If present, this episode has been fully processed — skip it.
    """
    results = (
        _svc()
        .files()
        .list(
            q=(
                f"'{episode_folder_id}' in parents"
                f" and name='.pipeline_done'"
                f" and trashed=false"
            ),
            fields="files(id)",
            pageSize=1,
        )
        .execute()
    )
    return len(results.get("files", [])) > 0


def create_pipeline_done_marker(episode_folder_id: str) -> None:
    """
    Write .pipeline_done to mark an episode as fully processed. Idempotent.

    Drive allows several files with the same name in one folder, and this function
    used to create unconditionally — so every re-run added another marker. E28's
    folder accumulated FIVE `.pipeline_done` files that way. The marker answers a
    yes/no question ("is this episode finished?"), so duplicates carry no extra
    information and only make the folder harder to read. Now: create once, then
    no-op. This matters more since PUBLISH_FINALIZE reprocess became the normal
    trigger (ADR-0011) rather than a rare recovery.
    """
    existing = [f for f in list_folder(episode_folder_id) if f["name"] == ".pipeline_done"]
    if existing:
        logger.info(
            "publish marker already present in folder %s (%d) — not adding another",
            episode_folder_id, len(existing),
        )
        return
    metadata = {
        "name": ".pipeline_done",
        "parents": [episode_folder_id],
        "mimeType": "text/plain",
    }
    _svc().files().create(body=metadata, fields="id").execute()
    logger.info("wrote .pipeline_done in folder %s", episode_folder_id)


# ── watch channels ─────────────────────────────────────────────────────────────


def register_watch(
    folder_id: str,
    channel_id: str,
    webhook_url: str,
    token: str,
) -> dict:
    """
    Register a Drive push-notification channel on a folder.
    Drive channels expire in 1–24 hours; watches/renew_watches.py refreshes
    them daily.  Returns the channel resource (includes 'expiration' ms).
    """
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "token": token,
        "payload": True,
    }
    result = _svc().files().watch(fileId=folder_id, body=body).execute()
    logger.info(
        "registered Drive watch channel=%s folder=%s expires=%s",
        channel_id,
        folder_id,
        result.get("expiration"),
    )
    return result


def stop_watch(channel_id: str, resource_id: str) -> None:
    """Stop a watch channel.  Called during renewal before re-registering.
    Works for both files.watch and changes.watch channels — channels().stop()
    is keyed on channel/resource id, not on what was watched."""
    try:
        _svc().channels().stop(
            body={"id": channel_id, "resourceId": resource_id}
        ).execute()
        logger.info("stopped Drive watch channel %s", channel_id)
    except HttpError as exc:
        # 404 = channel already expired — harmless during renewal
        if exc.resp.status != 404:
            raise


# ── changes feed — new-file detection done correctly ────────────────────────────
#
# WHY THIS EXISTS (read before touching):
#   files().watch(fileId=<folder>) notifies on changes to the folder *resource
#   itself* — rename, trash, share — NOT on a child file being *added inside it*.
#   Drive acks the watch with a single 'sync' ping at registration and then stays
#   silent on real drops.  On 2026-06-10 this silently stranded E28-everett.m4a in
#   recordings_inbox: the watch "renewed OK" every night yet had never once fired
#   on an actual audio drop.
#
# WHAT DEFENDS AGAINST IT:
#   The Drive *changes feed* is Google's supported "something in my Drive changed"
#   mechanism.  We arm ONE account-wide changes.watch and filter notifications by
#   the changed file's parent folder.  Crucially we persist a page-token cursor
#   (see webhook_service._save_changes_token): the push notification is only a
#   "go look" ping, while the cursor is the real source of truth.  If the watch
#   lapses for hours, the next list_changes() still returns everything since the
#   cursor — so a drop can never be lost the way it was with files.watch.


def get_changes_start_page_token() -> str:
    """Return the current changes cursor — the 'everything after now' baseline.
    Called once when first arming the changes watch; thereafter we persist and
    advance the token ourselves so no change is missed across a watch gap."""
    result = _svc().changes().getStartPageToken().execute()
    return result["startPageToken"]


def register_changes_watch(
    channel_id: str,
    webhook_url: str,
    token: str,
    page_token: str,
) -> dict:
    """
    Arm an account-wide push channel on the Drive changes feed.

    The notification Drive sends is only a 'go look' ping — it carries no list of
    what changed.  The real cursor is `page_token`, which the webhook owns and
    persists.  Returns the channel resource (includes 'expiration' ms).
    """
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "token": token,
        "payload": True,
    }
    result = (
        _svc().changes().watch(pageToken=page_token, body=body).execute()
    )
    logger.info(
        "registered Drive changes watch channel=%s expires=%s",
        channel_id,
        result.get("expiration"),
    )
    return result


def list_changes(page_token: str) -> dict:
    """
    Drain the changes feed from `page_token` forward, following nextPageToken to
    the end so one call returns every pending change.

    Returns: {"files": [{id, name, mimeType, parents, trashed}], "new_start_token": str}
      - files          changed (non-removed) file resources, in feed order
      - new_start_token the cursor to persist for the next call
    """
    svc = _svc()
    changed: list[dict] = []
    token = page_token
    while True:
        resp = (
            svc.changes()
            .list(
                pageToken=token,
                spaces="drive",
                includeRemoved=True,
                pageSize=100,
                fields=(
                    "newStartPageToken,nextPageToken,"
                    "changes(removed,fileId,"
                    "file(id,name,mimeType,parents,trashed))"
                ),
            )
            .execute()
        )
        for ch in resp.get("changes", []):
            f = ch.get("file")
            # 'removed' = file deleted or moved out of scope; no file resource.
            if ch.get("removed") or f is None:
                continue
            changed.append(
                {
                    "id": f.get("id"),
                    "name": f.get("name", ""),
                    "mimeType": f.get("mimeType", ""),
                    "parents": f.get("parents", []),
                    "trashed": f.get("trashed", False),
                }
            )
        if resp.get("nextPageToken"):
            token = resp["nextPageToken"]
            continue
        return {"files": changed, "new_start_token": resp.get("newStartPageToken")}


def get_folder_name(folder_id: str) -> str:
    """Return a folder/file's display name — used to map an episode folder id to
    its slug when routing a visuals drop.  Returns '' if the id is unresolvable."""
    try:
        f = _svc().files().get(fileId=folder_id, fields="name").execute()
        return f.get("name", "")
    except HttpError as exc:
        logger.warning("get_folder_name(%s) failed: %s", folder_id, exc)
        return ""
