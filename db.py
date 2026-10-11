"""
db.py — SQLite WAL-mode job queue for the Rockstar Pipeline.

Schema: one `jobs` table. A single worker process polls this table for
pending work. WAL mode lets other readers (health endpoint, status page)
query without blocking the writer.

Job lifecycle:  pending → running → done | failed
Retry:          exponential back-off managed by worker.py;
                db.py only stores state, never re-schedules.
"""

import contextlib
import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", "/opt/rockstar-pipeline/state/jobs.db"))

# ── schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type    TEXT    NOT NULL,
    episode_id  TEXT    NOT NULL,
    payload     TEXT    NOT NULL DEFAULT '{}',
    status      TEXT    NOT NULL DEFAULT 'pending',
    attempt     INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT    NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_at  TEXT    NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT    NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    error       TEXT,
    -- Stable identity of the trigger that produced this job (e.g. a Gmail
    -- message_id). NULL for triggers that carry no dedup identity (ROUND_1,
    -- manual reprocess, drive-resume). Only non-NULL keys are deduplicated —
    -- see idx_jobs_dedup below.
    dedup_key   TEXT
);

-- Covering index for the worker's polling query: only scans pending/running
-- rows ordered by next_run_at, so the full-table scan on a large history
-- table never blocks.
CREATE INDEX IF NOT EXISTS idx_jobs_poll
    ON jobs (status, next_run_at)
    WHERE status IN ('pending', 'running');

-- Idempotency guard (PUB-01). A *partial* UNIQUE index: it constrains only
-- rows where dedup_key IS NOT NULL, so the database itself refuses a second
-- job for the same trigger identity — even under concurrent webhook calls.
-- Pre-existing history (all dedup_key NULL) is exempt, so this is safe to add
-- to the live jobs.db without touching a single old row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup
    ON jobs (dedup_key)
    WHERE dedup_key IS NOT NULL;
"""

# ── connection ─────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _conn():
    """
    Yield a WAL-mode connection. Commits on clean exit, rolls back on error.

    check_same_thread=False is safe here because every call opens and closes
    its own connection; we never share a connection across threads.
    """
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL lets the health endpoint read while the worker writes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """
    Additive, history-preserving migrations for an EXISTING jobs.db.

    The live DB on the server predates the `dedup_key` column (PUB-01), and
    `CREATE TABLE IF NOT EXISTS` will not add a column to a table that already
    exists. So we add it here, idempotently, BEFORE _SCHEMA's partial UNIQUE
    index is created (that index references the column and would error if it
    were missing).

    Migrations are additive only: we never drop or rewrite existing rows.
    Old rows keep dedup_key = NULL and are exempt from the UNIQUE index by
    its `WHERE dedup_key IS NOT NULL` clause — existing history is untouched.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if not cols:
        # Brand-new DB: no `jobs` table yet. Nothing to migrate — _SCHEMA's
        # CREATE TABLE builds the current shape (dedup_key included) whole.
        return
    if "dedup_key" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN dedup_key TEXT")
        logger.info("migration: added jobs.dedup_key column (PUB-01)")


def init_db() -> None:
    """Create tables + indexes if they don't exist. Safe to call on every startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        # Migrate an existing DB up to the current column set first, then run
        # the full schema script (which is all IF NOT EXISTS) to create any
        # missing tables/indexes — including the dedup partial UNIQUE index.
        _migrate(conn)
        conn.executescript(_SCHEMA)
    logger.info("db initialised at %s", DB_PATH)


# ── write ──────────────────────────────────────────────────────────────────────


def enqueue(
    job_type: str,
    episode_id: str,
    payload: dict | None = None,
    dedup_key: str | None = None,
) -> int:
    """
    Insert a new pending job and return its id.

    dedup_key is the stable identity of the trigger (e.g. a Gmail message_id).
    When supplied, enqueue is idempotent: if a job already exists for that key,
    no new row is inserted and the EXISTING job's id is returned. The guarantee
    is enforced by the partial UNIQUE index idx_jobs_dedup — two concurrent
    callers with the same key cannot both insert.

    dedup_key=None (the default — ROUND_1, manual reprocess, drive-resume)
    behaves exactly as before: every call inserts a fresh row.

    This is the thin facade most callers want — it returns just the id. A caller
    that must distinguish a fresh insert from a suppressed duplicate (e.g. the
    gmail webhook, which marks the trigger email read only on the FIRST insert)
    calls enqueue_dedup() instead, which exposes that signal.
    """
    job_id, _inserted = enqueue_dedup(job_type, episode_id, payload, dedup_key)
    return job_id


def enqueue_dedup(
    job_type: str,
    episode_id: str,
    payload: dict | None = None,
    dedup_key: str | None = None,
) -> tuple[int, bool]:
    """
    Same as enqueue(), but returns (job_id, inserted).

    inserted is True when this call created the row, False when an existing job
    already owned dedup_key and the INSERT was suppressed. The webhook uses this
    to fire its one-time side effect (mark-read) only on the inserting call —
    PUB-01 criterion B — and never on a redelivery.
    """
    with _conn() as conn:
        # ON CONFLICT DO NOTHING makes the duplicate INSERT a no-op rather than
        # raising IntegrityError. We then read back the surviving row's id so
        # the caller always gets a real job id to log/act on.
        cur = conn.execute(
            """
            INSERT INTO jobs (job_type, episode_id, payload, dedup_key)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
            """,
            (job_type, episode_id, json.dumps(payload or {}), dedup_key),
        )
        if cur.rowcount == 1:
            job_id = cur.lastrowid
            logger.info(
                "enqueued job %d type=%s episode=%s", job_id, job_type, episode_id
            )
            return job_id, True

        # rowcount == 0 → the INSERT was suppressed by the dedup conflict. Find
        # the job that already owns this key and return it (criterion A1 + D).
        existing = conn.execute(
            "SELECT id FROM jobs WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()

    if existing is None:
        # Defensive: a conflict with no findable row should be impossible.
        # Fail loudly rather than return a bogus id — PQ-5, no silent lie.
        raise RuntimeError(
            f"enqueue: dedup conflict on {dedup_key!r} but no existing row found"
        )
    logger.info(
        "enqueue: duplicate trigger %s → existing job %d",
        dedup_key,
        existing["id"],
    )
    return existing["id"], False


def mark_done(job_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='done', "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (job_id,),
        )
    logger.info("job %d → done", job_id)


def mark_failed(job_id: int, error: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (error[:4096], job_id),
        )
    logger.warning("job %d → failed: %s", job_id, error[:200])


def reschedule(job_id: int, attempt: int, next_run_at: str, error: str) -> None:
    """
    Return a job to pending with a future next_run_at. Called by the worker's
    retry handler after a transient failure.
    """
    with _conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status='pending', attempt=?, next_run_at=?, error=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id=?
            """,
            (attempt, next_run_at, error[:4096], job_id),
        )
    logger.info("job %d → retry attempt=%d next=%s", job_id, attempt, next_run_at)


# ── read ───────────────────────────────────────────────────────────────────────


def claim_next() -> sqlite3.Row | None:
    """
    Atomically claim the next due pending job by transitioning it to 'running'.
    Returns the full row or None if the queue is empty / nothing is due yet.

    BEGIN IMMEDIATE acquires a write lock before the SELECT, preventing two
    worker threads from claiming the same row if we ever scale to N workers.
    (Currently we run one worker process, so this is defensive.)
    """
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'pending'
              AND next_run_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
            ORDER BY next_run_at ASC
            LIMIT 1
            """,
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE jobs SET status='running', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (row["id"],),
            )
    return row


def reclaim_stale(stale_minutes: int = 30) -> int:
    """
    On worker startup: jobs that are still 'running' after stale_minutes are
    almost certainly orphans from a previous crash. Reset them to pending so
    the retry logic picks them up fresh.

    30 minutes is conservative: no job step should take more than ~15 min
    (whisperx large-v3 on 5 clips ≈ 8–12 min on CX22).
    """
    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status   = 'pending',
                error    = 'reclaimed after crash / restart',
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE status = 'running'
              AND updated_at < strftime(
                    '%Y-%m-%dT%H:%M:%SZ', 'now',
                    '-' || ? || ' minutes'
                  )
            """,
            (stale_minutes,),
        )
        count = cur.rowcount
    if count:
        logger.warning("reclaimed %d stale running jobs (likely crash recovery)", count)
    return count


def get_status_counts() -> dict[str, int]:
    """Return {status: count} for the /status health endpoint."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def get_recent_jobs(limit: int = 20) -> list[dict]:
    """Return the most recent N jobs as plain dicts (for the status page)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
