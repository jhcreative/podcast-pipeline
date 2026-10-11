"""
test_card01_cards.py — CARD-01 quote-source normalization and split invariants.

Run: python3 test_card01_cards.py   (plain asserts, no pytest needed)

Covers CARD-01 acceptance criteria:
  AC3 flow-don't-shrink, verbatim, no orphan → test_split_*
  schema drift across quotes.json shapes     → test_source_*
  typographic normalization before layout    → test_typographic_*

The split tests drive real Chromium through render_cards, because the browser is
the authority on what fits — that is the whole point of the design, and a test that
mocked the measurement would prove nothing. They take a few seconds. If playwright
is absent they SKIP LOUDLY rather than passing quietly; a skipped fit test that
looks green is exactly the class of failure REEL-05 was about.

Fixtures are inline: Episodes/ is gitignored, so a test that reads real artifacts
would silently stop running for the next dev.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE / "quote_cards"))

import quote_source  # noqa: E402


# ── fixtures: the two schema generations that exist in the wild ────────────────

# E27+ shape.
NEW_SCHEMA = {"quotes": [
    {"id": "q01", "speaker": "Dr. Thessaly Brindlequist", "text": "Normal blood work "
     "does not always mean optimum health.", "transcript_start_ms": 1000},
    {"id": "q02", "speaker": "Dr. Thessaly Brindlequist", "text": "Do not wait."},
]}

# E23–E25 shape: no id, no text.
OLD_SCHEMA = {"quotes": [
    {"index": 1, "speaker": "Juniper Larkspirelle", "quote_text_clean": "Plants are the "
     "oldest medicine we have.", "topic_slug": "plants"},
    {"index": 2, "speaker": "Juniper Larkspirelle", "quote_text_clean": "Start where you are."},
]}


def _write(tmp, payload):
    d = Path(tmp) / "E99-fixture"
    d.mkdir(parents=True, exist_ok=True)
    (d / "quotes.json").write_text(json.dumps(payload))
    return str(d)


# ── quote_source: schema drift (ADR-0002's rule) ───────────────────────────────


def test_source_new_schema():
    with tempfile.TemporaryDirectory() as tmp:
        qs = quote_source.load_quotes(_write(tmp, NEW_SCHEMA))
    assert [q["id"] for q in qs] == ["q01", "q02"]
    assert qs[0]["text"].startswith("Normal blood work")
    assert qs[0]["speaker"] == "Dr. Thessaly Brindlequist"
    print("  ok  source: new schema (id + text)")


def test_source_old_schema():
    """Old episodes carry index + quote_text_clean. Before quote_source, the
    renderer raised KeyError partway through writing a set — every back-catalogue
    episode would have failed."""
    with tempfile.TemporaryDirectory() as tmp:
        qs = quote_source.load_quotes(_write(tmp, OLD_SCHEMA))
    assert [q["id"] for q in qs] == ["q01", "q02"], [q["id"] for q in qs]
    assert qs[0]["text"] == "Plants are the oldest medicine we have."
    assert qs[1]["speaker"] == "Juniper Larkspirelle"
    print("  ok  source: old schema (index + quote_text_clean) → same normal form")


def test_source_bare_list():
    with tempfile.TemporaryDirectory() as tmp:
        qs = quote_source.load_quotes(_write(tmp, NEW_SCHEMA["quotes"]))
    assert len(qs) == 2
    print("  ok  source: a bare list, with no 'quotes' wrapper, still reads")


def _expect_exit(fn, needle):
    try:
        fn()
    except SystemExit as e:
        assert needle.lower() in str(e).lower(), f"wrong reason: {e}"
        return
    raise AssertionError(f"expected a hard failure mentioning {needle!r}")


def test_source_rejects_missing_text():
    """Refuse the file rather than render four cards out of five."""
    bad = {"quotes": [{"id": "q01", "speaker": "X"}]}
    with tempfile.TemporaryDirectory() as tmp:
        _expect_exit(lambda: quote_source.load_quotes(_write(tmp, bad)), "quote_text_clean")
    print("  ok  source: a quote with no recoverable text is a hard failure")


def test_source_rejects_missing_speaker():
    """The card and the caption both attribute it; this cannot be guessed."""
    bad = {"quotes": [{"id": "q01", "text": "Something said aloud."}]}
    with tempfile.TemporaryDirectory() as tmp:
        _expect_exit(lambda: quote_source.load_quotes(_write(tmp, bad)), "speaker")
    print("  ok  source: a quote with no speaker is a hard failure")


def test_source_rejects_duplicate_ids():
    """Duplicate ids would collide in the output filenames, silently overwriting."""
    bad = {"quotes": [{"id": "q01", "speaker": "X", "text": "One."},
                      {"id": "q01", "speaker": "X", "text": "Two."}]}
    with tempfile.TemporaryDirectory() as tmp:
        _expect_exit(lambda: quote_source.load_quotes(_write(tmp, bad)), "duplicate")
    print("  ok  source: duplicate ids are a hard failure, not a silent overwrite")


# ── typographic normalization ──────────────────────────────────────────────────


def test_typographic_apostrophes():
    import render_cards as rc
    got = rc._typographic("You're the smartest doctor. It doesn't matter.")
    assert "’" in got and "'" not in got, got
    print("  ok  typographic: straight apostrophes become typographic marks")


def test_typographic_paired_quotes():
    import render_cards as rc
    got = rc._typographic('She said "no" firmly.')
    assert "“no”" in got, got
    print("  ok  typographic: double quotes pair as open/close")


def test_rehang_dash():
    """A slide must not end on a dangling em dash; the dash belongs to the thought
    it introduces. Concatenation is unchanged, so verbatim still holds."""
    import render_cards as rc
    parts = rc._rehang_dash(["labs fall in that range —", "but optimum health differs"])
    assert parts[0].endswith("range"), parts[0]
    assert parts[1].startswith("— but"), parts[1]
    assert " ".join(parts) == "labs fall in that range — but optimum health differs"
    print("  ok  rehang: trailing em dash moves to the next slide, text unchanged")


# ── split invariants, measured in a real browser ───────────────────────────────


def _fitter(page, mark="watermark"):
    import render_cards as rc
    return rc.Fitter(page, "B", "Dr. Thessaly Brindlequist", mark)


def _with_page(fn):
    from playwright.sync_api import sync_playwright
    import card_format as fmt
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": fmt.CANVAS, "height": fmt.CANVAS},
                          device_scale_factor=1)
        try:
            return fn(page)
        finally:
            b.close()


LONG = ("Normal blood work does not always mean optimum health. So many women come in "
        "and by the time they find me, they've seen about five different specialists. "
        "Many women are told that everything is fine because their labs fall within that "
        "broad reference range — but optimum health requires looking more closely at your "
        "hormones, your thyroid, your insulin, your inflammation, your nutrients, and your "
        "metabolic markers. Our body functions as a whole entire unit.")


def test_split_is_verbatim_and_fits():
    """The two guarantees together: nothing is lost, and every slide actually fits.
    Fixed type means the only remedy for overflow is another slide."""
    def run(page):
        f = _fitter(page)
        slides = f.split(LONG)
        assert len(slides) > 1, "this quote should need more than one slide"
        joined = " ".join(s.strip() for s in slides)
        assert " ".join(joined.split()) == " ".join(LONG.split()), joined
        for i, s in enumerate(slides, 1):
            assert f.fits(s), f"slide {i} does not fit"
        return len(slides)
    n = _with_page(run)
    print(f"  ok  split: {n} slides, concatenation verbatim, every slide fits")


# E29 q03: greedy packing strands the closing fragment "It's way beyond that."
# on its own slide. This is the shape rebalancing exists for.
# Re-tuned for IBM Plex (ADR-0015): under Plex the old E29 fixture no longer strands a
# remainder. E25 q04 (Juniper Larkspirelle) does: greedy leaves its last slide ~11% full;
# rebalancing lifts it to ~45% at the same slide count.
ORPHANING = "When you work with these ancient plant spirits who have been around since the beginning of time, they're like: remember, you used to be a part of nature. You used to operate at the pace of a tree. You didn't have to go, go, go. All you have to do is just exist and be yourself. There's no one else like you."


def test_rebalance_fixes_a_stranded_remainder():
    """Greedy fills each slide to the brim and dumps what's left on the last one.
    Rebalancing pushes units backward at CONSTANT slide count — nothing is shrunk
    to achieve it."""
    def run(page):
        f = _fitter(page)
        units = f._explode_overlong(__import__("render_cards")._SENT.split(
            __import__("render_cards")._typographic(ORPHANING).strip()))
        greedy, cur = [], []
        for u in units:
            if not cur or f.fits(" ".join(cur + [u])):
                cur.append(u)
            else:
                greedy.append(cur)
                cur = [u]
        if cur:
            greedy.append(cur)
        before = min(f.fill(" ".join(g)) for g in greedy)
        after_groups = f._rebalance([list(g) for g in greedy])
        after = min(f.fill(" ".join(g)) for g in after_groups)
        assert len(after_groups) == len(greedy), "rebalance changed the slide count"
        assert after > before, f"rebalance did not improve: {before:.2f} → {after:.2f}"
        return before, after
    before, after = _with_page(run)
    print(f"  ok  rebalance: emptiest slide {before:.0%} → {after:.0%}, same slide count")


def test_thin_final_slide_is_allowed_when_it_is_a_whole_sentence():
    """A short last slide is NOT automatically a defect. When one sentence alone
    nearly fills a column, no order-preserving arrangement can even it out, and
    breaking that sentence at a clause would put a fragment on screen where a clean
    sentence boundary exists. Fragments are orphans; complete sentences are not.

    LONG is exactly this case: units measure ~[.22 .33 .89 .22] (Plex, ADR-0015) and both u2+u3 and
    u3+u4 overflow."""
    def run(page):
        f = _fitter(page)
        slides = f.split(LONG)
        return slides, [f.fill(s) for s in slides]
    slides, fills = _with_page(run)
    assert min(fills) < 0.3, "fixture no longer exercises the thin-slide case"
    for i, s in enumerate(slides, 1):
        assert s.rstrip().endswith((".", "!", "?", "…")), f"slide {i} ends mid-sentence: {s[-40:]!r}"
    print(f"  ok  thin slide: fills {[f'{x:.0%}' for x in fills]}, every slide ends "
          f"on a complete sentence")


def test_short_quote_stays_one_slide():
    def run(page):
        f = _fitter(page)
        return f.split("Do not wait until your period stops to pay attention to your "
                       "symptoms. You live in your body.")
    slides = _with_page(run)
    assert len(slides) == 1, slides
    print("  ok  split: a short quote is not split at canon settings")


# ── runner ─────────────────────────────────────────────────────────────────────


def main() -> int:
    pure = [
        test_source_new_schema,
        test_source_old_schema,
        test_source_bare_list,
        test_source_rejects_missing_text,
        test_source_rejects_missing_speaker,
        test_source_rejects_duplicate_ids,
        test_typographic_apostrophes,
        test_typographic_paired_quotes,
        test_rehang_dash,
    ]
    browser = [
        test_split_is_verbatim_and_fits,
        test_rebalance_fixes_a_stranded_remainder,
        test_thin_final_slide_is_allowed_when_it_is_a_whole_sentence,
        test_short_quote_stays_one_slide,
    ]
    try:
        import playwright  # noqa: F401
        tests = pure + browser
    except ImportError:
        tests = pure
        print("!! playwright absent — SKIPPING 4 split tests. These are the fit "
              "guarantees; this run does NOT cover them.")

    failed = 0
    print("CARD-01 quote source + split invariants — test run")
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
