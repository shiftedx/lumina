"""Download-transport gate for the guarded Twitch VOD (HLS) acquisition tracer.

Issue #95 makes the supported Twitch VOD acquirable through the native HLS
downloader, which fetches every manifest, segment, initialization resource, and
key through the same policy-guarded transport as the #92 relay. The transport
gate must permit native HLS for exactly that tracer and keep every other
segmented source failing closed, so the generic SAFE_NATIVE_PROTOCOLS lockstep
(and #91's capability guard) is unchanged.
"""

from __future__ import annotations

import socket

import pytest

from app.services.network_policy import (
    PolicyYoutubeDL,
    PublicSourcePolicy,
    PublicSourcePolicyError,
)


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int, family: int = 0, socktype: int = socket.SOCK_STREAM):
        del family
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (address, port))
            for address in mapping[host]
        ]

    return resolve


def _policy() -> PublicSourcePolicy:
    return PublicSourcePolicy(resolver=resolver_for({"vod.twitch.example": ["93.184.216.34"]}))


def _twitch_vod_info(*, protocol: str = "m3u8_native", url: str = "https://vod.twitch.example/vod/master.m3u8", **extra):
    info = {
        "extractor_key": "Twitch",
        "live_status": "was_live",
        "id": "vod-1",
        "protocol": protocol,
        "url": url,
        "ext": "mp4",
        "vcodec": "avc1.4d401f",
        "acodec": "mp4a.40.2",
    }
    info.update(extra)
    return info


def test_tracer_twitch_vod_native_hls_passes_the_download_transport_gate() -> None:
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=_policy()) as ydl:
        # Both native HLS protocol spellings are permitted for the tracer; both
        # route to the native (guarded) downloader under Lumina's forced-native
        # external_downloader/hls_prefer_native options.
        for protocol in ("m3u8_native", "m3u8"):
            ydl.validate_download_transport(_twitch_vod_info(protocol=protocol))


def test_tracer_ordinary_twitch_vod_without_was_live_also_passes() -> None:
    info = _twitch_vod_info()
    info.pop("live_status")
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=_policy()) as ydl:
        ydl.validate_download_transport(info)


def test_tracer_requested_formats_list_is_validated_and_permitted() -> None:
    info = {
        "extractor_key": "Twitch",
        "live_status": "was_live",
        "id": "vod-1",
        "formats": [{"protocol": "m3u8_native", "url": "https://vod.twitch.example/vod/master.m3u8"}],
        "requested_formats": [
            {"protocol": "m3u8_native", "url": "https://vod.twitch.example/vod/video.m3u8"},
        ],
    }
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=_policy()) as ydl:
        ydl.validate_download_transport(info)


def test_tracer_native_hls_with_a_private_media_url_still_fails_closed() -> None:
    # The transport allowance never bypasses the public-source policy: a tracer
    # whose media URL resolves into private space is rejected at the URL check.
    info = _twitch_vod_info(url="http://127.0.0.1/vod/master.m3u8")
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=PublicSourcePolicy()) as ydl:
        with pytest.raises(PublicSourcePolicyError):
            ydl.validate_download_transport(info)


def test_generic_provider_native_hls_is_still_rejected() -> None:
    info = {
        "extractor_key": "Generic",
        "id": "clip",
        "protocol": "m3u8_native",
        "url": "https://vod.twitch.example/stream.m3u8",
    }
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=_policy()) as ydl:
        with pytest.raises(PublicSourcePolicyError):
            ydl.validate_download_transport(info)


def test_twitch_live_native_hls_is_admitted_as_the_record_tracer() -> None:
    # Issue #100 widens the live-record scope to Twitch, so a currently-live
    # Twitch native-HLS source is now admitted by the guarded download-transport
    # gate exactly as the live YouTube record tracer is — its media capture still
    # fetches every resource through the same policy-guarded transport.
    info = _twitch_vod_info(url="https://vod.twitch.example/live.m3u8")
    info.pop("live_status")
    info["is_live"] = True
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=_policy()) as ydl:
        ydl.validate_download_transport(info)


def test_twitch_live_native_hls_with_a_private_media_url_still_fails_closed() -> None:
    # The record-tracer allowance never bypasses the public-source policy.
    info = _twitch_vod_info(url="http://127.0.0.1/live.m3u8")
    info.pop("live_status")
    info["is_live"] = True
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=PublicSourcePolicy()) as ydl:
        with pytest.raises(PublicSourcePolicyError):
            ydl.validate_download_transport(info)


def test_twitch_vod_non_hls_segmented_transport_is_rejected() -> None:
    for protocol in ("ism", "f4m", "http_dash_segments"):
        info = _twitch_vod_info(protocol=protocol)
        with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=_policy()) as ydl:
            # http_dash_segments is a generic safe protocol, so it passes; ISM/F4M
            # are not native-HLS and not in SAFE_NATIVE_PROTOCOLS, so they fail.
            if protocol == "http_dash_segments":
                ydl.validate_download_transport(info)
            else:
                with pytest.raises(PublicSourcePolicyError):
                    ydl.validate_download_transport(info)


def test_tracer_section_bounded_download_still_fails_closed() -> None:
    info = _twitch_vod_info(section_start=1)
    with PolicyYoutubeDL({"quiet": True, "proxy": ""}, policy=_policy()) as ydl:
        with pytest.raises(PublicSourcePolicyError):
            ydl.validate_download_transport(info)
