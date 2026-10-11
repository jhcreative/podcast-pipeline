"""
CARD-05: Phase 2 builds the episode's quote cards on the box (ADR-0015).

Runs after Step 12 (reels uploaded). It stages the episode's quotes, show notes and
speaker images, drafts the cover titles and captions (model, gated), renders the
slides and covers in vendored IBM Plex, and uploads the set to the episode's Drive
`cards/`. The same tools John runs on the Mac (new-cards.sh), run here in subprocesses
so a card failure can never take the Phase 2 job down with it.

Rules (John, 2026-10-08):
  - alert and continue: any failure sends ONE alert naming the stage and the cause,
    and Phase 2 carries on (reels, blog, thank-you and calendar matter more);
  - never overwrite: an episode whose Drive cards/ already holds files is skipped
    (a hand-built set, or a re-run) -- upload uses card_upload --new-only;
  - the human gate is Drive: titles and captions land unreviewed, Heather posts by hand,
    and the standup says the set is ready to review;
  - the reels are not touched.

Returns a one-line status for the run log; raises nothing.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "quote_cards"
FOLDER = "application/vnd.google-apps.folder"


def _playwright_platform_override(os_release: str = "/etc/os-release") -> str | None:
    """Playwright 1.60 refuses Ubuntu releases newer than 24.04 at install AND at launch
    ("does not support chromium on ubuntu26.04-x64"; the box is 26.04). box-card-setup.sh
    installed the 24.04 build; every launch must name the same platform to find it."""
    try:
        info = dict(line.strip().split("=", 1) for line in open(os_release)
                    if "=" in line)
    except OSError:
        return None
    if info.get("ID", "").strip('"') != "ubuntu":
        return None
    major = info.get("VERSION_ID", "").strip('"').split(".")[0]
    return "ubuntu24.04-x64" if major.isdigit() and int(major) > 24 else None


class _StageFailed(Exception):
    def __init__(self, stage: str, detail: str):
        super().__init__(f"{stage}: {detail}")
        self.stage, self.detail = stage, detail


# Per stage, so a hung model call or Chromium can't hold the rest of Phase 2 for long:
# at most ~20 minutes in all, then one alert and Phase 2 carries on.
TIMEOUTS = {"cover titles": 180, "captions": 180, "render": 600, "upload": 300}


def _run(stage: str, args: list, cwd: Path) -> str:
    cmd = [sys.executable, *[str(a) for a in args]]
    logger.info("card_step %s: %s", stage, " ".join(cmd))
    # The scripts import pipeline modules (drive_client, anthropic_client) that live at the
    # repo root, not beside them; the worker's own path doesn't reach a subprocess.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (env.get("PYTHONPATH"), str(ROOT)) if p)
    override = _playwright_platform_override()
    if override:
        env.setdefault("PLAYWRIGHT_HOST_PLATFORM_OVERRIDE", override)
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=TIMEOUTS.get(stage, 300), env=env)
    except subprocess.TimeoutExpired:
        raise _StageFailed(stage, f"timed out after {TIMEOUTS.get(stage, 300)}s")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-6:])
        raise _StageFailed(stage, tail or f"exit {r.returncode}")
    logger.info("card_step %s ok: %.400s", stage, out.strip().splitlines()[-1:] or "")
    return out


def _existing_cards(drive_client, episode_folder_id: str) -> int:
    """Files in a FINISHED cards/ set. A partial upload (the in-flight marker is still
    there) counts as none, so a retry redoes it instead of skipping forever."""
    for f in drive_client.list_folder(episode_folder_id):
        if f["name"] == "cards" and f.get("mimeType") == FOLDER:
            names = [x["name"] for x in drive_client.list_folder(f["id"])]
            return 0 if "_upload_incomplete.txt" in names else len(names)
    return 0


def build_cards(*, work_dir: Path, episode_code: str, episode_number: int, slug: str,
                episode_title: str, quotes_path: Path, apple_summary_path: Path,
                images: list[Path], episode_folder_id: str, drive_client,
                alert, standup) -> str:
    """Build and upload the card set. `alert(subject, body)` and `standup(code, msg)`
    are publish_finalize's own helpers. Never raises."""
    try:
        n = _existing_cards(drive_client, episode_folder_id)
        if n:
            msg = f"cards/ already holds {n} file(s) -- left as is (never overwritten)"
            logger.info("card_step %s: %s", episode_code, msg)
            # A reprocess can change the quotes; say so rather than skip in silence.
            standup(episode_code,
                    f"Quote cards not rebuilt: {msg}. If this run changed the quotes, "
                    f"re-render them: scripts/rerender-cards.sh {episode_number}, then --push.")
            return f"skipped: {msg}"

        ep_dir = work_dir / "cards_build" / f"{episode_code}-{slug}"
        if ep_dir.exists():
            shutil.rmtree(ep_dir)
        (ep_dir / "cards").mkdir(parents=True)
        shutil.copy(quotes_path, ep_dir / "quotes.json")
        if apple_summary_path and apple_summary_path.exists():
            shutil.copy(apple_summary_path, ep_dir / "apple_summary.md")
        for img in images:
            if img and img.exists():
                shutil.copy(img, ep_dir / img.name)

        t = ["--episode", episode_number, "--dir", ep_dir, "--title", episode_title]
        _run("cover titles", [CARDS / "make_cover_titles.py", *t], CARDS)
        _run("captions", [CARDS / "make_captions.py", *t], CARDS)
        out = _run("render", [CARDS / "render_cards.py", *t], CARDS)
        _run("upload", [CARDS / "card_upload.py", "--episode", episode_number,
                        "--dir", ep_dir / "cards", "--new-only"], CARDS)

        made = sorted(p.name for p in (ep_dir / "cards").iterdir())
        covers = sum(1 for m in made if m.endswith("_0cover.png"))
        slides = sum(1 for m in made if m.endswith(".png")) - covers
        caps = sum(1 for m in made if m.endswith("_caption.txt"))
        summary = f"{slides} slides, {covers} covers, {caps} captions"
        standup(episode_code,
                f"Quote cards ready to review in Drive `cards/` ({summary}). Titles and "
                f"captions are model-drafted and unreviewed; fill [ADD @HANDLE] before posting.")
        logger.info("card_step %s: uploaded %s", episode_code, summary)
        return f"built: {summary}"
    except _StageFailed as e:
        cause = f"the {e.stage} stage failed"
        detail = e.detail
    except Exception as e:  # noqa: BLE001 -- alert and continue is the contract
        cause, detail = "an unexpected error", f"{type(e).__name__}: {e}"
    logger.error("card_step %s: %s -- %s", episode_code, cause, detail)
    alert(f"{episode_code}: quote cards not built ({cause})",
          f"Phase 2 carried on; reels, blog and thank-you are unaffected.\n\n"
          f"Cause: {cause}.\n\n{detail}\n\n"
          f"If cards/ in Drive holds _upload_incomplete.txt, the upload was cut short; the "
          f"push below clears and redoes it.\n\n"
          f"To build them by hand on the Mac:\n"
          f"  cd ~/RockstarPodcast && scripts/new-cards.sh {episode_number}\n"
          f"  then review, then: scripts/new-cards.sh --push {episode_number}")
    return f"failed: {cause}"
