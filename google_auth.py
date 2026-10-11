"""
google_auth.py — shared Google service account credentials.

All three Google API clients (Drive, Gmail, Calendar) use service account
auth loaded once from the key file at GCP_SA_KEY_PATH.

Why service account (D-2 decision, 2026-05-21):
  OAuth refresh tokens are tied to a human user's session; they fail after
  password rotation and are lost on a box rebuild. A service account key
  (gcp-sa.json) is independent of any human account and survives both.

Scopes are split by client so we only hold the access we need. lru_cache
means the credential object is built once per process; each API client
re-uses the same object without re-reading the file on every call.
"""

import logging
import os
from functools import lru_cache

from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# Minimum scopes required by each API surface.  Narrower is safer.
_SCOPES_DRIVE = ["https://www.googleapis.com/auth/drive"]
_SCOPES_GMAIL = [
    # modify = read + labels + create drafts
    "https://www.googleapis.com/auth/gmail.modify",
    # send = required for pipeline alert emails (visuals missing notification)
    # Prerequisite: Workspace Admin → DWD → add this scope to the SA entry
    "https://www.googleapis.com/auth/gmail.send",
]
_SCOPES_CALENDAR = ["https://www.googleapis.com/auth/calendar"]


def _sa_key_path() -> str:
    path = os.environ.get("GCP_SA_KEY_PATH", "")
    if not path:
        raise RuntimeError(
            "GCP_SA_KEY_PATH not set — check /opt/rockstar-pipeline/.env"
        )
    return path


def _gmail_user() -> str:
    """
    Gmail domain-wide delegation requires the service account to impersonate
    a real mailbox.  GMAIL_USER is the address of that mailbox.
    """
    user = os.environ.get("GMAIL_USER", "")
    if not user:
        raise RuntimeError(
            "GMAIL_USER not set — should be the Gmail address to impersonate"
        )
    return user


def _drive_user() -> str:
    """
    Drive DWD subject — the Google account that owns the Drive folders.
    Defaults to GMAIL_USER if DRIVE_USER is not set, but they are often
    different: e.g. Drive under admin@, Gmail under heather@.
    """
    user = os.environ.get("DRIVE_USER", "") or os.environ.get("GMAIL_USER", "")
    if not user:
        raise RuntimeError(
            "DRIVE_USER (or GMAIL_USER) not set — should be the Google account "
            "that owns the Drive recordings inbox and Episodes folder"
        )
    return user


@lru_cache(maxsize=None)
def drive_credentials() -> service_account.Credentials:
    creds = service_account.Credentials.from_service_account_file(
        _sa_key_path(), scopes=_SCOPES_DRIVE
    )
    # Domain-wide delegation: act as the human account that owns the Drive folders.
    # Service accounts have no personal storage quota — file creation (not moves)
    # fails with 403 storageQuotaExceeded when running as the bare SA.
    # Impersonating a real user routes writes through their quota, exactly as
    # gmail_credentials() does for the Gmail mailbox.
    # Prerequisite: Workspace Admin → Security → API Controls → Domain-wide Delegation
    # must include this SA client ID with scope:
    #   https://www.googleapis.com/auth/drive
    # DRIVE_USER is typically admin@example.test (Drive folder owner).
    # GMAIL_USER is typically heather@example.test (mailbox to draft into).
    return creds.with_subject(_drive_user())


@lru_cache(maxsize=None)
def gmail_credentials() -> service_account.Credentials:
    creds = service_account.Credentials.from_service_account_file(
        _sa_key_path(), scopes=_SCOPES_GMAIL
    )
    # Domain-wide delegation: act as the human mailbox, not the SA itself.
    return creds.with_subject(_gmail_user())


@lru_cache(maxsize=None)
def calendar_credentials() -> service_account.Credentials:
    creds = service_account.Credentials.from_service_account_file(
        _sa_key_path(), scopes=_SCOPES_CALENDAR
    )
    # Domain-wide delegation: act as the calendar owner (heather@example.test).
    # The SA has no calendar access of its own; without impersonation every write
    # returns 403 requiredAccessLevel.
    # Prerequisite: Workspace Admin → Domain-wide Delegation → SA client ID must
    # include scope: https://www.googleapis.com/auth/calendar
    # CALENDAR_USER defaults to GMAIL_USER (both are heather@ for this pipeline).
    user = os.environ.get("CALENDAR_USER", "") or _gmail_user()
    return creds.with_subject(user)
