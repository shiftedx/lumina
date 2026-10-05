"""Library gallery T3: album and artist pages and music summaries."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event

from app.models import LibraryItem, MediaTitle, PlaybackProgress, User
from app.services import art_urls
from support import make_user
from title_support import ALBUM, ALICE, ARTIST, BOB, ROOT, T0, TRACKS, add_file, seed_gallery, seed_tree, uid


@pytest.fixture(autouse=True)
def fresh_queue(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(art_urls, "on_enqueue", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")


@pytest.fixture
def music(db_factory, tmp_path):  # noqa: ANN001, ANN201
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_gallery(session, root)
        session.commit()
    return db_factory, root


def member(api_client, factory, user_id: str):  # noqa: ANN001, ANN201
    """A client for ``user_id``. Build a fresh one before each use: api_client's user override is global."""
    with factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def selects(factory, run) -> int:  # noqa: ANN001
    engine = factory.kw["bind"]
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_rest) -> None:  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return sum(1 for statement in statements if statement.lstrip().upper().startswith("SELECT"))


def test_an_album_page_lists_its_tracks_with_the_members_progress(music, api_client) -> None:  # noqa: ANN001
    factory, _root = music
    with factory() as session:
        session.add_all([
            PlaybackProgress(id="p1", user_id=ALICE, item_id=TRACKS[0], position_seconds=0, duration_seconds=180, completed=True, last_watched_at=T0),
            PlaybackProgress(id="p2", user_id=ALICE, item_id=TRACKS[1], position_seconds=60, duration_seconds=180, completed=False,
                             last_watched_at=T0 + timedelta(hours=1)),
            PlaybackProgress(id="p3", user_id=BOB, item_id=TRACKS[2], position_seconds=90, duration_seconds=180, completed=False, last_watched_at=T0),
        ])
        session.commit()
    album = member(api_client, factory, ALICE).get(f"/api/titles/{ALBUM}").json()
    assert (album["type"], album["artist_name"], album["child_count"], album["children"], album["category"]) == ("album", "Artist A", 3, [], None)
    assert [(track["item_id"], track["disc"], track["number"], track["name"]) for track in album["tracks"]] == [
        (TRACKS[0], 1, 1, "Song 1.1"), (TRACKS[1], 1, 2, "Song 1.2"), (TRACKS[2], 2, 1, "Song 2.1"),
    ]
    assert [track["artist"] for track in album["tracks"]] == [None, None, None]  # the album artist is not repeated
    assert [track["duration_seconds"] for track in album["tracks"]] == [180, 180, 180]
    played, started, fresh = (track["user_data"] for track in album["tracks"])
    assert played["played"] and (started["played"], started["position_seconds"]) == (False, 60)
    assert (fresh["position_seconds"], fresh["last_watched_at"]) == (0, None)  # bob's progress is bob's
    assert album["poster"]["widths"] == [240, 480, 960]
    assert (album["versions"], album["extras"], album["people"], album["boxset"]) == ([], [], [], None)


def test_track_order_puts_unknown_discs_and_numbers_last_and_names_differing_artists(music, api_client) -> None:  # noqa: ANN001
    factory, root = music
    with factory() as session:
        add_file(session, root, "Music/Artist A/Album One (2019)/Bonus.flac", item_id=uid(174), title_id=ALBUM, owner=ALICE,
                 title="Bonus", kind="track", duration=100, metadata={"disc_number": 1, "artist": "Singer B"})
        add_file(session, root, "Music/Artist A/Album One (2019)/Hidden.flac", item_id=uid(175), title_id=ALBUM, owner=ALICE,
                 title="Hidden", kind="track", duration=100, metadata={"track_number": 1, "artist": "ARTIST A"})
        session.get(LibraryItem, TRACKS[2]).status = "missing"
        session.commit()
    album = member(api_client, factory, ALICE).get(f"/api/titles/{ALBUM}").json()
    assert [(track["disc"], track["number"], track["name"], track["artist"]) for track in album["tracks"]] == [
        (1, 1, "Song 1.1", None), (1, 2, "Song 1.2", None), (1, None, "Bonus", "Singer B"), (None, 1, "Hidden", None),
    ]
    assert album["child_count"] == 4  # the missing track is not counted


def test_an_artist_page_lists_its_visible_albums_newest_first(music, api_client) -> None:  # noqa: ANN001
    factory, root = music
    with factory() as session:
        session.add_all([
            MediaTitle(id=uid(72), type="album", key="album:artist a/later", root_id=ROOT, name="Later", parent_id=ARTIST, year=2021),
            MediaTitle(id=uid(73), type="album", key="album:artist a/undated", root_id=ROOT, name="Undated", parent_id=ARTIST),
            MediaTitle(id=uid(74), type="album", key="album:artist a/empty", root_id=ROOT, name="Empty", parent_id=ARTIST, year=2030),
        ])
        add_file(session, root, "Music/Artist A/Later/01 Late.flac", item_id=uid(176), title_id=uid(72), owner=ALICE, title="Late", kind="track")
        add_file(session, root, "Music/Artist A/Undated/01 Old.flac", item_id=uid(177), title_id=uid(73), owner=ALICE, title="Old", kind="track")
        session.commit()
    artist = member(api_client, factory, ALICE).get(f"/api/titles/{ARTIST}").json()
    assert [child["name"] for child in artist["children"]] == ["Later", "Album One", "Undated"]  # "Empty" has no track
    assert [child["child_count"] for child in artist["children"]] == [1, 3, 1]
    assert {child["artist_name"] for child in artist["children"]} == {"Artist A"}
    assert (artist["type"], artist["child_count"], artist["tracks"], artist["artist_name"]) == ("artist", 3, None, None)


def test_a_members_private_album_is_invisible_to_others(music, api_client) -> None:  # noqa: ANN001
    factory, root = music
    with factory() as session:
        session.add(MediaTitle(id=uid(80), type="album", key="album:artist a/secret", root_id=ROOT, name="Secret", parent_id=ARTIST))
        add_file(session, root, "Music/Artist A/Secret/01 A.flac", item_id=uid(180), title_id=uid(80), owner=BOB, visibility="private",
                 title="A", kind="track")
        session.commit()
    assert member(api_client, factory, ALICE).get(f"/api/titles/{uid(80)}").json() == {"detail": "Title not found"}
    assert [track["name"] for track in member(api_client, factory, BOB).get(f"/api/titles/{uid(80)}").json()["tracks"]] == ["A"]
    assert [child["name"] for child in member(api_client, factory, ALICE).get(f"/api/titles/{ARTIST}").json()["children"]] == ["Album One"]


def test_album_and_artist_pages_are_a_fixed_number_of_queries(music, api_client) -> None:  # noqa: ANN001
    factory, _root = music
    alice = member(api_client, factory, ALICE)
    # +1 since 2.9.0 (album 7, artist 6): art_scope reads the member's access row once per request (a PK get, memoized) to bind a restricted
    # member's art signatures to their access; nothing earlier on this page reads that row, and admins skip it.
    assert selects(factory, lambda: alice.get(f"/api/titles/{ALBUM}").raise_for_status()) <= 7
    assert selects(factory, lambda: alice.get(f"/api/titles/{ARTIST}").raise_for_status()) <= 6  # +1 likewise
