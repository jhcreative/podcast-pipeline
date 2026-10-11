"""
test_reel09_host_identity.py — REEL-09: one host rule, by full name, hyphen-tolerant.

Run: python3 test_reel09_host_identity.py   (plain asserts, no pytest needed)

  AC1 E27's guest Heather keeps her own reel image      → test_e27_guest_heather_gets_her_image
  AC2 every spelling of the host, no other Heather       → test_is_host_*
  AC3 misspellings canonicalize, hyphenated included     → test_canonicalize_*
  AC4 no module keeps its own host rule                  → test_one_rule_everywhere
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "quote_cards"))

import content_gates  # noqa: E402
import episode_files  # noqa: E402
import host_identity  # noqa: E402
import summary_norm  # noqa: E402

HOST = "Heather Reilly Hiemstra"
HOST_SPELLINGS = ["Heather Reilly Hiemstra", "Heather Reilly-Hemstra", "Heather Riley-Hemstra",
                  "Heather Riley Heemstra", "Heather Riley", "Heather", "heather reilly hiemstra"]
OTHER_HEATHERS = ["Heather Kestrel-Morrowvane", "Heather Lockwoodby", "Dr. Heather Smith"]


def test_is_host_every_spelling():
    for s in HOST_SPELLINGS:
        assert host_identity.is_host(s), s
    print(f"  ok  {len(HOST_SPELLINGS)} spellings of the host are the host")


def test_is_host_no_other_heather():
    for s in OTHER_HEATHERS + ["Linnea Ashgrovenly", ""]:
        assert not host_identity.is_host(s), s
    print("  ok  Kestrel-Morrowvane, Lockwoodby, Smith are not the host")


def test_canonicalize_speaker_and_text():
    for s in HOST_SPELLINGS:
        assert summary_norm.canonicalize_speaker(s) == HOST, s
    assert summary_norm.canonicalize_speaker("Heather Kestrel-Morrowvane") == "Heather Kestrel-Morrowvane"
    text = "With Heather Reilly-Hemstra and Heather Riley-Hemstra on the mic."
    assert summary_norm.canonicalize_host(text) == f"With {HOST} and {HOST} on the mic."
    assert "-Hemstra" not in summary_norm.canonicalize_host("Heather Reilly-Hemstra")
    print("  ok  hyphenated misspellings become 'Heather Reilly Hiemstra', in names and in text")


def test_e27_guest_heather_gets_her_image():
    reels = [{"name": "E27-reel-v2.jpg", "id": "1"}]
    sp = ["Heather Kestrel-Morrowvane"] * 5
    assert episode_files.background_for(sp[0], reels, sp) == (reels[0], "single")
    assert episode_files.background_for(HOST, [], [HOST])[1] == "host-default"
    print("  ok  E27: the guest Heather gets the episode's image; the host still defaults")


def test_gates_and_cards_agree():
    import cover_image
    for s in HOST_SPELLINGS + OTHER_HEATHERS:
        assert content_gates._is_host_speaker(s) == host_identity.is_host(s), s
        assert cover_image.is_host(s) == host_identity.is_host(s), s
    assert cover_image.display_name("Heather Riley-Hemstra") == HOST
    print("  ok  the guest gate and the cards use the same rule")


def test_one_rule_everywhere():
    """AC4: no module keeps its own host rule."""
    hits = []
    for p in list(ROOT.glob("*.py")) + list(ROOT.glob("jobs/*.py")) + list(ROOT.glob("quote_cards/*.py")):
        if p.name.startswith("test_") or p.name == "host_identity.py":
            continue
        src = p.read_text(encoding="utf-8")
        if "HOST_FIRST_NAME" in src or re.search(r'^HOST_NAME\s*=\s*"', src, re.M):
            hits.append(p.name)
    assert not hits, f"own host rule in: {hits}"
    print("  ok  HOST_NAME and the host rule are defined once (host_identity)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)} tests passed")
