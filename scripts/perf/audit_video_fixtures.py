"""Synthetic media for the opt-in video audit, including selectable tracks.

The fixtures intentionally reuse the playperf codec/container matrix and add a
short, lawful MKV with two audio streams and one text subtitle.  Nothing here
contacts a provider or reads household media.
"""
from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "output" / "playperf-fixtures"


def _source(seconds: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=24:duration={seconds}", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]


SOURCE = _source(180)
LONG = _source(900)
ENCODES = {
    "h264.mp4": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"],
    "h264.mkv": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "hevc.mkv": ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error", "-b:v", "6M", "-c:a", "eac3", "-b:a", "384k"],
    "h264gop.mkv": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "250", "-keyint_min", "250", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "ac3"],
    "long.mp4": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "4M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"],
    "tail.mp4": ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "mpeg4.mkv": ["-c:v", "mpeg4", "-q:v", "3", "-g", "240", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "yt.webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-b:v", "3M", "-g", "240", "-pix_fmt", "yuv420p", "-c:a", "libopus"],
}
TITLES = {"Perf Direct": "h264.mp4", "Perf Remux": "h264.mkv", "Perf Hevc": "hevc.mkv", "Perf Gop": "h264gop.mkv", "Perf Long": "long.mp4", "Perf Tail": "tail.mp4", "Perf Webm": "yt.webm", "Perf Encode": "mpeg4.mkv"}


def _nfo(folder: Path, title: str) -> None:
    (folder / "movie.nfo").write_text(f"<movie><title>{title}</title><year>2020</year></movie>")


def _copy_or_link(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(source, target)


def _link(source: Path, media: Path, title: str) -> None:
    folder = media / "Movies" / f"{title} (2020)"
    folder.mkdir(parents=True)
    target = folder / f"{title} (2020){source.suffix}"
    _copy_or_link(source, target)
    _nfo(folder, title)


def build_media(media: Path) -> None:
    """Build all existing playperf media plus ``Perf Tracks`` once, then hard-link it."""
    from run_realstack_fixtures import ffmpeg

    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, codec in ENCODES.items():
        target = FIXTURES / name
        if not target.exists():
            ffmpeg(*(LONG if name == "long.mp4" else SOURCE), *codec, "-shortest", FIXTURES / f"partial-{name}")
            (FIXTURES / f"partial-{name}").rename(target)
    tracks = FIXTURES / "tracks.mkv"
    if not tracks.exists():
        subtitle = FIXTURES / "tracks.srt"
        subtitle.write_text("1\n00:00:01,000 --> 00:00:04,000\nSynthetic subtitle.\n")
        ffmpeg("-i", FIXTURES / "h264.mp4", "-f", "lavfi", "-t", "45", "-i", "sine=frequency=660:duration=45", "-i", subtitle,
               "-map", "0:v:0", "-map", "0:a:0", "-map", "1:a:0", "-map", "2:0", "-t", "45", "-c:v", "copy", "-c:a", "aac", "-c:s", "srt",
               "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English", "-metadata:s:a:1", "language=fra", "-metadata:s:a:1", "title=French", "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English", tracks)
    track = FIXTURES / "track.flac"
    if not track.exists():
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=300", "-c:a", "flac", "-metadata", "title=Perf Track", "-metadata", "artist=Perf Artist", "-metadata", "album_artist=Perf Artist", "-metadata", "album=Perf Album", "-metadata", "track=1/1", track)
    album = media / "Music" / "Perf Artist" / "Perf Album"
    album.mkdir(parents=True)
    _copy_or_link(track, album / "01 - Perf Track.flac")
    for title, name in TITLES.items():
        _link(FIXTURES / name, media, title)
    _link(tracks, media, "Perf Tracks")
    show = media / "Series" / "Perf Show" / "Season 01"
    show.mkdir(parents=True)
    (show.parent.parent / "tvshow.nfo").write_text("<tvshow><title>Perf Show</title><year>2021</year></tvshow>")
    for number in (1, 2):
        _copy_or_link(FIXTURES / "h264.mp4", show / f"Perf Show S01E0{number}.mp4")
