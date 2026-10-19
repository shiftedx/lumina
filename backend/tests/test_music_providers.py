"""Provider output stays bounded, exact enough to trust, and subordinate to manual edits."""
from types import SimpleNamespace

import pytest

from app.models import LibraryItem, MediaTitle
from app.services import metadata_editor, music_providers as music
from app.services.titles import title_image_bytes, title_image_source

ARTIST = "12345678-1234-4678-9234-123456789012"
RELEASE = "22345678-1234-4678-9234-123456789012"


def entry(title, uploader="Artist A", **kwargs):  # noqa: ANN001, ANN201
    return SimpleNamespace(id="01234567890", title=title, uploader=uploader, webpage_url="https://www.youtube.com/watch?v=01234567890",
                           thumbnail_url=None, duration=240, **kwargs)


@pytest.mark.parametrize("title", ["Artist A Song one reaction", "Artist A Song one tutorial", "Artist A interview", "Artist A album review"])
def test_nonperformances_are_not_associated(title):  # noqa: ANN001, ANN201
    assert music.correlate_video(entry(title), "Artist A") is None


def test_video_requires_artist_and_recording_and_uses_canonical_video_identity():
    assert music.correlate_video(entry("Other Artist - Song one", uploader="Other"), "Artist A", "Song one") is None
    assert music.correlate_video(entry("Artist A - Another song"), "Artist A", "Song one") is None
    result = music.correlate_video(entry("Artist A - Song one (live at the hall)"), "Artist A", "Song one")
    assert result.performance == "live" and result.association == "recording"
    assert result.url == "https://www.youtube.com/watch?v=01234567890"


@pytest.mark.parametrize(("title", "performance"), [("Artist A Song one cover", "cover"), ("Artist A Song one remix", "remix"),
    ("Artist A Song one official music video", "official"), ("Artist A Song one", "performance")])
def test_performance_types_are_separate(title, performance):  # noqa: ANN001, ANN201
    assert music.correlate_video(entry(title), "Artist A", "Song one").performance == performance


def test_lrc_offsets_multiple_timestamps_and_plain_lyrics():
    lyrics = music.parse_lyrics("[ar:Artist A]\n[offset:500]\n[00:01.50][00:03.000]First line\n[00:61]Invalid time\nSecond line", source="Your library")
    assert [cue.start for cue in lyrics.cues] == [2.0, 3.5]
    assert lyrics.cues[0].text == "First line" and "Second line" in lyrics.plain
    assert "ar:Artist" not in lyrics.plain


@pytest.mark.parametrize("url", ["http://coverartarchive.org/a", "https://coverartarchive.org:8443/a", "https://coverartarchive.org@evil.invalid/a", "https://evil.invalid/a"])
def test_artwork_rejects_unapproved_origins_and_ports(url):  # noqa: ANN001, ANN201
    with pytest.raises(ValueError):
        music.validate_art_url(url)


def test_provider_candidate_matching_requires_exact_artist_credit(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(music, "fetch_json", lambda _url: {"releases": [
        {"id": RELEASE, "title": "Album One", "score": 100, "artist-credit": [{"name": "Other Artist"}]},
        {"id": ARTIST, "title": "Album One", "score": 100, "artist-credit": [{"name": "Artist A"}]},
        {"id": "not-a-uuid", "title": "Album One", "score": 100},
    ]})
    candidates = music.candidates_for("album", "Album One", "Artist A")
    assert [candidate["exact"] for candidate in candidates] == [False, True]


def test_release_pages_are_bounded_and_have_an_explicit_cursor(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(music, "fetch_json", lambda url: {"release-group-count": 125, "release-groups": [{"id": RELEASE, "title": "Album One", "first-release-date": "2001-01-01", "primary-type": "Album"}]})
    page = music.release_page(ARTIST)
    assert page["items"] == [{"id": RELEASE, "name": "Album One", "year": "2001", "type": "Album"}]
    assert page["next_offset"] == 100
    assert music.release_page(ARTIST, 100)["next_offset"] is None


def test_editions_browse_releases_rather_than_release_group_identifiers(monkeypatch):  # noqa: ANN001, ANN201
    urls = []
    def fetch(url):  # noqa: ANN001, ANN202
        urls.append(url)
        return {"release-count": 101, "releases": [{"id": RELEASE, "title": "Album One", "date": "2001-01-01",
                "country": "US", "disambiguation": "Deluxe", "label-info": [{"label": {"name": "Example Label"}}]},
                {"id": "invalid", "title": "Not a valid release"}]}
    monkeypatch.setattr(music, "fetch_json", fetch)
    page = music.release_editions(ARTIST)
    assert page["items"] == [{"id": RELEASE, "name": "Album One", "date": "2001-01-01", "country": "US", "edition": "Deluxe", "label": "Example Label"}]
    assert page["next_offset"] == 100 and music.release_editions(ARTIST, 100)["next_offset"] is None
    assert "release-group=" in urls[0] and "/ws/2/release?" in urls[0]


def test_explicit_unmatch_does_not_automatically_guess_a_new_identity(db_factory, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(music, "session_scope", db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=RELEASE, type="album", key="album:a", name="My album", provider_ids={}, field_sources={"provider_ids": "user"}))
        db.commit()
    monkeypatch.setattr(music, "candidates_for", lambda *_args: pytest.fail("A protected no-match must stay unmatched"))
    assert music.enrich_title(RELEASE) == {"unmatched": True}


def test_inflight_provider_result_cannot_overwrite_a_newly_selected_identity(db_factory, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(music, "session_scope", db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=RELEASE, type="album", key="album:a", name="Album One", provider_ids={"MusicBrainz": RELEASE}))
        db.commit()
    def fetch(_url):  # noqa: ANN001, ANN202
        with db_factory() as db:
            title = db.get(MediaTitle, RELEASE)
            title.provider_ids, title.field_sources = {"MusicBrainz": ARTIST}, {"provider_ids": "user"}
            db.commit()
        return {"id": RELEASE, "title": "Old edition", "date": "2001-01-01"}
    monkeypatch.setattr(music, "fetch_json", fetch)
    assert music.enrich_title(RELEASE) == {"changed": True}
    with db_factory() as db:
        title = db.get(MediaTitle, RELEASE)
        assert title.name == "Album One" and title.provider_ids == {"MusicBrainz": ARTIST}


def test_forced_identification_runs_after_inflight_work_and_replaces_its_cache(db_factory, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(music, "session_scope", db_factory)
    tasks = []
    monkeypatch.setattr(music, "_pool", SimpleNamespace(submit=lambda callback: tasks.append(callback)))
    key = music.key_for("metadata", RELEASE)
    assert music.enqueue(key, "metadata", lambda: {"id": "old"})
    assert music.enqueue(key, "metadata", lambda: {"id": "new"}, force=True)
    tasks.pop(0)()
    assert len(tasks) == 1
    tasks.pop(0)()
    assert key not in music._pending and key not in music._rerun
    with db_factory() as db:
        from app.models import MusicLookup
        assert db.get(MusicLookup, key).payload == {"id": "new"}


def test_ambiguous_releases_wait_for_review(db_factory, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(music, "session_scope", db_factory)
    with db_factory() as db:
        db.add_all([MediaTitle(id=ARTIST, type="artist", key="artist:a", name="Artist A"), MediaTitle(id=RELEASE, type="album", key="album:a/one", name="Album One", parent_id=ARTIST)])
        db.commit()
    monkeypatch.setattr(music, "candidates_for", lambda *_args: [{"id": ARTIST, "name": "Album One", "exact": True, "score": 100}, {"id": RELEASE, "name": "Album One", "exact": True, "score": 100}])
    monkeypatch.setattr(music, "fetch_json", lambda _url: pytest.fail("An ambiguous match must not fetch or apply an identity"))
    assert len(music.enrich_title(RELEASE)["candidates"]) == 2
    with db_factory() as db:
        assert not db.get(MediaTitle, RELEASE).provider_ids


@pytest.mark.parametrize(('group_count', 'group_total'), [(1, 1), (2, 2), (1, 2)])
def test_album_cover_can_resolve_without_selecting_an_edition(db_factory, monkeypatch, group_count, group_total):
    import uuid
    group = str(uuid.UUID(int=70))
    monkeypatch.setattr(music, 'session_scope', db_factory)
    with db_factory() as db:
        db.add_all([MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A'),
                    MediaTitle(id=RELEASE, type='album', key='album:a/one', name='Album One', parent_id=ARTIST, year=2001)])
        db.commit()
    def fetch(url):
        if '/release-group/' in url:
            return {'count': group_total, 'release-groups': [
                {'id': str(uuid.UUID(int=70+i)), 'title': 'Album One', 'score': 100,
                 'artist-credit': [{'name': 'Artist A'}]} for i in range(group_count)]}
        return {'count': 2, 'releases': [
            {'id': str(uuid.UUID(int=80+i)), 'title': 'Album One', 'score': 100,
             'artist-credit': [{'name': 'Artist A'}], 'release-group': {'id': group}} for i in range(2)]}
    monkeypatch.setattr(music, 'fetch_json', fetch)
    monkeypatch.setattr(music, '_artwork', SimpleNamespace(load_pinned=lambda *_args, **_kwargs: SimpleNamespace(content=b'album-cover')))
    result = music.enrich_title(RELEASE)
    assert len(result['candidates']) == 2
    with db_factory() as db:
        album = db.get(MediaTitle, RELEASE)
        assert not album.provider_ids and album.year == 2001
        if group_count == group_total == 1:
            assert album.images['Primary']['music'] == f'https://coverartarchive.org/release-group/{group}/front-1200'
            assert album.metadata_json['release_group_id'] == group
        else:
            assert not album.images


def test_enrichment_protects_manual_fields_and_rejects_invalid_provider_values(db_factory, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(music, "session_scope", db_factory)
    monkeypatch.setattr(music, "import_art", lambda *_args: None)
    with db_factory() as db:
        title = MediaTitle(id=RELEASE, type="album", key="album:a", name="Album One", provider_ids={"MusicBrainz": RELEASE})
        db.add(title)
        batch = metadata_editor.Batch(db, "edit", None)
        metadata_editor.write_user_field(title, "edition", "My original pressing", batch)
        db.commit()
    monkeypatch.setattr(music, "fetch_json", lambda _url: {"id": RELEASE, "title": "Album One", "date": "2001-01-01", "disambiguation": "Provider edition", "country": "X" * 500,
        "release-group": {"id": ARTIST, "primary-type": "Album"}, "genres": [{"name": "Folk"}], "artist-credit": [{"name": "Artist A"}]})
    assert music.enrich_title(RELEASE)["identified"]
    with db_factory() as db:
        title = db.get(MediaTitle, RELEASE)
        assert title.metadata_json["edition"] == "My original pressing"
        assert title.field_sources["edition"] == "user"
        assert title.metadata_json.get("country") is None
        assert title.metadata_json["genres"] == ["Folk"]
        assert title.metadata_json["credits"][0]["name"] == "Artist A"


def test_music_art_identity_is_pure_and_image_bytes_use_bounded_shared_cache(db_factory):  # noqa: ANN001, ANN201
    title = MediaTitle(id=ARTIST, type="artist", key="artist:a", name="Artist A", images={"Primary": {"music": "https://upload.wikimedia.org/a.jpg", "tag": "source-tag"}})
    assert title_image_source(title, "Primary") == "source-tag"
    calls = []
    class Artwork:
        def load_pinned(self, url, **kwargs):  # noqa: ANN001, ANN201
            calls.append((url, kwargs))
            return SimpleNamespace(content_type="image/jpeg", content=b"image bytes")
    with db_factory() as db:
        assert title_image_bytes(db, title, "Primary", Artwork()) == ("image/jpeg", b"image bytes")
    assert calls[0][1]["max_bytes"] == 2 * 1024**3
    assert calls[0][1]["bucket"] == "music"


def test_wikimedia_current_thumbnail_host_is_pinned_without_allowing_subdomains():
    assert music.validate_art_url('https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a1/photo.jpg/1600px-photo.jpg')
    for host in ('thumb.wikimedia.org.evil.invalid', 'evil.wikimedia.org'):
        with pytest.raises(ValueError):
            music.validate_art_url(f'https://{host}/photo.jpg')


def test_identity_linked_photos_choose_distinct_portrait_and_landscape_with_plain_credits(monkeypatch):
    urls = []
    def fetch(url):
        urls.append(url)
        if 'EntityData' in url:
            return {'entities': {'Q123': {'claims': {'P18': [
                {'mainsnak': {'datavalue': {'value': 'Portrait.jpg'}}},
                {'mainsnak': {'datavalue': {'value': 'Concert.jpg'}}},
                {'rank': 'deprecated', 'mainsnak': {'datavalue': {'value': 'Wrong.jpg'}}},
            ]}}}}
        return {'query': {'pages': {str(i): {'imageinfo': [{
            'width': w, 'height': h, 'thumburl': f'https://thumb.wikimedia.org/{name}',
            'descriptionurl': f'https://commons.wikimedia.org/wiki/File:{name}',
            'extmetadata': {'Artist': {'value': '<a href="https://example.invalid">Photo &amp; Co</a>'}, 'LicenseShortName': {'value': 'CC BY-SA 4.0'}},
        }]} for i, (name, w, h) in enumerate([('Portrait.jpg', 900, 1200), ('Concert.jpg', 2400, 1200)])}}}
    monkeypatch.setattr(music, 'fetch_json', fetch)
    fields, pictures = music.artist_profile({'relations': [{'type': 'wikidata', 'url': {'resource': 'https://www.wikidata.org/wiki/Q123'}}]})
    assert not fields
    assert [p['image_type'] for p in pictures] == ['Primary', 'Backdrop']
    assert len({p['source_url'] for p in pictures}) == 2
    assert pictures[0]['author'] == 'Photo & Co'
    assert 'Wrong.jpg' not in urls[1]
    assert len(urls) == 2


def test_single_identity_photo_is_not_duplicated_as_a_background(monkeypatch):
    def fetch(url):
        if 'EntityData' in url:
            return {'entities': {'Q123': {'claims': {'P18': [{'mainsnak': {'datavalue': {'value': 'Concert.jpg'}}}]}}}}
        return {'query': {'pages': {'1': {'imageinfo': [{'width': 2400, 'height': 1200, 'url': 'https://upload.wikimedia.org/concert.jpg', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Concert.jpg'}]}}}}
    monkeypatch.setattr(music, 'fetch_json', fetch)
    _, pictures = music.artist_profile({'relations': [{'type': 'wikidata', 'url': {'resource': 'https://www.wikidata.org/wiki/Q123'}}]})
    assert [p['image_type'] for p in pictures] == ['Primary']


def test_profile_uses_no_name_search_and_decodes_article_title_once(monkeypatch):
    urls = []
    monkeypatch.setattr(music, 'fetch_json', lambda url: urls.append(url) or {'extract': 'A biography', 'originalimage': {'source': 'https://thumb.wikimedia.org/a.jpg'}})
    assert music.artist_profile({'name': 'Ambiguous artist'}) == ({}, [])
    fields, pictures = music.artist_profile({'relations': [{'type': 'wikipedia', 'url': {'resource': 'https://en.wikipedia.org/wiki/Artist_%28band%29'}}]})
    assert urls[0] == 'https://en.wikipedia.org/api/rest_v1/page/summary/Artist_%28band%29'
    assert all(url.startswith('https://commons.wikimedia.org/w/api.php?') for url in urls[1:])
    assert fields['overview'] == 'A biography' and pictures[0]['url'].startswith('https://thumb.wikimedia.org/')


@pytest.mark.parametrize('answer_id', [42, 43])
def test_artist_without_wikimedia_photo_uses_only_identity_linked_deezer_art(monkeypatch, answer_id):
    calls = []
    def fetch(url):
        calls.append(url)
        return {'id': answer_id, 'picture_xl': 'https://cdn-images.dzcdn.net/images/artist/abc123/1000x1000-000000-80-0-0.jpg'}
    monkeypatch.setattr(music, 'fetch_json', fetch)
    _, pictures = music.artist_profile({'relations': [
        {'type': 'streaming', 'url': {'resource': 'https://www.deezer.com/en/artist/42'}},
    ]})
    assert calls == ['https://api.deezer.com/artist/42']
    if answer_id == 42:
        assert pictures[0]['image_type'] == 'Primary'
        assert pictures[0]['source_url'] == 'https://www.deezer.com/artist/42'
        assert music.validate_art_url(pictures[0]['url']) == pictures[0]['url']
    else:
        assert pictures == []


@pytest.mark.parametrize('link', ['https://www.deezer.com.evil.invalid/artist/42',
    'https://user@www.deezer.com/artist/42', 'https://www.deezer.com:8443/artist/42',
    'https://www.deezer.com/album/42', 'https://www.deezer.com/artist/not-an-id'])
def test_artist_art_fallback_never_follows_untrusted_or_nonartist_links(monkeypatch, link):
    monkeypatch.setattr(music, 'fetch_json', lambda _url: pytest.fail('Not an identity-linked artist'))
    assert music.artist_profile({'relations': [{'url': {'resource': link}}]}) == ({}, [])


def test_nonenglish_wikipedia_relation_does_not_hide_the_linked_english_biography(monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return {'extract': 'An identity-linked biography'}
    monkeypatch.setattr(music, 'fetch_json', fetch)
    fields, _ = music.artist_profile({'relations': [
        {'type': 'wikipedia', 'url': {'resource': 'https://fr.wikipedia.org/wiki/Artist_A'}},
        {'type': 'wikipedia', 'url': {'resource': 'https://en.wikipedia.org/wiki/Artist_A'}},
    ]})
    assert calls == ['https://en.wikipedia.org/api/rest_v1/page/summary/Artist_A']
    assert fields['overview'] == 'An identity-linked biography'


def test_identified_artist_without_a_photo_is_revisited_weekly_without_repeated_work(db_factory, monkeypatch):
    from datetime import timedelta
    from app.models import MusicLookup, utcnow
    monkeypatch.setattr(music, 'session_scope', db_factory)
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'name': 'Artist A'})
    monkeypatch.setattr(music, 'release_page', lambda _id: {'items': []})
    tasks = []
    monkeypatch.setattr(music, '_pool', SimpleNamespace(submit=lambda callback: tasks.append(callback)))
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST}))
        db.commit()
    assert music.request_enrichment(ARTIST)
    tasks.pop()()
    with db_factory() as db:
        artist = db.get(MediaTitle, ARTIST)
        lookup = db.get(MusicLookup, music.key_for('metadata', ARTIST))
        assert lookup.payload['identified'] and lookup.payload['artwork_missing']
        assert utcnow() + timedelta(days=6) < artist.metadata_due_at < utcnow() + timedelta(days=8)
        assert utcnow() + timedelta(days=6) < lookup.expires_at < utcnow() + timedelta(days=8)
    assert not music.request_enrichment(ARTIST)
    assert not tasks


def test_artist_refresh_recovers_from_unavailable_wikimedia_photo_with_linked_portrait(db_factory, monkeypatch):
    monkeypatch.setattr(music, 'session_scope', db_factory)
    monkeypatch.setattr(music, 'release_page', lambda _id: {'items': []})
    def fetch(url):
        if '/ws/2/artist/' in url:
            return {'name': 'Artist A', 'relations': [
                {'type': 'wikipedia', 'url': {'resource': 'https://en.wikipedia.org/wiki/Artist_A'}},
                {'type': 'streaming', 'url': {'resource': 'https://www.deezer.com/artist/42'}},
            ]}
        if '/summary/' in url:
            return {'extract': 'Biography', 'originalimage': {'source': 'https://upload.wikimedia.org/photo.jpg'}}
        if 'api.deezer.com' in url:
            return {'id': 42, 'picture_xl': 'https://cdn-images.dzcdn.net/images/artist/abc123/1000x1000-000000-80-0-0.jpg'}
        return {}
    def load(url, **_kwargs):
        if 'wikimedia.org' in url:
            raise ValueError('Upstream is rate limited')
        return SimpleNamespace(content=b'verified portrait')
    monkeypatch.setattr(music, 'fetch_json', fetch)
    monkeypatch.setattr(music, '_artwork', SimpleNamespace(load_pinned=load))
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST},
                          images={'Primary': {'music': 'https://upload.wikimedia.org/photo.jpg', 'tag': 'old'}}, field_sources={'images.Primary': 'musicbrainz'}))
        db.commit()
    result = music.enrich_title(ARTIST)
    assert result['identified'] and not result.get('artwork_error')
    with db_factory() as db:
        artist = db.get(MediaTitle, ARTIST)
        assert artist.images['Primary']['music'].startswith('https://cdn-images.dzcdn.net/images/artist/')
        assert artist.metadata_json['artwork_credits'][0]['source_url'] == 'https://www.deezer.com/artist/42'


def test_gone_stored_portrait_is_not_cached_as_healthy_for_a_month(db_factory, monkeypatch):
    from datetime import timedelta
    from app.models import utcnow
    from app.services.artwork import ArtworkNotFoundError
    monkeypatch.setattr(music, 'session_scope', db_factory)
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'name': 'Artist A'})
    monkeypatch.setattr(music, 'release_page', lambda _id: {'items': []})
    monkeypatch.setattr(music, 'artist_profile', lambda _value: ({}, [{'url': 'https://upload.wikimedia.org/gone.jpg', 'image_type': 'Primary'}]))
    def gone(*_args, **_kwargs):
        raise ArtworkNotFoundError('Gone')
    monkeypatch.setattr(music, 'import_art', gone)
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST},
                          images={'Primary': {'music': 'https://upload.wikimedia.org/gone.jpg', 'tag': 'old'}}))
        db.commit()
    result = music.enrich_title(ARTIST)
    assert result['identified'] and result['artwork_missing']
    with db_factory() as db:
        assert db.get(MediaTitle, ARTIST).metadata_due_at < utcnow() + timedelta(days=8)


@pytest.mark.parametrize('project', ['commons', 'en'])
def test_blocked_wikimedia_original_recovers_with_the_same_photo_at_a_standard_size(db_factory, monkeypatch, project):
    from app.services.artwork import ArtworkUnavailableError
    monkeypatch.setattr(music, 'session_scope', db_factory)
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'name': 'Artist A'})
    monkeypatch.setattr(music, 'release_page', lambda _id: {'items': []})
    original = f'https://upload.wikimedia.org/wikipedia/{project}/a/a1/Artist_A.jpg'
    standard = f'https://upload.wikimedia.org/wikipedia/{project}/thumb/a/a1/Artist_A.jpg/500px-Artist_A.jpg'
    credit = {'url': original, 'image_type': 'Primary', 'source_url': 'https://commons.wikimedia.org/wiki/File:Artist_A.jpg', 'author': 'Photographer', 'license': 'CC BY'}
    monkeypatch.setattr(music, 'artist_profile', lambda _value: ({}, [credit]))
    monkeypatch.setattr(music, 'linked_artist_picture', lambda _value: pytest.fail('The same photograph is available; no second provider is needed'))
    calls = []
    def load(url, **_kwargs):
        calls.append(url)
        if url == original:
            raise ArtworkUnavailableError('Original returned HTTP 429')
        assert url == standard
        return SimpleNamespace(content=b'standard-size photograph')
    monkeypatch.setattr(music, '_artwork', SimpleNamespace(load_pinned=load))
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST}))
        db.commit()
    result = music.enrich_title(ARTIST)
    assert result['identified'] and not result.get('artwork_error')
    assert calls == [original, standard]
    with db_factory() as db:
        artist = db.get(MediaTitle, ARTIST)
        assert artist.images['Primary']['music'] == standard
        assert artist.metadata_json['artwork_credits'][0]['author'] == 'Photographer'


def test_unavailable_artist_image_queues_one_repair_despite_long_lived_metadata_cache(db_factory, monkeypatch):
    from datetime import timedelta
    from app.models import MusicLookup, utcnow
    from app.services.artwork import ArtworkNotFoundError
    monkeypatch.setattr(music, 'session_scope', db_factory)
    tasks = []
    monkeypatch.setattr(music, '_pool', SimpleNamespace(submit=lambda callback: tasks.append(callback)))
    monkeypatch.setattr(music, 'enrich_title', lambda *_args: {'identified': True, 'artwork_missing': True})
    key = music.key_for('metadata', ARTIST)
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST},
                          images={'Primary': {'music': 'https://upload.wikimedia.org/gone.jpg', 'tag': 'old'}}))
        db.add(MusicLookup(key=key, kind='metadata', payload={'identified': True}, refreshed_at=utcnow()-timedelta(days=2), expires_at=utcnow()+timedelta(days=28)))
        db.commit()
    def gone(*_args, **_kwargs):
        raise ArtworkNotFoundError('Gone')
    with db_factory() as db:
        artist = db.get(MediaTitle, ARTIST)
        for _ in range(2):
            with pytest.raises(FileNotFoundError):
                title_image_bytes(db, artist, 'Primary', SimpleNamespace(load_pinned=gone))
    assert len(tasks) == 1
    tasks.pop()()
    with db_factory() as db:
        with pytest.raises(FileNotFoundError):
            title_image_bytes(db, db.get(MediaTitle, ARTIST), 'Primary', SimpleNamespace(load_pinned=gone))
        assert utcnow() + timedelta(days=6) < db.get(MusicLookup, key).expires_at < utcnow() + timedelta(days=8)
        db.get(MusicLookup, key).refreshed_at = utcnow() - timedelta(days=2)
        db.commit()
        with pytest.raises(FileNotFoundError):
            title_image_bytes(db, db.get(MediaTitle, ARTIST), 'Primary', SimpleNamespace(load_pinned=gone))
    assert not tasks


def test_artwork_failure_keeps_identity_and_retries_before_month_long_metadata_ttl(db_factory, monkeypatch):
    from datetime import timedelta
    from app.models import utcnow
    monkeypatch.setattr(music, 'session_scope', db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST}))
        db.commit()
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'name': 'Artist A'})
    monkeypatch.setattr(music, 'release_page', lambda _id: {'items': []})
    monkeypatch.setattr(music, 'artist_profile', lambda _value: ({}, [{'url': 'https://thumb.wikimedia.org/photo.jpg', 'image_type': 'Primary'}]))
    def unavailable(*_args, **_kwargs):
        raise ValueError('provider unavailable')
    monkeypatch.setattr(music, 'import_art', unavailable)
    result = music.enrich_title(ARTIST)
    assert result['identified'] and result['artwork_error']
    with db_factory() as db:
        title = db.get(MediaTitle, ARTIST)
        assert title.provider_ids['MusicBrainz'] == ARTIST
        assert utcnow() < title.metadata_due_at < utcnow() + timedelta(hours=2)


def test_artwork_partial_failure_cache_expires_for_retry(db_factory, monkeypatch):
    from datetime import timedelta
    from app.models import MusicLookup, utcnow
    monkeypatch.setattr(music, 'session_scope', db_factory)
    tasks = []
    monkeypatch.setattr(music, '_pool', SimpleNamespace(submit=lambda callback: tasks.append(callback)))
    key = music.key_for('metadata', 'portrait-retry')
    assert music.enqueue(key, 'metadata', lambda: {'identified': True, 'artwork_error': 'Unavailable'}, ttl_days=30)
    tasks.pop()()
    with db_factory() as db:
        assert db.get(MusicLookup, key).expires_at < utcnow() + timedelta(hours=2)


def test_manual_artist_photo_and_credit_stay_protected(db_factory, monkeypatch):
    monkeypatch.setattr(music, 'session_scope', db_factory)
    monkeypatch.setattr(music, '_artwork', SimpleNamespace(load_pinned=lambda *args, **kwargs: SimpleNamespace(content=b'new photograph')))
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST},
                          images={'Primary': {'tag': 'manual'}}, field_sources={'images.Primary': 'user'}, metadata_json={'artwork_credits': []}))
        db.commit()
    music.import_art(ARTIST, 'https://thumb.wikimedia.org/a.jpg', identity=ARTIST, credit={'image_type': 'Primary', 'source_url': 'https://commons.wikimedia.org/wiki/File:A.jpg', 'author': 'Photographer', 'license': 'CC BY'})
    with db_factory() as db:
        title = db.get(MediaTitle, ARTIST)
        assert title.images['Primary']['tag'] == 'manual' and title.metadata_json['artwork_credits'] == []


def test_release_matching_preserves_artist_credit_joinphrases(monkeypatch):
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'releases': [{
        'id': RELEASE, 'title': 'Shared Album', 'score': 100,
        'artist-credit': [{'name': 'Artist A', 'joinphrase': ' feat. '}, {'name': 'Artist B', 'joinphrase': ''}],
    }]})
    result = music.candidates_for('album', 'Shared Album', 'Artist A feat. Artist B')
    assert result[0]['artist'] == 'Artist A feat. Artist B' and result[0]['exact']


def test_candidate_page_surfaces_sixth_match_but_truncation_cannot_prove_uniqueness(monkeypatch):
    rows = [{'id': str(__import__('uuid').UUID(int=i+1)), 'name': f'Other {i}', 'score': 100} for i in range(5)]
    rows.append({'id': ARTIST, 'name': 'Artist A', 'score': 100})
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'artists': rows, 'count': 6})
    assert music.candidates_for('artist', 'Artist A')[5]['exact']
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'artists': rows, 'count': 12})
    assert not any(row['exact'] for row in music.candidates_for('artist', 'Artist A'))


def test_known_group_prevents_automatic_match_to_another_release_group(db_factory, monkeypatch):
    monkeypatch.setattr(music, 'session_scope', db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=RELEASE, type='album', key='album:a', name='Album One', metadata_json={'release_group_id': ARTIST}))
        db.commit()
    monkeypatch.setattr(music, 'candidates_for', lambda *_args: [{'id': RELEASE, 'name': 'Album One', 'exact': True, 'score': 100, 'release_group_id': RELEASE}])
    monkeypatch.setattr(music, 'fetch_json', lambda _url: pytest.fail('Wrong release group must wait for review'))
    assert music.enrich_title(RELEASE)['candidates']
    with db_factory() as db:
        assert not db.get(MediaTitle, RELEASE).provider_ids


def test_changed_identity_result_is_immediately_retryable(db_factory, monkeypatch):
    from app.models import MusicLookup, utcnow
    monkeypatch.setattr(music, 'session_scope', db_factory)
    tasks = []
    monkeypatch.setattr(music, '_pool', SimpleNamespace(submit=lambda callback: tasks.append(callback)))
    key = music.key_for('metadata', 'changed-identity')
    assert music.enqueue(key, 'metadata', lambda: {'changed': True}, ttl_days=30)
    tasks.pop()()
    with db_factory() as db:
        assert db.get(MusicLookup, key).expires_at <= utcnow()
    assert music.enqueue(key, 'metadata', lambda: {'identified': True})
    tasks.pop()()


def test_wikipedia_link_can_resolve_entity_photos_without_a_direct_wikidata_relation(monkeypatch):
    urls = []
    def fetch(url):
        urls.append(url)
        if '/summary/' in url:
            return {'extract': 'Biography', 'wikibase_item': 'Q123'}
        if 'EntityData' in url:
            return {'entities': {'Q123': {'claims': {'P18': [{'mainsnak': {'datavalue': {'value': 'Photo.jpg'}}}]}}}}
        return {'query': {'pages': {'1': {'imageinfo': [{'width': 900, 'height': 1200, 'url': 'https://upload.wikimedia.org/photo.jpg', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Photo.jpg'}]}}}}
    monkeypatch.setattr(music, 'fetch_json', fetch)
    _, pictures = music.artist_profile({'relations': [{'type': 'wikipedia', 'url': {'resource': 'https://en.wikipedia.org/wiki/Artist_A'}}]})
    assert pictures[0]['source_url'] == 'https://commons.wikimedia.org/wiki/File:Photo.jpg'
    assert len(urls) == 3


def test_artist_stylization_and_provider_aliases_match_without_losing_non_latin_names(monkeypatch):
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'artists': [
        {'id': ARTIST, 'name': 'Mára‐Vale', 'score': 100},
        {'id': RELEASE, 'name': 'North Arcade', 'score': 100, 'aliases': [{'name': 'The Arcade', 'locale': 'en'}]},
    ]})
    assert music.candidates_for('artist', 'Mara-Vale')[0]['exact']
    assert music.candidates_for('artist', 'The Arcade')[1]['exact']
    assert music.normalized('東京') == '東京'
    assert music.normalized('Mara_Vale') == 'mara vale'
    assert not any(row['exact'] for row in music.candidates_for('artist', '!!!'))


@pytest.mark.parametrize('protected', [False, True])
def test_person_lifespan_is_not_a_career_and_manual_active_years_stay_protected(db_factory, monkeypatch, protected):
    monkeypatch.setattr(music, 'session_scope', db_factory)
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'name': 'Artist A', 'type': 'Person', 'life-span': {'begin': '1987-03-12'}})
    monkeypatch.setattr(music, 'release_page', lambda _id: {'items': []})
    monkeypatch.setattr(music, 'artist_profile', lambda _value: ({}, []))
    with db_factory() as db:
        db.add(MediaTitle(id=ARTIST, type='artist', key='artist:a', name='Artist A', provider_ids={'MusicBrainz': ARTIST},
                          metadata_json={'active_since': '1987-03-12'}, field_sources={'active_since': 'user' if protected else 'musicbrainz'}))
        db.commit()
    assert music.enrich_title(ARTIST)['identified']
    with db_factory() as db:
        artist = db.get(MediaTitle, ARTIST)
        assert artist.metadata_json.get('active_since') == ('1987-03-12' if protected else None)
        assert artist.metadata_json['birth_date'] == '1987-03-12'


def test_lyrics_outage_cache_recovers_soon_without_discarding_saved_words(db_factory, monkeypatch):
    from datetime import timedelta
    from app.models import MusicLookup, utcnow
    monkeypatch.setattr(music, 'session_scope', db_factory)
    tasks = []
    monkeypatch.setattr(music, '_pool', SimpleNamespace(submit=lambda callback: tasks.append(callback)))
    key = music.key_for('lyrics', 'outage-recovery')
    def outage():
        raise TimeoutError('provider timeout')
    assert music.enqueue(key, 'lyrics', outage)
    assert not music.enqueue(key, 'lyrics', outage, force=True)
    tasks.pop()()
    with db_factory() as db:
        row = db.get(MusicLookup, key)
        assert row.payload.get('error')
        assert row.expires_at <= utcnow() + timedelta(minutes=1)
    assert music.enqueue(key, 'lyrics', lambda: {'plain': 'Saved words', 'cues': []}, force=True)
    tasks.pop()()
    assert music.enqueue(key, 'lyrics', outage, force=True)
    tasks.pop()()
    with db_factory() as db:
        assert db.get(MusicLookup, key).payload['plain'] == 'Saved words'


def test_ambiguous_artist_uses_two_registered_album_matches_to_choose_one_candidate(db_factory, monkeypatch):
    local_artist = 'local-artist'
    third = '32345678-1234-4678-9234-123456789012'
    monkeypatch.setattr(music, 'session_scope', db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=local_artist, type='artist', key='artist:the sound', name='The Sound'))
        for index, (name, status) in enumerate((('Echoes', 'available'), ('Northern Lights', 'available'),
                                                ('Missing Decoy', 'missing'), ('Missing Other', 'missing'))):
            album_id = f'local-album-{index}'
            db.add(MediaTitle(id=album_id, type='album', key=f'album:the sound/{index}', name=name, parent_id=local_artist))
            db.add(LibraryItem(id=f'local-track-{index}', title=f'Track {index}', title_id=album_id, kind='track', status=status))
        db.commit()
    candidates = [
        {'id': ARTIST, 'name': 'The Sound', 'score': 100, 'exact': True, 'complete': True},
        {'id': RELEASE, 'name': 'The Sound', 'score': 100, 'exact': True, 'complete': True},
        {'id': third, 'name': 'The Sound', 'score': 94, 'exact': False, 'complete': True},
    ]
    monkeypatch.setattr(music, 'candidates_for', lambda *_args: candidates)
    pages = {
        ARTIST: {'items': [{'name': 'Echoes'}, {'name': 'Northern Lights'}], 'next_offset': None, 'complete': True},
        RELEASE: {'items': [{'name': 'Missing Decoy'}, {'name': 'Missing Other'}], 'next_offset': None, 'complete': True},
    }
    monkeypatch.setattr(music, 'release_page', lambda identity: pages[identity])
    monkeypatch.setattr(music, 'fetch_json', lambda _url: {'name': 'The Sound'})
    monkeypatch.setattr(music, 'artist_profile', lambda _value: ({}, []))

    assert music.enrich_title(local_artist)['id'] == ARTIST
    with db_factory() as db:
        assert db.get(MediaTitle, local_artist).provider_ids == {'MusicBrainz': ARTIST}


@pytest.mark.parametrize(('candidate_patch', 'pages'), [
    ({}, {
        ARTIST: {'items': [{'name': 'Echoes'}, {'name': 'Unrelated'}]},
        RELEASE: {'items': [{'name': 'Echoes'}, {'name': 'Different'}]},
    }),
    ({}, {
        ARTIST: {'items': [{'name': 'Echoes'}, {'name': 'Northern Lights'}]},
        RELEASE: {'items': [{'name': 'Echoes'}, {'name': 'Northern Lights'}]},
    }),
    ({}, {
        ARTIST: {'items': [{'name': 'Echoes'}, {'name': 'Northern Lights'}]},
        RELEASE: {'items': [{'name': 'Unrelated'}], 'next_offset': 100},
    }),
    ({'complete': False}, {}),
    ({'exact': False, 'score': 94}, {}),
])
def test_artist_catalog_evidence_refuses_one_album_ties_partial_pages_and_low_scores(
    db_factory, monkeypatch, candidate_patch, pages,
):
    local_artist = f'guarded-artist-{len(pages)}-{candidate_patch.get("score", 100)}-{candidate_patch.get("complete", True)}'
    monkeypatch.setattr(music, 'session_scope', db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=local_artist, type='artist', key=f'artist:{local_artist}', name='The Sound'))
        for index, name in enumerate(('Echoes', 'Northern Lights')):
            album_id = f'{local_artist}-album-{index}'
            db.add(MediaTitle(id=album_id, type='album', key=f'album:{local_artist}/{index}', name=name, parent_id=local_artist))
            db.add(LibraryItem(id=f'{local_artist}-track-{index}', title=f'Track {index}', title_id=album_id, kind='track', status='available'))
        db.commit()
    candidates = [
        {'id': identity, 'name': 'The Sound', 'score': 100, 'exact': True, 'complete': True, **candidate_patch}
        for identity in (ARTIST, RELEASE)
    ]
    monkeypatch.setattr(music, 'candidates_for', lambda *_args: candidates)
    calls = []
    def releases(identity):
        calls.append(identity)
        return {'next_offset': None, 'complete': True, **pages[identity]}
    monkeypatch.setattr(music, 'release_page', releases)
    monkeypatch.setattr(music, 'fetch_json', lambda _url: pytest.fail('Insufficient catalog evidence must not select an artist'))

    assert music.enrich_title(local_artist) == {'candidates': candidates}
    assert calls == list(pages)
    with db_factory() as db:
        assert db.get(MediaTitle, local_artist).provider_ids == {}


def test_artist_catalog_evidence_requires_a_complete_provider_release_count(db_factory, monkeypatch):
    local_artist = 'guarded-artist-missing-release-count'
    monkeypatch.setattr(music, 'session_scope', db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=local_artist, type='artist', key='artist:missing-release-count', name='The Sound'))
        for index, name in enumerate(('Echoes', 'Northern Lights')):
            album_id = f'missing-release-count-album-{index}'
            db.add(MediaTitle(id=album_id, type='album', key=f'album:missing-release-count/{index}', name=name,
                              parent_id=local_artist))
            db.add(LibraryItem(id=f'missing-release-count-track-{index}', title=f'Track {index}', title_id=album_id,
                               kind='track', status='available'))
        db.commit()
    candidates = [
        {'id': ARTIST, 'name': 'The Sound', 'score': 100, 'exact': True, 'complete': True},
        {'id': RELEASE, 'name': 'The Sound', 'score': 100, 'exact': True, 'complete': True},
    ]
    monkeypatch.setattr(music, 'candidates_for', lambda *_args: candidates)
    calls = []
    def fetch(url):
        calls.append(url)
        if '/release-group?' not in url:
            pytest.fail('Unknown release-page completeness must not select an artist')
        names = ('Echoes', 'Northern Lights') if f'artist={ARTIST}' in url else ('Unrelated',)
        return {'release-groups': [
            {'id': str(__import__('uuid').UUID(int=index + 10)), 'title': name}
            for index, name in enumerate(names)
        ]}
    monkeypatch.setattr(music, 'fetch_json', fetch)

    assert music.enrich_title(local_artist) == {'candidates': candidates}
    assert len(calls) == 1 and f'artist={ARTIST}' in calls[0]
    with db_factory() as db:
        assert db.get(MediaTitle, local_artist).provider_ids == {}


def test_artist_catalog_evidence_does_not_inspect_more_than_three_candidates(db_factory, monkeypatch):
    local_artist = 'guarded-artist-many-candidates'
    monkeypatch.setattr(music, 'session_scope', db_factory)
    with db_factory() as db:
        db.add(MediaTitle(id=local_artist, type='artist', key='artist:many-candidates', name='The Sound'))
        for index, name in enumerate(('Echoes', 'Northern Lights')):
            album_id = f'many-candidates-album-{index}'
            db.add(MediaTitle(id=album_id, type='album', key=f'album:many-candidates/{index}', name=name, parent_id=local_artist))
            db.add(LibraryItem(id=f'many-candidates-track-{index}', title=f'Track {index}', title_id=album_id, kind='track', status='available'))
        db.commit()
    candidates = [
        {'id': str(__import__('uuid').UUID(int=index)), 'name': 'The Sound', 'score': 100,
         'exact': True, 'complete': True}
        for index in range(1, 5)
    ]
    monkeypatch.setattr(music, 'candidates_for', lambda *_args: candidates)
    monkeypatch.setattr(music, 'release_page', lambda _identity: pytest.fail('Four ambiguous candidates must wait for review'))
    monkeypatch.setattr(music, 'fetch_json', lambda _url: pytest.fail('Four ambiguous candidates must wait for review'))

    assert music.enrich_title(local_artist) == {'candidates': candidates}
    with db_factory() as db:
        assert db.get(MediaTitle, local_artist).provider_ids == {}
