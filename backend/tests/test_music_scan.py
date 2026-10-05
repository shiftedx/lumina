"""Library gallery T3: music classification, the tag cache and album and artist titles."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.audio_tags import AudioTags
from app.services.local_metadata import describe

ALBUM_ONE = AudioTags(title="Song One", artist="Artist A", album_artist="Artist A", album="Album One", track=1, year=2019, genres=("Folk",))


def described(tmp_path: Path, target: str, others: tuple[str, ...] = (), tags: dict[str, AudioTags] | None = None,
              contents: dict[str, str] | None = None) -> dict:
    """describe() one file of a synthetic root with a fake tag reader (root-relative path -> AudioTags)."""
    root = tmp_path / "media"
    for relative in (target, *others, *(contents or {})):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((contents or {}).get(relative, "x"), encoding="utf-8")
    parts = tuple(target.split("/"))
    siblings = frozenset(p.name for p in root.joinpath(*parts[:-1]).iterdir() if p.is_file() and not p.is_symlink())
    return describe(root, parts, siblings, {}, audio_tags=lambda found: (tags or {}).get("/".join(found)))


def chain(result: dict) -> list[tuple[str, str]]:
    return [(spec["type"], spec["key"]) for spec in result["titles"]]


def test_a_tagged_artist_album_layout_makes_an_artist_and_an_album(tmp_path) -> None:  # noqa: ANN001
    target = "Music/Artist A/Album One (2019)/01 - Song One.flac"
    result = described(tmp_path, target, ("Music/Artist A/Album One (2019)/cover.jpg", "Music/Artist A/folder.jpg"), {target: ALBUM_ONE})
    assert result["metadata"]["lumina_import_kind"] == "track"
    assert chain(result) == [("artist", "artist:artist a"), ("album", "album:artist a/album one")]
    artist, album = result["titles"]
    assert artist["fields"]["path"] == {"name": "Artist A"}
    assert artist["fields"]["nfo"]["images.Primary"]["path"] == "Music/Artist A/folder.jpg"
    assert album["fields"]["path"] == {"name": "Album One", "year": 2019}
    assert album["fields"]["nfo"]["genres"] == ["Folk"]
    assert album["fields"]["nfo"]["images.Primary"]["path"] == "Music/Artist A/Album One (2019)/cover.jpg"
    assert (result["title"], result["uploader"], result["playlist_name"]) == ("Song One", "Artist A", "Album One")
    metadata = result["metadata"]
    assert (metadata["track"], metadata["artist"], metadata["album_artist"], metadata["album"]) == ("Song One", "Artist A", "Artist A", "Album One")
    assert (metadata["track_number"], metadata["release_year"], metadata["genre"], metadata["lumina_title_source"]) == (1, 2019, "Folk", "sidecar")
    assert "disc_number" not in metadata  # None values are dropped


def test_a_disc_folder_belongs_to_the_album_above_it_and_an_embedded_picture_is_its_cover(tmp_path) -> None:  # noqa: ANN001
    opening, closing = "Music/Artist A/Album Two/CD1/01 - Opening.flac", "Music/Artist A/Album Two/CD2/01 - Closing.flac"
    tags = {
        opening: AudioTags(title="Opening", album="Album Two", album_artist="Artist A", has_picture=True),
        closing: AudioTags(title="Closing", album="Album Two", album_artist="Artist A"),
    }
    result = described(tmp_path, closing, (opening,), tags)
    assert chain(result) == [("artist", "artist:artist a"), ("album", "album:artist a/album two")]
    assert result["metadata"]["disc_number"] == 2  # from the CD2 folder
    status = (tmp_path / "media" / opening).stat()
    assert result["titles"][1]["fields"]["nfo"]["images.Primary"] == {"embedded": opening, "tag": f"{status.st_size}:{status.st_mtime_ns}"}


def test_a_folder_image_in_any_disc_folder_wins_over_embedded_art(tmp_path) -> None:  # noqa: ANN001
    track = "Music/Artist A/Album Two/CD1/01 - Opening.flac"
    result = described(tmp_path, track, ("Music/Artist A/Album Two/CD2/front.png",), {track: AudioTags(album="Album Two", has_picture=True)})
    assert result["titles"][1]["fields"]["nfo"]["images.Primary"]["path"] == "Music/Artist A/Album Two/CD2/front.png"
    assert result["metadata"]["album_artist"] == "Artist A"  # the folder above the album folder


def test_a_compilation_is_one_various_artists_album_whose_tracks_keep_their_artists(tmp_path) -> None:  # noqa: ANN001
    first, second = "Music/Compilations/Mix/01 - First.flac", "Music/Compilations/Mix/02 - Second.flac"
    tags = {
        first: AudioTags(title="First", artist="Singer B", album="Mix", compilation=True),
        second: AudioTags(title="Second", artist="Singer C", album="Mix", compilation=True),
    }
    results = [described(tmp_path, target, (first, second), tags) for target in (first, second)]
    assert {tuple(chain(result)) for result in results} == {(("artist", "artist:various artists"), ("album", "album:various artists/mix"))}
    assert [(r["metadata"]["artist"], r["uploader"], r["metadata"]["album_artist"]) for r in results] == [
        ("Singer B", "Singer B", "Various Artists"), ("Singer C", "Singer C", "Various Artists"),
    ]


def test_an_untagged_album_is_named_by_its_folders(tmp_path) -> None:  # noqa: ANN001
    result = described(tmp_path, "Music/Untagged Artist/Untagged Album (2001)/01 Intro.mp3")
    assert chain(result) == [("artist", "artist:untagged artist"), ("album", "album:untagged artist/untagged album")]
    assert result["titles"][1]["fields"]["path"] == {"name": "Untagged Album", "year": 2001}
    assert (result["title"], result["metadata"]["track_number"], result["metadata"]["lumina_title_source"]) == ("Intro", 1, "filename")


def test_album_nfo_fills_what_the_tags_leave_out(tmp_path) -> None:  # noqa: ANN001
    result = described(tmp_path, "Music/Björk/Homogenic/03 - Jóga.flac", contents={
        "Music/Björk/Homogenic/album.nfo": "<album><title>Homogenic</title><albumartist>Björk Guðmundsdóttir</albumartist><year>1997</year></album>",
    })
    assert chain(result) == [("artist", "artist:björk guðmundsdóttir"), ("album", "album:björk guðmundsdóttir/homogenic")]
    assert (result["metadata"]["release_year"], result["title"], result["metadata"]["track_number"]) == (1997, "Jóga", 3)


@pytest.mark.parametrize(("target", "tags", "kind", "keys"), [
    ("Music/loose.mp3", None, "unclassified", []),  # depth 1 and no album tag
    ("Music/single.mp3", AudioTags(album="Single", artist="Solo"), "track", [("artist", "artist:solo"), ("album", "album:solo/single")]),
    ("single.mp3", AudioTags(album="Single"), "track", [("artist", "artist:unknown artist"), ("album", "album:unknown artist/single")]),
    ("Movies/Heat (1995)/Heat Main Title.mp3", None, "unclassified", []),  # beside a video: a soundtrack file, not an album
])
def test_what_counts_as_a_track(tmp_path, target, tags, kind, keys) -> None:  # noqa: ANN001
    others = ("Movies/Heat (1995)/Heat (1995).mkv",) if target.startswith("Movies/") else ()
    result = described(tmp_path, target, others, {target: tags} if tags else None)
    assert (result["metadata"]["lumina_import_kind"], chain(result)) == (kind, keys)


@pytest.mark.parametrize(("target", "others", "kind", "keys"), [
    ("TV/Omega/theme.mp3", ("TV/Omega/Season 01/Omega S01E01.mkv",), "extra", [("series", "TV/Omega")]),
    ("TV/Omega/theme-music/song.mp3", ("TV/Omega/Season 01/Omega S01E01.mkv",), "extra", [("series", "TV/Omega")]),
    ("Movies/Heat (1995)/theme.mp3", ("Movies/Heat (1995)/Heat (1995).mkv",), "extra", [("movie", "Movies/Heat (1995)")]),
    ("Loose/Stuff/theme.mp3", (), "unclassified", []),
])
def test_theme_music_is_an_extra_of_its_folder_never_an_album(tmp_path, target, others, kind, keys) -> None:  # noqa: ANN001
    result = described(tmp_path, target, others, {target: AudioTags(album="Theme")})
    assert (result["metadata"]["lumina_import_kind"], chain(result)) == (kind, keys)
    assert result["extra_type"] == ("other" if kind == "extra" else None)


# ---- real scans ---------------------------------------------------------------------------------------------

import os  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app import db as db_module  # noqa: E402
from app.models import MediaTitle  # noqa: E402
from app.services import library_import  # noqa: E402
from test_audio_tags import music_fixture, needs_ffmpeg  # noqa: E402
from test_v1_titles import admin, library_items, media, scan, write  # noqa: E402,F401  (admin and media are fixtures)


def music_titles() -> dict[str, MediaTitle]:
    with db_module.SessionLocal() as db:
        rows = db.scalars(select(MediaTitle).where(MediaTitle.type.in_(("album", "artist")))).all()
        db.expunge_all()
    return {f"{row.type}:{row.name}": row for row in rows}


@needs_ffmpeg
def test_a_music_folder_becomes_albums_and_artists(admin, media) -> None:  # noqa: ANN001
    fixture = music_fixture()
    fixture.build_music(media / "Music")
    write(media / "TV" / "Show" / "Season 01" / "Show S01E01.mkv", b"e1")
    fixture.add_theme_music(media / "TV" / "Show")
    assert scan(admin)["state"] == "succeeded"
    titles = music_titles()
    assert sorted(titles) == [
        "album:Album One", "album:Album Two", "album:Mix", "album:Untagged Album",
        "artist:Artist A", "artist:Untagged Artist", "artist:Various Artists",
    ]
    assert {titles[f"album:{name}"].parent_id for name in ("Album One", "Album Two")} == {titles["artist:Artist A"].id}
    assert titles["album:Mix"].parent_id == titles["artist:Various Artists"].id
    assert titles["album:Untagged Album"].parent_id == titles["artist:Untagged Artist"].id
    album_one = titles["album:Album One"]
    assert (album_one.key, album_one.year, album_one.metadata_json["genres"]) == ("album:artist a/album one", 2019, ["Folk"])
    assert album_one.images["Primary"]["path"] == "Music/Artist A/Album One (2019)/cover.jpg"
    assert titles["album:Album Two"].images["Primary"]["embedded"] == "Music/Artist A/Album Two/CD1/01 - Opening.flac"
    assert "Primary" not in (titles["album:Mix"].images or {})
    items = library_items()
    assert {items[name].title_id for name in ("Song One", "Song Two", "Song Three")} == {album_one.id}
    assert (items["First"].uploader, items["First"].metadata_json["album_artist"]) == ("Singer B", "Various Artists")
    assert (items["Closing"].metadata_json["disc_number"], items["Intro"].kind) == (2, "track")
    assert "theme" not in items  # theme songs never reach the Library


@needs_ffmpeg
def test_an_unchanged_rescan_reads_no_tags_and_a_touched_file_rereads_only_itself(admin, media, monkeypatch) -> None:  # noqa: ANN001
    music_fixture().build_music(media / "Music")
    scan(admin)
    read: list[str] = []
    real = library_import.read_tags
    monkeypatch.setattr(library_import, "read_tags", lambda path: (read.append(path.name), real(path))[1])
    assert scan(admin)["counters"]["unchanged"] == 8
    assert read == []
    touched = media / "Music" / "Compilations" / "Mix" / "02 - Second.flac"
    status = touched.stat()
    os.utime(touched, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))
    assert scan(admin)["counters"]["updated"] == 1
    assert read == ["02 - Second.flac"]


@needs_ffmpeg
def test_stored_tags_stay_on_the_server(admin, media) -> None:  # noqa: ANN001
    music_fixture().build_music(media / "Music")
    scan(admin)
    item = library_items()["Song One"]
    stored = item.metadata_json["lumina_audio_tags"]
    assert (stored["album"], stored["track"], stored["fp"].count(":")) == ("Album One", 1, 2)
    assert "lumina_audio_tags" not in admin.get(f"/api/library/{item.id}").json()["metadata_json"]


def test_an_album_split_across_roots_is_one_album(admin, media) -> None:  # noqa: ANN001
    more = media.parent / "more"
    write(media / "Music" / "Artist" / "Album" / "01 - One.flac", b"one")
    write(more / "Artist" / "Album" / "02 - Two.flac", b"two")
    other = admin.post("/api/admin/storage/roots", json={"label": "More", "container_path": str(more), "mode": "external"}).json()["id"]
    assert scan(admin)["state"] == "succeeded"
    started = admin.post("/api/admin/imports", json={"root_id": other, "visibility": "private"})
    assert admin.get(started.json()["status_url"]).json()["state"] == "succeeded"
    titles = music_titles()
    assert sorted(titles) == ["album:Album", "artist:Artist"]
    items = library_items()
    assert items["One"].title_id == items["Two"].title_id == titles["album:Album"].id
    assert titles["album:Album"].key == "album:artist/album"  # global: never root-prefixed


def test_a_split_album_keeps_its_art_on_the_root_that_created_it(admin, media) -> None:  # noqa: ANN001
    more = media.parent / "more"
    write(media / "Music" / "Artist" / "Album" / "01 - One.flac", b"one")
    write(media / "Music" / "Artist" / "Album" / "cover.jpg", b"first")
    write(more / "Artist" / "Album" / "02 - Two.flac", b"two")
    write(more / "Artist" / "Album" / "cover.jpg", b"second")
    other = admin.post("/api/admin/storage/roots", json={"label": "More", "container_path": str(more), "mode": "external"}).json()["id"]
    for _ in range(2):  # rescans of both roots never flip the art between them
        assert scan(admin)["state"] == "succeeded"
        started = admin.post("/api/admin/imports", json={"root_id": other, "visibility": "private"})
        assert admin.get(started.json()["status_url"]).json()["state"] == "succeeded"
        album = music_titles()["album:Album"]
        assert album.root_id != other
        assert album.images["Primary"]["path"] == "Music/Artist/Album/cover.jpg"


def _at(year: int, month: int = 1) -> datetime:
    return datetime(year, month, 1)


def _stamp(path: Path, when: datetime) -> None:
    seconds = when.replace(tzinfo=UTC).timestamp()
    os.utime(path, (seconds, seconds))


def test_albums_are_dated_by_their_newest_track_not_by_scan_order(admin, media) -> None:  # noqa: ANN001
    alpha, zed = media / "Music" / "Artist" / "Alpha" / "01 - One.flac", media / "Music" / "Artist" / "Zed" / "01 - Two.flac"
    write(alpha, b"one")
    write(zed, b"two")
    write(zed.with_name("02 - Three.flac"), b"three")
    _stamp(alpha, _at(2020))  # scanned first, arrived first
    _stamp(zed, _at(2021))
    _stamp(zed.with_name("02 - Three.flac"), _at(2024))  # Zed's newest track
    assert scan(admin)["state"] == "succeeded"
    titles = music_titles()
    assert (titles["album:Alpha"].added_at, titles["album:Zed"].added_at, titles["artist:Artist"].added_at) == (_at(2020), _at(2024), _at(2024))

    _stamp(alpha, datetime.now(UTC).replace(tzinfo=None))  # replaced in place: never later
    write(zed.with_name("03 - Four.flac"), b"four")  # a new track: the album is later
    _stamp(zed.with_name("03 - Four.flac"), _at(2025, 6))
    assert scan(admin)["state"] == "succeeded"
    titles = music_titles()
    assert (titles["album:Alpha"].added_at, titles["album:Zed"].added_at, titles["artist:Artist"].added_at) == (_at(2020), _at(2025, 6), _at(2025, 6))
