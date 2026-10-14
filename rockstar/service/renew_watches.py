"""
renew_watches.py — daily Drive + Gmail watch renewal.

Run by: rockstar-renew-watches.timer at 04:00 UTC daily.

Drive webhook channels expire every 1–24 hours (Google's choice, not ours).
Gmail watch expires every ~7 days.
Running this daily at 04:00 UTC (off-peak) keeps both fresh with margin.
Persistent=true in the timer means it runs immediately on next boot if the
box was down at 04:00 — no manual intervention needed after reboots.

Channel state (channel_id, resource_id, expiration) is stored in
state/watch_state.json so we can call stop_watch() before re-registering.
Not strictly required — old channels auto-expire — but clean renewal avoids
accumulating orphan channels in the GCP quota.

Usage:
  python -m rockstar.service.renew_watches
  (called by systemd, not directly)
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from rockstar import config

# Run from the checkout as `python -m rockstar.service.renew_watches` (the unit's ExecStart): the package
# imports resolve from there. (Run as a path, its own folder was on sys.path and the imports failed: E27, 2026-06-05.)
from rockstar.clients import drive_client, gmail_client

logger = logging.getLogger(__name__)

# ── config ─────────────────────────────────────────────────────────────────────

_STATE_PATH = config.watch_state_path()
# Shared with webhook_service: the changes-feed cursor.  renew_watches SEEDS it
# (first run) and otherwise leaves it alone; the webhook advances it as changes
# arrive.  Never overwrite an existing cursor here — that would skip every change
# between the last drain and this renewal.
_CHANGES_TOKEN_PATH = Path(
    os.environ.get(
        "DRIVE_CHANGES_TOKEN_PATH",
        "/opt/rockstar-pipeline/state/drive_changes_token.json",
    )
)
_PUBLIC_HOSTNAME = os.environ.get("PUBLIC_HOSTNAME", "pipeline.example.test")
_WEBHOOK_SECRET = os.environ.get("WEBHOOK_SHARED_SECRET", "").strip()  # stripped as the webhook strips it (SEC-01)

_INBOX_FOLDER_ID = config.inbox_folder_id()
_EPISODES_FOLDER_ID = config.episodes_folder_id()


# ── state helpers ──────────────────────────────────────────────────────────────


def _load_state() -> dict:
    if _STATE_PATH.exists():
        return json.loads(_STATE_PATH.read_text())
    return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2))


# ── Drive changes-feed watch renewal ─────────────────────────────────────────────
# One account-wide changes.watch: files().watch(<folder>) never fires on a child being added, only on the
# folder's own metadata. See drive_client.list_changes for the full why.
# log: docs/carry-forward/2026-10-13-remodel.md#drive-changes-feed


def _seed_changes_cursor() -> str:
    """Return the persisted changes cursor, creating it (at 'now') on first run.
    We never overwrite an existing cursor — the webhook owns advancing it, and
    resetting here would skip every change since the last drain."""
    if _CHANGES_TOKEN_PATH.exists():
        try:
            return json.loads(_CHANGES_TOKEN_PATH.read_text())["page_token"]
        except Exception as exc:
            logger.warning("changes cursor unreadable (%s) — re-seeding", exc)

    token = drive_client.get_changes_start_page_token()
    _CHANGES_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CHANGES_TOKEN_PATH.write_text(json.dumps({"page_token": token}, indent=2))
    logger.info("seeded Drive changes cursor at startPageToken=%s", token)
    return token


def _renew_changes_watch(state: dict) -> dict:
    """
    Stop the existing changes channel (if any) and arm a fresh one on the Drive
    changes feed.  Returns the updated state entry.
    """
    existing = state.get("drive_changes", {})
    channel_id = existing.get("channel_id", "")
    resource_id = existing.get("resource_id", "")

    if channel_id and resource_id:
        try:
            drive_client.stop_watch(channel_id, resource_id)
        except Exception as exc:
            logger.warning("could not stop old changes channel %s: %s", channel_id, exc)

    page_token = _seed_changes_cursor()
    new_channel_id = f"drive-changes-{uuid.uuid4()}"
    webhook_url = f"https://{_PUBLIC_HOSTNAME}/webhook/drive/changes"

    result = drive_client.register_changes_watch(
        channel_id=new_channel_id,
        webhook_url=webhook_url,
        token=_WEBHOOK_SECRET,
        page_token=page_token,
    )

    return {
        "channel_id": new_channel_id,
        "resource_id": result.get("resourceId", ""),
        "expiration_ms": result.get("expiration", ""),
        "renewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Gmail watch renewal ────────────────────────────────────────────────────────


def _renew_gmail_watch(state: dict) -> dict:
    """Stop and re-register the Gmail Pub/Sub push watch."""
    try:
        gmail_client.stop_watch()
    except Exception as exc:
        logger.warning("could not stop Gmail watch: %s", exc)

    result = gmail_client.register_watch()
    return {
        "history_id": result.get("historyId", ""),
        "expiration_ms": result.get("expiration", ""),
        "renewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=config.log_level(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not _INBOX_FOLDER_ID or not _EPISODES_FOLDER_ID:
        raise RuntimeError(
            "DRIVE_RECORDINGS_INBOX_FOLDER_ID and DRIVE_EPISODES_FOLDER_ID must be set in /opt/rockstar-pipeline/.env"
        )

    state = _load_state()
    errors = []

    # ── Drive: one account-wide changes-feed watch (covers inbox + Episodes)
    try:
        logger.info("renewing Drive changes-feed watch")
        state["drive_changes"] = _renew_changes_watch(state)
        # Drop stale per-folder entries from the pre-changes-feed design so the
        # state file reflects reality and we don't try to stop dead channels.
        state.pop("drive_inbox", None)
        state.pop("drive_episodes", None)
        logger.info("drive_changes watch renewed OK")
    except Exception as exc:
        logger.error("failed to renew drive_changes watch: %s", exc)
        errors.append(f"drive_changes: {exc}")

    # ── Gmail Pub/Sub watch
    try:
        logger.info("renewing Gmail Pub/Sub watch")
        state["gmail"] = _renew_gmail_watch(state)
        logger.info("gmail watch renewed OK")
    except Exception as exc:
        logger.error("failed to renew gmail watch: %s", exc)
        errors.append(f"gmail: {exc}")

    _save_state(state)

    if errors:
        raise RuntimeError(f"watch renewal completed with errors: {errors}")

    logger.info("all watches renewed successfully")


if __name__ == "__main__":
    main()
