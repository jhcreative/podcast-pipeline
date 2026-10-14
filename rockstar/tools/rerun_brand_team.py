#!/usr/bin/env python3
"""
rerun_brand_team.py — Re-run the Brand Team chain for an existing episode, through the SAME quote check as Round 1
(QUOTE-01 verbatim, CLIP-01c length: quote_gate.check_selected), then the heading and guest gates. Phase 2 is queued
only with --enqueue: a person reads the "listen" and long_form quotes first (QUOTE-04 isn't built yet).

Use when you want fresh quote selection without re-transcribing (e.g., after updating
Brand Team prompts or when existing quotes missed the point).

Flow:
  1. Read pub_schedule to get episode metadata + Drive folder
  2. Download transcript_fast.txt from Drive
  3. Run Brand Team chain (marcus → robert → dr_joe → lisa), told the box's quote ceilings
  4. The quote check (verbatim + length, Lisa's one restore-or-swap pass), the heading and guest gates: any refusal
     stops here, before the old quotes.json is touched
  5. Move the old quotes.json to Drive's trash (kept 30 days), upload the new one. A run where Lisa or Dr. Joe was
     SKIPPED stops before this: an unreviewed set never replaces a reviewed one
  6. With --enqueue only: enqueue the PUBLISH_FINALIZE reprocess job

Run on the server:
  cd /opt/rockstar-pipeline
  set -a && source .env && set +a && source venv/bin/activate
  python -m rockstar.tools.rerun_brand_team --episode 17 \\
    --apple "https://podcasts.apple.com/..." \\
    --spotify "https://open.spotify.com/episode/..."
"""

import argparse
import json
import tempfile
from pathlib import Path

from rockstar import config
from rockstar.clients import (
    anthropic_client,  # noqa: E402
    drive_client,
)
from rockstar.gates import clip_limits, content_gates, quote_gate, quote_title
from rockstar.ops import run_ledger
from rockstar.service import db

_STATE_DIR = config.state_dir()
REVIEWERS = ("brand_team_dr_joe", "brand_team_lisa")  # their skip means claims and disclaimers nobody reviewed
_PUB_SCHEDULE_PATH = _STATE_DIR / "publication_schedule.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run Brand Team chain for an existing episode")
    parser.add_argument("--episode", type=int, required=True, help="Episode number (e.g. 17)")
    parser.add_argument("--apple", default="", help="Apple Podcasts URL (optional if already in pub_schedule)")
    parser.add_argument("--spotify", default="", help="Spotify URL (optional if already in pub_schedule)")
    parser.add_argument("--mm", default="", help="Legacy Publisher URL (optional)")
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="also queue Phase 2 (only after the listen / long_form quotes have been read)",
    )
    args = parser.parse_args()

    try:
        limits = clip_limits.load()  # CLIP-01 AC 3: no measured limit, no quote selection
    except clip_limits.LimitsMissing as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from None

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
        "episode_number": args.episode,
        "episode_code": ep_entry.get("episode_code", f"E{args.episode}"),
        "season_episode_code": ep_entry.get("season_episode_code", ""),
        "slug": ep_entry.get("slug", ""),
        "guest_name": ep_entry.get("guest_name"),
        "quote_ceiling_seconds": limits.standard_s,  # CLIP-01c AC 5, as Round 1 tells the brand team
        "long_form_quote_ceiling_seconds": limits.long_s,
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
    segments_id = _find(folder_files, "transcript_fast.json")  # the quote check places quotes on these segments
    if not segments_id:
        print("ERROR: transcript_fast.json not found in Drive folder: the quote check needs it")
        raise SystemExit(1)

    with tempfile.TemporaryDirectory(prefix=f"rockstar-bt-E{args.episode}-") as tmp:
        tmp_path = Path(tmp)
        transcript_path = drive_client.download_file(transcript_id, tmp_path / "transcript_fast.txt")
        transcript_text = transcript_path.read_text(encoding="utf-8")
        print(f"  transcript: {len(transcript_text)} chars")

        segments = json.loads(
            drive_client.download_file(segments_id, tmp_path / "transcript_fast.json").read_text(encoding="utf-8")
        )

        print("Running Brand Team chain ...")
        code = f"E{args.episode}"
        run_ledger.bind(episode=code, run_kind="manual")  # OBS-01: hand-run, never episode spend
        brand_report: dict = {}
        selected = anthropic_client.run_brand_team_chain(
            transcript_text=transcript_text,
            episode_metadata=episode_metadata,
            report=brand_report,
        )
        print(
            f"  Brand Team → {len(selected)} quotes"
            + (f" (SKIPPED: {', '.join(brand_report['skipped'])})" if brand_report.get("skipped") else "")
        )
        if brand_report.get("reviewer_dropped"):
            print(f"  a reviewer left quotes out, carried forward unchanged: {brand_report['reviewer_dropped']}")
        # A reviewer who didn't run means a set nobody reviewed: never let it replace the one on Drive (Toby).
        unreviewed = [m for m in brand_report.get("skipped", []) if m in REVIEWERS]
        if "brand_team_dr_joe" in brand_report.get("reviewer_dropped", {}):  # a partial clinical review is no review
            unreviewed.append(
                "brand_team_dr_joe (quotes " + ", ".join(brand_report["reviewer_dropped"]["brand_team_dr_joe"]) + ")"
            )
        if unreviewed:
            print(
                f"ERROR: {', '.join(unreviewed)} SKIPPED (no valid JSON after retry); quotes.json on Drive is "
                f"unchanged. Run this again."
            )
            raise SystemExit(1)
        # The same check as Round 1 step 10b, then the same gates, all BEFORE the old quotes.json is deleted, so a
        # re-run can never replace a good set on Drive with an unchecked one.
        quotes, verbatim_report, line = quote_gate.check_selected(
            selected,
            segments,
            code,
            lambda problems: anthropic_client.restore_or_swap(transcript_text, selected, problems, episode_metadata),
            limits,
        )
        brand_report["verbatim"] = verbatim_report
        print(f"  {line}")
        quotes, title_report = quote_title.ensure(  # TITLE-02: the same one title as Round 1
            quotes, code, lambda bad: anthropic_client.rewrite_quote_titles("", quotes, bad)
        )
        if quote_title.summary(title_report):
            print(f"  {quote_title.summary(title_report)}")
            run_ledger.record(
                "quote_titles", **{k: title_report[k] for k in ("rewritten", "repaired", "plain", "unfixed")}
            )
        run_ledger.record(
            "quote_lengths",
            ceiling=verbatim_report.get("ceiling"),
            lengths=verbatim_report.get("lengths"),
            long_form=verbatim_report.get("long_form"),
            dropped=[{"id": d["id"], "why": d["why"], "span_s": d.get("span_s")} for d in verbatim_report["dropped"]],
            long_form_pending=verbatim_report.get("long_form_pending"),
        )
        if not quotes:
            print("ERROR: no quote survived the verbatim and length check; quotes.json on Drive is unchanged")
            raise SystemExit(1)
        try:
            content_gates.assert_quote_headings(quotes, code)
            content_gates.assert_guest_featured(quotes, episode_metadata.get("guest_name") or "", code)
        except content_gates.GateFailure as exc:
            print(f"ERROR: {exc}")
            raise SystemExit(1) from None

        quotes_path = tmp_path / "quotes.json"
        quotes_path.write_text(
            json.dumps({"quotes": quotes, "brand_team": brand_report}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Trash the old quotes.json (30 days in Drive's trash, as CARD-02 does): old sets are evidence (QUOTE-03).
        if "quotes.json" in file_map:
            drive_client.trash_file(file_map["quotes.json"])
            print("  old quotes.json moved to Drive's trash")

        drive_client.upload_file(quotes_path, folder_id, "quotes.json", "application/json")
        print("  uploaded new quotes.json")

    apple_url = args.apple or ep_entry.get("apple_url", "")
    spotify_url = args.spotify or ep_entry.get("spotify_url", "")
    mm_url = args.mm or ep_entry.get("mm_url", "")

    if not args.enqueue:
        print(
            f"\nPhase 2 NOT queued. Read the listen / long_form quotes first, then:"
            f"\n  python3 -m rockstar.tools.reprocess_episode --episode {args.episode} ...   "
            "(or re-run this with --enqueue)"
        )
        return
    job_id = db.enqueue(
        "PUBLISH_FINALIZE",
        f"E{args.episode}",
        {
            "trigger": "reprocess",
            "episode_number": args.episode,
            "apple_url": apple_url,
            "spotify_url": spotify_url,
            "mm_url": mm_url,
        },
    )
    print(f"\n✓ Enqueued PUBLISH_FINALIZE reprocess job {job_id} for E{args.episode}")
    print("  Watch: journalctl -u rockstar-worker -f")


def _find(folder_files: list[dict], name: str) -> str | None:
    """A file's id in the episode folder, or in its transcripts/ subfolder."""
    for f in folder_files:
        if f["name"] == name:
            return f["id"]
    for f in folder_files:
        if "folder" in f.get("mimeType", "") and f["name"] == "transcripts":
            for s in drive_client.list_folder(f["id"]):
                if s["name"] == name:
                    return s["id"]
    return None


if __name__ == "__main__":
    main()
