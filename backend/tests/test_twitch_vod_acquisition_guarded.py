"""End-to-end guarded acquisition of a Twitch VOD through the native HLS transport.

These tests drive yt-dlp's real native HLS downloader through the production
``PolicyYoutubeDL`` against a local HLS tree, so every manifest, segment,
initialization resource, and key is fetched through the same policy-guarded
transport the #92 relay uses. The production policy rejects loopback, so — like
the relay's own fail-closed transport tests — a ``LoopbackHlsPolicy`` permits the
fixture origin to connect while still failing closed on any private child.

Covered acceptance criteria: every HLS resource passes through the guarded
transport; Saved-sign-in headers are applied server-side and never exposed;
bounded fragment progress; malicious child addresses (private segment, key, map,
and redirect) fail closed; the finished file plays through the Library-media
route without the upstream; and member isolation on that route.
"""

from __future__ import annotations

import http.server
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yt_dlp
from yt_dlp.networking.exceptions import RequestError
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import settings
from app.models import LibraryItem
from app.services.media_artifacts import MediaArtifactService
from app.services.network_policy import PolicyYoutubeDL, PublicSourcePolicyError
from support import make_user, memory_session_factory

SEG0 = b"SEG0-media-bytes-0123456789ABCDEF"
SEG1 = b"SEG1-media-bytes-FEDCBA9876543210"
MEMBER_SECRET = "OAuth saved-sign-in-secret-must-not-escape"


class _Fixture(http.server.BaseHTTPRequestHandler):
    routes: dict[str, bytes] = {}
    redirects: dict[str, str] = {}
    statuses: dict[str, int] = {}
    seen: list[tuple[str, dict[str, str]]] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, dict(self.headers)))
        if self.path in self.statuses:
            self.send_response(self.statuses[self.path])
            self.end_headers()
            return
        if self.path in self.redirects:
            self.send_response(302)
            self.send_header("Location", self.redirects[self.path])
            self.end_headers()
            return
        body = self.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        del args


@contextmanager
def hls_fixture(
    routes: dict[str, bytes],
    redirects: dict[str, str] | None = None,
    statuses: dict[str, int] | None = None,
):
    handler = type(
        "BoundFixture",
        (_Fixture,),
        {"routes": dict(routes), "redirects": dict(redirects or {}), "statuses": dict(statuses or {}), "seen": []},
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], handler
    finally:
        server.shutdown()
        thread.join(timeout=5)


class LoopbackHlsPolicy:
    """Permit the loopback fixture origin; fail closed on any private child.

    Mirrors ``RedirectTestPolicy`` in test_remote_streaming_adapters: the real
    policy rejects loopback, so serving a real local HLS tree needs a test policy
    that admits the fixture origin while still refusing private segment, key,
    map, and redirect targets. Every non-loopback host is refused *before* a
    socket address is ever produced, so a private child can never be connected to.
    """

    def __init__(self) -> None:
        self.validated: list[str] = []
        self.resolved: list[str] = []
        self.connected: list[str] = []

    def _is_loopback(self, host: str | None) -> bool:
        return host == "127.0.0.1"

    def validate_url(self, value):  # noqa: ANN001
        self.validated.append(value)
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not self._is_loopback(parsed.hostname):
            raise PublicSourcePolicyError()
        return value

    def resolve(self, host, port):  # noqa: ANN001
        self.resolved.append(host)
        if not self._is_loopback(host):
            raise PublicSourcePolicyError()
        self.connected.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]


def _media_playlist(*segments: str, key_uri: str | None = None, map_uri: str | None = None) -> bytes:
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
    if map_uri is not None:
        lines.append(f'#EXT-X-MAP:URI="{map_uri}"')
    if key_uri is not None:
        lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="{key_uri}"')
    for segment in segments:
        lines.append("#EXTINF:2.0,")
        lines.append(segment)
    lines.append("#EXT-X-ENDLIST")
    return ("\n".join(lines) + "\n").encode()


def _download_params(tmp_path: Path) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "proxy": "",
        "outtmpl": {"default": str(tmp_path / "%(id)s.%(ext)s")},
        "paths": {"home": str(tmp_path), "temp": str(tmp_path)},
        "format": "best",
        "hls_prefer_native": True,
        "external_downloader": {"default": "native"},
        "skip_unavailable_fragments": False,
        "fixup": "never",
        "overwrites": True,
        "retries": 0,
        "fragment_retries": 0,
    }


def _vod_info(port: int, *, path: str = "/media.m3u8", headers: dict[str, str] | None = None) -> dict:
    fmt = {
        "format_id": "720p60",
        "protocol": "m3u8_native",
        "url": f"http://127.0.0.1:{port}{path}",
        "ext": "mp4",
        "vcodec": "avc1.4d401f",
        "acodec": "mp4a.40.2",
        "height": 720,
    }
    if headers is not None:
        fmt["http_headers"] = dict(headers)
    return {
        "id": "vod-1",
        "title": "Fixture Twitch VOD",
        "extractor": "twitch",
        "extractor_key": "Twitch",
        "webpage_url": f"http://127.0.0.1:{port}/videos/1",
        "was_live": True,
        "uploader": "Fixture Streamer",
        "duration": 4,
        "formats": [fmt],
    }


def _acquire(tmp_path: Path, policy: LoopbackHlsPolicy, info: dict, *, progress_hooks=None):
    params = _download_params(tmp_path)
    if progress_hooks is not None:
        params["progress_hooks"] = progress_hooks
    with PolicyYoutubeDL(params, policy=policy) as ydl:
        ydl.process_ie_result(dict(info), download=True)


def test_guarded_hls_acquisition_produces_a_local_file_through_the_guarded_transport(tmp_path) -> None:
    policy = LoopbackHlsPolicy()
    progress: list[dict] = []
    routes = {"/media.m3u8": _media_playlist("seg0.ts", "seg1.ts"), "/seg0.ts": SEG0, "/seg1.ts": SEG1}
    with hls_fixture(routes) as (port, handler):
        _acquire(
            tmp_path,
            policy,
            _vod_info(port, headers={"Authorization": MEMBER_SECRET, "Referer": "https://www.twitch.tv/"}),
            progress_hooks=[progress.append],
        )

    output = tmp_path / "vod-1.mp4"
    # A real, local, playable file materialized from the two fetched segments.
    assert output.is_file()
    assert output.read_bytes() == SEG0 + SEG1

    # Every fetched address was validated and resolved through the guarded policy;
    # only the loopback origin was ever connected to.
    assert set(policy.connected) == {"127.0.0.1"}
    fetched_paths = {path for path, _headers in handler.seen}
    assert fetched_paths == {"/media.m3u8", "/seg0.ts", "/seg1.ts"}

    # The member's Saved-sign-in headers were applied server-side to the manifest
    # and every segment fetch, and never leaked into the produced file.
    for _path, headers in handler.seen:
        assert headers.get("Authorization") == MEMBER_SECRET
    assert MEMBER_SECRET.encode() not in output.read_bytes()

    # Bounded, useful fragment progress was reported: the total is known and the
    # per-fragment index advances to it across multiple callbacks.
    downloading = [hook for hook in progress if hook.get("status") == "downloading"]
    assert len(downloading) >= 2
    assert {hook.get("fragment_count") for hook in downloading} == {2}
    assert 2 in {hook.get("fragment_index") for hook in downloading}


def test_finished_file_plays_through_the_library_media_route_without_the_upstream(tmp_path, monkeypatch) -> None:
    policy = LoopbackHlsPolicy()
    routes = {"/media.m3u8": _media_playlist("seg0.ts", "seg1.ts"), "/seg0.ts": SEG0, "/seg1.ts": SEG1}
    with hls_fixture(routes) as (port, _handler):
        _acquire(tmp_path, policy, _vod_info(port))
    output = tmp_path / "vod-1.mp4"
    assert output.is_file()
    # The fixture (the Twitch analogue) is now shut down; playback must not need it.
    settings.library_root.mkdir(parents=True, exist_ok=True)
    output = output.rename(settings.library_root / output.name)

    session_factory = memory_session_factory()
    owner = make_user("owner-1", username="owner", display_name="Owner")
    other = make_user("other-1", username="other", display_name="Other")
    item = LibraryItem(
        id="item-1",
        user_id=owner.id,
        visibility="private",
        extractor="Twitch",
        remote_id="vod-1",
        title="Fixture Twitch VOD",
        file_path=str(output),
        status="available",
    )
    with session_factory.begin() as session:
        session.add_all([owner, other, item])
        session.flush()
        MediaArtifactService(session).register_file(item, str(output))

    monkeypatch.setattr(main_module, "SessionLocal", session_factory)
    current = {"user": owner}
    monkeypatch.setattr(main_module, "resolve_request_user_snapshot", lambda *args, **kwargs: current["user"])
    client = TestClient(main_module.app, base_url="http://localhost", raise_server_exceptions=False)
    try:
        played = client.get(f"/api/library/{item.id}/media")
        assert played.status_code == 200
        assert played.content == SEG0 + SEG1

        # Member isolation: another household member cannot read the private file.
        current["user"] = other
        blocked = client.get(f"/api/library/{item.id}/media")
        assert blocked.status_code == 404
    finally:
        client.close()


def _assert_fails_closed(tmp_path: Path, policy: LoopbackHlsPolicy, info: dict) -> None:
    # The guarded transport surfaces a policy denial as a RequestError; yt-dlp may
    # also wrap it as a DownloadError. Either way the acquisition aborts, and the
    # worker's broad except turns it into a redacted job failure in production.
    with pytest.raises((PublicSourcePolicyError, yt_dlp.utils.DownloadError, RequestError)) as caught:
        _acquire(tmp_path, policy, info)
    message = str(caught.value) + str(getattr(caught.value, "__cause__", ""))
    assert "Public source policy" in message
    # No partial file is published for a manifest with a rejected child.
    assert not (tmp_path / "vod-1.mp4").exists()


def test_guarded_hls_acquisition_fails_closed_on_a_private_segment(tmp_path) -> None:
    policy = LoopbackHlsPolicy()
    routes = {"/media.m3u8": _media_playlist("seg0.ts", "http://169.254.169.254/latest/evil.ts"), "/seg0.ts": SEG0}
    with hls_fixture(routes) as (port, _handler):
        _assert_fails_closed(tmp_path, policy, _vod_info(port))
    # The private metadata host was refused before any socket connected to it.
    assert "169.254.169.254" not in policy.connected


def test_guarded_hls_acquisition_fails_closed_on_a_private_key(tmp_path) -> None:
    policy = LoopbackHlsPolicy()
    routes = {"/media.m3u8": _media_playlist("seg0.ts", key_uri="http://10.0.0.5/key.bin"), "/seg0.ts": SEG0}
    with hls_fixture(routes) as (port, _handler):
        _assert_fails_closed(tmp_path, policy, _vod_info(port))
    assert "10.0.0.5" not in policy.connected


def test_guarded_hls_acquisition_fails_closed_on_a_private_map(tmp_path) -> None:
    policy = LoopbackHlsPolicy()
    routes = {"/media.m3u8": _media_playlist("seg0.ts", map_uri="http://10.0.0.9/init.mp4"), "/seg0.ts": SEG0}
    with hls_fixture(routes) as (port, _handler):
        _assert_fails_closed(tmp_path, policy, _vod_info(port))
    assert "10.0.0.9" not in policy.connected


def test_guarded_hls_acquisition_fails_closed_on_a_redirect_into_private_space(tmp_path) -> None:
    policy = LoopbackHlsPolicy()
    routes = {"/media.m3u8": _media_playlist("seg0.ts"), "/seg0.ts": SEG0}
    redirects = {"/seg0.ts": "http://169.254.169.254/latest/meta-data/"}
    with hls_fixture(routes, redirects) as (port, _handler):
        _assert_fails_closed(tmp_path, policy, _vod_info(port))
    # The private redirect target was revalidated and never connected to.
    assert "169.254.169.254" not in policy.connected


def test_guarded_hls_acquisition_honors_mid_download_cancellation(tmp_path) -> None:
    # The worker checks its cancel flag inside the same per-fragment progress hook
    # this exercises; a raised cancellation aborts the fragmented download and
    # publishes no partial file.
    from app.services.yt_dlp_service import DownloadCancelled

    policy = LoopbackHlsPolicy()

    def cancel_on_first_fragment(status: dict) -> None:
        if status.get("status") == "downloading":
            raise DownloadCancelled("member cancelled")

    routes = {"/media.m3u8": _media_playlist("seg0.ts", "seg1.ts"), "/seg0.ts": SEG0, "/seg1.ts": SEG1}
    with hls_fixture(routes) as (port, _handler):
        with pytest.raises(DownloadCancelled):
            _acquire(tmp_path, policy, _vod_info(port), progress_hooks=[cancel_on_first_fragment])
    assert not (tmp_path / "vod-1.mp4").exists()


def _no_ffmpeg_delegation(monkeypatch) -> list[str]:
    """Record — instead of running — any FFmpegFD delegation.

    FFmpegFD fetches its manifest and every child URL through a spawned ffmpeg
    process, entirely outside the guarded transport. If the guard ever let HlsFD
    delegate here, this records the attempt (and the test fails) rather than
    performing a real unguarded fetch.
    """

    from yt_dlp.downloader.external import FFmpegFD

    delegated: list[str] = []

    def refuse(self, filename, info_dict):  # noqa: ANN001
        delegated.append(info_dict.get("url"))
        return True

    monkeypatch.setattr(FFmpegFD, "real_download", refuse)
    return delegated


def test_guarded_hls_acquisition_refuses_ffmpeg_delegation_for_a_hostile_encrypted_manifest(tmp_path, monkeypatch) -> None:
    # TM-010: a genuine Twitch URL (tracer, native HLS admitted pre-fetch) whose
    # upstream serves a SAMPLE-AES manifest. The pre-fetch gate cannot see the
    # body; unguarded yt-dlp would hand this to FFmpegFD, which would fetch the
    # attacker's private key and segment outside the policy and forward the
    # member's credential. The guard must fail closed before any delegation.
    delegated = _no_ffmpeg_delegation(monkeypatch)
    policy = LoopbackHlsPolicy()
    hostile = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n"
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="http://169.254.169.254/key.bin"\n'
        "#EXTINF:2.0,\nhttp://169.254.169.254/latest/seg0.ts\n#EXT-X-ENDLIST\n"
    ).encode()
    with hls_fixture({"/media.m3u8": hostile}) as (port, handler):
        with pytest.raises((PublicSourcePolicyError, yt_dlp.utils.DownloadError, RequestError)):
            _acquire(tmp_path, policy, _vod_info(port, headers={"Authorization": MEMBER_SECRET}))

    assert delegated == []  # ffmpeg delegation refused: no unguarded fetch was ever started
    assert not (tmp_path / "vod-1.mp4").exists()
    assert "169.254.169.254" not in policy.connected  # the attacker child was never contacted
    # Only the guarded manifest fetch happened; the member credential never went
    # anywhere but the guarded loopback origin.
    assert {path for path, _headers in handler.seen} == {"/media.m3u8"}


def test_guarded_hls_acquisition_hard_fails_a_drm_manifest_without_delegating(tmp_path, monkeypatch) -> None:
    delegated = _no_ffmpeg_delegation(monkeypatch)
    policy = LoopbackHlsPolicy()
    drm = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n"
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key-id",KEYFORMAT="com.apple.streamingkeydelivery"\n'
        "#EXTINF:2.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    ).encode()
    with hls_fixture({"/media.m3u8": drm, "/seg0.ts": SEG0}) as (port, _handler):
        with pytest.raises((PublicSourcePolicyError, yt_dlp.utils.DownloadError, RequestError)):
            _acquire(tmp_path, policy, _vod_info(port))
    assert delegated == []  # DRM hard-fails without a spawned ffmpeg fetch
    assert not (tmp_path / "vod-1.mp4").exists()


def test_guarded_hls_acquisition_fails_closed_on_an_expired_signed_segment(tmp_path) -> None:
    # An expired signed resource surfaces mid-download as an upstream rejection.
    # With unavailable-fragment skipping off the job aborts rather than publishing
    # a truncated file; the acquisition is then re-resolved by the existing retry
    # path, which re-extracts fresh signed addresses.
    policy = LoopbackHlsPolicy()
    routes = {"/media.m3u8": _media_playlist("seg0.ts", "seg1.ts"), "/seg0.ts": SEG0}
    statuses = {"/seg1.ts": 403}
    with hls_fixture(routes, statuses=statuses) as (port, _handler):
        with pytest.raises((PublicSourcePolicyError, yt_dlp.utils.DownloadError, RequestError)):
            _acquire(tmp_path, policy, _vod_info(port))
    assert not (tmp_path / "vod-1.mp4").exists()


@pytest.mark.parametrize("redirect_master", [False, True])
def test_cross_origin_hls_children_drop_credentials_including_merged_defaults_and_cookies(tmp_path, redirect_master: bool) -> None:
    from yt_dlp.dependencies import Cryptodome

    key = b'0' * 16
    padding = 16 - len(SEG0) % 16
    encrypted = Cryptodome.AES.new(key, Cryptodome.AES.MODE_CBC, b'\0' * 16).encrypt(SEG0 + bytes([padding]) * padding)
    with hls_fixture({'/init.mp4': b'INIT', '/seg0.ts': encrypted, '/key.bin': key}) as (child_port, child):
        child_base = f'http://127.0.0.1:{child_port}'
        playlist = _media_playlist(f'{child_base}/seg0.ts', key_uri=f'{child_base}/key.bin', map_uri=f'{child_base}/init.mp4')
        playlist = playlist.replace(b'#EXT-X-KEY:METHOD=AES-128,', b'#EXT-X-KEY:METHOD=AES-128,IV=0x00000000000000000000000000000000,')
        if redirect_master:
            child.routes['/redirected.m3u8'] = playlist
        routes = {} if redirect_master else {'/media.m3u8': playlist}
        redirects = {'/media.m3u8': f'{child_base}/redirected.m3u8'} if redirect_master else {}
        with hls_fixture(routes, redirects=redirects) as (port, source):
            params = _download_params(tmp_path)
            params['http_headers'] = {'X-Global-Api-Key': 'synthetic'}
            params['concurrent_fragment_downloads'] = 2
            headers = {'Authorization': 'synthetic', 'Cookie': 'synthetic=1', 'Proxy-Authorization': 'synthetic',
                       'X-API-Key': 'synthetic', 'X_AUTH_TOKEN': 'synthetic', 'X-Custom-Credential': 'synthetic',
                       'Referer': 'https://www.twitch.tv/', 'User-Agent': 'fixture-player'}
            with PolicyYoutubeDL(params, policy=LoopbackHlsPolicy()) as ydl:
                ydl.process_ie_result(_vod_info(port, headers=headers), download=True)
    assert (tmp_path / 'vod-1.mp4').read_bytes() == b'INIT' + SEG0
    assert any(name.lower() == 'authorization' for _path, received in source.seen for name in received)
    assert {path for path, _received in child.seen} == {'/init.mp4', '/seg0.ts', '/key.bin'} | ({'/redirected.m3u8'} if redirect_master else set())
    forbidden = {'authorization', 'cookie', 'proxy-authorization', 'x-api-key', 'x_auth_token', 'x-custom-credential', 'x-global-api-key', 'referer'}
    assert all(not forbidden.intersection(name.lower() for name in received) for _path, received in child.seen)
    assert all(received.get('User-Agent') == 'fixture-player' for _path, received in child.seen)


@pytest.mark.parametrize('resource', ['manifest', 'key'])
def test_native_hls_bulk_control_reads_are_bounded_before_allocation(tmp_path, monkeypatch, resource: str) -> None:
    import http.client
    from app.services import network_policy

    limit = 256
    original_init = network_policy.SafeHTTPHandler.__init__
    original_read = http.client.HTTPResponse.read
    read_sizes = []
    capped_responses = []

    def bounded_handler(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        kwargs['error_response_limit'] = limit
        original_init(self, *args, **kwargs)

    def recording_read(self, amt=None):  # noqa: ANN001
        read_sizes.append(amt)
        if self.getheader('Content-Length') == str(limit + 1):
            capped_responses.append(self)
        return original_read(self, amt)

    monkeypatch.setattr(network_policy.SafeHTTPHandler, '__init__', bounded_handler)
    monkeypatch.setattr(http.client.HTTPResponse, 'read', recording_read)
    if resource == 'manifest':
        playlist = _media_playlist('seg0.ts')
        playlist += b'#' + b'x' * (limit - len(playlist))
        routes = {'/media.m3u8': playlist, '/seg0.ts': SEG0}
    else:
        routes = {'/media.m3u8': _media_playlist('seg0.ts', key_uri='key.bin'), '/seg0.ts': SEG0, '/key.bin': b'k' * (limit + 1)}
    with hls_fixture(routes) as (port, handler):
        with pytest.raises((yt_dlp.utils.DownloadError, RequestError, OSError), match='safe byte limit'):
            _acquire(tmp_path, LoopbackHlsPolicy(), _vod_info(port))
    assert limit + 1 in read_sizes
    assert all(size is not None and 0 <= size <= limit + 1 for size in read_sizes)
    assert capped_responses and all(response.closed for response in capped_responses)
    assert not (tmp_path / 'vod-1.mp4').exists()
    if resource == 'manifest':
        assert {path for path, _headers in handler.seen} == {'/media.m3u8'}


def test_native_hls_identity_segment_larger_than_eight_mib_streams_in_bounded_blocks(tmp_path, monkeypatch) -> None:
    import http.client
    from app.services.network_policy import MAX_YTDLP_METADATA_RESPONSE_BYTES

    limit = MAX_YTDLP_METADATA_RESPONSE_BYTES
    segment = b'm' * (limit + 1)
    original_read = http.client.HTTPResponse.read
    segment_reads = []

    def recording_read(self, amt=None):  # noqa: ANN001
        if self.getheader('Content-Length') == str(len(segment)):
            segment_reads.append(amt)
        return original_read(self, amt)

    monkeypatch.setattr(http.client.HTTPResponse, 'read', recording_read)
    with hls_fixture({'/media.m3u8': _media_playlist('large.ts'), '/large.ts': segment}) as (port, _handler):
        _acquire(tmp_path, LoopbackHlsPolicy(), _vod_info(port))
    assert (tmp_path / 'vod-1.mp4').read_bytes() == segment
    assert len(segment_reads) > 1
    assert segment_reads[0] == 1024  # HttpFD's ordinary initial buffer
    assert all(size is not None and 0 < size <= 4 * 1024 * 1024 < limit for size in segment_reads)
