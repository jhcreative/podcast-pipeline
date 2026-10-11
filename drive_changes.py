"""
drive_changes.py — drain the Drive changes feed and route changes to jobs.

WHY THIS EXISTS (and why we poll)
---------------------------------
Drive push channels for the changes feed only live ~40 minutes — Google caps
them short (Gmail watches get 7 days; Drive watches do not), and renew_watches
runs once a day. So the push notification is dead for most of the day: an audio
drop at 10am sat unprocessed until the next morning's renewal window. That is
exactly why E28 had to be submitted manually (stranded 2026-06-10).

The fix leans on a property the design already has: the page-token cursor — not
the push — is the source of truth, and the feed SELF-HEALS across gaps (draining
from the saved token always returns everything since the last drain). So we
DRAIN ON A TIMER (poll_drive_changes.py, ~every 10 min) and keep the push as a
best-effort fast path. Both call drain() here.

A file lock makes the read-token → list-changes → advance-token sequence atomic,
so the push and the poll can never double-process or skip a change when they
fire at the same moment.

Routing (unchanged from the original webhook handler):
  • new audio in recordings_inbox            → enqueue ROUND_1 (at most once/drain)
  • a file dropped inside an episode folder   → maybe resume PUBLISH_FINALIZE
"""

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

import db
import drive_client
import episode_files

logger = logging.getLogger(__name__)

_STATE_DIR = Path(os.environ.get("STATE_DIR", "/opt/rockstar-pipeline/state"))
_TOKEN_PATH = _STATE_DIR / "drive_changes_token.json"
_PUB_SCHEDULE_PATH = _STATE_DIR / "publication_schedule.json"
_LOCK_PATH = _STATE_DIR / "drive_changes.lock"

_INBOX_FOLDER_ID = os.environ.get("DRIVE_RECORDINGS_INBOX_FOLDER_ID", "")
_EPISODES_FOLDER_ID = os.environ.get("DRIVE_EPISODES_FOLDER_ID", "")
from episode_files import AUDIO_EXTENSIONS as _AUDIO_EXTENSIONS  # R1-01: one shared list


@contextmanager
def _file_lock():
    """Cross-process lock so the webhook push and the poll timer never race on
    the drain-then-advance sequence (we run the worker/webhook/poll as separate
    processes, so an in-process asyncio lock isn't enough)."""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _load_token() -> str | None:
    if _TOKEN_PATH.exists():
        try:
            return json.loads(_TOKEN_PATH.read_text())["page_token"]
        except Exception as exc:
            logger.warning("drive_changes: could not read cursor: %s", exc)
    return None


def _save_token(token: str) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(json.dumps({"page_token": token}, indent=2))


_PHASE2_READY_STATUSES = ("drafting", "reprocessed", "waiting_for_visuals",
                          "submitted-awaiting-publish", "ready-for-publish")


def _maybe_resume_publish_finalize(folder_id: str) -> bool:
    """
    A file changed inside `folder_id`. TRIG-02 (John, 2026-10-01): this is now THE Phase 2 trigger. The
    publisher no longer e-mails "Your Episode is Live", so Phase 2 starts when the episode folder holds BOTH the
    tile and the reel image.

    Guards, all required:
      - the folder maps to a schedule entry (folder name "E{N}-{slug}" → slug);
      - that entry is still awaiting Phase 2 (_PHASE2_READY_STATUSES) and not pipeline_done;
      - the folder ROOT has a tile AND a reel image (episode_files.find_visual, the same rule Phase 2 uses).
    The dedup_key is the episode plus the two image ids: the same images enqueue once however many Drive changes
    arrive, while replacing an image is a new trigger. Returns True if a job was enqueued.
    """
    if not _PUB_SCHEDULE_PATH.exists():
        return False
    name = drive_client.get_folder_name(folder_id)
    if not name:
        return False
    slug = name.split("-", 1)[1].lower() if "-" in name else name.lower()
    try:
        pub_schedule = json.loads(_PUB_SCHEDULE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("drive_changes: pub_schedule read error: %s", exc)
        return False
    for ep in pub_schedule.get("episodes", []):
        if ep.get("slug", "").lower() != slug or ep.get("drive_folder_id") != folder_id:
            continue
        if ep.get("status") not in _PHASE2_READY_STATUSES or ep.get("pipeline_done"):
            return False
        files = drive_client.list_folder(folder_id)
        tile, reel = episode_files.find_visual(files, "tile"), episode_files.find_visual(files, "reel")
        if not (tile and reel):
            logger.info("drive_changes: %s changed; tile %s, reel %s: waiting for both",
                        ep.get("episode_code"), "yes" if tile else "no", "yes" if reel else "no")
            return False
        mm_ctx = ep.get("pending_mm_context", {})
        job_id = db.enqueue(
            job_type="PUBLISH_FINALIZE",
            episode_id=slug,
            payload={
                "trigger": "visuals",
                "apple_url": mm_ctx.get("apple_url", ""),
                "spotify_url": mm_ctx.get("spotify_url", ""),
                "mm_url": mm_ctx.get("mm_url", ""),
                "episode_number": ep.get("episode_number"),
            },
            dedup_key=f"visuals:E{ep.get('episode_number')}:{tile['id']}:{reel['id']}",
        )
        logger.info("drive_changes: %s has tile + reel — PUBLISH_FINALIZE job %d (visuals trigger)",
                    ep.get("episode_code"), job_id)
        return True
    return False


def drain(reason: str = "poll") -> dict:
    """
    Drain the changes feed from the saved cursor and route each change. Safe to
    call from the push webhook and the poll timer (file-locked). `reason` is just
    a label for logs/payloads ("push" or "poll").
    """
    with _file_lock():
        token = _load_token()
        if not token:
            # No cursor — we can't list a range. Baseline to 'now' so the NEXT
            # change is caught. renew_watches seeds this on registration; this is
            # a cold-start guard.
            token = drive_client.get_changes_start_page_token()
            _save_token(token)
            logger.warning(
                "drive_changes(%s): no cursor on disk — baselined to now "
                "(this event not replayable)", reason,
            )
            return {"baselined": True, "round1_enqueued": False, "resumed": 0}

        result = drive_client.list_changes(token)
        files = result["files"]
        new_token = result["new_start_token"]

        enqueued_round1 = False
        seen_episode_parents: set[str] = set()
        resumed = 0
        for f in files:
            if f["trashed"]:
                continue
            parents = f.get("parents", [])

            # new audio in recordings_inbox → ROUND_1 (at most once per drain;
            # round_1.py re-reads the inbox and is idempotent via .pipeline_done)
            if not enqueued_round1 and _INBOX_FOLDER_ID in parents:
                suffix = Path(f["name"]).suffix.lower()
                if suffix in _AUDIO_EXTENSIONS or "audio" in f.get("mimeType", ""):
                    job_id = db.enqueue(
                        job_type="ROUND_1",
                        episode_id="TBD",
                        payload={"trigger": f"drive_{reason}", "file": f["name"]},
                    )
                    logger.info(
                        "drive_changes(%s): %r added to inbox — enqueued ROUND_1 job %d",
                        reason, f["name"], job_id,
                    )
                    enqueued_round1 = True
                    continue

            # a file dropped inside an episode folder → maybe resume PUBLISH_FINALIZE
            for parent in parents:
                if parent in (_INBOX_FOLDER_ID, _EPISODES_FOLDER_ID):
                    continue
                if parent in seen_episode_parents:
                    continue
                seen_episode_parents.add(parent)
                if _maybe_resume_publish_finalize(parent):
                    resumed += 1

        if new_token:
            _save_token(new_token)

        return {
            "baselined": False,
            "changes": len(files),
            "round1_enqueued": enqueued_round1,
            "resumed": resumed,
            "new_token": new_token,
        }
