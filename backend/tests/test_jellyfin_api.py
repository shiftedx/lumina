"""Jellyfin-compatible API: wire format, visibility, playback state, security.

Seeds conftest's per-test file-backed database, so real ``jellyfin_user`` (or the
interim one) authenticates real device-token rows; nothing here overrides a dependency.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routers import jellyfin as jellyfin_router
from app.routers.jellyfin import SPA_SEGMENTS, JellyfinPathMiddleware, lower_query_keys, masked_path, normalize_jellyfin_path
from app.services.media_titles import jellyfin_id
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, jellyfin_household, mediabrowser


@pytest.fixture
def jf(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def get(client: TestClient, path: str, token: str | None = ALICE_TOKEN, **params):  # noqa: ANN201
    return client.get(path, params=params, headers=mediabrowser(token) if token else {})


@pytest.mark.parametrize(("raw", "expected"), [
    ("/Items/ABC/", "/jellyfin/items/abc"),
    ("//SYSTEM///Info/Public/", "/jellyfin/system/info/public"),
    ("/System", "/jellyfin/system"),
    ("/jellyfin/items", None),  # no aliases: the API lives at the root only
    ("/emby/Items", None),
    ("/Genres", None),  # not served, and no credential: see the middleware tests
    ("/itemsx/abc", None),
    ("/api/items", None),
    ("/assets/app.js", None),
    ("/", None),
])
def test_root_form_maps_only_router_segments(raw: str, expected: str | None) -> None:
    assert normalize_jellyfin_path(raw, frozenset({"items", "system"})) == expected


def test_a_credentialed_call_maps_any_segment_but_api_and_root() -> None:
    assert normalize_jellyfin_path("/Genres/X", frozenset(), any_segment=True) == "/jellyfin/genres/x"
    assert normalize_jellyfin_path("/api/library", frozenset(), any_segment=True) is None
    assert normalize_jellyfin_path("/", frozenset(), any_segment=True) is None


def test_root_segments_come_from_the_registered_routes() -> None:
    segments = next(entry.kwargs["root_segments"] for entry in app.user_middleware if entry.cls is JellyfinPathMiddleware)
    assert {"items", "users", "system", "videos", "shows", "playlists", "quickconnect", "branding"} <= segments
    assert not segments & {"api", "assets", "jellyfin", "emby", "", "index.html"}
    assert not [segment for segment in segments if segment.startswith("{")]


def test_spa_segments_match_the_frontend_routes() -> None:
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "app" / "routes.ts").read_text()
    parse = source[source.index("export function parseRoute"):source.index("export function routePath")]
    plain = re.search(r"PLAIN_SURFACES = \[([^\]]*)\]", source).group(1)
    found = set(re.findall(r"'/([a-z]+)'|\^\\/([a-z]+)", parse)) | {(name, "") for name in re.findall(r"'([a-z]+)'", plain)}
    assert {name for pair in found for name in pair if name} == SPA_SEGMENTS


def spa_with_jellyfin(tmp_path: Path) -> TestClient:
    """A SPA mount plus Jellyfin routes, one of which ("library") collides with a SPA top-level path."""
    from fastapi import FastAPI

    from app.main import SpaStaticFiles
    (tmp_path / "index.html").write_text("<div id=root></div>")
    mini = FastAPI()
    mini.get("/jellyfin/{rest:path}")(lambda rest: {"jellyfin": rest})
    mini.mount("/", SpaStaticFiles(directory=tmp_path, html=True), name="frontend")
    mini.add_middleware(JellyfinPathMiddleware, root_segments=frozenset({"library", "items"}))
    return TestClient(mini)


BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def test_spa_collisions_go_to_jellyfin_only_for_api_clients(tmp_path: Path) -> None:
    client = spa_with_jellyfin(tmp_path)
    shell = "<div id=root></div>"
    assert client.get("/library/movies", headers={**BROWSER, "Sec-Fetch-Mode": "navigate"}).text == shell
    assert client.get("/library/movies", headers=BROWSER).text == shell
    assert client.get("/library/movies", headers={"Sec-Fetch-Mode": "navigate", **mediabrowser("t")}).text == shell
    for headers in (mediabrowser("t"), {**BROWSER, **mediabrowser("t")}, {**BROWSER, "X-Emby-Token": "t"},
                    {**BROWSER, "X-MediaBrowser-Token": "t"}, {**BROWSER, "X-Emby-Authorization": mediabrowser("t")["Authorization"]},
                    {"Accept": "application/json"}, {}):
        assert client.get("/Library/VirtualFolders", headers=headers).json() == {"jellyfin": "library/virtualfolders"}, headers
    assert client.get("/library/x", params={"ApiKey": "t"}, headers=BROWSER).json() == {"jellyfin": "library/x"}
    assert client.get("/library/x", params={"api_key": "t"}, headers=BROWSER).json() == {"jellyfin": "library/x"}
    # Only colliding segments look at the request: a navigation to a Jellyfin-only segment is Jellyfin.
    assert client.get("/Items/x", headers={**BROWSER, "Sec-Fetch-Mode": "navigate"}).json() == {"jellyfin": "items/x"}
    assert client.get("/", headers=BROWSER).text == shell
    # A Jellyfin client calling an endpoint we do not serve gets JSON; a page load of a SPA path stays the SPA's.
    assert client.get("/Genres", headers=mediabrowser("t")).json() == {"jellyfin": "genres"}
    assert client.get("/Genres", headers=BROWSER).text == shell
    assert client.get("/settings", headers={"Sec-Fetch-Mode": "navigate", **mediabrowser("t")}).text == shell
    assert client.get("/watch/library/abc", headers=BROWSER).text == shell


def test_query_keys_are_lowercased_and_values_kept() -> None:
    assert lower_query_keys(b"ParentId=AbC&api_key=Sec%20ret&Fields=A,B") == b"parentid=AbC&api_key=Sec+ret&fields=A%2CB"
    assert lower_query_keys(b"") == b""


def test_unhandled_log_path_keeps_only_two_non_id_segments() -> None:
    assert masked_path("/jellyfin/items/0123456789abcdef0123456789abcdef/some-title/extra") == "/jellyfin/items/{id}/{id}/{id}"
    assert masked_path("/jellyfin/videos/42.mkv") == "/jellyfin/videos/{id}"
    assert masked_path("/jellyfin") == "/jellyfin"


def test_disabled_api_is_404_everywhere(tmp_path: Path) -> None:
    jellyfin_household(tmp_path.resolve() / "media", enabled=False)
    client = TestClient(app, base_url="http://localhost")
    for path in ("/Plugins", "/Plugins/nope", "/Branding/Configuration", "/System/Info/Public", "/users/public", "/Genres"):
        response = get(client, path)
        assert response.status_code == 404 and response.content == b""


def test_unknown_route_is_a_logged_empty_404(jf: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="lumina.jellyfin")
    response = get(jf, "/Some/Probe/0123456789ABCDEF0123456789ABCDEF")
    assert (response.status_code, response.content) == (404, b"")
    assert "jellyfin.unhandled GET /jellyfin/some/probe/{id}" in caplog.text
    # Endpoints we do not serve answer an empty 404 to a Jellyfin client, never the SPA shell with 200 (clients decode it).
    for path in ("/Environment/Drives", "/Genres/Action", "/Audio/0123456789ABCDEF0123456789ABCDEF/stream"):
        response = get(jf, path)
        assert (response.status_code, response.content) == (404, b""), path
    assert jf.get("/Environment/Drives", params={"api_key": ALICE_TOKEN}).content == b""
    assert jf.post("/Environment/Validate", headers={"X-Emby-Token": ALICE_TOKEN}).content == b""


def test_stubs_answer_client_probes(jf: TestClient) -> None:
    assert get(jf, "/Branding/Configuration", token=None).json()["LoginDisclaimer"] == ""
    assert get(jf, "/Plugins").json() == []
    assert get(jf, "/Plugins", token=None).status_code == 401
    assert len(get(jf, "/Playback/BitrateTest", Size=20_000_000).content) == 10_000_000
    assert get(jf, "/DisplayPreferences/usersettings", Client="infuse").json()["Client"] == "infuse"
    assert jf.post("/Sessions/Capabilities/Full", headers=mediabrowser(ALICE_TOKEN), json={}).status_code == 204
    assert get(jf, "/Items/Suggestions").json() == {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}


def test_legacy_uid_must_be_the_caller(jf: TestClient) -> None:
    assert get(jf, f"/Users/{jellyfin_id(ALICE)}/Suggestions").status_code == 200
    assert get(jf, f"/Users/{jellyfin_id(BOB)}/Suggestions").status_code == 404
    assert get(jf, f"/Users/{jellyfin_id(BOB)}/Suggestions", token=BOB_TOKEN).status_code == 200


def test_trace_logs_names_never_values_or_tokens(jf: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jellyfin_trace", True)
    caplog.set_level(logging.INFO, logger="lumina.jellyfin")
    jf.get("/Plugins", params={"api_key": ALICE_TOKEN, "SearchTerm": "secret-title"})
    get(jf, "/Plugins")
    get(jf, "/Unknown/Thing", ApiKey=ALICE_TOKEN)
    assert "jellyfin.trace GET /jellyfin/plugins api_key,searchterm" in caplog.text
    caplog.clear()
    jf.get("/Plugins", params={"ApiKey": ALICE_TOKEN, "SearchTerm": "secret-title"})
    assert "jellyfin.trace GET /jellyfin/plugins apikey,searchterm" in caplog.text
    assert ALICE_TOKEN not in caplog.text and "secret-title" not in caplog.text


def test_trace_reaches_server_logs_without_caplog() -> None:
    # caplog lowers levels itself; production only has the "lumina" logger's INFO handler.
    assert logging.getLogger("lumina.jellyfin").isEnabledFor(logging.INFO)


import json  # noqa: E402

from app import db as db_module  # noqa: E402
from app.models import MemberFavorite, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import jellyfin as jf_service  # noqa: E402
from app.services.media_titles import synthetic_id  # noqa: E402
from title_support import (  # noqa: E402
    BOXSET, FILE, MOVIE, MOVIE_1080, MOVIE_4K, S1E1, SECRET_EPISODE, SECRET_SERIES, SERIES, SIDECAR,
)

PROPS = json.loads((Path(__file__).parent / "fixtures" / "jellyfin_oas_props.json").read_text())
# Non-null fields the Kotlin SDK refuses to deserialize without: one missing key fails the whole response in the app.
REQUIRED = json.loads((Path(__file__).parent / "fixtures" / "jellyfin_sdk_required.json").read_text())
TV, MOVIES = jellyfin_id(synthetic_id("view:tvshows")), jellyfin_id(synthetic_id("view:movies"))
HEX = jellyfin_id


def assert_known(payload: dict, schema: str) -> None:
    unknown = set(payload) - set(PROPS[schema]) if schema in PROPS else set()  # UserPolicy etc.: SDK-required only
    assert not unknown, f"{schema} emits non-Jellyfin keys {sorted(unknown)}"
    if schema in REQUIRED:
        missing = {key for key in REQUIRED[schema] if payload.get(key) is None}
        assert not missing, f"{schema} lacks SDK-required keys {sorted(missing)}"


def assert_item(dto: dict) -> None:
    assert_known(dto, "BaseItemDto")
    assert_known(dto["UserData"], "UserItemDataDto")
    for studio in dto.get("Studios", []):
        assert_known(studio, "NameGuidPair")
    for source in dto.get("MediaSources", []):
        assert_known(source, "MediaSourceInfo")
        for stream in source["MediaStreams"]:
            assert_known(stream, "MediaStream")


def names(response) -> list[str]:  # noqa: ANN001
    assert response.status_code == 200, response.text
    return [item["Name"] for item in response.json()["Items"]]


def test_views_list_the_libraries_a_member_has(jf: TestClient) -> None:
    views = get(jf, "/UserViews").json()
    assert {"Movies", "Shows", "Collections"} <= {v["Name"] for v in views["Items"]}
    assert {v["CollectionType"] for v in views["Items"]} >= {"movies", "tvshows", "boxsets"}
    assert get(jf, f"/Users/{HEX(ALICE)}/Views").json() == views


def test_series_crawl_pages_and_hides_private(jf: TestClient) -> None:
    path = f"/Users/{HEX(ALICE)}/Items"
    crawl = get(jf, path, ParentId=TV, IncludeItemTypes="Series", Recursive="true", StartIndex=0, Limit=10).json()
    assert [i["Name"] for i in crawl["Items"]] == ["Show"] and crawl["TotalRecordCount"] == 1
    bob = get(jf, f"/Users/{HEX(BOB)}/Items", token=BOB_TOKEN, ParentId=TV, IncludeItemTypes="Series", Recursive="true").json()
    assert [i["Name"] for i in bob["Items"]] == ["Secret Show", "Show"]
    show = crawl["Items"][0]
    assert (show["Type"], show["IsFolder"], show["ChildCount"], show["RecursiveItemCount"]) == ("Series", True, 3, 4)
    assert show["UserData"]["UnplayedItemCount"] == 4 and show["ImageTags"]["Primary"]
    assert show["ProviderIds"] == {"Tmdb": "100"} and show["ParentId"] == TV


def test_series_children_and_recursive_episodes(jf: TestClient) -> None:
    assert names(get(jf, "/Items", ParentId=HEX(SERIES))) == ["Specials", "Season 1", "Season 2"]
    episodes = get(jf, "/Items", ParentId=HEX(SERIES), IncludeItemTypes="Episode", Recursive="true").json()["Items"]
    assert [e["Name"] for e in episodes] == ["Making Of", "Pilot", "Second", "Return"]
    assert [e["ParentIndexNumber"] for e in episodes] == [0, 1, 1, 2]
    assert {e["SeriesName"] for e in episodes} == {"Show"} and episodes[1]["SeriesId"] == HEX(SERIES)
    page = get(jf, "/Items", ParentId=HEX(SERIES), IncludeItemTypes="Episode", Recursive="true", StartIndex=2, Limit=2).json()
    assert [e["Name"] for e in page["Items"]] == ["Second", "Return"] and (page["TotalRecordCount"], page["StartIndex"]) == (4, 2)


def test_single_item_projects_versions_and_streams_without_paths(jf: TestClient, tmp_path: Path) -> None:
    listed = get(jf, "/Items", ParentId=MOVIES, IncludeItemTypes="Movie", Recursive="true").json()["Items"][0]
    assert "MediaSources" not in listed
    assert "MediaSources" in get(jf, "/Items", ParentId=MOVIES, Recursive="true", Fields="MediaSources,Overview").json()["Items"][0]
    response = get(jf, f"/Items/{HEX(MOVIE)}")
    movie = response.json()
    assert str(tmp_path.resolve()) not in response.text  # Path is library-relative (Infuse needs it), never the mount path
    assert [s["Name"] for s in movie["MediaSources"]] == ["1080p", "4K"]
    assert [s["Id"] for s in movie["MediaSources"]] == [HEX(MOVIE_1080), HEX(MOVIE_4K)]
    four_k = movie["MediaSources"][1]
    kinds = [(s["Index"], s["Type"], s.get("IsExternal")) for s in four_k["MediaStreams"]]
    assert kinds == [(0, "Video", False), (1, "Audio", False), (2, "Subtitle", False), (3, "Subtitle", True)]
    video, sidecar = four_k["MediaStreams"][0], four_k["MediaStreams"][3]
    assert (video["VideoRange"], video["VideoRangeType"], video["Height"]) == ("HDR", "HDR10", 2160)
    assert (sidecar["Language"], sidecar["IsForced"], sidecar["DeliveryMethod"]) == ("eng", True, "External")
    assert sidecar["DeliveryUrl"] == f"/Videos/{HEX(MOVIE_4K)}/{HEX(MOVIE_4K)}/Subtitles/3/0/Stream.{SIDECAR['format']}"
    assert (four_k["Container"], four_k["DefaultAudioStreamIndex"], four_k["DefaultSubtitleStreamIndex"]) == ("mkv", 1, 3)
    assert movie["Studios"][0]["Name"] == "Acme" and movie["CommunityRating"] == 7.9
    assert movie["PremiereDate"] == "2020-03-01T00:00:00.0000000Z" and movie["ParentId"] == MOVIES
    boxset = get(jf, f"/Items/{HEX(BOXSET)}").json()
    assert (boxset["Type"], boxset["ChildCount"]) == ("BoxSet", 1)


def test_ids_accept_dashed_undashed_any_case_and_aliases(jf: TestClient) -> None:
    canonical = get(jf, f"/Items/{HEX(MOVIE)}").json()
    for path in (f"/Items/{MOVIE}", f"/items/{HEX(MOVIE).upper()}", f"/ITEMS/{HEX(MOVIE)}/",
                 f"http://localhost//Items//{HEX(MOVIE)}", f"/Users/{HEX(ALICE)}/Items/{HEX(MOVIE)}"):
        assert get(jf, path).json() == canonical, path
    for garbage in ("not-an-id", "0" * 31, "..%2F..%2Fetc"):
        assert get(jf, f"/Items/{garbage}").status_code == 404


def test_private_titles_and_files_are_404_by_id_and_as_parent(jf: TestClient) -> None:
    for target in (SECRET_SERIES, SECRET_EPISODE, FILE[SECRET_EPISODE]):
        assert get(jf, f"/Items/{HEX(target)}").status_code == 404
        assert get(jf, f"/Items/{HEX(target)}", token=BOB_TOKEN).status_code == 200
    assert get(jf, "/Items", ParentId=HEX(SECRET_SERIES)).json()["Items"] == []  # hidden looks like missing: an empty list
    assert get(jf, "/Items", Ids=f"{HEX(SECRET_SERIES)},{HEX(MOVIE)}").json()["TotalRecordCount"] == 1


def test_filters_sorts_ids_and_provider_lookup(jf: TestClient) -> None:
    with db_module.SessionLocal() as session:
        session.add(MemberFavorite(user_id=ALICE, target_id=SERIES))
        session.commit()
    assert names(get(jf, "/Items", Recursive="true", Filters="IsFavorite")) == ["Show"]
    assert names(get(jf, "/Items", Recursive="true", IsFavorite="true")) == ["Show"]
    assert names(get(jf, "/Items", Recursive="true", AnyProviderIdEquals="tmdb.603")) == ["Movie"]
    assert names(get(jf, "/Items", Recursive="true", AnyProviderIdEquals="Imdb.nope")) == []
    assert names(get(jf, "/Items", Recursive="true", IncludeItemTypes="Episode", SearchTerm="PIL")) == ["Pilot"]
    assert names(get(jf, "/Items", Recursive="true", SearchTerm="%")) == []  # LIKE wildcards are escaped
    assert names(get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie,Series", SortBy="DateCreated", SortOrder="Descending")) == ["Movie", "Show"]
    assert names(get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie", ExcludeItemTypes="Movie")) == []
    assert names(get(jf, "/Items", Ids=f"{HEX(MOVIE)},garbage,{HEX(SERIES)}")) == ["Movie", "Show"]
    assert names(get(jf, "/Items", SeriesId=HEX(SERIES), IncludeItemTypes="Episode", Recursive="true", Limit=1)) == ["Making Of"]
    assert names(get(jf, "/Items", SeriesId="garbage", IncludeItemTypes="Series", Recursive="true")) == []  # B-F10
    assert names(get(jf, "/Items", ParentId=HEX(S1E1))) == []  # a leaf has no children


def test_paging_edges_keep_the_true_total(jf: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    query = {"ParentId": HEX(SERIES), "IncludeItemTypes": "Episode", "Recursive": "true"}
    count_only = get(jf, "/Items", Limit=0, **query).json()
    assert (count_only["Items"], count_only["TotalRecordCount"]) == ([], 4)
    past_end = get(jf, "/Items", StartIndex=50, **query).json()
    assert (past_end["Items"], past_end["TotalRecordCount"], past_end["StartIndex"]) == ([], 4, 50)
    monkeypatch.setattr(jf_service, "MAX_PAGE", 2)
    assert len(get(jf, "/Items", Limit=100_000, **query).json()["Items"]) == 2
    assert get(jf, "/Items", StartIndex=-1, **query).status_code == 400


def test_emitted_keys_are_jellyfin_property_names(jf: TestClient) -> None:
    listing = get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie,Series,Season,Episode,BoxSet", Fields="MediaSources").json()
    assert_known(listing, "BaseItemDtoQueryResult")
    for dto in [*listing["Items"], *get(jf, "/UserViews").json()["Items"], get(jf, f"/Items/{HEX(MOVIE)}").json()]:
        assert_item(dto)
    # Sign-in DTOs are held to the same guard.
    assert_known(get(jf, "/Users/Me").json(), "UserDto")
    with db_module.SessionLocal() as session:
        session.get(User, ALICE).password_hash = hash_password("Test-only-passphrase-1")
        session.commit()
    signed_in = jf.post("/Users/AuthenticateByName", json={"Username": "alice", "Pw": "Test-only-passphrase-1"},
                        headers={"Authorization": 'MediaBrowser Client="Infuse-Direct", Device="Apple TV", DeviceId="device-9", Version="8.0"'})
    assert signed_in.status_code == 200, signed_in.text
    assert_known(signed_in.json(), "AuthenticationResult")
    assert_known(signed_in.json()["User"], "UserDto")
    assert_known(signed_in.json()["SessionInfo"], "SessionInfoDto")
    for name in ("Policy", "Configuration"):
        assert_known(signed_in.json()["User"][name], f"User{name}")
    assert_known(get(jf, "/DisplayPreferences/usersettings", Client="androidtv").json(), "DisplayPreferencesDto")


from app.models import LibraryItem  # noqa: E402
from title_support import CHANNEL_NEW, CHANNEL_OLD, CHANNEL_PRIVATE  # noqa: E402

CHANNELS = HEX(synthetic_id("view:channels"))
CHAN = HEX(synthetic_id("channel:Youtube:Chan"))
CHAN_2025 = HEX(synthetic_id("channel:Youtube:Chan:2025"))
BOBCHAN = HEX(synthetic_id("channel:Youtube:BobChan"))


def test_channels_view_groups_uploads_by_uploader_and_year(jf: TestClient) -> None:
    assert "Channels" in {v["Name"] for v in get(jf, "/UserViews").json()["Items"]}
    assert names(get(jf, "/Items", ParentId=CHANNELS)) == ["Chan"]
    assert names(get(jf, "/Items", ParentId=CHANNELS, token=BOB_TOKEN)) == ["BobChan", "Chan"]
    chan = get(jf, f"/Items/{CHAN}").json()
    assert (chan["Type"], chan["ChildCount"], chan["RecursiveItemCount"], chan["ParentId"]) == ("Series", 2, 2, CHANNELS)
    assert names(get(jf, "/Items", ParentId=CHAN)) == ["2024", "2025"]
    assert names(get(jf, "/Items", ParentId=CHAN, Recursive="true")) == ["Old upload", "New upload"]
    assert names(get(jf, "/Items", ParentId=CHAN_2025)) == ["New upload"]
    for parent in (CHANNELS, CHAN, CHAN_2025):  # final review #23: a Movie query never gets year folders
        assert get(jf, "/Items", ParentId=parent, IncludeItemTypes="Movie").json()["TotalRecordCount"] == 0
    assert names(get(jf, "/Items", ParentId=CHAN, IncludeItemTypes="Season")) == ["2024", "2025"]
    everything = get(jf, "/Items", ParentId=CHANNELS, Recursive="true", IncludeItemTypes="Episode").json()
    assert [i["Name"] for i in everything["Items"]] == ["Old upload", "New upload"] and everything["TotalRecordCount"] == 2


def test_channel_video_dto_points_at_its_synthetic_folders(jf: TestClient) -> None:
    video = get(jf, f"/Items/{HEX(CHANNEL_NEW)}").json()
    assert (video["Type"], video["SeriesId"], video["SeasonId"], video["ParentIndexNumber"]) == ("Episode", CHAN, CHAN_2025, 2025)
    assert video["SeriesName"] == "Chan" and video["PremiereDate"] == "2025-02-10T00:00:00.0000000Z"
    assert len(video["MediaSources"]) == 1
    season = get(jf, f"/Items/{CHAN_2025}").json()
    assert (season["Type"], season["IndexNumber"], season["SeriesId"]) == ("Season", 2025, CHAN)
    for dto in (video, season, get(jf, f"/Items/{CHAN}").json()):
        assert_item(dto)
    assert names(get(jf, "/Items", Ids=f"{CHAN},{HEX(CHANNEL_OLD)}")) == ["Chan", "Old upload"]


def test_private_channel_is_invisible(jf: TestClient) -> None:
    assert get(jf, f"/Items/{BOBCHAN}").status_code == 404
    assert get(jf, f"/Items/{HEX(CHANNEL_PRIVATE)}").status_code == 404
    assert get(jf, "/Items", ParentId=BOBCHAN).json()["Items"] == []
    assert get(jf, f"/Items/{BOBCHAN}", token=BOB_TOKEN).status_code == 200
    with db_module.SessionLocal() as session:  # a tombstoned upload drops out of its channel
        session.get(LibraryItem, CHANNEL_OLD).status = "missing"
        session.commit()
    assert names(get(jf, "/Items", ParentId=CHAN)) == ["2025"]


import uuid  # noqa: E402
from datetime import timedelta  # noqa: E402

from app.models import PlaybackProgress  # noqa: E402
from title_support import S0E1, S1E2, S2E1, SEASON1, T0  # noqa: E402


def watch(user_id: str, item_id: str, *, hours: int, position: int = 0, completed: bool = False) -> None:
    with db_module.SessionLocal() as session:
        session.add(PlaybackProgress(
            id=str(uuid.uuid4()), user_id=user_id, item_id=item_id, position_seconds=position, duration_seconds=1500,
            completed=completed, last_watched_at=T0 + timedelta(hours=hours),
        ))
        session.commit()


def test_shows_seasons_and_episodes(jf: TestClient) -> None:
    assert names(get(jf, f"/Shows/{HEX(SERIES)}/Seasons")) == ["Specials", "Season 1", "Season 2"]
    path = f"/Shows/{HEX(SERIES)}/Episodes"
    assert names(get(jf, path, SeasonId=HEX(SEASON1))) == ["Pilot", "Second"]
    assert names(get(jf, path, Season=2)) == ["Return"]
    assert names(get(jf, path, StartItemId=HEX(S1E2))) == ["Second", "Return"]
    assert get(jf, path, Limit=1, StartIndex=1).json()["TotalRecordCount"] == 4
    assert get(jf, f"/Shows/{HEX(MOVIE)}/Episodes").json()["Items"] == []
    assert get(jf, f"/Shows/{HEX(SECRET_SERIES)}/Seasons").json()["Items"] == []
    assert names(get(jf, f"/Shows/{CHAN}/Seasons")) == ["2024", "2025"]
    assert names(get(jf, f"/Shows/{CHAN}/Episodes")) == ["Old upload", "New upload"]
    assert names(get(jf, f"/Shows/{CHAN}/Episodes", Season=2025)) == ["New upload"]


def test_next_up_and_resume_follow_progress(jf: TestClient) -> None:
    assert names(get(jf, "/Shows/NextUp")) == [] and names(get(jf, "/UserItems/Resume")) == []
    watch(ALICE, FILE[S1E1], hours=1, completed=True)
    assert names(get(jf, "/Shows/NextUp")) == ["Second"]
    assert names(get(jf, "/Shows/NextUp", SeriesId=HEX(MOVIE))) == []
    watch(ALICE, FILE[S1E2], hours=2, position=300)
    assert names(get(jf, "/Shows/NextUp")) == []  # the in-progress anchor belongs to Resume
    watch(ALICE, CHANNEL_NEW, hours=3, position=120)
    resume = get(jf, f"/Users/{HEX(ALICE)}/Items/Resume").json()["Items"]
    assert [(r["Name"], r["Type"]) for r in resume] == [("New upload", "Episode"), ("Second", "Episode")]
    assert resume[1]["UserData"]["PlaybackPositionTicks"] == 300 * 10_000_000
    assert names(get(jf, "/UserItems/Resume", ParentId=TV)) == ["Second"]
    assert names(get(jf, "/UserItems/Resume", ParentId=CHANNELS)) == ["New upload"]
    assert names(get(jf, "/UserItems/Resume", ParentId=HEX(SERIES))) == ["Second"]
    assert names(get(jf, "/UserItems/Resume", IncludeItemTypes="Movie")) == []
    assert names(get(jf, "/UserItems/Resume", Limit=1, StartIndex=1)) == ["Second"]


def test_tombstoned_episode_leaves_next_up_and_resume(jf: TestClient) -> None:
    watch(ALICE, FILE[S1E1], hours=1, completed=True)
    watch(ALICE, FILE[S1E2], hours=2, completed=True)
    watch(ALICE, FILE[S2E1], hours=0, position=60)
    assert names(get(jf, "/Shows/NextUp")) == ["Return"]
    assert names(get(jf, "/UserItems/Resume")) == ["Return"]
    with db_module.SessionLocal() as session:
        session.get(LibraryItem, FILE[S2E1]).status = "missing"
        session.commit()
    assert names(get(jf, "/Shows/NextUp")) == []  # Season 1 finale is now the last visible episode
    assert names(get(jf, "/UserItems/Resume")) == []
    assert names(get(jf, f"/Shows/{HEX(SERIES)}/Episodes")) == ["Making Of", "Pilot", "Second"]


def test_latest_groups_episodes_to_their_series(jf: TestClient) -> None:
    latest = get(jf, "/Items/Latest").json()
    assert [i["Name"] for i in latest] == ["Movie", "Show"]  # movie added T0+2d, newest episode file T0+3h
    assert [i["Name"] for i in get(jf, "/Items/Latest", ParentId=TV).json()] == ["Show"]
    assert [i["Name"] for i in get(jf, "/Items/Latest", ParentId=MOVIES).json()] == ["Movie"]
    assert [i["Name"] for i in get(jf, "/Items/Latest", ParentId=CHANNELS).json()] == ["New upload", "Old upload"]
    assert get(jf, f"/Users/{HEX(ALICE)}/Items/Latest", Limit=1).json()[0]["Name"] == "Movie"
    for dto in latest:
        assert_item(dto)


def test_recently_added_uses_when_the_files_arrived(jf: TestClient) -> None:
    """Infuse's Recently Added reads Latest, SortBy=DateCreated and DateCreated: all follow MediaTitle.added_at."""
    from datetime import datetime

    from app.models import MediaTitle
    from title_support import S2E1

    with db_module.SessionLocal() as session:
        session.get(MediaTitle, MOVIE).added_at = datetime(2020, 1, 1)
        session.get(MediaTitle, S2E1).added_at = session.get(MediaTitle, SERIES).added_at = datetime(2021, 6, 1)
        session.commit()
    assert [i["Name"] for i in get(jf, "/Items/Latest").json()] == ["Show", "Movie"]
    by_date = get(jf, "/Items", Recursive="true", IncludeItemTypes="Movie,Series", SortBy="DateCreated", SortOrder="Descending", Fields="DateCreated")
    assert [(i["Name"], i["DateCreated"]) for i in by_date.json()["Items"]] == [
        ("Show", "2021-06-01T00:00:00.0000000Z"), ("Movie", "2020-01-01T00:00:00.0000000Z"),
    ]


def test_search_hints(jf: TestClient) -> None:
    hints = get(jf, "/Search/Hints", SearchTerm="pil").json()
    assert_known(hints, "SearchHintResult")
    assert [(h["Name"], h["Series"], h["Type"]) for h in hints["SearchHints"]] == [("Pilot", "Show", "Episode")]
    for hint in hints["SearchHints"]:
        assert_known(hint, "SearchHint")
    assert get(jf, "/Search/Hints", SearchTerm="hidden").json()["SearchHints"] == []
    assert [h["Name"] for h in get(jf, "/Search/Hints", token=BOB_TOKEN, SearchTerm="hidden").json()["SearchHints"]] == ["Hidden"]
    assert get(jf, "/Search/Hints").status_code == 400


from app.services import media_probe  # noqa: E402
from title_support import POSTER, SRT, TRAILER  # noqa: E402


@pytest.fixture
def no_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unprobed files cache a probe error instead of shelling out to a host ffprobe."""
    monkeypatch.setattr(media_probe, "media_tool", lambda db, name: None)


def test_playback_info_lists_playable_versions(jf: TestClient, no_ffprobe: None) -> None:
    headers = mediabrowser(ALICE_TOKEN)
    one = jf.post(f"/Items/{HEX(MOVIE)}/PlaybackInfo", params={"UserId": HEX(ALICE), "MediaSourceId": HEX(MOVIE_4K)},
                  json={"DeviceProfile": {}}, headers=headers)
    body = one.json()
    assert_known(body, "PlaybackInfoResponse")
    for source in body["MediaSources"]:
        assert_known(source, "MediaSourceInfo")
        for stream in source["MediaStreams"]:
            assert_known(stream, "MediaStream")
    assert [s["Id"] for s in body["MediaSources"]] == [HEX(MOVIE_4K)] and len(body["PlaySessionId"]) == 32
    assert [s["Index"] for s in body["MediaSources"][0]["MediaStreams"]] == [0, 1, 2, 3]
    assert all(not source["Path"].startswith(("/tmp", "/private", "/media", "/mnt")) for source in body["MediaSources"])  # library-relative only
    sidecar = body["MediaSources"][0]["MediaStreams"][3]
    url, _, query = sidecar["DeliveryUrl"].partition("?")
    assert url.endswith("/Subtitles/3/0/Stream.srt")  # no SubtitleProfiles: the stored format
    # Final review #21: players fetch DeliveryUrl with no auth headers, so it carries the caller's token (as Jellyfin does).
    assert query == f"ApiKey={ALICE_TOKEN}" and jf.get(sidecar["DeliveryUrl"]).text == SRT
    assert jf.get(url, params={"ApiKey": "0" * 32}).status_code == 401
    vtt_only = {"DeviceProfile": {"SubtitleProfiles": [{"Format": "vtt", "Method": "External"}, {"Format": "srt", "Method": "Embed"}]}}
    converted = jf.post(f"/Items/{HEX(MOVIE)}/PlaybackInfo", params={"MediaSourceId": HEX(MOVIE_4K)}, json=vtt_only, headers=headers)
    assert converted.json()["MediaSources"][0]["MediaStreams"][3]["DeliveryUrl"].endswith(f"/Subtitles/3/0/Stream.vtt?ApiKey={ALICE_TOKEN}")
    both = get(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo").json()["MediaSources"]
    assert both[1]["MediaStreams"][3]["DeliveryUrl"].endswith(f"Stream.srt?ApiKey={ALICE_TOKEN}")  # GET too
    assert [s["Id"] for s in both] == [HEX(MOVIE_1080), HEX(MOVIE_4K)]
    assert get(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo", MediaSourceId=HEX(FILE[S1E1])).status_code == 404
    assert get(jf, f"/Items/{HEX(SECRET_EPISODE)}/PlaybackInfo").status_code == 404
    assert get(jf, f"/Items/{HEX(SERIES)}/PlaybackInfo").status_code == 404


def test_stream_direct_play_with_range(jf: TestClient) -> None:
    stream_grants.clear()
    path = f"/Videos/{HEX(MOVIE)}/stream.mkv"
    query = {"Static": "true", "MediaSourceId": HEX(MOVIE_4K), "api_key": ALICE_TOKEN}
    head = jf.head(path, params=query)
    assert head.status_code == 200 and head.headers["content-length"] == "4096"
    ranged = jf.get(path, params=query, headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    assert jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"ApiKey": ALICE_TOKEN}).status_code == 200  # preferred version
    assert jf.get(f"/Videos/{HEX(CHANNEL_NEW)}/stream", headers={"X-Emby-Token": ALICE_TOKEN}).status_code == 200
    assert jf.get(path, params={"Static": "true"}).status_code == 401
    assert jf.get(f"/Videos/{HEX(SECRET_EPISODE)}/stream", params={"api_key": ALICE_TOKEN}).status_code == 404


def test_known_stream_route_skips_credential_parsing_for_classification(jf: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Known roots normalize query keys without pre-parsing credentials in middleware."""
    def unexpected_credential_parse(_scope) -> bool:  # noqa: ANN001
        raise AssertionError("known direct streams do not need credential classification")

    monkeypatch.setattr(jellyfin_router, "has_jellyfin_credential", unexpected_credential_parse)
    response = jf.get(
        f"/VIDEOS/{HEX(MOVIE)}/STREAM",
        params={"MediaSourceId": HEX(MOVIE_4K), "ApiKey": ALICE_TOKEN},
        headers={"Range": "bytes=0-9"},
    )
    assert (response.status_code, response.content) == (206, b"\0" * 10)


def test_playback_info_grants_a_credential_free_stream(jf: TestClient) -> None:
    # Jellyfin for Android TV 0.19's player sends no credentials: PlaybackInfo lets that address stream that item.
    stream_grants.clear()
    bare = {"Static": "true", "MediaSourceId": HEX(MOVIE_4K)}
    assert jf.get(f"/Videos/{HEX(MOVIE)}/stream", params=bare).status_code == 401
    assert get(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo").status_code == 200
    assert jf.get(f"/Videos/{HEX(MOVIE)}/stream", params=bare).status_code == 200
    assert jf.get(f"/Videos/{HEX(FILE[S1E1])}/stream", params={"Static": "true"}).status_code == 401  # only the granted item
    with db_module.SessionLocal() as session:  # revoking the app ends the grant with it
        session.query(DeviceToken).filter_by(user_id=ALICE).delete()
        session.commit()
    assert jf.get(f"/Videos/{HEX(MOVIE)}/stream", params=bare).status_code == 401


def test_external_subtitles_are_served_as_stored_or_converted(jf: TestClient) -> None:
    base = f"/Videos/{HEX(MOVIE)}/{HEX(MOVIE_4K)}/Subtitles"
    assert get(jf, f"{base}/3/0/Stream.srt").text == SRT
    assert get(jf, f"{base}/3/Stream.srt").text == SRT
    vtt = get(jf, f"{base}/3/0/Stream.vtt")
    assert vtt.headers["content-type"].startswith("text/vtt") and vtt.text == "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"
    for bad in (f"{base}/2/Stream.srt", f"{base}/9/Stream.srt", f"{base}/3/Stream.ass", f"{base}/-1/Stream.srt"):
        assert get(jf, bad).status_code == 404, bad
    assert get(jf, f"/Videos/{HEX(SECRET_EPISODE)}/{HEX(SECRET_EPISODE)}/Subtitles/0/Stream.srt").status_code == 404


def test_images_need_a_token_or_the_signed_tag(jf: TestClient, tmp_path: Path) -> None:
    tag = get(jf, f"/Items/{HEX(SERIES)}").json()["ImageTags"]["Primary"]
    path = f"/Items/{HEX(SERIES)}/Images/Primary"
    with_token = get(jf, path)
    assert (with_token.status_code, with_token.content) == (200, POSTER)
    assert with_token.headers["cache-control"] == "private, max-age=31536000"  # a token's answer is never for a shared cache
    assert jf.head(path, headers=mediabrowser(ALICE_TOKEN)).status_code == 200
    image_grants.clear()
    assert jf.get(path).status_code == 404  # no token, no tag, and no recent call from this address
    assert jf.get(path, params={"tag": "0" * 32}).status_code == 404
    assert jf.get(path, params={"tag": tag}).content == POSTER  # Infuse fetches art without auth using the issued tag
    assert get(jf, f"/Items/{HEX(SERIES)}/Images/Backdrop").status_code == 404
    assert get(jf, f"{path}/1").status_code == 404
    assert get(jf, f"/Items/{HEX(SERIES)}/Images/Nope").status_code == 404
    assert get(jf, f"/Items/{HEX(SECRET_SERIES)}/Images/Primary").status_code == 404
    (tmp_path.resolve() / "media" / "youtube" / f"{CHANNEL_NEW}.jpg").write_bytes(b"thumb")
    assert get(jf, f"/Items/{HEX(CHANNEL_NEW)}/Images/Primary").content == b"thumb"
    assert get(jf, f"/Items/{CHAN}/Images/Primary").content == b"thumb"  # the channel's newest video


def test_channels_series_tags_on_episodes_and_seasons_fetch_without_a_token(jf: TestClient, tmp_path: Path) -> None:
    """Final review #20a: SeriesPrimaryImageTag is signed for the SeriesId it sits next to."""
    for name in (CHANNEL_NEW, CHANNEL_OLD):
        (tmp_path.resolve() / "media" / "youtube" / f"{name}.jpg").write_bytes(b"thumb")
    for dto in (get(jf, f"/Items/{HEX(CHANNEL_NEW)}").json(), get(jf, f"/Items/{CHAN_2025}").json()):
        art = jf.get(f"/Items/{dto['SeriesId']}/Images/Primary", params={"tag": dto["SeriesPrimaryImageTag"]})
        assert art.content == b"thumb", dto["Type"]


def test_token_less_item_and_title_art_skip_the_channel_lookup(jf: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Final review #20b: the grouped Channels query runs only for ids that are neither a title nor an item."""
    (tmp_path.resolve() / "media" / "youtube" / f"{CHANNEL_NEW}.jpg").write_bytes(b"thumb")
    video_tag = get(jf, f"/Items/{HEX(CHANNEL_NEW)}").json()["ImageTags"]["Primary"]
    poster_tag = get(jf, f"/Items/{HEX(SERIES)}").json()["ImageTags"]["Primary"]
    calls: list[object] = []
    real = jf_service.channel_index
    monkeypatch.setattr(jf_service, "channel_index", lambda db, user: calls.append(user) or real(db, user))
    assert jf.get(f"/Items/{HEX(CHANNEL_NEW)}/Images/Primary", params={"tag": video_tag}).content == b"thumb"
    assert jf.get(f"/Items/{HEX(SERIES)}/Images/Primary", params={"tag": poster_tag}).content == POSTER
    assert jf.get(f"/Items/{HEX(CHANNEL_NEW)}/Images/Primary", params={"tag": "0" * 32}).status_code == 404
    assert calls == []


def test_signed_tag_covers_library_items_and_dies_with_the_files(jf: TestClient, tmp_path: Path) -> None:
    (tmp_path.resolve() / "media" / "youtube" / f"{CHANNEL_NEW}.jpg").write_bytes(b"thumb")
    video_tag = get(jf, f"/Items/{HEX(CHANNEL_NEW)}").json()["ImageTags"]["Primary"]
    video = f"/Items/{HEX(CHANNEL_NEW)}/Images/Primary"
    assert jf.get(video, params={"tag": video_tag}).content == b"thumb"  # Library items too
    assert jf.get(video, params={"tag": "0" * 32}).status_code == 404
    chan_tag = get(jf, f"/Items/{CHAN}").json()["ImageTags"]["Primary"]
    chan_art = f"/Items/{CHAN}/Images/Primary"
    assert jf.get(chan_art, params={"tag": chan_tag}).content == b"thumb"  # final review #20: Channels art without a token
    season_tag = get(jf, f"/Items/{CHAN_2025}").json()["ImageTags"]["Primary"]
    assert jf.get(f"/Items/{CHAN_2025}/Images/Primary", params={"tag": season_tag}).content == b"thumb"
    assert jf.get(chan_art, params={"tag": "0" * 32}).status_code == 404
    assert jf.get(f"/Items/{CHAN_2025}/Images/Primary", params={"tag": chan_tag}).status_code == 404  # a tag names one id
    movie_tag = get(jf, f"/Items/{HEX(MOVIE)}").json()["ImageTags"]["Primary"]
    movie = f"/Items/{HEX(MOVIE)}/Images/Primary"
    assert jf.get(movie, params={"tag": movie_tag}).content == POSTER
    with db_module.SessionLocal() as session:  # Every file tombstoned -> the issued tags stop working
        for item_id in (MOVIE_1080, MOVIE_4K, TRAILER, CHANNEL_NEW):
            session.get(LibraryItem, item_id).status = "missing"
        session.commit()
    assert jf.get(movie, params={"tag": movie_tag}).status_code == 404
    assert jf.get(video, params={"tag": video_tag}).status_code == 404
    assert jf.get(chan_art, params={"tag": chan_tag}).status_code == 404


def test_special_features_are_extras(jf: TestClient) -> None:
    extras = get(jf, f"/Items/{HEX(MOVIE)}/SpecialFeatures").json()
    assert [(e["Id"], e["Type"], e["ExtraType"]) for e in extras] == [(HEX(TRAILER), "Video", "Trailer")]
    assert get(jf, f"/Users/{HEX(ALICE)}/Items/{HEX(MOVIE)}/LocalTrailers").json() == []
    assert get(jf, f"/Items/{HEX(SECRET_SERIES)}/SpecialFeatures").status_code == 404


def test_vanished_file_is_404_not_500(jf: TestClient, tmp_path: Path, no_ffprobe: None) -> None:
    (tmp_path.resolve() / "media" / "Movie (2020)" / "Movie (2020) - 4K.mkv").unlink()
    query = {"MediaSourceId": HEX(MOVIE_4K), "api_key": ALICE_TOKEN}
    assert jf.get(f"/Videos/{HEX(MOVIE)}/stream", params=query).status_code == 404
    assert jf.head(f"/Videos/{HEX(MOVIE)}/stream", params=query).status_code == 404
    assert get(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo", MediaSourceId=HEX(MOVIE_4K)).status_code == 404
    assert get(jf, f"/Videos/{HEX(MOVIE)}/{HEX(MOVIE_4K)}/Subtitles/3/Stream.srt").status_code == 404


from app.models import User  # noqa: E402
from app.security import get_current_user  # noqa: E402

TICKS = 10_000_000


def lumina_progress(client: TestClient, item_id: str) -> dict | None:
    """Lumina's own progress API for the same member (cookie auth stood in by a dependency override)."""
    with db_module.SessionLocal() as session:
        alice = session.get(User, ALICE)
    app.dependency_overrides[get_current_user] = lambda: alice
    try:
        return client.get(f"/api/library/{item_id}/playback").json()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def post(client: TestClient, path: str, body: dict | None = None, token: str = ALICE_TOKEN):  # noqa: ANN201
    return client.post(path, json=body or {}, headers=mediabrowser(token))


def test_progress_reports_are_lumina_progress(jf: TestClient) -> None:
    report = {"ItemId": HEX(S1E1), "MediaSourceId": HEX(FILE[S1E1])}
    assert post(jf, "/Sessions/Playing", {**report, "PositionTicks": 0}).status_code == 204
    assert post(jf, "/Sessions/Playing/Progress", {**report, "PositionTicks": 600 * TICKS, "IsPaused": True}).status_code == 204
    assert lumina_progress(jf, FILE[S1E1])["position_seconds"] == 600
    assert names(get(jf, "/UserItems/Resume")) == ["Pilot"]
    assert post(jf, "/Sessions/Playing/Stopped", {**report, "PositionTicks": 1450 * TICKS}).status_code == 204
    assert lumina_progress(jf, FILE[S1E1])["completed"] is True  # the shared 95% rule
    assert names(get(jf, "/Shows/NextUp")) == ["Second"]
    assert post(jf, "/Sessions/Playing/Ping").status_code == 204
    assert post(jf, "/Sessions/Playing/Progress", {"ItemId": HEX(S1E2)}).status_code == 204  # no position: nothing recorded
    assert lumina_progress(jf, FILE[S1E2]) is None
    assert post(jf, "/Sessions/Playing", {"ItemId": HEX(CHANNEL_NEW), "PositionTicks": 30 * TICKS}).status_code == 204
    assert names(get(jf, "/UserItems/Resume", ParentId=CHANNELS)) == ["New upload"]


def test_played_favorite_and_user_data_round_trip(jf: TestClient) -> None:
    assert post(jf, f"/UserPlayedItems/{HEX(S1E2)}").json()["Played"] is True
    assert jf.delete(f"/Users/{HEX(ALICE)}/PlayedItems/{HEX(S1E2)}", headers=mediabrowser(ALICE_TOKEN)).json()["Played"] is False
    series = post(jf, f"/UserPlayedItems/{HEX(SERIES)}").json()
    assert (series["Played"], series["UnplayedItemCount"]) == (True, 0)
    assert jf.delete(f"/UserPlayedItems/{HEX(SERIES)}", headers=mediabrowser(ALICE_TOKEN)).json()["UnplayedItemCount"] == 4
    assert post(jf, f"/UserPlayedItems/{CHAN}").json()["Key"] == CHAN
    assert get(jf, f"/Items/{HEX(CHANNEL_OLD)}").json()["UserData"]["Played"] is True
    assert post(jf, f"/UserFavoriteItems/{HEX(SERIES)}").json()["IsFavorite"] is True
    assert get(jf, f"/Items/{HEX(SERIES)}").json()["UserData"]["IsFavorite"] is True
    assert jf.delete(f"/Users/{HEX(ALICE)}/FavoriteItems/{HEX(SERIES)}", headers=mediabrowser(ALICE_TOKEN)).json()["IsFavorite"] is False
    assert post(jf, f"/UserFavoriteItems/{CHAN}").json()["IsFavorite"] is True
    path = f"/UserItems/{HEX(MOVIE)}/UserData"
    assert get(jf, path).json()["Played"] is False
    assert post(jf, path, {"IsFavorite": True}).json()["IsFavorite"] is True
    assert post(jf, path, {"PlaybackPositionTicks": 300 * TICKS}).json()["PlaybackPositionTicks"] == 300 * TICKS
    assert post(jf, f"/Users/{HEX(ALICE)}/Items/{HEX(MOVIE)}/UserData", {"Played": True}).json()["Played"] is True
    for dto in (get(jf, path).json(), post(jf, f"/UserPlayedItems/{HEX(S1E1)}").json()):
        assert_known(dto, "UserItemDataDto")


def test_foreign_and_garbage_ids_write_nothing(jf: TestClient) -> None:
    for body in ({"ItemId": HEX(SECRET_EPISODE), "PositionTicks": TICKS}, {"ItemId": "../../etc", "PositionTicks": TICKS},
                 {"ItemId": TV, "PositionTicks": TICKS}, {"ItemId": HEX(S1E1), "MediaSourceId": HEX(MOVIE_4K), "PositionTicks": TICKS},
                 {"PositionTicks": TICKS}):
        assert post(jf, "/Sessions/Playing/Progress", body).status_code == 404, body
    assert post(jf, "/Sessions/Playing", {"ItemId": HEX(S1E1), "PositionTicks": -1}).status_code == 400
    for path in (f"/UserPlayedItems/{HEX(SECRET_SERIES)}", f"/UserFavoriteItems/{HEX(SECRET_EPISODE)}",
                 f"/UserFavoriteItems/{TV}", f"/UserPlayedItems/{TV}", f"/UserPlayedItems/garbage",
                 f"/UserItems/{HEX(SECRET_SERIES)}/UserData"):
        assert post(jf, path, {"Played": True, "IsFavorite": True}).status_code == 404, path
    assert post(jf, f"/Users/{HEX(BOB)}/PlayedItems/{HEX(S1E1)}").status_code == 404  # uid is not the caller
    with db_module.SessionLocal() as session:
        assert session.query(PlaybackProgress).count() == 0
        assert session.query(MemberFavorite).count() == 0


from app.models import DeviceToken  # noqa: E402
from app.services.connected_apps import image_grants, stream_grants  # noqa: E402
from support import make_user  # noqa: E402
from title_support import SECRET_SEASON, device_token  # noqa: E402

ADMIN, ADMIN_TOKEN = "00000000-0000-0000-0000-0000000000ad", "admin-device-token-for-tests"


def test_infuse_style_session_replay(jf: TestClient, no_ffprobe: None) -> None:
    """Library Mode sync -> item -> PlaybackInfo -> direct play -> progress -> Stopped -> Next Up -> played/favorite."""
    headers, me = mediabrowser(ALICE_TOKEN), HEX(ALICE)

    def call(method: str, path: str, **kwargs):  # noqa: ANN003, ANN202
        response = jf.request(method, path, headers={**headers, **kwargs.pop("headers", {})}, **kwargs)
        assert response.status_code < 400, (method, path, response.status_code, response.text)
        return response

    views = call("GET", "/UserViews", params={"userId": me, "includeExternalContent": "false"}).json()["Items"]
    shows_view = next(v["Id"] for v in views if v["Name"] == "Shows")
    series, start = [], 0
    while True:  # Infuse pages its library sync
        page = call("GET", f"/Users/{me}/Items", params={
            "ParentId": shows_view, "IncludeItemTypes": "Series", "Recursive": "true", "StartIndex": start, "Limit": 1,
            "Fields": "ProviderIds,Overview,DateCreated,Genres", "EnableUserData": "true", "EnableImageTypes": "Primary,Backdrop",
        }).json()
        series += page["Items"]
        start += 1
        if start >= page["TotalRecordCount"]:
            break
    assert [s["Name"] for s in series] == ["Show"]
    show = series[0]["Id"]
    seasons = call("GET", f"/Shows/{show}/Seasons", params={"userId": me, "Fields": "ItemCounts"}).json()["Items"]
    season1 = next(s["Id"] for s in seasons if s["IndexNumber"] == 1)
    episodes = call("GET", f"/Shows/{show}/Episodes", params={"seasonId": season1, "userId": me, "Fields": "MediaSources"}).json()["Items"]
    pilot = episodes[0]
    source = pilot["MediaSources"][0]["Id"]
    assert (pilot["Name"], source) == ("Pilot", HEX(FILE[S1E1]))
    call("GET", f"/Users/{me}/Items/{pilot['Id']}")
    call("GET", f"/Items/{show}/Images/Primary", params={"tag": series[0]["ImageTags"]["Primary"], "maxWidth": 400})
    info = call("POST", f"/Items/{pilot['Id']}/PlaybackInfo",
                params={"UserId": me, "MediaSourceId": source, "StartTimeTicks": 0, "IsPlayback": "true"},
                json={"DeviceProfile": {"MaxStreamingBitrate": 120_000_000}}).json()
    session = info["PlaySessionId"]
    stream = f"/Videos/{pilot['Id']}/stream.mkv"
    query = {"Static": "true", "MediaSourceId": source, "PlaySessionId": session, "api_key": ALICE_TOKEN}
    assert jf.head(stream, params=query).status_code == 200
    ranged = jf.get(stream, params=query, headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    report = {"ItemId": pilot["Id"], "MediaSourceId": source, "PlaySessionId": session, "CanSeek": True}
    call("POST", "/Sessions/Playing", json={**report, "PositionTicks": 0})
    call("POST", "/Sessions/Playing/Progress", json={**report, "PositionTicks": 600 * TICKS, "EventName": "timeupdate"})
    resume = call("GET", "/UserItems/Resume", params={"userId": me, "MediaTypes": "Video", "Limit": 12}).json()["Items"]
    assert [(r["Name"], r["UserData"]["PlaybackPositionTicks"]) for r in resume] == [("Pilot", 600 * TICKS)]
    assert lumina_progress(jf, FILE[S1E1])["position_seconds"] == 600  # the same row Lumina's UI resumes from
    call("POST", "/Sessions/Playing/Stopped", json={**report, "PositionTicks": 1500 * TICKS})
    up_next = call("GET", "/Shows/NextUp", params={"userId": me, "SeriesId": show, "Fields": "MediaSources"}).json()["Items"]
    assert [e["Name"] for e in up_next] == ["Second"]
    assert call("POST", f"/Users/{me}/PlayedItems/{up_next[0]['Id']}").json()["Played"] is True
    assert [e["Name"] for e in call("GET", "/Shows/NextUp", params={"userId": me}).json()["Items"]] == ["Return"]
    assert call("POST", f"/Users/{me}/FavoriteItems/{show}").json()["IsFavorite"] is True
    favorites = call("GET", f"/Users/{me}/Items", params={"Recursive": "true", "Filters": "IsFavorite"}).json()["Items"]
    assert [f["Id"] for f in favorites] == [show]
    assert call("DELETE", f"/Users/{me}/FavoriteItems/{show}").json()["IsFavorite"] is False


def test_revoked_device_is_401_everywhere(jf: TestClient) -> None:
    assert get(jf, "/UserViews").status_code == 200
    with db_module.SessionLocal() as session:
        session.query(DeviceToken).filter_by(user_id=ALICE).delete()
        session.commit()
    assert get(jf, "/UserViews").status_code == 401
    assert jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"api_key": ALICE_TOKEN}).status_code == 401
    assert get(jf, "/UserViews", token=BOB_TOKEN).status_code == 200


def test_tokens_never_reach_server_logs(jf: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jellyfin_trace", True)
    caplog.set_level(logging.DEBUG)
    get(jf, "/Items", Recursive="true")
    jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"api_key": ALICE_TOKEN})
    jf.get("/Nope/Nothing", params={"ApiKey": ALICE_TOKEN})
    jf.get(f"/Items/{HEX(SECRET_SERIES)}", headers={"X-Emby-Token": ALICE_TOKEN})
    jf.get("/Items", headers={"X-Emby-Authorization": mediabrowser(BOB_TOKEN)["Authorization"]})
    server_lines = "\n".join(record.getMessage() for record in caplog.records if not record.name.startswith("httpx"))
    assert "jellyfin.trace" in server_lines and "jellyfin.unhandled" in server_lines
    assert ALICE_TOKEN not in server_lines and BOB_TOKEN not in server_lines


def test_private_items_stay_invisible_on_every_list(jf: TestClient) -> None:
    watch(BOB, FILE[SECRET_EPISODE], hours=5, position=100)
    watch(BOB, CHANNEL_PRIVATE, hours=6, position=100)
    with db_module.SessionLocal() as session:  # an admin does not see a member's private files either
        session.add(make_user(ADMIN, username="root", role="admin"))
        device_token(session, ADMIN, ADMIN_TOKEN, device_id="device-3")
        session.commit()
    for token in (ALICE_TOKEN, ADMIN_TOKEN):
        seen = json.dumps([
            get(jf, "/Items", token=token, Recursive="true", IncludeItemTypes="Movie,Series,Season,Episode,BoxSet").json(),
            get(jf, "/Items", token=token, ParentId=CHANNELS).json(),
            get(jf, "/Items", token=token, ParentId=CHANNELS, Recursive="true", IncludeItemTypes="Episode").json(),
            get(jf, "/Items/Latest", token=token).json(),
            get(jf, "/Items/Latest", token=token, ParentId=CHANNELS).json(),
            get(jf, "/UserItems/Resume", token=token).json(),
            get(jf, "/Shows/NextUp", token=token).json(),
            get(jf, "/Search/Hints", token=token, SearchTerm="e").json(),
            get(jf, "/UserViews", token=token).json(),
        ])
        for secret in (SECRET_SERIES, SECRET_SEASON, SECRET_EPISODE, FILE[SECRET_EPISODE], CHANNEL_PRIVATE, synthetic_id("channel:Youtube:BobChan")):
            assert HEX(secret) not in seen, (token, secret)
        assert "Secret Show" not in seen and "Hidden" not in seen and "BobChan" not in seen and "Bob vlog" not in seen


def test_casing_and_slashes_return_identical_lists(jf: TestClient) -> None:
    params = {"ParentId": TV, "IncludeItemTypes": "Series,Episode", "Recursive": "true"}
    canonical = get(jf, f"/Users/{HEX(ALICE)}/Items", **params).json()
    assert canonical["TotalRecordCount"] == 5
    assert get(jf, f"/users/{HEX(ALICE).upper()}/items", **{k.lower(): v for k, v in params.items()}).json() == canonical
    assert jf.get(f"http://localhost//USERS/{HEX(ALICE)}/items/", params={k.upper(): v for k, v in params.items()},
                  headers=mediabrowser(ALICE_TOKEN)).json() == canonical


def test_jellyfin_and_emby_prefixes_never_reach_the_api(jf: TestClient) -> None:
    assert get(jf, f"/Items/{HEX(MOVIE)}").status_code == 200
    for path in (f"/jellyfin/items/{HEX(MOVIE)}", f"/Jellyfin/Items/{HEX(MOVIE)}", f"/emby/Items/{HEX(MOVIE)}", "/EMBY",
                 "/jellyfin/System/Info/Public", "/jellyfin/system/info/public", "/emby/System/Info/Public", "/jellyfin",
                 "http://localhost//JELLYFIN//emby/Items/"):
        response = get(jf, path)
        assert (response.status_code, response.content) == (404, b""), path


def test_imported_item_runtime_falls_back_to_the_cached_probe(jf: TestClient) -> None:
    """The importer never sets LibraryItem.duration; lists and summaries read the cached probe instead (batched)."""
    from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, User
    from sqlalchemy import select

    from app.services.titles import TitleService
    from title_support import TRAILER

    with db_module.SessionLocal() as session:
        for item_id, seconds in ((FILE[S1E1], "1320.25"), (TRAILER, 90.0)):
            session.get(LibraryItem, item_id).duration = None
            artifact = session.scalar(select(MediaArtifact).join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id)
                                      .where(LibraryItemArtifact.library_item_id == item_id))
            artifact.probe = {"fingerprint": "x", "duration": seconds}
        session.commit()
    watch(ALICE, FILE[S1E1], hours=1, position=660)
    with db_module.SessionLocal() as session:
        session.query(PlaybackProgress).update({"duration_seconds": None})
        session.commit()
    ticks = 13_202_500_000
    listed = get(jf, f"/Shows/{HEX(SERIES)}/Episodes", SeasonId=HEX(SEASON1)).json()["Items"][0]
    assert listed["RunTimeTicks"] == ticks and listed["UserData"]["PlayedPercentage"] == 49.99
    assert get(jf, f"/Items/{HEX(S1E1)}").json()["RunTimeTicks"] == ticks
    extras = get(jf, f"/Users/{HEX(ALICE)}/Items/{HEX(MOVIE)}/SpecialFeatures").json()
    assert [extra["RunTimeTicks"] for extra in extras] == [900_000_000]
    with db_module.SessionLocal() as session:
        pilot, alice = session.get(MediaTitle, S1E1), session.get(User, ALICE)
        assert TitleService(session).summaries(alice, [pilot])[0].runtime_seconds == 1320
        assert [extra.duration_seconds for extra in TitleService(session).detail(alice, session.get(MediaTitle, MOVIE)).extras] == [90]


def test_root_form_serves_items_and_streams_with_the_same_auth(jf: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    canonical = get(jf, f"/Items/{HEX(MOVIE)}").json()
    assert get(jf, f"/Items/{HEX(MOVIE)}").json() == canonical
    assert get(jf, f"http://localhost//users/{HEX(ALICE).upper()}/items/{HEX(MOVIE)}/").json() == canonical
    assert get(jf, f"/Items/{HEX(MOVIE)}", token=None).status_code == 401
    assert jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"ApiKey": ALICE_TOKEN}).status_code == 200
    assert jf.head(f"/videos/{HEX(MOVIE)}/stream.mkv", params={"api_key": ALICE_TOKEN}).status_code == 200
    assert jf.get(f"/Videos/{HEX(SECRET_EPISODE)}/stream", params={"api_key": ALICE_TOKEN}).status_code == 404
    caplog.set_level(logging.WARNING, logger="lumina.jellyfin")
    assert get(jf, "/Items/0123456789ABCDEF0123456789ABCDEF/Some/Probe").status_code == 404
    assert "jellyfin.unhandled GET /jellyfin/items/{id}/{id}/{id}" in caplog.text
    assert jf.get("/api/health").json()["status"] == "ok"  # /api is never the Jellyfin surface
