"""
test_title01_gen_gate.py — TITLE-01 phase-1 title generation + shared gate.

Run: python3 test_title01_gen_gate.py   (plain asserts, no pytest needed)

Covers TITLE-01 acceptance criteria:
  1 preamble resilience  → test_parse_title_preamble / test_compose_title_*  (A2)
  2 gate blocks placeholder, no false positives → test_gate_*               (B4)
  3 round_1 gating side effects (no counter bump / no drafting entry on fail,
    real title completes) → test_round1_placeholder_*, test_round1_real_*    (C5/6)
  4 shared-gate parity (one definition, two callers) → test_shared_gate_parity (B4)
  5 reprocess path still gated → test_round1_reprocess_gated                  (D7)

Discipline (same as PUB-01):
  - anthropic / fastapi / googleapiclient / calendar_client / drive_client pull
    in heavy deps NOT installed locally. content_gates.py is PURE — tested
    directly. For compose_title we stub the `anthropic` SDK module and the
    client; for round_1 we stub the I/O-boundary modules in sys.modules and run
    Bill's REAL _run_pipeline, so the gate ordering (gate BEFORE counter/schedule
    write) is his code, exercised live — not a re-implementation.
  - Isolated temp state only: STATE_DIR is pointed at a throwaway dir via the
    env override round_1 reads at import. We NEVER touch /opt/rockstar-pipeline.
"""

import importlib
import json
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE))
_REAL_STATE = "/opt/rockstar-pipeline"


# ── anthropic SDK boundary (deterministic whether anthropic is installed or not) ──


def _install_anthropic_stub():
    """Guarantee `import anthropic` works AND `anthropic.Anthropic` is a real
    class — in BOTH environments (server venv where the SDK is installed, and a
    bare interpreter where it is not).

    Why this matters: anthropic_client.py:65 has a MODULE-LEVEL annotation
    `_client: anthropic.Anthropic | None`, evaluated at import. `X | None`
    requires X to support __or__ — a real class does; a lambda does NOT (that was
    Toby's 4× `TypeError: unsupported operand for |`). We never call the real
    API: every test that touches the client overrides `_get_client`. So the only
    contract we need is "Anthropic is a class". We assert that invariant in both
    branches rather than trusting it, so the test behaves identically present-or-
    absent and can't silently diverge by environment again (DOD-1)."""
    real = sys.modules.get("anthropic")
    if real is not None:
        # SDK genuinely installed (server venv / Toby's pipeline). Its real
        # `Anthropic` is already a class; assert the one property we depend on so
        # a future SDK change that broke it would fail loudly here, not mid-test.
        assert isinstance(getattr(real, "Anthropic", None), type), (
            "real anthropic.Anthropic is not a class — annotation in "
            "anthropic_client.py:65 would break"
        )
        return
    anthropic = types.ModuleType("anthropic")

    class Anthropic:  # noqa: N801 — mirrors the SDK name; a real class, not a lambda
        def __init__(self, *a, **k):
            pass

    anthropic.Anthropic = Anthropic
    assert isinstance(anthropic.Anthropic, type)  # the invariant, made explicit
    sys.modules["anthropic"] = anthropic


# ── criterion 1 — preamble resilience in title parsing/generation ──────────────

# The exact E29 repro: a *Pre-write note:* + `---` rule ABOVE a ```json fence.
E29_RESPONSE = (
    "*Pre-write note: Brand Team quotes are blank, working from transcript only.*\n"
    "---\n"
    "```json\n"
    '{"hook": "Don\'t Wait for the Stop", "topic": "Hormone Optimization After 40"}\n'
    "```\n"
)

# A bare brace-slice case: valid JSON object with trailing chatter, no fence.
BARE_BRACE_RESPONSE = (
    "Sure! Here is the title you asked for:\n"
    '{"hook": "The Silent Shift", "topic": "Perimenopause Nobody Warned You About"}\n'
    "Let me know if you want alternatives."
)


def test_parse_title_preamble():
    _install_anthropic_stub()
    import anthropic_client
    importlib.reload(anthropic_client)

    # E29 fenced-with-preamble → fence strip skips the note, parses clean.
    got = anthropic_client._parse_title_json(E29_RESPONSE)
    assert got == {
        "hook": "Don't Wait for the Stop",
        "topic": "Hormone Optimization After 40",
    }, got

    # Bare brace slice past trailing chatter (no fence) → second pass recovers it.
    got = anthropic_client._parse_title_json(BARE_BRACE_RESPONSE)
    assert got["hook"] == "The Silent Shift", got

    # Unparseable / empty → RAISES (never silently returns a default).
    for bad in ("", "no json here at all", "*just a note*\n---\n"):
        try:
            anthropic_client._parse_title_json(bad)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError(f"_parse_title_json should raise on {bad!r}")
    print("  1 _parse_title_json: E29 preamble + bare-brace parse; empty/garbage raises ✓")


class _FakeAnthropicResponse:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(text=text)]
        self.stop_reason = "end_turn"


class _FakeClient:
    def __init__(self, text):
        self._text = text
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        return _FakeAnthropicResponse(self._text)


def _compose_title_with_response(text: str):
    """Drive the REAL compose_title with a stubbed client returning `text`."""
    _install_anthropic_stub()
    import anthropic_client
    importlib.reload(anthropic_client)
    # Stub the two boundaries compose_title touches: the API client and the
    # prompt loader (no prompts dir in the test env). Everything else — fence
    # strip, brace slice, hook/topic assembly, raise-on-empty — is real.
    anthropic_client._get_client = lambda: _FakeClient(text)
    anthropic_client._load_prompt = lambda name: "SYSTEM"
    return anthropic_client.compose_title(transcript_text="t", episode_metadata={})


def test_compose_title_e29_preamble():
    result = _compose_title_with_response(E29_RESPONSE)
    assert result["hook"] == "Don't Wait for the Stop", result
    assert result["topic"] == "Hormone Optimization After 40", result
    # title is reassembled canonically as "Hook | Topic".
    assert result["title"] == "Don't Wait for the Stop | Hormone Optimization After 40", result
    print("  1 compose_title(E29): {hook, topic, title} correct, title=%r ✓" % result["title"])


def test_compose_title_empty_raises():
    # Empty / fields-missing responses must RAISE — never default to "TBD".
    for bad in ('{}', '{"hook": "", "topic": "x"}', "garbage not json"):
        try:
            _compose_title_with_response(bad)
        except (RuntimeError, json.JSONDecodeError):
            pass
        else:
            raise AssertionError(f"compose_title should raise on {bad!r}, not default")
    print("  1 compose_title: empty/missing-field/garbage → raises, never 'TBD' ✓")


# ── criterion 2 — gate blocks placeholders, no false positives ─────────────────


def test_gate_blocks_placeholders():
    import content_gates
    placeholders = [
        "TBD", "tbd", " TBD ", "", "   ",
        "Pending", "pending review", "PLACEHOLDER",
        "Untitled", "untitled episode", "tk", "TK come back",
        "Don't Wait | TBD",  # placeholder anywhere in the string
    ]
    for t in placeholders:
        try:
            content_gates.assert_real_title(t, "E99")
        except content_gates.GateFailure:
            pass
        else:
            raise AssertionError(f"gate must REJECT placeholder {t!r}")
    print(f"  2 gate rejects {len(placeholders)} placeholder/empty variants ✓")


def test_gate_no_false_positives():
    import content_gates
    # Real titles AND tricky words that *contain* a placeholder stem but are NOT
    # placeholders — the \b word-boundary in _PLACEHOLDER_TITLE_RE must let these
    # through. This is the regression that proves the boundary actually works.
    #
    # NOTE on "Pending Matters": the coordinator's brief flagged it as a
    # no-false-positive case, but Bill's regex matches the standalone WORD
    # 'pending' at a boundary, so "Pending Matters" is correctly REJECTED — the
    # gate is about whole-word placeholder tokens, not substrings. We assert the
    # ACTUAL behavior (words *containing* the stem like Pendulum/Attkisson pass;
    # the standalone word 'pending' does not) and surface the brief's mismatch as
    # a finding rather than encode a false expectation.
    passing = [
        "Don't Wait for the Stop | Hormone Optimization After 40",
        "The Silent Shift | Perimenopause Nobody Warned You About",
        "Pendulum | Finding Balance in Midlife",
        "Tkachenko's Method | Strength Training",
        "Attkisson Report | Media Literacy",
        "Suntk and Suntan | A Beach Story",
    ]
    for t in passing:
        content_gates.assert_real_title(t, "E99")  # must NOT raise

    # And confirm the boundary really is the mechanism: 'pending' as a standalone
    # word is rejected, but embedded in 'Pendulum'/'Attkisson' it is not.
    import content_gates as cg
    assert cg._PLACEHOLDER_TITLE_RE.search("pending") is not None
    assert cg._PLACEHOLDER_TITLE_RE.search("Pendulum") is None
    assert cg._PLACEHOLDER_TITLE_RE.search("Attkisson") is None
    print(f"  2 gate passes {len(passing)} real titles incl. words containing stems "
          "(Pendulum/Attkisson/Tkachenko) ✓")


# ── criterion 4 — shared-gate parity (one definition, two callers) ─────────────


def test_shared_gate_parity():
    """The SAME input must fail identically via round_1's path (content_gates
    directly) and publish_finalize's wrapper. publish_finalize imports its own
    deps; stub the heavy ones so we can import its wrapper, then prove the wrapper
    is a thin translation of the shared gate (GateFailure → PermanentFailure)."""
    import content_gates

    # round_1 path uses content_gates.assert_real_title directly.
    round1_raised = False
    try:
        content_gates.assert_real_title("TBD", "E99")
    except content_gates.GateFailure:
        round1_raised = True
    assert round1_raised, "round_1 path (shared gate) should reject TBD"

    # publish_finalize path: import its module with boundary deps stubbed, then
    # call its _assert_real_title wrapper.
    pf = _import_publish_finalize_with_stubs()
    pf_raised = False
    try:
        pf._assert_real_title("TBD", "E99")
    except pf.PermanentFailure as exc:
        pf_raised = True
        # parity: same message text comes from the shared gate.
        assert "missing/placeholder" in str(exc), str(exc)
    assert pf_raised, "publish_finalize wrapper should translate GateFailure→PermanentFailure"

    # A real title passes BOTH callers with no raise.
    content_gates.assert_real_title("Real Hook | Real Topic", "E99")
    pf._assert_real_title("Real Hook | Real Topic", "E99")
    print("  4 shared-gate parity: TBD rejected by BOTH round_1 & publish_finalize "
          "(GateFailure→PermanentFailure); real title passes both ✓")


def _import_publish_finalize_with_stubs():
    """Import jobs.publish_finalize with its heavy I/O-boundary modules stubbed.
    Only the wrapper + shared-gate delegation is exercised; nothing here calls
    Drive/calendar/anthropic."""
    _install_anthropic_stub()
    for name in ("calendar_client", "drive_client", "gmail_client",
                 "publisher_email", "db"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules.pop("jobs.publish_finalize", None)
    import jobs.publish_finalize as pf
    importlib.reload(pf)
    # publish_finalize must import the SAME content_gates module (one definition).
    import content_gates
    assert pf.content_gates is content_gates, "publish_finalize uses a different gate module!"
    return pf


# ── criterion 3 / 5 — round_1 gating side effects (the integrated test) ────────


def _fresh_state() -> Path:
    """A throwaway STATE_DIR seeded with a counter + empty schedule."""
    d = Path(tempfile.mkdtemp(prefix="title01_state_"))
    (d / "episode_counter.json").write_text(json.dumps({
        "next_episode_number": 30,
        "current_season": 2,
        "next_season_episode": 5,
        "updated_at": "2026-06-23T00:00:00Z",
    }), encoding="utf-8")
    (d / "publication_schedule.json").write_text(
        json.dumps({"episodes": [], "updated_at": "2026-06-23T00:00:00Z"}),
        encoding="utf-8",
    )
    return d


def _load_round1_with_stubs(state_dir: Path, title_response: str, *, seed_schedule=None):
    """
    Import the REAL jobs.round_1 with STATE_DIR pointed at `state_dir` and every
    I/O-boundary module (drive_client, calendar_client, anthropic_client, db,
    subprocess scripts) stubbed so execution reaches Bill's real step-11.5→15.

    compose_title is stubbed to return `title_response` (a {title:...} dict) —
    so the test controls whether the title is a placeholder or real, and we
    observe whether the REAL gate + the REAL counter/schedule writes that follow
    it fire. Returns the reloaded round_1 module.
    """
    _install_anthropic_stub()
    os.environ["STATE_DIR"] = str(state_dir)
    os.environ["SCRIPTS_DIR"] = str(state_dir / "scripts")  # never actually run
    os.environ["DRIVE_EPISODES_FOLDER_ID"] = "folder-root"
    # CAL-OFF (2026-10-01) made the calendar event opt-in. These tests pin the event-ON path (the id lands on
    # the schedule entry); test_r1_reliability.py pins the default-OFF path.
    os.environ["EDITORIAL_CALENDAR_EVENT"] = "on"

    if seed_schedule is not None:
        (state_dir / "publication_schedule.json").write_text(
            json.dumps(seed_schedule), encoding="utf-8")

    # ── drive_client stub: an inbox with one E30 audio file, no existing folder ──
    drive = types.ModuleType("drive_client")
    AUDIO = {"id": "aud-1", "name": "E30-test-episode.m4a"}

    drive.list_audio_in_inbox = lambda: [AUDIO]
    drive.list_folder = lambda fid: [AUDIO]
    drive.find_episode_folder = lambda code: None
    drive.has_pipeline_done_marker = lambda fid: False
    drive.create_folder = lambda name, parent_folder_id=None: {"id": "epfolder-1"}
    drive.download_file = lambda fid, path: Path(path).write_bytes(b"audio")
    drive.move_file = lambda fid, dest: None
    drive.upload_file = lambda path, folder: None

    # ── calendar_client stub ──
    cal = types.ModuleType("calendar_client")
    cal.create_editorial_event = lambda **k: {"id": "cal-evt-1"}

    # ── anthropic_client stub: skip transcription/brand-team/summary, control title ──
    ac = types.ModuleType("anthropic_client")
    # `heading` is required at Phase 1 since BLOG-02 F-1 (content_gates.assert_quote_headings).
    ac.run_brand_team_chain = lambda **k: [{"speaker": "Guest", "quote_text": "q",
                                            "heading": "A Real Heading"}]
    # A summary shaped like a real one: since R1-02, Round 1 normalizes it and proves it with the blog
    # builder's own parser before anything irreversible.
    ac.compose_apple_summary = lambda **k: (
        "# Gen X Hits Menopause · S2E5 · A Hook | A Topic\n\nOpening paragraph.\n\n"
        "Conversations include\n\n- **A Lead** A body.\n\nTune in.\n\nAbout A Guest\n\nA bio.\n"
    )

    # ── guest_form stub (BIO-01): no network in tests; a miss is the documented no-form path ──
    gf = types.ModuleType("guest_form")
    gf.lookup = lambda segments: (None, "test: no form")
    ac.compose_title = lambda **k: title_response

    # ── db stub (round_1 imports it; not used on the title path) ──
    db_stub = types.ModuleType("db")

    # Completeness guard: every drive_client name round_1 actually calls on the
    # title path MUST be present on the stub, or the test fails LOUDLY at setup
    # with a clear message — never mid-pipeline with an opaque AttributeError
    # (that was Toby's 3× `drive_client has no attribute 'list_audio_in_inbox'`).
    # Keep this list in sync with round_1's drive_client.* call sites.
    _required_drive = (
        "list_audio_in_inbox", "list_folder", "find_episode_folder",
        "has_pipeline_done_marker", "create_folder", "download_file",
        "move_file", "upload_file",
    )
    missing = [n for n in _required_drive if not hasattr(drive, n)]
    assert not missing, f"drive_client stub is missing: {missing}"

    for name, mod in (("drive_client", drive), ("calendar_client", cal),
                      ("anthropic_client", ac), ("db", db_stub), ("guest_form", gf)):
        sys.modules[name] = mod

    sys.modules.pop("jobs.round_1", None)
    import jobs.round_1 as round_1
    importlib.reload(round_1)
    assert str(round_1._STATE_DIR) == str(state_dir), round_1._STATE_DIR
    assert not str(round_1._STATE_DIR).startswith(_REAL_STATE), "must not use live state"
    return round_1, drive


def _read_counter(d: Path) -> dict:
    return json.loads((d / "episode_counter.json").read_text())


def _read_schedule(d: Path) -> dict:
    return json.loads((d / "publication_schedule.json").read_text())


# The transcription + boundary steps shell out via _run_script. We monkeypatch
# _run_script to instead write the transcript/boundary files the pipeline expects,
# so the real _run_pipeline flows through to the title step without invoking
# whisperx/node. This stays at the I/O boundary — the gate logic is untouched.
def _patch_scripts(round_1):
    def fake_run_script(cmd, work_dir, label):
        wd = Path(work_dir)
        tdir = wd / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        if label == "transcribe_fast":
            (tdir / "transcript_fast.json").write_text('{"segments": []}')
            (tdir / "transcript_fast.txt").write_text("hello world transcript")
        elif label == "detect_boundaries":
            (tdir / "boundaries.json").write_text('{"boundaries": []}')
    round_1._run_script = fake_run_script


def test_round1_placeholder_blocks_side_effects():
    state = _fresh_state()
    # compose_title returns a PLACEHOLDER title → the gate must fire.
    round_1, drive = _load_round1_with_stubs(state, {"title": "TBD", "hook": "", "topic": ""})
    _patch_scripts(round_1)

    raised = False
    try:
        round_1._run_pipeline(job_id=1, payload={})
    except round_1.content_gates.GateFailure as exc:
        # The TITLE gate, not an earlier one (e.g. BLOG-02's heading gate) — Jen's review.
        assert "missing/placeholder" in str(exc), str(exc)
        raised = True
    assert raised, "round_1 must RAISE on a placeholder title (gate before side effects)"

    # criterion C/6: counter NOT bumped, NO drafting entry written.
    counter = _read_counter(state)
    assert counter["next_episode_number"] == 30, f"counter bumped! {counter}"
    assert counter["next_season_episode"] == 5, counter
    sched = _read_schedule(state)
    assert sched["episodes"] == [], f"a schedule entry was written! {sched['episodes']}"
    print("  3 round_1(placeholder): RAISES, counter stays E30, NO drafting entry ✓")


def test_round1_real_title_completes():
    state = _fresh_state()
    real = {"title": "Don't Wait | Hormone Optimization", "hook": "Don't Wait",
            "topic": "Hormone Optimization"}
    round_1, drive = _load_round1_with_stubs(state, real)
    _patch_scripts(round_1)

    round_1._run_pipeline(job_id=1, payload={})  # must NOT raise

    # counter bumped E30→E31, schedule carries the REAL title with status drafting.
    counter = _read_counter(state)
    assert counter["next_episode_number"] == 31, counter
    sched = _read_schedule(state)
    assert len(sched["episodes"]) == 1, sched
    entry = sched["episodes"][0]
    assert entry["episode_title"] == "Don't Wait | Hormone Optimization", entry
    assert entry["status"] == "drafting", entry
    assert entry["calendar_event_id"] == "cal-evt-1", entry
    print("  3 round_1(real title): completes, counter→E31, schedule carries real "
          "title @ status=drafting ✓")


# ── criterion 5 — reprocess path still gated ───────────────────────────────────


def test_round1_reprocess_gated():
    state = _fresh_state()
    # Seed a prior E28 entry so an E28 inbox file is detected as a REPROCESS
    # (N < counter's next_episode_number=30). counter must NOT move on reprocess.
    seed = {"episodes": [{
        "episode_number": 28, "season": 2, "season_episode": 3,
        "episode_code": "E28", "season_episode_code": "S2E3",
        "drive_folder_id": "old-folder", "calendar_event_id": "old-cal",
        "episode_title": "Old Real Title", "status": "drafting",
        "drive_audio_file_id": "old-aud",
    }], "updated_at": "2026-06-23T00:00:00Z"}
    round_1, drive = _load_round1_with_stubs(
        state, {"title": "TBD", "hook": "", "topic": ""}, seed_schedule=seed)
    # Point the inbox at an E28 file → reprocess detection.
    AUDIO28 = {"id": "aud-28", "name": "E28-reprocess-me.m4a"}
    drive.list_audio_in_inbox = lambda: [AUDIO28]
    drive.list_folder = lambda fid: [AUDIO28]
    _patch_scripts(round_1)

    raised = False
    try:
        round_1._run_pipeline(job_id=1, payload={})
    except round_1.content_gates.GateFailure as exc:
        # The TITLE gate, not an earlier one (e.g. BLOG-02's heading gate) — Jen's review.
        assert "missing/placeholder" in str(exc), str(exc)
        raised = True
    assert raised, "reprocess path must ALSO be gated on a placeholder title"

    # Counter untouched (reprocess never bumps), and the existing E28 entry is
    # NOT flipped to a completed/reprocessed state by a failed run.
    counter = _read_counter(state)
    assert counter["next_episode_number"] == 30, counter
    sched = _read_schedule(state)
    e28 = next(e for e in sched["episodes"] if e["episode_code"] == "E28")
    assert e28["status"] != "reprocessed", f"failed reprocess marked complete! {e28}"
    print("  5 round_1(reprocess + placeholder): gated, counter untouched, E28 not "
          "marked reprocessed ✓")


# ── runner ─────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.disable(logging.CRITICAL)  # quiet the pipeline's own logging
    tests = [
        test_parse_title_preamble,
        test_compose_title_e29_preamble,
        test_compose_title_empty_raises,
        test_gate_blocks_placeholders,
        test_gate_no_false_positives,
        test_shared_gate_parity,
        test_round1_placeholder_blocks_side_effects,
        test_round1_real_title_completes,
        test_round1_reprocess_gated,
    ]
    failed = 0
    print("TITLE-01 title generation + shared gate — test run")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
