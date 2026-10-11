# CARD-05 — Phase 2 builds the quote cards on the box, set in IBM Plex

**Owner:** Bill (impl) · **Box:** Oliver · **Review:** Toby · **Design:** Joni · **Words:** Lisa · **Tests:** Jen · **PO:** Angela
**Priority:** High · **Status:** **BUILT 2026-10-08** — renderer merged (#23); Phase 2 step, box setup and back-catalogue replace in this PR; verified live on the next batch (John: *"we'll test it all on next batch"*)
**Created:** 2026-10-08 · **Raised by:** John · **Needs:** ADR-0015 (supersedes ADR-0012 §6 and §7)

---

## Why

John, 2026-10-08: *"a story that triggers card build with google font equivalents — use our IBM Plex
throughout for readability, maintain uppercase and font-weight patterns and adjust text slides to be
vertically centered and easy to read in terms of font-size and whitespace rhythm."*

Today the cards are a deliberate local step on the Mac (ADR-0012 §6), because American Typewriter
Condensed and Avenir Next are macOS faces that cannot be licensed onto the box. ADR-0012 named the cost:
*"a forgotten card run is silent, and silent misses are this pipeline's established failure class."*
IBM Plex is open (SIL OFL), is already the Rockstar site's text face, and can be vendored onto the box, so
the cards can join Phase 2 like the reels. ADR-0012 rejected "server-side with substitute webfonts" because
brand consistency with the reels outweighed automation; John now takes the other side of that trade.

## Scope

1. **One type system, IBM Plex, everywhere a card renders** (box and Mac render identically).
2. **Phase 2 builds the full set** (slides, covers, captions, cover titles) after the reels, and puts it in
   the episode's Drive `cards/`.
3. **Text slides are re-laid out**: the quote is vertically centred, at a size that reads on a phone, on a
   whitespace rhythm, with the footer lifted 20px as on the covers.
4. **The back catalogue is re-rendered in Plex** (John, 2026-10-08): every episode whose Drive `cards/`
   holds an Avenir set (E25, E28, E29, E30, E31, E33 today) gets the new set in its place.

**Out of scope: the reels, altogether** (John, 2026-10-08: *"leave reels out of it altogether for now"*).
Their burned-in captions, framing and fonts are unchanged by this story.

## Type mapping (uppercase and weight patterns kept)

| Role | Today | CARD-05 |
|---|---|---|
| Quote | American Typewriter Condensed 50px | **IBM Plex Sans** Regular **52px / 1.42** (Q1; setting B, John 2026-10-08) |
| Opening-mark watermark | American Typewriter `“` 300px, 20% | the quote face's `“`, same size and opacity |
| Citation | Avenir Next Demi Bold 24px | **IBM Plex Sans SemiBold** 24px |
| Episode line (footer) | Avenir Next Medium 24px | **IBM Plex Sans Medium** 24px |
| Cover name, first / rest | Avenir Next Condensed Demi Bold 49 / 41px, UPPERCASE, 0.06em | **IBM Plex Sans Condensed SemiBold**, UPPERCASE, 0.06em, same 1.2 ratio |
| Cover title | Avenir Next Condensed Bold 68px | **IBM Plex Sans Condensed Bold** |
| Lockup | image in `background.png` | unchanged |

Sizes are re-measured, not copied: Plex is wider than Avenir Next Condensed, so every fit rule
(name box 344px, "VASHTI-MERCER", the title's 3 lines) is re-proved on the proof set.

**Fonts are vendored, never linked** (house rule: self-host fonts). The TTFs and `OFL.txt` live in
`BrandAssets/fonts/ibm-plex/` at a pinned release and load as `@font-face` data URIs; no request to
Google Fonts at render time. The font gate is rewritten to prove each Plex face resolved, with the same
hard failure as today.

## Text slides: centred, readable, on a rhythm

- **Vertically centred.** The quote block sits centred in the field between the top margin and the
  citation. This replaces ADR-0012's fixed top anchor (*"only the words change"*) for slides; the citation
  stays fixed, right-aligned, so the swipe still has one steady element. Called out because it reverses a
  locked canon rule.
- **Readable size: 52px / 1.42** (setting B). John picked it from the proof sheet (46 / 52 / 58px, PR #22),
  and it is now fixed.
  Fixed type and flow-don't-shrink (ADR-0012 §1) still hold.
- **Whitespace rhythm.** One spacing scale for slides and covers (base 8px: 24 / 40 / 64 / 104), the same
  side margins as the cover's text, quote-to-citation gap and footer lift on that scale.
- **Footer lifted 20px** with a purple band beneath, as on the covers, so the footer no longer jumps when
  the carousel swipes from cover to slide.

## Phase 2 trigger

- **New step after Step 12** (reels and captions uploaded) in `jobs/publish_finalize.py`: draft cover titles
  and captions (model, gated, as today), render slides and covers on the box, upload to `cards/`.
- **Never overwrites.** If `cards/` already has files (a hand-built set, a re-run), the step skips and says
  so; it uses `card_upload --new-only` semantics. `reels_only` reprocesses skip it.
- **On failure (Q3, John):** the step **alerts once and Phase 2 continues**; reels, blog, thank-you and the
  calendar still finish. The alert names the episode and the cause; the fix is a hand run of `new-cards.sh`.
- **The human gate is Drive.** Model-written titles and captions land in `cards/` unreviewed, as captions do
  today; Heather posts by hand. The standup line says *"cards ready to review in Drive"*.

## Re-rendering the back catalogue (Q2, John: re-render all)

Today's tools only add (CARD-04: `--covers-only`, `--new-only`), so this needs a deliberate replace path,
run by John:

- **`scripts/rerender-cards.sh [episodes]`**, two steps like the others: build → review sheet → `--push`.
- **Words are kept, only the type changes.** Each episode's captions are fetched from Drive and re-uploaded
  as they are (they may be hand-edited); its cover titles come from the `cover_titles.json` already staged
  (E29–E31 in `Episodes/`, E25/E28/E33 in `Episodes/_new_cards/`), redrafted only where none exists.
- **The replace is CARD-02's clear-and-repopulate**: the old set goes to Drive's trash (recoverable for 30
  days), then the new set goes up. The push prints, per episode, what it trashes before it does it.
- **Posted carousels stay as posted.** Instagram keeps the Avenir versions Heather already shared; only Drive
  changes.

## Box readiness (Oliver, RTS)

- Playwright + Chromium in the box venv; disk and RAM checked. WhisperX already OOMs when doubled up
  (`reference_rockstar_pipeline_ops`), so the card step runs after transcription has exited, never alongside.
- `scripts/deploy.sh` gains the card modules, `BrandAssets/fonts/ibm-plex/` and the new prompt in `FILES`.
- First run of this deploy path is a named risk: deploy with `--check` first, then one supervised episode.

## Acceptance criteria

1. Every card face is IBM Plex; the font gate fails a render whose faces did not resolve, on box and Mac.
2. Uppercase and weight patterns match the table above; the cover layout (ADR-0014) is otherwise unchanged.
3. Text slides: quote vertically centred; size and spacing from John's pick on the proof sheet; fixed type,
   flow-don't-shrink, verbatim and no-orphan invariants all still asserted (`test_card01_cards.py`).
4. Footer lifted 20px on slides as on covers.
5. A Phase 2 run on a new episode ends with a full set in `cards/` and a standup line; nothing in an
   existing `cards/` is trashed or replaced by Phase 2.
6. A card-step failure alerts once and does not stop Phase 2.
6a. `rerender-cards.sh` replaces each back-catalogue set with a Plex set; captions are byte-identical to the
   ones it fetched; cover titles are the staged ones; nothing is pushed without the review step.
6b. Nothing in the reel path changes (reel tests and a reels-only reprocess are untouched).
7. Mac (`new-cards.sh`, `render_cards.py`) and box produce the same layout from the same inputs (visual
   check; Chromium versions may differ, so not byte parity).
8. Proof sheet approved by John before the size is locked; ADR-0015 records it.

## Decisions (John, 2026-10-08)

| # | Question | Answer |
|---|---|---|
| Q1 | Quote face: Plex Sans or Plex Serif? | **Plex Sans** |
| Q2 | Re-render the back catalogue (E25–E33) in Plex? | **Re-render all** |
| Q3 | A card-step failure: alert and continue, or stop Phase 2? | **Alert and continue** |
| Q4 | Reels' burned-in captions to Plex? | **No: leave reels out of it altogether for now** |

## Shipping it (John runs each line; agents don't touch the box)

```bash
cd ~/RockstarPodcast
scripts/box-card-setup.sh             # 1. Oliver's check: disk, memory, WhisperX, what's installed (read-only)
scripts/box-card-setup.sh --install   # 2. Playwright 1.60.0 + Chromium + Pillow into the venv; refuses under 2 GB free or mid-WhisperX
bash scripts/deploy.sh --check        # 3. what differs on the box (also lands anything older that never shipped)
bash scripts/deploy.sh                # 4. ships the card modules, fonts, prompt; restarts worker + webhook
# 5. the next episode's Phase 2 builds its cards (Step 12b) -> standup line, or one alert
scripts/rerender-cards.sh             # 6. back catalogue: build Plex sets with Drive's captions -> review _sheet.jpg
scripts/rerender-cards.sh --push      # 7. replace; old sets to Drive's trash (30 days)
```

**First run of this deploy path is a named risk.** Step 4 is the first deploy that creates directories
(`quote_cards/`, `BrandAssets/fonts/ibm-plex/`) and the first time Chromium runs on the box. What the
rehearsals did not test: the box's own Chromium and system libraries, its memory under a real render,
the real Drive. If Step 12b fails it alerts and Phase 2 finishes; the fix is `new-cards.sh N` on the Mac.

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 2, 3, 4 | renderer PR #23: Plex font gate (6 faces, per-weight), E29 end to end; 13 + 24 tests |
| 5, 6 | `test_card05_phase2.py`: real titles/captions/render/upload scripts in subprocesses with a fake model and Drive — full set built; existing `cards/` skipped; a failure sends one alert and returns |
| 6a | `rerender-cards.sh` rehearsed against a fake box: captions byte-identical (incl. a hand edit), reviewed titles reused, 15 trashed / 20 uploaded |
| 6b | the reel path is untouched (Step 12b runs only when not `reels_only`; no reel file changed) |
| — | full suite 17/17 (live-key and path-guarded tests excluded as before) |
