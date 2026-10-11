"""
test_blog02_build_post.py — BLOG-02a deterministic blog post build + gates.

Run: python3 test_blog02_build_post.py   (plain asserts, no pytest needed)

Covers BLOG-02 acceptance criteria (slice 02a — no network):
  AC-1 deterministic, no model      → test_deterministic_bytes
  AC-2 golden parity, E31 + E30     → test_golden_e31, test_golden_e30 (all 16 blocks, FAQ included)
  AC-3 gate fails before any write  → test_gate_rejects
  Resolved OPEN-2/3/4 rules         → test_meta_description_rule, test_share_block_is_the_constant,
                                      test_descriptor_and_topics, helpers
  F-1 ruling (quote `heading`)      → test_heading_gate_unit, test_round1_headless_quotes_fail_phase1
  F-2 ruling (markdown download)    → test_export_mime_for, test_title_from_markdown_summary,
                                      test_unescape_markdown, test_plain_text_export_refused

Fixtures are COMMITTED under tests/fixtures/blog02/ — never read from the gitignored
Episodes/ (the card-suite rule). They are SYNTHETIC (SHARE-02 / FIX-01 scope 5): invented
guests and quotes, shaped like the real files they replaced, so every assertion below still
pins the same mechanics:
  E3x/apple_summary.md  the markdown shape of the Drive export (exportMimeType text/markdown)
  E3x/faq.md            the faq.md Q:/A: shape
  E3x/quotes.json       Brand Team quotes + a `heading` per quote (see its _fixture_note)
  E3x/live_8627.json    the GET /api/pages/blog/<slug> shape: the build with the deltas
                        below applied in reverse (the hand edits the real live posts carried)

The golden title comes through the REAL publish_finalize._title_from_apple_summary (heavy
I/O modules stubbed, as test_blog01 does), so the ADR-0009 path is exercised on the
markdown export's "## …" title line.
"""

import copy
import importlib
import json
import sys
import types
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE))

import blog_post  # noqa: E402
import content_gates  # noqa: E402
FIXTURES = WORKTREE / "tests" / "fixtures" / "blog02"


def _pf():
    """jobs.publish_finalize with its I/O-boundary modules stubbed (test_blog01's pattern).
    Only the pure _title_from_apple_summary is called."""
    if "anthropic" not in sys.modules:
        anthropic = types.ModuleType("anthropic")
        anthropic.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
        sys.modules["anthropic"] = anthropic
    for name in ("calendar_client", "drive_client", "gmail_client", "publisher_email", "db"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules.pop("jobs.publish_finalize", None)
    import jobs.publish_finalize as pf
    importlib.reload(pf)
    return pf


PF = _pf()


def _load(ep: str):
    d = FIXTURES / ep
    return (
        (d / "apple_summary.md").read_text(encoding="utf-8"),
        json.loads((d / "quotes.json").read_text(encoding="utf-8"))["quotes"],
        (d / "faq.md").read_text(encoding="utf-8"),
        json.loads((d / "live_8627.json").read_text(encoding="utf-8")),
    )


def _golden_inputs(ep: str):
    apple, quotes, faq, live = _load(ep)
    cta = live["blocks"][0]["content"]
    meta = {
        "episode_title": PF._title_from_apple_summary(apple),  # ADR-0009, on "## …"
        "episode_number": int(ep[1:]),
        "spotify_url": cta[1]["href"],
        "apple_url": cta[2]["href"],
    }
    return apple, quotes, faq, meta, live


# ── AC-2 — golden parity ───────────────────────────────────────────────────────

# Every place the build is ALLOWED to differ from :8627, and why: (op, path, want, reason).
# A path is a tuple into the post dict.
#   "replace": the build's value is PINNED — ours == want, and live != want (a stale
#              delta fails). Then live's value is replaced by want. A mutation that
#              changes the build at a delta path goes red here, not green (Jen, G-1).
#   "extra":   the build has a list element live does not; it is dropped before comparing.
# After that the whole post — all 16 blocks and the page fields — must be EQUAL.
# Anything not listed here is a failure.
_B0_TITLE = ("blocks", 0, "content", 0, "inlines", 1, "text")
_B0_CLAUSE = ("blocks", 0, "content", 0, "inlines", 6, "text")
_BULLET = lambda i, j: ("blocks", 2, "content", 4, "items", i, "inlines", j, "text")  # noqa: E731
_SHARE = ("blocks", 14, "content", 0, "inlines", 0, "text")
_FAQ_ANSWER = lambda i: ("blocks", 15, "questions", i, "answer", 0, "inlines", 0, "text")  # noqa: E731

ACCEPTED_DELTAS = {
    "E31": [
        ("replace", ('hero_media_id',), None,
         "Cover Image ruling (John, 2026-09-23): the tile is the Cover Image (thumbnail_media_id, "
         "set by blog_publish after upload), the hero stays empty; the migrated posts carry the "
         "tile as hero"),
        ("replace", ('title',),
         "Bliss Is the System | The ECS, Estrogen & the Perimenopause Connection | GenX Hits Menopause E31",
         "live title hand-edited: '& the Perimenopause' → '& Perimenopause'; the apple_summary Doc (ADR-0009 source) still has 'the'"),
        ("replace", ('slug',),
         "bliss-is-the-system-the-ecs-estrogen-the-perimenopause",
         "follows the title edit; the rule reproduces the live slug from the live title (test_propose_slug)"),
        ("replace", _B0_TITLE,
         "“Bliss Is the System | The ECS, Estrogen & the Perimenopause Connection”",
         "the same title edit, in the intro"),
        ("replace", ('meta_description',),
         "Heather gets together with Dr. Saffi Velloquin - phytopharmacist, PhD in pharmaceutical biology and phytochemistry, and certified endocannabinoid medicine",
         "live is hand-written; ruling OPEN-2 = first hook sentence, ≤160 on a word boundary"),
        ("replace", _B0_CLAUSE,
         " — a phytopharmacist and women’s health strategist specializing in cannabinoid science and female-specific physiology — for a conversation about The Bliss Molecule You’re Running Low, Why Men and Women Don’t Share the Same ECS, 2-AG, Orgasm, and Depression, High-THC Culture and the Overloaded System and The Simplest ECS Reset You Can Start Today.",
         "AC-2 named: descriptor + topics clause (live was model-written; ruling OPEN-4)"),
        ("replace", _BULLET(0, 0),
         "The Bliss Molecule You’re Running Low",
         "hand-edit slip in the Doc: '**The Bliss Molecule You're Running Low** On' — the bold closes one word early; live has '…Running Low On'. Not auto-repaired (Angela)"),
        ("replace", _BULLET(0, 1),
         " On Anandamide - Sanskrit for ",
         "the same slip: 'On' lands in the body (' On Anandamide…')"),
        ("replace", _BULLET(2, 0),
         "2-AG, Orgasm, and Depression",
         "live bold lead carries a trailing space ('Depression ') — paste artifact; the Doc has none"),
        ("replace", _BULLET(4, 0),
         "The Simplest ECS Reset You Can Start Today",
         "same trailing-space artifact on live ('Start Today ')"),
        ("replace", ('blocks', 2, 'content', 5, 'inlines', 0, 'text'),
         "Tune in to recalibrate your understanding of what perimenopause is actually doing — and walk away with the science that turns confusion into agency, and symptoms into signals you finally know how to read.",
         "tune-in CTA edited on live ('how estrogen & perimenopause really relate'); the Doc says 'what perimenopause is actually doing' — not reproducible from inputs"),
        ("replace", _SHARE,
         "Know someone who needs this conversation right now? Send them this episode — and tag us @genxhitsmenopause so we can amplify right back.",
         "live share line was hand-written; ruling OPEN-3 = SHARE_SENTENCE"),
        ("replace", _FAQ_ANSWER(2),
         "Anandamide — from the Sanskrit word for “bliss” — is an endocannabinoid produced on demand in your body, and its function closely mirrors that of THC: it binds to CB1 receptors to reduce pain perception and improve mood. Dr. Velloquin explains that estrogen directly promotes anandamide production, which is why women typically feel better and have higher pain tolerance around ovulation when estrogen peaks. In perimenopause, as estrogen oscillates erratically rather than declining in a straight line, anandamide levels become equally unpredictable — stripping away the rhythmic mood and pain regulation that premenopausal women rely on without even realizing it. Understanding this link is the first step toward targeting ECS support intentionally during the perimenopause transition.",
         "FAQ Q3 answer edited on live (' — stripping' → ', stripping') and the edit kept straight quotes (\"bliss\"); faq.md is the source"),
        ("replace", _FAQ_ANSWER(3),
         "Chronic high-dose THC overstimulates the ECS — a finely tuned balancing system — until receptor sensitivity downregulates and the system essentially stops responding, which Dr. Velloquin describes as the body saying “I’m not going to accept this anymore.” Women are hit harder than men because they metabolize THC into stronger metabolites that stay in the body longer due to higher fat cell percentages, and after three to four days of use, women show significantly higher plasma THC levels than men at the same dose. Dr. Velloquin recommends that no one needs flower above 20% THC, and that any THC-containing products should be used in the late evening — never during the day — to avoid further impairing cognitive function that is already under stress during perimenopause.",
         "FAQ Q4 answer edited on live ('— a finely tuned balancing system —' removed) with straight quotes (\"I'm …\")"),
        ("extra", ('blocks', 15, 'questions', 4), None,
         "faq.md has 5 questions; Q5 ('What is the simplest thing…') was deleted on live"),
    ],
    "E30": [
        ("replace", ('hero_media_id',), None,
         "Cover Image ruling (John, 2026-09-23): the tile is the Cover Image (thumbnail_media_id, "
         "set by blog_publish after upload), the hero stays empty; the migrated posts carry the "
         "tile as hero"),
        ("replace", ('meta_description',),
         "Heather Reilly-Hemstra sits down with Oriel J. Fenwax, MD FIDSA — infectious disease physician, associate professor at the University of South Florida",
         "live is hand-written; ruling OPEN-2, plus the PO ruling that a cut never ends on a "
         "dangling ', and' (the raw 160-char cut ends '…South Florida, and')"),
        ("replace", _B0_CLAUSE,
         " — an infectious disease physician and Affiliate Associate Professor of Infectious Diseases and Tropical Medicine at the University of South Florida — for a conversation about The Microbiome Footprint, Microplastics & Menopause Hormones, Five Pillars of Health, The Triathlete Bacteria Study and Actionable First Steps.",
         "AC-2 named: descriptor + topics clause (ruling OPEN-4)"),
        ("replace", ('blocks', 3, 'content', 1, 'inlines'),
         [
            {"type": "text", "text": "Driven by his own journey from “I thought I was healthy” to nearly pre-diabetic, Dr. Fenwax launched the “", "marks": [], "href": None},
            {"type": "text", "text": "Whole Larder", "marks": ["strong"], "href": None},
            {"type": "text", "text": "” podcast — a doctor’s year of cooking from scratch, available wherever you listen. He also writes mystery novels and sails on weekends.", "marks": [], "href": None},
        ],
         "the Doc bolds “**Whole Larder**” in the About bio; the live post was pasted without it (one plain text node). The build carries the Doc's bold"),
        ("replace", _SHARE,
         "Know someone who needs this conversation right now? Send them this episode — and tag us @genxhitsmenopause so we can amplify right back.",
         "live share line was hand-written; ruling OPEN-3"),
    ],
}


def _get(d, path):
    for k in path:
        d = d[k]
    return d


def _set(d, path, value):
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = value


def _expected_from_live(live: dict) -> dict:
    """The live GET projected onto build_post's keys. tag_slugs comes from tags[]."""
    return {
        "title": live["title"],
        "slug": live["slug"],
        "parent_slug": live["parent_slug"],
        "tag_slugs": [t["slug"] for t in live["tags"]],
        "layout": live["layout"],
        "hero_layout": live["hero_layout"],
        "hero_media_id": live["hero_media_id"],
        "meta_description": live["meta_description"],
        "content_mode": live["content_mode"],
        "blocks": copy.deepcopy(live["blocks"]),
    }


def _first_diff(a, b, path=()):
    if type(a) is not type(b):
        return path, a, b
    if isinstance(a, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in a or k not in b:
                return path + (k,), a.get(k, "<absent>"), b.get(k, "<absent>")
            r = _first_diff(a[k], b[k], path + (k,))
            if r:
                return r
    elif isinstance(a, list):
        if len(a) != len(b):
            return path + ("len",), len(a), len(b)
        for i, (x, y) in enumerate(zip(a, b)):
            r = _first_diff(x, y, path + (i,))
            if r:
                return r
    elif a != b:
        return path, a, b
    return None


def _golden(ep: str):
    apple, quotes, faq, meta, live = _golden_inputs(ep)
    got = blog_post.build_post(apple, quotes, faq, meta)
    content_gates.assert_blog_post_wellformed(got, quotes, ep)  # the golden post passes the gate
    assert len(got["blocks"]) == len(live["blocks"]) == 16, len(got["blocks"])

    expected, got_cmp = _expected_from_live(live), copy.deepcopy(got)
    for op, path, want, reason in ACCEPTED_DELTAS[ep]:
        if op == "extra":
            parent, idx = _get(got_cmp, path[:-1]), path[-1]
            assert len(parent) == idx + 1 and len(_get(expected, path[:-1])) == idx, \
                f"{ep} extra-delta {path} no longer holds ({reason})"
            parent.pop(idx)
            continue
        live_val, ours = _get(expected, path), _get(got_cmp, path)
        assert ours == want, f"{ep} delta {path}: build changed\n  built: {ours!r}\n  want:  {want!r}"
        assert live_val != want, f"{ep} delta {path} no longer differs from live — remove it ({reason})"
        _set(expected, path, want)

    diff = _first_diff(got_cmp, expected)
    assert diff is None, (
        f"{ep}: UNLISTED difference at {diff[0]}\n  built: {diff[1]!r}\n  live:  {diff[2]!r}"
    )
    print(f"  AC-2 golden {ep}: all 16 blocks (FAQ included) + page fields equal :8627 except "
          f"{len(ACCEPTED_DELTAS[ep])} listed deltas ✓")


def test_golden_e31():
    _golden("E31")


def test_golden_e30():
    _golden("E30")


def test_plain_text_export_refused():
    """F-2: the OLD text/plain export flattened the bold that delimits a bullet lead.
    That shape (simulated by dropping the ** from the markdown fixture) is refused — the
    build never guesses where a lead ends."""
    for ep in ("E31", "E30"):
        apple, quotes, faq, meta, _ = _golden_inputs(ep)
        try:
            blog_post.build_post(apple.replace("**", ""), quotes, faq, meta)
        except content_gates.GateFailure as exc:
            assert "bold lead" in str(exc), exc
        else:
            raise AssertionError(f"{ep}: a summary with no **lead** must be refused")
    print("  F-2 refusal: a summary without bold leads (old text/plain export) raises GateFailure ✓")


# ── AC-1 — deterministic ───────────────────────────────────────────────────────


def test_deterministic_bytes():
    apple, quotes, faq, meta, _ = _golden_inputs("E31")
    a = json.dumps(blog_post.build_post(apple, quotes, faq, meta), ensure_ascii=False)
    b = json.dumps(blog_post.build_post(apple, quotes, faq, meta), ensure_ascii=False)
    assert a == b
    print("  AC-1 deterministic: same inputs → byte-identical JSON ✓")


# ── Resolved rulings ───────────────────────────────────────────────────────────


def test_meta_description_rule():
    # Pinned exactly (also pinned as golden deltas). E30's first sentence runs past
    # "Oriel J." (a middle initial, not a stop); its raw 160-char cut ends "…Florida, and",
    # and the PO ruling peels the dangling ", and".
    want = {
        "E31": "Heather gets together with Dr. Saffi Velloquin - phytopharmacist, PhD in "
               "pharmaceutical biology and phytochemistry, and certified endocannabinoid medicine",
        "E30": "Heather Reilly-Hemstra sits down with Oriel J. Fenwax, MD FIDSA — infectious "
               "disease physician, associate professor at the University of South Florida",
    }
    for ep, expected in want.items():
        apple, quotes, faq, meta, _ = _golden_inputs(ep)
        got = blog_post.build_post(apple, quotes, faq, meta)["meta_description"]
        assert got == expected, (ep, got)
        assert len(got) <= 160
    # FIRST sentence only: a short two-sentence hook yields just the first (Jen, G-2).
    apple, quotes, faq, meta, _ = _golden_inputs("E30")
    first_hook = apple.split("\n\n")[1]
    short = apple.replace(first_hook, "Short first. Second one.", 1)
    assert blog_post.build_post(short, quotes, faq, meta)["meta_description"] == "Short first."
    assert blog_post.first_sentence("Dr. Ann met Mr. Bob. Then Cy.") == "Dr. Ann met Mr. Bob."
    assert blog_post.cap_on_word("one two three", 9) == "one two"
    assert blog_post.cap_on_word("short", 160) == "short"
    assert blog_post.cap_on_word("alpha beta, and gamma", 16) == "alpha beta"
    assert blog_post.cap_on_word("the best of the rest", 16) == "the best"
    print("  OPEN-2 meta_description: E30/E31 pinned; first sentence only; ≤160 on a word; "
          "no dangling and/or/but/with/of/the/a/comma ✓")


def test_share_block_is_the_constant():
    """The built share block carries SHARE_SENTENCE, and the constant is the story's text."""
    assert blog_post.SHARE_SENTENCE == (
        "Know someone who needs this conversation right now? Send them this episode — and "
        "tag us @genxhitsmenopause so we can amplify right back."
    )
    for ep in ("E31", "E30"):
        apple, quotes, faq, meta, _ = _golden_inputs(ep)
        share = blog_post.build_post(apple, quotes, faq, meta)["blocks"][14]
        assert share["headline"] == {"inlines": [{"text": "Share this episode"}], "type": "h2"}
        assert _get(share, _SHARE[2:]) == blog_post.SHARE_SENTENCE, share
    print("  OPEN-3 share: the built block (E31, E30) is SHARE_SENTENCE, the story's text ✓")


def test_descriptor_and_topics():
    apple, quotes, faq, meta, _ = _golden_inputs("E31")
    intro = blog_post.build_post(apple, quotes, faq, meta)["blocks"][0]["content"][0]["inlines"]
    assert intro[3] == {"type": "text", "text": "Heather Reilly Hiemstra", "marks": [], "href": "/about"}
    assert intro[6]["text"].startswith(
        " — a phytopharmacist and women’s health strategist specializing in cannabinoid "
        "science and female-specific physiology — for a conversation about The Bliss "
        "Molecule You’re Running Low, Why Men"), intro[6]["text"]  # the Doc's early-closing bold
    assert intro[6]["text"].endswith(" System and The Simplest ECS Reset You Can Start Today.")
    assert blog_post.join_and(["a"]) == "a"
    assert blog_post.join_and(["a", "b"]) == "a and b"
    assert blog_post.join_and(["a", "b", "c"]) == "a, b and c"
    print("  OPEN-4 intro: descriptor after the guest name, leads joined '…, … and …', /about link ✓")


# ── helpers ────────────────────────────────────────────────────────────────────


def test_attribution():
    cases = [("Dr. Saffi Velloquin", "Dr. Saffi"), ("Dr. Oriel Fenwax", "Dr. Oriel"),
             ("Heather Reilly-Hemstra", "Heather"), ("Priya", "Priya")]
    for raw, want in cases:
        assert blog_post.attribution(raw) == want, (raw, blog_post.attribution(raw))
    print(f"  attribution: {len(cases)} cases (honorific kept, first name, host garble) ✓")


def test_propose_slug():
    # The rule reproduces both live slugs from the live titles.
    for ep in ("E31", "E30"):
        _, _, _, live = _load(ep)
        hook_topic = live["title"].rsplit(" | GenX Hits Menopause", 1)[0]
        assert blog_post.propose_slug(hook_topic) == live["slug"], (ep, blog_post.propose_slug(hook_topic))
    assert blog_post.propose_slug("Don't Wait for the Stop | X") == "dont-wait-for-the-stop-x"
    long = blog_post.propose_slug("word " * 30)
    assert len(long) <= 64 and not long.endswith("-"), long
    print("  slug: live E31/E30 slugs reproduced; apostrophe dropped; ≤64 on a hyphen ✓")


def test_smart_quotes():
    assert blog_post.smart_quotes('she said "hi" and it\'s fine') == "she said “hi” and it’s fine"
    assert blog_post.smart_quotes("already “curly” ’") == "already “curly” ’"
    print("  smart quotes: open/close by context, apostrophe → ’, idempotent on curly ✓")


def test_heading_curled():
    apple, quotes, faq, meta, _ = _golden_inputs("E31")
    q = copy.deepcopy(quotes); q[1]["heading"] = "The Bliss Molecule You're Running Low On"
    h3 = blog_post.build_post(apple, q, faq, meta)["blocks"][6]["content"][0]
    assert h3 == {"inlines": [{"text": "The Bliss Molecule You’re Running Low On"}], "type": "h3"}, h3
    print("  heading: straight apostrophe in `heading` → curly in the h3 ✓")


def test_quote_text_clean_only():
    """An old-schema quote (E23–E25: `quote_text_clean`, no `text`) still builds, and the
    gate accepts it as verbatim (Jen, A-4)."""
    apple, quotes, faq, meta, _ = _golden_inputs("E31")
    q = copy.deepcopy(quotes)
    q[0] = {"index": 1, "speaker": "Dr. Saffi Velloquin", "heading": "Old Schema",
            "quote_text_clean": "It's the old schema's text."}
    post = blog_post.build_post(apple, q, faq, meta)
    assert post["blocks"][5]["quote"] == "“It’s the old schema’s text.”", post["blocks"][5]["quote"]
    content_gates.assert_blog_post_wellformed(post, q, "E31")
    print("  quote source: `quote_text_clean`-only quote builds and passes the verbatim gate ✓")


def test_faq_parse_and_shape():
    # SYNTHETIC — the prompts/faq.md output format, not any episode's FAQ.
    faq = ("Q: What is \"X\" in perimenopause?\nA: It's a thing. Second sentence.\n\n"
           "Q: Who is Dr. Y?\nA: A doctor.\n")
    pairs = blog_post.parse_faq(faq)
    assert pairs == [('What is "X" in perimenopause?', ["It's a thing. Second sentence."]),
                     ("Who is Dr. Y?", ["A doctor."])], pairs
    apple, quotes, _, meta, _ = _golden_inputs("E30")
    block = blog_post.build_post(apple, quotes, faq, meta)["blocks"][-1]
    assert block["questions"][0] == {
        "type": "question", "prompt": [{"text": "What is “X” in perimenopause?"}],
        "answer": [{"type": "p", "inlines": [
            {"type": "text", "text": "It’s a thing. Second sentence.", "marks": [], "href": None}]}],
    }, block["questions"][0]
    try:
        blog_post.parse_faq("no pairs here")
    except content_gates.GateFailure:
        pass
    else:
        raise AssertionError("faq with no Q/A pairs must be refused")
    print("  FAQ: Q:/A: pairs → question nodes in the live shape (synthetic input) ✓")


# ── AC-3 — the gate ────────────────────────────────────────────────────────────


def _golden_post(ep="E31"):
    apple, quotes, faq, meta, _ = _golden_inputs(ep)
    return blog_post.build_post(apple, quotes, faq, meta), quotes


def _rejects(post, quotes) -> bool:
    try:
        content_gates.assert_blog_post_wellformed(post, quotes, "E31")
    except content_gates.GateFailure:
        return True
    return False


def test_gate_rejects():
    post, quotes = _golden_post()
    assert not _rejects(post, quotes), "the golden post must pass"

    cases = {}
    p = copy.deepcopy(post); p["blocks"].pop(1); cases["block count 15 ≠ 2n+6"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"][1]["href"] = "http://open.spotify.com/episode/x"
    cases["non-https Spotify"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"][2]["href"] = "APPLE-URL-PENDING"
    cases["APPLE-URL-PENDING"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"][2]["href"] = "https://"
    cases["bare https://"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"][2]["href"] = "https://podcasts.apple.com/APPLE-URL-PENDING"
    cases["APPLE-URL-PENDING on the real host"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"][2]["href"] = "https://example.com/ep"
    cases["example.com host"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"][1]["href"] = "https://evil.example/open.spotify.com/episode/x"
    cases["Spotify label, wrong host"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"][3]["href"] = "https://www.amazon.com/other"
    cases["Audible not the fixed URL"] = p
    p = copy.deepcopy(post); p["blocks"][0]["content"].pop(3)
    cases["Audible cta popped"] = p
    p = copy.deepcopy(post); p["title"] = "TBD | GenX Hits Menopause E31"
    cases["placeholder title"] = p
    p = copy.deepcopy(post); p["blocks"][3]["content"][0]["inlines"][0]["text"] += " <tool_call>"
    cases["<tool_call> in bio"] = p
    p = copy.deepcopy(post); p["blocks"][2]["content"][0]["inlines"][0]["text"] = "Step 1: gather"
    cases["'Step ' marker"] = p
    p = copy.deepcopy(post); p["blocks"][-1]["questions"][0]["answer"][0]["inlines"][0]["text"] = "| a | b |"
    cases["table row in FAQ"] = p
    p = copy.deepcopy(post); p["blocks"][5]["quote"] = p["blocks"][5]["quote"].replace("sleep", "rest", 1)
    cases["paraphrased quote"] = p
    p = copy.deepcopy(post); p["blocks"][6]["content"][0]["inlines"][0]["text"] = " "
    cases["empty h3 heading"] = p
    # C-11: Tailor's href rule, mirrored before any call (an inline link in the Doc).
    for name, href in (("inline http:// link", "http://example.com/x"),
                       ("inline protocol-relative //", "//evil.example/x"),
                       ("inline /\\ disguised off-site", "/\\evil.example"),
                       ("inline javascript:", "javascript:alert(1)"),
                       ("inline tab-disguised /<tab>/", "/\t/evil.example"),
                       ("inline control char", "https://ok.example/\x00")):
        p = copy.deepcopy(post); p["blocks"][3]["content"][0]["inlines"][0]["href"] = href
        cases[name] = p

    for name, bad in cases.items():
        assert _rejects(bad, quotes), f"gate must reject: {name}"

    # A-1: real-shaped URLs whose slug/ID merely CONTAIN "pending"/"tbd" must pass.
    for label_i, href in ((2, "https://podcasts.apple.com/us/podcast/depending-on-your-hormones/id1785708777?i=1000000000031"),
                          (2, "https://podcasts.apple.com/us/podcast/impending-change-spending-less/id1785708777?i=1"),
                          (1, "https://open.spotify.com/episode/4tbdQ9xZ0aBcDeFgHiJkLm")):
        ok = copy.deepcopy(post); ok["blocks"][0]["content"][label_i]["href"] = href
        assert not _rejects(ok, quotes), f"gate must PASS a real listen URL: {href}"
    # C-11 accepts what Tailor accepts: https, site-relative, fragment.
    for href in ("https://rockstarblends.com/shop", "/about", "#faq"):
        ok = copy.deepcopy(post); ok["blocks"][3]["content"][0]["inlines"][0]["href"] = href
        assert not _rejects(ok, quotes), f"gate must PASS the Tailor-accepted href {href!r}"
    # A-3: prose opening "Step into…" is legit; only a line opening "Step N" is narration.
    ok = copy.deepcopy(post)
    ok["blocks"][3]["content"][0]["inlines"][0]["text"] = "Step into midlife with the science. Step by step."
    assert not _rejects(ok, quotes), "'Step into…' prose must pass"
    # Wrong quote count for the same blocks is a count failure too.
    assert _rejects(post, quotes[:4]), "gate must reject: 5 highlights built, 4 quotes given"
    print(f"  AC-3 gate: golden passes; rejects {len(cases) + 1} cases "
          f"({', '.join(cases)}, quote-count mismatch); passes 'depending'/'impending'/'tbd'-"
          f"in-ID URLs and 'Step into…' prose ✓")


# ── F-2 — the markdown download ────────────────────────────────────────────────


def _drive_client():
    """The REAL drive_client with its Google SDK imports stubbed — only the pure
    export_mime_for is called."""
    for name in ("googleapiclient", "googleapiclient.discovery", "googleapiclient.errors",
                 "googleapiclient.http", "google_auth"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["googleapiclient.discovery"].build = lambda *a, **k: None
    sys.modules["googleapiclient.errors"].HttpError = type("HttpError", (Exception,), {})
    sys.modules["googleapiclient.http"].MediaFileUpload = object
    sys.modules["googleapiclient.http"].MediaIoBaseDownload = object
    sys.modules["google_auth"].drive_credentials = lambda: None
    stub = sys.modules.get("drive_client")
    sys.modules.pop("drive_client", None)
    try:
        import drive_client as dc
        importlib.reload(dc)
        return dc
    finally:
        if stub is not None:
            sys.modules["drive_client"] = stub


def test_export_mime_for():
    dc = _drive_client()
    doc = "application/vnd.google-apps.document"
    assert dc.export_mime_for(doc, "apple_summary.md") == "text/markdown"
    assert dc.export_mime_for(doc, "FAQ.MD") == "text/markdown"
    # Not markdown to begin with: the markdown export would backslash-escape it.
    assert dc.export_mime_for(doc, "quotes.json") == "text/plain"
    assert dc.export_mime_for(doc, "transcript_fast.txt") == "text/plain"
    assert dc.export_mime_for("application/vnd.google-apps.spreadsheet", "quotes.json") == "text/csv"
    assert dc.export_mime_for("application/vnd.google-apps.form", "x.md") is None
    print("  F-2 download: *.md Doc → text/markdown; json/txt Doc → text/plain; Sheet → text/csv ✓")


def test_download_file_exports_md_doc_as_markdown():
    """D-1: download_file itself asks Drive for text/markdown on a Doc named *.md, and for
    text/plain on a Doc named *.json — through a stubbed _svc(), no network."""
    import tempfile
    dc = _drive_client()
    calls = []

    class _Req:
        def __init__(self, kind, **kw):
            calls.append((kind, kw))

    class _Files:
        def __init__(self, meta):
            self.meta = meta

        def get(self, **kw):
            return types.SimpleNamespace(execute=lambda: self.meta)

        def export_media(self, **kw):
            return _Req("export_media", **kw)

        def get_media(self, **kw):
            return _Req("get_media", **kw)

    class _Download:
        def __init__(self, fh, request, chunksize=0):
            self.fh = fh

        def next_chunk(self):
            self.fh.write(b"\xef\xbb\xbf## Title\n")
            return None, True

    dc.MediaIoBaseDownload = _Download
    doc = "application/vnd.google-apps.document"
    with tempfile.TemporaryDirectory() as tmp:
        for name, want in (("apple_summary.md", "text/markdown"), ("quotes.json", "text/plain")):
            calls.clear()
            dc._svc = lambda: types.SimpleNamespace(files=lambda: _Files({"mimeType": doc, "name": name}))
            out = dc.download_file("fid-1", Path(tmp) / name)
            assert calls == [("export_media", {"fileId": "fid-1", "mimeType": want})], calls
            assert out.read_bytes() == b"## Title\n", out.read_bytes()  # BOM stripped
    print("  F-2 download_file: *.md Doc → export_media(text/markdown); *.json Doc → "
          "text/plain; BOM stripped (stubbed _svc, no network) ✓")


def test_title_from_markdown_summary():
    """ADR-0009 on the markdown export: '## ' heading, bold, and backslash escapes."""
    t = PF._title_from_apple_summary
    assert t("## Bliss Is the System | The ECS\n\nbody") == "Bliss Is the System | The ECS"
    assert t("## **Hook** \\- Topic | More\n") == "Hook - Topic | More"
    assert t("# Gen X Hits Menopause · S2E5 · Don't Wait | Topic · ft. X") == "Don't Wait | Topic"
    assert t("Plain Hook | Plain Topic\n\nbody") == "Plain Hook | Plain Topic"  # old text/plain shape
    _, _, _, meta, _ = _golden_inputs("E30")
    assert meta["episode_title"] == "Feed Your Head | Gut Microbiome, Hormones & Midlife Vitality"
    print("  F-2 title: '## ', bold and escapes stripped; H1 · form and plain line unchanged ✓")


def test_unescape_markdown():
    assert blog_post.unescape_markdown(r"Velloquin \- phyto \*bliss\* 1\. a\_b") == "Velloquin - phyto *bliss* 1. a_b"
    nodes = blog_post._inlines(r"Sanskrit for \*bliss\* \- surges")
    assert [(n["text"], n["marks"]) for n in nodes] == [
        ("Sanskrit for ", []), ("bliss", ["em"]), (" - surges", [])], nodes
    print("  F-2 escapes: \\- \\* \\. \\_ undone; escaped *bliss* is an em mark, as live ✓")


# ── F-1 — the quote heading ────────────────────────────────────────────────────


def test_heading_gate_unit():
    content_gates.assert_quote_headings([{"id": "q01", "heading": "The Idea"}], "E32")
    content_gates.assert_quote_headings([], "E32")  # no quotes is a different failure, not this one
    for bad in ([{"id": "q01"}], [{"id": "q01", "heading": "  "}],
                [{"id": "q01", "heading": "ok"}, {"id": "q02", "heading": None}]):
        try:
            content_gates.assert_quote_headings(bad, "E32")
        except content_gates.GateFailure as exc:
            assert "heading" in str(exc), exc
        else:
            raise AssertionError(f"headless quotes must be refused: {bad}")
    # build_post + the blog gate refuse a quote with no heading too (Phase-2 backstop).
    apple, quotes, faq, meta, _ = _golden_inputs("E31")
    q = copy.deepcopy(quotes); q[2]["heading"] = ""
    post = blog_post.build_post(apple, q, faq, meta)
    try:
        content_gates.assert_blog_post_wellformed(post, q, "E31")
    except content_gates.GateFailure as exc:
        assert "heading" in str(exc), exc
    else:
        raise AssertionError("blog gate must refuse an empty heading")
    print("  F-1 gate: missing/blank/None heading refused (Phase 1 gate + Phase 2 backstop) ✓")


def test_round1_headless_quotes_fail_phase1():
    """F-1: a Brand Team quote set without `heading` fails PHASE 1 — before the title,
    the counter bump, the schedule write and the Drive upload."""
    import test_title01_gen_gate as t1
    state = t1._fresh_state()
    real = {"title": "Don't Wait | Hormone Optimization", "hook": "Don't Wait",
            "topic": "Hormone Optimization"}
    round_1, drive = t1._load_round1_with_stubs(state, real)
    t1._patch_scripts(round_1)
    round_1.anthropic_client.run_brand_team_chain = (
        lambda **k: [{"id": "q01", "speaker": "Guest", "text": "q"}])  # no heading
    uploads = []
    drive.upload_file = lambda path, folder, *a, **k: uploads.append(Path(path).name)
    titles = []
    round_1.anthropic_client.compose_title = lambda **k: titles.append(1) or real

    try:
        round_1._run_pipeline(job_id=1, payload={})
    except round_1.content_gates.GateFailure as exc:
        assert "heading" in str(exc), exc
    else:
        raise AssertionError("round_1 must RAISE on quotes without `heading`")
    assert not titles, "the title step ran — the heading gate must fire first"
    assert "quotes.json" not in uploads, f"quotes.json was uploaded: {uploads}"
    assert t1._read_counter(state)["next_episode_number"] == 30, "counter bumped"
    assert t1._read_schedule(state)["episodes"] == [], "a schedule entry was written"
    print("  F-1 Phase 1: headless quotes raise in round_1 — no title, no upload, "
          "counter stays, no schedule entry ✓")


def test_brand_team_prompts_carry_heading():
    """Marcus writes `heading`; Lisa's explicit output schema must carry it through
    (Robert and Dr. Joe preserve every field by instruction)."""
    marcus = (WORKTREE / "prompts" / "brand_team_marcus.md").read_text(encoding="utf-8")
    lisa = (WORKTREE / "prompts" / "brand_team_lisa.md").read_text(encoding="utf-8")
    assert '"heading":' in marcus and "60 characters" in marcus and "Title Case" in marcus
    assert '"heading":' in lisa
    for name in ("robert", "dr_joe"):
        body = (WORKTREE / "prompts" / f"brand_team_{name}.md").read_text(encoding="utf-8")
        assert "Preserve all fields" in body, name
    print("  F-1 prompts: Marcus specs + emits heading; Lisa's schema carries it; Robert/Dr. Joe preserve ✓")



# ── runner ─────────────────────────────────────────────────────────────────────


def main() -> int:
    tests = [
        test_golden_e31,
        test_golden_e30,
        test_plain_text_export_refused,
        test_deterministic_bytes,
        test_meta_description_rule,
        test_share_block_is_the_constant,
        test_descriptor_and_topics,
        test_attribution,
        test_propose_slug,
        test_smart_quotes,
        test_heading_curled,
        test_quote_text_clean_only,
        test_faq_parse_and_shape,
        test_gate_rejects,
        test_export_mime_for,
        test_download_file_exports_md_doc_as_markdown,
        test_title_from_markdown_summary,
        test_unescape_markdown,
        test_heading_gate_unit,
        test_round1_headless_quotes_fail_phase1,
        test_brand_team_prompts_carry_heading,
    ]
    failed = 0
    print("BLOG-02a deterministic blog post build + gate — test run")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
