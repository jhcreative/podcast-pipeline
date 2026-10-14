"""
evals/baseline.py — what a frozen baseline must record (EVAL-00 §2). A comparison against a baseline missing any
of these fields is a comparison against an unknown, so validate() refuses it, naming the field.
"""

from __future__ import annotations

REQUIRED = {
    "created_at": "UTC timestamp the baseline was frozen",
    "model": "model ID requested",
    "response_models": "model IDs the API reported answering (from the OBS-01 ledger)",
    "temperature": "as sent; null = the API default (1.0), recorded as such",
    "prompt_sha": "per call site, SHA-256 of the system text sent (after include expansion, PROMPT-01)",
    "request_template_sha": "per call site, the request's shape (OBS-01)",
    "deploy_manifest_sha": "SHA-256 of scripts/deploy.sh FILES as deployed",
    "summary_norm_version": "git sha of rockstar/gates/summary_norm.py "
    "(it shapes what the model sees and what Heather saw)",
    "judge_model": "fixed per run; never a candidate (EVAL-01 AC-6)",
    "judge_prompt_sha": "SHA-256 of the judge prompt",
    "fixture_manifest_sha": "SHA-256 of the sorted list of fixture files and their hashes",
    "anchor_episodes": "rubric anchors (EVAL-01 AC-10): never among the scored episodes",
    "scored_episodes": "the episodes this baseline scored",
    "runs_per_episode": "3 unless stated",
}


def validate(baseline: dict) -> list[str]:
    """Problems with a baseline record ([] when complete)."""
    problems = [f"missing: {k} ({why})" for k, why in REQUIRED.items() if k not in baseline]
    if problems:
        return problems
    for k in ("anchor_episodes", "scored_episodes", "response_models"):
        v = baseline[k]
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            problems.append(f"{k} must be a list of non-empty strings")
    if not baseline["scored_episodes"]:
        problems.append("scored_episodes is empty: a baseline over nothing")
    if not (type(baseline["runs_per_episode"]) is int and baseline["runs_per_episode"] > 0):  # bool is an int
        problems.append("runs_per_episode must be a positive integer")
    for k in (
        "created_at",
        "model",
        "deploy_manifest_sha",
        "summary_norm_version",
        "judge_model",
        "judge_prompt_sha",
        "fixture_manifest_sha",
    ):
        if not (isinstance(baseline[k], str) and baseline[k].strip()):
            problems.append(f"{k} must be a non-empty string")
    if not problems:
        overlap = set(baseline["anchor_episodes"]) & set(baseline["scored_episodes"])
        if overlap:
            problems.append(f"anchors scored as items (leakage): {sorted(overlap)}")
    for k in ("prompt_sha", "request_template_sha"):
        if not isinstance(baseline[k], dict) or not baseline[k]:
            problems.append(f"{k} must be a non-empty map of call site → sha")
    return problems
