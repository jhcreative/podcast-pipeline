# ADR 0006 — Generate the title in Phase 1, gate it from a shared module

- **Status:** Accepted
- **Date:** 2026-06-23
- **Deciders:** John (owner, the decision); Angela facilitating; Bill (backend, impl); Toby (review/infra); Jen (tests)
- **Story:** TITLE-01 — Phase-1 title generation + shared placeholder gate
- **Builds on:** the title/guest gates added in `c9f1443` (which lived only in
  `publish_finalize.py`, Phase 5). This ADR hoists them earlier and shares them.

## Context

E29 (2026-06-23) completed Phase 1 (`round_1`) with `episode_title="TBD"`, bumped
the episode counter to E30, and wrote a `drafting` schedule entry — even though the
model had produced a perfectly good title. Two faults composed:

1. **The title was scraped from prose, not generated.** `round_1` took line 0 of
   the Apple-summary H1 and split on ` · `, using `parts[2]`. The model emitted a
   `*Pre-write note:*` line **above** the H1, so `split("\n")[0]` captured the note
   (which has no ` · `), the split failed, and the code fell back to its
   `episode_title = "TBD"` default. A prose scrape has a "line 0" that the wrong
   content can occupy.
2. **There was no gate in Phase 1.** The hard placeholder gate (`_assert_real_title`)
   existed only in `publish_finalize.py` (Phase 5) — too late: Phase 2 reels are
   already blocked by then, and its remedy was *manual* ("set the real title in
   `publication_schedule.json`"). So a `TBD` advanced silently.

A `TBD` title is not cosmetic: Phase 2 (reels) cannot run without a real title, so a
silent `TBD` advance blocks reels with no visible failure — exactly the kind of
flatter-by-deferring dishonesty PQ-5 forbids.

## Decision

**Generate the title as structured data in Phase 1, and enforce one shared
placeholder gate before anything irreversible.**

- **Dedicated, structured generation.** New `anthropic_client.compose_title(...)`
  returns `{"hook", "topic", "title"}` from a JSON-only model response (new
  `prompts/title.md`, house format `Hook | Topic`, BP-4 voice). `title` is
  *reassembled here* from `hook`/`topic`, so even a model that returns the parts
  but forgets the joined form still yields the canonical string. JSON has no
  "line 0" for preamble to occupy.
- **Preamble cannot corrupt the result.** `_parse_title_json` reuses the same
  robustness ladder `compose_seo_captions` uses: strip code fences (a regex that
  skips any leading note), then fall back to slicing from the first `{` to the last
  `}`. The exact E29 `*Pre-write note:* / --- / ```json` shape parses correctly
  under either pass (verified directly in tests, no live API).
- **Raise, never default.** `compose_title` raises on API error, on unparseable
  JSON, and on structurally-valid-but-empty (`hook`/`topic` missing). There is no
  silent `"TBD"` fallback anywhere in the title path (DOD-5). A title we cannot
  produce is a *failed job*, loudly.
- **One shared gate, two callers (PQ-4 DRY).** `_PLACEHOLDER_TITLE_RE`,
  `assert_real_title`, and `assert_guest_featured` are extracted **verbatim** into
  a new pure module `content_gates.py`. `round_1` (Phase 1) and `publish_finalize`
  (Phase 5) import the same definitions, so the same input fails identically in
  both phases. The placeholder regex is word-boundaried (`\b`) so a legitimate
  title containing a placeholder *stem* — "Pendulum", "Tkachenko", "Attkisson" —
  is not a false positive.
- **Gate before anything irreversible (criterion C).** `round_1` calls
  `assert_real_title` at step 11.6 — **before** the calendar event (step 13), the
  counter bump (step 14), and the schedule write (step 15). On failure the function
  returns nothing: the counter is untouched and no `drafting` entry is written, so
  no half-complete episode advances. A corrected reprocess re-enters the same gate
  and completes idempotently (criterion D).

### The GateFailure ↔ PermanentFailure split

`content_gates` is **phase-agnostic**, so it raises its own `GateFailure` — a
permanent failure by nature (retrying the same placeholder fails identically).
Each phase adapts that to its own retry contract:

- **`publish_finalize` translates explicitly.** Its `_assert_real_title` /
  `_assert_guest_featured` are now thin wrappers that catch `GateFailure` and
  re-raise `PermanentFailure`. The worker registers `publish_finalize.PermanentFailure`
  in its no-retry set, so a placeholder skips retries — and the **call sites are
  unchanged**, so existing Phase-5 behavior is byte-for-byte preserved.
- **`round_1` relies on its outer wrapper.** `round_1.run()` already wraps the whole
  pipeline: any exception is caught, written to standup, and re-raised as
  `round_1.PermanentFailure` (also in the worker's no-retry set). So a `GateFailure`
  from step 11.6 propagates up, gets re-wrapped, and the job fails permanently
  without retry. This is a deliberate, pre-existing Phase-1 stance ("a pipeline
  error is a code/data problem retrying won't fix, and retries risk picking up the
  wrong inbox file") — TITLE-01 inherits it rather than adding a second translation.

Keeping the gate definition phase-agnostic (its own exception type) and letting each
phase own the translation is what makes the single shared module safe to import in
two places with different retry semantics.

## Consequences

- **+** A `TBD` can no longer advance an episode. The title is generated, structurally,
  and gated at the earliest irreversible point. Phase 2 reels stop getting silently
  blocked.
- **+** One gate definition, two callers — drift between phases is now impossible.
- **+** Future title-source changes have no "line 0" to break; preamble is absorbed.
- **−** Phase 1 now makes an extra Claude call (`compose_title`) per episode. A title
  is tiny (`max_tokens=1024`) and the cost is negligible against the redundant-reel
  cost a `TBD` advance causes downstream.
- **−** `round_1`'s gate uses the broad outer wrapper rather than an explicit
  `GateFailure→PermanentFailure` translation like Phase 5. The behavior is correct
  (no retry), but the standup message is the generic "pipeline failed: …GateFailure…"
  rather than a title-specific one. Acceptable; noted as a review nit.
- **Deploy dependency:** `jobs/round_1.py`, the new `content_gates.py`, and
  `prompts/title.md` had to be **added to `scripts/deploy.sh`'s FILES list** —
  `round_1.py` and `content_gates.py` were not shipped at all before, so the fix
  could not reach the server. The two `.py` files trigger the worker/webhook
  restart so the new module is imported.

## Follow-ups (not in this change)

1. **TITLE-02 — "Brand Team quotes are blank" in `compose_apple_summary`** despite
   5 quotes existing (the E29 pre-write note's actual complaint). Suggests quotes may
   not be reaching the summary compose. Flagged for triage; not fixed here.
2. **Auto-capture `guest_name` in Phase 1** (currently null until manual) — would let
   `assert_guest_featured` run in Phase 1 too. Nice-to-have, deferred.
3. **Branch convergence on `main`.** This worktree is off `main` and does **not**
   carry PUB-01's `db.py` addition to `deploy.sh` (PUB-01 is on its own branch,
   deployed surgically, not yet merged). When TITLE-01 and PUB-01 land on `main`,
   `deploy.sh` must carry **both** the `db.py` line and the three TITLE-01 lines.
   Not reconciled here — flagged for whoever merges second.
4. **Test-harness debt (DOD-1).** `test_title01_gen_gate.py` has 2 pure-gate tests
   green; the other 7 fail on stub-harness issues (an `anthropic.Anthropic`-as-lambda
   stub trips the module-level `Anthropic | None` annotation; the `round_1`
   integration tests' `drive_client` stub omits `list_audio_in_inbox`). The
   *implementation* is verified correct by independent probes; the harness needs a
   fix before DOD-1 can be signed (Jen/Bill).
