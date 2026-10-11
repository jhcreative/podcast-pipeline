"""
fixtures_gen/generator.py — seeded synthetic episodes: test fixtures with known properties and no real people (FIX-01).

The tests used to run on real guest output copied from Episodes/. That is why the repo couldn't leave the house
(Orly, 2026-10-10), and it only covered the episodes that happened to occur. This generator writes the same files the
pipeline reads — transcript_fast.txt, apple_summary.md (the shape blog_post.parse_apple_summary accepts),
quotes.json, faq.md — for invented guests, with a chosen PROPERTY planted, so each gate is exercised on purpose.

    ep = generate(seed=7, prop="guest_not_featured")
    ep.write(Path("/tmp/E907"))
    run_gates(ep)   # → {"assert_guest_featured"}: the gates that refused it, by name

What it is NOT: evidence about writing quality. A synthetic transcript never enters the regression set (EVAL-01)
or a number in the case study (CASE-01).

The name pool is closed and invented, and every guest is marked synthetic in the files it appears in. The one real
person in every output is the show's public host, Heather Reilly Hiemstra — the carve-out is deliberate: she hosts
every episode, and her name is the show's, not a guest's. test_fix01_generator.py scans every output against the
real guests (the private denylist) and the real mailboxes and domains.

Determinism: random.Random(str) seeds through SHA-512, independent of PYTHONHASHSEED; Python promises reproducible
random() only, so choice()/sample() stability is PINNED BY A GOLDEN-HASH TEST rather than assumed.

Written: transcript_fast.txt, apple_summary.md, quotes.json, faq.md. NOT written (scope 1 also named them): brand-team
intermediate output and blog_post.json — the pipeline's own build_post makes the post from these files.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

HOST = "Heather Reilly Hiemstra"
# Invented, and checked against real surnames, places and brands (re-review: Tregothnan is a real herbal-tea
# estate, Ombersley a real village, and Marchbanks/Penhallow/Vellacott/Grisewood/Dunwiddie real surnames). Every
# surname is a coinage; every guest is also marked synthetic in the files (the About bio and the transcript header).
_FIRST = ["Marlowe", "Ondine", "Thessaly", "Ravenna", "Calloway", "Linnea", "Peregrine", "Saffi", "Isolde",
          "Tamsin", "Wren", "Corisande", "Bettany", "Juniper", "Oriel", "Sabeline"]
_LAST = ["Quillfeatherby", "Ashgrovenly", "Pemberly-Vantor", "Kestrelmoorish", "Larkspirelle", "Thistlewoodby",
         "Fairhallowen", "Brindlequist", "Morrowvane", "Velloquin", "Strandwhistle", "Corvellian"]
_CREDENTIAL = ["functional nutritionist", "sleep researcher", "pelvic-floor physiotherapist", "herbalist",
               "endocrinology nurse", "strength coach", "menopause doula", "integrative pharmacist"]
_TOPICS = [("Sleep Architecture", "how deep sleep changes after forty and what to do about the 3 a.m. wake-ups"),
           ("Strength Over Cardio", "why lifting heavy protects bones and blood sugar in perimenopause"),
           ("The Gut-Hormone Loop", "how the microbiome recycles estrogen and why fiber matters"),
           ("Hot Flash Triggers", "the overlooked triggers and a two-week tracking experiment"),
           ("Pelvic Floor Truths", "what leaks, aches and pressure are telling you, and the fixes"),
           ("Plant Medicine Basics", "how to read a botanical label and dose with care"),
           ("Midlife Mood Swings", "separating hormone noise from burnout, and when to ask for help"),
           ("Protein at Every Meal", "why the numbers change after forty and simple swaps")]
_HOOKS = ["Different Rhythm, Same Song", "The Quiet Engine", "Under the Surface", "Turning Down the Heat",
          "Small Levers, Big Shifts", "What the Body Knows", "Rewriting the Map", "Hold the Line"]

# Each property, and the gates that MUST refuse an episode carrying it (run_gates names them).
PROPERTIES = {
    "clean": set(),
    "placeholder_title": {"assert_real_title"},
    "guest_not_featured": {"assert_guest_featured"},
    "missing_heading": {"assert_quote_headings"},
    "no_bold_lead": {"parse_apple_summary"},
    "no_about_section": {"parse_apple_summary"},
    # Generated but NOT refused by a Python gate here — the empty set means "passes the Python gates", which is a
    # statement about the gates, not a check of the property (re-review: say so):
    "apostrophe_in_title": set(),   # checked: propose_slug still yields a clean slug (a local regex, not a gate)
    "host_as_guest": set(),         # DOCUMENTS a known gap: assert_guest_featured exempts any guest named Heather
                                    # (REEL-08), so an all-host quote set passes. Not a fix of that rule.
    "long_tangent": set(),          # for detect_boundaries.js (Node) — not checked by any Python suite yet
    "long_quote": set(),            # for the card-fit check (needs Pillow + fonts) — not checked here
}
GATE_CHECKED = {"placeholder_title", "guest_not_featured", "missing_heading", "no_bold_lead", "no_about_section"}


@dataclass
class Episode:
    seed: int
    prop: str
    number: int
    title: str
    guest_name: str
    guest_email: str
    transcript: str
    apple_summary: str
    quotes: list
    faq: str
    meta: dict = field(default_factory=dict)

    @property
    def code(self) -> str:
        return f"E{self.number}"

    def write(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "transcript_fast.txt").write_text(self.transcript, encoding="utf-8")
        (folder / "apple_summary.md").write_text(self.apple_summary, encoding="utf-8")
        (folder / "quotes.json").write_text(json.dumps({"_fixture_note": f"SYNTHETIC — fixtures_gen seed {self.seed}, "
                                                        f"property {self.prop}", "quotes": self.quotes},
                                                       indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "faq.md").write_text(self.faq, encoding="utf-8")
        return folder


def generate(seed: int, prop: str = "clean") -> Episode:
    if prop not in PROPERTIES:
        raise ValueError(f"unknown property {prop!r}; choose from {sorted(PROPERTIES)}")
    rng = random.Random(f"{seed}:{prop}")
    number = 900 + seed % 100
    first, last = rng.choice(_FIRST), rng.choice(_LAST)
    guest = f"{first} {last}"
    if prop == "host_as_guest":
        guest = HOST
    credential = rng.choice(_CREDENTIAL)
    topics = rng.sample(_TOPICS, 4)
    hook = rng.choice(_HOOKS)
    if prop == "apostrophe_in_title":
        hook = f"{first}'s {hook}"
    title = "TBD" if prop == "placeholder_title" else f"{hook} | {topics[0][0]} with {guest}"
    email = f"{first.lower()}.{last.lower()}@example.test"

    # transcript: alternating host and guest turns, one per topic
    lines = [f"[SYNTHETIC EPISODE — fixtures_gen seed {seed}, property {prop}]"]
    for lead, body in topics:
        lines.append(f"{HOST}: Let's talk about {lead.lower()}. What do people get wrong?")
        lines.append(f"{guest}: Mostly {body}. I see it every week in my practice.")
    if prop == "long_tangent":
        lines.insert(3, f"{HOST}: Before we start — " + "a story about my sourdough starter, " * 60 + "anyway.")
    transcript = "\n".join(lines) + "\n"

    # quotes: five, each with a heading (BLOG-02 F-1); the guest featured unless the property says otherwise
    quotes = []
    for i, (lead, body) in enumerate(topics + [topics[0]]):
        speaker = HOST if (prop == "guest_not_featured" or i == 4) else guest
        text = f"When it comes to {lead.lower()}, {body}."
        if prop == "long_quote" and i == 0:
            text = " ".join([text] * 8)
        q = {"id": f"q{i + 1:02d}", "speaker": speaker, "text": text, "heading": lead,
             "transcript_start_ms": 0, "transcript_end_ms": 0, "reel_candidate": True, "lisa_approved": True}
        if prop == "missing_heading" and i == 2:
            q["heading"] = ""
        quotes.append(q)

    # apple_summary.md in the shape parse_apple_summary accepts
    bullets = [(f"**{lead}**" if prop != "no_bold_lead" else lead) + f" {body[0].upper()}{body[1:]}."
               for lead, body in topics]
    about = [] if prop == "no_about_section" else [
        f"**About {guest}**",
        f"{guest} is a {credential} who helps women in midlife make sense of their changing bodies "
        f"(a synthetic guest, invented for testing)."]
    summary = "\n\n".join([
        f"## {title}",
        f"{HOST} sits down with {guest}, a {credential}, to talk about {topics[0][1]}.",
        "Conversations include",
        *bullets,
        "Tune in for practical steps you can start this week.",
        *about,
    ]) + "\n"

    faq = "\n\n".join(f"Q: What is the main idea behind {lead.lower()}?\nA: {body[0].upper()}{body[1:]}."
                      for lead, body in topics) + "\n"
    return Episode(seed=seed, prop=prop, number=number, title=title, guest_name=guest, guest_email=email,
                   transcript=transcript, apple_summary=summary, quotes=quotes, faq=faq,
                   meta={"credential": credential})


def run_gates(ep: Episode) -> set[str]:
    """Run the pipeline's own gates on a generated episode; return the names of those that refused it."""
    import re

    import blog_post
    import content_gates

    refused = set()
    checks = {
        "assert_real_title": lambda: content_gates.assert_real_title(ep.title, ep.code),
        "assert_quote_headings": lambda: content_gates.assert_quote_headings(ep.quotes, ep.code),
        "assert_guest_featured": lambda: content_gates.assert_guest_featured(ep.quotes, ep.guest_name, ep.code),
        "parse_apple_summary": lambda: blog_post.parse_apple_summary(ep.apple_summary),
    }
    for name, check in checks.items():
        try:
            check()
        except content_gates.GateFailure:
            refused.add(name)
    if content_gates.is_real_title(ep.title) and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*",
                                                                  blog_post.propose_slug(ep.title)):
        refused.add("propose_slug")
    return refused
