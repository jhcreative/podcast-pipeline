"""
TITLE-02: one title per quote, checked once, used everywhere (John, 2026-10-13).

    "why would we allow a title in early step that fails in later step???!"
    "I dont want stuff to fail with manual fall work - write one title that works everywhere"

Each quote's `heading` is its ONE title: the blog post's h3 above it and the cover of its card carousel. Round 1
writes it, so the person reading quotes.json reads the title every surface will carry. It must pass the strictest
reader's rules, the card cover's (quote_cards/cover_titles.assert_title: 2-8 words, at most 44 characters, no "&",
no claims or bait words, no first person, never five words of the quote in a row).

ensure() never fails a run. A heading that breaks a rule is fixed, in order:
  1. the model rewrites it, told exactly which rule it broke (up to `tries` asks);
  2. code repairs it: "&" to "and", the banned marks removed, or the title's own half before/after a colon or dash;
  3. a plain title that always passes: "<first name> in Conversation", or "In Conversation".
Every change is recorded on the quote (`title_check`) and in the report, so the read shows what was changed and why.

Round 1 runs it before the verbatim check and again after it (a swap brings a new heading);
rockstar/tools/rerun_brand_team.py
runs it too; Phase 2 runs it before the blog and the cards, for episodes titled before this rule, and saves what it
fixed to Drive's quotes.json. The card step then takes the
headings as the cover titles: the model is no longer asked for a second title.
"""

from __future__ import annotations

import re

from rockstar.cards import cover_titles  # noqa: E402  (the rules live with the renderer that proved the fit)

MAX_CHARS = cover_titles.MAX_CHARS
MIN_WORDS, MAX_WORDS = cover_titles.MIN_WORDS, cover_titles.MAX_WORDS
_HONORIFICS = {"dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "prof", "prof."}


def problem(title, quote_text: str, name: str) -> str | None:
    """The rule a title breaks, in cover_titles' own words, or None when it passes."""
    try:
        cover_titles.assert_title(title, quote_text, name)
        return None
    except SystemExit as exc:
        return str(exc)


def _code_repairs(title) -> list[str]:
    """The mechanical fixes, best first: no "&" or banned marks; then the title's own halves where it has a colon or a
    dash ("The Quiet Kitchen: Teaching Your Whole Household" -> "The Quiet Kitchen"). Never words cut off the end:
    a cut reads broken and can turn a qualified claim into a bigger one ("...Isn't Dangerous for Most Women" losing
    its qualifier), which no rule can see (Toby, #73). None of these fix a claims word or a copied phrase;
    problem() decides which passes, and a title none of them fixes gets the plain one."""
    t = str(title or "").replace("&", "and").replace("’", "'")
    t = re.sub(r"[!?…#@]|\.\.+|[\U0001F300-\U0001FAFF☀-➿]", "", t)
    t = re.sub(r"https?://\S+|www\.\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -—–:;,.")
    halves = (
        [h.strip(" -—–:;,.") for h in re.split(r":\s+|\s+[—–-]\s+", t, maxsplit=1)]
        if re.search(r":\s+|\s+[—–-]\s+", t)
        else []
    )
    return [t] + [h for h in halves if len(h.split()) >= MIN_WORDS]


def _plain(speaker: str) -> list[str]:
    first = next((w for w in str(speaker or "").split() if w.lower() not in _HONORIFICS), "")
    return ([f"{first} in Conversation"] if first.isalpha() else []) + ["In Conversation"]


def ensure(quotes: list[dict], episode_code: str, rewrite=None, tries: int = 2) -> tuple[list[dict], dict]:
    """Make every quote's heading pass the title rules. `rewrite(bad)` takes {id: (heading, rule broken)} and returns
    {id: new title}; None (or a failing call) skips straight to the code repair. Never raises."""
    report = {"rewritten": [], "repaired": [], "plain": []}
    by_id = {q.get("id"): q for q in quotes}
    name = lambda q: f"{episode_code}_{q.get('id')}"  # noqa: E731
    bad = {q.get("id"): problem(q.get("heading"), q.get("text", ""), name(q)) for q in quotes}
    bad = {i: p for i, p in bad.items() if p}
    first_rule = dict(bad)
    was = {i: by_id[i].get("heading") for i in bad}

    for _ in range(tries if rewrite else 0):
        if not bad:
            break
        try:
            drafts = rewrite({i: (by_id[i].get("heading") or "", p) for i, p in bad.items()}) or {}
        except Exception:  # noqa: BLE001 — a failed ask falls through to the code repair, never stops the run
            drafts = {}
        for i in list(bad):
            draft = str(drafts.get(i) or "").strip()
            if not draft:
                continue
            p = problem(draft, by_id[i].get("text", ""), name(by_id[i]))
            if p:
                bad[i] = p
            else:
                by_id[i]["heading"] = draft
                report["rewritten"].append(i)
                del bad[i]

    for i in list(bad):
        q = by_id[i]
        candidates = [("repaired", c) for c in _code_repairs(q.get("heading"))] + [
            ("plain", c) for c in _plain(q.get("speaker"))
        ]
        for how, candidate in candidates:
            if candidate and not problem(candidate, q.get("text", ""), name(q)):
                q["heading"] = candidate
                report[how].append(i)
                del bad[i]
                break
    # "In Conversation" passes every rule (2 words, no banned word, too short to copy five of the quote's); if one
    # ever doesn't, it's named here, never raised.
    report["unfixed"] = sorted(bad)
    for i in report["rewritten"] + report["repaired"] + report["plain"]:
        how = next(k for k in ("rewritten", "repaired", "plain") if i in report[k])
        by_id[i]["title_check"] = {"was": was[i], "rule": first_rule[i], "fixed_by": how}
    return quotes, report


def summary(report: dict) -> str:
    """One standup line, or "" when every heading passed as written."""
    parts = [f"{k} {', '.join(report[k])}" for k in ("rewritten", "repaired", "plain", "unfixed") if report.get(k)]
    return (
        (
            "Quote titles (TITLE-02): " + "; ".join(parts) + ". Each one's old title and the rule it broke are in "
            "quotes.json (title_check)."
        )
        if parts
        else ""
    )
