"""Jellyfin DTO fields that spec-strict and unguarded clients rely on being present."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import DeviceToken, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle
from app.services import jellyfin_playback
from app.services.media_artifacts import MediaArtifactService
from app.services.jellyfin_playback import transcoding_fields
from app.services.media_titles import jellyfin_id
from app.services.playback_decision import decide
from title_support import ALICE, ALICE_TOKEN, BOB, BOXSET, MOVIE, MOVIE_1080, MOVIE_4K, S1E1, SERIES, jellyfin_household, mediabrowser

HEADERS = mediabrowser(ALICE_TOKEN)
HEX = jellyfin_id
DETAIL_ARRAYS = ("People", "Taglines", "Tags", "Chapters")


@pytest.fixture
def jf(tmp_path):  # noqa: ANN001, ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def get(client: TestClient, path: str, **params):  # noqa: ANN201
    response = client.get(path, params=params, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_folders_and_views_send_media_type_unknown(jf: TestClient) -> None:
    for view in get(jf, "/UserViews")["Items"]:
        assert (view["MediaType"], view["CanDelete"], view["CanDownload"]) == ("Unknown", False, False)
    series, boxset = get(jf, f"/Items/{HEX(SERIES)}"), get(jf, f"/Items/{HEX(BOXSET)}")
    assert series["MediaType"] == boxset["MediaType"] == "Unknown"
    assert series["CanDownload"] is boxset["CanDownload"] is False
    movie = get(jf, f"/Items/{HEX(MOVIE)}")
    assert (movie["MediaType"], movie["CanDelete"], movie["CanDownload"]) == ("Video", False, True)


def test_detail_items_carry_the_arrays_clients_index(jf: TestClient) -> None:
    for item_id in (MOVIE, SERIES):
        detail = get(jf, f"/Items/{HEX(item_id)}")
        for key in DETAIL_ARRAYS:
            assert isinstance(detail[key], list), key
        assert isinstance(detail["ImageTags"], dict) and isinstance(detail["BackdropImageTags"], list)
        assert isinstance(detail["UserData"], dict)
    movie = get(jf, f"/Items/{HEX(MOVIE)}")
    assert isinstance(movie["MediaSources"], list) and isinstance(movie["MediaStreams"], list)


@pytest.mark.parametrize("item_id", [MOVIE, S1E1, MOVIE_4K])
def test_roku_can_build_video_options_from_item_details(jf: TestClient, item_id: str) -> None:
    detail = get(jf, f"/Items/{HEX(item_id)}")
    # Roku 3.2.4's detail screen lowercases this field before launching playback.
    assert all(source.get("VideoType") == "VideoFile" for source in detail["MediaSources"])
    assert detail["MediaSourceCount"] == len(detail["MediaSources"])


@pytest.mark.parametrize("item_id", [MOVIE, S1E1])
def test_android_tv_external_player_finds_and_streams_the_primary_source(jf: TestClient, item_id: str) -> None:
    detail = get(jf, f"/Items/{HEX(item_id)}")
    # Android TV closes its external player when no source has the item's ID.
    primary = next((source for source in detail["MediaSources"] if source["Id"] == detail["Id"]), None)
    assert primary is not None
    assert primary is detail["MediaSources"][0]
    response = jf.get(f"/Videos/{detail['Id']}/stream", params={"MediaSourceId": primary["Id"]},
                      headers=HEADERS | {"Range": "bytes=0-9"})
    assert response.status_code == 206 and response.content == b"\0" * 10


def test_android_tv_direct_play_retry_gets_a_stream_url(jf: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jellyfin_playback, "media_tool", lambda *_args: None)
    profile = {"DirectPlayProfiles": [{"Type": "Video", "Container": "mkv", "VideoCodec": "hevc", "AudioCodec": "truehd"}]}
    response = jf.post(f"/Items/{HEX(MOVIE)}/PlaybackInfo", headers=HEADERS,
                       json={"MediaSourceId": HEX(MOVIE_4K), "DeviceProfile": profile, "EnableDirectPlay": False})
    assert response.status_code == 200
    source = response.json()["MediaSources"][0]
    assert source["SupportsDirectPlay"] is False
    # Android TV requires a URL for every advertised direct-stream/transcode path.
    assert source["SupportsDirectStream"] or source["SupportsTranscoding"]
    assert source["TranscodingUrl"].startswith("/videos/")


@pytest.mark.parametrize("client", ["Jellyfin Roku", "Infuse-Direct"])
@pytest.mark.parametrize("audio_codec", ["ac3", "eac3"])
@pytest.mark.parametrize("direct", [True, False])
def test_roku_dolby_hls_fallback_keeps_direct_play_and_other_clients(
    jf: TestClient, monkeypatch: pytest.MonkeyPatch, client: str, audio_codec: str, direct: bool,
) -> None:
    from sqlalchemy import select
    monkeypatch.setattr(jellyfin_playback, "media_tool", lambda *_args: None)
    with db_module.SessionLocal() as db:
        token = db.scalar(select(DeviceToken).where(DeviceToken.user_id == ALICE))
        token.client = client
        device = token.id
        artifact = db.get(MediaArtifact, db.get(LibraryItemArtifact, MOVIE_4K).artifact_id)
        facts = deepcopy(artifact.probe)
        facts.update(video_codec="h264", audio_codec=audio_codec, width=1280, height=720)
        facts["streams"][0].update(codec="h264", width=1280, height=720, pix_fmt="yuv420p", color_transfer="bt709")
        facts["streams"][1].update(codec=audio_codec, channels=6)
        artifact.probe = facts
        db.commit()
    profile = {"DirectPlayProfiles": [{"Type": "Video", "Container": "mkv", "VideoCodec": "h264", "AudioCodec": "ac3,eac3"}],
               "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "ts", "VideoCodec": "h264", "AudioCodec": "ac3,eac3"}]}
    response = jf.post(f"/Items/{HEX(MOVIE)}/PlaybackInfo", headers={k: v.replace("Infuse-Direct", client) for k, v in HEADERS.items()},
                       json={"MediaSourceId": HEX(MOVIE_4K), "DeviceProfile": profile, "EnableDirectPlay": direct})
    assert response.status_code == 200
    info = response.json()
    source = next(s for s in info["MediaSources"] if s["Id"] == HEX(MOVIE_4K))
    assert source["SupportsDirectPlay"] is direct
    pending = jellyfin_playback.pending_transcodes.get(info["PlaySessionId"], MOVIE_4K, ALICE, device)
    if direct:
        assert pending is None and not source["SupportsTranscoding"]
    else:
        assert pending is not None and source["SupportsTranscoding"]
        decision = decide(pending.caps, facts)
        assert decision.video == "copy"
        assert decision.audio == ("encode" if client == "Jellyfin Roku" else "copy")
        assert decision.audio_channels == (2 if client == "Jellyfin Roku" else None)


@pytest.mark.parametrize("preferred", [MOVIE_1080, MOVIE_4K])
def test_primary_source_alias_keeps_version_resume_and_visibility_scoping(jf: TestClient, preferred: str) -> None:
    with db_module.SessionLocal() as db:
        for item_id, marker in ((MOVIE_1080, b"1"), (MOVIE_4K, b"4")):
            path, _root = MediaArtifactService(db).locate(db.get(LibraryItem, item_id))
            path.write_bytes(marker * 4096)
    report = jf.post("/Sessions/Playing/Progress", headers=HEADERS,
                     json={"ItemId": HEX(MOVIE), "MediaSourceId": HEX(preferred), "PositionTicks": 600 * 10_000_000})
    assert report.status_code == 204
    detail = get(jf, f"/Items/{HEX(MOVIE)}")
    assert detail["MediaSources"][0]["Id"] == detail["Id"]
    assert detail["UserData"]["PlaybackPositionTicks"] == 600 * 10_000_000
    url = f"/Videos/{detail['Id']}/stream"
    response = jf.get(url, headers=HEADERS | {"Range": "bytes=0-9"}, params={"MediaSourceId": detail["Id"]})
    marker = b"1" if preferred == MOVIE_1080 else b"4"
    assert response.status_code == 206 and response.content == marker * 10
    with db_module.SessionLocal() as db:
        item = db.get(LibraryItem, preferred)
        item.user_id, item.visibility = BOB, "private"
        db.commit()
    # The same alias now resolves to the remaining visible version; an explicit
    # request for the newly hidden file cannot borrow the alias's authorization.
    visible = get(jf, f"/Items/{HEX(MOVIE)}")
    assert visible["MediaSourceCount"] == 1 and visible["MediaSources"][0]["Id"] == detail["Id"]
    response = jf.get(url, headers=HEADERS | {"Range": "bytes=0-9"}, params={"MediaSourceId": detail["Id"]})
    assert response.status_code == 206 and response.content == (b"4" if marker == b"1" else b"1") * 10
    assert jf.get(url, headers=HEADERS, params={"MediaSourceId": HEX(preferred)}).status_code == 404


def test_every_stream_and_source_sends_the_non_nullable_fields(jf: TestClient) -> None:
    source = get(jf, f"/Items/{HEX(MOVIE)}")["MediaSources"][1]
    assert source["Id"] == HEX(MOVIE_4K) and source["UseMostCompatibleTranscodingProfile"] is False
    for stream in source["MediaStreams"]:
        assert (stream["AudioSpatialFormat"], stream["IsOriginal"]) == ("None", False)
        assert stream["VideoRange"] and stream["VideoRangeType"]
        if stream["Type"] != "Video":
            assert (stream["VideoRange"], stream["VideoRangeType"]) == ("Unknown", "Unknown")
        if stream["Type"] == "Subtitle":
            assert stream["DeliveryMethod"] == ("External" if stream["IsExternal"] else "Embed")


def test_an_unavailable_decision_offers_no_transcode_and_keeps_direct_play(jf: TestClient) -> None:
    decision = SimpleNamespace(mode="unavailable", height=None, reasons=())
    fields = transcoding_fields(decision, item_id="x", play_session_id="p", api_key="k", audio_index=None, subtitle_index=None)
    assert fields == {"SupportsTranscoding": False}  # Dolby Vision profile 5 files still direct play


def test_search_hints_and_facets_satisfy_strict_decoders(jf: TestClient) -> None:
    hints = get(jf, "/Search/Hints", SearchTerm="pil")["SearchHints"]
    assert hints and all(hint["Artists"] == [] for hint in hints)
    for path in ("/Genres", "/Studios", "/Persons"):
        for facet in get(jf, path)["Items"]:
            assert facet["UserData"]["Key"] and facet["UserData"]["ItemId"] == facet["Id"]


def test_an_episode_without_a_season_is_not_served_as_an_episode(jf: TestClient) -> None:
    with db_module.SessionLocal() as db:
        db.get(MediaTitle, S1E1).parent_id = None
        db.commit()
    orphan = get(jf, f"/Items/{HEX(S1E1)}")
    assert orphan["Type"] == "Video" and "SeriesId" not in orphan and orphan["MediaType"] == "Video"
    assert get(jf, f"/Items/{HEX(MOVIE)}")["Type"] == "Movie"
