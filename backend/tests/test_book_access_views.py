from __future__ import annotations

import pytest

from app.models import User
from test_book_native import pdf
from test_books_api import _cbz, _epub, _user


@pytest.mark.parametrize(('sections', 'visible'), [
    (['books'], {'ebook', 'manga'}),
    (['books:ebook'], {'ebook'}),
    (['books:manga'], {'manga'}),
    (['movies'], set()),
])
def test_book_view_grants_apply_to_catalog_reader_and_resources(api_client, db_factory, sections, visible):
    owner, reader = _user(db_factory, 'view-owner'), _user(db_factory, 'view-reader')
    owner.role = 'admin'
    with db_factory.begin() as db:
        db.get(User, owner.id).role = 'admin'
    with api_client(user=owner, base_url='http://localhost') as client:
        ebook = client.put('/api/books/import?filename=Story.epub', content=_epub()).json()
        manga = client.put('/api/books/import?filename=Moon.cbz', content=_cbz()).json()
        private = client.put('/api/books/import?filename=Private.cbz', content=_cbz(title='Private')).json()
        for book in (ebook, manga):
            assert client.patch('/api/books/'+book['id'], json={'visibility':'shared'}).status_code == 200
        response = client.put('/api/admin/members/'+reader.id+'/access', json={'sections':sections})
        assert response.status_code == 200, response.text
    with api_client(user=reader, base_url='http://localhost') as client:
        assert {book['kind'] for book in client.get('/api/books').json()['items']} == visible
        for book, resource in ((ebook, '/chapters/0'), (manga, '/pages/0')):
            expected = 200 if book['kind'] in visible else 404
            assert client.get('/api/books/'+book['id']).status_code == expected
            assert client.get('/api/books/'+book['id']+'/reader').status_code == expected
            assert client.get('/api/books/'+book['id']+resource).status_code == expected
        assert client.get('/api/books/'+private['id']).status_code == 404


def test_invite_book_counts_use_shared_catalog_entries_and_exclude_trash(api_client, db_factory):
    owner = _user(db_factory, 'count-owner')
    owner.role = 'admin'
    with db_factory.begin() as db:
        db.get(User, owner.id).role = 'admin'
    with api_client(user=owner, base_url='http://localhost') as client:
        ebook = client.put('/api/books/import?filename=Story.epub', content=_epub()).json()
        alternate = client.put('/api/books/import?filename=Story.pdf', content=pdf()).json()
        assert alternate['canonical_book_id'] == ebook['id']
        assert client.patch('/api/books/'+ebook['id'], json={'visibility':'shared'}).status_code == 200
        client.put('/api/books/import?filename=Private.cbz', content=_cbz())
        sections = {row['id']:row for row in client.get('/api/admin/sections').json()}
        assert sections['books:ebook']['count'] == 1
        assert sections['books:manga']['count'] == 0
        assert 'Private books stay private' in sections['books:ebook']['description']
        assert sections['shows']['label'] == 'TV shows'
        assert client.delete('/api/books/'+ebook['id']).status_code == 204
        sections = {row['id']:row for row in client.get('/api/admin/sections').json()}
        assert sections['books:ebook']['count'] == 0
