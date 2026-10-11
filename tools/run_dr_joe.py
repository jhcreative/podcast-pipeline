#!/usr/bin/env python3
"""
run_dr_joe.py — Manual Dr. Joe clinical review pass on existing quotes.json.

Reads current quotes.json + transcript, runs Dr. Joe's prompt, writes
updated quotes.json with his clinical review applied, then re-runs Lisa
for final QA on the corrected set.
"""

import json
import os
import sys
from pathlib import Path

ROCKSTAR_DIR = Path(__file__).resolve().parent.parent  # the repo root: tools/ is one level down

DRIVE_EPISODE = Path(
    os.path.expanduser(
        "~/Library/CloudStorage/GoogleDrive-admin@example.test"
        "/My Drive/RockstarPodcast/Episodes/E26-delphine-okaford"
    )
)

TRANSCRIPT_TXT  = DRIVE_EPISODE / "transcripts" / "transcript_fast.txt"
QUOTES_PATH     = DRIVE_EPISODE / "quotes.json"
PROMPTS_DIR     = ROCKSTAR_DIR / "prompts"

EPISODE_METADATA = {
    "episode_number":       26,
    "episode_code":         "E26",
    "season":               2,
    "season_episode":       1,
    "season_episode_code":  "S2E1",
    "original_filename":    "E26-Delphine.m4a",
    "slug":                 "delphine-okaford",
    "guest_name":           "Dr. Delphine Okaford",
}

def _load_env() -> None:
    env_file = ROCKSTAR_DIR / "scripts" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set")
    os.environ["PROMPTS_DIR"] = str(PROMPTS_DIR)

def _strip_json_fences(raw: str) -> str:
    """
    Extract a JSON object from a model response that may wrap it in markdown
    code fences and/or precede/follow it with prose.

    Strategy: find the first '{' and the last '}' in the raw string and return
    everything in between.  This is fence-agnostic and depth-agnostic — it
    works regardless of how deeply nested the JSON is or whether the model
    added explanatory text before or after the block.
    """
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    # Fallback for JSON arrays
    start = raw.find("[")
    end   = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw.strip()

def main() -> None:
    _load_env()

    sys.path.insert(0, str(ROCKSTAR_DIR))
    import anthropic
    import run_ledger  # OBS-01: recorded like every pipeline call (run_kind "manual")
    from anthropic_client import _MODEL  # one model definition (EVAL-02: a switch must reach this tool too)
    run_ledger.bind(episode=EPISODE_METADATA["episode_code"], run_kind="manual")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Load transcript + current quotes
    transcript_text = TRANSCRIPT_TXT.read_text(encoding="utf-8")
    current_quotes  = json.loads(QUOTES_PATH.read_text(encoding="utf-8"))
    current_quotes_json = json.dumps(current_quotes, ensure_ascii=False)

    print(f"Transcript: {len(transcript_text):,} chars")
    print(f"Current quotes: {len(current_quotes.get('quotes', []))} quotes")

    meta_suffix = f"\n\nEpisode: {EPISODE_METADATA['episode_code']} · guest: {EPISODE_METADATA['guest_name']}"

    # ── Dr. Joe pass ──────────────────────────────────────────────────────────
    print("\n── Dr. Joe — clinical accuracy review ──")
    dr_joe_prompt = (PROMPTS_DIR / "brand_team_dr_joe.md").read_text(encoding="utf-8")

    response = run_ledger.create(
        client, step="manual.dr_joe", prompt="brand_team_dr_joe.md",
        model=_MODEL,
        max_tokens=8192,
        system=dr_joe_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": transcript_text,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        f"Prior step output (quotes so far):\n\n"
                        f"```json\n{current_quotes_json}\n```\n\n"
                        f"Review the quotes below for clinical accuracy per your instructions. "
                        f"Return the full updated quotes array as ONLY valid JSON.{meta_suffix}"
                    ),
                },
            ],
        }],
    )

    raw = response.content[0].text
    cleaned = _strip_json_fences(raw)
    try:
        quotes_v3 = json.loads(cleaned)
        print(f"  Dr. Joe → {len(quotes_v3.get('quotes', []))} quotes")
    except json.JSONDecodeError as e:
        print(f"  ERROR: json.JSONDecodeError: {e}")
        print(f"  raw length:     {len(raw)}")
        print(f"  cleaned length: {len(cleaned)}")
        print(f"  cleaned[:200]:  {cleaned[:200]}")
        print(f"  cleaned[-200:]: {cleaned[-200:]}")
        sys.exit(1)

    quotes_v3_json = json.dumps(quotes_v3, ensure_ascii=False)

    # ── Lisa final QA pass ────────────────────────────────────────────────────
    print("\n── Lisa — final QA on Dr. Joe-reviewed set ──")
    lisa_prompt = (PROMPTS_DIR / "brand_team_lisa.md").read_text(encoding="utf-8")

    response2 = run_ledger.create(
        client, step="manual.lisa", prompt="brand_team_lisa.md",
        model=_MODEL,
        max_tokens=8192,
        system=lisa_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": transcript_text,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        f"Prior step output (quotes so far):\n\n"
                        f"```json\n{quotes_v3_json}\n```\n\n"
                        f"Apply your final QA checklist to the quotes below. "
                        f"Return the final clean quotes.json as ONLY valid JSON.{meta_suffix}"
                    ),
                },
            ],
        }],
    )

    raw2 = response2.content[0].text
    cleaned2 = _strip_json_fences(raw2)
    try:
        final = json.loads(cleaned2)
        final_quotes = final.get("quotes", [])
        print(f"  Lisa final → {len(final_quotes)} quotes")
    except json.JSONDecodeError:
        print(f"  ERROR: Lisa returned non-JSON:\n{raw2[:300]}")
        sys.exit(1)

    # Write back
    QUOTES_PATH.write_text(
        json.dumps({"quotes": final_quotes}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n✅ quotes.json updated with Dr. Joe + Lisa pass")
    print(f"   {QUOTES_PATH}")

    # Print summary of any clinical flags
    print("\n── Dr. Joe / Lisa clinical notes ──")
    for q in final_quotes:
        flags = []
        if q.get("dr_joe_note"):
            flags.append(f"  Dr. Joe: {q['dr_joe_note']}")
        if q.get("lisa_note"):
            flags.append(f"  Lisa:    {q['lisa_note']}")
        if flags:
            print(f"\n{q['id']} — {q.get('reel_hook', q['text'][:60])}")
            for f in flags:
                print(f)

if __name__ == "__main__":
    main()
