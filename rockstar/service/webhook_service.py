"""
webhook_service.py — FastAPI webhook receiver for the Rockstar Pipeline.

Endpoints:
  POST /webhook/drive/changes  Drive changes-feed push: any file added/changed in Drive
  POST /webhook/gmail          Gmail Pub/Sub push notification: new email
  GET  /health                 Liveness check
  GET  /status                 Recent jobs + queue counts (JSON)

Drive detection (see drive_client changes-feed section for the full why):
  We watch the account-wide Drive *changes feed*, not individual folders.
  files().watch(<folder>) does not fire when a child is added — it only watched
  the folder's own metadata, which silently stranded E28 on 2026-06-10.  One
  changes.watch + a persisted page-token cursor replaces the old per-folder
  /drive/inbox and /drive/episode watches and self-heals across watch gaps.

Security (SEC-01 — the rules live in rockstar/service/webhook_auth.py, each failing closed):
  Drive webhooks carry X-Goog-Channel-Token = WEBHOOK_SHARED_SECRET (constant-time compare).
  Gmail webhooks carry Pub/Sub's OIDC token, verified against Google's certs, our audience and our
  push service account; the envelope's subscription name is checked as well.
  /status answers only the box itself (loopback, no proxy forwarding header): 404 to anyone else.
  A missing setting stops the service at start-up instead of switching a check off.
  Auth failures answer 401 rather than silently discarding — we want to know if someone is probing.

Run with:
  uvicorn webhook_service:app --host 127.0.0.1 --port 8000 --workers 1

Note: --workers 1 keeps SQLite's single-writer model simple.  The only
concurrent writes are webhook→enqueue (fast) vs worker→update (slower),
which WAL mode handles without contention.
"""

import json as _json
import logging
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request, Response

from rockstar import config
from rockstar.clients import drive_changes, gmail_client, publisher_email
from rockstar.service import db, webhook_auth

# The Drive changes-feed drain (cursor I/O, routing, resume-publish-finalize)
# lives in rockstar/clients/drive_changes.py — shared by this push webhook AND the poll timer
# (rockstar/service/poll_drive_changes.py), because the Drive push channel only lives ~40 min.

logger = logging.getLogger(__name__)

# ── config ─────────────────────────────────────────────────────────────────────

# Stripped, as missing_settings() strips: a trailing space in .env must not pass start-up and then
# refuse every request (cold review of PR #32).
_WEBHOOK_SECRET = os.environ.get("WEBHOOK_SHARED_SECRET", "").strip()
_PUBSUB_SUBSCRIPTION = os.environ.get("GMAIL_PUBSUB_SUBSCRIPTION", "").strip()
_PUSH_AUDIENCE = os.environ.get("PUBSUB_PUSH_AUDIENCE", "").strip()
_PUSH_SA_EMAIL = os.environ.get("PUBSUB_PUSH_SA_EMAIL", "").strip()
_DEV = webhook_auth.dev_mode()

# Publisher "Episode is Live" email detection (sender allowlist, reply guard,
# subject test) lives in rockstar/clients/publisher_email.py — one home for a drifting contract.

# ── application setup ──────────────────────────────────────────────────────────

app = FastAPI(title="Rockstar Pipeline Webhook Service", docs_url=None, redoc_url=None)


@app.on_event("startup")
def on_startup():
    logging.basicConfig(
        level=config.log_level(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    missing = webhook_auth.missing_settings()
    if missing and not _DEV:
        # SEC-01: refuse to run with a check switched off. uvicorn exits non-zero; systemd shows why.
        logger.error(
            "webhook service NOT started: %s unset — every check fails closed, so set them in "
            "/opt/rockstar-pipeline/.env (or PIPELINE_DEV=1 on a laptop, never on the box)",
            ", ".join(missing),
        )
        raise RuntimeError(f"required settings unset: {', '.join(missing)}")
    if _DEV:
        logger.warning("PIPELINE_DEV=1: webhook auth checks are OFF (laptop only)")
    db.init_db()
    logger.info("webhook service started")


# ── helpers ────────────────────────────────────────────────────────────────────


def _assert_drive_token(x_goog_channel_token: str | None):
    """
    Validate the shared secret on inbound Drive webhook calls.
    Drive sends X-Goog-Channel-Token with the value we supplied at
    watch-registration time.  Without this check, any HTTP client could
    enqueue arbitrary jobs by hitting our endpoint.
    """
    if _DEV:
        return
    if not webhook_auth.secret_matches(x_goog_channel_token, _WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="invalid channel token")


def _assert_pubsub_push(authorization: str | None, envelope: dict):
    """
    Validate that a Pub/Sub push came from OUR subscription (SEC-01).

    The proof is Pub/Sub's OIDC token in the Authorization header: Google-signed, for our audience,
    from our push service account. The envelope's subscription name is checked too — exactly, not by
    suffix — but the caller writes the body, so on its own it proved nothing.
    """
    if _DEV:
        return
    try:
        ok, why = webhook_auth.verify_pubsub_push(authorization, audience=_PUSH_AUDIENCE, sa_email=_PUSH_SA_EMAIL)
    except webhook_auth.CertsUnavailable as exc:
        logger.error("gmail webhook: %s — answering 503 (Pub/Sub retries)", exc)
        raise HTTPException(status_code=503, detail="cannot verify right now") from exc
    if not ok:
        logger.warning("gmail webhook: push refused: %s", why)
        raise HTTPException(status_code=401, detail="unauthenticated Pub/Sub push")
    subscription = envelope.get("subscription", "")
    if subscription.split("/")[-1] != _PUBSUB_SUBSCRIPTION.split("/")[-1]:
        # Logged with the same phrase as a bad token, so the rollout's `grep -c 'push refused'` sees it.
        logger.warning("gmail webhook: push refused: unknown subscription %r", subscription.split("/")[-1])
        raise HTTPException(status_code=401, detail="unknown Pub/Sub subscription")


# ── endpoints ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    """Liveness check.  Caddy and external monitors hit this."""
    return {"status": "ok", "ts": int(time.time())}


@app.get("/status")
def status(request: Request):
    """
    Queue health + recent job history, for the operator ON THE BOX:
        curl -s 127.0.0.1:8000/status
    Anything that came through the reverse proxy gets 404 (SEC-01: it used to answer the internet).
    Returns queue counts and the last 20 jobs ordered by most recent first.
    """
    if not webhook_auth.status_allowed(request.client.host if request.client else None, request.headers):
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "queue": db.get_status_counts(),
        "recent": db.get_recent_jobs(20),
    }


@app.post("/webhook/drive/changes")
def drive_changes_webhook(
    x_goog_channel_token: str | None = Header(default=None),
    x_goog_resource_state: str | None = Header(default=None),
):
    """
    Drive changes-feed push notification — the FAST PATH.

    Drive fires this on any change in the account; the notification carries no
    payload (just a 'go look' ping). We delegate to drive_changes.drain(), which
    drains from the persisted cursor and routes each change. The SAME drain runs
    on a timer (rockstar/service/poll_drive_changes.py) because the Drive push channel only lives
    ~40 min — so even if this push never arrives, the poll picks everything up.
    The cursor (not the push) is the source of truth, so push + poll are safe to
    overlap (drain() is file-locked).

    Sync def: FastAPI runs it in a threadpool, so the blocking Drive API calls
    inside drain() don't stall the event loop.
    """
    _assert_drive_token(x_goog_channel_token)

    if x_goog_resource_state == "sync":
        logger.debug("drive/changes: discarding sync event")
        return Response(status_code=200)

    try:
        result = drive_changes.drain("push")
        logger.info("drive/changes (push): %s", result)
    except Exception as exc:
        # Ack anyway — the poll timer will retry the drain shortly. Returning
        # non-200 would only trigger Drive's retry storm for a transient error.
        logger.exception("drive/changes (push): drain failed: %s", exc)

    return Response(status_code=200)


@app.post("/webhook/gmail")
async def gmail_webhook(request: Request):
    """
    Gmail Pub/Sub push notification.

    Pub/Sub delivers a JSON envelope:
      {"message": {"data": "<base64>", "messageId": "...", ...},
       "subscription": "projects/.../subscriptions/..."}

    The inner data decodes to:
      {"emailAddress": "...", "historyId": "..."}

    We then query Gmail for recent unread messages from *@legacy-publisher.example.test
    with a publish-notification subject.  If found, enqueue PUBLISH_FINALIZE.

    Pub/Sub requires a 200 response to ack the message.  Any non-2xx causes
    Pub/Sub to retry with exponential back-off.  We always ack (return 200)
    even if we don't enqueue a job, to avoid retry storms on non-MM emails.
    """
    body = await request.body()

    try:
        envelope = _json.loads(body)
    except Exception:
        logger.warning("gmail webhook: could not parse body")
        return Response(status_code=200)  # ack anyway

    _assert_pubsub_push(request.headers.get("authorization"), envelope)

    notification = gmail_client.parse_pubsub_notification(body)
    if not notification:
        return Response(status_code=200)

    email_address = notification.get("emailAddress", "")
    history_id = notification.get("historyId", "")
    logger.info(
        "gmail webhook: notification from %s historyId=%s",
        email_address,
        history_id,
    )

    # Search for unread publisher notifications across ALL allowlisted senders
    # (Legacy Publisher + Example Publisher + whatever PUBLISHER_SENDER_DOMAINS adds).
    messages = gmail_client.list_unread_messages(
        query=f"{publisher_email.gmail_from_query()} is:unread",
        max_results=5,
    )

    for stub in messages:
        msg = gmail_client.get_message(stub["id"])
        subject = gmail_client.get_header(msg, "subject")
        sender = gmail_client.get_header(msg, "from")
        in_reply_to = gmail_client.get_header(msg, "in-reply-to")

        if not publisher_email.is_publisher_sender(sender):
            continue
        # Only the ORIGINAL "Episode is Live" mail — never a reply/forward in the
        # thread. Replies match the subject test but carry no links; firing on
        # them is what spawned the PermanentFailing jobs (E28, 2026-06-16).
        if not publisher_email.is_publish_notification(subject, in_reply_to=in_reply_to):
            continue
        # TRIG-03 AC-10 (Orly): the From line proves nothing — Legacy Publisher publishes DMARC p=NONE,
        # so a forged From is delivered. Google's verdict in the topmost Authentication-Results decides.
        # A refusal is named once and the message marked read, so it can't re-alert on every push.
        authentic, why = publisher_email.authenticated_sender(msg.get("payload", {}).get("headers", []))
        if not authentic:
            logger.warning("gmail webhook: publish-looking email %s refused: %s", stub["id"], why)
            # Mark read FIRST: if the alert then fails, the next push still won't find it and re-alert.
            try:
                gmail_client.mark_read(stub["id"])
            except Exception as exc:  # noqa: BLE001 — the 200 ack must still go out
                logger.warning("gmail webhook: could not mark the refused email %s read: %s", stub["id"], exc)
                continue  # unread → the next push finds it again; alert then, not on every retry now
            try:
                gmail_client.send_pipeline_alert(
                    to=config.alert_email(),
                    subject="Publisher email refused: sender not authenticated",
                    body=f"A 'Your Episode is Live' email was NOT acted on.\n\nSubject: {subject}\n"
                    f"Reason: {why}\n\nNothing was enqueued. If it is genuine, run Phase 2 by hand.\n",
                )
            except Exception as exc:  # noqa: BLE001
                # Never lose a refused email silently (a false positive would drop a genuine one): put it back
                # in the unread search, so the next push finds it and alerts then.
                logger.warning(
                    "gmail webhook: could not alert on the refused email %s: %s — marking it unread again",
                    stub["id"],
                    exc,
                )
                try:
                    gmail_client.mark_unread(stub["id"])
                except Exception as exc2:  # noqa: BLE001
                    logger.error("gmail webhook: refused email %s is read and un-alerted: %s", stub["id"], exc2)
            continue

        # Match: enqueue PUBLISH_FINALIZE.
        # dedup_key = the Gmail message_id makes this idempotent at the DB level
        # (PUB-01): Pub/Sub redelivery + Gmail's per-mailbox-change fan-out can
        # call this webhook many times for the same still-unread email, but only
        # the first call inserts a row. `inserted` tells us which call this is —
        # True exactly once per message_id — so the mark-read below fires once.
        job_id, inserted = db.enqueue_dedup(
            job_type="PUBLISH_FINALIZE",
            episode_id="TBD",
            payload={
                "trigger": "gmail",
                "message_id": stub["id"],
                "thread_id": stub.get("threadId", ""),
                "subject": subject,
                "sender": sender,
            },
            dedup_key=stub["id"],
        )
        logger.info(
            "gmail webhook: enqueued PUBLISH_FINALIZE job %d for message %s",
            job_id,
            stub["id"],
        )

        # Criterion B — tombstone the trigger on the FIRST insert only. Marking
        # the email read removes it from the `is:unread` search so later pushes
        # stop re-finding it, shrinking the redelivery window. This is hygiene,
        # NOT the correctness guarantee — criterion A's DB dedup already makes
        # redelivery harmless. So mark-read is strictly NON-FATAL: a failure must
        # not undo the enqueue (we don't), must not retry, and must not block the
        # 200 ack (we always reach `return Response(200)` below). We only attempt
        # it when `inserted` is True, so a redelivery never re-marks.
        if inserted:
            try:
                gmail_client.mark_read(stub["id"])
            except Exception as exc:
                logger.warning(
                    "gmail webhook: mark_read failed for message %s (job %d still "
                    "enqueued; dedup key guards redelivery): %s",
                    stub["id"],
                    job_id,
                    exc,
                )

        # Only enqueue once even if multiple matching messages came in
        break

    return Response(status_code=200)
