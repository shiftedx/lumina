"""real-stack launcher: fresh app root + synthetic media, then the real backend.

Usage: run_realstack_fixtures.py ROOT PORT

ROOT is wiped and recreated: ROOT/data is the isolated app root and ROOT/media
an external storage root of tiny lawful FFmpeg lavfi clips with NFO + posters.
The backend serves the built frontend (frontend/dist) and runs with the baseline
loopback-only socket guard, so provider network is blocked.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LAVFI = ["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=4", "-f", "lavfi", "-i", "sine=frequency=330:duration=4"]
# 90 s @ 320x180. The WebKit seek spec needs a runtime HEVC->H.264 conversion that takes several real
# seconds (issue #136); the series journey needs episodes long enough to seek, resume and reach the end.
SEEK_LAVFI = ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=90", "-f", "lavfi", "-i", "sine=frequency=330:duration=90"]
# Browser-playable (bundled Chromium lacks H.264), remux (H.264/AAC in MKV), transcode (HEVC).
WEBM = ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-b:v", "200k", "-c:a", "libopus"]
H264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
HEVC = ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-c:a", "aac", "-x265-params", "log-level=error"]


def ffmpeg(*args: str | Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def movie(root: Path, title: str, year: int, ext: str, codec: list[str], lavfi: list[str] = LAVFI) -> None:
    folder = root / "Movies" / f"{title} ({year})"
    folder.mkdir(parents=True)
    ffmpeg(*lavfi, *codec, "-shortest", folder / f"{title} ({year}){ext}")
    (folder / "movie.nfo").write_text(f"<movie><title>{title}</title><year>{year}</year><plot>Synthetic fixture.</plot></movie>")
    ffmpeg("-f", "lavfi", "-i", "color=c=0x356258:size=120x180", "-frames:v", "1", folder / "poster.jpg")


def build_media(media: Path) -> None:
    movie(media, "Realstack Direct", 2024, ".webm", WEBM)
    movie(media, "Realstack Remux", 2023, ".mkv", H264)
    movie(media, "Realstack Transcode", 2022, ".mp4", HEVC)
    # WebKit-only playback.webkit.spec.ts: long enough to observe a seek land ahead of the
    # converted range and restart (#137), unlike the 4 s fixtures above.
    movie(media, "Realstack Seek", 2019, ".mp4", HEVC, lavfi=SEEK_LAVFI)
    show = media / "Series" / "Realstack Show"
    (show / "Season 01").mkdir(parents=True)
    (show / "tvshow.nfo").write_text("<tvshow><title>Realstack Show</title><year>2021</year></tvshow>")
    ffmpeg("-f", "lavfi", "-i", "color=c=0xc08a4b:size=120x180", "-frames:v", "1", show / "poster.jpg")
    for number in (1, 2):
        # WebM (bundled Chromium decodes it) at 90 s: series.spec.ts seeks, resumes and plays to the end.
        ffmpeg(*SEEK_LAVFI, *WEBM, "-shortest", show / "Season 01" / f"Realstack Show S01E0{number}.webm")
    anime(media)


def anime(media: Path) -> None:
    """Library gallery: a show and a film under folders named Anime, which the default rule sorts into Anime."""
    show = media / "Series" / "Anime" / "Show A"
    (show / "Season 01").mkdir(parents=True)
    (show / "tvshow.nfo").write_text("<tvshow><title>Show A</title><year>2023</year></tvshow>")
    ffmpeg(*LAVFI, *WEBM, "-shortest", show / "Season 01" / "Show A S01E01.webm")
    film = media / "Movies" / "Anime" / "Film (2020)"
    film.mkdir(parents=True)
    (film / "movie.nfo").write_text("<movie><title>Film</title><year>2020</year><plot>Synthetic fixture.</plot></movie>")
    ffmpeg(*LAVFI, *WEBM, "-shortest", film / "Film (2020).webm")


def offline_setattr(owner: object, name: str, guarded) -> None:  # noqa: ANN001
    """Install a loopback guard hook that fails like an offline network (OSError), not a test assertion."""

    def hook(*args, **kwargs):  # noqa: ANN002, ANN003
        try:
            return guarded(*args, **kwargs)
        except AssertionError as exc:
            raise socket.gaierror(str(exc)) from None

    setattr(owner, name, hook)


def main() -> None:
    root, port = Path(sys.argv[1]).resolve(), sys.argv[2]
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    import test_safety

    test_safety.check_root_isolation(root)
    shutil.rmtree(root, ignore_errors=True)
    (root / "data").mkdir(parents=True)
    build_media(root / "media")
    from e2e.music_fixture import add_theme_music, build_music  # scripts/ is on sys.path (above)

    build_music(root / "Music")
    add_theme_music(root / "media" / "Series" / "Realstack Show")
    os.environ.update(
        LUMINA_DATA_DIR=str(root / "data"),
        LUMINA_PORT=port,
        LUMINA_STORAGE_MOUNT_PARENTS=f"{root / 'media'},{root / 'Music'}",
        LUMINA_ARTWORK_PASS="off",  # no background ffmpeg racing the playback journeys under load
    )
    test_safety.LoopbackOnlyGuard().apply(offline_setattr)
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.schemas import YouTubeSearchResponse  # noqa: PLC0415 - needs the backend on sys.path
    from app.services.yt_dlp_service import YtDlpService  # noqa: PLC0415

    # The recommender's provider, stubbed: the stack is offline, so a refresher's search returns nothing at once
    # instead of waiting on a blocked network. Journeys that need remote items would extend this stub; the recommendation journey plays a title.
    YtDlpService.youtube_search = lambda self, query, limit=10: YouTubeSearchResponse(query=query.strip(), items=[])
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(port), log_level="warning", timeout_keep_alive=30)


if __name__ == "__main__":
    main()
