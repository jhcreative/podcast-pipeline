"""
summary_norm.py — R1-02 / R1-04: make a model-written apple_summary.md safe to ship, in plain code.

WHY THIS EXISTS (E32, 2026-10-01): Round 1's apple_summary.md opened with the model's working notes
("Looking at the transcript, I can see…", "No Brand Team quotes were supplied, so I'll…", a `---` rule)
above the real title, called the host "Heather Riley Helmsdrum" (the transcription's mishearing of
"Heather Reilly Hiemstra"), and ran "Conversations include", the bullets and "About ⟨guest⟩" together as
one block. Nothing checked any of it until Phase 2's blog builder, a day or a week later. blog_post's parser
drops the first paragraph as "the title line", so the notes would have become the post's opening paragraphs.

WHAT IT DOES, in order, deterministically (no model call):
  1. finds the title line (a line that starts with "Gen X Hits Menopause", with any "#"/"**" around it) and
     drops everything above it. No title line → GateFailure: a summary we can't anchor is refused, never
     guessed at.
  2. drops bare horizontal rules ("---", "***", "___").
  3. gives "Conversations include", each **bold-lead** bullet and "About ⟨guest⟩" their own paragraph,
     because blog_post.parse_apple_summary reads the file paragraph by paragraph.
  4. canonicalizes the host's name everywhere (see HOST_NAME_RE).
  5. proves the result with blog_post.parse_apple_summary itself, the same parser Phase 2 uses, so a summary
     that would fail Phase 2 fails Round 1 now, before anything irreversible.

It never rewrites a sentence. It removes model chatter and fixes layout and one known name; the words that
remain are the model's (and, under the RSB content canon, the Rockstar team's to edit).
"""
from __future__ import annotations

import re

import blog_post
from content_gates import GateFailure

from host_identity import HOST_NAME, is_host  # noqa: E402  REEL-09: the one host rule

# The host's name as transcription mishears it, then as the model copies it: "Heather Riley",
# "Heather Riley Helmsdrum", "Heather Riley, Heems Drum", "Heather Riley Heemstra". The surname arm only
# accepts H-words ending like the real surname (…stra/…strum/…sdrum/"… Drum"), so "Heather Riley Hosts" and
# "Heather Riley Helps" keep their verb. Pinned by test_r1_reliability.py.
HOST_NAME_RE = re.compile(
    r"\bHeather\s+(?:Riley|Reilly|Rielly|Reily|Rilley)\b"
    # REEL-09: a hyphen joins them too ("Heather Reilly-Hemstra", E30; "Riley-Hemstra", E28).
    # Without [\s-] the match stopped at "Reilly" and left "-Hemstra" dangling.
    r"(?:,?[\s-]+(?:H[a-z]{1,4}(?:stra|strum|sdrum|s\s+[Dd]rum|\s+[Dd]rum)))?"
)

_TITLE_RE = re.compile(r"^\s*(?:#+\s*)?(?:\*\*\s*)?Gen X Hits Menopause\b", re.IGNORECASE)
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_CONVERSATIONS_LINE_RE = re.compile(r"^\s*(?:\*\*)?\s*conversations include:?\s*(?:\*\*)?\s*$", re.IGNORECASE)
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?\*\*")
_ABOUT_LINE_RE = re.compile(r"^\s*(?:#+\s*)?(?:\*\*)?\s*about\s+[^\n]{1,80}?(?:\*\*)?\s*$", re.IGNORECASE)


def canonicalize_host(text: str) -> str:
    """Replace every misheard form of the host's name with HOST_NAME."""
    return HOST_NAME_RE.sub(HOST_NAME, text or "")


def canonicalize_speaker(speaker: str) -> str:
    """A quote's speaker: any "Heather …" that isn't someone else's full name becomes HOST_NAME."""
    s = (speaker or "").strip()
    return HOST_NAME if is_host(s) else s


def normalize_apple_summary(text: str, episode_code: str = "") -> tuple[str, dict]:
    """Return (clean_text, report). Raises GateFailure if no title line or if the result won't parse.

    report = {"dropped_preamble_lines": int, "dropped_rules": int, "host_fixes": int}: counts only, safe to log.
    """
    tag = f"{episode_code}: " if episode_code else ""
    lines = (text or "").replace("\r\n", "\n").split("\n")

    title_at = next((i for i, ln in enumerate(lines) if _TITLE_RE.match(ln)), None)
    if title_at is None:
        raise GateFailure(f"{tag}apple_summary has no 'Gen X Hits Menopause' title line; refusing to guess where it starts")
    dropped_preamble = sum(1 for ln in lines[:title_at] if ln.strip())
    lines = lines[title_at:]

    dropped_rules = sum(1 for ln in lines if _RULE_RE.match(ln))
    lines = [ln for ln in lines if not _RULE_RE.match(ln)]

    out: list[str] = []
    seen_conversations = False
    for ln in lines:
        own_para = False
        if _CONVERSATIONS_LINE_RE.match(ln):
            seen_conversations = True
            own_para = True
        elif seen_conversations and (_BULLET_LINE_RE.match(ln) or _ABOUT_LINE_RE.match(ln)):
            own_para = True
        if own_para:
            out += ["", ln.rstrip(), ""]
        else:
            out.append(ln.rstrip())
    clean = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"

    host_fixes = sum(1 for m in HOST_NAME_RE.finditer(clean) if m.group(0) != HOST_NAME)
    fixed = canonicalize_host(clean)

    blog_post.parse_apple_summary(fixed)  # raises GateFailure with the parser's own reason

    return fixed, {
        "dropped_preamble_lines": dropped_preamble,
        "dropped_rules": dropped_rules,
        "host_fixes": host_fixes,
    }
