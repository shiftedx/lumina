"""The replay-chat download reuses the coordinated PolicyYoutubeDL seam.

These tests exercise the yt-dlp seam through a fake YoutubeDL factory so the
network is never touched: they assert that the download is subtitle-only,
requests the ``live_chat`` track, honours the public network policy factory,
reads bounded lines from the produced ``.live_chat.json`` file, and reports
unavailable/failed states without raising.
"""

from __future__ import annotations

from pathlib import Path

import yt_dlp

from app.services.yt_dlp_service import YtDlpService


class _FakeYDL:
    def __init__(self, options, *, work_writer=None, raise_error=False):
        self.options = options
        self._work_writer = work_writer
        self._raise_error = raise_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if self._raise_error:
            raise yt_dlp.utils.DownloadError("HTTP Error 429: Too Many Requests")
        if self._work_writer is not None and download:
            self._work_writer(self.options)
        return {"id": "vid", "extractor": "youtube"}


def _service_with_factory(factory) -> YtDlpService:
    service = YtDlpService.__new__(YtDlpService)
    service.db = None
    service.network_policy = None
    service._ydl_factory = factory

    class _AuthStub:
        def materialize_cookie_file(self, path, *, expected_profile_id=None):
            from contextlib import contextmanager

            @contextmanager
            def _noop():
                yield None

            return _noop()

    service.auth_service = _AuthStub()
    service.validate_source_url = lambda url: url  # policy exercised elsewhere
    service.build_base_options = lambda: {"noprogress": True}
    return service


def test_download_replay_chat_reads_bounded_lines_from_subtitle_file() -> None:
    captured: dict[str, object] = {}

    def write_chat(options):
        home = Path(options["paths"]["home"])
        target = home / "vid.live_chat.json"
        target.write_bytes(b'{"replayChatItemAction": {"videoOffsetTimeMsec": "10", "actions": []}}\n' * 3)
        captured["options"] = options

    fetch = _service_with_factory(lambda options: _FakeYDL(options, work_writer=write_chat)).download_replay_chat(
        "https://www.youtube.com/watch?v=vid"
    )
    assert fetch.status == "available"
    assert len(fetch.lines) == 3
    options = captured["options"]
    assert options["skip_download"] is True
    assert options["writesubtitles"] is True
    assert options["subtitleslangs"] == ["live_chat"]
    assert options["writeautomaticsub"] is False


def test_download_replay_chat_reports_unavailable_when_no_track_written() -> None:
    fetch = _service_with_factory(lambda options: _FakeYDL(options, work_writer=lambda opts: None)).download_replay_chat(
        "https://www.youtube.com/watch?v=vid"
    )
    assert fetch.status == "unavailable"
    assert fetch.lines == []


def test_download_replay_chat_reports_failed_on_provider_error() -> None:
    fetch = _service_with_factory(lambda options: _FakeYDL(options, raise_error=True)).download_replay_chat(
        "https://www.youtube.com/watch?v=vid"
    )
    assert fetch.status == "failed"


def test_download_replay_chat_caps_bytes_and_drops_truncated_tail() -> None:
    def write_chat(options):
        home = Path(options["paths"]["home"])
        target = home / "vid.live_chat.json"
        target.write_bytes(b"".join(b'{"line": %d}\n' % index for index in range(1000)))

    fetch = _service_with_factory(lambda options: _FakeYDL(options, work_writer=write_chat)).download_replay_chat(
        "https://www.youtube.com/watch?v=vid", max_source_bytes=64
    )
    assert fetch.status == "available"
    # The byte ceiling is honoured and no partial trailing line is emitted.
    assert sum(len(line) for line in fetch.lines) <= 64
    for line in fetch.lines:
        assert line.endswith(b"}")
