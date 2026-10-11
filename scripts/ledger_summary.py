#!/usr/bin/env python3
"""ledger_summary.py — what one episode cost, where its time went, and whether the cache hit (OBS-01 AC-7).

Usage (on the box):  /opt/rockstar-pipeline/venv/bin/python scripts/ledger_summary.py E34
                     [--ledger /opt/rockstar-pipeline/state/run_log.jsonl] [--kind prod]

Reads state/run_log.jsonl (read-only) and prints, for one episode:
  • model spend by call site, and the total — run_kind "prod" only unless --kind says otherwise;
  • the brand team's cache use per call; calls 2–4 reading 0 from cache print CACHE MISS (CACHE-01);
  • every step's duration and outcome, in order, per job.
Prints how many ledger lines it read and matched, so an empty answer is never mistaken for a zero (PQ-5a).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("episode")
    ap.add_argument("--ledger", default=None,
                    help="default: where run_ledger writes (the box's, or this repo's state/)")
    ap.add_argument("--kind", default="prod", help="run_kind to count (prod | eval | replay | manual)")
    args = ap.parse_args()

    if args.ledger:
        path = Path(args.ledger)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import run_ledger  # the same rule the writer uses, so the reader never looks in the wrong place
        path = run_ledger._path()
    if not path.is_file():
        print(f"UNVERIFIED: no ledger at {path}", file=sys.stderr)
        return 2
    lines = path.read_text(encoding="utf-8").splitlines()
    events, bad = [], 0
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if e.get("episode") == args.episode and e.get("run_kind", "prod") == args.kind:
            events.append(e)
    print(f"ledger: {path} — {len(lines)} lines read, {bad} unreadable, {len(events)} for {args.episode} "
          f"(run_kind {args.kind})")
    if not events:
        print(f"no {args.kind} events for {args.episode}: nothing to report (this is not a cost of $0)")
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
    print(f"  {'TOTAL (priced)':<24} ${sum(by_site.values()):.4f}"
          + (f"   (+ {failed} failed call(s), no usage returned)" if failed else ""))
    if unpriced:
        models = sorted({str(c.get("model")) for c in unpriced})
        print(f"  !! {len(unpriced)} answered call(s) NOT in the total: no price row for {', '.join(models)} — "
              f"add it to run_ledger.PRICES")

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
                print(f"    {c['step']:<20} try {c.get('attempt')}  read {read:>7}  wrote {wrote:>7}  "
                      f"input {c.get('input_tokens')}{flag}")

    steps = [e for e in events if e.get("event") == "step"]
    print(f"\nSteps — {len(steps)}")
    for s in steps:
        detail = f"  ({s['detail']})" if s.get("detail") else ""
        print(f"  job {s.get('job_id')} {s.get('phase'):<17} {s.get('step'):>5} {s.get('name'):<22} "
              f"{s.get('outcome'):<17} {s.get('duration_ms', 0) / 1000:>8.1f}s{detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
