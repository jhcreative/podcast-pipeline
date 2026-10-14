"""
CARD-04 backfill, the Drive half: find every episode whose carousels are already in
Drive, and stage what the Mac needs to render their covers. Runs where the Google
credentials are (the box); never writes to Drive -- card_upload.py --covers-only does
the add-only push afterwards.

    python -m rockstar.cards.cover_backfill list
    python -m rockstar.cards.cover_backfill fetch --episode 30 --dest /tmp/card04-stage

`fetch` writes <dest>/E<N>-<slug>/ with quotes.json, apple_summary.md (exported if it
was hand-edited into a Doc, ADR-0008), the episode's tile and reel images, and
drive_cards.txt (the names already in cards/, so nothing that exists is re-uploaded).

Drive is the truth for which episodes have carousels (John, 2026-10-08: "whatever
Drive has"); the Mac's Episodes/ folder is a stale partial copy.
"""

import argparse
import os
import re
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))

FOLDER = "application/vnd.google-apps.folder"
# Not \b: "_" is a word character, so \btile\b misses E30_Graham_tile.jpg.
_IMAGE = re.compile(r"^E\d+.*(?<![a-z])(tile|reel)(?![a-z]).*\.(jpg|jpeg|png|webp)$", re.I)
COVER_SUFFIX = "_0cover.png"


def _cards(drive_client, folder_id):
    return next(
        (f for f in drive_client.list_folder(folder_id) if f["name"] == "cards" and f["mimeType"] == FOLDER), None
    )


def episodes_with_cards(drive_client):
    """[(ep_num, folder, cards_files)] for every Episodes/ folder holding a cards/."""
    root = os.environ["DRIVE_EPISODES_FOLDER_ID"]
    out = []
    for f in drive_client.list_folder(root):
        m = re.match(r"^E(\d+)-", f["name"])
        if f["mimeType"] != FOLDER or not m:
            continue
        cards = _cards(drive_client, f["id"])
        if cards:
            out.append((int(m.group(1)), f, drive_client.list_folder(cards["id"])))
    return sorted(out, key=lambda t: t[0])


def cmd_list(drive_client):
    rows = episodes_with_cards(drive_client)
    for ep, folder, files in rows:
        names = [x["name"] for x in files]
        slides = sum(1 for n in names if n.endswith(".png") and not n.endswith(COVER_SUFFIX))
        covers = sum(1 for n in names if n.endswith(COVER_SUFFIX))
        print(f"E{ep}\t{folder['name']}\t{slides} slides\t{covers} covers")
    print(f"{len(rows)} episode(s) with cards/ in Drive")


def cmd_fetch(drive_client, ep_num, dest, new=False, captions=False):
    """Stage an episode. Default (backfill): it must already have cards/. new=True
    (new-cards.sh): it must NOT have any cards yet -- a full set is about to be made,
    and CARD-02's upload would trash what is there."""
    folder = drive_client.find_episode_folder(f"E{ep_num}-")
    if not folder or not re.match(rf"^E{ep_num}-", folder["name"]):  # "E3-" ⊂ "E30-"
        raise SystemExit(f"no Drive folder for E{ep_num}")
    cards = _cards(drive_client, folder["id"])
    present = drive_client.list_folder(cards["id"]) if cards else []
    if new and present:
        raise SystemExit(
            f"{folder['name']}/cards already holds {len(present)} file(s) -- "
            f"not a new episode; use backfill-covers.sh for its covers"
        )
    if not new and not cards:
        raise SystemExit(f"{folder['name']} has no cards/ -- nothing to cover")
    out = Path(dest) / folder["name"]
    out.mkdir(parents=True, exist_ok=True)
    got = []
    for f in drive_client.list_folder(folder["id"]):
        name = f["name"]
        stem = os.path.splitext(name)[0]
        if name == "quotes.json" or stem == "apple_summary" or _IMAGE.match(name):
            target = out / ("apple_summary.md" if stem == "apple_summary" else name)
            drive_client.download_file(f["id"], target)
            got.append(target.name)
    if captions:
        # CARD-05 re-render: the words stay exactly as they are in Drive (they may be
        # hand-edited); only the type changes. Every non-PNG file in cards/ (captions, and
        # anything hand-placed) comes back unchanged, so the replace trashes only images.
        (out / "cards").mkdir(exist_ok=True)
        for f in present:
            if not f["name"].lower().endswith(".png") and f["name"] != "_upload_incomplete.txt":
                drive_client.download_file(f["id"], out / "cards" / f["name"])
                got.append(f"cards/{f['name']}")
    (out / "drive_cards.txt").write_text("".join(f"{x['name']}\n" for x in sorted(present, key=lambda x: x["name"])))
    if "quotes.json" not in got:
        raise SystemExit(f"{folder['name']}: no quotes.json in Drive -- the covers take their speakers and ids from it")
    print(f"  {folder['name']}: {', '.join(sorted(got))}")
    return out


def episode_title(summary_text):
    """The published title for the footer, from apple_summary.md (ADR-0009: it wins).

    Four shapes are live: E29+ open on the bare title line; the same summaries
    hand-edited in Drive export as Docs with it as a heading ('## Feed Your Head | …',
    E30/E31, ADR-0008); E25 opens on '# E25 — Title'; backfilled E23/E24 carry
    '## Title (LOCKED …)' then '**Title**'. A heading that names a section ("Apple/
    Spotify Show Notes — E24", "About …") is not a title. None when nothing matches --
    the caller asks a person, it never guesses.
    """
    m = re.search(r"^#+\s*Title\b[^\n]*\n+\s*\*\*(.+?)\*\*", summary_text, re.M)
    if m:
        return m.group(1).strip()
    first = next((ln.strip() for ln in summary_text.lstrip("\ufeff").splitlines() if ln.strip()), "")
    m = re.match(r"^#+\s*E\d+\s*[—–-]\s*(.+)$", first)
    if m:
        return m.group(1).strip()
    m = re.match(r"^#+\s*(.+?)\s*#*$", first)
    if m and not re.match(r"(apple|spotify|show notes|about|title\b|e\d+\b)", m.group(1), re.I):
        first = m.group(1).replace("\\", "").strip("*").strip()
    elif m:
        return None
    if first and len(first) <= 120:
        return _without_show_name(first)
    return None


def _without_show_name(title):
    """QUOTE-CARD-FORMAT §6: the footer never repeats the show name -- the lockup says
    it. Season-era titles lead with it ("Gen X Hits Menopause · S2E3 · No Stone
    Unturned | …"); the season code goes too, since the line already opens on E{N}."""
    t = re.sub(r"^gen\s*x\s+hits\s+menopause\s*[·|:—–-]\s*", "", title, flags=re.I)
    t = re.sub(r"^s\d+\s*e\d+\s*[·|:—–-]\s*", "", t, flags=re.I)
    return t.strip() or title


def cmd_title(ep_dir):
    p = Path(ep_dir) / "apple_summary.md"
    t = episode_title(p.read_text()) if p.exists() else None
    if not t:
        raise SystemExit(f"no published title found in {p} -- pass it by hand")
    print(t)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    f = sub.add_parser("fetch")
    f.add_argument("--episode", type=int, required=True)
    f.add_argument("--dest", required=True)
    f.add_argument(
        "--captions",
        action="store_true",
        help="a re-render: also fetch cards/*_caption.txt, to re-upload unchanged",
    )
    f.add_argument("--new", action="store_true", help="a new episode: refuse an episode whose cards/ already has files")
    t = sub.add_parser("title", help="print the published title (no Drive access)")
    t.add_argument("--dir", required=True)
    a = ap.parse_args()
    if a.cmd == "title":
        return cmd_title(a.dir)
    from rockstar.clients import drive_client  # deferred: needs Google creds

    if a.cmd == "list":
        cmd_list(drive_client)
    else:
        cmd_fetch(drive_client, a.episode, a.dest, new=a.new, captions=a.captions)


if __name__ == "__main__":
    main()
