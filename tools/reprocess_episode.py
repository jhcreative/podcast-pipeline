#!/usr/bin/env python3
"""
reprocess_episode.py — Enqueue a PUBLISH_FINALIZE reprocess job for an old episode.

Runs Phase 2 from existing Drive artifacts: faq.md, the blog post (written to
rockstarblends.com as a DRAFT through the Tailor API, and blog_post.json to Drive —
BLOG-02), reels, SEO captions and the thank-you draft. A full reprocess completes Phase 2:
it writes the schedule back (status done, URLs) and the .pipeline_done marker. It does NOT
touch the episode counter. --reels-only replaces reel MP4s and nothing else.

Run on the server:
  cd /opt/rockstar-pipeline
  set -a && source .env && set +a && source venv/bin/activate
  python3 reprocess_episode.py --episode 23 \\
    --apple "https://podcasts.apple.com/us/podcast/..." \\
    --spotify "https://open.spotify.com/episode/..." \\
    --mm "https://legacy-publisher.example.test/..."

The worker picks up the job immediately. Watch progress:
  journalctl -u rockstar-worker -f
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repo root: tools/ is one level down

import db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enqueue a PUBLISH_FINALIZE reprocess job for an old episode"
    )
    parser.add_argument("--episode", type=int, required=True,
                        help="Episode number (e.g. 23)")
    parser.add_argument("--apple", default="",
                        help="Apple Podcasts episode URL (required unless --reels-only)")
    parser.add_argument("--spotify", default="",
                        help="Spotify episode URL (required unless --reels-only)")
    parser.add_argument("--mm", default="",
                        help="Legacy Publisher episode URL (optional)")
    parser.add_argument("--reels-only", action="store_true",
                        help="Re-render and replace ONLY reel_*.mp4 in the Drive reels folder. "
                             "Blog, FAQ, captions, thank-you draft, and calendar are untouched. "
                             "URLs not needed (they only feed the skipped compose steps).")
    args = parser.parse_args()

    if not args.reels_only and not (args.apple and args.spotify):
        parser.error("--apple and --spotify are required unless --reels-only is set")

    payload = {
        "trigger": "reprocess",
        "episode_number": args.episode,
        "apple_url": args.apple,
        "spotify_url": args.spotify,
        "mm_url": args.mm,
        "reels_only": args.reels_only,
    }

    job_id = db.enqueue("PUBLISH_FINALIZE", f"E{args.episode}", payload)
    print(f"✓ Enqueued PUBLISH_FINALIZE reprocess job {job_id} for E{args.episode}")
    print(f"  Watch: journalctl -u rockstar-worker -f")


if __name__ == "__main__":
    main()
