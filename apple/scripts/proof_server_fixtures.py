"""Synthetic media and catalog fixtures for the native simulator proof."""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

DIRECT_TITLE = "The Lantern Keepers’ Extraordinary Midnight Voyage"
CONVERTED_TITLE = "A Cartographer’s Long Road Beyond the Winter Mountains"
MATRIX_TITLE = "The Patient Observatory at the Edge of Morning"
AUDIO_ARTIST = "The Synthetic Lantern Ensemble"
AUDIO_ALBUM = "Signals from a Quiet Observatory"
AUDIO_TRACK = "First Light in the Listening Room"
AUDIO_SECOND_TRACK = "A Signal Answered at Dawn"
AUDIO_TRACK_DURATION_SECONDS = 90
SHARED_MUSIC_PLAYLIST = "Observatory household listening"
SYNTHETIC_LYRIC = "A lantern answers through the quiet night"
MUSIC_HOME_FIXTURE_ENV = "LUMINA_MUSIC_HOME_FIXTURE"
MUSIC_HOME_MEMBER_USERNAME = "music-home-member"
SHOW_TITLE = "The Observatory Signal Logs"
EPISODE_TITLE = "The First Signal"
SECOND_EPISODE_TITLE = "Morning Coordinates"
THIRD_EPISODE_TITLE = "The Relay Opens"
FOURTH_EPISODE_TITLE = "Signal in the Static"
SECOND_SEASON_EPISODE_TITLE = "The Signal Returns"
SERIES_ABOUT_FIRST_PARAGRAPH = (
    "Across three careful seasons, the Observatory Signal Logs follows the people who maintain a weather station "
    "built into a retired orbital relay. Each recovered transmission changes what the crew believes the station was "
    "meant to protect."
)
SERIES_ABOUT_FINAL_PARAGRAPH = (
    "The series moves between the quiet routines of calibration, the practical work of preserving damaged recordings, "
    "and the larger mystery carried by voices that arrive decades after their senders vanished. Its archive is a map, "
    "a workplace, and an invitation to listen more closely."
)
SERIES_ABOUT_MIDDLE_PARAGRAPH = (
    "Mira and her neighbors compare field notes with messages found in obsolete navigation beacons, discovering that "
    "the same constellation has been guiding strangers toward the relay for generations. Every episode adds a new "
    "fragment without erasing the uncertainty that makes the signal worth following."
)
SERIES_ABOUT_SOURCE = "Observatory Studio"
SERIES_ABOUT_FIRST_NOTE = "All transmission logs in this proof fixture are newly authored synthetic text."
SERIES_ABOUT_SECOND_NOTE = "Episode summaries remain distinct so the selected card owns its own context."
SERIES_OVERVIEW = "\n\n".join((
    SERIES_ABOUT_FIRST_PARAGRAPH,
    SERIES_ABOUT_MIDDLE_PARAGRAPH,
    SERIES_ABOUT_FINAL_PARAGRAPH,
    f"(Source: {SERIES_ABOUT_SOURCE})",
    "Notes:\n"
    f"- {SERIES_ABOUT_FIRST_NOTE}\n"
    f"- {SERIES_ABOUT_SECOND_NOTE}",
))
FIRST_EPISODE_OVERVIEW = "Mira traces a faint pulse to the relay's oldest antenna and finds a calibration note addressed to no one."
FIRST_EPISODE_SOURCE = "North Relay Archive"
FIRST_EPISODE_NOTE = "The antenna map was drawn for this disposable proof fixture."
SECOND_EPISODE_OVERVIEW = "A dawn transmission gives the crew new coordinates, but its final line points beyond the station's known charts."
SECOND_EPISODE_SOURCE = "Morning Signal Desk"
SECOND_EPISODE_NOTE = "The coordinate sequence is unique to the second episode fixture."
EPISODE_INTRO_END_SECONDS = 10
EPISODE_CREDITS_START_SECONDS = 75
AUTOPLAY_CREDITS_START_SECONDS = 10
AUTOPLAY_SERIES_ENV = "LUMINA_NATIVE_AUTOPLAY_SERIES"
AUTOPLAY_DISABLED_ENV = "LUMINA_NATIVE_AUTOPLAY_DISABLED"
PERFORMANCE_CATALOGUE_COUNT_ENV = "LUMINA_NATIVE_PERF_CATALOGUE_COUNT"
PERFORMANCE_SERIES_COUNT_ENV = "LUMINA_NATIVE_PERF_SERIES_COUNT"
MAX_PERFORMANCE_CATALOGUE_COUNT = 2_000
PERFORMANCE_POSTER_VARIANTS_ENV = "LUMINA_NATIVE_PERF_POSTER_VARIANTS"
MAX_PERFORMANCE_POSTER_VARIANTS = 256
PERFORMANCE_VIDEO_SIZE_ENV = "LUMINA_NATIVE_PERF_VIDEO_SIZE"
PERFORMANCE_VIDEO_SIZES = frozenset({"320x180", "1920x1080"})
PERFORMANCE_REQUEST_LOG_ENV = "LUMINA_NATIVE_PERF_REQUEST_LOG"
MAX_PERFORMANCE_REQUEST_LOG_BYTES = 4 * 1024 * 1024
ARTWORK_METRICS_PATH_ENV = "LUMINA_NATIVE_ARTWORK_METRICS_PATH"
MAX_ARTWORK_METRICS_BYTES = 1 * 1024 * 1024
REQUESTS_METRICS_ENV = "LUMINA_NATIVE_REQUESTS_METRICS"
METADATA_REFRESH_METRICS_ENV = "LUMINA_NATIVE_METADATA_REFRESH_METRICS"
COLLECTIONS_FIXTURE_ENV = "LUMINA_NATIVE_COLLECTIONS_FIXTURE"
FRANCHISE_COLLECTION_TITLE = "The Lantern Keepers Collection"
MANUAL_COLLECTION_TITLE = "Proof movie night"
SMART_COLLECTION_TITLE = "TMDB proof movies"
PLAYBACK_INFO_DELAY_ENV = "LUMINA_NATIVE_PLAYBACK_INFO_DELAY_SECONDS"
MAX_PLAYBACK_INFO_DELAY_SECONDS = 5
VIDEO_FORMAT_FAILURE_ENV = "LUMINA_NATIVE_VIDEO_FORMAT_FAILURE"
CLIENT_DEMUX_VIDEO_FORMAT_RECOVERY_ENV = "LUMINA_NATIVE_CLIENT_DEMUX_VIDEO_FORMAT_RECOVERY"
OVERSIZED_EPUB_ENV = "LUMINA_NATIVE_OVERSIZED_EPUB"
REQUEST_MOVIE_KEY = "movie:603"
REQUEST_MOVIE_TITLE = "The Matrix"
REQUEST_SHOW_KEY = "show:1399"
REQUEST_SHOW_TITLE = "Game of Thrones"
REQUEST_UNAVAILABLE_SHOW_KEY = "show:95396"
REQUEST_UNAVAILABLE_SHOW_TITLE = "Severance"
REQUEST_ANIME_KEY = "anime:154587"
REQUEST_ANIME_TITLE = "Frieren: Beyond Journey's End"
REQUEST_FRESH_ANIME_ID = 20003
REQUEST_FRESH_ANIME_KEY = f"anime:{REQUEST_FRESH_ANIME_ID}"
REQUEST_FRESH_ANIME_TITLE = "Orbiting Lanterns"
REQUEST_FRESH_ANIME_TMDB_ID = 209868
REQUEST_FRESH_ANIME_TVDB_ID = 424537


class _ProofCatalogPolicy:
    """A no-network policy for the disposable Requests upstream fixture."""

    def validate_url(self, value: str, resolved: Any = None) -> str:  # noqa: ARG002
        return value


class _ProofCatalogTransport:
    """Deterministic TMDB/AniList replies with enough distinct home cards for paging."""

    def __init__(self, fixtures: Path) -> None:
        from datetime import date, datetime, timedelta

        def load(name: str) -> dict[str, Any]:
            text = (fixtures / f"{name}.json").read_text(encoding="utf-8")
            air_time = int(datetime.combine(date.today(), datetime.min.time()).astimezone().timestamp()) + 20 * 3600
            return json.loads(text.replace("__AT__", str(air_time)))

        movie = load("tmdb_movie_603")
        show = load("tmdb_tv_1399")
        anime = load("anilist_browse")
        anime_detail = load("anilist_detail")
        frieren = {
            "id": 209867, "name": REQUEST_ANIME_TITLE, "original_name": "Sousou no Frieren",
            "genre_ids": [16, 10765], "original_language": "ja", "origin_country": ["JP"],
            "first_air_date": "2023-09-29", "overview": "An elf mage outlives her party.",
            "episode_run_time": [24], "status": "Returning Series", "poster_path": "/frieren.jpg",
            "backdrop_path": "/frieren-bd.jpg", "vote_average": 8.8, "vote_count": 2_000,
            "genres": [{"id": 16, "name": "Animation"}, {"id": 10765, "name": "Fantasy"}],
            "networks": [{"name": "Nippon Television"}], "created_by": [],
            "seasons": [{"season_number": 1, "name": "Season 1", "episode_count": 28,
                         "air_date": "2023-09-29", "poster_path": "/frieren-s1.jpg"}],
            "external_ids": {"tvdb_id": 424536}, "images": {"logos": []},
            "videos": {"results": [{"site": "YouTube", "key": "frierentrailer", "name": "Trailer", "type": "Trailer", "official": True}]},
            "credits": {"cast": [], "crew": []}, "content_ratings": {"results": []},
            "recommendations": {"results": []}, "similar": {"results": []},
        }
        fresh_anime = {
            "id": REQUEST_FRESH_ANIME_ID,
            "title": {"romaji": "Orbiting Lanterns", "english": REQUEST_FRESH_ANIME_TITLE, "native": "灯台軌道"},
            "format": "TV", "status": "FINISHED", "episodes": 12, "duration": 24,
            "season": "SPRING", "seasonYear": 2026, "startDate": {"year": 2026, "month": 4, "day": 3},
            "description": "A cartographer follows lanterns through an orbital archive.",
            "coverImage": {"extraLarge": "https://s4.anilist.co/orbiting-lanterns.jpg", "color": "#9fd7f0"},
            "bannerImage": "https://s4.anilist.co/orbiting-lanterns-banner.jpg", "averageScore": 84,
            "popularity": 40_000, "genres": ["Adventure", "Fantasy"],
            "studios": {"nodes": [{"name": "Lantern Studio", "isAnimationStudio": True}]},
            "nextAiringEpisode": None, "trailer": None, "isAdult": False,
        }
        fresh_anime_detail = {
            "data": {"Media": {**fresh_anime, "relations": {"edges": []}, "characters": {"edges": []},
                               "recommendations": {"nodes": []}}}
        }
        fresh_anime_tmdb = {
            "id": REQUEST_FRESH_ANIME_TMDB_ID, "name": REQUEST_FRESH_ANIME_TITLE,
            "original_name": "Orbiting Lanterns", "genre_ids": [16, 10765], "original_language": "ja",
            "origin_country": ["JP"], "first_air_date": "2026-04-03",
            "overview": "A cartographer follows lanterns through an orbital archive.", "episode_run_time": [24],
            "status": "Ended", "poster_path": "/orbiting-lanterns.jpg", "backdrop_path": "/orbiting-lanterns-bd.jpg",
            "vote_average": 8.4, "vote_count": 400, "genres": [{"id": 16, "name": "Animation"}, {"id": 10765, "name": "Fantasy"}],
            "networks": [{"name": "Lantern Studio"}], "created_by": [],
            "seasons": [{"season_number": 1, "name": "Season 1", "episode_count": 12,
                         "air_date": "2026-04-03", "poster_path": "/orbiting-lanterns-s1.jpg"}],
            "external_ids": {"tvdb_id": REQUEST_FRESH_ANIME_TVDB_ID}, "images": {"logos": []},
            "videos": {"results": []}, "credits": {"cast": [], "crew": []}, "content_ratings": {"results": []},
            "recommendations": {"results": []}, "similar": {"results": []},
        }

        def movie_card(identifier: int, title: str, year: int) -> dict[str, Any]:
            return {"id": identifier, "title": title, "original_title": title, "release_date": f"{year}-01-01",
                    "poster_path": f"/{identifier}.jpg", "backdrop_path": f"/{identifier}-bd.jpg",
                    "genre_ids": [28, 878], "vote_average": 7.4, "vote_count": 500}

        def show_card(identifier: int, title: str, year: int) -> dict[str, Any]:
            return {"id": identifier, "name": title, "original_name": title, "first_air_date": f"{year}-01-01",
                    "poster_path": f"/{identifier}.jpg", "backdrop_path": f"/{identifier}-bd.jpg",
                    "genre_ids": [18], "vote_average": 7.8, "vote_count": 500}

        movies = [movie_card(603, REQUEST_MOVIE_TITLE, 1999), movie_card(604, "The Matrix Reloaded", 2003),
                  movie_card(329865, "Arrival", 2016), movie_card(376867, "Moonlight", 2016)]
        shows = [show_card(1399, REQUEST_SHOW_TITLE, 2011), show_card(95396, "Severance", 2022),
                 show_card(125988, "Silo", 2023), show_card(83867, "Andor", 2022)]
        trending = [{**movies[0], "media_type": "movie"}, {**shows[0], "media_type": "tv"},
                    {**movies[2], "media_type": "movie"}, {**shows[1], "media_type": "tv"},
                    {**movies[3], "media_type": "movie"}, {**shows[2], "media_type": "tv"}]
        movie_detail = {**movie, "recommendations": {"results": movies[1:]}, "similar": {"results": movies[2:]}}
        show_detail = {**show, "recommendations": {"results": shows[1:]}, "similar": {"results": shows[2:]}}
        unavailable_show = {
            **shows[1], "overview": "A worker discovers a mystery beyond the office floor.",
            "episode_run_time": [50], "status": "Returning Series", "genres": [{"id": 18, "name": "Drama"}],
            "networks": [{"name": "Lumon"}], "created_by": [],
            "seasons": [
                {"season_number": 1, "name": "Season 1", "episode_count": 9, "air_date": "2022-02-18", "poster_path": "/95396.jpg"},
                {"season_number": 2, "name": "Season 2", "episode_count": 10, "air_date": "2025-01-17", "poster_path": "/95396.jpg"},
            ],
            "external_ids": {"tvdb_id": 371980}, "images": {"logos": []}, "videos": {"results": []},
            "credits": {"cast": [], "crew": []}, "content_ratings": {"results": []},
            "recommendations": {"results": shows[2:]}, "similar": {"results": shows[2:]},
        }
        self.tmdb = {
            "/3/genre/movie/list": load("tmdb_genres_movie"), "/3/genre/tv/list": load("tmdb_genres_tv"),
            "/3/trending/all/week": {"page": 1, "total_pages": 2, "results": trending},
            "/3/trending/movie/week": {"page": 1, "total_pages": 2, "results": movies},
            "/3/trending/tv/week": {"page": 1, "total_pages": 2, "results": shows},
            "/3/movie/popular": {"page": 1, "total_pages": 2, "results": movies},
            "/3/movie/upcoming": {"page": 1, "total_pages": 2, "results": movies[1:]},
            "/3/movie/top_rated": {"page": 1, "total_pages": 2, "results": movies[2:]},
            "/3/discover/movie": {"page": 1, "total_pages": 2, "results": movies},
            "/3/tv/popular": {"page": 1, "total_pages": 2, "results": shows},
            "/3/tv/on_the_air": {"page": 1, "total_pages": 2, "results": shows[1:]},
            "/3/tv/airing_today": {"page": 1, "total_pages": 2, "results": shows[2:]},
            "/3/movie/603": movie_detail, "/3/tv/1399": show_detail, "/3/tv/95396": unavailable_show, "/3/tv/209867": frieren,
            "/3/tv/95396/external_ids": {"id": 95396, "tvdb_id": 371980},
            "/3/tv/209867/external_ids": {"id": 209867, "tvdb_id": 424536},
            f"/3/tv/{REQUEST_FRESH_ANIME_TMDB_ID}": fresh_anime_tmdb,
            f"/3/tv/{REQUEST_FRESH_ANIME_TMDB_ID}/external_ids": {
                "id": REQUEST_FRESH_ANIME_TMDB_ID, "tvdb_id": REQUEST_FRESH_ANIME_TVDB_ID,
            },
            "/3/tv/37854/external_ids": load("tmdb_external_ids"),
            "/3/search/multi": {"page": 1, "total_pages": 2, "results": [{**frieren, "media_type": "tv"}, *trending]},
            "/3/search/tv": {"page": 1, "total_pages": 1, "results": [frieren]},
            "/3/search/movie": {"page": 1, "total_pages": 1, "results": movies},
        }
        upcoming = []
        for offset, title in enumerate(("Lanterns Beyond Tomorrow", "The Winter Cartographer", "The Last Observatory")):
            identifier = 700 + offset
            release = (date.today() + timedelta(days=14 + offset)).isoformat()
            card = {**movie_card(identifier, title, date.today().year + 1), "release_date": release}
            upcoming.append(card)
            self.tmdb[f"/3/movie/{identifier}"] = {**movie_detail, **card, "images": {"logos": []}}
        self.tmdb["/3/movie/upcoming"] = {"page": 1, "total_pages": 1, "results": upcoming}
        self.anilist = {
            "Browse": anime, "Detail": anime_detail, "Schedule": load("anilist_schedule"),
            "Genres": load("anilist_genres"), "Match": load("anilist_match"),
        }
        self.fresh_anime = fresh_anime
        self.fresh_anime_detail = fresh_anime_detail
        self.fresh_anime_tmdb = fresh_anime_tmdb

    def fetch(self, url: str, *, validate_redirect, headers=None, data=None):  # noqa: ANN001, ANN201
        from app.services.artwork import RemoteArtworkResponse

        validate_redirect(url)
        parts = urlsplit(url)
        if parts.hostname == "graphql.anilist.co":
            body = json.loads(data or b"{}")
            operation = body.get("query", "").split("(", 1)[0].removeprefix("query ").strip().split()[0]
            variables = body.get("variables") or {}
            if operation == "Detail" and variables.get("id") == REQUEST_FRESH_ANIME_ID:
                payload = self.fresh_anime_detail
            elif operation == "Match" and any(
                str(value).casefold() == REQUEST_FRESH_ANIME_TITLE.casefold() for value in variables.values()
            ):
                payload = {"data": {
                    f"m{key.removeprefix('s')}": (
                        self.fresh_anime if str(value).casefold() == REQUEST_FRESH_ANIME_TITLE.casefold() else None
                    )
                    for key, value in variables.items() if key.startswith("s")
                }}
            else:
                payload = self.anilist.get(operation, {"data": {}})
        else:
            # The fixture intentionally ignores filters and requested page except
            # the fresh anime's exact search title. That identity must remain
            # search-only so default rails retain their established card counts.
            query = (parse_qs(parts.query).get("query") or [""])[0].casefold()
            if query == REQUEST_FRESH_ANIME_TITLE.casefold() and parts.path == "/3/search/multi":
                payload = {"page": 1, "total_pages": 1, "results": [{**self.fresh_anime_tmdb, "media_type": "tv"}]}
            elif query == REQUEST_FRESH_ANIME_TITLE.casefold() and parts.path == "/3/search/tv":
                payload = {"page": 1, "total_pages": 1, "results": [self.fresh_anime_tmdb]}
            else:
                payload = self.tmdb.get(parts.path)
            if payload is None:
                return RemoteArtworkResponse(content_type="application/json", body=[b"{}"], status_code=404)
        return RemoteArtworkResponse(content_type="application/json", body=[json.dumps(payload).encode("utf-8")])


def install_requests_catalog_fixture() -> None:
    """Install a loopback-only upstream seam for the proof server; never production."""
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.config import settings
    from app.services import tmdb
    from app.services.requests import anilist, catalog

    fixture_root = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "requests"
    transport = _ProofCatalogTransport(fixture_root)
    policy = _ProofCatalogPolicy()
    tmdb.policy = policy
    tmdb.transport = transport
    tmdb.LIMITER = tmdb.RateLimiter(1_000_000)
    anilist.policy = policy
    anilist.transport = transport
    anilist.LIMITER = tmdb.RateLimiter(1_000_000)
    settings.tmdb_api_key = "0123456789abcdef0123456789abcdef"
    catalog.clear_cache()


def install_showcase_art_fixture(state_root: Path, origin: str) -> None:
    """Inject generated image bytes at the public art network seam, retaining the production token/cache route."""
    from app.main import artwork
    from app.services.artwork import ArtworkNotFoundError, RemoteArtworkResponse

    pictures = {}
    for identifier, title, year in ((700, DIRECT_TITLE, 2026), (701, CONVERTED_TITLE, 2025), (702, MATRIX_TITLE, 2024)):
        folder = state_root / "media" / "Movies" / f"{title} ({year})"
        image = next(path for path in (folder / "poster.png", folder / "poster.jpg") if path.is_file())
        content = image.read_bytes()
        mime = "image/png" if image.suffix == ".png" else "image/jpeg"
        pictures[f"{identifier}.jpg"] = (content, mime)
        pictures[f"{identifier}-bd.jpg"] = (content, mime)

    # The Requests fixture uses public TMDB/AniList artwork URLs.  Route only the
    # exact fixture addresses to locally generated media art: this keeps the
    # native screenshot useful while proving that the fixture never reaches an
    # upstream host.  The three showcase cards retain their original mappings.
    fixture_pictures: dict[str, tuple[bytes, str]] = {}

    def allow_tmdb(filename: str, size: str, generated_filename: str) -> None:
        fixture_pictures[f"https://image.tmdb.org/t/p/{size}/{filename}"] = pictures[generated_filename]

    def allow_card(identifier: int, generated_identifier: int) -> None:
        allow_tmdb(f"{identifier}.jpg", "w500", f"{generated_identifier}.jpg")
        allow_tmdb(f"{identifier}-bd.jpg", "w1280", f"{generated_identifier}-bd.jpg")

    # Existing public-sign-in showcase cards.
    for identifier in (700, 701, 702):
        allow_card(identifier, identifier)

    # TMDB cards assembled by _ProofCatalogTransport, including its existing
    # Requests JSON fixture details and the deterministic paging cards.
    for identifier, generated_identifier in {
        603: 702, 604: 702, 329865: 700, 376867: 701,
        1399: 701, 95396: 700, 125988: 702, 83867: 701,
        REQUEST_FRESH_ANIME_TMDB_ID: 700,
    }.items():
        allow_card(identifier, generated_identifier)
    for filename, generated_filename in {
        "matrix.jpg": "702.jpg", "reloaded.jpg": "702.jpg",
        "got.jpg": "701.jpg", "s1.jpg": "701.jpg", "frieren.jpg": "700.jpg",
        "orbiting-lanterns.jpg": "700.jpg", "orbiting-lanterns-s1.jpg": "700.jpg",
    }.items():
        allow_tmdb(filename, "w500", generated_filename)
    for filename, generated_filename in {
        "matrix-bd.jpg": "702-bd.jpg", "got-bd.jpg": "701-bd.jpg", "frieren-bd.jpg": "700-bd.jpg",
        "orbiting-lanterns-bd.jpg": "700-bd.jpg",
    }.items():
        allow_tmdb(filename, "w1280", generated_filename)
    allow_tmdb("frieren-s1.jpg", "w500", "700.jpg")

    # AniList fixture artwork is direct rather than size-prefixed TMDB artwork.
    for filename, generated_filename in {
        "frieren.jpg": "700.jpg", "frieren-banner.jpg": "700-bd.jpg", "m.jpg": "702.jpg",
        "a.jpg": "701.jpg", "c1.jpg": "700.jpg", "c2.jpg": "701.jpg",
        "orbiting-lanterns.jpg": "700.jpg", "orbiting-lanterns-banner.jpg": "700-bd.jpg",
    }.items():
        fixture_pictures[f"https://s4.anilist.co/{filename}"] = pictures[generated_filename]

    class GeneratedArtFetcher:
        def fetch(self, url, *, validate_redirect, headers=None):  # noqa: ANN001, ANN201, ARG002
            validate_redirect(url)
            picture = fixture_pictures.get(url)
            if picture is None:
                raise ArtworkNotFoundError("Not a generated showcase image.")
            content, mime = picture
            return RemoteArtworkResponse(content_type=mime, body=[content])

    artwork._public_source_policy = _ProofCatalogPolicy()
    artwork._remote_fetcher = GeneratedArtFetcher()

    # Native Requests loads public catalog art directly. Serve the same generated
    # bytes locally so its UI proofs never depend on public fixture URLs.
    from app.main import app
    from app.services.requests import catalog
    from fastapi.responses import Response

    original_image = catalog._image
    local_pictures = dict(enumerate(fixture_pictures.values()))
    local_urls = {source: f"{origin}/proof/requests-art/{index}"
                  for index, source in enumerate(fixture_pictures)}
    # The public welcome showcase proxies these same catalog URLs through its
    # signed image route; resolve both addresses to the identical local bytes.
    fixture_pictures.update({url: local_pictures[index] for index, url in enumerate(local_urls.values())})

    @app.get("/proof/requests-art/{index}")
    def request_fixture_art(index: int):
        content, mime = local_pictures[index]
        return Response(content=content, media_type=mime)

    # The production application mounts its SPA fallback at `/`.  This
    # proof-only image seam is registered after importing that application, so
    # place its exact route ahead of the catch-all without changing production
    # route order.
    request_art_route = next(
        route for route in reversed(app.router.routes)
        if getattr(route, "path", None) == "/proof/requests-art/{index}"
    )
    frontend_index = next(
        (index for index, route in enumerate(app.router.routes) if getattr(route, "name", None) == "frontend"),
        len(app.router.routes),
    )
    app.router.routes.remove(request_art_route)
    app.router.routes.insert(frontend_index, request_art_route)

    catalog._image = lambda path, size: local_urls.get(original_image(path, size))


def make_books(state_root: Path, db, owner) -> dict[str, str]:
    """Original synthetic prose and raster books, imported through the production archive boundary."""
    from app.services.books import BookService
    import pymupdf

    def page(number: int) -> bytes:
        width, height = 1200, 1800
        rows = [bytearray(b"\xf8\xf6\xf0" * width) for _ in range(height)]
        for top, bottom in [(80, 780), (840, 1720)]:
            for y in range(top, bottom):
                color = bytes((40 + number * 18, 70 + number * 12, 100 + number * 8))
                rows[y][80 * 3:1120 * 3] = color * 1040
            for y in range(top + 60, top + 100):
                rows[y][140 * 3:(200 + number * 100) * 3] = b"\xff\xff\xff" * (60 + number * 100)
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + row for row in rows))) + chunk(b"IEND", b"")

    images = [page(index) for index in range(6)]
    comic = state_root / "Lantern panels.cbz"
    with zipfile.ZipFile(comic, "w") as archive:
        archive.writestr("ComicInfo.xml", "<ComicInfo><Title>Lantern panels</Title><Writer>Synthetic Author</Writer><Manga>YesAndRightToLeft</Manga></ComicInfo>")
        for index, image in enumerate(images): archive.writestr(f"{index:03}.png", image)
    prose = state_root / "Lantern chapters.epub"
    oversized_epub = os.environ.get(OVERSIZED_EPUB_ENV) == "1"
    with zipfile.ZipFile(prose, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container><rootfiles><rootfile full-path='content.opf'/></rootfiles></container>")
        archive.writestr("content.opf", "<package><metadata><title>Lantern chapters</title><creator>Synthetic Author</creator><meta name='cover' content='cover'/></metadata><manifest><item id='cover' href='cover.png' media-type='image/png' properties='cover-image'/><item id='one' href='one.xhtml' media-type='application/xhtml+xml'/><item id='two' href='two.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='one'/><itemref idref='two'/></spine></package>")
        archive.writestr("cover.png", images[0])
        for filename, title in [("one.xhtml", "The harbour lights"), ("two.xhtml", "The morning map")]:
            if oversized_epub and filename == "one.xhtml":
                archive.writestr(filename, "<html><body><span>" + "🧭" * 500_001 + "</span></body></html>")
                continue
            paragraphs = "".join(f"<p id='line-{n}'>Passage {n + 1}. The keeper followed the lanterns beside the quiet harbour. A bright map marked the way home. <em>The story continued across the water.</em></p>" for n in range(50))
            archive.writestr(filename, f"<html><body><h1>{title}</h1>{paragraphs}</body></html>")
    pdf = state_root / "Lantern atlas.pdf"
    with pymupdf.open() as document:
        for index in range(4):
            sheet = document.new_page(width=600, height=900)
            sheet.insert_text((60, 80), f"Synthetic atlas — Page {index + 1}", fontsize=22)
            sheet.insert_image(pymupdf.Rect(60, 120, 540, 840), stream=images[index])
        document.save(pdf)
    result = {}
    cbr = state_root / "Lantern archive.cbr"
    shutil.copyfile(REPOSITORY_ROOT / "backend/tests/fixtures/books/solid-rar5.cbr", cbr)
    for key, path in [("comicBookID", comic), ("proseBookID", prose), ("pdfBookID", pdf), ("cbrBookID", cbr)]:
        service = BookService(db)
        format_, manifest = service.inspect_staged(path, path.name)
        book = service.import_staged(path, path.name, owner, format_=format_, manifest=manifest)
        if key == "comicBookID":
            book = service.update_book(book, owner, {"series_name": "Lantern journeys", "volume_number": 1})
            result["comicSeriesID"] = book.series_id
        result[key] = book.id
    shelf = service.create_shelf(owner, {"name": "The reading room", "visibility": "private"})
    service.replace_shelf_entries(shelf.id, owner, [result[key] for key in ("comicBookID", "proseBookID", "pdfBookID", "cbrBookID")])
    result["bookShelfID"] = shelf.id
    return result


def fixture_artwork() -> dict[str, Path]:
    directory = os.environ.get("LUMINA_NATIVE_ARTWORK_DIR")
    if not directory:
        return {}
    files = {}
    for name in ("cartographer.png", "lanterns.png", "observatory.png"):
        source = Path(directory) / name
        try:
            size = source.stat().st_size
            with source.open("rb") as image:
                header = image.read(33)
            valid = (source.is_file() and 33 <= size <= 12 * 1024 * 1024
                     and header[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                     and int.from_bytes(header[16:20], "big") > 0 and int.from_bytes(header[20:24], "big") > 0)
        except OSError:
            valid = False
        if not valid:
            raise RuntimeError(f"Original fixture artwork requires {name} to be a PNG no larger than 12 MiB.")
        files[name] = source
    return files


def copy_artwork(source: Path, target: Path) -> None:
    # Importers can prefer a JPEG sidecar over a PNG with the same image role.
    for extension in (".jpg", ".jpeg"):
        target.with_suffix(extension).unlink(missing_ok=True)
    shutil.copyfile(source, target)
def link_or_copy(source: Path, target: Path) -> None:
    """Avoid duplicate fixture bytes when the temporary filesystem permits it."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def ffmpeg(*args: str | Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to create the Apple proof fixture media.")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def make_title_logo(target: Path) -> None:
    """Create a deterministic transparent wordmark stand-in for the direct proof title."""
    ffmpeg(
        "-f", "lavfi", "-i",
        "color=c=black@0.0:s=600x180,format=rgba,"
        "drawbox=x=36:y=36:w=528:h=12:color=0xD4AF37@1:t=fill:replace=1,"
        "drawbox=x=36:y=66:w=390:h=16:color=white@1:t=fill:replace=1,"
        "drawbox=x=36:y=100:w=470:h=16:color=white@0.82:t=fill:replace=1",
        "-frames:v", "1", target,
    )


def make_movie(media_root: Path, title: str, year: int, extension: str, poster_size: str, duration: int = 90,
               artwork: Path | None = None, embedded_subtitle: bool = False, embedded_chapters: bool = False,
               video_size: str = "320x180", title_logo: bool = False) -> None:
    folder = media_root / "Movies" / f"{title} ({year})"
    folder.mkdir(parents=True)
    output = folder / f"{title} ({year}){extension}"
    subtitle_path: Path | None = None
    chapter_path: Path | None = None
    try:
        if embedded_subtitle:
            with tempfile.NamedTemporaryFile("w", suffix=".srt", encoding="utf-8", delete=False) as subtitle:
                subtitle.write(
                    "1\n00:00:02,000 --> 00:00:07,000\nSynthetic subtitle timing proof.\n\n"
                    "2\n00:01:20,000 --> 00:01:28,000\nThe native MKV subtitle track remains selectable.\n"
                )
                subtitle_path = Path(subtitle.name)
        if embedded_chapters:
            with tempfile.NamedTemporaryFile("w", suffix=".ffmetadata", encoding="utf-8", delete=False) as chapters:
                chapters.write(
                    ";FFMETADATA1\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=30000\ntitle=Opening\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=30000\nEND=60000\ntitle=Middle\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=60000\nEND=90000\ntitle=Closing\n"
                )
                chapter_path = Path(chapters.name)
        metadata_input = 2 + int(subtitle_path is not None)
        ffmpeg(
            "-f", "lavfi", "-i", f"testsrc2=size={video_size}:rate=24:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
            *(("-i", subtitle_path) if subtitle_path else ()),
            *(("-f", "ffmetadata", "-i", chapter_path) if chapter_path else ()),
            *(("-map", "0:v:0", "-map", "1:a:0", "-map", "2:0", "-c:s", "srt") if subtitle_path else ()),
            *(("-map_metadata", str(metadata_input), "-map_chapters", str(metadata_input)) if chapter_path else ()),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", *(() if subtitle_path else ("-shortest",)),
            *(("-movflags", "+faststart") if extension == ".mp4" else ()),
            output,
        )
    finally:
        if subtitle_path:
            subtitle_path.unlink(missing_ok=True)
        if chapter_path:
            chapter_path.unlink(missing_ok=True)
    (folder / "movie.nfo").write_text(
        f"""<movie>
<title>{title}</title>
<year>{year}</year>
<plot>Synthetic Apple proof fixture.</plot>
<genre>Adventure</genre>
<actor><name>Synthetic Performer</name><role>The navigator</role><type>Actor</type></actor>
</movie>""",
        encoding="utf-8",
    )
    if artwork:
        copy_artwork(artwork, folder / "poster.png")
    else:
        ffmpeg(
            "-f", "lavfi", "-i", f"testsrc2=size={poster_size}:rate=1:duration=1",
            "-frames:v", "1", folder / "poster.jpg",
        )
    if title_logo:
        make_title_logo(folder / "logo.png")


def xml_text(value: str) -> str:
    """Keep generated overview text literal while writing the small fixture NFO files."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_music(media_root: Path, artwork: Path | None = None) -> None:
    """Original generated tone and artwork; no downloaded music or recordings."""
    folder = media_root / "Music" / AUDIO_ARTIST / f"{AUDIO_ALBUM} (2026)"
    folder.mkdir(parents=True)
    from mutagen.mp4 import MP4
    for number, title, frequency in [(1, AUDIO_TRACK, 440), (2, AUDIO_SECOND_TRACK, 550)]:
        ffmpeg(
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={AUDIO_TRACK_DURATION_SECONDS}",
            "-c:a", "aac", "-metadata", f"title={title}",
            "-metadata", f"artist={AUDIO_ARTIST}", "-metadata", f"album_artist={AUDIO_ARTIST}",
            "-metadata", f"album={AUDIO_ALBUM}", "-metadata", f"track={number}", "-metadata", "date=2026",
            "-movflags", "+faststart", folder / f"{number:02d} {title}.m4a",
        )
        tagged = MP4(folder / f"{number:02d} {title}.m4a")
        tagged["©lyr"] = [f"[00:00.00]{SYNTHETIC_LYRIC}\n[00:08.00]Morning coordinates find the light"]
        tagged["©gen"] = ["Ambient"]
        for key, value in {"REPLAYGAIN_TRACK_GAIN": "-6 dB", "REPLAYGAIN_ALBUM_GAIN": "-4 dB",
                           "REPLAYGAIN_TRACK_PEAK": "0.8", "REPLAYGAIN_ALBUM_PEAK": "0.9"}.items():
            tagged[f"----:com.apple.iTunes:{key}"] = [value.encode()]
        tagged.save()
    if artwork:
        copy_artwork(artwork, folder / "cover.png")
        copy_artwork(artwork, folder.parent / "artist.png")
    else:
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=size=540x540:rate=1:duration=1",
            "-frames:v", "1", folder / "cover.jpg",
        )
        shutil.copyfile(folder / "cover.jpg", folder.parent / "artist.jpg")


def make_music_home_media(media_root: Path) -> None:
    """Add the small cross-client catalog only when the Music Home fixture is requested."""
    from e2e.music_fixture import (  # scripts/ is added before the proof server starts
        MUSIC_HOME_MULTIDISC_ALBUM,
        MUSIC_HOME_PRIVATE_ALBUM,
        MUSIC_HOME_PRIVATE_TRACK,
        MUSIC_HOME_UNTAGGED_FILE,
    )

    def tagged(path: Path, *, title: str, album: str, artist: str, track: int, disc: int = 1) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg(
            "-f", "lavfi", "-i", "sine=frequency=660:duration=1", "-c:a", "aac",
            "-metadata", f"title={title}", "-metadata", f"artist={artist}",
            "-metadata", f"album_artist={artist}", "-metadata", f"album={album}",
            "-metadata", f"track={track}", "-metadata", f"disc={disc}/2", path,
        )

    root = media_root / "Music"
    tagged(root / "Private Artist" / MUSIC_HOME_PRIVATE_ALBUM / f"01 {MUSIC_HOME_PRIVATE_TRACK}.m4a",
           title=MUSIC_HOME_PRIVATE_TRACK, album=MUSIC_HOME_PRIVATE_ALBUM, artist="Private Artist", track=1)
    for disc, title in ((1, "First Disc Signal"), (2, "Second Disc Signal")):
        tagged(root / "Fixture Artist" / MUSIC_HOME_MULTIDISC_ALBUM / f"CD{disc}" / f"01 {title}.m4a",
               title=title, album=MUSIC_HOME_MULTIDISC_ALBUM, artist="Fixture Artist", track=1, disc=disc)
    untagged = root / "Unlabeled Artist" / "Unlabeled Album" / MUSIC_HOME_UNTAGGED_FILE
    untagged.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=770:duration=1", "-c:a", "libmp3lame", untagged)


def make_music_catalogue(media_root: Path) -> None:
    """Copy the generated tone into enough tagged albums to cross a native page."""
    from mutagen.mp4 import MP4

    source = media_root / "Music" / AUDIO_ARTIST / f"{AUDIO_ALBUM} (2026)"
    cover = next(source.glob("cover.*"))
    for number in range(1, 56):
        artist = f"ZZ Music Artist {number:03d}"
        album = f"ZZ Music Album {number:03d}"
        title = f"ZZ Music Track {number:03d}"
        folder = media_root / "Music" / artist / album
        folder.mkdir(parents=True)
        track = folder / f"01 {title}.m4a"
        shutil.copyfile(source / f"01 {AUDIO_TRACK}.m4a", track)
        tagged = MP4(track)
        tagged["©nam"] = [title]
        tagged["©alb"] = [album]
        tagged["©ART"] = [artist]
        tagged["aART"] = [artist]
        tagged.save()
        shutil.copyfile(cover, folder / cover.name)


def make_performance_posters(media_root: Path, count: int) -> list[Path]:
    """Generate a bounded local set of distinct poster images for cache and decode profiling."""
    if count == 0:
        return []

    directory = media_root / "performance-posters"
    directory.mkdir(parents=True)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    def poster(index: int) -> bytes:
        width, height = 480, 720
        hue = (index * 47) % 256
        top = bytes(((hue + 38) % 256, (hue * 3 + 70) % 256, (hue * 5 + 112) % 256))
        bottom = bytes(((hue * 7 + 28) % 256, (hue + 134) % 256, (hue * 11 + 44) % 256))
        stripe = bytes(((hue * 13 + 210) % 256, (hue * 17 + 162) % 256, (hue + 196) % 256))
        rows = []
        stripe_start = (index * 31) % (width - 84)
        for y in range(height):
            color = top if y < height * 3 // 5 else bottom
            row = bytearray(color * width)
            if height // 5 < y < height * 4 // 5:
                row[stripe_start * 3:(stripe_start + 84) * 3] = stripe * 84
            rows.append(b"\0" + bytes(row))
        payload = b"".join(rows)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(payload, 6)) + chunk(b"IEND", b"")

    posters = []
    for index in range(count):
        path = directory / f"poster-{index:03d}.png"
        path.write_bytes(poster(index))
        posters.append(path)
    return posters


def make_performance_catalogue(media_root: Path, count: int, posters: list[Path] | None = None) -> None:
    """Add opt-in movie cards without re-encoding media or downloading fixture data."""
    if count == 0:
        return
    source = media_root / "Movies" / f"{DIRECT_TITLE} (2026)"
    source_video = source / f"{DIRECT_TITLE} (2026).mp4"
    source_poster = source / "poster.png" if (source / "poster.png").is_file() else source / "poster.jpg"
    if not source_video.is_file() or not source_poster.is_file():
        raise RuntimeError("The direct proof movie must exist before creating the performance catalog.")
    for index in range(1, count + 1):
        title = f"ZZ Performance Catalogue Film {index:03d}"
        folder = media_root / "Movies" / f"{title} (2026)"
        folder.mkdir(parents=True)
        link_or_copy(source_video, folder / f"{title} (2026).mp4")
        artwork = posters[(index - 1) % len(posters)] if posters else source_poster
        link_or_copy(artwork, folder / "poster.png" if posters else folder / source_poster.name)
        (folder / "movie.nfo").write_text(
            f"""<movie>
<title>{title}</title>
<year>2026</year>
<plot>Synthetic high-density scrolling proof fixture.</plot>
<genre>Adventure</genre>
<actor><name>Synthetic Performer</name><role>The navigator</role><type>Actor</type></actor>
</movie>""",
            encoding="utf-8",
        )


def make_show(media_root: Path, *, media_language_proof: bool) -> None:
    """Reuse only this harness's original generated video and artwork."""
    show = media_root / "Shows" / SHOW_TITLE
    show.mkdir(parents=True, exist_ok=True)
    (show / "tvshow.nfo").write_text(
        f"<tvshow><title>{xml_text(SHOW_TITLE)}</title><plot>{xml_text(SERIES_OVERVIEW)}</plot></tvshow>",
        encoding="utf-8",
    )
    movie = media_root / "Movies" / f"{DIRECT_TITLE} (2026)"
    poster = movie / "poster.png" if (movie / "poster.png").is_file() else movie / "poster.jpg"
    shutil.copyfile(poster, show / poster.name)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=1:duration=1", "-frames:v", "1", show / "fanart.jpg")
    make_title_logo(show / "logo.png")
    episodes = [
        (1, 1, EPISODE_TITLE),
        (1, 2, SECOND_EPISODE_TITLE),
        (2, 1, SECOND_SEASON_EPISODE_TITLE),
    ]
    if os.environ.get(AUTOPLAY_SERIES_ENV) == "1":
        episodes += [
            (1, 3, THIRD_EPISODE_TITLE),
            (1, 4, FOURTH_EPISODE_TITLE),
        ]
    if os.environ.get("LUMINA_NATIVE_LONG_SERIES") == "1":
        episodes += [(season, 1, f"Signal from season {season}") for season in range(3, 28)]
        episodes += [(27, episode, f"Late signal {episode}") for episode in range(2, 6)]
    for season_number, episode_number, title in episodes:
        season = show / f"Season {season_number:02d}"
        season.mkdir(parents=True, exist_ok=True)
        stem = f"{SHOW_TITLE} S{season_number:02d}E{episode_number:02d}"
        if media_language_proof and title == EPISODE_TITLE:
            ffmpeg("-i", movie / f"{DIRECT_TITLE} (2026).mp4", "-map", "0", "-c", "copy",
                   "-metadata:s:a:0", "language=jpn", season / f"{stem}.mp4")
            (season / f"{stem}.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nSynthetic English subtitle.\n", encoding="utf-8")
        elif os.environ.get("LUMINA_NATIVE_EPISODE_MKV") == "1":
            ffmpeg("-i", movie / f"{DIRECT_TITLE} (2026).mp4", "-c", "copy", season / f"{stem}.mkv")
        else:
            shutil.copyfile(movie / f"{DIRECT_TITLE} (2026).mp4", season / f"{stem}.mp4")
        overview, source, note = {
            EPISODE_TITLE: (FIRST_EPISODE_OVERVIEW, FIRST_EPISODE_SOURCE, FIRST_EPISODE_NOTE),
            SECOND_EPISODE_TITLE: (SECOND_EPISODE_OVERVIEW, SECOND_EPISODE_SOURCE, SECOND_EPISODE_NOTE),
        }.get(title, ("Synthetic second-season episode.", "Seasonal Signal Desk", "This episode remains outside the About workflow."))
        episode_overview = f"{overview}\n\n(Source: {source})\n\nNotes:\n- {note}"
        (season / f"{stem}.nfo").write_text(
            f"<episodedetails><title>{xml_text(title)}</title><showtitle>{xml_text(SHOW_TITLE)}</showtitle><season>{season_number}</season><episode>{episode_number}</episode><plot>{xml_text(episode_overview)}</plot></episodedetails>",
            encoding="utf-8",
        )


def make_performance_series(media_root: Path, count: int, posters: list[Path] | None = None) -> None:
    """Add separate series with one synthetic episode each for TV wall profiling."""
    source = media_root / "Movies" / f"{DIRECT_TITLE} (2026)"
    video = source / f"{DIRECT_TITLE} (2026).mp4"
    poster = source / "poster.png" if (source / "poster.png").is_file() else source / "poster.jpg"
    for index in range(1, count + 1):
        title = f"ZZ Performance Catalogue Series {index:04d}"
        series = media_root / "Shows" / title
        season = series / "Season 01"
        season.mkdir(parents=True)
        link_or_copy(video, season / f"{title} S01E01.mp4")
        artwork = posters[(index - 1) % len(posters)] if posters else poster
        link_or_copy(artwork, series / "poster.png" if posters else series / poster.name)
        (series / "tvshow.nfo").write_text(f"<tvshow><title>{title}</title></tvshow>", encoding="utf-8")
