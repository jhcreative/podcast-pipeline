# OBS-01 — One run ledger: every step's time and outcome, every model call's tokens and cost

**Owner:** Bill (impl) · **Event schema:** Jen · **Review:** Toby · **Security:** Orly (C-4 applies) · **Tests:** Jen · **PO:** Angela
**Priority:** Medium · **Status:** **Proposed** — drafted 2026-10-10 by Claude at John's request, for Angela's RTS pass; not Ready to Start (Q1–Q3 open)
**Created:** 2026-10-10 · **Raised by:** John · **Feeds:** EVAL-01, EVAL-02, DASH-01

---

## Why

The pipeline can't answer three questions today:

- **What does an episode cost** in model spend?
- **Is the transcript cache working?** The brand-team transcript block is marked
  `cache_control: ephemeral` (`anthropic_client.py:98-112`) so calls 2–4 read it from cache. Nothing
  confirms they do.
- **Where does an episode's time go**: transcription, alignment, reel render or upload?

Checked 2026-10-10: no first-party Python reads `response.usage`. A grep for `usage`, `input_tokens`,
`output_tokens` and `cache_read` returns zero hits. The only run record is at job level:
`worker._log_run_event` appends to `state/run_log.jsonl` (`worker.py:228`), and `/status` returns the
last 20 job rows (`webhook_service.py:118`). A job is one row whether Phase 2 took 4 minutes or 40, and
whether or not Step 12b retried.

EVAL-02 ("should we change models?") and DASH-01 ("show it") both depend on this record. It is also the
evidence John needs for his case study: *"an episode costs $X in model spend, and here is where the
time goes"* is a sentence he can't say today.

## Scope

1. **Model-call events.** Every `messages.create` writes one event. There are nine call sites today:
   seven in `anthropic_client.py` (lines 239, 337, 426, 473, 556, 603, 669) and two in `run_dr_joe.py`
   (96, 140). **One wrapper** in `anthropic_client.py` records the call; no call site records itself by
   hand (PQ-4, one definition).
2. **Step events.** Every named step in `jobs/round_1.py` and `jobs/publish_finalize.py` (Step 1 to
   Step 16, including 7a-0, 9b, 9c, 10.5 and 12b) records its start and its end with an outcome: `ok`,
   `skipped` (with the reason), `gate_failure` (with the gate's name), `retry` or `permanent_failure`.
   One context manager, not sixteen hand-written pairs.
3. **Same file, new event types.** Events append to the existing `state/run_log.jsonl` as
   `event: "model_call"` and `event: "step"`, next to today's job events. The box's `state/` is already
   the live record (README), so there is nothing new to back up.
4. **Cost is computed from one price table.** A single `PRICES` table (per model ID: input, output,
   cache write and cache read, per million tokens) lives in one module, with the date and the URL it
   was copied from. Changing a price is a one-line edit. Bill fills it from Anthropic's pricing page
   at build time; **this story deliberately quotes no prices.**
5. `scripts/deploy.sh` `FILES` gains any new module (the DEPLOY-01 guard catches a miss).

**Out of scope:** WhisperX compute (a fixed cost of the box, not a per-call one); any hosted tracing
vendor (Q1); charts (DASH-01).

## Event shape (Jen owns it; no field is added without her)

```json
{"ts":"…Z","event":"model_call","job_id":88,"episode":"E34","step":"round_1.brand_team.robert",
 "model":"claude-sonnet-4-6","prompt":"brand_team_robert.md","prompt_sha":"…",
 "input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,
 "stop_reason":"end_turn","latency_ms":0,"attempt":1,"outcome":"ok","cost_usd":0.0}

{"ts":"…Z","event":"step","job_id":88,"episode":"E34","phase":"publish_finalize","step":"12b",
 "name":"quote_cards","outcome":"ok","detail":"","duration_ms":0}
```

- `prompt_sha` is the SHA-256 of the prompt file as loaded. EVAL-02 groups results by it; without it,
  "which prompt version produced this?" is a guess.
- **Never record** the transcript, a prompt body, a response body or any credential. Orly's C-4 from the
  BLOG-02 review applies unchanged: lengths and hashes only.

## Acceptance criteria

1. Every model call writes exactly one `model_call` event per attempt, including calls that raise, hit
   `max_tokens` or are retried. *Test:* a fake client returns canned `usage`; the full brand-team chain
   writes 4 events carrying those numbers and the right `step` names.
2. A call that raises still writes its event (`outcome` set, token counts null) and then re-raises
   unchanged. The worker's retry and `PermanentFailure` behaviour is exactly what it was.
3. Every named step writes a `step` event with its duration and outcome. A `GateFailure` records
   `outcome: "gate_failure"` with the gate's name. A skipped step says why (`"cards/ not empty"`,
   `"reels_only"`).
4. `cost_usd` matches a hand calculation from `PRICES` on a fixed fixture. Jen checks the arithmetic
   once by hand, and after that it's an assertion.
5. **A ledger write that fails (disk full, bad permissions) logs a warning and never fails the job.**
   Observability must not become a new way to strand an episode (the R1-01 failure class).
6. Sentinel test (C-4 pattern): a sentinel string placed in the transcript and in a fake response
   appears in no ledger line.
7. One real episode on the box produces a complete ledger: every step is present, and the episode's
   cost is readable with the one `jq` line in the runbook.

## Decisions (open — John)

| # | Question | Recommendation |
|---|---|---|
| Q1 | JSONL on the box, or a hosted tracer (Langfuse, LangSmith, Braintrust)? | **Answered (John, 2026-10-10): plain JSONL.** One box, one user, and every dependency earns its place. The event shape maps onto OpenTelemetry spans if a tracer is ever wanted, so the data carries over. |
| Q2 | Backfill estimated costs for E23–E33 from transcript lengths? | **Answered (John, 2026-10-10): yes, backfill, labelled as an estimate from the old Sonnet.** See the John's answers section below. Original recommendation: **No.** Start clean at the next episode. An estimate in a ledger is non-actual data (Jen, Bill). |
| Q3 | `run_dr_joe.py`: is it still in the live path? | Bill checks. If it's a manual tool, wrap it anyway; it costs one import. |

## Verification (Jen)

| AC | Evidence |
|---|---|
| 1, 2, 4, 6 | `test_obs01_ledger.py`: a fake Anthropic client with canned `usage`, run through the brand-team chain, the title and the summary. Asserts counts, names, arithmetic and that the sentinel is absent |
| 3, 5 | Same file: a `GateFailure` fixture, and a read-only ledger path |
| 7 | The first live episode: the runbook's `jq` output pasted here, with the date and the sha |

## Shipping it (John runs each line; agents don't touch the box)

```bash
cd ~/RockstarPodcast
bash scripts/deploy.sh --check
bash scripts/deploy.sh
# after the next episode, on the box: model spend for that episode
jq -s '[.[]|select(.event=="model_call" and .episode=="E34")|.cost_usd]|add' state/run_log.jsonl
```

---

## RTS pass — Angela, 2026-10-10 (with Toby, Bill, Jen, Orly)

**Corrections from the code audit (Toby, Bill):**
- `run_log.jsonl` today is **failure-only**: `_log_run_event`'s only caller is `worker.py:203`, on retries
  exhausted. Successes and `PermanentFailure` never write. "Next to today's job events" overstates it.
- `round_1` step names include **11.5 and 11.6** (the title gate); `publish_finalize` has **7a** as well as 7a-0, and
  Step 5 is retired. The step list in Scope 2 is amended to match.
- **Q3 answered:** `run_dr_joe.py` is manual, not imported, not deployed, and pins its own model (`:97`, `:141`). Wrap
  it; SHARE-01 moves it to `tools/` and reads the model from `_MODEL`.
- A missing brand-team prompt is skipped with only a warning (`anthropic_client.py:196-198`) and never reaches
  `report["skipped"]`. The ledger records it as `outcome: "skipped"` with the member's name.
- `pipeline/run_log.jsonl` on the Mac is a stale v1 log with a different schema. It is not the box's `state/` file.

**Amendments adopted (Jen; John, 2026-10-10: "I want what Jen wants"):**
- AC-8: every event carries `run_kind` ∈ {`prod`, `eval`, `replay`}; episode cost sums `prod` only.
- AC-9: every `model_call` carries `prompt_sha` **after include expansion** (PROMPT-01), `request_template_sha`
  (user-message template, metadata suffix, `max_tokens`, `temperature`), and `response.model`.
- AC-7 adds: brand-team calls 2–4 show `cache_read_input_tokens`; when it is 0 the runbook output prints
  `CACHE MISS`. Toby expects a miss: see **CACHE-01**.
- No call sets `temperature` (default 1.0). It is recorded, not changed, in this story.

**Gate status:** RTS-1 pass after the amendments · RTS-2 pass (event shape is the contract; Jen owns it) · RTS-3 needs
PROMPT-01 Part 1 first (for `prompt_sha`) · RTS-4 needs **EVAL-00**'s strategy doc · RTS-6 pass (C-4 sentinel in
AC-6). Q1, Q2: recommendations stand, waiting on John.

## John's answers, 2026-10-10

**Q2: backfill E23–E33, labelled as an estimate from the old Sonnet.** John overrules the "no backfill"
recommendation. The guardrails below keep an estimate from ever passing as a measurement (PQ-5, Jen and Bill):

- **Separate record.** Estimates go to `state/cost_estimates.jsonl`, never to `run_log.jsonl`. Each row carries
  `"kind": "estimate"`, `"model": "claude-sonnet-4-6"`, `"method"`, the code sha it was computed on, and the date.
- **Method.** Input tokens are **counted, not guessed**: the free `messages.count_tokens` endpoint, run on each
  episode's real transcript and the prompts as they stand, with the same brand-team, title, summary and FAQ structure.
  Output tokens come from each artifact's stored length, so they are approximate. Cache: none assumed (CACHE-01: the
  code couldn't hit). Prices: the `PRICES` row for 4.6.
- **Known error.** Prompts changed between E23 and E33. The estimate uses today's prompts, so it's a model of each
  episode, not a record of it. The method line says so.
- **AC-10:** `pipeline_report` and the runbook's `jq` line never add estimates to measured cost. *Test:* a fixture with
  both kinds; the measured total excludes the estimates.
- **AC-11:** wherever an estimate is shown (DASH-01, CASE-01), it is labelled *"estimate, Sonnet 4.6, method …"* and
  drawn differently from measured points.
- **AC-12:** the first measured episode is compared with the estimate the same method gives for it. That gap is the
  estimate's stated error, and every display of an estimate shows it.

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
- **AC-12 amended:** the estimate's error comes from the 6 measured cycles (mean and range), not from one episode.
  Estimates for E23–E33 are written only after that.

---

## Built — Bill, 2026-10-10 (branch `feat/obs-01`)

**What changed:**
- **`run_ledger.py`** (new; in deploy FILES, which the PROMPT-01 guard demanded):
  - `record()` appends to `state/run_log.jsonl` and **never raises** (AC-5).
  - `create()` is the **one** way to call the model. It writes one `model_call` event per call, including a call
    that raises (outcome `error`, tokens null), and then re-raises the same exception.
  - `PRICES` are dated 2026-10-10 and sourced from the pricing page. An unknown model costs `None`, never 0.
  - `StepClock` with `begin`/`step`/`skip`/`skipped`/`end`: one line per step heading, no re-indenting.
- **What each event carries:**
  - every event: `job_id`, `episode`, `run_kind` (`prod` / `eval` / `replay` / `manual`);
  - every `model_call` additionally: `prompt`, `prompt_sha` (the system text actually sent), `request_template_sha`
    (the request's shape with every text replaced by its length), `response_model`, `temperature` (null = the API
    default, recorded and not changed), `max_tokens`, `attempt`.
- **Step events carry names only, never an exception's message** (C-4):
  - a gate failure records the `content_gates` function that raised, found by walking past the
    `PermanentFailure` that publish_finalize wraps around it;
  - a skip records a fixed reason from our own code.
- **`anthropic_client.py`:** all 7 calls go through `run_ledger.create`. A missing brand-team prompt is now a
  `skipped` step event and lands in `report["skipped"]`; before, it was a log line only.
- **`run_dr_joe.py`:** both calls go through it with `run_kind: manual`, and the tool uses `anthropic_client._MODEL`
  rather than its own copy of the model name (so EVAL-02's switch reaches it).
- **`worker.py`:** every job is bound (job id, episode, `prod`), its step clock starts, and the clock is closed with
  the job's outcome.
- **Both jobs:**
  - **Step lines:** 18 in Round 1, 22 in Phase 2 (including 7a, 7a-0, 9b, 9c, 10.5, 11.5, 11.6, 12b and TRIG-03's
    2b route).
  - **Rebinding:** each job rebinds the episode once it knows it.
  - **Skip reasons:** `reels_only`, `cards/ not empty`, `Heather is the guest`, `calendar event off (CAL-OFF)`,
    `no calendar_event_id`.
- **`scripts/ledger_summary.py E34`** (AC-7, runbook): spend per call site and the total (`prod` only), brand-team
  cache use with **CACHE MISS** flagged on calls 2–4, and every step's duration and outcome. It prints how many lines
  it read and matched; no events means "not a cost of $0", exit 1.

**Evidence, 2026-10-10:**
- `python3 -B test_obs01_ledger.py` → **12 passed, 0 failed**:
  - the four brand-team events with their canned usage and names;
  - no call bypasses the ledger (all 9 counted);
  - a raising call recorded and re-raised as the same object;
  - every step outcome, including a gate named through the PermanentFailure wrapper;
  - the worker closes the job's clock;
  - a skipped member recorded;
  - cost by hand, (1000×3 + 2000×3.75 + 3000×0.30 + 500×15)/1e6 = **$0.0189**, and an unknown model → None;
  - an unwritable ledger never fails the call;
  - the **sentinel**, placed in the transcript, the response, the metadata and a gate's message, appears in no
    ledger line, checked over 6+ events;
  - `run_kind` and binding;
  - the template sha changes with structure (a block moved into `system`, `max_tokens`), not with which model.
- **Mutation checks:** without the cause walk, the wrapped gate reads `permanent_failure` (11 of 12); with the
  message in the detail, 3 tests fail, the sentinel test among them (9 of 12).
- The rest of the suite matches `main`; test_blog02b and test_prompt01 both caught `run_ledger.py` missing from
  FILES before it was added. The manifest is OK.

**After the deploy (John):** the first real episode → `venv/bin/python scripts/ledger_summary.py E<N>` on the box →
its output pasted here (AC-7), with the date and sha. That output answers CACHE-01 step 1 and starts the 6
measured cycles.

**Cold review of `68a2ebe`, 2026-10-10: Toby approved with changes; Jen blocked on 1, 2 and 3; Orly approved (C-4
holds).**

| # | Finding | Resolution |
|---|---|---|
| 1 | `request_template_sha` hashed text lengths, so it differed on every call | It hashes only the request's **shape**: every text is a constant, and keys, `cache_control` and `max_tokens` are kept. The test now asserts that two episodes give the same sha. |
| 2 | "Sonnet 5.5's cache read is $0.20" | **Not accepted, with evidence:** the live pricing page (2026-10-10) gives **$0.10**. Footnote 2: Opus 5.5 and Sonnet 5.5 cache hits are 0.05× input. The $0.20 came from an older cached reference. **Accepted:** Haiku 5.5's higher price above 100K prompt tokens is now applied. |
| 3 | The summary's CACHE MISS numbering crossed jobs, and unpriced spend was hidden | Grouped per job; only Robert, Dr. Joe and Lisa can miss. An answered call with no price row prints **"!! … NOT in the total"**. |
| 4 | Steps closed `ok` that did nothing | Now skipped, with reasons: `waiting for visuals` (Phase 2, step 3); `not a late email for a finished episode` (2b); `reels_only` (steps 11, 15, 16); `transcript_fast.json not found` (7a); `.pipeline_done already present` (Round 1, step 3). ~~Round 1's step 2 has no silent return: an empty inbox raises.~~ **Wrong (re-review):** an empty inbox returns quietly. It is now `skipped("inbox empty")`. A skipped member gets its own step id, `10.<member>`. |
| 5 | The SDK's own retries are hidden | **Jen's decision:** one event per `create()`. `latency_ms` is wall time **including** the SDK's retries and backoff; `sdk_max_retries` is recorded on every call. A per-HTTP-attempt record would need the SDK's internals. |
| 6 | Ctrl-C during a call wrote no event | `except BaseException`, tested |
| 7 | 1-hour cache writes priced as 5-minute | **Jen's decision:** `usage.cache_creation.ephemeral_1h_input_tokens` is recorded as `cache_write_1h_tokens` and priced at the 1h rate. Every write today is 5-minute, so nothing changes until a 1h TTL is set. |
| 8 | Manual runs counted as `prod`; the Mac wrote toward `/opt` | `rerun_brand_team.py` binds `run_kind: manual`. Off the box, the default ledger is the repo's own (gitignored) `state/run_log.jsonl`. |
| 9 | Step-name shape | The `model_call` step names the **call site** (`brand_team.robert`); the phase lives on the step events and the job id ties them together. The story's example `round_1.brand_team.robert` is superseded. AC-7 and AC-10 (estimates) have no test yet: AC-7 is the first live episode, AC-10 lands with the backfill. |

**Evidence:**
- `test_obs01_ledger.py` → **15 passed**: the earlier 12 plus an interrupt being recorded, the laptop default path,
  and the summary's per-job flags with unpriced spend.
- The cost test adds the 1h-write and Haiku-tier hand calculations.
- The rest of the suite matches `main`. The manifest is OK.

**Re-review of `d105ba5`: no blockers; Jen's 1–3 resolved; the $0.10 price confirmed against the live page (row and
footnote quoted). Fixed:**
- **The table's false claim:** an empty inbox now records `skipped("inbox empty")`, and the row is corrected above.
  Also skipped: `no reels rendered` (10.5) and `no schedule entry for this episode` (15).
- **The shape hashes only free text** (`text`, `system`, string `content`). Enum-like values (`type`, `role`,
  `ttl`) stay verbatim, so a cache TTL change is a new template. C-4 holds: no free-text field survives.
- **New tests:** plain-string content is stable across episodes; a TTL change alters the sha; cache reads count
  toward Haiku's 100K threshold; 1-hour writes read from the SDK's `usage.cache_creation` and priced through
  `create()`.
- `ledger_summary.py` reads from the same place the writer writes (`run_ledger._path()`). An error event carries
  `cache_write_1h_tokens: null`, the same schema as an ok one.

**Evidence:**
- `test_obs01_ledger.py` → **16 passed**.
- **Mutation checks:** `content` not treated as text → the plain-string test fails; the threshold ignoring cache →
  the cost test fails.
- The rest of the suite matches `main`.

**Re-review 2 (`76d5e57`): approve with changes from all three. Done:**
- **C-4, before CACHE-01:** `prompt_sha` hashes only system blocks **without** `cache_control`, so the cached
  transcript is never part of it. Tested with two transcripts: the same sha, and the sentinel absent.
- **C-4, by allowlist:** `_shape` keeps verbatim only the setting keys `type`, `role`, `ttl` and `media_type`. Every
  other string becomes `<str>`. Tested: tool `description` and `metadata.user_id` with the sentinel never reach the
  hash.
- **The TTL test can fail now:** it compares `5m` with `1h`. Blanking setting values fails it.
- **Step 10.5 with zero reels is an `anomaly`, not a skip.** The defect behind it (Step 12 replacing good Drive reels
  with an empty folder) is **REEL-10**, filed.
- **Tested now:** `ledger_summary` reads where the ledger writes. The three `skipped()` calls are checked by review
  only; an integration test comes with EVAL-03's replay.

`test_obs01_ledger.py` → **18 passed**.

**Final review of `cef07b5`: all three approve.** Item 1 was made a CACHE-01 precondition, and is done now rather
than deferred:
- **`prompt_sha` no longer guesses which block is the prompt.** It hashes the string `system` (every call site
  today), or the `prompt_text` the caller passes. A block-list system without it records `None`. Tested in both
  cache layouts, `[transcript(cached), prompt]` and `[transcript, prompt(cached)]`: the transcript never reaches the
  hash. **CACHE-01 must pass `prompt_text`.**
- **Tested now:** a block type change (text → document) is a new template.
- **Step 10.5:** zero reels from quotes is the `anomaly`; no quotes at all is `skipped("no quotes, so no reels")`, as
  REEL-10 AC-3 calls normal.
- **Noted for later:** `name`, `service_tier`, `effort` and `betas` join `_ENUM_KEYS` when the first of them is
  used.

`test_obs01_ledger.py` → **18 passed**.
