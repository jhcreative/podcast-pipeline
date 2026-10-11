"""
gmail_client.py — Gmail API wrapper.

⚠️  NEVER SEND GUEST / EXTERNAL EMAILS AUTOMATICALLY.
    All outbound email to guests or publishers is created as a DRAFT only.
    Heather reviews and sends manually.  This is a hard constraint.

    EXCEPTION: send_pipeline_alert() sends automated internal notifications
    to John (john@example.test) only — these are pipeline status alerts,
    not communications with guests.  Requires gmail.send DWD scope.

Responsibilities:
  - parse_pubsub_notification()  decode an inbound Pub/Sub push envelope
  - list_unread_messages()        search for messages by Gmail query
  - get_message()                 fetch a full message resource
  - extract_plain_text()          get readable body from a Gmail payload
  - create_draft()                create a draft (DOES NOT SEND)
  - register_watch()              set up Gmail Pub/Sub push channel
  - stop_watch()                  tear down the watch before renewal
"""

import base64
import email.message
import json
import logging
import os

from googleapiclient.discovery import build

from google_auth import gmail_credentials

logger = logging.getLogger(__name__)


def _svc():
    return build("gmail", "v1", credentials=gmail_credentials(), cache_discovery=False)


# ── Pub/Sub notification parsing ───────────────────────────────────────────────


def parse_pubsub_notification(body: bytes) -> dict | None:
    """
    Decode a Pub/Sub HTTP push message envelope.
    The inner data field is base64-encoded JSON:
      {"emailAddress": "...", "historyId": "..."}

    Returns the decoded dict or None if the envelope is malformed.
    Malformed Pub/Sub pushes are common during watch setup races; log and
    return None so the webhook handler can ack them without crashing.
    """
    try:
        envelope = json.loads(body)
        raw_data = envelope["message"]["data"]
        inner = base64.b64decode(raw_data + "==").decode("utf-8")
        return json.loads(inner)
    except Exception as exc:
        logger.warning("could not parse Pub/Sub notification: %s", exc)
        return None


# ── message retrieval ──────────────────────────────────────────────────────────


def list_unread_messages(query: str = "is:unread", max_results: int = 20) -> list[dict]:
    """
    List message stubs (id + threadId) matching a Gmail search query.
    Call get_message() to fetch the full payload.
    """
    result = (
        _svc()
        .users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    return result.get("messages", [])


def get_message(message_id: str) -> dict:
    """Fetch a full Gmail message resource by ID."""
    return (
        _svc()
        .users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def extract_plain_text(message: dict) -> str:
    """
    Extract the plain-text body from a Gmail message resource.
    Walks the multipart tree; falls back to the message snippet if no
    text/plain part is found.  The snippet is HTML-stripped but truncated
    at ~200 chars — good enough for subject-line matching, not for full
    body extraction.
    """
    payload = message.get("payload", {})

    def _decode_part(part: dict) -> str | None:
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return None

    def _find_plain(part: dict) -> str | None:
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            return _decode_part(part)
        if mime.startswith("multipart/"):
            for sub in part.get("parts", []):
                found = _find_plain(sub)
                if found:
                    return found
        return None

    text = _find_plain(payload)
    return text if text else message.get("snippet", "")


def extract_html(message: dict) -> str:
    """
    Extract the text/html body from a Gmail message resource.

    The publisher's "Episode is Live" template is now HTML-only — the listen
    links live in button hrefs and there is no usable text/plain part, so
    extract_plain_text() returns only the ~200-char snippet. URL extraction must
    fall back to this HTML. Returns "" if no text/html part exists.
    """
    payload = message.get("payload", {})

    def _decode_part(part: dict) -> str | None:
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return None

    def _find_html(part: dict) -> str | None:
        mime = part.get("mimeType", "")
        if mime == "text/html":
            return _decode_part(part)
        if mime.startswith("multipart/"):
            for sub in part.get("parts", []):
                found = _find_html(sub)
                if found:
                    return found
        return None

    return _find_html(payload) or ""


def get_thread(thread_id: str) -> dict:
    """Fetch a full thread resource (all messages) by thread ID."""
    return (
        _svc()
        .users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )


def get_header(message: dict, name: str) -> str:
    """Extract a specific header value from a Gmail message resource."""
    headers = message.get("payload", {}).get("headers", [])
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


# ── message mutation ───────────────────────────────────────────────────────────


def mark_read(message_id: str) -> None:
    """
    Remove the UNREAD label from a message — marks it read.

    Tombstones a processed publisher "Episode is Live" email (PUB-01 criterion
    B) so it stops matching the `is:unread` search that re-finds it on every
    Gmail push. Uses the gmail.modify scope (already granted, previously unused).

    Raises on API failure — the caller decides whether that's fatal. For the
    webhook it is NOT: mark-read is hygiene, not the correctness guarantee
    (that is the DB dedup key), so the caller logs a warning and moves on.
    """
    _svc().users().messages().modify(
        userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def mark_unread(message_id: str) -> None:
    """Put the UNREAD label back (TRIG-03): a refused publisher email whose alert could not be sent is
    returned to the `is:unread` search, so the next push finds it and alerts — never lost silently."""
    _svc().users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": ["UNREAD"]}
    ).execute()
    logger.info("marked message %s read (removed UNREAD)", message_id)


# ── draft creation ─────────────────────────────────────────────────────────────


def create_draft(
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> dict:
    """
    Create a draft email.  ⚠️ DOES NOT SEND.  Returns the draft resource.

    thread_id: if supplied, attaches the draft as a reply in the thread.
    """
    msg = email.message.EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    draft_body: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    draft = _svc().users().drafts().create(userId="me", body=draft_body).execute()
    logger.info(
        "created draft id=%s to=%r subject=%r (NOT sent)", draft["id"], to, subject
    )
    return draft


# ── draft read / delete ────────────────────────────────────────────────────────
# The claude.ai Gmail MCP has no delete/update and rewrites every URL through
# google.com/url redirects. These server-side helpers are the sanctioned way to
# inspect and remove drafts (raw-verify a created draft; delete the E29 garbage).


def list_drafts(max_results: int = 100) -> list[dict]:
    """List draft stubs (id + message.{id,threadId}). Call get_draft/get_draft_body
    for content."""
    result = (
        _svc().users().drafts().list(userId="me", maxResults=max_results).execute()
    )
    return result.get("drafts", [])


def get_draft(draft_id: str, fmt: str = "full") -> dict:
    """Fetch a full draft resource. fmt='raw' returns the RFC822 bytes (base64url)
    under message.raw — use get_draft_raw/get_draft_body for the decoded text."""
    return (
        _svc()
        .users()
        .drafts()
        .get(userId="me", id=draft_id, format=fmt)
        .execute()
    )


def get_draft_raw(draft_id: str) -> str:
    """Return the draft's raw RFC822 message as a decoded string (headers + body).
    This is the ground truth of what is stored server-side — used to prove a draft
    carries the exact template text and no google.com/url redirect cruft."""
    d = get_draft(draft_id, fmt="raw")
    raw = d.get("message", {}).get("raw", "")
    return base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")


def get_draft_body(draft_id: str) -> str:
    """Return the draft's decoded text/plain body (transfer-encoding resolved).

    Fetches format='raw' and parses it with the default email policy so a
    quoted-printable/base64 body is decoded back to the exact text we wrote —
    grepping the raw blob would trip over '=3D'-style QP escapes. Used to run the
    content gate against what is ACTUALLY stored, not what we think we wrote."""
    import email as _email
    import email.policy as _policy

    raw = get_draft_raw(draft_id)
    msg = _email.message_from_string(raw, policy=_policy.default)
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_content()
        return ""
    return msg.get_content()


def update_draft(draft_id: str, to: str, subject: str, body: str) -> dict:
    """Replace a draft's message IN PLACE (drafts.update): the draft id stays, nothing is sent,
    nothing is deleted. TRIG-03 (Orly): delete-then-create can leave no draft, create-then-delete
    can leave two; an update is one call. Covered by the existing gmail.modify scope."""
    msg = email.message.EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    draft = (
        _svc().users().drafts()
        .update(userId="me", id=draft_id, body={"id": draft_id, "message": {"raw": raw}})
        .execute()
    )
    logger.info("updated draft id=%s in place, to=%r subject=%r (NOT sent)", draft_id, to, subject)
    return draft


class DraftGone(LookupError):
    """The draft no longer exists: sent, or deleted by hand."""


def draft_state(draft_id: str) -> dict:
    """What Gmail holds for a draft NOW: {message_id, single_text_plain, to, subject, body}.

    TRIG-03 compares this with the record taken at creation. `message_id` changes on ANY save (UI
    or API); `single_text_plain` is False once an attachment is added or the composer rewrites
    the draft as HTML — the cases where the plain body alone could look unchanged (Orly).
    Raises DraftGone on a 404."""
    try:
        full = get_draft(draft_id, fmt="full")
        body = get_draft_body(draft_id)  # the second GET can 404 too (sent between the two)
    except Exception as exc:  # googleapiclient HttpError carries .resp.status (an int)
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            raise DraftGone(draft_id) from None
        raise
    message = full.get("message", {})
    payload = message.get("payload", {})
    return {
        "message_id": message.get("id", ""),
        "single_text_plain": payload.get("mimeType") == "text/plain" and not payload.get("parts"),
        "to": get_header(message, "to"),
        "subject": get_header(message, "subject"),
        "body": body,
    }


def delete_draft(draft_id: str) -> None:
    """Permanently delete a draft server-side (Gmail API). Used to remove the E29
    transcript-garbage draft — the MCP cannot do this. Never sends anything."""
    _svc().users().drafts().delete(userId="me", id=draft_id).execute()
    logger.info("deleted draft id=%s", draft_id)


# ── pipeline alert (internal only) ────────────────────────────────────────────


def send_pipeline_alert(to: str, subject: str, body: str) -> dict:
    """
    Send an automated pipeline status alert email.

    ⚠️ FOR INTERNAL PIPELINE ALERTS ONLY (e.g. notify John that visuals are
    missing before publish can proceed).  NOT for guest communications.
    Those are always create_draft() — never send.

    Requires gmail.send DWD scope in addition to gmail.modify.
    Add https://www.googleapis.com/auth/gmail.send to the SA's DWD entry
    in Workspace Admin if not already present.
    """
    msg = email.message.EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    result = (
        _svc()
        .users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    logger.info("sent pipeline alert to=%r subject=%r id=%s", to, subject, result.get("id"))
    return result


# ── Gmail watch ────────────────────────────────────────────────────────────────


def register_watch(label_ids: list[str] | None = None) -> dict:
    """
    Register (or refresh) a Gmail Pub/Sub push watch.
    The watch expires in ~7 days; watches/renew_watches.py refreshes it daily.
    GMAIL_PUBSUB_TOPIC must be set in the environment:
      projects/{project}/topics/{topic}
    """
    topic = os.environ.get("GMAIL_PUBSUB_TOPIC", "")
    if not topic:
        raise RuntimeError("GMAIL_PUBSUB_TOPIC not set in environment")

    body: dict = {"topicName": topic}
    if label_ids:
        body["labelIds"] = label_ids

    result = _svc().users().watch(userId="me", body=body).execute()
    logger.info(
        "registered Gmail watch historyId=%s expiration=%s",
        result.get("historyId"),
        result.get("expiration"),
    )
    return result


def stop_watch() -> None:
    """Stop the current Gmail push watch.  Called before re-registering."""
    _svc().users().stop(userId="me").execute()
    logger.info("stopped Gmail watch")
