"""
jobs/round_1.py — Round 1 pipeline: intake, transcribe, Brand Team, compose.

Triggered by a Drive push-notification on recordings_inbox/ (ROUND_1 job).
One invocation processes the first unprocessed .m4a found in the inbox.
If multiple files are present, subsequent webhook events enqueue additional
ROUND_1 jobs that pick up the remaining files.

Flow:
  1.  Read episode_counter.json → next episode number + season
  2.  List recordings_inbox → find unprocessed .m4a files
  3.  Skip if episode folder already has .pipeline_done marker (idempotency)
  4.  Derive slug from filename (first 3 meaningful words, kebab, stopwords dropped)
  5.  Create Episodes/E{N}-{slug}/ folder in Drive
  6.  Download .m4a to /tmp work dir
  7.  Move .m4a from inbox into episode folder
  8.  bash scripts/transcribe_fast.sh → transcript_fast.json + transcript.md
  9.  node scripts/detect_boundaries.js → boundaries.json
  10. anthropic_client.run_brand_team_chain() → quotes.json
  11. anthropic_client.compose_apple_summary() → apple_summary.md
  12. Upload all artifacts to Drive episode folder
  13. calendar_client.create_editorial_event() → calendar event_id
  14. Update episode_counter.json (increment)
  15. Update publication_schedule.json (add entry, status=drafting)

State files:  STATE_DIR   (default /opt/rockstar-pipeline/state/)
Scripts:      SCRIPTS_DIR (default /opt/rockstar-pipeline/scripts/)
Work dir:     /tmp/rockstar-{episode_code}-{tempdir}/ — always cleaned up
              in the finally block, even on failure.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import run_ledger
import anthropic_client
import calendar_client
import content_gates
import db
import drive_client
import episode_files
import guest_form
import summary_norm
import workspace

logger = logging.getLogger(__name__)

# ── paths ────────────────────────────────────────────────────────────────────────

_STATE_DIR = Path(os.environ.get("STATE_DIR", "/opt/rockstar-pipeline/state"))
_SCRIPTS_DIR = Path(os.environ.get("SCRIPTS_DIR", "/opt/rockstar-pipeline/scripts"))
_EPISODE_COUNTER_PATH = _STATE_DIR / "episode_counter.json"
_PUB_SCHEDULE_PATH = _STATE_DIR / "publication_schedule.json"
_STANDUP_PATH = _STATE_DIR / "standup.md"

# R1-01: the audio / episode-file rules live in episode_files.py, shared with drive_changes and drive_client.
_AUDIO_EXTENSIONS = episode_files.PROCESSABLE_EXTENSIONS

# ── slug derivation ──────────────────────────────────────────────────────────────

# Common English stopwords dropped when building a 3-word slug.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "its", "it",
    "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "she", "his", "her", "they", "their", "them",
    "what", "which", "who", "how", "when", "where", "why", "all", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "not",
    "only", "same", "so", "than", "too", "very", "just", "about", "into",
    "over", "after", "before", "between", "through", "during", "up",
    "ep", "episode",  # strip common filename prefixes
})


def _derive_slug(filename: str) -> str:
    """
    Derive a URL-safe 3-word kebab slug from an audio filename.

    Examples:
      "EP25 - Red Light Blue Therapy with Dr Nadia.m4a" → "red-light-blue"
      "2026-05-10 guest interview heather.m4a"         → "guest-interview-heather"

    Strategy: strip extension → replace separators with spaces → lowercase →
    strip non-alphanumeric → drop stopwords → take first 3 → kebab-join.
    Falls back to first 3 raw tokens if stopword filtering leaves too few.
    """
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-–—]+", " ", stem)
    cleaned = re.sub(r"[^a-zA-Z0-9 ]", "", cleaned)
    words = cleaned.lower().split()

    # Strip episode number tokens (e25, e26, ep25, etc.) — these are file
    # naming conventions, not content words.
    words = [w for w in words if not re.match(r'^e\d+$', w) and not re.match(r'^ep\d+$', w)]

    meaningful = [w for w in words if w not in _STOPWORDS and len(w) > 1]

    if len(meaningful) >= 3:
        slug_words = meaningful[:3]
    elif len(meaningful) >= 1:
        used = set(meaningful)
        extras = [w for w in words if w not in used and len(w) > 1]
        slug_words = (meaningful + extras)[:3]
    else:
        slug_words = [w for w in words if len(w) > 1][:3]

    # R1-01: "E30.m4a" has no words; "untitled" poisoned the folder name and schedule slug.
    return "-".join(slug_words) if slug_words else "episode"


# ── state I/O ────────────────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    """Write via temp file + rename to guard against partial-write corruption."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _already_processed(pub_schedule: dict, file_id: str) -> bool:
    """Return True if this Drive file_id already has a schedule entry."""
    return any(
        ep.get("drive_audio_file_id") == file_id
        for ep in pub_schedule.get("episodes", [])
    )


# ── subprocess runner ─────────────────────────────────────────────────────────────


def _run_script(cmd: list, work_dir: Path, label: str) -> None:
    """
    Run an external script.  Logs stdout/stderr and raises RuntimeError on
    non-zero exit so the worker's exponential back-off handles transient
    failures (OOM during transcription, missing Node module, etc.).
    """
    logger.info("%s: %s", label, " ".join(str(c) for c in cmd))
    result = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
    )
    if result.stdout:
        logger.info("%s stdout: %.800s", label, result.stdout)
    if result.stderr:
        logger.warning("%s stderr: %.800s", label, result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {result.returncode}): {result.stderr[:300]}"
        )


# ── standup writer ───────────────────────────────────────────────────────────────


def _prepend_standup(episode_code: str, error: str) -> None:
    """
    Prepend a timestamped error block to standup.md without erasing existing content.
    Standup file lives at STATE_DIR/standup.md — readable via SSH or a future Drive sync.
    Never overwrites: new errors go to the top so the most recent failure is always first.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    block = (
        f"## ❌ PIPELINE ERROR — {episode_code} — {ts}\n\n"
        f"```\n{error}\n```\n\n"
        f"**Action required:** Fix the issue, move {episode_code} audio back to "
        f"recordings_inbox if needed, then restart the worker.\n\n"
        f"---\n\n"
    )
    existing = ""
    if _STANDUP_PATH.exists():
        existing = _STANDUP_PATH.read_text(encoding="utf-8")
    _STANDUP_PATH.write_text(block + existing, encoding="utf-8")
    logger.error("round_1: standup updated — %s", _STANDUP_PATH)


# ── main job ──────────────────────────────────────────────────────────────────────


def run(job_id: int, episode_id: str, payload: dict) -> None:
    """
    Round 1 pipeline — one .m4a from inbox to drafted episode in Drive.

    episode_id is "TBD" at enqueue time (set by webhook_service.py);
    this job resolves it by reading episode_counter.json and the inbox.
    payload: {"trigger": "drive_inbox", "resource_state": "..."}
    """
    logger.info("round_1 start — job_id=%d payload=%s", job_id, payload)

    # Wrap the entire pipeline so any unhandled exception:
    #   1. Writes a timestamped error block to standup.md (prepend, never erase)
    #   2. Raises PermanentFailure so the worker skips retries — a pipeline error
    #      is a code/data problem that retrying won't fix, and retries risk picking
    #      up the wrong episode file from the inbox.
    try:
        _run_pipeline(job_id, payload)
    except PermanentFailure:
        raise  # already logged + standup written; worker sees this and stops
    except Exception as exc:
        # Resolve episode code best-effort for the standup message.
        try:
            counter = _read_json(_EPISODE_COUNTER_PATH)
            ep_code = f"E{counter['next_episode_number']}"
        except Exception:
            ep_code = "UNKNOWN"
        _prepend_standup(ep_code, f"{type(exc).__name__}: {exc}")
        raise PermanentFailure(f"{ep_code} pipeline failed: {exc}") from exc


def _archive_existing_artifacts(episode_folder_id: str, names: list[str]) -> int:
    """R1-05: before a re-run uploads fresh artifacts, move same-named ROOT files into "_superseded-<UTC>".

    upload_file always CREATES, so a re-run used to leave two apple_summary.md (or quotes.json …) side by side,
    and Phase 2 read whichever the listing put first. Moving, never deleting: a hand-edited copy (including a
    Google-Doc conversion of the same name) is kept, as history Phase 2 already skips (_superseded* =
    publish_finalize._is_archive_folder). Returns how many files moved; 0 on a first run.
    """
    wanted = set(names) | {n.rsplit(".", 1)[0] for n in names}  # "apple_summary.md" and its Doc "apple_summary"
    clashes = [f for f in drive_client.list_folder(episode_folder_id)
               if f.get("name") in wanted and "folder" not in f.get("mimeType", "")]
    if not clashes:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    archive = drive_client.create_folder(f"_superseded-{stamp}", parent_folder_id=episode_folder_id)
    for f in clashes:
        drive_client.move_file(f["id"], archive["id"])
    logger.warning(
        "round_1: re-run archived %d existing artifact(s) to _superseded-%s: %s",
        len(clashes), stamp, ", ".join(sorted(f["name"] for f in clashes)),
    )
    return len(clashes)


def _season_for(ep_num: int, counter: dict) -> tuple[int, int]:
    """Season and in-season number for an episode with no schedule entry.

    The counter knows the current season and where it started: next_episode_number - next_season_episode + 1
    (E33/S2E8 → season 2 started at E26). An episode at or after that is in the current season. One before it
    is in the previous season, which (Season 1) started at E1, so its in-season number is its E-number. This
    pipeline has had two seasons; a third would need its start recorded, so anything older than one season back
    is refused rather than guessed.
    """
    current = int(counter["current_season"])
    start = int(counter["next_episode_number"]) - int(counter["next_season_episode"]) + 1
    if ep_num >= start:
        return current, ep_num - start + 1
    if current == 2:
        return 1, ep_num
    raise PermanentFailure(
        f"E{ep_num}: no schedule entry and season {current} started at E{start}; "
        "add the episode's season to publication_schedule.json before reprocessing"
    )


def _alert_stranded(target_prefix: str, lines: list[str]) -> None:
    """R1-01: an actionable alert (file + exact fix) to PIPELINE_ALERT_EMAIL. Best-effort: the job fails anyway."""
    to = os.environ.get("PIPELINE_ALERT_EMAIL", "john@example.test")
    body = (
        f"Round 1 woke up for {target_prefix} and found nothing it can process, but the recordings inbox "
        "holds audio:\n\n" + "\n".join(f"  - {ln}" for ln in lines)
        + "\n\nFix the file(s) in the inbox; the next Drive poll (≤10 min) picks them up again."
    )
    try:
        import gmail_client
        gmail_client.send_pipeline_alert(to=to, subject=f"Round 1: inbox audio not processed ({target_prefix})", body=body)
    except Exception as exc:  # noqa: BLE001 — the PermanentFailure + standup still record it
        logger.error("round_1: stranded-audio alert failed to send: %s", exc)


def calendar_event_enabled() -> bool:
    """CAL-OFF: the Round-1 editorial calendar event is OFF unless EDITORIAL_CALENDAR_EVENT is on/true/1."""
    return os.environ.get("EDITORIAL_CALENDAR_EVENT", "off").strip().lower() in ("on", "true", "1", "yes")


class PermanentFailure(Exception):
    """Raised when a pipeline error should not be retried (worker stops immediately)."""


def _run_pipeline(job_id: int, payload: dict) -> None:
    """Inner pipeline — called by run(); wrapped for standup-on-error."""
    # ── 1. Episode counter + reprocess detection ───────────────────────────────
    run_ledger.step("1", "episode_counter")
    counter = _read_json(_EPISODE_COUNTER_PATH)
    pub_schedule = _read_json(_PUB_SCHEDULE_PATH)

    # Read inbox once — reused in step 2, avoids a second Drive API call.
    inbox_files = drive_client.list_audio_in_inbox()
    episode_named = [f for f in inbox_files if episode_files.is_processable(f["name"])]

    # Reprocess detection: if a file with E{N} (N < current counter) is in the
    # inbox the operator wants to regenerate that episode's artifacts.
    # Use the filename's N; do not advance the counter.
    reprocess = False
    existing_sched_entry = None
    for f in episode_named:
        n = episode_files.episode_number(f["name"])
        if n is not None and n < counter["next_episode_number"]:
            ep_num = n
            reprocess = True
            existing_sched_entry = next(
                (e for e in pub_schedule.get("episodes", [])
                 if e.get("episode_number") == ep_num),
                None,
            )
            if existing_sched_entry:
                season = existing_sched_entry["season"]
                season_ep = existing_sched_entry["season_episode"]
            else:
                # An old episode the pipeline has never seen (John, 2026-10-01: back-catalogue episodes "don't
                # exist yet in this pipeline"). This used to give "current season, number = E-number", so E19
                # became S2E19. Derive it from the counter instead (see _season_for).
                season, season_ep = _season_for(ep_num, counter)
            logger.info(
                "round_1: reprocess mode — E%d from inbox (counter stays at E%d)",
                ep_num, counter["next_episode_number"],
            )
            break

    if not reprocess:
        ep_num = counter["next_episode_number"]
        season = counter["current_season"]
        season_ep = counter["next_season_episode"]

    episode_code = f"E{ep_num}"
    run_ledger.bind(episode=episode_code)
    season_ep_code = f"S{season}E{season_ep}"
    logger.info(
        "round_1: targeting %s (%s)%s",
        episode_code, season_ep_code, " [REPROCESS]" if reprocess else "",
    )

    # ── 2. Find target episode audio ───────────────────────────────────────────
    # Rules:
    #   a) Only consider files matching E<N>-* naming convention.
    #   b) Only process the target episode. Never fall through to a later one.
    #   c) Retry-safety: if audio was already moved to the episode folder on a
    #      prior failed run, the inbox won't have it — check the folder too.
    #   d) Reprocess: bypass _already_processed — operator explicitly wants re-run.
    run_ledger.step("2", "find_audio")
    target_prefix = f"E{ep_num}"

    # R1-07: match the target by episode NUMBER, never by name prefix ("E1" is a prefix of "E13-…").
    if reprocess:
        matched_in_inbox = [f for f in episode_named if episode_files.episode_number(f["name"]) == ep_num]
    else:
        unprocessed = [f for f in episode_named if not _already_processed(pub_schedule, f["id"])]
        matched_in_inbox = [f for f in unprocessed if episode_files.episode_number(f["name"]) == ep_num]

    audio_file = None
    audio_already_in_folder = False

    if matched_in_inbox:
        audio_file = matched_in_inbox[0]
    else:
        # Target not in inbox — check episode folder (retry resume path).
        early_folder = drive_client.find_episode_folder(episode_code)
        if early_folder:
            folder_files = drive_client.list_folder(early_folder["id"])
            ep_audio = [
                f for f in folder_files
                if Path(f["name"]).suffix.lower() in _AUDIO_EXTENSIONS
                and f["name"].upper().startswith(target_prefix.upper())
            ]
            if ep_audio:
                audio_file = ep_audio[0]
                audio_already_in_folder = True
                logger.info(
                    "round_1: %s audio not in inbox — found in episode folder "
                    "(resuming from previous failed run, skipping move step)",
                    episode_code,
                )

    if audio_file is None:
        # R1-01: a job that found nothing to process while audio IS in the inbox is an anomaly, never a
        # green "done" (E30 sat two hours that way). Name every file and its fix, alert, and fail.
        waiting = [] if reprocess else [f["name"] for f in unprocessed]
        stranded = [episode_files.problem(f["name"], ep_num) for f in inbox_files
                    if not episode_files.is_processable(f["name"])]
        if waiting or stranded:
            lines = stranded + [
                f"'{w}' is waiting: it will not be processed until {target_prefix} is in the inbox "
                f"(the pipeline processes episodes in order)" for w in waiting
            ]
            _alert_stranded(target_prefix, lines)
            raise PermanentFailure(
                f"{target_prefix}: no processable audio, but the inbox holds {len(lines)} file(s): "
                + "; ".join(lines)
            )
        logger.info("round_1: inbox is empty — nothing to do")
        run_ledger.skipped("inbox empty")
        return  # clean stop — never process a different episode

    file_id = audio_file["id"]
    filename = audio_file["name"]
    logger.info("round_1: processing %r (id=%s)", filename, file_id)

    # ── 3. Idempotency — .pipeline_done check ──────────────────────────────────
    # early_folder may already be set from the retry-resume path above; reuse it.
    run_ledger.step("3", "idempotency")
    existing_folder = (
        early_folder
        if audio_already_in_folder
        else drive_client.find_episode_folder(episode_code)
    )
    if not reprocess and existing_folder and drive_client.has_pipeline_done_marker(existing_folder["id"]):
        logger.info(
            "round_1: %s already has .pipeline_done — skipping (drop again to reprocess)",
            episode_code,
        )
        run_ledger.skipped(".pipeline_done already present")
        return

    # ── 4. Slug ────────────────────────────────────────────────────────────────
    run_ledger.step("4", "slug")
    slug = _derive_slug(filename)
    folder_name = f"{episode_code}-{slug}"
    logger.info("round_1: slug=%r folder=%r", slug, folder_name)

    # ── 5. Create episode folder in Drive ──────────────────────────────────────
    run_ledger.step("5", "create_folder")
    if existing_folder:
        episode_folder = existing_folder
        logger.info("round_1: reusing existing folder id=%s", existing_folder["id"])
    else:
        episode_folder = drive_client.create_folder(
            folder_name,
            parent_folder_id=os.environ["DRIVE_EPISODES_FOLDER_ID"],
        )
        logger.info(
            "round_1: created %r id=%s", folder_name, episode_folder["id"]
        )
    episode_folder_id = episode_folder["id"]
    episode_folder_url = (
        f"https://drive.google.com/drive/folders/{episode_folder_id}"
    )

    # ── work dir lives only for this job — cleaned up in finally ───────────────
    # On DISK, not /tmp: /tmp on the server is a 1.9 GB tmpfs (RAM), and holding
    # episode audio + transcripts there competes with WhisperX for the same
    # scarce memory. See workspace.py.
    work_dir = workspace.make_work_dir(f"rockstar-{episode_code}-")
    logger.info("round_1: work dir %s", work_dir)

    try:
        audio_path = work_dir / filename

        # ── 6. Download audio ──────────────────────────────────────────────────
        run_ledger.step("6", "download_audio")
        logger.info("round_1: downloading %r", filename)
        drive_client.download_file(file_id, audio_path)

        # ── 7. Move audio from inbox to episode folder ─────────────────────────
        run_ledger.step("7", "move_audio")
        if audio_already_in_folder:
            logger.info("round_1: audio already in episode folder — skipping move")
        else:
            logger.info("round_1: moving audio to episode folder")
            drive_client.move_file(file_id, episode_folder_id)

        # ── 8. Transcribe ──────────────────────────────────────────────────────
        # transcribe_fast.sh interface: <episode_folder> <episode_slug>
        # It searches for *.m4a / *.mp4 inside episode_folder and writes to
        # episode_folder/transcripts/.  work_dir IS the episode folder here —
        # we already downloaded the audio into it above.
        run_ledger.step("8", "transcribe")
        _run_script(
            ["bash", _SCRIPTS_DIR / "transcribe_fast.sh", work_dir, slug],
            work_dir,
            "transcribe_fast",
        )
        transcripts_dir = work_dir / "transcripts"
        transcript_fast_path = transcripts_dir / "transcript_fast.json"
        transcript_txt_path = transcripts_dir / "transcript_fast.txt"
        if not transcript_fast_path.exists():
            raise RuntimeError("transcribe_fast.sh did not produce transcripts/transcript_fast.json")
        if not transcript_txt_path.exists():
            raise RuntimeError("transcribe_fast.sh did not produce transcripts/transcript_fast.txt")
        transcript_text = transcript_txt_path.read_text(encoding="utf-8")
        logger.info("round_1: transcript %d chars", len(transcript_text))
        # R1-04 (E32): transcription hears the host as "Heather Riley, Heems Drum" and the models copied it
        # into the summary. Every model call gets the canonical name; the raw transcript file is unchanged.
        transcript_text = summary_norm.canonicalize_host(transcript_text)

        # BIO-01: the guest's own name, email and bio from the "Podcast Guest Info" form, matched by the
        # name the host says in the intro. A miss (or a form outage) is logged and recorded, never guessed.
        guest_rec, guest_rule = guest_form.lookup(
            json.loads(transcript_fast_path.read_text(encoding="utf-8")).get("segments", [])
        )

        # ── 9. Detect boundaries ───────────────────────────────────────────────
        # detect_boundaries.js CLI: --whisperx <json> --out <output_path>
        run_ledger.step("9", "detect_boundaries")
        boundaries_path = transcripts_dir / "boundaries.json"
        _run_script(
            ["node", _SCRIPTS_DIR / "detect_boundaries.js",
             "--whisperx", str(transcript_fast_path),
             "--out", str(boundaries_path)],
            work_dir,
            "detect_boundaries",
        )
        if not boundaries_path.exists():
            raise RuntimeError("detect_boundaries.js did not produce boundaries.json")

        # ── 10. Brand Team chain ───────────────────────────────────────────────
        run_ledger.step("10", "brand_team")
        episode_metadata = {
            "episode_number": ep_num,
            "episode_code": episode_code,
            "season": season,
            "season_episode": season_ep,
            "season_episode_code": season_ep_code,
            "original_filename": filename,
            "slug": slug,
            # R1-04: the host's real name, and today's date so "a 2025 time capsule" can't happen in 2026.
            "host": summary_norm.HOST_NAME,
            "processed_date": datetime.now(timezone.utc).date().isoformat(),
        }
        if guest_rec:
            episode_metadata["guest_name"] = guest_rec["name"]
            if guest_rec.get("bio"):
                # prompts/apple_summary.md §Bio sourcing: "If episode metadata includes a guest bio, use it
                # as the seed." The guest's own words; never logged here.
                episode_metadata["guest_bio"] = guest_rec["bio"]
        logger.info("round_1: running Brand Team chain")
        brand_report: dict = {}
        quotes = anthropic_client.run_brand_team_chain(
            transcript_text=transcript_text,
            episode_metadata=episode_metadata,
            report=brand_report,
        )
        for q in quotes:
            if q.get("speaker"):
                q["speaker"] = summary_norm.canonicalize_speaker(q["speaker"])
        quotes_path = work_dir / "quotes.json"
        quotes_path.write_text(
            # brand_team: which reviewers were retried or SKIPPED (R1-03); consumers read only "quotes".
            json.dumps({"quotes": quotes, "brand_team": brand_report}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("round_1: Brand Team → %d quotes", len(quotes))
        if brand_report.get("skipped"):
            logger.error("round_1: Brand Team step(s) SKIPPED after retry: %s", ", ".join(brand_report["skipped"]))
        # HEADING GATE (BLOG-02 F-1): each quote's `heading` is its blog h3. Checked
        # here, before the summary/title spend and before anything irreversible, so a
        # headless quote set fails Phase 1 loudly instead of Phase 2 silently.
        content_gates.assert_quote_headings(quotes, episode_code)

        # ── 11. Apple summary ──────────────────────────────────────────────────
        run_ledger.step("11", "apple_summary")
        logger.info("round_1: composing Apple summary")
        apple_summary_text = anthropic_client.compose_apple_summary(
            transcript_text=transcript_text,
            episode_metadata=episode_metadata,
            quotes=quotes,
        )
        # R1-02 (E32): drop leaked model notes above the title, give each part its own paragraph, fix the
        # host's name, and prove it with the blog builder's own parser NOW (GateFailure → PermanentFailure,
        # before Drive, calendar, counter or schedule are touched).
        apple_summary_text, norm_report = summary_norm.normalize_apple_summary(apple_summary_text, episode_code)
        logger.info("round_1: apple_summary normalized %s", norm_report)
        apple_summary_path = work_dir / "apple_summary.md"
        apple_summary_path.write_text(apple_summary_text, encoding="utf-8")
        logger.info("round_1: apple_summary %d chars", len(apple_summary_text))

        # ── 11.5 Episode title — DEDICATED generation (TITLE-01) ───────────────
        # We GENERATE the title as structured data instead of scraping line 0 of
        # the Apple summary's H1. E29 (2026-06-23) showed why the scrape is
        # brittle: the model emitted a "*Pre-write note:*" line above the H1, so
        # split("\n")[0] grabbed the note and the title silently fell back to
        # "TBD". compose_title returns {hook, topic, title} from a JSON field —
        # no "line 0" to capture the wrong thing, preamble cannot corrupt it, and
        # it RAISES rather than defaulting on a bad/empty response.
        run_ledger.step("11.5", "title")
        logger.info("round_1: generating episode title")
        title_result = anthropic_client.compose_title(
            transcript_text=transcript_text,
            episode_metadata=episode_metadata,
            quotes=quotes,
        )
        episode_title = title_result["title"]
        logger.info("round_1: episode title %r", episode_title)

        # ── 11.6 TITLE GATE — fail loudly BEFORE anything irreversible ─────────
        # The same shared gate publish_finalize uses (content_gates), called here
        # in Phase 1 so a missing/placeholder title fails the job NOW — before the
        # calendar event (step 13), the counter bump (step 14), and the schedule
        # write (step 15). On failure this raises content_gates.GateFailure and
        # the function returns nothing: counter stays put, no "drafting" entry is
        # written, no half-complete episode advances (PQ-5, criterion C). A
        # corrected re-run (reprocess) re-enters here and is gated identically.
        # compose_title already raises on a bad model response; this is the
        # belt-and-suspenders floor that also catches a placeholder slipping
        # through any future title source.
        run_ledger.step("11.6", "title_gate")
        content_gates.assert_real_title(episode_title, episode_code)

        # ── 12. Upload artifacts to Drive ──────────────────────────────────────
        run_ledger.step("12", "upload")
        logger.info("round_1: uploading artifacts")
        _archive_existing_artifacts(
            episode_folder_id,
            [p.name for p in (transcript_fast_path, transcript_txt_path, boundaries_path,
                              quotes_path, apple_summary_path) if p.exists()],
        )
        for artifact in [
            transcript_fast_path,
            transcript_txt_path,
            boundaries_path,
            quotes_path,
            apple_summary_path,
        ]:
            if artifact.exists():
                drive_client.upload_file(artifact, episode_folder_id)
                logger.info("round_1: uploaded %s", artifact.name)

        # ── 13. Editorial calendar event ───────────────────────────────────────
        run_ledger.step("13", "calendar")
        existing_cal_id = (
            existing_sched_entry.get("calendar_event_id")
            if existing_sched_entry
            else None
        )
        if not calendar_event_enabled():
            # CAL-OFF (John, 2026-10-01: "not helpful yet"). No Calendar call; an id already on the schedule
            # entry (reprocess) is kept as-is, never deleted or overwritten.
            calendar_event_id = existing_cal_id or ""
            logger.info("round_1: editorial calendar event disabled (EDITORIAL_CALENDAR_EVENT=off)")
        elif reprocess and existing_cal_id:
            calendar_event_id = existing_cal_id
            logger.info("round_1: reprocess — reusing calendar event %s", calendar_event_id)
        else:
            logger.info("round_1: creating editorial calendar event")
            cal_event = calendar_client.create_editorial_event(
                episode_code=episode_code,
                episode_title=episode_title,
                episode_folder_url=episode_folder_url,
            )
            calendar_event_id = cal_event["id"]
            logger.info("round_1: calendar event %s", calendar_event_id)

        # ── 14. Update episode counter ─────────────────────────────────────────
        run_ledger.step("14", "episode_counter_update")
        if not reprocess:
            counter["next_episode_number"] = ep_num + 1
            counter["next_season_episode"] = season_ep + 1
            counter["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write_json(_EPISODE_COUNTER_PATH, counter)
            logger.info("round_1: counter → E%d", ep_num + 1)
        else:
            logger.info(
                "round_1: reprocess — counter unchanged at E%d",
                counter["next_episode_number"],
            )

        # ── 15. Update publication schedule ───────────────────────────────────
        run_ledger.step("15", "schedule")
        if reprocess and existing_sched_entry:
            existing_sched_entry["episode_title"] = episode_title
            existing_sched_entry["drive_folder_id"] = episode_folder_id
            existing_sched_entry["calendar_event_id"] = calendar_event_id
            existing_sched_entry["status"] = "reprocessed"
            if guest_rec:
                existing_sched_entry["guest_name"] = guest_rec["name"]
                existing_sched_entry["guest_email"] = guest_rec.get("email", "")
            existing_sched_entry["notes"] = (
                existing_sched_entry.get("notes", "")
                + f" | Round 1 reprocessed {datetime.now(timezone.utc).date().isoformat()}"
                + f" | guest form: {guest_rule}"
            )
        else:
            pub_schedule.setdefault("episodes", []).append({
                "episode_number": ep_num,
                "season": season,
                "season_episode": season_ep,
                "episode_code": episode_code,
                "season_episode_code": season_ep_code,
                "slug": slug,
                "folder_name": folder_name,
                "drive_folder_id": episode_folder_id,
                "drive_audio_file_id": file_id,
                "original_filename": filename,
                "episode_title": episode_title,
                "guest_name": guest_rec["name"] if guest_rec else None,
                "guest_email": guest_rec.get("email", "") if guest_rec else "",
                "status": "drafting",
                "calendar_event_id": calendar_event_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "submitted_to_publisher_date": None,
                "scheduled_publish_date": None,
                "actual_live_date": None,
                "apple_url": None,
                "spotify_url": None,
                "mission_matters_email_id": None,
                "pipeline_done": False,
                "notes": (
                    "Round 1 complete. "
                    + ("Guest from form." if guest_rec else "Guest name TBD — fill in before Round 2.")
                    + f" | guest form: {guest_rule}"
                ),
            })
        pub_schedule["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(_PUB_SCHEDULE_PATH, pub_schedule)
        logger.info("round_1: schedule updated for %s", episode_code)

        logger.info(
            "round_1 complete — %s (%s) title=%r quotes=%d calendar=%s",
            episode_code, season_ep_code, episode_title, len(quotes), calendar_event_id,
        )

        # ── 16. Auto-chain: if next episode is already in inbox, enqueue it ───
        run_ledger.step("16", "auto_chain")
        if reprocess:
            # R1-07 (2026-10-01, E6 + E13 dropped together): a Drive drain enqueues ONE Round 1 however many
            # files land, and this step used to skip reprocess mode, so the second old episode sat in the inbox
            # until some unrelated Drive change woke the pipeline. Chain to any other old-episode audio left.
            remaining = [
                f for f in drive_client.list_audio_in_inbox()
                if episode_files.is_processable(f["name"])
                and f["id"] != file_id
                and (episode_files.episode_number(f["name"]) or 0) < counter["next_episode_number"]
            ]
            if remaining:
                next_job_id = db.enqueue("ROUND_1", "TBD", {
                    "trigger": "inbox_chain",
                    "source_episode": episode_code,
                })
                logger.info(
                    "round_1: %s complete — %d more old-episode file(s) in inbox, chaining (job %d, next %r)",
                    episode_code, len(remaining), next_job_id, remaining[0]["name"],
                )
        else:
            # Counter was just incremented to ep_num+1. If E{N+1}-*.m4a is waiting
            # in inbox, kick off its Round 1 now rather than waiting for a webhook.
            next_ep_num = ep_num + 1
            next_prefix = f"E{next_ep_num}"
            next_inbox = drive_client.list_audio_in_inbox()
            next_pub_schedule = _read_json(_PUB_SCHEDULE_PATH)  # re-read after our writes
            next_matched = [
                f for f in next_inbox
                if episode_files.is_processable(f["name"])
                and f["name"].upper().startswith(next_prefix.upper())
                and not _already_processed(next_pub_schedule, f["id"])
            ]
            if next_matched:
                next_job_id = db.enqueue("ROUND_1", "TBD", {
                    "trigger": "inbox_chain",
                    "source_episode": episode_code,
                })
                logger.info(
                    "round_1: %s complete — chaining to %s (job %d, file %r)",
                    episode_code, next_prefix, next_job_id, next_matched[0]["name"],
                )

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info("round_1: cleaned up %s", work_dir)
