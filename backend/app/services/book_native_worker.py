"""Bounded one-operation worker. Never extracts archives or returns active PDF content."""
from __future__ import annotations

import json
import math
import resource
import sys
from pathlib import Path

from app.services.book_archives import (
    MAX_IMAGE_BYTES, MAX_MEMBERS, MAX_METADATA_BYTES, MAX_TOTAL_UNCOMPRESSED,
    BookArchiveError, _cbz, _clean, _is_image, _image_type, _safe_name,
)


def _pdf(path: Path, member: str) -> dict | bytes:
    import pymupdf

    with pymupdf.open(path, filetype="pdf") as document:
        if document.needs_pass or document.metadata.get("encryption") or not 0 < document.page_count <= 2500:
            raise BookArchiveError("PDF is encrypted or has too many pages")
        if not member:
            metadata = document.metadata
            return {
                "format": "pdf", "title": _clean(metadata.get("title")) or _clean(path.stem),
                "author": _clean(metadata.get("author")), "description": _clean(metadata.get("subject")),
                "language": None, "genres": [], "series_name": None, "volume_number": None,
                "cover_member": "page-0.png", "chapters": [],
                "pages": [f"page-{index}.png" for index in range(document.page_count)], "reading_direction": "ltr",
            }
        import re
        match = re.fullmatch(r"page-(0|[1-9][0-9]*)\.png", member)
        if not match or int(match[1]) >= document.page_count:
            raise BookArchiveError("PDF page is unavailable")
        page = document[int(match[1])]
        width, height = page.rect.width, page.rect.height
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise BookArchiveError("PDF page dimensions are invalid")
        scale = min(2, 2400 / max(width, height))
        data = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False).tobytes("png")
        if len(data) > MAX_IMAGE_BYTES:
            raise BookArchiveError("PDF page exceeds the allowed size")
        return data


def _cbr(path: Path, member: str) -> dict | bytes:
    import libarchive

    with path.open("rb") as handle:
        if handle.read(7) not in {b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01"}:
            raise BookArchiveError("CBR is not a RAR archive")
    info: dict = {}
    comic: dict[str, bytes] = {}
    result = None
    total = 0
    with libarchive.file_reader(str(path)) as archive:
        for count, entry in enumerate(archive, 1):
            if count > MAX_MEMBERS:
                raise BookArchiveError("Book archive has too many members")
            name = _safe_name(entry.pathname.rstrip("/"))
            if entry.isdir:
                continue
            if not entry.isfile or entry.issym or entry.islnk or name in info:
                raise BookArchiveError("Book archive has links or duplicate members")
            if entry.size < 0 or entry.size > MAX_IMAGE_BYTES:
                raise BookArchiveError("Book archive has an oversized member")
            info[name] = entry
            # Consume every member to validate real decompressed sizes, including solid RARs.
            capture = name == member or name.casefold() == "comicinfo.xml"
            limit = MAX_METADATA_BYTES if name.casefold() == "comicinfo.xml" else MAX_IMAGE_BYTES
            chunks = []
            size = 0
            for block in entry.get_blocks():
                size += len(block); total += len(block)
                if size > limit or total > MAX_TOTAL_UNCOMPRESSED:
                    raise BookArchiveError("Book archive exceeds the allowed size")
                if capture:
                    chunks.append(block)
            if capture:
                data = b"".join(chunks)
                if name == member:
                    result = data
                if name.casefold() == "comicinfo.xml":
                    comic[name] = data
    if member:
        mime = _image_type(member)
        if result is None or mime is None or not _is_image(result, mime):
            raise BookArchiveError("Book image is unavailable")
        return result

    class ComicArchive:
        def __init__(self):
            self.info = info

        def read(self, name: str, limit: int) -> bytes:
            return comic[name]

    manifest = _cbz(ComicArchive(), path)
    manifest["format"] = "cbr"
    return manifest


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_IMAGE_BYTES, MAX_IMAGE_BYTES))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    path, format_, member = sys.argv[1:]
    result = _pdf(Path(path), member) if format_ == "pdf" else _cbr(Path(path), member)
    sys.stdout.buffer.write(result if isinstance(result, bytes) else json.dumps(result).encode())


if __name__ == "__main__":
    main()
