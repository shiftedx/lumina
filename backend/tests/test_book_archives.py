"""Synthetic EPUB and CBZ coverage for the bounded archive reader."""
from __future__ import annotations

import base64
import os
import struct
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import sleep

import pytest

from app.services.book_archives import BookArchiveError, chapter_nodes, inspect_book, read_resource


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(width: int = 1, height: int = 1) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + _chunk(b"IEND", b"")
    )


def _gif(width: int, height: int, frames: int) -> bytes:
    frame = b"\x2c" + b"\x00" * 4 + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00\x02\x02L\x01\x00"
    return b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00\x00\x00" + frame * frames + b"\x3b"


PNG = _png()


def _zip(path: Path, members: dict[str, bytes], *, encrypted_flag: bool = False) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    if encrypted_flag:
        # zipfile cannot author encrypted data. Set the central-directory encryption bit to model an encrypted input.
        data = bytearray(path.read_bytes())
        central = data.index(b"PK\x01\x02")
        data[central + 8] |= 0x01
        path.write_bytes(data)
    return path


def _epub(path: Path, chapter: bytes | None = None, description: bytes = b" A summary ") -> Path:
    chapter = chapter or b"""<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><h1 id=\"start\">Start</h1><p>Hello <em>reader</em>.</p><p><a href=\"two.xhtml#later\">Next</a><img src=\"images/cover.png\" alt=\"Cover\" /></p></body></html>"""
    return _zip(path, {
        "META-INF/container.xml": b"""<?xml version='1.0'?><container><rootfiles><rootfile full-path='OPS/book.opf'/></rootfiles></container>""",
        "OPS/book.opf": b"""<?xml version='1.0'?><package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title> A Book </dc:title><dc:creator> Author </dc:creator><dc:description>""" + description + b"""</dc:description><dc:language>en</dc:language><dc:subject>Fantasy</dc:subject><meta name='calibre:series' content='Stories'/><meta name='calibre:series_index' content='2.5'/><meta name='cover' content='cover'/></metadata><manifest><item id='one' href='one.xhtml' media-type='application/xhtml+xml'/><item id='two' href='two.xhtml' media-type='application/xhtml+xml'/><item id='cover' href='images/cover.png' media-type='image/png'/><item id='nav' href='nav.xhtml' properties='nav' media-type='application/xhtml+xml'/></manifest><spine page-progression-direction='rtl'><itemref idref='one'/><itemref idref='two'/></spine></package>""",
        "OPS/nav.xhtml": b"""<html><body><nav><a href='one.xhtml'>Beginning</a><a href='two.xhtml'>Later</a></nav></body></html>""",
        "OPS/one.xhtml": chapter,
        "OPS/two.xhtml": b"<html><body><h2 id='later'>Two</h2><p>Second chapter</p></body></html>",
        "OPS/images/cover.png": PNG,
    })


def test_epub_metadata_spine_resources_and_safe_chapter_tree(tmp_path: Path) -> None:
    book = _epub(tmp_path / "book.epub")
    manifest = inspect_book(book, "epub")
    assert manifest == {
        "format": "epub", "title": "A Book", "author": "Author", "description": "A summary", "language": "en",
        "genres": ["Fantasy"], "series_name": "Stories", "volume_number": 2.5,
        "cover_member": "OPS/images/cover.png", "chapters": [
            {"label": "Beginning", "member": "OPS/one.xhtml"}, {"label": "Later", "member": "OPS/two.xhtml"},
        ], "pages": [], "reading_direction": "rtl",
    }
    nodes = chapter_nodes(book, manifest, 0)
    assert nodes[0] == {"tag": "h1", "id": "start", "children": [{"tag": "span", "text": "Start"}]}
    assert nodes[1]["tag"] == "p"
    assert nodes[2]["tag"] == "p"
    assert nodes[2]["children"][0] == {"tag": "a", "children": [{"tag": "span", "text": "Next"}], "chapter": 1, "anchor": "later"}
    assert nodes[2]["children"][1] == {"tag": "img", "member": "OPS/images/cover.png", "alt": "Cover"}
    assert read_resource(book, "OPS/images/cover.png") == (PNG, "image/png")


def test_cbz_uses_natural_page_order_and_comicinfo(tmp_path: Path) -> None:
    book = _zip(tmp_path / "manga.cbz", {
        "10.png": PNG, "2.png": PNG, "1.png": PNG,
        "ComicInfo.xml": b"<ComicInfo><Title> Comic </Title><Writer> Writer </Writer><Series> Series </Series><Number>4</Number><Summary>Summary</Summary><Genre>Action; Comedy</Genre><Manga>YesAndRightToLeft</Manga></ComicInfo>",
    })
    assert inspect_book(book, "cbz") == {
        "format": "cbz", "title": "Comic", "author": "Writer", "description": "Summary", "language": None,
        "genres": ["Action", "Comedy"], "series_name": "Series", "volume_number": 4.0,
        "cover_member": "1.png", "chapters": [], "pages": ["1.png", "2.png", "10.png"], "reading_direction": "rtl",
    }


def test_repeated_resource_reads_reuse_work_and_file_changes_invalidate_cache(tmp_path: Path, monkeypatch) -> None:
    from app.services import book_archives

    book = _zip(tmp_path / "manga.cbz", {"1.png": PNG})
    real_zip = zipfile.ZipFile
    opens = 0

    def counting_zip(*args, **kwargs):
        nonlocal opens
        opens += 1
        return real_zip(*args, **kwargs)

    monkeypatch.setattr(book_archives.zipfile, "ZipFile", counting_zip)
    original_mtime = book.stat().st_mtime_ns
    assert read_resource(book, "1.png") == (PNG, "image/png")
    assert read_resource(book, "1.png") == (PNG, "image/png")
    assert opens == 1

    changed = _png(2, 1)
    with real_zip(book, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("1.png", changed)
    os.utime(book, ns=(original_mtime, original_mtime))
    assert read_resource(book, "1.png") == (changed, "image/png")
    assert opens == 2

    replacement = tmp_path / "replacement.cbz"
    with real_zip(replacement, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("1.png", changed)
        archive.writestr("2.png", changed)
    replacement.replace(book)
    assert read_resource(book, "1.png") == (changed, "image/png")
    assert opens == 3

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _index: read_resource(book, "2.png"), range(4)))
    assert results == [(changed, "image/png")] * 4
    assert opens == 4


@pytest.mark.parametrize(("byte_limit", "entry_limit"), [(len(PNG), 128), (64 * 1024 * 1024, 1)])
def test_resource_cache_enforces_byte_and_entry_bounds(tmp_path: Path, monkeypatch, byte_limit: int, entry_limit: int) -> None:
    from app.services import book_archives

    first = _zip(tmp_path / "first.cbz", {"1.png": PNG})
    second = _zip(tmp_path / "second.cbz", {"1.png": PNG})
    real_zip = zipfile.ZipFile
    opens = 0

    def counting_zip(*args, **kwargs):
        nonlocal opens
        opens += 1
        return real_zip(*args, **kwargs)

    monkeypatch.setattr(book_archives, "RESOURCE_CACHE_BYTES", byte_limit)
    monkeypatch.setattr(book_archives, "RESOURCE_CACHE_ENTRIES", entry_limit)
    monkeypatch.setattr(book_archives.zipfile, "ZipFile", counting_zip)
    assert read_resource(first, "1.png") == (PNG, "image/png")
    assert read_resource(second, "1.png") == (PNG, "image/png")
    assert read_resource(first, "1.png") == (PNG, "image/png")
    assert opens == 3
    assert book_archives._RESOURCE_CACHE_SIZE <= byte_limit
    assert len(book_archives._RESOURCE_CACHE) <= entry_limit


def test_uncached_resource_reads_have_bounded_concurrency(tmp_path: Path, monkeypatch) -> None:
    from app.services import book_archives

    book = _zip(tmp_path / "manga.cbz", {f"{index}.png": PNG for index in range(6)})
    real_read = book_archives._read_resource
    state_lock = Lock()
    active = 0
    peak = 0

    def delayed_read(*args, **kwargs):
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
        sleep(0.02)
        try:
            return real_read(*args, **kwargs)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(book_archives, "_RESOURCE_WORKERS", BoundedSemaphore(2))
    monkeypatch.setattr(book_archives, "_read_resource", delayed_read)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda index: read_resource(book, f"{index}.png"), range(6)))
    assert results == [(PNG, "image/png")] * 6
    assert peak == 2


def test_resource_delivery_rejects_oversized_canvases_and_animation_work(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from app.services import raster_images

    book = _zip(tmp_path / "unsafe.cbz", {
        "huge.png": _png(10_000, 5_000),
        "many.gif": _gif(1000, 1000, 3),
    })
    with pytest.raises(FileNotFoundError):
        read_resource(book, "huge.png")

    monkeypatch.setattr(raster_images, "MAX_FRAMES", 2)
    with pytest.raises(FileNotFoundError):
        read_resource(book, "many.gif")

    monkeypatch.setattr(raster_images, "MAX_FRAMES", 256)
    monkeypatch.setattr(raster_images, "MAX_TOTAL_PIXELS", 2_000_000)
    with pytest.raises(FileNotFoundError):
        read_resource(book, "many.gif")


def test_bounded_animated_resource_remains_available(tmp_path: Path) -> None:
    animated = _gif(100, 100, 2)
    book = _zip(tmp_path / "animated.cbz", {"cover.gif": animated})
    assert read_resource(book, "cover.gif") == (animated, "image/gif")


def test_animation_frames_and_compressed_png_metadata_stay_within_canvas_budgets(tmp_path: Path) -> None:
    bad_gif = _gif(1, 1, 1).replace(b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00", b"\x2c\x00\x00\x00\x00\xe8\x03\xe8\x03")
    png = _png(10, 10)
    after_ihdr = 8 + 12 + 13
    bad_apng = (
        png[:after_ihdr]
        + _chunk(b"acTL", struct.pack(">II", 1, 0))
        + _chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1000, 1000, 0, 0, 1, 1, 0, 0))
        + png[after_ihdr:]
    )
    metadata_bomb = png[:after_ihdr] + _chunk(b"zTXt", b"note\0\0" + zlib.compress(b"x" * (1024 * 1024 + 1))) + png[after_ihdr:]
    bad_webp = bytearray(base64.b64decode(
        "UklGRoQAAABXRUJQVlA4WAoAAAACAAAACAAADAAAQU5JTQYAAAD/////AABBTk1GKAAAAAAAAAAAAAgAAAwAAGQAAAJWUDhMDwAAAC8IAAMABxD1j/4HIqL/AQBBTk1GKAAAAAAAAAAAAAgAAAwAAGQAAABWUDhMDwAAAC8IAAMABxDR//4HIqL/AQA="
    ))
    frame = bad_webp.index(b"ANMF")
    bad_webp[frame + 14:frame + 17] = (999).to_bytes(3, "little")
    book = _zip(tmp_path / "bad-frames.cbz", {
        "bad.gif": bad_gif, "bad.png": bad_apng, "metadata.png": metadata_bomb, "bad.webp": bytes(bad_webp),
    })
    for member in ("bad.gif", "bad.png", "metadata.png", "bad.webp"):
        with pytest.raises(FileNotFoundError):
            read_resource(book, member)


def test_epub_description_cleans_escaped_html_and_keeps_long_paragraphs(tmp_path: Path) -> None:
    long_synopsis = " ".join(f"Meaningful synopsis sentence {index} advances the story." for index in range(40))
    description = (
        f"&lt;p&gt;First paragraph.&lt;/p&gt;&lt;p&gt;{long_synopsis}&lt;/p&gt;"
        "&lt;script&gt;discard this script&lt;/script&gt;"
        "&lt;style&gt;discard these styles&lt;/style&gt;"
        "&lt;p hidden&gt;discard this hidden paragraph&lt;/p&gt;"
        "&lt;span style='display:none'&gt;discard this hidden span&lt;/span&gt;"
    ).encode()
    manifest = inspect_book(_epub(tmp_path / "long.epub", description=description), "epub")

    assert manifest["description"] == f"First paragraph.\n\n{long_synopsis}"
    assert len(manifest["description"]) > 500
    assert "discard" not in manifest["description"]


def test_comicinfo_description_cleans_escaped_html(tmp_path: Path) -> None:
    description = b"&lt;p&gt;Comic opening.&lt;/p&gt;&lt;p&gt;Comic continuation.&lt;/p&gt;&lt;script&gt;discarded&lt;/script&gt;"
    book = _zip(tmp_path / "manga.cbz", {
        "1.png": PNG,
        "ComicInfo.xml": b"<ComicInfo><Summary>" + description + b"</Summary></ComicInfo>",
    })

    assert inspect_book(book, "cbz")["description"] == "Comic opening.\n\nComic continuation."


def test_chapter_markup_drops_active_hidden_and_remote_content(tmp_path: Path) -> None:
    book = _epub(tmp_path / "unsafe.epub", b"<html><body><p>Visible<script>alert(1)</script> text</p><p hidden>secret</p><style>p{color:red}</style><a href='https://bad.example'>remote</a><img src='javascript:x'/><strong>good</strong></body></html>")
    nodes = chapter_nodes(book, inspect_book(book, "epub"), 0)
    assert nodes == [
        {"tag": "p", "children": [{"tag": "span", "text": "Visible"}, {"tag": "span", "text": " text"}]},
        {"tag": "span", "text": "remote"}, {"tag": "strong", "children": [{"tag": "span", "text": "good"}]},
    ]


def test_chapter_html_fallback_keeps_ordinary_malformed_markup(tmp_path: Path) -> None:
    book = _epub(tmp_path / "loose.epub", b"<html><body><h2>Loose</h2><p>Still readable <em>text</body></html>")
    assert chapter_nodes(book, inspect_book(book, "epub"), 0) == [
        {"tag": "h2", "children": [{"tag": "span", "text": "Loose"}]},
        {"tag": "p", "children": [{"tag": "span", "text": "Still readable "}, {"tag": "em", "children": [{"tag": "span", "text": "text"}]}]},
    ]


@pytest.mark.parametrize("members,format", [
    ({"../escape.png": PNG}, "cbz"),
    ({"/absolute.png": PNG}, "cbz"),
    ({"only.txt": b"not a page"}, "cbz"),
])
def test_bad_cbz_members_and_missing_pages_are_rejected(tmp_path: Path, members: dict[str, bytes], format: str) -> None:
    book = _zip(tmp_path / "bad.cbz", members)
    with pytest.raises(BookArchiveError):
        inspect_book(book, format)


def test_bad_archive_oversize_encryption_and_private_resources(tmp_path: Path) -> None:
    (tmp_path / "bad.epub").write_bytes(b"not a zip")
    with pytest.raises(BookArchiveError):
        inspect_book(tmp_path / "bad.epub", "epub")
    huge = _zip(tmp_path / "huge.cbz", {"page.png": b"x" * (24 * 1024 * 1024 + 1)})
    with pytest.raises(BookArchiveError, match="oversized"):
        inspect_book(huge, "cbz")
    encrypted = _zip(tmp_path / "encrypted.cbz", {"page.png": PNG}, encrypted_flag=True)
    with pytest.raises(BookArchiveError, match="Encrypted"):
        inspect_book(encrypted, "cbz")
    book = _epub(tmp_path / "normal.epub")
    with pytest.raises(FileNotFoundError):
        read_resource(book, "OPS/one.xhtml")
    with pytest.raises(FileNotFoundError):
        read_resource(book, "../outside.png")
    fake = _zip(tmp_path / "fake.cbz", {"page.png": b"<script>alert(1)</script>"})
    with pytest.raises(FileNotFoundError):
        read_resource(fake, "page.png")


def test_inline_prose_preserves_word_boundaries_and_fragment_destinations(tmp_path: Path) -> None:
    book = _epub(tmp_path / "prose.epub", b"<html><body><div id='scene'><p>Hello <em>reader</em>, and welcome.</p><a href='#sc%65ne'>Return</a><img src='images/cover%2Epng'/></div></body></html>")
    nodes = chapter_nodes(book, inspect_book(book, "epub"), 0)
    assert nodes[0] == {"tag": "span", "id": "scene"}
    assert nodes[1]["children"] == [
        {"tag": "span", "text": "Hello "},
        {"tag": "em", "children": [{"tag": "span", "text": "reader"}]},
        {"tag": "span", "text": ", and welcome."},
    ]
    assert nodes[2]["anchor"] == "scene"
    assert nodes[3]["member"] == "OPS/images/cover.png"


def test_loose_html_omits_head_text_and_rejects_excessive_nesting(tmp_path: Path) -> None:
    book = _epub(tmp_path / "head.epub", b"<!DOCTYPE html><html><head><title>Hidden title</title></head><body><p>Readable <em>text</body></html>")
    assert chapter_nodes(book, inspect_book(book, "epub"), 0) == [
        {"tag": "p", "children": [{"tag": "span", "text": "Readable "}, {"tag": "em", "children": [{"tag": "span", "text": "text"}]}]},
    ]
    deep = _epub(tmp_path / "deep.epub", b"<html><body>" + b"<div>" * 150 + b"text" + b"</div>" * 150 + b"</body></html>")
    with pytest.raises(BookArchiveError, match="nested"):
        chapter_nodes(deep, inspect_book(deep, "epub"), 0)


def test_long_paragraph_is_not_silently_abridged(tmp_path: Path) -> None:
    prose = "A long quiet passage. " * 1000
    book = _epub(tmp_path / "long.epub", f"<html><body><p>{prose}</p></body></html>".encode())
    assert chapter_nodes(book, inspect_book(book, "epub"), 0)[0]["children"][0]["text"] == prose


def test_staged_archive_uses_original_filename_as_missing_title(tmp_path: Path) -> None:
    staged = _zip(tmp_path / ".upload-random.tmp", {"1.png": PNG})
    assert inspect_book(staged, "cbz", filename="Harbor, volume 1.cbz")["title"] == "Harbor, volume 1"


@pytest.mark.parametrize('filename,series,number', [
    ('Harbor v03 (2020).cbz', 'Harbor', 3),
    ('Harbor 012 (2020) (Digital).cbz', 'Harbor', 12),
    ('Harbor Vol. 2 Evening (2020).cbz', 'Harbor', 2),
    ('Harbor.cbz', None, None),
    ('1984.cbz', None, None),
])
def test_missing_comic_series_uses_explicit_filename_number(tmp_path, filename, series, number):
    staged = _zip(tmp_path / '.upload.tmp', {'1.png': PNG})
    manifest = inspect_book(staged, 'cbz', filename=filename)
    assert manifest['series_name'] == series
    assert manifest['volume_number'] == number
