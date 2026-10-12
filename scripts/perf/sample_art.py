"""24 lawful sample artwork images made once by ffmpeg lavfi. Shared by the title seed,
the rendition benchmark and the perf serve script. No network, no personal media.

    backend/.venv/bin/python scripts/perf/sample_art.py DEST
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# kind -> (count, width, height, extension); stills and posters are JPEG, logos PNG with alpha.
KINDS = {
    "poster": (8, 1000, 1500, "jpg"),
    "backdrop": (8, 1920, 1080, "jpg"),
    "logo": (4, 800, 310, "png"),
    "still": (4, 1280, 720, "jpg"),
}
SOURCES = ("testsrc2", "smptehdbars", "mandelbrot", "rgbtestsrc", "cellauto", "life", "testsrc", "smptebars")


def _render(source: str, width: int, height: int, target: Path, *, alpha: bool) -> None:
    graph = f"{source}=size={width}x{height}" if source not in ("cellauto", "life") else f"{source}=size={width}x{height}:rate=1"
    if alpha:
        graph += ",format=rgba,colorchannelmixer=aa=0.6"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", graph, "-frames:v", "1", *(["-q:v", "3"] if not alpha else []), str(target)],
        check=True, timeout=60,
    )


def generate(dest: Path) -> dict[str, list[Path]]:
    """Create (or reuse) DEST/<kind>-<n>.<ext>; returns {kind: [paths]} in a stable order."""
    dest.mkdir(parents=True, exist_ok=True)
    made: dict[str, list[Path]] = {}
    for kind, (count, width, height, extension) in KINDS.items():
        for number in range(count):
            target = dest / f"{kind}-{number}.{extension}"
            if not target.exists():
                _render(SOURCES[number % len(SOURCES)], width, height, target, alpha=kind == "logo")
            made.setdefault(kind, []).append(target)
    return made


if __name__ == "__main__":
    for kind, paths in generate(Path(sys.argv[1]).resolve()).items():
        print(kind, len(paths))
