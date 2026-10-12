from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from app.services.yt_dlp_service import YtDlpService


class FakeYDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def extract_info(self, source_url, download=False):
        self.requested_url = source_url
        return {
            "_type": "playlist",
            "entries": [
                {
                    "_type": "url",
                    "id": "livevid1",
                    "title": "24/7 lofi radio",
                    "channel": "Lofi Cafe",
                    "channel_url": "https://www.youtube.com/@loficafe",
                    "url": "https://www.youtube.com/watch?v=livevid1",
                    "thumbnails": [{"url": "https://i.ytimg.com/livevid1.jpg"}],
                    "concurrent_view_count": 4321,
                    "extractor_key": "Youtube",
                },
            ],
        }

    def sanitize_info(self, info):
        return info


def _service(recorder: dict) -> YtDlpService:
    def factory(options):
        ydl = FakeYDL(options)
        recorder["ydl"] = ydl
        return ydl

    service = YtDlpService.__new__(YtDlpService)
    service._ydl_factory = factory  # the documented test seam
    service.build_base_options = lambda: {}
    return service


def test_live_search_uses_live_filtered_results_url() -> None:
    recorder: dict = {}
    service = _service(recorder)
    service.youtube_live_search("lofi hip hop", 8)
    parts = urlsplit(recorder["ydl"].requested_url)
    assert parts.netloc == "www.youtube.com"
    assert parts.path == "/results"
    query = parse_qs(parts.query)
    assert query["search_query"] == ["lofi hip hop"]
    assert "sp" in query  # the live filter param is present
    assert recorder["ydl"].options["extract_flat"] is True
    assert recorder["ydl"].options["skip_download"] is True


def test_live_search_entries_are_marked_live() -> None:
    recorder: dict = {}
    service = _service(recorder)
    response = service.youtube_live_search("lofi hip hop", 8)
    assert len(response.items) == 1
    item = response.items[0]
    assert item.capabilities is not None
    assert item.capabilities.lifecycle == "live"
    assert item.capabilities.provider == "youtube"
    assert item.capabilities.can_play is True  # flat live relay-provider click-through


def test_blank_query_returns_empty_without_network() -> None:
    recorder: dict = {}
    service = _service(recorder)
    response = service.youtube_live_search("   ", 8)
    assert response.items == []
    assert "ydl" not in recorder


class FakeProbeYDL:
    def __init__(self, options, info):
        self.options = options
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def extract_info(self, source_url, download=False):
        self.requested_url = source_url
        return self._info

    def sanitize_info(self, info):
        return info


def _probe_service(info: dict) -> YtDlpService:
    service = YtDlpService.__new__(YtDlpService)
    service._ydl_factory = lambda options: FakeProbeYDL(options, info)
    service.build_base_options = lambda: {}
    return service


def test_probe_live_source_labels_twitch_provider() -> None:
    twitch_info = {
        "id": "somestreamer",
        "title": "Live coding",
        "webpage_url": "https://www.twitch.tv/somestreamer",
        "uploader": "somestreamer",
        "extractor_key": "TwitchStream",
        "is_live": True,
    }
    result = _probe_service(twitch_info).probe_live_source("https://www.twitch.tv/somestreamer")
    assert result is not None
    assert result.source == "twitch"
    assert result.source_label == "Twitch"
    assert result.capabilities is not None
    assert result.capabilities.provider == "twitch"
    assert result.capabilities.lifecycle == "live"


def test_probe_live_source_youtube_live_result() -> None:
    youtube_info = {
        "id": "livevid1",
        "title": "24/7 lofi radio",
        "webpage_url": "https://www.youtube.com/watch?v=livevid1",
        "extractor_key": "Youtube",
        "live_status": "is_live",
    }
    result = _probe_service(youtube_info).probe_live_source("https://www.youtube.com/@loficafe/live")
    assert result is not None
    assert result.source == "youtube"
    assert result.capabilities is not None
    assert result.capabilities.provider == "youtube"
    assert result.capabilities.lifecycle == "live"


def test_probe_live_source_not_live_returns_none() -> None:
    vod_info = {
        "id": "vod1",
        "title": "Old stream",
        "webpage_url": "https://www.youtube.com/watch?v=vod1",
        "extractor_key": "Youtube",
        "live_status": "was_live",
    }
    assert _probe_service(vod_info).probe_live_source("https://www.youtube.com/@x/live") is None
