"""Rendition throughput on the shared sample art.

    backend/.venv/bin/python scripts/perf/rendition_bench.py [--count 200] [--ffmpeg PATH]

Renders each kind of sample image ``--count`` times the way the background pass does (one ffmpeg per source,
nice 19, one thread) and prints one JSON line: per-kind timings and output sizes, and the estimated hours for a
first pass over the production library. The reference-host run is a lead step with the owner's go-ahead; its
numbers go in the release issue, never in the repository.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# sample kind -> (title type, image type) as the pass renders it
KINDS = {"poster": ("movie", "Primary"), "backdrop": ("movie", "Backdrop"), "logo": ("movie", "Logo"), "still": ("episode", "Primary")}
# Production sources per kind.
PRODUCTION = {"poster": 5_050, "backdrop": 2_450, "logo": 2_400, "still": 37_100}
BUDGET_HOURS = 6.0  # success criterion 6


def _sample_art():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("sample_art", ROOT / "scripts" / "perf" / "sample_art.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bench(count: int, ffmpeg: str, work: Path) -> dict:
    """``count`` renders of each kind; the app is imported here so ``main`` can point its data dir at ``work`` first."""
    from app.services import art_urls, renditions
    from app.services.artwork import LOCAL_ARTWORK_TYPES

    art_urls.set_extension("webp" if renditions.has_libwebp(ffmpeg) else "jpg")
    samples = _sample_art().generate(work / "art")
    content_types = dict(LOCAL_ARTWORK_TYPES)
    kinds: dict[str, dict] = {}
    for kind, (title_type, image_type) in KINDS.items():
        if image_type == "Logo" and art_urls.extension() == "jpg":  # no JPEG logos: the pass serves the original, at no cost
            kinds[kind] = {"mean_s": 0.0, "p95_s": 0.0, "bytes_mean": {}, "skipped": "unsupported_format"}
            continue
        seconds: list[float] = []
        sizes: dict[int, list[int]] = {}
        for index in range(count):
            path = samples[kind][index % len(samples[kind])]
            data = path.read_bytes()
            started = time.perf_counter()
            rendered = renditions.render(data, content_types[path.suffix], title_type=title_type, image_type=image_type, timeout=renditions.PASS_TIMEOUT_SECONDS, ffmpeg=ffmpeg)
            seconds.append(time.perf_counter() - started)
            for width, file in rendered.files.items():
                sizes.setdefault(width, []).append(file.stat().st_size)
            renditions.discard(rendered)
        ordered = sorted(seconds)
        kinds[kind] = {
            "mean_s": round(statistics.fmean(seconds), 4),
            "p95_s": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 4),
            "bytes_mean": {str(width): round(statistics.fmean(values)) for width, values in sorted(sizes.items())},
        }
    hours = sum(PRODUCTION[kind] * kinds[kind]["mean_s"] for kind in KINDS) / 3600
    return {
        "extension": art_urls.extension(), "count": count, "kinds": kinds, "production_sources": PRODUCTION,
        "estimate_hours": round(hours, 2), "within_budget": hours <= BUDGET_HOURS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rendition throughput on sample art.")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    args = parser.parse_args(argv)
    if not args.ffmpeg:
        parser.error("ffmpeg not found; pass --ffmpeg")
    work = Path(tempfile.mkdtemp(prefix="lumina-rendition-bench-"))
    try:
        (work / "data").mkdir()
        os.environ["LUMINA_DATA_DIR"] = str(work / "data")
        sys.path.insert(0, str(ROOT / "backend"))
        print(json.dumps(bench(args.count, args.ffmpeg, work)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
