"""Shared title-tree builders for the Lumina title and Jellyfin API tests.

Every id is a uuid (Jellyfin ids are uuid hex). ``seed_tree`` builds, under one external root:
  Show (Specials E1, Season 1 E1–E2, Season 2 E1; alice's shared files; a poster),
  Movie (1080p + 4K versions, a trailer extra, in the "Saga" boxset; a poster),
  Secret Show (bob's private file), and YouTube videos: two by "Chan" (2024, 2025, shared)
  and one by "BobChan" (bob's private).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import DeviceToken, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot
from app.security import session_digest
from app.services.library import LibraryService
from support import make_user, seed_app_settings


def uid(n: int) -> str:
    return str(uuid.UUID(int=n))


ALICE, BOB = uid(0xA11CE), uid(0xB0B)
ROOT = uid(900)
SERIES, SPECIALS, SEASON1, SEASON2 = uid(1), uid(2), uid(3), uid(4)
S0E1, S1E1, S1E2, S2E1 = uid(10), uid(11), uid(12), uid(13)
MOVIE, BOXSET = uid(20), uid(40)
SECRET_SERIES, SECRET_SEASON, SECRET_EPISODE = uid(30), uid(31), uid(32)
FILE = {S0E1: uid(110), S1E1: uid(111), S1E2: uid(112), S2E1: uid(113), SECRET_EPISODE: uid(132)}
MOVIE_1080, MOVIE_4K, TRAILER = uid(120), uid(121), uid(122)
CHANNEL_OLD, CHANNEL_NEW, CHANNEL_PRIVATE = uid(150), uid(151), uid(152)
T0 = datetime(2026, 9, 1)
POSTER = b"\x89PNG\r\n\x1a\nposter-bytes"
SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
SIDECAR = {"filename": "Movie (2020) - 4K.en.forced.srt", "language": "en", "forced": True, "hearing_impaired": False, "default": False, "format": "srt"}
MOVIE_4K_PROBE = {
    "container": "matroska,webm", "webm_suffix": False, "video_codec": "hevc", "audio_codec": "truehd",
    "width": 3840, "height": 2160, "duration": 7200.0, "audio_tracks": 1, "subtitles": ["subrip"],
    "streams": [
        {"index": 0, "type": "video", "codec": "hevc", "profile": "Main 10", "width": 3840, "height": 2160,
         "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084", "frame_rate": 23.976, "default": True, "forced": False},
        {"index": 1, "type": "audio", "codec": "truehd", "language": "eng", "channels": 8, "channel_layout": "7.1",
         "sample_rate": 48000, "default": True, "forced": False},
        {"index": 2, "type": "subtitle", "codec": "subrip", "language": "eng", "title": "English", "default": False, "forced": False},
    ],
}
ALICE_TOKEN, BOB_TOKEN = "alice-device-token-for-tests", "bob-device-token-for-tests"


def add_file(
    session: Session, root: Path, relative: str, *, item_id: str, title_id: str | None, owner: str, title: str,
    visibility: str = "shared", kind: str = "episode", extra_type: str | None = None, probe: dict | None = None,
    metadata: dict | None = None, created_at: datetime = T0, duration: int | None = 1500, **fields: Any,
) -> LibraryItem:
    """A 4 KiB file registered as an artifact under ROOT and linked to a new Library item."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 4096)
    info = path.stat()
    artifact = MediaArtifact(
        id=str(uuid.uuid4()), root_id=ROOT, relative_path=relative, ownership="external", lifecycle="available",
        size=info.st_size, mtime_ns=info.st_mtime_ns,
        probe=None if probe is None else {"fingerprint": f"{info.st_size}:{info.st_mtime_ns}", **probe},
    )
    metadata = metadata or {}
    item = LibraryItem(
        id=item_id, user_id=owner, visibility=visibility, title=title, file_size=info.st_size, duration=duration,
        title_id=title_id, extra_type=extra_type, kind=kind, status="available", created_at=created_at,
        metadata_json=metadata, metadata_summary=LibraryService.summarize_metadata(metadata), **fields,
    )
    session.add_all([artifact, item, LibraryItemArtifact(library_item_id=item_id, artifact_id=artifact.id)])
    return item


def seed_tree(session: Session, root: Path) -> None:
    """The fixture library described in the module docstring. ``root`` must be a resolved path; the caller commits."""
    for folder in ("Show", "Movie (2020)"):
        (root / folder).mkdir(parents=True, exist_ok=True)
        (root / folder / "poster.png").write_bytes(POSTER)
    session.add(StorageRoot(id=ROOT, label="Media", path=str(root), mode="external", enabled=True, observation={"state": "available"},
                            identity=str(root.stat().st_dev)))  # mounted and the same drive: is_online is True

    def title(title_id: str, kind: str, key: str, name: str, **fields: Any) -> MediaTitle:
        return MediaTitle(id=title_id, type=kind, key=f"{ROOT}:{key}", root_id=ROOT, name=name, **fields)

    session.add_all([
        title(SERIES, "series", "Show", "Show", year=2019, provider_ids={"Tmdb": "100"}, created_at=T0,
              images={"Primary": {"path": "Show/poster.png", "tag": "1"}}, metadata_json={"overview": "A show.", "genres": ["Drama"]}),
        title(SPECIALS, "season", "Show#s0", "Specials", parent_id=SERIES, index_number=0),
        title(SEASON1, "season", "Show#s1", "Season 1", parent_id=SERIES, index_number=1),
        title(SEASON2, "season", "Show#s2", "Season 2", parent_id=SERIES, index_number=2),
        title(S0E1, "episode", "Show#s0e1", "Making Of", parent_id=SPECIALS, index_number=1, added_at=T0),
        title(S1E1, "episode", "Show#s1e1", "Pilot", parent_id=SEASON1, index_number=1, added_at=T0 + timedelta(hours=1)),
        title(S1E2, "episode", "Show#s1e2", "Second", parent_id=SEASON1, index_number=2, added_at=T0 + timedelta(hours=2)),
        title(S2E1, "episode", "Show#s2e1", "Return", parent_id=SEASON2, index_number=1, added_at=T0 + timedelta(hours=3)),
        MediaTitle(id=BOXSET, type="boxset", key="set:saga", name="Saga"),
        title(MOVIE, "movie", "Movie (2020)", "Movie", year=2020, boxset_id=BOXSET, created_at=T0 + timedelta(days=2),
              provider_ids={"Tmdb": "603", "Imdb": "tt0133093"}, images={"Primary": {"path": "Movie (2020)/poster.png"}},
              metadata_json={"overview": "A heist.", "community_rating": 7.9, "premiered": "2020-03-01", "genres": ["Action"], "studios": ["Acme"]}),
        title(SECRET_SERIES, "series", "Secret Show", "Secret Show"),
        title(SECRET_SEASON, "season", "Secret Show#s1", "Season 1", parent_id=SECRET_SERIES, index_number=1),
        title(SECRET_EPISODE, "episode", "Secret Show#s1e1", "Hidden", parent_id=SECRET_SEASON, index_number=1),
    ])
    episodes = [(S0E1, "Show/Specials/Show S00E01.mkv"), (S1E1, "Show/Season 01/Show S01E01.mkv"),
                (S1E2, "Show/Season 01/Show S01E02.mkv"), (S2E1, "Show/Season 02/Show S02E01.mkv")]
    for hour, (episode, relative) in enumerate(episodes):
        add_file(session, root, relative, item_id=FILE[episode], title_id=episode, owner=ALICE,
                 title=relative.rsplit("/", 1)[1].removesuffix(".mkv"), created_at=T0 + timedelta(hours=hour))
    add_file(session, root, "Secret Show/Season 01/Secret S01E01.mkv", item_id=FILE[SECRET_EPISODE],
             title_id=SECRET_EPISODE, owner=BOB, visibility="private", title="Hidden")
    add_file(session, root, "Movie (2020)/Movie (2020) - 1080p.mp4", item_id=MOVIE_1080, title_id=MOVIE, owner=ALICE,
             title="Movie · 1080p", kind="movie", duration=7200, created_at=T0 + timedelta(days=2))
    add_file(session, root, "Movie (2020)/Movie (2020) - 4K.mkv", item_id=MOVIE_4K, title_id=MOVIE, owner=ALICE,
             title="Movie · 4K", kind="movie", duration=7200, probe=MOVIE_4K_PROBE, metadata={"lumina_subtitles": [SIDECAR]},
             created_at=T0 + timedelta(days=2, hours=1))
    (root / "Movie (2020)" / SIDECAR["filename"]).write_text(SRT)
    add_file(session, root, "Movie (2020)/trailers/Trailer.mp4", item_id=TRAILER, title_id=MOVIE, owner=ALICE,
             title="Trailer", kind="video", extra_type="trailer", duration=120)
    for item_id, name, owner, visibility, uploader, day, age in (
        (CHANNEL_OLD, "Old upload", ALICE, "shared", "Chan", "20240105", 300),
        (CHANNEL_NEW, "New upload", ALICE, "shared", "Chan", "20250210", 10),
        (CHANNEL_PRIVATE, "Bob vlog", BOB, "private", "BobChan", "20250301", 5),
    ):
        add_file(session, root, f"youtube/{item_id}.mp4", item_id=item_id, title_id=None, owner=owner, visibility=visibility,
                 title=name, kind="video", extractor="Youtube", uploader=uploader, duration=600,
                 metadata={"upload_date": day, "uploader": uploader}, created_at=T0 - timedelta(days=age))


def device_token(session: Session, user_id: str, token: str, *, device_id: str = "device-1") -> str:
    """A Jellyfin device token row as the Jellyfin auth route mints it (only the digest is stored)."""
    session.add(DeviceToken(
        id=str(uuid.uuid4()), user_id=user_id, kind="jellyfin", scope="write", token_digest=session_digest(token),
        device_id=device_id, device_name="Apple TV", client="Infuse-Direct", client_version="8.0",
    ))
    return token


def mediabrowser(token: str) -> dict[str, str]:
    """The header Infuse sends on every request."""
    return {"Authorization": f'MediaBrowser Client="Infuse-Direct", Device="Apple TV", DeviceId="device-1", Version="8.0", Token="{token}"'}


def jellyfin_household(root: Path, *, enabled: bool = True) -> None:
    """Seed conftest's per-test file-backed database (what SessionLocal and get_db use): members, tokens, tree, the API toggle."""
    from app import db as db_module
    from app.db import Base
    from app.services import library_search

    Base.metadata.create_all(bind=db_module.engine)
    library_search.ensure_search_index(db_module.engine)  # /Search/Hints and ?searchTerm run through the FTS index
    with db_module.SessionLocal() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        session.flush()
        for title_id in (SERIES, MOVIE, BOXSET, SECRET_SERIES):  # each cascades to its seasons/episodes
            library_search.index_title(session, session.get(MediaTitle, title_id))
        device_token(session, ALICE, ALICE_TOKEN)
        device_token(session, BOB, BOB_TOKEN, device_id="device-2")
        seed_app_settings(session, jellyfin_enabled=enabled)  # commits


# ---- Library gallery fixture; used read-only -----------------------
ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE, ANIME_MOVIE = uid(60), uid(61), uid(62), uid(63)
ANIME_FILE, ANIME_MOVIE_FILE = uid(160), uid(163)
ARTIST, ALBUM = uid(70), uid(71)
TRACKS = (uid(171), uid(172), uid(173))


def seed_gallery(session: Session, root: Path) -> None:
    """On top of ``seed_tree`` (same ``root``): an anime series "Show A" and an anime movie "Film" under Anime/, category
    anime as schema step 8 derives it, and the album "Album One" (2019) by "Artist A" with a cover.jpg and three shared
    tracks of alice's (disc 1: 1, 2; disc 2: 1). The caller commits."""
    cover = root / "Music" / "Artist A" / "Album One (2019)" / "cover.jpg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(POSTER)
    session.add_all([
        MediaTitle(id=ANIME_SERIES, type="series", key=f"{ROOT}:Anime/Show A", root_id=ROOT, name="Show A", year=2023,
                   category="anime", created_at=T0 + timedelta(days=3)),
        MediaTitle(id=ANIME_SEASON, type="season", key=f"{ROOT}:Anime/Show A#s1", root_id=ROOT, name="Season 1",
                   parent_id=ANIME_SERIES, index_number=1, category="anime"),
        MediaTitle(id=ANIME_EPISODE, type="episode", key=f"{ROOT}:Anime/Show A#s1e1", root_id=ROOT, name="Arrival",
                   parent_id=ANIME_SEASON, index_number=1, category="anime", added_at=T0 + timedelta(days=3)),
        MediaTitle(id=ANIME_MOVIE, type="movie", key=f"{ROOT}:Anime/Film (2020)", root_id=ROOT, name="Film", year=2020,
                   category="anime", created_at=T0 + timedelta(days=4)),
        MediaTitle(id=ARTIST, type="artist", key="artist:artist a", root_id=ROOT, name="Artist A"),
        MediaTitle(id=ALBUM, type="album", key="album:artist a/album one", root_id=ROOT, name="Album One", parent_id=ARTIST,
                   year=2019, images={"Primary": {"path": "Music/Artist A/Album One (2019)/cover.jpg", "tag": "1"}},
                   metadata_json={"genres": ["Folk"]}),
    ])
    add_file(session, root, "Anime/Show A/Season 01/Show A S01E01.mkv", item_id=ANIME_FILE, title_id=ANIME_EPISODE,
             owner=ALICE, title="Show A S01E01")
    add_file(session, root, "Anime/Film (2020)/Film (2020).mkv", item_id=ANIME_MOVIE_FILE, title_id=ANIME_MOVIE,
             owner=ALICE, title="Film", kind="movie", duration=5400)
    for (disc, number), item_id in zip(((1, 1), (1, 2), (2, 1)), TRACKS, strict=True):
        add_file(session, root, f"Music/Artist A/Album One (2019)/{disc}-{number:02d} Song.flac", item_id=item_id,
                 title_id=ALBUM, owner=ALICE, title=f"Song {disc}.{number}", kind="track", duration=180,
                 metadata={"artist": "Artist A", "album_artist": "Artist A", "album": "Album One",
                           "track_number": number, "disc_number": disc, "release_year": 2019})
