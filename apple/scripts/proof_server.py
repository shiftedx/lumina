#!/usr/bin/env python3
"""Run a disposable, loopback-only Lumina server for native simulator tests.

The server uses the production SQLite schema and import service. It creates
synthetic H.264/AAC videos and an AAC music album, enables the opt-in Jellyfin-compatible
protocol, verifies the client journey, then remains available until Ctrl-C.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import struct
import zlib
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("LUMINA_NATIVE_PORT", os.environ.get("LUMINA_NATIVE_PROOF_PORT", os.environ.get("LUMINA_APPLE_PROOF_PORT", "18743"))))
ORIGIN = f"http://127.0.0.1:{PORT}"
FIXTURE_PATH = Path(os.environ.get("LUMINA_NATIVE_FIXTURE", os.environ.get("LUMINA_APPLE_PROOF_FIXTURE", REPOSITORY_ROOT / "apple" / "Tests" / "UITests" / "Fixture.json"))).resolve()
DEVICE_ID = f"apple-proof-{secrets.token_hex(8)}"
AUTH_HEADER = f'MediaBrowser Client="Lumina Apple Proof", Device="Simulator", DeviceId="{DEVICE_ID}", Version="1.0"'
DIRECT_TITLE = "The Lantern Keepers’ Extraordinary Midnight Voyage"
CONVERTED_TITLE = "A Cartographer’s Long Road Beyond the Winter Mountains"
MATRIX_TITLE = "The Patient Observatory at the Edge of Morning"
AUDIO_ARTIST = "The Synthetic Lantern Ensemble"
AUDIO_ALBUM = "Signals from a Quiet Observatory"
AUDIO_TRACK = "First Light in the Listening Room"
AUDIO_SECOND_TRACK = "A Signal Answered at Dawn"
SHARED_MUSIC_PLAYLIST = "Observatory household listening"
SYNTHETIC_LYRIC = "A lantern answers through the quiet night"
SHOW_TITLE = "The Observatory Signal Logs"
EPISODE_TITLE = "The First Signal"
SECOND_EPISODE_TITLE = "Morning Coordinates"
EPISODE_INTRO_END_SECONDS = 10
EPISODE_CREDITS_START_SECONDS = 75
PERFORMANCE_CATALOGUE_COUNT_ENV = "LUMINA_NATIVE_PERF_CATALOGUE_COUNT"
MAX_PERFORMANCE_CATALOGUE_COUNT = 500
PERFORMANCE_VIDEO_SIZE_ENV = "LUMINA_NATIVE_PERF_VIDEO_SIZE"
PERFORMANCE_VIDEO_SIZES = frozenset({"320x180", "1920x1080"})
PERFORMANCE_REQUEST_LOG_ENV = "LUMINA_NATIVE_PERF_REQUEST_LOG"
MAX_PERFORMANCE_REQUEST_LOG_BYTES = 4 * 1024 * 1024
PLAYBACK_INFO_DELAY_ENV = "LUMINA_NATIVE_PLAYBACK_INFO_DELAY_SECONDS"
MAX_PLAYBACK_INFO_DELAY_SECONDS = 5
REQUEST_MOVIE_KEY = "movie:603"
REQUEST_MOVIE_TITLE = "The Matrix"
REQUEST_SHOW_KEY = "show:1399"
REQUEST_SHOW_TITLE = "Game of Thrones"
REQUEST_ANIME_KEY = "anime:154587"
REQUEST_ANIME_TITLE = "Frieren: Beyond Journey's End"


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
            "/3/movie/603": movie_detail, "/3/tv/1399": show_detail, "/3/tv/209867": frieren,
            "/3/tv/209867/external_ids": {"id": 209867, "tvdb_id": 424536},
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

    def fetch(self, url: str, *, validate_redirect, headers=None, data=None):  # noqa: ANN001, ANN201
        from app.services.artwork import RemoteArtworkResponse

        validate_redirect(url)
        parts = urlsplit(url)
        if parts.hostname == "graphql.anilist.co":
            body = json.loads(data or b"{}")
            operation = body.get("query", "").split("(", 1)[0].removeprefix("query ").strip().split()[0]
            payload = self.anilist.get(operation, {"data": {}})
        else:
            # The fixture intentionally ignores filters and requested page; it keeps
            # a stable, multi-card catalog while exercising the production normalizer.
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


def install_showcase_art_fixture(state_root: Path) -> None:
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
    }.items():
        allow_card(identifier, generated_identifier)
    for filename, generated_filename in {
        "matrix.jpg": "702.jpg", "reloaded.jpg": "702.jpg",
        "got.jpg": "701.jpg", "s1.jpg": "701.jpg", "frieren.jpg": "700.jpg",
    }.items():
        allow_tmdb(filename, "w500", generated_filename)
    for filename, generated_filename in {
        "matrix-bd.jpg": "702-bd.jpg", "got-bd.jpg": "701-bd.jpg", "frieren-bd.jpg": "700-bd.jpg",
    }.items():
        allow_tmdb(filename, "w1280", generated_filename)
    allow_tmdb("frieren-s1.jpg", "w500", "700.jpg")

    # AniList fixture artwork is direct rather than size-prefixed TMDB artwork.
    for filename, generated_filename in {
        "frieren.jpg": "700.jpg", "frieren-banner.jpg": "700-bd.jpg", "m.jpg": "702.jpg",
        "a.jpg": "701.jpg", "c1.jpg": "700.jpg", "c2.jpg": "701.jpg",
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
    with zipfile.ZipFile(prose, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container><rootfiles><rootfile full-path='content.opf'/></rootfiles></container>")
        archive.writestr("content.opf", "<package><metadata><title>Lantern chapters</title><creator>Synthetic Author</creator><meta name='cover' content='cover'/></metadata><manifest><item id='cover' href='cover.png' media-type='image/png' properties='cover-image'/><item id='one' href='one.xhtml' media-type='application/xhtml+xml'/><item id='two' href='two.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='one'/><itemref idref='two'/></spine></package>")
        archive.writestr("cover.png", images[0])
        for filename, title in [("one.xhtml", "The harbour lights"), ("two.xhtml", "The morning map")]:
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


def performance_catalogue_count() -> int:
    """Return the opt-in size of the disposable high-density movie catalog."""
    raw = os.environ.get(PERFORMANCE_CATALOGUE_COUNT_ENV, "0")
    try:
        count = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{PERFORMANCE_CATALOGUE_COUNT_ENV} must be an integer from 0 to {MAX_PERFORMANCE_CATALOGUE_COUNT}.") from error
    if not 0 <= count <= MAX_PERFORMANCE_CATALOGUE_COUNT:
        raise RuntimeError(f"{PERFORMANCE_CATALOGUE_COUNT_ENV} must be from 0 to {MAX_PERFORMANCE_CATALOGUE_COUNT}.")
    return count


def performance_video_size() -> str:
    size = os.environ.get(PERFORMANCE_VIDEO_SIZE_ENV, "320x180")
    if size not in PERFORMANCE_VIDEO_SIZES:
        choices = ", ".join(sorted(PERFORMANCE_VIDEO_SIZES))
        raise RuntimeError(f"{PERFORMANCE_VIDEO_SIZE_ENV} must be one of: {choices}.")
    return size


def playback_info_delay_seconds(value: str | None = None) -> float:
    """Read the broker-only delay used by cancellation journeys; unset is off."""
    raw = os.environ.get(PLAYBACK_INFO_DELAY_ENV) if value is None else value
    if raw in (None, ""):
        return 0
    try:
        seconds = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{PLAYBACK_INFO_DELAY_ENV} must be a number from 0 to {MAX_PLAYBACK_INFO_DELAY_SECONDS}.") from error
    if not 0 <= seconds <= MAX_PLAYBACK_INFO_DELAY_SECONDS:
        raise RuntimeError(f"{PLAYBACK_INFO_DELAY_ENV} must be from 0 to {MAX_PLAYBACK_INFO_DELAY_SECONDS}.")
    return seconds


def install_playback_info_delay(app: Any, seconds: float) -> None:
    """Delay only authenticated PlaybackInfo negotiation in the disposable fixture."""
    if seconds == 0:
        return

    @app.middleware("http")
    async def delay_playback_info(request, call_next):  # noqa: ANN001, ANN202
        path = request.url.path.split("/")
        if request.method == "POST" and len(path) == 4 and path[1] == "Items" and path[3] == "PlaybackInfo":
            await asyncio.sleep(seconds)
        return await call_next(request)


def performance_request_log_path() -> Path | None:
    raw = os.environ.get(PERFORMANCE_REQUEST_LOG_ENV)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError(f"{PERFORMANCE_REQUEST_LOG_ENV} must be an absolute path.")
    return path


def _normalise_request_path(path: str) -> str:
    path = urlsplit(path).path
    segments = path.split("/")
    if segments[1:5] == ["api", "public", "showcase", "art"] and len(segments) > 5:
        segments[5] = ":token"
    normalised = []
    for segment in segments:
        try:
            uuid.UUID(segment)
        except ValueError:
            normalised.append(segment)
        else:
            normalised.append(":id")
    return "/".join(normalised)


class _PerformanceRequestLog:
    """A bounded, credential-free recorder for the opt-in simulator profiling tour."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(self._descriptor, 0o600)
        self._lock = threading.Lock()
        self._bytes_written = 0
        self._active = False
        self._stopped = False

    def activate(self) -> None:
        self._active = True

    def write(self, *, method: str, path: str, status: int, duration_ms: int) -> None:
        if not self._active or self._stopped:
            return
        line = json.dumps({
            "timestamp": round(time.time(), 3),
            "method": method,
            "path": _normalise_request_path(path),
            "status": status,
            "duration_ms": duration_ms,
        }, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._lock:
            if self._stopped or self._bytes_written + len(line) > MAX_PERFORMANCE_REQUEST_LOG_BYTES:
                self._stopped = True
                return
            os.write(self._descriptor, line)
            self._bytes_written += len(line)

    def close(self) -> None:
        with self._lock:
            if self._descriptor >= 0:
                os.close(self._descriptor)
                self._descriptor = -1


def install_performance_request_log(app: Any, path: Path) -> _PerformanceRequestLog:
    recorder = _PerformanceRequestLog(path)

    @app.middleware("http")
    async def record_performance_request(request, call_next):  # noqa: ANN001, ANN202
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            recorder.write(
                method=request.method,
                path=request.url.path,
                status=500,
                duration_ms=round((time.perf_counter() - started) * 1_000),
            )
            raise
        recorder.write(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1_000),
        )
        return response

    return recorder


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


def make_movie(media_root: Path, title: str, year: int, extension: str, poster_size: str, duration: int = 90,
               artwork: Path | None = None, embedded_subtitle: bool = False, embedded_chapters: bool = False,
               video_size: str = "320x180") -> None:
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


def make_music(media_root: Path, artwork: Path | None = None) -> None:
    """Original generated tone and artwork; no downloaded music or recordings."""
    folder = media_root / "Music" / AUDIO_ARTIST / f"{AUDIO_ALBUM} (2026)"
    folder.mkdir(parents=True)
    from mutagen.mp4 import MP4
    for number, title, frequency in [(1, AUDIO_TRACK, 440), (2, AUDIO_SECOND_TRACK, 550)]:
        ffmpeg(
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=90",
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


def make_performance_catalogue(media_root: Path, count: int) -> None:
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
        link_or_copy(source_poster, folder / source_poster.name)
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


def make_show(media_root: Path) -> None:
    """Reuse only this harness's original generated video and artwork."""
    show = media_root / "Shows" / SHOW_TITLE
    season = show / "Season 01"
    season.mkdir(parents=True)
    (show / "tvshow.nfo").write_text(f"<tvshow><title>{SHOW_TITLE}</title><plot>Synthetic observatory episode fixture.</plot></tvshow>", encoding="utf-8")
    movie = media_root / "Movies" / f"{DIRECT_TITLE} (2026)"
    poster = movie / "poster.png" if (movie / "poster.png").is_file() else movie / "poster.jpg"
    shutil.copyfile(poster, show / poster.name)
    for number, title in [(1, EPISODE_TITLE), (2, SECOND_EPISODE_TITLE)]:
        stem = f"{SHOW_TITLE} S01E{number:02d}"
        shutil.copyfile(movie / f"{DIRECT_TITLE} (2026).mp4", season / f"{stem}.mp4")
        (season / f"{stem}.nfo").write_text(
            f"<episodedetails><title>{title}</title><showtitle>{SHOW_TITLE}</showtitle><season>1</season><episode>{number}</episode><plot>Synthetic episode.</plot></episodedetails>",
            encoding="utf-8",
        )


def install_loopback_guard() -> None:
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    import test_safety

    def set_attribute(owner: object, name: str, guarded: Any) -> None:
        def hook(*args: Any, **kwargs: Any) -> Any:
            try:
                return guarded(*args, **kwargs)
            except AssertionError as error:
                raise socket.gaierror(str(error)) from None
        setattr(owner, name, hook)

    test_safety.LoopbackOnlyGuard().apply(set_attribute)


def configure_environment(state_root: Path) -> None:
    os.environ.update(
        LUMINA_DATA_DIR=str(state_root / "data"),
        LUMINA_PORT=str(PORT),
        LUMINA_APP_PUBLIC_URL=ORIGIN,
        LUMINA_FRONTEND_PUBLIC_URL=ORIGIN,
        LUMINA_STORAGE_MOUNT_PARENTS=str(state_root / "media"),
        LUMINA_ARTWORK_PASS="off",
        LUMINA_MUSIC_YOUTUBE_DISCOVERY="false",
        LUMINA_TMDB_API_KEY="",
        LUMINA_AI_BASE_URL="",
        LUMINA_ASR_BASE_URL="",
    )


def provision(state_root: Path, username: str, password: str) -> dict[str, Any]:
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.db import init_db, session_scope
    from app.models import LibraryItem, MediaTitle, User
    from app.media_schemas import MediaSegmentInput
    from app.music_schemas import MusicPlaylistSave
    from app.services import media_segments
    from app.services import music_playlists
    from app.services.library_import import LibraryImportService, drive
    from app.services.storage_roots import StorageRootService
    from app.services.users import UserService
    from app.services.yt_dlp_service import YtDlpService

    init_db()
    with session_scope() as db:
        user = UserService(db).create_initial_admin(username, password, "Apple Proof")
        settings = YtDlpService(db).ensure_app_settings()
        settings.jellyfin_enabled = True
        settings.requests_enabled = True
        root = StorageRootService(db).create(
            label="Apple proof media",
            container_path=str(state_root / "media"),
            mode="external",
        )
        run = LibraryImportService(db).start(root.id, user.id, visibility="shared")
        run_id = run.id

    drive(run_id)
    with session_scope() as db:
        items = {item.title: item.id for item in db.query(LibraryItem).all()}
        titles = {(title.type, title.name): title.id for title in db.query(MediaTitle).all()}
        show_id = titles[("series", SHOW_TITLE)]
        season_id = db.query(MediaTitle).filter_by(type="season", parent_id=show_id).one().id
        first_episode_item = db.query(LibraryItem).filter_by(title_id=titles[("episode", EPISODE_TITLE)]).one()
        media_segments.save_user_segments(db, first_episode_item.id, [
            MediaSegmentInput(type="intro", start_seconds=0, end_seconds=EPISODE_INTRO_END_SECONDS),
            MediaSegmentInput(type="credits", start_seconds=EPISODE_CREDITS_START_SECONDS, end_seconds=90),
        ])
        owner = db.query(User).filter_by(username=username).one()
        curator = UserService(db).create_member_without_password(owner, "observatory-curator", "Observatory curator")
        shared_playlist = music_playlists.save(db, curator, MusicPlaylistSave(
            name=SHARED_MUSIC_PLAYLIST, shared=True, item_ids=[items[AUDIO_TRACK], items[AUDIO_SECOND_TRACK]]))
        book_items = make_books(state_root, db, owner)
    try:
        return {
            **book_items,
            "directItemID": items[DIRECT_TITLE],
            "directItemTitle": DIRECT_TITLE,
            "convertedItemID": items[CONVERTED_TITLE],
            "convertedItemTitle": CONVERTED_TITLE,
            "matrixItemID": items[MATRIX_TITLE],
            "matrixItemTitle": MATRIX_TITLE,
            "showID": uuid.UUID(show_id).hex,
            "showTitle": SHOW_TITLE,
            "seasonID": uuid.UUID(season_id).hex,
            "episodeID": uuid.UUID(titles[("episode", EPISODE_TITLE)]).hex,
            "episodeTitle": EPISODE_TITLE,
            "secondEpisodeID": uuid.UUID(titles[("episode", SECOND_EPISODE_TITLE)]).hex,
            "secondEpisodeTitle": SECOND_EPISODE_TITLE,
            "episodeIntroEndSeconds": EPISODE_INTRO_END_SECONDS,
            "episodeCreditsStartSeconds": EPISODE_CREDITS_START_SECONDS,
            "audioAlbumID": uuid.UUID(titles[("album", AUDIO_ALBUM)]).hex,
            "audioAlbumTitle": AUDIO_ALBUM,
            "audioArtistID": uuid.UUID(titles[("artist", AUDIO_ARTIST)]).hex,
            "audioTrackID": uuid.UUID(items[AUDIO_TRACK]).hex,
            "audioTrackTitle": AUDIO_TRACK,
            "audioSecondTrackID": uuid.UUID(items[AUDIO_SECOND_TRACK]).hex,
            "audioSecondTrackTitle": AUDIO_SECOND_TRACK,
            "sharedMusicPlaylistID": shared_playlist["id"],
            "sharedMusicPlaylistTitle": SHARED_MUSIC_PLAYLIST,
            "audioLyric": SYNTHETIC_LYRIC,
            "requestMovieKey": REQUEST_MOVIE_KEY,
            "requestMovieTitle": REQUEST_MOVIE_TITLE,
            "requestShowKey": REQUEST_SHOW_KEY,
            "requestShowTitle": REQUEST_SHOW_TITLE,
            "requestAnimeKey": REQUEST_ANIME_KEY,
            "requestAnimeTitle": REQUEST_ANIME_TITLE,
        }
    except KeyError as error:
        raise RuntimeError("The production import did not index all proof videos and music.") from error


def request_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "Authorization": AUTH_HEADER}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f'{AUTH_HEADER}, Token="{token}"'
    request = Request(ORIGIN + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read()
    except HTTPError as error:
        raise RuntimeError(f"{method} {path} returned {error.code}: {error.read().decode(errors='replace')}") from error
    return {} if not raw else json.loads(raw)


def verify_api(expected: dict[str, Any], username: str, password: str) -> dict[str, Any]:
    info = request_json("/System/Info/Public")
    if info.get("ServerName") != "Lumina":
        raise RuntimeError("The Jellyfin-compatible public-info endpoint did not identify Lumina.")

    signed_in = request_json("/Users/AuthenticateByName", method="POST", payload={"Username": username, "Pw": password})
    token = signed_in.get("AccessToken")
    if not isinstance(token, str) or not token:
        raise RuntimeError("The Jellyfin-compatible sign-in did not return an access token.")

    request_home = request_json("/Lumina/Requests/catalog/home", token=token)
    request_keys = {
        item.get("key")
        for rail in request_home.get("rails", []) if isinstance(rail, dict)
        for item in rail.get("items", []) if isinstance(item, dict)
    }
    if not {REQUEST_MOVIE_KEY, REQUEST_SHOW_KEY, REQUEST_ANIME_KEY}.issubset(request_keys):
        raise RuntimeError("The native Requests catalog did not expose the proof titles.")
    request_search = request_json("/Lumina/Requests/catalog/search?q=Frieren&page=1", token=token)
    if not any(item.get("key") == REQUEST_ANIME_KEY for item in request_search.get("items", [])):
        raise RuntimeError("The native Requests search did not expose the anime proof title.")
    request_quota = request_json("/Lumina/Requests/quota", token=token)
    if not any(row.get("kind") == "anime" for row in request_quota.get("quotas", []) if isinstance(row, dict)):
        raise RuntimeError("The native Requests quota route did not expose the anime policy.")
    request_show = request_json("/Lumina/Requests/catalog/title/show/1399", token=token)
    if not any(row.get("number") == 1 for row in request_show.get("seasons", []) if isinstance(row, dict)):
        raise RuntimeError("The native Requests title route did not expose the show season selector.")
    request_anime = request_json("/Lumina/Requests/catalog/title/anime/154587", token=token)
    if request_anime.get("key") != REQUEST_ANIME_KEY or request_anime.get("tvdb_id") != 424536:
        raise RuntimeError("The native Requests title route did not preserve anime request metadata.")

    items = request_json("/Items?IncludeItemTypes=Movie,Episode,Video&Recursive=true&Fields=Overview,UserData,ImageTags&SortBy=SortName&StartIndex=0&Limit=50", token=token)
    by_name = {item.get("Name"): item for item in items.get("Items", [])}
    direct = by_name.get(DIRECT_TITLE)
    converted = by_name.get(CONVERTED_TITLE)
    matrix = by_name.get(MATRIX_TITLE)
    if not isinstance(direct, dict) or not isinstance(converted, dict) or not isinstance(matrix, dict):
        raise RuntimeError("The Jellyfin-compatible items endpoint did not expose all imported videos.")
    if any(not item.get("ImageTags", {}).get("Primary") for item in (direct, converted, matrix)):
        raise RuntimeError("The Jellyfin-compatible items endpoint did not expose all synthetic posters.")

    for item in (direct, converted, matrix):
        artwork_url = ORIGIN + f"/Items/{item['Id']}/Images/Primary?" + urlencode({"maxWidth": "400", "ApiKey": token})
        with urlopen(Request(artwork_url), timeout=60) as response:
            if not response.headers.get_content_type().startswith("image/") or not response.read(1):
                raise RuntimeError("A synthetic poster did not load through the authenticated artwork route.")

    shows = request_json("/Items?IncludeItemTypes=Series&Recursive=true&Limit=50", token=token)
    if expected["showID"] not in {item.get("Id") for item in shows.get("Items", [])}:
        raise RuntimeError("The imported synthetic show was absent from the public catalog.")
    seasons = request_json(f"/Shows/{expected['showID']}/Seasons", token=token)
    if expected["seasonID"] not in {item.get("Id") for item in seasons.get("Items", [])}:
        raise RuntimeError("The synthetic show did not expose its imported season.")
    episodes = request_json(f"/Items?ParentId={expected['seasonID']}&IncludeItemTypes=Episode&Limit=50", token=token)
    if not {expected["episodeID"], expected["secondEpisodeID"]}.issubset({item.get("Id") for item in episodes.get("Items", [])}):
        raise RuntimeError("The synthetic season did not expose both imported episodes.")
    segments = request_json(f"/MediaSegments/{expected['episodeID']}", token=token)
    expected_segments = [("Intro", 0, EPISODE_INTRO_END_SECONDS * 10_000_000),
                         ("Outro", EPISODE_CREDITS_START_SECONDS * 10_000_000, 900_000_000)]
    actual_segments = [(item.get("Type"), item.get("StartTicks"), item.get("EndTicks"))
                       for item in segments.get("Items", []) if isinstance(item, dict)]
    if actual_segments != expected_segments:
        raise RuntimeError("The synthetic first episode did not expose its authoritative intro and credits segments.")

    albums = request_json("/Items?IncludeItemTypes=MusicAlbum&Recursive=true&Fields=Overview,UserData,ImageTags&Limit=50", token=token)
    album = next((item for item in albums.get("Items", []) if item.get("Id") == expected["audioAlbumID"]), None)
    if not isinstance(album, dict) or not album.get("ImageTags", {}).get("Primary"):
        raise RuntimeError("The authenticated music catalog did not expose the imported album and its cover.")
    artists = request_json("/Items?IncludeItemTypes=MusicArtist&Recursive=true&Limit=50", token=token)
    if expected["audioArtistID"] not in {item.get("Id") for item in artists.get("Items", [])}:
        raise RuntimeError("The authenticated music catalog did not expose the imported artist.")
    tracks = request_json(f"/Items?ParentId={expected['audioAlbumID']}&IncludeItemTypes=Audio&Limit=50", token=token)
    if not {expected["audioTrackID"], expected["audioSecondTrackID"]}.issubset({item.get("Id") for item in tracks.get("Items", [])}):
        raise RuntimeError("The authenticated album did not expose its imported audio track.")
    album_art = ORIGIN + f"/Items/{expected['audioAlbumID']}/Images/Primary?" + urlencode({"maxWidth": "400", "ApiKey": token})
    with urlopen(Request(album_art), timeout=60) as response:
        if not response.headers.get_content_type().startswith("image/") or not response.read(1):
            raise RuntimeError("The synthetic album cover did not load through authenticated artwork.")
    track = request_json(f"/Users/{signed_in['User']['Id']}/Items/{expected['audioTrackID']}", token=token)
    if track.get("Type") != "Audio" or track.get("Id") != expected["audioTrackID"]:
        raise RuntimeError("The authenticated fresh-item route did not expose the synthetic audio track.")
    native_track = request_json(f"/Lumina/Music/tracks/{expected['audioTrackID']}", token=token)
    if native_track.get("gain_db") != -6 or native_track.get("album_gain_db") != -4:
        raise RuntimeError("The production music import did not preserve synthetic ReplayGain metadata.")
    lyrics = request_json(f"/Lumina/Music/tracks/{expected['audioTrackID']}/lyrics", token=token)
    if not any(cue.get("text") == SYNTHETIC_LYRIC for cue in lyrics.get("cues", [])):
        raise RuntimeError("The production music import did not preserve synthetic timed lyrics.")
    playlists = request_json("/Lumina/Music/playlists", token=token)
    shared = next((entry for entry in playlists if entry.get("id") == expected["sharedMusicPlaylistID"]), None)
    if shared is None or shared.get("can_edit") is not False:
        raise RuntimeError("The native music boundary did not preserve a shared read-only playlist.")
    audio_url = ORIGIN + f"/Audio/{expected['audioTrackID']}/stream?" + urlencode({"Static": "true", "ApiKey": token})
    with urlopen(Request(audio_url, headers={"Range": "bytes=0-1023"}), timeout=60) as response:
        if response.headers.get_content_type() != "audio/mp4":
            raise RuntimeError("The generated AAC M4A stream did not return the audio/mp4 media type required by native playback.")
        if not response.read(1):
            raise RuntimeError("The authenticated audio stream returned no media bytes.")

    profile = {
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "h264", "AudioCodec": "aac"}],
        "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
    }
    playback = request_json(
        f"/Items/{direct['Id']}/PlaybackInfo",
        method="POST",
        token=token,
        payload={"DeviceProfile": profile, "EnableDirectPlay": True},
    )
    source = next((candidate for candidate in playback.get("MediaSources", []) if candidate.get("SupportsDirectPlay")), None)
    if not isinstance(source, dict):
        raise RuntimeError("The direct H.264/AAC fixture did not negotiate direct playback.")
    direct_url = ORIGIN + f"/Videos/{direct['Id']}/stream?" + urlencode({"MediaSourceId": source["Id"], "Static": "true", "ApiKey": token})
    with urlopen(Request(direct_url, headers={"Range": "bytes=0-1023"}), timeout=60) as response:
        if not response.read(1):
            raise RuntimeError("The negotiated direct stream returned no media bytes.")

    converted_playback = request_json(
        f"/Items/{converted['Id']}/PlaybackInfo",
        method="POST",
        token=token,
        payload={"DeviceProfile": profile, "EnableDirectPlay": False},
    )
    if not any(candidate.get("SupportsTranscoding") for candidate in converted_playback.get("MediaSources", [])):
        raise RuntimeError("The compatibility fixture did not negotiate a transcode source.")

    request_json(
        "/Sessions/Playing/Progress",
        method="POST",
        token=token,
        payload={
            "ItemId": direct["Id"],
            "MediaSourceId": source["Id"],
            "PlaySessionId": playback["PlaySessionId"],
            "PositionTicks": 120_000_000,
            "IsPaused": True,
        },
    )
    resume = request_json("/UserItems/Resume?IncludeItemTypes=Movie,Episode,Video&Recursive=true&Fields=Overview,UserData,ImageTags&Limit=50", token=token)
    if direct["Id"] not in {item.get("Id") for item in resume.get("Items", [])}:
        raise RuntimeError("Playback progress did not make the direct fixture resumable.")
    return {
        **expected,
        "directItemID": str(direct["Id"]),
        "directItemTitle": DIRECT_TITLE,
        "convertedItemID": str(converted["Id"]),
        "convertedItemTitle": CONVERTED_TITLE,
        "matrixItemID": str(matrix["Id"]),
        "matrixItemTitle": MATRIX_TITLE,
    }


def write_fixture(state_root: Path, username: str, password: str, items: dict[str, Any]) -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Publish only complete JSON, with private permissions before credentials are written.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=FIXTURE_PATH.parent, delete=False) as pending:
        temporary = Path(pending.name)
        try:
            json.dump({"serverURL": ORIGIN, "username": username, "password": password, **items, "stateRoot": str(state_root)}, pending, indent=2)
            pending.write("\n")
            pending.close()
            temporary.replace(FIXTURE_PATH)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    global PORT, ORIGIN
    # Reserve the port until Uvicorn takes ownership; parallel fixtures cannot race.
    listener = socket.socket()
    listener.bind(("127.0.0.1", PORT))
    listener.listen(128)
    PORT = listener.getsockname()[1]
    ORIGIN = f"http://127.0.0.1:{PORT}"
    def terminate(*_: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    state_root = Path(tempfile.mkdtemp(prefix="lumina-apple-proof-")).resolve()
    if Path(tempfile.gettempdir()).resolve() not in state_root.parents:
        raise RuntimeError("Refusing to create proof state outside the system temporary directory.")
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    import test_safety

    test_safety.check_root_isolation(state_root)
    username = f"proof-{secrets.token_hex(4)}"
    password = f"Proof-{secrets.token_urlsafe(18)}"
    thread: threading.Thread | None = None
    server: Any = None
    performance_request_log: _PerformanceRequestLog | None = None
    try:
        playback_info_delay = playback_info_delay_seconds()
        artwork = fixture_artwork()
        performance_catalogue = performance_catalogue_count()
        perf_video_size = performance_video_size() if performance_catalogue else "320x180"
        # Default sidecars are generated locally; optional original PNGs are copied unchanged.
        print("Generating synthetic proof video and music media...", flush=True)
        make_movie(state_root / "media", DIRECT_TITLE, 2026, ".mp4", "360x540", artwork=artwork.get("lanterns.png"),
                   video_size=perf_video_size)
        make_movie(state_root / "media", CONVERTED_TITLE, 2025, ".mkv", "960x540", artwork=artwork.get("cartographer.png"),
                   embedded_subtitle=True, embedded_chapters=True, video_size=perf_video_size)
        make_movie(state_root / "media", MATRIX_TITLE, 2024, ".mp4", "360x540", duration=40, artwork=artwork.get("observatory.png"))
        if performance_catalogue:
            print(f"Adding {performance_catalogue} synthetic performance-catalog movies...", flush=True)
            make_performance_catalogue(state_root / "media", performance_catalogue)
        make_music(state_root / "media", artwork=artwork.get("observatory.png"))
        make_show(state_root / "media")
        configure_environment(state_root)
        install_loopback_guard()
        install_requests_catalog_fixture()
        print("Importing the disposable video and music catalog...", flush=True)
        provisioned = provision(state_root, username, password)

        import uvicorn
        from app.main import app
        install_showcase_art_fixture(state_root)
        install_playback_info_delay(app, playback_info_delay)
        if performance_catalogue and (request_log_path := performance_request_log_path()) is not None:
            performance_request_log = install_performance_request_log(app, request_log_path)

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", timeout_keep_alive=30))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), name="lumina-apple-proof", daemon=True)
        thread.start()
        for _ in range(600):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError("The proof server stopped before becoming ready.")
            time.sleep(0.1)
        else:
            raise RuntimeError("The proof server did not become ready within 60 seconds.")

        print("Verifying authenticated catalog, artwork, and playback routes...", flush=True)
        verified = verify_api(provisioned, username, password)
        if performance_request_log is not None:
            performance_request_log.activate()
        write_fixture(state_root, username, password, verified)
        print(f"Lumina Apple proof server ready at {ORIGIN}")
        print(f"Fixture: {FIXTURE_PATH}")
        print("Press Ctrl-C to stop and remove the disposable state.")
        while thread.is_alive():
            thread.join(timeout=1)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=60)
        if performance_request_log is not None:
            performance_request_log.close()
        listener.close()
        FIXTURE_PATH.unlink(missing_ok=True)
        shutil.rmtree(state_root)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
