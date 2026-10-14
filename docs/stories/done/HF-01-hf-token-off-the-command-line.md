# HF-01 — The Hugging Face token never rides on a command line

**Owner:** Bill · **Tests:** Jen · **Security:** Orly · **Box:** Oliver · **PO:** Angela · **Priority:** High · **Size:** S
**Status:** Built, deploy waits for E32 Round 1 to finish · **Created:** 2026-10-01 · **Raised by:** Angela, John

---

## What happened

During E32's Round 1 (2026-10-01, job 126), a `ps` listing on the pipeline box showed WhisperX's full command
line, **including `--hf_token hf_…` in clear**. `transcribe_fast.sh:202` (and `transcribe_episode.sh:117`)
passed the token as an argument. On Linux, argv is world-readable for the whole run (`ps`,
`/proc/<pid>/cmdline`), so any user or process on the box could read it, and it's now in a session
transcript. John, 2026-10-01: *"lets get it fixed asap then I'll rotate token after we confirm it wont recur"*.

## Fix

- Both scripts `export HF_TOKEN` (already sourced from `ENV_FILE`) and no longer pass `--hf_token`.
- Verified on the box: whisperx **3.8.5** with no `--hf_token` passes `token=None` to pyannote **4.0.4**,
  and huggingface_hub **0.36.2** then reads `HF_TOKEN` from the environment.
- A process environment is readable only by its own user and root.

## Acceptance criteria

- **AC-1:** no script in `scripts/` passes the token as an argument (static check; comment lines skipped).
- **AC-2:** the real `transcribe_fast.sh`, run end to end against a stub whisperx, gives the stub `HF_TOKEN` in
  its environment and never in its argv. The token appears in no output or left-behind file.
- **AC-3:** a missing token still exits 4 before whisperx runs.
- **AC-4:** (box, after deploy) the next real transcription succeeds with diarization. While it runs, a check
  of **WhisperX's argv only**, count-based and never printing the line, shows `0` occurrences of `hf_`.
- **AC-5:** John rotates the token **after AC-4 passes**. Oliver installs the new value file to file into the box's
  env, without displaying it, and AC-4's count check is run again on the next transcription.

**Tests:** `test_hf01_token_off_cmdline.py`: red on the old scripts (2 of 3 fail, naming both lines), green on
the fix (3 of 3).

## Deploy (Oliver)

- **Only when no job is pending or running**, after E32's Round 1 has finished. Overwriting a bash script
  while it runs can corrupt that run.
- `deploy.sh` ships both scripts (they are in FILES). **Every `deploy.sh` run restarts the worker and webhook**,
  because it copies every file in FILES each time and any `.py` among them sets `NEED_RESTART` (`deploy.sh:103`).
  A restart kills a running job, so the idle check is required.

## Follow-ups (not in this story)

- **HF-02 (Orly):** `HF_TOKEN` lives in the shared `/opt/rockstar-pipeline/.env`, which every unit loads,
  including the public `rockstar-webhook`. Same shape as Orly's C-10/N-1 for the Tailor credential: the token
  should be worker-only (a `LoadCredential` or a worker-only env file), masked from the webhook.
- **Debugging rule (Oliver):** on the box, list processes with `ps -eo pid,etime,comm` (names only) or filter
  argv through a redaction. Never print full command lines of pipeline jobs into a shared transcript.

## Deployed and verified, 2026-10-01 (Oliver)

- **Merged** as PR #7 (`9a08cd0`) on John's go. Deployed after E32's Round 1 (job 126) finished and the idle
  check showed 0 jobs: `local==git==deploy OK`. The box's hashes match the repo (`401837e0a344`
  transcribe_fast.sh, `2bd4edb34308` transcribe_episode.sh); each script has 0 `--hf_token` code lines and one
  `export HF_TOKEN`.
- **AC-4: PASS.** The deployed `transcribe_fast.sh` ran as `rockstar` with the worker's environment, on a 20 s
  generated clip, in a scratch work dir under `/var/tmp`, removed afterwards. Counts only, never a value:
  7 samples of the WhisperX process (owner `rockstar`, exe = venv python, argv contains `bin/whisperx`):
  **`hf_` in argv 0, `--hf_token` 0, `HF_TOKEN` in its environment 1/1**. The run ended `SUCCESS`, so pyannote's
  gated diarization model loaded with the token from the environment.
- **Measurement note:** the first attempt matched with `pgrep -f "bin/whisperx"`. That also matched the root
  monitoring shell, whose own argv held the pattern text, so its counts were discarded. Sample by owner and exe.
- **Next, AC-5:** John rotates the token. Oliver installs the new value file to file into the box's `.env`
  without displaying it, then reruns this check.

## AC-5: rotated (2026-10-01): CLOSED

- **New token:** John created a Read token `rockstar-pipeline-2026-10` and saved it to
  `~/jhc/secrets/rockstarblends/prod-podcast-hf-token.env` (600, one key, shape-checked; never displayed).
- **Install (Oliver):** backed up to `scripts/.env.bak-20261001T163151Z-hf-rotate`. The file went to a root-only
  temp file over ssh stdin, the `HF_TOKEN` line in **`scripts/.env` only** was swapped (HF-02), owner and mode
  were kept, and the temp file was removed. Value changed: true. The shared `.env` still has 0.
- **Proof with the new token:** a real transcription; 7 WhisperX samples, `hf_` in argv 0, `HF_TOKEN` in its
  environment 7/7; `SUCCESS`.
- **John revoked the old token.** A second real transcription ended `SUCCESS`, so the pipeline depends on the
  new token alone. The token exposed in the 2026-10-01 session transcript is dead.
- **Lesson (the first install attempt):** a `python3 - <<PY` script can't also read the streamed secret on
  stdin, because the heredoc is stdin. The safety assertion refused and nothing changed. Land the stream in a
  root-only temp file, then read that file.
