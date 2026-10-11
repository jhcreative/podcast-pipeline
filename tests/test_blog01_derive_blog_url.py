"""
test_blog01_derive_blog_url.py — BLOG-01 apostrophe-slug fix.

Run: python3 test_blog01_derive_blog_url.py   (plain asserts, no pytest needed)

Covers BLOG-01 acceptance criteria:
  AC1 correct slug rule (apostrophes REMOVED, not hyphenated) → test_apostrophe_*
  AC3 tests: Don't→dont, plain title unchanged, punctuation cases → test_*

Confirmed against LIVE rockstarblends.com posts (Bill, 2026-07-06):
  E22 comma  → 200   E28 pipe → 200   E29 apostrophe → 200 (dont-wait)
  E23 colon  → post unpublished (empty stored URL); colon+space is structurally
               identical to the confirmed E22 comma+space rule, so it is covered
               at the rule level here.

BLOG-02 (2026-09-24): _derive_blog_url now delegates to blog_post.propose_slug — the one
slug rule — on the Tailor base /blog, with NO "-genx-hits-menopause-eN" suffix (live Tailor
slugs carry none). The apostrophe rule these tests pin is unchanged; only the base and the
suffix moved. The URL Phase 2 writes comes from the API (AC-6); this is the proposal.

Discipline (same as test_title01): _derive_blog_url lives in publish_finalize,
which imports heavy I/O deps (anthropic/googleapiclient) not installed locally.
We stub those boundary modules in sys.modules and call Bill's REAL _derive_blog_url
— the slug logic is exercised live, not re-implemented.
"""

import importlib
import sys
import types
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE))
BASE = "https://rockstarblends.com/blog"  # BLOG-02: Tailor serves /blog; /the-blog 301s


def _install_anthropic_stub():
    real = sys.modules.get("anthropic")
    if real is not None:
        assert isinstance(getattr(real, "Anthropic", None), type)
        return
    anthropic = types.ModuleType("anthropic")

    class Anthropic:  # noqa: N801 — mirrors the SDK name; a real class
        def __init__(self, *a, **k):
            pass

    anthropic.Anthropic = Anthropic
    sys.modules["anthropic"] = anthropic


def _pf():
    """Import jobs.publish_finalize with heavy I/O-boundary modules stubbed. Only
    the pure _derive_blog_url is exercised; nothing here calls Drive/anthropic."""
    _install_anthropic_stub()
    for name in ("calendar_client", "drive_client", "gmail_client", "publisher_email", "db"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules.pop("jobs.publish_finalize", None)
    import jobs.publish_finalize as pf
    importlib.reload(pf)
    return pf


PF = _pf()


def test_apostrophe_removed_not_hyphenated():
    # THE bug: "Don't" must slug to "dont", NEVER "don-t".
    got = PF._derive_blog_url("Don't Wait for the Stop | Optimize Hormones Before Perimenopause", 29)
    expected = (f"{BASE}/dont-wait-for-the-stop-optimize-hormones-before-perimenopause")
    assert got == expected, f"\n got={got}\nwant={expected}"
    assert "don-t" not in got, f"apostrophe was hyphenated, not removed: {got}"
    print("  AC1/AC3 apostrophe: \"Don't Wait…\" → dont-wait… (not don-t) ✓")


def test_curly_apostrophe_also_removed():
    # Hand-edited titles can carry a curly ’ (U+2019) or ‘ (U+2018) — strip both too.
    straight = PF._derive_blog_url("Don't Wait", 29)
    curly = PF._derive_blog_url("Don’t Wait", 29)
    assert straight == curly == f"{BASE}/dont-wait", (straight, curly)
    print("  AC1 curly apostrophe (’) stripped identically to straight (') ✓")


def test_multiple_apostrophes():
    got = PF._derive_blog_url("It's Can't Won't", 40)
    assert got == f"{BASE}/its-cant-wont", got
    print("  AC1 multiple apostrophes: It's Can't Won't → its-cant-wont ✓")


def test_plain_title_unchanged():
    # A title with NO apostrophe must be untouched by the new strip step.
    got = PF._derive_blog_url("No Stone Unturned | Bone Density at Every Age", 28)
    expected = f"{BASE}/no-stone-unturned-bone-density-at-every-age"
    assert got == expected, f"\n got={got}\nwant={expected}"
    print("  AC3 plain title (E28, pipe) unchanged → verified 200 live ✓")


def test_comma_and_colon_collapse_to_hyphen():
    # Separators surrounded by spaces collapse to ONE hyphen — comma confirmed 200
    # live (E22); colon is the same run shape.
    comma = PF._derive_blog_url("Not the Doctors | Personalized Medicine, Personal Responsibility", 22)
    assert comma == (f"{BASE}/not-the-doctors-personalized-medicine-personal-responsibility"), comma
    colon = PF._derive_blog_url("Red Light and Blue: Our Midlife Clean Energy Future", 23)
    assert colon == (f"{BASE}/red-light-and-blue-our-midlife-clean-energy-future"), colon
    print("  AC1 comma (E22, live 200) + colon (E23) → single hyphen, no double ✓")


def main() -> int:
    tests = [
        test_apostrophe_removed_not_hyphenated,
        test_curly_apostrophe_also_removed,
        test_multiple_apostrophes,
        test_plain_title_unchanged,
        test_comma_and_colon_collapse_to_hyphen,
    ]
    failed = 0
    print("BLOG-01 _derive_blog_url apostrophe-slug — test run")
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
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
