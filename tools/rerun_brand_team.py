#!/usr/bin/env python3
"""
rerun_brand_team.py — Re-run Brand Team chain for an existing episode and enqueue
a PUBLISH_FINALIZE reprocess job.

Use when you want fresh quote selection without re-transcribing (e.g., after updating
Brand Team prompts or when existing quotes missed the point).

Flow:
  1. Read pub_schedule to get episode metadata + Drive folder
  2. Download transcript_fast.txt from Drive
  3. Run Brand Team chain (marcus → robert → dr_joe → lisa)
  4. Delete old quotes.json from Drive, upload new one
  5. Enqueue PUBLISH_FINALIZE reprocess job

Run on the server:
  cd /opt/rockstar-pipeline
  set -a && source .env && set +a && source venv/bin/activate
  python3 rerun_brand_team.py --episode 17 \\
    --apple "https://podcasts.apple.com/..." \\
    --spotify "https://open.spotify.com/episode/..."
"""

import argparse
import json
import os
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repo root: tools/ is one level down

import anthropic_client  # noqa: E402
import run_ledger
import content_gates
import db
import drive_client

_STATE_DIR = Path(os.environ.get("STATE_DIR", "/opt/rockstar-pipeline/state"))
_PUB_SCHEDULE_PATH = _STATE_DIR / "publication_schedule.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-run Brand Team chain for an existing episode"
    )
    parser.add_argument("--episode", type=int, required=True, help="Episode number (e.g. 17)")
    parser.add_argument("--apple",   default="",   help="Apple Podcasts URL (optional if already in pub_schedule)")
    parser.add_argument("--spotify", default="",   help="Spotify URL (optional if already in pub_schedule)")
    parser.add_argument("--mm",      default="",   help="Legacy Publisher URL (optional)")
    args = parser.parse_args()

    pub_schedule = _read_json(_PUB_SCHEDULE_PATH)
    ep_entry = next(
        (e for e in pub_schedule.get("episodes", []) if e.get("episode_number") == args.episode),
        None,
    )
    if not ep_entry:
        print(f"ERROR: E{args.episode} not found in publication_schedule.json")
        raise SystemExit(1)

    folder_id = ep_entry.get("drive_folder_id")
    if not folder_id:
        print(f"ERROR: no drive_folder_id for E{args.episode}")
        raise SystemExit(1)

    episode_metadata = {
        "episode_number":    args.episode,
        "episode_code":      ep_entry.get("episode_code", f"E{args.episode}"),
        "season_episode_code": ep_entry.get("season_episode_code", ""),
        "slug":              ep_entry.get("slug", ""),
        "guest_name":        ep_entry.get("guest_name"),
    }

    print(f"E{args.episode} — folder {folder_id}")
    print("Downloading transcript_fast.txt ...")

    folder_files = drive_client.list_folder(folder_id)
    file_map = {f["name"]: f["id"] for f in folder_files}

    # Prefer root transcript; fall back to transcripts/ subfolder
    transcript_id = file_map.get("transcript_fast.txt")
    if not transcript_id:
        for f in folder_files:
            if "folder" in f.get("mimeType", "") and f["name"] == "transcripts":
                sub = drive_client.list_folder(f["id"])
                sub_map = {s["name"]: s["id"] for s in sub}
                transcript_id = sub_map.get("transcript_fast.txt")
                break

    if not transcript_id:
        print("ERROR: transcript_fast.txt not found in Drive folder")
        raise SystemExit(1)

    with tempfile.TemporaryDirectory(prefix=f"rockstar-bt-E{args.episode}-") as tmp:
        tmp_path = Path(tmp)
        transcript_path = drive_client.download_file(transcript_id, tmp_path / "transcript_fast.txt")
        transcript_text = transcript_path.read_text(encoding="utf-8")
        print(f"  transcript: {len(transcript_text)} chars")

        print("Running Brand Team chain ...")
        run_ledger.bind(episode=f"E{args.episode}", run_kind="manual")  # OBS-01: hand-run, never episode spend
        quotes = anthropic_client.run_brand_team_chain(
            transcript_text=transcript_text,
            episode_metadata=episode_metadata,
        )
        print(f"  Brand Team → {len(quotes)} quotes")
        # Same Phase-1 gate as round_1 (BLOG-02 F-1): refuse BEFORE the old quotes.json
        # is deleted, so a headless re-run can't replace a good set on Drive.
        try:
            content_gates.assert_quote_headings(quotes, f"E{args.episode}")
        except content_gates.GateFailure as exc:
            print(f"ERROR: {exc}")
            raise SystemExit(1)

        quotes_path = tmp_path / "quotes.json"
        quotes_path.write_text(
            json.dumps({"quotes": quotes}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Delete old quotes.json from Drive
        if "quotes.json" in file_map:
            from googleapiclient.discovery import build as _build
            from google_auth import drive_credentials as _drive_creds
            svc = _build("drive", "v3", credentials=_drive_creds(), cache_discovery=False)
            svc.files().delete(fileId=file_map["quotes.json"]).execute()
            print("  deleted old quotes.json")

        drive_client.upload_file(quotes_path, folder_id, "quotes.json", "application/json")
        print("  uploaded new quotes.json")

    apple_url   = args.apple   or ep_entry.get("apple_url",   "")
    spotify_url = args.spotify or ep_entry.get("spotify_url", "")
    mm_url      = args.mm      or ep_entry.get("mm_url",      "")

    job_id = db.enqueue("PUBLISH_FINALIZE", f"E{args.episode}", {
        "trigger":        "reprocess",
        "episode_number": args.episode,
        "apple_url":      apple_url,
        "spotify_url":    spotify_url,
        "mm_url":         mm_url,
    })
    print(f"\n✓ Enqueued PUBLISH_FINALIZE reprocess job {job_id} for E{args.episode}")
    print("  Watch: journalctl -u rockstar-worker -f")


if __name__ == "__main__":
    main()
