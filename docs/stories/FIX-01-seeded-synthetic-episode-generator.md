# FIX-01 — A seeded synthetic episode generator: test fixtures with known properties and no real people

**Owner:** Jen · **Impl:** Bill · **Security review:** Orly · **Review:** Toby · **PO:** Angela
**Priority:** Medium — **built alongside SHARE-02** · **Status:** **Proposed** — Angela, 2026-10-10, at John's request
**Created:** 2026-10-10 · **Raised by:** John · **Feeds:** SHARE-02 (G2), EVAL-03, SHARE-01

---

## Why

SHARE-02's gate G2 needs synthetic fixtures. EVAL-03 says synthetic transcripts belong in unit tests. Nobody is
assigned to make them. Today the tests run on real guest output copied from `Episodes/`
(`tests/fixtures/blog02/E30`, `E31`, `r1rel/E32`), which is why the repo can't leave the house (Orly, 2026-10-10).

A generator does more than replace those files. Hand-copied fixtures cover only the episodes that happened. A seeded
generator covers the cases the gates exist for, every time and on purpose.

**What it is not:** evidence about writing quality. Synthetic transcripts never enter the EVAL-01 regression set or
any number in CASE-01. E13 (Heather and John) is the real public episode (EVAL-03).

## Scope

1. `fixtures_gen/` (name Bill's call): `generate(seed, properties) -> Episode`, writing the same files the pipeline
   reads and writes — `transcript_fast.txt`, `quotes.json`, brand-team output, `apple_summary.md`, `blog_post.json` —
   in the shapes the current fixtures use.
2. **Templated guests and transcripts** from an invented name pool on `example.test` addresses, with selectable
   **known properties**:
   - a long tangent (start and end detection)
   - a missing guest name (title gate, thank-you fill)
   - a quote too long to fit a card or reel (CARD and REEL gates)
   - an apostrophe in the slug (BLOG-01's class)
   - the host speaking as guest (REEL-08 / REEL-09's class)
   - the title placeholder the TITLE-01 gate rejects
3. **Property-test mode for the gates:** for N seeds, generate an episode, run `content_gates.py`, and assert the gate
   result matches the property that was planted (planted defect → gate fails, naming that defect; clean → passes).
4. **No real person can appear:** the name pool is closed, and a G4-style test runs the generator over many seeds and
   scans every output against Orly's private denylist (guest names, John's and Heather's mailboxes, client domains).
5. Replace the real-content fixtures with generated ones, file by file, keeping each test's assertions unchanged.
   PP-8: a test whose fixture can't be generated yet keeps its real fixture in this private repo, and SHARE-02's
   allowlist leaves it out.

## Acceptance criteria

1. The same seed produces byte-identical output; a different seed produces different names and text. *Test.*
2. For each property in scope 2, a fixture with the property planted is caught by its gate, which names the property's
   defect; the clean fixture passes. Property mode runs at least 200 seeds in under 30 seconds.
3. The denylist scan over 1,000 seeds finds zero hits, and a test proves the scan can fail (a planted denylisted name
   is caught).
4. `tests/fixtures/blog02/E30`, `E31` and `r1rel/E32` are replaced, and their tests pass with assertions unchanged;
   any that can't be are listed here with the reason.
5. Orly's review of the name pool and the denylist test is filed in `docs/security/`, dated.

## Decisions

None open. Jen owns the property list; adding a property is adding a row to scope 2.

---

## Built — Jen with Bill, 2026-10-10 (branch `feat/fix-01`)

- **`fixtures_gen/generator.py`:** `generate(seed, prop)` writes `transcript_fast.txt`, `apple_summary.md` (in the
  shape `blog_post.parse_apple_summary` accepts), `quotes.json` (marked SYNTHETIC, with its seed and property) and
  `faq.md`.
  - **Guests** come from a closed, invented name pool, with `example.test` addresses.
  - **The 10 properties:** clean, placeholder title, guest not featured, missing heading, no bold lead, no About
    section, apostrophe in the title, the host as guest, a long tangent, and a long quote.
- **`run_gates(ep)`** runs the pipeline's **own** gates (`content_gates`, `blog_post`) and names the ones that
  refused the episode.
- **Two properties are generated here but measured elsewhere:** the long tangent (`detect_boundaries.js`) and the
  long quote (card fit needs Pillow and the real fonts). Here they only have to pass the Python gates.

**Evidence:**
- `python3 -B test_fix01_generator.py` → **4 passed**, in 0.25 s:
  - **Determinism:** the same seed gives byte-identical files for every property, and seeds vary the guests.
  - **Property mode:** 200 seeds × 10 properties, every one refused by exactly the gates its property names.
  - **Real names:** none across 1,000 seeds.
  - **Fixture quality:** a clean episode builds a well-formed post with the real `build_post`.
- **Mutation check:** a real guest's surname put into the pool is caught at seed 6.

**The denylist is thin, and named as such.** It has 8 name parts, taken from the real fixtures' speakers and the
guests named in the stories, plus Orly's private G4 list via `DENYLIST_PATH`. The test refuses to pass on fewer than
5 parts ("UNVERIFIED"). **In the public export the real fixtures are gone, so `DENYLIST_PATH` is required there**
(SHARE-02 G4).

**AC-4 (replace the real-content fixtures): decided, not done.**
- `test_blog02`, `test_blog02b` and `test_r1_reliability` assert on those episodes' real content: a block count of
  16, a guest's name, a summary's exact wording. Swapping in synthetic fixtures would mean rewriting their
  assertions, which AC-4 forbids.
- So, as scope 5 allows: they **keep their real fixtures in this private repo**, and SHARE-02's allowlist leaves
  them **out** of the export.
- The export's tests run on generated episodes: this suite, and the gate suites that take any fixture.
- Orly reviews the name pool and the denylist test (AC-5) with SHARE-02.

**Cold review of PR #37: changes requested by all three reviewers. The Built section above overstated AC-2.**

**Corrected claim: only 5 of the 10 properties are checked by a gate.**
- **Refused by exactly the gate named:** placeholder title, guest not featured, missing heading, no bold lead, no
  About section (`GATE_CHECKED`).
- **Generated, but no gate checks the property here.** Their "expected: nothing refuses it" is a statement about the
  gates, not a check:
  - apostrophe in the title: a local slug regex, not a pipeline gate;
  - host as guest: this **documents a known gap**. `assert_guest_featured` exempts any guest named Heather, so an
    all-host quote set passes;
  - long tangent: for `detect_boundaries.js`;
  - long quote: for the card-fit check, which needs Pillow.
- **"A missing guest name" (scope 2) is not generated,** and `assert_thank_you_wellformed` is not exercised. Both are
  open.
- **Scope 1's brand-team output and `blog_post.json` are not written:** the real `build_post` makes the post from
  the generated files.

**Fixed:**
- **Name pool:**
  - Removed a real herbal-tea estate (Tregothnan), a real village (Ombersley) and five real surnames.
  - Every surname is now a coinage.
  - Every guest is marked synthetic: the transcript header reads `[SYNTHETIC EPISODE — …]`, and the About bio says
    "a synthetic guest".
  - The public host's name is the one deliberate real name, and the docstring says why.
- **Denylist:**
  - **Removed the story regex,** which produced junk such as "Cover Image".
  - **Sources:** the real fixtures' speakers (synthetic fixtures skipped) plus `DENYLIST_PATH`. With neither, the
    test reports **UNVERIFIED**.
  - **Coverage:** parts of 3+ letters are scanned, and a real guest also named Heather is still scanned (only the
    host's exact name is carved out).
  - **Domains:** the real mailboxes' domains are scanned too.
- **Determinism is pinned by a golden-hash test,** not assumed. It is identical under `PYTHONHASHSEED` 0, 1 and
  random.

`test_fix01_generator.py` → **6 passed**.

**Still open:**
- **AC-5:** Orly's review of the reworked pool and denylist, filed in `docs/security/`.
- **SHARE-02:** must confirm its allowlist leaves out `blog02/E30`, `blog02/E31` and `r1rel/E32`.
