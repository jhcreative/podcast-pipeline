"""
Write the Instagram caption that accompanies each quote-card post.

One .txt per post, landing beside that post's PNGs in the episode's cards/ folder, so
posting is: open the file, select all, paste.

Division of labour, deliberate (ADR-0013):
  - The MODEL writes only the guest introduction, each post's opening line, and its
    hashtags -- the three genuinely editorial pieces.
  - CODE assembles everything else. The connect line and the listen line are fixed
    copy carrying a real person's name and the show's CTA; a model asked to reproduce
    fixed copy eventually rewords it (ADR-0010's lesson, ADR-0007's music credit).

Source for the guest introduction is the `About <Guest>` section of apple_summary.md.
That section is legitimately a placeholder for some episodes (E24's guest never filed
an intake form), so a missing bio surfaces as a marked gap -- never as a bio invented
for a real person.

Usage:
    python make_captions.py --episode 29 --dry-run   # no API key needed
    python make_captions.py --episode 29

Requires ANTHROPIC_API_KEY (and the repo's prompts/) unless --dry-run.
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import quote_source

SOCIAL_PLACEHOLDER = "[ADD @HANDLE]"
BIO_GAP = "[BIO PENDING — add before posting]"
SHOW_TAGS = ("#GenXHitsMenopause", "#RockstarBlends")
# Anything that would make a silent card claim a music licence it does not use.
_MUSIC_MARKERS = ("uppbeat", "🎵", "show me")


def extract_about(apple_summary_text, guest_hint=None):
    """Pull the `About <Guest>` block and the guest's name out of apple_summary.md.

    Tolerant of heading level and of the hand-edited summaries John writes directly
    in Drive -- ADR-0008 made those authoritative, so this must read what a human
    typed, not only what the generator emits.

    Hand-edited summaries frequently lose the heading altogether: E29's bio sits
    after a row of underscores (how a Google Docs horizontal rule exports) with no
    "About" line at all. When `guest_hint` is supplied, fall back to the LAST
    paragraph that BEGINS with the guest's name and take it to the end. Requiring
    the paragraph to open with the name keeps this from grabbing the hook intro,
    which mentions the guest but opens on Heather.
    """
    m = re.search(r"^#{0,4}\s*About\s+(.+?)\s*$", apple_summary_text,
                  re.MULTILINE | re.IGNORECASE)
    if not m:
        return _about_by_name(apple_summary_text, guest_hint)
    guest = m.group(1).strip().strip("#").strip()
    body = apple_summary_text[m.end():]
    # A Docs export can join the heading to the bio's first paragraph on one line
    # (E28: "About Avery Example Avery Example is the founding CEO ..."). A name is
    # short: split it off where the bio repeats it, else take the hinted name.
    words = guest.split()
    if len(words) > 5:
        k = next((k for k in (4, 3, 2) if words[k:2 * k] == words[:k]), None)
        if k:
            name = " ".join(words[:k])
        elif guest_hint and guest.startswith(guest_hint):
            name = guest_hint
        else:
            name = " ".join(words[:2])
        body = guest[len(name):].strip() + "\n\n" + body
        guest = name
    # Stop at the next heading or horizontal rule -- whichever comes first.
    stop = re.search(r"^(#{1,4}\s|\-{3,}\s*$)", body, re.MULTILINE)
    about = (body[:stop.start()] if stop else body).strip()
    return guest, about


def _about_by_name(text, guest_hint):
    """Heading-less fallback: the bio is the tail that starts with the guest's name."""
    if not guest_hint:
        return None, None
    paras = re.split(r"\n\s*\n", text)
    hit = None
    for i, para in enumerate(paras):
        if para.strip().startswith(guest_hint):
            hit = i                                   # keep the LAST match
    if hit is None:
        return None, None
    tail = []
    for para in paras[hit:]:
        if re.match(r"^#{1,4}\s", para.strip()):      # a real heading ends the bio
            break
        # Drop divider rows -- Docs exports a horizontal rule as underscores.
        if re.fullmatch(r"[_\-\s]{3,}", para.strip()):
            continue
        tail.append(para.strip())
    about = "\n\n".join(t for t in tail if t)
    return (guest_hint, about) if about else (None, None)


def bio_is_pending(about):
    """A bio that is a bracketed note, an empty block, or explicitly pending."""
    if not about or len(about.strip()) < 40:
        return True
    low = about.lower()
    return ("bio pending" in low or "pending —" in low
            or bool(re.fullmatch(r"[`\[].*[`\]]", about.strip(), re.DOTALL)))


# Words a paraphrase may legitimately introduce without saying anything new.
_INTRO_STOPWORDS = {
    "about", "after", "again", "against", "along", "among", "around", "because",
    "become", "becomes", "before", "began", "begins", "being", "below", "between",
    "both", "bring", "brings", "built", "could", "district", "during", "every",
    "first", "focus", "focused", "found", "founder", "founded", "given", "gives",
    "having", "helps", "here", "important", "including", "into", "known", "later",
    "leads", "learn", "makes", "means", "more", "most", "much", "never", "often",
    "other", "over", "own", "same", "since", "some", "spent", "still", "such",
    "than", "that", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "under", "until", "using", "very", "what", "when", "where",
    "which", "while", "whose", "with", "within", "without", "work", "works", "years",
}


def novel_terms(intro, about):
    """Content words in the intro that do not appear in the source bio.

    This is the one failure the structural gates cannot see. On E31's first real
    run the model wrote "including estrogen, the menstrual cycle, and
    perimenopause" — all true of the *episode*, none of it in the guest's bio,
    which says "female-specific biology" and names endometriosis. Nothing was
    inflated, but the rule is sharpen-don't-extend, and that extended.

    Advisory, not a gate: paraphrase legitimately introduces words, so a hard
    failure here would block good copy. It prints what to look at instead of
    pretending to judge. Matching is by five-character prefix so "pivoting" is
    recognised in "pivot" rather than reported as novel.

    Known false positive: irregular verb forms. The bio's "she led two projects"
    does not cover the intro's "leading", because led/leading share no prefix.
    Expect an occasional word that turns out fine on inspection — that is the
    correct trade for never missing a borrowed claim.
    """
    src = {w[:5] for w in re.findall(r"[a-z]{4,}", (about or "").lower())}
    seen, out = set(), []
    for w in re.findall(r"[a-z]{5,}", intro.lower()):
        if w in _INTRO_STOPWORDS or w[:5] in src or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def assemble(opening, guest_intro, guest_name, ep_num, hashtags):
    """The caption's fixed shape. Code owns every line the model does not write."""
    return "\n".join([
        opening.strip(),
        "",
        guest_intro.strip(),
        "",
        f"Connect with {guest_name} at {SOCIAL_PLACEHOLDER}",
        f"Listen to E{ep_num} on Apple Podcasts & Spotify — link in bio.",
        "",
        " ".join(hashtags),
    ]) + "\n"


def assert_caption_wellformed(text, hashtags, quote_text, name):
    """Gates. Each one exists because the failure it prevents is invisible once posted.

    The hashtag assertion is the one CAP-01 asked for on the reel captions and never
    got -- a soft, prompt-side rule with nothing checking it at generation time.
    """
    if not 3 <= len(hashtags) <= 4:
        raise SystemExit(f"FAIL {name}: {len(hashtags)} hashtags, house policy is 3–4 "
                         f"(ADR-0007) — {' '.join(hashtags)}")
    if not any(t in hashtags for t in SHOW_TAGS):
        raise SystemExit(f"FAIL {name}: no show tag; one of {SHOW_TAGS} is required")
    bad = [t for t in hashtags if not t.startswith("#") or " " in t]
    if bad:
        raise SystemExit(f"FAIL {name}: malformed hashtag(s) {bad}")
    if re.search(r"https?://", text):
        raise SystemExit(f"FAIL {name}: caption carries a URL. Captions here never do "
                         f"— the listen line says 'link in bio' (ADR-0007).")
    low = text.lower()
    if any(mark in low for mark in _MUSIC_MARKERS):
        raise SystemExit(f"FAIL {name}: caption carries a music/audio credit. A quote "
                         f"card is silent — crediting the reel's licensed track here "
                         f"would be a false attribution.")
    if SOCIAL_PLACEHOLDER not in text and "@" not in text:
        raise SystemExit(f"FAIL {name}: no connect handle and no {SOCIAL_PLACEHOLDER} "
                         f"placeholder — the connect line would ship blank")
    # The quote is already on the image; repeating it burns the one visible line.
    opening = text.split("\n", 1)[0].strip().strip("“”\"")
    if len(opening) > 20 and opening.lower() in quote_text.lower():
        raise SystemExit(f"FAIL {name}: opening line restates the quote verbatim")


def stub_model_output(quotes):
    """Deterministic stand-in so assembly and gates can be exercised with no API key.
    Clearly marked -- nothing here is postable copy."""
    return {
        "guest_intro": "[DRY RUN — model-written guest introduction goes here, drawn "
                       "only from the About section.]",
        "posts": {q["id"]: {
            "opening": f"[DRY RUN opening for {q['id']}]",
            "hashtags": ["#GenXHitsMenopause", "#MenopauseHealth", "#GenXWomen"],
        } for q in quotes},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--dir", help="episode folder (default: Episodes/E<N>-*); Phase 2 and "
                                  "new-cards.sh point this at a staged copy")
    ap.add_argument("--title", default="",
                    help="published episode title, for the model's context")
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble and gate with a stub instead of calling the model")
    a = ap.parse_args()

    ep_dir = a.dir or quote_source.find_episode_dir(REPO, a.episode)
    quotes = quote_source.load_quotes(ep_dir)

    # The quote speakers are the hint the heading-less fallback needs, so derive
    # them before parsing rather than only as a last resort.
    speakers = {q["speaker"] for q in quotes}
    hint = sorted(speakers)[0] if len(speakers) == 1 else None

    summary_path = os.path.join(ep_dir, "apple_summary.md")
    guest_name, about = None, None
    if os.path.exists(summary_path):
        guest_name, about = extract_about(open(summary_path).read(), hint)
    else:
        print(f"  ! no apple_summary.md in {os.path.basename(ep_dir)}")

    if not guest_name:
        # Fall back to the quote speakers -- the cards already attribute them, and a
        # caption naming nobody is worse than one naming the on-air handle.
        guest_name = hint or "[ADD GUEST NAME]"
        print(f"  ! no 'About <Guest>' section found — guest name falls back to "
              f"{guest_name!r}")

    pending = bio_is_pending(about)
    if pending:
        print(f"  ! About section is missing or pending — captions will carry "
              f"{BIO_GAP}. Nothing is invented.")

    if a.dry_run:
        model = stub_model_output(quotes)
    else:
        import anthropic_client
        model = anthropic_client.compose_card_captions(
            guest_name, about or "", a.title, quotes)
        if not model.get("posts"):
            sys.exit(f"FAIL E{a.episode}: model returned no usable caption JSON — "
                     f"nothing written")

    intro = model.get("guest_intro", "").strip()
    if pending or intro == "BIO_PENDING" or not intro:
        intro = BIO_GAP
    else:
        # The structural gates cannot see a claim the bio never made. Name the
        # words the intro introduces so a human decides, rather than assuming
        # "sharpen, don't extend" held.
        novel = novel_terms(intro, about)
        if novel:
            print(f"  ! REVIEW the guest intro — these words are NOT in the About "
                  f"section: {', '.join(novel)}")

    out_dir = os.path.join(ep_dir, "cards")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for q in quotes:
        post = model["posts"].get(q["id"])
        if not post:
            sys.exit(f"FAIL E{a.episode}: model returned nothing for {q['id']}")
        tags = post["hashtags"]
        text = assemble(post["opening"], intro, guest_name, a.episode, tags)
        assert_caption_wellformed(text, tags, q["text"], f"E{a.episode}_{q['id']}")
        path = os.path.join(out_dir, f"E{a.episode}_{q['id']}_caption.txt")
        open(path, "w").write(text)
        written.append(path)
        print(f"  {q['id']}  {len(text.split())} words, {len(tags)} tags")

    print(f"\n{len(written)} caption(s) -> {out_dir}")
    if pending:
        print(f"ACTION: fill {BIO_GAP} in every caption before posting.")
    print(f"ACTION: replace {SOCIAL_PLACEHOLDER} with the guest's handle.")


if __name__ == "__main__":
    main()
