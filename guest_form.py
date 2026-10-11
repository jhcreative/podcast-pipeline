"""
guest_form.py — BIO-01: the guest's own name, email and bio from the "Podcast Guest Info" form.

WHY: until 2026-10-01 nothing read the form. The guest name was "TBD" after Round 1, and the bio was whatever the
model wrote from the host's spoken intro (E32), or a placeholder someone pasted over by hand. Every guest from
here on fills in the form; this module makes that the source.

WHERE THE ANSWERS LIVE: the form's linked Sheet, "Podcast Guest Info (Responses)", tab gid 815049215 (John's
working view, 2026-10-01). It is read as a CSV export of THAT tab through the pipeline's existing Drive
delegation (google_auth.drive_credentials, full Drive scope). No Sheets API, no new scope, no new dependency.
The Sheets API is not enabled on the pipeline's project, and doesn't need to be.

HOW A GUEST IS MATCHED: the episode's guest is the form respondent whose name the HOST SAYS in the first minutes
of the recording ("…to our guest, Imogen Farrelly…"). A row matches when its full name (normalized: case,
punctuation, honorifics such as "Dr.") appears in the normalized intro text. Several rows for the same person
(re-submissions) collapse to the newest. Exactly one person must match:
  - one     → {"name", "email", "bio", "website", "timestamp"}
  - none    → None, reason "no form answer names a guest heard in the intro"
  - several → None, reason "N form answers match the intro: <names>" (two guests, or a name said in passing)
BIO-02 (E6, 2026-10-01): speech-to-text mishears surnames. Avery's form says "Avery Kestrelmore MD"; the
transcript says "dr avery kestral more md". So a row also matches when its FIRST name is heard exactly and its
SURNAME is at least SURNAME_SIMILARITY alike (difflib ratio, spaces ignored) to the words that follow that first
name in the intro: "kestrelmore" vs "kestralmore" = 0.91. The exactly-one-person rule still decides. No
first-name-only match, and a miss falls back to the prompt's documented path and is logged and written to the
schedule notes.

PRIVACY: answers are personal data. This module returns the matched row to the caller and NEVER logs answer
text: only counts, the row's timestamp and the match rule. test_r1_reliability.py pins that with a sentinel.
"""
from __future__ import annotations

import csv
import difflib
import io
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

SHEET_ID = "SHEET_ID_EXAMPLE"
SHEET_GID = "815049215"
INTRO_SECONDS = 300
SURNAME_SIMILARITY = 0.85  # the host names the guest in the opening; 5 minutes is generous and keeps chatter out

# Header → field. Matched on normalized header text (the Sheet's own headers carry stray spaces:
# " Your name ", "Bio/Introduction "). A header that is missing is an error, not a silent empty field.
_COLUMNS = {
    "name": ("your name",),
    "bio": ("bio/introduction", "bio / introduction", "bio"),
    "email": ("email address",),
    "timestamp": ("timestamp",),
    "website": ("your primary website",),
}
_HONORIFICS = {"dr", "doctor", "mr", "mrs", "ms", "miss", "prof", "professor", "rev", "phd", "md", "nd"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _name_key(name: str) -> str:
    return " ".join(w for w in _norm(name).split() if w not in _HONORIFICS)


def fetch_rows() -> list[dict]:
    """Download the responses tab and return one dict per answer, with the _COLUMNS fields. Raises on failure."""
    import google_auth
    from google.auth.transport.requests import AuthorizedSession

    session = AuthorizedSession(google_auth.drive_credentials())
    r = session.get(
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export",
        params={"format": "csv", "gid": SHEET_GID},
        timeout=30,
    )
    if r.status_code != 200 or "text/csv" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"guest form export failed: HTTP {r.status_code} {r.headers.get('content-type')}")
    return parse_csv(r.content.decode("utf-8"))


def parse_csv(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("guest form export is empty")
    header = [_norm(h).replace(" / ", "/") for h in rows[0]]
    raw_header = [re.sub(r"\s+", " ", h.strip().lower()) for h in rows[0]]
    idx = {}
    for field, names in _COLUMNS.items():
        hit = next((i for i, h in enumerate(raw_header) if h in names), None)
        if hit is None:
            hit = next((i for i, h in enumerate(header) if h in {_norm(n) for n in names}), None)
        if hit is None and field in ("name", "bio"):
            raise RuntimeError(f"guest form has no '{names[0]}' column (headers changed?)")
        idx[field] = hit
    out = []
    for row in rows[1:]:
        rec = {f: (row[i].strip() if i is not None and i < len(row) else "") for f, i in idx.items()}
        if rec["name"]:
            out.append(rec)
    return out


def _heard(key: str, intro_words: list[str]) -> str | None:
    """'exact' if the full name is in the intro, 'similar' if the first name is heard and the surname is
    SURNAME_SIMILARITY-alike to the 1–3 words after it, else None."""
    if f" {key} " in f" {' '.join(intro_words)} ":
        return "exact"
    first, rest = key.split()[0], "".join(key.split()[1:])
    for i, w in enumerate(intro_words):
        if w != first:
            continue
        for n in (1, 2, 3):
            cand = "".join(w2 for w2 in intro_words[i + 1:i + 1 + n] if w2 not in _HONORIFICS)
            if cand and difflib.SequenceMatcher(None, rest, cand).ratio() >= SURNAME_SIMILARITY:
                return "similar"
    return None


def match_guest(rows: list[dict], intro_text: str) -> tuple[dict | None, str]:
    """Return (row, rule) for exactly one person named in the intro, else (None, reason)."""
    intro_words = _norm(intro_text).split()
    people: dict[str, dict] = {}
    rules: dict[str, str] = {}
    for rec in rows:
        key = _name_key(rec["name"])
        if len(key.split()) < 2:
            continue  # a single-word name can't be matched safely against free speech
        how = _heard(key, intro_words)
        if how:
            rules[key] = how
            prev = people.get(key)
            if prev is None or rec.get("timestamp", "") >= prev.get("timestamp", ""):
                people[key] = rec
    if len(people) == 1:
        key, rec = next(iter(people.items()))
        return rec, ("full name heard in the intro" if rules[key] == "exact"
                     else "first name heard, surname spelled differently in the transcript (form spelling used)")
    if not people:
        return None, "no form answer names a guest heard in the intro"
    return None, f"{len(people)} form answers match the intro: {', '.join(sorted(r['name'] for r in people.values()))}"


def intro_text_from_segments(segments: list[dict], seconds: int = INTRO_SECONDS) -> str:
    return " ".join((s.get("text") or "") for s in segments if float(s.get("start") or 0) < seconds)


def lookup(segments: list[dict]) -> tuple[dict | None, str]:
    """Round 1 entry point. Never raises: a form outage is a logged miss, not a failed episode."""
    try:
        rows = fetch_rows()
    except Exception as exc:  # noqa: BLE001 — degrade to the documented no-bio path
        logger.warning("guest_form: lookup unavailable (%s): %s", type(exc).__name__, exc)
        return None, f"form unavailable ({type(exc).__name__})"
    rec, rule = match_guest(rows, intro_text_from_segments(segments))
    if rec:
        logger.info("guest_form: matched 1 of %d answers (%s; submitted %s)", len(rows), rule, rec.get("timestamp", "?"))
    else:
        logger.info("guest_form: no match among %d answers (%s)", len(rows), rule if "match the intro" not in rule else rule.split(":")[0])
    return rec, rule
