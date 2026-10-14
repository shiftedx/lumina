import pytest

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


@pytest.mark.parametrize('formats', [('epub', 'epub', 'pdf'), ('epub', 'pdf', 'epub')])
def test_match_sweep_rejects_a_pdf_that_matches_two_epub_entries(api_client, db_factory, formats) -> None:
    owner = _user(db_factory, 'ambiguous-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        books = [_import(client, f'{index}.{format_}', _epub() if format_ == 'epub' else pdf()) for index, format_ in enumerate(formats)]
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


def test_ongoing_format_takes_precedence_over_a_completed_format(api_client, db_factory) -> None:
    owner = _user(db_factory, 'completion-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        epub = _import(client, 'Story.epub', _epub())
        pdf_book = _import(client, 'Story.pdf', pdf())
        client.patch(f"/api/books/{pdf_book['id']}/state", json={'unit_index': 1})
        client.patch(f"/api/books/{epub['id']}/state", json={'unit_index': 0, 'completed': True})
        logical = client.get(f"/api/books/{epub['id']}").json()
        assert not logical['progress']['completed']
        assert logical['reading_file_id'] == pdf_book['id']
        assert logical['progress']['unit_index'] == 1
        assert client.get('/api/books?status=reading').json()['total'] == 1
        client.patch(f"/api/books/{epub['id']}/state", json={'completed': True})
        assert client.get('/api/books?status=reading').json()['total'] == 0
        assert client.get('/api/books?status=read').json()['total'] == 1


def test_child_file_is_separated_before_it_can_be_trashed_and_keeps_its_progress(api_client, db_factory) -> None:
    owner = _user(db_factory, 'separation-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        epub = _import(client, 'Story.epub', _epub())
        pdf_book = _import(client, 'Story.pdf', pdf())
        client.patch(f"/api/books/{pdf_book['id']}/state", json={'unit_index': 1, 'favorite': True})
        shelf = client.post('/api/books/shelves', json={'name': 'Favorites'}).json()
        client.put(f"/api/books/shelves/{shelf['id']}/entries", json={'book_ids': [epub['id']]})
        assert client.delete(f"/api/books/{pdf_book['id']}").status_code == 422
        separated = client.post(f"/api/books/{pdf_book['id']}/separate").json()
        assert separated['canonical_book_id'] == pdf_book['id']
        assert separated['progress']['unit_index'] == 1
        assert separated['favorite']
        assert client.get('/api/books').json()['total'] == 2
        assert client.get('/api/books/shelves').json()[0]['book_ids'] == [epub['id'], pdf_book['id']]
        assert client.delete(f"/api/books/{pdf_book['id']}").status_code == 204
        assert [book['id'] for book in client.get('/api/books/trash').json()] == [pdf_book['id']]
        assert client.post(f"/api/books/{pdf_book['id']}/restore").status_code == 200
        assert client.get(f"/api/books/{pdf_book['id']}/pages/1").status_code == 200


def test_continue_reading_chooses_an_available_file(api_client, db_factory) -> None:
    from app.services.books import stored_path
    from app.book_models import BookItem

    owner = _user(db_factory, 'available-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        epub = _import(client, 'Story.epub', _epub())
        pdf_book = _import(client, 'Story.pdf', pdf())
        client.patch(f"/api/books/{pdf_book['id']}/state", json={'unit_index': 1})
        with db_factory() as db:
            stored_path(db.get(BookItem, pdf_book['id']).storage_key).unlink()
        logical = client.get(f"/api/books/{epub['id']}").json()
        assert logical['available']
        assert logical['reading_file_id'] == epub['id']
        assert not next(file for file in logical['files'] if file['id'] == pdf_book['id'])['available']


@pytest.mark.parametrize('field, values', [('volume_number', [None, 1, 2]), ('language', ['', 'en', 'fr'])])
def test_manual_combine_rejects_conflicting_known_volume_or_language(api_client, db_factory, field, values) -> None:
    from app.book_models import BookItem

    owner = _user(db_factory, 'compatible-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        books = [_import(client, f'{index}.epub', _epub()) for index in range(3)]
        with db_factory() as db:
            for book, value in zip(books, values, strict=True):
                setattr(db.get(BookItem, book['id']), field, value)
            db.commit()
        response = client.post('/api/books/combine', json={'primary_book_id': books[0]['id'], 'book_ids': [book['id'] for book in books]})
        assert response.status_code == 422
        assert client.get('/api/books').json()['total'] == 3


def test_only_owner_can_separate_a_shared_file(api_client, db_factory) -> None:
    owner, other = _user(db_factory, 'separate-owner'), _user(db_factory, 'separate-other')
    with api_client(user=owner, base_url='http://localhost') as client:
        epub = _import(client, 'Story.epub', _epub())
        pdf_book = _import(client, 'Story.pdf', pdf())
        client.patch(f"/api/books/{epub['id']}", json={'visibility': 'shared'})
    with api_client(user=other, base_url='http://localhost') as client:
        assert client.post(f"/api/books/{pdf_book['id']}/separate").status_code == 403
        assert client.get('/api/books').json()['total'] == 1
