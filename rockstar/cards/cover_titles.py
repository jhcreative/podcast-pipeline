"""
CARD-04: the carousel title on each cover -- where it is kept and what it must pass.

Division of labour is ADR-0013's: the MODEL drafts the five titles (it reads each
quote for its substance); CODE gates them and owns everything else on the cover. The
titles live in a sidecar a person can edit before rendering:

    Episodes/E<N>-<guest>/cover_titles.json
    {"episode": 29, "drafted_by": "...", "titles": {"q01": "...", ...}}

It sits beside quotes.json, NOT in cards/: card_upload pushes every file in cards/
to Drive, and the sidecar is a working file, not a post.

A hand edit wins (PP-11). make_cover_titles.py never overwrites an existing sidecar
without --redraft, and the renderer reads whatever the file says.

The gates are hard failures. Each exists because the failure it prevents is the one a
thumbnail makes permanent: the title is the first thing a scroller reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

SIDECAR = "cover_titles.json"
MAX_CHARS = 44  # the fit is proven in Chromium; this is the editorial ceiling
MIN_WORDS, MAX_WORDS = 2, 8

# CONTENT-CANON §4 (claims words) and §2 (never clickbait) -- the Rockstar canon
# governs Rockstar copy, and a cover title is the most-read line of the post.
# Patterns, so word forms are caught ("Healing", "Cured") and plain uses are not:
# "How Your Brain Works" describes; "What Actually Works" promises.
_CLAIMS = (
    r"cur(e|es|ed|ing)",
    r"heal(s|ed|ing)?",
    r"treats",
    r"treated",
    r"prevent(s|ed|ing)?",
    r"clinically proven",
    r"miracles?",
    r"guarantee[sd]?",
    r"(what|that|it|really|actually) works",
)
_BAIT = (
    r"you won'?t believe",
    r"shocking",
    r"secrets?",
    r"hacks?",
    r"this one",
    r"nobody tells you",
    r"doctors hate",
    r"must-know",
    r"game[- ]changer",
    r"what happens next",
    r"the truth about",
)
_CANDOUR = (r"honestly", r"to be honest", r"truth be told")


def sidecar_path(ep_dir):
    return os.path.join(ep_dir, SIDECAR)


def load(ep_dir):
    p = sidecar_path(ep_dir)
    if not os.path.exists(p):
        return None
    return json.load(open(p))


def fingerprint(text):
    """Which quote a title was written for. Titles are keyed by quote id, and ids
    are positions: a regenerated quotes.json can put a different quote at q03."""
    return hashlib.sha1(re.sub(r"\s+", " ", text.strip()).encode()).hexdigest()[:12]


def save(ep_dir, ep_num, titles, drafted_by, quotes=None):
    p = sidecar_path(ep_dir)
    data = {"episode": ep_num, "drafted_by": drafted_by, "titles": titles}
    if quotes:
        data["quotes"] = {q["id"]: fingerprint(q["text"]) for q in quotes}
    json.dump(data, open(p, "w"), indent=2, ensure_ascii=False)
    open(p, "a").write("\n")
    return p


def _words(s):
    return re.findall(r"[a-z0-9']+", s.lower().replace("’", "'").replace("‘", "'"))


def assert_title(title, quote_text, name):
    """Raise SystemExit naming the failure. Returns the title, stripped."""
    if title is not None and not isinstance(title, str):
        raise SystemExit(f"FAIL {name}: title is a {type(title).__name__}, not text")
    t = (title or "").strip()
    low = t.lower().replace("’", "'")
    if not t:
        raise SystemExit(f"FAIL {name}: empty cover title")
    if len(t) > MAX_CHARS:
        raise SystemExit(
            f"FAIL {name}: title is {len(t)} characters, ceiling is {MAX_CHARS} -- shorten it; the type does not shrink"
        )
    n = sum(1 for w in t.split() if re.search(r"\w", w))  # a dash between words is not a word (TITLE-02, E34 q01)
    if not MIN_WORDS <= n <= MAX_WORDS:
        raise SystemExit(f"FAIL {name}: title has {n} words, {MIN_WORDS}–{MAX_WORDS} read in a thumbnail")
    if re.search(r"https?://|www\.|#|@", t):
        raise SystemExit(f"FAIL {name}: a cover title carries no URL, hashtag or handle")
    if re.search(r"[!?…]|\.\.|[\U0001F300-\U0001FAFF☀-➿]", t):
        raise SystemExit(
            f"FAIL {name}: no exclamation, question mark, ellipsis or emoji -- the canon's register is never clickbait"
        )
    if "&" in t:
        raise SystemExit(f"FAIL {name}: write 'and', not '&' (CONTENT-CANON §2)")
    if t.isupper() and len(t) > 4:
        raise SystemExit(f"FAIL {name}: all caps; the cover sets the case")
    for words, why in (
        (_CLAIMS, "a claims word (CONTENT-CANON §4)"),
        (_BAIT, "a clickbait phrase (CONTENT-CANON §2)"),
        (_CANDOUR, "a candour signal (PQ-5b)"),
    ):
        hit = next((m.group(0) for w in words for m in [re.search(rf"\b(?:{w})\b", low)] if m), None)
        if hit:
            raise SystemExit(f"FAIL {name}: {hit!r} is {why}")
    # "I" the pronoun, not "Part I" or "i.e.".
    if re.search(r"\b(i'm|my|me)\b|(?<!part )(?<!chapter )\bi\b(?!\.)", low):
        raise SystemExit(
            f"FAIL {name}: no first person on the cover -- the quote is the speaker's voice; the title is the show's"
        )
    # The quote is the next slide. A title that copies five of its words in a row
    # spends the cover repeating it instead of naming what it is about.
    qw, tw = _words(quote_text), _words(t)
    grams = {tuple(qw[i : i + 5]) for i in range(len(qw) - 4)}
    if any(tuple(tw[i : i + 5]) in grams for i in range(len(tw) - 4)):
        raise SystemExit(f"FAIL {name}: title restates the quote -- name its substance, don't repeat it")
    return t


def titles_for(ep_dir, ep_num, quotes):
    """The gated titles for every quote, or SystemExit naming what to do."""
    data = load(ep_dir)
    if not data:
        raise SystemExit(
            f"no {SIDECAR} in {os.path.basename(ep_dir)} -- run "
            f"make_cover_titles.py --episode {ep_num} first (it drafts, "
            f"you may edit, then render)"
        )
    titles = data.get("titles") or {}
    missing = [q["id"] for q in quotes if not titles.get(q["id"])]
    if missing:
        raise SystemExit(f"{SIDECAR}: no title for {', '.join(missing)}")
    prints = data.get("quotes") or {}
    moved = [q["id"] for q in quotes if q["id"] in prints and prints[q["id"]] != fingerprint(q["text"])]
    if moved:
        raise SystemExit(
            f"{SIDECAR}: {', '.join(moved)} was titled for a different "
            f"quote than quotes.json now holds -- delete {SIDECAR} and run again: "
            f"the titles come from the quotes' headings (TITLE-02)"
        )
    return {q["id"]: assert_title(titles[q["id"]], q["text"], f"E{ep_num}_{q['id']}") for q in quotes}
