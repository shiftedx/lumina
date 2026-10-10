"""Wall listing: keyset pages for every sort, total and letters, letter seeks, filters, errors."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event

from app.models import LibraryItemArtifact, MediaArtifact, MediaTitle, MemberFavorite, User
from app.services.library import encode_library_cursor
from app.services.titles import TitleFilters, TitleService
from discovery_support import BASE, add_movie, add_progress, add_series, add_version
from support import make_user
from title_support import uid

# (name, year, rating); movie n was added on day n // 3, so three titles share each created_at and ties fall to id.
SHELF = [
    ("Zulu", 2001, 7.5), ("alpha", None, None), ("Alpha", 2001, 7.5), ("Bravo", 1999, 8.0), ("bravo", None, 6.0),
    ("Charlie", 2010, None), ("9 Songs", 2001, 7.5), ("Été", 1999, 8.0), ("_under", None, 7.5), ("Delta", 2010, None),
    ("Echo", 2001, 6.0), ("Foxtrot", 2001, 7.5),
]
# NOCASE folds ASCII only: digits and "_" sort before letters, "É" after "Z"; equal names fall back to id.
NAME_ORDER = ["9 Songs", "_under", "alpha", "Alpha", "Bravo", "bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Zulu", "Été"]
CREATED_ORDER = ["Delta", "Echo", "Foxtrot", "9 Songs", "Été", "_under", "Bravo", "bravo", "Charlie", "Zulu", "alpha", "Alpha"]


@pytest.fixture
def shelf(db_factory):  # noqa: ANN001, ANN201
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member")])
        for n, (name, year, rating) in enumerate(SHELF):
            add_movie(session, uid(100 + n), name, year=year, rating=rating, days=n // 3)
        add_movie(session, uid(199), "Aardvark", owner="owner", visibility="private", genres=["Drama"])  # the owner's alone
        session.commit()
    return db_factory


def client_for(api_client, factory, user_id: str):  # noqa: ANN001, ANN201
    with factory() as session:
        user = session.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def walk(client, **params) -> list[dict]:  # noqa: ANN001
    """Every page of one movie list, checking start_index on the way."""
    pages, cursor, index = [], None, 0
    while True:
        page = client.get("/api/titles", params={"type": "movie", **params, **({"cursor": cursor} if cursor else {})}).json()
        assert page["start_index"] == index, page
        pages.append(page)
        index += len(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return pages


@pytest.mark.parametrize("sort", ["name", "created", "year", "rating"])
def test_keyset_pages_cover_the_list_once_in_order(shelf, api_client, sort) -> None:  # noqa: ANN001
    client = client_for(api_client, shelf, "member")
    whole = client.get("/api/titles", params={"type": "movie", "sort": sort, "limit": 200}).json()
    assert (whole["total"], whole["next_cursor"]) == (12, None)
    pages = walk(client, sort=sort, limit=5)
    assert [len(page["items"]) for page in pages] == [5, 5, 2]
    assert [t["id"] for page in pages for t in page["items"]] == [t["id"] for t in whole["items"]]
    assert all(page["total"] is None and page["letters"] is None for page in pages[1:])


def test_sort_orders_fold_ascii_case_break_ties_by_id_and_put_nulls_last(shelf, api_client) -> None:  # noqa: ANN001
    client = client_for(api_client, shelf, "member")

    def items(sort: str) -> list[dict]:
        return client.get("/api/titles", params={"type": "movie", "sort": sort, "limit": 200}).json()["items"]

    assert [t["name"] for t in items("name")] == NAME_ORDER
    assert [t["name"] for t in items("created")] == CREATED_ORDER
    years = [t["year"] for t in items("year")]
    assert years[:3] == [2010, 2010, 2001] and years[-3:] == [None, None, None]
    ratings = [t["community_rating"] for t in items("rating")]
    assert ratings[:2] == [8.0, 8.0] and ratings[-3:] == [None, None, None]


def test_recently_added_orders_by_file_arrival_and_falls_back_to_created(shelf, api_client) -> None:  # noqa: ANN001
    """added_at (when the files arrived) wins over created_at (when a scan met them); titles without it keep created_at."""
    with shelf() as session:
        session.get(MediaTitle, uid(111)).added_at = BASE + timedelta(days=30)  # Foxtrot: newest file of all
        session.get(MediaTitle, uid(109)).added_at = BASE - timedelta(days=400)  # Delta: its file predates every scan
        session.get(MediaTitle, uid(105)).added_at = BASE + timedelta(days=1)  # Charlie: ties day-1 created_at, id breaks it
        session.commit()
    client = client_for(api_client, shelf, "member")
    whole = client.get("/api/titles", params={"type": "movie", "sort": "created", "limit": 200}).json()["items"]
    assert [t["name"] for t in whole] == [
        "Foxtrot", "Echo", "9 Songs", "Été", "_under", "Bravo", "bravo", "Charlie", "Zulu", "alpha", "Alpha", "Delta",
    ]
    assert [t["id"] for page in walk(client, sort="created", limit=5) for t in page["items"]] == [t["id"] for t in whole]


def test_a_scan_dates_titles_from_file_times_never_later_and_writes_only_changes(db_factory) -> None:  # noqa: ANN001
    """refresh_added_at (the scan path): an upgraded-in-place file never makes a movie or episode later, a new episode
    still lifts its show, a pre-1970 or far-future (2098) mtime is no file time, and an unchanged rescan writes nothing."""
    import uuid
    from datetime import datetime, timezone

    from app.services.media_titles import refresh_added_at

    def at(ns: int) -> datetime:
        return datetime.fromtimestamp(ns / 1e9, timezone.utc).replace(tzinfo=None)

    t1, t2 = 1_600_000_000 * 10**9, 1_700_000_000 * 10**9
    with db_factory() as session:
        session.add(make_user("owner"))
        add_movie(session, "film", "Film")
        add_movie(session, "future", "Future")
        add_series(session, "show", "Show", seasons={1: 2})
        files = {}
        for item_id, mtime_ns in (("film-v", t1), ("show-s1e1-v", t1), ("show-s1e2-v", -500_000_000), ("future-v", 4_039_372_800 * 10**9)):
            files[item_id] = MediaArtifact(id=str(uuid.uuid4()), root_id="r", relative_path=item_id, ownership="external", mtime_ns=mtime_ns)
            session.add_all([files[item_id], LibraryItemArtifact(library_item_id=item_id, artifact_id=files[item_id].id)])
        session.flush()
        titles = ["film", "show", "show-s1", "show-s1e1", "show-s1e2", "future"]

        def dated() -> dict[str, datetime | None]:
            session.expire_all()
            return {title_id: session.get(MediaTitle, title_id).added_at for title_id in titles}

        refresh_added_at(session, titles)
        assert dated() == {"film": at(t1), "show": at(t1), "show-s1": at(t1), "show-s1e1": at(t1), "show-s1e2": None, "future": None}
        files["film-v"].mtime_ns = files["show-s1e2-v"].mtime_ns = t2  # the film re-encoded in place; episode 2's clock fixed
        session.flush()
        refresh_added_at(session, titles)
        assert dated() == {"film": at(t1), "show": at(t2), "show-s1": at(t2), "show-s1e1": at(t1), "show-s1e2": at(t2), "future": None}
        raw = session.connection().connection.dbapi_connection
        before = raw.total_changes
        refresh_added_at(session, titles)
        assert raw.total_changes == before


def test_rescan_never_redates_a_user_dated_or_locked_title(db_factory) -> None:  # noqa: ANN001
    import uuid
    from datetime import datetime

    from app.services.media_titles import refresh_added_at

    t1 = 1_600_000_000 * 10**9
    mine = datetime(2025, 1, 1)  # later than the file, so an unguarded scan would pull it back
    with db_factory() as session:
        session.add(make_user("owner"))
        for title_id in ("edited", "frozen", "plain"):
            add_movie(session, title_id, title_id)
            file = MediaArtifact(id=str(uuid.uuid4()), root_id="r", relative_path=title_id, ownership="external", mtime_ns=t1)
            session.add_all([file, LibraryItemArtifact(library_item_id=f"{title_id}-v", artifact_id=file.id)])
        add_series(session, "show", "Show", seasons={1: 1})
        file = MediaArtifact(id=str(uuid.uuid4()), root_id="r", relative_path="ep", ownership="external", mtime_ns=t1)
        session.add_all([file, LibraryItemArtifact(library_item_id="show-s1e1-v", artifact_id=file.id)])
        session.flush()
        edited, frozen = session.get(MediaTitle, "edited"), session.get(MediaTitle, "frozen")
        season, series = session.get(MediaTitle, "show-s1"), session.get(MediaTitle, "show")
        season.added_at, season.field_sources, series.added_at, series.locked = mine, {"added_at": "user"}, mine, True
        edited.added_at, edited.field_sources = mine, {"added_at": "user"}
        frozen.locked = True
        session.flush()
        refresh_added_at(session, ["edited", "frozen", "plain", "show", "show-s1", "show-s1e1"])
        session.expire_all()
        assert session.get(MediaTitle, "show-s1").added_at == mine and session.get(MediaTitle, "show").added_at == mine  # season and series guards
        assert session.get(MediaTitle, "edited").added_at == mine  # a user date is never re-dated
        assert session.get(MediaTitle, "frozen").added_at is None  # a locked title keeps its (empty) date
        assert session.get(MediaTitle, "plain").added_at is not None  # the guard is not a global off switch


def test_total_and_letters_on_the_first_name_page(shelf, api_client) -> None:  # noqa: ANN001
    client = client_for(api_client, shelf, "member")
    page = client.get("/api/titles", params={"type": "movie", "limit": 2}).json()
    assert page["total"] == 12
    assert page["letters"] == [
        {"letter": "#", "index": 0}, {"letter": "A", "index": 2}, {"letter": "B", "index": 4}, {"letter": "C", "index": 6},
        {"letter": "D", "index": 7}, {"letter": "E", "index": 8}, {"letter": "F", "index": 9}, {"letter": "Z", "index": 10},
    ]
    created = client.get("/api/titles", params={"type": "movie", "sort": "created", "limit": 2}).json()
    assert (created["total"], created["letters"], created["start_index"]) == (12, None, 0)


def test_letter_rail_aggregates_initials_in_sql(shelf) -> None:  # noqa: ANN001
    """A large wall returns one row per initial to Python, not one row per title."""
    engine = shelf.kw["bind"]
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_rest) -> None:  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with shelf() as session:
            TitleService(session).letters(session.get(User, "member"), types=("movie",), filters=TitleFilters())
    finally:
        event.remove(engine, "before_cursor_execute", record)

    rail = next(statement for statement in statements if "upper(substr(coalesce(media_titles.sort_name" in statement)
    assert "count(*)" in rail.lower() and "group by" in rail.lower(), rail


def test_letter_seeks_start_at_the_letter_and_continue(shelf, api_client) -> None:  # noqa: ANN001
    client = client_for(api_client, shelf, "member")
    b = client.get("/api/titles", params={"type": "movie", "letter": "B", "limit": 3}).json()
    assert [t["name"] for t in b["items"]] == ["Bravo", "bravo", "Charlie"]
    assert (b["start_index"], b["total"], b["letters"]) == (4, None, None)
    after = client.get("/api/titles", params={"type": "movie", "limit": 3, "cursor": b["next_cursor"]}).json()
    assert ([t["name"] for t in after["items"]], after["start_index"]) == (["Delta", "Echo", "Foxtrot"], 7)
    empty_letter = client.get("/api/titles", params={"type": "movie", "letter": "G", "limit": 3}).json()
    assert ([t["name"] for t in empty_letter["items"]], empty_letter["start_index"], empty_letter["next_cursor"]) == (["Zulu", "Été"], 10, None)
    start = client.get("/api/titles", params={"type": "movie", "letter": "#", "limit": 3}).json()
    assert (start["items"][0]["name"], start["start_index"]) == ("9 Songs", 0)


def test_counts_letters_and_filters_never_include_another_members_private_title(shelf, api_client) -> None:  # noqa: ANN001
    member = client_for(api_client, shelf, "member")  # api_client's user override is global: one member at a time
    first = member.get("/api/titles", params={"type": "movie", "limit": 1}).json()
    assert first["total"] == 12 and {"letter": "A", "index": 2} in first["letters"]
    assert member.get("/api/titles", params={"type": "movie", "genre": "Drama"}).json()["total"] == 0
    owner = client_for(api_client, shelf, "owner")
    mine = owner.get("/api/titles", params={"type": "movie", "genre": "drama"}).json()
    assert ([t["name"] for t in mine["items"]], mine["total"]) == (["Aardvark"], 1)


def test_invalid_letters_cursors_and_filters(shelf, api_client) -> None:  # noqa: ANN001
    client = client_for(api_client, shelf, "member")
    cursor = client.get("/api/titles", params={"type": "movie", "limit": 2}).json()["next_cursor"]
    invalid = (400, {"detail": "Invalid cursor"})
    for params in (
        {"cursor": "not-a-cursor"},
        {"cursor": cursor, "sort": "year"},  # another sort
        {"cursor": cursor, "unwatched": "true"},  # another filter set
        {"cursor": cursor, "type": "series"},
    ):
        response = client.get("/api/titles", params={"type": "movie", **params})
        assert (response.status_code, response.json()) == invalid, params
    offset = client.get("/api/titles", params={"type": "movie", "limit": 2, "cursor": encode_library_cursor({"o": 2})})
    assert offset.json() == client.get("/api/titles", params={"type": "movie", "limit": 2}).json()  # the old grid's offsets: page one
    digest = TitleFilters().digest(("movie",), "created")
    good = {"v": 2, "s": "created", "f": digest, "k": ["2026-09-01T00:00:00"], "id": uid(1), "i": 2}
    for forged in ({"f": "0" * 12}, {"k": [5]}, {"k": ["not a date"]}, {"k": []}, {"i": 0}, {"i": -3}, {"i": "2"}, {"id": "x" * 37}, {"id": ""}):
        response = client.get("/api/titles", params={"type": "movie", "sort": "created", "cursor": encode_library_cursor({**good, **forged})})
        assert (response.status_code, response.json()) == invalid, forged
    # A created-sort cursor from before titles.added_at (v2, keyed on created_at) restarts at the first page, never a 400.
    old = client.get("/api/titles", params={"type": "movie", "sort": "created", "limit": 2, "cursor": encode_library_cursor(good)}).json()
    assert old == client.get("/api/titles", params={"type": "movie", "sort": "created", "limit": 2}).json()
    for sort, keys in (("year", [2**70, "x"]), ("rating", [2**70, "x"]), ("rating", [1e300, "x"]), ("rating", [float("nan"), "x"])):
        forged = {**good, "s": sort, "f": TitleFilters().digest(("movie",), sort), "k": keys}  # SQLite integers overflow past 2**63
        response = client.get("/api/titles", params={"type": "movie", "sort": sort, "cursor": encode_library_cursor(forged)})
        assert (response.status_code, response.json()) == invalid, (sort, keys)
    letter = (400, {"detail": "Invalid letter"})
    for params in ({"sort": "created", "letter": "A"}, {"letter": "A", "cursor": cursor}):
        response = client.get("/api/titles", params={"type": "movie", **params})
        assert (response.status_code, response.json()) == letter, params
    for params in (
        {"letter": "a"}, {"letter": "AB"}, {"genre": ["x"] * 11}, {"genre": "x" * 65}, {"genre": ""},
        {"year_from": 1869}, {"year_to": 2101}, {"resolution": "8k"}, {"resolution": ["4k"] * 5}, {"limit": 0},
    ):
        assert client.get("/api/titles", params={"type": "movie", **params}).status_code == 422, params
    empty = client.get("/api/titles", params={"type": "movie", "year_from": 2010, "year_to": 2000}).json()
    assert (empty["items"], empty["total"], empty["next_cursor"]) == ([], 0, None)


def test_year_and_rating_cursors_carry_any_stored_value(db_factory, api_client) -> None:  # noqa: ANN001
    """NFO years and ratings are not always plausible (an 1869 film, a 9999 typo, a -1 rating): the wall must page past them."""
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member")])
        for n, (name, year, rating) in enumerate((("Early", 1869, -1.0), ("Typo", 9999, 1e6), ("Zero", 0, 5.0), ("Plain", 2001, None))):
            add_movie(session, uid(300 + n), name, year=year, rating=rating)
        session.commit()
    client = client_for(api_client, db_factory, "member")
    for sort in ("year", "rating"):
        pages = walk(client, sort=sort, limit=1)
        assert sorted(t["name"] for page in pages for t in page["items"]) == ["Early", "Plain", "Typo", "Zero"], sort
    for keys in ([1869, "Early"], [-1, "Early"]):
        sort = "year" if keys[0] == 1869 else "rating"
        cursor = encode_library_cursor({"v": 2, "s": sort, "f": TitleFilters().digest(("movie",), sort), "k": keys, "id": uid(300), "i": 1})
        assert client.get("/api/titles", params={"type": "movie", "sort": sort, "cursor": cursor}).status_code == 200, keys


def test_a_page_is_a_fixed_number_of_queries(shelf, api_client) -> None:  # noqa: ANN001
    client = client_for(api_client, shelf, "member")
    engine = shelf.kw["bind"]
    counts = []
    for limit in (3, 12):
        statements: list[str] = []

        def record(_connection, _cursor, statement, *_rest) -> None:  # noqa: ANN001
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert client.get("/api/titles", params={"type": "movie", "limit": limit}).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)
        counts.append(sum(1 for statement in statements if statement.lstrip().upper().startswith("SELECT")))
    assert counts[0] == counts[1] <= 9, counts  # page + letters + TitleService.load (≤ 7)


def _names(session, user_id: str, types: tuple[str, ...], **filters) -> list[str]:  # noqa: ANN001
    titles, _start, _next = TitleService(session).page(session.get(User, user_id), types=types, sort="name", limit=200, filters=TitleFilters(**filters))
    return [title.name for title in titles]


def _probe(session, item_id: str, width: int, height: int) -> None:  # noqa: ANN001
    artifact = MediaArtifact(id=f"{item_id}-a", root_id="root", relative_path=f"{item_id}.mkv", ownership="external", probe={"width": width, "height": height})
    session.add_all([artifact, LibraryItemArtifact(library_item_id=item_id, artifact_id=artifact.id)])


def test_watched_state_chips_for_movies_and_shows(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member"), make_user("other")])
        for movie_id, name in (("m-new", "New"), ("m-half", "Half"), ("m-done", "Done"), ("m-redo", "Redo")):
            add_movie(session, movie_id, name)
        add_version(session, "m-redo-4k", "m-redo")
        for series_id, name, count in (("s-fresh", "Fresh", 2), ("s-mid", "Mid", 3), ("s-all", "All", 2), ("s-started", "Started", 2)):
            add_series(session, series_id, name, seasons={1: count})
        add_progress(session, "member", "m-half-v", position=300)
        add_progress(session, "member", "m-done-v", completed=True)
        add_progress(session, "member", "m-redo-v", completed=True, minutes_ago=100)  # finished once …
        add_progress(session, "member", "m-redo-4k", position=90, minutes_ago=5)  # … and rewatching: the latest row counts
        add_progress(session, "member", "s-mid-s1e1-v", completed=True)
        add_progress(session, "member", "s-all-s1e1-v", completed=True)
        add_progress(session, "member", "s-all-s1e2-v", completed=True)
        add_progress(session, "member", "s-started-s1e1-v", position=100)
        add_progress(session, "other", "m-new-v", completed=True)  # another member's history never counts
        session.add_all([MemberFavorite(user_id="member", target_id="m-new"), MemberFavorite(user_id="member", target_id="s-mid")])
        session.commit()
        movies, shows = ("movie",), ("series",)
        assert _names(session, "member", movies, unwatched=True) == ["Half", "New", "Redo"]
        assert _names(session, "member", movies, in_progress=True) == ["Half", "Redo"]
        assert _names(session, "member", movies, favorites=True) == ["New"]
        assert _names(session, "member", movies, unwatched=True, favorites=True) == ["New"]
        assert _names(session, "member", shows, unwatched=True) == ["Fresh", "Mid", "Started"]
        assert _names(session, "member", shows, in_progress=True) == ["Mid", "Started"]
        assert _names(session, "member", shows, favorites=True) == ["Mid"]
        assert _names(session, "other", movies, unwatched=True) == ["Done", "Half", "Redo"]


def test_genre_or_and_inclusive_year_range(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member")])
        add_movie(session, "g-drama", "Drama One", genres=["Drama"], year=1999)
        add_movie(session, "g-comedy", "Comedy Two", genres=["comedy", "Romance"], year=2005)
        add_movie(session, "g-2000", "Millennium", genres=["War"], year=2000)
        add_movie(session, "g-none", "Plain", year=None)
        session.commit()
        movies = ("movie",)
        assert _names(session, "member", movies, genres=("drama", "COMEDY")) == ["Comedy Two", "Drama One"]
        assert _names(session, "member", movies, year_from=2000) == ["Comedy Two", "Millennium"]
        assert _names(session, "member", movies, year_to=2000) == ["Drama One", "Millennium"]
        assert _names(session, "member", movies, year_from=2000, year_to=2000) == ["Millennium"]
        assert _names(session, "member", movies, year_from=2006) == []


def test_resolution_buckets_for_versions_and_episodes(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member")])
        for movie_id, name, size in (
            ("r-uhd", "UHD", (3840, 2160)), ("r-wide", "Wide", (3996, 1680)), ("r-scope", "Scope", (1920, 800)),
            ("r-hd", "HD", (1280, 720)), ("r-sd", "SD", (720, 480)),
        ):
            add_movie(session, movie_id, name)
            _probe(session, f"{movie_id}-v", *size)
        add_movie(session, "r-none", "Unprobed")
        add_movie(session, "r-dual", "Dual")
        _probe(session, "r-dual-v", 1920, 1080)
        add_version(session, "r-dual-4k", "r-dual")
        _probe(session, "r-dual-4k", 3840, 2160)
        add_movie(session, "r-gone", "Gone")
        _probe(session, "r-gone-v", 720, 480)
        add_version(session, "r-gone-4k", "r-gone", status="missing")  # missing files and extras never count
        _probe(session, "r-gone-4k", 3840, 2160)
        add_version(session, "r-gone-trailer", "r-gone", extra_type="trailer")
        _probe(session, "r-gone-trailer", 3840, 2160)
        add_movie(session, "r-private", "Private", owner="owner", visibility="private")
        _probe(session, "r-private-v", 3840, 2160)
        add_series(session, "s-mixed", "Mixed", seasons={1: 2})
        _probe(session, "s-mixed-s1e1-v", 1920, 1080)
        _probe(session, "s-mixed-s1e2-v", 3840, 2160)
        add_series(session, "s-small", "Small", seasons={1: 1})
        _probe(session, "s-small-s1e1-v", 640, 360)
        session.commit()

        def movies(*buckets: str) -> list[str]:
            return _names(session, "member", ("movie",), resolutions=buckets)

        def shows(*buckets: str) -> list[str]:
            return _names(session, "member", ("series",), resolutions=buckets)

        assert movies("4k") == ["Dual", "UHD", "Wide"]
        assert movies("1080p") == ["Dual", "Scope"]
        assert movies("720p") == ["HD"]
        assert movies("sd") == ["Gone", "SD"]
        assert movies("4k", "sd") == ["Dual", "Gone", "SD", "UHD", "Wide"]
        assert (shows("4k"), shows("1080p"), shows("720p"), shows("sd")) == (["Mixed"], ["Mixed"], [], ["Small"])
