"""Build a deterministic, synthetic library for cross-server measurements.

The generated media comes only from FFmpeg lavfi sources.  Most library entries
are hard links to one small direct-play file, so a useful scanner/index size does
not require hundreds of encoded copies.

    backend/.venv/bin/python scripts/perf/compare_fixtures.py ROOT [--movies 300]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def encode(ffmpeg: str, output: Path, *args: str) -> None:
    partial = output.with_name(f"partial-{output.name}")
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args,
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            str(partial),
        ],
        check=True,
    )
    partial.replace(output)


def link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def nfo(path: Path, title: str, year: int = 2024) -> None:
    path.write_text(
        f"<movie><title>{title}</title><sorttitle>{title}</sorttitle><year>{year}</year>"
        "<plot>Deterministic synthetic benchmark fixture.</plot></movie>\n"
    )


def build(root: Path, movies: int, ffmpeg: str) -> dict:
    if root.exists():
        shutil.rmtree(root)
    sources = root / ".sources"
    sources.mkdir(parents=True)
    direct = sources / "direct.mp4"
    transcode = sources / "transcode.mkv"
    direct_video = "testsrc2=size=1280x720:rate=24:duration=120"
    direct_audio = "sine=frequency=440:sample_rate=48000:duration=120"
    transcode_video = "testsrc2=size=1280x720:rate=24:duration=30"
    transcode_audio = "sine=frequency=440:sample_rate=48000:duration=30"
    encode(
        ffmpeg,
        direct,
        "-f", "lavfi", "-i", direct_video,
        "-f", "lavfi", "-i", direct_audio,
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "4M", "-g", "48",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest",
    )
    encode(
        ffmpeg,
        transcode,
        "-f", "lavfi", "-i", transcode_video,
        "-f", "lavfi", "-i", transcode_audio,
        "-c:v", "libx265", "-preset", "ultrafast", "-b:v", "3M", "-g", "48",
        "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error",
        "-c:a", "eac3", "-b:a", "192k", "-shortest",
    )

    library = root / "Movies"
    for index in range(movies):
        title = f"Benchmark Movie {index:04d}"
        folder = library / f"{title} (2024)"
        link(direct, folder / f"{title} (2024).mp4")
        nfo(folder / "movie.nfo", title)
    for title, source in (("Benchmark Direct", direct), ("Benchmark Transcode", transcode)):
        folder = library / f"{title} (2024)"
        link(source, folder / f"{title} (2024){source.suffix}")
        nfo(folder / "movie.nfo", title)

    manifest = {
        "schema": 1,
        "generator": "scripts/perf/compare_fixtures.py",
        "platform": platform.platform(),
        "movies": movies + 2,
        "encoded_sources": 2,
        "direct": {
            "title": "Benchmark Direct",
            "path": str((library / "Benchmark Direct (2024)" / "Benchmark Direct (2024).mp4").relative_to(root)),
            "codec": "h264/aac",
            "resolution": "1280x720",
            "duration_seconds": 120,
            "size_bytes": direct.stat().st_size,
            "sha256": hashlib.sha256(direct.read_bytes()).hexdigest(),
        },
        "transcode": {
            "title": "Benchmark Transcode",
            "path": str((library / "Benchmark Transcode (2024)" / "Benchmark Transcode (2024).mkv").relative_to(root)),
            "codec": "hevc-main10/eac3",
            "resolution": "1280x720",
            "duration_seconds": 30,
            "size_bytes": transcode.stat().st_size,
            "sha256": hashlib.sha256(transcode.read_bytes()).hexdigest(),
        },
    }
    (root / "fixture-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the shared Lumina/Jellyfin synthetic media library.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--movies", type=int, default=300)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    args = parser.parse_args()
    if not args.ffmpeg:
        parser.error("ffmpeg not found; pass --ffmpeg")
    if args.movies < 1:
        parser.error("--movies must be positive")
    print(json.dumps(build(args.root.resolve(), args.movies, args.ffmpeg), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
