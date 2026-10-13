"""Music participates in the shared FTS corpus without weakening household visibility."""
from __future__ import annotations

from sqlalchemy import event

from app.models import LibraryItem, MediaTitle, MusicTrackOverride
from app.services import library_search
from app.services.library_search import LibrarySearchService, ensure_search_index, index_title
from support import make_user, memory_session_factory


def make_session():  # noqa: ANN201
    factory = memory_session_factory()
    return factory(), factory.kw["bind"]


def add_music(
    db,  # noqa: ANN001
    *,
    suffix: str,
    owner: str,
    visibility: str,
    artist_metadata: dict | None = None,
    album_metadata: dict | None = None,
    track_metadata: dict | None = None,
) -> tuple[MediaTitle, MediaTitle, LibraryItem]:
    artist = MediaTitle(
        id=f"artist-{suffix}", type="artist", key=f"artist:{suffix}", name=f"Artist {suffix}",
        metadata_json=artist_metadata or {},
    )
    album = MediaTitle(
        id=f"album-{suffix}", type="album", key=f"album:{suffix}", name=f"Album {suffix}",
        parent_id=artist.id, metadata_json=album_metadata or {},
    )
    track = LibraryItem(
        id=f"track-{suffix}", user_id=owner, visibility=visibility, title=f"Source track {suffix}",
        uploader=f"Source artist {suffix}", playlist_name=album.name, metadata_json=track_metadata or {},
        status="available", kind="track", title_id=album.id,
    )
    db.add_all([artist, album, track])
    return artist, album, track


def index_music_titles(db, *titles: MediaTitle) -> None:  # noqa: ANN001
    for title in titles:
        index_title(db, title)
    db.commit()


def test_artist_and_album_search_includes_parent_music_metadata_and_credits() -> None:
    db, _engine = make_session()
    member = make_user("member")
    db.add(member)
    artist, album, _track = add_music(
        db,
        suffix="shared",
        owner=member.id,
        visibility="shared",
        artist_metadata={"styles": ["Etherealwave"], "mood": ["Moonlit"]},
        album_metadata={
            "overview": "Recorded beside the observatory",
            "tags": ["Archivalpressing"],
            "credits": [{"name": "Amina Vale", "role": "Composer"}],
            "composers": ["Sora Finch"],
        },
    )
    artist.name = "Celestial Parentmusician"
    db.commit()
    index_music_titles(db, artist, album)
    search = LibrarySearchService(db)

    assert search.title_ids_for_query(member, "shared", limit=10) == [album.id]
    assert set(search.title_ids_for_query(member, "parentmusician", limit=10)) == {artist.id, album.id}
    assert search.title_ids_for_query(member, "etherealwave", limit=10) == [artist.id]
    assert search.title_ids_for_query(member, "archivalpressing", limit=10) == [album.id]
    assert search.title_ids_for_query(member, "observatory", limit=10) == [album.id]
    assert search.title_ids_for_query(member, "amina", limit=10) == [album.id]
    assert search.title_ids_for_query(member, "sora", limit=10) == [album.id]

    artist.name = "Sunward Ensemble"
    index_title(db, artist)
    db.commit()
    assert set(search.title_ids_for_query(member, "sunward", limit=10)) == {artist.id, album.id}
    assert search.title_ids_for_query(member, "parentmusician", limit=10) == []


def test_private_music_title_track_and_credit_matches_never_leak() -> None:
    db, _engine = make_session()
    owner, other = make_user("owner"), make_user("other")
    db.add_all([owner, other])
    artist, album, track = add_music(
        db,
        suffix="vaultsignal",
        owner=owner.id,
        visibility="private",
        album_metadata={"credits": [{"name": "Hiddencomposer", "role": "Composer"}]},
        track_metadata={"artist": "Source artist", "genres": ["Sourcegenre"]},
    )
    db.commit()
    index_music_titles(db, artist, album)
    search = LibrarySearchService(db)
    search.sync_music_items([track], {track.id: {"title": "Secretrecording", "genres": ["Hiddenrhythm"]}})
    db.commit()

    assert set(search.title_ids_for_query(owner, "vaultsignal", limit=10)) == {artist.id, album.id}
    assert search.title_ids_for_query(owner, "hiddencomposer", limit=10) == [album.id]
    assert search.item_ids_for_query(owner, "secretrecording", limit=10) == [track.id]
    for query in ("vaultsignal", "hiddencomposer"):
        assert search.title_ids_for_query(other, query, limit=10) == []
    for query in ("secretrecording", "hiddenrhythm"):
        assert search.item_ids_for_query(other, query, limit=10) == []


def test_track_overrides_are_searchable_and_revert_restores_source_text() -> None:
    db, engine = make_session()
    member = make_user("member")
    db.add(member)
    _artist, _album, track = add_music(
        db,
        suffix="editable",
        owner=member.id,
        visibility="shared",
        track_metadata={
            "artist": "Originalperformer",
            "album_artist": "Originalensemble",
            "genres": ["Sourcefolk"],
            "composers": ["Rawcomposer"],
        },
    )
    db.commit()
    search = LibrarySearchService(db)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, *_args):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        search.sync_music_items([track], {track.id: {
            "title": "Householdcut", "artist": "Editedperformer", "album_artist": "Editedensemble",
            "genres": ["Nightjazz"],
        }})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    db.commit()

    assert len([statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]) == 2
    for query in ("householdcut", "editedperformer", "editedensemble", "nightjazz", "rawcomposer"):
        assert search.item_ids_for_query(member, query, limit=10) == [track.id]
    assert search.item_ids_for_query(member, "sourcefolk", limit=10) == []
    assert track.title == "Source track editable"
    assert track.metadata_json["genres"] == ["Sourcefolk"]

    search.sync_music_items([track], {})
    db.commit()

    for query in ("householdcut", "nightjazz"):
        assert search.item_ids_for_query(member, query, limit=10) == []
    for query in ("source track", "originalperformer", "originalensemble", "sourcefolk"):
        assert search.item_ids_for_query(member, query, limit=10) == [track.id]


def test_rebuild_loads_overrides_once_and_music_counts_stay_in_sync(monkeypatch) -> None:  # noqa: ANN001
    db, engine = make_session()
    member = make_user("member")
    db.add(member)
    artist, album, first = add_music(
        db,
        suffix="rebuild",
        owner=member.id,
        visibility="shared",
        track_metadata={"genres": ["Rawgenre"]},
    )
    tracks = [first]
    for number in range(19):
        item = LibraryItem(
            id=f"rebuild-track-{number}", user_id=member.id, visibility="shared", title=f"Raw {number}",
            metadata_json={"genres": ["Rawgenre"]}, status="available", kind="track", title_id=album.id,
        )
        tracks.append(item)
        db.add(item)
    db.add_all([
        MusicTrackOverride(item_id=item.id, fields={"title": f"Indexedoverride {number}"})
        for number, item in enumerate(tracks)
    ])
    db.commit()
    statements: list[str] = []

    def capture(_connection, _cursor, statement, *_args):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        ensure_search_index(engine)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    override_reads = [statement for statement in statements if "FROM music_track_overrides" in statement]
    assert len(override_reads) == 1
    search = LibrarySearchService(db)
    assert set(search.title_ids_for_query(member, "rebuild", limit=10)) == {artist.id, album.id}
    assert set(search.item_ids_for_query(member, "indexedoverride", limit=50)) == {item.id for item in tracks}

    monkeypatch.setattr(library_search, "_rebuild", lambda _connection: (_ for _ in ()).throw(AssertionError("rebuilt")))
    ensure_search_index(engine)
