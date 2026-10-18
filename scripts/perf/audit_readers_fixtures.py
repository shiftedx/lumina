"""Synthetic, redistributable fixtures for the non-video media audit."""
from __future__ import annotations

import subprocess
import struct
import tempfile
import zipfile
import zlib
import os
from pathlib import Path


def _ffmpeg(*args: str | Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)], check=True)


def _track(path: Path, number: int, codec: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = [
        "-metadata", f"title=Audit Track {number}", "-metadata", "artist=Audit Artist",
        "-metadata", "album_artist=Audit Artist", "-metadata", "album=Audit Album",
        "-metadata", f"track={number}/3", "-metadata", "date=2026",
    ]
    _ffmpeg("-f", "lavfi", "-i", f"sine=frequency={330 + number * 55}:duration=12",
            *{"flac": ["-c:a", "flac"], "mp3": ["-c:a", "libmp3lame", "-q:a", "4"], "m4a": ["-c:a", "aac"], "opus": ["-c:a", "libopus"]}[codec], *metadata, path)


def _long_track(path: Path) -> None:
    """A 12:48 stereo 48 kHz FLAC whose decoded PCM estimate exceeds 128 MiB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=220:duration=768",
        "-ac", "2", "-ar", "48000", "-c:a", "flac",
        "-metadata", "title=Audit Long Track", "-metadata", "artist=Audit Long Artist",
        "-metadata", "album_artist=Audit Long Artist", "-metadata", "album=Audit Long Album",
        "-metadata", "track=1/1", "-metadata", "date=2026", path,
    )


def _png(path: Path, index: int) -> None:
    """Use the installed PyMuPDF renderer to produce a real, moderately sized page image."""
    import fitz

    document = fitz.open()
    page = document.new_page(width=900, height=1300)
    page.draw_rect(page.rect, color=None, fill=(0.06 * (index % 8), 0.18, 0.33 + 0.05 * (index % 5)))
    page.insert_text((70, 120), f"Audit page {index}", fontsize=48, color=(1, 1, 1))
    page.insert_textbox(fitz.Rect(70, 180, 830, 1120), "\n\n".join(["Synthetic panel content for decoder and navigation measurement."] * 28), fontsize=18, color=(0.94, 0.94, 0.94))
    page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(path)
    document.close()


def _epub(path: Path, chapters: int = 36) -> None:
    spine = "".join(f'<itemref idref="chapter-{index}"/>' for index in range(1, chapters + 1))
    manifest = "".join(f'<item id="chapter-{index}" href="chapter-{index}.xhtml" media-type="application/xhtml+xml"/>' for index in range(1, chapters + 1))
    nav = "".join(f'<li><a href="chapter-{index}.xhtml">Chapter {index}</a></li>' for index in range(1, chapters + 1))
    paragraph = "Synthetic prose gives this reader a stable parse and layout workload without private text. " * 18
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", "<?xml version='1.0'?><container><rootfiles><rootfile full-path='OPS/book.opf'/></rootfiles></container>")
        archive.writestr("OPS/book.opf", f"<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>Audit EPUB</dc:title><dc:creator>Lumina</dc:creator><dc:language>en</dc:language></metadata><manifest>{manifest}<item id='nav' href='nav.xhtml' properties='nav' media-type='application/xhtml+xml'/></manifest><spine>{spine}</spine></package>")
        archive.writestr("OPS/nav.xhtml", f"<html><body><nav><ol>{nav}</ol></nav></body></html>")
        for index in range(1, chapters + 1):
            archive.writestr(f"OPS/chapter-{index}.xhtml", f"<html><body><h1>Chapter {index}</h1><p>{paragraph}</p><p>{paragraph}</p></body></html>")


def _pdf(path: Path, pages: int = 72) -> None:
    import fitz

    document = fitz.open()
    text = "Synthetic PDF prose gives this reader a stable render workload without private text. " * 20
    for index in range(1, pages + 1):
        page = document.new_page(width=612, height=792)
        if index == 1:
            document.set_metadata({"title": "Audit PDF", "author": "Lumina"})
        page.insert_text((48, 60), f"Audit PDF page {index}", fontsize=20)
        page.insert_textbox(fitz.Rect(48, 100, 564, 730), text, fontsize=11)
    document.save(path)
    document.close()


def _cbz(path: Path, pages: int = 28) -> None:
    with tempfile.TemporaryDirectory(prefix="lumina-audit-cbz-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index in range(1, pages + 1):
                image = root / f"{index:03d}.png"
                _png(image, index)
                archive.write(image, f"pages/{image.name}")
            archive.writestr("ComicInfo.xml", "<ComicInfo><Title>Audit CBZ</Title><Writer>Lumina</Writer><Manga>YesAndRightToLeft</Manga></ComicInfo>")


def _rar(members: dict[str, bytes]) -> bytes:
    """Minimal RAR4 stored-member writer from the checked-in native-reader fixture."""
    def header(kind: int, flags: int, fields: bytes) -> bytes:
        body = struct.pack("<BHH", kind, flags, 7 + len(fields)) + fields
        return struct.pack("<H", zlib.crc32(body) & 0xffff) + body

    data = b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\x00" * 6)
    for name, body in members.items():
        encoded = name.encode()
        fields = struct.pack("<IIBIIBBHI", len(body), len(body), 3, zlib.crc32(body), 0, 20, 0x30, len(encoded), 0o100644)
        data += header(0x74, 0x8000, fields + encoded) + body
    return data + header(0x7B, 0, b"")


def _cbr(path: Path, pages: int = 16) -> None:
    with tempfile.TemporaryDirectory(prefix="lumina-audit-cbr-") as temporary:
        root = Path(temporary)
        members: dict[str, bytes] = {}
        for index in range(1, pages + 1):
            image = root / f"{index:03d}.png"
            _png(image, index)
            members[f"pages/{image.name}"] = image.read_bytes()
        members["ComicInfo.xml"] = b"<ComicInfo><Title>Audit CBR</Title><Writer>Lumina</Writer><Manga>YesAndRightToLeft</Manga></ComicInfo>"
        path.write_bytes(_rar(members))


def build(root: Path) -> None:
    """Create media and reader fixtures using the native-reader test's minimal RAR4 writer."""
    media = root / "media" / "Music" / "Audit Artist" / "Audit Album (2026)"
    for index, codec in enumerate(("flac", "mp3", "m4a", "opus"), start=1):
        _track(media / f"{index:02d} - Audit Track {index}.{codec}", index, codec)
    if os.environ.get("AUDIT_READERS_LONG_FORM") == "1":
        _long_track(root / "media" / "Music" / "Audit Long Artist" / "Audit Long Album (2026)" / "01 - Audit Long Track.flac")
    books = root / "books-fixtures"
    books.mkdir(parents=True, exist_ok=True)
    # Distinct stems deliberately prevent the product's compatible-format bundler
    # from collapsing the separate reader-format journeys into one logical title.
    _epub(books / "audit-epub.epub")
    _pdf(books / "audit-pdf.pdf")
    _cbz(books / "audit-cbz.cbz")
    _cbr(books / "audit-cbr.cbr")
