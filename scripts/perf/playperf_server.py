"""Realstack backend with 1080p, 180 s library fixtures for `make playperf` (click -> first frame, resumes, quality switches).

Usage: playperf_server.py ROOT PORT. Fixtures are encoded once into output/playperf-fixtures (about a minute):
H.264 MP4 (direct), the same in MKV (remux), 10-bit HEVC + E-AC-3 MKV, long-GOP (~10 s) H.264 MKV, whose
mid-GOP restarts are what made copied playback hang, and MPEG-4 Part 2 (no browser decodes it: a video encode from the start).
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "output" / "playperf-fixtures"
def source(seconds: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=24:duration={seconds}", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]


SOURCE = source(180)
LONG = source(900)  # a resume at t=600 needs a file whose moov and index span a real length
ENCODES = {
    "h264.mp4": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"],
    "h264.mkv": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "hevc.mkv": ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error", "-b:v", "6M", "-c:a", "eac3", "-b:a", "384k"],
    "h264gop.mkv": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "250", "-keyint_min", "250", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "ac3"],
    "long.mp4": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "4M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"],
    "tail.mp4": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac"],  # moov at the end
    # a vault download as yt-dlp leaves it for YouTube: VP9 + Opus in WebM
    "mpeg4.mkv": ["-c:v", "mpeg4", "-q:v", "3", "-g", "240", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "yt.webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-b:v", "3M", "-g", "240", "-pix_fmt", "yuv420p", "-c:a", "libopus"],
}
LONG_FILES = {"long.mp4"}
TITLES = {"Perf Direct": "h264.mp4", "Perf Remux": "h264.mkv", "Perf Hevc": "hevc.mkv", "Perf Gop": "h264gop.mkv", "Perf Long": "long.mp4", "Perf Tail": "tail.mp4", "Perf Webm": "yt.webm", "Perf Encode": "mpeg4.mkv"}
EPISODES = ["h264.mp4", "h264.mp4"]  # "Perf Show" S01E01-02 for Next up

sys.path.insert(0, str(REPO / "scripts"))
import run_realstack_fixtures as rs  # noqa: E402
from e2e import music_fixture  # noqa: E402


def build_media(media: Path) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, codec in ENCODES.items():
        if not (FIXTURES / name).exists():
            rs.ffmpeg(*(LONG if name in LONG_FILES else SOURCE), *codec, "-shortest", FIXTURES / f"partial-{name}")
            (FIXTURES / f"partial-{name}").rename(FIXTURES / name)
    track = FIXTURES / "track.flac"  # a 5 minute lossless track for the music scenario
    if not track.exists():
        rs.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=300", "-c:a", "flac", "-metadata", "title=Perf Track", "-metadata", "artist=Perf Artist", "-metadata", "album_artist=Perf Artist", "-metadata", "album=Perf Album", "-metadata", "track=1/1", FIXTURES / "partial-track.flac")
        (FIXTURES / "partial-track.flac").rename(track)
    album = media / "Music" / "Perf Artist" / "Perf Album"
    album.mkdir(parents=True)
    os.link(track, album / "01 - Perf Track.flac")
    for title, name in TITLES.items():
        folder = media / "Movies" / f"{title} (2020)"
        folder.mkdir(parents=True)
        os.link(FIXTURES / name, folder / f"{title} (2020){Path(name).suffix}")
        (folder / "movie.nfo").write_text(f"<movie><title>{title}</title><year>2020</year></movie>")
    show = media / "Series" / "Perf Show"
    (show / "Season 01").mkdir(parents=True)
    (show / "tvshow.nfo").write_text("<tvshow><title>Perf Show</title><year>2021</year></tvshow>")
    for number, name in enumerate(EPISODES, 1):
        os.link(FIXTURES / name, show / "Season 01" / f"Perf Show S01E0{number}.mp4")


rs.build_media = build_media
music_fixture.build_music = lambda root: Path(root).mkdir(parents=True, exist_ok=True)
music_fixture.add_theme_music = lambda *_: None
rs.main()
