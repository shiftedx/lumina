"""Synthetic EPUB and CBZ coverage for the bounded archive reader."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services.book_archives import BookArchiveError, chapter_nodes, inspect_book, read_resource


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20


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


def _epub(path: Path, chapter: bytes | None = None) -> Path:
    chapter = chapter or b"""<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><h1 id=\"start\">Start</h1><p>Hello <em>reader</em>.</p><p><a href=\"two.xhtml#later\">Next</a><img src=\"images/cover.png\" alt=\"Cover\" /></p></body></html>"""
    return _zip(path, {
        "META-INF/container.xml": b"""<?xml version='1.0'?><container><rootfiles><rootfile full-path='OPS/book.opf'/></rootfiles></container>""",
        "OPS/book.opf": b"""<?xml version='1.0'?><package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title> A Book </dc:title><dc:creator> Author </dc:creator><dc:description> A summary </dc:description><dc:language>en</dc:language><dc:subject>Fantasy</dc:subject><meta name='calibre:series' content='Stories'/><meta name='calibre:series_index' content='2.5'/><meta name='cover' content='cover'/></metadata><manifest><item id='one' href='one.xhtml' media-type='application/xhtml+xml'/><item id='two' href='two.xhtml' media-type='application/xhtml+xml'/><item id='cover' href='images/cover.png' media-type='image/png'/><item id='nav' href='nav.xhtml' properties='nav' media-type='application/xhtml+xml'/></manifest><spine page-progression-direction='rtl'><itemref idref='one'/><itemref idref='two'/></spine></package>""",
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
