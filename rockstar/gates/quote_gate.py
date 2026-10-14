"""
quote_gate.py — Round 1's verbatim gate: every quote is what the speaker said, or it's restored, swapped or dropped
(QUOTE-01 B and C; ST-10). CLIP-01c's length check joins the same single pass.

  enforce(quotes, segments, episode_code, repair, limits=None) -> (kept_quotes, report)

Each quote is placed on the transcript with quote_tape (the one tape check). Then:
  not_verbatim  → Lisa's ONE restore-or-swap pass (`repair`, given each failing id with its reasons). Her replacement
                  is checked again; still not_verbatim (or left out by her) → dropped, and recorded.
  listen        → kept, and listed for a person (a join, an unruled mishear, mixed speakers, a dropped negation).
  verbatim      → kept.
With `limits` (CLIP-01c, clip_limits.load()), length joins the same single pass: a quote whose segment span is over its
ceiling (standard, or long_form with Lisa's written reason) is a problem too. Her cut, swap or long_form mark is checked
again, words and length; still over → dropped and recorded.
Every kept quote carries `tape_check` (verdict, reasons, placement) so a person and the next steps can read it.

Checked by position, never by a model's id (Toby's review): a missing or repeated id is renumbered first (`qNN`, the
first free number), so one quote's placement can never stand for another's. The speaker is checked too: a name whose
quotes sit under more than one diarised label, or two names on one label, is `listen` for every quote of theirs
(Round 1 has no reliable name-to-label map, so a person rules).
Known limit (named, not fixed): a guest with one quote, or whose every quote sits under the host's label, passes —
with no anchor for the host's label, one label per name proves nothing. The fix is an anchor (the label of the segment
where the host names herself, or one recorded at intake): QUOTE-04.
A pass that fails to parse drops its quotes; any other error (the API down, after the SDK's own retries) is raised,
so Round 1 fails with a standup line, and a re-run (re-drop the audio, by hand) keeps them.
Nothing writes transcript_start_ms / transcript_end_ms: Phase 2's lookup skips quotes that have them, and its
word-level timing (forced alignment) is more exact than this segment-level placement.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

from rockstar.gates import clip_limits, quote_tape, summary_norm
from rockstar.gates.host_identity import is_host


def _check(quote: dict, tape: quote_tape.Tape) -> quote_tape.Placement:
    return quote_tape.place(quote.get("text", ""), tape)


def _renumber(quotes: list[dict]) -> list[dict]:
    """A missing or repeated id gets the first free `qNN`. Returns what changed: [{"index", "was", "now"}]."""
    taken = {str(q.get("id")) for q in quotes if q.get("id")}
    seen: set[str] = set()
    changed, n = [], 0
    for i, q in enumerate(quotes):
        qid = str(q.get("id")) if q.get("id") else None
        if qid and qid not in seen:
            seen.add(qid)
            q["id"] = qid
            continue
        n += 1
        while f"q{n:02d}" in taken:
            n += 1
        new = f"q{n:02d}"
        taken.add(new)
        seen.add(new)
        changed.append({"index": i, "was": q.get("id"), "now": new})
        q["id"] = new
    return changed


def _speaker_doubts(quotes: list[dict], placed: list[quote_tape.Placement]) -> list[list[str]]:
    """Per quote: why its words may not be its speaker's. A name is trusted with a label only when ALL its single-label
    quotes sit under that one label, and no other name shares it. A majority can't decide: Marcus often picks no host
    quote, so three host lines given to the guest would outvote her one real line (Toby's re-review)."""
    labels: dict[str, set[str]] = {}
    for q, p in zip(quotes, placed, strict=False):
        if p.found and len(p.speakers) == 1:
            labels.setdefault(str(q.get("speaker") or "?"), set()).add(p.speakers[0])
    owner = Counter(lab for labs in labels.values() for lab in labs)
    doubts = []
    for q, _p in zip(quotes, placed, strict=False):
        name = str(q.get("speaker") or "?")
        mine, why = labels.get(name, set()), []
        if len(mine) > 1:
            why.append(f"{name}'s quotes sit under {len(mine)} labels ({', '.join(sorted(mine))}): listen")
        elif mine and owner[next(iter(mine))] > 1:
            why.append(f"{next(iter(mine))} is also another name's label: listen that {name} said it")
        doubts.append(why)
    return doubts


def _record(quote: dict, p: quote_tape.Placement, doubts: list[str] | None = None) -> dict:
    verdict = p.verdict
    if doubts and verdict == "verbatim":
        verdict = "listen"
    quote["tape_check"] = {
        "verdict": verdict,
        "reasons": p.reasons + (doubts or []),
        "start_ms": p.start_ms,
        "end_ms": p.end_ms,
        "spoken_ms": p.spoken_ms,
        "joins": p.joins,
        "speakers": p.speakers,
        "precision": "segment",
    }
    return quote


PENDING_REASON = (
    "Kept whole by the code, not by Lisa: still over the standard reel ceiling after her pass. "
    "A person approves or drops it before posting (long_form pending)."
)


def _span(p: quote_tape.Placement) -> float | None:
    return (p.end_ms - p.start_ms) / 1000 if p.found and p.end_ms > p.start_ms else None


def _length_problem(q: dict, p: quote_tape.Placement, limits: clip_limits.Limits | None) -> str | None:
    if limits is None or p.verdict == "not_verbatim":
        return None
    return clip_limits.too_long(q, _span(p), limits)


def enforce(
    quotes: list[dict],
    segments: list[dict],
    episode_code: str,
    repair: Callable[[dict[str, list[str]]], list[dict]],
    limits: clip_limits.Limits | None = None,
) -> tuple[list[dict], dict]:
    """The gate. `repair(problems)` is Lisa's single pass: {id: [reasons]} → her replacement quotes for those ids."""
    tape = quote_tape.Tape(segments)
    report: dict = {"checked": len(quotes), "repaired": [], "dropped": [], "listen": []}
    renumbered = _renumber(quotes)
    if renumbered:
        report["renumbered"] = renumbered
    placed = [_check(q, tape) for q in quotes]
    problems = {q["id"]: p.reasons for q, p in zip(quotes, placed, strict=False) if p.verdict == "not_verbatim"}
    too_long: set[str] = set()
    for q, p in zip(quotes, placed, strict=False):
        why = _length_problem(q, p, limits)
        if why:
            problems[q["id"]] = [why]
            too_long.add(q["id"])
    if limits is not None:
        report["ceiling"] = limits.as_dict()
    replacements: dict[str, dict] = {}
    if problems:
        try:
            for q in repair(problems):
                if isinstance(q, dict) and q.get("id") in problems:
                    replacements.setdefault(q["id"], q)  # one replacement per id: the first; it's checked again
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # her reply didn't parse: drop, recorded
            report["repair_error"] = f"{type(exc).__name__}: {exc}"[:200]
    survivors, checks = [], []
    for q, p in zip(quotes, placed, strict=False):
        qid = q["id"]
        if qid not in problems:
            survivors.append(q)
            checks.append(p)
            continue
        new = replacements.get(qid)
        if new is None:
            what = "too long" if qid in too_long else "not verbatim"
            span1 = _span(p)
            report["dropped"].append(
                {
                    "id": qid,
                    "why": f"{what}; Lisa dropped it or the pass failed",
                    "reasons": problems[qid],
                    "span_s": round(span1, 1) if span1 is not None else None,
                }
            )
            continue
        p2 = _check(new, tape)
        if p2.verdict == "not_verbatim":
            report["dropped"].append({"id": qid, "why": "still not verbatim after Lisa's pass", "reasons": p2.reasons})
            continue
        long_why = _length_problem(new, p2, limits)
        span2 = _span(p2)
        if long_why and span2 is not None and span2 <= limits.long_s and not clip_limits.is_long_form(new):
            # John, 2026-10-12 (E13): a quote Lisa kept but couldn't bring under the standard ceiling is not dropped.
            # It's kept whole as long_form PENDING: a person approves it or drops it ("only if it's really a good
            # quote"). The segment-span reading can overstate a cut by most of a segment at each end.
            new["long_form"], new["long_form_reason"], new["long_form_pending"] = True, PENDING_REASON, True
            report.setdefault("long_form_pending", []).append({"id": qid, "span_s": round(span2, 1)})
            long_why = None
        if long_why:
            report["dropped"].append(
                {
                    "id": qid,
                    "why": "still too long after Lisa's pass",
                    "reasons": [long_why],
                    "span_s": round(span2, 1) if span2 is not None else None,
                }
            )
            continue
        report["repaired"].append({"id": qid, "was": problems[qid]})
        survivors.append(new)
        checks.append(p2)
    doubts = _speaker_doubts(survivors, checks)
    for q, d in zip(survivors, doubts, strict=False):
        if q.get("long_form_pending"):
            d.append(
                "kept whole as long_form by the code, over the standard ceiling after Lisa's pass: approve or drop"
            )
    kept = [_record(q, p, d) for q, p, d in zip(survivors, checks, doubts, strict=False)]
    cleared = clip_limits.clear_bare_long_form(kept)  # long_form counts only with Lisa's written reason
    if cleared:
        report["long_form_cleared"] = cleared
    if limits is not None:
        for q, p in zip(kept, checks, strict=False):
            q["tape_check"]["span_s"] = round(_span(p) or 0, 1)
            q["tape_check"]["ceiling_s"] = limits.for_quote(q)
        report["lengths"] = {q["id"]: q["tape_check"]["span_s"] for q in kept}
        report["long_form"] = [q["id"] for q in kept if clip_limits.is_long_form(q)]
    report["one_label"] = (
        len(kept) > 1
        and len({s for c in checks for s in c.speakers}) == 1
        and len({str(q.get("speaker")) for q in kept}) > 1
    )
    report["listen"] = [
        {"id": q.get("id"), "reasons": q["tape_check"]["reasons"]}
        for q in kept
        if q["tape_check"]["verdict"] == "listen"
    ]
    pending = [q for q in kept if q.get("long_form_pending")]
    if (
        pending
        and all(is_host(str(q.get("speaker") or "")) for q in kept if not q.get("long_form_pending"))
        and any(not is_host(str(q.get("speaker") or "")) for q in pending)
    ):
        # Toby, condition B: the guest gate counts pending quotes; if they're the guest's only appearance, dropping
        # them leaves a host-only episode that Phase 2's guest gate refuses weeks later. Say it now.
        report["guest_only_via_pending"] = [q["id"] for q in pending if not is_host(str(q.get("speaker") or ""))]
    report["kept"] = len(kept)
    return kept, report


def check_selected(
    quotes: list[dict],
    transcript_fast: dict,
    episode_code: str,
    repair: Callable[[dict[str, list[str]]], list[dict]],
    limits: clip_limits.Limits,
) -> tuple[list[dict], dict, str]:
    """The whole quote check, as Round 1 step 10b runs it, for every caller that picks quotes (Round 1 and
    rockstar/tools/rerun_brand_team.py): the segments get the host-name canonicalisation the brand team's
    transcript had (so
    her name isn't read as a rewrite), every speaker gets one spelling before the speaker check and again after (a
    swap brings its own), then enforce(). Returns (kept quotes, report, standup line). Side effects are the caller's."""
    segments = [
        {**seg, "text": summary_norm.canonicalize_host(seg.get("text", ""))}
        for seg in transcript_fast.get("segments", [])
    ]
    for q in quotes:
        if q.get("speaker"):
            q["speaker"] = summary_norm.canonicalize_speaker(q["speaker"])
    kept, report = enforce(quotes, segments, episode_code, repair, limits)
    for q in kept:
        if q.get("speaker"):
            q["speaker"] = summary_norm.canonicalize_speaker(q["speaker"])
    return kept, report, standup_line(episode_code, report)


def _drop_kind(d: dict) -> str:
    return "too long" if "too long" in d["why"] else "not the speaker's words"


def standup_line(episode_code: str, report: dict) -> str:
    """One line for the standup: what the gate did, so nothing about a quote changes silently."""
    parts = [f"{report['kept']} of {report['checked']} quotes kept"]
    if report["repaired"]:
        parts.append("restored or swapped by Lisa: " + ", ".join(str(r["id"]) for r in report["repaired"]))
    if report["dropped"]:
        parts.append("dropped: " + ", ".join(f"{d['id']} ({_drop_kind(d)})" for d in report["dropped"]))
    if report.get("long_form"):
        parts.append("kept whole as long_form (approve before posting): " + ", ".join(map(str, report["long_form"])))
    if report.get("guest_only_via_pending"):
        parts.append(
            "guest featured only through pending long_form "
            + ", ".join(report["guest_only_via_pending"])
            + ": dropping them leaves the episode host-only"
        )
    if report.get("long_form_pending"):
        parts.append(
            "of those, kept by the code after Lisa's pass (no reason from her): "
            + ", ".join(f"{x['id']} ({x['span_s']:.0f} s)" for x in report["long_form_pending"])
        )
    if report["listen"]:
        parts.append("listen before posting: " + ", ".join(str(x["id"]) for x in report["listen"]))
    if report.get("one_label"):
        parts.append("every quote: one speaker label (diarisation), so every quote is to listen to")
    if report.get("renumbered"):
        parts.append("ids renumbered: " + ", ".join(f"#{r['index'] + 1} -> {r['now']}" for r in report["renumbered"]))
    if report.get("repair_error"):
        parts.append(f"Lisa's pass failed ({report['repair_error'].split(':')[0]})")
    return f"Quote check (ST-10) for {episode_code}: " + "; ".join(parts) + "."
