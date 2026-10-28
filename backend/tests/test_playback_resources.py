from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.services.playback_decision import BROWSER_DEFAULT, Limits, decide
from app.services.playback_resources import IDLE_SECONDS, PlaybackResources
from app.services.resource_policy import HouseholdResources, ResourceAllowances
from tests.test_jellyfin_dto_compat import HEADERS, HEX, MOVIE_4K, jf
from title_support import ALICE
from tests.test_v1_artifacts import household


def _facts(*, bitrate: int | None = 100_000_000) -> dict:
    return {"container": "mov,mp4,m4a,3gp,3g2,mj2", "bit_rate": bitrate, "streams": [
        {"index": 0, "type": "video", "codec": "h264", "height": 2160, "width": 3840, "pix_fmt": "yuv420p", "default": True},
        {"index": 1, "type": "audio", "codec": "aac", "channels": 2, "default": True},
    ]}


def _policy(upload: int = 100_000_000) -> HouseholdResources:
    # Resource-admission exercises intentionally use explicit quality caps. The
    # durable household default remains uncapped per stream/member.
    return HouseholdResources(
        upload_bps=upload,
        defaults=ResourceAllowances(
            remote_stream_bps=8_000_000,
            remote_member_bps=10_000_000,
            playback_sessions=3,
            video_encodes=3,
        ),
    )


def _protected(monkeypatch: pytest.MonkeyPatch, policy: HouseholdResources) -> PlaybackResources:
    import app.services.playback_resources as module

    monkeypatch.setattr(module, "household", lambda db: policy)
    monkeypatch.setattr(module, "effective", lambda db, user_id: policy.defaults)
    monkeypatch.setattr(module, "remote", lambda request, configured: True)
    monkeypatch.setattr(module, "runtime_state", lambda: (True, 1))
    return PlaybackResources()


def test_remote_100mb_source_is_admitted_as_a_lower_viable_rendition(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy())

    admission = resources.admit(None, SimpleNamespace(id="member"), SimpleNamespace(), "movie", "tv", "hls", _facts(), BROWSER_DEFAULT)

    assert admission.allocation_bps == 8_000_000
    assert (admission.decision.mode, admission.decision.video, admission.decision.height, admission.decision.bitrate) == ("transcode", "encode", 720, 4_000_000)


def test_preview_of_an_existing_logical_playback_keeps_its_full_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    user, request = SimpleNamespace(id="member"), SimpleNamespace()
    resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)

    preview = resources.preview(None, user, request, "movie", "tv", _facts(), BROWSER_DEFAULT, logical_id="web:movie")

    assert (preview.allocation_bps, preview.decision.mode, preview.decision.bitrate) == (8_000_000, "transcode", 4_000_000)


def test_preview_of_an_existing_direct_playback_keeps_its_full_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    user, request = SimpleNamespace(id="member"), SimpleNamespace()
    resources.admit(None, user, request, "movie", "tv", "direct", _facts(), BROWSER_DEFAULT)

    preview = resources.preview(None, user, request, "movie", "tv", _facts(), BROWSER_DEFAULT, logical_id=("web:movie", "direct"))

    assert preview.allocation_bps == 8_000_000


def test_repeat_admission_keeps_its_reservation_and_exhausted_upload_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    first = resources.admit(None, SimpleNamespace(id="one"), SimpleNamespace(), "movie", "tv", "hls", _facts(), BROWSER_DEFAULT)

    changed = resources.admit(None, SimpleNamespace(id="one"), SimpleNamespace(), "movie", "tv", "hls", _facts(), BROWSER_DEFAULT, max_height=480)
    assert (changed.allocation_bps, changed.decision.height) == (first.allocation_bps, 480)
    resources.release(first)  # a superseded ffmpeg process must not release changed's reservation
    with pytest.raises(HTTPException) as exc:
        resources.admit(None, SimpleNamespace(id="two"), SimpleNamespace(), "other", "other-tv", "hls", _facts(), BROWSER_DEFAULT)
    assert exc.value.status_code == 429


def test_protected_unknown_bitrate_audio_source_is_not_direct_played() -> None:
    facts = {"container": "mp3", "bit_rate": None, "streams": [{"index": 0, "type": "audio", "codec": "mp3", "channels": 2, "default": True}]}

    decision = decide(BROWSER_DEFAULT, facts, Limits(max_bitrate=7_500_000, require_known_bitrate=True))

    assert decision.mode == "unavailable"


def test_constrained_remux_reencodes_lossless_audio_inside_the_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    facts = _facts(bitrate=7_000_000)
    facts["container"] = "matroska,webm"
    facts["streams"][1].update(codec="truehd", channels=8)

    admission = resources.admit(None, SimpleNamespace(id="member"), SimpleNamespace(), "movie", "tv", "hls", facts, BROWSER_DEFAULT)

    assert admission.allocation_bps == 8_000_000
    assert (admission.decision.video, admission.decision.audio) == ("copy", "encode")


def test_same_rendition_seek_keeps_the_new_owner_and_failed_replacement_restores_old_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    user, request = SimpleNamespace(id="one"), SimpleNamespace()
    first = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)
    seek = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)

    assert seek.token != first.token and seek.allocation_bps == first.allocation_bps
    resources.release(first)  # the old runtime ends after its seek replacement was admitted
    with pytest.raises(HTTPException):
        resources.admit(None, SimpleNamespace(id="two"), request, "other", "other-tv", "web:other", _facts(), BROWSER_DEFAULT)
    resources.restore(seek)  # replacement startup failed; old runtime is still entitled to its allocation
    assert resources._items[seek.key].admission.token == first.token


def test_three_overlapping_replacement_failures_restore_each_predecessor(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    user, request = SimpleNamespace(id="one"), SimpleNamespace()
    first = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)
    second = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)
    third = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)

    resources.restore(third)
    resources.restore(second)

    assert resources._items[first.key].admission.token == first.token


def test_noncurrent_failure_is_discarded_before_the_current_replacement_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    user, request = SimpleNamespace(id="one"), SimpleNamespace()
    first = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)
    second = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)
    third = resources.admit(None, user, request, "movie", "tv", "web:movie", _facts(), BROWSER_DEFAULT)

    resources.restore(second)
    resources.restore(third)

    assert resources._items[first.key].admission.token == first.token


def test_successful_direct_and_seek_retries_discard_predecessor_history(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    user, request = SimpleNamespace(id="one"), SimpleNamespace()
    direct_facts = _facts(bitrate=2_000_000)
    direct_facts["streams"][0].update(height=720, width=1280)
    for _ in range(3):
        resources.admit_direct(None, user, request, "movie", "tv", direct_facts, BROWSER_DEFAULT)
    direct = resources._items[("one", "tv", "movie", "direct")]
    assert not direct.previous
    resources.release_direct("one", "movie", "tv")
    for _ in range(3):
        admission = resources.admit(None, user, request, "other", "tv", "web:other", _facts(), BROWSER_DEFAULT)
        resources.commit(admission)
    assert not resources._items[("one", "tv", "other", "web:other")].previous


def test_simultaneous_starts_and_direct_stop_or_expiry_free_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _protected(monkeypatch, _policy(upload=8_000_000))

    def start(member: str) -> int:
        try:
            resources.admit(None, SimpleNamespace(id=member), SimpleNamespace(), member, "tv", "direct", _facts(), BROWSER_DEFAULT)
            return 201
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(start, ("one", "two"))) == [201, 429]
    resources.release_direct("one", "one", "tv")
    assert start("two") == 201
    resources._items[("two", "tv", "two", "direct")].touched = time.monotonic() - IDLE_SECONDS - 1
    resources.reap()
    assert start("three") == 201


def test_direct_progress_heartbeat_extends_the_resource_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.playback_resources as module

    now = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    resources = _protected(monkeypatch, _policy(upload=8_000_000))
    resources.admit(None, SimpleNamespace(id="one"), SimpleNamespace(), "movie", "web", "direct", _facts(), BROWSER_DEFAULT)
    now[0] = 599.0
    resources.touch_direct("one", "movie", "web")
    now[0] = 601.0
    resources.reap()

    assert ("one", "web", "movie", "direct") in resources._items


def test_protected_web_options_refuse_original_but_head_does_not_reserve(
    household: None, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: F811
    """The advertised rendition and raw-media guard agree at the public HTTP boundary."""
    from app import db as db_module
    from app.config import settings
    from app.services import resource_policy
    from app.services.media_probe import MediaProbeService
    from app.services.playback_resources import playback_resources
    from app.services.yt_dlp_service import YtDlpService
    from tests.test_v1_artifacts import _client, _download

    item_id = _download("alice", settings.library_root / "alice" / "large.mp4")
    policy = _policy(upload=20_000_000)
    with db_module.SessionLocal() as db:
        YtDlpService(db).ensure_app_settings().resource_policy = policy.model_dump(mode="json")
        db.commit()
    resource_policy.configure_runtime(policy)
    monkeypatch.setattr(MediaProbeService, "facts", lambda *_args: _facts())
    playback_resources._items.clear()
    try:
        with _client("alice") as client:
            assert client.get(f"/api/library/{item_id}/playback-options").json()["mode"] == "transcode"
            head = client.head(f"/api/library/{item_id}/media")
            assert head.status_code == 200 and not playback_resources._items
            raw = client.get(f"/api/library/{item_id}/media")
        assert raw.status_code == 409
        assert raw.json()["detail"]["code"] == "resource_conversion_required"
    finally:
        playback_resources._items.clear()
        resource_policy.configure_runtime(HouseholdResources())


def test_protected_jellyfin_playback_info_advertises_hls_conversion(jf, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app import db as db_module
    from app.models import AppSettings
    from app.services import resource_policy
    from app.services.media_probe import MediaProbeService
    from app.services.playback_resources import playback_resources
    from app.services.yt_dlp_service import YtDlpService

    policy = _policy(upload=20_000_000)
    with db_module.SessionLocal() as db:
        YtDlpService(db).ensure_app_settings().resource_policy = policy.model_dump(mode="json")
        db.commit()
    resource_policy.configure_runtime(policy)
    monkeypatch.setattr(MediaProbeService, "facts", lambda *_args: _facts())
    playback_resources._items.clear()
    profile = {
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
        "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
    }
    try:
        response = jf.post(f"/Items/{HEX(MOVIE_4K)}/PlaybackInfo", headers=HEADERS, json={"MediaSourceId": HEX(MOVIE_4K), "DeviceProfile": profile})
        source = response.json()["MediaSources"][0]
        assert response.status_code == 200
        assert source["SupportsDirectPlay"] is False and source["SupportsTranscoding"] is True
    finally:
        playback_resources._items.clear()


def test_jellyfin_4k_direct_default_and_explicit_global_member_caps(
    jf, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    """An upload budget alone does not downscale direct 4K; explicit caps do."""
    from app import db as db_module
    from app.models import User
    from app.services import resource_policy
    from app.services.media_probe import MediaProbeService
    from app.services.playback_resources import playback_resources
    from app.services.yt_dlp_service import YtDlpService

    profile = {
        "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
        "TranscodingProfiles": [{"Type": "Video", "Protocol": "hls", "Container": "mp4", "VideoCodec": "h264", "AudioCodec": "aac"}],
    }
    body = {"MediaSourceId": HEX(MOVIE_4K), "DeviceProfile": profile}
    monkeypatch.setattr(MediaProbeService, "facts", lambda *_args: _facts())

    def source() -> dict:
        response = jf.post(f"/Items/{HEX(MOVIE_4K)}/PlaybackInfo", headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        return response.json()["MediaSources"][0]

    try:
        # An unconfigured household and an upload-only policy both retain a
        # compatible 4K original. The upload budget remains the aggregate cap.
        resource_policy.configure_runtime(HouseholdResources())
        assert source()["SupportsDirectPlay"] is True

        upload_only = HouseholdResources(upload_bps=200_000_000)
        with db_module.SessionLocal() as db:
            YtDlpService(db).ensure_app_settings().resource_policy = upload_only.model_dump(mode="json")
            db.commit()
        resource_policy.configure_runtime(upload_only)
        assert source()["SupportsDirectPlay"] is True
        assert jf.get(f"/Videos/{HEX(MOVIE_4K)}/stream", headers=HEADERS | {"Range": "bytes=0-9"}).status_code == 206
        # Model a fresh playback after an administrator changes policy rather
        # than treating an already admitted direct stream as a new request.
        playback_resources._items.clear()

        capped = _policy(upload=200_000_000)
        with db_module.SessionLocal() as db:
            YtDlpService(db).ensure_app_settings().resource_policy = capped.model_dump(mode="json")
            db.commit()
        resource_policy.configure_runtime(capped)
        globally_capped = source()
        assert globally_capped["SupportsDirectPlay"] is False
        assert parse_qs(urlparse(globally_capped["TranscodingUrl"]).query)["MaxHeight"] == ["720"]
        # The raw Jellyfin stream route cannot bypass a server-selected rendition.
        assert jf.get(f"/Videos/{HEX(MOVIE_4K)}/stream", headers=HEADERS | {"Range": "bytes=0-9"}).status_code == 409
        playback_resources._items.clear()

        with db_module.SessionLocal() as db:
            db.get(User, ALICE).resource_overrides = {"remote_stream_bps": 3_000_000, "remote_member_bps": 4_000_000}
            db.commit()
        member_capped = source()
        assert parse_qs(urlparse(member_capped["TranscodingUrl"]).query)["MaxHeight"] == ["480"]
    finally:
        playback_resources._items.clear()
        resource_policy.configure_runtime(HouseholdResources())


def test_reused_allocation_obeys_new_client_and_household_quality_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy(upload=20_000_000)
    resources = _protected(monkeypatch, policy)
    user = SimpleNamespace(id="member")
    original = resources.admit(None, user, SimpleNamespace(), "movie", "tv", "hls", _facts(), BROWSER_DEFAULT)
    lowered = resources.admit(None, user, SimpleNamespace(), "movie", "tv", "hls", _facts(), BROWSER_DEFAULT, max_bitrate=2_000_000)
    assert lowered.allocation_bps == original.allocation_bps
    assert lowered.decision.bitrate <= 2_000_000
    policy.defaults.remote_stream_bps = 4_000_000
    lower_default = resources.admit(None, user, SimpleNamespace(), "movie", "tv", "hls", _facts(), BROWSER_DEFAULT)
    assert lower_default.allocation_bps == original.allocation_bps
    assert lower_default.decision.bitrate <= 4_000_000
