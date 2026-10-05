from __future__ import annotations

import functools
import http.client
import ipaddress
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable
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
    def __init__(self, policy: PublicSourcePolicy, **kwargs):
        super().__init__(**kwargs)
        self.policy = policy

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


class SafeRedirectHandler(ytdlp_urllib.RedirectHandler):
    def __init__(self, policy: PublicSourcePolicy):
        self.policy = policy
        super().__init__()

    def redirect_request(self, request, fp, code, message, headers, new_url):
        absolute_url = urllib.parse.urljoin(request.full_url, new_url)
        self.policy.validate_url(absolute_url)
        return super().redirect_request(request, fp, code, message, headers, absolute_url)


class SafeUrllibRH(ytdlp_urllib.UrllibRH):
    _SUPPORTED_URL_SCHEMES = ("http", "https", "data")

    def __init__(self, *, policy: PublicSourcePolicy, **kwargs):
        self.policy = policy
        super().__init__(**kwargs)

    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        if any(value for key, value in (proxies or {}).items() if key != "no"):
            raise PublicSourcePolicyError()
        opener = urllib.request.OpenerDirector()
        handlers = [
            ytdlp_urllib.ProxyHandler({}),
            SafeHTTPHandler(
                self.policy,
                debuglevel=int(bool(self.verbose)),
                context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support),
                source_address=self.source_address,
            ),
            ytdlp_urllib.HTTPCookieProcessor(cookiejar),
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
        super().__init__(params, **kwargs)

    def build_request_director(self, handlers, preferences=None):
        del handlers
        policy = self.public_source_policy

        class BoundSafeUrllibRH(SafeUrllibRH):
            def __init__(self, **kwargs):
                super().__init__(policy=policy, **kwargs)

        return super().build_request_director([BoundSafeUrllibRH], preferences)

    def process_info(self, info_dict):
        if not self.params.get("skip_download"):
            self.validate_download_transport(info_dict)
        return super().process_info(info_dict)

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
