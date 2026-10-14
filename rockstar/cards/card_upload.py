"""
Put a rendered card set in the episode's Drive `cards/` folder (CARD-02).

Separate from render_cards on purpose: rendering needs Playwright and Chromium,
uploading needs Google credentials. Until CARD-05 they lived in different places (the
cards were made on the Mac and pushed from the box), and a module that required both
could run in neither. Kept apart so the upload stays importable without Playwright.

Usage (on a host with Google credentials):
    python -m rockstar.cards.card_upload --episode 31 --dir /path/to/E31-leyla/cards
    python -m rockstar.cards.card_upload --episode 31 --dir /path/to/covers --covers-only   # CARD-04
"""

import argparse
import os
import re
from pathlib import Path

# Present in cards/ only while an upload is in flight. A cards/ holding it is a partial
# upload (a 5xx, a timeout) -- not a finished set -- so --new-only may clear and redo it.
INCOMPLETE = "_upload_incomplete.txt"


def is_partial(names):
    return INCOMPLETE in names


def upload_cards(ep_num, local_dir, new_only=False):
    """Clear the episode's Drive cards/ folder, then upload everything local.

    The folder is CLEARED first (John, 2026-07-25): `drive_client.upload_file`
    creates rather than replaces, so re-running an episode would otherwise leave two
    copies of every slide side by side — the second run's fixes next to the first
    run's defects, with nothing to tell them apart. Clearing is a trash, not a
    delete, so a wrong run is recoverable from the Drive UI for 30 days.

    The WHOLE local folder goes up, not one run's output: captions land here too
    (make_captions.py), and if each tool cleared the folder before pushing only its
    own files, the second to run would trash the first's work.

    Local files are never touched. If this raises, everything still exists on disk —
    but note the clear necessarily precedes the uploads, so a failure partway leaves
    Drive cleared and not yet repopulated. Re-running restores it.
    """
    from rockstar.clients import drive_client  # deferred: needs Google creds

    folder = drive_client.find_episode_folder(f"E{ep_num}-")
    if not folder:
        raise SystemExit(f"no Drive folder matching 'E{ep_num}-' -- nothing uploaded; local files are untouched")
    print(f"  Drive folder: {folder['name']}")

    cards = next(
        (
            f
            for f in drive_client.list_folder(folder["id"])
            if f["name"] == "cards" and f["mimeType"] == "application/vnd.google-apps.folder"
        ),
        None,
    )
    if cards and new_only:
        names = [f["name"] for f in drive_client.list_folder(cards["id"])]
        if names and not is_partial(names):
            raise SystemExit(f"{folder['name']}/cards is not empty -- --new-only refuses to trash it; nothing uploaded")
        if names:
            print(f"  cards/ holds a partial upload ({INCOMPLETE}) -- clearing and redoing it")
    if cards:
        stale = drive_client.list_folder(cards["id"])
        for f in stale:
            print(f"  trashing stale {f['name']}")
            drive_client.trash_file(f["id"])
        print(f"  cleared {len(stale)} file(s) from cards/")
    else:
        cards = drive_client.create_folder("cards", folder["id"])
        print("  created cards/")

    # Files starting with "_" are local scratch (contact sheets) and stay on the Mac.
    local = sorted(f for f in os.listdir(local_dir) if not f.startswith("_") and not f.startswith("."))
    marker = Path(local_dir) / INCOMPLETE
    marker.write_text(
        "An upload into this folder is in progress or was interrupted. "
        "If it stays, the set is incomplete: re-run the push.\n"
    )
    try:
        mark = drive_client.upload_file(marker, cards["id"], mime_type="text/plain")
        for name in local:
            mime = "image/png" if name.endswith(".png") else "text/plain"
            drive_client.upload_file(Path(local_dir) / name, cards["id"], mime_type=mime)
        drive_client.trash_file(mark["id"])  # finished: the set is whole
    finally:
        marker.unlink(missing_ok=True)
    print(
        f"  uploaded {len(local)} file(s): "
        f"{sum(1 for f in local if f.endswith('.png'))} slide(s), "
        f"{sum(1 for f in local if f.endswith('.txt'))} caption(s)"
    )
    return local


COVER_SUFFIX = "_0cover.png"


def add_covers(ep_num, local_dir):
    """CARD-04 backfill: put cover slides into an EXISTING cards/ folder, add-only.

    The opposite contract to upload_cards, on purpose (John, 2026-10-08: "no need to
    redo the originals just drop the cover image in with them"). The slides and
    captions already in Drive may have been posted or hand-fixed, so this never
    trashes, never replaces, and never creates cards/: an episode without one has no
    carousels to cover. A cover whose name is already in Drive is skipped, so a
    re-run uploads nothing twice.

    Returns (uploaded, not uploaded) names: already there, or no carousel to join.
    """
    from rockstar.clients import drive_client  # deferred: needs Google creds

    folder = drive_client.find_episode_folder(f"E{ep_num}-")
    # find_episode_folder is a Drive `name contains` query: "E3-" can return E30's
    # folder. This push writes, so the match must be exact.
    if not folder or not re.match(rf"^E{ep_num}-", folder["name"]):
        raise SystemExit(f"no Drive folder named 'E{ep_num}-…' (got {folder and folder['name']!r}) -- nothing uploaded")
    cards = next(
        (
            f
            for f in drive_client.list_folder(folder["id"])
            if f["name"] == "cards" and f["mimeType"] == "application/vnd.google-apps.folder"
        ),
        None,
    )
    if not cards:
        raise SystemExit(f"{folder['name']} has no cards/ folder -- no carousels to cover; nothing uploaded")
    present = {f["name"] for f in drive_client.list_folder(cards["id"])}
    covers = sorted(f for f in os.listdir(local_dir) if f.endswith(COVER_SUFFIX))
    if not covers:
        raise SystemExit(f"no *{COVER_SUFFIX} in {local_dir} -- render them first")
    uploaded, skipped, lone = [], [], []
    for name in covers:
        if name in present:
            skipped.append(name)
            continue
        # A cover only joins a carousel that is there: E29_q01_0cover.png needs an
        # E29_q01_<i>of<n>.png beside it, or it would post as a lone cover.
        stem = name[: -len(COVER_SUFFIX)] + "_"
        if not any(p.startswith(stem) and p.endswith(".png") and p != name for p in present):
            print(f"  ! {name}: no {stem}*of*.png slides in Drive -- not uploaded")
            lone.append(name)
            continue
        drive_client.upload_file(Path(local_dir) / name, cards["id"], mime_type="image/png")
        uploaded.append(name)
    print(
        f"  {folder['name']}/cards: added {len(uploaded)} cover(s), "
        f"{len(skipped)} already there" + (f", {len(lone)} with no carousel" if lone else "") + "; nothing else touched"
    )
    return uploaded, skipped + lone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--dir", required=True, help="local cards/ folder to push")
    ap.add_argument(
        "--new-only",
        action="store_true",
        help="Phase 2's card step: upload a full set only into an empty or absent cards/",
    )
    ap.add_argument(
        "--covers-only",
        action="store_true",
        help="CARD-04: add *_0cover.png to the existing cards/ folder; trashes and replaces nothing",
    )
    a = ap.parse_args()
    if not os.path.isdir(a.dir):
        raise SystemExit(f"not a directory: {a.dir}")
    if a.covers_only:
        add_covers(a.episode, a.dir)
    else:
        upload_cards(a.episode, a.dir, new_only=a.new_only)


if __name__ == "__main__":
    main()
