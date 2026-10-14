"""
episode_files.py — R1-01: ONE definition of "an episode audio file", shared by every gate.

WHY: E30 (2026-07-06) sat in the inbox for two hours with a green job. Enqueue (drive_changes, drive_client)
accepted any audio extension; Round 1 accepted only names matching ^E\\d+[-_]; "E30.m4a" passed the first and
failed the second, and Round 1 returned cleanly ("nothing to do"). Three modules held three different extension
lists, and transcription only reads .m4a / .mp4 anyway.

THE RULES (docs/stories/done/R1-01-inbox-matcher-drift-silent-noop.md, option (a): lenient intake, loud on
anything else):
  - AUDIO_EXTENSIONS: what counts as audio for the inbox (enqueue fires on any of these, so even a wrongly named
    or wrongly formatted file wakes Round 1 up, and Round 1 then says loudly what is wrong with it).
  - PROCESSABLE_EXTENSIONS: what transcription can read (scripts/transcribe_fast.sh globs *.m4a and *.mp4).
  - episode_number(name): "E30-guest.m4a", "E30_guest.m4a", "E30 guest.m4a", "E30.m4a" → 30. The number must be
    followed by a separator, a space, the extension dot, or nothing. "E300x.m4a" and "Episode 30.m4a" → None.
  - is_processable(name): an episode number AND a processable extension.
  - problem(name): for audio that is NOT processable, the one-line reason and the exact fix, for the alert.
"""

from __future__ import annotations

import re
from pathlib import Path

from rockstar.gates import host_identity

AUDIO_EXTENSIONS = frozenset({".m4a", ".mp3", ".wav", ".aac", ".mp4", ".ogg", ".flac"})
PROCESSABLE_EXTENSIONS = frozenset({".m4a", ".mp4"})

_EP_RE = re.compile(r"^E(\d+)(?=[-_ .]|$)", re.IGNORECASE)


def is_audio(name: str, mime: str = "") -> bool:
    return Path(name or "").suffix.lower() in AUDIO_EXTENSIONS or "audio" in (mime or "")


def episode_number(name: str) -> int | None:
    m = _EP_RE.match((name or "").strip())
    return int(m.group(1)) if m else None


def is_processable(name: str) -> bool:
    return episode_number(name) is not None and Path(name).suffix.lower() in PROCESSABLE_EXTENSIONS


def problem(name: str, next_episode: int | None = None) -> str:
    """Why an inbox audio file won't be processed, and the fix. '' if it will be."""
    if is_processable(name):
        return ""
    suffix = Path(name).suffix.lower()
    n = episode_number(name)
    if n is None:
        target = f"E{next_episode}" if next_episode else "E<number>"
        return f"'{name}' has no episode number: rename it to '{target}-<guest>{suffix or '.m4a'}'"
    return f"'{name}' is {suffix or 'not'} audio transcription can't read: export it as '.m4a' (same name)"


# TRIG-02: the two images that start Phase 2. "tile" or "reel" anywhere after the E-number, image formats only:
# E26-Delphine-tile.jpg, E26-tile-web.jpg, E27-firstname-reel.png. Shared by drive_changes (trigger) and
# publish_finalize (step 3), so the trigger can never fire on a file Phase 2 then fails to find.
VISUAL_RE = {
    "tile": re.compile(r"^E\d+.*\btile\b.*\.(jpg|jpeg|png|webp)$", re.IGNORECASE),
    "reel": re.compile(r"^E\d+.*\breel\b.*\.(jpg|jpeg|png|webp)$", re.IGNORECASE),
}


def folder_slug(folder_name: str) -> str:
    """The schedule slug an episode folder stands for: "E6-jane" → "jane", "E6" → "e6". One definition, shared by
    Round 1 (which writes it) and the visuals trigger (which matches on it)."""
    return folder_name.split("-", 1)[1].lower() if "-" in folder_name else folder_name.lower()


def is_episode_folder(folder_name: str, episode_code: str) -> bool:
    """True for "E6" and "E6-anything"; False for "E61-x" or "E16-x" (Drive's `contains` matches those too)."""
    code = episode_code.rstrip("-").lower()
    name = folder_name.lower()
    return name == code or name.startswith(code + "-")


def find_visual(files: list[dict], kind: str) -> dict | None:
    """The first root file that is this episode's tile or reel image (folders never match)."""
    pattern = VISUAL_RE[kind]
    for f in files:
        if "folder" not in (f.get("mimeType") or "") and pattern.match(f.get("name") or ""):
            return f
    return None


# REEL-08 (John, 2026-10-01): one reel background per speaker, named by first name: E13-heather-reel.jpg,
# E13-john-reel.jpg, E6-avery-reel.jpg ("yes thats what we do"). A single reel image still works as before.
_HONORIFICS = {"dr", "doctor", "mr", "mrs", "ms", "prof", "rev", "md", "phd", "nd"}


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


def first_name(speaker: str) -> str:
    words = [w for w in _tokens(speaker) if w not in _HONORIFICS]
    return words[0] if words else ""


def reel_images(files: list[dict]) -> list[dict]:
    return [
        f for f in files if "folder" not in (f.get("mimeType") or "") and VISUAL_RE["reel"].match(f.get("name") or "")
    ]


def background_for(speaker: str, reels: list[dict], all_speakers: list[str]) -> tuple[dict | None, str]:
    """The reel image for this speaker: (file, rule), (None, "host-default"), or (None, problem).

    1. an image whose filename names the speaker's first name ("E13-john-reel.jpg" for "John …");
    2. the host (recognised by her full name, host_identity -- REEL-09) with no image of her own
       → None, "host-default" (render_reel uses BrandAssets' host image);
    3. exactly one reel image that names NO other speaker → that image (the one-image episodes, E32/E6 today);
    4. otherwise a problem string: the operator is told which file to drop. Never a guess.
    """
    first = first_name(speaker)
    named = [f for f in reels if first and first in _tokens(f["name"])]
    if len(named) == 1:
        return named[0], "named"
    if len(named) > 1:
        return None, f"{len(named)} reel images name '{first}': keep one"
    # REEL-09: the host by her full name, never by first name -- E27's guest is also a
    # Heather, and the first-name rule gave her the host's reel background.
    if host_identity.is_host(speaker):
        return None, "host-default"
    others = {first_name(s) for s in all_speakers} - {first, ""}
    unclaimed = [f for f in reels if not (set(_tokens(f["name"])) & others)]
    if len(reels) == 1 and unclaimed:
        return reels[0], "single"
    return (
        None,
        f"no reel image for {speaker or 'an unnamed speaker'}: drop one named like 'E<N>-{first or 'name'}-reel.jpg'",
    )
