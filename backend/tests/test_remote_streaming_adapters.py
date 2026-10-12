from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
from threading import Event, Thread

import pytest

from app.services import remote_streaming_adapters as adapters
from app.services.remote_streaming import ByteRange, MediaTrack, UnsupportedPlaybackError, UpstreamMediaResponse


def track(*, format_id: str = "18", url: str = "https://media.example/video") -> MediaTrack:
    return MediaTrack(
        format_id=format_id,
        url=url,
        content_type="video/mp4",
        protocol="https",
        ext="mp4",
        vcodec="avc1.42001e",
        acodec="mp4a.40.2",
        height=720,
        content_length=12,
        expires_at=5_000,
        headers={"Cookie": "private-cookie"},
    )


def test_public_reader_preserves_private_headers_and_ranges_inside_safe_transport(monkeypatch) -> None:
    opened = []
    closed = []

    class FakeResponse:
        status = 206
        headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 2-6/12"}

        def __init__(self) -> None:
            self.parts = iter([b"range", b""])

        def read(self, amount):  # noqa: ANN001
            assert amount == 64 * 1024
            return next(self.parts)

        def close(self) -> None:
            closed.append("response")

    class FakeYDL:
        def __init__(self, options, policy):  # noqa: ANN001
            assert options["proxy"] == ""
            self.policy = policy

        def urlopen(self, request):  # noqa: ANN001
            opened.append(request)
            return FakeResponse()

        def close(self) -> None:
            closed.append("ydl")

    class FakePolicy:
        def validate_url(self, value):  # noqa: ANN001
            assert value == "https://media.example/video"
            return value

    monkeypatch.setattr(adapters, "PolicyYoutubeDL", FakeYDL)
    response = adapters.PublicMediaReader(policy=FakePolicy()).open(track(), ByteRange(2, 6))

    assert response.status_code == 206
    assert b"".join(response.body) == b"range"
    assert opened[0].headers["Range"] == "bytes=2-6"
    assert opened[0].headers["Cookie"] == "private-cookie"
    assert opened[0].extensions["timeout"] == 30
    response.close()
    assert closed == ["response", "ydl"]

    suffix = adapters.PublicMediaReader(policy=FakePolicy()).open(track(), ByteRange(None, 5))
    assert opened[1].headers["Range"] == "bytes=-5"
    suffix.close()


def test_public_relay_fetcher_validates_url_applies_headers_and_ranges_and_keeps_connections_alive() -> None:
    """A live player fetches a playlist and a segment every ~2 s: each must not pay a new client and TLS handshake."""
    import http.server
    import socket as socket_module
    import threading

    seen: list[dict[str, str]] = []
    connections: list[object] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            connections.append(self.connection)

        def do_GET(self):  # noqa: N802
            seen.append(dict(self.headers))
            body = b"seg"
            self.send_response(206)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Range", "bytes 0-2/9")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: ANN001
            del args

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    validated: list[str] = []

    class LoopbackPolicy:
        def validate_url(self, value):  # noqa: ANN001
            validated.append(value)
            return value

        def resolve(self, host, port):  # noqa: ANN001
            return [(socket_module.AF_INET, socket_module.SOCK_STREAM, socket_module.IPPROTO_TCP, "", (host, port))]

    fetcher = adapters.PublicRelayFetcher(policy=LoopbackPolicy())
    url = f"http://127.0.0.1:{port}/vod/seg0.ts"
    try:
        for _ in range(3):
            response = fetcher.fetch(url, headers={"Cookie": "member-owned"}, byte_range=ByteRange(0, 2), timeout_seconds=5)
            assert response.status_code == 206
            assert b"".join(response.body) == b"seg"
            response.close()
    finally:
        server.shutdown()
    assert validated == [url] * 3
    assert seen[0]["Range"] == "bytes=0-2"
    assert seen[0]["Cookie"] == "member-owned"
    assert len(connections) == 1  # one socket served all three requests


def test_public_relay_fetcher_fails_closed_on_redirect_into_private_space() -> None:
    import http.server
    import socket as socket_module
    import threading

    from app.services.network_policy import PublicSourcePolicyError
    from yt_dlp.networking.exceptions import RequestError

    connected_targets: list[str] = []

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *args):  # noqa: ANN001
            del args

    server = http.server.HTTPServer(("127.0.0.1", 0), RedirectHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class RedirectTestPolicy:
        """Permit the loopback origin to connect but reject the private redirect."""

        def validate_url(self, value):  # noqa: ANN001
            if value.startswith("http://169.254.169.254"):
                raise PublicSourcePolicyError()
            return value

        def resolve(self, host, port):  # noqa: ANN001
            connected_targets.append(host)
            return [(socket_module.AF_INET, socket_module.SOCK_STREAM, socket_module.IPPROTO_TCP, "", (host, port))]

    try:
        fetcher = adapters.PublicRelayFetcher(policy=RedirectTestPolicy())
        with pytest.raises((PublicSourcePolicyError, RequestError)) as caught:
            fetcher.fetch(f"http://127.0.0.1:{port}/start", timeout_seconds=5)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    # The private redirect target was revalidated and never connected to.
    assert "169.254.169.254" not in connected_targets
    message = str(caught.value) + str(getattr(caught.value, "__cause__", ""))
    assert "Public source policy" in message


def test_public_reader_closes_transport_when_open_fails(monkeypatch) -> None:
    closed: list[str] = []

    class FailingYDL:
        def __init__(self, options, policy):  # noqa: ANN001
            del options, policy

        def urlopen(self, request):  # noqa: ANN001
            del request
            raise OSError("open failed")

        def close(self) -> None:
            closed.append("ydl")

    class FakePolicy:
        def validate_url(self, value):  # noqa: ANN001
            return value

    monkeypatch.setattr(adapters, "PolicyYoutubeDL", FailingYDL)
    with pytest.raises(OSError, match="open failed"):
        adapters.PublicMediaReader(policy=FakePolicy()).open(track(), None)
    assert closed == ["ydl"]


def test_hls_packager_cleans_partial_files_when_input_is_rejected(tmp_path: Path, idle_ffmpeg) -> None:  # noqa: ANN001
    class RejectingReader:
        def open(self, media_track, byte_range, timeout_seconds=None):  # noqa: ANN001
            del media_track, byte_range, timeout_seconds
            return UpstreamMediaResponse(403, {}, [])

    packager = adapters.FfmpegHlsPackager(
        reader=RejectingReader(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        ffmpeg_path="ffmpeg",
    )

    with pytest.raises(UnsupportedPlaybackError, match="expired"):
        packager.prepare("secret-stream-id", 1, track(format_id="video"), track(format_id="audio"))

    assert list((tmp_path / "remote-streams").glob("**/*")) == [tmp_path / "remote-streams" / ".leases"]


def test_hls_packager_reclaims_only_expired_process_roots_on_startup(tmp_path: Path) -> None:
    base = tmp_path / "remote-streams"
    abandoned = base / "abandonedprocess"
    recent = base / "recentprocess"
    (abandoned / "stream" / "1").mkdir(parents=True)
    (abandoned / "stream" / "1" / "segment.m4s").write_bytes(b"orphan")
    (recent / "stream" / "1").mkdir(parents=True)
    (recent / "stream" / "1" / "segment.m4s").write_bytes(b"active")
    os.utime(abandoned, (100.0, 100.0))
    os.utime(recent, (950.0, 950.0))

    adapters.FfmpegHlsPackager(
        reader=object(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        wall_clock=lambda: 1_000.0,
        orphan_ttl_seconds=100,
        process_token_factory=lambda: "currentprocess",
    )

    assert not abandoned.exists()
    assert recent.exists()


def test_hls_reclamation_never_deletes_an_old_live_process_root(tmp_path: Path) -> None:
    live = adapters.FfmpegHlsPackager(
        reader=object(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        wall_clock=lambda: 100.0,
        orphan_ttl_seconds=100,
        process_token_factory=lambda: "liveprocess",
    )
    with live._lock:  # noqa: SLF001 - exercise the cross-process lease without invoking FFmpeg
        live._ensure_process_root_locked()  # noqa: SLF001
    assert live._lease_path.parent == tmp_path / "remote-streams" / ".leases"  # noqa: SLF001
    assert live._root not in live._lease_path.parents  # noqa: SLF001
    live_asset = live._root / "stream" / "1" / "segment.m4s"  # noqa: SLF001
    live_asset.parent.mkdir(parents=True)
    live_asset.write_bytes(b"active")
    os.utime(live._root, (100.0, 100.0))  # noqa: SLF001

    adapters.FfmpegHlsPackager(
        reader=object(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        wall_clock=lambda: 1_000.0,
        orphan_ttl_seconds=100,
        process_token_factory=lambda: "newprocess",
    )

    assert live_asset.read_bytes() == b"active"
    live_asset.unlink()
    live_asset.parent.rmdir()
    live_asset.parent.parent.rmdir()
    live._release_process_root_if_unused()  # noqa: SLF001


def test_hls_release_holds_lease_during_root_deletion_and_keeps_namespace(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    owner = adapters.FfmpegHlsPackager(
        reader=object(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        wall_clock=lambda: 100.0,
        orphan_ttl_seconds=100,
        process_token_factory=lambda: "ownerprocess",
    )
    with owner._lock:  # noqa: SLF001
        owner._ensure_process_root_locked()  # noqa: SLF001
    os.utime(owner._root, (100.0, 100.0))  # noqa: SLF001

    deletion_started = Event()
    allow_deletion = Event()
    real_rmtree = shutil.rmtree

    def blocking_rmtree(path, *args, **kwargs):  # noqa: ANN001
        if Path(path) == owner._root:  # noqa: SLF001
            deletion_started.set()
            assert allow_deletion.wait(timeout=5)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(adapters.shutil, "rmtree", blocking_rmtree)
    release = Thread(target=owner._release_process_root_if_unused)  # noqa: SLF001
    release.start()
    assert deletion_started.wait(timeout=5)

    contender = adapters.FfmpegHlsPackager(
        reader=object(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        wall_clock=lambda: 1_000.0,
        orphan_ttl_seconds=100,
        process_token_factory=lambda: "contenderprocess",
    )
    assert owner._root.exists()  # noqa: SLF001 - the contender could not reclaim the leased root
    assert contender._lease_root.is_dir()  # noqa: SLF001 - shared namespace is never removed

    allow_deletion.set()
    release.join(timeout=5)
    assert not release.is_alive()
    assert not owner._root.exists()  # noqa: SLF001
    assert owner._lease_path.exists() is False  # noqa: SLF001
    assert contender._lease_root.is_dir()  # noqa: SLF001


def test_file_lock_adapter_supports_the_windows_byte_range_api(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    calls: list[tuple[int, int]] = []

    class FakeMsvcrt:
        LK_NBLCK = 2
        LK_UNLCK = 0

        @staticmethod
        def locking(_descriptor: int, mode: int, length: int) -> None:
            calls.append((mode, length))

    monkeypatch.setattr(adapters, "_fcntl", None)
    monkeypatch.setattr(adapters, "_msvcrt", FakeMsvcrt)
    with (tmp_path / "lease").open("a+b") as lease:
        assert adapters._try_lock_file(lease) is True  # noqa: SLF001
        adapters._unlock_file(lease)  # noqa: SLF001
        lease.seek(0, 2)
        assert lease.tell() == 1

    assert calls == [(FakeMsvcrt.LK_NBLCK, 1), (FakeMsvcrt.LK_UNLCK, 1)]

    packager = adapters.FfmpegHlsPackager(
        reader=object(),  # type: ignore[arg-type]
        temp_root=tmp_path / "windows-cleanup",
        process_token_factory=lambda: "windowsprocess",
    )
    with packager._lock:  # noqa: SLF001
        packager._ensure_process_root_locked()  # noqa: SLF001
    lease_path = packager._lease_path  # noqa: SLF001
    process_root = packager._root  # noqa: SLF001
    assert lease_path.exists() and process_root.exists()
    packager._release_process_root_if_unused()  # noqa: SLF001
    assert not lease_path.exists()
    assert not process_root.exists()


def test_hls_packager_rejects_tracks_over_one_aggregate_budget_before_downloading(tmp_path: Path) -> None:
    class UnexpectedReader:
        def open(self, media_track, byte_range, timeout_seconds=None):  # noqa: ANN001
            raise AssertionError((media_track, byte_range, timeout_seconds))

    packager = adapters.FfmpegHlsPackager(
        reader=UnexpectedReader(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        ffmpeg_path="ffmpeg",
        max_total_bytes=20,
    )

    with pytest.raises(UnsupportedPlaybackError, match="aggregate budget"):
        packager.prepare("budget-stream", 1, track(format_id="video"), track(format_id="audio"))


def test_hls_packager_reports_only_known_inputs_that_fit_the_byte_budget(tmp_path: Path) -> None:
    packager = adapters.FfmpegHlsPackager(
        reader=object(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        max_total_bytes=100,
    )

    assert packager.can_package(
        replace(track(format_id="video"), content_length=60),
        replace(track(format_id="audio"), content_length=40),
    ) is True
    assert packager.can_package(
        replace(track(format_id="video"), content_length=61),
        replace(track(format_id="audio"), content_length=40),
    ) is False
    assert packager.can_package(
        replace(track(format_id="video"), content_length=None),
        replace(track(format_id="audio"), content_length=None),
    ) is True
    assert packager.can_package(
        replace(track(format_id="video"), content_length=None, estimated_content_length=61),
        replace(track(format_id="audio"), content_length=None, estimated_content_length=40),
    ) is False
    assert packager.can_package(
        replace(track(format_id="video"), height=None),
        replace(track(format_id="audio"), content_length=20),
    ) is False


def test_hls_packager_rejects_high_resolution_split_tracks_before_downloading(tmp_path: Path) -> None:
    class UnexpectedReader:
        def open(self, media_track, byte_range, timeout_seconds=None):  # noqa: ANN001
            raise AssertionError((media_track, byte_range, timeout_seconds))

    packager = adapters.FfmpegHlsPackager(
        reader=UnexpectedReader(),  # type: ignore[arg-type]
        temp_root=tmp_path,
        ffmpeg_path="ffmpeg",
    )

    with pytest.raises(UnsupportedPlaybackError, match="DASH range relay"):
        packager.prepare(
            "high-resolution",
            1,
            replace(track(format_id="video"), height=2160),
            track(format_id="audio"),
        )

    assert not (tmp_path / "remote-streams").exists()


# --- Chunked ranged reads (YouTube throttles unranged googlevideo requests) ---

_CHUNKED_MEDIA = bytes(range(256)) * 4  # 1024 bytes
_CHUNK = 100


class _LoopbackPolicy:
    def validate_url(self, value):  # noqa: ANN001
        return value

    def resolve(self, host, port):  # noqa: ANN001
        import socket as socket_module

        return [(socket_module.AF_INET, socket_module.SOCK_STREAM, socket_module.IPPROTO_TCP, "", (host, port))]


@pytest.fixture
def chunked_upstream():
    """Serve only bounded ranges no larger than _CHUNK; refuse everything else with 403."""
    import http.server
    import threading

    import time

    # "pair": hold each request after the first until another is in flight too (a serial reader waits out 5 s each).
    state = {"requests": [], "fail_from": None, "fail_status": 403, "delay": 0.0, "pair": False, "active": 0, "peak": 0}
    in_flight = threading.Condition()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            value = self.headers.get("Range")
            state["requests"].append(value)
            with in_flight:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                in_flight.notify_all()
                if state["pair"] and len(state["requests"]) > 1:
                    in_flight.wait_for(lambda: state["active"] > 1 or state["peak"] > 1, timeout=5)
            try:
                self._answer(value)
            finally:
                with in_flight:
                    state["active"] -= 1

        def _answer(self, value):  # noqa: ANN001
            time.sleep(state["delay"])
            try:
                start_text, end_text = value.removeprefix("bytes=").split("-")
                start, end = int(start_text), int(end_text)
            except (AttributeError, ValueError):
                start = end = None
            if start is None or end - start + 1 > _CHUNK or (state["fail_from"] is not None and start >= state["fail_from"]):
                self.send_response(state["fail_status"] if start is not None else 403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, len(_CHUNKED_MEDIA) - 1)
            body = _CHUNKED_MEDIA[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(_CHUNKED_MEDIA)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: ANN001
            del args

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}/media"
    try:
        yield state
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _chunked_track(url: str, *, chunk_size: int | None = _CHUNK, content_length: int | None = len(_CHUNKED_MEDIA)) -> MediaTrack:
    return replace(track(url=url), content_length=content_length, chunk_size=chunk_size)


def test_public_reader_reads_whole_chunked_track_through_bounded_ranges(chunked_upstream) -> None:
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    response = reader.open(_chunked_track(chunked_upstream["url"]), None, timeout_seconds=10)
    try:
        assert response.status_code == 200
        assert response.headers["Content-Length"] == str(len(_CHUNKED_MEDIA))
        assert not any(name.lower() == "content-range" for name in response.headers)
        assert b"".join(response.body) == _CHUNKED_MEDIA
    finally:
        response.close()
    assert chunked_upstream["requests"][0] == "bytes=0-99"
    assert "bytes=1000-1023" in chunked_upstream["requests"]  # later chunks run concurrently, in no fixed order
    assert len(chunked_upstream["requests"]) == 11


def test_public_reader_reads_the_chunks_after_the_first_a_few_at_once_in_order(chunked_upstream) -> None:
    """26-pp: googlevideo paces each request (3-25 Mb/s while other connections ran at 100+), so a 21 MB 4K segment
    read 10 MB at a time took 5-8 s and dash.js abandoned the top rung; parallel ranges went 1.5-4x faster."""
    chunked_upstream["pair"] = True
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    reader.parallel_pieces = 4  # off by default since 2.5.0's production 403s; the mechanism stays covered
    response = reader.open(_chunked_track(chunked_upstream["url"]), ByteRange(0, 799))
    try:
        assert b"".join(response.body) == _CHUNKED_MEDIA[:800]
    finally:
        response.close()
    assert 1 < chunked_upstream["peak"] <= 4  # one, then the other seven up to four at a time (no wall-clock race)


def test_public_reader_learns_length_from_first_chunk_when_track_size_unknown(chunked_upstream) -> None:
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    response = reader.open(_chunked_track(chunked_upstream["url"], content_length=None), ByteRange(850, None))
    try:
        assert response.status_code == 206
        assert response.headers["Content-Range"] == "bytes 850-1023/1024"
        assert response.headers["Content-Length"] == "174"
        assert b"".join(response.body) == _CHUNKED_MEDIA[850:]
    finally:
        response.close()


def test_public_reader_full_chunked_read_trusts_upstream_total_over_filesize_hint(chunked_upstream) -> None:
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    response = reader.open(_chunked_track(chunked_upstream["url"], content_length=50), None)
    try:
        assert response.headers["Content-Length"] == "1024"
        assert b"".join(response.body) == _CHUNKED_MEDIA
    finally:
        response.close()


@pytest.mark.parametrize(("byte_range", "expected"), [(ByteRange(150, None), (150, 1023)), (ByteRange(95, 305), (95, 305))])
def test_public_reader_chunked_range_crosses_chunk_boundaries(chunked_upstream, byte_range, expected) -> None:
    start, end = expected
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    response = reader.open(_chunked_track(chunked_upstream["url"]), byte_range)
    try:
        assert response.status_code == 206
        assert response.headers["Content-Range"] == f"bytes {start}-{end}/1024"
        assert response.headers["Content-Length"] == str(end - start + 1)
        assert b"".join(response.body) == _CHUNKED_MEDIA[start : end + 1]
    finally:
        response.close()


def test_public_reader_surfaces_first_chunk_refusal_as_status(chunked_upstream) -> None:
    chunked_upstream["fail_from"] = 0
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    response = reader.open(_chunked_track(chunked_upstream["url"]), None)
    response.close()
    assert response.status_code == 403


def test_public_reader_aborts_when_a_later_chunk_fails(chunked_upstream) -> None:
    chunked_upstream["fail_from"] = 300
    chunked_upstream["fail_status"] = 500
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    response = reader.open(_chunked_track(chunked_upstream["url"]), None)
    received = bytearray()
    try:
        assert response.status_code == 200
        with pytest.raises(UnsupportedPlaybackError):
            for chunk in response.body:
                received.extend(chunk)
    finally:
        response.close()
    assert bytes(received) == _CHUNKED_MEDIA[:300]


def test_public_reader_without_chunk_size_issues_one_request(chunked_upstream) -> None:
    reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
    response = reader.open(_chunked_track(chunked_upstream["url"], chunk_size=None), ByteRange(10, 50))
    try:
        assert response.status_code == 206
        assert b"".join(response.body) == _CHUNKED_MEDIA[10:51]
    finally:
        response.close()
    assert chunked_upstream["requests"] == ["bytes=10-50"]


def test_track_builder_reads_http_chunk_size() -> None:
    from app.services.remote_streaming import _track

    base = {"url": "https://media.example/v", "format_id": "137", "protocol": "https", "ext": "mp4"}
    assert _track({**base, "downloader_options": {"http_chunk_size": 10485760}}).chunk_size == 10485760
    assert _track({**base, "downloader_options": {"http_chunk_size": 0}}).chunk_size is None
    assert _track(base).chunk_size is None


# --- Incremental packaging (#138) ---

@pytest.fixture
def idle_ffmpeg(monkeypatch):  # noqa: ANN001, ANN201
    """Replace FFmpeg with an idle child and record every spawned process."""
    import sys

    spawned: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(adapters, "_pipe_command", lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(adapters.subprocess, "Popen", popen)
    return spawned


def _only_leases_left(root: Path) -> bool:
    return list((root / "remote-streams").glob("**/*")) == [root / "remote-streams" / ".leases"]


def _eventually(check, timeout: float = 3) -> bool:  # noqa: ANN001
    from time import monotonic, sleep

    deadline = monotonic() + timeout
    while not check():
        if monotonic() > deadline:
            return False
        sleep(0.02)
    return True


def test_hls_packager_enforces_the_aggregate_budget_while_streaming(tmp_path: Path, idle_ffmpeg) -> None:  # noqa: ANN001
    class EndlessReader:
        def open(self, media_track, byte_range, timeout_seconds=None):  # noqa: ANN001
            del media_track, byte_range, timeout_seconds
            return UpstreamMediaResponse(200, {}, iter(lambda: b"x" * 64, None))

    packager = adapters.FfmpegHlsPackager(reader=EndlessReader(), temp_root=tmp_path, ffmpeg_path="ffmpeg", max_total_bytes=4096)  # type: ignore[arg-type]
    unknown = replace(track(), content_length=None)
    with pytest.raises(UnsupportedPlaybackError, match="aggregate budget"):
        packager.prepare("budget", 1, unknown, unknown)

    assert all(process.poll() is not None for process in idle_ffmpeg)
    assert _only_leases_left(tmp_path)


def test_hls_packager_fails_when_packaging_stops_making_progress(tmp_path: Path, idle_ffmpeg) -> None:  # noqa: ANN001
    now = [0.0]
    stalled = Event()

    class StallingReader:
        def open(self, media_track, byte_range, timeout_seconds=None):  # noqa: ANN001
            del media_track, byte_range, timeout_seconds

            def body():
                yield b"first"
                stalled.set()
                Event().wait(2)  # an upstream that stops sending

            return UpstreamMediaResponse(200, {}, body())

    packager = adapters.FfmpegHlsPackager(reader=StallingReader(), temp_root=tmp_path, ffmpeg_path="ffmpeg", timeout_seconds=10, clock=lambda: now[0])  # type: ignore[arg-type]
    # Once both reads have delivered their bytes (progress at t=0), time passes with nothing new.
    Thread(target=lambda: (stalled.wait(2), Event().wait(0.5), now.__setitem__(0, 11.0)), daemon=True).start()
    unknown = replace(track(), content_length=None)
    with pytest.raises(UnsupportedPlaybackError, match="stopped making progress"):
        packager.prepare("stalled", 1, unknown, unknown)

    assert all(process.poll() is not None for process in idle_ffmpeg)
    assert _only_leases_left(tmp_path)


def test_hls_packager_close_generation_cancels_an_in_flight_preparation(tmp_path: Path, idle_ffmpeg) -> None:  # noqa: ANN001
    reading = Event()
    closed: list[str] = []

    class EndlessReader:
        def open(self, media_track, byte_range, timeout_seconds=None):  # noqa: ANN001
            del media_track, byte_range, timeout_seconds

            def body():
                while True:
                    reading.set()
                    yield b"x"

            return UpstreamMediaResponse(200, {}, body(), lambda: closed.append("closed"))

    packager = adapters.FfmpegHlsPackager(reader=EndlessReader(), temp_root=tmp_path, ffmpeg_path="ffmpeg", max_total_bytes=1 << 40)  # type: ignore[arg-type]
    errors: list[BaseException] = []

    def prepare() -> None:
        try:
            packager.prepare("cancelled", 1, replace(track(), content_length=None), replace(track(), content_length=None))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = Thread(target=prepare)
    worker.start()
    assert reading.wait(timeout=2)
    packager.close_generation("cancelled", 1)
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert "newer request" in str(errors[0])
    assert _eventually(lambda: closed == ["closed", "closed"])  # both upstream reads stopped
    assert all(process.poll() is not None for process in idle_ffmpeg)  # FFmpeg killed
    assert _only_leases_left(tmp_path)


def _fragmented_fixture(tmp_path: Path, seconds: int) -> dict[str, bytes]:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    video, audio = tmp_path / "fixture-video.mp4", tmp_path / "fixture-audio.m4a"
    fragmented = ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=25:duration={seconds}", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", *fragmented, str(video)], check=True)
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-vn", "-c:a", "aac", *fragmented, str(audio)], check=True)
    return {"video": video.read_bytes(), "audio": audio.read_bytes()}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_real_ffmpeg_packager_creates_tiny_playable_hls(tmp_path: Path) -> None:
    payloads = _fragmented_fixture(tmp_path, 1)

    class FixtureReader:
        def open(self, media_track, byte_range, timeout_seconds=None):  # noqa: ANN001
            del byte_range, timeout_seconds
            return UpstreamMediaResponse(200, {}, [payloads[media_track.format_id]])

    video = replace(track(format_id="video"), content_length=len(payloads["video"]), acodec="none")
    audio = replace(track(format_id="audio"), content_length=len(payloads["audio"]), ext="m4a", vcodec="none", acodec="mp4a.40.2")
    packager = adapters.FfmpegHlsPackager(reader=FixtureReader(), temp_root=tmp_path / "package", ffmpeg_path=shutil.which("ffmpeg"), max_total_bytes=5 * 1024 * 1024)  # type: ignore[arg-type]

    presentation = packager.prepare("real-ffmpeg", 1, video, audio)

    manifest = presentation.manifest.read_bytes()
    assert manifest.startswith(b"#EXTM3U") and b"#EXT-X-ENDLIST" in manifest  # short: finished before two segments
    assert "init.mp4" in presentation.assets and any(name.endswith(".m4s") for name in presentation.assets)
    assert presentation.assets.get("video.mp4") is None and presentation.assets.get("../manifest.m3u8") is None
    assert presentation.done is not None and presentation.done.done()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_first_playlist_arrives_before_a_throttled_long_source_finishes_downloading(tmp_path: Path) -> None:
    """A 40 s source through a throttled public upstream: playable after its first segments, finished later."""
    import http.server
    from time import monotonic, sleep

    payloads = _fragmented_fixture(tmp_path, 40)
    total = sum(len(body) for body in payloads.values())
    served = [0]
    bytes_per_second = total / 3  # the whole source takes ~3 s to download

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = payloads[self.path.strip("/")]
            start, end = (int(part) for part in self.headers["Range"].removeprefix("bytes=").split("-"))
            end = min(end, len(body) - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            for offset in range(start, end + 1, 8192):
                piece = body[offset : min(offset + 8192, end + 1)]
                self.wfile.write(piece)
                served[0] += len(piece)
                sleep(len(piece) / bytes_per_second)

        def log_message(self, *args):  # noqa: ANN001
            del args

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        video = replace(track(format_id="video", url=f"{base}/video"), content_length=len(payloads["video"]), acodec="none", chunk_size=64 * 1024)
        audio = replace(track(format_id="audio", url=f"{base}/audio"), content_length=len(payloads["audio"]), ext="m4a", vcodec="none", acodec="mp4a.40.2", chunk_size=64 * 1024)
        reader = adapters.PublicMediaReader(policy=_LoopbackPolicy())
        reader.parallel_pieces = 1  # this paces each request: one at a time keeps "served" a measure of the packager alone
        packager = adapters.FfmpegHlsPackager(reader=reader, temp_root=tmp_path / "package", ffmpeg_path=shutil.which("ffmpeg"))  # type: ignore[arg-type]
        started = monotonic()
        presentation = packager.prepare("throttled", 1, video, audio)
        first_playlist = monotonic() - started
        first_fraction = served[0] / total
        manifest = presentation.manifest.read_bytes()
        assert b"#EXT-X-ENDLIST" not in manifest and manifest.count(b"#EXTINF") >= 2
        assert first_fraction < 0.6, (first_playlist, first_fraction)
        presentation.done.result(timeout=15)  # type: ignore[union-attr]
        assert _eventually(lambda: b"#EXT-X-ENDLIST" in presentation.manifest.read_bytes(), timeout=10)
        finished = monotonic() - started
        assert finished > first_playlist
        segments = presentation.manifest.read_bytes().count(b"#EXTINF")
        assert segments >= 6 and all(f"segment-{index:05d}.m4s" in presentation.assets for index in range(segments))
        print(f"first playlist {first_playlist:.2f}s at {first_fraction:.0%} downloaded; complete {finished:.2f}s, {segments} segments")
    finally:
        server.shutdown()
