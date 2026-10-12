"""Opening a channel page makes only the flat extractions the channel page needs; /api/discovery/live makes none on the
request thread. The socket guard of conftest already refuses any real connection."""
from __future__ import annotations

import threading

import app.main as main
from app.services.followed_live import FollowedLiveChecker
from app.services.youtube_channels import channel_pages
from app.services.yt_dlp_service import PUBLIC_OPTION_ALLOWLIST, YtDlpService
from support import make_user

CID = "UCabcdefghijklmnopqrstuv"
ALICE = make_user("alice")


class RecordingYdl:
    """The yt-dlp seam: every extraction's URL and options, and the thread that ran it."""

    calls: list[tuple[str, dict, str]] = []

    def __init__(self, options: dict) -> None:
        self.options = options

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        return False

    def extract_info(self, url: str, download: bool) -> dict:  # noqa: FBT001
        RecordingYdl.calls.append((url, dict(self.options), threading.current_thread().name))
        return {"channel_id": CID, "channel": "Harbor Films", "entries": []}

    def sanitize_info(self, info: dict) -> dict:
        return info


class OpenPolicy:
    def validate_url(self, url: str) -> str:
        return url


class SettingsFree(YtDlpService):
    """The real extraction path minus the settings row (a worker-thread database is not the point here)."""

    def build_base_options(self) -> dict:
        return {"ignoreconfig": True, "download_archive": "/tmp/unused"}


def test_a_channel_page_makes_two_flat_extractions_off_the_request_thread(monkeypatch, api_client) -> None:  # noqa: ANN001
    RecordingYdl.calls = []

    def extract(url: str, limit: int) -> dict:
        return SettingsFree(None, ydl_factory=RecordingYdl, network_policy=OpenPolicy()).extract_channel_listing(url, limit)

    pages = channel_pages(extract)
    monkeypatch.setattr(main, "channel_pages", pages)
    try:
        response = api_client(user=ALICE, base_url="http://localhost").get(f"/api/channels/youtube/{CID}")
    finally:
        pages.close()
    assert response.status_code == 200, response.text
    # The header and tab loads run on the pool at once: their order is not fixed.
    assert sorted(url for url, _, _ in RecordingYdl.calls) == [f"https://www.youtube.com/channel/{CID}", f"https://www.youtube.com/channel/{CID}/videos"]
    for _, options, thread in RecordingYdl.calls:
        assert options["extract_flat"] == "in_playlist" and options["skip_download"] is True
        assert set(options) <= PUBLIC_OPTION_ALLOWLIST
        assert thread.startswith("channel-pages")


def test_the_live_route_never_probes_on_the_request_thread(monkeypatch, api_client) -> None:  # noqa: ANN001
    probed: list[str] = []
    submitted: list[str] = []

    class HoldingExecutor:
        def submit(self, fn, url):  # noqa: ANN001, ANN201
            submitted.append(url)  # scheduled for the background, never run here

    checker = FollowedLiveChecker(lambda url: probed.append(url), executor=HoldingExecutor())
    monkeypatch.setattr(main, "followed_live_checker", checker)
    monkeypatch.setattr(main.MemberFollowService, "existing_follow_identities", lambda self, member: {f"https://www.youtube.com/channel/{CID}"})
    response = api_client(user=ALICE, base_url="http://localhost").get("/api/discovery/live")
    assert response.status_code == 200
    assert probed == [] and submitted == [f"https://www.youtube.com/channel/{CID}/live"]
