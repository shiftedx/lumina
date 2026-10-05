"""S33 — generic public URLs: truthful per-item play/save, never a misleading promise."""

from __future__ import annotations

import pytest
import yt_dlp

from app import main as main_module
from app.schemas import FormatSelection
from app.services.hls_relay_support import derive_provider, public_provider
from app.services.job_manager import _actual_height
from app.services.media_capabilities import derive_media_capabilities
from app.services.network_policy import PublicSourcePolicy
from app.services.storage_routing import StorageRule, decide
from app.services.yt_dlp_service import YtDlpService
from tests.test_twitch_vod_acquisition_guarded import LoopbackHlsPolicy, _acquire, hls_fixture
from support import memory_session_factory

PAGE = "https://videos.example.org/watch/42"


def _session():
    return memory_session_factory()()


def _ydl(result):  # noqa: ANN001
    class FakeYDL:
        def __init__(self, options):  # noqa: ANN001
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):  # noqa: ANN002
            return False

        def extract_info(self, source_url, download=False):  # noqa: ANN001
            if isinstance(result, Exception):
                raise result
            return dict(result)

        def sanitize_info(self, info):  # noqa: ANN001
            return info

    return FakeYDL


def _preview(result, url: str = PAGE):  # noqa: ANN001
    # Distinct URLs per call: preview results are cached by address.
    # Public DNS answer for the fixture host without touching the network.
    policy = PublicSourcePolicy(resolver=lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))])
    return YtDlpService(_session(), ydl_factory=_ydl(result), network_policy=policy).preview(url, lazy_playlist=False, format_selection=FormatSelection(preset="best"))


def test_generic_metadata_not_play_promise() -> None:
    # Title and poster resolved, but inspection found no media: not a play/save promise.
    preview = _preview({
        "id": "42", "title": "A page with a poster", "thumbnail": "https://videos.example.org/poster.jpg",
        "extractor": "generic", "extractor_key": "Generic", "webpage_url": PAGE, "formats": [],
    })
    caps = preview.capabilities
    assert (caps.provider, caps.provisional) == ("generic", False)
    assert (caps.can_play, caps.play_reason, caps.can_acquire, caps.acquire_reason) == (False, "no_supported_transport", False, "no_supported_transport")
    assert public_provider(derive_provider({"extractor_key": "Vimeo"})).label == "Web"

    # A page with nothing yt-dlp can extract is explained as such, without operator advice.
    for index, message in enumerate((
        "ERROR: [generic] 42: No video formats found!; please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. Confirm you are on the latest version using  yt-dlp -U",
        "ERROR: Unsupported URL: https://videos.example.org/watch/42",
    )):
        with pytest.raises(yt_dlp.utils.DownloadError) as error:
            _preview(yt_dlp.utils.DownloadError(message), f"{PAGE}/unsupported/{index}")
        detail = main_module._download_error_http_exception(error.value).detail
        assert detail["category"] == "unsupported"
        assert "yt-dlp" not in detail["message"] and "github" not in detail["message"]


def test_generic_public_fixture_save(tmp_path) -> None:  # noqa: ANN001
    body = b"\x1aE\xdf\xa3" + b"progressive-webm-bytes" * 64
    info = {
        "id": "42", "title": "Public clip", "extractor": "vimeo", "extractor_key": "Vimeo", "webpage_url": PAGE,
        "formats": [{"format_id": "http-720p", "protocol": "https", "url": "https://cdn.videos.example.org/42.webm",
                     "ext": "webm", "vcodec": "vp9", "acodec": "opus", "height": 720}],
    }
    caps = derive_media_capabilities(info)
    assert (caps.provider, caps.can_play, caps.can_acquire, caps.provisional) == ("unknown", True, True, False)

    policy = LoopbackHlsPolicy()
    with hls_fixture({"/42.webm": body}) as (port, handler):
        local = {**info, "formats": [{**info["formats"][0], "protocol": "http", "url": f"http://127.0.0.1:{port}/42.webm"}]}
        _acquire(tmp_path, policy, local)
    assert (tmp_path / "42.webm").read_bytes() == body
    assert [path for path, _ in handler.seen] == ["/42.webm"] and set(policy.connected) == {"127.0.0.1"}

    # Managed routing files unknown extractors under the generic source by actual height.
    rules = [StorageRule(id="web", priority=1, sources=["generic"], target_root_id="web-root", relative_template="{source}/{height}")]
    route = decide(rules, "default", 1, derive_provider(info), "video", _actual_height(info["formats"][0]))
    assert (route.rule_id, route.folder) == ("web", "generic/720p")


def test_generic_gated_no_cookie() -> None:
    for index, message in enumerate((
        "ERROR: [Instagram] C1a2B3c4D5e: Requested content is not available, rate-limit reached or login required. Use --cookies, --cookies-from-browser, --username and --password, --netrc-cmd, or --netrc (instagram) to provide account credentials",
        "ERROR: [Instagram] C1a2B3c4D5e: Instagram sent an empty media response. Check if this post is accessible in your browser without being logged-in. If it is not, then use --cookies-from-browser or --cookies for the authentication.",
    )):
        with pytest.raises(yt_dlp.utils.DownloadError) as error:
            _preview(yt_dlp.utils.DownloadError(message), f"https://www.instagram.com/p/C1a2B3c4D5e/?n={index}")
        detail = main_module._download_error_http_exception(error.value).detail
        assert detail["category"] == "sign_in_required"
        lowered = detail["message"].lower()
        assert "cookie" not in lowered and "password" not in lowered and "--" not in lowered
        assert "open the original link" in lowered
