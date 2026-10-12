"""Subtitle auto-sync is pure and stdlib-only; one VAD case runs real ffmpeg on lavfi bursts."""
from __future__ import annotations

import random
import shutil
import statistics
import subprocess
from pathlib import Path

import pytest

from app.services import local_asr
from app.services.subtitle_sync import SyncError, jaccard, merge, speech_from_silencedetect, sync


def _speech(seed: int = 1, minutes: int = 20) -> list[tuple[int, int]]:
    rnd, at, out = random.Random(seed), 2000, []
    while at < minutes * 60_000:
        length = rnd.randint(500, 4000)
        out.append((at, at + length))
        at += length + rnd.randint(300, 3000)
    return out


REF = _speech()
CUES = [(start, end, f"line {n}") for n, (start, end) in enumerate(REF)]


def _max_error(synced: list, expected: list = CUES) -> int:
    return max(abs(got[0] - want[0]) for got, want in zip(synced, expected, strict=True))


def test_merge_and_jaccard() -> None:
    assert merge([(5, 8), (0, 2), (1, 3), (9, 9)]) == [(0, 3), (5, 8)]
    assert jaccard([(0, 10)], [(5, 15)]) == pytest.approx(5 / 15)
    assert jaccard([], [(0, 1)]) == 0.0


def test_constant_offset_is_removed() -> None:
    assert _max_error(sync([(s + 2500, e + 2500, t) for s, e, t in CUES], speech=REF)) <= 100


def test_frame_rate_drift_is_removed() -> None:
    ratio = 25 / 23.976
    assert _max_error(sync([(round(s * ratio), round(e * ratio), t) for s, e, t in CUES], speech=REF)) <= 100


def test_two_piece_cut_is_split() -> None:
    half = len(CUES) // 2
    cut = [(s + 3000 * (n >= half), e + 3000 * (n >= half), t) for n, (s, e, t) in enumerate(CUES)]
    assert _max_error(sync(cut, speech=REF)) <= 100


def test_dropped_lines_and_jitter_still_sync() -> None:
    rnd = random.Random(5)
    kept = [cue for cue in CUES if rnd.random() < 0.7]
    jittered = [(s + 2500 + rnd.randint(-150, 150), e + 2500 + rnd.randint(-300, 300), t) for s, e, t in kept]
    synced = sync(jittered, speech=REF)
    assert statistics.median(abs(got[0] - want[0]) for got, want in zip(synced, kept)) <= 200


def test_unrelated_track_is_rejected() -> None:
    with pytest.raises(SyncError, match="Could not sync confidently"):
        sync([(s, e, "x") for s, e in _speech(seed=99)], speech=REF)


def test_text_anchors_from_asr_words() -> None:
    words = [(s, e, f"w{n}") for n, (s, e) in enumerate(REF)]
    expected = [(s, e, f"w{n}") for n, (s, e) in enumerate(REF)]
    assert _max_error(sync([(s + 2500, e + 2500, t) for s, e, t in expected], speech=REF, words=words), expected) <= 100


def test_speech_from_silencedetect() -> None:
    log = "silence_start: 0\nsilence_end: 1.5 | silence_duration: 1.5\nsilence_start: 4.25\nsilence_end: 6\n"
    assert speech_from_silencedetect(log, 9000) == [(1500, 4250), (6000, 9000)]
    assert speech_from_silencedetect("silence_start: 2\n", 5000) == [(0, 2000)]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_vad_reference_from_lavfi_sine_bursts(tmp_path: Path) -> None:
    rnd, at, bursts = random.Random(3), 1000, []
    while at < 80_000:
        length = rnd.randint(600, 3000)
        bursts.append((at, at + length))
        at += length + rnd.randint(500, 2500)
    gate = "+".join(f"between(t,{start / 1000},{end / 1000})" for start, end in bursts)
    media = tmp_path / "bursts.mka"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='0.5*sin(2*PI*440*t)*({gate})':s=16000:d=85",
         "-c:a", "aac", str(media)],
        check=True, timeout=60,
    )
    log = subprocess.run(local_asr.vad_command("ffmpeg", media), capture_output=True, text=True, timeout=60).stderr
    shifted = [(start + 2000, end + 2000, f"burst {n}") for n, (start, end) in enumerate(bursts)]
    synced = sync(shifted, speech=speech_from_silencedetect(log, 85_000))
    assert max(abs(got[0] - start) for got, (start, _end) in zip(synced, bursts, strict=True)) <= 100
