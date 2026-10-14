"""
run_ledger.py — one run ledger: every step's time and outcome, every model call's tokens and cost (OBS-01).

Before this, the pipeline could not say what an episode costs, whether the transcript cache hits, or where the
time goes: no code read `response.usage`, and state/run_log.jsonl held one line per job that exhausted its
retries — nothing else. Every event now appends to that same file, next to the old job lines:

  {"event": "model_call", "step": "round_1.brand_team.robert", "model": …, "prompt": …, "prompt_sha": …,
   "request_template_sha": …, "response_model": …, "temperature": …, "input_tokens": …, "output_tokens": …,
   "cache_creation_input_tokens": …, "cache_read_input_tokens": …, "stop_reason": …, "latency_ms": …,
   "attempt": …, "outcome": "ok"|"error", "cost_usd": …}
  {"event": "step", "phase": "round_1"|"publish_finalize", "step": "12b", "name": "quote_cards",
   "outcome": "ok"|"skipped"|"gate_failure"|"permanent_failure"|"error", "detail": …, "duration_ms": …}

Every event also carries ts, job_id, episode and run_kind ("prod" | "eval" | "replay" | "manual"): an episode's
cost sums run_kind "prod" only (Jen, EVAL-00); "manual" is a hand-run tool such as run_dr_joe.py.

Rules:
  • NEVER record a transcript, a prompt body, a response body or a credential — lengths and hashes only
    (Orly C-4; a sentinel test proves it).
  • A ledger write that fails logs a warning and NEVER fails the job: observability must not become a new way
    to strand an episode (AC-5, the R1-01 failure class).
  • Cost comes from ONE price table, dated and sourced. A model not in it costs None — unknown, never 0.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from rockstar import config

logger = logging.getLogger(__name__)

# ── prices: USD per million tokens ────────────────────────────────────────────
# Copied 2026-10-10 from https://platform.claude.com/docs/en/about-claude/pricing (base input, 5-minute cache
# write, cache hit, output). A price change is a one-line edit here; EVAL-02 adds rows before any switch.
PRICES_AS_OF = "2026-10-10"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
# Opus 5.5 and Sonnet 5.5 cache hits are 0.05× input (the page's footnote 2), not the usual 0.1× — Sonnet 5.5's
# $0.10 is right, re-checked on the live page 2026-10-10 against an older cached $0.20.
PRICES = {
    #                     input  cache_write_5m  cache_write_1h  cache_read  output
    "claude-sonnet-4-6": (3.00, 3.75, 6.00, 0.30, 15.00),
    "claude-sonnet-5-5": (2.00, 2.50, 4.00, 0.10, 10.00),
    "claude-opus-5-5": (4.00, 5.00, 8.00, 0.20, 20.00),
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),
}
# Haiku 5.5 is priced higher once a prompt passes 100K tokens (input + cache reads + cache writes).
LONG_PROMPT_TOKENS = 100_000
LONG_PROMPT_PRICES = {"claude-haiku-5-5": (0.50, 0.625, 1.00, 0.05, 2.50)}

# ── context: which job, which episode, what kind of run ──────────────────────
_JOB = contextvars.ContextVar("ledger_job_id", default=None)
_EPISODE = contextvars.ContextVar("ledger_episode", default=None)
_RUN_KIND = contextvars.ContextVar("ledger_run_kind", default=None)


def bind(*, job_id=None, episode=None, run_kind=None) -> None:
    """Tag every later event in this context. Call again when the episode becomes known (jobs start as 'TBD')."""
    if job_id is not None:
        _JOB.set(job_id)
    if episode is not None:
        _EPISODE.set(episode)
    if run_kind is not None:
        _RUN_KIND.set(run_kind)


_BOX_LEDGER = Path("/opt/rockstar-pipeline/state/run_log.jsonl")


def _path() -> Path:
    """RUN_LOG_PATH, else the box's ledger on the box, else the repo's own state/ (gitignored) on a laptop —
    a hand-run tool on the Mac must not write toward /opt and get only a warning (cold review)."""
    if os.environ.get("RUN_LOG_PATH"):
        return Path(os.environ["RUN_LOG_PATH"])
    if _BOX_LEDGER.parent.parent.is_dir():
        return _BOX_LEDGER
    return config.CODE_ROOT / "state" / "run_log.jsonl"


def record(event: str, **fields) -> None:
    """Append one event. Never raises."""
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "event": event,
        "job_id": _JOB.get(),
        "episode": _EPISODE.get(),
        "run_kind": _RUN_KIND.get() or os.environ.get("RUN_KIND", "prod"),
        **fields,
    }
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 — see module docstring: never fail the job
        logger.warning("run_ledger: could not write %s event (%s: %s)", event, type(exc).__name__, exc)


# ── model calls ───────────────────────────────────────────────────────────────


def sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


# An ALLOWLIST of keys whose string values are settings the sha must see (a TTL change is a new template).
# Every other string — text, content, tool input, metadata, anything a future request adds — is replaced by a
# constant: a denylist would let a new free-text field through verbatim (re-review 2, Orly: C-4).
_ENUM_KEYS = ("type", "role", "ttl", "media_type")


def _shape(value, key=None):
    """The request with every piece of text replaced by a constant: what the request LOOKS like, never what it
    says or how long it is. Hashing this gives request_template_sha — it changes when the structure changes (a
    block moved into `system`, a cache breakpoint added, max_tokens raised), never with the episode (cold
    review, Jen: lengths made it differ on every call)."""
    if isinstance(value, dict):
        return {k: _shape(v, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v, key) for v in value]
    if isinstance(value, str):
        return value if key in _ENUM_KEYS else "<str>"
    return value


def request_template_sha(kwargs: dict) -> str:
    return sha256(json.dumps(_shape({k: v for k, v in kwargs.items() if k != "model"}), sort_keys=True))


def cost_usd(model: str | None, usage: dict) -> float | None:
    """USD for one call from PRICES; None when the model has no price row (unknown is not zero)."""
    row = PRICES.get(model or "")
    if row is None or usage.get("input_tokens") is None:
        return None
    prompt = sum(usage.get(f) or 0 for f in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    if prompt > LONG_PROMPT_TOKENS and (model or "") in LONG_PROMPT_PRICES:
        row = LONG_PROMPT_PRICES[model]
    p_in, p_write_5m, p_write_1h, p_read, p_out = row
    # A 1-hour cache write costs more than a 5-minute one; usage reports the split when there is one.
    writes = usage.get("cache_creation_input_tokens") or 0
    writes_1h = usage.get("cache_write_1h_tokens") or 0
    total = (
        (usage.get("input_tokens") or 0) * p_in
        + (writes - writes_1h) * p_write_5m
        + writes_1h * p_write_1h
        + (usage.get("cache_read_input_tokens") or 0) * p_read
        + (usage.get("output_tokens") or 0) * p_out
    ) / 1_000_000
    return round(total, 6)


_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def create(client, *, step: str, prompt: str | None = None, prompt_text: str | None = None, attempt: int = 1, **kwargs):
    """client.messages.create(**kwargs), recorded. The ONE way the pipeline calls the model (PQ-4).

    Writes exactly one model_call event per call — including a call that raises (outcome "error", tokens null),
    which then re-raises unchanged, so the caller's retry and PermanentFailure behaviour is exactly as before.
    `prompt` is the prompt FILE name. prompt_sha fingerprints that prompt's text: the string `system` when the
    system is a plain string (every call site today), else `prompt_text` as the caller passes it. A block-list
    system without `prompt_text` records prompt_sha None — unknown, never guessed from block order (re-review 3:
    a cache_control filter takes the transcript in the usual [transcript, prompt(cache_control)] layout)."""
    system = kwargs.get("system")
    # prompt_sha fingerprints the PROMPT, never the episode: an exact hash of a transcript is a way to confirm a
    # guessed one (C-4). CACHE-01 must pass prompt_text when it moves the transcript into `system`.
    if prompt_text is not None:
        system_text = prompt_text
    elif isinstance(system, str):
        system_text = system
    else:
        system_text = ""
    base = {
        "step": step,
        "model": kwargs.get("model"),
        "prompt": prompt,
        "prompt_sha": sha256(system_text) if system_text else None,
        "request_template_sha": request_template_sha(kwargs),
        "temperature": kwargs.get("temperature"),  # None = the API default (1.0) — recorded, not changed
        "max_tokens": kwargs.get("max_tokens"),
        "attempt": attempt,
    }
    # The SDK retries a failed HTTP call itself (max_retries, 2 by default) before create() returns or raises:
    # one event is one create(), and latency_ms is its wall time INCLUDING any SDK retries and their backoff.
    base["sdk_max_retries"] = getattr(client, "max_retries", None)
    started = time.monotonic()
    try:
        response = client.messages.create(**kwargs)
    except BaseException as exc:  # Ctrl-C and SystemExit too: an interrupted call still cost something
        record(
            "model_call",
            **base,
            outcome="error",
            error=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=None,
            **{f: None for f in _USAGE_FIELDS},
            cache_write_1h_tokens=None,
            stop_reason=None,
            response_model=None,
        )
        raise
    usage_obj = getattr(response, "usage", None)
    usage = {f: getattr(usage_obj, f, None) for f in _USAGE_FIELDS}
    split = getattr(usage_obj, "cache_creation", None)  # {ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}
    usage["cache_write_1h_tokens"] = getattr(split, "ephemeral_1h_input_tokens", None) if split is not None else None
    response_model = getattr(response, "model", None)
    record(
        "model_call",
        **base,
        outcome="ok",
        latency_ms=int((time.monotonic() - started) * 1000),
        stop_reason=getattr(response, "stop_reason", None),
        response_model=response_model,
        cost_usd=cost_usd(kwargs.get("model"), usage),
        **usage,
    )
    return response


# ── steps ─────────────────────────────────────────────────────────────────────


def _gate_name(exc: BaseException) -> str:
    """The content_gates function that raised a GateFailure, read from the traceback (the gate's own name)."""
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if frame.filename.endswith("rockstar/gates/content_gates.py"):
            return frame.name
    return type(exc).__name__


class StepClock:
    """Step events with one line per step heading, instead of re-indenting every step under a `with`.

        steps = run_ledger.StepClock("publish_finalize")
        steps.start("4", "download")        # closes the previous step as ok
        steps.skip("12b", "quote_cards", "cards/ not empty")
        steps.finish()                      # closes the last step as ok
        steps.fail(exc)                     # in the job's except: closes the running step with its outcome

    Every method is safe to call in any order, and none of them ever raises."""

    def __init__(self, phase: str):
        self.phase = phase
        self._current = None  # (step, name, started)
        self._held = None  # OBS-02: the running step caught a failure and carried on; it closes as "anomaly"

    def _close(self, outcome: str, detail: str = "") -> None:
        held, self._held = self._held, None
        if self._current is None:
            return
        if held is not None and outcome in ("ok", "skipped"):
            # OBS-02: a step that caught a failure and carried on never closes as ok (E6's 12b did).
            outcome, detail = "anomaly", held
        step, name, started = self._current
        self._current = None
        record(
            "step",
            phase=self.phase,
            step=step,
            name=name,
            outcome=outcome,
            detail=detail[:200],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def start(self, step: str, name: str) -> None:
        try:
            self._close("ok")
            self._current = (step, name, time.monotonic())
        except Exception:  # noqa: BLE001
            logger.warning("run_ledger: step clock failed at %s", step)

    def skip(self, step: str, name: str, reason: str) -> None:
        try:
            self._close("ok")
            record(
                "step", phase=self.phase, step=step, name=name, outcome="skipped", detail=reason[:200], duration_ms=0
            )
        except Exception:  # noqa: BLE001
            logger.warning("run_ledger: step clock failed at %s", step)

    def hold(self, reason: str) -> None:
        """The running step caught a failure and carries on: it will close as "anomaly" with this reason, at its real
        end, so its duration stays whole. The first reason wins; a later failure (fail()) outranks it.
        Needs a running step: an anomaly after skipped() closed the step, or outside any step, is dropped
        (none today)."""
        try:
            if self._current is not None and self._held is None:
                self._held = reason[:200]
        except Exception:  # noqa: BLE001
            logger.warning("run_ledger: step clock failed holding an anomaly")

    def skipped(self, reason: str) -> None:
        """The running step turned out to have nothing to do: close it as skipped, with the reason."""
        try:
            self._close("skipped", reason)
        except Exception:  # noqa: BLE001
            logger.warning("run_ledger: step clock failed marking a skip")

    def finish(self) -> None:
        try:
            self._close("ok")
        except Exception:  # noqa: BLE001
            logger.warning("run_ledger: step clock failed at finish")

    def current(self) -> str:
        """The running step as "8 transcribe_clips", or "" between steps."""
        return f"{self._current[0]} {self._current[1]}" if self._current else ""

    def fail(self, exc: BaseException) -> None:
        """Close the running step with what happened. Class names decide: GateFailure → gate_failure (named),
        PermanentFailure → permanent_failure, anything else → error (the worker retries it)."""
        try:
            _FAILED_STEP.set(self.current())
            # Names only, never the exception's message: a gate's message can quote a title, a guest's name or
            # a slice of model output (C-4). The journal and the alert carry the full text.
            # A job may wrap a GateFailure in its own PermanentFailure (publish_finalize does): the gate is the
            # cause that matters, so look down the chain for it.
            cause = exc
            while cause is not None and type(cause).__name__ != "GateFailure":
                cause = cause.__cause__
            if cause is not None:
                exc = cause
            kind = type(exc).__name__
            if kind == "GateFailure":
                self._close("gate_failure", _gate_name(exc))
            elif kind == "PermanentFailure":
                self._close("permanent_failure", kind)
            else:
                self._close("error", kind)
        except Exception:  # noqa: BLE001
            logger.warning("run_ledger: step clock failed recording a failure")


# ── the job's clock, so a step heading anywhere in the job is one line ────────
_CLOCK = contextvars.ContextVar("ledger_clock", default=None)
# The step a job died in, kept after its clock closes so the worker can name it in an alert (CLIP-01a).
_FAILED_STEP = contextvars.ContextVar("ledger_failed_step", default="")


def episode() -> str | None:
    """The episode this context is bound to (an alert's subject line)."""
    return _EPISODE.get()


def current_step() -> str:
    """The running step as "8 transcribe_clips", or "" when no clock is running."""
    clock = _CLOCK.get()
    return clock.current() if clock is not None else ""


def failed_step() -> str:
    """The step the last job failed in, or "" when it failed outside a step (or hasn't failed)."""
    return _FAILED_STEP.get()


def begin(phase: str) -> StepClock:
    """Start a job's step clock (worker → job.run). Later step()/skip() calls in the same context use it."""
    _FAILED_STEP.set("")
    clock = StepClock(phase)
    _CLOCK.set(clock)
    return clock


def step(step_id: str, name: str) -> None:
    clock = _CLOCK.get()
    if clock is not None:
        clock.start(step_id, name)


def skip(step_id: str, name: str, reason: str) -> None:
    clock = _CLOCK.get()
    if clock is not None:
        clock.skip(step_id, name, reason)


def anomaly(reason: str) -> None:
    """The running step met something that should not happen but does not stop the job: it closes as "anomaly",
    with a fixed reason, when it ends. Distinct from a skip — a skip is nothing to do; an anomaly is something wrong.
    A fixed string from our code, never data (OBS-02 AC 2)."""
    clock = _CLOCK.get()
    if clock is not None:
        clock.hold(reason)


def alert_failed(site: str, subject: str, exc: BaseException) -> None:
    """An internal alert that never went out (OBS-02 AC 5). The site is a fixed name from our code; the subject is
    recorded only as a hash, since it can carry an episode's data. Never raises."""
    try:
        # step: the step the alert was about (a fixed name), so E6's question — did 12b's card alert go out? — has an
        # answer. The worker's dead-job alert comes after the job's clock closed, so it falls back to the failed step.
        record(
            "alert_failed",
            site=site,
            step=current_step() or failed_step(),
            subject_sha=sha256(subject),
            error=type(exc).__name__,
        )
    except Exception:  # noqa: BLE001
        logger.warning("run_ledger: could not record a failed alert")


def skipped(reason: str) -> None:
    """Mark the running step skipped (a fixed reason from our own code — never data)."""
    clock = _CLOCK.get()
    if clock is not None:
        clock.skipped(reason)


def end(exc: BaseException | None = None) -> None:
    """Close the job's clock: the running step as ok, or with the failure's outcome."""
    clock = _CLOCK.get()
    if clock is not None:
        clock.fail(exc) if exc is not None else clock.finish()
        _CLOCK.set(None)
