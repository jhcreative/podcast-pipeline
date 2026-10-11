"""
test_blog02b_tailor_write.py — BLOG-02b: the Tailor write, against a FAKED Tailor.

Run: python3 test_blog02b_tailor_write.py   (plain asserts, no pytest, no network)

The fake (FakeTailor) is an HTTP transport that answers the way Tailor v0.9.94 answers,
read from its source (templates/backend/, ~/jhc/tailor-v0995 @ eaceb24f):
  POST /api/auth/login   → 200 + Set-Cookie tailor_session, or 401    routers/auth.py
  GET  /api/auth/me      → {email, role}                               routers/auth.py whoami
  POST /api/auth/logout  → 204, ends the session                       routers/auth.py logout
  GET  /api/pages/blog/{slug}?preview=1 → drafts too when authed       routers/pages.py get_child_page
  POST /api/pages        → 201 draft, owner = caller; dup slug → 422   routers/pages.py create_page
  PUT  /api/pages/blog/{slug} → owner-only (403); byline change → 403  routers/pages.py _apply_update
  POST /api/media        → author only onto an owned page (0216)       routers/media.py _authorize_upload
Every request is recorded, so a test asserts on what was SENT — bodies, methods, hosts.

Covers (Orly's C-numbers; story AC-numbers):
  C-1 role check            test_role_not_author_stops
  C-2 logout in finally     test_logout_after_exception, test_happy_path_created_auto_cover
  C-3 pinned transport      test_base_url_pinning, test_redirect_not_followed, test_url_status_pinned
  C-4 no secret in output   test_sentinel_secret_never_output
  C-5 login/supersede/429   test_login_401_no_retry, test_supersede_one_relogin, test_429_stops
  C-6 one write at a time   test_lock_busy
  C-7 our own record        test_collision_not_ours_stops, test_recorded_post_gone_stops,
                            test_recorded_slug_now_other_page_stops
  C-8 never publishes       test_bodies_never_carry_status_published_at_byline
  C-9 published = hands off test_published_post_skipped, test_published_between_get_and_put,
                            test_never_deletes
  C-11 hosts in the alert   test_happy_path_created_auto_cover
  C-13 tags exactly         test_create_body_shape
  AC-3 gate before any call test_gate_failure_makes_no_call, test_pf_gate_failure_alerts_no_call
  AC-4/Cover ruling         test_happy_path_created_auto_cover, test_manual_cover_on_403
  AC-5 idempotent           test_rerun_updates_in_place_one_upload, test_changed_tile_uploads_again,
                            test_transient_then_rerun_same_post
  AC-6 URL from the API     test_url_is_from_returned_slug, test_pf_derive_blog_url_delegates
  AC-9 blog_post.json       test_pf_writes_post_and_returns_api_url
  OPEN-1 200 check          test_post_live_check
  byline                    test_byline_warning_when_not_heather
  Oliver #1 deploy FILES       test_deploy_files_carry_every_local_import
"""

import copy
import importlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import types
import uuid
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "tools"))  # publish_blog_post, create_thank_you_draft (SHARE-01)
FIXTURES = WORKTREE / "tests" / "fixtures" / "blog02"

import blog_post
import blog_publish
import content_gates
import tailor_client

_TEMP_DIRS = []


def _tmpdir(prefix="blog02b-"):
    """A temp dir removed when the run ends (F-12: tests leave nothing behind)."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_DIRS.append(d)
    return d


def _cleanup():
    import shutil
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


EMAIL = "blog-pipeline@rockstarblends.test"
# Sentinels: if either string ever appears in a log record, an exception, an alert or a
# Drive artifact, C-4 is broken.
PASSWORD = "SENTINEL-PASSWORD-7f3a9c"
COOKIE_PREFIX = "SENTINEL-COOKIE-"
API = "https://rockstarblends.com"
PUBLIC = "https://rockstarblends.com/blog"
LISTEN = {
    "spotify_url": "https://open.spotify.com/episode/0SynthEp31AbCdEfGhJkLm",
    "apple_url": "https://podcasts.apple.com/us/podcast/bliss-is-the-system-the-ecs-estrogen/id1785708777?i=1000000000031",
}


# ── the fake Tailor ────────────────────────────────────────────────────────────


def _err(status, code):
    return status, [("Content-Type", "application/json")], json.dumps(
        {"error": {"code": code, "message": "canned"}}).encode()


class FakeTailor:
    """An HTTP transport that behaves like Tailor v0.9.94 for the calls the pipeline makes.
    Each rule below cites the source it mirrors (templates/backend/, eaceb24f)."""

    MAX_UPLOAD_BYTES = 20_000          # stands in for settings.MAX_UPLOAD_BYTES (413 above it)
    AUTHOR_UPLOADS_PER_HOUR = 30       # core/rate_limit.py author_upload_limiter
    TAGS = frozenset({"podcast"})      # existing tags; an unknown slug → 422 (_reconcile_tags)
    MODULE_PILLARS = frozenset({"blog"})  # /blog is page_mode=module (create needs the opt-in)

    def __init__(self, *, role="author", upload=201, account_name=("Heather", "Reilly Hiemstra"),
                 create_suffix=""):
        self.role, self.upload, self.account_name = role, upload, account_name
        self.create_suffix = create_suffix  # the server stores a different slug than proposed
        self.requests = []          # every request, as sent
        self.pages = {}             # slug → page (children of /blog)
        self.media = []
        self.session = None
        self.logins = 0
        self.forced = []            # [(method, path-regex, response)] consumed first-match
        self.on_get = None          # hook(page) before a GET answers — for races
        self.supersede_at = []      # [(method, path-regex)]: rotate the session BEFORE that request
        self.on_logout = None       # hook() when logout arrives — e.g. to probe the lock
        self.writes = 0

    # helpers for tests
    def force(self, method, path_re, response, times=1):
        for _ in range(times):
            self.forced.append((method, re.compile(path_re), response))

    def supersede_before(self, method, path_re):
        """Someone else logs in as this account just before the matching request: our
        cookie is stale from then on (core/sessions.py — one session per account)."""
        self.supersede_at.append((method, re.compile(path_re)))

    def other_page(self, slug, status="draft"):
        """A page someone ELSE owns (Heather's), e.g. the migrated E31."""
        self.pages[slug] = self._page(slug, owner="host@example.test", status=status)
        return self.pages[slug]

    def calls(self, method=None, path_re=None):
        return [r for r in self.requests
                if (method is None or r["method"] == method)
                and (path_re is None or re.search(path_re, r["path"]))]

    def _page(self, slug, owner, status="draft", title="t"):
        return {"id": str(uuid.uuid4()), "slug": slug, "title": title, "parent_slug": "blog",
                "status": status, "owner": owner, "blocks": [], "byline": None,
                "thumbnail_media_id": None, "hero_media_id": None, "hero_layout": "simple_text",
                "tags": [], "published_at": None,
                "content_mode": "code"}  # PageRead.content_mode: default "code" (schemas/pages.py)

    def _read(self, page, status=200):
        out = {k: v for k, v in page.items() if k != "owner"}  # SEC-04: no owner email on the wire
        if page["owner"] == EMAIL:
            first, last = self.account_name
        else:
            first, last = "Heather", "Reilly Hiemstra"
        out["author_display_name"] = {"first_name": first, "last_name": last}
        return status, [("Content-Type", "application/json")], json.dumps(out).encode()

    def _authed(self, headers):
        """core/auth.py: a cookie counts only while it is the account's CURRENT session."""
        return self.session is not None and headers.get("Cookie") == f"tailor_session={self.session}"

    @staticmethod
    def _pydantic_422(field):
        """FastAPI request validation: `{"detail": [...]}`, NO `error` envelope, and the
        item echoes the input (why the client never reads it)."""
        return 422, [("Content-Type", "application/json")], json.dumps(
            {"detail": [{"type": "missing", "loc": ["body", field], "msg": "Field required",
                         "input": "echoed"}]}).encode()

    # the transport interface
    def request(self, method, scheme, host, port, path, headers, body):
        self.requests.append({"method": method, "scheme": scheme, "host": host, "port": port,
                              "path": path, "headers": dict(headers), "body": body})
        for m, rx in list(self.supersede_at):
            if m == method and rx.search(path):
                self.supersede_at.remove((m, rx))
                self.session = "someone-else-" + uuid.uuid4().hex
        for i, (m, rx, resp) in enumerate(self.forced):
            if m == method and rx.search(path):
                self.forced.pop(i)
                return resp
        route = path.split("?")[0]
        authed = self._authed(headers)

        # ── auth (routers/auth.py) ──
        if method == "POST" and route == "/api/auth/login":
            self.logins += 1
            creds = json.loads(body)
            if set(creds) != {"email", "password"}:
                return self._pydantic_422("password")
            if creds == {"email": EMAIL, "password": PASSWORD}:
                self.session = COOKIE_PREFIX + uuid.uuid4().hex  # rotation: one session per account
                return 200, [("Set-Cookie", f"tailor_session={self.session}; HttpOnly; Secure; SameSite=Strict")], \
                    json.dumps({"email": EMAIL, "role": self.role}).encode()
            return _err(401, "INVALID_CREDENTIALS")
        if method == "POST" and route == "/api/auth/logout":
            if self.on_logout:
                self.on_logout()
            if authed:
                self.session = None  # end_session: the row is deleted
            return 204, [("Set-Cookie", "tailor_session=; Max-Age=0")], b""

        # ── public reads: a stale cookie is ANONYMOUS, not 401 (authenticated_claims_or_none) ──
        m = re.fullmatch(r"/api/pages/blog/([a-z0-9-]+)", route)
        if method == "GET" and m:
            page = self.pages.get(m.group(1))
            if page is None or (page["status"] != "published"
                                and not ("preview=1" in path and authed)):
                return _err(404, "NOT_FOUND")
            if self.on_get:
                self.on_get(page)
            return self._read(page)
        pm = re.fullmatch(r"/blog/([a-z0-9-]+)", route)  # the rendered public page
        if method == "GET" and pm:
            page = self.pages.get(pm.group(1))
            return (200, [], b"<html>") if page and page["status"] == "published" else (404, [], b"")
        mm = re.fullmatch(r"/api/media/([0-9a-f-]+)", route)  # serve_media: public
        if method == "GET" and mm:
            return (200, [], b"\xff\xd8\xff") if any(x["id"] == mm.group(1) for x in self.media) \
                else _err(404, "NOT_FOUND")

        # ── require_user from here on ──
        if not authed:
            return _err(401, "UNAUTHENTICATED")
        if method == "GET" and route == "/api/auth/me":
            return 200, [], json.dumps({"email": EMAIL, "role": self.role}).encode()
        if method == "POST" and route == "/api/pages":
            b = json.loads(body)
            if b.get("parent_slug") in self.MODULE_PILLARS and not b.get("under_module_pillar"):
                return _err(422, "MODULE_PILLAR_PARENT")
            if self.role == "author" and (b.get("html") or b.get("js") or b.get("scss")):
                return _err(403, "CONTENT_PRIVILEGE")
            if self.role == "author" and b.get("published_at") is not None:
                return _err(403, "CONTENT_PRIVILEGE")
            if not set(b.get("tag_slugs") or []) <= self.TAGS:
                return _err(422, "VALIDATION_ERROR")
            slug = b["slug"] + self.create_suffix
            if slug in self.pages or len(b["slug"]) > 64:
                return _err(422, "VALIDATION_ERROR")
            page = self._page(slug, owner=EMAIL, title=b["title"])
            page["tags"] = [{"slug": t} for t in b.get("tag_slugs") or []]
            self.pages[slug] = page
            return self._read(page, status=201)
        if method == "PUT" and m:
            b = json.loads(body)
            for required in ("title", "html", "js", "scss"):  # PageUpdate: required fields
                if required not in b:
                    return self._pydantic_422(required)
            page = self.pages.get(m.group(1))
            if page is None:
                return _err(404, "NOT_FOUND")
            if self.role == "author" and page["owner"] != EMAIL:
                return _err(403, "FORBIDDEN")                    # require_page_owner
            if self.role == "author" and (b["html"] or b["js"] or b["scss"]):
                return _err(403, "CONTENT_PRIVILEGE")            # core/content_privilege.py
            if "published_at" in b and self.role == "author":
                return _err(403, "CONTENT_PRIVILEGE")            # explicit published_at
            if "byline" in b and b["byline"] != page["byline"] and self.role == "author":
                return _err(403, "CONTENT_PRIVILEGE")            # _require_editor_for_byline
            if b.get("tag_slugs") is not None and not set(b["tag_slugs"]) <= self.TAGS:
                return _err(422, "VALIDATION_ERROR")
            for k in ("title", "blocks", "hero_media_id", "hero_layout", "thumbnail_media_id",
                      "meta_description", "layout", "status", "content_mode"):
                if k in b:
                    page[k] = b[k]
            if b.get("tag_slugs") is not None:
                page["tags"] = [{"slug": t} for t in b["tag_slugs"]]
            self.writes += 1
            page["updated_at"] = f"2026-09-24T00:00:{self.writes:02d}Z"
            return self._read(page)
        if method == "POST" and route == "/api/media":
            if self.upload != 201:
                return _err(self.upload, {403: "FORBIDDEN", 429: "RATE_LIMITED"}.get(self.upload, "X"))
            found = re.search(rb'name="page_id"\r\n\r\n([0-9a-f-]+)\r\n', body)
            if self.role == "author" and not found:
                return _err(403, "FORBIDDEN")                    # author needs a page_id
            pid = found.group(1).decode() if found else None
            owner = next((pg["owner"] for pg in self.pages.values() if pg["id"] == pid), None)
            if pid and owner is None:
                return _err(404, "NOT_FOUND")                    # _page_or_404
            if self.role == "author" and owner != EMAIL:
                return _err(403, "FORBIDDEN")                    # require_page_owner
            if sum(1 for x in self.media if x.get("by_author")) >= self.AUTHOR_UPLOADS_PER_HOUR:
                return _err(429, "RATE_LIMITED")
            file_part = body.split(b'name="file"', 1)[1].split(b"\r\n\r\n", 1)[1]
            if len(file_part) > self.MAX_UPLOAD_BYTES:
                return _err(413, "FILE_TOO_LARGE")
            if not file_part.startswith((b"\xff\xd8\xff", b"\x89PNG", b"RIFF")):
                return _err(415, "UNSUPPORTED_MEDIA_TYPE")        # sniff_image
            mid = str(uuid.uuid4())
            self.media.append({"id": mid, "page_id": pid, "by_author": self.role == "author"})
            return 201, [], json.dumps({"id": mid, "url": f"/api/media/{mid}.jpg"}).encode()
        return _err(405, "METHOD_NOT_ALLOWED")


# ── fixtures ───────────────────────────────────────────────────────────────────


def _inputs(ep="E31"):
    d = FIXTURES / ep
    apple = (d / "apple_summary.md").read_text(encoding="utf-8")
    quotes = json.loads((d / "quotes.json").read_text(encoding="utf-8"))["quotes"]
    faq = (d / "faq.md").read_text(encoding="utf-8")
    return apple, quotes, faq


def _post(ep="E31"):
    apple, quotes, faq = _inputs(ep)
    meta = {"episode_title": blog_post.title_from_apple_summary(apple),
            "episode_number": int(ep[1:]), **LISTEN}
    return blog_post.build_post(apple, quotes, faq, meta), quotes


class Env:
    """A temp state dir + tile + fake, and a publish() that uses them."""

    def __init__(self, **fake_kw):
        self.tmp = _tmpdir()
        self.fake = FakeTailor(**fake_kw)
        self.tile = self.tmp / "E31-saffi-tile.jpg"
        self.tile.write_bytes(b"\xff\xd8\xff\xe0" + b"tile-bytes" * 100)

    def publish(self, post=None, quotes=None, **kw):
        if post is None:
            post, quotes = _post()
        args = dict(episode_code="E31", api_base=API, public_base=PUBLIC,
                    credentials=(EMAIL, PASSWORD), record_path=self.tmp / "blog_posts.json",
                    lock_path=self.tmp / "tailor_blog.lock", tile_path=self.tile,
                    transport=self.fake, lock_timeout=0)
        args.update(kw)
        return blog_publish.publish(post, quotes, **args)


def _raises(exc_type, fn):
    try:
        fn()
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__}")


def _body(r):
    return json.loads(r["body"]) if r["body"] and r["headers"].get("Content-Type") == "application/json" else None


# ── tests ──────────────────────────────────────────────────────────────────────


def test_happy_path_created_auto_cover():
    e = Env()
    res = e.publish()
    f = e.fake
    assert res.outcome == "created" and res.status == "draft", res
    assert res.cover_mode == blog_publish.COVER_AUTO and res.cover_media_id == f.media[0]["id"]
    order = [(r["method"], r["path"].split("?")[0]) for r in f.requests]
    slug = res.slug
    assert order == [
        ("POST", "/api/auth/login"), ("GET", "/api/auth/me"),
        ("GET", f"/api/pages/blog/{slug}"),          # no record → is the slug free?
        ("GET", "/api/auth/me"),                       # F-1: a 404 is believed only if we're current
        ("POST", "/api/pages"),                        # create the shell
        ("POST", "/api/media"),                        # the tile, onto our page
        ("GET", f"/api/pages/blog/{slug}"),          # C-9 status right before the PUT
        ("PUT", f"/api/pages/blog/{slug}"),
        ("POST", "/api/auth/logout"),                  # C-2
    ], order
    put = _body(f.calls("PUT")[0])
    assert put["thumbnail_media_id"] == res.cover_media_id, "the tile is the Cover Image"
    assert put["hero_media_id"] is None and put["hero_layout"] == "simple_text", "no hero (ruling)"
    assert put["content_mode"] == "blocks" and len(put["blocks"]) == 16
    assert put["html"] == put["js"] == put["scss"] == "", "author may not send buffers"
    assert f.session is None, "logged out"
    assert res.hosts == ["open.spotify.com", "podcasts.apple.com", "www.amazon.com"], res.hosts
    subject, body = blog_publish.draft_alert(res, "E31")
    assert "automatic" in subject and "Cover Image: set automatically" in body
    for h in res.hosts:
        assert f"• {h}" in body, h
    print("  AC-4/cover: login → me → slug free → POST → upload → GET → PUT → logout; draft, "
          "16 blocks, thumbnail = tile, hero null; alert names automatic cover + 3 hosts ✓")


def test_create_body_shape():
    e = Env()
    res = e.publish()
    post, _ = _post()
    create = _body(e.fake.calls("POST", r"^/api/pages$")[0])
    assert create["tag_slugs"] == ["podcast"], "C-13: exactly ['podcast']"
    assert create["under_module_pillar"] is True, "/blog is a module pillar"
    assert create["parent_slug"] == "blog"
    assert create["slug"] == post["slug"] == blog_post.propose_slug(
        blog_post.title_from_apple_summary(_inputs()[0])) and len(create["slug"]) <= 64
    assert "blocks" not in create and "content_mode" not in create, "create takes the shell only"
    put = _body(e.fake.calls("PUT")[0])
    assert put["tag_slugs"] == ["podcast"] and "parent_slug" not in put, "no reparent on PUT"
    assert res.slug == create["slug"]
    print("  create body: tags ['podcast'], under_module_pillar, 64-cap propose_slug, shell only; "
          "PUT carries no parent_slug ✓")


def test_bodies_never_carry_status_published_at_byline():
    e = Env()
    e.publish()
    e.publish()  # an update run too
    sent = [_body(r) for r in e.fake.calls() if r["method"] in ("POST", "PUT")
            and r["path"].startswith("/api/pages")]
    assert len(sent) == 3, len(sent)  # one POST, two PUTs
    for b in sent:
        for k in ("status", "published_at", "byline"):
            assert k not in b, f"C-8: body carries {k!r}"
    # The client refuses such a body before it exists — a second line of defence.
    c = tailor_client.TailorClient(API, transport=e.fake)
    for k in ("status", "published_at", "byline"):
        _raises(ValueError, lambda k=k: c.create_page({"title": "x", k: "published"}))
        _raises(ValueError, lambda k=k: c.update_child("blog", "x", {"title": "x", k: None}))
    print("  C-8: no POST/PUT body (create + 2 updates) carries status/published_at/byline; "
          "the client refuses one that does ✓")


def test_url_is_from_returned_slug():
    # Tailor stores (and returns) a slug that differs from the proposal: the URL must be
    # built from the ANSWER, not the guess.
    e = Env(create_suffix="-x")
    post, quotes = _post()
    res = e.publish(post, quotes)
    assert res.slug == post["slug"] + "-x" and res.url == f"{PUBLIC}/{post['slug']}-x", res.url
    assert e.fake.calls("PUT", re.escape(post["slug"] + "-x") + "$"), "PUT went to the returned slug"
    print("  AC-6: blog_post_url = public base + the slug the API RETURNED ✓")


def test_manual_cover_on_403():
    e = Env(upload=403)
    res = e.publish()
    assert res.outcome == "created" and res.cover_mode == blog_publish.COVER_MANUAL
    put = _body(e.fake.calls("PUT")[0])
    assert "thumbnail_media_id" not in put, "manual mode leaves the Cover Image to Heather"
    assert put["hero_media_id"] is None and len(put["blocks"]) == 16, "the draft is still whole"
    subject, body = blog_publish.draft_alert(res, "E31", tile_link="https://drive.google.com/file/d/T/view")
    assert "manual" in subject and "Set Cover Image in the Post tab" in body
    assert "https://drive.google.com/file/d/T/view" in body and "403" in body
    # a re-run on the same old instance: still manual, still one post, cover untouched
    res2 = e.publish()
    assert res2.outcome == "updated" and len(e.fake.calls("POST", r"^/api/pages$")) == 1
    assert "thumbnail_media_id" not in _body(e.fake.calls("PUT")[1])
    print("  cover fallback: upload 403 → manual mode; blocks still written; PUT omits the "
          "cover; alert says 'Set Cover Image in the Post tab' + Drive tile link ✓")


def test_rerun_updates_in_place_one_upload():
    e = Env()
    r1 = e.publish()
    r2 = e.publish()
    f = e.fake
    assert (r1.outcome, r2.outcome) == ("created", "updated")
    assert r1.page_id == r2.page_id and len([p for p in f.pages.values() if p["owner"] == EMAIL]) == 1
    assert len(f.calls("POST", r"^/api/pages$")) == 1, "one POST in total"
    assert len(f.media) == 1 and len(f.calls("POST", r"^/api/media$")) == 1, "one cover upload in total"
    assert _body(f.calls("PUT")[1])["thumbnail_media_id"] == r1.cover_media_id, "the recorded tile reused"
    assert "reused" in r2.cover_note
    rec = json.loads((e.tmp / "blog_posts.json").read_text())["posts"][f"{API}|E31"]
    assert rec["page_id"] == r1.page_id and rec["slug"] == r1.slug and rec["cover_media_id"] == r1.cover_media_id
    assert PASSWORD not in (e.tmp / "blog_posts.json").read_text()
    print("  AC-5: second run updates the SAME draft — one post, one POST, one upload; the "
          "record holds {page_id, slug, cover} keyed by API base + episode ✓")


def test_changed_tile_uploads_again():
    e = Env()
    e.publish()
    e.tile.write_bytes(b"\xff\xd8\xff\xe0 a new tile")
    r2 = e.publish()
    assert len(e.fake.media) == 2 and r2.cover_media_id == e.fake.media[1]["id"]
    print("  cover: a CHANGED tile is uploaded again (identical tile is not) ✓")


def test_collision_not_ours_stops():
    e = Env()
    post, quotes = _post()
    e.fake.other_page(post["slug"])  # e.g. the migrated E31, owned by Heather
    exc = _raises(blog_publish.BlogStop, lambda: e.publish(post, quotes))
    assert "Not adopting it" in str(exc)
    assert not e.fake.calls("POST", r"^/api/pages$") and not e.fake.calls("PUT"), "no write"
    assert not e.fake.calls("POST", r"^/api/media"), "no upload"
    assert not (e.tmp / "blog_posts.json").exists(), "no record of a page we don't own"
    assert e.fake.calls("POST", r"logout"), "C-2"
    print("  C-7: proposed slug taken by a page we have no record of → STOP, no POST/PUT/upload, "
          "never adopted ✓")


def test_recorded_post_gone_stops():
    e = Env()
    r1 = e.publish()
    del e.fake.pages[r1.slug]  # Heather renamed or deleted it
    exc = _raises(blog_publish.BlogStop, lambda: e.publish())
    assert "Not creating a second post" in str(exc)
    assert len(e.fake.calls("POST", r"^/api/pages$")) == 1, "no second create"
    print("  C-7: recorded slug now 404 → STOP, no second post ✓")


def test_recorded_slug_now_other_page_stops():
    e = Env()
    r1 = e.publish()
    e.fake.pages[r1.slug]["id"] = str(uuid.uuid4())  # a different page now holds the slug
    _raises(blog_publish.BlogStop, lambda: e.publish())
    assert len(e.fake.calls("PUT")) == 1, "no PUT to the other page"
    print("  C-7: recorded slug now names a different page id → STOP ✓")


def test_published_post_skipped():
    e = Env()
    r1 = e.publish()
    e.fake.pages[r1.slug]["status"] = "published"  # Heather published it
    puts, uploads = len(e.fake.calls("PUT")), len(e.fake.media)
    r2 = e.publish()
    assert r2.outcome == "skipped_published" and r2.url == r1.url
    assert len(e.fake.calls("PUT")) == puts and len(e.fake.media) == uploads, "hands off"
    subject, body = blog_publish.draft_alert(r2, "E31")
    assert "already published" in subject and r1.url in body
    print("  AC-5/C-9: a PUBLISHED post → skip + alert, no PUT, no upload, URL kept ✓")


def test_published_between_get_and_put():
    e = Env()
    e.publish()
    gets = {"n": 0}

    def publish_on_second_get(page):
        gets["n"] += 1
        if gets["n"] == 2:
            page["status"] = "published"

    e.fake.on_get = publish_on_second_get
    r2 = e.publish()
    assert r2.outcome == "skipped_published" and len(e.fake.calls("PUT")) == 1
    print("  C-9: published between the record GET and the PUT → the pre-PUT GET catches it, "
          "no PUT ✓")


def test_never_deletes():
    e = Env()
    e.publish(); e.publish()
    assert not e.fake.calls("DELETE")
    assert not any("delete" in n.lower() for n in dir(tailor_client.TailorClient) if not n.startswith("__"))
    print("  C-9: no DELETE was sent, and the client has no delete method ✓")


def test_role_not_author_stops():
    for role in ("editor", "admin", "agency"):
        e = Env(role=role)
        exc = _raises(blog_publish.BlogStop, lambda e=e: e.publish())
        assert repr(role) in str(exc) and "not 'author'" in str(exc)
        paths = [r["path"].split("?")[0] for r in e.fake.requests]
        assert paths == ["/api/auth/login", "/api/auth/me", "/api/auth/logout"], paths
    print("  C-1: role editor/admin/agency → STOP after /me, nothing else, logged out ✓")


def test_logout_after_exception():
    e = Env()
    e.fake.force("POST", r"^/api/pages$", (500, [], b"boom"))
    exc = _raises(tailor_client.TailorError, lambda: e.publish())
    assert not isinstance(exc, blog_publish.BlogStop) and exc.status == 500 and not exc.answered
    assert e.fake.requests[-1]["path"] == "/api/auth/logout" and e.fake.session is None
    print("  C-2: a 5xx mid-run raises (transient, retryable) — and logout still ran ✓")


def test_transient_then_rerun_same_post():
    e = Env()
    e.fake.force("PUT", r"^/api/pages/blog/", (502, [], b""))
    _raises(tailor_client.TailorError, lambda: e.publish())
    r2 = e.publish()  # the worker's retry
    assert r2.outcome == "updated" and len(e.fake.calls("POST", r"^/api/pages$")) == 1
    assert len(e.fake.media) == 1, "the retry reused the tile uploaded before the 502"
    print("  AC-5: a 502 on the PUT, then the retry → updates the SAME draft (record written "
          "before the PUT), one upload ✓")


def test_login_401_no_retry():
    e = Env()
    exc = _raises(blog_publish.BlogStop,
                  lambda: e.publish(credentials=(EMAIL, "wrong-password")))
    assert e.fake.logins == 1, "C-5: exactly one login attempt"
    assert "401" in str(exc) and "INVALID_CREDENTIALS" in str(exc)
    assert [r["path"] for r in e.fake.requests] == ["/api/auth/login"], "nothing after a refused login"
    print("  C-5: login 401 → STOP after ONE attempt, nothing else sent ✓")


def test_supersede_one_relogin():
    e = Env()
    # Someone logs in elsewhere mid-run: the session rotates, our cookie is stale.
    e.fake.force("POST", r"^/api/pages$", _err(401, "SESSION_SUPERSEDED"))
    res = e.publish()
    assert res.outcome == "created" and e.fake.logins == 2, "one re-login, then the call retried"
    # N-2: the recovery is not silent — its own line and a subject suffix
    subject, body = blog_publish.draft_alert(res, "E31")
    assert res.superseded and subject.endswith("— SESSION SUPERSEDED")
    assert blog_publish.SUPERSEDED_LINE in body
    clean = Env()
    r_clean = clean.publish()
    assert not r_clean.superseded and "SUPERSEDED" not in "".join(blog_publish.draft_alert(r_clean, "E31"))
    e2 = Env()
    e2.fake.force("POST", r"^/api/pages$", _err(401, "SESSION_SUPERSEDED"), times=2)
    exc = _raises(blog_publish.BlogStop, lambda: e2.publish())
    assert e2.fake.logins == 2, "never a third login"
    assert blog_publish.SUPERSEDED_LINE in str(exc), "N-2: the stop names the supersede"
    assert "no retry (C-5)" in str(exc) and "create refused" not in str(exc), \
        "a refused re-login is a C-5 auth stop, not reported as a content refusal"
    print("  C-5: mid-run 401 → one re-login + one retry; a second 401 → STOP ✓")


def test_429_stops():
    e = Env()
    e.fake.force("POST", r"^/api/auth/login$", _err(429, "RATE_LIMITED"))
    exc = _raises(blog_publish.BlogStop, lambda: e.publish())
    assert "429" in str(exc) and e.fake.logins == 0
    e2 = Env()
    e2.fake.force("GET", r"^/api/pages/blog/", _err(429, "RATE_LIMITED"))
    _raises(blog_publish.BlogStop, lambda: e2.publish())
    assert not e2.fake.calls("POST", r"^/api/pages$")
    print("  C-5: 429 on login or a later call → STOP, no retry ✓")


def test_create_and_put_refusals_stop():
    e = Env()
    e.fake.force("POST", r"^/api/pages$", _err(422, "VALIDATION_ERROR"))
    exc = _raises(blog_publish.BlogStop, lambda: e.publish())
    assert "nothing was written" in str(exc) and not (e.tmp / "blog_posts.json").exists()
    e2 = Env()
    e2.fake.force("PUT", r"^/api/pages/blog/", _err(403, "FORBIDDEN"))
    exc = _raises(blog_publish.BlogStop, lambda: e2.publish())
    assert "403" in str(exc) and "re-run" in str(exc)
    print("  create 422 → STOP (no record); PUT 403 → STOP naming the draft ✓")


def test_gate_failure_makes_no_call():
    e = Env()
    post, quotes = _post()
    bad = copy.deepcopy(post)
    bad["blocks"][0]["content"][2]["href"] = "APPLE-URL-PENDING"
    _raises(content_gates.GateFailure, lambda: e.publish(bad, quotes))
    bad2 = copy.deepcopy(post)
    bad2["blocks"][3]["content"][0]["inlines"][0]["href"] = "http://insecure.example/"
    _raises(content_gates.GateFailure, lambda: e.publish(bad2, quotes))
    bad3 = copy.deepcopy(post)
    bad3["tag_slugs"] = ["podcast", "news"]
    _raises(content_gates.GateFailure, lambda: e.publish(bad3, quotes))
    assert e.fake.requests == [], "AC-3: not one request"
    print("  AC-3: placeholder URL / http inline link / extra tag → GateFailure before ANY request ✓")


def test_lock_busy():
    import fcntl
    e = Env()
    holder = open(e.tmp / "tailor_blog.lock", "a+")  # noqa: SIM115 — closed in finally
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        _raises(blog_publish.BlogBusy, lambda: e.publish())
        assert e.fake.requests == [], "no login while another write holds the lock"
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
    assert e.publish().outcome == "created"
    print("  C-6: lock held elsewhere → BlogBusy (retryable) with no request; free → proceeds ✓")


def test_base_url_pinning():
    ok = {"https://rockstarblends.com": False, "https://rockstarblends.com/": False,
          "http://localhost:8695": True, "http://127.0.0.1:8695": True}
    for base, loop in ok.items():
        tailor_client.TailorClient(base, allow_loopback=loop, transport=FakeTailor())
    refused = [
        ("http://rockstarblends.com", False),              # not https
        ("https://rockstarblends.com.evil.example", False),  # lookalike
        ("https://www.rockstarblends.com", False),        # not on the list
        ("https://evil.example", False),
        ("http://localhost:8695", False),                  # loopback without the opt-in
        ("ftp://localhost", True),
        ("https://user:pw@rockstarblends.com", False),     # userinfo
        ("https://rockstarblends.com/api", False),         # not the site root
        ("", False),
    ]
    for base, loop in refused:
        fake = FakeTailor()
        _raises(tailor_client.TailorConfigError,
                lambda base=base, loop=loop, fake=fake:
                tailor_client.TailorClient(base, allow_loopback=loop, transport=fake))
        assert fake.requests == []
    print(f"  C-3: {len(ok)} bases accepted; {len(refused)} refused before any socket (http, "
          "lookalike, www, loopback w/o opt-in, userinfo, path) ✓")


def test_redirect_not_followed():
    e = Env()
    e.fake.force("GET", r"^/api/auth/me$",
                 (302, [("Location", "https://evil.example/steal")], b""))
    exc = _raises(blog_publish.BlogStop, lambda: e.publish())
    assert "redirect" in str(exc)
    assert {r["host"] for r in e.fake.requests} == {"rockstarblends.com"}, "never another host"
    assert e.fake.requests[-1]["path"] == "/api/auth/logout"
    print("  C-3: a 302 → error, not followed; every request stayed on rockstarblends.com ✓")


def test_url_status_pinned():
    f = FakeTailor()
    f.force("GET", r"^/blog/x$", (301, [("Location", "https://evil.example/")], b""))
    assert tailor_client.url_status(f"{PUBLIC}/x", transport=f) == 301, "301 is reported, not followed"
    assert "Cookie" not in f.requests[0]["headers"], "anonymous"
    for bad in ("http://rockstarblends.com/blog/x", "https://evil.example/blog/x"):
        _raises(tailor_client.TailorConfigError,
                lambda bad=bad: tailor_client.url_status(bad, transport=f))
    assert len(f.requests) == 1
    print("  C-3/OPEN-1: url_status reports a 301 without following it, sends no cookie, "
          "refuses http/unlisted hosts ✓")


def test_post_live_check():
    f = FakeTailor()
    f.force("GET", r"^/blog/live$", (200, [], b"<html>"))
    f.force("GET", r"^/blog/draft$", _err(404, "NOT_FOUND"))
    live, line = blog_publish.post_live_check(f"{PUBLIC}/live", transport=f)
    assert live and "HTTP 200" in line
    live, line = blog_publish.post_live_check(f"{PUBLIC}/draft", transport=f)
    assert not live and line.startswith("WARNING — post not published yet") and "HTTP 404" in line
    live, line = blog_publish.post_live_check("http://rockstarblends.com/blog/x", transport=f)
    assert not live and "not checked" in line
    assert len(f.requests) == 2, "the refused URL was never fetched"
    print("  OPEN-1: 200 → live; 404 → 'WARNING — post not published yet'; a refused URL is "
          "named, not fetched ✓")


def test_byline_warning_when_not_heather():
    e = Env(account_name=("Blog", "Pipeline"))
    res = e.publish()
    assert res.byline == "Blog Pipeline"
    _, body = blog_publish.draft_alert(res, "E31")
    assert "Byline: WARNING" in body and "'Blog Pipeline'" in body
    e2 = Env()
    _, body2 = blog_publish.draft_alert(e2.publish(), "E31")
    assert "Byline: Heather Reilly Hiemstra" in body2
    print("  byline: read back after the write; 'Blog Pipeline' → WARNING in the alert; "
          "'Heather Reilly Hiemstra' → plain ✓")


def test_credentials_sources():
    tmp = _tmpdir()
    f = tmp / "cred.env"
    f.write_text(f"# comment\nOTHER=zzz\nexport TAILOR_BLOG_EMAIL={EMAIL}\nTAILOR_BLOG_PASSWORD='{PASSWORD}'\n")
    assert blog_publish.load_credentials(env_file=f) == (EMAIL, PASSWORD)
    assert blog_publish.read_env_file(f) == {"TAILOR_BLOG_EMAIL": EMAIL, "TAILOR_BLOG_PASSWORD": PASSWORD}
    cd = tmp / "creds"; cd.mkdir()
    (cd / "tailor_blog").write_text(f"TAILOR_BLOG_EMAIL={EMAIL}\nTAILOR_BLOG_PASSWORD={PASSWORD}\n")
    assert blog_publish.load_credentials(environ={"CREDENTIALS_DIRECTORY": str(cd)}) == (EMAIL, PASSWORD)
    assert blog_publish.load_credentials(
        environ={"TAILOR_BLOG_EMAIL": EMAIL, "TAILOR_BLOG_PASSWORD": PASSWORD}) == (EMAIL, PASSWORD)
    exc = _raises(blog_publish.BlogStop,
                  lambda: blog_publish.load_credentials(environ={"TAILOR_BLOG_EMAIL": EMAIL}))
    assert "TAILOR_BLOG_PASSWORD" in str(exc) and EMAIL not in str(exc)
    print("  credential: explicit file / $CREDENTIALS_DIRECTORY/tailor_blog / env; allow-listed "
          "keys only; a missing key is named, no value shown ✓")


def test_sentinel_secret_never_output():
    """C-4: run every path with a sentinel password and sentinel cookies; capture every log
    record (DEBUG, root), every exception string, every alert, and the record file."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    texts = []
    try:
        e = Env()
        texts += list(blog_publish.draft_alert(e.publish(), "E31"))         # created
        texts += list(blog_publish.draft_alert(e.publish(), "E31"))         # updated
        e.fake.force("PUT", r"^/api/pages/blog/", _err(401, "X"), times=2)  # supersede ×2
        texts.append(str(_raises(blog_publish.BlogStop, lambda: e.publish())))
        e.fake.pages[next(iter(e.fake.pages))]["status"] = "published"
        texts += list(blog_publish.draft_alert(e.publish(), "E31"))         # skipped
        e2 = Env(upload=403)
        texts += list(blog_publish.draft_alert(e2.publish(), "E31"))        # manual cover
        e3 = Env()
        texts.append(str(_raises(blog_publish.BlogStop,
                                 lambda: e3.publish(credentials=(EMAIL, PASSWORD + "x")))))
        e4 = Env(role="editor")
        texts.append(str(_raises(blog_publish.BlogStop, lambda: e4.publish())))
        e5 = Env()
        e5.fake.force("POST", r"^/api/pages$", (500, [], PASSWORD.encode()))  # a body echoing it
        texts.append(str(_raises(tailor_client.TailorError, lambda: e5.publish())))
        records = [(x.tmp / "blog_posts.json") for x in (e, e2, e3, e4, e5)]
        texts += [p.read_text() for p in records if p.exists()]
        cookies = {c for x in (e, e2, e3, e4, e5)
                   for r in x.fake.requests for c in re.findall(COOKIE_PREFIX + r"[0-9a-f]+",
                                                               r["headers"].get("Cookie", ""))}
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    logs = buf.getvalue()
    assert cookies, "the fake issued cookies (the test is live)"
    assert "tailor POST /api/auth/login → 200" in logs, "the log does record the call"
    everything = logs + "\n".join(texts)
    assert PASSWORD not in everything, "C-4: the password appeared in output"
    assert COOKIE_PREFIX not in everything, "C-4: a session cookie appeared in output"
    assert "Set-Cookie" not in logs and "Cookie:" not in logs
    print(f"  C-4: sentinel password + {len(cookies)} session cookies absent from {len(logs)} chars "
          f"of DEBUG logs, {len(texts)} alerts/errors and the record files ✓")


# ── Phase 2 wiring (jobs/publish_finalize.py) ─────────────────────────────────


def _pf():
    """jobs.publish_finalize with its I/O-boundary modules stubbed (test_blog02's pattern);
    blog_publish, blog_post, content_gates and tailor_client are the REAL modules."""
    if "anthropic" not in sys.modules:
        anthropic = types.ModuleType("anthropic")
        anthropic.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
        sys.modules["anthropic"] = anthropic
    for name in ("calendar_client", "drive_client", "gmail_client", "publisher_email", "db"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules.pop("jobs.publish_finalize", None)
    import jobs.publish_finalize as pf
    importlib.reload(pf)
    return pf


class PFEnv:
    def __init__(self, **fake_kw):
        self.pf = _pf()
        self.fake = FakeTailor(**fake_kw)
        self.tmp = _tmpdir("blog02b-pf-")
        self.alerts, self.uploads = [], []
        sys.modules["gmail_client"].send_pipeline_alert = \
            lambda to, subject, body: self.alerts.append((subject, body))
        sys.modules["drive_client"].upload_file = \
            lambda path, folder, name, mime: self.uploads.append((name, Path(path).read_text(), mime))
        self.pf._BLOG_RECORD_PATH = self.tmp / "blog_posts.json"
        self.pf._BLOG_LOCK_PATH = self.tmp / "tailor_blog.lock"
        self._real_transport = tailor_client.HttpTransport
        tailor_client.HttpTransport = lambda: self.fake  # what publish() builds by default
        os.environ["TAILOR_BLOG_EMAIL"], os.environ["TAILOR_BLOG_PASSWORD"] = EMAIL, PASSWORD
        self.tile = self.tmp / "E31-tile.jpg"
        self.tile.write_bytes(b"\xff\xd8\xff tile")

    def close(self):
        tailor_client.HttpTransport = self._real_transport
        os.environ.pop("TAILOR_BLOG_EMAIL", None)
        os.environ.pop("TAILOR_BLOG_PASSWORD", None)

    def write(self, **over):
        apple, quotes, faq = _inputs()
        args = dict(work_dir=self.tmp, episode_code="E31", episode_number=31,
                    episode_title=blog_post.title_from_apple_summary(apple),
                    apple_summary_text=apple, quotes=quotes, faq_text=faq, tile_local=self.tile,
                    tile_file={"id": "DRIVE-TILE-ID", "name": "E31-tile.jpg"}, file_map={},
                    episode_folder_id="FOLDER", **LISTEN)
        args.update(over)
        return self.pf._write_blog_post(**args)


def test_pf_writes_post_and_returns_api_url():
    e = PFEnv(create_suffix="-x")  # Tailor returns a slug that differs from the proposal
    try:
        url = e.write()
        post, _ = _post()
        assert url == f"https://rockstarblends.com/blog/{post['slug']}-x", url
        assert url != e.pf._derive_blog_url(blog_post.title_from_apple_summary(_inputs()[0])), \
            "M18b: Phase 2 returns the API's slug, never the derived guess"
        assert e.pf._BLOG_BASE_URL == "https://rockstarblends.com/blog", "AC-6 default"
        [(name, text, mime)] = e.uploads
        assert name == "blog_post.json" and mime == "application/json"
        assert json.loads(text) == post, "AC-9: the built post, exactly"
        assert PASSWORD not in text and COOKIE_PREFIX not in text
        [(subject, _body_text)] = e.alerts
        assert "blog draft ready" in subject and "automatic" in subject
        assert e.fake.calls("PUT") and e.fake.session is None
    finally:
        e.close()
    print("  Phase 2 Step 6: blog_post.json (the built post) → Drive; draft written; URL = "
          "…/blog/<returned slug>; one draft-ready alert ✓")


def test_pf_gate_failure_alerts_no_call():
    e = PFEnv()
    try:
        exc = _raises(e.pf.PermanentFailure, lambda: e.write(apple_url="APPLE-URL-PENDING"))
        assert "Apple" in str(exc) or "apple" in str(exc).lower()
        assert e.fake.requests == [], "AC-3: no Tailor call"
        assert e.uploads == [], "nothing to Drive either"
        [(subject, body)] = e.alerts
        assert "refused before any write" in subject and "reprocess_episode.py --episode 31" in body
    finally:
        e.close()
    print("  Phase 2 AC-3: gate failure → send_pipeline_alert, NO Tailor request, "
          "PermanentFailure (Phase 2 stops before reels/thank-you) ✓")


def test_pf_stop_alerts_and_skip_continues():
    e = PFEnv()
    try:
        post, _ = _post()
        e.fake.other_page(post["slug"])
        _raises(e.pf.PermanentFailure, lambda: e.write())
        assert "NOT written" in e.alerts[-1][0] and "Not adopting it" in e.alerts[-1][1]
        del e.fake.pages[post["slug"]]
        e.write()
        e.fake.pages[post["slug"]]["status"] = "published"
        url = e.write()
        assert url.endswith(post["slug"]) and "already published" in e.alerts[-1][0]
    finally:
        e.close()
    print("  Phase 2: BlogStop → alert + PermanentFailure; a published post → alert, skip, "
          "Phase 2 carries on with its real URL ✓")


def test_pf_missing_credential_stops_without_call():
    e = PFEnv()
    try:
        os.environ.pop("TAILOR_BLOG_PASSWORD")
        _raises(e.pf.PermanentFailure, lambda: e.write())
        assert e.fake.requests == [] and "TAILOR_BLOG_PASSWORD" in e.alerts[-1][1]
    finally:
        e.close()
    print("  Phase 2: missing credential → alert naming the key, no request ✓")


def test_pf_derive_blog_url_delegates():
    pf = _pf()
    t = "Don't Wait for the Stop | Optimize Hormones Before Perimenopause"
    assert pf._derive_blog_url(t, 29) == f"{pf._BLOG_BASE_URL}/{blog_post.propose_slug(t)}"
    assert "genx-hits-menopause" not in pf._derive_blog_url(t, 29)
    src = (WORKTREE / "jobs" / "publish_finalize.py").read_text()
    assert "compose_blog_page" not in src and "_DROPINBLOG_CDN" not in src and "dropinblog.net" not in src
    assert not hasattr(pf.anthropic_client, "compose_blog_page"), "M-3: removed, not just unwired"
    assert "PENDING" not in src, "no placeholder injection left"
    print("  AC-6: _derive_blog_url = BLOG_BASE_URL + propose_slug (no suffix); Phase 2 no longer "
          "calls compose_blog_page or injects DropInBlog placeholders ✓")


def test_pf_archive_subfolder_is_not_input():
    """E32 re-run (Angela, 2026-09-24): July's artifacts moved into E32-linnea/_superseded-…/
    must not feed Phase 2. Before the fix, an archived BINARY apple_summary.md beat the
    root's hand-edited Doc (binaries win pass 1 wherever they live), and faq.md existing
    only in the archive would have been DELETED by the faq replace."""
    pf = _pf()
    DOC = "application/vnd.google-apps.document"
    root = [
        {"id": "ROOT-DOC-SUMMARY", "name": "apple_summary.md", "mimeType": DOC},
        {"id": "ROOT-QUOTES", "name": "quotes.json", "mimeType": "application/json"},
        {"id": "ARCH", "name": "_superseded-2026-07-21", "mimeType": "application/vnd.google-apps.folder"},
        {"id": "TR", "name": "transcripts", "mimeType": "application/vnd.google-apps.folder"},
    ]
    subs = {
        "ARCH": [{"id": "OLD-SUMMARY", "name": "apple_summary.md", "mimeType": "text/markdown"},
                 {"id": "OLD-QUOTES", "name": "quotes.json", "mimeType": "application/json"},
                 {"id": "OLD-FAQ", "name": "faq.md", "mimeType": "text/plain"}],
        "TR": [{"id": "TR-JSON", "name": "transcript_fast.json", "mimeType": "application/json"}],
    }
    listed = []

    def list_folder(fid):
        listed.append(fid)
        return subs[fid]

    _extended, fmap = pf._gather_files(root, list_folder)
    assert listed == ["TR"], f"the archive is never even listed: {listed}"
    assert fmap["apple_summary.md"] == "ROOT-DOC-SUMMARY", "John's root Doc, not July's binary"
    assert fmap["quotes.json"] == "ROOT-QUOTES"
    assert "faq.md" not in fmap, "an archived faq.md is not a file the replace may delete"
    assert fmap["transcript_fast.json"] == "TR-JSON", "ordinary subfolders still count"
    # and without an archive, the old behaviour is unchanged: a subfolder binary beats a root Doc
    _, fmap2 = pf._gather_files([root[0], dict(root[2], name="local")],
                                lambda fid: subs["ARCH"])
    assert fmap2["apple_summary.md"] == "OLD-SUMMARY"
    print("  E32 re-run: a `_superseded*` subfolder is skipped — root Doc wins, archived faq.md "
          "is safe, transcripts/ still read; other subfolders unchanged ✓")


def test_cli_slug_override():
    """--slug (Mac CLI only): Tailor's shape + 64 cap, applied before any call; the worker
    path never imports the CLI."""
    import argparse
    import contextlib as _cl

    import publish_blog_post as cli
    s64 = "bliss-is-the-system-the-ecs-estrogen-perimenopause-connection-32"
    assert len(s64) == 64 and cli.slug_override(s64) == s64
    for bad in (s64 + "x", "Bliss-Is", "-lead", "trail-", "dou--ble", "", "a/b", "sp ace"):
        _raises(argparse.ArgumentTypeError, lambda bad=bad: cli.slug_override(bad))
    tmp = _tmpdir()
    cred = tmp / "c.env"
    cred.write_text("BLOG_API_BASE=http://localhost:8695\n")
    out = io.StringIO()
    with _cl.redirect_stdout(out):
        rc = cli.main(["--episode", "31", "--inputs", str(FIXTURES / "E31"), "--spotify",
                       LISTEN["spotify_url"], "--apple", LISTEN["apple_url"], "--slug", s64,
                       "--credential-file", str(cred), "--dry-run"])
    assert rc == 0 and f"proposed slug: {s64} (64 chars, --slug override)" in out.getvalue()
    with _cl.redirect_stderr(io.StringIO()):
        _raises(SystemExit, lambda: cli.main(["--episode", "31", "--inputs", str(FIXTURES / "E31"),
                                              "--spotify", "x", "--apple", "y", "--slug", s64 + "x",
                                              "--credential-file", str(cred), "--dry-run"]))
    for f in ("jobs/publish_finalize.py", "blog_publish.py", "tailor_client.py", "worker.py"):
        assert not re.search(r"^\s*(import|from)\s+publish_blog_post", (WORKTREE / f).read_text(),
                             re.MULTILINE), f"{f} must not import the CLI"
    print("  --slug: the 64-char E31-32 slug passes; 65 chars / uppercase / edge or doubled "
          "hyphen / space / slash refused by argparse before anything runs; worker never imports "
          "the CLI ✓")


# ── round 2 (Jen's review of 2f03a8c) ──────────────────────────────────────────


def test_stale_session_preview_404_three_sites():
    """F-1: a superseded cookie on the PUBLIC preview GET is anonymous → 404 for a draft,
    not 401 (core/auth.py authenticated_claims_or_none). Every get_child call site must
    confirm a 404 with /me and spend the one re-login before believing it."""
    # (1) the record GET: our own draft must not look "gone"
    e = Env()
    r1 = e.publish()
    e.fake.supersede_before("GET", r"^/api/pages/blog/")
    r2 = e.publish()
    assert r2.outcome == "updated" and r2.page_id == r1.page_id, r2
    assert e.fake.logins == 3, "run 1 login + run 2 login + ONE re-login"
    # (2) the pre-PUT GET: the page must not have "changed under us"
    e = Env()
    e.publish()
    # the 2nd page GET of run 2 is the pre-PUT one
    e.fake.supersede_at.append(("GET", _NthMatch(r"^/api/pages/blog/", 2)))
    r = e.publish()
    assert r.outcome == "updated" and len(e.fake.calls("PUT")) == 2, r
    assert e.fake.logins == 3, "the pre-PUT 404 was confirmed and the one re-login spent"
    # (3) the pre-create slug check: someone else's draft must not look free
    e = Env()
    post, quotes = _post()
    e.fake.other_page(post["slug"])  # Heather's DRAFT at our proposed slug
    e.fake.supersede_before("GET", r"^/api/pages/blog/")
    exc = _raises(blog_publish.BlogStop, lambda: e.publish(post, quotes))
    assert "Not adopting it" in str(exc) and not e.fake.calls("POST", r"^/api/pages$")
    # and a 404 while the session IS current stays a 404 — no re-login
    e = Env()
    e.publish()
    assert e.fake.logins == 1
    # superseded twice → stop, never a third login
    e = Env()
    e.publish()
    e.fake.supersede_before("GET", r"^/api/pages/blog/")
    e.fake.supersede_at.append(("GET", _NthMatch(r"^/api/pages/blog/", 2)))
    _raises(blog_publish.BlogStop, lambda: e.publish())
    assert e.fake.logins == 3 and not e.fake.calls("PUT")[1:], "one run-2 re-login, no PUT"
    print("  F-1: stale cookie on a preview GET (404, not 401) → confirmed via /me, one re-login, "
          "retried — at the record GET, the pre-PUT GET and the pre-create check; twice → STOP ✓")


class _NthMatch:
    """A path matcher that fires on the n-th matching request only (for supersede_at)."""

    def __init__(self, pattern, n):
        self.rx, self.n, self.seen = re.compile(pattern), n, 0

    def search(self, path):
        if self.rx.search(path):
            self.seen += 1
            return self.seen == self.n
        return False


def test_relogin_401_no_third_login():
    """F-6 / M4c: the supersede re-login itself gets 401 (password rotated mid-run) → stop."""
    e = Env()
    e.fake.force("POST", r"^/api/pages$", _err(401, "UNAUTHENTICATED"))  # superseded
    # the SECOND login (the re-login) is refused; the first one is real
    e.fake.forced.append(("POST", _NthMatch(r"^/api/auth/login$", 2), _err(401, "INVALID_CREDENTIALS")))
    exc = _raises(blog_publish.BlogStop, lambda: e.publish())
    logins = len(e.fake.calls("POST", r"^/api/auth/login$"))
    assert logins == 2, f"C-5: exactly one re-login, never a third ({logins})"
    assert "401" in str(exc) and not e.fake.calls("PUT")
    print("  F-6: the re-login itself refused (401) → STOP with logins == 2 ✓")


def test_ctor_config_error_is_blogstop():
    """F-2: a refused BLOG_API_BASE is a permanent BlogStop, not a retryable error."""
    e = Env()
    for base in ("https://evil.example", "http://rockstarblends.com", "http://localhost:8695"):
        exc = _raises(blog_publish.BlogStop, lambda base=base: e.publish(api_base=base))
        assert "nothing was sent" in str(exc)
    assert e.fake.requests == []
    pfe = PFEnv()
    try:
        pfe.pf._BLOG_API_BASE = "https://evil.example"
        _raises(pfe.pf.PermanentFailure, lambda: pfe.write())
        assert pfe.fake.requests == [] and "NOT written" in pfe.alerts[-1][0]
    finally:
        pfe.close()
    print("  F-2: a refused API base → BlogStop (Phase 2: alert + PermanentFailure, no retry, "
          "no faq re-compose), zero requests ✓")


def test_logout_inside_lock():
    """F-4 / M13b: logout runs while the blog lock is still held — the next writer can't log
    in (and rotate our session) before ours has ended."""
    import fcntl
    for force_error in (False, True):
        e = Env()
        held = []

        def probe(e=e, held=held):
            with open(e.tmp / "tailor_blog.lock", "a+") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(f, fcntl.LOCK_UN)
                    held.append(False)
                except BlockingIOError:
                    held.append(True)

        e.fake.on_logout = probe
        if force_error:
            e.fake.force("PUT", r"^/api/pages/blog/", (502, [], b""))
            _raises(tailor_client.TailorError, lambda e=e: e.publish())
        else:
            e.publish()
        assert held == [True], f"logout must run inside the lock (error path={force_error})"
    print("  F-4: logout happens while the lock is held — on success and on a raise ✓")


def test_cover_reuse_verifies_media():
    """F-11: a recorded cover is reused only while GET /api/media/{id} (public — the one read
    an author has) answers 200; gone (404) → upload again and say so; unknown → retry."""
    e = Env()
    r1 = e.publish()
    r2 = e.publish()
    assert r2.cover_media_id == r1.cover_media_id and len(e.fake.media) == 1
    assert e.fake.calls("GET", "^/api/media/" + r1.cover_media_id), "the reuse was verified"
    assert "Cookie" not in e.fake.calls("GET", "^/api/media/")[0]["headers"], "public read, no cookie"
    e.fake.media.clear()  # an editor deleted it from the library
    r3 = e.publish()
    assert r3.cover_mode == blog_publish.COVER_AUTO and r3.cover_media_id != r1.cover_media_id
    assert "gone from the site" in r3.cover_note
    _, body = blog_publish.draft_alert(r3, "E31")
    assert "gone from the site" in body
    e.fake.force("GET", r"^/api/media/", (503, [], b""))
    _raises(tailor_client.TailorError, lambda: e.publish())
    print("  F-11: reuse verified by public GET /api/media/{id}; 404 → re-upload named in the "
          "alert; 503 → transient, no guess ✓")


def test_upload_budget_is_named():
    """429 on the upload is not a quiet manual fallback: the alert names the budget."""
    e = Env()
    e.fake.media = [{"id": str(uuid.uuid4()), "page_id": None, "by_author": True} for _ in range(30)]
    res = e.publish()
    assert res.cover_mode == blog_publish.COVER_MANUAL and res.cover_warning
    subject, body = blog_publish.draft_alert(res, "E31")
    assert subject.endswith("upload budget exceeded") and "UPLOAD BUDGET EXCEEDED" in body
    assert len(_body(e.fake.calls("PUT")[0])["blocks"]) == 16, "the draft is still written whole"
    print("  429 upload → draft still whole, but subject + body name 'upload budget exceeded' ✓")


def test_upload_refusals_413_415():
    for payload, code in ((b"\xff\xd8\xff" + b"x" * 30_000, "413"), (b"GIF-not-sniffed", "415")):
        e = Env()
        e.tile.write_bytes(payload)
        res = e.publish()
        assert res.cover_mode == blog_publish.COVER_MANUAL and code in res.cover_note, res.cover_note
        assert res.cover_warning is None
    print("  upload 413 (too large) / 415 (not an image) → manual cover, named in the note ✓")


def test_fake_matches_v0994_refusals():
    """The fake refuses what Tailor v0.9.94 refuses, and the client reports each shape
    without reading the body."""
    e = Env()
    c = tailor_client.TailorClient(API, transport=e.fake)
    c.login(EMAIL, PASSWORD)
    base = {"title": "t", "slug": "s1", "parent_slug": "blog", "tag_slugs": ["podcast"]}
    for body, code in (
        (base, "MODULE_PILLAR_PARENT"),                                         # no opt-in
        ({**base, "under_module_pillar": True, "tag_slugs": ["nope"]}, "VALIDATION_ERROR"),
        ({**base, "under_module_pillar": True, "html": "<b>x</b>"}, "CONTENT_PRIVILEGE"),
    ):
        exc = _raises(tailor_client.TailorError, lambda body=body: c.create_page(body))
        assert exc.code == code and exc.status in (403, 422), (exc.status, exc.code)
    page = c.create_page({**base, "under_module_pillar": True})
    exc = _raises(tailor_client.TailorError, lambda: c.update_child("blog", page["slug"], {"title": "t"}))
    assert exc.status == 422 and exc.code == tailor_client.REQUEST_VALIDATION_CODE
    assert "echoed" not in str(exc), "a Pydantic `detail` is never read into the error"
    exc = _raises(tailor_client.TailorError, lambda: c.update_child(
        "blog", page["slug"], {"title": "t", "html": "<p>x</p>", "js": "", "scss": ""}))
    assert exc.code == "CONTENT_PRIVILEGE"
    for pid, status in ((str(uuid.uuid4()), 404),):
        exc = _raises(tailor_client.TailorError,
                      lambda pid=pid: c.upload_media(pid, "t.jpg", b"\xff\xd8\xff", "image/jpeg"))
        assert exc.status == status
    login_body = json.loads(e.fake.calls("POST", r"^/api/auth/login$")[0]["body"])
    assert set(login_body) == {"email", "password"}, "LoginRequest shape"
    c.logout()
    print("  fake = v0.9.94: MODULE_PILLAR_PARENT, unknown tag 422, author buffers 403, PUT "
          "missing keys → Pydantic 422 (reported as REQUEST_VALIDATION, body unread), unknown "
          "page_id 404; login body is exactly {email, password} ✓")


def test_loopback_not_widened_by_env():
    """M1a/M1b: no environment variable turns loopback on, and Phase 2 never passes it."""
    import ast
    names = ("TAILOR_ALLOW_LOOPBACK", "BLOG_ALLOW_LOOPBACK", "ALLOW_LOOPBACK")
    old = {n: os.environ.get(n) for n in names}
    try:
        for n in names:
            os.environ[n] = "1"
        _raises(tailor_client.TailorConfigError,
                lambda: tailor_client.TailorClient("http://localhost:8695", transport=FakeTailor()))
        _raises(tailor_client.TailorConfigError,
                lambda: tailor_client.url_status("http://127.0.0.1/blog/x", transport=FakeTailor()))
        pfe = PFEnv()
        try:
            pfe.pf._BLOG_API_BASE = "http://localhost:8695"
            _raises(pfe.pf.PermanentFailure, lambda: pfe.write())
            assert pfe.fake.requests == []
        finally:
            pfe.close()
    finally:
        for n, v in old.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v
    tree = ast.parse((WORKTREE / "jobs" / "publish_finalize.py").read_text())
    kw = [k.arg for node in ast.walk(tree) if isinstance(node, ast.Call) for k in node.keywords]
    assert "allow_loopback" not in kw, "Phase 2 must never pass allow_loopback"
    print("  M1: loopback refused with *_ALLOW_LOOPBACK=1 set (client, url_status, Phase 2); "
          "publish_finalize passes no allow_loopback keyword (AST) ✓")


def test_https_transport_verifies_tls():
    """M3: the real transport's TLS context verifies the chain AND the hostname."""
    import http.client as hc
    import ssl
    seen = {}

    class Conn:
        def __init__(self, host, port=None, timeout=None, context=None):
            seen["context"] = context

        def request(self, *a, **k):
            raise OSError("stop here")

        def close(self):
            pass

    real = hc.HTTPSConnection
    hc.HTTPSConnection = Conn
    try:
        _raises(OSError, lambda: tailor_client.HttpTransport().request(
            "GET", "https", "rockstarblends.com", None, "/", {}, None))
    finally:
        hc.HTTPSConnection = real
    ctx = seen["context"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    print("  M3: HttpTransport's HTTPS context has verify_mode CERT_REQUIRED + check_hostname ✓")


def test_pf_sentinel_alerts_and_log():
    """M5f/h/i: no Phase-2 alert payload and no publish_finalize log line carries the
    password or a session cookie — success, stop, gate failure."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    alerts = []
    try:
        e = PFEnv()
        try:
            e.write()                                                       # created
            e.write()                                                       # updated
            post, _ = _post()
            e.fake.pages[next(iter(e.fake.pages))]["status"] = "published"
            e.write()                                                       # skipped
            e2 = PFEnv()
            e2.fake.other_page(post["slug"])
            _raises(e2.pf.PermanentFailure, lambda: e2.write())             # BlogStop
            _raises(e2.pf.PermanentFailure, lambda: e2.write(apple_url="x"))  # gate
            e2.pf._thank_you_ready_alert("E31", "guest@x.test", "Thank you",
                                         f"{PUBLIC}/{post['slug']}")          # Step 13
            alerts = e.alerts + e2.alerts
        finally:
            e.close()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    everything = buf.getvalue() + "\n".join(a + "\n" + b for a, b in alerts)
    assert len(alerts) >= 6 and "publish_finalize: blog" in buf.getvalue()
    assert PASSWORD not in everything and COOKIE_PREFIX not in everything
    print(f"  M5: {len(alerts)} Phase-2 alerts + the publish_finalize DEBUG log carry no password "
          "or cookie ✓")


def test_lock_shared_holder_blocks():
    """M12b: the lock is EXCLUSIVE — even a shared holder keeps a second writer out."""
    import fcntl
    e = Env()
    holder = open(e.tmp / "tailor_blog.lock", "a+")  # noqa: SIM115 — closed in finally
    fcntl.flock(holder, fcntl.LOCK_SH)
    try:
        _raises(blog_publish.BlogBusy, lambda: e.publish())
        assert e.fake.requests == []
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
    print("  M12b: a LOCK_SH holder → BlogBusy, no request ✓")


def test_pf_step13_thank_you_ready():
    """M19a: Step 13 reports the thank-you ready only on a 200; a draft post (404) → the
    named WARNING line and subject."""
    e = PFEnv()
    try:
        e.write()
        url = f"{PUBLIC}/{next(iter(e.fake.pages))}"
        e.pf._thank_you_ready_alert("E31", "guest@x.test", "Thank you!", url)
        subject, body = e.alerts[-1]
        assert subject == "E31: thank-you draft ready — blog post not published yet", subject
        assert "WARNING — post not published yet" in body and "HTTP 404" in body
        e.fake.pages[next(iter(e.fake.pages))]["status"] = "published"
        e.pf._thank_you_ready_alert("E31", "guest@x.test", "Thank you!", url)
        subject, body = e.alerts[-1]
        assert subject == "E31: thank-you draft ready" and "HTTP 200" in body
        assert "Cookie" not in e.fake.calls("GET", r"^/blog/")[0]["headers"]
    finally:
        e.close()
    print("  M19a: Step 13 — draft post (404) → 'blog post not published yet' subject + WARNING "
          "line; published (200) → plain 'ready' ✓")


def test_create_thank_you_draft_checks_blog_url():
    """M19b: the CLI prints the OPEN-1 line after creating the draft."""
    import contextlib as _cl
    fake = FakeTailor()
    tmp = _tmpdir()
    sched = tmp / "publication_schedule.json"
    sched.write_text(json.dumps({"episodes": [{
        "episode_number": 31, "guest_name": "Dr. Saffi Velloquin", "guest_email": "g@x.test",
        "episode_title": "Bliss Is the System | The ECS", "apple_url": LISTEN["apple_url"],
        "spotify_url": LISTEN["spotify_url"],
        "blog_post_url": f"{PUBLIC}/bliss-is-the-system-the-ecs"}]}))
    gm = types.ModuleType("gmail_client")
    stored = {}
    gm.create_draft = lambda to, subject, body: stored.update(body=body) or {"id": "D1"}
    gm.get_draft_body = lambda i: stored["body"]
    gm.get_draft_raw = lambda i: "raw"
    old_gm = sys.modules.get("gmail_client")
    sys.modules["gmail_client"] = gm
    real = tailor_client.HttpTransport
    tailor_client.HttpTransport = lambda: fake
    try:
        import create_thank_you_draft as ctd
        importlib.reload(ctd)
        ctd._PUB_SCHEDULE_PATH = sched
        out = io.StringIO()
        with _cl.redirect_stdout(out):
            assert ctd.create(31) == 0
    finally:
        tailor_client.HttpTransport = real
        if old_gm is not None:
            sys.modules["gmail_client"] = old_gm
    text = out.getvalue()
    assert "created draft id=D1" in text
    assert "blog URL: WARNING — post not published yet" in text and "HTTP 404" in text
    assert fake.calls("GET", r"^/blog/bliss-is-the-system-the-ecs$")
    print("  M19b: create_thank_you_draft creates the draft, then prints 'WARNING — post not "
          "published yet (HTTP 404)' ✓")


def test_cli_is_loopback_only():
    """F-3: publish_blog_post.py refuses every non-loopback base — before the credential."""
    import contextlib as _cl

    import publish_blog_post as cli
    tmp = _tmpdir()
    args = ["--episode", "31", "--inputs", str(FIXTURES / "E31"), "--spotify",
            LISTEN["spotify_url"], "--apple", LISTEN["apple_url"], "--dry-run"]
    for base in ("https://rockstarblends.com", "https://evil.example", "http://10.0.0.5:8695"):
        cred = tmp / "c.env"
        cred.write_text(f"BLOG_API_BASE={base}\n")
        with _cl.redirect_stdout(io.StringIO()):
            exc = _raises(SystemExit, lambda cred=cred: cli.main(args + ["--credential-file", str(cred)]))
            assert "not a loopback host" in str(exc.code), exc.code
            cred.write_text("BLOG_API_BASE=http://localhost:8695\n")
            exc = _raises(SystemExit, lambda cred=cred, base=base: cli.main(
                args + ["--credential-file", str(cred), "--api-base", base]))
            assert "not a loopback host" in str(exc.code)
    for base in ("http://localhost:8695", "http://127.0.0.1:8695"):
        cred = tmp / "ok.env"
        cred.write_text(f"BLOG_API_BASE={base}\n")
        with _cl.redirect_stdout(io.StringIO()):
            assert cli.main(args + ["--credential-file", str(cred)]) == 0
    print("  F-3: the CLI refuses production / any non-loopback base (file or --api-base), "
          "loopback passes ✓")


def _hand_edit(fake, slug, field="blocks"):
    """Heather saves the draft in the editor: an owned field changes (the fake stands in for
    an editor-tier PUT — it does not go through the pipeline's client)."""
    page = fake.pages[slug]
    if field == "blocks":
        page["blocks"] = page["blocks"][:-1] + [{"type": "section", "headline": {"inlines": [], "type": "h2"},
                                                 "content": [], "image": None, "keep_narrow": False}]
    else:
        page[field] = "Heather's edit"


def test_hand_edited_draft_left_alone():
    """Toby F-1: a draft edited by hand since the pipeline's last write is left alone, like a
    published post; an untouched one is updated as before."""
    for field in ("blocks", "title", "meta_description"):
        e = Env()
        r1 = e.publish()
        _hand_edit(e.fake, r1.slug, field)
        e.tile.write_bytes(b"\xff\xd8\xff\xe0 a new tile")  # would upload, if we touched it
        puts, uploads = len(e.fake.calls("PUT")), len(e.fake.media)
        r2 = e.publish()
        assert r2.outcome == "skipped_edited" and r2.url == r1.url, (field, r2.outcome)
        assert len(e.fake.calls("PUT")) == puts and len(e.fake.media) == uploads, \
            "hands off: no PUT, and no cover upload either — the record GET decides first"
        subject, body = blog_publish.draft_alert(r2, "E31")
        assert subject == "E31: blog draft edited by hand — pipeline left it alone"
        assert "edited by hand since the pipeline's last write" in body and r1.url in body
    # untouched → updated, and the stamp moves with each write
    e = Env()
    e.publish()
    rec1 = json.loads((e.tmp / "blog_posts.json").read_text())["posts"][f"{API}|E31"]
    assert e.publish().outcome == "updated" and e.publish().outcome == "updated"
    rec3 = json.loads((e.tmp / "blog_posts.json").read_text())["posts"][f"{API}|E31"]
    assert rec1["fingerprint"] and rec3["written_at"] and rec3["fingerprint_cover"] is True
    print("  F-1: hand edit to blocks / title / meta → skipped_edited + 'left alone' alert, no PUT "
          "or upload; untouched → updated, stamp refreshed ✓")


def test_hand_edit_between_get_and_put():
    """F-1 at the pre-PUT GET: Heather saves while the cover uploads."""
    e = Env()
    r1 = e.publish()
    seen = {"n": 0}

    def edit_on_second_get(page):
        seen["n"] += 1
        if seen["n"] == 2:
            _hand_edit(e.fake, r1.slug)

    e.fake.on_get = edit_on_second_get
    r2 = e.publish()
    assert r2.outcome == "skipped_edited" and len(e.fake.calls("PUT")) == 1
    print("  F-1: edited between the record GET and the PUT → the pre-PUT check leaves it alone ✓")


def test_cover_set_by_hand_in_manual_mode_is_not_an_edit():
    """The fingerprint covers only what the pipeline writes: in manual mode the Cover Image is
    Heather's, so setting it does not freeze the pipeline."""
    e = Env(upload=403)
    r1 = e.publish()
    e.fake.pages[r1.slug]["thumbnail_media_id"] = str(uuid.uuid4())  # Heather picks the cover
    r2 = e.publish()
    assert r2.outcome == "updated"
    assert "thumbnail_media_id" not in _body(e.fake.calls("PUT")[-1]), "and the PUT leaves it be"
    print("  F-1 scope: Heather setting the cover in manual mode is not an edit of ours ✓")


def test_record_without_fingerprint_stands_down():
    """A record from before the guard can't rule out a hand edit — not knowing is not
    permission (_authored.py)."""
    e = Env()
    r1 = e.publish()
    path = e.tmp / "blog_posts.json"
    data = json.loads(path.read_text())
    data["posts"][f"{API}|E31"].pop("fingerprint")
    path.write_text(json.dumps(data))
    r2 = e.publish()
    assert r2.outcome == "skipped_edited" and "no fingerprint" in r2.skip_reason
    assert len(e.fake.calls("PUT")) == 1 and r2.url == r1.url
    print("  F-1: a record with no fingerprint (pre-guard) → left alone, named ✓")


def test_shell_edited_after_failed_put():
    """A PUT that failed after create, then a retry: an untouched shell is filled in; a shell
    Heather has started writing in is left alone (the create stamps the shell)."""
    e = Env()
    e.fake.force("PUT", r"^/api/pages/blog/", (502, [], b""))
    _raises(tailor_client.TailorError, lambda: e.publish())
    slug = next(iter(e.fake.pages))
    _hand_edit(e.fake, slug, "title")
    r = e.publish()
    assert r.outcome == "skipped_edited" and len(e.fake.calls("PUT")) == 1
    print("  F-1: create → PUT 502 → Heather edits the shell → retry leaves it alone ✓")


def test_pf_retry_after_step6_skips_hand_edit():
    """F-1 in Phase 2: Step 6 wrote the draft, a later step failed transiently, the worker
    retries the whole job (1m…6h later) — by then Heather is editing the draft."""
    e = PFEnv()
    try:
        url1 = e.write()                               # attempt 1: Step 6 writes the draft
        _hand_edit(e.fake, next(iter(e.fake.pages)))   # Heather edits while reels render
        url2 = e.write()                               # attempt 2: the retry reaches Step 6 again
        assert url2 == url1 and len(e.fake.calls("PUT")) == 1
        assert e.alerts[-1][0].startswith("E31: blog draft edited by hand — pipeline left it alone")
    finally:
        e.close()
    print("  F-1 Phase 2: a worker retry after a later-step failure leaves the hand-edited draft "
          "alone and carries on with its URL ✓")


def test_corrupt_record_is_a_named_stop():
    """Toby F-5: an unreadable ownership record is a BlogStop before login, not a retried
    ValueError."""
    for content in ("{not json", "[]", '{"posts": []}', '{"posts": {"k": "v"}}'):
        e = Env()
        (e.tmp / "blog_posts.json").write_text(content)
        exc = _raises(blog_publish.BlogStop, lambda e=e: e.publish())
        assert "ownership record" in str(exc) and "not guessing" in str(exc)
        assert e.fake.requests == [], "stopped before login"
    pfe = PFEnv()
    try:
        pfe.pf._BLOG_RECORD_PATH.write_text("{not json")
        _raises(pfe.pf.PermanentFailure, lambda: pfe.write())
        assert "NOT written" in pfe.alerts[-1][0] and "ownership record" in pfe.alerts[-1][1]
    finally:
        pfe.close()
    print("  F-5: a corrupt/misshapen record → named BlogStop before login; Phase 2 alerts + "
          "PermanentFailure (no five retries) ✓")


def test_superseded_on_skip_and_stale_preview():
    """N-2 on every outcome: a supersede found through the F-1 stale-preview path still
    reaches the alert, including when the post is then skipped as published."""
    e = Env()
    r1 = e.publish()
    e.fake.pages[r1.slug]["status"] = "draft"
    e.fake.supersede_before("GET", r"^/api/pages/blog/")
    r2 = e.publish()
    assert r2.outcome == "updated" and r2.superseded
    e.fake.pages[r1.slug]["status"] = "published"
    e.fake.supersede_before("GET", r"^/api/auth/me$")
    r3 = e.publish()
    assert r3.outcome == "skipped_published" and r3.superseded
    subject, body = blog_publish.draft_alert(r3, "E31")
    assert "SESSION SUPERSEDED" in subject and blog_publish.SUPERSEDED_LINE in body
    print("  N-2: a supersede found via the stale preview, or on a published-skip run, is in the "
          "alert subject + body ✓")


def test_credential_source_named_and_alerted():
    """N-3: the source is a NAME, logged; environ is flagged in Phase 2's alert; the smoke
    check can assert LoadCredential."""
    tmp = _tmpdir()
    cd = tmp / "creds"
    cd.mkdir()
    (cd / "tailor_blog").write_text(f"TAILOR_BLOG_EMAIL={EMAIL}\nTAILOR_BLOG_PASSWORD={PASSWORD}\n")
    f = tmp / "c.env"
    f.write_text(f"TAILOR_BLOG_EMAIL={EMAIL}\nTAILOR_BLOG_PASSWORD={PASSWORD}\n")
    env = {"TAILOR_BLOG_EMAIL": EMAIL, "TAILOR_BLOG_PASSWORD": PASSWORD}
    cs = blog_publish.credential_source
    assert cs(env_file=f) == "file"
    assert cs(environ={"CREDENTIALS_DIRECTORY": str(cd), **env}) == "LoadCredential", \
        "LoadCredential wins even when the env also carries the keys"
    assert cs(environ={"CREDENTIALS_DIRECTORY": str(tmp / "missing"), **env}) == "environ"
    assert cs(environ=env) == "environ"
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    lg = logging.getLogger("blog_publish")
    lg.addHandler(h)
    old = lg.level
    lg.setLevel(logging.INFO)
    try:
        blog_publish.load_credentials(environ={"CREDENTIALS_DIRECTORY": str(cd)})
        blog_publish.load_credentials(environ=env)
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)
    log = buf.getvalue()
    assert "blog credential source: LoadCredential" in log and "blog credential source: environ" in log
    assert "(C-10)" in log and PASSWORD not in log and EMAIL not in log
    # Phase 2: PFEnv reads from the environment → the alert says so
    e = PFEnv()
    try:
        e.write()
        subject, body = e.alerts[-1]
        assert subject.endswith("— credential from environ") and blog_publish.ENVIRON_SOURCE_LINE in body
        os.environ["CREDENTIALS_DIRECTORY"] = str(cd)
        e.write()
        subject, body = e.alerts[-1]
        assert "environ" not in subject and blog_publish.ENVIRON_SOURCE_LINE not in body
    finally:
        os.environ.pop("CREDENTIALS_DIRECTORY", None)
        e.close()
    print("  N-3: source named (file / LoadCredential / environ) and logged without values; environ "
          "→ C-10 warning in the log AND a named line in the Step-6 alert; LoadCredential → clean ✓")


def test_systemd_credential_mask():
    """Orly N-1 (P-2): every unit other than the worker that runs as `rockstar` masks the
    credential paths — with NO "-" prefix — and tmpfiles guarantees they exist first."""
    sd = WORKTREE / "systemd"
    units = sorted(u.stem for u in sd.glob("*.service")
                   if "User=rockstar" in u.read_text() and u.stem != "rockstar-worker")
    assert units == ["rockstar-poll-drive", "rockstar-renew-watches", "rockstar-webhook"], units
    for u in units:
        lines = [ln.strip() for ln in (sd / f"{u}.service.d" / "no-credentials.conf").read_text().splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        assert lines == ["[Service]", "InaccessiblePaths=/run/credentials /etc/rockstar-pipeline"], (u, lines)
    tmp = [ln.split() for ln in (sd / "tmpfiles.d" / "rockstar-credentials.conf").read_text().splitlines()
           if ln.strip() and not ln.startswith("#")]
    assert ["d", "/run/credentials", "0755", "root", "root", "-"] in tmp
    assert ["d", "/etc/rockstar-pipeline", "0700", "root", "root", "-"] in tmp
    worker = [ln.strip() for ln in (sd / "rockstar-worker.service.d" / "tailor-blog.conf").read_text().splitlines()
              if ln.strip() and not ln.strip().startswith("#")]
    assert worker == ["[Service]", "LoadCredential=tailor_blog:/etc/rockstar-pipeline/tailor-blog.env"]
    print("  N-1: webhook/poll-drive/renew-watches mask /run/credentials + /etc/rockstar-pipeline (no "
          "'-' prefix); tmpfiles creates both; only the worker loads the credential ✓")


def test_supersede_named_on_write_stops_and_transients():
    """N-9 (Orly): _write's OWN stops after a re-login (collision, gone, hand-edit) carry the
    supersede line; a transient error after a re-login names it in the log."""
    # collision found after the stale-preview re-login
    e = Env()
    post, quotes = _post()
    e.fake.other_page(post["slug"])
    e.fake.supersede_before("GET", r"^/api/pages/blog/")
    exc = _raises(blog_publish.BlogStop, lambda: e.publish(post, quotes))
    assert "Not adopting it" in str(exc) and blog_publish.SUPERSEDED_LINE in str(exc)
    # recorded post gone, after a re-login
    e = Env()
    r1 = e.publish()
    del e.fake.pages[r1.slug]
    e.fake.supersede_before("GET", r"^/api/pages/blog/")
    exc = _raises(blog_publish.BlogStop, lambda: e.publish())
    assert "Not creating a second post" in str(exc) and blog_publish.SUPERSEDED_LINE in str(exc)
    # a stop with NO supersede stays unmarked
    e = Env()
    e.fake.other_page(post["slug"])
    exc = _raises(blog_publish.BlogStop, lambda: e.publish(post, quotes))
    assert "SUPERSEDED" not in str(exc)
    # transient after a re-login → the journal names it
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    lg = logging.getLogger("blog_publish")
    lg.addHandler(h)
    try:
        e = Env()
        e.fake.force("POST", r"^/api/pages$", _err(401, "UNAUTHENTICATED"))
        e.fake.force("POST", r"^/api/media$", (503, [], b""))
        e.fake.force("PUT", r"^/api/pages/blog/", (502, [], b""))
        _raises(tailor_client.TailorError, lambda: e.publish())
        assert blog_publish.SUPERSEDED_LINE in buf.getvalue()
        before = buf.getvalue()
        e2 = Env()
        e2.fake.force("PUT", r"^/api/pages/blog/", (502, [], b""))
        _raises(tailor_client.TailorError, lambda: e2.publish())
        assert buf.getvalue().count(blog_publish.SUPERSEDED_LINE) == before.count(blog_publish.SUPERSEDED_LINE)
    finally:
        lg.removeHandler(h)
    print("  N-9: collision / gone stops after a re-login name the supersede; an unsuperseded stop "
          "doesn't; a transient after a re-login names it in the journal ✓")


# ── Accepted survivors (Jen, 2f03a8c review) ──────────────────────────────────
# M8b  "record-path published check removed": survives because the pre-PUT GET (C-9,
#      M8a — killed) catches the same published page a few ms later. Kept as defence in
#      depth; the backstop is tested (test_published_post_skipped, test_published_between…).
# X4   "404 on the GET-before-PUT treated as fine": survives because the PUT to a missing
#      page answers 404 and that becomes a BlogStop anyway; since F-1 a stale-session 404 is
#      confirmed via /me before it gets here. Backstopped by test_recorded_post_gone_stops.


# Found by this gate on 2026-09-24, NOT introduced by BLOG-02: both are imported by the
# worker and absent from FILES, so the box runs whatever copy the first install left. Named
# here so the gate stays green for BLOG-02's own modules while the gap is visible; raised to
# Oliver/Toby for 02c (adding them changes what a deploy ships, which is their call).
PRE_EXISTING_UNMANAGED = frozenset({"calendar_client.py", "google_auth.py"})


def test_deploy_files_carry_every_local_import():
    """Oliver's 02c blocker #1, as a gate: every repo module the worker imports (transitively,
    from jobs/publish_finalize.py) is in scripts/deploy.sh FILES."""
    files = set(re.findall(r'^\s*"([^"]+)"', (WORKTREE / "scripts" / "deploy.sh").read_text(), re.MULTILINE))
    local = {p.stem for p in WORKTREE.glob("*.py")}
    seen, todo = set(), ["jobs/publish_finalize.py"]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for mod in re.findall(r"^\s*(?:import|from)\s+([a-z_]+)", (WORKTREE / f).read_text(), re.MULTILINE):
            if mod in local:
                todo.append(f"{mod}.py")
    missing = sorted(seen - files - PRE_EXISTING_UNMANAGED)
    assert not missing, f"deploy.sh FILES is missing {missing}"
    for m in ("blog_post.py", "blog_publish.py", "tailor_client.py"):
        assert m in seen, m
    print(f"  deploy: all {len(seen)} modules publish_finalize imports (transitively) are in FILES, "
          "incl. blog_post/blog_publish/tailor_client ✓")


def main() -> int:
    tests = [
        test_happy_path_created_auto_cover,
        test_create_body_shape,
        test_bodies_never_carry_status_published_at_byline,
        test_url_is_from_returned_slug,
        test_manual_cover_on_403,
        test_rerun_updates_in_place_one_upload,
        test_changed_tile_uploads_again,
        test_collision_not_ours_stops,
        test_recorded_post_gone_stops,
        test_recorded_slug_now_other_page_stops,
        test_published_post_skipped,
        test_published_between_get_and_put,
        test_never_deletes,
        test_role_not_author_stops,
        test_logout_after_exception,
        test_transient_then_rerun_same_post,
        test_login_401_no_retry,
        test_supersede_one_relogin,
        test_429_stops,
        test_create_and_put_refusals_stop,
        test_gate_failure_makes_no_call,
        test_lock_busy,
        test_base_url_pinning,
        test_redirect_not_followed,
        test_url_status_pinned,
        test_post_live_check,
        test_byline_warning_when_not_heather,
        test_credentials_sources,
        test_sentinel_secret_never_output,
        test_pf_writes_post_and_returns_api_url,
        test_pf_gate_failure_alerts_no_call,
        test_pf_stop_alerts_and_skip_continues,
        test_pf_missing_credential_stops_without_call,
        test_pf_derive_blog_url_delegates,
        test_pf_archive_subfolder_is_not_input,
        test_cli_slug_override,
        test_stale_session_preview_404_three_sites,
        test_relogin_401_no_third_login,
        test_ctor_config_error_is_blogstop,
        test_logout_inside_lock,
        test_cover_reuse_verifies_media,
        test_upload_budget_is_named,
        test_upload_refusals_413_415,
        test_fake_matches_v0994_refusals,
        test_loopback_not_widened_by_env,
        test_https_transport_verifies_tls,
        test_pf_sentinel_alerts_and_log,
        test_lock_shared_holder_blocks,
        test_pf_step13_thank_you_ready,
        test_create_thank_you_draft_checks_blog_url,
        test_cli_is_loopback_only,
        test_hand_edited_draft_left_alone,
        test_hand_edit_between_get_and_put,
        test_cover_set_by_hand_in_manual_mode_is_not_an_edit,
        test_record_without_fingerprint_stands_down,
        test_shell_edited_after_failed_put,
        test_pf_retry_after_step6_skips_hand_edit,
        test_corrupt_record_is_a_named_stop,
        test_superseded_on_skip_and_stale_preview,
        test_credential_source_named_and_alerted,
        test_systemd_credential_mask,
        test_supersede_named_on_write_stops_and_transients,
        test_deploy_files_carry_every_local_import,
    ]
    failed = 0
    print("BLOG-02b Tailor write (faked Tailor, no network) — test run")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    _cleanup()
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
