"""
blog_post.py — deterministic Tailor blog post build (NO model in the path). BLOG-02.

rockstarblends.com moved to Tailor on 2026-09-21. The old Step 6 had the model write
DropInBlog HTML (a prompt since retired, BLOG-02b) for a person to paste. Every
input the post needs is already structured — apple_summary.md (hook, bullets, tune-in,
About), quotes.json (the approved quotes), faq.md (Q/A pairs), and the listen URLs the
Phase-2 trigger carries — so the model has nothing to decide. Same principle as TY-02 /
ADR-0010: a template fill is code.

The target is the live :8627 shape shared by E27/E28/E30/E31 (16 blocks for 5 quotes;
2n+6 in general):

    0      section, empty h2 — author intro + Spotify/Apple/Audible cta nodes
    1      toc "On this page"
    2      section, empty h2 — hook paras, "Conversations include:", ul, tail paras
    3      section "About ⟨guest⟩" — bio paras
    4, 5   section "Episode Highlights" (content = h3 heading) + blockquote
    6..    per remaining quote: section (empty h2, content = h3 heading) + blockquote
    n-2    section "Share this episode" — SHARE_SENTENCE
    n-1    faq "FAQs"

PURE module: stdlib + thank_you (itself stdlib-only). No Drive/HTTP/anthropic imports,
so it is unit-testable directly. The gate is content_gates.assert_blog_post_wellformed —
the caller runs it BEFORE any API call (AC-3).

What this module returns is the whole post as ONE dict. Tailor v0.9.91 splits it across
two calls: PageCreate (POST /api/pages) takes title/slug/parent_slug/tag_slugs/layout/
hero_layout/meta_description but NOT blocks, content_mode or status — those are
PageUpdate (PUT) fields. The split is blog_publish's job (02b), not the build's. `status`
is deliberately absent: a created page is a draft, and nothing in the pipeline sets
`published` (AC-4). `hero_media_id` is always None: the episode tile is the post's Cover
Image (`thumbnail_media_id`, John 2026-09-23), which blog_publish sets after uploading it.
"""

import re

from content_gates import GateFailure
from thank_you import AUDIBLE_URL, first_name

# The one fixed share line (BLOG-02 Resolved OPEN-3), written from the five live share
# lines. Heather rewrites it per post in the editor when she wants to.
SHARE_SENTENCE = (
    "Know someone who needs this conversation right now? Send them this episode — and "
    "tag us @genxhitsmenopause so we can amplify right back."
)

SHOW_SUFFIX = "GenX Hits Menopause"
from host_identity import HOST_NAME  # noqa: E402  REEL-09: one definition of her name
HOST_HREF = "/about"
META_DESCRIPTION_MAX = 160
# Tailor's PageCreate slug cap (schemas/pages.py, max_length=64).
SLUG_MAX = 64

# Abbreviations a sentence can't end on. The first hook sentence of E31 opens
# "Heather gets together with Dr. Avery …" — a naive split ends it at "Dr."
_NON_TERMINAL = ("Dr.", "Mr.", "Mrs.", "Ms.", "Mx.", "Prof.", "St.", "Jr.", "Sr.", "vs.", "e.g.", "i.e.")

_CONVERSATIONS_RE = re.compile(r"^conversations include:?$", re.IGNORECASE)
# "About ⟨guest⟩" — matched on the paragraph's plain text, because the markdown export
# bolds it ("**About Dr. Avery Example**"). One short line that does not END like a
# sentence ("Dr." inside is fine), so a tail paragraph that merely starts "About twenty
# minutes in, …" is not taken for it.
_ABOUT_RE = re.compile(r"^about\s+([^\n]{0,79}[^.!?\n])$", re.IGNORECASE)
# A Conversations bullet: optional "- " then a **bold lead**, then the body.
_BULLET_RE = re.compile(r"^(?:[-*]\s+)?\*\*(.+?)\*\*(.*)$", re.DOTALL)
# Inline markdown the summary actually uses: **bold**, *em*, [text](url).
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)")


# ── text ───────────────────────────────────────────────────────────────────────


def _squash(s: str) -> str:
    """Collapse every whitespace run to one space and trim. The hand-edited summaries
    carry double spaces ("specialist -  to"); the live posts never do."""
    return re.sub(r"\s+", " ", s or "").strip()


def smart_quotes(s: str, prev: str = " ") -> str:
    """Straight quotes → typographic, the way every live post reads (the summary says
    "doesn't", the post says "doesn’t").

    A quote OPENS when it follows the start of text, whitespace or an opening bracket/
    dash; otherwise it closes. An apostrophe is always a closing ’ unless it opens a
    quotation by the same rule. `prev` is the character before `s`, so inline segments
    split by markdown keep their context ("**bad**" inside quotes). Already-curly
    characters pass through untouched, so this is idempotent.
    """
    out = []
    for ch in s:
        opens = prev.isspace() or prev in "([{“‘—–-"
        if ch == '"':
            ch = "“" if opens else "”"
        elif ch == "'":
            ch = "‘" if opens else "’"
        out.append(ch)
        prev = ch
    return "".join(out)


def _text(s: str, marks=(), href=None) -> dict:
    return {"type": "text", "text": s, "marks": list(marks), "href": href}


def unescape_markdown(s: str) -> str:
    """Undo the backslash escapes Drive's text/markdown export adds (ADR-0008 addendum,
    2026-09-22): "Example \\- phytopharmacist", "\\*bliss\\*". The Doc holds the plain
    characters; the escape only exists so a markdown renderer won't read them as syntax.
    Escaped asterisks come back as real asterisks, so a hand-typed *bliss* is emphasis —
    which is what the live post shows (an em mark)."""
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~<])", r"\1", s)


def _segments(s: str) -> list[tuple[str, str | None, str | None]]:
    """Markdown-lite → [(text, mark, href)]. **x** → strong, *x* → em, [t](u) → href.
    Whitespace is squashed and escapes undone BEFORE the split, so a segment keeps its
    own leading space (" Anandamide - Sanskrit for "), exactly as the live nodes do."""
    s = unescape_markdown(_squash(s))
    out, pos = [], 0
    for m in _INLINE_RE.finditer(s):
        if m.start() > pos:
            out.append((s[pos:m.start()], None, None))
        bold, em, link_text, url = m.groups()
        out.append((bold or em or link_text, "strong" if bold else "em" if em else None, url))
        pos = m.end()
    if pos < len(s):
        out.append((s[pos:], None, None))
    return out


def markdown_to_text(s: str) -> str:
    """The plain text of one markdown line — escapes undone, emphasis markers dropped,
    NO typographic change. For values other code compares or slugs (the episode title
    from a markdown-exported apple_summary, ADR-0009)."""
    return "".join(text for text, _, _ in _segments(s))


def _inlines(s: str, smart: bool = True) -> list[dict]:
    """Tailor inline nodes for one paragraph of markdown-lite. `smart` curls straight
    quotes with each segment's context carried across the markdown split."""
    nodes, prev = [], " "
    for text, mark, href in _segments(s):
        nodes.append(_text(smart_quotes(text, prev) if smart else text,
                           marks=(mark,) if mark else (), href=href))
        prev = text[-1]
    return nodes


def _plain(s: str) -> str:
    """Inline text with the markdown stripped — for headings, which carry bare text."""
    return "".join(n["text"] for n in _inlines(s))


def _para(s: str) -> dict:
    return {"type": "p", "inlines": _inlines(s)}


def first_sentence(s: str) -> str:
    """The text up to and including the first sentence terminator that is not an
    abbreviation from _NON_TERMINAL or a middle initial (E30: "Morgan W. Kestrel").
    The whole string if there is none."""
    s = _squash(s)
    for m in re.finditer(r"[.!?](?=\s|$)", s):
        head = s[: m.end()]
        if not head.endswith(_NON_TERMINAL) and not re.search(r"(?:^|\s)[A-Z]\.$", head):
            return head
    return s


# Words a CUT description must not end on (PO ruling, Jen's review): "…South Florida, and"
# reads as broken, so they are peeled off after the cap along with trailing punctuation.
_DANGLING = frozenset({"and", "or", "but", "with", "of", "the", "a"})


def cap_on_word(s: str, limit: int) -> str:
    """≤ limit chars, cut on a word boundary, no ellipsis (Resolved OPEN-2). When a cut
    happens, a trailing conjunction/preposition/article or comma is peeled off too."""
    if len(s) <= limit:
        return s
    cut = s[:limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:—–-")
    while " " in cut and cut.rsplit(" ", 1)[1].lower() in _DANGLING:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:—–-")
    return cut


def join_and(items: list[str]) -> str:
    """'a', 'a and b', 'a, b and c' — last pair joined with "and", no Oxford comma
    (Resolved OPEN-4)."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def propose_slug(title: str) -> str:
    """The slug the live posts use: the title with apostrophes dropped and every
    non-alphanumeric run hyphenated — the BLOG-01 rule — with NO show/episode suffix
    (E30 "feed-your-head-gut-microbiome-hormones-midlife-vitality"), capped at Tailor's
    64 on a hyphen boundary. A proposal only: the URL comes from what create returns
    (AC-6)."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", re.sub(r"['‘’]", "", title)).lower().strip("-")
    if len(slug) > SLUG_MAX:
        slug = slug[: SLUG_MAX + 1].rsplit("-", 1)[0]
    return slug


def attribution(speaker: str) -> str:
    """Honorific(s) + first name, the live attribution: "Dr. Avery Example" →
    "Dr. Avery", "Heather Reilly-Hemstra" → "Heather". The first-name rule is
    thank_you.first_name (one definition); the honorifics are whatever precedes it."""
    s = _squash(speaker)
    fn = first_name(s)
    return (s[: s.index(fn)] + fn).strip() if fn else s


def quote_source_text(q: dict) -> str:
    """The quote's verbatim text, in the order quote_cards/quote_source reads it:
    `text` (E27+), else `quote_text_clean` (E23–E25)."""
    return (q.get("text") or q.get("quote_text_clean") or "").strip()


def quote_display(q: dict) -> str:
    """What a blockquote carries: the verbatim text, typographic, in curly quotes."""
    return "“" + smart_quotes(_squash(quote_source_text(q))) + "”"


def title_from_apple_summary(text: str) -> str | None:
    """Extract the episode title from apple_summary.md — the human-review surface.

    Handles both shapes the file takes:
      • Pipeline-generated H1:
        "Gen X Hits Menopause · S2E4 · Evocative Hook | Specific Topic · ft. Guest"
        → the title is the 3rd ' · '-delimited segment (index 2); index -1 would
        wrongly grab the optional "ft. Guest" byline.
      • Hand-edited by John: he rewrites the summary as a clean title line followed
        by prose, dropping the show/code/guest segments — so the first non-empty
        line IS the title.

    Distinguisher: the structured H1 always has ≥3 ' · ' segments; a hand-edited
    title does not (John separates with ' | ' / ' — ', never ' · '). Returns None
    if there is no non-empty line. BOM is already stripped by drive_client.
    """
    for raw in (text or "").splitlines():
        # The Doc now downloads as text/markdown (ADR-0008 addendum, 2026-09-22): the
        # title arrives as "## Hook | Topic", may carry bold, and the export escapes
        # characters ("Hook \- Topic"). markdown_to_text undoes both, and changes
        # nothing else — no smart quotes, so reels/thank-you/slug see the same title.
        line = markdown_to_text(raw.strip().lstrip("#").strip())
        if not line:
            continue
        parts = [p.strip() for p in line.split(" · ")]
        if len(parts) >= 3:
            return parts[2]
        return line
    return None


# ── parsing ────────────────────────────────────────────────────────────────────


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def parse_apple_summary(text: str) -> dict:
    """apple_summary.md → {hook, bullets, tail, guest, bio}.

    Shape (prompts/apple_summary.md, and John's hand-edited form): a title line,
    hook paragraphs, "Conversations include", one paragraph per bullet, tail
    paragraphs (tune-in CTA, sometimes one before it — E30), "About ⟨guest⟩", bio.

    A bullet is a paragraph that opens with a **bold lead**. That bold is the ONLY
    lead/body delimiter this trusts. The old text/plain export of John's edited Doc
    flattened it ("The Microbiome Footprint What your…", one space), so the download now
    exports *.md Docs as text/markdown, which keeps it (ADR-0008 addendum, 2026-09-22).
    A summary without it is refused — guessing where a heading ends is worse.
    The markdown export's "## " title line and bold "**About …**" are handled here.
    """
    paras = _paragraphs(text)
    if not paras:
        raise GateFailure("apple_summary is empty — nothing to build a post from")
    paras = paras[1:]  # the title line; the resolved title arrives via meta

    try:
        ci = next(i for i, p in enumerate(paras) if _CONVERSATIONS_RE.match(p))
    except StopIteration:
        raise GateFailure("apple_summary has no 'Conversations include' line") from None
    try:
        ai = next(i for i, p in enumerate(paras) if i > ci and _ABOUT_RE.match(markdown_to_text(p)))
    except StopIteration:
        raise GateFailure("apple_summary has no 'About ⟨guest⟩' section after the bullets") from None

    hook = paras[:ci]
    bullets, tail = [], []
    for p in paras[ci + 1: ai]:
        m = _BULLET_RE.match(p)
        if m and not tail:
            bullets.append((m.group(1), m.group(2)))
        elif bullets:
            tail.append(p)
        else:
            raise GateFailure(
                f"apple_summary bullet has no **bold lead** ({_squash(p)[:60]!r}…). The lead "
                "is the only lead/body delimiter; bold the lead in apple_summary.md."
            )
    if not hook or not bullets or not tail:
        raise GateFailure(
            f"apple_summary is missing a part (hook={len(hook)}, bullets={len(bullets)}, "
            f"tail={len(tail)} paragraphs)"
        )
    guest = _squash(_ABOUT_RE.match(markdown_to_text(paras[ai])).group(1))
    bio = paras[ai + 1:]
    if not bio:
        raise GateFailure(f"apple_summary 'About {guest}' has no bio paragraphs")
    return {"hook": hook, "bullets": bullets, "tail": tail, "guest": guest, "bio": bio}


def parse_faq(text: str) -> list[tuple[str, list[str]]]:
    """faq.md (prompts/faq.md: 'Q: …' / 'A: …' pairs) → [(question, [answer paras])]."""
    pairs = []
    for m in re.finditer(r"^Q:\s*(.+?)\n\s*A:\s*(.+?)(?=^Q:|\Z)", text or "", re.M | re.S):
        answer = _paragraphs(m.group(2))
        pairs.append((_squash(m.group(1)), answer))
    if not pairs:
        raise GateFailure("faq.md has no 'Q: … / A: …' pairs")
    return pairs


# ── blocks ─────────────────────────────────────────────────────────────────────


def _headline(text: str = "") -> dict:
    return {"inlines": [{"text": text}] if text else [], "type": "h2"}


def _section(headline: str, content: list[dict]) -> dict:
    return {"type": "section", "headline": _headline(headline), "content": content,
            "image": None, "keep_narrow": False}


def _h3(text: str) -> dict:
    return {"inlines": [{"text": text}], "type": "h3"}


def _cta(label: str, href: str) -> dict:
    return {"type": "cta", "label": label, "href": href}


def _blockquote(q: dict) -> dict:
    return {"type": "blockquote", "quote": quote_display(q),
            "attribution": attribution(q.get("speaker", "")), "source": None,
            "source_url": None, "background": "plain", "background_media_id": None}


def descriptor(guest: str, bio_first_para: str) -> str:
    """The About section's first sentence after the guest's name (Resolved OPEN-4),
    with the linking verb and full stop dropped so it reads inside the em dashes:
    "Dr. X is a phytopharmacist … physiology." → "a phytopharmacist … physiology"."""
    sentence = _plain(first_sentence(bio_first_para))
    if not sentence.startswith(guest):
        raise GateFailure(
            f"About's first sentence does not open with the guest name {guest!r} "
            f"({sentence[:60]!r}…) — cannot take the descriptor after it"
        )
    rest = sentence[len(guest):].strip()
    rest = re.sub(r"^(is|was|,)\s+", "", rest)
    return rest.rstrip(".").strip()


def build_post(apple_summary_text: str, quotes: list, faq_text: str, meta: dict) -> dict:
    """The whole post, deterministically. Same inputs → the same dict, key order
    included, so json.dumps is byte-identical (AC-1).

    meta:
      episode_title   "Hook | Topic" — the title publish_finalize resolved (ADR-0009:
                      apple_summary wins over the schedule). Gated by is_real_title.
      episode_number  int
      spotify_url, apple_url

    Each quote needs a `heading` — its h3, written by the Brand Team at quote selection
    (BLOG-02 F-1 ruling). Raises GateFailure on input it cannot parse.
    """
    title = _squash(meta["episode_title"])
    summary = parse_apple_summary(apple_summary_text)
    guest = summary["guest"]
    leads = [_plain(lead) for lead, _ in summary["bullets"]]

    intro = [
        _text("In "),
        _text(f"“{smart_quotes(title)}”", marks=("strong",)),
        _text(", host "),
        _text(HOST_NAME, href=HOST_HREF),
        _text(" sits down with "),
        _text(guest, marks=("strong",)),
        _text(f" — {descriptor(guest, summary['bio'][0])} — for a conversation about "
              f"{join_and(leads)}."),
    ]
    blocks = [
        _section("", [
            {"type": "p", "inlines": intro},
            _cta("Listen on Spotify", meta["spotify_url"]),
            _cta("Listen on Apple", meta["apple_url"]),
            _cta("Listen on Audible", AUDIBLE_URL),
        ]),
        {"type": "toc", "title": "On this page"},
        _section("", [
            *(_para(p) for p in summary["hook"]),
            _para("Conversations include:"),
            {"type": "ul", "items": [
                {"type": "li", "inlines": _inlines(f"**{lead}**{body}")}
                for lead, body in summary["bullets"]
            ]},
            *(_para(p) for p in summary["tail"]),
        ]),
        _section(f"About {guest}", [_para(p) for p in summary["bio"]]),
    ]
    for i, q in enumerate(quotes):
        heading = _squash(q.get("heading", ""))
        blocks.append(_section("Episode Highlights" if i == 0 else "",
                               [_h3(smart_quotes(heading))]))
        blocks.append(_blockquote(q))
    blocks.append(_section("Share this episode", [_para(SHARE_SENTENCE)]))
    blocks.append({"type": "faq", "title": "FAQs", "questions": [
        {"type": "question", "prompt": [{"text": smart_quotes(question)}],
         "answer": [_para(a) for a in answer]}
        for question, answer in parse_faq(faq_text)
    ]})

    full_title = f"{title} | {SHOW_SUFFIX} E{int(meta['episode_number'])}"
    return {
        "title": full_title,
        "slug": propose_slug(title),
        "parent_slug": "blog",
        "tag_slugs": ["podcast"],
        "layout": "child",
        "hero_layout": "simple_text",
        # The episode tile is the Cover Image (thumbnail_media_id), NOT the hero (John,
        # 2026-09-23). The hero stays empty; blog_publish sets the cover after the upload.
        "hero_media_id": None,
        "meta_description": cap_on_word(_plain(first_sentence(summary["hook"][0])),
                                        META_DESCRIPTION_MAX),
        "content_mode": "blocks",
        "blocks": blocks,
    }
