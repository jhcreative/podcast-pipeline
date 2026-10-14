"""
test_clip01c_ceiling.py — CLIP-01c: a quote fits its reel and the box, or it's cut, swapped or dropped before money is spent.

- The ceilings are derived (render_reel.js, extract_quote_clips.sh, verify_reel_spec.js, box-limits.env), never typed.
- No limits file, no run: Round 1, Phase 2 and transcribe_clips.sh refuse.
- Phase 1: an over-ceiling quote joins Lisa's ONE pass; still over → dropped. A long_form quote needs her reason.
- Phase 2: the same limit before faq_blog and alignment, after alignment, and on each clip before WhisperX.
Every transcript here is invented words (tests are exported). The model is a fake; the gate is real.
"""

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOX_LIMITS_FILE", str(REPO / "tests" / "fixtures" / "box-limits.env"))
os.environ.setdefault("BOX_LIMITS_TEST", "1")
os.environ["RUN_LOG_PATH"] = str(Path(tempfile.mkdtemp(prefix="clip01c-")) / "run_log.jsonl")

from rockstar.gates import (
    clip_limits,  # noqa: E402
    quote_gate,  # noqa: E402
)

BRAND = REPO / "BrandAssets"
LIMITS = clip_limits.load(brand_assets=BRAND)

# An invented tape: 10 segments of 25 s, 20 made-up words each, all one guest.
_WORDS = [
    "".join(p)
    for p in itertools.product(
        ["ba", "ko", "mi", "ru", "te", "lo"],
        ["san", "vek", "dor", "pil"],
        ["a", "o", "u", "i", "e", "y", "en", "ar", "is"],
    )
]
SEGS = [
    {"start": 25.0 * i, "end": 25.0 * (i + 1), "speaker": "SPEAKER_01", "text": " ".join(_WORDS[20 * i : 20 * (i + 1)])}
    for i in range(10)
]


def words_of(first: int, last: int) -> str:
    return " ".join(s["text"] for s in SEGS[first : last + 1])


def Q(qid, text, **kw):
    return {"id": qid, "speaker": "Jane Invented", "text": text, "heading": "A Heading", **kw}


REASON = "The whole arc is the point: cut anywhere and the turn at the end lands on nothing."


def tc(seconds):
    return {"tape_check": {"start_ms": 0, "end_ms": int(seconds * 1000)}}


# ── the ceilings are derived ──────────────────────────────────────────────────


_BPM = (
    json.loads((BRAND / "audio" / "theme_bpm.json").read_text())
    if (BRAND / "audio" / "theme_bpm.json").exists()
    else None
)
_BAR = 4 * _BPM["beat_period_sec"] if _BPM else 0.0  # the closing snaps up to one bar; no grid, no snap


def test_the_ceilings_come_from_the_code_that_uses_them():
    assert clip_limits.reel_caps() == (90, 180) == (LIMITS.reel_max_s, LIMITS.long_reel_max_s)  # verify_reel_spec.js
    assert clip_limits.clip_padding_sec() == 2.5  # extract_quote_clips.sh
    assert abs(LIMITS.frame_s - (2.5 + 1.5 + 0.5 + 5.5 + _BAR)) < 1e-6, LIMITS  # render_reel.js + one bar
    floor = lambda x: int(x // 1)  # noqa: E731
    assert LIMITS.standard_s == floor(90 - LIMITS.frame_s - clip_limits.SPAN_SHORT_SEC), LIMITS
    assert LIMITS.long_s == floor(180 - LIMITS.frame_s - clip_limits.SPAN_SHORT_SEC) and LIMITS.clip_max_s == 180, (
        LIMITS
    )
    if _BPM and abs(_BPM["beat_period_sec"] - 0.554977) < 1e-6:  # the show's own theme (private)
        assert (LIMITS.standard_s, LIMITS.long_s) == (76, 166), LIMITS


def test_a_longer_opener_lowers_the_ceiling_on_its_own():
    d = Path(tempfile.mkdtemp())
    js = d / "render_reel.js"
    js.write_text(
        (REPO / "reels" / "render_reel.js").read_text().replace("openingDurationSec: 2.5", "openingDurationSec: 4.5")
    )
    assert abs(clip_limits.reel_frame_sec(js, BRAND) - LIMITS.frame_s - 2.0) < 1e-6


def test_a_quote_at_the_max_passes_both_phases_and_half_a_second_more_is_refused():
    at_max = {"id": "q01", **tc(LIMITS.standard_s)}
    assert clip_limits.before_spend([at_max], LIMITS) == ([], [])
    # Its aligned window can read up to SPAN_SHORT_SEC longer than Phase 1's span; the reel still fits 90 s.
    window = LIMITS.standard_s + clip_limits.SPAN_SHORT_SEC
    at_max.update(transcript_start_ms=0, transcript_end_ms=int(window * 1000))
    assert clip_limits.after_alignment([at_max], LIMITS) == []
    assert window + LIMITS.frame_s <= LIMITS.reel_max_s
    over, _ = clip_limits.before_spend([{"id": "q02", **tc(LIMITS.standard_s + 0.5)}], LIMITS)
    assert over and over[0].startswith("q02"), over


# ── no limits file, no guess (AC 3) ───────────────────────────────────────────


def test_no_limits_file_refuses_and_says_how_to_make_one():
    for content in (None, "CLIP_MAX_SECONDS=0\nCLIP_BATCH_SIZE=2\n", "CLIP_BATCH_SIZE=2\n"):
        d = Path(tempfile.mkdtemp())
        if content is not None:
            (d / "box-limits.env").write_text(content)
        try:
            clip_limits.load(state_dir=d, brand_assets=BRAND)
        except clip_limits.LimitsMissing as exc:
            assert "measure_clip_limit.sh" in str(exc) and "box-limits.env" in str(exc), exc
            continue
        raise AssertionError(f"no refusal for {content!r}")


def test_a_stray_limits_variable_cannot_redirect_production():
    d = Path(tempfile.mkdtemp())
    saved = os.environ.pop("BOX_LIMITS_TEST")
    try:
        os.environ["STATE_DIR"] = str(d)
        assert clip_limits.limits_path() == d / "box-limits.env"  # BOX_LIMITS_FILE set, ignored without the flag
    finally:
        os.environ["BOX_LIMITS_TEST"] = saved
        os.environ.pop("STATE_DIR")


# ── Phase 1: one pass, then a drop (AC 7, 8) ──────────────────────────────────


def test_a_175s_quote_gets_one_pass_and_is_dropped_if_still_over():
    seen = {}

    def repair(problems):
        seen.update(problems)
        return [Q("q01", words_of(0, 6))]  # Lisa sends it back unchanged

    kept, report = quote_gate.enforce([Q("q01", words_of(0, 6)), Q("q02", words_of(8, 8))], SEGS, "E99", repair, LIMITS)
    assert list(seen) == ["q01"] and "175 s" in seen["q01"][0], seen  # she's told its length and the ceiling
    assert [q["id"] for q in kept] == ["q02"], kept
    assert report["dropped"] == [
        {
            "id": "q01",
            "why": "still too long after Lisa's pass",
            "reasons": report["dropped"][0]["reasons"],
            "span_s": 175.0,
        }
    ], report  # over 166 too
    assert all(q["tape_check"]["span_s"] <= LIMITS.for_quote(q) for q in kept)
    assert "q01 (too long)" in quote_gate.standup_line("E99", report)
    assert report["ceiling"]["standard_s"] == LIMITS.standard_s and report["lengths"] == {"q02": 25.0}, report


def test_lisas_cut_is_measured_again_and_kept():
    kept, report = quote_gate.enforce(
        [Q("q01", words_of(0, 6))], SEGS, "E99", lambda p: [Q("q01", words_of(2, 3))], LIMITS
    )
    assert kept[0]["tape_check"]["span_s"] == 50.0 and report["repaired"][0]["id"] == "q01", (kept, report)


def test_a_dropped_quote_the_pass_left_out_is_recorded_as_too_long():
    kept, report = quote_gate.enforce(
        [Q("q01", words_of(0, 6)), Q("q02", words_of(8, 8))], SEGS, "E99", lambda p: [], LIMITS
    )
    assert report["dropped"][0]["why"].startswith("too long"), report


def test_long_form_needs_lisas_reason_and_stays_under_its_own_ceiling():
    bare = Q("q01", words_of(0, 3), long_form=True)  # 100 s, flag but no reason
    kept, report = quote_gate.enforce(
        [bare], SEGS, "E99", lambda p: [Q("q01", words_of(0, 3), long_form=True, long_form_reason=REASON)], LIMITS
    )
    assert kept[0]["tape_check"]["ceiling_s"] == LIMITS.long_s and report["long_form"] == ["q01"], report
    assert "long_form (approve before posting): q01" in quote_gate.standup_line("E99", report)
    kept, report = quote_gate.enforce(
        [Q("q01", words_of(0, 6), long_form=True, long_form_reason=REASON)], SEGS, "E99", lambda p: [], LIMITS
    )  # 175 s: over even long_form
    assert kept == [] and report["dropped"][0]["why"].startswith("too long"), report


def test_a_quote_lisa_keeps_but_cannot_cut_under_76s_is_kept_as_long_form_pending():
    """John, 2026-10-12 (E13): three of his answers were dropped after Lisa's pass. Now one she keeps, still over the
    standard ceiling but within the long_form one, is kept whole as long_form PENDING, listed for a person."""
    kept, report = quote_gate.enforce(
        [Q("q01", words_of(0, 3)), Q("q02", words_of(8, 8))], SEGS, "E99", lambda p: [Q("q01", words_of(0, 3))], LIMITS
    )  # 100 s, returned unchanged
    q = next(x for x in kept if x["id"] == "q01")
    assert q["long_form"] is True and q["long_form_pending"] is True and clip_limits.is_long_form(q), q
    assert q["tape_check"]["verdict"] == "listen" and q["tape_check"]["ceiling_s"] == LIMITS.long_s, q["tape_check"]
    assert report["long_form_pending"] == [{"id": "q01", "span_s": 100.0}] and report["dropped"] == [], report
    line = quote_gate.standup_line("E99", report)
    assert "long_form (approve before posting): q01" in line and "q01 (100 s)" in line, line
    assert "q01" in [x["id"] for x in report["listen"]], report


def test_a_replacement_over_166s_is_dropped_not_pending():
    kept, report = quote_gate.enforce(
        [Q("q01", words_of(0, 3)), Q("q02", words_of(8, 8))], SEGS, "E99", lambda p: [Q("q01", words_of(0, 6))], LIMITS
    )  # she returns 175 s
    assert [q["id"] for q in kept] == ["q02"] and not report.get("long_form_pending"), report
    assert report["dropped"][0]["span_s"] == 175.0 and report["dropped"][0]["why"] == "still too long after Lisa's pass"


def test_a_guest_featured_only_through_pending_quotes_is_said():
    """Toby's condition B: drop the pending quote later and the episode is host-only. The standup says so now."""
    host = dict(Q("q02", words_of(8, 8)), speaker="Heather Reilly Hiemstra")
    kept, report = quote_gate.enforce(
        [Q("q01", words_of(0, 3)), host], SEGS, "E99", lambda p: [Q("q01", words_of(0, 3))], LIMITS
    )
    assert report["guest_only_via_pending"] == ["q01"], report
    assert "dropping them leaves the episode host-only" in quote_gate.standup_line("E99", report)


def test_a_pending_reel_cannot_pass_for_an_approved_one():
    render = (REPO / "reels" / "render_reel.js").read_text()
    assert "quote.long_form_pending === true ? 'PENDING-APPROVAL_'" in render
    assert "reel_${reelNum}_${pendingTag}${quote.topic_slug}.mp4" in render
    assert clip_limits.spec_args(
        ["reel_01_PENDING-APPROVAL_x.mp4"], [{"index": 1, "long_form": True, "long_form_reason": REASON}]
    ) == ["--long-form", "reel_01_PENDING-APPROVAL_x.mp4"]


def test_a_quote_lisa_leaves_out_is_dropped_with_its_length_recorded():
    kept, report = quote_gate.enforce(
        [Q("q01", words_of(0, 3)), Q("q02", words_of(8, 8))], SEGS, "E99", lambda p: [], LIMITS
    )
    assert [q["id"] for q in kept] == ["q02"] and report["dropped"][0]["span_s"] == 100.0, report
    for f in ("rockstar/jobs/round_1.py", "rockstar/tools/rerun_brand_team.py"):
        assert '"span_s": d.get("span_s")' in (REPO / f).read_text(), f"{f}: the ledger keeps each drop's length"


def test_a_bare_long_form_flag_is_cleared_and_said():
    kept, report = quote_gate.enforce([Q("q01", words_of(1, 1), long_form=True)], SEGS, "E99", lambda p: [], LIMITS)
    assert kept[0]["long_form"] is False and report["long_form_cleared"] == ["q01"], report


def test_an_unplaced_quote_never_passes_on_a_zero_length():
    kept, report = quote_gate.enforce(
        [Q("q01", "nothing like this was ever said on the tape at all")], SEGS, "E99", lambda p: [], LIMITS
    )
    assert kept == [] and report["dropped"], report


# ── Phase 2 (AC 10, 11) ───────────────────────────────────────────────────────


def test_phase2_refuses_a_hand_edited_175s_quote_before_any_spend():
    q = {"id": "q03", **tc(175)}
    over, unmeasured = clip_limits.before_spend([q, {"id": "q04"}], LIMITS)
    assert over and over[0].startswith("q03: reads up to 175 s") and unmeasured == ["q04"], (over, unmeasured)


def test_phase2_checks_the_aligned_window_against_the_reel_and_the_box():
    std = {"id": "q01", "transcript_start_ms": 1000, "transcript_end_ms": 176000}
    long_ok = {
        "id": "q02",
        "transcript_start_ms": 0,
        "transcript_end_ms": 150000,
        "long_form": True,
        "long_form_reason": REASON,
    }
    assert [p.split(":")[0] for p in clip_limits.after_alignment([std, long_ok], LIMITS)] == ["q01"]


def test_step8_refuses_a_clip_over_the_box_limit():
    manifest = {
        "clips": [
            {"quote_index": 1, "clip_start_ms": 0, "clip_end_ms": 60000},
            {"quote_index": 3, "clip_start_ms": 1000, "clip_end_ms": 182000},
        ]
    }
    assert clip_limits.manifest_problems(manifest, LIMITS) == ["quote 3: clip 181.0 s, box limit 180 s"]


def test_only_long_form_reels_get_the_long_cap():
    quotes = [
        {"index": 1},
        {"index": 2, "long_form": True, "long_form_reason": REASON},
        {"index": 3, "long_form": True},
    ]
    reels = ["reel_01_a.mp4", "reel_02_b.mp4", "reel_03_c.mp4"]
    assert clip_limits.spec_args(reels, quotes) == ["reel_01_a.mp4", "--long-form", "reel_02_b.mp4", "reel_03_c.mp4"]


def test_a_bare_long_form_reaching_phase2_is_cleared_before_render_and_the_spec_gate():
    """Toby's review: render_reel.js reads `long_form === true`, 10.5 also wants the reason. One rule, applied at load."""
    quotes = [
        {"id": "q01", "index": 1, "long_form": True},
        {"id": "q02", "index": 2, "long_form": True, "long_form_reason": REASON},
    ]
    assert clip_limits.clear_bare_long_form(quotes) == ["q01"]
    assert [q["long_form"] for q in quotes] == [False, True]
    assert clip_limits.spec_args(["reel_01_a.mp4", "reel_02_b.mp4"], quotes) == [
        "reel_01_a.mp4",
        "--long-form",
        "reel_02_b.mp4",
    ]
    p2 = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    assert p2.index("clip_limits.clear_bare_long_form(quotes)") < p2.index('run_ledger.step("6", "faq_blog")')


def test_the_margins_evidence_goes_to_the_ledger_every_run():
    quotes = [
        {"id": "q01", "transcript_start_ms": 2000, "transcript_end_ms": 62000, **tc(59)},
        {"id": "q02", "transcript_start_ms": 0, "transcript_end_ms": 0, **tc(30)},
        {"id": "q03"},
    ]
    assert clip_limits.span_vs_aligned(quotes) == [{"id": "q01", "span_s": 59.0, "aligned_s": 60.0, "short_s": 1.0}]
    assert '"span_vs_aligned"' in (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()


def test_the_checks_sit_before_the_spend():
    p2 = (REPO / "rockstar" / "jobs" / "publish_finalize.py").read_text()
    guard, faq, align = (
        p2.index('run_ledger.step("5b", "clip_ceiling")'),
        p2.index('run_ledger.step("6", "faq_blog")'),
        p2.index('run_ledger.step("7a-0", "forced_alignment")'),
    )
    assert guard < faq < align
    assert guard < p2.index("_clip_guard(over, episode_code", guard) < faq, "the guard must stop the job, not just log"
    step7 = p2.index('run_ledger.step("7", "extract_clips")')
    assert (
        step7
        < p2.index("_clip_guard(clip_limits.after_alignment(", step7)
        < p2.index('"extract_quote_clips.sh"', step7)
    )
    step8 = p2.index('run_ledger.step("8", "transcribe_clips")')
    assert step8 < p2.index("clip_limits.manifest_problems(", step8) < p2.index('"transcribe_clips.sh"', step8)
    r1 = (REPO / "rockstar" / "jobs" / "round_1.py").read_text()
    assert r1.index("clip_limits.load()") < r1.index("quotes = anthropic_client.run_brand_team_chain(")
    assert "limits,\n" in r1[r1.index("quote_gate.check_selected(") : r1.index('brand_report["verbatim"]')]
    tool = (REPO / "rockstar" / "tools" / "rerun_brand_team.py").read_text()
    assert "clip_limits.load()" in tool and "quote_gate.check_selected(" in tool, "the re-run picks quotes too"
    assert '"quote_lengths"' in r1, "AC 16: the ceiling and each length go to the ledger"
    render = (REPO / "reels" / "render_reel.js").read_text()
    assert "quote.long_form === true ? ['--long-form']" in render


# ── transcribe_clips.sh: the measured batch, and no WhisperX over the limit ───


def _transcribe(
    tmp: Path, clip_ms: int, limits_text: str | None, flag: bool = True
) -> tuple[subprocess.CompletedProcess, Path]:
    ep, venv, home = tmp / "ep", tmp / "venv", tmp / "home"
    (ep / "clips").mkdir(parents=True)
    (venv / "bin").mkdir(parents=True)
    home.mkdir()
    called = tmp / "whisperx-called"
    (venv / "bin" / "whisperx").write_text(f'#!/bin/bash\necho "$@" > "{called}"\nexit 1\n')
    (venv / "bin" / "whisperx").chmod(0o755)
    (venv / "bin" / "activate").write_text(f'export PATH="{venv / "bin"}:$PATH"\n')
    (ep / "clips" / "quote_01_x.m4a").write_bytes(b"")
    (ep / "clips" / "clips_manifest.json").write_text(
        json.dumps(
            {
                "clips": [
                    {"quote_index": 1, "clip_filename": "quote_01_x.m4a", "clip_start_ms": 0, "clip_end_ms": clip_ms}
                ]
            }
        )
    )
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "VENV_PATH": str(venv)}
    if limits_text is not None:
        (tmp / "box-limits.env").write_text(limits_text)
        env["BOX_LIMITS_FILE"] = str(tmp / "box-limits.env")
        if flag:
            env["BOX_LIMITS_TEST"] = "1"
    if limits_text is None or not flag:
        env["STATE_DIR"] = str(tmp / "no-state")
    r = subprocess.run(
        ["bash", str(REPO / "scripts" / "transcribe_clips.sh"), str(ep)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r, called


def test_transcribe_clips_refuses_without_limits_and_over_the_limit_and_uses_the_measured_batch():
    if not shutil.which("jq"):
        print("  -- jq absent: transcribe_clips.sh checks NOT run (NOT a pass)")
        return
    box = (REPO / "tests" / "fixtures" / "box-limits.env").read_text()
    r, called = _transcribe(Path(tempfile.mkdtemp()), 60000, None)
    assert r.returncode == 4 and "measure_clip_limit.sh" in r.stderr and not called.exists(), r.stderr
    r, called = _transcribe(Path(tempfile.mkdtemp()), 60000, box, flag=False)  # a stray variable is ignored
    assert r.returncode == 4 and not called.exists(), r.stderr
    r, called = _transcribe(Path(tempfile.mkdtemp()), 181000, box)
    assert r.returncode == 5 and "quote 1" in r.stderr and not called.exists(), r.stderr
    r, called = _transcribe(Path(tempfile.mkdtemp()), 60000, box)
    assert called.exists() and "--batch_size 2" in called.read_text(), (r.stdout, r.stderr)


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
