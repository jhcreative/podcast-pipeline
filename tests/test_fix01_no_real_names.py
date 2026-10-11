"""
test_fix01_no_real_names.py — FIX-01 AC-3: no real person's name or domain comes out of 1,000 seeds, and the scan
proves it can fail.

Run: python3 -B tests/test_fix01_no_real_names.py   (plain asserts, no pytest needed)

It needs the list of real names, which is private: Orly's G4 denylist, at DENYLIST_PATH or in the secrets store
(~/jhc/secrets/rockstar-podcast/share-denylist.txt). Where neither exists (the public export), conftest SKIPS this
file and says why; it never reports a pass. The export script runs it with the list before every commit (G2).
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fixtures_gen import (  # noqa: E402
    PROPERTIES,
    generate,
    generator,  # noqa: E402
)

pytestmark = pytest.mark.private  # conftest: skipped where the private denylist isn't present


def denylist_names(deny: Path) -> set[str]:
    """Names only: the lines under a `## [...names...]` section header, with a `word:` prefix stripped. Mailboxes,
    domains, IDs and misheard variants are other sections: their pieces ('word', a surname fragment) aren't names a
    generated episode must avoid, and would fail this falsely (Orly, PR #40)."""
    names, in_names = set(), False
    for ln in deny.read_text().splitlines():
        if ln.startswith("## "):
            in_names = "names" in ln.split("]")[0]
            continue
        ln = ln.strip()
        if in_names and ln and not ln.startswith("#"):
            names.add(ln[5:] if ln.startswith("word:") else ln)
    return names


def denylist_path() -> Path | None:
    for p in (os.environ.get("DENYLIST_PATH"), Path.home() / "jhc/secrets/rockstar-podcast/share-denylist.txt"):
        if p and Path(p).is_file():
            return Path(p)
    return None


# The show's own domains are public (the consented ones); third parties' come from the private denylist, so the
# public export never names them (Orly, SHARE-02 pre-scan).
OWN_DOMAINS = ("admin-domain.test", "rockstarblends.com", "johnhiemstra.com")


def real_domains() -> tuple[str, ...]:
    deny = denylist_path()
    listed = [ln.strip().lower() for ln in deny.read_text().splitlines()] if deny else []
    return OWN_DOMAINS + tuple(t for t in listed if t and not t.startswith("#") and "." in t and " " not in t
                               and "@" not in t and not t.replace(".", "").isdigit())


def _real_names() -> tuple[set[str], str]:
    """(name parts that must never come out, where they came from). Sources: the speakers in any real fixtures (a
    fixture marked SYNTHETIC is skipped), and Orly's private G4 denylist. With neither (the public export), this file
    is skipped by conftest, never passed — a scan over nothing is not a pass. Only the public host's exact name is
    carved out: a real guest who is also named Heather is still scanned."""
    names, sources = set(), []
    fixtures = list((REPO / "tests" / "fixtures").rglob("quotes.json"))
    for f in fixtures:
        quotes = json.loads(f.read_text()).get("quotes", [])
        if "SYNTHETIC" in json.loads(f.read_text()).get("_fixture_note", ""):
            continue
        names |= {q.get("speaker", "") for q in quotes}
        sources.append(f.parent.name)
    deny = denylist_path()
    if deny:
        names |= denylist_names(deny)
        sources.append("the denylist")
    names.discard(generator.HOST)
    parts = {p for n in names for p in re.split(r"[^A-Za-z]+", n) if len(p) > 2 and p not in generator.HOST.split()}
    return parts, ", ".join(sources)


def _scan(text: str, parts: set[str]) -> list[str]:
    words = set(re.split(r"[^A-Za-z]+", text))
    hits = sorted(parts & words)
    hits += [d for d in real_domains() if d in text]
    return hits


def test_no_real_names():
    parts, sources = _real_names()
    assert sources, "UNVERIFIED: no real fixtures here and no DENYLIST_PATH — set it to Orly's G4 list"
    assert len(parts) >= 5, f"UNVERIFIED: the denylist has only {len(parts)} name parts (from {sources})"
    for seed in range(1000):
        ep = generate(seed, list(PROPERTIES)[seed % len(PROPERTIES)])
        blob = json.dumps(ep.__dict__, ensure_ascii=False)
        hits = _scan(blob, parts)
        # a count, never the names: this runs inside the export's G2, whose output is printed (Orly, PR #40)
        assert not hits, f"seed {seed}: {len(hits)} real name part(s) or domain(s) in generated output"
    planted = generate(0).apple_summary.replace(generate(0).guest_name, sorted(parts)[0] + " Example")
    assert _scan(planted, parts), "the scan must be able to fail (a planted real name is caught)"
    assert _scan("write to someone@rockstarblends.com", parts), "a real domain is caught"


def main() -> int:
    try:
        test_no_real_names()
    except AssertionError as e:
        print(f"  FAIL test_no_real_names: {e}")
        return 1
    print("  ok   test_no_real_names\n\n1 passed, 0 failed (of 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
