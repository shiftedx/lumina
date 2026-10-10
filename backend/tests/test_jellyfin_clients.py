"""Jellyfin client compatibility: the error-body policy, empty-not-404 lists, client probes, downloads and /socket."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from starlette.requests import Request

from app import db as db_module
from app.config import settings
from app.main import app
from app.models import DeviceToken, LibraryItem, MemberAccess
from app.routers import jellyfin_probes
from app.services import activity, media_probe, public_address
from app.services.rate_limit import grant_address
from app.services.connected_apps import image_grants
from app.services.media_titles import jellyfin_id, synthetic_id
from test_jellyfin_api import MOVIES, TV, get, post
from title_support import ALICE, ALICE_TOKEN, BOB, BOB_TOKEN, FILE, TRAILER, MOVIE, MOVIE_1080, MOVIE_4K, S1E1, SEASON1, SECRET_SERIES, SERIES, jellyfin_household, mediabrowser

HEX = jellyfin_id
EMPTY = {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
UNKNOWN = "0" * 32


@pytest.fixture
def jf(tmp_path: Path):  # noqa: ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


@pytest.fixture
def no_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unprobed files cache a probe error instead of shelling out to a host ffprobe."""
    monkeypatch.setattr(media_probe, "media_tool", lambda db, name: None)


# ---- R1: error bodies ----------------------------------------------------------------------------------------

def test_additional_parts_is_an_empty_result(jf: TestClient) -> None:
    for item_id in (HEX(MOVIE), UNKNOWN):  # Roku asks for every item, known or not
        response = get(jf, f"/Videos/{item_id}/AdditionalParts")
        assert (response.status_code, response.json()) == (200, EMPTY)
    assert get(jf, f"/Videos/{HEX(MOVIE)}/AdditionalParts", token=None).status_code == 401


def test_an_unknown_route_is_an_empty_bodied_404(jf: TestClient) -> None:
    response = get(jf, "/Some/Unserved/Probe")
    assert (response.status_code, response.content) == (404, b"")


def test_errors_on_the_jellyfin_surface_have_no_body(jf: TestClient) -> None:
    login = {"Authorization": 'MediaBrowser Client="Roku", Device="r", DeviceId="roku-1", Version="3"'}
    bad_password = jf.post("/Users/AuthenticateByName", json={"Username": "alice", "Pw": "wrong"}, headers=login)
    assert (bad_password.status_code, bad_password.content) == (401, b"")
    assert (get(jf, "/Items", token="not-a-token").status_code, get(jf, "/Items", token="not-a-token").content) == (401, b"")
    assert get(jf, f"/Items/{UNKNOWN}").content == b""
    invalid = get(jf, "/Items", StartIndex=-1)
    assert (invalid.status_code, invalid.content) == (400, b"")  # Jellyfin has no 422
    assert jf.get("/jellyfin/items").content == b""  # the retired private prefix


def test_api_errors_keep_their_json_body(jf: TestClient) -> None:
    assert jf.get("/api/library/not-an-item").headers["content-type"] == "application/json"


# ---- R2: lists never 404 on an unknown parent ------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/Items", f"/Users/{HEX(ALICE)}/Items", "/UserItems/Resume", f"/Users/{HEX(ALICE)}/Items/Resume", "/Shows/NextUp", "/Genres",
])
@pytest.mark.parametrize("parent", [UNKNOWN, HEX(SECRET_SERIES)])  # missing, and hidden from alice
def test_a_list_below_an_unknown_parent_is_empty(jf: TestClient, path: str, parent: str) -> None:
    response = get(jf, path, ParentId=parent, SeriesId=parent)
    assert (response.status_code, response.json()) == (200, EMPTY)


@pytest.mark.parametrize("path", [f"/Shows/{UNKNOWN}/Seasons", f"/Shows/{HEX(SECRET_SERIES)}/Episodes", f"/Shows/{HEX(MOVIE)}/Episodes"])
def test_show_children_of_a_non_series_are_empty(jf: TestClient, path: str) -> None:
    assert get(jf, path).json() == EMPTY


def test_latest_below_an_unknown_parent_is_an_empty_array(jf: TestClient) -> None:
    for path in ("/Items/Latest", f"/Users/{HEX(ALICE)}/Items/Latest"):
        response = get(jf, path, ParentId=HEX(SECRET_SERIES))
        assert (response.status_code, response.json()) == (200, [])


def test_latest_of_unserved_types_is_empty(jf: TestClient) -> None:
    assert get(jf, "/Items/Latest", IncludeItemTypes="Audio,MusicAlbum").json() == []
    assert get(jf, "/Items/Latest", IncludeItemTypes="Movie").json() != []


# ---- R3: probes -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    f"/Items/{HEX(MOVIE)}/Intros", f"/Users/{HEX(ALICE)}/Items/{HEX(MOVIE)}/Intros", "/Artists", "/Artists/AlbumArtists", "/Shows/Upcoming",
    "/Trailers", "/Channels", "/LiveTv/Channels", "/LiveTv/Programs", "/LiveTv/Programs/Recommended",
])
def test_empty_query_results(jf: TestClient, path: str) -> None:
    assert get(jf, path).json() == EMPTY
    assert get(jf, path, token=None).status_code == 401


def test_empty_arrays_and_objects(jf: TestClient) -> None:
    assert get(jf, "/Movies/Recommendations").json() == []
    assert get(jf, "/LiveTv/Info").json() == {"Services": [], "IsEnabled": False, "EnabledUsers": []}


def test_theme_media_is_empty_and_owned_by_the_item(jf: TestClient) -> None:
    owned = EMPTY | {"OwnerId": HEX(MOVIE)}
    assert get(jf, f"/Items/{HEX(MOVIE)}/ThemeSongs").json() == owned
    assert get(jf, f"/Items/{HEX(MOVIE)}/ThemeVideos").json() == owned
    assert get(jf, f"/Items/{HEX(MOVIE)}/ThemeMedia").json() == {
        "ThemeVideosResult": owned, "ThemeSongsResult": owned, "SoundtrackSongsResult": owned}
    assert get(jf, f"/Items/{HEX(SECRET_SERIES)}/ThemeSongs").status_code == 404


def test_ancestors_run_from_the_parent_to_the_library_view(jf: TestClient) -> None:
    chain = get(jf, f"/Items/{HEX(S1E1)}/Ancestors").json()
    assert [(item["Type"], item["Id"]) for item in chain] == [("Season", HEX(SEASON1)), ("Series", HEX(SERIES)), ("CollectionFolder", TV)]
    assert get(jf, f"/Items/{TV}/Ancestors").json() == []
    assert get(jf, f"/Items/{HEX(SECRET_SERIES)}/Ancestors").status_code == 404


def test_image_infos_list_the_art_the_item_has(jf: TestClient) -> None:
    dto = get(jf, f"/Items/{HEX(SERIES)}").json()
    infos = get(jf, f"/Items/{HEX(SERIES)}/Images").json()
    assert {(info["ImageType"], info["ImageTag"]) for info in infos if info["ImageType"] != "Backdrop"} == set(dto["ImageTags"].items())
    assert all(isinstance(info[key], int) for info in infos for key in ("Height", "Width", "Size"))
    assert get(jf, f"/Items/{TV}/Images").json() == []
    assert get(jf, f"/Items/{HEX(SECRET_SERIES)}/Images").status_code == 404


def test_item_counts_are_the_callers_visible_titles(jf: TestClient) -> None:
    alice, bob = get(jf, "/Items/Counts").json(), get(jf, "/Items/Counts", token=BOB_TOKEN).json()
    assert alice["SeriesCount"] == 1 and bob["SeriesCount"] == 2  # bob also sees the private series
    assert alice["EpisodeCount"] < bob["EpisodeCount"] and alice["MovieCount"] >= 1 and alice["SongCount"] == 0


def test_items_root_and_media_folders(jf: TestClient) -> None:
    root = get(jf, "/Items/Root").json()
    assert (root["Type"], root["IsFolder"]) == ("AggregateFolder", True)
    folders = get(jf, "/Library/MediaFolders").json()
    assert {MOVIES, TV} <= {item["Id"] for item in folders["Items"]} and {item["Type"] for item in folders["Items"]} == {"CollectionFolder"}


def test_sessions_list_only_the_callers_own_app(jf: TestClient) -> None:
    sessions = get(jf, "/Sessions").json()
    assert [(s["UserId"], s["DeviceId"]) for s in sessions] == [(HEX(ALICE), "device-1")] and "PlayState" in sessions[0]
    assert get(jf, "/Sessions", token=BOB_TOKEN).json()[0]["DeviceId"] == "device-2"
    assert get(jf, "/Sessions", deviceId="device-2").json() == []  # bob's device is never listed to alice


def test_system_probes(jf: TestClient) -> None:
    assert get(jf, "/System/Endpoint").json() == {"IsLocal": False, "IsInNetwork": False}  # the test client has no address
    assert get(jf, "/System/Configuration/encoding").json()["EnableSubtitleExtraction"] is True
    assert get(jf, "/System/Configuration").json()["IsStartupWizardCompleted"] is True
    times = get(jf, "/GetUtcTime").json()
    assert set(times) == {"RequestReceptionTime", "ResponseTransmissionTime"} and times["RequestReceptionTime"].endswith("Z")


@pytest.mark.parametrize(("address", "expected"), [
    ("127.0.0.1", {"IsLocal": True, "IsInNetwork": True}),
    ("192.168.1.20", {"IsLocal": False, "IsInNetwork": True}),
    ("8.8.8.8", {"IsLocal": False, "IsInNetwork": False}),
])
def test_system_endpoint_reads_the_client_address(jf: TestClient, monkeypatch: pytest.MonkeyPatch, address: str, expected: dict) -> None:
    monkeypatch.setattr(jellyfin_probes, "client_ip", lambda _request: address)
    assert get(jf, "/System/Endpoint").json() == expected


def test_quick_connect_is_off(jf: TestClient) -> None:
    assert jf.get("/QuickConnect/Enabled", headers=mediabrowser(ALICE_TOKEN)).json() is False
    response = jf.post("/QuickConnect/Initiate")
    assert (response.status_code, response.content) == (401, b"")


def test_client_logs_are_accepted_and_discarded(jf: TestClient) -> None:
    ok = jf.post("/ClientLog/Document", content=b"crash", headers=mediabrowser(ALICE_TOKEN))
    assert ok.status_code == 200 and ok.json()["FileName"].endswith(".log")
    assert jf.post("/ClientLog/Document", content=b"x" * (1024 * 1024 + 1), headers=mediabrowser(ALICE_TOKEN)).status_code == 413
    assert jf.post("/ClientLog/Document", content=b"crash").status_code == 401


def test_user_configuration_posts_are_accepted(jf: TestClient) -> None:
    assert post(jf, "/Users/Configuration", {"SubtitleMode": "Default"}).status_code == 204
    assert post(jf, f"/Users/{HEX(ALICE)}/Configuration", {}).status_code == 204
    assert jf.post("/Users/Configuration", json={}).status_code == 401


# ---- R3: downloads ---------------------------------------------------------------------------------------------

def test_download_needs_a_token_and_supports_ranges(jf: TestClient, no_ffprobe: None) -> None:
    path = f"/Items/{HEX(MOVIE)}/Download"
    assert get(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo").status_code == 200  # a stream grant, which a download must not accept
    assert jf.get(path).status_code == 401
    whole = jf.get(path, params={"MediaSourceId": HEX(MOVIE_4K), "api_key": ALICE_TOKEN})
    assert whole.status_code == 200 and len(whole.content) == 4096 and whole.headers["content-disposition"].startswith("attachment")
    ranged = jf.get(path, params={"api_key": ALICE_TOKEN}, headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    assert get(jf, f"/Items/{HEX(SECRET_SERIES)}/Download").status_code == 404
    assert get(jf, f"/Items/{HEX(MOVIE)}/Download", MediaSourceId=HEX(S1E1)).status_code == 404


def test_download_is_allowed_exactly_when_streaming_is(jf: TestClient) -> None:
    with db_module.SessionLocal() as session:
        session.add(MemberAccess(user_id=ALICE, can_download=False))  # the vault-save flag does not gate device downloads
        session.commit()
    assert get(jf, f"/Items/{HEX(MOVIE)}/Download").status_code == 200
    assert get(jf, f"/Users/{HEX(ALICE)}").json()["Policy"]["EnableContentDownloading"] is True
    with db_module.SessionLocal() as session:
        app_id = session.query(DeviceToken).filter_by(user_id=ALICE).one().id
    activity.block(ALICE, MOVIE_1080, app_id)  # an admin stop ends the download as it does the stream
    assert get(jf, f"/Items/{HEX(MOVIE)}/Download", MediaSourceId=HEX(MOVIE_1080)).status_code == 403


# ---- R4: PlaybackInfo ------------------------------------------------------------------------------------------

def test_playback_info_honours_the_body_media_source_id(jf: TestClient, no_ffprobe: None) -> None:
    both = post(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo", {"DeviceProfile": {}}).json()["MediaSources"]
    assert [source["Id"] for source in both] == [HEX(MOVIE_1080), HEX(MOVIE_4K)]
    chosen = post(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo", {"MediaSourceId": HEX(MOVIE_4K)}).json()["MediaSources"]
    assert [source["Id"] for source in chosen] == [HEX(MOVIE_4K)]


def test_playback_info_of_an_unplayable_item_is_an_empty_404(jf: TestClient) -> None:
    response = post(jf, f"/Items/{HEX(SERIES)}/PlaybackInfo")
    assert (response.status_code, response.content) == (404, b"")


# ---- R5: /socket -----------------------------------------------------------------------------------------------

def test_socket_greets_an_authenticated_client_and_answers_keep_alive(jf: TestClient) -> None:
    with jf.websocket_connect("ws://localhost/socket", params={"api_key": ALICE_TOKEN, "deviceId": "device-1"}) as socket:
        greeting = socket.receive_json()
        assert (greeting["MessageType"], greeting["Data"]) == ("ForceKeepAlive", 60)
        socket.send_text("not json")
        socket.send_json({"MessageType": "SessionsStart", "Data": "0,1500"})  # ignored, not an error
        socket.send_json({"MessageType": "KeepAlive"})
        assert socket.receive_json()["MessageType"] == "KeepAlive"


def test_socket_takes_the_token_from_a_header_too(jf: TestClient) -> None:
    with jf.websocket_connect("ws://localhost/socket", headers=mediabrowser(ALICE_TOKEN)) as socket:
        assert socket.receive_json()["MessageType"] == "ForceKeepAlive"


def test_socket_refuses_the_unauthenticated(jf: TestClient) -> None:
    for kwargs in ({}, {"params": {"api_key": "nope"}}):  # closed before accept: the handshake fails (HTTP 403 on the wire)
        with pytest.raises(WebSocketDisconnect) as refused, jf.websocket_connect("ws://localhost/socket", **kwargs):
            pass
        assert refused.value.code == 1008


def test_socket_ends_on_an_oversize_message(jf: TestClient) -> None:
    with jf.websocket_connect("ws://localhost/socket", params={"api_key": ALICE_TOKEN}) as socket:
        socket.receive_json()
        socket.send_text("x" * 5000)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1009


def test_socket_is_refused_while_the_api_is_off(tmp_path: Path) -> None:
    jellyfin_household(tmp_path.resolve() / "media", enabled=False)
    client = TestClient(app, base_url="http://localhost")
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("ws://localhost/socket", params={"api_key": ALICE_TOKEN}):
        pass


# ---- R6/R7: images ---------------------------------------------------------------------------------------------

def test_tags_is_an_alias_of_tag(jf: TestClient) -> None:
    tag = get(jf, f"/Items/{HEX(SERIES)}").json()["ImageTags"]["Primary"]
    assert jf.get(f"/Items/{HEX(SERIES)}/Images/Primary", params={"Tags": tag}).status_code == 200
    assert jf.get(f"/Items/{HEX(SERIES)}/Images/Primary", params={"Tags": "0" * 32}).status_code == 404


def test_a_tagless_poster_follows_an_authenticated_call_from_its_address(jf: TestClient) -> None:
    path = f"/Items/{HEX(SERIES)}/Images/Primary"
    assert jf.get(path).status_code == 404  # nothing seen from this address yet
    get(jf, "/Items")
    assert jf.get(path).status_code == 200
    assert jf.get(path, params={"tag": "0" * 32}).status_code == 404  # a wrong tag is never rescued by the grant
    image_grants.clear()
    assert jf.get(path).status_code == 404


def test_the_grant_dies_with_the_token_and_sees_only_what_its_member_sees(jf: TestClient) -> None:
    get(jf, "/Items", token=BOB_TOKEN)
    assert jf.get(f"/Items/{HEX(SERIES)}/Images/Primary").status_code == 200
    get(jf, "/Items")  # alice is now the address's latest caller; the private series is not hers
    assert jf.get(f"/Items/{HEX(SECRET_SERIES)}/Images/Primary").status_code == 404
    with db_module.SessionLocal() as session:
        session.query(DeviceToken).filter_by(user_id=ALICE).delete()
        session.commit()
    assert jf.get(f"/Items/{HEX(SERIES)}/Images/Primary").status_code == 404


def test_a_grant_never_covers_derived_ids(jf: TestClient) -> None:
    get(jf, "/Items")
    for target in (TV, HEX(synthetic_id("person:anyone")), HEX(synthetic_id("channel:Youtube:Chan"))):
        assert jf.get(f"/Items/{target}/Images/Primary").status_code == 404, target


def test_a_user_avatar_is_an_unauthenticated_empty_404(jf: TestClient) -> None:
    for path in (f"/Users/{HEX(ALICE)}/Images/Primary", "/UserImage"):
        response = jf.get(path)
        assert (response.status_code, response.content) == (404, b""), path


# ---- Android TV: adjacentTo, people, repeated keys, device/mix probes --------------------------------------------

def test_adjacent_to_returns_the_neighbours_in_play_order(jf: TestClient) -> None:
    every = [item["Id"] for item in get(jf, f"/Shows/{HEX(SERIES)}/Episodes").json()["Items"]]
    middle = get(jf, f"/Shows/{HEX(SERIES)}/Episodes", adjacentTo=every[1]).json()
    assert [item["Id"] for item in middle["Items"]] == every[0:3] and middle["TotalRecordCount"] == 3
    first = get(jf, f"/Shows/{HEX(SERIES)}/Episodes", adjacentTo=every[0]).json()
    assert [item["Id"] for item in first["Items"]] == every[0:2]
    assert get(jf, f"/Shows/{HEX(SERIES)}/Episodes", adjacentTo=UNKNOWN).json() == EMPTY


def test_a_credited_person_opens_as_an_item(jf: TestClient) -> None:
    from app.models import MediaTitle

    person = synthetic_id("tmdb-person:9273")
    with db_module.SessionLocal() as session:
        title = session.get(MediaTitle, MOVIE)
        title.metadata_json = {**(title.metadata_json or {}), "people": [{"person_id": person, "name": "Amy Adams", "type": "Actor"}]}
        session.commit()
    for path in (f"/Items/{HEX(person)}", f"/Users/{HEX(ALICE)}/Items/{HEX(person)}"):
        dto = get(jf, path).json()
        assert (dto["Name"], dto["Type"], dto["Id"]) == ("Amy Adams", "Person", HEX(person)) and "UserData" in dto
    assert get(jf, f"/Items/{HEX(synthetic_id('tmdb-person:1'))}").status_code == 404  # credited by nothing she can see


def test_fields_accept_repeated_and_comma_joined_keys(jf: TestClient) -> None:
    for path in (f"/Shows/{HEX(SERIES)}/Seasons", f"/Items/{HEX(MOVIE)}/Similar"):
        for params in ([("fields", "Overview"), ("fields", "People")], [("fields", "Overview,People")]):
            assert jf.get(path, params=params, headers=mediabrowser(ALICE_TOKEN)).status_code == 200


def test_device_options_and_instant_mix_are_accepted(jf: TestClient) -> None:
    assert post(jf, "/Devices/Options?id=device-1", {"CustomName": "TV"}).status_code == 204
    assert get(jf, f"/Items/{HEX(MOVIE)}/InstantMix").json() == EMPTY
    assert get(jf, f"/Users/{HEX(ALICE)}/Items/{HEX(MOVIE)}/InstantMix").json() == EMPTY


def test_home_rows_do_not_fail_on_odd_data(jf: TestClient) -> None:
    for path in ("/UserViews", f"/Users/{HEX(ALICE)}/Views", "/UserItems/Resume", "/Shows/NextUp", "/Items/Latest", "/Items/Suggestions"):
        assert get(jf, path, Limit=0, StartIndex=999, IncludeItemTypes="Bogus", ParentId="not-an-id").status_code < 500, path
    assert get(jf, "/UserViews").status_code == 200


def test_policy_and_system_info_fields_clients_deserialize(jf: TestClient) -> None:
    assert get(jf, f"/Users/{HEX(ALICE)}").json()["Policy"]["EnableLyricManagement"] is False
    assert get(jf, "/System/Info/Public", token=None).json()["OperatingSystem"] == "Linux"
    assert isinstance(get(jf, "/System/Info").json()["WebSocketPortNumber"], int)


# ---- security round: grants, caching, socket caps, port --------------------------------------------------------------

def tunnel_request(visitor: str, hop: str | None) -> Request:
    headers = [(b"host", b"lumina.example.com"), (b"cf-connecting-ip", visitor.encode())]
    if hop is not None:
        headers.append((b"x-forwarded-for", hop.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "raw_path": b"/", "query_string": b"", "headers": headers,
                    "client": ("172.18.0.2", 1234), "server": ("172.18.0.2", 80), "scheme": "http"})


def test_a_forged_visitor_header_cannot_borrow_a_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_ips", "172.18.0.2")
    monkeypatch.setattr(public_address, "host", lambda: "lumina.example.com")
    earned = grant_address(tunnel_request("203.0.113.7", "172.18.0.9"))  # cloudflared is the hop that reached the proxy
    assert earned == grant_address(tunnel_request("203.0.113.7", "172.18.0.9"))
    assert earned != grant_address(tunnel_request("203.0.113.7", "192.168.1.66"))  # a LAN host sending the same header
    assert earned != grant_address(tunnel_request("203.0.113.8", "172.18.0.9"))


def test_art_a_token_or_grant_earned_is_never_public(jf: TestClient) -> None:
    path = f"/Items/{HEX(SERIES)}/Images/Primary"
    tag = get(jf, f"/Items/{HEX(SERIES)}").json()["ImageTags"]["Primary"]
    assert get(jf, path).headers["cache-control"].startswith("private")
    assert jf.get(path).headers["cache-control"].startswith("private")  # via the grant
    assert jf.get(path, params={"tag": tag}).headers["cache-control"].startswith("public")


def test_sockets_are_capped_per_token(jf: TestClient) -> None:
    from contextlib import ExitStack

    with ExitStack() as stack:
        for _ in range(4):
            stack.enter_context(jf.websocket_connect("ws://localhost/socket", params={"api_key": ALICE_TOKEN})).receive_json()
        with pytest.raises(WebSocketDisconnect) as extra, jf.websocket_connect("ws://localhost/socket", params={"api_key": ALICE_TOKEN}):
            pass
        assert extra.value.code == 1013
        with jf.websocket_connect("ws://localhost/socket", params={"api_key": BOB_TOKEN}) as other:  # another token is unaffected
            assert other.receive_json()["MessageType"] == "ForceKeepAlive"


def test_websocket_port_follows_the_address_the_caller_used(jf: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public_address, "_origin", "https://lumina.example.com")
    assert jf.get("https://lumina.example.com/System/Info", headers=mediabrowser(ALICE_TOKEN)).json()["WebSocketPortNumber"] == 443
    monkeypatch.setattr(public_address, "_origin", "https://lumina.example.com:8443")
    assert jf.get("https://lumina.example.com/System/Info", headers=mediabrowser(ALICE_TOKEN)).json()["WebSocketPortNumber"] == 8443


# ---- MediaSourceId conventions ---------------------------------------------------------------------------------------

def test_roku_shaped_playback_info_with_the_items_own_id_as_source(jf: TestClient, no_ffprobe: None) -> None:
    for item in (MOVIE, S1E1):
        response = jf.post(f"/Items/{HEX(item)}/PlaybackInfo", headers=mediabrowser(ALICE_TOKEN), json={"DeviceProfile": {}},
                           params={"MediaSourceId": HEX(item), "UserId": HEX(ALICE), "IsPlayback": "true", "StartTimeTicks": 0})
        assert response.status_code == 200 and response.json()["MediaSources"], item


def test_the_items_own_id_selects_the_default_version_for_stream_and_progress(jf: TestClient) -> None:
    ranged = jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"Static": "true", "MediaSourceId": HEX(MOVIE), "api_key": ALICE_TOKEN},
                    headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206
    report = {"ItemId": HEX(S1E1), "MediaSourceId": HEX(S1E1), "PositionTicks": 600 * 10_000_000}
    assert post(jf, "/Sessions/Playing/Progress", report).status_code == 204
    assert jf.get(f"/Items/{HEX(S1E1)}", headers=mediabrowser(ALICE_TOKEN)).json()["UserData"]["PlaybackPositionTicks"] == report["PositionTicks"]


def test_a_blank_body_source_id_means_none(jf: TestClient, no_ffprobe: None) -> None:
    assert len(post(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo", {"MediaSourceId": ""}).json()["MediaSources"]) == 2


def test_a_camel_case_body_is_read_like_pascal_case(jf: TestClient, no_ffprobe: None) -> None:
    only_h264 = {"Name": "t", "DirectPlayProfiles": [{"Type": "Video", "Container": "avi", "VideoCodec": "mpeg2video"}], "TranscodingProfiles": [
        {"Type": "Video", "Container": "ts", "Protocol": "hls", "VideoCodec": "h264", "AudioCodec": "aac"}]}
    body = post(jf, f"/Items/{HEX(MOVIE)}/PlaybackInfo", {"mediaSourceId": HEX(MOVIE_4K), "deviceProfile": only_h264}).json()
    assert [source["Id"] for source in body["MediaSources"]] == [HEX(MOVIE_4K)]
    assert body["MediaSources"][0]["SupportsDirectPlay"] is False and "TranscodingUrl" in body["MediaSources"][0]


def test_explicit_stream_version_does_not_load_title_page_data(jf: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.titles import TitleService

    def unused_page(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("a selected file does not need progress, favorites or artwork")

    monkeypatch.setattr(TitleService, "load", unused_page)
    monkeypatch.setattr(TitleService, "get_visible", unused_page)
    response = jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"MediaSourceId": HEX(MOVIE_4K), "api_key": ALICE_TOKEN},
                      headers={"Range": "bytes=0-9"})
    assert (response.status_code, response.content) == (206, b"\0" * 10)


@pytest.mark.parametrize("source", [UNKNOWN, HEX(S1E1), HEX(FILE[S1E1]), HEX(SECRET_SERIES), HEX(TRAILER)])
def test_explicit_stream_version_must_belong_to_the_visible_leaf(jf: TestClient, source: str) -> None:
    response = jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"MediaSourceId": source, "api_key": ALICE_TOKEN})
    assert (response.status_code, response.content) == (404, b"")


def test_named_stream_version_does_not_borrow_its_parents_visibility(jf: TestClient) -> None:
    with db_module.SessionLocal() as db:
        version = db.get(LibraryItem, MOVIE_4K)
        version.visibility, version.user_id = "private", BOB
        db.commit()
    path = f"/Videos/{HEX(MOVIE)}/stream"
    params = {"MediaSourceId": HEX(MOVIE_4K)}
    assert jf.get(path, params=params, headers=mediabrowser(ALICE_TOKEN)).status_code == 404
    assert jf.get(path, params=params, headers=mediabrowser(BOB_TOKEN)).status_code == 200


def test_connected_app_auth_loads_the_token_and_member_together(jf: TestClient) -> None:
    from sqlalchemy import event

    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):  # noqa: ANN001
        if "from device_tokens" in statement.lower() or "from users" in statement.lower():
            statements.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", capture)
    try:
        assert get(jf, "/System/Info").status_code == 200
    finally:
        event.remove(db_module.engine, "before_cursor_execute", capture)
    assert len(statements) == 1, "the token and its current member need one live query"
    assert "join users" in statements[0].lower()


def test_selected_stream_loads_its_registered_file_with_the_visible_version(jf: TestClient) -> None:
    from sqlalchemy import event

    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):  # noqa: ANN001
        if "from library_items" in statement.lower() or "from media_artifacts" in statement.lower():
            statements.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", capture)
    try:
        response = jf.get(f"/Videos/{HEX(MOVIE)}/stream", params={"MediaSourceId": HEX(MOVIE_4K), "api_key": ALICE_TOKEN},
                          headers={"Range": "bytes=0-9"})
        assert (response.status_code, len(response.content)) == (206, 10)
    finally:
        event.remove(db_module.engine, "before_cursor_execute", capture)
    assert len(statements) == 1, "a selected stream resolves visibility and its registered file together"
    assert "join media_artifacts" in statements[0].lower()
    assert "join storage_roots" in statements[0].lower()


@pytest.mark.parametrize("unavailable", ["quarantined", "disabled", "unlinked", "symlink"])
def test_selected_stream_rechecks_registered_file_availability(jf: TestClient, unavailable: str, tmp_path: Path) -> None:
    from app.models import LibraryItemArtifact, MediaArtifact, StorageRoot

    path = f"/Videos/{HEX(MOVIE)}/stream"
    params = {"MediaSourceId": HEX(MOVIE_4K), "api_key": ALICE_TOKEN}
    assert jf.get(path, params=params).status_code == 200
    with db_module.SessionLocal() as db:
        link = db.get(LibraryItemArtifact, MOVIE_4K)
        artifact = db.get(MediaArtifact, link.artifact_id)
        root = db.get(StorageRoot, artifact.root_id)
        if unavailable == "quarantined":
            artifact.lifecycle = "quarantined"
        elif unavailable == "disabled":
            root.enabled = False
        elif unavailable == "unlinked":
            db.delete(link)
        else:
            media = Path(root.path) / artifact.relative_path
            replacement = tmp_path / "replacement.mkv"
            replacement.write_bytes(media.read_bytes())
            media.unlink()
            media.symlink_to(replacement)
        db.commit()
    response = jf.get(path, params=params)
    assert (response.status_code, response.content) == (404, b"")
