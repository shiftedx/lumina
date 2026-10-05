"""Gallery contract: signed rendition URLs, TitleArt building and the preparation queue."""
from __future__ import annotations

import re
import stat
from types import SimpleNamespace

import pytest

from app.models import MediaTitle, TitleArtwork
from app.services import art_urls

TITLE_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_secret", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(art_urls, "on_enqueue", None)


def _title(type_: str = "movie", path: str = "movies/Dune/poster.jpg") -> MediaTitle:
    return MediaTitle(id=TITLE_ID, type=type_, key="k", name="Dune", images={"Primary": {"path": path, "tag": "1f-2a"}})


def test_secret_is_created_once_with_mode_0600() -> None:
    first = art_urls.secret()
    assert len(first) == 32
    assert stat.S_IMODE(art_urls.secret_path().stat().st_mode) == 0o600
    art_urls._secret = None
    assert art_urls.secret() == first


def test_signature_is_22_url_safe_chars_and_verifies_only_its_own_triple() -> None:
    key = art_urls.source_key(TITLE_ID, "Primary", "1f-2a")
    sig = art_urls.signature(TITLE_ID, "Primary", key)
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", sig)
    assert art_urls.verify(sig, TITLE_ID, "Primary", key)
    assert not art_urls.verify(sig, TITLE_ID, "Backdrop", key)
    assert not art_urls.verify(sig, TITLE_ID, "Primary", "0" * 64)


def test_source_key_changes_with_the_source_and_the_rendition_version(monkeypatch) -> None:  # noqa: ANN001
    key = art_urls.source_key(TITLE_ID, "Primary", "1f-2a")
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert art_urls.source_key(TITLE_ID, "Primary", "1f-2b") != key
    monkeypatch.setattr(art_urls, "RENDITION_VERSION", 2)
    assert art_urls.source_key(TITLE_ID, "Primary", "1f-2a") != key


def test_title_art_without_a_row_has_a_rendition_and_no_facts() -> None:
    art = art_urls.title_art(_title(), "Primary", source="1f-2a", url="/api/titles/x/images/Primary?tag=t", row=None, preview=True)
    key = art_urls.source_key(TITLE_ID, "Primary", "1f-2a")
    assert art.rendition == f"/api/art/{art_urls.signature(TITLE_ID, 'Primary', key)}/{TITLE_ID}/Primary/{key}-{{w}}.webp"
    assert art.widths == [240, 480]
    assert (art.preview, art.dominant, art.width) == (None, None, None)
    assert art_urls.needs_preparation(_title(), "Primary", "1f-2a", None)


def test_title_art_uses_only_a_current_ready_row() -> None:
    key = art_urls.source_key(TITLE_ID, "Primary", "1f-2a")
    row = TitleArtwork(title_id=TITLE_ID, image_type="Primary", source_key=key, state="ready", width=1000, height=1500,
                       preview=b"RIFF", preview_type="image/webp", dominant="#112233", accent="#aa5500")
    art = art_urls.title_art(_title("episode"), "Primary", source="1f-2a", url="/u", row=row, preview=True)
    assert art.widths == [400]
    assert (art.width, art.dominant, art.accent) == (1000, "#112233", "#aa5500")
    assert art.preview == "data:image/webp;base64,UklGRg=="
    assert art_urls.title_art(_title(), "Primary", source="1f-2a", url="/u", row=row, preview=False).preview is None
    stale = art_urls.title_art(_title(), "Primary", source="1f-2b", url="/u", row=row, preview=True)
    assert (stale.dominant, stale.preview, stale.width) == (None, None, None)
    row.state = "failed"
    failed = art_urls.title_art(_title(), "Primary", source="1f-2a", url="/u", row=row, preview=True)
    assert (failed.rendition, failed.widths) == (None, [])
    assert not art_urls.needs_preparation(_title(), "Primary", "1f-2a", row)


def test_avif_sources_get_no_rendition_and_no_preparation() -> None:
    art = art_urls.title_art(_title(path="movies/Dune/poster.avif"), "Primary", source="1f-2a", url="/u", row=None, preview=True)
    assert (art.rendition, art.widths) == (None, [])
    assert not art_urls.needs_preparation(_title(path="movies/Dune/poster.avif"), "Primary", "1f-2a", None)


def test_logo_never_carries_preview_or_colours() -> None:
    title = MediaTitle(id=TITLE_ID, type="movie", key="k", name="Dune", images={"Logo": {"path": "logo.png", "tag": "9"}})
    key = art_urls.source_key(TITLE_ID, "Logo", "9")
    row = SimpleNamespace(source_key=key, state="ready", width=800, height=310, preview=b"x", preview_type="image/webp", dominant="#000000", accent="#000000")
    art = art_urls.title_art(title, "Logo", source="9", url="/u", row=row, preview=True)
    assert (art.widths, art.preview, art.dominant, art.accent, art.width) == ([600], None, None, None, 800)


def test_jpeg_extension_when_set() -> None:
    art_urls.set_extension("jpg")
    assert art_urls.title_art(_title(), "Primary", source="1f-2a", url="/u", row=None, preview=True).rendition.endswith("-{w}.jpg")
    with pytest.raises(ValueError):
        art_urls.set_extension("png")


def test_enqueue_is_fifo_deduplicated_bounded_and_wakes(monkeypatch) -> None:  # noqa: ANN001
    woken = []
    monkeypatch.setattr(art_urls, "on_enqueue", lambda: woken.append(1))
    monkeypatch.setattr(art_urls, "MAX_PENDING", 3)
    art_urls.enqueue([("a", "Primary"), ("b", "Primary"), ("a", "Primary"), ("c", "Logo"), ("d", "Logo")])
    assert art_urls.take(10) == [("a", "Primary"), ("b", "Primary"), ("c", "Logo")]
    assert woken == [1]
    art_urls.enqueue([])
    assert woken == [1]


def test_art_support_rows_round_trip(db_factory) -> None:  # noqa: ANN001
    from art_support import artwork_row, give_art
    from discovery_support import add_movie
    from app.services.titles import image_url, title_image_source

    with db_factory() as session:
        movie = add_movie(session, TITLE_ID, "Dune")
        give_art(movie, "Primary")
        row = artwork_row(session, movie, "Primary")
        source = title_image_source(movie, "Primary")
        art = art_urls.title_art(movie, "Primary", source=source, url=image_url(movie, "Primary"), row=session.get(TitleArtwork, (TITLE_ID, "Primary")), preview=True)
        assert art.dominant == "#2a3b4c" and art.preview.startswith("data:image/webp;base64,")
        assert not art_urls.needs_preparation(movie, "Primary", source, row)
        stale = artwork_row(session, movie, "Backdrop", stale=True) if give_art(movie, "Backdrop") else None
        assert art_urls.needs_preparation(movie, "Backdrop", title_image_source(movie, "Backdrop"), stale)
