"""Remote playback format planning, rendition selection, and request parsing."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import quoteattr

from app.services.remote_streaming_types import (
    BrowserCapabilities,
    ByteRange,
    DashIndex,
    MediaTrack,
    PlaybackTransport,
    RangeNotSatisfiableError,
    UnsupportedPlaybackError,
    _RenditionKey,
    _StreamRecord,
)


def _auto_ladder(videos: Iterable[MediaTrack], top: MediaTrack) -> tuple[MediaTrack, ...]:
    """The DASH rungs Auto may move among: ``top``'s container and codec, one per quality, up to ``top``.

    One codec keeps every switch inside one SourceBuffer without a codec change.
    """
    # HDR and SDR tracks of one codec are not told apart; YouTube does not mix them within one codec.
    family = (top.ext, _video_codec_family(top.vcodec))
    best: dict[str, MediaTrack] = {}
    for track in videos:
        if (track.ext, _video_codec_family(track.vcodec)) != family or (track.height or 0) > (top.height or 0):
            continue
        label = _rendition_label(track)
        if label not in best or (track.bitrate_kbps or 0) > (best[label].bitrate_kbps or 0):
            best[label] = track
    return tuple(sorted(best.values(), key=lambda track: (_video_score(track), track.bitrate_kbps or 0)))


# (level, max picture size, max luma sample rate) from the VP9 bitstream spec, annex A.
_VP9_LEVELS = (
    (10, 36_864, 829_440), (11, 73_728, 2_764_800), (20, 122_880, 4_608_000), (21, 245_760, 9_216_000),
    (30, 552_960, 20_736_000), (31, 983_040, 36_864_000), (40, 2_228_224, 83_558_400),
    (41, 2_228_224, 160_432_128), (50, 8_912_896, 311_951_360), (51, 8_912_896, 588_251_136),
    (52, 8_912_896, 1_176_502_272), (60, 35_651_584, 1_176_502_272), (61, 35_651_584, 2_353_004_544),
)


def _manifest_video_codec(track: MediaTrack) -> str:
    """Providers label WebM VP9 plainly "vp9"; MediaCapabilities (and so dash.js) needs the full RFC 6381 form."""
    if track.vcodec.lower() != "vp9":
        return track.vcodec
    picture = (track.width or 0) * (track.height or 0)
    rate = picture * (track.frame_rate or 30)
    level = next((level for level, max_picture, max_rate in _VP9_LEVELS if picture <= max_picture and rate <= max_rate), 62)
    return f"vp09.00.{level}.08"  # profile 0, 8-bit: the only VP9 the DASH ladder accepts


def _dash_manifest(
    record: _StreamRecord,
    generation: int,
    videos: list[tuple[MediaTrack, DashIndex]],
    audio_index: DashIndex,
) -> bytes:
    audio = record.plan.audio
    if not videos or audio is None or record.plan.selected_rendition_id is None:
        raise UnsupportedPlaybackError("This playback session does not expose DASH content.")
    duration = max(audio_index.duration_seconds, *(index.duration_seconds for _video, index in videos))
    duration_text = f"PT{duration:.3f}S"
    audio_bandwidth = max(1, round((audio.bitrate_kbps or 128) * 1_000))
    base = f"/api/remote-streams/{record.stream_id}/dash/{generation}"

    def representation(position: int, video: MediaTrack, index: DashIndex) -> str:
        frame_rate = f' frameRate={quoteattr(_rendition_frame_rate(video))}' if video.frame_rate else ""
        return (
            f'      <Representation id="video-{position}" bandwidth="{max(1, round((video.bitrate_kbps or 1) * 1_000))}" '
            f'codecs={quoteattr(_manifest_video_codec(video))} width="{video.width or 1}" height="{video.height or 1}"{frame_rate}>\n'
            f'        <BaseURL>{base}/video-{position}</BaseURL>\n'
            f'        <SegmentBase indexRange="{index.index_start}-{index.index_end}" indexRangeExact="true">\n'
            f'          <Initialization range="0-{index.initialization_end}"/>\n'
            '        </SegmentBase>\n'
            '      </Representation>\n'
        )

    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
        f'type="static" mediaPresentationDuration={quoteattr(duration_text)} minBufferTime="PT1.5S">\n'
        f'  <Period id="period" duration={quoteattr(duration_text)}>\n'
        f'    <AdaptationSet id="video" contentType="video" mimeType={quoteattr(videos[0][0].content_type)} segmentAlignment="true" startWithSAP="1">\n'
        + "".join(representation(position, video, index) for position, (video, index) in enumerate(videos))
        + '    </AdaptationSet>\n'
        '    <AdaptationSet id="audio" contentType="audio" mimeType="audio/mp4" segmentAlignment="true" startWithSAP="1">\n'
        f'      <Representation id="audio" bandwidth="{audio_bandwidth}" codecs={quoteattr(audio.acodec)}>\n'
        f'        <BaseURL>{base}/audio</BaseURL>\n'
        f'        <SegmentBase indexRange="{audio_index.index_start}-{audio_index.index_end}" indexRangeExact="true">\n'
        f'          <Initialization range="0-{audio_index.initialization_end}"/>\n'
        '        </SegmentBase>\n'
        '      </Representation>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    return manifest.encode()


def parse_range_header(value: str | None, content_length: int | None) -> ByteRange | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized.startswith("bytes=") or "," in normalized:
        raise RangeNotSatisfiableError(content_length)
    start_text, separator, end_text = normalized[6:].partition("-")
    if not separator:
        raise RangeNotSatisfiableError(content_length)
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else None
            if start < 0 or (end is not None and end < start) or (content_length is not None and start >= content_length):
                raise RangeNotSatisfiableError(content_length)
            if content_length is not None and end is not None:
                end = min(end, content_length - 1)
            return ByteRange(start, end)
        suffix = int(end_text)
        if suffix <= 0:
            raise RangeNotSatisfiableError(content_length)
        if content_length is None:
            return ByteRange(None, suffix)
        return ByteRange(max(0, content_length - suffix), content_length - 1)
    except ValueError as exc:
        raise RangeNotSatisfiableError(content_length) from exc


def _bounded_byte_range(
    byte_range: ByteRange | None,
    content_length: int | None,
    byte_limit: int,
) -> ByteRange | None:
    if byte_range is None:
        if content_length is not None and content_length <= byte_limit:
            return None
        end = byte_limit - 1
        if content_length is not None:
            end = min(end, content_length - 1)
        return ByteRange(0, end)
    if byte_range.start is None:
        return ByteRange(None, min(byte_range.end or byte_limit, byte_limit))
    budget_end = byte_range.start + byte_limit - 1
    end = budget_end if byte_range.end is None else min(byte_range.end, budget_end)
    if content_length is not None:
        end = min(end, content_length - 1)
    return ByteRange(byte_range.start, end)


def redact_preview_info(info: Mapping[str, Any]) -> dict[str, Any]:
    """Return public metadata without upstream addresses, headers, or fragments."""

    public_keys = {
        "_type", "id", "title", "description", "uploader", "channel", "duration",
        "webpage_url", "channel_url", "uploader_url", "availability", "extractor", "extractor_key", "upload_date", "view_count",
        "channel_follower_count", "channel_id", "uploader_id", "format_resolution",
    }
    format_keys = {
        "format_id", "format_note", "ext", "vcodec", "acodec", "width", "height", "fps", "tbr", "abr",
        "filesize", "filesize_approx", "protocol",
    }
    result = {key: info[key] for key in public_keys if key in info}
    categories = info.get("categories")
    if isinstance(categories, list):
        result["categories"] = [category.strip()[:100] for category in categories[:25] if isinstance(category, str) and category.strip()]
    formats = info.get("formats")
    if isinstance(formats, list):
        result["formats"] = [
            {key: candidate[key] for key in format_keys if key in candidate}
            for candidate in formats
            if isinstance(candidate, Mapping)
        ]
    entries = info.get("entries")
    if isinstance(entries, list):
        result["entries"] = [redact_preview_info(entry) for entry in entries[:100] if isinstance(entry, Mapping)]
    return result


def _track(candidate: Mapping[str, Any]) -> MediaTrack | None:
    url = candidate.get("url")
    format_id = candidate.get("format_id")
    protocol = str(candidate.get("protocol") or "").lower()
    if not isinstance(url, str) or not url or not format_id or protocol not in {"http", "https"}:
        return None
    ext = str(candidate.get("ext") or "").lower()
    raw_vcodec = str(candidate.get("vcodec") or "none").strip()
    raw_acodec = str(candidate.get("acodec") or "none").strip()
    # ISO BMFF codec strings are not uniformly case-insensitive. In particular,
    # Chromium accepts the AV1 tier marker in ``av01.0.12M.08`` but rejects the
    # lowercased ``m`` form. Preserve provider spelling for manifests while the
    # codec-family helpers normalize only for comparisons.
    vcodec = "none" if raw_vcodec.lower() == "none" else raw_vcodec
    acodec = "none" if raw_acodec.lower() == "none" else raw_acodec
    content_type = _content_type(ext, vcodec)
    expires_at = candidate.get("url_expiry")
    if not isinstance(expires_at, (int, float)):
        try:
            query_expiry = parse_qs(urlsplit(url).query).get("expire", [None])[0]
            expires_at = float(query_expiry) if query_expiry else None
        except (TypeError, ValueError):
            expires_at = None
    headers = candidate.get("http_headers") if isinstance(candidate.get("http_headers"), Mapping) else {}
    content_length = candidate.get("filesize")
    estimated_content_length = candidate.get("filesize_approx")
    downloader_options = candidate.get("downloader_options")
    chunk_size = downloader_options.get("http_chunk_size") if isinstance(downloader_options, Mapping) else None
    return MediaTrack(
        format_id=str(format_id),
        url=url,
        content_type=content_type,
        protocol=protocol,
        ext=ext,
        vcodec=vcodec,
        acodec=acodec,
        height=candidate.get("height") if isinstance(candidate.get("height"), int) else None,
        content_length=content_length if isinstance(content_length, int) and content_length >= 0 else None,
        expires_at=float(expires_at) if isinstance(expires_at, (int, float)) else None,
        headers={str(key): str(value) for key, value in headers.items()},
        width=candidate.get("width") if isinstance(candidate.get("width"), int) else None,
        frame_rate=float(candidate["fps"]) if isinstance(candidate.get("fps"), (int, float)) else None,
        bitrate_kbps=float(candidate["tbr"]) if isinstance(candidate.get("tbr"), (int, float)) else None,
        estimated_content_length=(
            estimated_content_length
            if isinstance(estimated_content_length, int) and estimated_content_length >= 0
            else None
        ),
        source_container=str(candidate.get("container") or "").lower() or None,
        chunk_size=chunk_size if isinstance(chunk_size, int) and not isinstance(chunk_size, bool) and chunk_size > 0 else None,
        language_preference=preference if isinstance((preference := candidate.get("language_preference")), int) and not isinstance(preference, bool) else 0,
    )


def _progressive(track: MediaTrack) -> bool:
    return track.protocol in {"http", "https"}


def _track_cache_fingerprint(track: MediaTrack) -> str:
    """Identify media bytes without retaining an expiring URL or private headers."""

    stable_fields = (
        "track-v1",
        track.format_id,
        track.content_type,
        track.protocol,
        track.ext,
        track.vcodec,
        track.acodec,
        track.width,
        track.height,
        track.frame_rate,
        track.bitrate_kbps,
        track.content_length,
        track.estimated_content_length,
        track.source_container,
    )
    payload = json.dumps(stable_fields, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _stable_source_identity(info: Mapping[str, Any], source_url: str) -> str:
    """Use provider media identity for cache reuse without retaining signed URLs."""

    remote_id = str(info.get("id") or "").strip()
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").strip().lower()
    if remote_id and extractor == "youtube":
        return f"youtube:{remote_id}"
    return f"url:{_canonical_remote_source_url(source_url)}"


def _canonical_remote_source_url(value: str) -> str:
    """Mirror the frontend's generic source identity normalization."""

    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
        if not parsed.scheme or not parsed.hostname:
            return normalized
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        try:
            port = parsed.port
        except ValueError:
            return normalized
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (
            parsed.scheme.lower() == "https" and port == 443
        )
        port_suffix = f":{port}" if port is not None and not default_port else ""
        userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
        query = urlencode(
            sorted(
                (
                    (name, item)
                    for name, item in parse_qsl(parsed.query, keep_blank_values=True)
                    if not name.casefold().startswith("utm_")
                ),
                key=lambda pair: pair[0],
            ),
            doseq=True,
        )
        return urlunsplit((
            parsed.scheme.lower(),
            f"{userinfo}{hostname}{port_suffix}",
            parsed.path or "/",
            query,
            "",
        ))
    except (UnicodeError, ValueError):
        return normalized


def _video_score(track: MediaTrack) -> tuple[int, int, int]:
    compatible = int(_video_codec_family(track.vcodec) != "unknown")
    container = 2 if track.ext == "mp4" else 1 if track.ext == "webm" else 0
    height = track.height or 0
    return height, compatible, container


def _audio_score(track: MediaTrack) -> tuple[int, int, int, float]:
    """The original language first (never an auto-dub), then codec, container and bitrate."""
    compatible = int(_audio_codec_family(track.acodec) != "unknown")
    container = 2 if track.ext in {"m4a", "mp4"} else 1 if track.ext in {"webm", "mp3"} else 0
    return track.language_preference, compatible, container, track.bitrate_kbps or 0


def _content_type(ext: str, vcodec: str) -> str:
    if vcodec == "none":
        return {"m4a": "audio/mp4", "mp3": "audio/mpeg", "webm": "audio/webm"}.get(ext, "application/octet-stream")
    return {"mp4": "video/mp4", "webm": "video/webm"}.get(ext, "application/octet-stream")


def _video_codec_family(codec: str) -> str:
    normalized = codec.lower()
    if normalized.startswith(("avc", "h264")):
        return "avc"
    if normalized.startswith(("hev", "hvc", "h265")):
        return "hevc"
    if normalized.startswith(("vp09", "vp9")):
        return "vp9"
    if normalized.startswith(("vp08", "vp8")):
        return "vp8"
    if normalized.startswith(("av01", "av1")):
        return "av1"
    return "unknown"


def _audio_codec_family(codec: str) -> str:
    normalized = codec.lower()
    if normalized.startswith(("mp4a", "aac")):
        return "aac"
    if normalized.startswith("opus"):
        return "opus"
    if normalized.startswith("vorbis"):
        return "vorbis"
    if normalized.startswith("mp3"):
        return "mp3"
    return "unknown"


def _profile(track: MediaTrack) -> str | None:
    video = _video_codec_family(track.vcodec)
    audio = _audio_codec_family(track.acodec)
    if track.ext in {"mp4", "webm"} and video != "unknown" and audio != "unknown":
        return f"{track.ext}-{video}-{audio}"
    return None


def _audio_profile(track: MediaTrack) -> str | None:
    audio = _audio_codec_family(track.acodec)
    if audio == "unknown":
        return None
    if track.ext in {"m4a", "mp4"} and audio == "aac":
        return "m4a-aac"
    if track.ext == "webm" and audio in {"opus", "vorbis"}:
        return f"webm-{audio}"
    if track.ext == "mp3" and audio == "mp3":
        return "mp3-mp3"
    return None


def _known_video_container(track: MediaTrack) -> bool:
    return track.ext in {"mp4", "webm"} and _video_codec_family(track.vcodec) != "unknown"


def _legacy_hls_video(track: MediaTrack, capabilities: BrowserCapabilities) -> bool:
    return (
        track.ext == "mp4"
        and _video_codec_family(track.vcodec) == "avc"
        and track.height is not None
        and 0 < track.height <= 1080
        and "mp4-avc-aac" in capabilities.supported_profiles
    )


def _split_video_supported(track: MediaTrack, capabilities: BrowserCapabilities) -> bool:
    prefix = f"{track.ext}-{_video_codec_family(track.vcodec)}-"
    return _known_video_container(track) and any(
        profile.startswith(prefix) for profile in capabilities.supported_profiles
    )


def _dash_video_supported(track: MediaTrack, capabilities: BrowserCapabilities) -> bool:
    if track.ext == "webm":
        # Exactly "vp9": YouTube's 10-bit "vp9.2" is not an MSE codec string, and
        # every such source also carries the 8-bit track.
        return (
            track.source_container == "webm_dash"
            and track.vcodec.lower() == "vp9"
            and "dash-webm-vp9" in capabilities.supported_profiles
        )
    return (
        track.ext == "mp4"
        and track.source_container == "mp4_dash"
        and _video_codec_family(track.vcodec) in {"avc", "av1"}
        and _split_video_supported(track, capabilities)
    )


def _dash_audio_supported(track: MediaTrack) -> bool:
    return (
        track.ext in {"m4a", "mp4"}
        and track.source_container in {"m4a_dash", "mp4_dash"}
        and _audio_codec_family(track.acodec) == "aac"
    )


def _rendition_key(track: MediaTrack, transport: PlaybackTransport) -> _RenditionKey:
    frame_rate = _rendition_frame_rate(track)
    return (
        transport,
        track.ext,
        _video_codec_family(track.vcodec),
        _audio_codec_family(track.acodec),
        frame_rate,
        track.width,
        track.height,
    )


def _rendition_quality_key(track: MediaTrack) -> str:
    # The selector must not expose two visually identical choices just because
    # providers report a cropped raster (338px) beside a nominal one (360px).
    return _rendition_label(track)


def _rendition_frame_rate(track: MediaTrack) -> str:
    return str(int(round(track.frame_rate or 0)))


def _rendition_label(track: MediaTrack) -> str:
    height = track.height
    if height is None:
        label = "Auto"
    else:
        standard_heights = (144, 240, 360, 480, 720, 1080, 1440, 2160)
        nominal_height = min(standard_heights, key=lambda candidate: abs(candidate - height))
        # Providers often report the encoded 16:9 raster (for example 1012) rather
        # than the conventional quality name (1080p). Keep unfamiliar formats exact.
        display_height = nominal_height if abs(nominal_height - height) / nominal_height <= 0.08 else height
        label = "4K" if display_height >= 2160 else f"{display_height}p"
    if track.frame_rate is not None and track.frame_rate >= 50:
        label += f" · {round(track.frame_rate):d} fps"
    return label
