"""
evals/metric.py — how much a person changed what the model wrote (EVAL-00 §3, Jen).

The human-edit metric compares the draft the model wrote (snapshotted after normalize_apple_summary: what
Heather saw) with the final she published. Defined once, here, with golden cases (test_eval00_definitions.py),
before any number exists — so nobody picks a formula after seeing the results.

  normalize   NFC, LF line endings, trailing whitespace stripped, runs of blank lines collapsed to one.
              Markdown markers stay: changing a heading is an edit.
  primary     word-level Levenshtein distance ÷ max(word count of draft, of final).  0 = untouched, 1 = nothing kept.
              Not difflib's ratio: that is a matching heuristic, not an edit distance.
  secondary   the same at character level (reported, never the headline). It sees what the word level can't:
              a whitespace-only change inside a line is 0 at word level but not 0 here — by design.
  similarity  this is a similarity-to-final measure. Heather's finals are edits of the incumbent model's drafts, so
              it favours the incumbent: it reports, it never decides a model choice (EVAL-01 AC-9).
  rewrite     share ≥ REWRITE_AT: reported as its own category, because a rewrite saturates the scale near 1
              and its exact value says little.
  reviewed    supplied by the caller (Drive revision author and time): 0 with reviewed=False means "never
              opened", not "accepted" — the two must never be averaged together.
"""

from __future__ import annotations

import re
import unicodedata

REWRITE_AT = 0.9


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip("\n")


def _words(text: str) -> list[str]:
    return normalize(text).split()


def _levenshtein(a: list, b: list) -> int:
    """Edit distance between two sequences (insert, delete, substitute = 1), O(len(a)·len(b)) time, O(len(b)) space.
    A shared prefix and suffix are trimmed first — exact, and most of a lightly edited text."""
    start = 0
    while start < len(a) and start < len(b) and a[start] == b[start]:
        start += 1
    end = 0
    while end < len(a) - start and end < len(b) - start and a[-1 - end] == b[-1 - end]:
        end += 1
    a, b = a[start : len(a) - end], b[start : len(b) - end]
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def human_edit(draft: str, final: str, *, reviewed: bool | None = None) -> dict:
    """The human-edit line for one artifact. Every field is reported; `share` is the headline."""
    dw, fw = _words(draft), _words(final)
    distance = _levenshtein(dw, fw)
    denom = max(len(dw), len(fw))
    share = round(distance / denom, 4) if denom else 0.0  # rounded once, so `rewrite` agrees with what's reported
    dc, fc = normalize(draft), normalize(final)
    char_denom = max(len(dc), len(fc))
    return {
        "share": share,
        "words_edited": distance,
        "words_draft": len(dw),
        "words_final": len(fw),
        "char_share": round(_levenshtein(list(dc), list(fc)) / char_denom, 4) if char_denom else 0.0,
        "rewrite": share >= REWRITE_AT,
        "reviewed": reviewed,
    }
