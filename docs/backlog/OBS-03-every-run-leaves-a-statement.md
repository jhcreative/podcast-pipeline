# OBS-03 — Every run leaves a statement in Drive, success or failure

**Owner:** Bill (pipeline), Oliver (box) · **Tests:** Jen · **Security:** Orly · **Review:** Toby · **PO:** Angela
**Priority:** High: the eval sessions and CLIP-01a both read it · **Status:** **Proposed** (Angela, 2026-10-12)
**Created:** 2026-10-12 · **Raised by:** John: *"do we have/need a download or 'permanent record' step that drops a 'statement' somewhere?"*
**Lane:** with CLIP-01a, its own PR. CLIP-01a's exhausted-retry alert links to this statement.

## Why

Last night's E13 failure was proved from three places, and only one of them lasts:

| Record | Where | Lasts? |
|---|---|---|
| Run ledger | `/opt/rockstar-pipeline/state/run_log.jsonl` | Survives a deploy (not in `deploy.sh` FILES). **The only copy:** no backup, so a rebuilt or lost box loses it. |
| The OOM cause | journald only (`The kernel OOM killer killed some processes in this unit`) | **No.** It rotates away, and it's the only place the cause appears. The ledger recorded only `error (RuntimeError)`. |
| Each attempt's outputs (FAQ, draft, clips) | `/var/tmp/rockstar-work/…` | **No.** Deleted at the end of every job (MEM-01, by design). |

What we have came from a one-off hand pull (`~/RockstarPodcast-evaldata/`), and that copy stops before the final
attempt. MEM-01's June OOM evidence survived only because someone copied it into a story.

John's rulings, 2026-10-12:
- **Drive** is the permanent home (*"thats what I was thinking 💯"*).
- **Both** successful and failed runs get a statement.
- **Its own story**, in CLIP-01a's lane.
- **Round 1 and Phase 2** jobs both get statements; Drive keeps them **forever** (Q2, Q3).

The principle: **a run isn't over until its record is somewhere a person can read it after the box forgets.** It
carries the 2026-10-12 manual rule (every command leaves a file) into the pipeline itself.

## Acceptance criteria

### A. What a statement is

1. **One statement per job**, named `E<N>-job<id>-<job_type>.md`. Each attempt adds a section, so E13's job 147
   would have been one file with five attempt sections. It isn't five files.
2. **The top of the statement**, readable in ten seconds:
   - episode, job type, job id, `run_kind`, git SHA deployed (from the deploy's parity record)
   - the outcome: `done`, `retrying (attempt n of 5, next at …)`, or `failed permanently`
   - the cause where there is one, read from evidence, never guessed from an exit code (the same rule as CLIP-01a AC 12)
   - wall time, model spend, and a link to the episode's Drive folder
   - all times in UTC, labelled UTC
3. **The body:**
   - the job's ledger lines, formatted by the **same code as `scripts/ledger_summary.py`**: step table, spend by
     call site, cache use. Factor out the formatting so it isn't written twice.
   - the job's memory evidence, from the unit's cgroup (`memory.peak`, `memory.swap.peak`, `memory.events`
     `oom_kill`), which the worker reads about itself (AC 4).
   - a SHA256 and size for each output the job produced (FAQ, draft body, quotes.json, each clip, reel and card),
     so an eval session can tell which run an artifact came from (eval checklist item 2).
4. **The job's own log, with no journal access** (Orly, Q1). Nothing in this story grants the worker any new privilege.
   - **The worker's own lines.** At claim, the worker attaches a per-job log file handler, flushed per line, and detaches it at the end.
     It captures every line the job logs, including the stderr it collects from each subprocess. The file survives a kill up to its
     last line. The statement saves it **in full** below a filtered summary (the manual rule).
   - **An OOM inside a step.** When a step fails, the worker reads its own cgroup's `memory.events` (`oom_kill`),
     `memory.peak` and `memory.swap.peak` while the cgroup still exists. systemd's default `OOMPolicy=stop` sends
     SIGTERM after the kernel kill, and last night the handler still ran (`job 147 failed attempt=0`), so that window exists.
   - **The unit killed outright.** An `ExecStopPost=` line in `rockstar-worker.service` **without the `+` prefix** (so it
     runs as `rockstar`) appends systemd's own verdict (`$SERVICE_RESULT`, `$EXIT_CODE`, `$EXIT_STATUS`, time) to
     `state/statements/_unit-stops.jsonl`. The next worker start uses it for AC 6. Last night's verdict would have read
     `oom-kill` every time.
   - *Test:* a faked step failure with `oom_kill 1` in a fake `memory.events` gives the cause `OOM in step 8`, never
     `exit -15`. A `_unit-stops.jsonl` line with `oom-kill` and a job left `running` give the AC 6 section that names the OOM.

### B. When it's written

5. **On every attempt's end**: done, retry scheduled, or permanent failure. That's where `worker.py` already logs
   `job N done`, `rescheduled`, or `mark_failed`. It's triggered by the event, never by a timer (V-6).
6. **An attempt killed outright** (SIGKILL, or the unit killed before the handler returns) gets its section written by
   the next worker start: on startup the worker finds any job still marked running, writes `attempt n: worker died
   mid-job` with the cgroup evidence, then carries on.
   - *Test:* a job left `running` in a test DB gets that section on startup.
7. **The local file comes first.** It goes to `state/statements/` before any upload. A Drive failure never loses it.

### C. Where it goes

8. **Drive:** uploaded to a `records/` subfolder of the episode's Drive folder. It's created if missing, and each attempt
   updates the same file (`update_file`), never adds a duplicate.
   - If the episode has no folder yet (Round 1 failed before step 5), it goes to `Episodes/_records/`.
   - `records/` is machine output. ADR-0008 hand-edit precedence doesn't apply to it, and the story says so in one
     line so nobody ever treats it as an editable artifact.
9. **The upload can't fail the job.** A failed upload logs, adds `drive: upload failed (<reason>)` to the local
   statement, and is retried at the next attempt's end or the next worker start. The job's own outcome is unchanged.
   - *Test:* the Drive fake raises; the job still reports `done`, and the local file says the upload failed.
10. **The Mac pull:** `scripts/pull-record.sh E<N>` copies an episode's `records/` from Drive into
    `~/RockstarPodcast-evaldata/records/E<N>/` and prints the path and the top of each statement. It's read-only on Drive.

### D. Safe to keep (Orly)

11. **Nothing secret in a statement.** Before writing, every statement, **including the per-job log (AC 4)**, passes
    the same secret-pattern check SHARE-02 uses (API keys, tokens, `Authorization:`, `?key=` URLs, the HF token form),
    **plus IP addresses** (the box IP was in history 58 times). A match is redacted to
    `[redacted:<kind>]` and noted in the statement's top. *Test:* a planted fake key in a step's stderr comes out
    redacted.
12. **Who can see `records/`** (Orly's condition). Before the first upload, Bill lists the episode folder's Drive
    permissions. `records/` must be visible only to John, Heather and the pipeline's account. If any episode folder is
    shared wider (a guest, "anyone with the link"), statements go to `Episodes/_records/E<N>/` instead, and the story records
    which. `state/statements/` on the box is `0700 rockstar`.
13. **Private by default.** Statements name guests and quote transcripts, so they stay in Drive and the private
    evaldata folder and **never go into git**. `state/statements/` is gitignored. SHARE-02's allowlist already excludes `state/`;
    Orly confirms.

### E. Proof

14. **Local gate green**, with the existing exclusions. The 2 `google.auth` failures are named, not counted.
15. **Live proof:** the next real Phase 2 run (E34, or the E13 re-run under CLIP-01c) leaves a `done` statement in
    Drive `records/`, with the step table, spend and output hashes, and `pull-record.sh` brings it to the Mac.
    A failure statement is proved in tests (AC 4, 6, 9), not by breaking the box on purpose.

## Out of scope (named so nobody loses them)

- **A `state/` backup before every deploy** (the ledger, job DB and schedule), the same routine as Tailor's
  export at the swap (`deploy-tailor-vps`). Oliver, as its own story. Statements cover each run; the backup covers the box.
- Turning statements into dashboard charts (DASH-01 reads the ledger, and that stays the source).
- Backfilling statements for E6 and E13. The hand pull in `~/RockstarPodcast-evaldata/as-written-2026-10-11/`
  stands as their record.

## Open questions

| # | Question | Angela's recommendation | Answer |
|---|---|---|---|
| Q1 | Journal access: the worker runs as `rockstar`, and reading a system unit's journal needs `systemd-journal` group membership, which grants **every** system unit's log. Grant it, or capture the window another way? | Orly decides. | **Neither the group nor a root helper** (Orly, 2026-10-12). The worker writes its own per-job log, reads its own cgroup, and an unprivileged `ExecStopPost=` records systemd's verdict. See AC 4 and Orly's ruling below. |
| Q2 | Statements for Round 1 jobs too, or Phase 2 only? | Both. Round 1's transcribe ran 3,682–5,134 s last night, and that time belongs in the record. | **Both** (John, 2026-10-12) |
| Q3 | Retention in Drive | Keep forever. They're a few KB each, and the eval wants history. | **Forever** (John, 2026-10-12) |

## Orly's ruling on Q1 (2026-10-12, verbatim record)

> The worker is the most exposed process on the box. It takes guest audio, Drive files, inbound publisher mail
> and model output, and runs ffmpeg, Chromium and Node over them. I assume it is compromised.
>
> `systemd-journal` gives that process every unit's log: sshd authentication, sudo, the webhook service's
> requests. That's reconnaissance handed over for free. Denied.
>
> A `+`-prefixed (root) helper on the worker's lifecycle is a new privileged code path that a compromised worker can
> trigger. Denied.
>
> Neither is needed. Last night's cause sat in three places the worker can reach without privilege: its own
> stderr capture, its own cgroup's `memory.events`, and systemd's `$SERVICE_RESULT`, which `ExecStopPost=` hands to a
> process running as `rockstar`. Net new privilege: zero.
>
> Conditions: AC 11 covers the per-job log and IPs; AC 12's permission check runs before the first upload.
> A statement shared wider than John and Heather is a finding, not a setting.
