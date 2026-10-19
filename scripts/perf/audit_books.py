#!/usr/bin/env python3
"""Generate synthetic books and measure archive-reader cold and warm costs.

Run from the repository root:
    mise exec -- backend/.venv/bin/python scripts/perf/audit_books.py
    mise exec -- backend/.venv/bin/python scripts/perf/audit_books.py --fixtures-only DIR --pages 120
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(os.environ.get("LUMINA_AUDIT_BACKEND", ROOT / "backend"))
sys.path.insert(0, str(BACKEND))

from app.services import book_archives, book_native  # noqa: E402
from app.services.book_archives import chapter_nodes, inspect_book, read_resource  # noqa: E402

PAGE_WIDTH = 900
PAGE_HEIGHT = 1300
PAGE_PADDING = 96 * 1024


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = bytes((index * 17) % 256 for index in range(PAGE_WIDTH * 3))
    pixels = (b"\0" + row) * PAGE_HEIGHT
    padding = bytes((index * 73 + 19) % 251 for index in range(PAGE_PADDING))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", PAGE_WIDTH, PAGE_HEIGHT, 8, 2, 0, 0, 0))
        + chunk(b"ruSt", padding)
        + chunk(b"IDAT", zlib.compress(pixels, 6))
        + chunk(b"IEND", b"")
    )


def _rar(path: Path, pages: int, image: bytes) -> None:
    def header(kind: int, flags: int, fields: bytes) -> bytes:
        body = struct.pack("<BHH", kind, flags, 7 + len(fields)) + fields
        return struct.pack("<H", zlib.crc32(body) & 0xFFFF) + body

    with path.open("wb") as output:
        output.write(b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\0" * 6))
        for index in range(pages):
            name = f"{index:04}.png".encode()
            fields = struct.pack(
                "<IIBIIBBHI", len(image), len(image), 3, zlib.crc32(image), 0, 20, 0x30, len(name), 0o100644,
            )
            output.write(header(0x74, 0x8000, fields + name))
            output.write(image)
        output.write(header(0x7B, 0, b""))


def generate_fixtures(destination: Path, pages: int, *, solid_rar_tool: Path | None = None) -> dict[str, Path]:
    if not 2 <= pages <= 2500:
        raise ValueError("pages must be between 2 and 2500")
    destination.mkdir(parents=True, exist_ok=True)
    image = _png()
    cbz = destination / "audit.cbz"
    with zipfile.ZipFile(cbz, "w", zipfile.ZIP_STORED) as archive:
        for index in range(pages):
            archive.writestr(f"{index:04}.png", image)
    cbr = destination / "audit.cbr"
    if solid_rar_tool is None:
        _rar(cbr, pages, image)
    else:
        # Optional fixture-generation tool only; no application/runtime dependency.
        with tempfile.TemporaryDirectory(prefix="lumina-solid-fixture-") as raw:
            inputs = Path(raw)
            for index in range(pages):
                (inputs / f"{index:04}.png").write_bytes(image)
            subprocess.run(
                [str(solid_rar_tool.resolve()), "a", "-cfg-", "-ma5", "-s", "-m3", "-md4m", "-mt1", "-idq", "-ep",
                 str(cbr.resolve()), *[f"{index:04}.png" for index in range(pages)]],
                cwd=inputs, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )

    chapters = []
    manifest = []
    spine = []
    paragraph = "A synthetic household reading benchmark paragraph with enough prose to exercise safe markup parsing. "
    epub = destination / "audit.epub"
    with zipfile.ZipFile(epub, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<container><rootfiles><rootfile full-path='OPS/book.opf'/></rootfiles></container>",
        )
        for index in range(pages):
            identifier = f"chapter-{index}"
            member = f"chapter-{index:04}.xhtml"
            manifest.append(f"<item id='{identifier}' href='{member}' media-type='application/xhtml+xml'/>")
            spine.append(f"<itemref idref='{identifier}'/>")
            body = "".join(f"<p>{index}: {part} {paragraph}</p>" for part in range(400))
            chapters.append((f"OPS/{member}", f"<html><body><h1>Chapter {index + 1}</h1>{body}</body></html>"))
        manifest.append("<item id='cover' href='cover.png' media-type='image/png' properties='cover-image'/>")
        package = (
            "<package><metadata><title>Synthetic Audit Book</title><creator>Benchmark</creator></metadata>"
            f"<manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>"
        )
        archive.writestr("OPS/book.opf", package)
        archive.writestr("OPS/cover.png", image)
        for member, body in chapters:
            archive.writestr(member, body)

    import pymupdf

    pdf = destination / "audit.pdf"
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            page.insert_text((72, 96), f"Synthetic audit page {index + 1}", fontsize=24)
            for offset in range(12):
                shade = (offset % 4) / 5
                page.draw_rect((72 + offset * 6, 150 + offset * 70, 820, 200 + offset * 70), color=(shade, 0.3, 0.7))
        document.set_metadata({"title": "Synthetic Audit PDF", "author": "Benchmark"})
        document.save(pdf)
    return {"epub": epub, "pdf": pdf, "cbz": cbz, "cbr": cbr}


def _cpu_seconds() -> float:
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def _result_bytes(value: Any) -> int:
    if isinstance(value, tuple) and value and isinstance(value[0], bytes):
        return len(value[0])
    if isinstance(value, bytes):
        return len(value)
    return len(json.dumps(value, separators=(",", ":")).encode())


def _measure(call: Callable[[], Any]) -> dict[str, float | int]:
    wall_started = time.perf_counter()
    cpu_started = _cpu_seconds()
    value = call()
    return {
        "wall_ms": round((time.perf_counter() - wall_started) * 1000, 3),
        "cpu_ms": round((_cpu_seconds() - cpu_started) * 1000, 3),
        "bytes": _result_bytes(value),
    }


def _reset_runtime() -> None:
    if hasattr(book_archives, "_RESOURCE_LOCK"):
        with book_archives._RESOURCE_LOCK:
            book_archives._RESOURCE_CACHE.clear()
            book_archives._RESOURCE_LOADS.clear()
            book_archives._RESOURCE_CACHE_SIZE = 0
    if hasattr(book_native, "_VALIDATED_LOCK"):
        with book_native._VALIDATED_LOCK:
            book_native._VALIDATED.clear()


def _median(samples: list[dict[str, float | int]]) -> dict[str, float | int]:
    return {
        "wall_ms": round(statistics.median(float(sample["wall_ms"]) for sample in samples), 3),
        "cpu_ms": round(statistics.median(float(sample["cpu_ms"]) for sample in samples), 3),
        "bytes": int(statistics.median(int(sample["bytes"]) for sample in samples)),
    }


def benchmark(paths: dict[str, Path], pages: int, runs: int) -> dict[str, Any]:
    with paths["cbr"].open("rb") as source:
        solid_rar5 = source.read(8) == b"Rar!\x1a\x07\x01\x00"
    manifests = {format_: inspect_book(path, format_) for format_, path in paths.items()}
    samples: dict[str, dict[str, list[dict[str, float | int]]]] = {}
    for format_, path in paths.items():
        phases = samples[format_] = {
            name: [] for name in ("inspect", "cold_open", "repeat", "next_page", "late_resume", "late_repeat")
        }
        for _ in range(runs):
            _reset_runtime()
            phases["inspect"].append(_measure(lambda path=path, format_=format_: inspect_book(path, format_)))
            _reset_runtime()
            manifest = manifests[format_]
            if format_ == "epub":
                early = lambda: chapter_nodes(path, manifest, 0)
                following = lambda: chapter_nodes(path, manifest, 1)
                late = lambda: chapter_nodes(path, manifest, pages - 1)
            else:
                early = lambda: read_resource(path, manifest["pages"][0])
                following = lambda: read_resource(path, manifest["pages"][1])
                late = lambda: read_resource(path, manifest["pages"][-1])
            phases["cold_open"].append(_measure(early))
            phases["repeat"].append(_measure(early))
            phases["next_page"].append(_measure(following))
            phases["late_resume"].append(_measure(late))
            phases["late_repeat"].append(_measure(late))
    return {
        "benchmark": "books_archive_opening",
        "pages": pages,
        "runs": runs,
        "fixture_bytes": {format_: path.stat().st_size for format_, path in paths.items()},
        "page_image_bytes": len(_png()),
        "fixture_notes": {
            "images": "Valid 900x1300 PNGs use identical deterministic payloads of about 100 KiB.",
            "cbr": ("Synthetic RAR5 uses compressed solid members; repeated artwork is not representative of scan complexity."
                    if solid_rar5 else
                    "Synthetic RAR4 uses stored, non-solid members and does not qualify solid-archive decompression cost."),
        },
        "bounds": {
            "resource_cache_bytes": getattr(book_archives, "RESOURCE_CACHE_BYTES", 0),
            "resource_cache_entries": getattr(book_archives, "RESOURCE_CACHE_ENTRIES", 0),
            "resource_readers": getattr(book_archives, "RESOURCE_READERS", 0),
            "native_validated_files": getattr(book_native, "_VALIDATED_FILES", 0),
            "native_workers": 2,
        },
        "results": {
            format_: {phase: {"median": _median(values), "samples": values} for phase, values in phases.items()}
            for format_, phases in samples.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-only", type=Path, metavar="DIR")
    parser.add_argument("--pages", type=int, default=180)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--solid-rar-tool", type=Path, help="Optional RAR CLI for compressed solid RAR5 fixture generation")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if args.fixtures_only:
        paths = generate_fixtures(args.fixtures_only, args.pages, solid_rar_tool=args.solid_rar_tool)
        print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
        return
    with tempfile.TemporaryDirectory(prefix="lumina-books-audit-") as raw:
        paths = generate_fixtures(Path(raw), args.pages, solid_rar_tool=args.solid_rar_tool)
        report = benchmark(paths, args.pages, args.runs)
    output = args.output or ROOT / "output" / "books-performance" / "audit-books.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Wrote {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
