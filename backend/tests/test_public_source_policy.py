from __future__ import annotations

import gzip
import io
import socket
import urllib.request
import zlib
from email.message import Message
from urllib.response import addinfourl

import pytest
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking import Request
from yt_dlp.networking._urllib import UrllibResponseAdapter
from yt_dlp.networking.exceptions import HTTPError, RequestError, TransportError

from app.models import AutomationRun, DownloadJob, SourceAutomation
from app.services.network_policy import (
    MAX_YTDLP_METADATA_RESPONSE_BYTES,
    PUBLIC_SOURCE_POLICY_MESSAGE,
    PolicyYoutubeDL,
    PublicSourcePolicy,
    PublicSourcePolicyError,
    SafeHTTPHandler,
    SafeRedirectHandler,
    SafeUrllibRH,
    create_public_connection,
)
from support import make_user


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
        del family
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (address, port))
            for address in mapping[host]
        ]

    return resolve


class _TrackingBody(io.BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


def _urllib_response(
    body: _TrackingBody,
    *,
    content_encoding: str | tuple[str, ...] | None = None,
    status: int = 200,
):
    headers = Message()
    for value in (content_encoding,) if isinstance(content_encoding, str) else (content_encoding or ()):
        headers.add_header("Content-Encoding", value)
    return addinfourl(body, headers, "https://public.example/metadata", status)


def _metadata_handler(limit: int) -> SafeHTTPHandler:
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    return SafeHTTPHandler(policy, response_limit=lambda: limit)


class _DecoderProbeHTTPHandler(SafeHTTPHandler):
    def __init__(self, policy: PublicSourcePolicy, *, response_limit, error_response_limit=32):  # noqa: ANN001
        super().__init__(
            policy,
            response_limit=response_limit,
            error_response_limit=error_response_limit,
        )
        self.decoder_calls: list[str] = []

    def gz(self, data: bytes) -> bytes:
        self.decoder_calls.append("gzip")
        return super().gz(data)

    def deflate(self, data: bytes) -> bytes:
        self.decoder_calls.append("deflate")
        return super().deflate(data)

    def brotli(self, data: bytes) -> bytes:
        self.decoder_calls.append("br")
        return super().brotli(data)


def test_metadata_identity_response_fails_at_cap_plus_one_with_a_bounded_read() -> None:
    limit = 32
    body = _TrackingBody(b"x" * (limit + 1))
    response = _metadata_handler(limit).http_response(
        urllib.request.Request("https://public.example/metadata"),
        _urllib_response(body),
    )

    with pytest.raises(OSError, match="safe byte limit"):
        response.read()

    assert body.read_sizes == [limit + 1]


@pytest.mark.parametrize(
    ("encoding", "body"),
    [
        pytest.param("gzip", gzip.compress(b"x" * 4096, mtime=0), id="gzip"),
        pytest.param("deflate", zlib.compress(b"x" * 4096), id="deflate"),
        # The guard rejects an unsolicited Brotli-coded body from its header;
        # no Brotli decoder sees even this compact stand-in payload.
        pytest.param("br", b"compact-brotli-style-expansion", id="br"),
    ],
)
def test_metadata_compressed_expansion_is_rejected_before_decoder_or_body_read(
    encoding: str,
    body: bytes,
) -> None:
    source = _TrackingBody(body)
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: 32)

    with pytest.raises(OSError, match="cannot be safely decoded"):
        handler.http_response(
            urllib.request.Request("https://public.example/metadata"),
            _urllib_response(source, content_encoding=encoding),
        )

    assert source.read_sizes == []
    assert handler.decoder_calls == []


@pytest.mark.parametrize("content_encoding", [("identity", "gzip"), "identity, br"])
def test_alternate_content_encodings_cannot_hide_compressed_media_from_the_guard(
    content_encoding: str | tuple[str, ...],
) -> None:
    source = _TrackingBody(gzip.compress(b"x" * 4096))
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: None)

    with pytest.raises(OSError, match="cannot be safely decoded"):
        handler.http_response(
            urllib.request.Request("https://public.example/media"),
            _urllib_response(source, content_encoding=content_encoding),
        )

    assert source.read_sizes == []
    assert handler.decoder_calls == []


def test_nonidentity_media_is_refused_before_body_read_or_inherited_decode() -> None:
    source = _TrackingBody(gzip.compress(b"x" * 4096))
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: None)

    with pytest.raises(OSError, match="cannot be safely decoded"):
        handler.http_response(
            urllib.request.Request("https://public.example/media"),
            _urllib_response(source, content_encoding="gzip"),
        )

    assert source.read_sizes == []
    assert handler.decoder_calls == []


def test_identity_media_larger_than_the_metadata_cap_remains_streamable() -> None:
    metadata_limit = 32
    body = _TrackingBody(b"m" * (metadata_limit + 1))
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: None)

    response = handler.http_response(
        urllib.request.Request("https://public.example/media"),
        _urllib_response(body, content_encoding="identity"),
    )

    assert response.read(16) + response.read(16) + response.read(16) == b"m" * (metadata_limit + 1)
    assert handler.decoder_calls == []


@pytest.mark.parametrize("status", [302, 500])
def test_metadata_non_success_body_keeps_the_bound_before_ytdlp_exposes_it(status: int) -> None:
    limit = 32
    body = _TrackingBody(b"e" * (limit + 1))
    request = urllib.request.Request("https://public.example/metadata")

    # urllib runs protocol response processors in handler_order: Lumina's
    # bounded HTTP handler wraps the body before HTTPErrorProcessor exposes it.
    assert SafeHTTPHandler.handler_order < urllib.request.HTTPErrorProcessor.handler_order
    bounded = _metadata_handler(limit).http_response(request, _urllib_response(body, status=status))
    error = HTTPError(UrllibResponseAdapter(bounded))

    with pytest.raises(TransportError, match="safe byte limit"):
        error.response.read()

    assert body.read_sizes == [limit + 1]


@pytest.mark.parametrize("status", [302, 500])
def test_media_phase_non_success_body_uses_the_metadata_bound(status: int) -> None:
    limit = 32
    body = _TrackingBody(b"e" * (limit + 1))
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    handler = _DecoderProbeHTTPHandler(
        policy,
        response_limit=lambda: None,
        error_response_limit=limit,
    )

    bounded = handler.http_response(
        urllib.request.Request("https://public.example/media"),
        _urllib_response(body, status=status),
    )

    with pytest.raises(OSError, match="safe byte limit"):
        bounded.read()

    assert body.read_sizes == [limit + 1]
    assert handler.decoder_calls == []


def test_metadata_and_streaming_requests_both_force_identity_encoding() -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    logger = object()
    metadata = SafeUrllibRH(
        policy=policy,
        response_limit=lambda: MAX_YTDLP_METADATA_RESPONSE_BYTES,
        logger=logger,
    )
    streaming = SafeUrllibRH(
        policy=policy,
        response_limit=lambda: None,
        logger=logger,
    )
    metadata_headers: dict[str, str] = {}
    streaming_headers: dict[str, str] = {}

    metadata._prepare_headers(None, metadata_headers)
    streaming._prepare_headers(None, streaming_headers)

    assert metadata_headers["Accept-Encoding"] == "identity"
    assert streaming_headers["Accept-Encoding"] == "identity"
    metadata.close()
    streaming.close()


def test_policy_ytdlp_enables_the_limit_only_while_an_extractor_resolves_metadata() -> None:
    observed_limits: list[int | None] = []

    class LimitProbeIE(InfoExtractor):
        IE_NAME = "limit-probe"
        _VALID_URL = r"limit-probe:(?P<id>.+)"

        def _real_extract(self, url):  # noqa: ANN001, ANN202
            observed_limits.append(self._downloader._metadata_response_limit())
            return {"id": self._match_id(url), "title": "Limit probe"}

    with PolicyYoutubeDL({"quiet": True}) as ydl:
        ydl.add_info_extractor(LimitProbeIE())
        assert ydl._metadata_response_limit() is None
        result = ydl.extract_info("limit-probe:item", download=False, process=False, ie_key="LimitProbe")
        assert ydl._metadata_response_limit() is None

    assert result["id"] == "item"
    assert observed_limits == [MAX_YTDLP_METADATA_RESPONSE_BYTES]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/media",
        "gopher://example.test/",
        "http://127.0.0.1/admin",
        "http://10.0.0.8/media",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/admin",
        "http://[fec0::1]/internal",
        "http://224.0.0.1/stream",
        "http://192.0.2.1/reserved",
    ],
)
def test_policy_rejects_unsupported_or_non_public_destinations(url: str) -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({"example.test": ["93.184.216.34"]}))
    with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
        policy.validate_url(url)


def test_policy_rejects_encoded_loopback_and_mixed_dns_answers() -> None:
    policy = PublicSourcePolicy(
        resolver=resolver_for(
            {
                "2130706433": ["127.0.0.1"],
                "mixed.example": ["93.184.216.34", "10.0.0.4"],
            }
        )
    )

    for url in ("http://2130706433/", "https://mixed.example/media"):
        with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
            policy.validate_url(url)


def test_redirect_target_is_revalidated_before_following() -> None:
    policy = PublicSourcePolicy(
        resolver=resolver_for({"public.example": ["93.184.216.34"], "internal.example": ["127.0.0.1"]})
    )
    handler = SafeRedirectHandler(policy)
    request = urllib.request.Request("https://public.example/start")

    with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://internal.example/admin")


def test_connection_rechecks_dns_and_rejects_rebinding() -> None:
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    def rebinding_resolver(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
        del host, family
        return [(socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (address, port)) for address in next(answers)]

    policy = PublicSourcePolicy(resolver=rebinding_resolver)
    connected: list[str] = []

    def fake_socket(ip_addr, timeout, source_address):
        del timeout, source_address
        connected.append(ip_addr[4][0])
        return object()

    assert create_public_connection(policy, ("rebind.example", 443), _create_socket_func=fake_socket) is not None
    assert connected == ["93.184.216.34"]
    with pytest.raises(PublicSourcePolicyError, match="public HTTP"):
        create_public_connection(policy, ("rebind.example", 443), _create_socket_func=fake_socket)
    assert connected == ["93.184.216.34"]


def test_policy_error_category_is_stable_and_non_reflective() -> None:
    marker = "http://127.0.0.1/sensitive-marker"
    with pytest.raises(PublicSourcePolicyError) as caught:
        PublicSourcePolicy().validate_url(marker)
    assert str(caught.value) == PUBLIC_SOURCE_POLICY_MESSAGE
    assert marker not in str(caught.value)


def test_policy_ytdlp_transport_blocks_extractor_generated_private_requests() -> None:
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}) as ydl:
        with pytest.raises(RequestError, match="Public source policy"):
            ydl.urlopen(Request("http://127.0.0.1/internal"))


@pytest.mark.parametrize(
    "media",
    [
        {"protocol": "rtmp", "url": "rtmp://93.184.216.34/live"},
        {"protocol": "m3u8", "url": "https://media.example/video.m3u8"},
        {"protocol": "m3u8", "url": "https://media.example/live.m3u8", "is_live": True},
        {
            "is_live": True,
            "requested_formats": [{"protocol": "m3u8", "url": "https://media.example/live.m3u8"}],
        },
        {
            "protocol": "https",
            "url": "https://media.example/clip.mp4",
            "section_start": 1,
        },
        {"protocol": "https", "url": "http://127.0.0.1/private.mp4"},
        # A text playlist saved as "media": yt-dlp's ffmpeg postprocessors would follow its
        # file:// / http:// entries (other members' files, the LAN) before publication refuses it.
        {"protocol": "https", "url": "https://media.example/a.m3u", "ext": "m3u"},
        {"protocol": "https", "url": "https://media.example/a", "ext": "M3U8"},
        {"requested_formats": [
            {"protocol": "https", "url": "https://media.example/v.mp4", "ext": "mp4"},
            {"protocol": "https", "url": "https://media.example/a.sdp", "ext": "sdp"},
        ]},
    ],
)
def test_external_or_private_media_transport_is_rejected_before_download(media: dict) -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({"media.example": ["93.184.216.34"]}))
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=policy) as ydl:
        with pytest.raises(PublicSourcePolicyError):
            ydl.validate_download_transport(media)


def test_preview_job_automation_and_retry_share_the_stable_policy_error(monkeypatch, db_factory, api_client) -> None:
    session_factory = db_factory
    user = make_user("user-1", username="viewer", display_name="Viewer")
    with session_factory.begin() as session:
        session.add(user)
        session.add(
            DownloadJob(
                id="legacy-private-job",
                user_id=user.id,
                source_url="http://127.0.0.1/legacy-marker",
                status="failed",
                format_selection={},
                output_profile={},
            )
        )
        session.add(
            SourceAutomation(
                id="legacy-private-automation",
                user_id=user.id,
                label="Legacy private automation",
                source_url="http://127.0.0.1/legacy-automation-marker",
                source_type="generic_url",
                cron_expression="*/30 * * * *",
                active=True,
                auto_download=True,
                format_selection={},
                output_profile={},
                rules={},
                duplicate_policy="skip_same_source",
            )
        )

    monkeypatch.setattr("app.main.resolve_request_user_snapshot", lambda request, credentials=None: user)
    monkeypatch.setattr("app.main.SessionLocal", session_factory)  # the streaming gate reads the member's access
    client = api_client(user=user, base_url="http://localhost")
    hostile_url = "http://127.0.0.1/sensitive-marker"
    rejected_responses = [
        client.post("/api/preview", json={"source_url": hostile_url}),
        client.post("/api/jobs", json={"source_url": hostile_url}),
        client.post("/api/automations", json={"label": "Blocked", "source_url": hostile_url}),
        client.post("/api/jobs/legacy-private-job/retry"),
    ]
    for response in rejected_responses:
        assert response.status_code == 400
        assert response.json() == {"detail": PUBLIC_SOURCE_POLICY_MESSAGE}
        assert "sensitive-marker" not in response.text
        assert "legacy-marker" not in response.text
        assert "legacy-automation-marker" not in response.text

    automation_response = client.post("/api/automations/legacy-private-automation/run")
    assert automation_response.status_code == 200
    assert automation_response.json()["status"] == "failed"
    assert automation_response.json()["error"] == PUBLIC_SOURCE_POLICY_MESSAGE
    assert "legacy-automation-marker" not in automation_response.text

    with session_factory() as session:
        assert session.query(DownloadJob).count() == 1
        assert session.query(SourceAutomation).count() == 1
        assert session.get(DownloadJob, "legacy-private-job").status == "failed"
        assert session.query(AutomationRun).one().status == "failed"


def test_a_playlist_rewrite_resolves_each_host_once_but_still_checks_every_url() -> None:
    calls: list[str] = []
    base = resolver_for({"cdn.example": ["93.184.216.34"]})

    def counting(host, port, family, kind):  # noqa: ANN001
        calls.append(host)
        return base(host, port, family, kind)

    policy = PublicSourcePolicy(resolver=counting)
    resolved: set[tuple[str, int]] = set()
    for n in range(450):
        policy.validate_url(f"https://cdn.example/seg/{n}.ts", resolved)
    assert calls == ["cdn.example"]
    with pytest.raises(PublicSourcePolicyError):
        policy.validate_url("https://user:pw@cdn.example/seg/1.ts", resolved)  # per-URL checks still run
    policy.validate_url("https://cdn.example/seg/1.ts")  # without the set every call resolves
    assert calls == ["cdn.example", "cdn.example"]


@pytest.mark.parametrize("url", ["http://public.example:22/", "https://public.example:25/x", "http://93.184.216.34:6379/", "http://public.example:8096/"])
def test_policy_admits_only_web_ports(url: str) -> None:
    """A member-supplied source cannot turn Lumina into a port scanner or a client for SSH, SMTP, Redis, or a
    non-web service published on the household's own public address."""
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    with pytest.raises(PublicSourcePolicyError):
        policy.validate_url(url)
    with pytest.raises(PublicSourcePolicyError):  # every connection, including redirects and manifest children
        create_public_connection(policy, ("public.example", int(url.rsplit(":", 1)[1].split("/")[0])))


def test_policy_admits_explicit_default_ports() -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({"public.example": ["93.184.216.34"]}))
    for url in ("http://public.example:80/a", "https://public.example:443/a", "https://public.example/a"):
        assert policy.validate_url(url) == url


def test_an_admins_extra_ports_admit_public_hosts_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import network_policy

    monkeypatch.setattr(network_policy, "_extra_ports", frozenset())
    network_policy.set_extra_ports([8000, 22, "8080", True])  # hand-edited JSON: only the valid, safe int survives
    assert network_policy._extra_ports == {8000}
    policy = PublicSourcePolicy(resolver=resolver_for({"radio.example": ["93.184.216.34"], "lan.example": ["192.168.1.5"]}))
    assert policy.validate_url("http://radio.example:8000/stream") == "http://radio.example:8000/stream"
    for url in ("http://lan.example:8000/stream", "http://10.0.0.8:8000/stream", "http://radio.example:8080/", "http://radio.example:22/"):
        with pytest.raises(PublicSourcePolicyError):
            policy.validate_url(url)


@pytest.mark.parametrize('target', ['https://other.example/path', 'http://public.example/path', 'https://public.example:80/path'])
def test_redirect_strips_all_non_public_headers_across_origins(target: str) -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({'public.example': ['93.184.216.34'], 'other.example': ['93.184.216.34']}))
    request = urllib.request.Request('https://public.example/start', headers={
        'aUtHoRiZaTiOn': 'synthetic', 'cOoKiE': 'synthetic=1', 'pRoXy-AuThOrIzAtIoN': 'synthetic',
        'x-API-key': 'synthetic', 'X_AUTH_TOKEN': 'synthetic', 'X-Custom-Credential': 'synthetic',
        'Referer': 'https://public.example/private', 'User-Agent': 'fixture-player', 'Range': 'bytes=0-9',
    })
    request.add_unredirected_header('X-Alternate-Token', 'synthetic')
    redirected = SafeRedirectHandler(policy).redirect_request(request, None, 302, 'Found', {}, target)
    assert {name.lower() for name, _value in redirected.header_items()} <= {'user-agent', 'range'}


@pytest.mark.parametrize(('source', 'target'), [
    ('https://PUBLIC.example/start', 'https://public.example:443/next'),
    ('http://public.example/start', 'http://public.example:80/next'),
    ('https://bücher.example/start', 'https://xn--bcher-kva.example/next'),
])
def test_same_effective_origin_preserves_explicit_credentials(source: str, target: str) -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({'public.example': ['93.184.216.34'], 'xn--bcher-kva.example': ['93.184.216.34']}))
    request = urllib.request.Request(source, headers={'Authorization': 'synthetic', 'Cookie': 'synthetic=1', 'X-Token': 'synthetic'})
    redirected = SafeRedirectHandler(policy).redirect_request(request, None, 302, 'Found', {}, target)
    assert {'authorization', 'cookie', 'x-token'} <= {name.lower() for name, _value in redirected.header_items()}


def test_redirect_chain_strips_alternate_header_spellings_and_never_reintroduces_credentials() -> None:
    policy = PublicSourcePolicy(resolver=resolver_for({'public.example': ['93.184.216.34'], 'other.example': ['93.184.216.34']}))
    handler = SafeRedirectHandler(policy)
    request = urllib.request.Request('https://public.example/start', headers={'Authorization': 'synthetic', 'Range': 'bytes=0-9'})
    request.headers['authorization'] = 'synthetic-alternate'
    request.add_unredirected_header('X-Unredirected-Token', 'synthetic')
    for target in ['https://public.example:443/one', 'https://other.example/two', 'https://other.example/three', 'https://public.example/four']:
        request = handler.redirect_request(request, None, 302, 'Found', {}, target)
        if '/one' not in target:
            assert {name.lower() for name, _value in request.header_items()} == {'range'}


@pytest.mark.parametrize('target', ['https://public.example:/path', 'https://public.example:0/path', 'https://user@public.example/path',
                                   'https://public.example\\other/path', 'https://public.example%2fother/path', 'https://public..example/path',
                                   'https://public.example/\npath', 'https://[v1.public.example]/path'])
def test_ambiguous_header_origins_are_rejected(target: str) -> None:
    from app.services.network_policy import headers_for_origin

    with pytest.raises(PublicSourcePolicyError):
        headers_for_origin({'Authorization': 'synthetic'}, 'https://public.example/', target)


def test_streaming_positive_read_and_readinto_clamp_each_allocation_without_a_total_cap() -> None:
    limit = 32
    body = _TrackingBody(b'm' * (limit * 3))
    policy = PublicSourcePolicy(resolver=resolver_for({'public.example': ['93.184.216.34']}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: None, error_response_limit=limit)
    response = handler.http_response(urllib.request.Request('https://public.example/media'), _urllib_response(body))
    assert response.read(limit * 4) == b'm' * limit
    buffer = bytearray(limit * 4)
    assert response.readinto(buffer) == limit
    assert buffer[:limit] == b'm' * limit
    assert response.read(limit * 4) == b'm' * limit
    assert response.read(limit * 4) == b''
    assert body.read_sizes == [limit, limit, limit, limit]
    response.close()
    assert body.closed


@pytest.mark.parametrize('method', ['read', 'read-negative', 'read-none'])
def test_streaming_unsized_reads_fail_at_cap_plus_one_and_close(method: str) -> None:
    limit = 32
    body = _TrackingBody(b'x' * (limit + 1))
    policy = PublicSourcePolicy(resolver=resolver_for({'public.example': ['93.184.216.34']}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: None, error_response_limit=limit)
    response = handler.http_response(urllib.request.Request('https://public.example/media'), _urllib_response(body))
    with pytest.raises(OSError, match='safe byte limit'):
        response.read() if method == 'read' else response.read(-1 if method == 'read-negative' else None)
    assert body.read_sizes == [limit + 1]
    assert body.closed


def test_metadata_readinto_and_iteration_keep_the_cumulative_limit() -> None:
    limit = 32
    body = _TrackingBody(b'x\n' * limit)
    response = _metadata_handler(limit).http_response(urllib.request.Request('https://public.example/metadata'), _urllib_response(body))
    buffer = bytearray(16)
    assert response.readinto(buffer) == 16
    with pytest.raises(OSError, match='safe byte limit'):
        list(response)
    assert body.closed


def test_streaming_iteration_bounds_each_line_and_keeps_small_lines_incremental() -> None:
    limit = 32
    policy = PublicSourcePolicy(resolver=resolver_for({'public.example': ['93.184.216.34']}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: None, error_response_limit=limit)
    body = _TrackingBody(b'x' * (limit + 1))
    response = handler.http_response(urllib.request.Request('https://public.example/media'), _urllib_response(body))
    with pytest.raises(OSError, match='safe byte limit'):
        next(iter(response))
    assert body.closed
    incremental = handler.http_response(urllib.request.Request('https://public.example/media'), _urllib_response(_TrackingBody(b'x\n' * limit)))
    assert sum(len(line) for line in incremental) == 2 * limit
    incremental.close()


def test_streaming_readlines_cannot_bulk_accumulate_many_short_lines_past_the_cap() -> None:
    limit = 32
    body = _TrackingBody(b'x\n' * limit)
    policy = PublicSourcePolicy(resolver=resolver_for({'public.example': ['93.184.216.34']}))
    handler = _DecoderProbeHTTPHandler(policy, response_limit=lambda: None, error_response_limit=limit)
    response = handler.http_response(urllib.request.Request('https://public.example/media'), _urllib_response(body))
    with pytest.raises(OSError, match='safe byte limit'):
        response.readlines()
    assert body.closed
