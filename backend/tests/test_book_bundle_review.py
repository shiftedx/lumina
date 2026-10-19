import pytest

"""Reader coordinate and ambiguous matching regressions for format bundles."""
from test_book_bundles import _import
from test_book_native import pdf
from test_books_api import _cbz, _epub, _user


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


def test_overview_queries_do_not_grow_per_shelf(api_client, db_factory) -> None:
    from sqlalchemy import event

    from app.book_models import BookItem, BookShelf, BookShelfMembership

    owner = _user(db_factory, 'shelf-batch-owner')
    with db_factory.begin() as db:
        db.add(BookItem(
            id='shelf-batch-book', storage_key='shelf-batch-book.epub', format='epub', kind='ebook',
            title='Shelf book', filename='Shelf book.epub', author='Author', owner_user_id=owner.id,
            visibility='private', reading_direction='ltr', genres=[], manifest={'chapters': []},
        ))

    counts = []
    for size in [2, 20]:
        with db_factory.begin() as db:
            existing = db.query(BookShelf).count()
            for index in range(existing, size):
                shelf_id = f'shelf-batch-{index}'
                db.add(BookShelf(
                    id=shelf_id, owner_user_id=owner.id, name=f'Shelf {index}', name_key=f'shelf {index}',
                    visibility='private',
                ))
                db.add(BookShelfMembership(
                    id=f'shelf-batch-membership-{index}', shelf_id=shelf_id, book_id='shelf-batch-book', position=0,
                ))
            engine = db.get_bind()
        queries = []

        def count_query(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith('SELECT'):
                queries.append(statement)

        event.listen(engine, 'before_cursor_execute', count_query)
        try:
            with api_client(user=owner, base_url='http://localhost') as client:
                response = client.get('/api/books/overview')
                assert response.status_code == 200
                assert len(response.json()['shelves']) == size
        finally:
            event.remove(engine, 'before_cursor_execute', count_query)
        counts.append(len(queries))
    assert counts[1] <= counts[0] + 2


def test_catalog_filters_and_series_detail_only_probe_returned_files(api_client, db_factory, monkeypatch) -> None:
    from app.book_models import BookItem
    from app.services.books import BookService

    owner = _user(db_factory, 'bounded-series-owner')
    with api_client(user=owner, base_url='http://localhost') as client:
        first = _import(client, 'bounded-one.cbz', _cbz(title='One', series='Bounded', number=1))
        second = _import(client, 'bounded-two.cbz', _cbz(title='Two', series='Bounded', number=2))
        client.patch(f"/api/books/{first['id']}/state", json={'favorite': True})

    with db_factory.begin() as db:
        for index in range(80):
            db.add(BookItem(
                id=f'bounded-decoy-{index}', storage_key=f'bounded-decoy-{index}.cbz', format='cbz', kind='manga',
                title=f'Decoy {index}', filename=f'Decoy {index}.cbz', author='Other', owner_user_id=owner.id,
                visibility='private', reading_direction='ltr', genres=[], manifest={'pages': ['1.png']},
                series_id=f'bounded-decoy-series-{index}', series_name=f'Decoy series {index}', volume_number=1,
            ))

    probes = 0

    def available(_book):
        nonlocal probes
        probes += 1
        return True

    monkeypatch.setattr(BookService, '_available', staticmethod(available))
    with api_client(user=owner, base_url='http://localhost') as client:
        page = client.get('/api/books?status=favorites&limit=1')
        assert page.status_code == 200 and page.json()['total'] == 1
        assert probes <= 4

        probes = 0
        detail = client.get(f"/api/books/series/{first['series_id']}")
        assert detail.status_code == 200
        assert [book['id'] for book in detail.json()['books']] == [first['id'], second['id']]
        assert probes <= 8


def test_catalog_series_and_overview_only_hydrate_returned_books(api_client, db_factory, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import event

    from app.book_models import BookItem, BookState
    from app.services.books import BookService

    owner = _user(db_factory, 'bounded-hydration-owner')
    with db_factory.begin() as db:
        for index in range(240):
            db.add(BookItem(
                id=f'bounded-hydration-{index}', storage_key=f'bounded-hydration-{index}.cbz',
                format='cbz', kind='manga', title=f'Volume {index:03d}',
                filename=f'Volume {index:03d}.cbz', author=f'Author {index % 12}',
                owner_user_id=owner.id, visibility='private', reading_direction='ltr', genres=[],
                manifest={'pages': ['1.png']}, series_id=f'bounded-hydration-series-{index}',
                series_name=f'Series {index:03d}', volume_number=1,
            ))
            if index % 2 == 0:
                db.add(BookState(
                    id=f'bounded-hydration-state-{index}', user_id=owner.id,
                    book_id=f'bounded-hydration-{index}', unit_index=0, offset=0,
                    favorite=True, last_read_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
                ))

    loaded = 0
    probes = 0

    def count_load(_book, _context):
        nonlocal loaded
        loaded += 1

    def available(_book):
        nonlocal probes
        probes += 1
        return True

    monkeypatch.setattr(BookService, '_available', staticmethod(available))
    event.listen(BookItem, 'load', count_load)
    try:
        with api_client(user=owner, base_url='http://localhost') as client:
            catalog = client.get('/api/books?sort=title&limit=5')
            assert catalog.status_code == 200 and catalog.json()['total'] == 240
            assert loaded <= 20

            loaded = 0
            filtered = client.get('/api/books?q=volume&sort=author&status=favorites&limit=5')
            assert filtered.status_code == 200 and filtered.json()['total'] == 120
            assert loaded <= 20

            loaded = 0
            series = client.get('/api/books/series?q=series&sort=author&status=favorites&limit=5')
            assert series.status_code == 200 and series.json()['total'] == 120
            assert loaded <= 20

            loaded = 0
            probes = 0
            overview = client.get('/api/books/overview')
            assert overview.status_code == 200
            assert overview.json()['totals'] == {'ebooks': 0, 'manga_series': 240, 'volumes': 240}
            assert loaded <= 40
            assert probes <= 90
    finally:
        event.remove(BookItem, 'load', count_load)


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
