"""Source-file audio facts shared by album tracks and batched album summaries."""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from app.media_schemas import AlbumAudioQuality, FileAudioQuality

LOSSY_CODECS = {"aac", "mp3", "mp2", "opus", "vorbis", "ac3", "eac3", "wmav1", "wmav2"}


def positive(value: Any, ceiling: float = 1_000_000_000) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 < number <= ceiling else None
    except (TypeError, ValueError, OverflowError):
        return None


def file_audio_quality(probe: Any, size: int | None = None, duration: float | None = None) -> FileAudioQuality:
    probe = probe if isinstance(probe, dict) else {}
    streams = probe.get("streams")
    stream = next((s for s in streams if isinstance(s, dict) and s.get("type") == "audio"), {}) if isinstance(streams, list) else {}
    codec = stream.get("codec") or probe.get("audio_codec")
    codec = codec.strip().lower() if isinstance(codec, str) and 0 < len(codec.strip()) <= 40 else None
    rate = positive(stream.get("bit_rate")) or positive(probe.get("bit_rate"))
    seconds = positive(duration) or positive(probe.get("duration"))
    if rate is None and codec and seconds and (length := positive(size, 1_000_000_000_000)):
        rate = positive(length * 8 / seconds)
    depth = None if codec in LOSSY_CODECS else positive(stream.get("bit_depth") or stream.get("bits_per_raw_sample") or stream.get("bits_per_sample"), 64)
    sample_rate = positive(stream.get("sample_rate"), 2_000_000)
    return FileAudioQuality(codec=codec, bitrate_kbps=rate / 1_000 if rate else None,
        bit_depth=int(depth) if depth else None, sample_rate=int(sample_rate) if sample_rate else None)


def album_audio_quality(tracks: Iterable[tuple[FileAudioQuality, float | None]]) -> AlbumAudioQuality:
    tracks = list(tracks)
    complete_rates = bool(tracks) and all(q.bitrate_kbps is not None and positive(seconds) for q, seconds in tracks)
    average = sum(q.bitrate_kbps * seconds for q, seconds in tracks) / sum(seconds for _, seconds in tracks) if complete_rates else None
    return AlbumAudioQuality(
        track_count=len(tracks), codecs=sorted({q.codec for q, _ in tracks if q.codec}),
        bit_depths=sorted({q.bit_depth for q, _ in tracks if q.bit_depth}),
        sample_rates=sorted({q.sample_rate for q, _ in tracks if q.sample_rate}), bitrate_kbps=average,
        formats_complete=bool(tracks) and all(q.codec and q.sample_rate and (q.bit_depth or q.codec in LOSSY_CODECS) for q, _ in tracks),
    )
