"""
test_publisher_email.py — the publisher-email tolerance matrix.

Run: python3 test_publisher_email.py   (plain asserts, no pytest needed)

Every row here is a way the publisher's "Episode is Live" email has drifted or
could drift (sender × subject × URL shape × identification). If the publisher
changes again, add the new shape here FIRST — a red test is how we learn the
contract moved, instead of a silently dropped episode.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repo root: tests/ is one level down

import publisher_email as pe  # noqa: E402

# ── sender allowlist: old AND new both accepted; strangers rejected ───────────
assert pe.is_publisher_sender("notices@publisher.example.test")
assert pe.is_publisher_sender("notices@legacy-publisher.example.test")
assert pe.is_publisher_sender("Someone <x@legacy-publisher.example.org>")
assert not pe.is_publisher_sender("scammer@gmail.com")
assert not pe.is_publisher_sender("")
assert pe.gmail_from_query() == "from:(@legacy-publisher.example.test OR @legacy-publisher.example.org OR @publisher.example.test)"

# ── original notification vs reply/forward ───────────────────────────────────
assert pe.is_publish_notification("Your Episode is Live | No Stone Unturned | Bone Density at Every Ages")
assert pe.is_publish_notification("Your Episode is Live | EP 26 GenX Hits Menopause")  # old format
assert not pe.is_publish_notification("Re: Your Episode is Live | No Stone Unturned")  # reply
assert not pe.is_publish_notification("Fwd: Your Episode is Live | EP 26")            # forward
# original subject but carries an In-Reply-To header → still a reply in-thread
assert not pe.is_publish_notification("Your Episode is Live | x", in_reply_to="<abc@mail>")
assert not pe.is_publish_notification("Re: quick question")
assert not pe.is_publish_notification("Your weekly digest")

# ── Apple id repair (the publisher mangles ?i=1000… three different ways) ─────
assert pe.normalize_apple_url(
    "https://podcasts.apple.com/us/podcast/no-stone-unturned/id1785708777?i00700000101&uo=4"
) == "https://podcasts.apple.com/us/podcast/no-stone-unturned/id1785708777?i=1000700000101"
assert pe.normalize_apple_url(
    "https://podcasts.apple.com/us/podcast/one-omega/id1785708777?i00700000102"
) == "https://podcasts.apple.com/us/podcast/one-omega/id1785708777?i=1000700000102"
# already-correct id passes through untouched (minus tracking suffix)
assert pe.normalize_apple_url(
    "https://podcasts.apple.com/us/podcast/glow-up/id1785708777?i=1000700000103"
) == "https://podcasts.apple.com/us/podcast/glow-up/id1785708777?i=1000700000103"

# ── URL extraction from an HTML-only body (links in button hrefs) ────────────
html = (
    '<a href="https://podcasts.apple.com/us/podcast/no-stone-unturned/id1785708777?i00700000101&uo=4">Listen</a>'
    '<a href="https://open.spotify.com/episode/0SynthPubXAbCdEfGhJkLm">Listen</a>'
    '<a href="https://legacy-publisher.example.test/no-stone-unturned/">More</a>'
)
urls = pe.extract_publish_urls(text="", html=html)
assert urls["apple_url"] == "https://podcasts.apple.com/us/podcast/no-stone-unturned/id1785708777?i=1000700000101", urls
assert urls["spotify_url"] == "https://open.spotify.com/episode/0SynthPubXAbCdEfGhJkLm", urls
assert urls["mm_url"] == "https://legacy-publisher.example.test/no-stone-unturned/", urls

# ── episode identification WITHOUT an EP number ───────────────────────────────
EPISODES = [
    {"episode_number": 27, "episode_title": "Time to Glow Up | Finding Yourself Again in Midlife",
     "drive_folder_id": "f27", "status": "done", "pipeline_done": True},
    {"episode_number": 28, "episode_title": "No Stone Unturned | Bone Density at Every Age",
     "drive_folder_id": "f28", "status": "drafting", "pipeline_done": False},
]
# title match (the new no-number subject) → E28
got = pe.identify_episode(
    "Your Episode is Live | No Stone Unturned | Bone Density at Every Ages", "", EPISODES
)
assert got and got["episode_number"] == 28, got
# explicit EP number still works (back-compat) → E27
got = pe.identify_episode("Your Episode is Live | EP 27 GenX Hits Menopause", "", EPISODES)
assert got and got["episode_number"] == 27, got
# single awaiting-publish fallback when nothing else matches → E28 (only un-done)
got = pe.identify_episode("Your Episode is Live | (some title we don't recognize)", "", EPISODES)
assert got and got["episode_number"] == 28, got
# genuinely ambiguous → None (caller must fail safe, never guess)
two_waiting = EPISODES + [
    {"episode_number": 29, "episode_title": "Another One | Different Topic",
     "drive_folder_id": "f29", "status": "drafting", "pipeline_done": False}
]
got = pe.identify_episode("Your Episode is Live | nothing matches here", "", two_waiting)
assert got is None, got
# a Round-1 RE-RUN writes status "reprocessed" (round_1.py) — still awaiting publication
reprocessed = [dict(EPISODES[0]), dict(EPISODES[1], status="reprocessed")]
got = pe.identify_episode("Your Episode is Live | (some title we don't recognize)", "", reprocessed)
assert got and got["episode_number"] == 28, got
# …and it counts toward ambiguity like "drafting" does (fail safe, never guess)
got = pe.identify_episode("Your Episode is Live | nothing matches here", "",
                          reprocessed + [two_waiting[2]])
assert got is None, got

print("publisher_email: all tolerance-matrix cases passed ✓")
