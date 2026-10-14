#!/usr/bin/env python3
"""
quote_tape_report.py — every quote in an episode folder, checked against its tape with quote_tape (QUOTE-01 AC 5, 10).

  python3 -m rockstar.tools.quote_tape_report EPISODE_DIR [EPISODE_DIR ...]

An episode folder holds a quotes.json and a transcript_fast.json anywhere beneath it (scripts' pulls, a work dir).
Prints one block per quote and a summary line per episode. Read-only. The output quotes guests: keep it in the
private evaldata folder, never in git.
"""

import json
import sys
from pathlib import Path

from rockstar.gates import quote_tape  # noqa: E402


def _find(d: Path, name: str) -> Path:
    return d / name if (d / name).exists() else next(d.rglob(name))


def main(argv: list[str]) -> int:
    for arg in argv:
        d = Path(arg)
        data = json.loads(_find(d, "quotes.json").read_text(encoding="utf-8"))
        quotes = data["quotes"] if isinstance(data, dict) else data
        tape = quote_tape.Tape(json.loads(_find(d, "transcript_fast.json").read_text(encoding="utf-8"))["segments"])
        print(f"\n######## {d.name}: {len(quotes)} quotes, {len(tape.words)} tape words")
        bad = 0
        for q in quotes:
            p = quote_tape.place(q.get("text", ""), tape)
            bad += not p.verbatim
            print("\n".join(quote_tape.report(q.get("id", "?"), q.get("speaker", "?"), p)))
        print(f"-- {d.name}: {len(quotes) - bad} verbatim, {bad} not")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
