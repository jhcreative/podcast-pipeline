"""
worker.py — job queue consumer for the Rockstar Pipeline.

Polls the SQLite jobs table every 10 seconds.  On startup, reclaims any
jobs stuck in 'running' (crash recovery).

Retry schedule (exponential back-off):
  attempt 0 → fail → retry in  60s
  attempt 1 → fail → retry in   5m
  attempt 2 → fail → retry in  30m
  attempt 3 → fail → retry in   2h
  attempt 4 → fail → permanently failed

Max 5 attempts total (attempts 0–4).

The worker is intentionally simple: one process, one thread, no concurrency.
SQLite WAL handles concurrent reads from the webhook service and status page
without blocking the worker's writes.

Job handlers live in jobs/.  Each handler receives the full job row and is
responsible for its own Drive/Gmail/Anthropic calls.  If a handler raises
any exception, the worker retries.  If a handler returns normally, the job
is marked done.  A job that runs out of retries sends one internal alert (CLIP-01a).
"""

import json
import logging
import os
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from rockstar import config
from rockstar.service import db

logger = logging.getLogger(__name__)

# ── config ─────────────────────────────────────────────────────────────────────

_POLL_INTERVAL_SEC = int(os.environ.get("WORKER_POLL_INTERVAL", "10"))
_MAX_ATTEMPTS = int(os.environ.get("WORKER_MAX_ATTEMPTS", "5"))

# Retry delays in seconds, indexed by attempt number (0-based).
# attempt 0 fail → wait 60s before attempt 1
# attempt 3 fail → wait 7200s before attempt 4
# attempt 4 fail → mark permanently failed (no attempt 5)
_RETRY_DELAYS = [60, 300, 1800, 7200, 21600]

_STALE_JOB_MINUTES = int(os.environ.get("WORKER_STALE_JOB_MINUTES", "30"))

# ── job dispatch ───────────────────────────────────────────────────────────────

# Lazy import handlers to avoid import-time failures if a dep is missing.
# Each handler module is loaded only when a job of that type is claimed.


def _dispatch(job_type: str, job: sqlite3.Row) -> None:
    """
    Route a job row to its handler.  Raises on failure (worker retries).
    """
    payload = json.loads(job["payload"])
    episode_id = job["episode_id"]

    # OBS-01: every event from this job carries its id and episode; the job's steps run on one clock, closed
    # here with the job's outcome. The ledger never raises, so it can't change what the job does.
    from rockstar.ops import run_ledger

    run_ledger.bind(job_id=job["id"], episode=episode_id, run_kind="prod")
    run_ledger.begin({"ROUND_1": "round_1", "PUBLISH_FINALIZE": "publish_finalize"}.get(job_type, job_type.lower()))
    try:
        _run_handler(job_type, job, payload, episode_id)
    except BaseException as exc:
        run_ledger.end(exc)
        raise
    run_ledger.end()


def _run_handler(job_type: str, job: sqlite3.Row, payload: dict, episode_id: str) -> None:
    if job_type == "ROUND_1":
        from rockstar.jobs.round_1 import run

        run(job_id=job["id"], episode_id=episode_id, payload=payload)

    elif job_type == "PUBLISH_FINALIZE":
        from rockstar.jobs.publish_finalize import run

        run(job_id=job["id"], episode_id=episode_id, payload=payload)

    else:
        raise ValueError(f"unknown job type: {job_type!r}")


# ── permanent failure helper ───────────────────────────────────────────────────


def _permanent_failure_types():
    """
    Return a tuple of exception types that should never be retried.
    Imported lazily so job modules aren't loaded at worker startup.
    Each job module that defines a PermanentFailure class registers it here.
    """
    types = []
    for module_path, cls_name in [
        ("rockstar.jobs.round_1", "PermanentFailure"),
        ("rockstar.jobs.publish_finalize", "PermanentFailure"),
    ]:
        try:
            import importlib

            mod = importlib.import_module(module_path)
            cls = getattr(mod, cls_name, None)
            if cls is not None:
                types.append(cls)
        except ImportError:
            pass
    return tuple(types) if types else (type(None),)  # type(None) never matches


# ── retry scheduling ───────────────────────────────────────────────────────────


def _next_run_at(attempt: int) -> str:
    """
    Return an ISO-8601 UTC timestamp for when to next run a failed job.
    attempt is the attempt number that just FAILED (0-based).
    """
    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
    when = datetime.now(timezone.utc) + timedelta(seconds=delay)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── main loop ──────────────────────────────────────────────────────────────────

_running = True


def _handle_sigterm(signum, frame):
    global _running
    logger.info("received SIGTERM — finishing current job then exiting")
    _running = False


def main():
    logging.basicConfig(
        level=config.log_level(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    logger.info("worker starting (poll_interval=%ds, max_attempts=%d)", _POLL_INTERVAL_SEC, _MAX_ATTEMPTS)

    db.init_db()

    # Crash recovery: reset jobs that were 'running' when the process died.
    reclaimed = db.reclaim_stale(_STALE_JOB_MINUTES)
    if reclaimed:
        logger.warning("reclaimed %d stale jobs on startup", reclaimed)

    while _running:
        job = db.claim_next()

        if job is None:
            time.sleep(_POLL_INTERVAL_SEC)
            continue

        _run_one(job)

        # Short sleep to avoid a tight loop when many jobs are queued
        time.sleep(0.5)

    logger.info("worker exiting cleanly")


def _run_one(job) -> None:
    """Run one claimed job and record its outcome: done, rescheduled, or failed for good."""
    job_id = job["id"]
    job_type = job["job_type"]
    attempt = job["attempt"]  # already incremented by claim_next? No —
    # claim_next sets status='running' but doesn't increment attempt.
    # We track attempt here: current attempt = job["attempt"] + 1 for logging,
    # but we increment in the reschedule/fail calls below.

    current_attempt = attempt  # 0-based, first run = 0
    logger.info(
        "claiming job %d type=%s episode=%s attempt=%d",
        job_id,
        job_type,
        job["episode_id"],
        current_attempt,
    )

    try:
        _dispatch(job_type, job)
        db.mark_done(job_id)
        logger.info(
            "job %d done (type=%s episode=%s attempt=%d)",
            job_id,
            job_type,
            job["episode_id"],
            current_attempt,
        )

    except _permanent_failure_types() as exc:
        # PermanentFailure from a job handler: standup already written,
        # skip retries entirely and mark the job failed immediately.
        error_str = f"{type(exc).__name__}: {exc}"
        db.mark_failed(job_id, error_str)
        logger.error(
            "job %d permanently failed (no retry) — standup updated: %s",
            job_id,
            error_str,
        )

    except Exception as exc:
        next_attempt = current_attempt + 1
        error_str = f"{type(exc).__name__}: {exc}"
        logger.error(
            "job %d failed attempt=%d: %s",
            job_id,
            current_attempt,
            error_str,
        )

        if next_attempt >= _MAX_ATTEMPTS:
            # Exhausted all retries — permanently fail the job.
            db.mark_failed(job_id, error_str)
            logger.error(
                "job %d permanently failed after %d attempts",
                job_id,
                next_attempt,
            )
            _alert_dead_job(job, next_attempt, error_str)
            _log_run_event(
                job_id=job_id,
                job_type=job_type,
                episode_id=job["episode_id"],
                status="failed",
                error=error_str,
                attempt=current_attempt,
            )
        else:
            next_run = _next_run_at(current_attempt)
            db.reschedule(job_id, next_attempt, next_run, error_str)
            logger.info(
                "job %d rescheduled for %s (attempt %d of %d)",
                job_id,
                next_run,
                next_attempt,
                _MAX_ATTEMPTS,
            )


# ── the one alert for a job that ran out of retries (CLIP-01a) ────────────────


def _alert_dead_job(job, attempts: int, error: str) -> None:
    """E13 (2026-10-12) failed five times and went dead with no word to anyone; only the box's logs knew.
    One internal alert when retries run out. Best-effort: a failed alert must not stop the worker."""
    from rockstar.ops import run_ledger

    step = run_ledger.failed_step() or "(outside a step)"
    # Jobs are enqueued as "TBD" (Round 1, Gmail-triggered Phase 2); the job binds the real code once it knows it.
    episode = run_ledger.episode() or job["episode_id"]
    subject = f"{episode}: {job['job_type']} failed for good after {attempts} attempts — step {step}"
    body = (
        f"Job {job['id']} ({job['job_type']}, episode {episode}) ran out of retries.\n\n"
        f"Step: {step}\nAttempts: {attempts}\nLast error: {error[:600]}\n\n"
        "Nothing retries it now. Fix the cause, then re-run the episode.\n"
    )
    try:
        from rockstar.clients import gmail_client

        gmail_client.send_pipeline_alert(to=config.alert_email(), subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001
        logger.exception("job %d: could not send the dead-job alert", job["id"])
        run_ledger.alert_failed("worker.dead_job", subject, exc)


# ── run log ────────────────────────────────────────────────────────────────────


def _log_run_event(**kwargs) -> None:
    """Append a JSONL entry to state/run_log.jsonl."""
    import json as _json

    log_path = config.run_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **kwargs,
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(entry) + "\n")


# ── entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
