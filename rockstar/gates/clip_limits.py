"""
clip_limits.py — how long a quote may be (CLIP-01c). Each number lives in one place and is derived from the code that
uses it, never typed in twice.

  reel_caps()         90: a standard reel (the Reels-tab cap; render_reel_PRD 3g, John 2026-10-12)
                      180: a `long_form` reel, under 3 minutes. Both are read from verify_reel_spec.js SPEC, the gate.
                      John, 2026-10-12: "we should now be able to handle up to 180 seconds - but only if it's
                      really a good quote". Lisa marks it, with a written reason.
  reel_frame_sec()    the most a reel adds around the quote: render_reel.js RENDER_CONFIG (opening, lead-in, tail,
                      closing) plus one bar of the theme when the closing snaps to the beat
  clip_padding_sec()  what extract_quote_clips.sh adds around the quote for the clip WhisperX transcribes
  load()              the ceilings for this box: min(editorial, box). Refuses (LimitsMissing) without box-limits.env

Phase 1 measures a quote by its SEGMENT SPAN (tape_check end_ms − start_ms): first run's segment start to last run's
segment end. The clip Phase 2 cuts is the whole window, gaps between joins included, so the span is the right
measure, not the summed runs. The fast transcript's segments run ~24 s, so the span can read long (up to ~50 s); it
was never more than SPAN_SHORT_SEC short of Phase 2's forced alignment (10 quotes, E13 and E29, 2026-10-12). John chose
the safe reading (2026-10-12): a quote that may be over gets Lisa's one pass, and nothing over reaches the box.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from rockstar import config

_ROOT = config.CODE_ROOT
_SPEC = _ROOT / "reels" / "verify_reel_spec.js"


def _spec_seconds(key: str) -> int:
    """The reel caps live in ONE place, the gate that enforces them: verify_reel_spec.js SPEC."""
    m = re.search(rf"\b{key}:\s*([0-9]+)", _SPEC.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"verify_reel_spec.js SPEC has no {key}")
    return int(m.group(1))


def reel_caps() -> tuple[int, int]:
    """(standard, long_form) reel caps, 90 and 180. Read when the limits load, never at import: a missing script
    then fails the clip steps, not every job's import (Toby's review)."""
    return _spec_seconds("maxDurationSec"), _spec_seconds("longFormMaxDurationSec")


SPAN_SHORT_SEC = 1.5  # the most the segment span read short of the aligned window (measured, see above)
LONG_REASON_MIN = 20  # a long_form reason is a sentence, not a word

_RENDER = _ROOT / "reels" / "render_reel.js"
_EXTRACT = _ROOT / "scripts" / "extract_quote_clips.sh"
LIMITS_FILE = "box-limits.env"
MAKE_LIMITS = "scripts/measure_clip_limit.sh --measure, then --write --reserve-mb 600 (CLIP-01b)"


class LimitsMissing(RuntimeError):
    """No measured box limit: the clip steps don't start on a guess (CLIP-01 AC 3)."""


def _config_number(src: str, key: str) -> float:
    m = re.search(rf"\b{key}:\s*([0-9.]+)", src)
    if not m:
        raise ValueError(f"render_reel.js RENDER_CONFIG has no {key}")
    return float(m.group(1))


def reel_frame_sec(render_js: Path = _RENDER, brand_assets: Path | None = None) -> float:
    """Worst-case seconds a reel adds to its quote. The closing snaps to the first bar boundary at or after its minimum
    length, so it can run up to one bar over (findBeatSnapPoint); without theme_bpm.json render doesn't snap."""
    src = render_js.read_text(encoding="utf-8")
    frame = (
        _config_number(src, "openingDurationSec")
        + _config_number(src, "leadInMs") / 1000
        + _config_number(src, "tailMs") / 1000
        + _config_number(src, "closingDurationSec")
    )
    if re.search(r"\bbeatSnapEnabled:\s*true", src):
        bpm = (brand_assets or _brand_assets()) / "audio" / "theme_bpm.json"
        if bpm.exists():
            frame += 4 * float(json.loads(bpm.read_text(encoding="utf-8"))["beat_period_sec"])
    return frame


def clip_padding_sec(extract_sh: Path = _EXTRACT) -> float:
    src = extract_sh.read_text(encoding="utf-8")
    vals = {k: re.search(rf"^{k}=([0-9]+)", src, re.M) for k in ("LEAD_IN_MS", "TAIL_MS")}
    if not all(vals.values()):
        raise ValueError("extract_quote_clips.sh has no LEAD_IN_MS / TAIL_MS")
    return sum(int(m.group(1)) for m in vals.values()) / 1000


def _brand_assets() -> Path:  # read at call time, the same default render_reel's caller passes (publish_finalize)
    return config.brand_assets_dir()


def limits_path(state_dir: Path | None = None) -> Path:
    """STATE_DIR/box-limits.env. BOX_LIMITS_FILE names another file only with BOX_LIMITS_TEST=1 (the test suite's
    fixture): a stray variable can't redirect a production process (Toby's review)."""
    if state_dir is None and os.environ.get("BOX_LIMITS_TEST") == "1" and os.environ.get("BOX_LIMITS_FILE"):
        return Path(os.environ["BOX_LIMITS_FILE"])
    return (state_dir or config.state_dir()) / LIMITS_FILE


def read_box_limits(state_dir: Path | None = None) -> dict[str, str]:
    path = limits_path(state_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LimitsMissing(
            f"{path} is missing or unreadable ({type(exc).__name__}). Create it on the box: {MAKE_LIMITS}"
        ) from exc
    env = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
    try:
        if int(env["CLIP_MAX_SECONDS"]) <= 0 or int(env["CLIP_BATCH_SIZE"]) <= 0:
            raise ValueError("not positive")
    except (KeyError, ValueError) as exc:
        raise LimitsMissing(
            f"{path} has no usable CLIP_MAX_SECONDS / CLIP_BATCH_SIZE ({exc}). Re-create it: {MAKE_LIMITS}"
        ) from exc
    return env


@dataclass(frozen=True)
class Limits:
    standard_s: int  # a quote's ceiling for a standard reel
    long_s: int  # a long_form quote's ceiling
    clip_max_s: int  # the box: the longest clip WhisperX may be given (CLIP_MAX_SECONDS)
    padding_s: float  # the clip around the quote (extract_quote_clips.sh)
    frame_s: float  # the reel around the quote (render_reel.js)
    reel_max_s: int = 90  # verify_reel_spec.js SPEC, read by load()
    long_reel_max_s: int = 180

    def for_quote(self, quote: dict) -> int:
        return self.long_s if is_long_form(quote) else self.standard_s

    def reel_max_for(self, quote: dict) -> int:
        return self.long_reel_max_s if is_long_form(quote) else self.reel_max_s

    def as_dict(self) -> dict:
        return {
            "standard_s": self.standard_s,
            "long_s": self.long_s,
            "clip_max_s": self.clip_max_s,
            "padding_s": self.padding_s,
            "frame_s": round(self.frame_s, 3),
        }


def load(state_dir: Path | None = None, brand_assets: Path | None = None) -> Limits:
    """min(editorial, box), each less the span's measured shortfall, whole seconds down."""
    box = int(read_box_limits(state_dir)["CLIP_MAX_SECONDS"])
    frame, pad = reel_frame_sec(brand_assets=brand_assets), clip_padding_sec()
    box_quote = box - pad - SPAN_SHORT_SEC

    def ceiling(reel_max: int) -> int:
        return max(0, math.floor(min(reel_max - frame - SPAN_SHORT_SEC, box_quote)))

    reel_max, long_max = reel_caps()
    return Limits(ceiling(reel_max), ceiling(long_max), box, pad, frame, reel_max, long_max)


def is_long_form(quote: dict) -> bool:
    """Lisa's mark: long_form true AND a written reason. A mark with no reason is no mark."""
    return quote.get("long_form") is True and len(str(quote.get("long_form_reason") or "").strip()) >= LONG_REASON_MIN


def clear_bare_long_form(quotes: list[dict]) -> list[str]:
    """A long_form flag without Lisa's reason counts for nothing: cleared, so every reader (the gate, Phase 2's checks,
    render_reel.js's --long-form, step 10.5) sees one rule. Returns the ids cleared, to be said."""
    cleared = []
    for i, q in enumerate(quotes):
        if q.get("long_form") and not is_long_form(q):
            q["long_form"] = False
            cleared.append(_qname(q, i))
    return cleared


def span_sec(quote: dict) -> float | None:
    """Phase 1's reading (segment span), or None when the quote has no placement."""
    tc = quote.get("tape_check") or {}
    s, e = tc.get("start_ms"), tc.get("end_ms")
    if not isinstance(s, (int, float)) or not isinstance(e, (int, float)) or e <= s:
        return None
    return (e - s) / 1000


def too_long(quote: dict, seconds: float | None, limits: Limits) -> str | None:
    """Why the quote is over its ceiling, in words for Lisa and the standup; None when it fits."""
    if seconds is None:
        return "no length: not placed on the tape, so it can't be shown to fit"
    cap = limits.for_quote(quote)
    if seconds <= cap:
        return None
    if is_long_form(quote) or seconds > limits.long_s:
        return (
            f"too long: reads up to {seconds:.0f} s (segment bounds), the {'long_form ' if is_long_form(quote) else ''}"
            f"ceiling is {cap if is_long_form(quote) else limits.long_s} s. Cut it, or swap"
        )
    return (
        f"over the standard reel ceiling: reads up to {seconds:.0f} s (segment bounds), ceiling {cap} s. Cut it or "
        f"swap; or, only if it's really a good quote, keep it whole as long_form (up to {limits.long_s} s) with "
        f"long_form_reason saying why"
    )


# ── Phase 2: the same limit, checked before money is spent (CLIP-01 AC 10, 11) ──────────────────────────────────────


def _qname(q: dict, i: int) -> str:
    return str(q.get("id") or f"#{i + 1}")


def before_spend(quotes: list[dict], limits: Limits) -> tuple[list[str], list[str]]:
    """Before step 6 (faq_blog) and 7a-0 (alignment): Phase 1's lengths against today's ceilings. Returns (problems,
    unmeasured): a quote with no Phase 1 length (a hand-edited quotes.json, or one from before CLIP-01c) is checked at
    step 7 on its aligned window instead, and named."""
    problems, unmeasured = [], []
    for i, q in enumerate(quotes):
        s = span_sec(q)
        if s is None:
            unmeasured.append(_qname(q, i))
        elif s > limits.for_quote(q):
            problems.append(
                f"{_qname(q, i)}: reads up to {s:.0f} s, ceiling {limits.for_quote(q)} s"
                f"{' (long_form)' if is_long_form(q) else ''}"
            )
    return problems, unmeasured


def span_vs_aligned(quotes: list[dict]) -> list[dict]:
    """Each quote's Phase 1 span beside its aligned window: the evidence SPAN_SHORT_SEC is re-derived from (ledger)."""
    out = []
    for i, q in enumerate(quotes):
        s, e, span = q.get("transcript_start_ms"), q.get("transcript_end_ms"), span_sec(q)
        if span is not None and isinstance(s, (int, float)) and isinstance(e, (int, float)) and e > s:
            out.append(
                {
                    "id": _qname(q, i),
                    "span_s": round(span, 1),
                    "aligned_s": round((e - s) / 1000, 1),
                    "short_s": round((e - s) / 1000 - span, 1),
                }
            )
    return out


def after_alignment(quotes: list[dict], limits: Limits) -> list[str]:
    """After 7a: each quote's aligned window (the clip WhisperX gets, the reel's body) against the reel and the box."""
    problems = []
    for i, q in enumerate(quotes):
        s, e = q.get("transcript_start_ms"), q.get("transcript_end_ms")
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)) or e <= s:
            continue  # no window: extract_quote_clips.sh skips it and says so
        w = (e - s) / 1000
        if w + limits.frame_s > limits.reel_max_for(q):
            problems.append(
                f"{_qname(q, i)}: {w:.1f} s makes a reel over {limits.reel_max_for(q)} s (frame {limits.frame_s:.1f} s)"
            )
        elif w + limits.padding_s > limits.clip_max_s:
            problems.append(
                f"{_qname(q, i)}: a {w + limits.padding_s:.1f} s clip, over the box's {limits.clip_max_s} s"
            )
    return problems


def manifest_problems(manifest: dict, limits: Limits) -> list[str]:
    """Step 8, before WhisperX loads: each cut clip against the box's measured limit."""
    out = []
    for c in manifest.get("clips", []):
        secs = (c.get("clip_end_ms", 0) - c.get("clip_start_ms", 0)) / 1000
        if secs > limits.clip_max_s:
            out.append(f"quote {c.get('quote_index')}: clip {secs:.1f} s, box limit {limits.clip_max_s} s")
    return out


def spec_args(reels: list, quotes: list[dict]) -> list[str]:
    """verify_reel_spec.js's arguments: `--long-form` before each reel whose quote is long_form
    (reel_NN = its index)."""
    long_idx = {f"{int(q['index']):02d}" for q in quotes if is_long_form(q) and q.get("index") is not None}
    args: list[str] = []
    for r in reels:
        m = re.match(r"reel_(\d+)_", Path(r).name)
        args += (["--long-form"] if m and m.group(1) in long_idx else []) + [str(r)]
    return args
