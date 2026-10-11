"""
thank_you.py — deterministic guest thank-you fill (NO model in the path).

`prompts/thank_you.md` is explicit: the guest thank-you is a STANDARD TEMPLATE
with deterministic fills — not an AI-composed letter. Every input the fill needs
(guest first name, episode title, Apple/Spotify/blog URLs) is a known value at
the call site; the model has nothing to decide. Round-tripping it through the LLM
(the old `anthropic_client.compose_thank_you`) could — and on E29 did — drift into
emitting the model's own reasoning/tool-call narration as the draft body.

This module IS the path now: it reads the ONE canonical template
(`prompts/thank_you.md`, the file Heather tunes), fills its slots by literal
substitution, and returns a subject + body. Because the body is the template's
own bytes with slots replaced, a fixed slot set produces a byte-identical body —
no generation, no drift. See ADR-0010.

PURE module: stdlib only (os, re, pathlib). No Gmail/Drive/anthropic imports, so
it is unit-testable directly like content_gates.py. The gate that rejects cruft
lives in content_gates.assert_thank_you_wellformed — the caller runs it BEFORE
create_draft.
"""

import os
import re
from pathlib import Path

# Audible is the FIXED show URL — same every episode, never a slot. Kept here so
# the one place the template hardcodes it (prompts/thank_you.md) and any code that
# needs to reference it agree. It is NOT substituted; it is literal in the block.
AUDIBLE_URL = "https://www.amazon.com/GenX-Hits-Menopause/dp/B0DQ8YXF3D"

# Where the draft routes when we have no guest address: Heather sends it herself
# after filling the recipient in. DRAFT-only either way — this module never sends.
HEATHER_ADDRESS = os.environ.get("HEATHER_ADDRESS", "heather@example.test")

# Prompts dir: match anthropic_client (PROMPTS_DIR on the box), but fall back to
# the repo's own prompts/ so local tests find the template with no env set.
_PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR") or (Path(__file__).parent / "prompts"))
_TEMPLATE_FILE = "thank_you.md"

# Honorific/title tokens stripped before taking the first name. Compared
# case-insensitively with any trailing '.' removed, so "Dr." and "dr" both match.
_TITLE_PREFIXES = frozenset({
    "dr", "mr", "mrs", "ms", "mx", "prof", "professor",
    "rev", "sir", "dame", "fr", "st",
})


def first_name(guest_name: str) -> str:
    """Derive the guest's first name deterministically for the SUBJECT line.

    The template personalizes ONLY the subject with the first name, so this is the
    one spot a name is rendered. Rules, in order:
      • strip leading honorifics — "Dr. Corinne Maltravers" -> "Corinne"
      • take the first remaining whitespace-delimited token — multi-word names keep
        only the first ("Corinne Maltravers" -> "Corinne")
      • a hyphenated first name is ONE token, kept whole ("Mary-Jane Watson" -> "Mary-Jane")
      • a single token is returned as-is ("Heather" -> "Heather")
    Returns "" for an empty/whitespace name (caller decides what to do with that).
    """
    tokens = (guest_name or "").strip().split()
    idx = 0
    while idx < len(tokens) and tokens[idx].lower().rstrip(".") in _TITLE_PREFIXES:
        idx += 1
    if idx >= len(tokens):
        # The name was nothing but honorifics (or empty). Fall back to the first
        # raw token rather than inventing one; "" if there was truly nothing.
        return tokens[0] if tokens else ""
    return tokens[idx]


def _load_output_block() -> str:
    """Read prompts/thank_you.md and return the single fenced output block verbatim
    (the text between the ``` fences under '## Exact output'). This is the byte
    source of truth — we fill slots in THESE bytes, never a hand-copied duplicate,
    so the template stays a single definition Heather can tune."""
    path = _PROMPTS_DIR / _TEMPLATE_FILE
    md = path.read_text(encoding="utf-8")
    m = re.search(r"```(?:\w+)?\n(.*?)\n```", md, re.DOTALL)
    if not m:
        raise ValueError(
            f"{path}: no fenced output block found — the template must contain the "
            f"exact-output ``` block the fill substitutes into."
        )
    return m.group(1)


def fill_template(
    guest_first_name: str,
    episode_title: str,
    apple_url: str,
    spotify_url: str,
    blog_url: str,
) -> str:
    """Fill the template's output block (Subject line + body) by literal slot
    substitution. Returns the whole block — Subject included — so it can be written
    to Drive as thank_you.md AND split for the draft. Byte-identical to the template
    for a fixed slot set (that is the golden test)."""
    block = _load_output_block()
    return (
        block
        .replace("{{guest_first_name}}", guest_first_name)
        .replace("{{episode_title}}", episode_title)
        .replace("{{apple_url}}", apple_url)
        .replace("{{spotify_url}}", spotify_url)
        .replace("{{blog_url}}", blog_url)
    )


def _split_subject_body(block: str) -> tuple[str, str]:
    """Split a filled block into (subject_core, body). The block's first line is
    'Subject: ...'; the body is everything after it with the separating blank line
    removed. subject_core has no '[FILL IN GUEST EMAIL]' prefix — that is applied
    by build_draft based on whether we have a guest address."""
    lines = block.split("\n")
    if not lines[0].lower().startswith("subject:"):
        raise ValueError(
            "thank_you template output block does not start with a 'Subject:' line"
        )
    subject_core = lines[0].split(":", 1)[1].strip()
    body = "\n".join(lines[1:]).lstrip("\n")
    return subject_core, body


def has_guest_email(guest_email: str) -> bool:
    """True when we have a usable guest address. Missing OR malformed (no '@')
    both count as 'no email' — the draft then routes to Heather with the subject
    flagged so she sets the recipient before sending."""
    return bool(guest_email) and "@" in guest_email


def build_draft(
    guest_name: str,
    episode_title: str,
    apple_url: str,
    spotify_url: str,
    blog_url: str,
    guest_email: str = "",
) -> dict:
    """One deterministic call producing everything create_draft needs.

    Returns:
      {
        "first_name": str,   # derived from guest_name
        "to":         str,   # guest_email if usable, else Heather
        "subject":    str,   # 'Hi {first_name} ~ podcast episode & links are live!',
                             #   prefixed '[FILL IN GUEST EMAIL] ' only when no guest email
        "body":       str,   # template body — starts with the fixed first line
        "block":      str,   # full filled block (Subject + body), for Drive upload
      }

    The caller (publish_finalize Step 13 and create_thank_you_draft.py) runs
    content_gates.assert_thank_you_wellformed(body) BEFORE create_draft.
    """
    fn = first_name(guest_name)
    block = fill_template(fn, episode_title, apple_url, spotify_url, blog_url)
    subject_core, body = _split_subject_body(block)

    if has_guest_email(guest_email):
        to = guest_email
        subject = subject_core
    else:
        # No usable address: Heather sends it, so flag the subject and route to her.
        to = HEATHER_ADDRESS
        subject = f"[FILL IN GUEST EMAIL] {subject_core}"

    return {
        "first_name": fn,
        "to": to,
        "subject": subject,
        "body": body,
        "block": block,
    }
