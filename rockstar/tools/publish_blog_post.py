#!/usr/bin/env python3
"""
publish_blog_post.py — the BLOG-02 AC-8 retest, from the Mac: ONLY the blog step.

    build (blog_post.build_post, no model) → gate (AC-3) → Tailor write (blog_publish)

against BLOG_API_BASE, from committed inputs. It runs exactly the code Phase 2 Step 6 runs
(blog_publish.publish), and nothing else: it never touches Drive, the publication schedule,
the thank-you draft, captions or reels, and it sends no email — the alert Phase 2 would
send is PRINTED instead. That is AC-8 as revised by Angela (2026-09-22): a full
reprocess_episode.py run would write the disposable post's URL into E31's real schedule
entry and render reels, and the box can't reach a local instance anyway.

Usage (the disposable instance; never production, never the content lane):

  python3 -m rockstar.tools.publish_blog_post --episode 31 \\
      --inputs tests/fixtures/blog02/E31 \\
      --spotify "https://open.spotify.com/episode/…" --apple "https://podcasts.apple.com/…" \\
      --tile tests/fixtures/blog02/E31/E31-leyla-tile.jpg \\
      --credential-file ~/jhc/secrets/rockstarblends/local-rsb-995-blogtest-pipeline.env

The credential file is KEY=VALUE (TAILOR_BLOG_EMAIL, TAILOR_BLOG_PASSWORD, optionally
BLOG_API_BASE). It is read by allow-listed key and never printed. The API base MUST be a
loopback host (http://localhost:…): this CLI refuses every other host, production included,
before the credential is read. The worker never allows a loopback host.

State: the ownership record (which page this pipeline created for which episode, keyed by
API base) and the write lock live in --state-dir (default ./state, gitignored). Keep it
between runs: run 2 finds run 1's post through it (Orly C-7).

Exit codes: 0 written or skipped-as-published · 2 the gate refused (no call was made) ·
3 stopped (collision, gone, wrong role, refused login, 429 — see the alert) · 1 transient.
"""

import argparse
import json
import logging
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

from rockstar import config
from rockstar.clients import tailor_client
from rockstar.clients.tailor_client import LOOPBACK_HOSTS, TailorError
from rockstar.content import blog_post, blog_publish
from rockstar.gates import content_gates

REPO = config.CODE_ROOT  # state/ lives there on a laptop

# Tailor's slug shape (core/pages.py SLUG_REGEX) and PageCreate cap (schemas/pages.py,
# max_length=64) — the same rules the server enforces on create.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def slug_override(value: str) -> str:
    """Validate a hand-given slug (argparse type for --slug). MAC-CLI ONLY: the worker path
    (rockstar/jobs/publish_finalize.py) never imports this module, so Phase 2 always proposes the slug
    from the title (blog_post.propose_slug) and takes the URL from what the API returns.
    Exists for the AC-8 retest, where the live E31 must be left in place (John, 2026-09-24)."""
    if not _SLUG_RE.match(value or ""):
        raise argparse.ArgumentTypeError(f"slug {value!r} must match {_SLUG_RE.pattern}")
    if len(value) > blog_post.SLUG_MAX:
        raise argparse.ArgumentTypeError(f"slug is {len(value)} characters; Tailor's cap is {blog_post.SLUG_MAX}")
    if value.endswith("-") or "--" in value:
        raise argparse.ArgumentTypeError(f"slug {value!r} has a dangling or doubled hyphen")
    return value


def _read_inputs(inputs: Path) -> tuple[str, list, str]:
    apple = (inputs / "apple_summary.md").read_text(encoding="utf-8")
    quotes = json.loads((inputs / "quotes.json").read_text(encoding="utf-8"))["quotes"]
    faq = (inputs / "faq.md").read_text(encoding="utf-8")
    return apple, quotes, faq


def _credential_file(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_file():
        raise SystemExit(f"credential file not found: {path}")
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"  note: {path.name} is readable by group/other — it should be 0600", file=sys.stderr)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BLOG-02 AC-8: build → gate → write ONE blog draft to Tailor.")
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument(
        "--inputs", type=Path, required=True, help="folder with apple_summary.md, quotes.json (with `heading`s), faq.md"
    )
    ap.add_argument("--spotify", required=True)
    ap.add_argument("--apple", required=True)
    ap.add_argument(
        "--tile", type=Path, default=None, help="episode tile image for the Cover Image (omit → manual-cover mode)"
    )
    ap.add_argument("--title", default=None, help="override the episode title (default: apple_summary's, ADR-0009)")
    ap.add_argument(
        "--slug",
        type=slug_override,
        default=None,
        help="override the proposed slug (retest only; Tailor's shape rule + 64 cap)",
    )
    ap.add_argument("--credential-file", type=Path, required=True)
    ap.add_argument(
        "--api-base", default=None, help="Tailor site root (default: BLOG_API_BASE from the credential file)"
    )
    ap.add_argument("--state-dir", type=Path, default=REPO / "state")
    ap.add_argument("--dry-run", action="store_true", help="build + gate + print the plan; no login, no call")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    code = f"E{args.episode}"
    apple, quotes, faq = _read_inputs(args.inputs)
    title = args.title or blog_post.title_from_apple_summary(apple)
    meta = {
        "episode_title": title,
        "episode_number": args.episode,
        "spotify_url": args.spotify,
        "apple_url": args.apple,
    }

    print(f"{code} blog step — build → gate → Tailor write (nothing else)")
    try:
        post = blog_post.build_post(apple, quotes, faq, meta)
        if args.slug:
            post["slug"] = args.slug
        content_gates.assert_blog_post_wellformed(post, quotes, code)
    except content_gates.GateFailure as exc:
        print(f"  GATE REFUSED — no call was made:\n    {exc}")
        return 2
    print(f"  built: {len(post['blocks'])} blocks · title {post['title']!r}")
    print(f"  proposed slug: {post['slug']} ({len(post['slug'])} chars" + (", --slug override)" if args.slug else ")"))

    cred_path = _credential_file(args.credential_file)
    api_base = args.api_base or blog_publish.read_env_file(cred_path, keys=("BLOG_API_BASE",)).get("BLOG_API_BASE")
    if not api_base:
        raise SystemExit("no API base: pass --api-base or put BLOG_API_BASE in the credential file")
    # F-3 (Angela): this CLI is for the disposable instance ONLY. A non-loopback base —
    # production included — is refused before the credential is even read. E32 and every
    # real episode go through Phase 2 on the box.
    if (urlsplit(api_base).hostname or "").lower() not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"refused: {api_base!r} is not a loopback host — this CLI writes only to "
            f"a local disposable instance (localhost / 127.0.0.1 / ::1)"
        )
    # Orly N-8: print the validated root, never the raw string (it could carry userinfo).
    try:
        shown = tailor_client.validate_base(api_base, allow_loopback=True)
    except tailor_client.TailorConfigError as exc:
        raise SystemExit(f"refused: {exc}") from None
    print(f"  API base: {shown}")
    if args.dry_run:
        print("  dry run — stopping before login")
        return 0

    try:
        result = blog_publish.publish(
            post,
            quotes,
            episode_code=code,
            api_base=api_base,
            public_base=api_base.rstrip("/") + "/blog",
            credentials=blog_publish.load_credentials(env_file=cred_path),
            record_path=args.state_dir / "blog_posts.json",
            lock_path=args.state_dir / "tailor_blog.lock",
            tile_path=args.tile,
            allow_loopback=True,
            lock_timeout=30,
        )
    except content_gates.GateFailure as exc:
        print(f"  GATE REFUSED — no call was made:\n    {exc}")
        return 2
    except blog_publish.BlogStop as exc:
        print("  STOPPED — nothing further was written. The alert Phase 2 would send:")
        print(f"    {code}: blog post NOT written — Phase 2 stopped")
        print(f"    {exc}")
        return 3
    except (TailorError, blog_publish.BlogBusy) as exc:
        print(f"  TRANSIENT — {exc}. Re-running is safe (it updates the same draft).")
        return 1

    print(f"  outcome:  {result.outcome}")
    print(f"  post id:  {result.page_id}")
    print(f"  slug:     {result.slug}   (from the API — AC-6)")
    print(f"  url:      {result.url}")
    print(f"  status:   {result.status}")
    if result.skip_reason:
        print(f"  left alone: {result.skip_reason}")
    if not result.outcome.startswith("skipped"):
        print(f"  blocks:   {result.block_count}")
        print(
            f"  cover:    {result.cover_mode} cover — {result.cover_note}"
            + (f" (media {result.cover_media_id})" if result.cover_media_id else "")
        )
        print(
            f"  byline:   {result.byline!r}"
            + ("" if result.byline == blog_publish.EXPECTED_BYLINE else f"   ← NOT {blog_publish.EXPECTED_BYLINE!r}")
        )
    subject, body = blog_publish.draft_alert(result, code, tile_link=str(args.tile) if args.tile else None)
    print("  alert Phase 2 would send:")
    print(f"    Subject: {subject}")
    for line in body.rstrip().splitlines():
        print(f"    {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
