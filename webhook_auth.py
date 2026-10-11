"""
webhook_auth.py — who may call the webhook service, decided in one place (SEC-01).

Found 2026-10-10 (Orly, confirmed live: HTTP 200, 20 job rows to an anonymous GET):
  1. /status answered anyone on the internet with the last 20 job rows (payloads, error text).
  2. /webhook/gmail trusted a subscription name the CALLER writes in the body; anyone could make the box
     query Gmail.
  3. Both checks were skipped when their secret was unset ("dev mode"), silently.
  4. The Drive token was compared with `!=` (timing leak).

The rules here, each one failing CLOSED:
  • /status is for the box's own operator only: a loopback peer AND no proxy forwarding header. uvicorn
    listens on 127.0.0.1, so every request from outside arrives through Caddy — whose own connection is
    loopback too. What tells them apart is that Caddy always adds X-Forwarded-For; a `curl` on the box
    doesn't. A client cannot remove a header Caddy adds.
  • A Gmail push must carry Pub/Sub's OIDC token (Authorization: Bearer <JWT>), signed by Google, for OUR
    audience, from OUR push service account, with a verified email. The body's subscription name is
    checked too, but it proves nothing on its own.
  • Every shared secret is compared with hmac.compare_digest, and an unset expected secret matches nothing.
  • The service refuses to start while a required setting is unset, unless PIPELINE_DEV=1 (never set
    on the box: a test reads every systemd unit).
"""

from __future__ import annotations

import hmac
import os

# Everything the service needs to authenticate a caller. Missing any → no start (unless PIPELINE_DEV=1).
REQUIRED_SETTINGS = (
    "WEBHOOK_SHARED_SECRET",      # Drive: X-Goog-Channel-Token, set at watch registration
    "GMAIL_PUBSUB_SUBSCRIPTION",  # Gmail: the push subscription's name
    "PUBSUB_PUSH_AUDIENCE",       # Gmail: the `aud` the push subscription puts in its OIDC token
    "PUBSUB_PUSH_SA_EMAIL",       # Gmail: the service account the push subscription signs as
)

_FORWARDING_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip")
_LOOPBACK = ("127.0.0.1", "::1")
_GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
# google-auth checks iat/exp with zero skew by default: a box clock one second behind Google's would refuse
# every fresh token as "used too early" (cold review of PR #32, Orly).
CLOCK_SKEW_SECONDS = 30
# The certs fetch runs inside the async Gmail handler; google-auth's default timeout is 120 s, which would stall
# every route that long on a slow Google endpoint. 10 s, then 503 (Pub/Sub retries).
CERTS_TIMEOUT_SECONDS = 10


class CertsUnavailable(RuntimeError):
    """Google's signing certs could not be fetched: OUR failure, not the caller's (answered 503, not 401)."""


def dev_mode(env=os.environ) -> bool:
    return env.get("PIPELINE_DEV", "") == "1"


def missing_settings(env=os.environ) -> list[str]:
    """Required settings that are unset or blank, by name."""
    return [k for k in REQUIRED_SETTINGS if not env.get(k, "").strip()]


def secret_matches(given: str | None, expected: str) -> bool:
    """Constant-time comparison. An unset expected secret matches NOTHING (it used to match everything)."""
    if not expected:
        return False
    return hmac.compare_digest((given or "").encode("utf-8"), expected.encode("utf-8"))


def status_allowed(client_host: str | None, headers) -> bool:
    """True only for a request made on the box itself, not through the reverse proxy."""
    if client_host not in _LOOPBACK:
        return False
    names = {k.lower() for k in headers.keys()}
    return not any(h in names for h in _FORWARDING_HEADERS)


def _timed(request):
    """google-auth's requests transport with a short timeout on every call."""
    return lambda url, method="GET", **kw: request(url, method=method, **{**kw, "timeout": CERTS_TIMEOUT_SECONDS})


def verify_pubsub_push(authorization: str | None, *, audience: str, sa_email: str,
                       request=None, certs_url: str | None = None) -> tuple[bool, str]:
    """(ok, reason) for a Pub/Sub push's `Authorization: Bearer <JWT>`. Raises CertsUnavailable when
    Google's certs can't be fetched — a fault on our side, which the caller reports as 503.

    Verified with google-auth (signature against Google's published certs, expiry, `aud`), then the
    claims: issuer is Google, `email` is our push service account, `email_verified` is true.
    `request` / `certs_url` exist for the test, which serves its own signing cert; production passes
    neither and google-auth fetches Google's."""
    if not audience or not sa_email:
        return False, "push audience / service account not configured"
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return False, "no Bearer token"
    from google.auth import exceptions as gexc
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token

    kwargs = {"audience": audience, "clock_skew_in_seconds": CLOCK_SKEW_SECONDS}
    if certs_url:
        kwargs["certs_url"] = certs_url
    try:
        claims = id_token.verify_token(token.strip(), request or _timed(greq.Request()), **kwargs)
    except gexc.TransportError as exc:
        raise CertsUnavailable(f"Google's signing certs unavailable: {type(exc).__name__}") from None
    except (ValueError, gexc.GoogleAuthError) as exc:
        return False, f"token rejected: {type(exc).__name__}"
    if claims.get("iss") not in _GOOGLE_ISSUERS:
        return False, f"issuer {claims.get('iss')!r} is not Google"
    if claims.get("email") != sa_email or claims.get("email_verified") is not True:
        return False, "token is not from the push service account"
    return True, "ok"
