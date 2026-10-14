#!/usr/bin/env python3
"""
reprocess_episode.py — Enqueue a PUBLISH_FINALIZE reprocess job for an old episode.

Runs Phase 2 from existing Drive artifacts: faq.md, the blog post (written to
rockstarblends.com as a DRAFT through the Tailor API, and blog_post.json to Drive —
BLOG-02), reels, SEO captions and the thank-you draft. A full reprocess completes Phase 2:
it writes the schedule back (status done, URLs) and the .pipeline_done marker. It does NOT
touch the episode counter. --reels-only replaces reel MP4s and nothing else; --cards-only builds the quote-card
set (titles checked first) and nothing else.

Run on the server:
  cd /opt/rockstar-pipeline
  set -a && source .env && set +a && source venv/bin/activate
  python -m rockstar.tools.reprocess_episode --episode 23 \\
    --apple "https://podcasts.apple.com/us/podcast/..." \\
    --spotify "https://open.spotify.com/episode/..." \\
    --mm "https://legacy-publisher.example.test/..."

The worker picks up the job immediately. Watch progress:
  journalctl -u rockstar-worker -f
"""

import argparse
import re

from rockstar.service import db  # noqa: E402


def parse_quote_ids(text: str) -> list[str]:
    """ "q01,q04" → ["q01", "q04"]; [] if any id isn't qNN (the caller refuses), sorted and without repeats."""
    ids = [t.strip().lower() for t in (text or "").split(",") if t.strip()]
    if not ids or not all(re.fullmatch(r"q\d{2}", t) for t in ids):
        return []
    return sorted(set(ids))


def main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue a PUBLISH_FINALIZE reprocess job for an old episode")
    parser.add_argument("--episode", type=int, required=True, help="Episode number (e.g. 23)")
    parser.add_argument("--apple", default="", help="Apple Podcasts episode URL (required unless --reels-only)")
    parser.add_argument("--spotify", default="", help="Spotify episode URL (required unless --reels-only)")
    parser.add_argument("--mm", default="", help="Legacy Publisher episode URL (optional)")
    parser.add_argument(
        "--reels-only",
        action="store_true",
        help="Re-render and replace ONLY reel_*.mp4 in the Drive reels folder. "
        "Blog, FAQ, captions, thank-you draft, and calendar are untouched. "
        "URLs not needed (they only feed the skipped compose steps).",
    )
    parser.add_argument(
        "--cards-only",
        action="store_true",
        help="Rebuild ONLY the quote-card set (titles checked first) into Drive cards/. Nothing else "
        "is touched; a cards/ that already has files is left as is. URLs not needed.",
    )
    parser.add_argument(
        "--quotes",
        default="",
        help="With --reels-only: re-render ONLY these quotes' reels, e.g. q01,q04 (QUOTE-03). "
        "Every other reel in Drive is left as it is.",
    )
    args = parser.parse_args()

    if args.cards_only and args.reels_only:
        parser.error("--cards-only and --reels-only are separate runs")
    if not (args.reels_only or args.cards_only) and not (args.apple and args.spotify):
        parser.error("--apple and --spotify are required unless --reels-only or --cards-only is set")
    quotes = parse_quote_ids(args.quotes)
    if quotes and not args.reels_only:
        parser.error("--quotes needs --reels-only (a full reprocess rebuilds every surface)")
    if args.quotes and not quotes:
        parser.error(f"--quotes {args.quotes!r}: give quote ids like q01,q04")

    payload = {
        "trigger": "reprocess",
        "episode_number": args.episode,
        "apple_url": args.apple,
        "spotify_url": args.spotify,
        "mm_url": args.mm,
        "reels_only": args.reels_only,
        "cards_only": args.cards_only,
    }
    if quotes:
        payload["quotes"] = quotes

    job_id = db.enqueue("PUBLISH_FINALIZE", f"E{args.episode}", payload)
    print(
        f"✓ Enqueued PUBLISH_FINALIZE reprocess job {job_id} for E{args.episode}"
        + (f" (reels for {', '.join(quotes)} only)" if quotes else "")
        + (" (the quote cards only)" if args.cards_only else "")
    )
    print("  Watch: journalctl -u rockstar-worker -f")


if __name__ == "__main__":
    main()
