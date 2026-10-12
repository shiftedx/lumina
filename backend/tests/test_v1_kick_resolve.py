"""Canonical public Kick resolution through yt-dlp's Kick extractors, no auth."""

from __future__ import annotations

import pytest
import yt_dlp

from app.schemas import SourceAutomationCreateRequest
from app.services.kick_public import KICK_UNSUPPORTED_LINK, parse_kick_url
from app.services.network_policy import PolicyYoutubeDL, PublicSourcePolicy, PublicSourcePolicyError
from app.services.source_automation import SourceAutomationService
from app.services.yt_dlp_service import YtDlpService, _PREVIEW_CACHE
from yt_dlp.extractor.kick import KickClipIE, KickIE, KickVODIE
from support import make_user, memory_session_factory, seed_app_settings

VOD_ID = "5c697a87-afce-4256-b01f-3c8fe71ef5cb"
CLIP_ID = "clip_01GYXVB5Y8PWAPWCWMSBCFB05X"

# Sanitized API payloads shaped like yt-dlp's documented Kick test cases; no tokens.
RECORDED_API = {
    f"v1/video/{VOD_ID}": {
        "source": "https://stream.kick.example/vod/master.m3u8", "created_at": "2025-08-25T00:40:43Z", "views": 10,
        "livestream": {"session_title": "Same title", "duration": 22278000, "is_live": False,
                       "channel": {"slug": "xqc", "id": 668, "user_id": 676, "user": {"username": "xQc"}}},
    },
    f"v2/clips/{CLIP_ID}/play": {"clip": {
        "clip_url": "https://clips.kick.example/c.mp4", "title": "Same title", "duration": 35,
        "channel": {"slug": "mxddy", "id": 133789}, "creator": {"username": "AbdCreates", "id": 3309077},
    }},
    "v2/channels/buddha": {"id": 32807, "user_id": 33057, "playback_url": "https://stream.kick.example/live.m3u8",
                           "user": {"username": "Buddha"},
                           "livestream": {"slug": "92722911-nopixel-40", "session_title": "Live", "viewer_count": 5}},
    "v2/channels/offline": {"id": 1, "livestream": None},
}


def _session():
    db = memory_session_factory()()
    seed_app_settings(db)
    return db


def _fake_extract(monkeypatch, calls):
    """Run the real pinned Kick extractors with the network replaced by recorded JSON."""

    def download_json(self, url, video_id, note=None, headers=None, **kwargs):  # noqa: ANN001, ARG001
        calls.append((url, dict(headers or {})))
        return RECORDED_API[url.removeprefix("https://kick.com/api/")]

    def m3u8(self, url, video_id, ext=None, **kwargs):  # noqa: ANN001, ARG001
        return [{"url": url, "protocol": "m3u8_native", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"}]

    for ie in (KickIE, KickVODIE, KickClipIE):
        monkeypatch.setattr(ie, "_download_json", download_json)
        monkeypatch.setattr(ie, "_extract_m3u8_formats", m3u8)


def _service():
    # Public DNS answer for kick.com without touching the network.
    policy = PublicSourcePolicy(resolver=lambda host, port, *a, **k: [(2, 1, 6, "", ("151.101.1.1", port))])
    return YtDlpService(_session(), network_policy=policy)


def test_kick_channel_vod_clip_identities(monkeypatch) -> None:
    _PREVIEW_CACHE.clear()
    _fake_extract(monkeypatch, [])
    service = _service()

    vod = service.preview(f"https://www.kick.com/xqc/videos/{VOD_ID}")
    clip = service.preview(f"https://kick.com/mxddy?clip={CLIP_ID}")
    live = service.preview("https://kick.com/Buddha")

    assert vod.webpage_url == f"https://kick.com/xqc/videos/{VOD_ID}"
    assert clip.webpage_url == f"https://kick.com/mxddy/clips/{CLIP_ID}"
    assert live.webpage_url == "https://kick.com/buddha"
    # Same title, different stable ids: never merged.
    assert vod.title == clip.title and vod.raw["id"] != clip.raw["id"]
    for preview, lifecycle in ((vod, "vod"), (clip, "vod"), (live, "live")):
        assert preview.capabilities.provider == "kick"
        assert preview.capabilities.lifecycle == lifecycle
        # Guarded-relay playback for all three; finite media acquires, live never
        # does, and only the live channel records from now (#142).
        caps = preview.capabilities
        assert (caps.can_play, caps.can_acquire, caps.can_record) == (True, lifecycle == "vod", lifecycle == "live")

    with pytest.raises(yt_dlp.utils.DownloadError, match="not currently live"):
        service.preview("https://kick.com/offline")


def test_kick_lookalike_and_redirect_denied() -> None:
    assert parse_kick_url("https://kick.com/xqc").kind == "channel"
    for lookalike in (
        "https://kick.com.evil.example/xqc", "https://evilkick.com/xqc", "https://kick.co/xqc",
        "https://kick.com@evil.example/xqc", "https://user@kick.com/xqc", "https://kick.com:8443/xqc",
        "ftp://kick.com/xqc", "https://kick.com/categories/games", "https://kick.com/xqc/videos/not-a-uuid",
        "https://kick.com/../admin",
    ):
        assert parse_kick_url(lookalike) is None, lookalike

    service = _service()
    with pytest.raises(yt_dlp.utils.DownloadError, match=KICK_UNSUPPORTED_LINK):
        service.preview("https://kick.com/categories/games")

    # A Kick-looking address resolving to a private destination is refused by the
    # same public policy every request and redirect hop passes through.
    private = PublicSourcePolicy(resolver=lambda host, port, *a, **k: [(2, 1, 6, "", ("10.0.0.5", port))])
    with pytest.raises(PublicSourcePolicyError):
        private.validate_url("https://kick.com/xqc")
    with pytest.raises(PublicSourcePolicyError):
        _service().network_policy.validate_url("https://127.0.0.1/xqc")


def test_kick_no_oauth_or_cookie(monkeypatch) -> None:
    _PREVIEW_CACHE.clear()
    calls: list = []
    _fake_extract(monkeypatch, calls)
    service = _service()
    service.preview(f"https://kick.com/xqc/videos/{VOD_ID}")
    service.preview(f"https://kick.com/mxddy/clips/{CLIP_ID}")

    assert [url for url, _ in calls] == [
        f"https://kick.com/api/v1/video/{VOD_ID}", f"https://kick.com/api/v2/clips/{CLIP_ID}/play",
    ]
    assert all("Authorization" not in headers and "Cookie" not in headers for _, headers in calls)
    assert not any("oauth" in url or "id.kick.com" in url or "token" in url for url, _ in calls)
    # With Lumina's public options the extractor has no session to turn into a bearer token.
    with PolicyYoutubeDL(service.build_base_options()) as ydl:
        assert KickVODIE(ydl)._api_headers == {}


def test_kick_resolve_unavailable_saved_ref(monkeypatch) -> None:
    db = _session()
    user = make_user("u")
    db.add(user)
    db.commit()

    class Jobs:
        def stage_enqueue(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("no acquisition for a failed resolve")

    service = SourceAutomationService(db, Jobs())  # type: ignore[arg-type]
    saved = service.create_automation(
        SourceAutomationCreateRequest(
            label="Buddha", source_url="https://kick.com/buddha", source_type="channel", auto_download=False,
        ), user,
    )
    db.commit()
    monkeypatch.setattr(YtDlpService, "preview", lambda *_a, **_k: (_ for _ in ()).throw(
        yt_dlp.utils.DownloadError("ERROR: [kick:live] buddha: The channel is not currently live")
    ))

    run = service.run_automation(saved.id, user)
    db.commit()

    assert run.status == "failed"
    kept = service.get_automation(saved.id, user)
    assert kept is not None and kept.source_url == "https://kick.com/buddha"
    assert "not currently live" in (kept.last_error or "")
    assert kept.last_checked_at is not None
