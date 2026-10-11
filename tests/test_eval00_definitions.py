"""
test_eval00_definitions.py — EVAL-00: the eval's definitions, pinned by golden cases before any number exists.

Run: python3 -B test_eval00_definitions.py   (plain asserts, no pytest needed)

Acceptance criteria → tests:
  AC-1 the human-edit metric: identical = 0; a no-edit round trip through the normalizer = 0; one word changed in N
       = 1/N exactly; a full rewrite ≥ 0.9 and flagged; whitespace-only = 0      → test_metric_*
  AC-2 the comparison rule: a known effect's CI excludes 0, no effect's CI contains 0, deterministic → test_bootstrap_*
  AC-3 baseline.json: every field required, anchors never scored                → test_baseline_*
  AC-4 the test strategy exists and names what is NOT tested                     → test_strategy_doc
  AC-5 calibration: n, raw agreement, Wilson CI, kappa; "advisory — n < 30"     → test_calibration_*
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))

from evals import baseline, metric, stats  # noqa: E402

TEXT = "Heather sits down with Linnea Ashgrovenly to talk about the apothecary in your yard and going rogue."


def test_metric_identical_and_round_trip_are_zero():
    assert metric.human_edit(TEXT, TEXT)["share"] == 0.0
    clean = "## Title\n\nFirst line.\nSecond line."
    exported = "## Title   \r\n\r\n\r\n\r\nFirst line.  \r\nSecond line.\r\n"  # CRLF, trailing spaces, blank runs
    r = metric.human_edit(clean, exported)
    assert r["share"] == 0.0 and r["char_share"] == 0.0, f"a no-edit export round trip is 0 at BOTH levels: {r}"
    assert metric.human_edit(TEXT, TEXT.replace(" ", "  "))["share"] == 0.0, "whitespace-only is 0 at word level"


def test_metric_nfc():
    nfc, nfd = "Caf\u00e9 cr\u00e8me", "Cafe\u0301 cre\u0300me"  # the same words, composed vs decomposed
    r = metric.human_edit(nfc, nfd)
    assert r["share"] == 0.0 and r["char_share"] == 0.0, f"Unicode form is not an edit: {r}"


def test_metric_char_share_golden():
    assert metric.human_edit("abcd", "abXd")["char_share"] == 0.25
    assert metric.human_edit("one two", "one  two")["char_share"] == round(1 / 8, 4), \
        "a whitespace-only change inside a line is 0 at word level but not at character level (documented)"


def test_metric_empty_sides():
    assert metric.human_edit("", "")["share"] == 0.0
    r = metric.human_edit("", TEXT)
    assert r["share"] == 1.0 and r["rewrite"], "an empty draft fully written by a person is a rewrite"
    assert metric.human_edit(TEXT, "")["share"] == 1.0


def test_metric_one_word_in_n_is_exactly_one_over_n():
    words = TEXT.split()
    edited = " ".join(["Joanna" if w == "Linnea" else w for w in words])
    r = metric.human_edit(TEXT, edited)
    assert r["words_edited"] == 1 and r["share"] == round(1 / len(words), 4), r
    assert not r["rewrite"]


def test_metric_full_rewrite_saturates_and_is_flagged():
    r = metric.human_edit(TEXT, "Completely different words written by a person from scratch here today now.")
    assert r["share"] >= 0.9 and r["rewrite"], r
    # the flag agrees with the reported (rounded) share at the boundary: 17999 of 20000 = 0.89995 → reported 0.9.
    # The distance is stubbed: the point is the rounding, and 20000-word edit distance is minutes of CPU.
    real = metric._levenshtein
    metric._levenshtein = lambda a, b: 17999 if len(a) == 20000 else real(a, b)
    try:
        words = " ".join(["w"] * 20000)
        r = metric.human_edit(words, words)
    finally:
        metric._levenshtein = real
    assert r["share"] == 0.9 and r["rewrite"] is True, r


def test_metric_markdown_markers_count():
    r = metric.human_edit("## A heading\n" + TEXT, "### A heading\n" + TEXT)
    assert r["words_edited"] == 1, "changing a heading level is an edit"


def test_metric_reviewed_is_carried_not_inferred():
    assert metric.human_edit(TEXT, TEXT, reviewed=False)["reviewed"] is False
    assert metric.human_edit(TEXT, TEXT)["reviewed"] is None, "unknown stays unknown"


def test_bootstrap_known_effect_and_no_effect():
    eps = [f"E{n}" for n in range(1, 21)]
    base = {e: [0.30 + 0.01 * (i % 5)] * 3 for i, e in enumerate(eps)}
    better = {e: [v[0] - 0.10] * 3 for e, v in base.items()}
    noise = {e: [v[0] + (0.02 if i % 2 else -0.02)] * 3 for i, (e, v) in enumerate(base.items())}
    r = stats.paired_bootstrap(base, better, lower_is_better=True)
    assert r["verdict"] == "candidate better" and r["ci95"][1] < 0 and r["metric_direction"] == "lower is better", r
    assert stats.paired_bootstrap(base, better, lower_is_better=False)["verdict"] == "candidate worse"
    r = stats.paired_bootstrap(base, noise, lower_is_better=True)
    assert r["verdict"] == "no detectable difference" and r["ci95"][0] <= 0 <= r["ci95"][1], r
    assert stats.paired_bootstrap(base, noise, lower_is_better=True) == r, "a fixed seed makes it deterministic"


def test_bootstrap_null_coverage_is_at_least_nominal():
    import random
    rng, trials, hits = random.Random(7), 200, 0
    for k in range(trials):
        base = {f"E{i}": [rng.gauss(0.3, 0.1)] for i in range(20)}
        cand = {e: [v[0] + rng.gauss(0, 0.05)] for e, v in base.items()}
        hits += stats.paired_bootstrap(base, cand, lower_is_better=True, resamples=1000, seed=k)["verdict"] == "no detectable difference"
    assert hits / trials >= 0.94, f"with no real effect, 'no detectable difference' {hits}/{trials} — luck would pass as a win"


def test_verdict_interval_covers_both_and_t_table():
    import random
    rng = random.Random(3)
    base = {f"E{i}": [rng.gauss(0.3, 0.1)] for i in range(20)}
    cand = {e: [v[0] + rng.gauss(0.01, 0.05)] for e, v in base.items()}
    r = stats.paired_bootstrap(base, cand, lower_is_better=True, resamples=2000)
    lo, hi = r["ci95"]
    for name in ("ci95_t", "ci95_bootstrap"):
        assert lo <= r[name][0] and r[name][1] <= hi, f"the verdict interval must contain {name}: {r}"
    # spot-check the t table against published values, and conservativeness everywhere
    for df, want in ((4, 2.776), (19, 2.093), (29, 2.045)):
        assert stats._t975(df) == want, (df, stats._t975(df))
    assert stats._t975(45) == 2.021 >= 2.014, "between rows, the lower df's (wider) value"
    assert stats._t975(200) == 1.980 > 1.972, "beyond 120, still conservative (z = 1.960 would not be)"


def test_bootstrap_under_ten_episodes_is_advisory():
    base = {f"E{i}": [0.5] for i in range(5)}
    cand = {e: [0.1] for e in base}
    assert stats.paired_bootstrap(base, cand, lower_is_better=True)["verdict"].startswith("advisory")


def test_bootstrap_pairs_only_shared_episodes():
    r = stats.paired_bootstrap({"E1": [0.1], "E2": [0.2], "E3": [0.3], "E4": [0.4]},
                               {"E2": [0.2], "E3": [0.3], "E4": [], "E9": [0.9]}, lower_is_better=True)
    assert r["n_episodes"] == 2, "only shared episodes WITH runs count"


def test_baseline_names_every_missing_field_and_refuses_leakage():
    problems = baseline.validate({})
    assert len(problems) == len(baseline.REQUIRED) and all(p.startswith("missing: ") for p in problems)
    full = {k: "x" for k in baseline.REQUIRED}
    full.update(response_models=["claude-sonnet-4-6"], runs_per_episode=3)
    full.update(prompt_sha={"title": "a"}, request_template_sha={"title": "b"},
                anchor_episodes=["E22", "E27"], scored_episodes=["E13", "E27"])
    assert baseline.validate(full) == ["anchors scored as items (leakage): ['E27']"]
    full["scored_episodes"] = ["E13"]
    full["runs_per_episode"] = 3
    assert baseline.validate(full) == [], baseline.validate(full)
    for bad, want in ((dict(anchor_episodes="E22", scored_episodes="E2"), "must be a list"),
                      (dict(runs_per_episode=True), "positive integer"),
                      (dict(runs_per_episode="three"), "positive integer"), (dict(model=""), "model must be"),
                      (dict(scored_episodes=[]), "baseline over nothing")):
        probs = baseline.validate({**full, **bad})
        assert any(want in p for p in probs), (bad, probs)


def test_calibration_numbers_from_jens_review():
    lo, hi = stats.wilson(8, 10)
    assert (round(lo, 2), round(hi, 2)) == (0.49, 0.94), "8 of 10 → about 0.49–0.94 (Jen, EVAL-01 review)"
    # a classic kappa example: 20 items, observed 0.7, expected 0.5 → 0.4
    a = ["y"] * 10 + ["n"] * 10
    b = ["y"] * 7 + ["n"] * 3 + ["y"] * 3 + ["n"] * 7
    assert stats.cohen_kappa(a, b) == 0.4
    assert stats.cohen_kappa(["y"] * 5, ["y"] * 5) is None, "undefined when everything is one category"


def test_stats_refuse_bad_input():
    for fn in (lambda: stats.wilson(11, 10), lambda: stats.wilson(1, 0), lambda: stats.calibration_report([], []),
               lambda: stats.calibration_report(["y"], ["y", "n"])):
        try:
            fn()
        except ValueError:
            continue
        raise AssertionError("bad input must raise ValueError, named")


def test_calibration_report_is_advisory_below_30():
    r = stats.calibration_report(["y", "n"] * 5, ["y", "n"] * 4 + ["n", "y"])
    assert r["n"] == 10 and r["status"] == "advisory — n < 30" and "wilson95" in r and "kappa" in r
    r = stats.calibration_report(["y"] * 30, ["y"] * 30)
    assert r["status"] == "may gate" and r["kappa"] is None


def test_strategy_doc():
    doc = (REPO / "docs" / "eval" / "test-strategy.md").read_text()
    for must in ("Not tested", "real API", "Drive", "judge", "cassette", "charts", "Signed"):
        assert must in doc, f"test strategy lacks: {must}"


def main() -> int:
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
