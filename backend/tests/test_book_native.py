from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pymupdf
import pytest

from app import book_models  # noqa: F401 - register tables before db_factory creates its schema
from app.services.book_archives import BookArchiveError, inspect_book, read_resource


def rar(members: dict[str, bytes], *, attributes: int = 0o100644) -> bytes:
    """Synthetic RAR4 stored members, so tests need no proprietary archive writer."""
    def header(kind: int, flags: int, fields: bytes) -> bytes:
        body = struct.pack('<BHH', kind, flags, 7 + len(fields)) + fields
        return struct.pack('<H', zlib.crc32(body) & 0xffff) + body

    data = b'Rar!\x1a\x07\x00' + header(0x73, 0, b'\x00' * 6)
    for name, body in members.items():
        encoded = name.encode()
        fields = struct.pack('<IIBIIBBHI', len(body), len(body), 3, zlib.crc32(body), 0, 20, 0x30, len(encoded), attributes)
        data += header(0x74, 0x8000, fields + encoded) + body
    return data + header(0x7b, 0, b'')


def pdf(*, encrypted: bool = False) -> bytes:
    with pymupdf.open() as document:
        for text in ['First page', 'Second page']:
            document.new_page().insert_text((50, 50), text)
        document.set_metadata({'title': 'Paper Book', 'author': 'Reader'})
        return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader') if encrypted else document.tobytes()


def test_pdf_metadata_raster_pages_and_encryption(tmp_path: Path) -> None:
    path = tmp_path / 'book.pdf'; path.write_bytes(pdf())
    manifest = inspect_book(path, 'pdf')
    assert manifest['title'] == 'Paper Book'
    assert manifest['author'] == 'Reader'
    assert manifest['pages'] == ['page-0.png', 'page-1.png']
    image, mime = read_resource(path, 'page-1.png')
    assert image.startswith(b'\x89PNG\r\n\x1a\n') and mime == 'image/png'
    for member in ['page-2.png', 'page--1.png', '../page-0.png', 'document.pdf', 'page-01.png']:
        with pytest.raises(FileNotFoundError): read_resource(path, member)
    path.write_bytes(pdf(encrypted=True))
    with pytest.raises(BookArchiveError): inspect_book(path, 'pdf')
    path.write_bytes(b'not a PDF')
    with pytest.raises(BookArchiveError): inspect_book(path, 'pdf')


def test_cbr_comicinfo_natural_pages_and_safety(tmp_path: Path) -> None:
    path = tmp_path / 'comic.cbr'
    image = b'\x89PNG\r\n\x1a\nsmall'
    path.write_bytes(rar({'10.png': image, '2.png': image, 'ComicInfo.xml': b'<ComicInfo><Series>Stars</Series><Number>2</Number><Manga>YesAndRightToLeft</Manga></ComicInfo>'}))
    manifest = inspect_book(path, 'cbr', filename='Stars 2.cbr')
    assert manifest['format'] == 'cbr' and manifest['title'] == 'Stars 2'
    assert manifest['pages'] == ['2.png', '10.png']
    assert manifest['series_name'] == 'Stars' and manifest['volume_number'] == 2
    assert manifest['reading_direction'] == 'rtl'
    assert read_resource(path, '2.png') == (image, 'image/png')
    for members in [{'../escape.png': image}, {'fake.png': b'active content'}, {'only.txt': b'no pages'}]:
        path.write_bytes(rar(members))
        if 'fake.png' in members:
            with pytest.raises(FileNotFoundError): read_resource(path, 'fake.png')
        else:
            with pytest.raises(BookArchiveError): inspect_book(path, 'cbr')
    path.write_bytes(rar({'link.png': image}, attributes=0o120777))
    with pytest.raises(BookArchiveError): inspect_book(path, 'cbr')


def test_native_timeout_and_output_limit_are_enforced(tmp_path: Path, monkeypatch) -> None:
    import subprocess
    from app.services import book_native

    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == 30
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(book_native.subprocess, 'run', timeout)
    with pytest.raises(BookArchiveError): book_native.native_book(tmp_path / 'book.pdf', 'pdf')

    def oversized(*args, **kwargs):
        kwargs['stdout'].write(b'x' * (book_native.MAX_IMAGE_BYTES + 1))

    monkeypatch.setattr(book_native.subprocess, 'run', oversized)
    with pytest.raises(BookArchiveError): book_native.native_book(tmp_path / 'book.pdf', 'pdf', 'page-0.png')


def test_pdf_page_limit_and_cbr_member_limit(tmp_path: Path) -> None:
    path = tmp_path / 'book.pdf'
    with pymupdf.open() as document:
        for _ in range(2501): document.new_page()
        document.save(path)
    with pytest.raises(BookArchiveError): inspect_book(path, 'pdf')
    path = tmp_path / 'book.cbr'
    path.write_bytes(rar({'huge.png': b'x' * (24 * 1024 * 1024 + 1)}))
    with pytest.raises(BookArchiveError): inspect_book(path, 'cbr')


@pytest.mark.parametrize('filename,body,kind', [('paper.pdf', pdf(), 'ebook'), ('comic.cbr', rar({'1.png': b'\x89PNG\r\n\x1a\nsmall', '2.png': b'\x89PNG\r\n\x1a\nsmall'}), 'manga')], ids=['pdf', 'cbr'])
def test_native_import_private_reader_and_checkpoint(api_client, db_factory, filename, body, kind) -> None:
    from test_books_api import _user

    user = _user(db_factory, 'native-owner')
    stranger = _user(db_factory, 'native-stranger')
    with api_client(user=user, base_url="http://localhost") as client:
        response = client.put(f'/api/books/import?filename={filename}', content=body)
        assert response.status_code == 201, response.text
        book = response.json()
        assert book['kind'] == kind and book['visibility'] == 'private' and book['page_count'] == 2
        book_id = book['id']
        assert client.get(f'/api/books/{book_id}/cover').status_code == 200
        assert client.get(f'/api/books/{book_id}/pages/1').status_code == 200
        assert client.get(f'/api/books/{book_id}/chapters/0').status_code == 404
        assert client.patch(f'/api/books/{book_id}/state', json={'unit_index': 1}).json()['progress']['percent'] == 50
        assert client.patch(f'/api/books/{book_id}/state', json={'unit_index': 2}).status_code == 422
    with api_client(user=stranger, base_url="http://localhost") as client:
        for suffix in ['', '/reader', '/cover', '/pages/0']:
            assert client.get(f'/api/books/{book_id}{suffix}').status_code == 404
