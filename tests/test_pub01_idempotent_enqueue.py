"""
test_pub01_idempotent_enqueue.py — PUB-01 idempotent publisher-email trigger.

Run: python3 test_pub01_idempotent_enqueue.py   (plain asserts, no pytest needed)

Covers PUB-01 acceptance criteria A (idempotent enqueue + DB-level guarantee),
B (mark-read tombstone, fired once on first insert, non-fatal), and C (migration
& back-compat).

Every test runs against a THROWAWAY sqlite db under a temp dir — never the live
/opt/rockstar-pipeline/state/jobs.db. We point db.DB_PATH at the temp file via
the DB_PATH env override (read at import time in db.py) and importlib.reload so
each case gets a clean, isolated DB (PQ-7: clean state, no test depends on
another's output).

The criterion-B test drives Bill's REAL webhook_service.gmail_webhook handler.
fastapi, gmail_client and the drive_* modules pull in heavy I/O deps that aren't
installed in the test env, so we stub those modules AT THE BOUNDARY in sys.modules
before importing webhook_service — gmail_client.mark_read is the one we count /
make raise. Everything the criterion is about (the `if inserted:` gate, the
non-fatal try/except, the 200 ack) is Bill's real code, exercised live.

Maps to story criteria:
  A1/A2  → test_idempotent_enqueue_same_key      (N calls, one row, same id)
  A3     → test_concurrency_db_level_guarantee   (two connections, one row)
  A4 / 8 → test_null_key_passthrough             (dedup_key=None → always inserts)
  7      → test_migration_old_schema             (additive, history-preserving)
  5/6    → test_markread_fires_once_and_nonfatal  (B: once-on-insert + non-fatal)
"""

import asyncio
import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import types
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pub01_"))
_REAL_DB = "/opt/rockstar-pipeline/state/jobs.db"


def _fresh_db(name: str):
    """
    Point db at a clean temp file named `name` and return the (re)loaded module.

    db.DB_PATH is captured from the DB_PATH env var at import time, so we set the
    env var and importlib.reload to re-read it. Guarantees an isolated DB per
    test (PQ-7). Refuses point-blank to ever touch the live server DB.
    """
    path = _TMP / name
    if path.exists():
        path.unlink()
    os.environ["DB_PATH"] = str(path)
    import db as _db
    importlib.reload(_db)
    assert str(_db.DB_PATH) != _REAL_DB, "test must NOT use the live DB"
    assert str(_db.DB_PATH).startswith(str(_TMP)), _db.DB_PATH
    return _db, path


def _rows(path: Path, where: str = "") -> list[sqlite3.Row]:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(f"SELECT * FROM jobs {where} ORDER BY id").fetchall()
    finally:
        con.close()


# ── A1 / A2 — idempotent enqueue: N calls, same key → one row, same id ────────


def test_idempotent_enqueue_same_key():
    db, path = _fresh_db("idempotent.db")
    db.init_db()

    key = "19e7018dabe1d91f"  # a real offending message_id from the bug report
    ids = [
        db.enqueue("PUBLISH_FINALIZE", "TBD", {"trigger": "gmail"}, dedup_key=key)
        for _ in range(5)
    ]

    rows = _rows(path)
    assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}"
    assert rows[0]["dedup_key"] == key, rows[0]["dedup_key"]
    assert len(set(ids)) == 1, f"expected one stable id across 5 calls, got {ids}"
    assert ids[0] == rows[0]["id"], (ids[0], rows[0]["id"])
    # A2: the email's read-state is irrelevant — we never read/modify Gmail in
    # enqueue, so dedup holds purely on the DB key regardless of mark-read.
    print(f"  A1/A2 idempotent enqueue: 5 calls → 1 row, id={ids[0]} stable ✓")


# ── A3 — DB-level guarantee: two separate connections, same key → one row ─────


def test_concurrency_db_level_guarantee():
    db, path = _fresh_db("concurrency.db")
    db.init_db()

    key = "19edd73ebafa7312"
    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def worker(tag: str):
        # Each thread → its own db._conn() (its own sqlite connection), as the
        # webhook does. Barrier makes them race the INSERT near-simultaneously,
        # so a passing result proves the *partial UNIQUE index* enforces single
        # insertion, not app-level check-then-insert.
        try:
            barrier.wait(timeout=5)
            results[tag] = db.enqueue(
                "PUBLISH_FINALIZE", "TBD", {"trigger": "gmail"}, dedup_key=key
            )
        except Exception as e:  # capture so we can assert on it, not crash silently
            results[tag] = e

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    for tag, r in results.items():
        assert not isinstance(r, Exception), f"thread {tag} raised: {r!r}"

    rows = _rows(path)
    assert len(rows) == 1, f"DB constraint failed: expected 1 row, got {len(rows)}"
    assert results["a"] == results["b"] == rows[0]["id"], (results, rows[0]["id"])
    print(f"  A3 concurrency: 2 racing connections → 1 row, both got id={rows[0]['id']} ✓")


# ── A4 / 8 — NULL-key passthrough: dedup_key=None → every call inserts ────────


def test_null_key_passthrough():
    db, path = _fresh_db("nullkey.db")
    db.init_db()

    # ROUND_1 / manual reprocess carry no dedup identity → behave exactly as
    # before: a fresh row per call, even though the rows are otherwise identical.
    ids = [db.enqueue("ROUND_1", "TBD", {"trigger": "manual"}) for _ in range(4)]
    # explicit None should be identical to the default
    ids.append(db.enqueue("ROUND_1", "TBD", {"trigger": "manual"}, dedup_key=None))

    rows = _rows(path)
    assert len(rows) == 5, f"NULL keys must not dedup: expected 5 rows, got {len(rows)}"
    assert len(set(ids)) == 5, f"expected 5 distinct ids, got {ids}"
    assert all(r["dedup_key"] is None for r in rows), [r["dedup_key"] for r in rows]
    print(f"  A4/8 NULL-key passthrough: 5 calls → 5 rows, ids={ids} ✓")


# ── 7 — migration: old schema (no dedup_key) → additive, history-preserving ───


def _build_old_schema_db(path: Path) -> None:
    """
    Reconstruct the PRE-PUB-01 jobs table (no dedup_key column, no dedup index)
    and seed a couple of history rows, to stand in for the live server DB.
    """
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type    TEXT    NOT NULL,
            episode_id  TEXT    NOT NULL,
            payload     TEXT    NOT NULL DEFAULT '{}',
            status      TEXT    NOT NULL DEFAULT 'pending',
            attempt     INTEGER NOT NULL DEFAULT 0,
            next_run_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            error       TEXT
        );
        CREATE INDEX idx_jobs_poll ON jobs (status, next_run_at)
            WHERE status IN ('pending','running');
        """
    )
    con.execute(
        "INSERT INTO jobs (job_type, episode_id, status) VALUES ('ROUND_1','E25','done')"
    )
    con.execute(
        "INSERT INTO jobs (job_type, episode_id, status) VALUES "
        "('PUBLISH_FINALIZE','E26','failed')"
    )
    con.commit()
    con.close()


def test_migration_old_schema():
    db, path = _fresh_db("migrate.db")
    # Overwrite the path with an OLD-schema DB carrying real history.
    _build_old_schema_db(path)
    pre = _rows(path)
    assert len(pre) == 2 and "dedup_key" not in pre[0].keys(), "fixture setup wrong"

    # The migration under test.
    db.init_db()

    # history intact (same 2 rows, same ids, unchanged)
    post = _rows(path)
    assert len(post) == 2, f"history lost: expected 2 rows, got {len(post)}"
    assert [r["id"] for r in post] == [r["id"] for r in pre], "row ids changed"
    assert [r["job_type"] for r in post] == ["ROUND_1", "PUBLISH_FINALIZE"]

    # dedup_key column added; old rows get NULL
    # (PRAGMA table_info: row[1] is the column name)
    cols = {r[1] for r in sqlite3.connect(str(path)).execute("PRAGMA table_info(jobs)")}
    assert "dedup_key" in cols, "migration did not add dedup_key column"
    assert all(r["dedup_key"] is None for r in post), "old rows must have NULL dedup_key"

    # idx_jobs_dedup index present
    idx = {
        r[0]
        for r in sqlite3.connect(str(path)).execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_jobs_dedup" in idx, f"dedup index missing; have {idx}"

    # old rows are EXEMPT from the constraint: two NULL-key inserts both succeed
    a = db.enqueue("ROUND_1", "E27")
    b = db.enqueue("ROUND_1", "E27")
    assert a != b, "NULL-key rows wrongly deduped after migration"

    # but a real dedup_key is now enforced on the migrated DB
    k = "post-migration-key"
    j1 = db.enqueue("PUBLISH_FINALIZE", "E27", dedup_key=k)
    j2 = db.enqueue("PUBLISH_FINALIZE", "E27", dedup_key=k)
    assert j1 == j2, "dedup not enforced after migration"

    # second init_db() is a no-op (idempotent migration): no schema churn, and the
    # row count is whatever we have now (5: 2 history + 2 null + 1 deduped pair).
    count_before = len(_rows(path))
    db.init_db()
    assert len(_rows(path)) == count_before, "second init_db() changed data"
    cols2 = {r[1] for r in sqlite3.connect(str(path)).execute("PRAGMA table_info(jobs)")}
    # ALTER must not have run twice (would error); column set unchanged.
    assert cols2 == cols
    print(
        f"  7 migration: history intact (2 rows), dedup_key+index added, "
        f"old rows exempt, re-run no-op ✓"
    )


# ── 5 / 6 — criterion B: mark-read fires once on first insert, non-fatal ───────


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — .body() and .headers are what the handler reads."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._body


def _load_webhook_with_stubs(mark_read_impl):
    """
    Import the REAL webhook_service.gmail_webhook with only its I/O-boundary
    modules stubbed in sys.modules. Returns (webhook_module, fake_gmail_client).

    What's real: webhook_service.gmail_webhook (the code under test) and db
    (enqueue_dedup against the temp DB). What's faked: fastapi, gmail_client,
    drive_changes, drive_client, publisher_email — the external edges. mark_read
    is `mark_read_impl` so a test can count calls or make it raise.
    """
    saved = {
        name: sys.modules.get(name)
        for name in (
            "fastapi", "gmail_client", "drive_changes", "drive_client",
            "publisher_email", "webhook_service",
        )
    }

    # fastapi: the handler only needs FastAPI() (with .post/.get/.on_event
    # decorators that pass functions through) and a Response carrying status_code.
    fastapi = types.ModuleType("fastapi")

    class _App:
        def __init__(self, *a, **k):
            pass

        def _passthrough(self, *a, **k):
            return lambda fn: fn

        post = get = on_event = _passthrough

    class _Response:
        def __init__(self, status_code: int = 200):
            self.status_code = status_code

    fastapi.FastAPI = _App
    fastapi.Response = _Response
    fastapi.Request = _FakeRequest
    fastapi.Header = lambda *a, **k: None
    fastapi.HTTPException = type("HTTPException", (Exception,), {})

    # gmail_client: feed the handler exactly one matching publisher message and
    # provide a parse_pubsub_notification that yields a non-empty notification.
    gc = types.ModuleType("gmail_client")
    msg_id = "19ed1689f95595b1"
    gc.MSG_ID = msg_id  # exposed so the test knows the key in play
    gc.parse_pubsub_notification = lambda body: {"emailAddress": "me", "historyId": "42"}
    gc.list_unread_messages = lambda query, max_results: [
        {"id": msg_id, "threadId": "t1"}
    ]
    gc.get_message = lambda mid: {"id": mid}
    _headers = {"subject": "Your Episode is Live | X", "from": "notices@legacy-publisher.example.test",
                "in-reply-to": ""}
    gc.get_header = lambda msg, name: _headers.get(name, "")
    gc.mark_read = mark_read_impl

    # publisher_email: predicates that pass the one message through to enqueue.
    pe = types.ModuleType("publisher_email")
    pe.gmail_from_query = lambda: "from:(@legacy-publisher.example.test)"
    pe.is_publisher_sender = lambda sender: True
    pe.is_publish_notification = lambda subject, in_reply_to="": True
    pe.authenticated_sender = lambda headers: (True, "stub: authentic")  # TRIG-03 AC-10; tested in test_trig03

    # drive_* are imported at module top but unused by the gmail handler.
    drive_changes = types.ModuleType("drive_changes")
    drive_client = types.ModuleType("drive_client")

    sys.modules.update(
        fastapi=fastapi, gmail_client=gc, publisher_email=pe,
        drive_changes=drive_changes, drive_client=drive_client,
    )
    sys.modules.pop("webhook_service", None)
    import webhook_service  # noqa: E402 — imported under the stubs above
    importlib.reload(webhook_service)
    return webhook_service, gc, saved, msg_id


def _restore_modules(saved: dict) -> None:
    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class _CountingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def test_markread_fires_once_and_nonfatal():
    db, path = _fresh_db("markread.db")
    db.init_db()

    # We swap mark_read's behaviour between the two phases of the test.
    calls: list[str] = []
    raise_now = {"on": False}

    def mark_read(message_id: str):
        calls.append(message_id)
        if raise_now["on"]:
            raise RuntimeError("simulated Gmail 500 on modify")

    webhook_service, gc, saved, msg_id = _load_webhook_with_stubs(mark_read)
    log = _CountingHandler()
    logging.getLogger(webhook_service.__name__).addHandler(log)
    body = b'{"message": {"data": "x"}, "subscription": "projects/p/subscriptions/s"}'

    try:
        # webhook_service imported `db` before our reload pointed it at the temp
        # file. Rebind the handler's db reference to the temp-DB module so the
        # real enqueue_dedup writes to the throwaway DB, not the live one.
        webhook_service.db = db
        # Bypass the Pub/Sub subscription allowlist assertion (env-specific).
        webhook_service._assert_pubsub_push = lambda authorization, env: None

        # ---- Phase 1: first delivery → inserts → mark_read fires exactly once ----
        resp = asyncio.run(webhook_service.gmail_webhook(_FakeRequest(body)))
        assert resp.status_code == 200, resp.status_code
        assert len(_rows(path)) == 1, "first delivery should insert one job"
        assert calls == [msg_id], f"mark_read should fire once on insert, got {calls}"

        # ---- Phase 2: redelivery (same key) → suppressed → mark_read NOT called ----
        resp = asyncio.run(webhook_service.gmail_webhook(_FakeRequest(body)))
        assert resp.status_code == 200
        assert len(_rows(path)) == 1, "redelivery must not insert a second job"
        assert calls == [msg_id], (
            f"mark_read must NOT fire on redelivery (inserted=False), got {calls}"
        )

        # ---- Phase 3: a NEW message whose mark_read RAISES → non-fatal ----
        new_id = "19edd73ebafa7312"
        gc.list_unread_messages = lambda query, max_results: [
            {"id": new_id, "threadId": "t2"}
        ]
        gc.get_message = lambda mid: {"id": mid}
        raise_now["on"] = True
        log.records.clear()
        resp = asyncio.run(webhook_service.gmail_webhook(_FakeRequest(body)))
        # Non-fatal: still a 200 ack, and the job was still enqueued.
        assert resp.status_code == 200, "mark_read failure must not break the 200 ack"
        new_rows = _rows(path, f"WHERE dedup_key = '{new_id}'")
        assert len(new_rows) == 1, "job must still enqueue even when mark_read raises"
        # ...and the failure was logged at WARNING (criterion 6 / D).
        warnings = [r for r in log.records if r.levelno == logging.WARNING
                    and "mark_read failed" in r.getMessage()]
        assert warnings, "mark_read failure must be logged at WARNING"
        assert new_id in warnings[0].getMessage(), warnings[0].getMessage()
    finally:
        logging.getLogger(webhook_service.__name__).removeHandler(log)
        _restore_modules(saved)

    print(
        "  5/6 criterion B: mark_read fires once on insert, NOT on redelivery, "
        "and a mark_read failure is non-fatal (200 ack, job enqueued, warning logged) ✓"
    )


# ── runner (plain asserts, repo convention — no pytest dependency) ────────────


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    tests = [
        test_idempotent_enqueue_same_key,
        test_concurrency_db_level_guarantee,
        test_null_key_passthrough,
        test_migration_old_schema,
        test_markread_fires_once_and_nonfatal,
    ]
    failed = 0
    skipped = 0
    print("PUB-01 idempotent enqueue — test run")
    for t in tests:
        try:
            if t() == "skipped":
                skipped += 1
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # unexpected error is also a failure
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    total = len(tests)
    print(
        f"\n{total - failed - skipped} passed, {failed} failed, {skipped} skipped "
        f"(of {total})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
