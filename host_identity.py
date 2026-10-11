"""
host_identity.py — REEL-09: the ONE rule for "is this speaker the host?", and her name.

The host is **Heather Reilly Hiemstra**: Heather, her legal middle name Reilly, her surname
Hiemstra -- no hyphen (John, 2026-10-08). Transcription and the Brand Team mishear it
('Heather Riley', 'Reilly-Hemstra', 'Riley-Hemstra', 'Heemstra', 'Riley-Hemesdra',
'Eilaheemstra') and sometimes label her a bare 'Heather'. Every one of those is the host.

Another woman named Heather is not. E27's guest is Lorna Vashti-Mercer; the old
reel rule (first name == "heather") gave her the host's photo, and four modules each
kept their own copy of the idea. They all import this now: episode_files (reel
backgrounds), content_gates (guest-featured gate), summary_norm (canonicalizing),
publish_finalize (Heather as featured guest), quote_cards/cover_image (cards).

No imports beyond the standard library, so anything can use it.
"""
from __future__ import annotations

import difflib
import re

HOST_NAME = "Heather Reilly Hiemstra"

_HONORIFICS = {"dr", "doctor", "mr", "mrs", "ms", "prof", "rev", "md", "phd", "nd"}
# Surname and middle-name forms the transcripts produce. A speaker named Heather with
# any of these (or a word within 0.8 of "hiemstra") is the host.
_MARKERS = ("hiemstra", "hemstra", "heemstra", "hemesdra", "eilahee",
            "reilly", "reilley", "riley", "rielly", "reily", "rilley")


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if w and w not in _HONORIFICS]


def is_host(speaker: str) -> bool:
    """True for every spelling of the host; False for any other Heather."""
    words = _words(speaker)
    if not words or words[0] != "heather":
        return False
    rest = words[1:]
    if not rest:
        return True                       # a bare "Heather" is the host (R1, REEL-08)
    return any(any(m in w for m in _MARKERS)
               or difflib.SequenceMatcher(None, w, "hiemstra").ratio() >= 0.8
               for w in rest)


def display_name(speaker: str) -> str:
    """The name to print: the host's canonical form, a guest's as given."""
    return HOST_NAME if is_host(speaker) else (speaker or "").strip()
