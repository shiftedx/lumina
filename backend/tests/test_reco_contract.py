"""Recommendations wire contract: the fields added to existing
shapes exist on both sides, and the frozen value types keep their specified numbers. New media_schemas models and Literals are
checked field for field by test_media_contract.py."""
from __future__ import annotations

import re
import typing
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app import media_schemas, schemas
from app.routers.admin_diagnostics import DiagnosticsResponse
from app.services import reco
from test_media_contract import TS, _ts_fields


def test_existing_shapes_carry_the_recommendation_fields_on_both_sides() -> None:
    ts = _ts_fields()
    extended = {
        "YouTubeSearchResult": (schemas.PopularItemResponse, {"reco"}),
        "PopularSnapshot": (schemas.PopularSnapshotResponse, {"for_you", "category_order"}),
        "UpNextRequest": (schemas.UpNextRequest, {"channel_id", "channel_url"}),
        "TitleSummary": (media_schemas.TitleSummary, {"reco"}),
        "AdminDiagnostics": (DiagnosticsResponse, {"recommendations"}),
    }
    for ts_name, (model, fields) in extended.items():
        assert fields <= set(model.model_fields), model.__name__
        assert fields <= ts[ts_name], ts_name


@pytest.mark.parametrize(("ts_name", "model"), [
    ("SuppressRecommendationInput", schemas.SuppressRecommendationRequest),
    ("Suppression", schemas.SuppressionResponse),
    ("SuppressionList", schemas.SuppressionListResponse),
])
def test_suppression_shapes_match_field_for_field(ts_name: str, model: type) -> None:
    assert set(model.model_fields) == _ts_fields()[ts_name]


def test_the_suppression_scopes_match() -> None:
    match = re.search(r"^export type SuppressionScope =(.*?);$", TS, re.M | re.S)
    assert match, "types.ts lacks `export type SuppressionScope`"
    scopes = set(re.findall(r"'([^']*)'", match[1]))
    assert scopes == set(typing.get_args(schemas.SuppressRecommendationRequest.model_fields["scope"].annotation))
    assert scopes == set(typing.get_args(schemas.SuppressionResponse.model_fields["scope"].annotation)) == {"item", "channel", "fewer", "title"}


def test_a_remote_checkpoint_may_carry_its_channel_on_both_sides() -> None:
    assert {"channel_id", "channel_url"} <= set(schemas.RemotePlaybackProgressUpdateRequest.model_fields)
    payload = re.search(r"export function updateRemotePlaybackProgress\((.*?)\): Promise", TS, re.S)
    assert payload and "channel_id?: string | null;" in payload[1] and "channel_url?: string | null;" in payload[1]


KEY = "a" * 64
LIST = "0123456789abcdef"


def test_event_batches_accept_only_what_the_client_may_send() -> None:
    batch = media_schemas.RecoEventBatch.model_validate({"events": [{"kind": "impression", "list_id": LIST, "key": KEY, "age_ms": 1200}]})
    assert batch.csrf is None and batch.events[0].kind == "impression"
    title_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert media_schemas.RecoEventIn(kind="open", list_id=LIST, key=title_id, age_ms=0).key == title_id
    bad = [
        {"kind": "play", "list_id": LIST, "key": KEY, "age_ms": 0},  # plays are the server's
        {"kind": "open", "list_id": "0123456789ABCDEF", "key": KEY, "age_ms": 0},
        {"kind": "open", "list_id": LIST, "key": "https://www.youtube.com/watch?v=x", "age_ms": 0},  # never a URL
        {"kind": "open", "list_id": LIST, "key": KEY, "age_ms": 900_001},
        {"kind": "open", "list_id": LIST, "key": KEY, "age_ms": 0, "surface": "home_picked"},  # the server fills context
    ]
    for event in bad:
        with pytest.raises(ValidationError):
            media_schemas.RecoEventIn.model_validate(event)
    with pytest.raises(ValidationError):
        media_schemas.RecoEventBatch.model_validate({"events": [{"kind": "open", "list_id": LIST, "key": KEY, "age_ms": 0}] * 201})


def test_requests_validate_channel_ids_and_list_ids() -> None:
    assert schemas.UpNextRequest(source_url="https://www.youtube.com/watch?v=x", channel_id="UC" + "a" * 22).channel_id
    for bad in ("UC" + "a" * 21, "@handle", "UC" + "a" * 21 + "!"):
        with pytest.raises(ValidationError):
            schemas.UpNextRequest(source_url="https://www.youtube.com/watch?v=x", channel_id=bad)
    request = schemas.SuppressRecommendationRequest(scope="fewer", uploader="Chan", list_id=LIST, key=KEY)
    assert request.scope == "fewer"
    with pytest.raises(ValidationError):
        schemas.SuppressRecommendationRequest(scope="mute", uploader="Chan")
    with pytest.raises(ValidationError):
        media_schemas.RecoAnnotation(list_id=LIST, key=KEY, position=0, slot="exploit", reason_code="finished", reason="x" * 81)


def test_exploration_takes_the_tail_of_every_twelve() -> None:
    """Ruling D6: 2 of 12 on Home Picked, Home Recommended and Explore For you; 1 of 12 on anchored lists; 0 on rails."""
    assert reco.explore_positions(20, reco.SURFACE_EXPLORE["home_picked"]) == {11, 12}
    assert reco.explore_positions(24, reco.SURFACE_EXPLORE["explore_for_you"]) == {11, 12, 23, 24}
    assert reco.explore_positions(12, reco.SURFACE_EXPLORE["up_next"]) == {12}
    assert reco.explore_positions(10, reco.SURFACE_EXPLORE["title_similar"]) == set()
    assert reco.explore_positions(24, reco.SURFACE_EXPLORE["explore_popular"]) == set()
    assert set(reco.SURFACE_EXPLORE) == set(reco.SURFACE_K) == set(typing.get_args(media_schemas.RecoSurface))


def test_the_constants_are_the_specs() -> None:
    c = reco.CONSTANTS
    assert (c.w_follow, c.w_completion, c.affinity_scale, c.w_affinity, c.w_topic, c.w_interest) == (3.0, 1.5, 3.0, 2.0, 2.0, 0.5)
    assert (c.remote_fresh_boost, c.remote_fresh_tau_days, c.channel_floor, c.calibration_lambda) == (0.6, 7.0, 0.4, 0.3)
    assert (c.dpp_alpha, c.dpp_sigma, c.dpp_window, c.explore_top_m, c.explore_temperature) == (0.9, 0.7, 12, 40, 0.3)
    assert (c.fewer_floor, c.fewer_step, c.fewer_step_days, c.fatigue_exclude_count) == (0.2, 0.2, 35.0, 4)
    with pytest.raises(AttributeError):
        c.w_follow = 1.0  # type: ignore[misc]


def test_value_types_hash_without_their_vectors() -> None:
    from array import array

    now = datetime(2026, 9, 30, tzinfo=UTC)
    one = reco.Candidate(key=KEY, target_kind="remote", title="t", channel_key=None, channel_name=None, tokens=frozenset({"t"}),
                         published_at=now, sources=reco.SOURCE_FOLLOW, vector=array("f", [1.0]))
    two = reco.Candidate(key=KEY, target_kind="remote", title="t", channel_key=None, channel_name=None, tokens=frozenset({"t"}),
                         published_at=now, sources=reco.SOURCE_FOLLOW, vector=array("f", [0.0]))
    assert one == two and hash(one) == hash(two)
    assert reco.SurfaceContext(surface="up_next", k=12, local_date=date(2026, 9, 30), anchor=one).context_key == "-"


def test_the_kill_switch_reads_the_admin_feature_list(db_factory) -> None:  # noqa: ANN001
    from app.services.yt_dlp_service import YtDlpService

    with db_factory() as db:
        assert reco.enabled(db) is True  # a fresh database serves default settings
        YtDlpService(db).ensure_app_settings().ai_features_disabled = ["semantic_search"]
        db.flush()
        assert reco.enabled(db) is True
        YtDlpService(db).get_app_settings().ai_features_disabled = ["semantic_search", reco.FEATURE_KEY]
        db.flush()
        assert reco.enabled(db) is False
