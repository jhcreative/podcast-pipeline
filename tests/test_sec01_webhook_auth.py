"""
test_sec01_webhook_auth.py — SEC-01: the webhook service fails closed.

Run: python3 -B test_sec01_webhook_auth.py   (plain asserts, no pytest needed)

Why (2026-10-10, Orly, confirmed live): an anonymous GET of https://pipeline.example.test/status answered 200 with
the last 20 job rows. The Gmail webhook trusted a subscription name the caller writes in the body. Both checks were
skipped when their secret was unset, and the Drive token was compared with `!=`.

Acceptance criteria → tests:
  AC-1 /status answers only the box itself; through the proxy or from outside → 404
       → test_status_rule, test_status_endpoint_404_through_proxy_200_on_box
  AC-2 a Gmail push with a missing, expired, wrong-audience, wrong-signer or wrongly-signed token → 401, nothing
       enqueued → test_pubsub_token_*, test_gmail_endpoint_refuses_unauthenticated_push
  AC-3 a required setting unset (and PIPELINE_DEV unset) → the service refuses to start, naming it
       → test_startup_refuses_with_a_setting_missing, test_pipeline_dev_is_never_set_on_the_box
  AC-4 every shared secret is compared in constant time; an unset secret matches nothing
       → test_secret_matches, test_no_plain_token_comparison

Discipline: tokens are REAL RS256 JWTs signed with a throwaway key and checked by google-auth's own verify_token;
only the certificate fetch is faked (it serves the throwaway cert in place of Google's). webhook_service is the real
module under test_pub01's stubs (fastapi, Gmail, Drive).
"""

import asyncio
import datetime
import json
import os
import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))

import webhook_auth as wa  # noqa: E402

AUD = "https://pm.example.test/webhook/gmail"
SA = "gmail-push@rockstar-pipeline.iam.gserviceaccount.example"
CERTS_URL = "https://certs.example.test/oauth2/v1/certs"


# ── a throwaway signing key, served as if it were Google's ────────────────────


def _keypair():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sec01-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, CERT = _keypair()
OTHER_KEY, _ = _keypair()


class _Resp:
    def __init__(self, data):
        self.status, self.data, self.headers = 200, data, {}


def _certs_request(url, method="GET", **kw):
    assert url == CERTS_URL, url
    return _Resp(json.dumps({"k1": CERT}).encode())


def _token(key=KEY, **over):
    from google.auth import crypt, jwt
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": AUD, "email": SA, "email_verified": True,
              "iat": now, "exp": now + 3600, "sub": "1"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(crypt.RSASigner.from_string(key, key_id="k1"), claims).decode()


def _verify(authorization, **kw):
    args = dict(audience=AUD, sa_email=SA, request=_certs_request, certs_url=CERTS_URL)
    args.update(kw)
    return wa.verify_pubsub_push(authorization, **args)


# ── AC-2: the Pub/Sub token ───────────────────────────────────────────────────


def test_pubsub_token_good_passes():
    ok, why = _verify(f"Bearer {_token()}")
    assert ok, why


def test_pubsub_token_refusals():
    now = int(time.time())
    cases = {
        "no header": None,
        "not bearer": f"Basic {_token()}",
        "empty bearer": "Bearer ",
        "garbage": "Bearer not.a.jwt",
        "wrong audience": f"Bearer {_token(aud='https://attacker.example/')}",
        "expired": f"Bearer {_token(iat=now - 7200, exp=now - 3600)}",
        "signed by another key": f"Bearer {_token(key=OTHER_KEY)}",
        "another service account": f"Bearer {_token(email='someone@attacker.example')}",
        "email not verified": f"Bearer {_token(email_verified=False)}",
        "issuer not Google": f"Bearer {_token(iss='https://attacker.example')}",
    }
    for label, header in cases.items():
        ok, why = _verify(header)
        assert not ok, f"{label} passed"
        assert why, label


def test_pubsub_token_unconfigured_refuses():
    ok, why = _verify(f"Bearer {_token()}", audience="")
    assert not ok and "not configured" in why
    ok, why = _verify(f"Bearer {_token()}", sa_email="")
    assert not ok and "not configured" in why


# ── AC-1: /status ─────────────────────────────────────────────────────────────


def test_status_rule():
    assert wa.status_allowed("127.0.0.1", {}) and wa.status_allowed("::1", {"Accept": "*/*"})
    for h in ("X-Forwarded-For", "x-forwarded-host", "X-Forwarded-Proto", "Forwarded", "X-Real-IP"):
        assert not wa.status_allowed("127.0.0.1", {h: "203.0.113.9"}), h
    assert not wa.status_allowed("203.0.113.9", {}), "not loopback"
    assert not wa.status_allowed(None, {}), "no peer"


# ── AC-4: secrets ─────────────────────────────────────────────────────────────


def test_secret_matches():
    assert wa.secret_matches("s3cret", "s3cret")
    assert not wa.secret_matches("wrong", "s3cret")
    assert not wa.secret_matches(None, "s3cret")
    assert not wa.secret_matches("", ""), "an unset secret matches nothing (it used to match everything)"
    assert not wa.secret_matches("anything", "")


def test_no_plain_token_comparison():
    import re
    src = (REPO / "webhook_service.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"(token|secret|SECRET)\w*\s*!=|!=\s*_?\w*(token|secret|SECRET)", code), \
        "a shared secret compared with != (timing leak)"
    assert "compare_digest" in (REPO / "webhook_auth.py").read_text()


# ── the real webhook_service, under test_pub01's stubs ────────────────────────


class _HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _service(env: dict):
    """webhook_service imported with `env` in place (it reads its settings at import)."""
    import test_pub01_idempotent_enqueue as p1
    old = {k: os.environ.get(k) for k in list(wa.REQUIRED_SETTINGS) + ["PIPELINE_DEV"]}
    for k in old:
        os.environ.pop(k, None)
    os.environ.update(env)
    marked = []
    try:
        ws, gc, saved, msg_id = p1._load_webhook_with_stubs(lambda mid: marked.append(mid))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    ws.HTTPException = _HTTPException
    return ws, saved, p1


FULL = {"WEBHOOK_SHARED_SECRET": "drive-s3cret", "GMAIL_PUBSUB_SUBSCRIPTION": "gmail-push-sub",
        "PUBSUB_PUSH_AUDIENCE": AUD, "PUBSUB_PUSH_SA_EMAIL": SA}


def test_startup_refuses_with_a_setting_missing():
    for missing in wa.REQUIRED_SETTINGS:
        env = {k: v for k, v in FULL.items() if k != missing}
        ws, saved, p1 = _service(env)
        os.environ.update(env)
        os.environ.pop(missing, None)
        try:
            ws.on_startup()
        except RuntimeError as exc:
            assert missing in str(exc), exc
        else:
            raise AssertionError(f"started with {missing} unset")
        finally:
            for k in FULL:
                os.environ.pop(k, None)
            p1._restore_modules(saved)


def test_status_endpoint_404_through_proxy_200_on_box():
    ws, saved, p1 = _service(FULL)
    try:
        db, _ = p1._fresh_db("sec01.db")
        db.init_db()
        ws.db = db
        req = lambda host, headers: types.SimpleNamespace(client=types.SimpleNamespace(host=host), headers=headers)
        through_caddy = req("127.0.0.1", {"X-Forwarded-For": "198.51.100.4", "Host": "pipeline.example.test"})
        try:
            ws.status(through_caddy)
        except _HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("/status answered a request that came through the proxy")
        out = ws.status(req("127.0.0.1", {"Host": "127.0.0.1:8000"}))
        assert set(out) == {"queue", "recent"}, "the operator on the box still gets the status"
    finally:
        p1._restore_modules(saved)


def test_gmail_endpoint_refuses_unauthenticated_push():
    ws, saved, p1 = _service(FULL)
    try:
        db, path = p1._fresh_db("sec01-gmail.db")
        db.init_db()
        ws.db = db
        body = b'{"message": {"data": "x"}, "subscription": "projects/p/subscriptions/gmail-push-sub"}'
        for label, headers in (("no token", {}), ("bad token", {"authorization": "Bearer not.a.jwt"})):
            try:
                asyncio.run(ws.gmail_webhook(p1._FakeRequest(body, headers)))
            except _HTTPException as exc:
                assert exc.status_code == 401, label
            else:
                raise AssertionError(f"{label}: accepted")
            assert p1._rows(path) == [], f"{label}: nothing may be enqueued"
    finally:
        p1._restore_modules(saved)


def _any_certs(url, method="GET", **kw):
    return _Resp(json.dumps({"k1": CERT}).encode())


def _with_google_transport(fn):
    """Run fn with google-auth's transport serving the throwaway cert, as the endpoint calls it (no args)."""
    import google.auth.transport.requests as greq
    real = greq.Request
    greq.Request = lambda *a, **k: _any_certs
    try:
        return fn()
    finally:
        greq.Request = real


GOOD_BODY = b'{"message": {"data": "x"}, "subscription": "projects/p/subscriptions/gmail-push-sub"}'


def test_gmail_endpoint_accepts_a_good_push_end_to_end():
    ws, saved, p1 = _service(FULL)
    try:
        db, path = p1._fresh_db("sec01-good.db")
        db.init_db()
        ws.db = db
        req = p1._FakeRequest(GOOD_BODY, {"authorization": f"Bearer {_token()}"})
        resp = _with_google_transport(lambda: asyncio.run(ws.gmail_webhook(req)))
        assert resp.status_code == 200 and len(p1._rows(path)) == 1, "a verified push from our subscription enqueues"
        wrong_sub = GOOD_BODY.replace(b"gmail-push-sub", b"evil-gmail-push-sub")
        req = p1._FakeRequest(wrong_sub, {"authorization": f"Bearer {_token()}"})
        try:
            _with_google_transport(lambda: asyncio.run(ws.gmail_webhook(req)))
        except _HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("a good token for another subscription name was accepted")
        seen = {}
        _with_google_transport(lambda: seen.setdefault("timeout", None))
        import google.auth.transport.requests as greq
        real = greq.Request
        greq.Request = lambda *a, **k: (lambda url, method="GET", **kw: seen.update(kw) or _any_certs(url))
        try:
            asyncio.run(ws.gmail_webhook(p1._FakeRequest(GOOD_BODY, {"authorization": f"Bearer {_token()}"})))
        finally:
            greq.Request = real
        assert seen.get("timeout") == wa.CERTS_TIMEOUT_SECONDS, f"certs fetch timeout: {seen}"
    finally:
        p1._restore_modules(saved)


def test_gmail_endpoint_503_when_google_certs_unreachable():
    from google.auth import exceptions as gexc

    def down(url, method="GET", **kw):
        raise gexc.TransportError("connection refused")
    ws, saved, p1 = _service(FULL)
    import google.auth.transport.requests as greq
    real = greq.Request
    greq.Request = lambda *a, **k: down
    try:
        asyncio.run(ws.gmail_webhook(p1._FakeRequest(GOOD_BODY, {"authorization": f"Bearer {_token()}"})))
    except _HTTPException as exc:
        assert exc.status_code == 503, "our outage is a 503, not the caller's 401"
    else:
        raise AssertionError("accepted without verifying")
    finally:
        greq.Request = real
        p1._restore_modules(saved)


def test_clock_skew_is_tolerated_within_30s_only():
    now = int(time.time())
    ok, why = _verify(f"Bearer {_token(iat=now + 10, exp=now + 3610)}")
    assert ok, f"a token 10 s ahead of the box clock must pass: {why}"
    ok, _ = _verify(f"Bearer {_token(iat=now + 120, exp=now + 3720)}")
    assert not ok, "a token 2 minutes in the future must not"


def test_drive_endpoint_token():
    ws, saved, p1 = _service(FULL)
    try:
        for token in (None, "", "wrong"):
            try:
                ws.drive_changes_webhook(x_goog_channel_token=token, x_goog_resource_state="sync")
            except _HTTPException as exc:
                assert exc.status_code == 401, token
            else:
                raise AssertionError(f"Drive push with token {token!r} accepted")
        resp = ws.drive_changes_webhook(x_goog_channel_token="drive-s3cret", x_goog_resource_state="sync")
        assert resp.status_code == 200
    finally:
        p1._restore_modules(saved)


def test_pipeline_dev_is_never_set_on_the_box():
    hits = [str(p) for p in (REPO / "systemd").rglob("*") if p.is_file() and "PIPELINE_DEV" in p.read_text()]
    assert not hits, f"PIPELINE_DEV in a systemd unit: {hits}"
    assert "PIPELINE_DEV" not in (REPO / ".env.example").read_text().replace("# PIPELINE_DEV", "")


def main() -> int:
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
