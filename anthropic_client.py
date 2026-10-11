"""
anthropic_client.py — Brand Team chain + composition calls.

Model: claude-sonnet-4-6  (D-5 decision, 2026-05-21: Sonnet 4.6 for all
Brand Team calls — enough headroom for long transcripts, well within budget.)

Prompt caching strategy:
  The transcript block is marked cache_control: {type: "ephemeral"}.
  All four Brand Team calls share the same transcript prefix, so the cached
  KV is reused across calls 2–4.  Cache hit on a 1-hour transcript (~30K
  tokens) saves ~2–3 seconds and ~$0.10 per episode at Sonnet 4.6 pricing.

Brand Team chain — sequential refinement (not parallel):
  Step 1 — Marcus Rivera (SEO):  transcript → initial quote selection → quotes_v1
  Step 2 — Robert Kirk (voice):  transcript + quotes_v1 → voice review → quotes_v2
  Step 3 — Dr. Joe (clinical):   transcript + quotes_v2 → clinical review → quotes_v3
  Step 4 — Lisa Prince (QA):     transcript + quotes_v3 → final QA → quotes.json (clean)

Each member receives the prior step's output so they can build on it.
The transcript is cached across all 4 calls (cache hit on steps 2–4).
Lisa's output is the final artifact written to Drive — review fields stripped.

Functions:
  run_brand_team_chain()   4-step sequential chain → final list[dict] quotes
  compose_apple_summary()  1 call → str (apple_summary.md body)
  (compose_blog_page was REMOVED 2026-09-24 — the blog post is plain code, see
   blog_post.py + blog_publish.py, BLOG-02.)
  compose_seo_captions()   1 call → dict (Instagram/LinkedIn/YouTube captions, all reels)

  (compose_thank_you was RETIRED 2026-07-06 — the guest thank-you is a
   deterministic template fill, not a generation. See thank_you.py + ADR-0010.)

Prompts are loaded from PROMPTS_DIR (default /opt/rockstar-pipeline/prompts/).
D-5 decision: prompts in individual .md files so Heather can tune them
without touching chain logic.
"""

import json
import logging
import os
import re
from pathlib import Path

import anthropic

import run_ledger  # OBS-01: every model call is recorded through run_ledger.create

logger = logging.getLogger(__name__)

# ── config ─────────────────────────────────────────────────────────────────────

_MODEL = "claude-sonnet-4-6"

_PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR", "/opt/rockstar-pipeline/prompts"))

# Brand Team members in chain order.  Each maps to a {name}.md prompt file.
_BRAND_TEAM_MEMBERS = [
    "brand_team_marcus",
    "brand_team_robert",
    "brand_team_dr_joe",
    "brand_team_lisa",
]

# Back-off: if a prompt file is missing, we log a warning and skip that member
# rather than crashing the whole chain.  Phase 3 integration tests will catch
# any missing files before production use.

# ── client ─────────────────────────────────────────────────────────────────────

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


# ── prompt loading ─────────────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    """Load a prompt from PROMPTS_DIR.  Raises FileNotFoundError if missing."""
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Extract prompts from podcast_manager_agent_v1_3.md per D-5 decision."
        )
    return path.read_text(encoding="utf-8").strip()


# ── shared transcript block ────────────────────────────────────────────────────


def _transcript_user_block(transcript_text: str) -> dict:
    """
    Return a user message content block for the transcript with cache_control.
    This block is identical across all four Brand Team calls, so the API
    caches the KV after the first call and reuses it for calls 2–4.
    """
    return {
        "type": "text",
        "text": (
            "Here is the episode transcript:\n\n"
            "<transcript>\n"
            f"{transcript_text}\n"
            "</transcript>"
        ),
        "cache_control": {"type": "ephemeral"},
    }


# ── helpers ────────────────────────────────────────────────────────────────────


# Matches ```json ... ``` or ``` ... ``` (with or without language tag).
# Compiled at module load — shared across all Brand Team calls.
# re.IGNORECASE: handles ```JSON, ```Json, etc.
# Non-greedy [\s\S]*? : picks the first/innermost fence when model produces multiple.
# re.search (not re.match): handles preamble text before the fence, e.g.
#   "Here is the JSON:\n```json\n{...}\n```"
_JSON_FENCE_RE = re.compile(r"```[ \t]*(?:json)?[ \t]*\n([\s\S]*?)\n?[ \t]*```", re.IGNORECASE)
_OPEN_FENCE_RE = re.compile(r"^[^`]*```[ \t]*(?:json)?[ \t]*\n", re.IGNORECASE)


def _strip_json_fences(raw: str) -> str:
    """Strip markdown code fences if the model wrapped the JSON in them.

    Handles: preamble text, case variants (```JSON, ``` json),
    plain ``` with no language hint, and multiline JSON bodies.
    Falls through unchanged if no fence is found (plain JSON response).
    """
    raw = raw.strip()
    m = _JSON_FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    # R1-03 (E32, 2026-10-01): an OPENING fence with no closing one (a reply cut off, or a model that
    # forgot to close it) fell through unchanged, so json.loads saw "```json…" and the step was dropped.
    # Strip the opening fence line; if the JSON is complete it parses, and if it was truncated it still
    # fails, honestly, as non-JSON.
    m = _OPEN_FENCE_RE.match(raw)
    if m:
        return raw[m.end():].strip()
    return raw


def _metadata_suffix(episode_metadata: dict | None) -> str:
    if not episode_metadata:
        return ""
    meta_lines = "\n".join(f"  {k}: {v}" for k, v in episode_metadata.items())
    return f"\n\nEpisode metadata:\n{meta_lines}"


# ── Brand Team chain ───────────────────────────────────────────────────────────


_BRAND_TEAM_MAX_TOKENS = 8192  # R1-03: 4096 could cut a 5-quote review mid-JSON


def run_brand_team_chain(
    transcript_text: str,
    episode_metadata: dict | None = None,
    report: dict | None = None,
) -> list[dict]:
    """
    Sequential Brand Team refinement chain — 4 steps, each building on the last.

      Step 1 — Marcus (SEO):     transcript → selects 3–5 quotes → quotes_v1 JSON
      Step 2 — Robert (voice):   transcript + quotes_v1 → voice review → quotes_v2 JSON
      Step 3 — Dr. Joe (clin.):  transcript + quotes_v2 → clinical review → quotes_v3 JSON
      Step 4 — Lisa (final QA):  transcript + quotes_v3 → final clean quotes.json

    The transcript block is marked cache_control so it's cached across calls 2–4.
    Returns the final Lisa-approved list of quote dicts.

    R1-03 (E32, 2026-10-01): Dr. Joe's clinical review came back unparseable and the chain silently carried
    Robert's quotes forward. Lisa's final note then read "No Dr. Joe flags" about a review that never ran.
    Now a step that returns non-JSON is retried ONCE, and a step that is still unparseable is named in
    `report["skipped"]` (the caller records it in quotes.json and the Round-1 log), never dropped silently.
    """
    if report is None:
        report = {}
    report.setdefault("skipped", [])
    report.setdefault("retried", [])
    client = _get_client()
    transcript_block = _transcript_user_block(transcript_text)
    meta_suffix = _metadata_suffix(episode_metadata)
    current_quotes_json: str = ""  # accumulates between steps

    for step, member in enumerate(_BRAND_TEAM_MEMBERS):
        try:
            system_prompt = _load_prompt(f"{member}.md")
        except FileNotFoundError as exc:
            logger.warning("skipping Brand Team step %d (%s): %s", step + 1, member, exc)
            # OBS-01: a missing prompt used to skip a member with only a log line — the clinical review could
            # vanish unnoticed. The ledger names it.
            run_ledger.record("step", phase="round_1", step=f"10.{member.removeprefix('brand_team_')}",
                              name=f"brand_team.{member.removeprefix('brand_team_')}",
                              outcome="skipped", detail="prompt file missing", duration_ms=0)
            report["skipped"].append(member)
            continue

        logger.info("Brand Team step %d: %s", step + 1, member)

        # User message:
        #   1. Cached transcript block (same bytes every call → cache hit on steps 2–4)
        #   2. Prior step's output (different each call, not cached — tells the member
        #      what the previous reviewer produced)
        #   3. Short per-step instruction
        user_parts: list[dict] = [transcript_block]

        if step == 0:
            # Marcus: fresh selection, no prior quotes
            user_parts.append({
                "type": "text",
                "text": (
                    "Select the 3–5 quotes from this transcript per your instructions. "
                    f"Return ONLY valid JSON.{meta_suffix}"
                ),
            })
        else:
            # Robert, Dr. Joe, Lisa: receive prior step's quotes for review
            step_instructions = {
                1: "Review the quotes below for voice fidelity per your instructions. Return the full updated quotes array as ONLY valid JSON.",
                2: "Review the quotes below for clinical accuracy per your instructions. Return the full updated quotes array as ONLY valid JSON.",
                3: "Apply your final QA checklist to the quotes below. Return the final clean quotes.json as ONLY valid JSON.",
            }
            user_parts.append({
                "type": "text",
                "text": (
                    f"Prior step output (quotes so far):\n\n"
                    f"```json\n{current_quotes_json}\n```\n\n"
                    f"{step_instructions.get(step, 'Review and return updated quotes as ONLY valid JSON.')}"
                    f"{meta_suffix}"
                ),
            })

        parsed = None
        for try_no in (1, 2):
            raw = ""
            try:
                response = run_ledger.create(
                    client, step=f"brand_team.{member.removeprefix('brand_team_')}", prompt=f"{member}.md",
                    attempt=try_no,
                    model=_MODEL,
                    max_tokens=_BRAND_TEAM_MAX_TOKENS,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_parts}],
                )
                raw = response.content[0].text
                if getattr(response, "stop_reason", None) == "max_tokens":
                    logger.warning("Brand Team step %d (%s) hit max_tokens (try %d)", step + 1, member, try_no)
                parsed = json.loads(_strip_json_fences(raw))
                if not isinstance(parsed, dict) or not isinstance(parsed.get("quotes"), list):
                    raise json.JSONDecodeError("no 'quotes' array", raw[:80], 0)
                break
            except json.JSONDecodeError:
                logger.error(
                    "Brand Team step %d (%s) returned non-JSON (try %d): %.200s",
                    step + 1, member, try_no, raw,
                )
                parsed = None
                if try_no == 1:
                    report["retried"].append(member)
            except Exception as exc:
                logger.error("Brand Team step %d (%s) failed: %s", step + 1, member, exc)
                raise  # Let the worker's retry logic handle transient API errors

        if parsed is None:
            # Still unparseable after the retry: carry the prior output forward, and SAY SO.
            report["skipped"].append(member)
            logger.error("Brand Team step %d (%s) SKIPPED: no valid JSON after retry", step + 1, member)
            continue
        current_quotes_json = json.dumps(parsed, ensure_ascii=False)
        logger.info("  step %d (%s): %d quotes", step + 1, member, len(parsed.get("quotes", [])))

    if not current_quotes_json:
        logger.error("Brand Team chain produced no output — all steps failed or skipped")
        return []

    try:
        final = json.loads(current_quotes_json)
        quotes = final.get("quotes", [])
    except Exception:
        logger.error("Brand Team final output unparseable: %.200s", current_quotes_json)
        return []

    logger.info("Brand Team chain complete: %d approved quotes", len(quotes))
    return quotes


# ── episode title ──────────────────────────────────────────────────────────────


def compose_title(
    transcript_text: str,
    episode_metadata: dict | None = None,
    quotes: list[dict] | None = None,
) -> dict:
    """
    Generate the episode title as STRUCTURED data — the dedicated Phase-1 title
    step (TITLE-01). Returns:

        {"hook": str, "topic": str, "title": str}

    where `title` is the house format "{hook} | {topic}", guest-featured per the
    brand rule, true to voice (BP-4).

    Why structured, not a prose scrape: round_1 used to take line 0 of the Apple
    summary's H1 and split on " · ". E29 (2026-06-23) put a "*Pre-write note:*"
    line ABOVE the H1, so line 0 was the note and the title silently fell back to
    "TBD". A JSON field has no "line 0" to capture the wrong thing, and the
    fence-strip + brace-slice extraction below sails past any leading commentary
    the model emits. Preamble cannot corrupt the result.

    `title` is reassembled here from hook/topic, so even if the model returns the
    two parts but forgets the joined form, the canonical "Hook | Topic" still
    comes back. Raises on API error and on an unparseable / empty response —
    Phase 1 must FAIL on a bad title, never default to a placeholder (DOD-5).
    """
    client = _get_client()
    system_prompt = _load_prompt("title.md")

    user_message = (
        "Here is the episode transcript:\n\n"
        f"<transcript>\n{transcript_text}\n</transcript>"
        + _metadata_suffix(episode_metadata)
    )
    if quotes:
        # The selected Brand-Team quotes surface the episode's sharpest moments —
        # useful raw material for the hook. Optional: title generation works
        # without them (reprocess paths may not pass quotes).
        quote_lines = "\n".join(
            f"  - {q.get('speaker', 'Unknown')}: {q.get('quote_text', q.get('text', ''))}"
            for q in quotes
        )
        user_message += f"\n\nSelected quotes:\n{quote_lines}"
    user_message += '\n\nReturn ONLY valid JSON: {"hook": "...", "topic": "..."}.'

    raw = ""
    try:
        response = run_ledger.create(
            client, step="title", prompt="title.md",
            model=_MODEL,
            max_tokens=1024,  # a title is tiny; this is generous headroom
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text
        parsed = _parse_title_json(raw)
    except json.JSONDecodeError as exc:
        # No silent default. A title we cannot parse is a failed title, and the
        # gate downstream would reject "TBD" anyway — fail here, loudly, with the
        # raw response in the log so we can see what the model actually said.
        logger.error(
            "compose_title: unparseable response (len=%d stop=%s): %.400s",
            len(raw),
            response.stop_reason if "response" in dir() else "unknown",
            raw,
        )
        raise RuntimeError("compose_title: model did not return parseable title JSON") from exc
    except Exception as exc:
        logger.error("compose_title: API error: %s", exc)
        raise

    hook = (parsed.get("hook") or "").strip()
    topic = (parsed.get("topic") or "").strip()
    if not hook or not topic:
        # Structurally valid JSON but missing the fields we require. Still a
        # failed title — do not assemble a half-empty "Hook | " and pass it on.
        raise RuntimeError(
            f"compose_title: response missing hook/topic (hook={hook!r} topic={topic!r})"
        )
    title = f"{hook} | {topic}"
    logger.info("compose_title: %r", title)
    return {"hook": hook, "topic": topic, "title": title}


def _parse_title_json(raw: str) -> dict:
    """Extract the title JSON object from a model response, preamble and all.

    Two passes, same robustness ladder compose_seo_captions uses: strip code
    fences first (re.search skips any leading note), then if that still doesn't
    parse, slice from the first '{' to the last '}'. Either pass tolerates the
    E29 '*Pre-write note:*' style commentary before the JSON. Raises
    json.JSONDecodeError if neither pass yields valid JSON."""
    cleaned = _strip_json_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
        raise


# ── Apple Podcasts summary ─────────────────────────────────────────────────────


def compose_apple_summary(
    transcript_text: str,
    episode_metadata: dict | None = None,
    quotes: list[dict] | None = None,
) -> str:
    """
    Compose the Apple Podcasts episode summary / description.
    Returns the plain-text / Markdown body to be written as apple_summary.md.
    """
    client = _get_client()
    system_prompt = _load_prompt("apple_summary.md")

    metadata_suffix = ""
    if episode_metadata:
        meta_lines = "\n".join(f"  {k}: {v}" for k, v in episode_metadata.items())
        metadata_suffix = f"\n\nEpisode metadata:\n{meta_lines}"

    quotes_suffix = ""
    if quotes:
        # R1-03 (E32): the Brand Team writes `text` and `heading` (E27+). This read the E23-era
        # `quote_text_clean`/`topic`, sent five empty lines, and the model wrote "No Brand Team quotes were
        # supplied". Same fallback order as blog_post.quote_text. An empty quote is refused, never sent.
        lines = []
        for i, q in enumerate(quotes, 1):
            topic = (q.get("heading") or q.get("topic") or "").strip()
            text = (q.get("text") or q.get("quote_text_clean") or "").strip()
            if not text:
                raise ValueError(f"compose_apple_summary: quote {i} has no text (keys: {sorted(q)})")
            speaker = (q.get("speaker") or "").strip()
            lines.append(f"  {i}. [{topic}] {speaker + ': ' if speaker else ''}\"{text}\"")
        quotes_suffix = "\n\nBrand Team selected quotes (use to anchor SEO check):\n" + "\n".join(lines)

    response = run_ledger.create(
        client, step="apple_summary", prompt="apple_summary.md",
        model=_MODEL,
        max_tokens=1500,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    _transcript_user_block(transcript_text),
                    {
                        "type": "text",
                        "text": (
                            "Write the Apple Podcasts episode summary for this episode."
                            + metadata_suffix
                            + quotes_suffix
                        ),
                    },
                ],
            }
        ],
    )

    result = response.content[0].text.strip()
    logger.info("compose_apple_summary: %d chars", len(result))
    return result


# ── Blog page ──────────────────────────────────────────────────────────────────


def compose_faq(
    transcript_text: str,
    apple_summary: str,
    episode_metadata: dict | None = None,
) -> str:
    """
    Generate 3–5 FAQ Q&A pairs from the episode transcript.
    Returns plain text in faq.md format (Q:/A: pairs).
    """
    client = _get_client()
    system_prompt = _load_prompt("faq.md")

    context = f"Apple Summary:\n{apple_summary}"
    if episode_metadata:
        meta_lines = "\n".join(f"  {k}: {v}" for k, v in episode_metadata.items())
        context = f"Episode metadata:\n{meta_lines}\n\n" + context

    response = run_ledger.create(
        client, step="faq", prompt="faq.md",
        model=_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    _transcript_user_block(transcript_text),
                    {"type": "text", "text": context},
                ],
            }
        ],
    )

    result = response.content[0].text.strip()
    logger.info("compose_faq: %d chars", len(result))
    return result


# compose_blog_page() was removed 2026-09-24 (BLOG-02b). It had the model write DropInBlog
# HTML for a person to paste into BigCommerce. rockstarblends.com is Tailor now, and the post
# is built in plain code (blog_post.build_post) and written as a draft through the API
# (blog_publish). Same reasoning as compose_thank_you below; git keeps the old function.


# compose_thank_you() was removed 2026-07-06. The guest thank-you is a STANDARD
# TEMPLATE with deterministic fills, not an AI-composed letter — every slot is a
# known value at the call site, so a generation call only adds drift risk (on E29
# it drifted into emitting tool-call narration as the draft body). The template
# fill now lives in thank_you.py (thank_you.build_draft), gated by
# content_gates.assert_thank_you_wellformed and exposed as create_thank_you_draft.py.


# ── SEO captions ───────────────────────────────────────────────────────────────


def compose_card_captions(
    guest_name: str,
    about_guest: str,
    episode_title: str,
    quotes: list[dict],
) -> dict:
    """
    Generate the model-authored parts of the five quote-card Instagram captions.

    One API call per episode.  Returns:
      {"guest_intro": str, "posts": {"q01": {"opening": str, "hashtags": [...]}, ...}}

    The model writes ONLY the guest intro, the per-post opening line, and the
    hashtags.  The connect line, the listen line and the layout are assembled
    deterministically by quote_cards/make_captions.py — a model asked to write a
    fixed line will eventually reword it, and this one carries the guest's name.

    `guest_intro` comes back as the literal string "BIO_PENDING" when about_guest
    is a placeholder.  That is deliberate: an absent bio must surface as a gap the
    caller marks for manual fill, never as a bio invented for a real person.

    Raises on API error; returns {} on unparseable JSON so the caller can fail
    loudly with the episode named rather than write half a caption set.
    """
    client = _get_client()
    system_prompt = _load_prompt("quote_card_captions.md")

    parts = [
        f"guest_name: {guest_name}",
        f"episode_title: {episode_title}",
        "",
        "about_guest:",
        about_guest.strip(),
        "",
        "quotes:",
    ]
    for q in quotes:
        parts.append(
            f"  - id: {q.get('id')}\n"
            f"    speaker: {q.get('speaker', 'Unknown')}\n"
            f"    text: {q.get('text', '')}"
        )
    user_message = "\n".join(parts) + "\n\nReturn ONLY valid JSON."

    raw = ""
    try:
        response = run_ledger.create(
            client, step="card_captions", prompt="quote_card_captions.md",
            model=_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text
        result = json.loads(_strip_json_fences(raw))
        logger.info("compose_card_captions: %d post entries",
                    len(result.get("posts", {})))
        return result
    except json.JSONDecodeError:
        # Same brace-slice fallback as compose_seo_captions — the JSON body is
        # usually intact even when the fence is not.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.error("compose_card_captions: non-JSON response: %.400s", raw)
        return {}
    except Exception as exc:
        logger.error("compose_card_captions: API error: %s", exc)
        raise


def compose_cover_titles(episode_title: str, quotes: list[dict], feedback: str = "") -> dict:
    """
    CARD-04: draft the title on each quote carousel's cover slide.

    One call per episode.  Returns {"q01": "...", ...}, or {} on unparseable JSON so
    the caller fails with the episode named.  quote_cards/cover_titles.py gates every
    title; `feedback` carries the gate failures of a first attempt into one retry.
    """
    client = _get_client()
    system_prompt = _load_prompt("cover_titles.md")
    parts = [f"episode_title: {episode_title}", "", "quotes:"]
    for q in quotes:
        parts.append(f"  - id: {q.get('id')}\n    speaker: {q.get('speaker', 'Unknown')}\n"
                     f"    text: {q.get('text', '')}")
    if feedback:
        parts += ["", "A first draft was rejected by the checks. Fix these and return all five:",
                  feedback]
    user_message = "\n".join(parts) + "\n\nReturn ONLY valid JSON."
    raw = ""
    try:
        response = run_ledger.create(
            client, step="cover_titles", prompt="cover_titles.md", attempt=2 if feedback else 1,
            model=_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text
        result = json.loads(_strip_json_fences(raw))
        return result.get("titles", {}) if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        logger.error("compose_cover_titles: non-JSON response: %.400s", raw)
        return {}
    except Exception as exc:
        logger.error("compose_cover_titles: API error: %s", exc)
        raise


def compose_seo_captions(
    quotes: list[dict],
    episode_title: str,
    apple_url: str,
    spotify_url: str,
    blog_post_url: str,
    episode_metadata: dict | None = None,
) -> dict:
    """
    Generate Instagram, LinkedIn, and YouTube captions for up to 5 quote reels.

    One API call produces captions for all reels.  Returns a dict keyed by
    reel index (string "1"–"5"), each value having keys "instagram", "linkedin",
    "youtube".

    Example return value:
      {
        "1": {"instagram": "...", "linkedin": "...", "youtube": "..."},
        "2": {...},
        ...
      }

    Falls back to an empty dict on JSON parse failure — caller logs and continues.
    """
    client = _get_client()
    system_prompt = _load_prompt("seo_captions.md")

    context_parts = [
        f"episode_title: {episode_title}",
        f"apple_url: {apple_url}",
        f"spotify_url: {spotify_url}",
        f"blog_post_url: {blog_post_url}",
    ]
    if episode_metadata:
        for k, v in episode_metadata.items():
            context_parts.append(f"{k}: {v}")

    context_parts.append("\nquotes:")
    for i, q in enumerate(quotes, 1):
        context_parts.append(
            f"  {i}. speaker: {q.get('speaker', 'Unknown')}\n"
            f"     quote_text: {q.get('quote_text', q.get('text', ''))}\n"
            f"     context: {q.get('context', '')}"
        )

    user_message = "\n".join(context_parts) + "\n\nGenerate captions for all reels. Return ONLY valid JSON."

    raw = ""
    try:
        response = run_ledger.create(
            client, step="seo_captions", prompt="seo_captions.md",
            model=_MODEL,
            max_tokens=16000,  # 5 reels × 3 platforms × 200+ words each ≈ 10-14k tokens
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text
        cleaned = _strip_json_fences(raw)
        result = json.loads(cleaned)
        logger.info("compose_seo_captions: %d reel entries", len(result))
        return result
    except json.JSONDecodeError:
        # Fence-strip failed (e.g. the model opened ```json but never closed it,
        # or caption text contains backticks that truncated the regex match).
        # The JSON object itself is usually intact — slice from the first '{'
        # to the last '}' and try once more before giving up. Cost of failure
        # here is real: E27 shipped with zero caption files (2026-06-05).
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                result = json.loads(raw[start:end + 1])
                logger.info(
                    "compose_seo_captions: %d reel entries (brace-slice fallback)",
                    len(result),
                )
                return result
            except json.JSONDecodeError:
                pass
        logger.error(
            "compose_seo_captions: non-JSON response (len=%d stop=%s): %.400s",
            len(raw),
            response.stop_reason if "response" in dir() else "unknown",
            raw,
        )
        return {}
    except Exception as exc:
        logger.error("compose_seo_captions: API error: %s", exc)
        raise
