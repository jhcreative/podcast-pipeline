"""
CARD-04: the five carousel cover titles for an episode, into cover_titles.json.

TITLE-02 (2026-10-13): each title is the quote's `heading`, the one title per quote that Round 1 makes pass these
rules. The model is no longer asked for a second title; quote_title.ensure fixes a heading from before the rule.

    python -m rockstar.cards.make_cover_titles --episode 31 --title "Bliss Is the System | ..."
    python -m rockstar.cards.make_cover_titles --dir /tmp/card04-stage/E30-graham --episode 30 --title "..."
    python -m rockstar.cards.make_cover_titles --episode 31 --dry-run     # stub titles, no API key

cover_titles.assert_title gates every title. A heading that fails is rewritten by the model, told the rule
(prompts/cover_titles.md), or repaired by code: never a stop for a person to write one by hand.

An existing cover_titles.json is never overwritten without --redraft: once a person
has edited it, the file is the truth (PP-11).

Requires ANTHROPIC_API_KEY unless --dry-run -- it lives on the box, as for captions.
"""

import argparse
import os
import sys

from rockstar import config
from rockstar.cards import (
    cover_titles,  # noqa: E402
    quote_source,  # noqa: E402
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = str(config.CODE_ROOT)


def _gate(titles, quotes, ep_num):
    """{qid: failure} for every title that does not pass."""
    bad = {}
    for q in quotes:
        try:
            cover_titles.assert_title(titles.get(q["id"], ""), q["text"], f"E{ep_num}_{q['id']}")
        except SystemExit as e:
            bad[q["id"]] = str(e)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--dir", help="episode folder (default: Episodes/E<N>-*)")
    ap.add_argument("--title", default="", help="published episode title, for context")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--redraft", action="store_true", help="replace an existing cover_titles.json (discards hand edits)"
    )
    a = ap.parse_args()

    ep_dir = a.dir or quote_source.find_episode_dir(REPO, a.episode)
    if cover_titles.load(ep_dir) and not a.redraft:
        print(
            f"  {cover_titles.SIDECAR} exists in {os.path.basename(ep_dir)} -- kept "
            f"(a hand edit wins; --redraft to replace)"
        )
        return
    quotes = quote_source.load_quotes(ep_dir)

    if a.dry_run:
        titles = {q["id"]: f"Dry Run Title {q['id'][-2:]}" for q in quotes}
        by = "dry-run stub"
    else:
        # TITLE-02: the cover title IS the quote's heading, the one title Round 1 made pass these rules (and the blog
        # prints). A heading from before that rule is fixed by the same code, never a stop (quote_title.ensure).
        from rockstar.clients import anthropic_client
        from rockstar.gates import quote_title

        qs = [
            {
                "id": q["id"],
                "text": q["text"],
                "speaker": q["speaker"],
                "heading": str((q.get("raw") or {}).get("heading") or ""),
            }
            for q in quotes
        ]
        qs, report = quote_title.ensure(
            qs, f"E{a.episode}", lambda bad: anthropic_client.rewrite_quote_titles(a.title, qs, bad)
        )
        titles = {q["id"]: q["heading"] for q in qs}
        by = "the quotes' headings (TITLE-02)"
        if quote_title.summary(report):
            print(f"  {quote_title.summary(report)}")
            by += f"; {quote_title.summary(report)}"

    bad = _gate(titles, quotes, a.episode)
    if bad:
        sys.exit(f"nothing written -- write these by hand in {cover_titles.SIDECAR}:\n  " + "\n  ".join(bad.values()))
    p = cover_titles.save(ep_dir, a.episode, {q["id"]: str(titles[q["id"]]).strip() for q in quotes}, by, quotes)
    for q in quotes:
        print(f"  {q['id']}  {titles[q['id']]}")
    print(f"\n-> {p}  (edit freely before rendering)")


if __name__ == "__main__":
    main()
