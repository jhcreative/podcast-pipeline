"""
test_card04_covers.py — CARD-04 carousel cover slides.

Run: python3 test_card04_covers.py   (plain asserts, no pytest needed)

Covers CARD-04 acceptance criteria:
  AC2 the speaker's reel image, tile fallback, host   → test_pick_*
  AC2 only the photograph, never the design's text    → test_photo_box_*
  AC3 titles gated (canon register, no restatement)   → test_title_*
  AC4 fixed type, a title that cannot fit fails       → test_render_*  (real Chromium)
  AC6 backfill is add-only: no trash, no replace      → test_add_covers_*
  AC5 the footer title follows ADR-0009               → test_episode_title_*

Fixtures are inline (Episodes/ is gitignored). The render tests drive real Chromium
because the browser is the authority on what fits; without playwright they SKIP
LOUDLY rather than pass quietly (REEL-05).
"""

import os
import sys
import tempfile
import types
from pathlib import Path

from PIL import Image

WORKTREE = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(WORKTREE / "quote_cards"))

import cover_backfill  # noqa: E402
import cover_image  # noqa: E402
import cover_titles  # noqa: E402


def _raises(fn, *a, contains=""):
    try:
        fn(*a)
    except SystemExit as e:
        assert contains in str(e), f"expected {contains!r} in {e}"
        return str(e)
    raise AssertionError(f"{fn.__name__}{a} did not fail")


# ── which image (AC2) ──────────────────────────────────────────────────────────

HOST = "Heather Reilly Hiemstra"


def test_is_host_by_surname_not_first_name():
    assert cover_image.is_host(HOST)
    assert cover_image.is_host("Heather Reilly-Hemstra")          # E30's transcript
    assert not cover_image.is_host("Heather Kestrel-Morrowvane")      # E27's guest
    assert cover_image.display_name("Heather Reilly-Hemstra") == HOST
    print("  ok  host recognised by surname; E27's guest Heather is not the host")


def test_pick_reel_before_tile():
    """The newer tiles have no seam between text and photo (E30/E31); reels always do."""
    names = ["E30-reel-oriel-web.jpg", "E30-tile-oriel.jpg", "quotes.json"]
    sp = ["Dr. Oriel Fenwax"] * 4 + ["Heather Reilly-Hemstra"]
    assert cover_image.pick_image("Dr. Oriel Fenwax", names, sp) == ("E30-reel-oriel-web.jpg", "reel-named")
    assert cover_image.pick_image("Dr. Oriel Fenwax", ["E30-tile-oriel.jpg"], sp) == \
        ("E30-tile-oriel.jpg", "tile-named")
    print("  ok  the speaker's reel image wins; the tile is the fallback")


def test_pick_named_with_repeated_speakers():
    """Speakers repeat once per quote. A first name is 'shared' only between
    different people -- the bug the rehearsal caught."""
    names = ["E9-Ann-tile.jpg", "E9-Bea-tile.jpg"]
    sp = ["Ann Ames"] * 3 + ["Bea Bell"] * 2
    assert cover_image.pick_image("Ann Ames", names, sp)[0] == "E9-Ann-tile.jpg"
    assert cover_image.pick_image("Bea Bell", names, sp)[0] == "E9-Bea-tile.jpg"
    print("  ok  two guests each get their own tile")


def test_pick_host_default_and_never_a_guests_single_image():
    sp = ["Juniper Larkspirelle", HOST]
    names = ["E25-Juniper-Larkspirelle-tile.jpg"]
    assert cover_image.pick_image(HOST, names, sp) == (cover_image.HOST_IMAGE, "host-default")
    assert cover_image.pick_image("Juniper Larkspirelle", names, sp)[1] == "tile-named"   # no reel: tile
    print("  ok  the host falls back to BrandAssets, never to the guest's photo")


def test_pick_guest_named_heather_gets_her_own_image():
    sp = ["Heather Kestrel-Morrowvane"] * 5
    assert cover_image.pick_image(sp[0], ["E27-reel-v2.jpg"], sp) == ("E27-reel-v2.jpg", "reel-single")
    print("  ok  E27: a guest named Heather gets the episode image, not the host's")


def test_pick_missing_image_fails_naming_the_file():
    msg = _raises(cover_image.pick_image, "Dr. Saffi Velloquin", ["quotes.json"],
                  ["Dr. Saffi Velloquin"], contains="E<N>-Saffi-tile.jpg")
    assert "no tile or reel image" in msg
    print("  ok  no image -> hard failure that names the file to drop")


def test_pick_never_gives_a_guest_the_hosts_single_image():
    """Cold review #1: the host has no quote, so she is not among `others`."""
    sp = ["Oriel Smith"] * 5
    _raises(cover_image.pick_image, "Oriel Smith", ["E30-Heather-Reilly-Hiemstra-reel.jpg"],
            sp, contains="no tile or reel image")
    print("  ok  a guest never inherits the host's lone image")


def test_backfill_fetches_underscore_names():
    assert cover_backfill._IMAGE.match("E30_Oriel_tile.jpg")
    assert cover_backfill._IMAGE.match("E30-Oriel-tile.jpg")
    assert not cover_backfill._IMAGE.match("E30-reelection.jpg")
    print("  ok  E30_Oriel_tile.jpg is fetched (\\b treats _ as a word character)")


# ── the photograph inside the design (AC2) ─────────────────────────────────────

PURPLE, PANEL, PHOTO = (45, 25, 60), (205, 208, 204), (60, 110, 210)


def _tile():
    """Name panel left 44%, photo right, purple wordmark across the bottom 42%."""
    im = Image.new("RGB", (1000, 1000), PURPLE)
    im.paste(PANEL, (0, 0, 440, 580))
    im.paste(PHOTO, (440, 0, 1000, 580))
    return im


def _reel():
    """Purple header 12%, photo, purple below 70%."""
    im = Image.new("RGB", (600, 1066), PURPLE)
    im.paste((200, 200, 200), (0, 130, 600, 746))         # a light backdrop photo
    return im


def test_photo_box_tile_drops_name_panel_and_wordmark():
    l, t, r, b = cover_image.photo_box(_tile(), "tile")
    assert abs(l - 440) <= 10 and t == 0 and r == 1000 and abs(b - 580) <= 10, (l, t, r, b)
    print(f"  ok  tile photo box {l, t, r, b}: panel and wordmark excluded")


def test_photo_box_reel_keeps_full_width_on_a_light_backdrop():
    """The host photo sits on light grey; the panel rule must not run on a reel."""
    l, t, r, b = cover_image.photo_box(_reel(), "reel")
    assert l == 0 and r == 600 and abs(t - 130) <= 6 and abs(b - 746) <= 6, (l, t, r, b)
    print(f"  ok  reel photo box {l, t, r, b}: header and footer excluded, full width")


def test_photo_is_the_largest_square():
    """John, 2026-10-08: square, not a tight crop on the face."""
    l, t, r, b = cover_image.square_box((0, 100, 600, 1100))     # tall: keep the top
    assert (r - l, b - t) == (600, 600) and t == 100 + 60, (l, t, r, b)
    l, t, r, b = cover_image.square_box((0, 0, 1200, 600))       # wide: centre across
    assert (l, r, t, b) == (300, 900, 0, 600)
    print("  ok  the photo is the largest square, head kept on a tall photo")


def test_name_splits_after_the_first_name():
    """John, 2026-10-08: 'Heather' then 'Reilly Hiemstra'; first name larger."""
    import cover_format as cf
    assert cf.split_name(HOST) == ("Heather", "Reilly Hiemstra")
    assert cf.split_name("Dr. Thessaly Brindlequist") == ("Dr. Thessaly", "Brindlequist")
    assert cf.split_name("Juniper Larkspirelle") == ("Juniper", "Larkspirelle")
    assert cf.split_name("Cher") == ("Cher", "")
    html = cf._name_html("Heather Kestrel-Morrowvane")
    assert 'class="first"' in html and "Kestrel-Morrowvane</span>" in html
    assert cf.FIRST_PX == round(cf.LAST_PX * 1.2)
    print("  ok  first name (with honorific) on its own larger line; surnames never break")


# ── titles (AC3) ───────────────────────────────────────────────────────────────

QUOTE = ("Do not wait until your period stops to pay attention to your symptoms. "
         "You live in your body.")


def test_title_passes_a_good_one():
    assert cover_titles.assert_title(" You Know Your Body Best ", QUOTE, "t") == "You Know Your Body Best"
    print("  ok  a plain, substantive title passes")


def test_title_gates():
    cases = [
        ("", "empty"),
        ("Word", "words"),
        ("A Title That Is Far Too Long To Read In Any Thumbnail", "characters"),
        ("Why Wait?", "no exclamation"),
        ("Hormones & Health", "'and'"),
        ("This Herb Cures Hot Flashes", "claims word"),
        ("The Secret of Perimenopause", "clickbait"),
        ("Honestly About Hormones", "candour"),
        ("My Hormone Story", "first person"),
        ("Visit www.example.com Today", "URL"),
        ("HORMONES AND HEALTH", "all caps"),
        ("Pay Attention to Your Symptoms", "restates the quote"),
        ("Healing the Nervous System", "claims word"),
        ("Cured by Plants", "claims word"),
        ("What Actually Works in Midlife", "claims word"),
        ("Five Hormone Hacks", "clickbait"),
        ("Secrets of the ECS", "clickbait"),
        ("Wait for It...", "ellipsis"),
        (["a", "list"], "not text"),
    ]
    for title, why in cases:
        _raises(cover_titles.assert_title, title, QUOTE, "t", contains=why)
    # A straight apostrophe in the transcript, a curly one in the title: still a copy.
    _raises(cover_titles.assert_title, "You Don’t Have to Wait Long",
            "You don't have to wait for the symptoms.", "t", contains="restates")
    for good in ("How Your Brain Works in Midlife", "Treat Sleep Like Training",
                 "Part I of the Hormone Story", "Estrogen, i.e. the Signal"):
        cover_titles.assert_title(good, QUOTE, "t")
    print(f"  ok  {len(cases)} gate failures each named; 4 plain titles not over-caught")


def test_titles_refuse_a_moved_quote():
    """Cold review #4: ids are positions; a regenerated quotes.json can move them."""
    q = [{"id": "q01", "text": QUOTE}]
    with tempfile.TemporaryDirectory() as d:
        cover_titles.save(d, 29, {"q01": "You Know Your Body Best"}, "test", q)
        assert cover_titles.titles_for(d, 29, q)["q01"] == "You Know Your Body Best"
        _raises(cover_titles.titles_for, d, 29, [{"id": "q01", "text": "Another quote."}],
                contains="different quote")
    print("  ok  a title follows its quote, not its position")


def test_titles_for_requires_the_sidecar_and_every_id():
    q = [{"id": "q01", "text": QUOTE}, {"id": "q02", "text": QUOTE}]
    with tempfile.TemporaryDirectory() as d:
        _raises(cover_titles.titles_for, d, 29, q, contains="make_cover_titles.py")
        cover_titles.save(d, 29, {"q01": "You Know Your Body Best"}, "test")
        _raises(cover_titles.titles_for, d, 29, q, contains="no title for q02")
    print("  ok  no sidecar or a missing id stops the render before any slide")


# ── footer title, ADR-0009 (AC5) ───────────────────────────────────────────────

def test_episode_title_shapes():
    t = cover_backfill.episode_title
    assert t("Feed Your Head | Gut Microbiome\nHeather sits down…") == "Feed Your Head | Gut Microbiome"
    assert t("# E25 — Get Back To Your Authentic Self\n\n**Guest:** Juniper") == "Get Back To Your Authentic Self"
    assert t("# Apple/Spotify Show Notes — E24\n\n## Title (LOCKED — published)\n\n**My Generations**\n") == "My Generations"
    assert t("## Feed Your Head | Gut Microbiome, Hormones & Midlife Vitality\n\nHeather…") == \
        "Feed Your Head | Gut Microbiome, Hormones & Midlife Vitality"        # Drive Doc export
    assert t("\ufeff## Bliss Is the System | The ECS\n") == "Bliss Is the System | The ECS"
    assert t("# Apple/Spotify Show Notes — E24\n\nno locked title") is None
    assert t("## About Juniper Larkspirelle\n\nJuniper is…") is None
    print("  ok  three live apple_summary shapes; none matching -> None, never a guess")


def test_footer_title_drops_show_name_and_season_code():
    t = cover_backfill.episode_title
    assert t("# Gen X Hits Menopause · S2E3 · No Stone Unturned | Bone Density\n") == \
        "No Stone Unturned | Bone Density"
    assert t("# Gen X Hits Menopause · S2E8 · The Alarm Is Lying\n") == "The Alarm Is Lying"
    print("  ok  the footer never repeats the show name (format §6)")


def test_caption_about_heading_joined_to_its_paragraph():
    """E28's Docs export: 'About Calloway Thistlewoodby Calloway Thistlewoodby is the founding CEO …'."""
    import make_captions
    g, about = make_captions.extract_about(
        "# Title\n\nAbout Calloway Thistlewoodby Calloway Thistlewoodby is the founding CEO of Vellorix.\n\n"
        "Before building Vellorix, he founded a clinic.\n", "Calloway Thistlewoodby")
    assert g == "Calloway Thistlewoodby", g
    assert about.startswith("Calloway Thistlewoodby is the founding CEO") and "Before building" in about
    print("  ok  a heading run into its bio yields the name, and the bio keeps its first line")


def test_slide_citation_spells_the_host_canonically():
    import render_cards  # noqa: F401  (the citation goes through display_name)
    assert cover_image.display_name("Heather Riley-Hemstra") == HOST
    print("  ok  'Heather Riley-Hemstra' cites as Heather Reilly Hiemstra")


# ── add-only backfill (AC6) ────────────────────────────────────────────────────

class FakeDrive(types.ModuleType):
    def __init__(self, existing=(), cards=True):
        super().__init__("drive_client")
        self.calls, self.existing, self.cards = [], list(existing), cards

    folder_name = "E29-thessaly"

    def find_episode_folder(self, prefix):
        return {"id": "ep", "name": self.folder_name}

    def list_folder(self, fid):
        if fid == "ep":
            return ([{"id": "cards", "name": "cards",
                      "mimeType": "application/vnd.google-apps.folder"}] if self.cards else [])
        return [{"id": n, "name": n} for n in self.existing]

    def upload_file(self, path, folder, mime_type=""):
        self.calls.append(("upload", Path(path).name))

    def trash_file(self, fid):
        self.calls.append(("TRASH", fid))

    def create_folder(self, name, parent):
        self.calls.append(("CREATE", name))


def _covers_dir(tmp):
    d = Path(tmp)
    for n in ("E29_q01_0cover.png", "E29_q02_0cover.png", "E29_q01_1of2.png", "x.txt"):
        (d / n).write_text("x")
    return str(d)


SLIDES = ["E29_q01_1of2.png", "E29_q01_2of2.png", "E29_q02_1of1.png", "E29_q01_caption.txt"]


def test_add_covers_adds_only_covers_and_never_trashes():
    fake = FakeDrive(existing=SLIDES)
    sys.modules["drive_client"] = fake
    import card_upload
    with tempfile.TemporaryDirectory() as tmp:
        up, skip = card_upload.add_covers(29, _covers_dir(tmp))
    assert up == ["E29_q01_0cover.png", "E29_q02_0cover.png"] and skip == [], (up, skip)
    assert not any(c[0] in ("TRASH", "CREATE") for c in fake.calls), fake.calls
    print("  ok  only *_0cover.png go up; slides and captions untouched")


def test_add_covers_only_joins_carousels_that_exist_in_the_exact_folder():
    """Cold review #2 and #3."""
    import card_upload
    sys.modules["drive_client"] = fake = FakeDrive(existing=["E29_q01_1of2.png"])
    with tempfile.TemporaryDirectory() as tmp:
        up, skip = card_upload.add_covers(29, _covers_dir(tmp))
    assert up == ["E29_q01_0cover.png"] and skip == ["E29_q02_0cover.png"], (up, skip)
    sys.modules["drive_client"] = fake = FakeDrive(existing=SLIDES)
    fake.folder_name = "E30-oriel"
    with tempfile.TemporaryDirectory() as tmp:
        _raises(card_upload.add_covers, 3, _covers_dir(tmp), contains="no Drive folder named")
    assert not fake.calls
    print("  ok  no lone covers; 'E3-' never writes into E30's folder")


def test_add_covers_skips_what_is_there_and_refuses_without_cards():
    sys.modules["drive_client"] = FakeDrive(existing=SLIDES + ["E29_q01_0cover.png"])
    import card_upload
    with tempfile.TemporaryDirectory() as tmp:
        up, skip = card_upload.add_covers(29, _covers_dir(tmp))
    assert up == ["E29_q02_0cover.png"] and skip == ["E29_q01_0cover.png"]
    sys.modules["drive_client"] = fake = FakeDrive(cards=False)
    with tempfile.TemporaryDirectory() as tmp:
        _raises(card_upload.add_covers, 29, _covers_dir(tmp), contains="no cards/")
    assert not fake.calls
    print("  ok  re-run uploads nothing twice; no cards/ -> nothing created")


def test_new_episode_paths_refuse_existing_cards():
    """new-cards.sh: a full set only goes where there is none -- CARD-02's upload
    trashes cards/ first, and those may be posted."""
    import card_upload
    sys.modules["drive_client"] = fake = FakeDrive(existing=SLIDES)
    with tempfile.TemporaryDirectory() as tmp:
        _raises(card_upload.upload_cards, 29, _covers_dir(tmp), True, contains="--new-only refuses")
    assert not any(c[0] == "TRASH" for c in fake.calls), fake.calls
    with tempfile.TemporaryDirectory() as tmp:
        _raises(cover_backfill.cmd_fetch, fake, 29, tmp, True, contains="not a new episode")
    print("  ok  a new-episode push or fetch refuses a cards/ that already has files")


# ── render (AC4), real Chromium ────────────────────────────────────────────────

def _render_tests():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  SKIP  render tests: playwright not installed -- NOT a pass")
        return
    import render_covers
    with tempfile.TemporaryDirectory() as tmp:
        ep = Path(tmp) / "E99-fixture"
        ep.mkdir()
        _tile().save(ep / "E99-Ann-tile.jpg")
        q = {"id": "q01", "speaker": "Dr. Ann Ames", "text": QUOTE}
        with sync_playwright() as pw:
            page = render_covers.open_page(pw)
            path, rule = render_covers.render_cover(
                page, str(ep), 99, q, [q["speaker"]], "You Know Your Body Best",
                "Feed Your Head | Gut Microbiome, Hormones & Midlife Vitality", tmp)
            assert os.path.basename(path) == "E99_q01_0cover.png" and rule == "tile-named"
            assert Image.open(path).size == (1080, 1080)
            print("  ok  a cover renders 1080x1080, chrome and photo proven painted")
            _raises(render_covers.render_cover, page, str(ep), 99, q, [q["speaker"]],
                    "Supercalifragilisticexpialidocious Endocannabinoids", "T", tmp,
                    contains="does not shrink")
            print("  ok  a title that cannot fit fails; the type never shrinks")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    _render_tests()
    print(f"\n{len(tests)} tests + render checks passed")
