#!/usr/bin/env python3
"""
clip_limit.py — the longest quote clip the box can transcribe, from measure_clip_limit.sh's evidence (CLIP-01b AC 1).

A run fits when it finished (exit 0) and its peak stayed within the box's memory available at idle minus the
reserve. Swap never counts as room. The limit for a batch size is the longest length such that it AND every shorter
length measured fit, so one lucky long run can't lift the limit over a failed shorter one.

  python3 -m rockstar.tools.clip_limit <evidence.jsonl> --reserve-mb 600            # print the proposal
  python3 -m rockstar.tools.clip_limit <evidence.jsonl> --reserve-mb 600 --env      # print box-limits.env

The evidence's first line is the box ({"box": {...}}); each later line is one measure_one_clip.py run.
"""

import argparse
import json
import sys
from pathlib import Path


def load(path: Path) -> tuple[dict, list[dict]]:
    box, runs = {}, []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        row = json.loads(line)
        if "box" in row:
            box = row["box"]
        elif "seconds" in row:
            runs.append(row)
    if not box:
        raise ValueError("no box line in the evidence")
    return box, runs


def fits(run: dict, budget_mb: int) -> bool:
    return run.get("exit") == 0 and isinstance(run.get("peak_mb"), int) and run["peak_mb"] <= budget_mb


def broken(run: dict) -> bool:
    """A run that failed for a reason other than memory (a missing token, a cache miss, a bad flag): it says nothing
    about the box's limit, so no limit is proposed until it's fixed (Toby, CLIP-01b review)."""
    return run.get("exit") not in (0, "killed") and run.get("oom") is False


def verdict(run: dict, budget_mb: int) -> str:
    if fits(run, budget_mb):
        return "fits"
    if run.get("exit") == 0:
        return "over budget"
    if run.get("oom") or run.get("exit") == "killed":
        return "killed: out of memory" if run.get("oom") else "killed (no report; memory most likely)"
    return "FAILED, not memory: see whisperx.log on the box" if broken(run) else f"exit {run.get('exit')}"


def limit(runs: list[dict], batch: int, budget_mb: int) -> int:
    """Longest measured length at this batch with it and every shorter one fitting; 0 when the shortest doesn't."""
    best = 0
    for run in sorted((r for r in runs if r["batch"] == batch), key=lambda r: r["seconds"]):
        if not fits(run, budget_mb):
            break
        best = run["seconds"]
    return best


def proposal(box: dict, runs: list[dict], reserve_mb: int) -> dict:
    budget = box["mem_available_mb"] - reserve_mb
    by_batch = {b: limit(runs, b, budget) for b in sorted({r["batch"] for r in runs})}
    # The batch to use: the one with the longest limit; on a tie, the larger batch (faster), never a smaller one
    # for nothing.
    batch = max(by_batch, key=lambda b: (by_batch[b], b)) if by_batch else None
    return {
        "budget_mb": budget,
        "reserve_mb": reserve_mb,
        "by_batch": by_batch,
        "batch": batch,
        "clip_max_seconds": by_batch.get(batch, 0) if batch is not None else 0,
        "broken": [(r["batch"], r["seconds"]) for r in runs if broken(r)],
    }


def env_text(box: dict, p: dict, evidence_name: str) -> str:
    return (
        f"# Written by scripts/measure_clip_limit.sh --write from {evidence_name} (CLIP-01b).\n"
        f"# A clip fits when WhisperX finished with its peak within MemAvailable at idle minus the reserve; no swap.\n"
        f"CLIP_MAX_SECONDS={p['clip_max_seconds']}\n"
        f"CLIP_BATCH_SIZE={p['batch']}\n"
        f"CLIP_RESERVE_MB={p['reserve_mb']}\n"
        f"BOX_MEM_TOTAL_MB={box['mem_total_mb']}\n"
        f"BOX_MEM_AVAILABLE_MB={box['mem_available_mb']}\n"
        f"MEASURED_AT={box['measured_at']}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("evidence", type=Path)
    ap.add_argument("--reserve-mb", type=int, required=True)
    ap.add_argument("--env", action="store_true", help="print box-limits.env instead of the proposal")
    a = ap.parse_args()
    box, runs = load(a.evidence)
    p = proposal(box, runs, a.reserve_mb)
    if a.env and p["broken"]:
        print(f"REFUSED: runs failed for a reason other than memory {p['broken']}; fix and re-measure", file=sys.stderr)
        return 2
    if a.env:
        sys.stdout.write(env_text(box, p, a.evidence.name))
        return 0
    print(
        f"box: {box['mem_total_mb']} MB total, {box['mem_available_mb']} MB available at idle, "
        f"swap {box['swap_mb']} MB (not counted), measured {box['measured_at']}"
    )
    print(f"budget: {box['mem_available_mb']} - reserve {a.reserve_mb} = {p['budget_mb']} MB")
    for r in sorted(runs, key=lambda r: (r["batch"], r["seconds"])):
        print(
            f"  batch {r['batch']:>2}  {r['seconds']:>4} s  peak {str(r.get('peak_mb')):>6} MB  "
            f"(cgroup {str(r.get('cgroup_peak_mb')):>6})  {str(r.get('wall_s', '?')):>4} s  "
            f"{verdict(r, p['budget_mb'])}"
        )
    for b, s in p["by_batch"].items():
        print(f"  limit at batch {b}: {s} s" + ("  (not even the shortest clip fits)" if s == 0 else ""))
    if p["broken"]:
        print(f"NO PROPOSAL: {len(p['broken'])} run(s) failed for a reason other than memory; fix and re-measure")
        return 0
    print(f"PROPOSED: CLIP_MAX_SECONDS={p['clip_max_seconds']} at batch {p['batch']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
