#!/usr/bin/env python3
"""
test_compose.py — Smoke test compose functions against E25 data.

Usage:
  cd ~/RockstarPodcast
  source scripts/.env && python3 tests/test_compose.py [apple|faq|all]   (or: pytest -m live)

Outputs to test_output/ for manual review.
"""

import json
import os
import sys

try:  # the live marker, so a plain `pytest` never calls the API (pyproject: addopts "-m 'not live'")
    import pytest

    pytestmark = pytest.mark.live
except ImportError:  # run directly without pytest installed
    pass
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
E25 = ROOT / "Episodes/E25-get-back-authentic-self"
OUT = ROOT / "test_output"
OUT.mkdir(exist_ok=True)

# ── load E25 fixtures ──────────────────────────────────────────────────────────


def load_transcript() -> str:
    json_path = E25 / "transcripts/transcript_fast.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data if isinstance(data, list) else data.get("segments", [])
    return " ".join(s.get("text", "").strip() for s in segments if s.get("text"))


def load_quotes() -> list[dict]:
    return json.loads((E25 / "quotes.json").read_text(encoding="utf-8")).get("quotes", [])


def load_apple_summary() -> str:
    return (E25 / "apple_summary.md").read_text(encoding="utf-8")


EPISODE_METADATA = {
    "episode_number": 25,
    "episode_code": "E25",
    "season_episode_code": "S1E25",
    "slug": "get-back-authentic-self",
    "guest_name": "Juniper Larkspirelle",
}

# ── tests ──────────────────────────────────────────────────────────────────────


def test_apple(transcript: str, quotes: list[dict]) -> str:
    from rockstar.clients import anthropic_client

    print("→ compose_apple_summary ...", end=" ", flush=True)
    result = anthropic_client.compose_apple_summary(
        transcript_text=transcript,
        episode_metadata=EPISODE_METADATA,
        quotes=quotes,
    )
    out_path = OUT / "apple_summary_test.md"
    out_path.write_text(result, encoding="utf-8")
    print(f"done ({len(result)} chars) → {out_path.name}")
    return result


def test_faq(transcript: str, apple_summary: str) -> str:
    from rockstar.clients import anthropic_client

    print("→ compose_faq ...", end=" ", flush=True)
    result = anthropic_client.compose_faq(
        transcript_text=transcript,
        apple_summary=apple_summary,
        episode_metadata=EPISODE_METADATA,
    )
    out_path = OUT / "faq_test.md"
    out_path.write_text(result, encoding="utf-8")
    print(f"done ({len(result)} chars) → {out_path.name}")
    return result


# ── main ───────────────────────────────────────────────────────────────────────


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Run: source scripts/.env && pytest -m live")
        sys.exit(1)

    print(f"Loading E25 fixtures from {E25} ...")
    transcript = load_transcript()
    quotes = load_quotes()
    apple_summary = load_apple_summary()
    print(f"  transcript: {len(transcript)} chars | quotes: {len(quotes)} | apple_summary: {len(apple_summary)} chars")
    print()

    new_apple = apple_summary  # default: use existing unless re-generating

    if mode in ("apple", "all"):
        new_apple = test_apple(transcript, quotes)

    if mode in ("faq", "all"):
        test_faq(transcript, new_apple)

    # The blog step was removed with compose_blog_page (BLOG-02b, 2026-09-24): the post is
    # built in plain code now and tested offline in test_blog02_build_post.py.

    print(f"\nOutputs in {OUT}/")
    print("  apple_summary_test.md  — check: locked title format, SEO keyword presence in opening 2 paragraphs")
    print("  faq_test.md            — check: 3-5 Q&A pairs, search-phrased, keyword-grounded")


if __name__ == "__main__":
    main()
