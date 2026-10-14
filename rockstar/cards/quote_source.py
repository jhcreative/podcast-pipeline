"""
Read quotes.json in either schema generation.

Two shapes exist in the wild and both are live, because the back catalogue is
re-rendered on demand:

    old (E23-E25):  index + quote_text_clean, no id, no text
    new (E27+):     id + text  (E27 carries both text and quote_text_clean)

This is the same drift-tolerance rule ADR-0002 set for the publisher email: support
the old AND new forms together rather than picking one and silently failing on the
other. A missing key here would surface as a KeyError halfway through an episode,
after some cards were already written.
"""

import json
import os


def load_quotes(episode_dir):
    """Return [{id, text, speaker, raw}] normalized across schema generations.

    Raises rather than skipping: a quote with no recoverable text means the file is
    not what we think it is, and rendering four cards out of five silently is worse
    than stopping.
    """
    path = os.path.join(episode_dir, "quotes.json")
    data = json.load(open(path))
    raw = data["quotes"] if isinstance(data, dict) and "quotes" in data else data

    out, seen = [], set()
    for i, q in enumerate(raw, 1):
        qid = q.get("id") or f"q{int(q.get('index', i)):02d}"
        text = (q.get("text") or q.get("quote_text_clean") or "").strip()
        speaker = (q.get("speaker") or "").strip()
        if not text:
            raise SystemExit(
                f"{path}: quote {qid} has neither 'text' nor 'quote_text_clean' — refusing to render a partial set"
            )
        if not speaker:
            raise SystemExit(
                f"{path}: quote {qid} has no speaker — the card and the "
                f"caption both attribute it, so this cannot be guessed"
            )
        if qid in seen:
            raise SystemExit(f"{path}: duplicate quote id {qid} — output filenames would collide")
        seen.add(qid)
        out.append({"id": qid, "text": text, "speaker": speaker, "raw": q})
    return out


def find_episode_dir(repo, ep_num):
    folder = next((d for d in os.listdir(os.path.join(repo, "Episodes")) if d.startswith(f"E{ep_num}-")), None)
    if not folder:
        raise SystemExit(f"no Episodes/E{ep_num}-* folder")
    return os.path.join(repo, "Episodes", folder)
