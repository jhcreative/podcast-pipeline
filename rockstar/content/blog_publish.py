"""
blog_publish.py — write the built episode post to Tailor as a DRAFT (BLOG-02b).

blog_post.build_post makes the post (no model, no network). This module puts it on the
site, once, safely. It is shared by Phase 2 (rockstar/jobs/publish_finalize.py Step 6) and the
Mac-side AC-8 retest CLI (publish_blog_post.py), so the retest exercises exactly the code
E32 will run.

    gate (AC-3) → lock (C-6) → login → role == author (C-1)
      → our own record? (C-7)
           yes → GET by the recorded slug: gone → STOP · different page → STOP
                                           published → SKIP (AC-5, the human copy wins)
           no  → proposed slug already exists → STOP (never adopt a page we didn't make)
                 POST create → record {page_id, slug} BEFORE anything else
      → Cover Image (automatic when the site lets an author upload; manual otherwise)
      → GET immediately before the PUT (C-9): published → SKIP
      → PUT blocks + fields (never `status`, `published_at`, `byline` — C-8)
    → logout in `finally` (C-2)

WHY OUR OWN RECORD (C-7, Orly T-2). Tailor gives an author no way to prove it owns a page:
its page list hides its own posts under /blog (routers/pages.py `list_pages`, author-scoped
rows then children of a parent it doesn't own dropped), and no response carries the owner.
The server still enforces ownership on every write (403, core/page_ownership.py), so the
record's job is narrower: it tells a re-run WHICH page is ours, so a re-run updates that
one in place and never creates a second post (AC-5), and so an unexpected page at our
proposed slug is recognised as someone else's and left alone. The record is keyed by API
base + episode, so a retest against a disposable instance can never be mistaken for the
production post.

WHY THE COVER HAS TWO MODES (John, 2026-09-23). The episode tile is the post's Cover Image
(`thumbnail_media_id`), not its hero. An `author` may upload an image onto a page it owns
from Tailor v0.9.94 (BACKLOG-0216). On an older instance the upload answers 403, and the
draft is still written whole — Cover Image empty — and the alert tells Heather to set it
from the tile on Drive. The alert always names which mode ran. A re-run with the same tile
reuses the media id recorded on the first run: one post and one cover upload in total.

Outcomes, for the caller:
  BlogResult            the draft was created or updated, or the post is published and was
                        skipped. `url` is the public URL from the slug the API returned (AC-6).
  BlogStop              refuse and alert; retrying the same input fails the same way.
  TailorError (transient, `answered` False)  the network or a 5xx; the caller may retry —
                        the record makes a retry idempotent.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from rockstar.clients.tailor_client import (
    TailorAuthError,
    TailorClient,
    TailorConfigError,
    TailorError,
    TailorRateLimited,
    url_status,
)
from rockstar.content import blog_post
from rockstar.gates import content_gates

logger = logging.getLogger(__name__)

PARENT_SLUG = "blog"
TAG_SLUGS = ["podcast"]  # exactly this set, every write (C-13)
EXPECTED_BYLINE = blog_post.HOST_NAME  # "Heather Reilly Hiemstra"
CREDENTIAL_KEYS = ("TAILOR_BLOG_EMAIL", "TAILOR_BLOG_PASSWORD")
# systemd LoadCredential= name (Orly C-10's preferred custody): the worker then reads
# $CREDENTIALS_DIRECTORY/tailor_blog and the password never enters its environment.
SYSTEMD_CREDENTIAL_NAME = "tailor_blog"

COVER_AUTO = "automatic"
COVER_MANUAL = "manual"

_TILE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class BlogStop(Exception):
    """Refuse and alert. Never a half-adopted page, never a second post. Permanent."""


class BlogBusy(RuntimeError):
    """Another blog write holds the lock (C-6). Transient: the worker's back-off retries."""


@dataclasses.dataclass
class BlogResult:
    outcome: str  # "created" | "updated" | "skipped_published" | "skipped_edited"
    page_id: str
    slug: str  # the slug the API returned / recorded (AC-6)
    url: str  # public URL = public_base + "/" + slug
    status: str  # what Tailor reports now
    cover_mode: str | None  # COVER_AUTO | COVER_MANUAL | None when skipped
    cover_note: str  # why that mode
    cover_media_id: str | None
    cover_warning: str | None  # a named problem with the cover (e.g. the upload budget)
    byline: str | None  # the public byline Tailor resolved, joined
    block_count: int | None
    hosts: list[str]  # every distinct external host in the post (C-11)
    skip_reason: str | None = None  # why a draft was left alone (skipped_edited)
    superseded: bool = False  # N-2: another login to the account during this run


# ── credential ──────────────────────────────────────────────────────────────────


def read_env_file(path: Path, keys=CREDENTIAL_KEYS) -> dict[str, str]:
    """KEY=VALUE lines → dict, for the allow-listed `keys` ONLY (the credential keys by
    default). Comments and every other key are ignored, and nothing is printed or logged —
    the file holds a password."""
    out: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if key in keys:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            out[key] = value
    return out


SOURCE_FILE = "file"  # an explicit path (the Mac retest CLI)
SOURCE_LOADCREDENTIAL = "LoadCredential"  # $CREDENTIALS_DIRECTORY/tailor_blog (Orly C-10)
SOURCE_ENVIRON = "environ"  # the process environment — C-10's visible fallback

SUPERSEDED_LINE = "SESSION SUPERSEDED — another login to the pipeline account during this run; check audit_log (C-14)"
ENVIRON_SOURCE_LINE = (
    "CREDENTIAL SOURCE: environ — the blog password came from the process "
    "environment, not LoadCredential. If it is in the shared .env the webhook "
    "holds it too (C-10): move it to /etc/rockstar-pipeline/tailor-blog.env."
)


def credential_source(env_file: Path | None = None, environ=None) -> str:
    """WHERE load_credentials would read the credential from — a name, never a value. The one
    decision point, so the smoke check (runbook step 10) can assert it (Orly N-3)."""
    environ = os.environ if environ is None else environ
    if env_file is not None:
        return SOURCE_FILE
    cred_dir = environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir and (Path(cred_dir) / SYSTEMD_CREDENTIAL_NAME).is_file():
        return SOURCE_LOADCREDENTIAL
    return SOURCE_ENVIRON


def load_credentials(env_file: Path | None = None, environ=None) -> tuple[str, str]:
    """(email, password), from the first source that exists (credential_source):
      1. `env_file` — an explicit path (the Mac retest CLI);
      2. $CREDENTIALS_DIRECTORY/tailor_blog — systemd LoadCredential= (Orly C-10);
      3. the environment — kept for systemd < 247, but never silently (N-3): it logs a C-10
         warning, and Phase 2 puts ENVIRON_SOURCE_LINE in the alert.
    The source NAME is logged; a missing key names the key; a value is never shown."""
    environ = os.environ if environ is None else environ
    source = credential_source(env_file, environ)
    if source == SOURCE_FILE:
        values = read_env_file(env_file)
    elif source == SOURCE_LOADCREDENTIAL:
        values = read_env_file(Path(environ["CREDENTIALS_DIRECTORY"]) / SYSTEMD_CREDENTIAL_NAME)
    else:
        values = {k: environ.get(k, "") for k in CREDENTIAL_KEYS}
    logger.info("blog credential source: %s", source)
    if source == SOURCE_ENVIRON:
        logger.warning("blog credential source is the process environment, not LoadCredential (C-10)")
    missing = [k for k in CREDENTIAL_KEYS if not values.get(k)]
    if missing:
        raise BlogStop(f"Tailor blog credential is missing {', '.join(missing)} — nothing was sent")
    return values["TAILOR_BLOG_EMAIL"], values["TAILOR_BLOG_PASSWORD"]


# ── lock (C-6) ──────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def blog_write_lock(lock_path: Path, timeout: float = 600.0, poll: float = 2.0):
    """One blog write at a time, across processes. Tailor keeps ONE session per account,
    so a second concurrent login would 401 the first mid-write (Orly Q2). flock is released
    by the kernel if the holder dies, so a crash cannot wedge the next run."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")  # noqa: SIM115 — held across the yield, closed in finally
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BlogBusy(f"another blog write holds {lock_path.name}") from None
                time.sleep(poll)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


# ── our own record (C-7) ────────────────────────────────────────────────────────


class RecordStore:
    """{api_base|episode_code: {page_id, slug, cover_media_id, cover_sha256, …}} in one JSON
    file, rewritten via temp + rename. Only ever written while the blog lock is held."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @staticmethod
    def key(api_base: str, episode_code: str) -> str:
        return f"{api_base}|{episode_code}"

    def _load(self) -> dict:
        """The whole record. A file that is not the shape this class writes is a STOP, named
        (Toby F-5): guessing past it could forget a page we own — and a retry cannot fix it,
        so it must not surface as a ValueError the worker retries five times."""
        if not self.path.exists():
            return {"version": 1, "posts": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise BlogStop(
                f"the blog ownership record {self.path} is unreadable "
                f"({type(exc).__name__}) — not guessing which posts are ours. "
                f"Restore it from a backup or fix it by hand."
            ) from None
        if not (
            isinstance(data, dict)
            and isinstance(data.get("posts"), dict)
            and all(isinstance(v, dict) for v in data["posts"].values())
        ):
            raise BlogStop(
                f"the blog ownership record {self.path} is not in the expected shape — "
                f"not guessing which posts are ours. Fix it by hand."
            )
        return data

    def get(self, key: str) -> dict | None:
        return self._load()["posts"].get(key)

    def put(self, key: str, **fields) -> dict:
        data = self._load()
        rec = {**data["posts"].get(key, {}), **fields}
        data["posts"][key] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return rec


# ── bodies ──────────────────────────────────────────────────────────────────────


def create_body(post: dict) -> dict:
    """PageCreate. Tailor's create takes the shell only; blocks/content_mode are PUT fields.
    `under_module_pillar`: /blog is a module pillar and the generic create refuses a child
    of one without it (Tailor routers/pages.py create_page, ADR-0047). `nav_area` matches
    the blog console's +Post and the live posts."""
    return {
        "title": post["title"],
        "slug": post["slug"],
        "parent_slug": PARENT_SLUG,
        "under_module_pillar": True,
        "nav_area": "header",
        "tag_slugs": list(TAG_SLUGS),
        "layout": post["layout"],
        "hero_layout": post["hero_layout"],
        "meta_description": post["meta_description"],
    }


def update_body(post: dict, cover_media_id: str | None) -> dict:
    """PageUpdate. html/js/scss are required keys and must be empty for an author (Blocks
    mode: the buffer is Tailor's render cache). `hero_media_id` is null — the tile is the
    Cover Image (John, 2026-09-23). `thumbnail_media_id` is sent ONLY when we set the cover:
    in manual mode it is omitted, so a cover Heather already picked on the draft survives
    a re-run. `parent_slug` is omitted (on PUT it means "reparent")."""
    body = {
        "title": post["title"],
        "html": "",
        "js": "",
        "scss": "",
        "content_mode": "blocks",
        "blocks": post["blocks"],
        "tag_slugs": list(TAG_SLUGS),
        "layout": post["layout"],
        "hero_layout": post["hero_layout"],
        "hero_media_id": None,
        "meta_description": post["meta_description"],
    }
    if cover_media_id is not None:
        body["thumbnail_media_id"] = cover_media_id
    return body


def owned_state(page: dict, *, with_cover: bool) -> dict:
    """The part of a page this pipeline WRITES, as Tailor returns it (a PageRead): the fields
    update_body sends, plus the Cover Image only when the pipeline set it. A hand edit to
    anything here means Heather has touched the draft; a field outside it (the cover she set
    in manual mode, the byline, a note) does not freeze the pipeline."""
    state = {
        "title": page.get("title"),
        "blocks": page.get("blocks"),
        "tags": sorted(t.get("slug") for t in page.get("tags") or []),
        "layout": page.get("layout"),
        "hero_layout": page.get("hero_layout"),
        "hero_media_id": page.get("hero_media_id"),
        "meta_description": page.get("meta_description"),
    }
    if with_cover:
        state["thumbnail_media_id"] = page.get("thumbnail_media_id")
    return state


def fingerprint(page: dict, *, with_cover: bool) -> str:
    """sha256 of owned_state — the ~/jhc/clients/rockstar-blends/scripts/_authored.py rule
    (John, 2026-09-21: scripts defer to hand edits). Computed from what TAILOR RETURNED, not
    from what we sent, so server-side normalisation of the blocks can't look like an edit."""
    blob = json.dumps(
        owned_state(page, with_cover=with_cover), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _hand_edit_reason(page: dict, rec: dict) -> str | None:
    """Why the pipeline must NOT write this draft, or None when it may."""
    stamp = rec.get("fingerprint")
    if not stamp:
        return (
            "there is no fingerprint of the pipeline's last write in the record (it predates "
            "the hand-edit guard), so a hand edit can't be ruled out"
        )
    if fingerprint(page, with_cover=bool(rec.get("fingerprint_cover"))) != stamp:
        return "the draft was edited by hand since the pipeline's last write"
    return None


def external_hosts(post: dict) -> list[str]:
    """Every distinct external host a link in the post points at — the draft-ready alert
    lists them so Heather reviews links by host (C-11)."""
    hosts = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "href" and isinstance(v, str) and v.startswith("https://"):
                    hosts.add((urlsplit(v).hostname or "").lower())
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(post.get("blocks"))
    return sorted(h for h in hosts if h)


def _byline(page: dict) -> str | None:
    name = page.get("author_display_name") or {}
    joined = " ".join(p for p in (name.get("first_name"), name.get("last_name")) if p)
    return joined or None


# ── the write ───────────────────────────────────────────────────────────────────


def _cover(client, store, key, rec, page_id, tile_path) -> tuple[str, str | None, str, str | None]:
    """(mode, media_id, note, warning). A refused upload does not stop the write — a draft
    with an empty Cover Image is whole; a draft with no blocks is not (AC-3). `warning` is
    set when the refusal needs its own named line in the alert (the upload budget)."""
    if tile_path is None:
        return COVER_MANUAL, None, "no episode tile was given to upload", None
    tile_path = Path(tile_path)
    data = tile_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    prefix = ""
    if rec and rec.get("cover_media_id") and rec.get("cover_sha256") == sha:
        # F-11: the recorded image must still exist before it is reused — someone may have
        # deleted it from the media library (editor tier can). The public GET is the only
        # read an author has; 200 = there, 404 = gone → upload again and say so.
        status = client.media_status(rec["cover_media_id"])
        if status == 200:
            return (
                COVER_AUTO,
                rec["cover_media_id"],
                "the same tile was uploaded on an earlier run — reused, not re-uploaded",
                None,
            )
        if status != 404:
            raise TailorError("could not confirm the recorded cover image", status)
        prefix = "the recorded cover image is gone from the site, so "
    ctype = _TILE_TYPES.get(tile_path.suffix.lower(), "application/octet-stream")
    try:
        media = client.upload_media(page_id, tile_path.name, data, ctype, name=tile_path.stem)
    except (TailorAuthError, TailorConfigError):
        raise
    except TailorRateLimited as exc:
        # Not a quiet fall-back (Jen): Tailor allows an author 30 uploads an hour
        # (core/rate_limit.py author_upload_limiter). Hitting it on one post means something
        # is looping or the credential is being used elsewhere — name it.
        note = f"the tile upload was refused ({exc})"
        logger.warning("blog_publish: cover upload budget exceeded — %s", note)
        return (
            COVER_MANUAL,
            None,
            note,
            "UPLOAD BUDGET EXCEEDED — Tailor allows this account 30 image uploads an "
            "hour and refused this one. Check nothing else is using the pipeline "
            "account; re-run after the hour to set the cover, or set it by hand.",
        )
    except TailorError as exc:
        if not exc.answered:
            raise  # an outage, not a refusal — let the caller retry the whole idempotent write
        if exc.status == 403:
            note = "this site refuses an author's image upload (HTTP 403 — Tailor before v0.9.94 / BACKLOG-0216)"
        else:
            note = f"the tile upload was refused ({exc})"
        logger.warning("blog_publish: cover upload refused — manual-cover mode: %s", note)
        return COVER_MANUAL, None, prefix + note, None
    store.put(key, cover_media_id=str(media["id"]), cover_sha256=sha)
    return (COVER_AUTO, str(media["id"]), prefix + "uploaded the episode tile and set it as the Cover Image", None)


def _skip_published(page: dict, public_base: str, hosts: list[str], reason: str | None = None) -> BlogResult:
    """Hands off: a PUBLISHED post (AC-5), or — with `reason` — a draft someone edited by hand
    since our last write (Toby F-1). Either way the human copy wins and its URL is real."""
    if reason:
        logger.warning("blog_publish: /blog/%s — %s — left alone", page["slug"], reason)
    else:
        logger.warning("blog_publish: /blog/%s is PUBLISHED — hands off (AC-5)", page["slug"])
    return BlogResult(
        outcome="skipped_edited" if reason else "skipped_published",
        page_id=str(page["id"]),
        slug=page["slug"],
        skip_reason=reason,
        url=f"{public_base}/{page['slug']}",
        status=page["status"],
        cover_mode=None,
        cover_note="",
        cover_media_id=None,
        cover_warning=None,
        byline=_byline(page),
        block_count=len(page.get("blocks") or []),
        hosts=hosts,
    )


def publish(
    post: dict,
    quotes: list,
    *,
    episode_code: str,
    api_base: str,
    public_base: str,
    credentials: tuple[str, str],
    record_path: Path,
    lock_path: Path,
    tile_path: Path | None = None,
    allow_loopback: bool = False,
    transport=None,
    lock_timeout: float = 600.0,
) -> BlogResult:
    """Gate, then write `post` to Tailor as a draft. See the module docstring."""
    # AC-3 — before any call, before the lock, before the credential is touched.
    content_gates.assert_blog_post_wellformed(post, quotes, episode_code)
    if post.get("tag_slugs") != TAG_SLUGS:
        raise content_gates.GateFailure(f"{episode_code}: tag set is {post.get('tag_slugs')!r}, not {TAG_SLUGS}")
    hosts = external_hosts(post)
    public_base = public_base.rstrip("/")

    try:
        client = TailorClient(api_base, allow_loopback=allow_loopback, transport=transport)
    except TailorConfigError as exc:
        # F-2: a refused BLOG_API_BASE is configuration, not an outage — a retry (which would
        # also re-compose faq.md with the model) cannot fix it. Nothing was sent.
        raise BlogStop(f"{episode_code}: BLOG_API_BASE refused ({exc}) — nothing was sent") from None
    store = RecordStore(record_path)
    key = RecordStore.key(client.base, episode_code)

    with blog_write_lock(lock_path, timeout=lock_timeout):
        try:
            result = _write(client, store, key, post, hosts, episode_code, public_base, credentials, tile_path)
            result.superseded = client.superseded
            return result
        except BlogStop as exc:
            # N-9 (Orly): _write's own stops (collision, gone, changed under us, the F-1
            # hand-edit stand-down) can follow a re-login too — name the supersede on them.
            if client.superseded:
                raise BlogStop(f"{exc}{_superseded_suffix(client)}") from None
            raise
        except (TailorAuthError, TailorRateLimited, TailorConfigError) as exc:
            raise BlogStop(f"{episode_code}: {exc} — stopped, no retry (C-5)" + _superseded_suffix(client)) from None
        except TailorError as exc:
            if exc.answered:
                raise BlogStop(f"{episode_code}: {exc} — stopped" + _superseded_suffix(client)) from None
            if client.superseded:
                # N-9: a transient error goes to the worker's retry, not to an alert; the
                # supersede must at least reach the journal by name before it does.
                logger.warning("blog_publish: %s: %s", episode_code, SUPERSEDED_LINE)
            raise
        finally:
            client.logout()  # C-2: every path, including a raise


def _superseded_suffix(client) -> str:
    return f". {SUPERSEDED_LINE}" if client.superseded else ""


def _write(client, store, key, post, hosts, episode_code, public_base, credentials, tile_path) -> BlogResult:
    store.get(key)  # F-5: an unreadable record stops the run BEFORE it logs in
    client.login(*credentials)
    role = client.whoami().get("role")
    if role != "author":
        # C-1: a credential with more power than it needs is a failure, not a convenience.
        raise BlogStop(f"{episode_code}: the blog credential logs in as {role!r}, not 'author' — refusing to write")

    rec = store.get(key)
    if rec:
        page = client.get_child(PARENT_SLUG, rec["slug"])
        if page is None:
            raise BlogStop(
                f"{episode_code}: our recorded post /blog/{rec['slug']} is gone (renamed or deleted "
                f"in the editor). Not creating a second post — resolve by hand."
            )
        if str(page["id"]) != rec["page_id"]:
            raise BlogStop(
                f"{episode_code}: /blog/{rec['slug']} is now a different page than the one this "
                f"pipeline created. Not touching it."
            )
        if page["status"] == "published":
            return _skip_published(page, public_base, hosts)
        reason = _hand_edit_reason(page, rec)
        if reason:
            return _skip_published(page, public_base, hosts, reason)
        outcome = "updated"
    else:
        if client.get_child(PARENT_SLUG, post["slug"]) is not None:
            raise BlogStop(
                f"{episode_code}: /blog/{post['slug']} already exists and this pipeline has no "
                f"record of creating it. Not adopting it — resolve the slug by hand."
            )
        try:
            page = client.create_page(create_body(post))
        except (TailorAuthError, TailorRateLimited):
            raise  # C-5 stops, named by publish() (with the N-2 supersede line)
        except TailorError as exc:
            if exc.answered:
                raise BlogStop(f"{episode_code}: create refused ({exc}) — nothing was written") from None
            raise
        # C-7: the record is written BEFORE anything else touches the page, so every later
        # failure leaves a re-run that updates THIS page instead of making another.
        rec = store.put(
            key,
            page_id=str(page["id"]),
            slug=page["slug"],
            episode=episode_code,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # the shell as Tailor stored it — so a retry after a failed PUT can
            # tell our untouched shell from one Heather has started writing in
            fingerprint=fingerprint(page, with_cover=False),
            fingerprint_cover=False,
        )
        outcome = "created"

    slug, page_id = rec["slug"], rec["page_id"]
    cover_mode, cover_media_id, cover_note, cover_warning = _cover(client, store, key, rec, page_id, tile_path)

    # C-9: status from a GET immediately before the PUT. Heather may have published between.
    fresh = client.get_child(PARENT_SLUG, slug)
    if fresh is None or str(fresh["id"]) != page_id:
        raise BlogStop(f"{episode_code}: /blog/{slug} changed under us before the write — stopped")
    if fresh["status"] == "published":
        return _skip_published(fresh, public_base, hosts)
    reason = _hand_edit_reason(fresh, store.get(key))
    if reason:
        return _skip_published(fresh, public_base, hosts, reason)

    try:
        page = client.update_child(PARENT_SLUG, slug, update_body(post, cover_media_id))
    except (TailorAuthError, TailorRateLimited):
        raise  # C-5 stops, named by publish() (with the N-2 supersede line)
    except TailorError as exc:
        if exc.answered:
            raise BlogStop(
                f"{episode_code}: the content write was refused ({exc}). The draft /blog/{slug} "
                f"exists but its content is not current — fix and re-run (it updates in place)."
            ) from None
        raise
    # F-1: stamp what Tailor now holds as OURS. The next run writes only if it is unchanged.
    store.put(
        key,
        fingerprint=fingerprint(page, with_cover=cover_media_id is not None),
        fingerprint_cover=cover_media_id is not None,
        written_at=page.get("updated_at"),
    )

    return BlogResult(
        outcome=outcome,
        page_id=page_id,
        slug=page["slug"],
        url=f"{public_base}/{page['slug']}",
        status=page["status"],
        cover_mode=cover_mode,
        cover_note=cover_note,
        cover_media_id=cover_media_id,
        cover_warning=cover_warning,
        byline=_byline(page),
        block_count=len(page.get("blocks") or []),
        hosts=hosts,
    )


# ── links only: a late publisher email swaps the listen links (TRIG-03) ─────────


@dataclasses.dataclass
class SwapResult:
    outcome: str  # "swapped" | "nothing_to_swap" | "skipped_published" | "skipped_mode"
    url: str  # public URL of the post
    swapped: int  # listen buttons changed
    restamped: bool  # True only when the draft was still the pipeline's own before the swap
    reason: str = ""


def swap_hrefs(blocks, mapping: dict[str, str]) -> tuple[object, int]:
    """(a copy of `blocks`, n): every `cta` block whose href is EXACTLY a key of `mapping` gets
    the mapped href. Nothing else changes — not the label, not any other block, not a show
    link quoted in body text (Orly, TRIG-03 C)."""
    count = 0

    def walk(node):
        nonlocal count
        if isinstance(node, list):
            return [walk(n) for n in node]
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == "cta" and out.get("href") in mapping:
                out["href"] = mapping[out["href"]]
                count += 1
            return out
        return node

    return walk(blocks), count


def swap_listen_links(
    *,
    episode_code: str,
    mapping: dict[str, str],
    api_base: str,
    public_base: str,
    credentials: tuple[str, str],
    record_path: Path,
    lock_path: Path,
    allow_loopback: bool = False,
    transport=None,
    lock_timeout: float = 600.0,
) -> SwapResult:
    """Swap the show-page listen links on OUR draft for the episode's, and change nothing else.

    `mapping` is {show_url: episode_url}. Same safeguards as publish(): the lock (C-6), one login
    as an author (C-1, C-5), our own record (C-7: never a page we didn't make), and the status
    from a GET immediately before the PUT (C-9: a published post is never touched).

    Q1 (John, 2026-10-10): the swap goes ahead on a draft Heather has edited, because it only
    touches links that are still the pipeline's placeholders. But it re-stamps the fingerprint
    ONLY when the draft was still the pipeline's own before the swap; otherwise re-stamping would
    adopt her edits as ours and the next full run would overwrite them (Orly, TRIG-03 C).

    The PUT carries the fields Tailor requires (title as it stands, html/js/scss "") plus `blocks`;
    everything else is omitted, which Tailor leaves unchanged. `html` "" is only safe in Blocks
    mode (there the buffer is the render cache), so any other mode is left alone and named."""
    public_base = public_base.rstrip("/")
    try:
        client = TailorClient(api_base, allow_loopback=allow_loopback, transport=transport)
    except TailorConfigError as exc:
        raise BlogStop(f"{episode_code}: BLOG_API_BASE refused ({exc}) — nothing was sent") from None
    store = RecordStore(record_path)
    key = RecordStore.key(client.base, episode_code)

    with blog_write_lock(lock_path, timeout=lock_timeout):
        try:
            rec = store.get(key)  # F-5: an unreadable record stops the run before it logs in
            if not rec:
                raise BlogStop(
                    f"{episode_code}: no record of a post this pipeline made — not "
                    f"searching for one to edit. Paste the links by hand."
                )
            client.login(*credentials)
            role = client.whoami().get("role")
            if role != "author":
                raise BlogStop(
                    f"{episode_code}: the blog credential logs in as {role!r}, not 'author' — refusing to write"
                )
            page = client.get_child(PARENT_SLUG, rec["slug"])
            if page is None or str(page["id"]) != rec["page_id"]:
                raise BlogStop(
                    f"{episode_code}: our recorded post /blog/{rec['slug']} is gone or is a "
                    f"different page (renamed in the editor?). Not touching it — paste the links by hand."
                )
            url = f"{public_base}/{page['slug']}"
            if page["status"] == "published":
                return SwapResult("skipped_published", url, 0, False, "the post is published (AC-5)")
            if page.get("content_mode") != "blocks":
                return SwapResult(
                    "skipped_mode", url, 0, False, f"the draft is in {page.get('content_mode')!r} mode, not blocks"
                )
            ours = _hand_edit_reason(page, rec) is None
            blocks, n = swap_hrefs(page.get("blocks") or [], mapping)
            if n == 0:
                return SwapResult("nothing_to_swap", url, 0, False, "no listen button still points at a show page")
            # C-9: status from a GET immediately before the PUT.
            fresh = client.get_child(PARENT_SLUG, rec["slug"])
            if fresh is None or str(fresh["id"]) != rec["page_id"]:
                raise BlogStop(f"{episode_code}: /blog/{rec['slug']} changed under us before the write — stopped")
            if fresh["status"] == "published":
                return SwapResult("skipped_published", url, 0, False, "published between the read and the write")
            if fresh.get("blocks") != page.get("blocks"):
                raise BlogStop(
                    f"{episode_code}: /blog/{rec['slug']} was edited during the swap — stopped, "
                    f"nothing written. Re-run, or paste the links by hand."
                )
            try:
                written = client.update_child(
                    PARENT_SLUG,
                    rec["slug"],
                    {"title": fresh["title"], "html": "", "js": "", "scss": "", "blocks": blocks},
                )
            except TailorError as exc:
                if exc.answered:
                    raise BlogStop(f"{episode_code}: the link swap was refused ({exc}) — nothing changed") from None
                raise
            if ours:
                store.put(
                    key,
                    fingerprint=fingerprint(written, with_cover=bool(rec.get("fingerprint_cover"))),
                    written_at=written.get("updated_at"),
                )
            return SwapResult("swapped", url, n, ours)
        except (TailorAuthError, TailorRateLimited, TailorConfigError) as exc:
            raise BlogStop(f"{episode_code}: {exc} — stopped, no retry (C-5)") from None
        finally:
            client.logout()  # C-2: every path, including a raise


# ── the alert ───────────────────────────────────────────────────────────────────


def draft_alert(
    result: BlogResult, episode_code: str, *, tile_link: str | None = None, source: str | None = None
) -> tuple[str, str]:
    """(subject, body) for the draft-ready (or skipped) alert. Carries no credential and no
    cookie — only what Heather needs to review (C-4, C-11). Two security lines ride on EVERY
    outcome when they apply: a supersede during the run (N-2) and an environ credential
    source (N-3)."""
    subject, body = _alert_text(result, episode_code, tile_link)
    extra = []
    if result.superseded:
        subject += " — SESSION SUPERSEDED"
        extra.append(f"  {SUPERSEDED_LINE}")
    if source == SOURCE_ENVIRON:
        subject += " — credential from environ"
        extra.append(f"  {ENVIRON_SOURCE_LINE}")
    if extra:
        body += "\n" + "\n".join(extra) + "\n"
    return subject, body


def _alert_text(result: BlogResult, episode_code: str, tile_link: str | None) -> tuple[str, str]:
    if result.outcome == "skipped_edited":
        subject = f"{episode_code}: blog draft edited by hand — pipeline left it alone"
        body = (
            f"The draft {result.url} was NOT updated: {result.skip_reason}. The pipeline "
            f"defers to hand edits (John's standing rule), so your copy stands. If you want the "
            f"pipeline's version instead, delete the draft's changes by hand or tell John. "
            f"Links elsewhere in Phase 2 use this URL.\n"
        )
        return subject, body
    if result.outcome == "skipped_published":
        subject = f"{episode_code}: blog post already published — pipeline left it alone"
        body = (
            f"The post {result.url} is PUBLISHED, so this run did not touch it (AC-5: the "
            f"human copy wins). Links elsewhere in Phase 2 use this URL.\n"
        )
        return subject, body

    lines = [
        (
            f"The {episode_code} blog post is a DRAFT on the site ({result.outcome}). Review it in "
            f"the editor and publish it yourself — the pipeline never publishes."
        ),
        "",
        f"  Post:   {result.url}  (the public URL once published)",
        f"  Status: {result.status} · {result.block_count} blocks",
    ]
    if result.cover_warning:
        lines.append(f"  Cover Image: {result.cover_warning}")
    if result.cover_mode == COVER_AUTO:
        lines.append(f"  Cover Image: set automatically — {result.cover_note}.")
    else:
        lines.append(f"  Cover Image: MANUAL — {result.cover_note}.")
        lines.append(
            "    → Set Cover Image in the Post tab from the episode tile" + (f": {tile_link}" if tile_link else ".")
        )
    if result.byline == EXPECTED_BYLINE:
        lines.append(f"  Byline: {result.byline}")
    else:
        lines.append(
            f"  Byline: WARNING — the site shows {result.byline!r}, not {EXPECTED_BYLINE!r}. "
            f"Set the byline in the Post tab (editor role) before publishing."
        )
    lines += ["", "  Links in this post point at these hosts — check each one:"]
    lines += [f"    • {h}" for h in result.hosts] or ["    (none)"]
    subject = f"{episode_code}: blog draft ready for review ({result.cover_mode} cover)"
    if result.cover_warning:
        subject += " — upload budget exceeded"
    return subject, "\n".join(lines) + "\n"


# ── OPEN-1 — is the post live? ───────────────────────────────────────────────────


def post_live_check(url: str, *, allow_loopback: bool = False, transport=None) -> tuple[bool, str]:
    """(live, line) for the thank-you step (OPEN-1, John 2026-09-22). The thank-you draft is
    created either way; it is reported READY only when the post answers 200. On a normal
    Phase 2 run it won't — the post was just created as a draft and Heather publishes it on
    review — so the warning is named, not alarming. One definition, used by Phase 2's Step 13
    and create_thank_you_draft.py. Pinned transport: allow-listed host, no redirect followed."""
    try:
        status = url_status(url, allow_loopback=allow_loopback, transport=transport)
    except TailorConfigError as exc:
        return False, f"WARNING — the blog URL was not checked ({exc})."
    if status == 200:
        return True, f"Blog post is live: {url} (HTTP 200)."
    shown = f"HTTP {status}" if status is not None else "no response"
    return False, (
        f"WARNING — post not published yet: {url} answers {shown}. Publish the post before sending the thank-you."
    )
