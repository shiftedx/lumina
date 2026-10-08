"""Jellyfin DTO fields that spec-strict and unguarded clients rely on being present."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import MediaTitle
from app.services.jellyfin_playback import transcoding_fields
from app.services.media_titles import jellyfin_id
from title_support import ALICE_TOKEN, BOXSET, MOVIE, MOVIE_4K, S1E1, SERIES, jellyfin_household, mediabrowser

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
