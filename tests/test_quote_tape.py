"""
test_quote_tape.py — the one tape check (QUOTE-01 B). Three verdicts: verbatim (cuts only), listen (a join, an unruled
mishear, mixed speakers), not_verbatim (added, replaced, normalised, reordered, stranded, another speaker). Every
transcript here is invented (tests are exported, FIX-01 scope 5).

Calibrated 2026-10-12 on the back catalogue (private): every quote the correction sheet calls REWRITE,
NORMALISED or BRIDGE WORDS fails; mishears pass and are listed. The cases below are those shapes, in made-up words.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))

from rockstar.gates import quote_tape as qt  # noqa: E402


def seg(start, text, speaker="SPEAKER_01"):
    return {"start": start, "end": start + 10, "text": text, "speaker": speaker}


TAPE = [
    seg(0, "Welcome back to the show, it's lovely to have you here with me today.", "SPEAKER_00"),
    seg(10, "So the river finds its way around every stone, you know, um, it always finds a way to the sea."),
    seg(20, "And when we hold the tension in our shoulders the whole body starts to brace for a storm."),
    seg(30, "Really? Tell me more about that.", "SPEAKER_00"),
    seg(40, "Yeah so every morning I walk the dog along the harbour and I count the boats."),
    seg(50, "Fifty percent of the people I see are rushing, they don't even look at the water."),
    seg(300, "Later on I learned that the body keeps a record of every winter we survive."),
    seg(310, "And you have to choose, are you going to keep running or are you going to stop."),
    seg(320, "I'm gonna be honest with you, parent metapause was the hardest stretch for me."),
    seg(330, "She had a death scan and the results were fine."),
    seg(390, "You know what, the pain was gone by the morning."),
    seg(400, "I tried everything and the patches were never safe for me, I can sleep now though."),
    seg(420, "The doctor told me cannabis helps some people and not others and that is the honest truth about it."),
    seg(430, "She was in pain every day and nobody listened to her at all.", "SPEAKER_00"),
    seg(440, "It is not true that warm baths cure every single headache."),
    seg(450, "The cream is not a cure for anything at all."),
    seg(460, "People keep telling me it is not true that"),
    seg(462, "cold showers fix every kind of back pain."),
]


def test_cuts_only_is_verbatim():
    p = qt.place("So the river finds its way around every stone, it always finds a way to the sea.", TAPE)
    assert p.verbatim and p.joins == 0 and not p.mishears, qt.report("t", "s", p)


def test_a_join_across_minutes_is_verbatim():
    """John: joined passages are fine. The second passage is five minutes later."""
    p = qt.place(
        "Every morning I walk the dog along the harbour. The body keeps a record of every winter we survive.", TAPE
    )
    assert p.verdict == "listen" and p.joins == 1 and not p.reordered, qt.report("t", "s", p)  # a person hears the join


def test_an_inserted_attribution_fails():
    """A rewrite's shape: '…, as Dr. X describes it, …' put inside the speaker's own words."""
    p = qt.place(
        "And when we hold the tension in our shoulders, as Dr. Invented describes it, the whole body starts "
        "to brace for a storm.",
        TAPE,
    )
    assert not p.verbatim, qt.report("t", "s", p)
    bad = " ".join(b for _, b, _ in p.replaced) + " ".join(p.added)
    assert "describes it" in bad, qt.report("t", "s", p)


def test_a_replacement_sentence_fails():
    """A replacement's shape: a reviewer's sentence instead of the speaker's."""
    p = qt.place(
        "Every morning I walk the dog along the harbour. Muscular tension can signal the nervous system to "
        "prepare for threat.",
        TAPE,
    )
    assert not p.verbatim and (p.added or p.replaced), qt.report("t", "s", p)


def test_bridge_words_between_joined_passages_fail():
    """A join's shape: joining is fine, but words added to make the join read are not."""
    p = qt.place(
        "Every morning I walk the dog along the harbour, and they told me the body keeps a record of every "
        "winter we survive.",
        TAPE,
    )
    assert not p.verbatim, qt.report("t", "s", p)


def test_a_contraction_expanded_is_normalised_and_fails():
    """The sheet's NORMALISED: 'don't' -> 'do not' is restored to the spoken form."""
    p = qt.place("Fifty percent of the people I see are rushing, they do not even look at the water.", TAPE)
    assert not p.verbatim and [k for _, _, k in p.replaced] == ["normalised"], qt.report("t", "s", p)
    p = qt.place("I'm going to be honest with you", TAPE)
    assert not p.verbatim and p.replaced and p.replaced[0][2] == "normalised", qt.report("t", "s", p)


def test_mishears_are_tolerated_and_listed():
    """The fast transcript mishears: 'shoes' for 'to choose', 'parent metapause' for 'perimenopause'."""
    p = qt.place("Every morning I walk the dog along the harbor and I count the boats.", TAPE)
    assert p.verdict == "listen" and p.mishears == [("harbour", "harbor", False)], qt.report("t", "s", p)  # unruled
    p = qt.place("And you have to chews, are you going to keep running or are you going to stop.", TAPE)
    assert p.verdict == "not_verbatim", qt.report("t", "s", p)  # short words: too few letters shared to pass unheard
    p = qt.place("I'm gonna be honest with you, perimenopause was the hardest stretch for me.", TAPE)
    assert p.verdict == "listen" and p.mishears == [("parent metapause", "perimenopause", False)], qt.report(
        "t", "s", p
    )
    p = qt.place(
        "I'm gonna be honest with you, perimenopause was the hardest stretch for me.",
        TAPE,
        ear_corrections=[("parent metapause", "perimenopause")],
    )
    assert p.verdict == "verbatim", qt.report("t", "s", p)  # ruled on by ear


def test_a_mishear_too_far_apart_needs_an_ear_correction():
    p = qt.place("She had a DEXA scan and the results were fine.", TAPE)
    assert not p.verbatim, qt.report("t", "s", p)
    p = qt.place("She had a DEXA scan and the results were fine.", TAPE, ear_corrections=[("death", "DEXA")])
    assert p.verbatim and p.mishears == [("death", "dexa", True)], qt.report("t", "s", p)


def test_numbers_read_the_same_as_words():
    assert qt.place("50% of the people I see are rushing", TAPE).verbatim
    assert qt.words("twenty five") == qt.words("25") == ["25"]
    assert qt.words("one hundred") == qt.words("100") and qt.words("1,000") == qt.words("one thousand") == ["1000"]
    assert qt.words("two thousand") == ["2000"] and qt.words("per cent") == qt.words("percent")


def test_a_common_word_further_on_is_not_a_cut():
    """The calibration bug: a one-word fragment jumped ahead to an unrelated 'the' and threw the rest out of context."""
    p = qt.place("So the river finds its way to the sea.", TAPE)
    assert all(r.tape_start < 40 for r in p.runs), [(r.tape_start, r.length) for r in p.runs]


def test_two_speakers_under_one_quote_is_reported_mixed():
    p = qt.place("It's lovely to have you here with me today. So the river finds its way around every stone.", TAPE)
    assert p.verdict == "listen" and p.mixed_speakers and p.speakers == ["SPEAKER_00", "SPEAKER_01"], qt.report(
        "t", "s", p
    )


def test_a_quote_that_is_not_on_the_tape_is_not_found():
    p = qt.place("Nothing in this sentence was ever said by anyone here.", TAPE)
    assert not p.found and not p.verbatim and "NOT FOUND" in qt.report("q09", "Jane Invented", p)[0]


def test_placement_gives_the_quote_its_times():
    """CLIP-01c reads the quote's window from the same placement; a join counts only the audio it uses."""
    p = qt.place("Every morning I walk the dog along the harbour and I count the boats.", TAPE)
    assert (p.start_ms, p.end_ms, p.spoken_ms) == (40000, 50000, 10000), (p.start_ms, p.end_ms, p.spoken_ms)
    p = qt.place(
        "Every morning I walk the dog along the harbour. The body keeps a record of every winter we survive.", TAPE
    )
    assert p.spoken_ms == 20000 and p.end_ms - p.start_ms == 270000, p.spoken_ms  # two segments, not five minutes


# ── Toby's attacks (cold review, 2026-10-12): each passed the first version as verbatim ──


def test_a_polarity_flip_is_never_a_mishear():
    for quote in (
        "the patches were ever safe for me",
        "the patches were never unsafe for me",
        "I tried everything and the patches were never safe for me, I can't sleep now though",
    ):
        p = qt.place(quote, TAPE)
        assert p.verdict == "not_verbatim" and not p.mishears, (quote, qt.report("t", "s", p))


def test_a_stock_phrase_from_elsewhere_is_stranded_not_joined():
    p = qt.place("I tried everything and you know what, the patches were never safe for me.", TAPE)
    # The phrase sits 10 s earlier on the tape: whichever piece reads as the odd one out, it's out of place.
    assert p.verdict == "not_verbatim" and (p.stranded or p.reordered), qt.report("t", "s", p)
    far = qt.place("Every morning I walk the dog, the pain was gone, along the harbour and I count the boats.", TAPE)
    assert far.verdict == "not_verbatim" and far.stranded == ["the pain was gone"], qt.report("t", "s", far)


def test_reordered_sentences_fail():
    p = qt.place(
        "The doctor told me cannabis helps some people and not others. Every morning I walk the dog along the "
        "harbour and I count the boats.",
        TAPE,
    )
    assert p.verdict == "not_verbatim" and p.reordered, qt.report("t", "s", p)


def test_a_long_cut_is_a_join_not_an_insertion():
    p = qt.place("So the river finds its way to the sea.", TAPE)  # 11 tape words cut: a person hears it, nothing added
    assert p.verdict == "listen" and p.joins == 1 and not p.stranded, qt.report("t", "s", p)


def test_a_long_cut_into_another_speakers_words_fails_with_the_speaker_named():
    """'The doctor told me cannabis …' + 'She was in pain every day' (the host's): a cut across 13 words and a turn."""
    quote = "The doctor told me cannabis helps some people. She was in pain every day."
    p = qt.place(quote, TAPE)
    assert p.verdict == "listen" and p.joins == 1 and p.mixed_speakers, qt.report("t", "s", p)
    p = qt.place(quote, TAPE, speaker="SPEAKER_01")
    assert p.verdict == "not_verbatim" and p.off_speaker, qt.report("t", "s", p)


def test_a_quote_wholly_in_the_hosts_turn_fails_for_a_guest():
    p = qt.place("She was in pain every day and nobody listened to her at all.", TAPE, speaker="SPEAKER_01")
    assert p.verdict == "not_verbatim" and p.off_speaker, qt.report("t", "s", p)


def test_a_cut_that_drops_a_negation_is_heard_not_passed():
    """Toby's re-review: cuts are allowed, but cutting 'not' inverts the meaning."""
    for quote in (
        "The cream is a cure for anything at all.",  # 'not' cut from the middle
        "Warm baths cure every single headache.",
    ):  # 'It is not true that' cut from the front
        p = qt.place(quote, TAPE)
        assert p.verdict == "listen" and "not" in p.cut_negations, (quote, qt.report("t", "s", p))
    assert qt.place("So the river finds its way around every stone, it always finds a way to the sea.", TAPE).verbatim
    assert qt.place("50% of the people I see are rushing", TAPE).verbatim  # the next clause's "don't" isn't a cut
    p = qt.place("Cold showers fix every kind of back pain.", TAPE)  # the "not" is in the segment before
    assert p.verdict == "listen" and "not" in p.cut_negations, qt.report("t", "s", p)


def test_a_short_whole_quote_is_heard_not_passed():
    p = qt.place("Really?", TAPE)
    assert p.verdict == "listen" and p.short_whole, qt.report("t", "s", p)
    p = qt.place("Really?", TAPE, speaker="SPEAKER_01")  # the host said it
    assert p.verdict == "not_verbatim" and p.off_speaker, qt.report("t", "s", p)


def test_reasons_put_failures_first():
    p = qt.place("Every morning I walk the dog, the pain was gone, along the harbour and I count the boats.", TAPE)
    assert p.reasons[0].startswith("stranded"), p.reasons


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
