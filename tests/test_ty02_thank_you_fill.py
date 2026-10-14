"""
test_ty02_thank_you_fill.py — TY-02 deterministic thank-you fill + gate.

Run: python3 test_ty02_thank_you_fill.py   (plain asserts, no pytest needed)

Covers TY-02 acceptance criteria:
  AC1 no model in path / byte-identical → test_golden_byte_equality
  AC2 content gate rejects cruft, passes clean → test_gate_*
  AC3 subject shape + first-name derivation → test_first_name, test_subject_matrix

Discipline (same as test_title01 / test_publisher_email):
  rockstar/content/thank_you.py and rockstar/gates/content_gates.py are PURE (stdlib only) — tested directly, no
  stubs. gmail_client's draft helpers + create_thank_you_draft.py pull in
  googleapiclient (not installed locally) and hit the live Gmail API; they are
  exercised server-side by TY-01's raw-verify, not here. We point PROMPTS_DIR at
  this worktree's prompts/ so the fill reads the real canonical template.
"""

import os
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE))
# Read the REAL canonical template from this repo (not the box path).
os.environ["PROMPTS_DIR"] = str(WORKTREE / "prompts")

from rockstar.content import thank_you  # noqa: E402
from rockstar.gates import content_gates  # noqa: E402  (import after env set)

# Belt-and-suspenders: module read _PROMPTS_DIR at import; force it to the repo's.
thank_you._PROMPTS_DIR = WORKTREE / "prompts"


# ── AC1 — golden byte-equality ─────────────────────────────────────────────────

# A fixed slot set (E29-shaped — the title deliberately carries a ' | ' separator).
_FN = "Thessaly"
_TITLE = "Don't Wait for the Stop | Optimize Your Hormones Before Perimenopause"
_APPLE = "https://podcasts.apple.com/us/podcast/dont-wait-for-the-stop/id1785708777?i=1000700000101"
_SPOTIFY = "https://open.spotify.com/episode/4aBcDeFgHiJkLmNoPqRsTu"
_BLOG = (
    "https://rockstarblends.com/the-blog/dont-wait-for-the-stop-optimize-your-"
    "hormones-before-perimenopause-genx-hits-menopause-e29"
)

# The exact bytes the template must produce for those slots. Hand-written here
# (independent of the fill) so template drift OR a fill bug fails this test loudly.
_EXPECTED_BLOCK = "\n".join(
    [
        f"Subject: Hi {_FN} ~ podcast episode & links are live!",
        "",
        "Thank you so much for joining us on GenX Hits Menopause!",
        "",
        f'Our episode together "{_TITLE}" is live ~ here are links you can share:',
        "",
        f"- Apple Podcasts: {_APPLE}",
        "",
        f"- Spotify: {_SPOTIFY}",
        "",
        "- Audible: https://www.amazon.com/GenX-Hits-Menopause/dp/B0DQ8YXF3D",
        "",
        f"- Our blog: {_BLOG}",
        "",
        "We'll be sharing audio clips from our conversation on Instagram:",
        "@genxhitsmenopause, @rockstar.blends, and @heatherreillyalchemist and we will tag you as collaborator.",
        "",
        "Please share your social posts and tag us above; we'll amplify right back so the episode reaches as many folks as possible.",
        "",
        "Thanks again for being part of the show!",
    ]
)


def test_golden_byte_equality():
    got = thank_you.fill_template(_FN, _TITLE, _APPLE, _SPOTIFY, _BLOG)
    assert got == _EXPECTED_BLOCK, (
        "filled template is NOT byte-identical to the expected output block.\n"
        f"--- got ---\n{got!r}\n--- expected ---\n{_EXPECTED_BLOCK!r}"
    )
    # Fixed Audible URL is present and literal (never a slot).
    assert thank_you.AUDIBLE_URL in got
    print("  AC1 golden: fill_template == expected bytes (fixed slots), Audible literal ✓")


# ── AC2 — gate rejects cruft, passes clean ─────────────────────────────────────

# The actual E29 regression shape: composing agent's narration + faked tool table.
E29_TRANSCRIPT_BODY = (
    "I'll verify the URLs, then create the draft server-side. Let me work through this.\n"
    "Step 1: gather the verified links from the schedule.\n"
    "<tool_call>\n"
    '{"name": "get_schedule", "arguments": {"episode": 29}}\n'
    "</tool_call>\n"
    "tool_response:\n"
    "| Field | Value |\n"
    "| Apple | https://podcasts.apple.com/... |\n"
    "Now composing the thank-you..."
)

CODE_FENCED_BODY = (
    "Thank you so much for joining us on GenX Hits Menopause!\n\n"
    "```\n"
    "some code fence that must never appear in a guest email\n"
    "```\n"
)

TABLE_BODY = (
    "Thank you so much for joining us on GenX Hits Menopause!\n\n| Platform | Link |\n| Apple | https://... |\n"
)

MISSING_FIRST_LINE_BODY = "Hi Thessaly, thanks so much for coming on the show! Here are your links...\n"


def _rejects(body: str) -> bool:
    try:
        content_gates.assert_thank_you_wellformed(body)
    except content_gates.GateFailure:
        return True
    return False


def test_gate_rejects_cruft():
    assert _rejects(E29_TRANSCRIPT_BODY), "E29 transcript garbage must be rejected"
    assert _rejects(CODE_FENCED_BODY), "code-fenced body must be rejected (``` marker)"
    assert _rejects(TABLE_BODY), "markdown table body must be rejected"
    assert _rejects(MISSING_FIRST_LINE_BODY), "body missing the fixed first line must be rejected"
    print("  AC2 gate rejects: E29 transcript, code fence, table row, missing-first-line ✓")


def test_gate_passes_clean_body():
    # A normal clean body (no pipe in title).
    ty = thank_you.build_draft(
        guest_name="Dr. Thessaly Brindlequist",
        episode_title="No Stone Unturned",
        apple_url=_APPLE,
        spotify_url=_SPOTIFY,
        blog_url=_BLOG,
        guest_email="",
    )
    content_gates.assert_thank_you_wellformed(ty["body"])  # must NOT raise
    print("  AC2 gate passes: a clean filled body is well-formed ✓")


def test_gate_passes_title_with_single_pipe():
    # THE reconciliation: E29's real title carries one ' | ' separator, rendered
    # into the body. A single pipe is NOT a table row — the gate must pass it, or
    # TY-01's own clean draft (and every piped-title episode) would be un-draftable.
    ty = thank_you.build_draft(
        guest_name="Dr. Thessaly Brindlequist",
        episode_title=_TITLE,  # contains ' | '
        apple_url=_APPLE,
        spotify_url=_SPOTIFY,
        blog_url=_BLOG,
        guest_email="",
    )
    assert " | " in ty["body"], "sanity: the E29 title's pipe should be in the body"
    content_gates.assert_thank_you_wellformed(ty["body"])  # must NOT raise
    print("  AC2 gate passes: body whose title has a single ' | ' (E29) — not a table ✓")


# ── AC3 — subject shape + first-name derivation ────────────────────────────────


def test_first_name():
    cases = [
        ("Dr. Thessaly Brindlequist", "Thessaly"),  # honorific + multi-word
        ("Thessaly Brindlequist", "Thessaly"),  # multi-word, no title
        ("Heather", "Heather"),  # single token (host-name guest)
        ("Priya", "Priya"),  # single token
        ("Mary-Jane Watson", "Mary-Jane"),  # hyphenated first name kept whole
        ("Prof. Alan Turing", "Alan"),  # another honorific
        ("mrs. jane doe", "jane"),  # lowercased honorific
        ("", ""),  # empty → empty
    ]
    for raw, expected in cases:
        got = thank_you.first_name(raw)
        assert got == expected, f"first_name({raw!r}) = {got!r}, expected {expected!r}"
    print(f"  AC3 first_name: {len(cases)} cases (Dr./Prof./Mrs., multi-word, hyphen, single, empty) ✓")


def test_subject_matrix():
    # Without guest_email → '[FILL IN GUEST EMAIL] ' prefix + route to Heather.
    no_email = thank_you.build_draft(
        guest_name="Dr. Thessaly Brindlequist",
        episode_title=_TITLE,
        apple_url=_APPLE,
        spotify_url=_SPOTIFY,
        blog_url=_BLOG,
        guest_email="",
    )
    assert no_email["subject"] == "[FILL IN GUEST EMAIL] Hi Thessaly ~ podcast episode & links are live!", no_email[
        "subject"
    ]
    assert no_email["to"] == thank_you.HEATHER_ADDRESS, no_email["to"]

    # With guest_email → no prefix, route to the guest.
    with_email = thank_you.build_draft(
        guest_name="Dr. Thessaly Brindlequist",
        episode_title=_TITLE,
        apple_url=_APPLE,
        spotify_url=_SPOTIFY,
        blog_url=_BLOG,
        guest_email="thessaly@example.com",
    )
    assert with_email["subject"] == "Hi Thessaly ~ podcast episode & links are live!", with_email["subject"]
    assert with_email["to"] == "thessaly@example.com", with_email["to"]

    # Malformed email (no '@') is treated as missing → prefixed, routed to Heather.
    bad_email = thank_you.build_draft(
        guest_name="Heather",
        episode_title=_TITLE,
        apple_url=_APPLE,
        spotify_url=_SPOTIFY,
        blog_url=_BLOG,
        guest_email="not-an-email",
    )
    assert bad_email["subject"] == "[FILL IN GUEST EMAIL] Hi Heather ~ podcast episode & links are live!", bad_email[
        "subject"
    ]
    assert bad_email["to"] == thank_you.HEATHER_ADDRESS

    # Single-token + hyphenated names carry through to the subject.
    single = thank_you.build_draft(
        guest_name="Priya",
        episode_title=_TITLE,
        apple_url=_APPLE,
        spotify_url=_SPOTIFY,
        blog_url=_BLOG,
        guest_email="p@x.com",
    )
    assert single["subject"] == "Hi Priya ~ podcast episode & links are live!", single["subject"]
    hyphen = thank_you.build_draft(
        guest_name="Mary-Jane Watson",
        episode_title=_TITLE,
        apple_url=_APPLE,
        spotify_url=_SPOTIFY,
        blog_url=_BLOG,
        guest_email="mj@x.com",
    )
    assert hyphen["subject"] == "Hi Mary-Jane ~ podcast episode & links are live!", hyphen["subject"]
    print("  AC3 subject matrix: prefix iff no email; guest vs Heather routing; Dr./Heather/single/hyphen names ✓")


# ── runner ─────────────────────────────────────────────────────────────────────


def main() -> int:
    tests = [
        test_golden_byte_equality,
        test_gate_rejects_cruft,
        test_gate_passes_clean_body,
        test_gate_passes_title_with_single_pipe,
        test_first_name,
        test_subject_matrix,
    ]
    failed = 0
    print("TY-02 deterministic thank-you fill + gate — test run")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback

            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
