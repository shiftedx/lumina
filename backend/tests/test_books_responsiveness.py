"""Slow archive inspection must not freeze other household HTTP requests."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

from app.services.books import BookService
from test_books_api import _cbz, _user


def test_book_import_allows_requests_while_native_inspection_is_busy(api_client, db_factory, monkeypatch):
    owner = _user(db_factory, 'responsive-book-owner')
    entered, release = Event(), Event()
    inspect = BookService.inspect_staged

    def slow_inspect(self, path, filename):
        entered.set()
        if not release.wait(10):
            raise AssertionError('inspection was not released')
        return inspect(self, path, filename)

    monkeypatch.setattr(BookService, 'inspect_staged', slow_inspect)
    with api_client(user=owner, base_url='http://localhost') as client, ThreadPoolExecutor(max_workers=2) as workers:
        upload = workers.submit(client.put, '/api/books/import?filename=responsive.cbz', content=_cbz())
        try:
            assert entered.wait(5), 'upload never reached archive inspection'
            health = workers.submit(client.get, '/api/health')
            try:
                response = health.result(timeout=2)
            except TimeoutError:
                raise AssertionError('archive inspection blocked unrelated household requests') from None
            assert response.status_code == 200
            assert not upload.done(), 'inspection should still be waiting'
        finally:
            release.set()
        response = upload.result(timeout=5)
        assert response.status_code == 201, response.text
        assert client.get(f"/api/books/{response.json()['id']}/pages/0").status_code == 200
