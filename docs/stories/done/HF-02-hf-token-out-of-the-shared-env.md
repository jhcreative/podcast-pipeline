# HF-02 — The Hugging Face token leaves the shared `.env` (the public webhook never holds it)

**Owner:** Oliver · **Security:** Orly · **PO:** Angela · **Priority:** High · **Size:** XS (box config only)
**Status:** Done 2026-10-01 · **Created:** 2026-10-01 · **Follows:** HF-01

## What's wrong

`HF_TOKEN` is in **both** `/opt/rockstar-pipeline/.env` (loaded by every unit through `EnvironmentFile=`,
including the internet-facing `rockstar-webhook`) and `/opt/rockstar-pipeline/scripts/.env`. On 2026-10-01
the webhook's `/proc/<pid>/environ` held it (count 1). It's the same shape as Orly's C-10/N-1 for the Tailor
credential: a secret the webhook never uses is still exposed through it.

## Why removing it is enough

Only `scripts/transcribe_fast.sh` and `scripts/transcribe_episode.sh` read `HF_TOKEN`. They `source "$ENV_FILE"`,
and the worker unit sets `ENV_FILE=/opt/rockstar-pipeline/scripts/.env`. No Python module reads it from the
environment (`git grep HF_TOKEN`, 2026-10-01). The copy in the shared `.env` is redundant.

## Steps (Oliver, box idle)

1. `umask 077; cp -p -n /opt/rockstar-pipeline/.env /opt/rockstar-pipeline/.env.bak-<TS>-hf02` (fresh TS).
2. `sed -i '/^HF_TOKEN=/d' /opt/rockstar-pipeline/.env`. Then `grep -c '^HF_TOKEN=' …/.env` must print 0, and
   `…/scripts/.env` must still print 1. Owner and mode are unchanged.
3. Restart the worker and webhook (the R1-01 deploy does this).

## Acceptance criteria

- **AC-1:** `HF_TOKEN` count in the `environ` of the webhook and the worker is **0**.
- **AC-2:** the next transcription diarizes. WhisperX's own environment holds `HF_TOKEN` (from `scripts/.env`),
  and its argv holds no `hf_` (HF-01).
- **AC-3:** AC-5 of HF-01, the rotation, installs the new token into **`scripts/.env` only**.

## Done (2026-10-01, Oliver)

- **Backup:** `.env.bak-20261001T161514Z-hf02`.
- **Counts after the change:** shared `.env` `HF_TOKEN` 0; `scripts/.env` 1. `.env` is still `rockstar:rockstar 600`.
- **Deployed** with R1-01 (`254539e`): `local==git==deploy OK`.
- **AC-1: PASS.** `HF_TOKEN` count in `environ` is **0** for both the worker and the webhook.
- **AC-2: PASS.** A 20 s generated clip, run through the deployed `transcribe_fast.sh` with the worker's
  environment: 10 WhisperX samples, `hf_` in argv 0, `HF_TOKEN` in WhisperX's environment in every sample, and
  the run ended `SUCCESS` (pyannote loaded).
- **Next:** AC-3, John's rotation. The new token goes into `scripts/.env` only.
