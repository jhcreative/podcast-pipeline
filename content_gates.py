"""
content_gates.py — shared content-quality gates for the Rockstar Pipeline.

ONE definition, many callers (PQ-4 DRY). These gates make "junk that looks
publishable" impossible by failing the job loudly BEFORE anything irreversible
happens — a counter bump, a schedule write, a render, an upload. Quality is a
gate, not a hope (PQ-5).

History: the title + guest gates first lived inside jobs/publish_finalize.py
(Phase 5). That was too late — Phase 2 reels are already blocked by then, and
E29 (2026-06-23) completed Phase 1 with episode_title="TBD" because Phase 1 had
NO gate at all. TITLE-01 extracts them here so BOTH phases enforce the same
rule from the same source.

A failed gate raises GateFailure. It is a *permanent* failure: retrying the
same input will fail identically (the title is still a placeholder), so the
worker must not back-off-retry it — the caller is responsible for translating
GateFailure into its phase's permanent-failure type if it has one.
"""

import host_identity
import re

# Placeholder tokens a real title must never contain. Word-boundaried so a
# legitimate title containing e.g. "Pendulum" is not matched on "pending".
_PLACEHOLDER_TITLE_RE = re.compile(
    r"\b(tbd|pending|placeholder|untitled|tk)\b", re.IGNORECASE
)


class GateFailure(Exception):
    """A content-quality gate rejected the input. Permanent — retry won't fix it.

    Phase-specific callers may catch this and re-raise as their own permanent
    failure type (e.g. publish_finalize.PermanentFailure) so the worker skips
    retries; round_1 lets it propagate as a loud, job-failing exception.
    """


def is_real_title(episode_title: str) -> bool:
    """True if the title is present and not a placeholder. The predicate behind
    assert_real_title, exposed so callers can BRANCH on title validity without
    catching an exception — e.g. publish_finalize deciding whether a hand-edited
    apple_summary title is usable enough to override the schedule title."""
    t = (episode_title or "").strip()
    return bool(t) and not _PLACEHOLDER_TITLE_RE.search(t)


def assert_real_title(episode_title: str, episode_code: str) -> None:
    """RULE: never advance/render/ship an episode with a missing or placeholder
    title. A reel card or blog H1 reading 'TBD' is unusable, and an episode that
    silently advances on 'TBD' (E29) blocks Phase 2 reels with no visible failure.
    Fail loudly here instead — before the counter bump and schedule write."""
    if not is_real_title(episode_title):
        raise GateFailure(
            f"{episode_code}: refusing to proceed — episode_title is "
            f"missing/placeholder ({episode_title!r}). A real title is required "
            f"before this phase may advance the episode."
        )


# The guest thank-you body must OPEN with exactly this line. It is the template's
# fixed first sentence (prompts/thank_you.md). Anything else — a greeting the model
# invented, or a wall of tool-call narration (E29) — fails here before it can become
# a Gmail draft a guest receives.
_THANK_YOU_FIRST_LINE = "Thank you so much for joining us on GenX Hits Menopause!"

# Transcript / model-reasoning markers that must NEVER appear in a guest-facing
# body. These are the fingerprints of the E29 regression: the composing agent's
# own narration ("Step 1…", "<tool_call>…") and a faked tool-response table
# ("| Field | Value |") leaked in as the draft body. A deterministic template fill
# can't produce any of these; the gate exists so a future non-deterministic path
# (or a bad hand-paste) can't either.
_THANK_YOU_MARKERS = ("<tool_call>", "tool_response", "Step ", "```")


def assert_thank_you_wellformed(body: str) -> None:
    """RULE: a guest thank-you draft is the standard template, nothing else.

    Raises GateFailure — BEFORE gmail_client.create_draft writes — when the body:
      • does not start with the template's fixed first line, or
      • contains a transcript/model marker (<tool_call>, tool_response, 'Step ',
        or a ``` code fence), or
      • contains a markdown TABLE row.

    On the table check: the E29 garbage contained faked tool-response table rows
    ('| Field | Value |'). We reject a LINE with two or more pipes — a table row —
    NOT any single pipe, because a legitimate episode title carries one ' | '
    separator ("Hook | Topic") and that title is rendered into the body. Rejecting
    any single pipe would reject every real draft whose title has a pipe (e.g. E29's
    own clean draft), so the grain is the table row, not the character. See ADR-0010.
    """
    text = body or ""
    if not text.lstrip().startswith(_THANK_YOU_FIRST_LINE):
        opening = text.lstrip()[:80]
        raise GateFailure(
            "thank-you body does not start with the template's fixed first line "
            f"({_THANK_YOU_FIRST_LINE!r}). Got: {opening!r}. Refusing to draft — "
            "this is not the standard template."
        )
    for marker in _THANK_YOU_MARKERS:
        if marker in text:
            raise GateFailure(
                f"thank-you body contains a transcript/model marker ({marker!r}); "
                "this is model narration, not the template. Refusing to draft."
            )
    for line in text.splitlines():
        if line.count("|") >= 2:
            raise GateFailure(
                f"thank-you body contains a markdown table row ({line.strip()!r}); "
                "this is faked tool-response cruft, not the template. Refusing to draft."
            )


def _is_host_speaker(speaker: str) -> bool:
    """True if a quote's speaker looks like the host, Heather Reilly Hiemstra.
    Diarization + Brand Team garble her surname ('Hemstra', 'Riley-Hemesdra',
    'Eilaheemstra'…) and sometimes label her bare 'Heather'."""
    return host_identity.is_host(speaker)   # REEL-09: the one host rule


def assert_guest_featured(quotes: list, guest_name: str, episode_code: str) -> None:
    """RULE: feature the guest at least as much as the host. Reels of an
    interview that are entirely the interviewer are unusable. We can't always
    disambiguate by name (some guests are themselves named Heather), so the hard
    floor is: a named, non-host guest MUST appear in the quote set. Stronger
    balance is enforced at selection time in prompts/brand_team_marcus.md."""
    g = (guest_name or "").strip()
    if g.lower() in ("", "guest", "tbd"):
        # TRIG-02 (2026-10-01): an episode with no form answer reaches Phase 2 with the placeholder "Guest"
        # (publish_finalize's fallback). Matching speakers against the word "guest" refused every such
        # episode's reels. With no name, check the same rule by the host instead: at least one quote must
        # be someone other than Heather.
        if quotes and all("heather" in (q.get("speaker") or "").lower() for q in quotes):
            raise GateFailure(
                f"{episode_code}: refusing to produce reels — all {len(quotes)} selected quotes are the "
                f"host's and the guest's name is unknown. Re-run quote selection so the guest is featured."
            )
        return
    if "heather" in g.lower():
        return  # the guest is also a Heather — name can't disambiguate
    tokens = [t for t in re.split(r"[^a-zA-Z]+", g.lower()) if len(t) > 2]
    featured = any(
        any(tok in (q.get("speaker") or "").lower() for tok in tokens) for q in quotes
    )
    if not featured:
        raise GateFailure(
            f"{episode_code}: refusing to produce reels — none of the {len(quotes)} "
            f"selected quotes feature the guest ({guest_name!r}); they are all the host. "
            f"Re-run quote selection (tools/rerun_brand_team.py) so the guest is featured."
        )



def assert_quote_headings(quotes: list, episode_code: str) -> None:
    """RULE (BLOG-02 F-1): every selected quote carries a `heading` — the h3 the blog
    post shows above it. The Brand Team writes it at selection (prompts/brand_team_marcus.md,
    carried through Lisa), and John reviews it with the rest of quotes.json.

    Enforced in PHASE 1, right after the Brand Team, so a headless quote set fails loudly
    there — before the title, the counter bump and the Drive upload — and not silently
    weeks later when Phase 2 tries to build the post. rerun_brand_team.py runs it too."""
    missing = [q.get("id") or f"#{i + 1}" for i, q in enumerate(quotes or [])
               if not str(q.get("heading") or "").strip()]
    if missing:
        raise GateFailure(
            f"{episode_code}: {len(missing)} of {len(quotes)} Brand Team quotes have no "
            f"`heading` ({', '.join(missing)}). The blog post needs one per quote; re-run "
            f"quote selection (tools/rerun_brand_team.py) or add them to quotes.json."
        )

# ── BLOG-02 — the Tailor blog post, before any API call ─────────────────────────

# Where each listen button may point. A HOST allow-list, not a word blocklist: the first
# version refused any URL containing "pending"/"tbd" as a substring, which rejects a real
# Apple URL whose slug reads "depending-on-your-hormones" and, ~1 in 1500, a Spotify ID
# with "tbd" in it (Jen, A-1). Audible is the one fixed show URL (thank_you.AUDIBLE_URL).
_LISTEN_HOSTS = {"Listen on Spotify": "open.spotify.com", "Listen on Apple": "podcasts.apple.com"}

# The placeholders the old DropInBlog path injected ("APPLE-URL-PENDING",
# "SPOTIFY-URL-PENDING"), matched as a whole token so no real slug can trip it.
_PLACEHOLDER_URL_RE = re.compile(r"(?<![A-Za-z0-9-])[A-Z]+(?:-[A-Z]+)*-URL-PENDING(?![A-Za-z0-9-])")

# TY-02's "Step " marker, narrowed for blog text (PO ruling A-3): model narration is a
# line that opens "Step 1…"; prose like "Step into midlife…" in a hook or bio is fine.
_BLOG_MARKERS = tuple(m for m in _THANK_YOU_MARKERS if m != "Step ")
_STEP_LINE_RE = re.compile(r"^\s*Step \d", re.MULTILINE)


def _listen_url_problem(label: str, href: str) -> str | None:
    """Why this cta href is not a real listen URL for its button, or None if it is."""
    from urllib.parse import urlparse

    from thank_you import AUDIBLE_URL

    if label == "Listen on Audible":
        return None if href == AUDIBLE_URL else f"is not the fixed Audible URL {AUDIBLE_URL}"
    host = _LISTEN_HOSTS.get(label)
    if host is None:
        return "is on an unknown listen button"
    u = urlparse(href or "")
    if u.scheme != "https" or u.hostname != host:
        return f"must be https://{host}/…"
    if not u.path.strip("/"):
        return "has no episode path"
    if _PLACEHOLDER_URL_RE.search(href):
        return "is a pipeline placeholder"
    return None


def _href_problem(href) -> str | None:
    """Why Tailor would refuse this href, or None if it accepts it — a MIRROR of Tailor's
    core/href.py `safe_href` (Orly C-11), plus a refusal of any control character.

    Why mirror it here: the post is written in two calls (POST the shell, PUT the blocks).
    A `[t](http://…)` link in the summary Doc would pass the POST and fail the PUT with a
    422, leaving an empty draft on the site — the half-written post AC-3 forbids. Checking
    the same rule before the first call makes that impossible.

    Accepted, and only these: `https://…`, a site-relative `/…` that does not resolve
    off-site (`//host`, `/\\host`, `/<tab>/host`), or an in-page `#…`. Empty = no link."""
    if href is None:
        return None
    if not isinstance(href, str):
        return "is not a string"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in href):
        return "contains a control character"
    v = href.strip()
    if v == "":
        return None
    if v.startswith("//"):
        return "is protocol-relative (//…)"
    if v.startswith("/") and v[1:2] == "\\":
        return "is a disguised protocol-relative link (/\\…)"
    if v.startswith("https://") or v.startswith("/") or v.startswith("#"):
        return None
    return "is not https://, a site-relative /…, or #…"


def _block_hrefs(node):
    """Every href in a block tree (inline links, cta buttons, any future block)."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key in ("href", "source_url"):
                yield val
            else:
                yield from _block_hrefs(val)
    elif isinstance(node, list):
        for item in node:
            yield from _block_hrefs(item)


def _block_texts(node):
    """Every human-readable string in a block tree: inline text, cta labels, quotes,
    attributions, titles. Walks dicts/lists generically so a new block type can't
    slip text past the marker check."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key in ("text", "label", "quote", "attribution", "title") and isinstance(val, str):
                yield val
            else:
                yield from _block_texts(val)
    elif isinstance(node, list):
        for item in node:
            yield from _block_texts(item)


def assert_blog_post_wellformed(post: dict, quotes: list, episode_code: str) -> None:
    """RULE: a blog post goes to Tailor only if it is the house shape, built from the
    real inputs. Raises GateFailure BEFORE any API call (BLOG-02 AC-3) when:
      • the block count is not 2n+6 for n quotes, or
      • a listen URL is not on its button's host (open.spotify.com, podcasts.apple.com,
        the fixed Audible URL) or is a pipeline placeholder, or
      • the title fails is_real_title, or
      • any link fails Tailor's public-href rule (_href_problem, Orly C-11), or
      • any text carries a transcript/model marker (TY-02's set, with 'Step ' narrowed
        to a line opening 'Step N') or a table row, or
      • a blockquote is not its quote's text verbatim (typographic quotes allowed —
        blog_post.quote_display is the one definition of that transform).

    The caller turns GateFailure into send_pipeline_alert — never a half-written post.
    """
    # Local import: blog_post imports GateFailure from here.
    import blog_post

    title = post.get("title") or ""
    if not is_real_title(title):
        raise GateFailure(f"{episode_code}: blog post title is missing/placeholder ({title!r})")

    blocks = post.get("blocks") or []
    n = len(quotes or [])
    if n < 1 or len(blocks) != 2 * n + 6:
        raise GateFailure(
            f"{episode_code}: blog post has {len(blocks)} blocks for {n} quotes; "
            f"the house shape is 2n+6 = {2 * n + 6}"
        )

    ctas = [c for c in blocks[0].get("content", []) if c.get("type") == "cta"]
    if len(ctas) != 3:
        raise GateFailure(f"{episode_code}: author intro has {len(ctas)} listen buttons, not 3")
    for cta in ctas:
        href = cta.get("href") or ""
        problem = _listen_url_problem(cta.get("label") or "", href)
        if problem:
            raise GateFailure(
                f"{episode_code}: listen URL for {cta.get('label')!r} {problem} ({href!r}). "
                f"Refusing to write the post."
            )

    for href in _block_hrefs(blocks):
        problem = _href_problem(href)
        if problem:
            raise GateFailure(
                f"{episode_code}: blog post link {str(href)[:80]!r} {problem} — Tailor would "
                f"refuse it on the content write and leave an empty draft (C-11)"
            )

    for text in _block_texts(blocks):
        for marker in _BLOG_MARKERS:
            if marker in text:
                raise GateFailure(
                    f"{episode_code}: blog post text contains a transcript/model marker "
                    f"({marker!r}): {text[:80]!r}"
                )
        if _STEP_LINE_RE.search(text):
            raise GateFailure(
                f"{episode_code}: blog post text opens a line with model narration "
                f"('Step N'): {text[:80]!r}"
            )
        for line in text.splitlines():
            if line.count("|") >= 2:
                raise GateFailure(
                    f"{episode_code}: blog post text contains a markdown table row "
                    f"({line.strip()[:80]!r})"
                )

    for i, q in enumerate(quotes):
        block = blocks[5 + 2 * i]
        heading = "".join(x.get("text", "") for x in blocks[4 + 2 * i]["content"][0]["inlines"])
        if not heading.strip():
            raise GateFailure(f"{episode_code}: highlight {i + 1} has no heading (empty h3)")
        if block.get("type") != "blockquote" or block.get("quote") != blog_post.quote_display(q):
            raise GateFailure(
                f"{episode_code}: highlight {i + 1} is not quote {q.get('id', i + 1)!r} "
                f"verbatim — refusing to publish a paraphrase under a guest's name"
            )
