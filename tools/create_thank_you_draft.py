#!/usr/bin/env python3
"""
create_thank_you_draft.py — (re)create ONE guest thank-you draft, deterministically.

This is the sanctioned way to produce a guest thank-you so no one hand-composes it
in a chat again (that is exactly what produced the E29 transcript-garbage draft).
It runs ONLY the deterministic draft step — no LLM, no claude.ai Gmail MCP:

  1. read the reconciled publication_schedule.json entry for episode N
     (guest, title, and the verified Apple/Spotify/blog URLs written back at
      status=done)
  2. fill the template via thank_you.build_draft  (NO model)
  3. gate the body  (content_gates.assert_thank_you_wellformed) BEFORE writing
  4. create the draft server-side  (gmail_client.create_draft — DRAFT ONLY)
  5. raw-verify: re-read the stored draft and assert the gate passes on what is
     ACTUALLY stored + no 'google.com/url' redirect cruft

Run on the box (it reads the box's reconciled schedule + creates the draft there):
  cd /opt/rockstar-pipeline
  set -a && source .env && set +a && source venv/bin/activate
  python3 create_thank_you_draft.py --episode 29

⚠️ Creates a DRAFT only. It NEVER sends. Heather reviews and sends manually.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repo root: tools/ is one level down

import content_gates  # noqa: E402
import thank_you

# gmail_client (googleapiclient) is imported lazily inside create() so --dry-run
# runs anywhere — fill + gate need no network or SDK.

_STATE_DIR = Path(os.environ.get("STATE_DIR", "/opt/rockstar-pipeline/state"))
_PUB_SCHEDULE_PATH = _STATE_DIR / "publication_schedule.json"


def _find_episode(schedule: dict, episode_number: int) -> dict | None:
    for ep in schedule.get("episodes", []):
        if ep.get("episode_number") == episode_number:
            return ep
    return None


def _require(value: str, field: str, episode_number: int) -> str:
    """A verified URL/field is REQUIRED — refuse to draft on a missing slot rather
    than cite an empty or guessed value (PQ-5). The reconciled schedule is the only
    trusted source; if it lacks a slot, that is a real impediment, not a default."""
    v = (value or "").strip()
    if not v:
        raise SystemExit(
            f"E{episode_number}: schedule entry is missing {field!r}. Refusing to "
            f"draft with a missing slot — reconcile publication_schedule.json first "
            f"(this CLI never fabricates a URL)."
        )
    return v


def create(episode_number: int, *, dry_run: bool = False) -> int:
    if not _PUB_SCHEDULE_PATH.exists():
        raise SystemExit(f"publication_schedule.json not found at {_PUB_SCHEDULE_PATH}")

    schedule = json.loads(_PUB_SCHEDULE_PATH.read_text(encoding="utf-8"))
    ep = _find_episode(schedule, episode_number)
    if not ep:
        raise SystemExit(f"E{episode_number}: no entry in {_PUB_SCHEDULE_PATH}")

    guest_name = ep.get("guest_name") or "Guest"
    guest_email = ep.get("guest_email") or ""
    episode_title = _require(ep.get("episode_title"), "episode_title", episode_number)
    apple_url = _require(ep.get("apple_url"), "apple_url", episode_number)
    spotify_url = _require(ep.get("spotify_url"), "spotify_url", episode_number)
    blog_url = _require(ep.get("blog_post_url"), "blog_post_url", episode_number)

    ty = thank_you.build_draft(
        guest_name=guest_name,
        episode_title=episode_title,
        apple_url=apple_url,
        spotify_url=spotify_url,
        blog_url=blog_url,
        guest_email=guest_email,
    )

    # Gate BEFORE create_draft — junk never becomes a draft.
    content_gates.assert_thank_you_wellformed(ty["body"])

    print(f"E{episode_number} thank-you draft (deterministic fill):")
    print(f"  to:      {ty['to']}")
    print(f"  subject: {ty['subject']}")
    print(f"  first_name: {ty['first_name']}")
    print("  body gate: PASS (well-formed template)")

    if dry_run:
        print("\n--- body ---")
        print(ty["body"])
        print("--- end body (dry run — no draft created) ---")
        return 0

    import gmail_client  # deferred: only the real draft path needs the Gmail SDK

    draft = gmail_client.create_draft(to=ty["to"], subject=ty["subject"], body=ty["body"])
    draft_id = draft["id"]
    print(f"  created draft id={draft_id} (NOT sent)")
    # OPEN-1 (BLOG-02, John 2026-09-22): the draft is created either way, but it is only
    # reported READY when the blog post answers 200 — a draft post 404s until Heather
    # publishes it. One definition of the check, shared with Phase 2's Step 13.
    import blog_publish  # stdlib-only; deferred so --dry-run needs nothing new

    print(f"  blog URL: {blog_publish.post_live_check(blog_url)[1]}")

    # ── raw-verify: prove what is stored server-side ──────────────────────────
    stored_body = gmail_client.get_draft_body(draft_id)
    content_gates.assert_thank_you_wellformed(stored_body)  # raises on any drift
    raw = gmail_client.get_draft_raw(draft_id)
    if "google.com/url" in raw:
        raise SystemExit(
            f"E{episode_number}: stored draft {draft_id} contains a google.com/url "
            f"redirect — that is the MCP's URL-rewrite cruft. This path uses "
            f"gmail_client directly and must never produce it."
        )
    print("  raw-verify: PASS (stored body gate OK, no google.com/url redirects)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministically (re)create ONE guest thank-you Gmail draft (DRAFT only)."
    )
    parser.add_argument("--episode", type=int, required=True, help="Episode number (e.g. 29)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fill + gate + print the body, but do NOT create the draft.",
    )
    args = parser.parse_args()
    sys.exit(create(args.episode, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
