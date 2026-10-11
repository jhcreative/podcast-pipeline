"""
evals/stats.py — the comparison rule and the agreement statistics (EVAL-00 §4–5, Jen).

  paired_bootstrap   The unit is the EPISODE. For each episode: candidate mean − baseline mean (each the mean of
                     its runs). Two 95% CIs over episodes — bootstrap percentile (10,000 resamples, fixed seed)
                     and Student-t — and "no detectable difference" unless BOTH exclude 0. Under 10 episodes: advisory.
                     The bootstrap interval sits inside the t interval in practice, so the t-test decides: false
                     positives ≈ 4.9%, and a real effect of 0.5 SD of the differences is found 58% of the time at
                     n = 20 (re-review simulation). Minimum detectable effects: docs/eval/test-strategy.md.
  wilson             95% Wilson interval for a proportion: judge–Heather agreement, gate pass rates.
  cohen_kappa        Agreement corrected for chance, per criterion. Unstable on small n, misleading when nearly
                     everything passes — reported beside raw agreement, never instead of it.
Stdlib only.
"""

from __future__ import annotations

import math
import random
from statistics import mean, stdev

Z95 = 1.959963984540054


# Two-sided 95% Student-t critical values by degrees of freedom; between rows the LOWER df is used (wider, so
# conservative). Beyond 120, the df-120 value — still above the true value, where z (1.960) would be below it.
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
         11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
         20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
         29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980}
MIN_EPISODES = 10


def _t975(df: int) -> float:
    if df > 120:
        return _T975[120]
    return _T975[max(k for k in _T975 if k <= df)]


def paired_bootstrap(baseline: dict[str, list[float]], candidate: dict[str, list[float]], *,
                     lower_is_better: bool, resamples: int = 10_000, seed: int = 20261010) -> dict:
    """baseline/candidate: {episode: [score per run]}. Only episodes present in both, with runs, count (paired).

    Two 95% intervals on the per-episode differences — the bootstrap percentile and the Student-t — and the
    verdict needs BOTH to exclude 0 (cold review: the percentile interval alone covered only ~92% at n = 20,
    passing luck as a win). Below MIN_EPISODES the verdict is advisory, whatever the intervals say.
    `lower_is_better` must be given: a human-edit share wants lower; a rubric score wants higher."""
    episodes = sorted(e for e in set(baseline) & set(candidate) if baseline[e] and candidate[e])
    if len(episodes) < 2:
        raise ValueError(f"need at least 2 paired episodes with runs, got {len(episodes)}")
    diffs = [mean(candidate[e]) - mean(baseline[e]) for e in episodes]
    n = len(diffs)
    rng = random.Random(seed)
    boots = sorted(mean(rng.choice(diffs) for _ in diffs) for _ in range(resamples))
    cut = int(round(0.025 * resamples))
    boot_ci = (boots[cut], boots[resamples - 1 - cut])
    sd = stdev(diffs) if n > 1 else 0.0
    half = _t975(n - 1) * sd / math.sqrt(n)
    t_ci = (mean(diffs) - half, mean(diffs) + half)
    lo, hi = min(boot_ci[0], t_ci[0]), max(boot_ci[1], t_ci[1])  # the union: conservative
    if n < MIN_EPISODES:
        verdict = f"advisory — n < {MIN_EPISODES}"
    elif lo <= 0 <= hi:
        verdict = "no detectable difference"  # not "no difference": the test can miss a real, small effect
    else:
        lower = hi < 0
        verdict = "candidate better" if lower == lower_is_better else "candidate worse"
    return {"n_episodes": n, "mean_diff": round(mean(diffs), 4), "ci95": [round(lo, 4), round(hi, 4)],
            "ci95_bootstrap": [round(boot_ci[0], 4), round(boot_ci[1], 4)],
            "ci95_t": [round(t_ci[0], 4), round(t_ci[1], 4)], "verdict": verdict,
            "metric_direction": "lower is better" if lower_is_better else "higher is better",
            "resamples": resamples, "seed": seed}


def wilson(successes: int, n: int) -> tuple[float, float]:
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError(f"need 0 <= successes <= n and n > 0, got {successes}/{n}")
    p = successes / n
    denom = 1 + Z95 ** 2 / n
    centre = (p + Z95 ** 2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95 ** 2 / (4 * n * n)) / denom
    return round(centre - half, 4), round(centre + half, 4)


def cohen_kappa(a: list, b: list) -> float | None:
    """None when chance agreement is total (every rating the same category): kappa is undefined there."""
    if len(a) != len(b) or not a:
        raise ValueError("two equal, non-empty rating lists")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    cats = set(a) | set(b)
    expected = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 4)


def calibration_report(judge: list, heather: list) -> dict:
    """One criterion's judge-vs-Heather agreement, with the advisory flag below 30 items (EVAL-00 §5)."""
    if len(judge) != len(heather) or not judge:
        raise ValueError("judge and Heather must rate the same, non-empty list of items")
    n = len(judge)
    agree = sum(x == y for x, y in zip(judge, heather, strict=True))
    return {"n": n, "raw_agreement": round(agree / n, 4), "wilson95": wilson(agree, n),
            "kappa": cohen_kappa(judge, heather), "status": "advisory — n < 30" if n < 30 else "may gate"}
