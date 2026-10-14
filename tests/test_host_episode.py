"""
test_host_episode.py — an episode with no guest is Heather's episode (John, 2026-10-13).

"if there is no guest, use existing bio info for Heather" / "dont make me write up fake data about it".
E13 (Heather and John) failed Round 1's guest gate twice: no guest form answer, every kept quote the host's.
- No guest named + every quote the host's → host episode: the guest gate doesn't apply; her PUBLISHED bio
  (rockstarblends.com/about, verbatim) seeds the About section. Nothing is invented.
- A real guest who goes unnamed still has quotes of her own → the guest gate applies as before.
"""

import sys
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from rockstar.gates import content_gates  # noqa: E402

HOST = "Heather Reilly Hiemstra"


def test_no_guest_and_only_host_quotes_is_a_host_episode():
    assert content_gates.host_episode([{"speaker": HOST}, {"speaker": HOST}], "")
    assert content_gates.host_episode([{"speaker": HOST}], "Guest")  # the placeholder isn't a name


def test_heather_and_john_with_no_guest_is_a_host_episode():
    """E13: Heather and John, no guest. John's quotes don't make him a guest."""
    assert content_gates.host_episode([{"speaker": HOST}, {"speaker": "John Hiemstra"}, {"speaker": "John"}], "")
    assert not content_gates.host_episode([{"speaker": HOST}, {"speaker": "John Invented-Guest"}], "")


def test_an_unnamed_guest_with_her_own_quotes_is_not():
    assert not content_gates.host_episode([{"speaker": HOST}, {"speaker": "Jane Invented"}], "")


def test_a_named_guest_is_never_a_host_episode():
    assert not content_gates.host_episode([{"speaker": HOST}], "Jane Invented")


def test_no_quotes_is_not_a_host_episode():
    assert not content_gates.host_episode([], "")


def test_the_bio_is_the_published_text_and_ships():
    bio = content_gates.host_bio()
    assert bio.startswith("Heather Reilly Hiemstra is a metalsmith, alchemist, cancer survivor"), bio[:80]
    assert "<!--" not in bio and "rockstarblends.com/about" in (REPO / "BrandAssets" / "host_bio.md").read_text()
    assert '"BrandAssets/host_bio.md"' in (REPO / "scripts" / "deploy.sh").read_text()


def test_round1_checks_for_a_host_episode_before_the_guest_gate():
    src = (REPO / "rockstar" / "jobs" / "round_1.py").read_text()
    he, gate, summary = (
        src.index("content_gates.host_episode(selected"),
        src.index("content_gates.assert_guest_featured(quotes, episode_metadata"),
        src.index('run_ledger.step("11", "apple_summary")'),
    )
    assert he < gate < summary
    block = src[he:gate]
    assert 'episode_metadata["guest_bio"] = content_gates.host_bio()' in block and "_prepend_standup(" in block
    # Toby's condition 1: judged on the brand team's selection BEFORE the drops, so a guest whose quotes were all
    # dropped as too long never turns the episode into Heather's.
    assert "host_episode(selected," in src and "host_episode(quotes," not in src
    # The schedule names the host as the featured person, so Phase 2 doesn't depend on parsing the summary.
    assert "elif host_ep:" in src and "summary_norm.HOST_NAME if host_ep else None" in src
    # The ledger keeps the rule, never a name (an ambiguous reason lists names after its colon).
    assert 'rule=guest_rule.split(":")[0]' in src


def _round_1():
    """jobs.round_1 with its outside services stubbed (no Drive, queue, model or calendar is reached)."""
    import types

    if "anthropic" not in sys.modules:
        a = types.ModuleType("anthropic")
        a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *x, **k: None})
        _swap.put("anthropic", a)
    for name in ("rockstar.clients.drive_client", "rockstar.service.db", "rockstar.clients.calendar_client"):
        _swap.setdefault(name, types.ModuleType(name))
    from rockstar.jobs import round_1 as r1

    return r1


def test_a_rerun_names_the_guest_and_her_email():
    """E34, 2026-10-13: the re-run matched the guest but never wrote her email, so the thank-you drafted to Heather."""
    r1 = _round_1()
    entry = {"guest_name": "Guest name TBD", "guest_email": ""}
    r1._set_featured_person(entry, {"name": "Jane Invented", "email": "jane@example.test"}, False)
    assert entry == {"guest_name": "Jane Invented", "guest_email": "jane@example.test"}, entry


def test_a_host_episode_rerun_names_the_host_and_never_reads_a_missing_guest():
    """E13, job 161, 2026-10-13: the email line sat under the host branch and read guest_rec None: the run crashed
    after every upload, at the schedule step."""
    r1 = _round_1()
    entry = {"guest_name": "", "guest_email": "old@example.test"}
    r1._set_featured_person(entry, None, True)
    assert entry == {"guest_name": HOST, "guest_email": ""}, entry
    keep = {"guest_name": "Jane Invented", "guest_email": "jane@example.test"}
    r1._set_featured_person(keep, None, False)  # neither: the entry keeps what it had
    assert keep == {"guest_name": "Jane Invented", "guest_email": "jane@example.test"}, keep
    src = (REPO / "rockstar" / "jobs" / "round_1.py").read_text()
    assert "_set_featured_person(existing_sched_entry, guest_rec, host_ep)" in src


def test_a_failed_rerun_names_its_own_episode_not_the_counters():
    """Job 161 was E13 and reported "E36 pipeline failed": the standup and the failure name the bound episode."""
    r1 = _round_1()
    from rockstar.ops import run_ledger

    standup, saved = [], (r1._run_pipeline, r1._prepend_standup)

    def boom(job_id, payload):
        run_ledger.bind(episode="E13")
        raise AttributeError("'NoneType' object has no attribute 'get'")

    r1._run_pipeline, r1._prepend_standup = boom, lambda code, msg: standup.append(code)
    try:
        r1.run(1, "TBD", {"trigger": "test"})
        raise AssertionError("no failure raised")
    except r1.PermanentFailure as exc:
        assert str(exc).startswith("E13 pipeline failed"), exc
        assert standup == ["E13"], standup
    finally:
        r1._run_pipeline, r1._prepend_standup = saved
        run_ledger.bind(episode="TBD")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ok  ", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("  FAIL", name, f"{type(exc).__name__}: {exc}")
    sys.exit(1 if failed else 0)
