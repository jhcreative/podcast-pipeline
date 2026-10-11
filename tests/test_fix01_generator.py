"""
test_fix01_generator.py — FIX-01: a seeded synthetic episode generator, with known properties and no real people.

Run: python3 -B test_fix01_generator.py   (plain asserts, no pytest needed)

Acceptance criteria → tests:
  AC-1 the same seed gives byte-identical output; a different seed gives different names  → test_seeded
  AC-2 for the 5 gate-checked properties, the gate it plants a defect for refuses it, by name; a clean episode
       passes; the other 4 are generated and pass the Python gates (stated, not a check of the property); 200+
       seeds in under 30 s                                                                   → test_property_mode
  AC-3 no real person's name comes out of 1,000 seeds → test_fix01_no_real_names.py (needs the private denylist)
  (and) a generated clean episode is a working fixture: the real build_post makes a well-formed post
                                                                                             → test_clean_episode_builds
Discipline: the gates are the pipeline's own (content_gates, blog_post), never re-implemented here.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))

from fixtures_gen import (  # noqa: E402
    PROPERTIES,
    generate,
    run_gates,
)

GOLDEN = "f326a1b0827c7506a399baedd1d8b8dcde07bfd004c7bbf78fd590582767f277"


def _files(ep) -> dict:
    d = Path(tempfile.mkdtemp(prefix="fix01-"))
    ep.write(d)
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


def test_seeded():
    for prop in PROPERTIES:
        assert _files(generate(11, prop)) == _files(generate(11, prop)), f"{prop}: same seed, same bytes"
    names = {generate(s).guest_name for s in range(40)}
    assert len(names) > 10, f"different seeds give different guests: {names}"


def test_property_mode():
    started, seeds = time.monotonic(), range(200)
    for seed in seeds:
        for prop, want in PROPERTIES.items():
            got = run_gates(generate(seed, prop))
            assert got == want, f"seed {seed}, {prop}: refused by {sorted(got)}, expected {sorted(want)}"
    took = time.monotonic() - started
    assert took < 30, f"{len(seeds)} seeds × {len(PROPERTIES)} properties took {took:.1f}s"


def test_every_guest_is_marked_synthetic():
    for seed in range(50):
        ep = generate(seed)
        assert ep.transcript.startswith("[SYNTHETIC EPISODE") and "synthetic guest" in ep.apple_summary


def test_golden_hash_pins_determinism():
    import hashlib
    blob = json.dumps([generate(s, p).__dict__ for s in (1, 2) for p in sorted(PROPERTIES)], sort_keys=True)
    got = hashlib.sha256(blob.encode()).hexdigest()
    assert got == GOLDEN, f"generator output drifted (Python's random or the code changed): {got}"


def test_clean_episode_builds():
    import blog_post
    import content_gates
    ep = generate(5)
    meta = {"episode_title": ep.title, "episode_number": ep.number,
            "spotify_url": "https://open.spotify.com/episode/0TPlCabcdefghijklmnopq",
            "apple_url": "https://podcasts.apple.com/us/podcast/genx-hits-menopause/id1785708777?i=1000700000033"}
    post = blog_post.build_post(ep.apple_summary, ep.quotes, ep.faq, meta)
    content_gates.assert_blog_post_wellformed(post, ep.quotes, ep.code)
    assert ep.guest_name in json.dumps(post) and post["slug"] == blog_post.propose_slug(ep.title)


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
