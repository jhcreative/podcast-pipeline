"""config.py — every setting the pipeline reads from its environment, with the box's defaults, in one place.

The box sets its values in /opt/rockstar-pipeline/.env and the systemd units (Environment=); a test or the sandbox
sets them before importing a module. Each function reads the environment when it is called, so a module that keeps
a constant (`_STATE_DIR = config.state_dir()`) reads it at import, exactly as the inline `os.environ.get` it
replaced did.

Two roots:
  CODE_ROOT  the checkout this file is in. Prompts, scripts and brand assets ship with the code.
  BOX_ROOT   /opt/rockstar-pipeline. Runtime state lives here whatever checkout the code runs from.
On the box the two are the same directory today.
"""

from __future__ import annotations

import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent  # rockstar/config.py → the checkout
BOX_ROOT = Path("/opt/rockstar-pipeline")

ALERT_EMAIL_DEFAULT = "john@example.test"
_TRUE = ("on", "true", "1", "yes")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def flag(name: str, default: str = "off") -> bool:
    """A switch: on/true/1/yes (any case, spaces ignored) is on; anything else is off."""
    return os.environ.get(name, default).strip().lower() in _TRUE


def _path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default)))


# ── where things are ─────────────────────────────────────────────────────────────────────────────────────────────


def state_dir() -> Path:
    return _path("STATE_DIR", BOX_ROOT / "state")


def db_path() -> Path:
    return _path("DB_PATH", BOX_ROOT / "state" / "jobs.db")


def run_log_path() -> Path:
    return _path("RUN_LOG_PATH", BOX_ROOT / "state" / "run_log.jsonl")


def watch_state_path() -> Path:
    return _path("WATCH_STATE_PATH", BOX_ROOT / "state" / "watch_state.json")


def prompts_dir() -> Path:
    return Path(os.environ.get("PROMPTS_DIR") or CODE_ROOT / "prompts")


def scripts_dir() -> Path:
    return _path("SCRIPTS_DIR", CODE_ROOT / "scripts")


def reels_dir() -> Path:
    """The Node reel scripts (render, verify, lookup, inject, detect_boundaries)."""
    return _path("REELS_DIR", CODE_ROOT / "reels")


def brand_assets_dir() -> Path:
    return _path("BRAND_ASSETS_DIR", CODE_ROOT / "BrandAssets")


def whisperx_venv() -> Path:
    return _path("VENV_PATH", scripts_dir() / "whisperx_env")


def work_dir_base() -> Path:
    return Path(os.environ.get("WORK_DIR_BASE") or "/var/tmp/rockstar-work")


# ── who and where to ─────────────────────────────────────────────────────────────────────────────────────────────


def alert_email() -> str:
    return os.environ.get("PIPELINE_ALERT_EMAIL", ALERT_EMAIL_DEFAULT)


def log_level() -> str:
    return os.environ.get("LOG_LEVEL", "INFO")


def editorial_calendar_on() -> bool:
    """CAL-OFF: the Round-1 editorial calendar event is OFF unless EDITORIAL_CALENDAR_EVENT is on/true/1/yes."""
    return flag("EDITORIAL_CALENDAR_EVENT")


def episodes_folder_id() -> str:
    return os.environ.get("DRIVE_EPISODES_FOLDER_ID", "")


def inbox_folder_id() -> str:
    return os.environ.get("DRIVE_RECORDINGS_INBOX_FOLDER_ID", "")
