#!/usr/bin/env python3
"""ledger_summary.py — what one episode cost, where its time went, and whether the cache hit (OBS-01 AC-7).

Usage (on the box):  /opt/rockstar-pipeline/venv/bin/python -m rockstar.tools.ledger_summary E34
                     [--ledger /opt/rockstar-pipeline/state/run_log.jsonl] [--kind prod]

Reads state/run_log.jsonl (read-only) and prints, for one episode:
  • model spend by call site, and the total — run_kind "prod" only unless --kind says otherwise;
  • the brand team's cache use per call; calls 2–4 reading 0 from cache print CACHE MISS (CACHE-01);
  • every step's duration and outcome, in order, per job;
  • the episode's records (RECORDS), from every run kind, a hand re-run included: the guest-match rule, a host
    episode, a reviewer that skipped, dropped or mangled quotes, the quote lengths and long_form ruling. Records hold
    rules and ids, never a name (round_1.py writes them so).
Prints how many ledger lines it read and matched, so an empty answer is never mistaken for a zero (PQ-5a).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# What a person reviewing a run needs beyond spend and steps (2026-10-13, John: "we'll be able to look at all this in
# ledger after right?"). Each is written once per run by rockstar/jobs/round_1.py, rockstar/tools/rerun_brand_team.py
# or publish_finalize.
# Each event prints ONLY the fields named here (Toby, PR #71): a writer that later adds a field holding quote text or a
# name doesn't reach this output until someone adds it here on purpose. `dropped` items print id, why
# and span_s only; `long_form_pending` items id and span_s.
RECORDS = {
    "guest_match": ("matched", "rule"),
    "host_episode": ("reason",),
    "brand_team_skipped": ("members", "max_tokens"),
    "reviewer_mangled": ("members",),
    "reviewer_dropped": ("members",),
    "quote_lengths": ("ceiling", "lengths", "long_form", "dropped", "long_form_pending"),
    "long_form_cleared": ("ids",),
    "quote_titles": ("rewritten", "repaired", "plain", "unfixed"),
    "summary_about_added": ("source",),
}
_DROPPED_FIELDS = ("id", "why", "span_s")
_PENDING_FIELDS = ("id", "span_s")


def _value(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(v)


def _print_records(records: list[dict], kind: str) -> None:
    print(f"\nRecords — {len(records)} (every run kind; a run that isn't {kind} says so)")
    for r in records:
        shown = {k: r.get(k) for k in RECORDS[r["event"]] if r.get(k) is not None}
        for key, keep in (("dropped", _DROPPED_FIELDS), ("long_form_pending", _PENDING_FIELDS)):
            if isinstance(shown.get(key), list):
                shown[key] = [{f: d.get(f) for f in keep if f in d} if isinstance(d, dict) else "?" for d in shown[key]]
        fields = "  ".join(f"{k}={_value(v)}" for k, v in shown.items())
        rkind = r.get("run_kind", "prod")
        who = f"job {r.get('job_id')}" if r.get("job_id") is not None else rkind
        tag = f" [{rkind}]" if rkind != kind and r.get("job_id") is not None else ""
        print(f"  {who}{tag} {str(r.get('ts', ''))[:10]} {str(r.get('ts', ''))[11:19]}  {r['event']:<18} {fields}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("episode")
    ap.add_argument(
        "--ledger", default=None, help="default: where run_ledger writes (the box's, or this repo's state/)"
    )
    ap.add_argument("--kind", default="prod", help="run_kind to count (prod | eval | replay | manual)")
    args = ap.parse_args()

    if args.ledger:
        path = Path(args.ledger)
    else:
        from rockstar.ops import (
            run_ledger,  # the same rule the writer uses, so the reader never looks in the wrong place
        )

        path = run_ledger._path()
    if not path.is_file():
        print(f"UNVERIFIED: no ledger at {path}", file=sys.stderr)
        return 2
    lines = path.read_text(encoding="utf-8").splitlines()
    events, records, bad = [], [], 0
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if e.get("episode") == args.episode and e.get("run_kind", "prod") == args.kind:
            events.append(e)
        if e.get("episode") == args.episode and e.get("event") in RECORDS:
            records.append(e)  # every run kind: a hand re-run (rerun_brand_team.py, "manual") is part of the story
    print(
        f"ledger: {path} — {len(lines)} lines read, {bad} unreadable, {len(events)} for {args.episode} "
        f"(run_kind {args.kind})"
    )
    if not events:
        print(f"no {args.kind} events for {args.episode}: nothing to report (this is not a cost of $0)")
        if records:  # a hand re-run alone (run_kind "manual") still has its records to show (Toby, PR #71 F1)
            _print_records(records, args.kind)
        return 1

    calls = [e for e in events if e.get("event") == "model_call"]
    by_site, failed, unpriced = defaultdict(float), 0, []
    for c in calls:
        if c.get("cost_usd") is not None:
            by_site[c["step"]] += c["cost_usd"]
        elif c.get("outcome") == "ok":
            unpriced.append(c)  # a real, answered call whose model has no price row: spend we can't price
        else:
            failed += 1
    print(f"\nModel spend — {len(calls)} calls (retries included: each one cost money)")
    for site, usd in sorted(by_site.items(), key=lambda kv: -kv[1]):
        print(f"  {site:<24} ${usd:.4f}")
    print(
        f"  {'TOTAL (priced)':<24} ${sum(by_site.values()):.4f}"
        + (f"   (+ {failed} failed call(s), no usage returned)" if failed else "")
    )
    if unpriced:
        models = sorted({str(c.get("model")) for c in unpriced})
        print(
            f"  !! {len(unpriced)} answered call(s) NOT in the total: no price row for {', '.join(models)} — "
            f"add it to run_ledger.PRICES"
        )

    # Per job: within one Round-1 run, Marcus writes the transcript to the cache and Robert, Dr. Joe and Lisa
    # should read it. A retry or a reprocess is a new job with its own first write (cold review).
    team = [c for c in calls if str(c.get("step", "")).startswith("brand_team.") and c.get("outcome") == "ok"]
    if team:
        print("\nBrand-team cache, per job (Robert, Dr. Joe and Lisa should read the transcript from cache)")
        for job in sorted({c.get("job_id") for c in team}, key=str):
            print(f"  job {job}")
            for c in (c for c in team if c.get("job_id") == job):
                read, wrote = c.get("cache_read_input_tokens") or 0, c.get("cache_creation_input_tokens") or 0
                reader = c["step"] != "brand_team.marcus"
                flag = "  CACHE MISS" if reader and read == 0 else ""
                print(
                    f"    {c['step']:<20} try {c.get('attempt')}  read {read:>7}  wrote {wrote:>7}  "
                    f"input {c.get('input_tokens')}{flag}"
                )

    steps = [e for e in events if e.get("event") == "step"]
    # OBS-02: a step that caught a failure and carried on closes as "anomaly"; count them up top so a reader sees
    # one without scanning every row.
    anomalies = sum(1 for e in steps if e.get("outcome") == "anomaly")
    print(f"\nSteps — {len(steps)}" + (f" ({anomalies} anomal{'y' if anomalies == 1 else 'ies'})" if anomalies else ""))
    for s in steps:
        detail = f"  ({s['detail']})" if s.get("detail") else ""
        print(
            f"  job {s.get('job_id')} {s.get('phase'):<17} {s.get('step'):>5} {s.get('name'):<22} "
            f"{s.get('outcome'):<17} {s.get('duration_ms', 0) / 1000:>8.1f}s{detail}"
        )

    _print_records(records, args.kind)

    failed_alerts = [e for e in events if e.get("event") == "alert_failed"]
    if failed_alerts:
        print(f"\nAlerts that never went out — {len(failed_alerts)}")
        for e in failed_alerts:
            print(f"  job {e.get('job_id')} {e.get('ts')}  {e.get('site')}  ({e.get('error')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
