"""
test_title02_one_title.py — one title per quote, checked once, used everywhere (TITLE-02, John 2026-10-13).

E34's Phase 2 lost its card set to a 45-character q05 title: Round 1 had passed the heading at 60 characters, the card
step asked the model for a second title under a 44-character rule, the retry missed, and the cards stopped for a hand
fix. Now the heading is the one title, made to pass the cover's rules in Round 1, and nothing downstream can refuse it:
  - a heading that breaks a rule is rewritten (told the rule), then repaired by code, then given a plain title;
  - every change is recorded on the quote (title_check), never silent;
  - Round 1, the re-run tool and Phase 2 all run it; the card step takes the headings as its cover titles.
Every name and quote here is invented.
"""

import json
import random
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from rockstar.gates import quote_title  # noqa: E402

QUOTE = "Every night I put the phone in another room and the evening slows down for me."


def Q(qid, heading, text=QUOTE, speaker="Jane Invented"):
    return {"id": qid, "speaker": speaker, "text": text, "heading": heading}


def test_a_passing_heading_is_left_alone():
    quotes, report = quote_title.ensure([Q("q01", "The Phone Leaves the Bedroom")], "E99", rewrite=None)
    assert quotes[0]["heading"] == "The Phone Leaves the Bedroom" and "title_check" not in quotes[0], quotes
    assert report == {"rewritten": [], "repaired": [], "plain": [], "unfixed": []}, report
    assert quote_title.summary(report) == ""


def test_the_e34_shape_a_45_character_heading_is_rewritten_with_the_rule_named():
    long = "The Third Phase of Changes Deserves Its Party"  # 45 characters, like E34's q05
    assert len(long) == 45
    asked = []

    def rewrite(bad):
        asked.append(bad)
        return {"q05": "A Third Change Worth Celebrating"}

    quotes, report = quote_title.ensure([Q("q05", long)], "E99", rewrite)
    assert quotes[0]["heading"] == "A Third Change Worth Celebrating", quotes
    assert report["rewritten"] == ["q05"], report
    assert "44" in asked[0]["q05"][1] and asked[0]["q05"][0] == long, "the model is told the title and the rule"
    check = quotes[0]["title_check"]
    assert check["was"] == long and check["fixed_by"] == "rewritten" and "44" in check["rule"], check


def test_a_rewrite_that_misses_twice_is_repaired_by_code():
    calls = []

    def rewrite(bad):
        calls.append(1)
        return {"q01": "Still A Title That Is Much Too Long For The Cover Slide"}

    quotes, report = quote_title.ensure(
        [Q("q01", "Sleep & Screens: The Evening That Finally Slows Down for Good")], "E99", rewrite
    )
    assert len(calls) == 2 and report["repaired"] == ["q01"], (calls, report)
    assert quotes[0]["heading"] == "Sleep and Screens", quotes  # its own first half, "&" written out


def test_code_never_cuts_words_off_the_end():
    """Toby, #73: a cut reads broken and can widen a qualified claim, which no rule sees. No colon or dash to split
    on: the plain title, never a cut."""
    for heading in (
        "Why Estrogen Patches Are Fine for Most Women After Fifty",
        "A Second Season of Change Deserves Its Welcome",
    ):
        quotes, report = quote_title.ensure([Q("q01", heading)], "E99", rewrite=None)
        assert quotes[0]["heading"] == "Jane in Conversation" and report["plain"] == ["q01"], (heading, quotes)


def test_a_model_error_never_stops_the_run():
    def rewrite(bad):
        raise RuntimeError("API down")

    quotes, report = quote_title.ensure([Q("q01", "Sleep and Screens & the Slow Evening?")], "E99", rewrite)
    assert report["repaired"] == ["q01"] and quote_title.problem(quotes[0]["heading"], QUOTE, "t") is None, quotes


def test_code_cuts_at_the_titles_own_break_first_and_a_dash_is_not_a_word():
    """Round-2 shapes (2026-10-13), invented here: a colon or dash title keeps its first half rather than losing words off the
    end; "Hurry Night — Only Sleep It" counts 7 words, not 8."""
    quotes, report = quote_title.ensure(
        [
            Q("q01", "The Quiet Kitchen: Teaching Your Whole Household Patience"),
            Q("q02", "Every Garden Survives Winter — And Comes Back Greener"),
            Q("q03", "You Can't Hurry Night — Only Sleep It"),
        ],
        "E99",
        rewrite=None,
    )
    assert [q["heading"] for q in quotes] == [
        "The Quiet Kitchen",
        "Every Garden Survives Winter",
        "You Can't Hurry Night — Only Sleep It",
    ], quotes
    assert report["repaired"] == ["q01", "q02"] and "title_check" not in quotes[2], report


def test_what_code_cant_fix_gets_a_plain_title():
    quotes, report = quote_title.ensure(
        [
            Q("q01", "Magnesium Cures Insomnia"),
            Q("q02", "", speaker="Dr. Jane Invented"),
            Q("q03", "Ab", speaker="Heather Reilly Hiemstra"),
        ],
        "E99",
        rewrite=None,
    )
    assert [q["heading"] for q in quotes] == [
        "Jane in Conversation",
        "Jane in Conversation",
        "Heather in Conversation",
    ], quotes
    assert report["plain"] == ["q01", "q02", "q03"] and report["unfixed"] == [], report
    assert "plain q01, q02, q03" in quote_title.summary(report)


def test_no_heading_can_make_it_fail():
    """Fuzz: whatever the model wrote, every quote leaves with a title the cover accepts."""
    rnd = random.Random(13)
    pieces = [
        "The",
        "Midlife",
        "&",
        "Shocking",
        "Secret",
        "Cures",
        "I",
        "My",
        "Sleep",
        "Evening",
        "Phone",
        "Room",
        "Hormones",
        "?",
        "!",
        "…",
        "#tag",
        "@handle",
        "https://x.test",
        "Every",
        "night",
        "put",
        "the",
        "phone",
        "in",
        "another",
        "room",
        "and",
        "Of",
        "To",
        "Estrogen",
        "Bone",
        "Density",
        "Journey",
        "😀",
        "",
    ]
    quotes = [
        Q(
            f"q{i:02d}",
            " ".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 16))),
            speaker=rnd.choice(["Jane Invented", "Dr. Ann Roe", "", "X"]),
        )
        for i in range(300)
    ]
    quotes, report = quote_title.ensure(quotes, "E99", rewrite=lambda bad: {i: "Nope & Nope" for i in bad})
    assert report["unfixed"] == [], report["unfixed"]
    for q in quotes:
        assert quote_title.problem(q["heading"], q["text"], "t") is None, q


def test_round_1_the_rerun_and_phase_2_all_run_it():
    r1 = (REPO / "rockstar" / "jobs" / "round_1.py").read_text()
    first, gate, again, summary = (
        r1.index("quotes = _ensure_titles(quotes, episode_code)"),
        r1.index("quote_gate.check_selected("),
        r1.rindex("quotes = _ensure_titles(quotes, episode_code)"),
        r1.index('run_ledger.step("11", "apple_summary")'),
    )
    assert first < gate < again < summary, "before the check, and again after it (a swap brings its own heading)"
    assert again < r1.index("quotes_path.write_text(", again), "the fixed titles are what quotes.json holds"
    rerun = (REPO / "rockstar" / "tools" / "rerun_brand_team.py").read_text()
    assert "quote_title.ensure(" in rerun and rerun.index("quote_title.ensure(") < rerun.index("assert_quote_headings")
    pf = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    assert pf.index('run_ledger.step("5c", "quote_titles")') < pf.index('run_ledger.step("6", "faq_blog")')
    import subprocess

    shipped = subprocess.run(["bash", "scripts/deploy.sh", "--list"], cwd=REPO, capture_output=True, text=True).stdout
    assert "rockstar/gates/quote_title.py" in shipped.split(), "quote_title ships with a deploy"


def test_phase_2_saves_the_fixed_titles_to_drive_and_never_stops_on_a_drive_error():
    """Toby, #73: Drive's quotes.json is what a person reads; a title fixed in Phase 2 is saved there (upload, then
    the old one to the trash), so the blog and the cards never carry a title the read can't see."""
    for name in (
        "rockstar.clients.drive_client",
        "rockstar.service.db",
        "rockstar.clients.calendar_client",
        "rockstar.clients.gmail_client",
    ):
        _swap.setdefault(name, types.ModuleType(name))
    if "anthropic" not in sys.modules:
        a = types.ModuleType("anthropic")
        a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *x, **k: None})
        _swap.put("anthropic", a)
    from rockstar.jobs import publish_finalize as pf

    calls, saved = [], (getattr(pf.drive_client, "upload_file", None), getattr(pf.drive_client, "trash_file", None))
    pf.drive_client.upload_file = lambda path, folder, name, mime: calls.append(("upload", folder, name)) or {"id": "n"}
    pf.drive_client.trash_file = lambda fid: calls.append(("trash", fid))
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "quotes.json")
            p.write_text("{}")
            note = pf._save_fixed_titles(p, "old-id", "folder-1", "E99")
            assert calls == [("upload", "folder-1", "quotes.json"), ("trash", "old-id")], calls
            assert "Saved to Drive" in note, note

            def boom(*a, **k):
                raise OSError("Drive down")

            pf.drive_client.upload_file = boom
            note = pf._save_fixed_titles(p, "old-id", "folder-1", "E99")
            assert "NOT saved to Drive" in note, note
    finally:
        pf.drive_client.upload_file, pf.drive_client.trash_file = saved
    src = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    helper = src[src.index("def _fix_quote_titles(") : src.index("def _save_fixed_titles(")]
    assert "_save_fixed_titles(quotes_path, quotes_file_id, episode_folder_id, episode_code)" in helper
    step = src[src.index('run_ledger.step("5b", "clip_ceiling")') : src.index('run_ledger.step("6", "faq_blog")')]
    assert 'run_ledger.step("5c", "quote_titles")' in step and "_fix_quote_titles(" in step


def test_the_card_step_takes_the_headings_as_its_cover_titles():
    """No second title: the sidecar holds the headings, fixed by the same code when one breaks a rule."""
    src = (REPO / "rockstar" / "cards" / "make_cover_titles.py").read_text()
    assert "compose_cover_titles(" not in src, "the card step no longer asks the model for its own title"
    with tempfile.TemporaryDirectory() as d:
        quotes = [
            Q("q01", "The Phone Leaves the Bedroom"),
            Q("q02", "The Third Phase of Changes Deserves Its Party", text="A different quote about change."),
        ]
        Path(d, "quotes.json").write_text(json.dumps({"quotes": quotes}))
        fake = types.ModuleType("anthropic_client")
        fake.rewrite_quote_titles = lambda title, qs, bad: (_ for _ in ()).throw(RuntimeError("no model in a test"))
        saved_mod, saved_argv = sys.modules.get("rockstar.clients.anthropic_client"), sys.argv
        _swap.put("rockstar.clients.anthropic_client", fake)
        sys.path.insert(0, str(REPO))
        from rockstar.cards import make_cover_titles

        try:
            sys.argv = ["make_cover_titles.py", "--episode", "99", "--dir", d, "--title", "An Invented Episode"]
            make_cover_titles.main()
        finally:
            sys.argv = saved_argv
            if saved_mod is not None:
                _swap.put("rockstar.clients.anthropic_client", saved_mod)
            else:
                _swap.pop("rockstar.clients.anthropic_client", None)
        titles = json.loads(Path(d, "cover_titles.json").read_text())["titles"]
        assert titles["q01"] == "The Phone Leaves the Bedroom", titles
        assert titles["q02"] == "Jane in Conversation", titles  # no clean repair and no model here: the plain title


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
