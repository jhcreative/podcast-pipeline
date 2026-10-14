"""
test_quote01_gate.py — QUOTE-01's first slice: a direct quote is never edited (ST-10).

- Dr. Joe flags and never edits: a text he changes is restored by the code, whatever his prompt says.
- Round 1's verbatim gate (quote_gate + quote_tape): a quote that isn't the speaker's words gets ONE restore-or-swap
  pass from Lisa; still not verbatim, or left out, or the pass fails, it's dropped and recorded. Never let through.
Every transcript and name here is invented (tests are exported). The model is a fake; the chain and the gate are real.
"""

import ast
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
os.environ["RUN_LOG_PATH"] = str(Path(tempfile.mkdtemp(prefix="quote01-")) / "run_log.jsonl")
if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    _swap.put("anthropic", _a)

from rockstar.clients import anthropic_client  # noqa: E402
from rockstar.gates import (
    quote_gate,  # noqa: E402
    summary_norm,  # noqa: E402
)


def seg(start, text, speaker="SPEAKER_01"):
    return {"start": start, "end": start + 10, "text": text, "speaker": speaker}


TAPE = [
    seg(0, "Welcome to the show, I'm Heather Riley Hemstra and today we talk about sleep.", "SPEAKER_00"),
    seg(10, "Every night I put the phone in another room and the whole evening slows down for me."),
    seg(20, "Magnesium helped me a little but it was the walking in the morning that really changed things."),
    seg(30, "I tell my patients that rest is not a reward, it is part of the work."),
]

Q = lambda qid, text, **kw: {"id": qid, "speaker": "Jane Invented", "text": text, "heading": "A Heading", **kw}  # noqa: E731


# ── Dr. Joe flags, never edits ────────────────────────────────────────────────


def test_a_text_joe_changed_is_restored():
    before = {"quotes": [Q("q01", "rest is not a reward"), Q("q02", "the whole evening slows down")]}
    after = {
        "quotes": [Q("q01", "rest may help, as Dr. Invented describes it"), Q("q02", "the whole evening slows down")]
    }
    assert anthropic_client._keep_quote_text(before, after) == ["q01"]
    assert after["quotes"][0]["text"] == "rest is not a reward"


def test_the_chain_restores_joe_whatever_his_prompt_says():
    """The fake model is a Joe who rewrites q01; Lisa then echoes what she's given. The final text is the original."""
    marcus = {"quotes": [Q("q01", "I tell my patients that rest is not a reward")]}
    joe = {
        "quotes": [Q("q01", "Rest may support recovery, as research suggests", dr_joe_review={"status": "qualified"})]
    }
    replies = [json.dumps(marcus), json.dumps(marcus), json.dumps(joe)]
    seen = []

    def create(**kw):
        seen.append(kw["messages"][0]["content"][-1]["text"])
        text = replies.pop(0) if replies else seen[-1].split("```json\n", 1)[1].split("\n```", 1)[0]  # Lisa echoes
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=text)],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(
                input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0
            ),
            model="m",
        )

    anthropic_client._get_client = lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    anthropic_client._load_prompt = lambda name: f"system prompt for {name}"
    report = {}
    final = anthropic_client.run_brand_team_chain("transcript", {}, report=report)
    assert final[0]["text"] == "I tell my patients that rest is not a reward", final
    assert report["joe_text_restored"] == ["q01"], report
    assert "as research suggests" not in seen[-1], "Lisa must never be shown Joe's wording as the quote"


def _chain(replies_by_step, report=None):
    """Run the real chain with a fake model. replies_by_step: one reply per call in order; None = echo the request."""
    replies, seen = list(replies_by_step), []

    def create(**kw):
        seen.append(kw["messages"][0]["content"][-1]["text"])
        r = replies.pop(0) if replies else None
        text = json.dumps(r) if r is not None else seen[-1].split("```json\n", 1)[1].split("\n```", 1)[0]
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=text)],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(
                input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0
            ),
            model="m",
        )

    anthropic_client._get_client = lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    anthropic_client._load_prompt = lambda name: f"system prompt for {name}"
    report = {} if report is None else report
    return anthropic_client.run_brand_team_chain("transcript", {}, report=report), report, seen


THREE = {
    "quotes": [
        Q("q01", "rest is not a reward"),
        Q("q02", "the whole evening slows down"),
        Q("q03", "Magnesium helped me a little"),
    ]
}


def test_a_reviewer_who_leaves_quotes_out_cannot_shrink_the_set():
    """E34, 2026-10-13: Robert returned 1 of Marcus's 5; Joe then reviewed 1 and Lisa rebuilt the set. A reviewer flags,
    only Lisa cuts: what Robert leaves out goes on unchanged, marked not_reviewed_by, and the report names it."""
    final, report, seen = _chain([THREE, {"quotes": [Q("q02", "the whole evening slows down")]}])
    assert [q["id"] for q in final] == ["q01", "q02", "q03"], final
    assert report["reviewer_dropped"] == {"brand_team_robert": ["q01", "q03"]}, report
    assert all(f'"id": "{i}"' in seen[2] for i in ("q01", "q02", "q03")), "Joe must review every quote"
    assert [q.get("not_reviewed_by") for q in final] == [["robert"], None, ["robert"]], final
    assert "q01 (not reviewed by robert)" in seen[3], "Lisa is told which quotes a reviewer never saw"


def test_a_reply_with_no_ids_or_new_ids_is_set_aside():
    """Toby: a reviewer's reply is a subset of what it was given. No ids, or an id it wasn't given: set aside."""
    for bad in (
        {"quotes": [{"text": "x"}, {"text": "y"}, {"text": "z"}]},
        {
            "quotes": [
                Q("q01b", "a swapped quote"),
                Q("q02", "the whole evening slows down"),
                Q("q03", "Magnesium helped me a little"),
            ]
        },
        {
            "quotes": [
                Q("q01", "rest is not a reward"),
                Q("q01", "rest is not a reward"),  # a repeated id
                Q("q02", "the whole evening slows down"),
                Q("q03", "Magnesium helped me a little"),
            ]
        },
    ):
        final, report, _ = _chain([THREE, bad])
        assert [q["id"] for q in final] == ["q01", "q02", "q03"], (bad, final)  # never grows, never doubles
        assert report["reviewer_mangled"] == ["brand_team_robert"], report


def test_joe_is_asked_again_for_what_he_left_out():
    """A partial clinical review is no review: Joe gets one more ask, for just the quotes he left out."""
    joe_partial = {"quotes": [Q("q02", "the whole evening slows down")]}
    final, report, seen = _chain([THREE, None, joe_partial])  # Robert echoes; Joe drops two
    assert "brand_team_dr_joe" not in report.get("reviewer_dropped", {}), report  # the second ask (an echo) filled it
    assert '"id": "q01"' in seen[3] and '"id": "q02"' not in seen[3], "the second ask carries only the gaps"
    assert [q["id"] for q in final] == ["q01", "q02", "q03"] and not any(q.get("not_reviewed_by") for q in final)


def test_quotes_joe_never_reviews_are_marked_and_reported():
    joe_partial = {"quotes": [Q("q02", "the whole evening slows down")]}
    final, report, seen = _chain([THREE, None, joe_partial, {"quotes": []}])  # the second ask returns nothing
    assert report["reviewer_dropped"] == {"brand_team_dr_joe": ["q01", "q03"]}, report
    assert "q01 (not reviewed by dr_joe)" in seen[-1], "Lisa is told"
    tool = (REPO / "rockstar" / "tools" / "rerun_brand_team.py").read_text()
    assert '"brand_team_dr_joe" in brand_report.get("reviewer_dropped", {})' in tool, "the re-run refuses it"


# ── the verbatim gate ─────────────────────────────────────────────────────────


def test_a_verbatim_quote_is_kept_with_its_tape_check_and_no_phase2_times():
    calls = []
    kept, report = quote_gate.enforce(
        [Q("q01", "rest is not a reward, it is part of the work")], TAPE, "E99", lambda p: calls.append(p) or []
    )
    assert [q["id"] for q in kept] == ["q01"] and calls == [], (kept, calls)
    tc = kept[0]["tape_check"]
    assert tc["verdict"] == "verbatim" and tc["precision"] == "segment" and tc["spoken_ms"] == 10000, tc
    assert "transcript_start_ms" not in kept[0], "Phase 2's word-level lookup skips quotes that have times"


def test_a_rewritten_quote_gets_one_pass_and_comes_back_verbatim():
    bad = Q("q01", "Magnesium is a proven cure, as Dr. Invented describes it")
    seen = {}

    def repair(problems):
        seen.update(problems)
        return [
            Q("q01", "Magnesium helped me a little but it was the walking in the morning that really changed things")
        ]

    kept, report = quote_gate.enforce([bad], TAPE, "E99", repair)
    assert list(seen) == ["q01"] and seen["q01"], seen  # Lisa was told exactly what was wrong
    assert kept[0]["tape_check"]["verdict"] == "verbatim", kept
    assert report["repaired"][0]["id"] == "q01" and report["dropped"] == [], report


def test_still_rewritten_after_the_pass_is_dropped():
    kept, report = quote_gate.enforce(
        [Q("q01", "Magnesium cures insomnia for everyone")],
        TAPE,
        "E99",
        lambda p: [Q("q01", "Magnesium cures insomnia for most people")],
    )
    assert kept == [] and report["dropped"][0]["why"].startswith("still not verbatim"), report


def test_a_quote_lisa_leaves_out_is_dropped():
    kept, report = quote_gate.enforce(
        [Q("q01", "Magnesium cures insomnia"), Q("q02", "rest is not a reward")], TAPE, "E99", lambda p: []
    )
    assert [q["id"] for q in kept] == ["q02"] and report["dropped"][0]["id"] == "q01", report


def test_a_pass_that_does_not_parse_drops_and_never_lets_a_rewrite_through():
    def garbled(problems):
        raise ValueError("Expecting value: line 1 column 1")  # json.JSONDecodeError is a ValueError

    kept, report = quote_gate.enforce([Q("q01", "Magnesium cures insomnia")], TAPE, "E99", garbled)
    assert kept == [] and report["repair_error"].startswith("ValueError"), report
    assert "pass failed" in quote_gate.standup_line("E99", report)


def test_an_api_error_in_the_pass_fails_the_run_instead_of_dropping():
    """A 529 is transient: dropping would lose the quotes for good; raising lets a re-run keep them."""

    def down(problems):
        raise RuntimeError("API 529")

    try:
        quote_gate.enforce([Q("q01", "Magnesium cures insomnia")], TAPE, "E99", down)
    except RuntimeError:
        return
    raise AssertionError("an API error must not be swallowed as a drop")


def test_a_repeated_id_cannot_borrow_another_quotes_placement():
    """Toby's blocker: keyed by id, the second q01's placement stood for both and a rewrite was kept as verbatim."""
    quotes = [
        Q("q01", "Magnesium is a proven cure for everyone"),
        Q("q01", "Magnesium helped me a little but it was the walking in the morning that really changed things"),
    ]
    kept, report = quote_gate.enforce(quotes, TAPE, "E99", lambda p: [])
    assert [q["text"] for q in kept] == [quotes[1]["text"]], kept
    assert len({q["id"] for q in quotes}) == 2 and report["renumbered"], report
    assert report["dropped"][0]["id"] == "q01" and kept[0]["tape_check"]["verdict"] == "verbatim", report


def test_quotes_with_no_id_are_numbered_and_checked_one_by_one():
    quotes = [
        {"speaker": "Jane Invented", "text": "Magnesium cures insomnia", "heading": "H"},
        {"speaker": "Jane Invented", "text": "rest is not a reward, it is part of the work", "heading": "H"},
    ]
    kept, report = quote_gate.enforce(quotes, TAPE, "E99", lambda p: [])
    assert [q["id"] for q in kept] == ["q02"] and report["dropped"][0]["id"] == "q01", report
    assert "ids renumbered" in quote_gate.standup_line("E99", report)


def test_words_in_another_speakers_turn_are_listened_to():
    """Jane's quotes sit under two labels: the code can't tell which is hers, so every one of them is held for a person."""
    quotes = [
        Q("q01", "the whole evening slows down for me"),
        Q("q02", "rest is not a reward, it is part of the work"),
        Q("q03", "and today we talk about sleep"),
    ]
    kept, report = quote_gate.enforce(quotes, TAPE, "E99", lambda p: [])
    verdicts = {q["id"]: q["tape_check"]["verdict"] for q in kept}
    assert verdicts == {"q01": "listen", "q02": "listen", "q03": "listen"}, kept
    assert any("2 labels (SPEAKER_00, SPEAKER_01)" in r for r in kept[2]["tape_check"]["reasons"]), kept[2]


def test_a_name_on_one_label_of_its_own_is_verbatim():
    quotes = [
        Q("q01", "the whole evening slows down for me"),
        Q("q02", "rest is not a reward, it is part of the work"),
        Q("q03", "and today we talk about sleep", speaker="Heather Reilly Hiemstra"),
    ]
    kept, report = quote_gate.enforce(quotes, TAPE, "E99", lambda p: [])
    assert [q["tape_check"]["verdict"] for q in kept] == ["verbatim"] * 3 and not report["one_label"], kept


def test_a_majority_of_host_lines_cannot_set_the_guests_label():
    """Toby's re-review: three host lines given to the guest outvoted her one real line. Now all four are listened to."""
    tape = [
        seg(0, "Welcome back everyone to another hour about menopause and midlife", "SPEAKER_00"),
        seg(10, "Tell us how the first year of symptoms went for you and your family", "SPEAKER_00"),
        seg(20, "We will be right back after a word from our sponsor this week", "SPEAKER_00"),
        seg(30, "The first year I could not sleep and nobody had any answers for me"),
    ]
    quotes = [
        Q("q01", "another hour about menopause and midlife"),
        Q("q02", "how the first year of symptoms went"),
        Q("q03", "right back after a word from our sponsor"),
        Q("q04", "I could not sleep and nobody had any answers"),
    ]
    kept, _ = quote_gate.enforce(quotes, tape, "E99", lambda p: [])
    assert [q["tape_check"]["verdict"] for q in kept] == ["listen"] * 4, kept


def test_one_label_for_everyone_is_named_in_the_standup():
    tape = [{**s, "speaker": "SPEAKER_00"} for s in TAPE]
    quotes = [
        Q("q01", "rest is not a reward, it is part of the work"),
        Q("q02", "and today we talk about sleep", speaker="Heather Reilly Hiemstra"),
    ]
    kept, report = quote_gate.enforce(quotes, tape, "E99", lambda p: [])
    assert {q["tape_check"]["verdict"] for q in kept} == {"listen"} and report["one_label"], report
    assert "one speaker label (diarisation)" in quote_gate.standup_line("E99", report)


def test_the_hosts_words_given_to_the_guest_are_listened_to():
    """Toby's case: a guest quote that's entirely the host's words. Two names on one label: neither passes unheard."""
    quotes = [
        Q("q01", "and today we talk about sleep"),
        Q("q02", "Welcome to the show, I'm Heather", speaker="Heather Reilly Hiemstra"),
    ]
    kept, _ = quote_gate.enforce(quotes, TAPE, "E99", lambda p: [])
    assert {q["tape_check"]["verdict"] for q in kept} == {"listen"}, kept


def test_a_join_is_kept_and_listed_for_a_person():
    kept, report = quote_gate.enforce(
        [Q("q01", "Every night I put the phone in another room. Rest is not a reward, it is part of the work.")],
        TAPE,
        "E99",
        lambda p: [],
    )
    assert kept[0]["tape_check"]["verdict"] == "listen" and report["listen"][0]["id"] == "q01", report


def test_the_hosts_misheard_name_is_not_a_rewrite():
    """The brand team reads a transcript with the host's name made canonical; Round 1 canonicalises the segments too."""
    quote = Q("q01", "I'm Heather Reilly Hiemstra and today we talk about sleep", speaker="Heather Reilly Hiemstra")
    raw, _ = quote_gate.enforce([dict(quote)], TAPE, "E99", lambda p: [])
    assert raw[0]["tape_check"]["verdict"] == "listen", raw  # raw: a tolerated mishear a person would have to clear
    canon = [{**s, "text": summary_norm.canonicalize_host(s["text"])} for s in TAPE]
    kept, report = quote_gate.enforce([dict(quote)], canon, "E99", lambda p: [])
    assert kept[0]["tape_check"]["verdict"] == "verbatim", kept  # canonical: nothing for a person to clear


def test_round1_runs_the_gate_after_the_headings_and_before_the_summary():
    src = (REPO / "rockstar" / "jobs" / "round_1.py").read_text()
    # TITLE-02: the headings are made to pass (quote_title.ensure) before the gate, not just checked for presence.
    headings, gate, summary = (
        src.index("quotes = _ensure_titles(quotes, episode_code)"),
        src.index("quote_gate.check_selected("),
        src.index('run_ledger.step("11", "apple_summary")'),
    )
    assert headings < gate < summary
    assert "quote_gate.enforce(" not in src, "Round 1 goes through check_selected, the one check every picker shares"
    assert gate < src.index("content_gates.assert_guest_featured(quotes", gate), "drops can take every guest quote"
    tree = ast.parse(src)
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise) and "survived the verbatim" in ast.unparse(n)]
    assert raises, "no quote left must stop Round 1"


def test_check_selected_canonicalises_the_tape_and_the_speakers_around_the_gate():
    """The check Round 1 and the re-run tool share: the host's misheard name on the tape isn't a rewrite, and two
    spellings of her name are one person, before the speaker check and after a swap."""
    quote = Q("q01", "I'm Heather Reilly Hiemstra and today we talk about sleep", speaker="Heather Riley Hemstra")
    kept, report, line = quote_gate.check_selected([quote], {"segments": TAPE}, "E99", lambda p: [], None)
    assert kept[0]["tape_check"]["verdict"] == "verbatim", kept
    assert kept[0]["speaker"] == "Heather Reilly Hiemstra" and line.startswith("Quote check (ST-10) for E99"), kept


def test_every_brand_team_step_is_told_to_keep_its_working_out_of_the_reply():
    """E6, 2026-10-12: Lisa wrote her working in prose, hit max_tokens twice, and her step was SKIPPED."""
    assert anthropic_client._BRAND_TEAM_MAX_TOKENS >= 16384
    src = (REPO / "rockstar/clients/anthropic_client.py").read_text()
    assert src.count("{_NO_WORKING}") == 3 and "+ _NO_WORKING" in src, (
        "the chain's two instructions, Joe's second ask, and the restore pass"
    )
    assert "Your reply is the JSON alone" in (REPO / "prompts" / "brand_team_lisa.md").read_text()


def test_a_skipped_reviewer_is_an_anomaly_and_a_standup_line_in_round_1():
    """Toby, 2026-10-12: E6's Lisa skip closed step 10 "ok" with one log line. Now the ledger and the standup say so."""
    src = (REPO / "rockstar" / "jobs" / "round_1.py").read_text()
    block = src[
        src.index('if brand_report.get("skipped"):') : src.index("# TITLE-02: each quote's `heading` is its ONE title")
    ]
    assert 'run_ledger.anomaly("brand team step skipped")' in block and "_prepend_standup(" in block, block


def test_the_prompts_no_longer_ask_for_new_wording():
    joe = (REPO / "prompts" / "brand_team_dr_joe.md").read_text()
    lisa = (REPO / "prompts" / "brand_team_lisa.md").read_text()
    assert "suggested_qualification" not in joe and "updated in place" not in joe
    assert "apply Dr. Joe's suggested qualification to the text" not in lisa
    assert "Apply any text refinements" not in lisa
    assert "never edited" in joe and "never edited" in lisa


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
