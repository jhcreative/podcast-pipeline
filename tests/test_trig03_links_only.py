"""
test_trig03_links_only.py — TRIG-03: a late "Your Episode is Live" email swaps in the real links, and nothing else.

Run: python3 -B test_trig03_links_only.py   (plain asserts, no pytest needed)

Why (2026-10-10, read from the box): the publisher email is back, and it can arrive AFTER Phase 2 ran on the show
pages. E33 (job 142) then ran ALL of Phase 2 again — a second thank-you draft, re-rendered reels, regenerated captions.
E32 (job 141) did the same and stopped at a renamed post. A late email now swaps the links where the pipeline put a
placeholder, and touches nothing else.

Acceptance criteria → tests:
  AC-1  images first then email → a links-only run: no Phase 2, ZERO model calls     → test_images_first_then_email_*
  AC-2  only the listen buttons that still equal the show URLs change; PUT is the required
        fields + blocks; no re-stamp after a hand-edited swap                        → test_swap_*
  AC-3  a published post is not touched                                              → test_swap_published_post_untouched
  AC-4  exactly one thank-you draft, updated IN PLACE (same id), episode links        → test_images_first_then_email_*
  AC-5  schedule: real URLs, pending false (true while a manual step is left)         → same, test_e32_shape_*
  AC-6  links already real → nothing written                                          → test_links_already_real_is_a_noop
  AC-9  an edited draft is left alone: body edit, new message id, an attachment;
        a draft that is gone or has no fingerprint → nothing created                  → test_draft_left_alone_*
  AC-10 sender: exact domain; topmost mx.google.com; dmarc=pass or aligned dkim=pass;
        real box headers pass, forgeries refused, in the job AND the webhook          → test_sender_*, test_webhook_*
  AC-11 strict episode links (Apple show id pinned), no id another episode holds      → test_links_refused_*
  AC-12 the route is decided in the worker; an email naming another episode's title
        stops; a retry after a partial run ends in the same state                    → test_title_*, test_retry_*
  Step 13 records the draft as Gmail stored it                                        → test_record_ty_draft_*

Discipline: publisher_email, blog_publish, thank_you, content_gates and the routing are the real code. Gmail, Drive,
Tailor (test_blog02b's FakeTailor) and the model are faked; the fake model fails the test if anything calls it.
"""

import asyncio
import copy
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

import _swap as _fakes  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
STATE = Path(tempfile.mkdtemp(prefix="trig03-"))
os.environ["STATE_DIR"] = str(STATE)
AUTH = json.loads((REPO / "tests" / "fixtures" / "trig03" / "auth_headers.json").read_text())

import test_blog02b_tailor_write as b2  # noqa: E402  — FakeTailor, Env, the blog fixtures

from rockstar.clients import publisher_email as pe  # noqa: E402
from rockstar.content import blog_publish  # noqa: E402

SHOW_APPLE = "https://podcasts.apple.com/us/podcast/genx-hits-menopause/id1785708777"
SHOW_SPOTIFY = "https://open.spotify.com/show/3CIrqvlAWVsin0ZwFzykv6"
EP_APPLE = "https://podcasts.apple.com/us/podcast/genx-hits-menopause/id1785708777?i=1000700000104"
EP_SPOTIFY = "https://open.spotify.com/episode/0TPlCabcdefghijklmnopq"
SUBJECT = "Your Episode is Live | EP 33 GenX Hits Menopause"


def _hdrs(case: dict) -> list[dict]:
    return [{"name": "From", "value": case["from"]}] + [
        {"name": "Authentication-Results", "value": v} for v in case["authentication_results"]
    ]


# ── AC-10: the sender ─────────────────────────────────────────────────────────


def test_sender_real_box_headers_pass():
    assert AUTH["pass"], "fixture has no pass cases"
    for c in AUTH["pass"]:
        ok, why = pe.authenticated_sender(_hdrs(c))
        assert ok, f"{c['label']}: {why}"


def test_sender_forgeries_refused():
    assert len(AUTH["refuse"]) >= 6, "fixture lost refusal cases"
    for c in AUTH["refuse"]:
        ok, why = pe.authenticated_sender(_hdrs(c))
        assert not ok, f"{c['label']} passed: {why}"


def test_sender_aligned_dkim_alone_passes_unaligned_does_not():
    base = "Publisher <x@publisher.example.test>"
    ok, _ = pe.authenticated_sender(
        _hdrs(
            {
                "from": base,
                "authentication_results": [
                    "mx.google.com; dkim=pass header.i=@send.publisher.example.test header.s=resend; spf=pass"
                ],
            }
        )
    )
    assert ok, "a DKIM signature from a subdomain of the From domain is aligned (relaxed)"
    ok, _ = pe.authenticated_sender(
        _hdrs(
            {
                "from": base,
                "authentication_results": ["mx.google.com; dkim=pass header.i=@publisher.example.test.attacker.example"],
            }
        )
    )
    assert not ok, "a signer that merely CONTAINS the domain is not aligned"


def test_sender_arc_comment_is_not_google_verdict():
    # ARC's comment carries its own 'dkim=pass … dmarc=pass'; with comments removed nothing passes.
    ok, why = pe.authenticated_sender(
        _hdrs(
            {
                "from": "P <x@legacy-publisher.example.test>",
                "authentication_results": [
                    "mx.google.com; arc=pass (i=1 spf=pass dkim=pass dkdomain=legacy-publisher.example.test dmarc=pass "
                    "fromdomain=legacy-publisher.example.test); spf=fail smtp.mailfrom=a@attacker.example"
                ],
            }
        )
    )
    assert not ok, why


def test_sender_one_from_address_only():
    ar = ["mx.google.com; dmarc=pass header.from=legacy-publisher.example.test"]
    ok, _ = pe.authenticated_sender(
        _hdrs({"from": "y@legacy-publisher.example.test, x@attacker.example", "authentication_results": ar})
    )
    assert not ok, "two From addresses"
    two = _hdrs({"from": "y@legacy-publisher.example.test", "authentication_results": ar}) + [
        {"name": "From", "value": "x@attacker.example"}
    ]
    ok, _ = pe.authenticated_sender(two)
    assert not ok, "two From headers"


def test_sender_domain_is_exact():
    assert pe.is_publisher_sender("Publisher <notices@legacy-publisher.example.test>")
    assert not pe.is_publisher_sender("x@legacy-publisher.example.test.attacker.example")
    assert not pe.is_publisher_sender("x@evillegacy-publisher.example.test")
    assert not pe.is_publisher_sender("legacy-publisher.example.test <x@attacker.example>")


# ── AC-11: the links ──────────────────────────────────────────────────────────


def test_episode_link_patterns():
    assert pe.episode_url_problems(EP_APPLE, EP_SPOTIFY) == []
    assert len(pe.episode_url_problems(SHOW_APPLE, SHOW_SPOTIFY)) == 2, "show pages are not episode links"
    other_show = EP_APPLE.replace("id1785708777", "id1111111111")
    assert pe.episode_url_problems(other_show, EP_SPOTIFY), "another show's episode is refused"
    assert pe.episode_url_problems(EP_APPLE + "&uo=4", EP_SPOTIFY), "trailing junk is refused"
    assert pe.clean_spotify_url(EP_SPOTIFY + "?si=8OG1L9TK") == EP_SPOTIFY


# ── AC-2/3: the blog swap, against the FakeTailor ─────────────────────────────


def _draft_with_show_links():
    """Our draft on the fake Tailor, built exactly as Phase 2 builds it, with the show links."""
    e = b2.Env()
    saved = dict(b2.LISTEN)
    b2.LISTEN.update(apple_url=SHOW_APPLE, spotify_url=SHOW_SPOTIFY)
    try:
        post, quotes = b2._post()
    finally:
        b2.LISTEN.clear()
        b2.LISTEN.update(saved)
    res = e.publish(post, quotes)
    assert res.outcome == "created", res
    return e, res


def _swap(e, **kw):
    args = dict(
        episode_code="E31",
        mapping={SHOW_APPLE: EP_APPLE, SHOW_SPOTIFY: EP_SPOTIFY},
        api_base=b2.API,
        public_base=b2.PUBLIC,
        credentials=(b2.EMAIL, b2.PASSWORD),
        record_path=e.tmp / "blog_posts.json",
        lock_path=e.tmp / "tailor_blog.lock",
        transport=e.fake,
        lock_timeout=0,
    )
    args.update(kw)
    return blog_publish.swap_listen_links(**args)


def _ctas(blocks):
    out = []

    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            if n.get("type") == "cta":
                out.append((n.get("label"), n.get("href")))
            for v in n.values():
                walk(v)

    walk(blocks)
    return out


def _without_hrefs(blocks, hrefs):
    """The blocks with the given hrefs blanked — what must be byte-identical before and after."""
    s = json.dumps(blocks, sort_keys=True)
    for h in hrefs:
        s = s.replace(json.dumps(h), '"<listen>"')
    return s


def test_swap_changes_only_the_listen_buttons():
    e, res = _draft_with_show_links()
    page = e.fake.pages[res.slug]
    before = copy.deepcopy(page["blocks"])
    puts_before = len(e.fake.calls("PUT"))
    r = _swap(e)
    assert r.outcome == "swapped" and r.swapped == 2 and r.restamped, r
    put = b2._body(e.fake.calls("PUT")[-1])
    assert len(e.fake.calls("PUT")) == puts_before + 1
    assert set(put) == {"title", "html", "js", "scss", "blocks"}, sorted(put)
    assert put["html"] == put["js"] == put["scss"] == "" and put["title"] == page["title"]
    ctas = dict(_ctas(page["blocks"]))
    assert ctas["Listen on Apple"] == EP_APPLE and ctas["Listen on Spotify"] == EP_SPOTIFY, ctas
    assert _without_hrefs(before, [SHOW_APPLE, SHOW_SPOTIFY]) == _without_hrefs(
        page["blocks"], [EP_APPLE, EP_SPOTIFY]
    ), "every other block must be byte-identical"
    # The draft was ours and is ours again: a later full run may still update it.
    again = e.publish()
    assert again.outcome == "updated", again


def test_swap_on_a_hand_edited_draft_never_adopts_the_edit():
    e, res = _draft_with_show_links()
    page = e.fake.pages[res.slug]
    page["blocks"].append({"type": "p", "inlines": [{"text": "Heather's own note."}]})  # her edit
    rec_before = json.loads((e.tmp / "blog_posts.json").read_text())
    r = _swap(e)
    assert r.outcome == "swapped" and r.swapped == 2 and not r.restamped, r
    assert json.loads((e.tmp / "blog_posts.json").read_text()) == rec_before, "fingerprint must not be re-stamped"
    assert any("Heather's own note." in json.dumps(b) for b in page["blocks"]), "her edit survives"
    later = e.publish()
    assert later.outcome == "skipped_edited", f"a later full run must still see her edit: {later.outcome}"


def test_swap_published_post_untouched():
    e, res = _draft_with_show_links()
    e.fake.pages[res.slug]["status"] = "published"
    puts = len(e.fake.calls("PUT"))
    r = _swap(e)
    assert r.outcome == "skipped_published" and len(e.fake.calls("PUT")) == puts, r


def test_swap_link_already_changed_by_hand_is_left():
    e, res = _draft_with_show_links()
    page = e.fake.pages[res.slug]
    page["blocks"] = json.loads(json.dumps(page["blocks"]).replace(json.dumps(SHOW_APPLE), json.dumps(EP_APPLE)))
    r = _swap(e)
    assert r.swapped == 1, "only the Spotify button still held a show link"
    page["blocks"] = json.loads(json.dumps(page["blocks"]).replace(json.dumps(EP_SPOTIFY), json.dumps(EP_SPOTIFY)))
    puts = len(e.fake.calls("PUT"))
    r = _swap(e)
    assert r.outcome == "nothing_to_swap" and len(e.fake.calls("PUT")) == puts, r


def test_swap_post_gone_or_not_ours_stops():
    e, res = _draft_with_show_links()
    del e.fake.pages[res.slug]
    try:
        _swap(e)
    except blog_publish.BlogStop as exc:
        assert "gone" in str(exc)
    else:
        raise AssertionError("a renamed post must stop the swap (E32's shape)")
    e2 = b2.Env()
    try:
        _swap(e2)
    except blog_publish.BlogStop as exc:
        assert "no record" in str(exc)
    else:
        raise AssertionError("no record → never search for a page to edit")


def test_swap_edited_between_read_and_write_stops():
    e, res = _draft_with_show_links()
    gets = {"n": 0}

    def on_get(page):
        gets["n"] += 1
        if gets["n"] == 2:  # the C-9 re-read
            page["blocks"].append({"type": "p", "inlines": [{"text": "typed just now"}]})

    e.fake.on_get = on_get
    puts = len(e.fake.calls("PUT"))
    try:
        _swap(e)
    except blog_publish.BlogStop as exc:
        assert "edited during the swap" in str(exc)
    else:
        raise AssertionError("an edit between the read and the write must stop the swap")
    assert len(e.fake.calls("PUT")) == puts, "nothing written"


def test_swap_page_without_a_mode_left_alone():
    e, res = _draft_with_show_links()
    e.fake.pages[res.slug].pop("content_mode")
    assert _swap(e).outcome == "skipped_mode", "a page that doesn't say it is in blocks mode is not written"


def test_swap_html_mode_left_alone():
    e, res = _draft_with_show_links()
    e.fake.pages[res.slug]["content_mode"] = "html"
    r = _swap(e)
    assert r.outcome == "skipped_mode", r


# ── the links-only run: publish_finalize with Gmail/Drive/model faked ─────────


class FakeGmail(types.ModuleType):
    """Drafts held in memory. create/delete must never be called by a links-only run."""

    class DraftGone(LookupError):
        pass

    def __init__(self, msg_headers, html):
        super().__init__("gmail_client")
        self.msg_headers, self.html = msg_headers, html
        self.drafts, self.alerts, self.updates, self.n = {}, [], [], 0
        self.fail_next_update = False

    def add_draft(self, to, subject, body, single=True):
        self.n += 1
        did = f"d{self.n}"
        self.drafts[did] = {
            "to": to,
            "subject": subject,
            "body": body,
            "message_id": f"m{self.n}",
            "single_text_plain": single,
        }
        return did

    # the gmail_client surface publish_finalize uses
    def get_message(self, mid):
        return {"id": mid, "payload": {"headers": self.msg_headers}}

    def get_header(self, msg, name):
        for h in msg.get("payload", {}).get("headers", []):
            if h["name"].lower() == name.lower():
                return h["value"]
        return ""

    def extract_plain_text(self, msg):
        return ""

    def extract_html(self, msg):
        return self.html

    def draft_state(self, did):
        if did not in self.drafts:
            raise self.DraftGone(did)
        d = self.drafts[did]
        return {k: d[k] for k in ("message_id", "single_text_plain", "to", "subject", "body")}

    def update_draft(self, did, to, subject, body):
        if self.fail_next_update:
            self.fail_next_update = False
            raise ConnectionError("simulated Gmail 503")
        self.n += 1
        self.drafts[did].update(to=to, subject=subject, body=body, message_id=f"m{self.n}")
        self.updates.append(did)
        return {"id": did}

    def create_draft(self, *a, **k):
        raise AssertionError("a links-only run must never create a draft")

    def delete_draft(self, *a, **k):
        raise AssertionError("a links-only run must never delete a draft")

    def send_pipeline_alert(self, to, subject, body):
        self.alerts.append((subject, body))


class _NoModel(types.ModuleType):
    def __getattr__(self, name):
        raise AssertionError(f"model touched: anthropic_client.{name} (a links-only run makes no model call)")


class _NoDrive(types.ModuleType):
    def __getattr__(self, name):
        raise AssertionError(f"Drive touched: drive_client.{name} (a links-only run never reaches Step 3)")


def _pf(gmail):
    for name, mod in (
        ("gmail_client", gmail),
        ("drive_client", _NoDrive("drive_client")),
        ("anthropic_client", _NoModel("anthropic_client")),
        ("calendar_client", types.ModuleType("calendar_client")),
        ("db", types.ModuleType("db")),
        ("workspace", types.ModuleType("workspace")),
    ):
        _fakes.put(name, mod)
    _fakes.pop("rockstar.jobs.publish_finalize", None)
    from rockstar.jobs import publish_finalize as pf

    return pf


SWAPS = []


def _fake_swap(outcome="swapped", stop=None):
    def swap(**kw):
        SWAPS.append(kw)
        if stop:
            raise blog_publish.BlogStop(stop)
        return blog_publish.SwapResult(
            outcome,
            "https://rockstarblends.com/blog/e33",
            2 if outcome == "swapped" else 0,
            True,
            "" if outcome == "swapped" else "a reason",
        )

    return swap


def _setup(
    *,
    pending=True,
    done=True,
    ty="untouched",
    email_apple=EP_APPLE,
    email_spotify=EP_SPOTIFY + "?si=abc",
    subject=SUBJECT,
    headers_case=None,
    swap=None,
    others=None,
    title_in_email="Same Alarm, Different House",  # E33's real shape: 3 of 8 words
    guest_email="guest@example.test",
    flag_pending=None,
):
    """E33 after Phase 2 on the show pages, our thank-you draft, and the publisher email arriving."""
    case = headers_case or AUTH["pass"][1]
    headers = _hdrs(case) + [{"name": "Subject", "value": subject}]
    html = (
        f'<p>{title_in_email}</p> <a href="{email_apple}">Apple</a> <a href="{email_spotify}">Spotify</a>'
        if title_in_email
        else f'<a href="{email_apple}">Apple</a> <a href="{email_spotify}">Spotify</a>'
    )
    g = FakeGmail(headers, html)
    pf = _pf(g)
    from rockstar.content import thank_you

    t = thank_you.build_draft(
        guest_name="Rowan Example",
        episode_title="Different House, Same Alarm | Clearing Trauma's Charge with Rowan Example",
        apple_url=SHOW_APPLE,
        spotify_url=SHOW_SPOTIFY,
        blog_url="https://rockstarblends.com/blog/e33",
        guest_email=guest_email,
    )
    ep = {
        "episode_number": 33,
        "episode_code": "E33",
        "drive_folder_id": "F33",
        "status": "done",
        "pipeline_done": done,
        "episode_links_pending": pending if flag_pending is None else flag_pending,
        "guest_name": "Rowan Example",
        "guest_email": guest_email,
        "episode_title": "Different House, Same Alarm | Clearing Trauma's Charge with Rowan Example",
        "blog_post_url": "https://rockstarblends.com/blog/e33",
        "apple_url": SHOW_APPLE,
        "spotify_url": SHOW_SPOTIFY,
        "notes": "Phase 2 complete 2026-10-06 (episode links pending)",
    }
    if not pending:
        ep["apple_url"], ep["spotify_url"] = EP_APPLE, EP_SPOTIFY
    if ty != "none":
        did = g.add_draft(t["to"], t["subject"], t["body"])
        ep["ty_draft"] = {
            "draft_id": did,
            "message_id": g.drafts[did]["message_id"],
            "sha256": pf._ty_fingerprint(g.draft_state(did)),
            "single_text_plain": True,
        }
        d = g.drafts[did]
        if ty == "edited":
            d["body"] += "\nP.S. Loved talking with you!"
        elif ty == "resaved":
            d["message_id"] = "m-resaved"
        elif ty == "attachment":
            d["single_text_plain"] = False
        elif ty == "gone":
            del g.drafts[did]
    episodes = [ep] + (
        others
        or [
            {
                "episode_number": 31,
                "episode_code": "E31",
                "drive_folder_id": "F31",
                "episode_title": "Bliss Is the System | The ECS",
                "pipeline_done": True,
                "apple_url": SHOW_APPLE.replace("genx", "x") + "?i=1000000000031",
                "spotify_url": "https://open.spotify.com/episode/0SynthEp31AbCdEfGhJkLm",
            }
        ]
    )
    (STATE / "publication_schedule.json").write_text(json.dumps({"episodes": episodes}))
    SWAPS.clear()
    blog_publish.swap_listen_links = swap or _fake_swap()
    blog_publish.load_credentials = lambda *a, **k: ("e", "p")
    return pf, g


def _run(pf):
    pf._run_pipeline(142, {"trigger": "gmail", "message_id": "msg-e33"})


def _ep33():
    return next(
        e
        for e in json.loads((STATE / "publication_schedule.json").read_text())["episodes"]
        if e["episode_number"] == 33
    )


def test_images_first_then_email_swaps_links_only():
    pf, g = _setup()
    _run(pf)  # the fake model and the fake Drive fail the test if Phase 2 runs
    assert len(SWAPS) == 1 and SWAPS[0]["mapping"] == {
        pf._SHOW_APPLE_URL: EP_APPLE,
        pf._SHOW_SPOTIFY_URL: EP_SPOTIFY,
    }, "the blog swap maps show → episode, with Spotify's ?si= removed"
    assert len(g.drafts) == 1 and g.updates == ["d1"], "exactly one draft, updated in place (same id)"
    d = g.drafts["d1"]
    # the episode link STARTS with the show link, so look for the bare show link (end of line) instead
    assert EP_APPLE in d["body"] and EP_SPOTIFY in d["body"], d["body"]
    assert f"{SHOW_APPLE}\n" not in d["body"] and SHOW_SPOTIFY not in d["body"], d["body"]
    ep = _ep33()
    assert ep["apple_url"] == EP_APPLE and ep["spotify_url"] == EP_SPOTIFY
    assert ep["episode_links_pending"] is False and ep["mission_matters_email_id"] == "msg-e33"
    assert ep["ty_draft"]["message_id"] == d["message_id"], "the record follows the in-place update"
    assert len(g.alerts) == 1 and "real episode links in" in g.alerts[0][0], g.alerts


def test_links_already_real_is_a_noop():
    pf, g = _setup(pending=False)
    before = (STATE / "publication_schedule.json").read_text()
    _run(pf)
    assert not SWAPS and not g.updates and not g.alerts
    assert (STATE / "publication_schedule.json").read_text() == before, "nothing written"


def test_draft_left_alone_when_edited_resaved_attached_gone_or_unrecorded():
    for ty in ("edited", "resaved", "attachment", "gone", "none"):
        pf, g = _setup(ty=ty)
        snapshot = copy.deepcopy(g.drafts)
        _run(pf)
        assert not g.updates and g.drafts == snapshot, f"{ty}: the draft must be left exactly as it was"
        assert len(SWAPS) == 1, f"{ty}: the blog swap still runs"
        ep = _ep33()
        assert ep["episode_links_pending"] is True, f"{ty}: a manual step is left, the record says so"
        assert ep["apple_url"] == EP_APPLE, f"{ty}: the real links are recorded"
        assert "Thank-you" in g.alerts[0][1] and ("by hand" in g.alerts[0][1] or "gone" in g.alerts[0][1]), (
            f"{ty}: {g.alerts}"
        )


def test_e32_shape_post_gone_alerts_and_composes_nothing():
    pf, g = _setup(ty="none", swap=_fake_swap(stop="E33: our recorded post /blog/x is gone"))
    _run(pf)
    assert not g.updates
    body = g.alerts[0][1]
    assert "Blog: not changed" in body and "gone" in body, body
    assert _ep33()["episode_links_pending"] is True


def test_links_refused_show_pages_other_show_or_taken_ids():
    for label, kw in (
        ("show pages", dict(email_apple=SHOW_APPLE, email_spotify="https://open.spotify.com/episode/short")),
        ("other show", dict(email_apple=EP_APPLE.replace("id1785708777", "id1111111111"))),
    ):
        pf, g = _setup(**kw)
        before = (STATE / "publication_schedule.json").read_text()
        _run(pf)
        assert not SWAPS and not g.updates, label
        assert (STATE / "publication_schedule.json").read_text() == before, f"{label}: nothing written"
        assert "links refused" in g.alerts[0][0], (label, g.alerts)
    taken = [
        {
            "episode_number": 31,
            "episode_code": "E31",
            "drive_folder_id": "F31",
            "episode_title": "x",
            "apple_url": EP_APPLE,
            "spotify_url": "",
        }
    ]
    pf, g = _setup(others=taken)
    _run(pf)
    assert not SWAPS and "already belongs to E31" in g.alerts[0][1], g.alerts


def test_another_episode_matching_as_well_stops():
    rival = [
        {
            "episode_number": 34,
            "episode_code": "E34",
            "drive_folder_id": "F34",
            "pipeline_done": False,
            "episode_title": "Same Alarm, Different House, Again | Part Two",
            "apple_url": "",
            "spotify_url": "",
        }
    ]
    pf, g = _setup(others=rival)
    _run(pf)
    assert not SWAPS and not g.updates and "names two episodes" in g.alerts[0][0], g.alerts
    assert "E34 (alarm, different, house)" in g.alerts[0][1], g.alerts[0][1]


def test_title_word_rule_on_the_real_shapes():
    pf, g = _setup()
    e33 = {"episode_title": "Different House, Same Alarm | Clearing Trauma's Charge with Rowan Example"}
    e32 = {"episode_title": "The Apothecary in Your Yard | Food, Trust & Going Rogue with Linnea Ashgrovenly"}
    show = "Your Episode is Live | EP 33 GenX Hits Menopause"
    assert pf._title_score(e33, show, "Same Alarm, Different House") == (3, 3), "E33's real email: 3 of 8 → enough"
    assert pf._title_score(e32, show, "The Apothecary in Your Yard with Linnea Ashgrovenly")[0] >= 3, "E32's real email"
    assert pf._title_score(e33, show, "Different House")[0] == 2, "two words are not enough"
    meno = {"episode_title": "Menopause Episode Podcast | GenX Listen"}
    assert pf._title_score(meno, show, "")[0] == 0, "the show's own name and email words never count"
    short = {"episode_title": "Bliss | ECS"}
    assert pf._title_score(short, show, "bliss") == (1, 1), "a title with one distinctive word needs that word"


def test_two_title_words_stop():
    pf, g = _setup(title_in_email="Different House")
    _run(pf)
    assert not SWAPS and "doesn't name the episode" in g.alerts[0][0], g.alerts
    assert "found: different, house" in g.alerts[0][1], "the alert names the words it found"


def test_sender_forged_in_the_job_is_refused_before_anything():
    pf, g = _setup(headers_case=AUTH["refuse"][0])
    try:
        _run(pf)
    except pf.PermanentFailure as exc:
        assert "not authenticated" in str(exc), exc
    else:
        raise AssertionError("a forged sender must stop the job")
    assert not SWAPS and not g.updates and not g.alerts


def test_retry_after_partial_run_ends_in_the_same_state():
    pf, g = _setup()
    g.fail_next_update = True
    try:
        _run(pf)
    except ConnectionError:
        pass
    else:
        raise AssertionError("a transient Gmail error must reach the worker's retry")
    assert _ep33()["episode_links_pending"] is True, "the schedule is written last: still pending after a failure"
    blog_publish.swap_listen_links = _fake_swap("nothing_to_swap")  # the first attempt already swapped the blog
    _run(pf)
    assert g.updates == ["d1"] and len(g.drafts) == 1
    assert _ep33()["episode_links_pending"] is False and EP_APPLE in g.drafts["d1"]["body"]


def test_visuals_trigger_on_a_finished_episode_is_not_rerouted():
    pf, g = _setup()
    try:
        pf._run_pipeline(139, {"trigger": "visuals", "episode_number": 33})
    except AssertionError as exc:
        assert "Drive touched" in str(exc), exc  # it went on to Step 3, as a visuals run always has
    else:
        raise AssertionError("a visuals run must not take the links-only route")
    assert not SWAPS and not g.updates and not g.alerts


def test_ep_number_without_the_title_stops():
    pf, g = _setup(title_in_email="")
    _run(pf)
    assert not SWAPS and not g.updates and "doesn't name the episode" in g.alerts[0][0], g.alerts
    assert _ep33()["apple_url"] == SHOW_APPLE, "nothing written"


def test_show_links_on_record_route_to_links_only_even_if_the_flag_is_missing():
    pf, g = _setup(flag_pending=False)  # flag says done, but the stored links are still the show pages
    _run(pf)
    assert len(SWAPS) == 1 and g.updates == ["d1"]


def test_retry_after_an_update_that_succeeded_is_not_called_a_hand_edit():
    pf, g = _setup()
    real_state = g.draft_state
    calls = {"n": 0}

    def flaky_state(did):
        calls["n"] += 1
        if calls["n"] == 2:  # the read-back right after the update
            raise ConnectionError("simulated Gmail 503 on the read-back")
        return real_state(did)

    g.draft_state = flaky_state
    try:
        _run(pf)
    except ConnectionError:
        pass
    else:
        raise AssertionError("the read-back failure must reach the worker's retry")
    g.draft_state = real_state
    g.alerts.clear()
    blog_publish.swap_listen_links = _fake_swap("nothing_to_swap")
    _run(pf)
    assert g.updates == ["d1"], "updated once, not again"
    assert "already carries the episode links" in g.alerts[0][1] and "by hand" not in g.alerts[0][1], g.alerts
    assert _ep33()["episode_links_pending"] is False


def test_recipient_change_is_named():
    pf, g = _setup(guest_email="")  # created to Heather with the flagged subject...
    did = "d1"
    ep = json.loads((STATE / "publication_schedule.json").read_text())
    ep["episodes"][0]["guest_email"] = "guest@example.test"  # ...the guest's email added later
    (STATE / "publication_schedule.json").write_text(json.dumps(ep))
    _run(pf)
    assert g.drafts[did]["to"] == "guest@example.test"
    assert "recipient changed" in g.alerts[0][1], g.alerts[0][1]


def test_email_first_then_images_waits_without_links_only():
    pf, g = _setup(done=False, pending=True)
    drive = types.ModuleType("drive_client")
    drive.list_folder = lambda fid: []  # no tile, no reel yet
    pf.drive_client = drive
    _run(pf)
    ep = _ep33()
    assert ep["status"] == "waiting_for_visuals" and ep["pending_mm_context"]["apple_url"] == EP_APPLE
    assert not SWAPS and not g.updates, "an unfinished episode never takes the links-only route"


def test_sender_parser_refuses_ambiguous_headers():
    F = "P <x@legacy-publisher.example.test>"
    probes = {
        "quoted local part": 'mx.google.com; spf=pass (google.com: domain of "a)b;dkim=pass header.d=legacy-publisher.example.test x"'
        '@attacker.example designates 1.2.3.4) smtp.mailfrom="a)b;dkim=pass header.d=legacy-publisher.example.test x"'
        "@attacker.example; dmarc=fail header.from=legacy-publisher.example.test",
        "two dmarc clauses": "mx.google.com; dmarc=fail header.from=legacy-publisher.example.test; dmarc=pass header.from=legacy-publisher.example.test",
        "extra close paren": "mx.google.com; spf=pass (x) y) dkim=pass header.d=legacy-publisher.example.test",
        "dkim conflict": "mx.google.com; dkim=fail header.d=legacy-publisher.example.test; dkim=pass header.d=legacy-publisher.example.test",
        "dmarc for another domain": "mx.google.com; dkim=pass header.d=legacy-publisher.example.test; dmarc=pass header.from=attacker.example",
        # Only the guards catch these two: no other rule would refuse them.
        "quote smuggles a clause, no dmarc": 'mx.google.com; spf=pass (google.com: domain of "x); dkim=pass '
        'header.d=legacy-publisher.example.test; (y"@attacker.example designates 1.2.3.4)',
        "pass written before fail": "mx.google.com; dmarc=pass header.from=legacy-publisher.example.test; dmarc=fail header.from=legacy-publisher.example.test",
        # Only the parenthesis guards catch this one (re-review, finding 2).
        "unbalanced, trailing open paren": "mx.google.com; spf=pass (x) ; dkim=pass header.d=legacy-publisher.example.test; (y",
        "close paren first": "mx.google.com; spf=pass x) ; dkim=pass header.d=legacy-publisher.example.test; (y",
        # Repeated property: the last value would win (re-review, finding 3).
        "header.from given twice": "mx.google.com; dmarc=pass header.from=attacker.example header.from=legacy-publisher.example.test",
    }
    for label, ar in probes.items():
        ok, why = pe.authenticated_sender(_hdrs({"from": F, "authentication_results": [ar]}))
        assert not ok, f"{label} passed: {why}"


def test_gmail_wrappers_against_a_fake_api():
    """gmail_client.draft_state / update_draft, real code, with only the Gmail API object faked."""
    import base64
    import email.message
    import importlib

    _fakes.pop("rockstar.clients.gmail_client", None)
    gc = importlib.import_module("rockstar.clients.gmail_client")

    class HttpError(Exception):
        def __init__(self, status):
            self.resp = types.SimpleNamespace(status=status)

    store = {
        "d1": {
            "id": "d1",
            "message": {
                "id": "m1",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [{"name": "To", "value": "guest@example.test"}, {"name": "Subject", "value": "Thanks"}],
                },
            },
        }
    }
    raw_msg = email.message.EmailMessage()
    raw_msg["To"], raw_msg["Subject"] = "guest@example.test", "Thanks"
    raw_msg.set_content("Hello = there")
    raw = base64.urlsafe_b64encode(raw_msg.as_bytes()).decode()
    sent = {}

    class Call:
        def __init__(self, fn):
            self.fn = fn

        def execute(self):
            return self.fn()

    class Drafts:
        def get(self, userId, id, format):
            def go():
                if id not in store:
                    raise HttpError(404)
                if format == "raw":
                    return {"message": {"raw": raw}}
                return store[id]

            return Call(go)

        def update(self, userId, id, body):
            sent.update(id=id, body=body)
            return Call(lambda: {"id": id})

    svc = types.SimpleNamespace(users=lambda: types.SimpleNamespace(drafts=lambda: Drafts()))
    gc._svc = lambda: svc
    try:
        s = gc.draft_state("d1")
        assert s["message_id"] == "m1" and s["single_text_plain"] and s["to"] == "guest@example.test"
        assert s["body"].strip() == "Hello = there", repr(s["body"])
        store["d1"]["message"]["payload"]["parts"] = [{"mimeType": "text/plain"}, {"mimeType": "image/png"}]
        assert gc.draft_state("d1")["single_text_plain"] is False, "an attachment makes it multipart"
        try:
            gc.draft_state("gone")
        except gc.DraftGone:
            pass
        else:
            raise AssertionError("a 404 must be DraftGone")
        gc.update_draft("d1", "guest@example.test", "Thanks", "New body")
        assert sent["id"] == "d1" and sent["body"]["id"] == "d1", sent
        decoded = base64.urlsafe_b64decode(sent["body"]["message"]["raw"]).decode()
        assert "To: guest@example.test" in decoded and "New body" in decoded
    finally:
        _fakes.pop("rockstar.clients.gmail_client", None)


# ── Step 13: the draft is recorded as Gmail stored it ─────────────────────────


def test_record_ty_draft_reads_back_and_never_fails_the_job():
    pf, g = _setup(ty="none")
    did = g.add_draft("guest@example.test", "Thanks", "Body text")
    pf._record_ty_draft(33, {"id": did})
    rec = _ep33()["ty_draft"]
    assert rec["draft_id"] == did and rec["message_id"] == g.drafts[did]["message_id"]
    assert rec["sha256"] == pf._ty_fingerprint(g.draft_state(did))
    del g.drafts[did]
    pf._record_ty_draft(33, {"id": did})  # draft_state raises → logged, not raised


# ── AC-10 in the webhook: refused, alerted once, never enqueued ───────────────


def test_webhook_refuses_an_unauthenticated_publish_email():
    import test_pub01_idempotent_enqueue as p1

    db, path = p1._fresh_db("trig03.db")
    db.init_db()
    marked = []
    webhook_service, gc, saved, msg_id = p1._load_webhook_with_stubs(lambda mid: marked.append(mid))
    alerts = []
    gc.send_pipeline_alert = lambda to, subject, body: alerts.append(subject)
    sys.modules["rockstar.clients.publisher_email"].authenticated_sender = lambda headers: (
        False,
        "dmarc=fail for legacy-publisher.example.test",
    )
    try:
        webhook_service.db = db
        webhook_service._assert_pubsub_push = lambda authorization, env: None
        body = b'{"message": {"data": "x"}, "subscription": "projects/p/subscriptions/s"}'
        resp = asyncio.run(webhook_service.gmail_webhook(p1._FakeRequest(body)))
        assert resp.status_code == 200, "always ack"
        assert p1._rows(path) == [], "nothing enqueued"
        assert len(alerts) == 1 and "not authenticated" in alerts[0], alerts
        assert marked == [msg_id], "marked read so it cannot re-alert on every push"
    finally:
        p1._restore_modules(saved)


def test_webhook_refused_email_alert_fails_goes_back_to_unread():
    import test_pub01_idempotent_enqueue as p1

    db, path = p1._fresh_db("trig03-unread.db")
    db.init_db()
    marked, unmarked = [], []
    webhook_service, gc, saved, msg_id = p1._load_webhook_with_stubs(lambda mid: marked.append(mid))

    def alert_down(**kw):
        raise ConnectionError("simulated Gmail 503 on send")

    gc.send_pipeline_alert = alert_down
    gc.mark_unread = lambda mid: unmarked.append(mid)
    sys.modules["rockstar.clients.publisher_email"].authenticated_sender = lambda headers: (False, "dmarc=fail")
    try:
        webhook_service.db = db
        webhook_service._assert_pubsub_push = lambda authorization, env: None
        body = b'{"message": {"data": "x"}, "subscription": "projects/p/subscriptions/s"}'
        resp = asyncio.run(webhook_service.gmail_webhook(p1._FakeRequest(body)))
        assert resp.status_code == 200 and p1._rows(path) == []
        assert marked == [msg_id] and unmarked == [msg_id], "un-alerted → back to unread, so the next push alerts"
    finally:
        p1._restore_modules(saved)


def main() -> int:
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    real_swap, real_creds = blog_publish.swap_listen_links, blog_publish.load_credentials
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
        finally:
            blog_publish.swap_listen_links, blog_publish.load_credentials = real_swap, real_creds
    b2._cleanup()
    shutil.rmtree(STATE, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
