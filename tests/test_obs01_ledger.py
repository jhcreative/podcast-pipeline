"""
test_obs01_ledger.py — OBS-01: one run ledger, every step's time and outcome, every model call's tokens and cost.

Run: python3 -B test_obs01_ledger.py   (plain asserts, no pytest needed)

Why (2026-10-10): the pipeline could not say what an episode costs, whether the transcript cache hits, or where the
time goes. No code read `response.usage`; state/run_log.jsonl held one line per job that ran out of retries.

Acceptance criteria → tests:
  AC-1 one model_call event per call, with the usage numbers and the step's name
       → test_brand_team_chain_writes_four_events, test_every_compose_call_is_recorded
  AC-2 a call that raises still writes its event (outcome error, tokens null) and re-raises unchanged
       → test_failed_call_recorded_and_reraised
  AC-3 every step writes duration and outcome; a gate names itself; a skip says why
       → test_step_clock_outcomes, test_gate_wrapped_in_permanent_failure_is_a_gate_failure,
         test_worker_closes_the_jobs_clock, test_skipped_brand_team_member_is_recorded
  AC-4 cost_usd equals a hand calculation from PRICES; an unknown model costs None, never 0
       → test_cost_hand_calculation
  AC-5 a ledger write that fails never fails the job → test_unwritable_ledger_never_fails_the_call
  AC-6 sentinel: nothing from a transcript, a response or a gate's message reaches the ledger
       → test_sentinel_never_reaches_the_ledger
  AC-8 (Jen) run_kind on every event → test_run_kind_and_binding
  AC-9 (Jen) prompt_sha, request_template_sha, response_model → test_template_sha_tracks_shape_not_text

Discipline: run_ledger, anthropic_client and worker._dispatch are the real code; the Anthropic client is a fake that
returns canned `usage`. Every test writes to its own temp ledger file.
"""

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix="obs01-"))
os.environ["RUN_LOG_PATH"] = str(TMP / "run_log.jsonl")

if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["anthropic"] = _a

import anthropic_client  # noqa: E402
import run_ledger  # noqa: E402

SENTINEL = "SENTINEL-obs01-guest-words-7d3f"
QUOTES = {"quotes": [{"id": "q01", "speaker": "Guest Example", "text": f"A quote with {SENTINEL} in it.",
                      "heading": "A heading"}]}


def _ledger_path(name: str) -> Path:
    p = TMP / f"{name}.jsonl"
    os.environ["RUN_LOG_PATH"] = str(p)
    if p.exists():
        p.unlink()
    return p


def _events(p: Path, kind: str | None = None) -> list[dict]:
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return [r for r in rows if kind is None or r["event"] == kind]


class _Usage:
    def __init__(self, i=1000, o=500, cw=2000, cr=3000):
        self.input_tokens, self.output_tokens = i, o
        self.cache_creation_input_tokens, self.cache_read_input_tokens = cw, cr


class _Resp:
    def __init__(self, text, usage=None, stop="end_turn", model="claude-sonnet-4-6"):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage, self.stop_reason, self.model = usage or _Usage(), stop, model


class _Fake:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r if isinstance(r, _Resp) else _Resp(r)


def _with(fake):
    anthropic_client._get_client = lambda: fake
    anthropic_client._load_prompt = lambda name: f"system prompt for {name}"


# ── AC-1 ──────────────────────────────────────────────────────────────────────


def test_brand_team_chain_writes_four_events():
    p = _ledger_path("chain")
    good = json.dumps(QUOTES)
    _with(_Fake([_Resp(good, _Usage(i=900 + n, cr=100 * n)) for n in range(4)]))
    anthropic_client.run_brand_team_chain(f"transcript with {SENTINEL}", {"episode_code": "E99"})
    ev = _events(p, "model_call")
    assert [e["step"] for e in ev] == ["brand_team.marcus", "brand_team.robert", "brand_team.dr_joe",
                                       "brand_team.lisa"], [e["step"] for e in ev]
    assert [e["input_tokens"] for e in ev] == [900, 901, 902, 903], "the canned usage, per call"
    assert [e["cache_read_input_tokens"] for e in ev] == [0, 100, 200, 300]
    assert all(e["outcome"] == "ok" and e["prompt"] == f"brand_team_{e['step'].split('.')[1]}.md" for e in ev)
    assert all(e["response_model"] == "claude-sonnet-4-6" and e["cost_usd"] > 0 for e in ev)


def test_every_compose_call_is_recorded():
    src = (REPO / "anthropic_client.py").read_text() + (REPO / "tools" / "run_dr_joe.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "client.messages.create(" not in code, "a model call that bypasses the ledger"
    assert code.count("run_ledger.create(") == 9, "seven in anthropic_client, two in run_dr_joe"


# ── AC-2 ──────────────────────────────────────────────────────────────────────


def test_failed_call_recorded_and_reraised():
    p = _ledger_path("raise")

    class Overloaded(Exception):
        pass
    boom = Overloaded("503 overloaded")
    fake = _Fake([boom])
    try:
        run_ledger.create(fake, step="faq", prompt="faq.md", model="claude-sonnet-4-6", max_tokens=10,
                          system="s", messages=[{"role": "user", "content": "x"}])
    except Overloaded as exc:
        assert exc is boom, "the same exception, unchanged"
    else:
        raise AssertionError("the error must re-raise")
    (e,) = _events(p, "model_call")
    assert e["outcome"] == "error" and e["error"] == "Overloaded"
    assert e["input_tokens"] is None and e["cost_usd"] is None, "a failed call has no tokens and no cost"


# ── AC-3 ──────────────────────────────────────────────────────────────────────


def test_step_clock_outcomes():
    import content_gates
    p = _ledger_path("steps")
    clock = run_ledger.StepClock("publish_finalize")
    clock.start("4", "download")
    clock.start("6", "faq_blog")
    clock.skip("12b", "quote_cards", "cards/ not empty")
    clock.start("13", "thank_you")
    clock.skipped("Heather is the guest")
    clock.start("15", "schedule")
    try:
        content_gates.assert_real_title("TBD", "E99")
    except content_gates.GateFailure as exc:
        clock.fail(exc)
    ev = _events(p, "step")
    got = [(e["step"], e["outcome"], e["detail"]) for e in ev]
    assert got == [("4", "ok", ""), ("6", "ok", ""), ("12b", "skipped", "cards/ not empty"),
                   ("13", "skipped", "Heather is the guest"), ("15", "gate_failure", "assert_real_title")], got
    assert all(isinstance(e["duration_ms"], int) and e["phase"] == "publish_finalize" for e in ev)


def test_gate_wrapped_in_permanent_failure_is_a_gate_failure():
    import content_gates
    p = _ledger_path("wrapped")

    class PermanentFailure(Exception):
        pass
    clock = run_ledger.StepClock("publish_finalize")
    clock.start("6", "faq_blog")
    try:
        try:
            content_gates.assert_real_title("TBD", "E99")
        except content_gates.GateFailure as exc:
            raise PermanentFailure(f"gate: {exc}") from exc
    except PermanentFailure as exc:
        clock.fail(exc)
    (e,) = _events(p, "step")
    assert (e["outcome"], e["detail"]) == ("gate_failure", "assert_real_title"), e
    clock.start("7", "extract_clips")
    clock.fail(PermanentFailure("no audio"))
    clock.start("8", "transcribe_clips")
    clock.fail(TimeoutError("whisperx"))
    ev = _events(p, "step")
    assert [(e["outcome"], e["detail"]) for e in ev[1:]] == [("permanent_failure", "PermanentFailure"),
                                                              ("error", "TimeoutError")]


def test_worker_closes_the_jobs_clock():
    p = _ledger_path("worker")
    import worker
    job = {"id": 77, "payload": "{}", "episode_id": "TBD"}

    def handler(job_type, job, payload, episode_id):
        run_ledger.bind(episode="E99")
        run_ledger.step("1", "parse_email")
        run_ledger.step("2", "identify")
        raise RuntimeError("drive 500")
    real = worker._run_handler
    worker._run_handler = handler
    try:
        worker._dispatch("PUBLISH_FINALIZE", job)
    except RuntimeError:
        pass
    finally:
        worker._run_handler = real
    ev = _events(p, "step")
    assert [(e["step"], e["outcome"], e["job_id"], e["episode"]) for e in ev] == [
        ("1", "ok", 77, "E99"), ("2", "error", 77, "E99")], ev


def test_skipped_brand_team_member_is_recorded():
    p = _ledger_path("skipmember")
    good = json.dumps(QUOTES)
    fake = _Fake([good, good, good])

    def load(name):
        if name == "brand_team_dr_joe.md":
            raise FileNotFoundError(name)
        return f"system prompt for {name}"
    anthropic_client._get_client = lambda: fake
    anthropic_client._load_prompt = load
    report = {}
    anthropic_client.run_brand_team_chain("t", {}, report=report)
    assert "brand_team_dr_joe" in report["skipped"], report
    (e,) = _events(p, "step")
    assert (e["step"], e["name"], e["outcome"], e["detail"]) == ("10.dr_joe", "brand_team.dr_joe", "skipped",
                                                                 "prompt file missing")


# ── AC-4 ──────────────────────────────────────────────────────────────────────


def test_cost_hand_calculation():
    usage = {"input_tokens": 1000, "output_tokens": 500, "cache_creation_input_tokens": 2000,
             "cache_read_input_tokens": 3000}
    # by hand: (1000×3.00 + 2000×3.75 + 3000×0.30 + 500×15.00) / 1e6 = (3000 + 7500 + 900 + 7500) / 1e6
    assert run_ledger.cost_usd("claude-sonnet-4-6", usage) == 0.0189
    assert run_ledger.cost_usd("claude-unknown-9", usage) is None, "unknown is not zero"
    # 1-hour cache writes cost more: of the 2000 written, 500 at the 1h rate ($6.00) instead of 5m ($3.75)
    assert run_ledger.cost_usd("claude-sonnet-4-6", {**usage, "cache_write_1h_tokens": 500}) == round(
        0.0189 + 500 * (6.00 - 3.75) / 1e6, 6)
    # Haiku 5.5: a prompt over 100K tokens is priced on the higher row
    small = {"input_tokens": 50_000, "output_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    big = {**small, "input_tokens": 120_000}
    # the threshold counts cache reads and writes too (the page: "all of its input tokens")
    cached_big = {**small, "input_tokens": 10_000, "cache_read_input_tokens": 95_000}
    assert run_ledger.cost_usd("claude-haiku-5-5", cached_big) == round(
        (10_000 * 0.50 + 95_000 * 0.05 + 1000 * 2.50) / 1e6, 6), "cache reads push a prompt over 100K"
    assert run_ledger.cost_usd("claude-haiku-5-5", small) == round((50_000 * 0.10 + 1000 * 0.50) / 1e6, 6)
    assert run_ledger.cost_usd("claude-haiku-5-5", big) == round((120_000 * 0.50 + 1000 * 2.50) / 1e6, 6)
    assert run_ledger.PRICES["claude-sonnet-5-5"][3] == 0.10, "Sonnet 5.5 cache hit: 0.05× input (live page)"
    assert run_ledger.PRICES_AS_OF == "2026-10-10" and run_ledger.PRICES_SOURCE.startswith("https://platform.claude.com")


# ── AC-5 ──────────────────────────────────────────────────────────────────────


def test_one_hour_cache_writes_read_from_the_sdk_usage():
    p = _ledger_path("onehour")
    usage = _Usage(i=1000, o=500, cw=2000, cr=0)
    usage.cache_creation = types.SimpleNamespace(ephemeral_5m_input_tokens=1500, ephemeral_1h_input_tokens=500)
    run_ledger.create(_Fake([_Resp("x", usage)]), step="faq", prompt="faq.md", model="claude-sonnet-4-6",
                      max_tokens=5, system="s", messages=[{"role": "user", "content": "x"}])
    (e,) = _events(p, "model_call")
    assert e["cache_write_1h_tokens"] == 500
    assert e["cost_usd"] == round((1000 * 3 + 1500 * 3.75 + 500 * 6.00 + 500 * 15) / 1e6, 6), e["cost_usd"]


def test_anomaly_is_not_a_skip():
    p = _ledger_path("anomaly")
    run_ledger.begin("publish_finalize")
    run_ledger.step("10.5", "instagram_gate")
    run_ledger.anomaly("no reels rendered")
    run_ledger.end()
    (e,) = _events(p, "step")
    assert (e["outcome"], e["detail"]) == ("anomaly", "no reels rendered")


def test_summary_default_reads_where_the_ledger_writes():
    import subprocess
    p = _ledger_path("default")
    run_ledger.bind(job_id=1, episode="E91", run_kind="prod")
    run_ledger.record("step", phase="x", step="1", name="n", outcome="ok", detail="", duration_ms=0)
    out = subprocess.run([sys.executable, "-B", str(REPO / "scripts" / "ledger_summary.py"), "E91"],
                         capture_output=True, text=True, env={**os.environ, "RUN_LOG_PATH": str(p)}).stdout
    assert str(p) in out and "1 for E91" in out, out


def test_interrupt_during_a_call_is_recorded():
    p = _ledger_path("interrupt")
    fake = _Fake([KeyboardInterrupt()])
    try:
        run_ledger.create(fake, step="faq", prompt="faq.md", model="claude-sonnet-4-6", max_tokens=5,
                          system="s", messages=[{"role": "user", "content": "x"}])
    except KeyboardInterrupt:
        pass
    (e,) = _events(p, "model_call")
    assert e["outcome"] == "error" and e["error"] == "KeyboardInterrupt"


def test_laptop_default_path_is_the_repo_not_opt():
    saved = os.environ.pop("RUN_LOG_PATH")
    try:
        got = run_ledger._path()
        if run_ledger._BOX_LEDGER.parent.parent.is_dir():
            assert got == run_ledger._BOX_LEDGER
        else:
            assert got == REPO / "state" / "run_log.jsonl", got
    finally:
        os.environ["RUN_LOG_PATH"] = saved


def test_summary_flags_cache_misses_per_job():
    import subprocess
    p = _ledger_path("summary")

    def call(job, step, read, attempt=1, model="claude-sonnet-4-6", cost=0.01):
        run_ledger.bind(job_id=job, episode="E90", run_kind="prod")
        run_ledger.record("model_call", step=step, outcome="ok", attempt=attempt, model=model, cost_usd=cost,
                          cache_read_input_tokens=read, cache_creation_input_tokens=0 if read else 9000,
                          input_tokens=9000)
    for job in (1, 2):  # a reprocess: two jobs, each with its own first write
        call(job, "brand_team.marcus", 0)
        call(job, "brand_team.robert", 9000 if job == 1 else 0)
    call(2, "faq", 0, model="claude-new-model", cost=None)
    out = subprocess.run([sys.executable, "-B", str(REPO / "scripts" / "ledger_summary.py"), "E90", "--ledger",
                          str(p)], capture_output=True, text=True).stdout
    misses = [l for l in out.splitlines() if "CACHE MISS" in l]
    assert len(misses) == 1 and "robert" in misses[0], out
    assert "NOT in the total" in out and "claude-new-model" in out, "unpriced real spend is called out"


def test_unwritable_ledger_never_fails_the_call():
    blocker = TMP / "not-a-dir"
    blocker.write_text("x")
    os.environ["RUN_LOG_PATH"] = str(blocker / "run_log.jsonl")  # a file where a directory should be
    fake = _Fake([_Resp("hello")])
    r = run_ledger.create(fake, step="title", prompt="title.md", model="claude-sonnet-4-6", max_tokens=5,
                          system="s", messages=[{"role": "user", "content": "x"}])
    assert r.content[0].text == "hello", "the call's result comes back as if nothing happened"
    clock = run_ledger.StepClock("round_1")
    clock.start("1", "x")
    clock.finish()  # nothing raised


# ── AC-6 ──────────────────────────────────────────────────────────────────────


def test_sentinel_never_reaches_the_ledger():
    import content_gates
    p = _ledger_path("sentinel")
    _with(_Fake([_Resp(json.dumps(QUOTES)) for _ in range(4)] + [_Resp(json.dumps({"hook": SENTINEL,
                                                                                    "topic": "t"}))]))
    anthropic_client.run_brand_team_chain(f"transcript {SENTINEL}", {"guest_name": SENTINEL})
    try:
        anthropic_client.compose_title(f"transcript {SENTINEL}", {"guest": SENTINEL})
    except Exception:  # noqa: BLE001 — only the ledger's contents matter here
        pass
    clock = run_ledger.StepClock("round_1")
    clock.start("11.6", "title_gate")
    try:
        raise content_gates.GateFailure(f"title {SENTINEL} is a placeholder")
    except content_gates.GateFailure as exc:
        clock.fail(exc)
    text = p.read_text()
    assert len(_events(p)) >= 6, "the ledger was written (a sentinel check over nothing proves nothing)"
    assert SENTINEL not in text, "guest/transcript/response text leaked into the ledger"


# ── AC-8 / AC-9 (Jen) ─────────────────────────────────────────────────────────


def test_run_kind_and_binding():
    p = _ledger_path("kind")
    run_ledger.bind(job_id=5, episode="E98", run_kind="eval")
    run_ledger.record("step", phase="x", step="1", name="n", outcome="ok", detail="", duration_ms=0)
    run_ledger.bind(run_kind="prod")
    run_ledger.record("step", phase="x", step="2", name="n", outcome="ok", detail="", duration_ms=0)
    a, b = _events(p)
    assert (a["run_kind"], a["job_id"], a["episode"]) == ("eval", 5, "E98")
    assert b["run_kind"] == "prod"


def test_template_sha_tracks_shape_not_text():
    base = dict(model="m", max_tokens=100, system="prompt A",
                messages=[{"role": "user", "content": [{"type": "text", "text": "episode one",
                                                         "cache_control": {"type": "ephemeral"}}]}])
    other_episode = json.loads(json.dumps(base))
    other_episode["messages"][0]["content"][0]["text"] = "episode two!"
    moved = dict(base, system=[{"type": "text", "text": "transcript", "cache_control": {"type": "ephemeral"}},
                               {"type": "text", "text": "prompt A"}])
    more_tokens = dict(base, max_tokens=200)
    sha = run_ledger.request_template_sha
    assert sha(base) == sha(dict(base, model="other")), "the model is recorded on its own"
    assert sha(base) != sha(moved), "moving the transcript into system (CACHE-01) is a new template"
    assert sha(base) != sha(more_tokens)
    assert sha(base) == sha(other_episode), "another episode's text (any length) is the same template"
    plain = dict(model="m", max_tokens=100, system="p", messages=[{"role": "user", "content": "short"}])
    plain_other = dict(plain, messages=[{"role": "user", "content": "a much longer user message " * 20}])
    assert sha(plain) == sha(plain_other), "plain-string content (title, captions…) is stable across episodes too"
    ttl_5m, ttl_1h = json.loads(json.dumps(base)), json.loads(json.dumps(base))
    ttl_5m["messages"][0]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "5m"}
    ttl_1h["messages"][0]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    assert sha(ttl_5m) != sha(ttl_1h), "a cache TTL VALUE change (5m → 1h) is a new template"
    # any string under a key that is not a known setting is blanked — tool input, metadata, a future field
    leaky = dict(base, metadata={"user_id": SENTINEL}, tools=[{"name": "t", "description": SENTINEL,
                                                                "input_schema": {"type": "object"}}])
    assert SENTINEL not in json.dumps(run_ledger._shape(leaky)), "a free-text value reached the template hash"
    # prompt_sha is the system text actually sent
    p = _ledger_path("sha")
    run_ledger.create(_Fake([_Resp("x")]), step="faq", prompt="faq.md", **base)
    (e,) = _events(p, "model_call")
    assert e["prompt_sha"] == run_ledger.sha256("prompt A") and e["max_tokens"] == 100
    # CACHE-01's shapes: a block system never puts the transcript into prompt_sha — in either cache layout
    layouts = (
        lambda tr: [{"type": "text", "text": tr, "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": "prompt A"}],
        lambda tr: [{"type": "text", "text": tr}, {"type": "text", "text": "prompt A", "cache_control": {"type": "ephemeral"}}],
    )
    for layout in layouts:
        for transcript in (f"episode one {SENTINEL}", "episode two, a different transcript"):
            run_ledger.create(_Fake([_Resp("x")]), step="faq", prompt="faq.md", **dict(base, system=layout(transcript)))
            run_ledger.create(_Fake([_Resp("x")]), step="faq", prompt="faq.md", prompt_text="prompt A",
                              **dict(base, system=layout(transcript)))
    later = _events(p, "model_call")[1:]
    assert [e["prompt_sha"] for e in later[0::2]] == [None] * 4, "block system without prompt_text: unknown, not guessed"
    assert {e["prompt_sha"] for e in later[1::2]} == {run_ledger.sha256("prompt A")}, "prompt_text is what's hashed"
    assert SENTINEL not in p.read_text()
    doc = json.loads(json.dumps(base))
    doc["messages"][0]["content"][0]["type"] = "document"
    assert sha(base) != sha(doc), "a block TYPE change is a new template"


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
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
