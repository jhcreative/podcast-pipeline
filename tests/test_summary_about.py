"""
test_summary_about.py — an About heading never stops a Round 1 (E13, job 169, 2026-10-14).

E13's whole Round 1 (56 min of transcription, the brand team, the quote check) stopped at step 11: "apple_summary has no
'About ⟨guest⟩' section after the bullets". The normalizer accepted "## About …" or "About ….", as the About line and
left the marks on; the blog parser reads "About ⟨name⟩" only. Now:
  - the normalizer writes the About line in the parser's form;
  - a refused summary is asked for once more, told the reason;
  - still none: the About section comes from the bio already held, as written (never invented);
  - still refused: the text is kept in Drive as apple_summary.REJECTED.md, and the run stops.
Names and text here are invented, except the host's name.
"""

import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from rockstar.gates import summary_norm  # noqa: E402
from rockstar.gates.content_gates import GateFailure  # noqa: E402

HOST = "Heather Reilly Hiemstra"
BIO = "Jane Invented is a sleep coach who works with women in midlife."
BASE = """# Gen X Hits Menopause · S1E99 · Rest Test | A Topic

A hook paragraph about the episode.

Conversations include

**Lead One** Body one.

**Lead Two** Body two.

Tune in to hear it all.
{about}"""


def _with(about):
    return BASE.format(about=f"\n{about}\n\n{BIO}\n" if about else "\n")


def test_every_about_heading_the_normalizer_accepts_the_parser_accepts():
    for heading in (
        "About Jane Invented",
        "## About Jane Invented",
        "### About Your Hosts",
        "## About Jane Invented.",
        "About the Hosts: Heather and John",
        "About Jane Invented:",
    ):
        fixed, _ = summary_norm.normalize_apple_summary(_with(heading), "E99")
        assert "\n\n" + summary_norm.canonical_about(heading) + "\n\n" in fixed, (heading, fixed)
    # Bold lines are read as bullets first (a short "**About Time** …" bullet must stay a bullet); the parser takes a
    # bold About as it is.
    for heading in ("**About Jane Invented**", "**About Jane Invented:**"):
        summary_norm.normalize_apple_summary(_with(heading), "E99")
    assert summary_norm.canonical_about("## **About Jane Invented:**") == "About Jane Invented"


def test_a_sentence_opening_with_about_is_never_made_a_heading():
    """Toby, #75: these parse on main and must still parse, with the real About as the About."""
    from rockstar.content import blog_post

    first = BASE.format(about="").replace(
        "Tune in to hear it all.", "About twenty minutes in, she cries.\n\nTune in to hear it all."
    )
    later = BASE.format(about="").replace(
        "Tune in to hear it all.", "Tune in to hear it all.\n\nAbout twenty minutes in, she cries."
    )
    for text in (first, later):
        fixed, _ = summary_norm.normalize_apple_summary(text + f"\nAbout Jane Invented\n\n{BIO}\n", "E99")
        parsed = blog_post.parse_apple_summary(fixed)
        assert parsed["guest"] == "Jane Invented" and "About twenty minutes in, she cries." in fixed, parsed
    assert summary_norm.add_about(later, "Jane Invented", BIO), "a sentence opening with About doesn't block it"


def test_a_bio_already_in_the_text_gets_a_heading_not_a_second_copy():
    text = BASE.format(about=f"\nMeet Your Guest\n\n{BIO}\n")
    added = summary_norm.add_about(text, "Jane Invented", BIO)
    assert added.count(BIO) == 1 and f"About Jane Invented\n\n{BIO}" in added, added
    summary_norm.normalize_apple_summary(added, "E99")


def test_the_known_bio_is_added_only_where_there_is_no_about():
    added = summary_norm.add_about(_with(""), "Jane Invented", BIO)
    assert added.rstrip().endswith(f"About Jane Invented\n\n{BIO}"), added
    summary_norm.normalize_apple_summary(added, "E99")  # and it parses
    assert summary_norm.add_about(_with("## About Jane Invented, a Very Long Heading " + "x" * 90), "Jane", BIO) is None
    assert summary_norm.add_about(_with(""), "Jane Invented", "") is None, "no bio: nothing to add"
    assert summary_norm.add_about(_with(""), "", BIO) is None, "no name: nothing to add"


_SAVED = {}


def _restore():

    for (mod, attr), val in _SAVED.items():
        setattr(mod, attr, val)
    _SAVED.clear()


def _round_1(replies):
    """rockstar.jobs.round_1 with the model replying `replies` in order, and Drive recording uploads. _restore() undoes it."""
    for name in ("drive_client", "db", "calendar_client"):
        _swap.setdefault(name, types.ModuleType(name))
    if "anthropic" not in sys.modules:
        a = types.ModuleType("anthropic")
        a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *x, **k: None})
        _swap.put("anthropic", a)
    from rockstar.jobs import round_1 as r1

    asked, uploads, standups = [], [], []
    for mod, attr in (
        (r1.anthropic_client, "compose_apple_summary"),
        (r1.drive_client, "upload_file"),
        (r1, "_prepend_standup"),
    ):
        _SAVED.setdefault((mod, attr), getattr(mod, attr, None))
    r1.anthropic_client.compose_apple_summary = lambda **kw: (
        asked.append(kw.get("feedback", "")) or replies[len(asked) - 1]
    )
    r1.drive_client.upload_file = lambda path, folder, name, mime: uploads.append((name, Path(path).read_text()))
    r1._prepend_standup = lambda code, msg: standups.append(msg)
    return r1, asked, uploads, standups


def _call(r1, host_ep=False, bio=BIO):
    meta = {"guest_name": "" if host_ep else "Jane Invented", "guest_bio": bio}
    try:
        return r1._summary_that_parses("transcript", meta, [], "E99", "folder-99", Path(tempfile.mkdtemp()), host_ep)
    finally:
        _restore()


def test_a_good_first_draft_is_used_as_is():
    r1, asked, uploads, _ = _round_1([_with("## About Jane Invented")])
    text, _ = _call(r1)
    assert len(asked) == 1 and "\nAbout Jane Invented\n" in text and not uploads


def test_a_refused_draft_is_asked_again_with_the_reason():
    r1, asked, uploads, _ = _round_1([_with(""), _with("About Jane Invented")])
    text, _ = _call(r1)
    assert len(asked) == 2 and asked[0] == "" and "About" in asked[1], asked
    assert "\nAbout Jane Invented\n" in text and not uploads


def test_two_drafts_without_an_about_get_the_known_bio_never_a_stop():
    r1, asked, uploads, standups = _round_1([_with(""), _with("")])
    text, _ = _call(r1, host_ep=True, bio="Heather Reilly Hiemstra is a metalsmith, alchemist, cancer survivor.")
    assert f"\nAbout {HOST}\n\nHeather Reilly Hiemstra is a metalsmith" in text, text
    assert not uploads and "added from the host's bio" in standups[0], standups


def test_still_refused_the_text_is_kept_in_drive_and_the_run_stops():
    bad = BASE.format(about="\n").replace("Conversations include", "Topics")  # no Conversations line at all
    r1, asked, uploads, _ = _round_1([bad, bad])
    try:
        _call(r1)
        raise AssertionError("a summary nothing can repair must stop the run")
    except GateFailure as exc:
        assert "apple_summary.REJECTED.md" in str(exc), exc
    assert uploads == [("apple_summary.REJECTED.md", bad)], uploads


def test_round_1_uses_it():
    src = (REPO / "rockstar" / "jobs" / "round_1.py").read_text()
    step = src[src.index('run_ledger.step("11", "apple_summary")') : src.index('run_ledger.step("11.5", "title")')]
    assert "_summary_that_parses(" in step and "compose_apple_summary(" not in step


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ok  ", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("  FAIL", name, f"{type(exc).__name__}: {exc}")
    sys.exit(1 if failed else 0)
