#!/usr/bin/env python3
"""
test_sched01_phase2_writeback.py — publication_schedule write-back on Phase 2 completion.

Born from a gap paid for twice by hand. Step 15 was gated on
`trigger != "reprocess"`, so a FULL reprocess finished Phase 2 — reels, captions,
blog, thank-you draft — the job logged `done`, and the schedule still read
`status: drafting`, no URLs, `pipeline_done: false`. E29 (2026-06-26) and E30
(2026-07-21) were both reconciled by hand afterwards.

It stops being an edge case: per ADR-0011 the publisher's trigger email is gone for
good and `reprocess_episode.py` is the sanctioned permanent kickoff, so the reprocess
path is now the NORMAL path for every episode.

These cases lock the merge rules, which is where the risk actually is — writing the
file is trivial, deciding what to overwrite is not.

Usage:  python3 test_sched01_phase2_writeback.py
Exit:   0 = all pass · 1 = one or more failed
"""

import sys
from pathlib import Path

import re

# Extract ONLY the function under test from the source.
#
# Importing jobs/publish_finalize.py outright pulls in anthropic_client → the
# `anthropic` SDK, plus Google auth and a populated .env — none of which a pure
# merge-rule test should need. `_apply_phase2_completion` was deliberately written
# as a pure function over a dict for exactly this reason: the merge rules are where
# the bugs live, and they should be testable without a network or a credential.
_SRC = (Path(__file__).resolve().parent.parent / "jobs" / "publish_finalize.py").read_text()
_match = re.search(r"^def _apply_phase2_completion\(.*?(?=\n\ndef )", _SRC, re.S | re.M)
if not _match:
    print("could not locate _apply_phase2_completion in jobs/publish_finalize.py")
    sys.exit(1)
_ns: dict = {}
exec(_match.group(0), _ns)
apply_completion = _ns["_apply_phase2_completion"]

APPLE = "https://podcasts.apple.com/us/podcast/x/id1785708777?i=1000000000030"
SPOTIFY = "https://open.spotify.com/episode/0SynthEp30AbCdEfGhJkLm"
BLOG = "https://rockstarblends.com/the-blog/feed-your-head-genx-hits-menopause-e30"

failures = 0


def check(name, cond, detail=""):
    global failures
    if cond:
        print(f"  ✓ {name}")
    else:
        failures += 1
        print(f"  ✗ {name}" + (f"\n      {detail}" if detail else ""))


# ── AC1 — a reprocess-completed episode is marked done ────────────────────────
print("\nAC1 — full reprocess marks the entry done (the E29/E30 gap)")
ep = {
    "episode_number": 30,
    "drive_folder_id": "abc",
    "status": "drafting",
    "pipeline_done": False,
    "apple_url": None,
    "spotify_url": None,
    "blog_post_url": None,
    "actual_live_date": None,
    "notes": "Round 1 complete.",
}
apply_completion(ep, apple_url=APPLE, spotify_url=SPOTIFY, mm_url="",
                 blog_post_url=BLOG, message_id="", today="2026-07-21")
check("status -> done", ep["status"] == "done", ep["status"])
check("pipeline_done -> True", ep["pipeline_done"] is True)
check("apple_url written", ep["apple_url"] == APPLE)
check("spotify_url written", ep["spotify_url"] == SPOTIFY)
check("blog_post_url written", ep["blog_post_url"] == BLOG)
check("notes appended, not replaced", ep["notes"].startswith("Round 1 complete."))

# ── AC2 — empty reprocess values never clobber known ones ─────────────────────
print("\nAC2 — a reprocess carries no mm_url/message_id; they must not erase")
ep = {
    "episode_number": 28,
    "drive_folder_id": "abc",
    "mm_url": "https://legacy-publisher.example.test/e28",
    "mission_matters_email_id": "r640177291",
    "apple_url": APPLE,
    "spotify_url": SPOTIFY,
    "notes": "",
}
apply_completion(ep, apple_url="", spotify_url="", mm_url="",
                 blog_post_url=BLOG, message_id="", today="2026-07-21")
check("mm_url preserved", ep["mm_url"] == "https://legacy-publisher.example.test/e28", ep["mm_url"])
check("mission_matters_email_id preserved",
      ep["mission_matters_email_id"] == "r640177291", ep["mission_matters_email_id"])
check("apple_url preserved when reprocess passes none", ep["apple_url"] == APPLE)
check("spotify_url preserved when reprocess passes none", ep["spotify_url"] == SPOTIFY)

# ── AC3 — actual_live_date is the publication date, not the recovery date ─────
print("\nAC3 — actual_live_date is preserved once known (E30 went live 07-09)")
ep = {"episode_number": 30, "drive_folder_id": "abc",
      "actual_live_date": "2026-07-09", "notes": ""}
apply_completion(ep, apple_url=APPLE, spotify_url=SPOTIFY, mm_url="",
                 blog_post_url=BLOG, message_id="", today="2026-07-21")
check("existing live date NOT overwritten with the recovery date",
      ep["actual_live_date"] == "2026-07-09", ep["actual_live_date"])

ep = {"episode_number": 31, "drive_folder_id": "abc", "notes": ""}
apply_completion(ep, apple_url=APPLE, spotify_url=SPOTIFY, mm_url="",
                 blog_post_url=BLOG, message_id="", today="2026-07-21")
check("live date defaults to today when previously unknown",
      ep["actual_live_date"] == "2026-07-21", ep["actual_live_date"])

# ── AC4 — the waiting-for-visuals context is cleared ──────────────────────────
print("\nAC4 — pending_mm_context is cleared on completion")
ep = {"episode_number": 30, "drive_folder_id": "abc", "notes": "",
      "pending_mm_context": {"apple_url": "x"}}
apply_completion(ep, apple_url=APPLE, spotify_url=SPOTIFY, mm_url="",
                 blog_post_url=BLOG, message_id="", today="2026-07-21")
check("pending_mm_context removed", "pending_mm_context" not in ep)

# ── AC5 — a blank notes field doesn't produce "None | ..." ────────────────────
print("\nAC5 — notes handles a None/absent field")
ep = {"episode_number": 30, "drive_folder_id": "abc", "notes": None}
apply_completion(ep, apple_url=APPLE, spotify_url=SPOTIFY, mm_url="",
                 blog_post_url=BLOG, message_id="", today="2026-07-21")
check("no 'None' leaked into notes", "None" not in ep["notes"], ep["notes"])

print()
if failures:
    print(f"❌ {failures} check(s) failed\n")
    sys.exit(1)
print("✅ schedule write-back: all cases pass\n")
