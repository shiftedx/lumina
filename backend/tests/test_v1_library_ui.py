"""Library views (kind/source/group/sort), series/artist groups and media state."""
from datetime import datetime

from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, StorageRoot
from app.services.library import LibraryService, library_kind
from app.services.live_recording_adapters import recording_library_info

from tests.test_library_pagination import collect_all_pages, library_api  # noqa: F401  (fixture)


def _entry(item_id: str, metadata: dict, *, extractor: str = "Youtube", uploader: str | None = None, playlist: str | None = None,
           title: str | None = None, user_id: str = "member-1", visibility: str = "shared", status: str = "available", day: int = 1) -> LibraryItem:
    item = LibraryItem(
        id=item_id, user_id=user_id, visibility=visibility, extractor=extractor, remote_id=item_id,
        title=title or item_id, uploader=uploader, playlist_name=playlist, status=status,
        downloaded_at=datetime(2026, 3, day), created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
    )
    LibraryService._set_metadata(item, metadata)
    return item


def _episode(item_id: str, series: str, season: int, number: int, **kwargs) -> LibraryItem:
    return _entry(item_id, {"lumina_import_kind": "episode", "series": series, "season_number": season, "episode_number": number},
                  extractor="external_library", uploader=series, playlist=f"{series} · Season {season}", **kwargs)


def test_kind_is_derived_from_import_grouping_recording_and_media() -> None:
    assert library_kind({"lumina_import_kind": "movie", "ext": "mkv"}) == "movie"
    assert library_kind({"lumina_import_kind": "unclassified", "ext": "flac"}) == "audio"
    assert library_kind({"lumina_import_kind": "unclassified", "ext": "mkv"}) == "video"
    assert library_kind({"vcodec": "none", "acodec": "opus"}) == "audio"
    assert library_kind(recording_library_info("twitch:1", "https://twitch.tv/a", "/v/rec.mp4")) == "recording"


def test_library_series_and_unclassified(library_api) -> None:
    client, _engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _episode("e1", "Blue Harbor", 1, 1, day=1),
            _episode("e2", "Blue Harbor", 1, 2, day=2),
            _episode("e3", "Blue Harbor", 2, 1, day=3),
            _episode("e4", "Arcadia", 1, 1, day=4),
            # A friend's private episode never counts toward this member's groups.
            _episode("e5", "Arcadia", 1, 2, user_id="member-2", visibility="private", day=5),
            _entry("clip", {"lumina_import_kind": "unclassified", "ext": "mkv"}, extractor="external_library", day=6),
            _entry("movie", {"lumina_import_kind": "movie", "release_year": 1999}, extractor="external_library", day=7),
            _entry("saved", {"height": 1080}, day=8),
        ])

    groups = client.get("/api/library/groups", params={"kind": "episode"}).json()
    assert [(g["name"], g["items"], g["subgroups"]) for g in groups] == [("Arcadia", 1, 1), ("Blue Harbor", 3, 2)]

    drill = collect_all_pages(client, {"kind": "episode", "group": "Blue Harbor", "limit": 2})
    assert sorted(drill) == ["e1", "e2", "e3"]
    # Ambiguous imports stay plain videos (no invented episode data); movies are their own view.
    assert collect_all_pages(client, {"kind": "video"}) == ["saved", "clip"]
    assert collect_all_pages(client, {"kind": "movie"}) == ["movie"]
    clip = next(item for item in client.get("/api/library").json()["items"] if item["id"] == "clip")
    assert clip["kind"] == "video" and "season_number" not in clip["metadata_json"]


def test_music_groups_saved_audio_and_imported_tracks_by_artist(library_api) -> None:
    client, _engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            _entry("t1", {"lumina_import_kind": "track", "track_number": 1}, extractor="external_library", uploader="Nova", playlist="Tides"),
            _entry("t2", {"lumina_import_kind": "track", "track_number": 2}, extractor="external_library", uploader="Nova", playlist="Tides"),
            _entry("a1", {"vcodec": "none"}, uploader="Nova"),
            _entry("v1", {"height": 720}, uploader="Nova"),
        ])
    groups = client.get("/api/library/groups", params={"kind": "music"}).json()
    assert [(g["name"], g["items"], g["subgroups"]) for g in groups] == [("Nova", 3, 1)]
    assert sorted(collect_all_pages(client, {"kind": "music", "source": "imported"})) == ["t1", "t2"]
    assert collect_all_pages(client, {"kind": "music", "source": "saved"}) == ["a1"]
    assert sorted(collect_all_pages(client, {"source": "youtube"})) == ["a1", "v1"]
    assert client.get("/api/library", params={"kind": "bogus"}).status_code == 422


def test_title_sort_pages_case_insensitively_without_gaps(library_api) -> None:
    client, _engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([_entry(f"i{n}", {}, title=title, day=n + 1) for n, title in enumerate(["beta", "Alpha", "gamma", "alpha", "Delta"])])
    ids = collect_all_pages(client, {"sort": "title", "limit": 2})
    assert ids == ["i1", "i3", "i0", "i4", "i2"]
    assert client.get("/api/library", params={"sort": "title", "cursor": "bad"}).status_code == 400


def test_library_mount_offline_not_empty(library_api) -> None:
    client, _engine, session_factory, *_ = library_api
    with session_factory.begin() as session:
        session.add_all([
            StorageRoot(id="ext", label="Media", path="/mnt/media", mode="external", observation={"state": "offline"}),
            StorageRoot(id="vault", label="Vault", path="/vault", mode="managed", observation={"state": "available"}),
            MediaArtifact(id="a-ext", root_id="ext", relative_path="Movie (1999).mkv", ownership="external"),
            MediaArtifact(id="a-q", root_id="vault", relative_path="x.mp4", ownership="managed", lifecycle="quarantined"),
            MediaArtifact(id="a-ok", root_id="vault", relative_path="y.mp4", ownership="managed"),
            LibraryItemArtifact(library_item_id="movie", artifact_id="a-ext"),
            LibraryItemArtifact(library_item_id="deleted", artifact_id="a-q"),
            LibraryItemArtifact(library_item_id="ok", artifact_id="a-ok"),
            _entry("movie", {"lumina_import_kind": "movie"}, extractor="external_library", day=3),
            _entry("deleted", {"height": 720}, status="missing", day=2),
            _entry("ok", {"height": 720}, day=1),
        ])
    items = {item["id"]: item for item in client.get("/api/library").json()["items"]}
    assert items["movie"]["status"] == "available" and items["movie"]["media_state"] == "offline"
    assert items["deleted"]["media_state"] == "quarantined"
    assert items["ok"]["media_state"] == "available"
    assert collect_all_pages(client, {"status": "missing"}) == ["deleted"]
    assert collect_all_pages(client, {"status": "available"}) == ["movie", "ok"]  # a chapter's slice never spends its limit on deleted files
    assert client.get("/api/library/movie").json()["media_state"] == "offline"
