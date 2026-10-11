# ADR 0007 — Music credit is appended in code; hashtags capped at 3–4 in the prompt

- **Status:** Accepted
- **Date:** 2026-06-25
- **Deciders:** John (owner — set both rules); Angela (PO — content + prompt wording);
  Toby (tech director — deterministic-vs-prompt design call, review, deploy); Jen (tests — CAP-01 follow-up)
- **Story:** CAP-01 — caption-rule assertions (the test half of this decision)
- **Lands in:** `main` @ `712c452` (PR #1), deployed + verified 2026-06-25.

## Context

Backfilling missing reel captions for E23/E25/E26/E27/E28 surfaced two standing
changes to the Instagram caption format that John wants applied now and going forward:

1. **A music attribution credit on every Instagram caption.** The reel theme is
   "Show Me" by Daze, licensed via Uppbeat, whose terms require a specific
   attribution string: `🎵 from #uppbeat https://uppbeat.io/t/real-daze/show-me`.
2. **Far fewer hashtags.** The prompt produced 15–20 on Instagram (5–8 LinkedIn,
   8–12 YouTube). To this audience — GenX women, skeptical of hype — a wall of tags
   reads as spam. John wants the best 3–4, every platform.

The open design question was *where* each rule lives: prompt or code.

## Decision

**Hashtag cap → prompt.** Choosing the 3–4 strongest, most relevant tags per reel is
a judgment call the model should make with the quote in front of it. Encoded as a
single hashtag policy in `prompts/seo_captions.md` covering all three platforms.

**Music credit → code, not the prompt.** The credit is a license-attribution string
that must be reproduced *exactly*. A model asked to write it will eventually paraphrase
the URL, drop or swap the emoji, or reflow the line — a silent compliance drift we'd
never see until Uppbeat did. So `publish_finalize.py` Step 11 appends the credit
deterministically after the model's Instagram text:

- **Idempotent** — appended only if not already present, so re-runs and reprocesses
  never double it.
- **Env-overridable** — `IG_MUSIC_CREDIT` (default = the Uppbeat string). A future
  track is a config change, not a code change (BP-7), mirroring the publisher-drift
  and transcribe-path patterns.
- The prompt is told **not** to write its own credit, so the two never fight.

This is the honest-systems call: the part that must be exact is made exact by
construction, not by trusting generation.

## Consequences

- The exact credit ships on every IG caption, forever, regardless of model behavior.
- The hashtag rule is a *soft* (model-guided) constraint — it needs a generation-time
  assertion so a future caption that emits 5+ tags is caught. That's **CAP-01** (Jen).
  Until then the blast radius is cosmetic (one over-tagged caption), not broken output.
- **Deploy-FILES gap fixed in passing:** `prompts/seo_captions.md` was never in
  `deploy.sh` FILES — it had reached the server once and silently stopped being
  deploy-managed. Without the fix this whole ADR would have half-deployed (credit
  append lands, prompt change does not). Added to FILES; server confirmed no prior
  drift. See the carry-forward's impediment note — this is the third FILES omission.
- The 24 backfilled captions were authored by hand to these exact rules, so the
  current Drive state already matches what the pipeline will now produce.
