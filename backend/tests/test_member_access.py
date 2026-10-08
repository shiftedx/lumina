"""Member access (ADR 0019): Sections and rating ceilings inside the one visibility predicate, the admin API, ratings.

The leak matrix seeds the shared title fixture (+ the gallery's anime and music, + an imported home video) and proves a
kid (shows only, G / TV-Y7, unrated hidden), a guest (music only), an adult (no row) and an admin each see exactly their
set on every surface: library list, search, Home shelves, title pages, artwork, playback start, the Jellyfin API and the
requests catalog.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import MediaTitle, MemberAccess, User
from app.security import get_current_user
from app.services import member_access
from app.services.media_titles import apply_field, jellyfin_id
from app.services.requests.status import catalog_statuses
from support import make_user
from title_support import (
    ALBUM, ALICE, ANIME_EPISODE, ANIME_FILE, ANIME_MOVIE, ANIME_MOVIE_FILE, ANIME_SERIES, CHANNEL_NEW, CHANNEL_OLD, FILE,
    MOVIE, MOVIE_1080, MOVIE_4K, ROOT, S0E1, S1E1, S1E2, S2E1, SEASON1, SERIES, TRACKS, TRAILER, add_file, device_token,
    jellyfin_household, mediabrowser, seed_gallery, uid,
)

KID, GUEST, ADULT, ADMIN = uid(0xC1D), uid(0x6E5), uid(0xAD1), uid(0xAD0)
HOME_VIDEO = uid(180)
TOKENS = {KID: "kid-device-token-for-tests", GUEST: "guest-device-token-for-tests", ADULT: "adult-device-token-for-tests",
          ADMIN: "admin-device-token-for-tests"}
SHOW_FILES = {FILE[e] for e in (S0E1, S1E1, S1E2, S2E1)}
MOVIE_FILES = {MOVIE_1080, MOVIE_4K, TRAILER}
VAULT = {CHANNEL_OLD, CHANNEL_NEW}
ALL_SHARED = SHOW_FILES | MOVIE_FILES | VAULT | {ANIME_FILE, ANIME_MOVIE_FILE, HOME_VIDEO, *TRACKS}
SEES = {  # item ids each viewer may reach
    KID: SHOW_FILES,
    GUEST: set(TRACKS) | {HOME_VIDEO},
    ADULT: ALL_SHARED,
    ADMIN: ALL_SHARED,
}
TOP_TITLES = {SERIES: SHOW_FILES, MOVIE: MOVIE_FILES, ANIME_SERIES: {ANIME_FILE}, ANIME_MOVIE: {ANIME_MOVIE_FILE}, ALBUM: set(TRACKS)}


@pytest.fixture
def household(tmp_path: Path):  # noqa: ANN201
    root = tmp_path.resolve() / "media"
    jellyfin_household(root)
    with db_module.SessionLocal() as session:
        seed_gallery(session, root)
        session.add_all([make_user(KID), make_user(GUEST), make_user(ADULT), make_user(ADMIN, role="admin")])
        for user_id, token in TOKENS.items():
            device_token(session, user_id, token, device_id=f"device-{user_id}")
        add_file(session, root, "Home Videos/beach.mp4", item_id=HOME_VIDEO, title_id=None, owner=ALICE, title="Beach day",
                 kind="video", extractor="external_library")
        session.flush()
        for title_id, rating in ((MOVIE, "Rated R"), (SERIES, "US:TV-Y7"), (ANIME_MOVIE, "PG")):  # ANIME_SERIES unrated
            apply_field(session.get(MediaTitle, title_id), "official_rating", rating, "nfo")
        session.commit()
    client = TestClient(app, base_url="http://localhost")
    admin = as_user(client, ADMIN)
    assert admin.put(f"/api/admin/members/{KID}/access", json={
        "sections": ["shows", "movies", "anime"], "movie_rating_max": "G", "tv_rating_max": "TV-Y7", "unrated": "hide",
    }).status_code == 200
    assert admin.put(f"/api/admin/members/{GUEST}/access", json={"sections": ["music", f"root:{ROOT}"]}).status_code == 200
    yield client
    app.dependency_overrides.pop(get_current_user, None)
    client.close()


def as_user(client: TestClient, user_id: str) -> TestClient:
    with db_module.SessionLocal() as session:
        user = member_access.carry_access(session, session.get(User, user_id), session.get(User, user_id))  # as get_current_user's
        session.expunge(user)
    app.dependency_overrides[get_current_user] = lambda: user
    return client


def jf(client: TestClient, method: str, path: str, user_id: str, **kwargs):  # noqa: ANN003, ANN201
    app.dependency_overrides.pop(get_current_user, None)
    return client.request(method, path, headers=mediabrowser(TOKENS[user_id]), **kwargs)


def test_ratings_are_normalized_ranked_and_inherited(household: TestClient) -> None:
    with db_module.SessionLocal() as session:
        rated = {t.id: (t.rating, t.rating_rank) for t in session.query(MediaTitle).filter(MediaTitle.id.in_([MOVIE, SERIES, SEASON1, S1E1, ANIME_EPISODE])).all()}
        assert rated == {MOVIE: ("R", 4), SERIES: ("TV-Y7", 2), SEASON1: ("TV-Y7", 2), S1E1: ("TV-Y7", 2), ANIME_EPISODE: (None, None)}
        apply_field(session.get(MediaTitle, SERIES), "official_rating", "TV-MA", "tmdb")  # below the NFO's rank: kept
        session.commit()
        assert session.get(MediaTitle, S1E1).rating == "TV-Y7"
        apply_field(session.get(MediaTitle, SERIES), "official_rating", "TV-MA", "user")  # a hand edit wins and cascades
        session.commit()
        assert (session.get(MediaTitle, S1E1).rating, session.get(MediaTitle, S1E1).rating_rank) == ("TV-MA", 5)
    assert SHOW_FILES.isdisjoint(ids(as_user(household, KID).get("/api/library", params={"limit": 100})))


def ids(response) -> set[str]:  # noqa: ANN001
    body = response.json()
    return {entry["id"] for entry in body["items"]}


@pytest.mark.parametrize("viewer", [KID, GUEST, ADULT, ADMIN])
def test_no_surface_leaks_a_title_outside_the_members_access(household: TestClient, viewer: str) -> None:
    sees = SEES[viewer]
    client = as_user(household, viewer)
    assert ids(client.get("/api/library", params={"limit": 100})) & ALL_SHARED == sees
    for item_id in ALL_SHARED:
        assert (client.get(f"/api/library/{item_id}").status_code == 200) == (item_id in sees), item_id
        assert (client.get(f"/api/library/{item_id}/up-next").status_code == 200) == (item_id in sees), item_id
        started = client.post(f"/api/library/{item_id}/playback-sessions")
        assert (started.status_code != 404) == (item_id in sees), (item_id, started.status_code)
    for title_id, files in TOP_TITLES.items():
        visible = bool(files & sees)
        assert (client.get(f"/api/titles/{title_id}").status_code == 200) == visible, title_id
        art = client.get(f"/api/titles/{title_id}/images/Primary")
        assert art.status_code == 404 or visible, title_id
    walls = {t["id"] for kind in ("movie", "series") for t in client.get("/api/titles", params={"type": kind, "limit": 100}).json()["items"]}
    assert walls == {t for t, files in TOP_TITLES.items() if files & sees and t != ALBUM}
    shelves = client.get("/api/home/title-rows").json()["rows"]
    assert all(entry["id"] in {t for t, f in TOP_TITLES.items() if f & sees} for row in shelves for entry in row["items"])
    all_titles = {t for t in TOP_TITLES} | {S1E1, ANIME_EPISODE}
    visible_titles = {t for t, f in TOP_TITLES.items() if f & sees} | ({S1E1} if SHOW_FILES & sees else set()) | ({ANIME_EPISODE} if ANIME_FILE in sees else set())
    for query in ("Pilot", "Movie", "Film", "Song", "upload", "Beach", "Arrival"):
        found = client.get("/api/search", params={"q": query}).json()
        items = {item["id"] for item in found["items"]} | {m["item"]["id"] for m in found["matches"] if m["item"]}
        titles = {m["title_id"] for m in found["matches"] if m["title_id"]} | {m["id"] for m in found["matches"] if m["kind"] == "title"}
        assert items & ALL_SHARED <= sees and titles & all_titles <= visible_titles, (query, items, titles)
    assert any(m["kind"] == "title" for m in client.get("/api/search", params={"q": "Pilot"}).json()["matches"]) == bool(SHOW_FILES & sees)
    with db_module.SessionLocal() as session:
        status = catalog_statuses(session, session.get(User, viewer), ["movie:603", "show:100"])
    assert (status["movie:603"]["state"] == "available") == bool(MOVIE_FILES & sees)
    assert (status["show:100"]["state"] == "available") == bool(SHOW_FILES & sees)


@pytest.mark.parametrize("viewer", [KID, GUEST, ADULT])
def test_the_jellyfin_api_obeys_the_same_access(household: TestClient, viewer: str) -> None:
    sees = SEES[viewer]
    hexes = {jellyfin_id(t): t for t in TOP_TITLES}
    listed = jf(household, "GET", "/Items", viewer, params={"Recursive": "true", "IncludeItemTypes": "Movie,Series", "Limit": 100}).json()
    assert {hexes[i["Id"]] for i in listed["Items"] if i["Id"] in hexes} == {t for t, f in TOP_TITLES.items() if f & sees and t != ALBUM}
    for title_id, files in TOP_TITLES.items():
        if title_id == ALBUM:
            continue
        visible = bool(files & sees)
        assert (jf(household, "GET", f"/Items/{jellyfin_id(title_id)}", viewer).status_code == 200) == visible
        leaf = {SERIES: S1E1, ANIME_SERIES: ANIME_EPISODE}.get(title_id, title_id)
        info = jf(household, "POST", f"/Items/{jellyfin_id(leaf)}/PlaybackInfo", viewer, json={"DeviceProfile": {}})
        assert (info.status_code == 200) == visible, (leaf, info.status_code)
    for term, title_id, files in (("Pilot", S1E1, SHOW_FILES), ("Movie", MOVIE, MOVIE_FILES)):  # the fixture's indexed titles
        hints = {h["Id"] for h in jf(household, "GET", "/Search/Hints", viewer, params={"SearchTerm": term}).json()["SearchHints"]}
        assert (jellyfin_id(title_id) in hints) == bool(files & sees), term


def test_admin_access_api(household: TestClient) -> None:
    admin = as_user(household, ADMIN)
    kid = admin.get(f"/api/admin/members/{KID}/access").json()
    assert kid["restricted"] and kid["sections"] == ["anime", "movies", "shows"] and kid["streaming"]["youtube"] is True
    assert admin.get(f"/api/admin/members/{ADULT}/access").json()["restricted"] is False
    assert admin.put(f"/api/admin/members/{ADMIN}/access", json={}).status_code == 409
    assert admin.put(f"/api/admin/members/{KID}/access", json={"sections": ["../etc"]}).status_code == 422
    assert admin.put(f"/api/admin/members/{KID}/access", json={"schedule": {"mon": [["20:00", "07:00"]]}}).status_code == 422
    assert admin.get(f"/api/admin/members/{uid(1234)}/access").status_code == 404
    sections = {s["id"]: s["count"] for s in admin.get("/api/admin/sections").json()}
    assert sections == {"movies": 1, "shows": 2, "anime": 2, "music": 1, "vault": 3, f"root:{ROOT}": 1}
    listed = {row["id"]: row for row in admin.get("/api/admin/users").json()}
    assert listed[ADULT]["access"] is None and listed[KID]["access"]["tv_rating_max"] == "TV-Y7"
    assert listed[KID]["screen_time_today_seconds"] == 0 and kid["screen_time_today_seconds"] == 0 and kid["bonus_minutes_today"] == 0
    me = as_user(household, KID).get("/api/me/access").json()
    assert me == {"restricted": True, "sections": ["anime", "movies", "shows"], "blocked_streaming": [], "followed_only": False,
                  "can_download": True, "schedule_state": None, "allowed_now": True, "until": None, "remaining_minutes": None}
    kid_client = as_user(household, KID)
    assert kid_client.get(f"/api/admin/members/{GUEST}/access").status_code == 403  # never another member's limits
    assert kid_client.put(f"/api/admin/members/{KID}/access", json={}).status_code == 403
    assert as_user(household, ADULT).get("/api/me/access").json()["restricted"] is False
    # Lifting the limits shows everything again at once (the per-session memo is invalidated on save).
    admin = as_user(household, ADMIN)
    assert admin.put(f"/api/admin/members/{KID}/access", json={"streaming": {"youtube": False}}).json()["sections"] is None
    kid_client = as_user(household, KID)
    assert ids(kid_client.get("/api/library", params={"limit": 100})) & ALL_SHARED == ALL_SHARED
    assert kid_client.get("/api/me/access").json()["blocked_streaming"] == ["youtube"]
    with db_module.SessionLocal() as session:
        assert session.get(MemberAccess, KID).streaming["youtube"] is False


@pytest.mark.parametrize(("raw", "expected"), [
    ("Rated R", "R"), ("US:PG-13", "PG-13"), ("tv14", "TV-14"), ("TV-Y7-FV", "TV-Y7"), ("GB:15", None), ("US:R / GB:15", "R"),
    ("GB:15 / US:PG", "PG"), ("NR", None), ("Not Rated", None), ("UNRATED", None), ("nc17", "NC-17"), ("G", "G"),
    ("Rated PG-13 for violence", "PG-13"), ("DE:12", None), (None, None), ("", None), ("TV-G", "TV-G"),
])
def test_normalize_rating(raw: str | None, expected: str | None) -> None:
    assert member_access.normalize_rating(raw) == expected


def test_fill_in_ratings_backfills_without_network_and_queues_matched_unrated(household: TestClient) -> None:
    from datetime import datetime

    from sqlalchemy import text

    with db_module.SessionLocal() as session:  # a pre-2.8 database: official ratings stored, rating columns empty
        session.execute(text("UPDATE media_titles SET rating = NULL, rating_rank = NULL"))
        session.execute(text("UPDATE media_titles SET metadata_json = json_remove(metadata_json, '$.official_rating') WHERE id = :id"), {"id": MOVIE})
        session.execute(text("UPDATE media_titles SET metadata_due_at = NULL"))
        session.commit()
    now = datetime(2100, 1, 1)  # later than the background run's due time: nothing is pulled forward again
    assert as_user(household, ADMIN).post("/api/admin/ratings/fill").status_code == 202  # ran in the background task
    with db_module.SessionLocal() as session:
        assert (session.get(MediaTitle, S1E1).rating, session.get(MediaTitle, ANIME_MOVIE).rating) == ("TV-Y7", "PG")
        assert session.get(MediaTitle, MOVIE).rating is None and session.get(MediaTitle, MOVIE).metadata_due_at is not None  # matched: TMDB
        assert session.get(MediaTitle, ANIME_MOVIE).metadata_due_at is None  # rated already, unmatched: nothing to fetch
    assert member_access.fill_ratings(now) == {"rated": 0, "queued": 0}  # idempotent
    member_access._filling.acquire()
    try:
        with pytest.raises(RuntimeError):
            member_access.fill_ratings(now)
    finally:
        member_access._filling.release()


def test_nfo_certification_is_read_when_there_is_no_mpaa() -> None:
    from app.services.local_metadata import nfo_title_fields

    assert member_access.normalize_rating(nfo_title_fields("movie", {"certification": "US:PG-13 / GB:12A"})["official_rating"]) == "PG-13"
    assert nfo_title_fields("movie", {"mpaa": "Rated R", "certification": "US:PG"})["official_rating"] == "Rated R"


def test_a_bare_transient_user_gets_the_same_answer_from_the_row_itself(household: TestClient) -> None:
    from sqlalchemy import select

    from app.models import LibraryItem
    from app.services.library import LibraryService

    with db_module.SessionLocal() as session:
        for viewer in (KID, GUEST, ADULT):
            stored = session.get(User, viewer)
            bare = make_user(viewer)  # no session, no carried access: the uncorrelated-subquery form
            carried = member_access.carry_access(session, make_user(viewer), stored)
            answers = [set(session.scalars(select(LibraryItem.id).where(LibraryService.visible_predicate(u)))) & ALL_SHARED for u in (bare, carried)]
            assert answers[0] == answers[1] == SEES[viewer], viewer


@pytest.mark.parametrize(("viewer", "views"), [
    (KID, {"Movies", "Shows"}),  # anime granted but every anime title is unrated: no Anime tile
    (GUEST, set()),  # music and a root only: no title library at all
    (ADULT, {"Movies", "Shows", "Anime"}),
])
def test_jellyfin_libraries_without_a_section_are_absent(household: TestClient, viewer: str, views: set[str]) -> None:
    from app.services.jellyfin import VIEWS
    from app.services.media_titles import synthetic_id

    title_views = {"Movies", "Shows", "Anime"}
    for path in ("/UserViews", f"/Users/{jellyfin_id(viewer)}/Views", "/Items"):
        assert {v["Name"] for v in jf(household, "GET", path, viewer).json()["Items"]} & title_views == views, path
    folders = {v["Name"] for v in jf(household, "GET", "/Library/VirtualFolders", viewer).json()}
    assert folders & title_views == views
    for view, (name, _collection) in VIEWS.items():
        if name not in title_views:
            continue
        view_id = jellyfin_id(synthetic_id(f"view:{view}"))
        listed = jf(household, "GET", "/Items", viewer, params={"ParentId": view_id})
        latest = jf(household, "GET", "/Items/Latest", viewer, params={"ParentId": view_id})
        # An ungranted library lists as empty, exactly like a missing one (Jellyfin apps read a 404 body as data).
        assert (listed.status_code, latest.status_code) == (200, 200), name
        if viewer == GUEST:
            assert (listed.json()["Items"], latest.json()) == ([], []), name


@pytest.mark.parametrize("viewer", [KID, ADULT])
def test_a_request_never_links_a_title_the_viewer_cannot_see(household: TestClient, viewer: str) -> None:
    from app.models import MediaRequest

    with db_module.SessionLocal() as session:
        session.add(MediaRequest(id=uid(0xEE0 + int(viewer == ADULT)), kind="movie", media_type="movie", tmdb_id=603, title="Movie",
                                 status="available", requested_by=viewer, library_title_id=MOVIE))
        session.commit()
    sees_movie = viewer == ADULT
    [mine] = as_user(household, viewer).get("/api/requests").json()["items"]
    assert (mine.get("library_title_id") == MOVIE, mine["status"]) == ((True, "available") if sees_movie else (False, "approved"))
    with db_module.SessionLocal() as session:
        status = catalog_statuses(session, session.get(User, viewer), ["movie:603"])["movie:603"]
    assert (status.get("library_title_id"), status["state"]) == ((MOVIE, "available") if sees_movie else (None, "approved"))


@pytest.mark.parametrize(("movie_cert", "series_cert", "movie_max", "tv_max", "sees_movie", "sees_series"), [
    ("TV-14", "PG-13", "PG-13", "TV-14", True, True),  # cross-scale equivalents pass the matching ceiling
    ("TV-14", "PG-13", "PG", "TV-PG", False, False),  # ...and nothing looser
    ("TV-MA", "R", "R", "TV-14", True, False),  # TV-MA = R; R = TV-MA
    ("TV-MA", "NC-17", "PG-13", "TV-MA", False, True),
    ("TV-Y7", "G", "G", "TV-Y7", True, True),  # TV-Y7 = G; G = TV-G (= TV-Y7's rank)
    ("TV-PG", "G", "G", "TV-Y", False, False),  # TV-PG = PG; G is stricter-ranked than TV-Y
])
def test_ratings_from_the_other_scale_compare_with_their_equivalent(
    household: TestClient, movie_cert: str, series_cert: str, movie_max: str, tv_max: str, sees_movie: bool, sees_series: bool,
) -> None:
    from sqlalchemy import select

    from app.services.library import LibraryService

    with db_module.SessionLocal() as session:
        apply_field(session.get(MediaTitle, MOVIE), "official_rating", movie_cert, "user")
        apply_field(session.get(MediaTitle, SERIES), "official_rating", series_cert, "user")
        session.commit()
    admin = as_user(household, ADMIN)
    assert admin.put(f"/api/admin/members/{KID}/access", json={"movie_rating_max": movie_max, "tv_rating_max": tv_max}).status_code == 200
    kid = as_user(household, KID)
    assert (kid.get(f"/api/titles/{MOVIE}").status_code == 200) == sees_movie
    assert (kid.get(f"/api/titles/{SERIES}").status_code == 200) == sees_series
    assert (kid.get(f"/api/titles/{S1E1}").status_code == 200) == sees_series  # episodes inherit the series' scale
    with db_module.SessionLocal() as session:  # the row-subquery form agrees
        titles = set(session.scalars(select(MediaTitle.id).where(LibraryService.visible_title_predicate(make_user(KID)))))
    assert (MOVIE in titles, SERIES in titles) == (sees_movie, sees_series)


def test_a_restricted_members_art_tags_are_bound_to_them_and_die_with_their_access(household: TestClient) -> None:
    item = f"/Items/{jellyfin_id(SERIES)}"
    tag = jf(household, "GET", item, KID).json()["ImageTags"]["Primary"]
    assert tag.endswith(KID) and len(tag) == 32 + 8 + len(KID)
    shared = jf(household, "GET", item, ADULT).json()["ImageTags"]["Primary"]
    assert len(shared) == 32 and shared == jf(household, "GET", item, ADMIN).json()["ImageTags"]["Primary"]  # unchanged, no churn
    app.dependency_overrides.pop(get_current_user, None)
    tokenless = lambda t: household.get(f"{item}/Images/Primary", params={"tag": t}).status_code  # noqa: E731 - as Infuse fetches art
    assert tokenless(tag) == 200 and tokenless(shared) == 200
    cache = lambda t: household.get(f"{item}/Images/Primary", params={"tag": t}).headers["cache-control"]  # noqa: E731
    assert cache(tag) == "private, max-age=300" and cache(shared) == "public, max-age=31536000"  # scoped art never sits at the edge
    assert tokenless(tag[:32]) == 404 and tokenless(tag[:40] + GUEST) == 404  # stripped, or re-pointed at another member
    poster = as_user(household, KID).get(f"/api/titles/{SERIES}").json()["poster"]["rendition"].replace("{w}", "240")
    assert poster.split("/")[3].endswith(KID)
    assert as_user(household, ADULT).get(f"/api/titles/{SERIES}").json()["poster"]["rendition"].split("/")[3].__len__() == 22
    assert household.get(poster).status_code != 404
    forged = poster.replace(KID, GUEST)
    assert household.get(forged).status_code == 404
    admin = as_user(household, ADMIN)  # shows hidden from the kid: their old tag and signature stop working at once
    assert admin.put(f"/api/admin/members/{KID}/access", json={"sections": ["movies"]}).status_code == 200
    app.dependency_overrides.pop(get_current_user, None)
    assert tokenless(tag) == 404 and household.get(poster).status_code == 404
    assert tokenless(shared) == 200  # the shared tag is untouched
