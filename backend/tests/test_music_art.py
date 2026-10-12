"""Library gallery T3: album and artist art: the embedded source, square renditions and the pass order."""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture

from app import db as db_module
from app.models import MediaTitle, TitleArtwork
from app.services import art_urls, renditions
from app.services.titles import title_image_bytes, title_image_source
from art_support import ART_ROOT_ID, add_art_title, fake_calls, seed_art_root, use_fake_ffmpeg
from discovery_support import add_title
from test_audio_tags import PNG, music_fixture, needs_ffmpeg


def uid(n: int) -> str:
    return str(uuid.UUID(int=n))


ALBUM_ID, ARTIST_ID, MOVIE_ID, SHOW, SEASON, EPISODE = (uid(0x710 + n) for n in range(6))
CROP = "crop='min(iw,ih)':'min(iw,ih)'"


@pytest.fixture(autouse=True)
def fresh_urls(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_secret", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(art_urls, "on_enqueue", None)
    renditions.has_libwebp.cache_clear()


@pytest.fixture
def media(tmp_path) -> Path:  # noqa: ANN001
    root = tmp_path.resolve() / "media"
    seed_art_root(root)
    return root


def add_album(relative: str) -> None:
    """An album under seed_art_root whose Primary is the picture inside ``relative`` (a track)."""
    with db_module.SessionLocal() as session:
        album = session.get(MediaTitle, ALBUM_ID)
        if album is None:
            album = MediaTitle(id=ALBUM_ID, type="album", key="album:a/b", root_id=ART_ROOT_ID, name="B")
            session.add(album)
        album.images = {"Primary": {"embedded": relative, "tag": "1:2"}}
        session.commit()


def album_bytes() -> tuple[str, bytes]:
    with db_module.SessionLocal() as session:
        return title_image_bytes(session, session.get(MediaTitle, ALBUM_ID), "Primary", renditions.offline_artwork())


def test_album_and_artist_posters_are_square_at_three_widths() -> None:
    assert art_urls.widths("album", "Primary") == art_urls.widths("artist", "Primary") == (240, 480, 960)
    assert art_urls.widths("movie", "Primary") == (240, 480) and art_urls.widths("album", "Backdrop") == (960, 1920)
    assert 960 in art_urls.ALLOWED_WIDTHS["Primary"]
    assert art_urls.square("album", "Primary") and not art_urls.square("movie", "Primary") and not art_urls.square("album", "Backdrop")
    embedded = {"embedded": "Music/A/B/01.flac", "tag": "10:20"}
    assert art_urls.supported(embedded) and art_urls.local(embedded)
    assert art_urls.local({"path": "x.jpg"}) and not art_urls.local({"tmdb": "/x.jpg"})
    assert title_image_source(MediaTitle(id="a", type="album", images={"Primary": embedded}), "Primary") == "10:20"
    assert title_image_source(MediaTitle(id="a", type="album", images={"Primary": {"embedded": "t.flac"}}), "Primary") == "t.flac"


@needs_ffmpeg
def test_an_embedded_cover_is_read_from_its_track(media) -> None:  # noqa: ANN001
    fixture = music_fixture()
    cover = fixture.cover_image(media.parent / "front.jpg", "200x200")
    fixture.write_track(media / "Music" / "01.flac", {"album": "B"}, cover=cover)
    add_album("Music/01.flac")
    assert album_bytes() == ("image/jpeg", cover.read_bytes())


@needs_ffmpeg
def test_a_symlinked_or_escaping_track_is_refused(media) -> None:  # noqa: ANN001
    fixture = music_fixture()
    outside = fixture.write_track(media.parent / "outside.flac", {"album": "B"}, cover=fixture.cover_image(media.parent / "front.jpg", "200x200"))
    (media / "link.flac").symlink_to(outside)
    for relative in ("link.flac", "../outside.flac"):
        add_album(relative)
        with pytest.raises(FileNotFoundError):
            album_bytes()


@needs_ffmpeg
def test_only_a_jpeg_or_png_within_the_cap_is_served(media, monkeypatch) -> None:  # noqa: ANN001
    track = music_fixture().write_track(media / "Music" / "01.flac", {"album": "B"})
    audio = FLAC(track)
    picture = Picture()
    picture.type, picture.mime, picture.data = 3, "image/gif", b"GIF89a" + b"\x00" * 10
    audio.add_picture(picture)
    audio.save()
    add_album("Music/01.flac")
    with pytest.raises(FileNotFoundError):
        album_bytes()
    audio.clear_pictures()
    picture.mime, picture.data = "image/png", PNG
    audio.add_picture(picture)
    audio.save()
    assert album_bytes() == ("image/png", PNG)
    monkeypatch.setattr("app.services.titles.MAX_IMAGE_BYTES", len(PNG) - 1)
    with pytest.raises(FileNotFoundError):
        album_bytes()


def test_the_pass_prepares_album_covers_after_posters_and_artists_with_the_rest(media) -> None:  # noqa: ANN001
    add_art_title(media, MOVIE_ID)
    add_art_title(media, ALBUM_ID, "album", size=(300, 200))
    add_art_title(media, ARTIST_ID, "artist")
    with db_module.SessionLocal() as session:
        add_title(session, SHOW, "series", "Show")
        add_title(session, SEASON, "season", "Season 1", parent_id=SHOW, index=1)
        session.commit()
    add_art_title(media, EPISODE, "episode", parent_id=SEASON)
    with db_module.SessionLocal() as session:
        found = renditions.scan(session)
    assert found.order == [(MOVIE_ID, "Primary"), (ALBUM_ID, "Primary"), (EPISODE, "Primary"), (ARTIST_ID, "Primary")]


@needs_ffmpeg
def test_the_pass_renders_an_embedded_cover_centre_cropped(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    fixture = music_fixture()
    fixture.write_track(media / "Music" / "01.flac", {"album": "B"}, cover=fixture.cover_image(tmp_path / "front.jpg", "300x200"))
    add_album("Music/01.flac")
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    assert renditions.ArtworkRenditions().run_once() is True
    with db_module.SessionLocal() as session:
        row = session.get(TitleArtwork, (ALBUM_ID, "Primary"))
    assert (row.state, row.width, row.height) == ("ready", 200, 200)
    graph = next(arg for arg in fake_calls(log)[0]["argv"] if arg.startswith("[0:v]split"))
    assert graph.count(CROP) == 6  # w240, w480, w960, two previews, colours
    assert "scale='min(iw,960)':'min(iw,960)':flags=lanczos" in graph


def test_posters_and_movies_are_never_cropped(tmp_path) -> None:  # noqa: ANN001
    argv, _paths = renditions.ffmpeg_command("ffmpeg", "image/jpeg", tmp_path, widths=(240, 480), ext="webp", preview=True, colours=True)
    graph = argv[argv.index("-filter_complex") + 1]
    assert CROP not in graph and "scale='min(iw,240)':-2:flags=lanczos" in graph


@needs_ffmpeg
def test_square_renditions_are_exactly_one_to_one(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    ffmpeg = shutil.which("ffmpeg")
    monkeypatch.setattr(art_urls, "_extension", "webp" if renditions.has_libwebp(ffmpeg) else "jpg")
    for size in ("300x200", "301x201", "1200x1000"):
        source = tmp_path / f"{size}.png"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", f"color=c=0x356258:size={size}",
                        "-frames:v", "1", str(source)], check=True, timeout=60)
        rendered = renditions.render(source.read_bytes(), "image/png", title_type="album", image_type="Primary", timeout=20, ffmpeg=ffmpeg)
        side = min(int(value) for value in size.split("x"))
        assert (rendered.width, rendered.height) == (side, side), size
        for width, path in rendered.files.items():
            assert renditions.image_size(path.read_bytes()) == (min(width, side), min(width, side)), (size, width)
        renditions.discard(rendered)
