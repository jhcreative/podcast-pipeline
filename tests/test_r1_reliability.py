"""
test_r1_reliability.py — Round 1 made reliable (2026-10-01): R1-02, R1-03, R1-04, BIO-01, CAL-OFF.

Run: python3 -B test_r1_reliability.py   (plain asserts, no pytest needed)

Every case below is a failure E32's Round 1 actually produced on 2026-10-01, or the guard that stops it:
  R1-02 leaked model notes above the title; parts run together  → test_normalize_*
  R1-03 summary read stale quote keys (5 empty lines); an unclosed ```json fence dropped Dr. Joe's review
        silently; a skipped reviewer was invisible               → test_fence_*, test_brand_team_*, test_summary_quotes_*
  R1-04 host name misheard ("Heather Riley Helmsdrum")          → test_host_*
  BIO-01 guest name/bio/email from the form, matched in the intro, never logged → test_form_*
  CAL-OFF calendar event off by default                         → test_calendar_*
  DEPLOY every local module Round 1 / Phase 2 import is in deploy.sh FILES → test_deploy_files_*

The fixture tests/fixtures/r1rel/E32_apple_summary_generated.md is SYNTHETIC (SHARE-02): E32's Round-1 output rewritten with an invented guest, line for line — the same preamble, misheard host name, title and part counts.
Model calls are faked at the SDK boundary; the code under test is the real code.
"""

import ast
import io
import json
import logging
import os
import sys
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
FIX = REPO / "tests" / "fixtures" / "r1rel"


def _install_stubs():
    if "anthropic" not in sys.modules:
        a = types.ModuleType("anthropic")

        class Anthropic:  # noqa: N801
            def __init__(self, *a, **k):
                pass

        a.Anthropic = Anthropic
        _swap.put("anthropic", a)


_install_stubs()
from rockstar.clients import anthropic_client  # noqa: E402
from rockstar.content import (
    blog_post,  # noqa: E402
    guest_form,  # noqa: E402
)
from rockstar.gates import summary_norm  # noqa: E402
from rockstar.gates.content_gates import GateFailure  # noqa: E402


# ── fake Anthropic client ─────────────────────────────────────────────────────
class _Resp:
    def __init__(self, text, stop="end_turn"):
        self.content = [types.SimpleNamespace(text=text)]
        self.stop_reason = stop


class _FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        return r if isinstance(r, _Resp) else _Resp(r)


def _with_client(fake):
    anthropic_client._get_client = lambda: fake
    anthropic_client._load_prompt = lambda name: f"system prompt for {name}"


QUOTES = {
    "quotes": [
        {
            "id": "q01",
            "speaker": "Linnea Ashgrovenly",
            "text": "Those weeds are an apothecary.",
            "heading": "The Apothecary in Your Yard",
        }
    ]
}


# ── R1-03: fences ─────────────────────────────────────────────────────────────
def test_fence_closed_and_unclosed():
    body = json.dumps(QUOTES)
    assert json.loads(anthropic_client._strip_json_fences(f"```json\n{body}\n```")) == QUOTES
    assert json.loads(anthropic_client._strip_json_fences(f"Here you go:\n```json\n{body}\n```")) == QUOTES
    # E32 Dr. Joe shape: an opening fence and no closing one
    assert json.loads(anthropic_client._strip_json_fences(f"```json\n{body}")) == QUOTES
    assert json.loads(anthropic_client._strip_json_fences(body)) == QUOTES


def test_fence_truncated_still_fails_honestly():
    try:
        json.loads(anthropic_client._strip_json_fences('```json\n{"quotes": [{"id": "q01", "te'))
    except json.JSONDecodeError:
        return
    raise AssertionError("a truncated reply must not parse")


# ── R1-03: brand team ─────────────────────────────────────────────────────────
def _members():
    return list(anthropic_client._BRAND_TEAM_MEMBERS)


def test_brand_team_retry_recovers():
    good = json.dumps(QUOTES)
    replies = [good, good, "```json\n{oops", f"```json\n{good}\n```", good]  # step 3 bad once, then good
    fake = _FakeClient(replies)
    _with_client(fake)
    report = {}
    quotes = anthropic_client.run_brand_team_chain("transcript", {}, report=report)
    assert quotes == QUOTES["quotes"]
    assert report["skipped"] == [], report
    assert report["retried"] == [_members()[2]], report
    assert all(c["max_tokens"] >= 8192 for c in fake.calls), "Brand Team budget must cover a 5-quote review"


def test_brand_team_skip_is_reported_not_silent():
    good = json.dumps(QUOTES)
    replies = [good, good, "not json", "still not json", good]
    fake = _FakeClient(replies)
    _with_client(fake)
    report = {}
    quotes = anthropic_client.run_brand_team_chain("transcript", {}, report=report)
    assert quotes == QUOTES["quotes"]
    assert report["skipped"] == [_members()[2]], report


def test_brand_team_object_without_quotes_is_not_accepted():
    good = json.dumps(QUOTES)
    replies = [good, good, '{"note": "looks fine"}', '{"note": "looks fine"}', good]
    fake = _FakeClient(replies)
    _with_client(fake)
    report = {}
    anthropic_client.run_brand_team_chain("transcript", {}, report=report)
    assert report["skipped"] == [_members()[2]], "a reply with no quotes array must not replace the quotes"


# ── R1-03: summary gets the real quotes ───────────────────────────────────────
def test_summary_quotes_are_sent_with_current_schema():
    fake = _FakeClient(["# Gen X Hits Menopause · S2E7 · X | Y\n\nbody"])
    _with_client(fake)
    anthropic_client.compose_apple_summary("transcript", {"episode_code": "E32"}, quotes=QUOTES["quotes"])
    sent = fake.calls[0]["messages"][0]["content"][1]["text"]
    assert "Those weeds are an apothecary." in sent, sent
    assert "The Apothecary in Your Yard" in sent, sent
    assert '""' not in sent, "an empty quote line was sent"


def test_summary_refuses_empty_quote():
    fake = _FakeClient(["unused"])
    _with_client(fake)
    try:
        anthropic_client.compose_apple_summary("t", {}, quotes=[{"id": "q01", "heading": "H"}])
    except ValueError:
        assert not fake.calls, "no model call may be spent on an empty quote set"
        return
    raise AssertionError("an empty quote must be refused")


# ── R1-02 / R1-04: normalize ──────────────────────────────────────────────────
def test_normalize_real_e32_output():
    raw = (FIX / "E32_apple_summary_generated.md").read_text()
    assert raw.startswith("Looking at the transcript")
    clean, rep = summary_norm.normalize_apple_summary(raw, "E32")
    assert clean.startswith("# Gen X Hits Menopause"), clean[:80]
    for leak in ("Looking at the transcript", "No Brand Team quotes were supplied", "Helmsdrum"):
        assert leak not in clean, leak
    assert "Heather Reilly Hiemstra" in clean
    assert rep == {"dropped_preamble_lines": 3, "dropped_rules": 0, "host_fixes": 1}, rep
    s = blog_post.parse_apple_summary(clean)
    assert (s["guest"], len(s["hook"]), len(s["bullets"]), len(s["tail"]), len(s["bio"])) == (
        "Linnea Ashgrovenly",
        2,
        5,
        1,
        3,
    )


def test_normalize_is_idempotent():
    raw = (FIX / "E32_apple_summary_generated.md").read_text()
    once, _ = summary_norm.normalize_apple_summary(raw)
    twice, rep = summary_norm.normalize_apple_summary(once)
    assert once == twice and rep == {"dropped_preamble_lines": 0, "dropped_rules": 0, "host_fixes": 0}


def test_normalize_refuses_without_title():
    try:
        summary_norm.normalize_apple_summary("Some notes.\n\nConversations include\n\n- **A** b\n")
    except GateFailure as e:
        assert "title" in str(e)
        return
    raise AssertionError("no title line must be refused")


def test_normalize_refuses_unparseable():
    try:
        summary_norm.normalize_apple_summary("# Gen X Hits Menopause · S1E1 · A | B\n\nJust one paragraph.\n")
    except GateFailure:
        return
    raise AssertionError("a summary the blog parser rejects must fail in Round 1")


def test_host_variants():
    f = summary_norm.canonicalize_host
    assert (
        f("I'm your host, Heather Riley, Heems Drum, the founder")
        == "I'm your host, Heather Reilly Hiemstra, the founder"
    )
    assert f("Heather Riley Helmsdrum sits down") == "Heather Reilly Hiemstra sits down"
    assert f("Heather Riley") == "Heather Reilly Hiemstra"
    assert f("Heather Riley Hosts") == "Heather Reilly Hiemstra Hosts"
    assert f("Heather Lockwoodby") == "Heather Lockwoodby"
    assert summary_norm.canonicalize_speaker("Heather Riley") == "Heather Reilly Hiemstra"
    assert summary_norm.canonicalize_speaker("Linnea Ashgrovenly") == "Linnea Ashgrovenly"


# ── BIO-01: the form ──────────────────────────────────────────────────────────
_HDR = (
    "Timestamp,Email Address, Your name ,Profile image or business logo,Bio/Introduction ,"
    "Your primary website,Episode topic or title suggestion\n"
)
SENTINEL_BIO = "SENTINEL-BIO-do-not-log-7f3a"


def _csv(*rows):
    out = io.StringIO()
    out.write(_HDR)
    import csv as _c

    w = _c.writer(out)
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def test_form_headers_by_name_not_position():
    rows = guest_form.parse_csv(
        _csv(["2026/06/01", "ah@example.test", " Linnea Ashgrovenly ", "", SENTINEL_BIO, "", ""])
    )
    assert rows == [
        {
            "name": "Linnea Ashgrovenly",
            "bio": SENTINEL_BIO,
            "email": "ah@example.test",
            "timestamp": "2026/06/01",
            "website": "",
        }
    ], rows


def test_form_missing_bio_column_is_an_error():
    try:
        guest_form.parse_csv("Timestamp, Your name \n2026/06/01,Linnea Ashgrovenly\n")
    except RuntimeError as e:
        assert "bio" in str(e).lower()
        return
    raise AssertionError("a changed form must fail loudly, not return empty bios")


def test_form_match_one_none_several_newest():
    rows = guest_form.parse_csv(
        _csv(
            ["2026/01/01", "old@example.test", "Linnea Ashgrovenly", "", "old bio", "", ""],
            ["2026/06/01", "new@example.test", "Linnea Ashgrovenly", "", "new bio", "", ""],
            ["2026/02/01", "x@example.test", "Dr. Jane Roe", "", "jr bio", "", ""],
            ["2026/02/01", "y@example.test", "Cher", "", "single", "", ""],
        )
    )
    rec, rule = guest_form.match_guest(rows, "So to our guest, Linnea Ashgrovenly, is this woman such a badass")
    assert rec and rec["bio"] == "new bio" and rec["email"] == "new@example.test", (rec, rule)
    rec, rule = guest_form.match_guest(rows, "Today we have Jane Roe with us")  # honorific dropped
    assert rec and rec["name"] == "Dr. Jane Roe", (rec, rule)
    rec, rule = guest_form.match_guest(rows, "Today we have someone new")
    assert rec is None and "no form answer" in rule
    rec, rule = guest_form.match_guest(rows, "Linnea Ashgrovenly and Jane Roe are both here")
    assert rec is None and "2 form answers" in rule
    rec, rule = guest_form.match_guest(rows, "and Cher sang")  # one-word names are never matched
    assert rec is None


def test_form_first_name_alone_matches_only_the_one_person_with_it():
    """E34, 2026-10-13: the host said the guest's first name four times in the intro and never her surname; her form
    line was there and the surname rule missed it. A first name heard (twice or more) that only one form answer has
    is that person. Two people sharing it, the host's own first name, or a name that's an everyday word: no match."""
    rows = guest_form.parse_csv(
        _csv(
            ["2026/07/01", "zq@example.test", "Zephyrine Quillfeather", "", "zq bio", "", ""],
            ["2026/07/01", "lb@example.test", "Linnea Brightwater", "", "lb bio", "", ""],
            ["2026/07/02", "la@example.test", "Linnea Ashgrovenly", "", "la bio", "", ""],
            ["2026/07/03", "hx@example.test", "Heather Exampleton", "", "hx bio", "", ""],
            ["2026/07/04", "ap@example.test", "May Invented", "", "ap bio", "", ""],
        )
    )
    rec, rule = guest_form.match_guest(rows, "Zephyrine is a board certified coach. So Zephyrine tell me how it began")
    assert rec and rec["name"] == "Zephyrine Quillfeather" and "first name" in rule, (rec, rule)
    rec, rule = guest_form.match_guest(rows, "Zephyrine is here today")  # heard once: no
    assert rec is None, (rec, rule)
    rec, rule = guest_form.match_guest(rows, "Linnea joins us. Linnea, welcome")  # two Linneas: no
    assert rec is None, (rec, rule)
    rec, rule = guest_form.match_guest(rows, "I'm Heather and Heather is your host")  # the host: no
    assert rec is None, (rec, rule)
    rec, rule = guest_form.match_guest(rows, "in May we went, and May again")  # an everyday word: no
    assert rec is None, (rec, rule)


def test_form_first_name_never_picks_an_answer_another_episode_used():
    """Toby's condition 2: the form holds every past guest. "My husband Mike", said twice, mustn't pull in a past Mike."""
    rows = guest_form.parse_csv(
        _csv(
            ["2026/01/01", "mike@example.test", "Mikkel Pastguest", "", "past bio", "", ""],
            ["2026/07/01", "zq@example.test", "Zephyrine Quillfeather", "", "zq bio", "", ""],
        )
    )
    used = guest_form.used_by_other_episodes(
        {
            "episodes": [
                {"episode_code": "E10", "guest_name": "Mikkel Pastguest", "guest_email": "mike@example.test"},
                {"episode_code": "E99", "guest_name": "Zephyrine Quillfeather"},
            ]
        },
        "E99",
    )  # E99's own entry doesn't count
    rec, rule = guest_form.match_guest(rows, "my husband Mikkel came too, Mikkel says hi", used)
    assert rec is None, (rec, rule)
    rec, rule = guest_form.match_guest(rows, "Zephyrine is here. Zephyrine, welcome", used)
    assert rec and rec["name"] == "Zephyrine Quillfeather", (rec, rule)
    rec, rule = guest_form.match_guest(rows, "my husband Mikkel came too, Mikkel says hi")  # nothing used: matches
    assert rec and rec["name"] == "Mikkel Pastguest", (rec, rule)


def test_form_misheard_surname_matches_with_form_spelling():
    rows = guest_form.parse_csv(
        _csv(
            ["2/12/2025", "tk@example.test", "Tamsin Kestrelmore MD", "", "bio", "", ""],
            ["2/12/2025", "ts@example.test", "Tamsin Jones", "", "bio2", "", ""],
        )
    )
    rec, rule = guest_form.match_guest(rows, "we are so excited to have dr tamsin cestrel more md with us")
    assert rec and rec["name"] == "Tamsin Kestrelmore MD" and "spelled differently" in rule, (rec, rule)
    rec, rule = guest_form.match_guest(rows, "and Tamsin Smith joined")  # first name only + unlike surname
    assert rec is None, rec


def test_form_similar_surname_never_guesses_between_two():
    rows = guest_form.parse_csv(
        _csv(
            ["1", "a@x.test", "Ann Kestrelmore", "", "b", "", ""], ["2", "b@x.test", "Ann Cestrelmore", "", "b", "", ""]
        )
    )
    rec, rule = guest_form.match_guest(rows, "today ann cestrel more is here")
    assert rec is None and "2 form answers" in rule, (rec, rule)


def test_form_intro_window_only():
    segs = [{"start": 10, "text": "welcome"}, {"start": 400, "text": "Linnea Ashgrovenly said later"}]
    assert "Linnea Ashgrovenly" not in guest_form.intro_text_from_segments(segs)


def test_form_lookup_never_logs_answers():
    rows = guest_form.parse_csv(_csv(["2026/06/01", "ah@example.test", "Linnea Ashgrovenly", "", SENTINEL_BIO, "", ""]))
    guest_form.fetch_rows = lambda: rows
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    logging.getLogger().addHandler(h)
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        rec, _ = guest_form.lookup([{"start": 5, "text": "our guest, Linnea Ashgrovenly"}])
    finally:
        logging.getLogger().removeHandler(h)
    assert rec and rec["bio"] == SENTINEL_BIO
    log = buf.getvalue()
    assert SENTINEL_BIO not in log and "ah@example.test" not in log, log


def test_form_outage_is_a_miss_not_a_crash():
    def boom():
        raise RuntimeError("HTTP 500")

    guest_form.fetch_rows = boom
    rec, rule = guest_form.lookup([{"start": 5, "text": "Linnea Ashgrovenly"}])
    assert rec is None and "unavailable" in rule


# ── CAL-OFF ───────────────────────────────────────────────────────────────────
def _round_1_source():
    return (REPO / "rockstar" / "jobs" / "round_1.py").read_text()


def test_calendar_off_by_default_and_switchable():
    src = _round_1_source()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "calendar_event_enabled")
    from rockstar import config

    ns = {"os": os, "config": config}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "round_1", "exec"), ns)
    old = os.environ.pop("EDITORIAL_CALENDAR_EVENT", None)
    try:
        assert ns["calendar_event_enabled"]() is False
        os.environ["EDITORIAL_CALENDAR_EVENT"] = "on"
        assert ns["calendar_event_enabled"]() is True
    finally:
        os.environ.pop("EDITORIAL_CALENDAR_EVENT", None)
        if old is not None:
            os.environ["EDITORIAL_CALENDAR_EVENT"] = old


def test_calendar_create_only_behind_the_switch():
    src = _round_1_source()
    body = src[src.index("def _run_pipeline") :]  # code only; the module docstring mentions the call too
    gate = body.index("if not calendar_event_enabled():")
    create = body.index("calendar_client.create_editorial_event(")
    assert gate < create, "create_editorial_event must sit after the CAL-OFF gate"
    assert body.count("calendar_client.create_editorial_event(") == 1


# ── R1-05: a re-run archives, never duplicates or deletes ─────────────────────
def _round_1_with_fake_drive(files):
    for name in (
        "rockstar.clients.drive_client",
        "rockstar.clients.calendar_client",
        "rockstar.service.db",
        "rockstar.ops.workspace",
    ):
        _swap.setdefault(name, types.ModuleType(name))
    _swap.pop("rockstar.jobs.round_1", None)
    from rockstar.jobs import round_1 as r1

    calls = {"created": [], "moved": []}
    fake = types.SimpleNamespace(
        list_folder=lambda fid: list(files),
        create_folder=lambda name, parent_folder_id=None: calls["created"].append(name) or {"id": "arch-1"},
        move_file=lambda fid, dest: calls["moved"].append((fid, dest)),
    )
    r1.drive_client = fake
    return r1, calls


def test_rerun_archives_same_named_root_artifacts():
    files = [
        {"id": "a1", "name": "apple_summary.md", "mimeType": "text/markdown"},
        {"id": "a2", "name": "apple_summary", "mimeType": "application/vnd.google-apps.document"},
        {"id": "q1", "name": "quotes.json", "mimeType": "application/json"},
        {"id": "au", "name": "E32-linnea.m4a", "mimeType": "audio/x-m4a"},
        {"id": "f1", "name": "_superseded-2026-07-21", "mimeType": "application/vnd.google-apps.folder"},
    ]
    r1, calls = _round_1_with_fake_drive(files)
    n = r1._archive_existing_artifacts("ep", ["apple_summary.md", "quotes.json", "transcript_fast.txt"])
    assert n == 3, n
    assert len(calls["created"]) == 1 and calls["created"][0].startswith("_superseded-"), calls
    assert sorted(fid for fid, _ in calls["moved"]) == ["a1", "a2", "q1"], calls  # audio and folders untouched


def test_first_run_touches_nothing():
    r1, calls = _round_1_with_fake_drive([{"id": "au", "name": "E40-x.m4a", "mimeType": "audio/x-m4a"}])
    assert r1._archive_existing_artifacts("ep", ["apple_summary.md"]) == 0
    assert calls == {"created": [], "moved": []}


# ── DEPLOY: no module left behind ─────────────────────────────────────────────
def _deploy_files():
    """What a deploy ships, from the real script (since the 2026-10-14 remodel the code list comes from git)."""
    import subprocess

    r = subprocess.run(["bash", "scripts/deploy.sh", "--list"], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return set(r.stdout.split())


def test_deploy_files_cover_round_1_and_phase_2_imports():
    files = _deploy_files()
    missing = []
    for mod in (
        "rockstar/jobs/round_1.py",
        "rockstar/jobs/publish_finalize.py",
        "rockstar/content/blog_publish.py",
        "rockstar/gates/summary_norm.py",
    ):
        for node in ast.walk(ast.parse((REPO / mod).read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                # `from rockstar.clients import drive_client` names a module too, not only `from x.y import f`
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                local = REPO / (n.replace(".", "/") + ".py")
                if local.exists() and str(local.relative_to(REPO)) not in files:
                    missing.append(f"{mod} imports {n}")  # PROMPT-01 managed the last exemptions
    assert not missing, "not in deploy.sh FILES (the worker would crash on import):\n  " + "\n  ".join(missing)


def test_deploy_files_cover_every_tracked_brand_asset():
    """HOST-01: the reel's theme, colours and closing stills lived on the old box only (placed by hand) and the cutover
    left them behind. Every BrandAssets file git tracks ships with the deploy."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "-z", "BrandAssets"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    tracked = [t for t in tracked if t]  # -z: a name with a space stays one name
    assert tracked, "git ls-files found no BrandAssets (run from a checkout)"
    docs = {"BrandAssets/fonts/ibm-plex/README.md"}  # where the fonts came from: for people, not the renderer
    missing = sorted(set(tracked) - set(_deploy_files()) - docs)
    assert not missing, (
        "tracked brand assets not in deploy.sh FILES (reels would render without them):\n  " + "\n  ".join(missing)
    )


def test_the_reel_font_is_the_cards_plex_and_licensed_to_ship():
    """John, 2026-10-12: the reels use the cards' Plex. American Typewriter (a macOS system font) is gone from the repo."""
    import hashlib

    render = (REPO / "reels" / "render_reel.js").read_text()
    assert "path.join('fonts', 'ibm-plex', 'IBMPlexSans-SemiBold.ttf')" in render and "primary.tt" not in render
    ttf = REPO / "BrandAssets" / "fonts" / "ibm-plex" / "IBMPlexSans-SemiBold.ttf"
    digest = hashlib.sha256(ttf.read_bytes()).hexdigest()
    assert digest in (ttf.parent / "README.md").read_text(), "the vendored TTF must match its recorded checksum"
    assert "BrandAssets/fonts/ibm-plex/IBMPlexSans-SemiBold.ttf" in _deploy_files()
    assert not (REPO / "BrandAssets" / "fonts" / "primary.ttc").exists()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    print("R1 reliability (R1-02/03/04, BIO-01, CAL-OFF, deploy) — test run")
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
