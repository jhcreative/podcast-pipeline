# CACHE-01 — The brand-team transcript cache can't hit as built

**Owner:** Bill (impl) · **Measure:** Jen · **Review:** Toby · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — Angela, 2026-10-10; **ships after OBS-01**, so the fix is measured, not assumed
**Created:** 2026-10-10 · **Raised by:** Toby and Bill (story audit) · **Needs:** OBS-01 · **Feeds:** EVAL-02 (cost), CASE-01

---

## Why

`_transcript_user_block` (`anthropic_client.py:98-112`) marks the transcript `cache_control: ephemeral`, and its
docstring says *"identical across all four Brand Team calls, so the API caches the KV after the first call and reuses
it for calls 2–4."*

It can't. Each call sends a **different `system=`** (the member's prompt, `:194` → `:242`). The cache is a prefix
match in render order, `tools` → `system` → `messages`, so a different system prompt changes the prefix ahead of the
transcript. Each of the four calls therefore **writes** the cache (1.25× input on Sonnet 4.6) and none reads it. The
caching we believe saves money most likely costs a little extra.

Read from the code, not yet measured. OBS-01 records `cache_read_input_tokens`; if it is 0 on calls 2–4 of the first
ledgered episode, this story is confirmed. That makes it the first defect the measurement work finds — the
case-study example of why you instrument before you optimize (CASE-01).

## Scope

1. **Measure first.** One OBS-01 ledger from a live episode. Paste the four brand-team `cache_*` numbers here.
2. **Fix (if confirmed).** Put what is shared first: the transcript goes into `system` as the first block, with
   `cache_control`, and the member's role prompt follows as a second system block. Calls 2–4 then share the
   transcript prefix.
3. **This changes what the model sees** (order of instructions and transcript). It runs through the EVAL-01
   regression set against the 4.6 baseline before it ships. Equal within the noise rule (EVAL-00) means ship.
4. Fix the docstring to say what the code does.

## Acceptance criteria

1. Before the fix: the ledger shows the measured `cache_read_input_tokens` for calls 2–4 (expected 0), pasted here with the episode and date.
2. After the fix, with a fake client: the request bodies of calls 2–4 share a byte-identical prefix through the
   transcript block. *Test:* serialize the four requests and compare prefixes.
3. After the fix, live: calls 2–4 show `cache_read_input_tokens` > 0, and the episode's brand-team cost falls
   (OBS-01 runbook line, before/after).
4. **Non-inferiority, not "no difference" (EVAL-00 re-review):** on the regression set, the upper bound of the paired
   95% CI for "worse" stays inside a margin Jen writes down **before** the run, and the gates' pass rate doesn't drop.
   At 20 episodes the minimum detectable effect is ≈ 0.66 SD, so a "no detectable difference" alone proves nothing
   about equivalence.

## Decisions

None open. If step 1 shows reads > 0, close this story as "not a defect", with the numbers.

## Measured cycles before any change (John, 2026-10-10)

> *"we should run a few cycles before we change models so we can measure with the new tools, then project backwards"*

- **The cycles.** Once OBS-01 and EVAL-01 A (the draft snapshot) are live, the pipeline runs **6 live episodes
  unchanged**: Sonnet 4.6, today's prompts, today's request structure. Six is Jen's floor for a live human-edit
  baseline (6–8); cost needs fewer. John may set a different number.
- **Project backwards.** The backfill method (OBS-01) is run on those same 6 episodes and compared with what was
  measured. The gap, given as a mean and a range over 6 episodes rather than 1, is the estimate's stated error. Only
  then is it applied to E23–E33.
- **Nothing changes during the cycles.** No model switch (EVAL-02), no prompt change (PROMPT-01 Part 2 is
  byte-identical by its AC-4, so it may land), no request-structure change (the CACHE-01 fix waits; its *measurement*
  is the cycles themselves). A change mid-cycle restarts the count, and the plan says so on the day it happens.
- **Amended order:** step 1 (measure) **is** the cycles: all 6 show the four brand-team cache numbers. The fix in
  step 2 ships after cycle 6, then goes through the regression set as written.
