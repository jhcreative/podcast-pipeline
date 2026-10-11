"""
calendar_client.py — Google Calendar API wrapper.

Used in the pipeline to:
  - create_editorial_event()   Round 1: "E{N} editorial review — {Guest}"
                               on the next business day at 10:00 ET with a
                               TODO checklist in the description
  - update_event()             Finalize: mark event "live" + add URLs
  - list_events()              context for Brand Team chain (optional)

GOOGLE_CALENDAR_ID defaults to "primary".  Override in .env if the
rockstar calendar is a separate calendar (recommended for clean separation).
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone

from googleapiclient.discovery import build

from google_auth import calendar_credentials

logger = logging.getLogger(__name__)

_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

# Eastern time offset — naive approximation for 10:00 ET scheduling.
# Heather's calendar is in Eastern time; we schedule "10:00 ET".
# Using UTC-5 (EST) year-round here because Calendar API displays in the
# calendar's own timezone regardless of what UTC offset we submit.
# Phase 3 can make this DST-aware if needed.
_ET_OFFSET_HOURS = int(os.environ.get("ET_UTC_OFFSET", "-5"))


def _svc():
    return build("calendar", "v3", credentials=calendar_credentials(), cache_discovery=False)


# ── helpers ────────────────────────────────────────────────────────────────────


def _next_business_day(from_date: date | None = None) -> date:
    """Return the next weekday (Mon–Fri) after from_date (defaults to today)."""
    d = from_date or date.today()
    d += timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d += timedelta(days=1)
    return d


def _et_datetime(d: date, hour: int = 10, minute: int = 0) -> datetime:
    """Return a timezone-aware datetime at ET for a given date + hour."""
    tz = timezone(timedelta(hours=_ET_OFFSET_HOURS))
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)


# ── create ─────────────────────────────────────────────────────────────────────


def create_editorial_event(
    episode_code: str,
    episode_title: str = "TBD",
    episode_folder_url: str = "",
) -> dict:
    """
    Create the post-Round-1 editorial review event.

    Title:  "E{N} · {Episode Title}"
    Time:   next business day, 10:00–11:00 ET
    Body:   TODO checklist + Drive folder link

    Returns the created event resource (caller stores the event ID for
    the Finalize job to update later).
    """
    review_date = _next_business_day()
    start = _et_datetime(review_date, hour=10)
    end = _et_datetime(review_date, hour=11)

    description_lines = [
        f"Automated by Rockstar Pipeline · {episode_code}",
        "",
        "TODO:",
        "  [ ] Read apple_summary.md",
        "  [ ] Review transcript.md for accuracy",
        "  [ ] Check quotes.json — approve or edit quotes",
        "  [ ] Drop tile.jpg + reel.jpg into Drive folder",
        "  [ ] Add guest bio in this event description (bio field below)",
        "  [ ] Review + publish the blog post draft on rockstarblends.com (after Phase 2)",
        "",
        "Bio (fill in before Round 2 runs):",
        "  [paste guest bio here]",
    ]
    if episode_folder_url:
        description_lines.insert(2, f"Drive folder: {episode_folder_url}")

    event = {
        "summary": f"{episode_code} · {episode_title}",
        "description": "\n".join(description_lines),
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    result = _svc().events().insert(calendarId=_CALENDAR_ID, body=event).execute()
    logger.info(
        "created editorial event id=%s date=%s title=%r",
        result["id"], review_date, episode_title,
    )
    return result


def mark_episode_live(
    event_id: str,
    apple_url: str,
    spotify_url: str,
    episode_code: str,
) -> dict:
    """
    Update the editorial event once the episode goes live.
    Prepends a "LIVE" banner with Apple + Spotify URLs to the description.
    """
    existing = _svc().events().get(calendarId=_CALENDAR_ID, eventId=event_id).execute()
    old_desc = existing.get("description", "")
    live_banner = (
        f"🎙 LIVE\n"
        f"  Apple: {apple_url}\n"
        f"  Spotify: {spotify_url}\n"
        f"\n"
        f"─────────────────────────────────\n"
    )
    updates = {
        "summary": f"{episode_code} editorial review — ✅ LIVE",
        "description": live_banner + old_desc,
    }
    result = (
        _svc()
        .events()
        .patch(calendarId=_CALENDAR_ID, eventId=event_id, body=updates)
        .execute()
    )
    logger.info("updated event %s to LIVE (%s / %s)", event_id, apple_url, spotify_url)
    return result


# ── generic wrappers ───────────────────────────────────────────────────────────


def create_event(
    summary: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str = "",
    calendar_id: str | None = None,
) -> dict:
    """Generic event creator for ad-hoc use."""
    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }
    result = (
        _svc()
        .events()
        .insert(calendarId=calendar_id or _CALENDAR_ID, body=event)
        .execute()
    )
    logger.info("created event id=%s %r", result["id"], summary)
    return result


def update_event(
    event_id: str,
    updates: dict,
    calendar_id: str | None = None,
) -> dict:
    """Patch an existing event with a partial updates dict."""
    result = (
        _svc()
        .events()
        .patch(
            calendarId=calendar_id or _CALENDAR_ID,
            eventId=event_id,
            body=updates,
        )
        .execute()
    )
    logger.info("patched event %s", event_id)
    return result


def list_events(
    days_ahead: int = 30,
    max_results: int = 20,
    calendar_id: str | None = None,
) -> list[dict]:
    """Return upcoming calendar events within the next N days."""
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=days_ahead)
    result = (
        _svc()
        .events()
        .list(
            calendarId=calendar_id or _CALENDAR_ID,
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])
