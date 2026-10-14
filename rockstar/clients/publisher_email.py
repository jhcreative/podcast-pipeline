"""
publisher_email.py — one home for every assumption about the publisher's
"Your Episode is Live" notification email.

WHY THIS MODULE EXISTS
----------------------
The podcast publisher's notification is a DRIFTING integration contract, and we
keep getting burned when it shifts under us:

  • 2026-06-16: Legacy Publisher started sending via **Example Publisher** (hello@publisher.example.test).
    Our trigger only matched @legacy-publisher.example.test, so the live email was silently
    ignored — nothing kicked off.
  • Same day: the subject dropped the "EP NN" number. Our parser identified the
    episode *by that number*, so even a hand-triggered run hard-failed.
  • Same day: the email is now HTML-only (listen links behind buttons, no
    text/plain), and the publisher's template mangles the Apple episode id
    ('?i=1000…' → '?i<ctrl>00…').
  • Long-standing latent bug: every reply in the thread ("Re: Your Episode is
    Live …") also matched the trigger, so the publisher contact's bio-character-limit reply spawned
    a PUBLISH_FINALIZE job that PermanentFailed.

The lesson (John, 2026-06-16): assume it will drift again. Support old AND new
forms together, and make the next change a config/fixture edit — not a hunt
through rockstar/service/webhook_service.py and publish_finalize.py. Everything below is what this
module defends against.
"""

import os
import re
from email.utils import getaddresses, parseaddr

# ── sender allowlist ──────────────────────────────────────────────────────────
# Keep BOTH legacy Legacy Publisher domains AND Example Publisher. Override (or add the next
# vendor) via PUBLISHER_SENDER_DOMAINS=a.com,b.com — one env edit, no code change.
_DEFAULT_SENDER_DOMAINS = "legacy-publisher.example.test,legacy-publisher.example.org,publisher.example.test"


def sender_domains() -> list[str]:
    raw = os.environ.get("PUBLISHER_SENDER_DOMAINS", _DEFAULT_SENDER_DOMAINS)
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def sender_domain(sender: str) -> str:
    """The From address's domain, lower-cased ("" when there is no address)."""
    addr = parseaddr(sender or "")[1]
    return addr.rpartition("@")[2].strip().lower() if "@" in addr else ""


def is_publisher_sender(sender: str) -> bool:
    """True if the From address's domain IS an allowlisted domain — exactly (TRIG-03 AC-10, Orly).
    The old test was a substring: '@legacy-publisher.example.test' in the header, which a lookalike such as
    x@legacy-publisher.example.test.attacker.example also contains. A vendor subdomain is added to the
    allowlist by name, never matched by suffix."""
    return sender_domain(sender) in sender_domains()


# ── sender authentication (TRIG-03 AC-10, Orly's RTS-6 rule) ──────────────────
# The From line is whatever the sender typed. Legacy Publisher publishes DMARC p=NONE, so a forged
# From is still delivered — only marked dmarc=fail. What we trust is GOOGLE'S verdict: the topmost
# Authentication-Results header, stamped by mx.google.com when the mail arrived. A sender can add
# headers of its own, but they land BELOW Google's; any further copy is ignored. Real headers from
# the box are the fixture (tests/fixtures/trig03/auth_headers.json).
_GOOGLE_AUTHSERV = "mx.google.com"
_COMMENT_RE = re.compile(r"\([^()]*\)")


def _ar_clauses(header: str) -> tuple[str, list[dict]] | None:
    """(authserv-id, [{'method', 'result', props…}]) from one Authentication-Results value, or None when
    the header is ambiguous. Comments are removed first: ARC's comment carries 'dkim=pass … dmarc=pass' of
    its own, a claim about an earlier hop, not Google's result.

    Ambiguous = fails closed (cold review of PR #31, Orly): a quote, or parentheses that don't balance.
    The sender controls its envelope local part, which Google copies into the header (the spf comment and
    smtp.mailfrom); a local part such as `"a)b;dkim=pass header.d=…"` could close a comment early and leave
    a forged clause behind. Real verdicts carry neither (tests/fixtures/trig03/auth_headers.json)."""
    if '"' in header or "\\" in header:
        return None
    depth = 0
    for ch in header:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            return None
    if depth:
        return None
    text = header
    while _COMMENT_RE.search(text):
        text = _COMMENT_RE.sub(" ", text)
    parts = [p.strip() for p in text.split(";")]
    authserv = parts[0].split()[0].lower() if parts and parts[0] else ""
    clauses = []
    for part in parts[1:]:
        tokens = part.split()
        if not tokens or "=" not in tokens[0]:
            continue
        method, _, result = tokens[0].partition("=")
        clause = {"method": method.lower(), "result": result.lower()}
        for tok in tokens[1:]:
            k, sep, v = tok.partition("=")
            if sep:
                if k.lower() in clause:
                    return None  # a property given twice: which one counts is a guess, so it fails closed
                clause[k.lower()] = v.lower()
        clauses.append(clause)
    return authserv, clauses


def _aligned(domain: str, from_domain: str) -> bool:
    """Relaxed DMARC alignment: the signing domain is the From domain or a subdomain of it."""
    return domain == from_domain or domain.endswith("." + from_domain)


def authenticated_sender(headers: list[dict]) -> tuple[bool, str]:
    """(ok, reason) for a Gmail message's headers ([{'name','value'}], in message order).

    ok only when the From domain is allowlisted (exactly) AND the topmost Authentication-Results
    is Google's AND it shows either dmarc=pass for the From domain, or dkim=pass from a domain
    aligned with it. Everything else fails closed, with the reason named."""

    def values(name: str) -> list[str]:
        return [h.get("value", "") for h in headers if h.get("name", "").lower() == name]

    froms = values("from")
    if len(froms) != 1 or len(getaddresses(froms)) != 1:
        return False, "the message must carry exactly one From address"
    sender = froms[0]
    domain = sender_domain(sender)
    if domain not in sender_domains():
        return False, f"sender domain {domain or '(none)'!r} is not an allowlisted publisher domain"
    results = values("authentication-results")
    if not results:
        return False, "no Authentication-Results header"
    parsed = _ar_clauses(results[0])
    if parsed is None:
        return False, "the topmost Authentication-Results is ambiguous (a quote or unbalanced parentheses)"
    authserv, clauses = parsed
    if authserv != _GOOGLE_AUTHSERV:
        return False, f"the topmost Authentication-Results is from {authserv!r}, not {_GOOGLE_AUTHSERV}"
    # One verdict decides. Google writes one dmarc clause; two is a header we can't read, never a vote.
    dmarc = [c for c in clauses if c["method"] == "dmarc"]
    if len(dmarc) > 1:
        return False, f"{len(dmarc)} dmarc clauses in {_GOOGLE_AUTHSERV}'s header — ambiguous"
    if dmarc:
        d = dmarc[0]
        if d["result"] == "pass" and d.get("header.from") == domain:
            return True, f"dmarc=pass for {domain} ({_GOOGLE_AUTHSERV})"
        return False, f"{_GOOGLE_AUTHSERV} says dmarc={d['result']} for {d.get('header.from') or '(no header.from)'}"
    # No dmarc verdict: an aligned dkim=pass decides, unless any aligned signature failed (a conflict).
    aligned = []
    for c in clauses:
        if c["method"] == "dkim":
            signer = c.get("header.d") or c.get("header.i", "").rpartition("@")[2]
            if signer and _aligned(signer, domain):
                aligned.append(c["result"])
    if aligned and all(r == "pass" for r in aligned):
        return True, f"dkim=pass aligned with {domain} ({_GOOGLE_AUTHSERV})"
    if aligned:
        return False, f"aligned dkim results conflict ({', '.join(aligned)})"
    return False, f"{_GOOGLE_AUTHSERV} shows no dmarc verdict and no aligned dkim=pass for {domain}"


def gmail_from_query() -> str:
    """Gmail search clause covering every allowlisted sender, e.g.
    'from:(@legacy-publisher.example.test OR @publisher.example.test)'."""
    return "from:(" + " OR ".join("@" + d for d in sender_domains()) + ")"


# ── publish-notification vs reply ─────────────────────────────────────────────
# The trigger phrase, anywhere in the subject. We deliberately do NOT require an
# episode number — the publisher dropped it.
_LIVE_SUBJECT_RE = re.compile(r"your episode is (now )?live|your episode .*(is|now) published", re.IGNORECASE)
# The live email is the FIRST message in its thread. Replies/forwards
# ("Re:"/"Fwd:") match the live-subject test but carry no links — they must NOT
# trigger a job. This is what made jobs 52/55/56/57 fire on the publisher contact's replies.
_REPLY_SUBJECT_RE = re.compile(r"^\s*(re|fwd|fw)\s*:", re.IGNORECASE)


def is_publish_notification(subject: str, *, in_reply_to: str = "") -> bool:
    """True only for an ORIGINAL publish notification (not a reply/forward)."""
    subject = subject or ""
    if not _LIVE_SUBJECT_RE.search(subject):
        return False
    if _REPLY_SUBJECT_RE.match(subject):
        return False
    if in_reply_to.strip():  # an In-Reply-To header means it's a reply in a thread
        return False
    return True


# ── URL extraction + Apple-id repair ──────────────────────────────────────────
_APPLE_RE = re.compile(r"https://podcasts\.apple\.com/[^\s\"'<>)]+", re.IGNORECASE)
_SPOTIFY_RE = re.compile(r"https://open\.spotify\.com/episode/[^\s\"'<>)]+", re.IGNORECASE)
_MM_RE = re.compile(r"https://legacy-publisher\.example\.test/[^\s\"'<>)]+", re.IGNORECASE)
_APPLE_ID_RE = re.compile(r"(https://podcasts\.apple\.com/\S*?/id\d+)\?i([^&\s\"'<>)]*)", re.IGNORECASE)


def normalize_apple_url(url: str) -> str:
    """
    Repair the Apple episode id the publisher's template corrupts.

    Apple episode ids are 13 digits beginning '1000…'. The publisher repeatedly
    mangles '?i=1000xxxxxxxxx' into '?i<ctrl>00xxxxxxxxx' — the '=10' collapses to
    a control char, leaving 11 digits. We take the digits after '?i'; if there are
    11 we restore the leading '10'. Tracking suffixes (&uo=…) are dropped.
    Returns the cleaned URL, or the original (minus &uo) if it can't be repaired
    safely — the caller still URL-verifies before any artifact cites it.
    """
    if not url:
        return url
    m = _APPLE_ID_RE.search(url)
    if not m:
        return url.split("&uo=")[0]
    base, raw_i = m.group(1), m.group(2)
    digits = re.sub(r"\D", "", raw_i)
    if len(digits) == 11:
        digits = "10" + digits
    if len(digits) != 13:
        return url.split("&uo=")[0]
    return f"{base}?i={digits}"


# ── episode-link validation (TRIG-03 AC-11, Orly) ─────────────────────────────
# Before any artifact cites a link from an email, the link must be an EPISODE link of THIS show:
# Apple's show id is pinned, the episode id is Apple's 13-digit '1000…' form, and a Spotify
# episode id is 22 base-62 characters. A link that fails is never written; the caller alerts.
APPLE_SHOW_ID = "1785708777"
_APPLE_EPISODE_RE = re.compile(
    rf"^https://podcasts\.apple\.com/[a-z]{{2}}/podcast/[^/?#]+/id{APPLE_SHOW_ID}\?i=1000\d{{9}}$"
)
_SPOTIFY_EPISODE_RE = re.compile(r"^https://open\.spotify\.com/episode/[A-Za-z0-9]{22}$")


def clean_spotify_url(url: str) -> str:
    """Drop Spotify's share tracking (?si=…) and any other query or fragment."""
    return re.split(r"[?#]", url or "", maxsplit=1)[0]


def episode_url_problems(apple_url: str, spotify_url: str) -> list[str]:
    """Why these are not this show's episode links ([] when both are). Spotify is checked after
    clean_spotify_url; Apple after normalize_apple_url (the caller passes the cleaned values)."""
    problems = []
    if not _APPLE_EPISODE_RE.match(apple_url or ""):
        problems.append(f"Apple link is not an episode of show id{APPLE_SHOW_ID}: {apple_url!r}")
    if not _SPOTIFY_EPISODE_RE.match(spotify_url or ""):
        problems.append(f"Spotify link is not an episode link: {spotify_url!r}")
    return problems


def episode_ids(apple_url: str, spotify_url: str) -> tuple[str, str]:
    """(Apple ?i= id, Spotify episode id) — what a duplicate check compares across episodes."""
    a = re.search(r"\?i=(\d+)", apple_url or "")
    s = re.search(r"/episode/([A-Za-z0-9]+)", spotify_url or "")
    return (a.group(1) if a else "", s.group(1) if s else "")


def extract_publish_urls(text: str = "", html: str = "") -> dict:
    """Pull {apple_url, spotify_url, mm_url} from the email body.

    Searches text AND html because the current template is HTML-only (links live
    in button hrefs, with no text/plain part). Apple URL is repaired in place.
    """
    blob = (text or "") + "\n" + (html or "")
    apple = _APPLE_RE.search(blob)
    spotify = _SPOTIFY_RE.search(blob)
    mm = _MM_RE.search(blob)
    strip = ".,;)\"'"
    return {
        "apple_url": normalize_apple_url(apple.group(0).rstrip(strip)) if apple else "",
        "spotify_url": spotify.group(0).rstrip(strip) if spotify else "",
        "mm_url": mm.group(0).rstrip(strip) if mm else "",
    }


# ── episode identification (no EP number required) ────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Schedule statuses that mean "Round 1 done, not yet published" (round_1.py writes
# "drafting" on a first run and "reprocessed" on a re-run).
_AWAITING_STATUSES = ("submitted-awaiting-publish", "ready-for-publish", "drafting", "reprocessed")


def identify_episode(subject: str, body: str, episodes: list[dict]) -> dict | None:
    """
    Map a publish email to a pub_schedule episode WITHOUT relying on an EP number.

    Tried in order:
      1. explicit 'EP NN' — if the publisher still sends it (back-compat);
      2. title / hook match — the subject text against each episode's title;
      3. the single episode still awaiting publication.

    Returns the matched episode dict, or None if zero/ambiguous. The caller MUST
    fail safe on None (alert a human, never guess) — a wrong-episode publish is
    worse than a held one.
    """
    eligible = [e for e in episodes if e.get("drive_folder_id")]

    # 1) explicit EP number (back-compat with the old subject format)
    m = re.search(r"\bEP\s*0*(\d+)", f"{subject} {body}", re.IGNORECASE)
    if m:
        n = int(m.group(1))
        for e in eligible:
            if e.get("episode_number") == n:
                return e

    # 2) title / hook match against the subject
    subj_norm = _norm(subject)
    hook_hit = None
    for e in eligible:
        title = e.get("episode_title") or ""
        if not title:
            continue
        if _norm(title) and _norm(title) in subj_norm:
            return e  # full-title match is unambiguous
        hook = title.split("|")[0] if "|" in title else title
        hook_n = _norm(hook)
        if len(hook_n) > 8 and hook_n in subj_norm and hook_hit is None:
            hook_hit = e
    if hook_hit:
        return hook_hit

    # 3) single awaiting-publication candidate. "reprocessed" is what rockstar/jobs/round_1.py
    # writes when an episode's Round 1 is re-run from the inbox (E32, 2026-09): it is as
    # un-published as "drafting", so it must count, or a re-run episode could never be
    # the fallback match.
    waiting = [e for e in eligible if e.get("status") in _AWAITING_STATUSES and not e.get("pipeline_done")]
    if len(waiting) == 1:
        return waiting[0]

    return None
