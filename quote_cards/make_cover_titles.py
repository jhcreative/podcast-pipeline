"""
CARD-04: draft the five carousel cover titles for an episode into cover_titles.json.

    python make_cover_titles.py --episode 31 --title "Bliss Is the System | ..."
    python make_cover_titles.py --dir /tmp/card04-stage/E30-graham --episode 30 --title "..."
    python make_cover_titles.py --episode 31 --dry-run     # stub titles, no API key

The model drafts (prompts/cover_titles.md); cover_titles.assert_title gates every
title. A rejected draft gets ONE retry with the failures named; a second rejection
writes nothing and says which quote, so a person writes that title by hand.

An existing cover_titles.json is never overwritten without --redraft: once a person
has edited it, the file is the truth (PP-11).

Requires ANTHROPIC_API_KEY unless --dry-run -- it lives on the box, as for captions.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

import cover_titles                # noqa: E402
import quote_source                # noqa: E402


def _gate(titles, quotes, ep_num):
    """{qid: failure} for every title that does not pass."""
    bad = {}
    for q in quotes:
        try:
            cover_titles.assert_title(titles.get(q["id"], ""), q["text"],
                                      f"E{ep_num}_{q['id']}")
        except SystemExit as e:
            bad[q["id"]] = str(e)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--dir", help="episode folder (default: Episodes/E<N>-*)")
    ap.add_argument("--title", default="", help="published episode title, for context")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redraft", action="store_true",
                    help="replace an existing cover_titles.json (discards hand edits)")
    a = ap.parse_args()

    ep_dir = a.dir or quote_source.find_episode_dir(REPO, a.episode)
    if cover_titles.load(ep_dir) and not a.redraft:
        print(f"  {cover_titles.SIDECAR} exists in {os.path.basename(ep_dir)} -- kept "
              f"(a hand edit wins; --redraft to replace)")
        return
    quotes = quote_source.load_quotes(ep_dir)

    if a.dry_run:
        titles = {q["id"]: f"Dry Run Title {q['id'][-2:]}" for q in quotes}
        by = "dry-run stub"
    else:
        import anthropic_client
        titles = anthropic_client.compose_cover_titles(a.title, quotes)
        bad = _gate(titles, quotes, a.episode)
        if bad:
            print(f"  ! first draft rejected for {', '.join(bad)} -- one retry")
            keep = {k: v for k, v in titles.items() if k not in bad}
            retry = anthropic_client.compose_cover_titles(
                a.title, quotes, feedback="\n".join(bad.values()))
            titles = {**retry, **keep}
        by = f"model ({anthropic_client._MODEL}), code-gated"

    bad = _gate(titles, quotes, a.episode)
    if bad:
        sys.exit("nothing written -- write these by hand in "
                 f"{cover_titles.SIDECAR}:\n  " + "\n  ".join(bad.values()))
    p = cover_titles.save(ep_dir, a.episode, {q["id"]: str(titles[q["id"]]).strip()
                                              for q in quotes}, by, quotes)
    for q in quotes:
        print(f"  {q['id']}  {titles[q['id']]}")
    print(f"\n-> {p}  (edit freely before rendering)")


if __name__ == "__main__":
    main()
