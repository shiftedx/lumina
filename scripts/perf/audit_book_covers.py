#!/usr/bin/env python3
"""Measure static cover bytes/pixels and identity-cache reuse, including a large scan stress fixture."""
from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import struct
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.services import book_covers, raster_images  # noqa: E402
from audit_books import generate_fixtures  # noqa: E402


def scan_png() -> bytes:
    """Seeded grayscale noise stresses bytes/pixels; it is not representative comic artwork."""
    width, height = 2700, 3900
    pixels = random.Random(42).randbytes(width * height)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    rows = b"".join(b"\0" + pixels[row * width:(row + 1) * width] for row in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/books-performance/cover-scan-qualification.json")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("ffmpeg is required for the cover rendition benchmark")
    observations = []
    with tempfile.TemporaryDirectory(prefix="lumina-cover-audit-") as raw:
        root = Path(raw)
        settings.data_dir = root / "app-data"
        settings.data_dir.mkdir()
        fixtures = generate_fixtures(root / "fixtures", 2)
        scan = scan_png()
        large = root / "scan.cbz"
        with zipfile.ZipFile(large, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("scan.png", scan)
        from app.services.book_archives import inspect_book, read_resource

        for name, path in [*fixtures.items(), ("large_scan_cbz", large)]:
            format_ = path.suffix[1:]
            member = inspect_book(path, format_)["cover_member"]
            source, source_mime = read_resource(path, member)
            source_size = raster_images.image_shape(source, source_mime)[:2]
            started = time.perf_counter()
            deadline = started + 35
            while True:
                try:
                    image, mime = book_covers.read_cover(path, format_, member, ffmpeg)
                    break
                except book_covers.BookCoverPreparing:
                    if time.perf_counter() >= deadline:
                        raise RuntimeError("Cover generation did not complete within its qualification window") from None
                    time.sleep(0.025)
            cold_ms = (time.perf_counter() - started) * 1000
            size = raster_images.image_size(image)
            assert mime == "image/jpeg" and size and max(size) <= 600 and len(image) <= 1024 * 1024
            warm = []
            for _ in range(5):
                at = time.perf_counter()
                assert book_covers.read_cover(path, format_, member, ffmpeg) == (image, mime)
                warm.append((time.perf_counter() - at) * 1000)
            observations.append({"fixture": name, "source_bytes": len(source), "source_dimensions": source_size,
                                 "cover_bytes": len(image), "cover_dimensions": size, "generation_observed_ms": cold_ms,
                                 "warm_disk_median_ms": statistics.median(warm)})
    report = {"observations": observations, "note": "Synthetic deterministic artwork plus a 10.53 MP grayscale-noise stress fixture. Dimensions/bytes are measured; they are not process RSS or scan-quality measurements. Generation includes bounded pending polling; OS cache uncontrolled."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
