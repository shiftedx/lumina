"""Ffprobe facts carry a per-stream list and an explicit cache version."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.services.media_probe import PROBE_CACHE_VERSION, is_stale, normalize, run_ffprobe

RAW = {
    "format": {"format_name": "matroska,webm", "duration": "5400.5", "bit_rate": "24000000"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "profile": "Main 10", "level": 153, "width": 3840, "height": 2160,
         "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084", "avg_frame_rate": "24000/1001", "disposition": {"default": 1},
         "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": 8, "dv_bl_signal_compatibility_id": 1}]},
        {"index": 1, "codec_type": "audio", "codec_name": "truehd", "channels": 8, "channel_layout": "7.1", "sample_rate": "48000",
         "tags": {"language": "eng", "title": "Atmos"}, "disposition": {"default": 1}},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2, "bit_rate": "192000", "tags": {"language": "und"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}, "disposition": {"forced": 1}},
        {"index": 4, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "spa"}, "disposition": {"hearing_impaired": 1}},
        {"index": 5, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        {"index": 6, "codec_type": "attachment", "codec_name": "ttf"},
        {"index": "x", "codec_type": "audio", "codec_name": "ac3"},
        "not-a-dict",
    ],
}


def test_every_real_stream_is_listed_with_normalized_facts() -> None:
    facts = normalize(RAW, "Movie.mkv")
    assert facts["bit_rate"] == 24_000_000
    assert [s["index"] for s in facts["streams"]] == [0, 1, 2, 3, 4]  # cover art, attachments, bad index dropped
    video, truehd, aac, pgs, srt = facts["streams"]
    assert video == {
        "index": 0, "type": "video", "codec": "hevc", "profile": "Main 10", "level": 153, "language": None, "title": None,
        "default": True, "forced": False, "hearing_impaired": False, "width": 3840, "height": 2160, "channels": None,
        "channel_layout": None, "sample_rate": None, "bit_rate": None, "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084",
        "frame_rate": 23.976, "dv_profile": 8, "dv_compat": 1,
    }
    assert (truehd["language"], truehd["title"], truehd["channels"], truehd["sample_rate"], truehd["default"]) == ("eng", "Atmos", 8, 48000, True)
    assert (aac["language"], aac["bit_rate"], aac["default"]) == (None, 192000, False)  # "und" is no language
    assert (pgs["codec"], pgs["forced"]) == ("hdmv_pgs_subtitle", True)
    assert srt["hearing_impaired"] is True
    # The compact facts the web player already shows are unchanged.
    assert (facts["video_codec"], facts["audio_codec"], facts["height"], facts["audio_tracks"]) == ("hevc", "truehd", 2160, 3)


def test_frame_colors_follow_playable_video_when_cover_art_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([
        {
            "format": {"format_name": "matroska,webm"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                {"index": 2, "codec_type": "video", "codec_name": "hevc", "color_space": "bt2020nc", "disposition": "bad"},
            ],
        },
        {"frames": [{"stream_index": 2, "color_space": "bt2020nc", "color_transfer": "smpte2084", "color_primaries": "bt2020"}]},
    ])
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(next(responses)).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    facts = normalize(run_ffprobe("ffprobe", Path("/media/movie.mkv")), "movie.mkv")

    assert len(commands) == 2
    assert commands[1][commands[1].index("-select_streams") + 1] == "2"
    assert facts["video_codec"] == "hevc"
    assert facts["streams"][1]["color_transfer"] == "smpte2084"


def test_zero_rates_and_junk_values_become_none() -> None:
    facts = normalize({"format": {}, "streams": [{"index": 0, "codec_type": "audio", "codec_name": "aac", "avg_frame_rate": "0/0", "channels": "two", "tags": {"language": "x" * 99}}]}, "a.m4a")
    (stream,) = facts["streams"]
    assert (stream["frame_rate"], stream["channels"], stream["language"]) == (None, None, "x" * 16)
    assert facts["bit_rate"] is None


def test_audio_precision_uses_actual_bits_when_the_probe_reports_them() -> None:
    facts = normalize({"streams": [
        {"index": 0, "codec_type": "audio", "codec_name": "flac", "bits_per_raw_sample": "24", "bits_per_sample": "32"},
        {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le", "bits_per_sample": "16"},
        {"index": 2, "codec_type": "audio", "codec_name": "opus", "bits_per_raw_sample": "0"},
    ]}, "Music.flac")
    assert [stream.get("bit_depth") for stream in facts["streams"]] == [24, 16, None]


def test_legacy_successful_caches_are_stale_but_cached_errors_are_not() -> None:
    assert is_stale(None, "1:2")
    assert is_stale({"fingerprint": "1:2", "container": "mp4"}, "1:2")  # legacy successful cache: no streams
    assert is_stale({"fingerprint": "1:1", "streams": []}, "1:2")  # the file changed
    assert is_stale({"fingerprint": "1:2", "streams": []}, "1:2")  # legacy successful cache lacks decoded-frame colors
    assert not is_stale({"fingerprint": "1:2", "streams": [], "_probe_cache_version": PROBE_CACHE_VERSION}, "1:2")
    assert not is_stale({"fingerprint": "1:2", "error": "probe_failed"}, "1:2")
