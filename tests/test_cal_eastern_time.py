"""
test_cal_eastern_time.py — the editorial event's "10:00 ET" is 10:00 in New York on both sides of daylight saving.

A fixed UTC-5 (the ET_UTC_OFFSET it replaced) put the event at 11:00 from March to November: the Calendar API stores
the instant and shows it in the calendar's own zone. The calendar is off by default (CAL-OFF); this holds the fix for
the day it is turned back on.
"""

import sys
import types
from datetime import date, timedelta, timezone
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))

# calendar_client imports the Google client and our auth at module load; neither is reached here.
disc = types.ModuleType("googleapiclient.discovery")
disc.build = lambda *a, **k: None
_swap.put("googleapiclient", types.ModuleType("googleapiclient"))
_swap.put("googleapiclient.discovery", disc)
auth = types.ModuleType("google_auth")
auth.calendar_credentials = lambda: None
_swap.put("rockstar.clients.google_auth", auth)

from rockstar.clients import calendar_client  # noqa: E402


def test_summer_is_edt():
    t = calendar_client._et_datetime(date(2026, 10, 14))  # EDT
    assert t.utcoffset() == timedelta(hours=-4), t
    assert t.astimezone(timezone.utc).hour == 14, t
    print("  ok  2026-10-14 10:00 ET = 14:00 UTC (EDT)")


def test_winter_is_est():
    t = calendar_client._et_datetime(date(2026, 12, 14))  # EST
    assert t.utcoffset() == timedelta(hours=-5), t
    assert t.astimezone(timezone.utc).hour == 15, t
    print("  ok  2026-12-14 10:00 ET = 15:00 UTC (EST)")


def test_the_wall_clock_hour_is_kept():
    for d in (date(2026, 3, 9), date(2026, 11, 2), date(2026, 7, 1), date(2026, 1, 5)):
        assert calendar_client._et_datetime(d, hour=10).hour == 10, d
    print("  ok  the hour asked for is the hour on the calendar, across both DST changes")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_cal_eastern_time: all passed")
