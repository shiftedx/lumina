"""Jellyfin Anime view, explicit music browsing and member/artwork boundaries.

Seeds conftest's per-test database with title_support's tree plus seed_gallery: "Show A" (an anime series, Season 1,
"Arrival"), "Film" (an anime movie) and the album "Album One" by "Artist A" with three tracks, all alice's and shared.
Requests use alice's device token unless a test says otherwise.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import LibraryItem, MediaTitle
from app.services.jellyfin import JellyfinMapper
from app.services.media_titles import jellyfin_id, synthetic_id
from test_query_plans import _seed_gallery as seed_plan_titles, explain_plan, plan_env  # noqa: F401 - plan_env is a fixture
from title_support import (
    ALICE, ALICE_TOKEN, ANIME_EPISODE, ANIME_FILE, ANIME_MOVIE, ANIME_SEASON, ANIME_SERIES, BOB, BOB_TOKEN, BOXSET, MOVIE, SERIES,
    jellyfin_household, mediabrowser, seed_gallery,
)

HEX = jellyfin_id
MOVIES, SHOWS, ANIME = (HEX(synthetic_id(f"view:{view}")) for view in ("movies", "tvshows", "anime"))
# Infuse keys each library by these ids: a change orphans every Apple TV's library. Literal on purpose.
USER_VIEWS = [
    ("1e68a4f62128502f8df7426402c8f545", "Movies", "movies"),
    ("e0a24981e2075c958276d82ab3c8e32c", "Shows", "tvshows"),
    ("555151516f1a5c83bb741fb88ad5cce6", "Anime", "tvshows"),
    ("b8e6e5f3a40f598dbdc1c27bf7a21d37", "Collections", "boxsets"),
    ("5fe4102fd93e5680bd01138b092b10d7", "Channels", "tvshows"),
]
VIEW_KEYS = {"Id", "ServerId", "Name", "SortName", "Type", "CollectionType", "IsFolder", "MediaType", "ImageTags",
             "BackdropImageTags", "LocationType", "PlayAccess", "CanDelete", "CanDownload", "UserData"}


@pytest.fixture
def jf(tmp_path: Path):  # noqa: ANN201
    root = tmp_path.resolve() / "media"
    jellyfin_household(root)
    with db_module.SessionLocal() as session:
        seed_gallery(session, root)
        session.commit()
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def get(client: TestClient, path: str, token: str = ALICE_TOKEN, **params):  # noqa: ANN201
    return client.get(path, params=params, headers=mediabrowser(token))


def names(response) -> list[str]:  # noqa: ANN001
    assert response.status_code == 200, response.text
    return [item["Name"] for item in response.json()["Items"]]


def recategorise(category: str, *title_ids: str) -> None:
    """What T1's re-sort does to a show: its series, seasons and episodes change category together."""
    with db_module.SessionLocal() as session:
        for title_id in title_ids:
            session.get(MediaTitle, title_id).category = category
        session.commit()


def test_user_views_snapshot_for_infuse(jf: TestClient) -> None:
    views = get(jf, "/UserViews").json()
    assert [(view["Id"], view["Name"], view["CollectionType"]) for view in views["Items"]] == USER_VIEWS
    assert views["TotalRecordCount"] == len(USER_VIEWS)
    for view in views["Items"]:
        assert set(view) - {"ChildCount", "RecursiveItemCount"} == VIEW_KEYS, view
        assert (view["Type"], view["IsFolder"], view["SortName"]) == ("CollectionFolder", True, view["Name"])
    assert get(jf, f"/Users/{HEX(ALICE)}/Views").json() == views
    assert get(jf, "/Items").json() == views  # the root listing Infuse falls back to
    assert get(jf, f"/Items/{ANIME}").json() == views["Items"][2]


def test_anime_view_is_listed_only_for_a_member_who_can_see_an_anime_series(jf: TestClient) -> None:
    assert "Anime" in names(get(jf, "/UserViews", token=BOB_TOKEN))  # alice's anime files are shared
    with db_module.SessionLocal() as session:
        session.get(LibraryItem, ANIME_FILE).visibility = "private"
        session.commit()
    assert "Anime" in names(get(jf, "/UserViews"))
    assert "Anime" not in names(get(jf, "/UserViews", token=BOB_TOKEN))
    recategorise("shows", ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE)
    assert names(get(jf, "/UserViews")) == ["Movies", "Shows", "Collections", "Channels"]  # an anime movie alone makes no view


def test_parent_ids_follow_type_and_category(jf: TestClient) -> None:
    parents = {
        title_id: get(jf, f"/Items/{HEX(title_id)}").json()["ParentId"]
        for title_id in (ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE, ANIME_MOVIE, SERIES, MOVIE, BOXSET)
    }
    assert parents == {
        ANIME_SERIES: ANIME, ANIME_SEASON: HEX(ANIME_SERIES), ANIME_EPISODE: HEX(ANIME_SEASON),
        ANIME_MOVIE: MOVIES, SERIES: SHOWS, MOVIE: MOVIES, BOXSET: HEX(synthetic_id("view:boxsets")),
    }


def test_anime_movies_stay_in_movies(jf: TestClient) -> None:
    """The Anime view is a tvshows collection, as in the household's old Jellyfin."""
    assert names(get(jf, "/Items", ParentId=MOVIES, IncludeItemTypes="Movie", Recursive="true")) == ["Film", "Movie"]
    under_anime = get(jf, "/Items", ParentId=ANIME, IncludeItemTypes="Movie", Recursive="true").json()
    assert (under_anime["Items"], under_anime["TotalRecordCount"]) == ([], 0)
    everything = get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie,Series").json()["Items"]
    assert [(item["Name"], item["ParentId"]) for item in everything] == [
        ("Film", MOVIES), ("Movie", MOVIES), ("Show", SHOWS), ("Show A", ANIME),
    ]


def test_native_category_walls_match_the_web_without_changing_jellyfin_views(jf: TestClient) -> None:
    anime = get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie,Series", LuminaCategory="anime").json()
    movies = get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie", LuminaCategory="movies").json()
    assert [item["Name"] for item in anime["Items"]] == ["Film", "Show A"]
    assert [item["Name"] for item in movies["Items"]] == ["Movie"]
    assert get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie", LuminaCategory="invalid").json()["Items"] == []


def test_the_anime_view_check_seeks_the_category_index(plan_env) -> None:  # noqa: ANN001
    """/UserViews runs on every Infuse launch: finding one visible anime series must not walk every series."""
    engine, session, user = plan_env
    seed_plan_titles(session, user)
    session.get(MediaTitle, "gs3").category = "anime"
    session.commit()
    mapper = JellyfinMapper(session, user)
    listed: list[str] = []
    plan = explain_plan(engine, session, lambda: listed.extend(view["Name"] for view in mapper.views()))
    assert "Anime" in listed
    assert re.search(r"SEARCH media_titles USING INDEX ix_media_titles_category_(sort|added) \(category=\?\)", plan), plan


def test_shows_and_anime_split_series_seasons_and_episodes_by_category(jf: TestClient) -> None:
    crawl = {"IncludeItemTypes": "Series", "Recursive": "true", "Fields": "ProviderIds,Overview"}
    shows, anime = (get(jf, "/Items", ParentId=view, **crawl).json() for view in (SHOWS, ANIME))
    assert ([s["Name"] for s in shows["Items"]], shows["TotalRecordCount"]) == (["Show"], 1)
    assert ([s["Name"] for s in anime["Items"]], anime["TotalRecordCount"]) == (["Show A"], 1)
    assert names(get(jf, "/Items", ParentId=ANIME)) == ["Show A"]  # a view's default type is its series
    shows_seasons = get(jf, "/Items", ParentId=SHOWS, IncludeItemTypes="Season", Recursive="true").json()
    assert shows_seasons["TotalRecordCount"] == 3 and HEX(ANIME_SEASON) not in {s["Id"] for s in shows_seasons["Items"]}
    assert [s["Id"] for s in get(jf, "/Items", ParentId=ANIME, IncludeItemTypes="Season", Recursive="true").json()["Items"]] == [HEX(ANIME_SEASON)]
    shows_episodes = names(get(jf, "/Items", ParentId=SHOWS, IncludeItemTypes="Episode", Recursive="true"))
    assert sorted(shows_episodes) == ["Making Of", "Pilot", "Return", "Second"]
    assert names(get(jf, "/Items", ParentId=ANIME, IncludeItemTypes="Episode", Recursive="true")) == ["Arrival"]
    everything = "Movie,Series,Season,Episode,BoxSet"
    shows_all = get(jf, "/Items", ParentId=SHOWS, IncludeItemTypes=everything, Recursive="true").json()["Items"]
    assert not {HEX(ANIME_SERIES), HEX(ANIME_SEASON), HEX(ANIME_EPISODE)} & {item["Id"] for item in shows_all}
    anime_all = get(jf, "/Items", ParentId=ANIME, IncludeItemTypes=everything, Recursive="true").json()["Items"]
    assert sorted(item["Id"] for item in anime_all) == sorted(HEX(t) for t in (ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE))


def test_types_jellyfin_never_serves_match_nothing(jf: TestClient) -> None:
    """Books and unsupported media types stay excluded while music requires an explicit query."""
    for types in ("Book", "AudioBook", "MusicVideo", "Book,AudioBook", "MusicAlbum,Playlist"):
        page = get(jf, "/Items", Recursive="true", IncludeItemTypes=types).json()
        assert (page["Items"], page["TotalRecordCount"]) == ([], 0), types
    assert names(get(jf, "/Items", ParentId=SHOWS, IncludeItemTypes="MusicAlbum", Recursive="true")) == []
    assert names(get(jf, "/Items", Recursive="true", IncludeItemTypes="MusicAlbum,Movie")) == ["Film", "Movie"]
    assert names(get(jf, "/Items", Recursive="true", IncludeItemTypes="MusicAlbum")) == ["Album One"]
    assert names(get(jf, "/Items", Recursive="true", IncludeItemTypes="MusicArtist")) == ["Artist A"]


from test_jellyfin_api import watch  # noqa: E402
from title_support import ANIME_MOVIE_FILE, FILE, S1E2  # noqa: E402


def test_latest_splits_shows_and_anime_and_spans_both_without_a_parent(jf: TestClient) -> None:
    """Series group by their newest episode: Show A's arrived T0+3d, Show's T0+3h; Film T0+4d, Movie T0+2d."""
    assert [i["Name"] for i in get(jf, "/Items/Latest", ParentId=ANIME).json()] == ["Show A"]
    assert [i["Name"] for i in get(jf, "/Items/Latest", ParentId=SHOWS).json()] == ["Show"]
    assert [i["Name"] for i in get(jf, "/Items/Latest", ParentId=MOVIES).json()] == ["Film", "Movie"]
    assert [i["Name"] for i in get(jf, "/Items/Latest").json()] == ["Film", "Show A", "Movie", "Show"]
    assert get(jf, "/Items/Latest", ParentId=ANIME, IncludeItemTypes="Movie").json() == []


def test_resume_under_a_view_follows_the_category(jf: TestClient) -> None:
    watch(ALICE, FILE[S1E2], hours=1, position=300)
    watch(ALICE, ANIME_FILE, hours=2, position=300)
    watch(ALICE, ANIME_MOVIE_FILE, hours=3, position=300)
    assert names(get(jf, "/UserItems/Resume")) == ["Film", "Arrival", "Second"]
    assert names(get(jf, "/UserItems/Resume", ParentId=ANIME)) == ["Arrival"]
    assert names(get(jf, "/UserItems/Resume", ParentId=SHOWS)) == ["Second"]
    assert names(get(jf, "/UserItems/Resume", ParentId=MOVIES)) == ["Film"]


from title_support import ALBUM, ARTIST, TRACKS  # noqa: E402


def test_music_ids_are_browseable_only_when_visible_and_folders_are_not_playable(jf: TestClient) -> None:
    me, headers = HEX(ALICE), mediabrowser(ALICE_TOKEN)
    for title_id, kind in ((ALBUM, "MusicAlbum"), (ARTIST, "MusicArtist")):
        hex_id = HEX(title_id)
        for path in (f"/Items/{hex_id}", f"/Users/{me}/Items/{hex_id}"):
            assert get(jf, path).json()["Type"] == kind
        assert get(jf, f"/Shows/{hex_id}/Seasons").json()["Items"] == []
        assert get(jf, "/Items/Latest", ParentId=hex_id).json() == []
        assert get(jf, "/UserItems/Resume", ParentId=hex_id).json()["Items"] == []
        assert jf.post(f"/Items/{hex_id}/PlaybackInfo", json={}, headers=headers).status_code == 404
    with db_module.SessionLocal() as session:
        for track in TRACKS:
            session.get(LibraryItem, track).visibility = "private"
        session.commit()
    for title_id in (ALBUM, ARTIST):
        hex_id = HEX(title_id)
        for path in (f"/Items/{hex_id}", f"/Users/{HEX(BOB)}/Items/{hex_id}", f"/Items/{hex_id}/Images/Primary"):
            assert get(jf, path, token=BOB_TOKEN).status_code == 404
        for kind in ("PlayedItems", "FavoriteItems"):
            assert jf.post(f"/Users/{HEX(BOB)}/{kind}/{hex_id}", headers=mediabrowser(BOB_TOKEN)).status_code == 404
    assert names(get(jf, "/Items", IncludeItemTypes="MusicAlbum", token=BOB_TOKEN)) == []
    assert names(get(jf, "/Items", IncludeItemTypes="MusicArtist", token=BOB_TOKEN)) == []
    assert names(get(jf, "/Items", IncludeItemTypes="Audio", ParentId=HEX(ALBUM), token=BOB_TOKEN)) == []
    assert get(jf, f"/Audio/{HEX(TRACKS[0])}/stream", token=BOB_TOKEN).status_code == 404


def test_signed_album_art_requires_the_issued_tag_and_a_live_file(jf: TestClient) -> None:
    """Album covers use the same item-scoped signed capability as video artwork."""
    tag = get(jf, f"/Items/{HEX(ALBUM)}").json()["ImageTags"]["Primary"]
    assert jf.get(f"/Items/{HEX(ALBUM)}/Images/Primary", params={"tag": tag}).status_code == 200
    assert jf.get(f"/Items/{HEX(ALBUM)}/Images/Primary", params={"tag": "invalid"}).status_code == 404
    assert jf.get(f"/Items/{HEX(SERIES)}/Images/Primary", params={"tag": tag}).status_code == 404
    show_tag = get(jf, f"/Items/{HEX(SERIES)}").json()["ImageTags"]["Primary"]
    assert jf.get(f"/Items/{HEX(SERIES)}/Images/Primary", params={"tag": show_tag}).status_code == 200
    with db_module.SessionLocal() as session:
        for track in TRACKS:
            session.get(LibraryItem, track).status = "missing"
        session.commit()
    assert jf.get(f"/Items/{HEX(ALBUM)}/Images/Primary", params={"tag": tag}).status_code == 404


def test_video_listings_stay_separate_from_explicit_music_items(jf: TestClient) -> None:
    watch(ALICE, TRACKS[0], hours=1, position=60)
    music = {HEX(ALBUM), HEX(ARTIST)}
    listings = [
        get(jf, "/UserViews").json()["Items"],
        get(jf, "/Items", Recursive="true").json()["Items"],
        get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie,Series,Season,Episode,BoxSet").json()["Items"],
        get(jf, "/Items/Latest").json(),
        get(jf, "/UserItems/Resume").json()["Items"],
        get(jf, "/Shows/NextUp").json()["Items"],
    ]
    for dtos in listings:
        for dto in dtos:
            assert not music & {dto.get("Id"), dto.get("ParentId"), dto.get("SeriesId"), dto.get("SeasonId")}, dto
    for track in TRACKS:
        dto = get(jf, f"/Items/{HEX(track)}").json()
        assert (dto["Type"], dto["ParentId"], dto["AlbumId"]) == ("Audio", HEX(ALBUM), HEX(ALBUM))
    assert names(get(jf, "/Items", IncludeItemTypes="MusicAlbum")) == ["Album One"]


from test_jellyfin_api import TICKS, lumina_progress, no_ffprobe  # noqa: E402,F401 - no_ffprobe is a fixture


def test_infuse_style_session_replay_through_anime(jf: TestClient, no_ffprobe: None) -> None:
    """Library sync of Shows and Anime -> seasons -> episodes -> PlaybackInfo -> progress -> Resume and Latest."""
    headers, me = mediabrowser(ALICE_TOKEN), HEX(ALICE)

    def call(method: str, path: str, **kwargs):  # noqa: ANN003, ANN202
        response = jf.request(method, path, headers=headers, **kwargs)
        assert response.status_code < 400, (method, path, response.status_code, response.text)
        return response

    views = {view["Name"]: view for view in call("GET", "/UserViews", params={"userId": me, "includeExternalContent": "false"}).json()["Items"]}
    assert (views["Anime"]["Id"], views["Anime"]["CollectionType"]) == (ANIME, "tvshows")
    synced: dict[str, list[dict]] = {}
    for name in ("Shows", "Anime"):
        series, start = [], 0
        while True:  # Infuse pages its library sync
            page = call("GET", f"/Users/{me}/Items", params={
                "ParentId": views[name]["Id"], "IncludeItemTypes": "Series", "Recursive": "true", "StartIndex": start, "Limit": 1,
                "Fields": "ProviderIds,Overview,DateCreated,Genres", "EnableUserData": "true", "EnableImageTypes": "Primary,Backdrop",
            }).json()
            series += page["Items"]
            start += 1
            if start >= page["TotalRecordCount"]:
                break
        synced[name] = series
    assert {name: [s["Name"] for s in series] for name, series in synced.items()} == {"Shows": ["Show"], "Anime": ["Show A"]}
    show = synced["Anime"][0]["Id"]
    [season] = call("GET", f"/Shows/{show}/Seasons", params={"userId": me, "Fields": "ItemCounts"}).json()["Items"]
    [episode] = call("GET", f"/Shows/{show}/Episodes", params={"seasonId": season["Id"], "userId": me, "Fields": "MediaSources"}).json()["Items"]
    source = episode["MediaSources"][0]["Id"]
    assert (episode["Name"], episode["SeriesId"], source) == ("Arrival", show, episode["Id"])
    info = call("POST", f"/Items/{episode['Id']}/PlaybackInfo", params={"UserId": me, "MediaSourceId": source, "IsPlayback": "true"},
                json={"DeviceProfile": {"MaxStreamingBitrate": 120_000_000}}).json()
    report = {"ItemId": episode["Id"], "MediaSourceId": source, "PlaySessionId": info["PlaySessionId"], "CanSeek": True}
    call("POST", "/Sessions/Playing", json={**report, "PositionTicks": 0})
    call("POST", "/Sessions/Playing/Progress", json={**report, "PositionTicks": 600 * TICKS, "EventName": "timeupdate"})
    assert names(call("GET", "/UserItems/Resume", params={"userId": me, "ParentId": ANIME})) == ["Arrival"]
    assert names(call("GET", "/UserItems/Resume", params={"userId": me, "ParentId": SHOWS})) == []
    assert lumina_progress(jf, ANIME_FILE)["position_seconds"] == 600  # the same row Lumina's UI resumes from
    assert [i["Name"] for i in call("GET", f"/Users/{me}/Items/Latest", params={"ParentId": ANIME}).json()] == ["Show A"]


def test_watched_state_and_ids_survive_a_move_into_anime(jf: TestClient) -> None:
    """Upgrade day: under 1.5.1 Show A sat in Shows; the re-sort moves it to Anime. Infuse keys state by id."""
    me, headers = HEX(ALICE), mediabrowser(ALICE_TOKEN)
    recategorise("shows", ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE)
    before = get(jf, "/Items", ParentId=SHOWS, IncludeItemTypes="Series", Recursive="true").json()["Items"]
    show_a = next(series for series in before if series["Name"] == "Show A")
    assert show_a["ParentId"] == SHOWS
    assert "Anime" not in names(get(jf, "/UserViews"))
    assert jf.post(f"/Users/{me}/PlayedItems/{HEX(ANIME_EPISODE)}", headers=headers).json()["Played"] is True
    assert jf.post(f"/Users/{me}/FavoriteItems/{show_a['Id']}", headers=headers).json()["IsFavorite"] is True
    recategorise("anime", ANIME_SERIES, ANIME_SEASON, ANIME_EPISODE)
    assert "Show A" not in names(get(jf, "/Items", ParentId=SHOWS, IncludeItemTypes="Series", Recursive="true"))
    [after] = get(jf, "/Items", ParentId=ANIME, IncludeItemTypes="Series", Recursive="true").json()["Items"]
    assert (after["Id"], after["ParentId"], after["UserData"]["IsFavorite"]) == (show_a["Id"], ANIME, True)
    [episode] = get(jf, "/Items", ParentId=ANIME, IncludeItemTypes="Episode", Recursive="true").json()["Items"]
    assert (episode["Id"], episode["UserData"]["Played"]) == (HEX(ANIME_EPISODE), True)
    assert lumina_progress(jf, ANIME_FILE)["completed"] is True  # the same row Lumina's UI reads
