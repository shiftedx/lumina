from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import threading

from fastapi import HTTPException
import pytest

from app.config import settings
from app.routers import music as music_router
from app.services import music_audio


@pytest.fixture
def audio_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", root)
    with music_audio._cache_lock:
        music_audio._leased_files.clear()
    yield root / "music-audio"
    with music_audio._cache_lock:
        assert not music_audio._leased_files


def _fake_conversion(monkeypatch: pytest.MonkeyPatch, payload: bytes = b"data") -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(music_audio.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(music_audio, "_probe_audio", lambda _source: {})

    def run(args, **_kwargs) -> None:  # noqa: ANN001
        calls.append(args)
        Path(args[-1]).write_bytes(payload)

    monkeypatch.setattr(music_audio.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        ({"codec_name": "pcm_s16le", "bits_per_sample": 16, "sample_fmt": "s16"}, "pcm_s16le"),
        ({"codec_name": "flac", "bits_per_raw_sample": "24", "sample_fmt": "s32"}, "pcm_s24le"),
        ({"codec_name": "pcm_s32le", "bits_per_sample": 32, "sample_fmt": "s32"}, "pcm_s32le"),
        ({"codec_name": "pcm_f32le", "sample_fmt": "flt"}, "pcm_f32le"),
        ({"codec_name": "aac", "sample_fmt": "fltp"}, "pcm_f32le"),
        ({"codec_name": "mp3"}, "pcm_f32le"),
        ({"codec_name": "opus"}, "pcm_f32le"),
        ({"codec_name": "vorbis"}, "pcm_f32le"),
    ],
)
def test_wav_codec_preserves_reported_integer_or_decoded_float_precision(
    stream: dict, expected: str,
) -> None:
    assert music_audio._wav_codec(stream) == expected


def test_same_digest_conversion_is_single_flight(
    audio_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audio_cache
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    entered = threading.Event()
    proceed = threading.Event()
    calls: list[list[str]] = []
    monkeypatch.setattr(music_audio.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(music_audio, "_probe_audio", lambda _source: {})

    def run(args, **_kwargs) -> None:  # noqa: ANN001
        calls.append(args)
        entered.set()
        assert proceed.wait(timeout=2)
        Path(args[-1]).write_bytes(b"converted")

    monkeypatch.setattr(music_audio.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(music_audio.playback_file, source, "flac")
        assert entered.wait(timeout=2)
        second = pool.submit(music_audio.playback_file, source, "flac")
        proceed.set()
        results = [first.result(timeout=2), second.result(timeout=2)]

    assert len(calls) == 1
    assert results[0].path == results[1].path
    assert results[0].path.read_bytes() == b"converted"
    for result in results:
        result.release()


def test_cache_hit_and_eviction_are_serialized_and_active_lease_is_preserved(
    audio_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audio_cache
    _fake_conversion(monkeypatch)
    monkeypatch.setattr(music_audio, "MAX_CACHE_BYTES", 4)
    first_source = tmp_path / "first.bin"
    second_source = tmp_path / "second.bin"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    initial = music_audio.playback_file(first_source, "flac")
    first_path = initial.path
    initial.release()

    entered = threading.Event()
    proceed = threading.Event()
    real_utime = music_audio.os.utime

    def blocked_utime(path, times) -> None:  # noqa: ANN001
        entered.set()
        assert proceed.wait(timeout=2)
        real_utime(path, times)

    monkeypatch.setattr(music_audio.os, "utime", blocked_utime)
    with ThreadPoolExecutor(max_workers=2) as pool:
        hit_future = pool.submit(music_audio.playback_file, first_source, "flac")
        assert entered.wait(timeout=2)
        eviction_future = pool.submit(music_audio.playback_file, second_source, "flac")
        proceed.set()
        hit = hit_future.result(timeout=2)
        with pytest.raises(HTTPException, match="capacity is currently in use"):
            eviction_future.result(timeout=2)

    assert first_path.exists()
    hit.release()
    replacement = music_audio.playback_file(second_source, "flac")
    assert replacement.path.exists()
    assert not first_path.exists()
    replacement.release()


def test_response_lease_pins_cache_before_open_and_releases_after_serving(
    audio_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audio_cache
    _fake_conversion(monkeypatch)
    monkeypatch.setattr(music_audio, "MAX_CACHE_BYTES", 4)
    first_source = tmp_path / "first.bin"
    second_source = tmp_path / "second.bin"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    playback = music_audio.playback_file(first_source, "flac")
    first_path = playback.path
    response = music_router._music_audio_response(playback, headers={"Cache-Control": "no-store"})
    started = threading.Event()
    proceed = threading.Event()

    async def request() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                started.set()
                assert proceed.wait(timeout=2)

        await response({"type": "http", "method": "GET", "headers": []}, receive, send)

    with ThreadPoolExecutor(max_workers=1) as pool:
        serving = pool.submit(asyncio.run, request())
        assert started.wait(timeout=2)
        with pytest.raises(HTTPException, match="capacity is currently in use"):
            music_audio.playback_file(second_source, "flac")
        assert first_path.exists()
        proceed.set()
        serving.result(timeout=2)

    replacement = music_audio.playback_file(second_source, "flac")
    assert not first_path.exists()
    replacement.release()


class _FakePlayback:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.releases = 0

    def __iter__(self):  # noqa: ANN204
        yield self.path
        yield "audio/flac"

    def release(self) -> None:
        self.releases += 1


@pytest.mark.parametrize("failure", [None, RuntimeError("send failed"), asyncio.CancelledError()])
def test_audio_response_releases_lease_on_success_error_and_cancellation(
    tmp_path: Path, failure: BaseException | None,
) -> None:
    path = tmp_path / "track.flac"
    path.write_bytes(b"audio")
    playback = _FakePlayback(path)
    response = music_router._music_audio_response(playback, headers={"Cache-Control": "no-store"})

    async def request() -> None:
        async def receive() -> dict:
            return {"type": "http.request"}

        async def send(_message: dict) -> None:
            if failure is not None:
                raise failure

        await response({"type": "http", "method": "GET", "headers": []}, receive, send)

    if failure is None:
        asyncio.run(request())
    else:
        with pytest.raises(type(failure)):
            asyncio.run(request())
    assert playback.releases == 1


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools are unavailable")
@pytest.mark.parametrize("codec", ["pcm_s32le", "pcm_f32le"])
def test_wav_fallback_preserves_32_bit_source_precision(
    audio_cache: Path, tmp_path: Path, codec: str,
) -> None:
    del audio_cache
    source = tmp_path / f"source-{codec}.wav"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=32000:duration=0.02",
            "-ac",
            "1",
            "-c:a",
            codec,
            str(source),
        ],
        check=True,
    )

    playback = music_audio.playback_file(source, "wav")
    probe = subprocess.run(
        [shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(playback.path)],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]

    assert stream["codec_name"] == codec
    assert stream["sample_rate"] == "32000"
    assert stream["channels"] == 1
    playback.release()


@pytest.mark.parametrize("suffix", ["m4a", "m4b", "mp4"])
def test_native_mp4_audio_has_mp4_content_type(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"registered.{suffix}"
    source.write_bytes(b"registered-audio")
    playback = music_audio.playback_file(source)
    assert playback.path == source
    assert playback.content_type == "audio/mp4"


def test_chromecast_audio_is_cached_as_stereo_aac_m4a(audio_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del audio_cache
    source = tmp_path / "source.flac"
    source.write_bytes(b"source")
    calls = _fake_conversion(monkeypatch)
    playback = music_audio.playback_file(source, "chromecast")
    assert playback.path.suffix == ".m4a" and playback.content_type == "audio/mp4"
    assert ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"] == calls[0][calls[0].index("-c:a"):calls[0].index("-c:a") + 10]
    playback.release()
