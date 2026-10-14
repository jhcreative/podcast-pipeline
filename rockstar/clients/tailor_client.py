"""
tailor_client.py — the pipeline's one door into a Tailor site's API (BLOG-02b).

Stdlib only (http.client + ssl + json). The pipeline has never carried an HTTP library of
its own, and this client needs very little: five JSON calls, one multipart upload and one
cookie. Owning ~300 lines here means every behaviour Orly's conditions depend on is visible
in this file, not implied by a library default:

  C-3  PINNED TRANSPORT. The base must be https:// on an allow-listed host
       (rockstarblends.com). The only exception is a loopback host (localhost /
       127.0.0.1 / ::1) and only when the caller passes allow_loopback=True — the Mac-side
       AC-8 retest CLI does, the worker never does, and no environment variable can.
       TLS is verified with the system trust store (ssl.create_default_context()); there
       is no switch to turn that off. http.client never follows redirects, and this
       client treats any 3xx as an error — so neither the password nor the session
       cookie can be carried to a host we did not name.
  C-4  NO SECRET IN ANY OUTPUT. A request is logged as method + path + status. Never a
       header, never a body, never a query string. An error carries the HTTP status and
       Tailor's `error.code` only — never the message, never the body.
  C-5  ONE LOGIN ATTEMPT. A 401 on login is final (no retry: a rotated or wrong password
       must not hammer the 5/min login limit). A 401 on a later call means the session was
       superseded (Tailor keeps one session per account) — one re-login, one retry of that
       call, then stop. A 429 is final.
  C-8  NEVER PUBLISHES. Every page-write body is checked before it leaves: `status`,
       `published_at` and `byline` are refused as keys. An author could publish its own
       post and the server would allow it (Orly, Q1), so this is the guarantee.
  C-9  NO DELETE. There is no method here that sends DELETE.

What this module does NOT decide — ownership, idempotency, the cover mode, alerts — lives
in rockstar/content/blog_publish.py. This file is the wire.
"""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import uuid
from urllib.parse import quote, urlsplit

logger = logging.getLogger(__name__)

# The only production host the pipeline may send the credential to (Orly C-3).
ALLOWED_HOSTS = frozenset({"rockstarblends.com"})
# Loopback hosts, allowed only with an explicit allow_loopback=True (AC-8 retest on the Mac).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

SESSION_COOKIE = "tailor_session"
TIMEOUT_SECONDS = 30
USER_AGENT = "rockstar-pipeline-blog/1"

# Keys a page-write body may never carry (C-8). `byline` too: an author that sends a value
# different from the stored one gets a 403 (Tailor routers/pages.py `_require_editor_for_byline`),
# and the public byline is resolved server-side from the account name — never written by us.
FORBIDDEN_PAGE_KEYS = frozenset({"status", "published_at", "byline"})


class TailorError(Exception):
    """A Tailor call did not do what we asked. `status` is the HTTP status (None when no
    HTTP answer arrived at all — a network error), `code` is Tailor's `error.code` when the
    body carried one. The string form carries only those two — never a body (C-4)."""

    def __init__(self, what: str, status: int | None = None, code: str | None = None):
        self.what, self.status, self.code = what, status, code
        detail = f"HTTP {status}" if status is not None else "no HTTP response"
        if code:
            detail += f" {code}"
        super().__init__(f"{what}: {detail}")

    @property
    def answered(self) -> bool:
        """True when the server answered with a 4xx — a decision, not an outage. A retry
        of the same request gets the same answer, so callers treat it as permanent."""
        return self.status is not None and 400 <= self.status < 500


class TailorConfigError(TailorError):
    """The base URL (or a URL to check) is not one this client may talk to (C-3)."""


class TailorAuthError(TailorError):
    """Login refused, or the session was superseded twice. Stop; never retry (C-5)."""


class TailorRateLimited(TailorError):
    """HTTP 429. Stop; never retry (C-5)."""


# ── C-3 — where we may talk ─────────────────────────────────────────────────────


def check_target(url: str, *, allow_loopback: bool = False) -> tuple[str, str, int | None]:
    """Refuse any URL this client may not send a request to. Returns (scheme, host, port).

    WHAT: https on an allow-listed host; http or https on loopback only when the caller
    opted in. No userinfo (`https://user:pw@host`), since that would put a secret in a URL
    and in any log line that prints one.

    WHAT IT DEFENDS AGAINST: a mistyped or tampered BLOG_API_BASE sending the pipeline's
    password to a host that is not the Tailor site — a lookalike domain, an http:// URL on
    the open internet, or a URL that smuggles credentials. Checked before any socket opens.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.username is not None or parts.password is not None:
        raise TailorConfigError("URL carries userinfo — refusing")
    if host in LOOPBACK_HOSTS:
        if not allow_loopback:
            raise TailorConfigError(f"loopback host {host!r} is allowed only for the AC-8 retest CLI")
        if parts.scheme not in ("http", "https"):
            raise TailorConfigError(f"scheme {parts.scheme!r} is not http(s)")
    else:
        if parts.scheme != "https":
            raise TailorConfigError(f"{url!r} is not https:// — refusing to send anything to it")
        if host not in ALLOWED_HOSTS:
            raise TailorConfigError(f"host {host!r} is not on the allow-list ({', '.join(sorted(ALLOWED_HOSTS))})")
    try:
        port = parts.port
    except ValueError:
        raise TailorConfigError(f"{url!r} has an invalid port") from None
    return parts.scheme, host, port


def validate_base(base: str, *, allow_loopback: bool = False) -> str:
    """The API base as `scheme://host[:port]` — the site root, nothing after it."""
    scheme, host, port = check_target(base, allow_loopback=allow_loopback)
    parts = urlsplit(base)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise TailorConfigError(f"BLOG_API_BASE must be the site root, got path {parts.path!r}")
    netloc = f"[{host}]" if ":" in host else host
    return f"{scheme}://{netloc}" + (f":{port}" if port else "")


# ── the wire ────────────────────────────────────────────────────────────────────


class HttpTransport:
    """One request per connection, http.client underneath. http.client does not follow
    redirects — that is why it is used rather than urllib.request, whose default opener
    follows a 3xx to any host and replays the headers. Tests replace this object."""

    def request(
        self, method: str, scheme: str, host: str, port: int | None, path: str, headers: dict, body: bytes | None
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        if scheme == "https":
            conn = http.client.HTTPSConnection(
                host, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
            )
        else:  # only reachable for a loopback host (check_target)
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT_SECONDS)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, list(resp.msg.items()), data
        finally:
            conn.close()


# FastAPI's own request-validation 422 is `{"detail": [...]}` with NO `error` envelope, and
# each item echoes the offending `input` — which, for a login body, could be the password.
# It is reported as this constant, never as anything read out of `detail` (C-4).
REQUEST_VALIDATION_CODE = "REQUEST_VALIDATION"


def _error_code(data: bytes) -> str | None:
    """Tailor's `{"error": {"code": …}}` code, and nothing else from the body (C-4)."""
    try:
        body = json.loads(data or b"{}")
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        return code if isinstance(code, str) and code.isascii() and len(code) <= 64 else None
    if "detail" in body:
        return REQUEST_VALIDATION_CODE
    return None


def _multipart(
    fields: dict[str, str], file_field: str, filename: str, content_type: str, data: bytes
) -> tuple[bytes, str]:
    boundary = f"----rockstar-{uuid.uuid4().hex}"
    out = bytearray()
    for name, value in fields.items():
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
    safe_name = "".join(c for c in filename if c.isalnum() or c in "-_.") or "tile"
    out += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{safe_name}"\r\nContent-Type: {content_type}\r\n\r\n'
    ).encode()
    out += data + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class TailorClient:
    """A logged-in conversation with one Tailor site. Use as:

    client = TailorClient(base)
    try:
        client.login(email, password)
        ...
    finally:
        client.logout()
    """

    def __init__(self, base: str, *, allow_loopback: bool = False, transport=None):
        self.base = validate_base(base, allow_loopback=allow_loopback)
        self._scheme, self._host, self._port = check_target(self.base, allow_loopback=allow_loopback)
        self._transport = transport or HttpTransport()
        self._cookie: str | None = None
        # Held in memory for ONE supersede re-login (C-5), dropped at logout.
        self._creds: tuple[str, str] | None = None
        self._relogged = False
        # N-2 (Orly): True once this run saw its session superseded. Nothing legitimate logs in
        # to this account in parallel, so it means someone else used the password mid-run —
        # the C-14 signal, in real time. Surfaced in the alert, not only the journal.
        self.superseded = False

    # ── plumbing ──

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        with_cookie: bool = True,
    ):
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if content_type:
            headers["Content-Type"] = content_type
        if with_cookie and self._cookie:
            headers["Cookie"] = f"{SESSION_COOKIE}={self._cookie}"
        try:
            status, resp_headers, data = self._transport.request(
                method, self._scheme, self._host, self._port, path, headers, body
            )
        except (OSError, http.client.HTTPException) as exc:
            # The exception TYPE only: a socket error's text can echo the host, never a
            # secret, but there is no reason to carry more than the class name.
            logger.warning("tailor %s %s → %s", method, path.split("?")[0], type(exc).__name__)
            raise TailorError(f"{method} {path.split('?')[0]} failed ({type(exc).__name__})") from None
        logger.info("tailor %s %s → %d", method, path.split("?")[0], status)
        for name, value in resp_headers:
            if name.lower() == "set-cookie":
                self._absorb_cookie(value)
        if 300 <= status < 400:
            # A redirect is a wrong base (http→https, a moved host), not an outage: never
            # followed, and permanent — the caller stops rather than retrying into it.
            raise TailorConfigError(f"{method} {path.split('?')[0]} answered a redirect — not followed", status)
        return status, data

    def _absorb_cookie(self, set_cookie: str) -> None:
        pair = set_cookie.split(";", 1)[0]
        name, _, value = pair.partition("=")
        if name.strip() == SESSION_COOKIE:
            self._cookie = value.strip() or None

    def _call(
        self,
        method: str,
        path: str,
        *,
        json_body=None,
        multipart=None,
        expect: tuple[int, ...] = (200,),
        what: str = "",
    ):
        """An authenticated call with the C-5 supersede rule: a 401 re-logs in ONCE and
        retries this call once; a second 401 is final."""
        what = what or f"{method} {path.split('?')[0]}"
        if json_body is not None:
            body, ctype = json.dumps(json_body, ensure_ascii=False).encode("utf-8"), "application/json"
        elif multipart is not None:
            body, ctype = multipart
        else:
            body, ctype = None, None
        status, data = self._send(method, path, body=body, content_type=ctype)
        if status == 401 and self._relogin_once(what):
            status, data = self._send(method, path, body=body, content_type=ctype)
        if status == 401:
            raise TailorAuthError(f"{what} refused after re-login", status, _error_code(data))
        if status == 429:
            raise TailorRateLimited(what, status, _error_code(data))
        if status not in expect:
            raise TailorError(what, status, _error_code(data))
        try:
            return json.loads(data) if data else None
        except ValueError:
            raise TailorError(f"{what} returned a body that is not JSON", status) from None

    def _relogin_once(self, what: str) -> bool:
        """C-5: the ONE re-login a run gets after its session was superseded. False when it
        was already spent (or there is no credential). A 401 on the re-login itself raises
        TailorAuthError from `_login` — never a third attempt."""
        self.superseded = True
        if not self._creds or self._relogged:
            return False
        logger.warning("tailor: %s — session superseded; one re-login (C-5)", what)
        self._relogged = True
        self._login(*self._creds)
        return True

    def _session_alive(self) -> bool:
        """Is our session still the account's current one? GET /api/auth/me is
        `require_user`, so a superseded cookie answers 401 here — unlike a preview GET,
        which quietly treats it as anonymous (see get_child)."""
        status, _ = self._send("GET", "/api/auth/me")
        if status == 200:
            return True
        if status == 401:
            return False
        if status == 429:
            raise TailorRateLimited("GET /api/auth/me", status)
        raise TailorError("GET /api/auth/me", status)

    @staticmethod
    def _guard_page_body(body: dict) -> None:
        bad = FORBIDDEN_PAGE_KEYS.intersection(body)
        if bad:
            # A programming error, caught before the request exists. Never sent.
            raise ValueError(f"page-write body carries forbidden key(s) {sorted(bad)} (C-8)")

    # ── auth ──

    def _login(self, email: str, password: str) -> None:
        body = json.dumps({"email": email, "password": password}).encode("utf-8")
        self._cookie = None
        status, data = self._send(
            "POST", "/api/auth/login", body=body, content_type="application/json", with_cookie=False
        )
        if status == 429:
            raise TailorRateLimited("login", status, _error_code(data))
        if status != 200:
            raise TailorAuthError("login refused", status, _error_code(data))
        if not self._cookie:
            raise TailorAuthError("login answered 200 but set no session cookie", status)

    def login(self, email: str, password: str) -> None:
        """One attempt, no retry (C-5)."""
        if not email or not password:
            raise TailorAuthError("credential is missing a key")
        self._login(email, password)
        self._creds = (email, password)

    def whoami(self) -> dict:
        return self._call("GET", "/api/auth/me", what="GET /api/auth/me") or {}

    def logout(self) -> None:
        """Best effort, never raises: it runs in a `finally`, and a failure here must not
        mask the error that got us there. Logout ends the session server-side."""
        try:
            if self._cookie:
                self._send("POST", "/api/auth/logout")
        except Exception as exc:  # noqa: BLE001 — see docstring
            logger.warning("tailor: logout did not complete (%s)", type(exc).__name__)
        finally:
            self._cookie = None
            self._creds = None

    # ── pages ──

    def get_child(self, parent: str, slug: str) -> dict | None:
        """The child page at /{parent}/{slug}, drafts included (`?preview=1` with a session —
        Tailor routers/pages.py get_child_page). None only when the page truly isn't there.

        WHY A 404 IS CONFIRMED (Jen F-1): the preview GET is a PUBLIC route. A superseded
        cookie does not 401 there — `authenticated_claims_or_none` (core/auth.py) resolves it
        to an anonymous visitor, and an anonymous visitor gets 404 for every draft. Taken at
        face value, that 404 would make our own recorded draft look "gone" (a permanent
        stop), make the page change "under us", and make someone else's draft slug look
        free. So a 404 is only believed once /api/auth/me proves the session is current; if
        it isn't, the one C-5 re-login is spent and the GET is asked again."""
        path = f"/api/pages/{quote(parent, safe='')}/{quote(slug, safe='')}?preview=1"
        what = f"GET /api/pages/{parent}/{slug}"
        for _ in range(2):
            try:
                return self._call("GET", path, what=what)
            except (TailorAuthError, TailorRateLimited):
                raise
            except TailorError as exc:
                if exc.status != 404:
                    raise
            if self._session_alive():
                return None
            if not self._relogin_once(what):
                raise TailorAuthError(f"{what}: session superseded again after re-login", 401)
        raise TailorAuthError(f"{what}: session superseded again after re-login", 401)

    def create_page(self, body: dict) -> dict:
        self._guard_page_body(body)
        return self._call("POST", "/api/pages", json_body=body, expect=(201,), what="POST /api/pages")

    def update_child(self, parent: str, slug: str, body: dict) -> dict:
        self._guard_page_body(body)
        path = f"/api/pages/{quote(parent, safe='')}/{quote(slug, safe='')}"
        return self._call("PUT", path, json_body=body, what=f"PUT /api/pages/{parent}/{slug}")

    # ── media ──

    def media_status(self, media_id: str) -> int | None:
        """HTTP status of the public GET /api/media/{id} (routers/media.py serve_media: 200
        while the row exists, 404 once it's deleted). Anonymous — the route is public, and
        an author has no media list/read route of its own. None when no answer arrived."""
        try:
            status, _ = self._send("GET", f"/api/media/{quote(str(media_id), safe='')}", with_cookie=False)
        except TailorConfigError:
            raise
        except TailorError:
            return None
        return status

    def upload_media(
        self, page_id: str, filename: str, data: bytes, content_type: str, name: str | None = None
    ) -> dict:
        """POST /api/media with the `page_id` of a page this account owns. An author may do
        this since Tailor v0.9.94 (BACKLOG-0216, routers/media.py `_authorize_upload`);
        older instances answer 403."""
        fields = {"page_id": str(page_id)}
        if name:
            fields["name"] = name
        return self._call(
            "POST",
            "/api/media",
            multipart=_multipart(fields, "file", filename, content_type, data),
            expect=(201,),
            what="POST /api/media",
        )


def url_status(url: str, *, allow_loopback: bool = False, transport=None) -> int | None:
    """The HTTP status of an anonymous GET of `url` — the OPEN-1 "is the post live?" check.
    Same pinning as the API client (C-3): allow-listed host, https, redirects NOT followed
    (a 301 comes back as 301, which is not 200). No cookie is ever sent. None when no HTTP
    answer arrived."""
    scheme, host, port = check_target(url, allow_loopback=allow_loopback)
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        status, _, _ = (transport or HttpTransport()).request(
            "GET", scheme, host, port, path, {"User-Agent": USER_AGENT}, None
        )
    except (OSError, http.client.HTTPException) as exc:
        logger.warning("url_status: GET %s → %s", parts.path, type(exc).__name__)
        return None
    logger.info("url_status: GET %s → %d", parts.path, status)
    return status
