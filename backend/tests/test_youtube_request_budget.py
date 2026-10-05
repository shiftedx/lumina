"""2.6.1: every yt-dlp call that reaches YouTube spends from the household's one budget at its caller's priority, and the
extractions members repeat (a video's preview under any format choice, a stream's playback resolve) are shared."""
from __future__ import annotations

import time
from copy import deepcopy

import pytest
import yt_dlp

from app.schemas import FormatSelection
from app.services import provider_budget, yt_dlp_service
from app.services.yt_dlp_service import YtDlpService, youtube_request_cost

WATCH = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


def _video(expire: float | None = None) -> dict:
    query = f"?expire={int(expire)}" if expire else ""
    return {
        "id": "aaaaaaaaaaa", "title": "v", "extractor": "youtube", "extractor_key": "Youtube", "webpage_url": WATCH,
        "formats": [
            {"format_id": "137", "url": f"https://rr1.googlevideo.com/videoplayback{query}", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "protocol": "https"},
            {"format_id": "140", "url": f"https://rr1.googlevideo.com/videoplayback{query}", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "protocol": "https"},
        ],
    }


@pytest.fixture
def ydl(monkeypatch):  # noqa: ANN001, ANN201
    """A real YoutubeDL (format selection and all) whose extraction answers from ``ydl.answer`` and counts its calls."""
    monkeypatch.setattr(YtDlpService, "build_base_options", lambda self: {"ignoreconfig": True})
    monkeypatch.setattr(provider_budget, "youtube", provider_budget.fresh("youtube", clock=lambda: 0.0))  # no refill
    calls: list[str] = []

    class Counting(yt_dlp.YoutubeDL):
        answer: object = staticmethod(lambda url: _video())

        def extract_info(self, url, download=False, **kwargs):  # noqa: ANN001, ANN003, ANN201
            calls.append(url)
            answer = Counting.answer(url)
            if isinstance(answer, BaseException):
                raise answer
            return self.process_ie_result(deepcopy(answer), download=False)

    Counting.calls = calls
    return Counting


def _spent(action) -> float:  # noqa: ANN001
    before = provider_budget.youtube._tokens
    action()
    return before - provider_budget.youtube._tokens


def test_the_seam_charges_youtube_hosts_and_searches_only() -> None:
    assert youtube_request_cost("ytsearch40:cats", {}) == 2
    assert youtube_request_cost(WATCH, {}) == 2
    assert youtube_request_cost("https://youtu.be/aaaaaaaaaaa", {}) == 2
    assert youtube_request_cost("https://music.youtube.com/watch?v=aaaaaaaaaaa", {}) == 2
    assert youtube_request_cost("https://www.youtube.com/@chan/live", {}) == 3
    assert youtube_request_cost("https://www.youtube.com/results?search_query=x", {"playlistend": 100}) == 5
    for other in ("scsearch10:cats", "https://www.twitch.tv/x", "https://rr1.googlevideo.com/videoplayback", "https://example.com/v"):
        assert youtube_request_cost(other, {}) is None


def test_every_youtube_extraction_spends_and_other_hosts_do_not(ydl) -> None:  # noqa: ANN001
    service = YtDlpService(None, ydl_factory=ydl)
    assert _spent(lambda: service.preview(WATCH)) == 2
    assert _spent(lambda: service.youtube_search("cats", 10, cache=False)) == 1
    ydl.answer = staticmethod(lambda url: {"id": "x", "title": "x", "url": "https://example.com/v.mp4", "ext": "mp4"})
    assert _spent(lambda: service.preview("https://example.com/v")) == 0


def test_a_rate_limit_on_a_click_pauses_background_work_but_never_the_next_click(ydl) -> None:  # noqa: ANN001
    service = YtDlpService(None, ydl_factory=ydl)
    ydl.answer = staticmethod(lambda url: yt_dlp.utils.DownloadError("ERROR: [youtube] aaaaaaaaaaa: HTTP Error 403: Forbidden"))
    with pytest.raises(yt_dlp.utils.DownloadError):
        service.preview(WATCH)
    assert provider_budget.paused(provider_budget.youtube)

    ydl.answer = staticmethod(lambda url: {"_type": "playlist", "entries": []} if "results" in url else _video())
    asked = len(ydl.calls)
    for level in ("live", "followed", "background", "prefetch"):
        with provider_budget.priority(level), pytest.raises(provider_budget.BudgetExhausted):
            service.youtube_live_search("news")
    assert len(ydl.calls) == asked  # nothing reached YouTube

    assert service.preview(WATCH).title == "v"  # a member's click still goes


def test_a_videos_preview_is_shared_across_format_choices_and_kept_ten_minutes(ydl) -> None:  # noqa: ANN001
    service = YtDlpService(None, ydl_factory=ydl)
    best = service.preview(WATCH, format_selection=FormatSelection(preset="best"))
    audio = service.preview(WATCH, format_selection=FormatSelection(preset="audio_only"))

    assert len(ydl.calls) == 1
    assert best.format_resolution.selected_format_id == "137+140"
    assert audio.format_resolution.requested_selector == "bestaudio/best"
    assert audio.format_resolution.selected_format_id == "140"  # re-selected locally from the shared format list
    assert audio.raw["format_id"] == "140" and "height" not in audio.raw and "requested_formats" not in audio.raw
    (expires_at, _), = yt_dlp_service._PREVIEW_CACHE.values()
    assert expires_at - time.monotonic() > 590


def test_a_live_preview_is_kept_two_minutes_at_most(ydl) -> None:  # noqa: ANN001
    ydl.answer = staticmethod(lambda url: {**_video(), "live_status": "is_live", "is_live": True})
    YtDlpService(None, ydl_factory=ydl).preview(WATCH)
    (expires_at, _), = yt_dlp_service._PREVIEW_CACHE.values()
    assert expires_at - time.monotonic() <= 120


def test_playback_resolves_are_shared_but_never_past_their_signed_expiry(ydl) -> None:  # noqa: ANN001
    service = YtDlpService(None, ydl_factory=ydl)
    ydl.answer = staticmethod(lambda url: _video(expire=time.time() + 6 * 3600))
    assert service.resolve_remote_playback(WATCH) == service.resolve_remote_playback(WATCH)
    assert len(ydl.calls) == 1
    (expires_at, _), = yt_dlp_service._RESOLVE_CACHE.values()
    assert 110 < expires_at - time.monotonic() <= 120

    yt_dlp_service._RESOLVE_CACHE.clear()
    ydl.answer = staticmethod(lambda url: _video(expire=time.time() + 90))  # 30 s of margin left
    service.resolve_remote_playback(WATCH)
    (expires_at, _), = yt_dlp_service._RESOLVE_CACHE.values()
    assert expires_at - time.monotonic() <= 30

    yt_dlp_service._RESOLVE_CACHE.clear()
    ydl.answer = staticmethod(lambda url: _video(expire=time.time() + 45))  # inside the margin: never cached
    calls = len(ydl.calls)
    service.resolve_remote_playback(WATCH)
    service.resolve_remote_playback(WATCH)
    assert len(ydl.calls) == calls + 2
