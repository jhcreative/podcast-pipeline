#!/usr/bin/env python3
"""
poll_drive_changes.py — periodic safety drain of the Drive changes feed.

Run by rockstar-poll-drive.timer (~every 10 min). The Drive push channel only
lives ~40 min while renew_watches runs daily, so the push is dead most of the
day; this timer guarantees that an audio drop in recordings_inbox (→ ROUND_1)
and a visuals drop in an episode folder (→ resume PUBLISH_FINALIZE) get picked
up within ~10 min regardless of push delivery.

Idempotent and file-locked (see drive_changes.drain): if a push and this poll
fire together, the cursor advances exactly once. Cheap — one changes.list call.
"""

import logging
import os

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import drive_changes  # noqa: E402  (after logging config)

if __name__ == "__main__":
    result = drive_changes.drain("poll")
    logging.getLogger("poll_drive_changes").info("drain result: %s", result)
