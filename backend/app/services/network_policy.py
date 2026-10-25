from __future__ import annotations

import contextvars
import functools
import http.client
import io
import ipaddress
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

import yt_dlp
from yt_dlp.dependencies import Cryptodome
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.dash import DashSegmentsFD
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.downloader.hls import HlsFD
from yt_dlp.downloader.http import HttpFD
from yt_dlp.downloader.youtube_live_chat import YoutubeLiveChatFD
from yt_dlp.networking import _urllib as ytdlp_urllib
from yt_dlp.utils import determine_protocol

from app.services.hls_relay_support import (
    supports_hls_acquisition_source,
    supports_live_hls_acquisition_source,
)

_CRYPTODOME_AES_AVAILABLE = bool(getattr(Cryptodome, "AES", None))
MAX_YTDLP_METADATA_RESPONSE_BYTES = 8 * 1024 * 1024
YTDLP_METADATA_RESPONSE_LIMIT_MESSAGE = "The source metadata response exceeded the safe byte limit."
YTDLP_UNSAFE_ENCODING_MESSAGE = "The source sent a compressed response that cannot be safely decoded."

# Downloaders whose every fetch flows through ``YoutubeDL.urlopen`` (the guarded
# transport). HLS is handled separately by ``GuardedHlsFD``.
_GUARDED_DOWNLOADERS = (HttpFD, DashSegmentsFD, YoutubeLiveChatFD)


# Text playlists/session descriptions name other files or URLs. Downloaded under such an extension, yt-dlp's own
# ffmpeg/ffprobe postprocessors (which pick the hls demuxer by extension) would follow file:// or LAN entries
# before Lumina's publication gate ever sees the file.
PLAYLIST_EXTENSIONS = frozenset({"m3u", "m3u8", "hls", "pls", "ffconcat", "sdp", "txt"})
PUBLIC_SOURCE_POLICY_MESSAGE = (
    "Public source policy blocked this address. Sources must use public HTTP or HTTPS destinations."
)


class PublicSourcePolicyError(ValueError):
    """Stable, non-reflective error for an acquisition network-policy denial."""

    def __init__(self) -> None:
        super().__init__(PUBLIC_SOURCE_POLICY_MESSAGE)


Resolver = Callable[..., list[tuple[Any, ...]]]
ResponseLimit = Callable[[], int | None]
# A member-supplied source must not make Lumina a client for SSH, SMTP, Redis or a port scanner.
WEB_PORTS = frozenset({80, 443})
# Never admissible, even as an admin's extra source port: remote shells, mail, file sharing, databases, Docker, VNC.
DANGEROUS_PORTS = frozenset({
    22, 23, 25, 110, 143, 445, 465, 587, 993, 995, 1433, 1521, 2375, 2376, 3306, 3389, 5432, 5900, 6379, 9200, 11211, 27017,
})
_extra_ports: frozenset[int] = frozenset()  # the admin's "Extra allowed source ports" (AppSettings.ui_prefs)


def check_extra_ports(values: list[int]) -> list[int]:
    """The admin's extra source ports, sorted and deduplicated; ValueError names the first refused one."""
    for port in values:
        if not 1 <= port <= 65535:
            raise ValueError(f"Port {port} is not between 1 and 65535.")
        if port in DANGEROUS_PORTS:
            raise ValueError(f"Port {port} cannot be allowed: it belongs to a service such as SSH, mail or a database.")
    return sorted(set(values) - WEB_PORTS)


def set_extra_ports(values: object) -> None:
    """Apply the stored setting; anything malformed or refused (e.g. hand-edited JSON) is dropped, never admitted."""
    global _extra_ports
    ports = values if isinstance(values, list) else []
    _extra_ports = frozenset(p for p in ports if type(p) is int and 1 <= p <= 65535 and p not in DANGEROUS_PORTS)


class PublicSourcePolicy:
    """Resolve acquisition hosts and fail closed unless every answer is public."""

    def __init__(self, *, resolver: Resolver = socket.getaddrinfo):
        self._resolver = resolver

    def validate_url(self, value: str, resolved: set[tuple[str, int]] | None = None) -> str:
        """``resolved`` lets one caller pass (e.g. a playlist rewrite) resolve each host once; the fetch revalidates anyway."""
        normalized = value.strip() if isinstance(value, str) else ""
        try:
            parsed = urllib.parse.urlsplit(normalized)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise PublicSourcePolicyError() from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise PublicSourcePolicyError()
        if parsed.username is not None or parsed.password is not None or "%" in parsed.hostname:
            raise PublicSourcePolicyError()
        try:
            literal_address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal_address = None
        if literal_address is not None and not self._is_public_address(literal_address):
            raise PublicSourcePolicyError()
        key = (parsed.hostname.lower(), port or (443 if parsed.scheme.lower() == "https" else 80))
        if resolved is None or key not in resolved:
            self.resolve(*key)
            if resolved is not None:
                resolved.add(key)
        return normalized

    def resolve(self, host: str, port: int) -> list[tuple[Any, ...]]:
        # Every fetch (first request, redirect, manifest child, the connection itself) passes here.
        if port not in WEB_PORTS and port not in _extra_ports:
            raise PublicSourcePolicyError()
        try:
            answers = self._resolver(host, port, 0, socket.SOCK_STREAM)
        except (OSError, TypeError, ValueError) as exc:
            raise PublicSourcePolicyError() from exc
        if not answers:
            raise PublicSourcePolicyError()
        for answer in answers:
            try:
                address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
            except (IndexError, TypeError, ValueError) as exc:
                raise PublicSourcePolicyError() from exc
            if not self._is_public_address(address):
                raise PublicSourcePolicyError()
        return answers

    @staticmethod
    def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return bool(
            address.is_global
            and not address.is_link_local
            and not address.is_loopback
            and not address.is_multicast
            and not address.is_private
            and not address.is_reserved
            and not getattr(address, "is_site_local", False)
            and not address.is_unspecified
        )


def _socket_connect(ip_addr: tuple[Any, ...], timeout: object, source_address: tuple[str, int] | None):
    family, socktype, proto, _canonname, sockaddr = ip_addr
    sock = socket.socket(family, socktype, proto)
    try:
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)
        return sock
    except OSError:
        sock.close()
        raise


def create_public_connection(
    policy: PublicSourcePolicy,
    address: tuple[str, int],
    timeout: object = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
    *,
    _create_socket_func: Callable[..., Any] = _socket_connect,
):
    host, port = address
    answers = policy.resolve(host, port)
    error: OSError | None = None
    for answer in answers:
        try:
            return _create_socket_func(answer, timeout, source_address)
        except OSError as exc:
            error = exc
    if error is not None:
        raise error
    raise PublicSourcePolicyError()


def _create_http_connection(
    http_class,
    source_address: str | None,
    policy: PublicSourcePolicy,
    *args,
    **kwargs,
):
    connection = http_class(*args, **kwargs)
    connection._create_connection = functools.partial(create_public_connection, policy)
    if source_address is not None:
        connection.source_address = (source_address, 0)
    return connection


class SafeHTTPHandler(ytdlp_urllib.HTTPHandler):
    def __init__(
        self,
        policy: PublicSourcePolicy,
        *,
        response_limit: ResponseLimit,
        error_response_limit: int = MAX_YTDLP_METADATA_RESPONSE_BYTES,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.policy = policy
        self._response_limit = response_limit
        self._error_response_limit = error_response_limit

    def http_open(self, request):
        return self.do_open(
            functools.partial(_create_http_connection, http.client.HTTPConnection, self._source_address, self.policy),
            request,
        )

    def https_open(self, request):
        return self.do_open(
            functools.partial(_create_http_connection, http.client.HTTPSConnection, self._source_address, self.policy),
            request,
            context=self._context,
        )

    def http_response(self, request, response):  # noqa: ANN001
        # Every guarded request asks for identity below. If an upstream ignores
        # that request, reject its compressed body before yt-dlp's handler calls
        # ``read()`` and hands the complete payload to a bulk gzip/deflate/Brotli
        # decoder. ``get_all`` matters: duplicate Content-Encoding fields must
        # not let an unsafe alternate hide behind an innocuous first value.
        encoding_values = response.headers.get_all("Content-Encoding", [])
        encodings = [
            item.strip().lower()
            for value in encoding_values
            for item in value.split(",")
            if item.strip()
        ]
        if any(encoding != "identity" for encoding in encodings):
            response.close()
            raise OSError(YTDLP_UNSAFE_ENCODING_MESSAGE)

        # With compression excluded, the inherited handler only retains its URL
        # normalization behavior; it cannot enter any whole-body decoder.
        response = super().http_response(request, response)
        limit = self._response_limit()
        status = getattr(response, "status", None)
        if limit is None and (status is None or not 200 <= status < 300):
            # Redirect and error bodies are metadata-sized control responses,
            # including during a media download. Only successful identity media
            # is allowed to stream without this in-memory response ceiling.
            limit = self._error_response_limit
        cumulative = limit is not None
        if limit is None:
            # Native HLS manifests and AES keys use unsized read() even during
            # media download. Only positive incremental reads may stream past
            # the byte ceiling; every individual read still has a fixed bound.
            limit = self._error_response_limit

        content_length = response.headers.get("Content-Length")
        try:
            declared_length = int(content_length) if content_length is not None else None
        except (TypeError, ValueError):
            declared_length = None
        if cumulative and declared_length is not None and declared_length > limit:
            response.close()
            raise OSError(YTDLP_METADATA_RESPONSE_LIMIT_MESSAGE)

        bounded = urllib.request.addinfourl(
            _BoundedResponseReader(response, limit, cumulative=cumulative),
            response.headers,
            response.url,
            response.code,
        )
        bounded.msg = getattr(response, "msg", None)
        return bounded

    https_response = http_response


class _BoundedResponseReader(io.RawIOBase):
    """Cap metadata cumulatively and media bulk reads without limiting streaming."""

    def __init__(self, response, limit: int, *, cumulative: bool = True):  # noqa: ANN001
        super().__init__()
        self._response = response
        self._remaining = limit
        self._cumulative = cumulative

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        return self._read(self._response.read, size)

    def readline(self, size: int | None = -1) -> bytes:
        return self._read(self._response.readline, size)

    def _read(self, reader: Callable[[int], bytes], size: int | None) -> bytes:
        if self.closed:
            return b""
        bulk = size is None or size < 0
        if bulk:
            requested = self._remaining + 1
        else:
            # Raw reads may return short: clamp large positive requests so a
            # streaming caller can loop without allocating a bulk body here.
            requested = min(size, self._remaining + int(self._cumulative))
        data = reader(requested)
        if (self._cumulative or bulk) and len(data) > self._remaining:
            self.close()
            raise OSError(YTDLP_METADATA_RESPONSE_LIMIT_MESSAGE)
        if self._cumulative:
            self._remaining -= len(data)
        return data

    def readlines(self, hint: int = -1) -> list[bytes]:
        lines: list[bytes] = []
        limit = remaining = self._remaining
        while True:
            line = self.readline(remaining + 1)
            if len(line) > remaining:
                self.close()
                raise OSError(YTDLP_METADATA_RESPONSE_LIMIT_MESSAGE)
            if not line:
                return lines
            lines.append(line)
            remaining -= len(line)
            if hint > 0 and limit - remaining >= hint:
                return lines

    def readinto(self, buffer) -> int:  # noqa: ANN001
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        if not self.closed:
            self._response.close()
        super().close()


# Only public transport preferences may accompany a change of origin. A secret
# header denylist cannot cover arbitrary extractor/provider-specific names.
_CROSS_ORIGIN_HEADERS = frozenset({"accept", "accept-language", "accept-encoding", "user-agent", "range",
                                   "if-range", "if-match", "if-none-match", "if-modified-since", "if-unmodified-since"})
Origin = tuple[str, str, int]
_credential_origin: contextvars.ContextVar[Origin | None] = contextvars.ContextVar("credential_origin", default=None)


def effective_origin(url: str) -> Origin:
    try:
        if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
            raise ValueError("url")
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        if (scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or "%" in host or "\\" in parsed.netloc or parsed.netloc.endswith(":")):
            raise ValueError("origin")
        port = parsed.port if parsed.port is not None else 443 if scheme == "https" else 80
        if not 1 <= port <= 65535:
            raise ValueError("port")
        try:
            host = ipaddress.ip_address(host).compressed
        except ValueError:
            if "[" in parsed.netloc or "]" in parsed.netloc:
                raise ValueError("host")
            host = host.encode("idna").decode("ascii").lower()
            labels = host.removesuffix(".").split(".")
            if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                   or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in label) for label in labels):
                raise ValueError("host")
        return scheme, host, port
    except (UnicodeError, TypeError, ValueError) as exc:
        raise PublicSourcePolicyError() from exc


def _restrict_cross_origin_headers(headers) -> None:  # noqa: ANN001
    for name in list(headers):
        if name.lower() not in _CROSS_ORIGIN_HEADERS:
            del headers[name]


def headers_for_origin(headers: Mapping[str, str], credential_url: str, target_url: str) -> dict[str, str]:
    """Retain full headers only within the original credential origin."""
    scoped = dict(headers)
    if effective_origin(credential_url) != effective_origin(target_url):
        _restrict_cross_origin_headers(scoped)
    return scoped


class _ScopedCookieProcessor(ytdlp_urllib.HTTPCookieProcessor):
    def http_request(self, request):  # noqa: ANN001
        request = super().http_request(request)
        origin = _credential_origin.get()
        if origin is not None and origin != effective_origin(request.full_url):
            # Cookie jars are scoped by domain/path, not origin (notably ports).
            # Filter after the jar runs, including its alternate header store.
            _restrict_cross_origin_headers(request.headers)
            _restrict_cross_origin_headers(request.unredirected_hdrs)
        return request

    https_request = http_request


class _OriginBoundYoutubeDL:
    """Carry one HLS credential origin through fragment worker threads and keys."""

    def __init__(self, ydl, origin: Origin):  # noqa: ANN001
        self._ydl = ydl
        self._origin = origin

    def __getattr__(self, name):  # noqa: ANN001
        return getattr(self._ydl, name)

    def urlopen(self, request):  # noqa: ANN001
        token = _credential_origin.set(self._origin)
        try:
            return self._ydl.urlopen(request)
        finally:
            _credential_origin.reset(token)


class SafeRedirectHandler(ytdlp_urllib.RedirectHandler):
    def __init__(self, policy: PublicSourcePolicy):
        self.policy = policy
        super().__init__()

    def redirect_request(self, request, fp, code, message, headers, new_url):
        absolute_url = urllib.parse.urljoin(request.full_url, new_url)
        self.policy.validate_url(absolute_url)
        origin = getattr(request, "_lumina_credential_origin", None) or _credential_origin.get() or effective_origin(request.full_url)
        redirected = super().redirect_request(request, fp, code, message, headers, absolute_url)
        redirected._lumina_credential_origin = origin
        if origin != effective_origin(absolute_url):
            _restrict_cross_origin_headers(redirected.headers)
            _restrict_cross_origin_headers(redirected.unredirected_hdrs)
        else:
            # yt-dlp drops an explicit Cookie on every redirect; it remains
            # scoped correctly when the effective origin has not changed.
            for name, value in request.headers.items():
                if name.lower() == "cookie":
                    redirected.add_header(name, value)
        return redirected


class SafeUrllibRH(ytdlp_urllib.UrllibRH):
    _SUPPORTED_URL_SCHEMES = ("http", "https", "data")

    def __init__(self, *, policy: PublicSourcePolicy, response_limit: ResponseLimit, **kwargs):
        self.policy = policy
        self._response_limit = response_limit
        super().__init__(**kwargs)

    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        if any(value for key, value in (proxies or {}).items() if key != "no"):
            raise PublicSourcePolicyError()
        opener = urllib.request.OpenerDirector()
        handlers = [
            ytdlp_urllib.ProxyHandler({}),
            SafeHTTPHandler(
                self.policy,
                response_limit=self._response_limit,
                debuglevel=int(bool(self.verbose)),
                context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support),
                source_address=self.source_address,
            ),
            _ScopedCookieProcessor(cookiejar),
            ytdlp_urllib.DataHandler(),
            ytdlp_urllib.UnknownHandler(),
            ytdlp_urllib.HTTPDefaultErrorHandler(),
            ytdlp_urllib.HTTPErrorProcessor(),
            SafeRedirectHandler(self.policy),
        ]
        for handler in handlers:
            opener.add_handler(handler)
        opener.addheaders = []
        return opener

    def _send(self, request):  # noqa: ANN001
        if request.url.startswith("data:"):
            return super()._send(request)
        token = _credential_origin.set(_credential_origin.get() or effective_origin(request.url))
        try:
            return super()._send(request)
        finally:
            _credential_origin.reset(token)

    def _prepare_headers(self, request, headers):  # noqa: ANN001
        origin = _credential_origin.get()
        if origin is not None and origin != effective_origin(request.url):
            _restrict_cross_origin_headers(headers)
        headers["Accept-Encoding"] = "identity"


class GuardedHlsFD(HlsFD):
    """Native HLS downloader that never delegates an HLS fetch to ffmpeg.

    The pre-fetch transport gate (``validate_download_transport``) sees only the
    declared protocol and the manifest URL, never the manifest body. Upstream
    ``HlsFD`` fetches the media playlist through the guarded transport but then,
    for any content it cannot download natively (a ``METHOD`` other than
    ``NONE``/``AES-128``, e.g. ``SAMPLE-AES``, or an AES-128 stream it cannot
    decrypt natively), hands the whole download to ``FFmpegFD`` — which fetches
    the manifest and every segment, key, and map through a spawned ffmpeg process
    entirely outside this policy, forwarding member credentials to whatever child
    URLs the manifest names. A hostile or compromised upstream on an otherwise
    genuine (tracer) source could reach that path, so this subclass inspects the
    fetched manifest and fails closed rather than ever delegating: it decides
    whether the native downloader can fully handle the manifest, and if not it
    raises ``PublicSourcePolicyError`` before any ffmpeg process is started. When
    it can, it hands the already-fetched manifest to the native downloader so no
    resource is fetched twice.
    """

    def real_download(self, filename, info_dict):  # noqa: ANN001
        original_ydl = self.ydl
        self.ydl = _OriginBoundYoutubeDL(original_ydl, effective_origin(info_dict["url"]))
        try:
            return self._download_scoped(filename, info_dict)
        finally:
            self.ydl = original_ydl

    def _download_scoped(self, filename, info_dict):  # noqa: ANN001
        man_url = info_dict["url"]
        response = self.ydl.urlopen(self._prepare_url(info_dict, man_url))
        manifest = response.read().decode("utf-8", "ignore")
        if not self._native_can_fully_download(manifest, info_dict):
            raise PublicSourcePolicyError()
        guarded = dict(info_dict)
        # Use the post-redirect URL as the fragment-resolution base and reuse the
        # manifest we already fetched through the guard, so the native path never
        # re-fetches or resolves fragments against a stale base.
        guarded["url"] = response.url
        guarded["hls_media_playlist_data"] = manifest
        return super().real_download(filename, guarded)

    def _native_can_fully_download(self, manifest: str, info_dict: dict[str, Any]) -> bool:
        if not self.can_download(manifest, info_dict, self.params.get("allow_unplayable_formats")):
            # Encrypted-beyond-AES-128 or DRM: upstream HlsFD would delegate to
            # ffmpeg (or hard-fail on DRM). Refuse either way.
            return False
        # Mirror HlsFD's remaining ffmpeg hand-off: an AES-128 stream is delegated
        # to ffmpeg when pycryptodomex is unavailable and ffmpeg is present. That
        # delegation would also fetch outside the guard, so refuse it too.
        if "#EXT-X-KEY:METHOD=AES-128" in manifest and not _CRYPTODOME_AES_AVAILABLE and FFmpegFD.available():
            return False
        return True


class PolicyYoutubeDL(yt_dlp.YoutubeDL):
    SAFE_NATIVE_PROTOCOLS = {
        "http",
        "https",
        "http_dash_segments",
        "http_dash_segments_generator",
    }

    # Native HLS (issue #95) is permitted only for the supported Twitch VOD
    # tracer, never generically. Both spellings resolve to yt-dlp's native HLS
    # downloader (HlsFD) under Lumina's forced-native external_downloader and
    # hls_prefer_native options, so every manifest, segment, initialization
    # resource, key, and redirect it fetches flows through this same guarded
    # transport. This set intentionally excludes ffmpeg-delegating HLS variants.
    _TRACER_NATIVE_HLS_PROTOCOLS = {"m3u8", "m3u8_native"}

    def __init__(self, params=None, *, policy: PublicSourcePolicy | None = None, **kwargs):
        self.public_source_policy = policy or PublicSourcePolicy()
        self._metadata_extraction_depth = 0
        self._media_download_depth = 0
        super().__init__(params, **kwargs)

    def extract_info(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self._metadata_extraction_depth += 1
        try:
            return super().extract_info(*args, **kwargs)
        finally:
            self._metadata_extraction_depth -= 1

    def _metadata_response_limit(self) -> int | None:
        if self._metadata_extraction_depth and not self._media_download_depth:
            return MAX_YTDLP_METADATA_RESPONSE_BYTES
        return None

    def build_request_director(self, handlers, preferences=None):
        del handlers
        policy = self.public_source_policy
        self_outer = self

        class BoundSafeUrllibRH(SafeUrllibRH):
            def __init__(self, **kwargs):
                super().__init__(policy=policy, response_limit=self_outer._metadata_response_limit, **kwargs)

        return super().build_request_director([BoundSafeUrllibRH], preferences)

    def process_info(self, info_dict):
        if not self.params.get("skip_download"):
            self.validate_download_transport(info_dict)
        self._media_download_depth += 1
        try:
            return super().process_info(info_dict)
        finally:
            self._media_download_depth -= 1

    def dl(self, name, info, subtitle=False, test=False):
        """The single chokepoint for every yt-dlp network download.

        Media, merged formats, subtitles, replay chat and format tests all reach
        here. Only downloaders that fetch through this guarded transport may run:
        any HLS (including ``is_live`` HLS, which yt-dlp hands to ffmpeg, and
        subtitle tracks served as m3u8) goes through ``GuardedHlsFD``, which
        refuses anything it cannot fetch natively; every other downloader
        (ffmpeg, external programs, rtmp, ...) is refused outright.
        """
        if not info.get("url"):
            return super().dl(name, info, subtitle=subtitle, test=test)
        for candidate in info.get("requested_formats") or [info]:
            self.public_source_policy.validate_url(candidate.get("url"))
        downloader = get_suitable_downloader(info, self.params, to_stdout=(name == "-"))
        if downloader in _GUARDED_DOWNLOADERS:
            return super().dl(name, info, subtitle=subtitle, test=test)
        if not set(info["protocol"].split("+")) <= self._TRACER_NATIVE_HLS_PROTOCOLS or "\n" in info["url"]:
            raise PublicSourcePolicyError()
        fd = GuardedHlsFD(self, {**self.params, "test": True} if test else self.params)
        if not test:
            for progress_hook in self._progress_hooks:
                fd.add_progress_hook(progress_hook)
        new_info = self._copy_infodict(info)
        if new_info.get("http_headers") is None:
            new_info["http_headers"] = self._calc_headers(new_info)
        return fd.download(name, new_info, subtitle)

    def validate_download_transport(self, info_dict: dict[str, Any]) -> None:
        if info_dict.get("section_start") is not None or info_dict.get("section_end") is not None:
            raise PublicSourcePolicyError()
        allowed_protocols = self._allowed_download_protocols(info_dict)
        selected = info_dict.get("requested_formats")
        candidates = selected if isinstance(selected, list) and selected else [info_dict]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise PublicSourcePolicyError()
            if candidate.get("section_start") is not None or candidate.get("section_end") is not None:
                raise PublicSourcePolicyError()
            protocol = str(candidate.get("protocol") or determine_protocol(candidate)).lower()
            if protocol not in allowed_protocols:
                raise PublicSourcePolicyError()
            if str(candidate.get("ext") or info_dict.get("ext") or "").lower() in PLAYLIST_EXTENSIONS:
                raise PublicSourcePolicyError()
            media_url = candidate.get("url")
            if not isinstance(media_url, str):
                raise PublicSourcePolicyError()
            self.public_source_policy.validate_url(media_url)
        # Subtitle tracks are downloaded too (``dl(subtitle=True)``): same gate,
        # plus yt-dlp's chat protocols, whose downloader fetches through urlopen.
        # A track that fails the gate is dropped, never fetched; the media still downloads.
        subtitles = info_dict.get("requested_subtitles")
        if subtitles:
            subtitle_protocols = allowed_protocols | {"youtube_live_chat", "youtube_live_chat_replay"}
            info_dict["requested_subtitles"] = {
                lang: track for lang, track in subtitles.items() if self._subtitle_allowed(track, subtitle_protocols)
            }

    def _subtitle_allowed(self, track: Any, protocols: set[str]) -> bool:
        if not isinstance(track, dict):
            return False
        if track.get("data") is not None:
            return True
        if str(track.get("protocol") or determine_protocol(track)).lower() not in protocols:
            return False
        try:
            self.public_source_policy.validate_url(track.get("url"))
        except PublicSourcePolicyError:
            return False
        return True

    def _allowed_download_protocols(self, info_dict: dict[str, Any]) -> set[str]:
        """The protocols this item may download over.

        The generic gate is always ``SAFE_NATIVE_PROTOCOLS``. Two scoped tracers
        additionally admit native HLS, and only because both fetch every resource
        through this guarded transport (``GuardedHlsFD``): the completed Twitch
        VOD tracer (issue #95) and the currently-live recording tracer — YouTube
        (issue #97), Twitch (issue #100) and Kick (issue #142), via the shared
        ``supports_live_hls_acquisition_source`` predicate. Both decisions come
        from the same shared predicates the capability seam uses, so a source
        advertised as acquirable/recordable is never rejected here, and no other
        source ever gains the native-HLS allowance.
        """

        if supports_hls_acquisition_source(info_dict) or supports_live_hls_acquisition_source(info_dict):
            return self.SAFE_NATIVE_PROTOCOLS | self._TRACER_NATIVE_HLS_PROTOCOLS
        return self.SAFE_NATIVE_PROTOCOLS
