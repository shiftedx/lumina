"""Provider output stays bounded, exact enough to trust, and subordinate to manual edits."""
from types import SimpleNamespace

import pytest

from app.models import MediaTitle
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
