"""Reader coordinate and ambiguous matching regressions for format bundles."""
from test_book_bundles import _import
from test_book_native import pdf
from test_books_api import _epub, _user


def test_primary_reader_uses_its_own_checkpoint_after_another_format_was_read(api_client, db_factory) -> None:
    owner = _user(db_factory, 'coordinates-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        epub = _import(client, 'Story.epub', _epub())
        pdf_book = _import(client, 'Story.pdf', pdf())
        client.patch(f"/api/books/{epub['id']}/state", json={'unit_index': 0, 'offset': 0.25})
        client.patch(f"/api/books/{pdf_book['id']}/state", json={'unit_index': 1})
        logical = client.get(f"/api/books/{epub['id']}").json()
        assert logical['reading_file_id'] == pdf_book['id']
        reader = client.get(f"/api/books/{epub['id']}/reader").json()['book']
        assert reader['progress']['unit_index'] == 0
        assert reader['progress']['offset'] == 0.25
        assert reader['progress']['percent'] == 25
        pdf_reader = client.get(f"/api/books/{pdf_book['id']}/reader").json()['book']
        assert pdf_reader['progress']['unit_index'] == 1


def test_match_sweep_rejects_a_pdf_that_matches_two_epub_entries(api_client, db_factory) -> None:
    owner = _user(db_factory, 'ambiguous-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        books = [_import(client, 'a.epub', _epub()), _import(client, 'b.epub', _epub()), _import(client, 'c.pdf', pdf())]
        for book in books:
            client.patch(f"/api/books/{book['id']}", json={'filename': 'Story.' + book['format']})
        result = client.post('/api/books/bundle-matches').json()
        assert result == {'combined': 0, 'remaining': 3}
        assert client.get('/api/books').json()['total'] == 3


def test_catalog_bundle_queries_do_not_grow_per_book(api_client, db_factory) -> None:
    from sqlalchemy import event

    from app.book_models import BookItem

    owner = _user(db_factory, 'batch-owner')
    counts = []
    for size in [8, 24]:
        with db_factory() as db:
            existing = db.query(BookItem).count()
            for index in range(existing, size):
                db.add(BookItem(id=f'batch-{index}', storage_key=f'batch-{index}.epub', format='epub', kind='ebook',
                                title=f'Book {index}', filename=f'Book {index}.epub', author='Author',
                                owner_user_id=owner.id, visibility='private', reading_direction='ltr',
                                genres=[], manifest={'chapters': [{'member': 'chapter.xhtml'}]}))
            db.commit()
            engine = db.get_bind()
        queries = []

        def count_query(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith('SELECT'):
                queries.append(statement)

        event.listen(engine, 'before_cursor_execute', count_query)
        try:
            with api_client(user=owner, base_url='http://localhost') as client:
                response = client.get('/api/books?limit=40')
                assert response.status_code == 200
                assert response.json()['total'] == size
        finally:
            event.remove(engine, 'before_cursor_execute', count_query)
        counts.append(len(queries))
    assert counts[1] <= counts[0] + 2
