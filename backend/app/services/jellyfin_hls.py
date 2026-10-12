"""Whole-file HLS playlists for Jellyfin clients (their DynamicHlsController model, clean-room from observed behaviour).

Jellyfin's HLS timeline is always the whole file from 0: the playlist lists every segment up front with ENDLIST, the
client seeks inside it (StartTimeTicks never reaches an HLS URL), and the server starts ffmpeg wherever a requested
segment is missing. A ``Plan`` is that timeline. Encoded video is cut on a fixed grid (1 s, then 4 s) so any segment
can start a run. Copied video is cut where the source has keyframes, and ffmpeg's hls muxer places the cuts of each
run relative to the run's first keyframe, so the plan is a chain of runs of ``CHUNK_SEGMENTS`` segments: a restart
begins at its chunk's first keyframe and reproduces the plan exactly.
"""
from __future__ import annotations

import bisect
import logging
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from app.services.local_playback_sessions import FIRST_SEGMENT_SECONDS, SEGMENT_SECONDS
from app.services.media_probe import LOCAL_INPUT_ARGS

CHUNK_SEGMENTS = 15  # copied video: segments per keyframe-anchored run (a seek waits for at most one run's worth)
KEYFRAME_WAIT_SECONDS = 2.0  # how long a master playlist waits for a keyframe scan before the growing playlist is used
KEYFRAME_SCAN_SECONDS = 900
KEYFRAME_CACHE = 32


@dataclass(frozen=True)
class HlsRun:
    """One ffmpeg run: from segment ``index`` (first timestamp ``seek``) for ``length`` seconds, None = to the end."""
    index: int
    seek: float
    length: float | None = None
    init: str = "init.mp4"  # a restart writes its own copy, so the one clients fetch is never rewritten under them


@dataclass(frozen=True)
class Plan:
    starts: tuple[float, ...]
    durations: tuple[float, ...]
    anchors: tuple[int, ...] | None = None  # copied video: first segment of each run; None = any segment starts a run

    def index_at(self, seconds: float) -> int:
        return max(0, min(bisect.bisect_right(self.starts, seconds) - 1, len(self.starts) - 1))

    def run_for(self, index: int) -> HlsRun:
        if self.anchors is None:
            return HlsRun(index, self.starts[index])
        slot = bisect.bisect_right(self.anchors, index) - 1
        first = self.anchors[slot]
        end = self.anchors[slot + 1] if slot + 1 < len(self.anchors) else None
        return HlsRun(first, self.starts[first], self.starts[end] - self.starts[first] if end is not None else None)

    def run_end(self, run: HlsRun) -> int:
        """First segment this run does not write."""
        if self.anchors is None:
            return len(self.starts)
        later = [a for a in self.anchors if a > run.index]
        return later[0] if later else len(self.starts)

    def playlist(self, api_key: str) -> str:
        token = quote(api_key, safe="")
        lines = [
            "#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{max(1, int(max(self.durations)) + 1)}",
            "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS",
            f'#EXT-X-MAP:URI="init.mp4?ApiKey={token}"',
        ]
        for index, duration in enumerate(self.durations):
            lines += [f"#EXTINF:{duration:.6f},", f"seg{index}.m4s?ApiKey={token}"]
        return "\n".join([*lines, "#EXT-X-ENDLIST", ""])


def encode_plan(duration: float) -> Plan:
    """Cuts at 0, 1, 5, 9 … s: where ffmpeg_command forces keyframes for an encode."""
    starts = [0.0]
    while starts[-1] + (SEGMENT_SECONDS if len(starts) > 1 else FIRST_SEGMENT_SECONDS) < duration - 0.05:
        starts.append(starts[-1] + (SEGMENT_SECONDS if len(starts) > 1 else FIRST_SEGMENT_SECONDS))
    return _plan(starts, duration, None)


def copy_plan(keyframes: list[float], duration: float, pin: float = 0) -> Plan | None:
    """Cut where ffmpeg's hls muxer cuts copied video: per run, the k-th cut is the first keyframe after the previous cut
    that is at least ``k`` segment lengths past the run's first keyframe. A run starts at the first keyframe of a chunk
    of ``CHUNK_SEGMENTS`` segments, and the chunk holding ``pin`` seconds ends at the last cut at or before it so the
    session's first run can start right there. None when the keyframes are unusable."""
    kfs = sorted({round(t, 6) for t in keyframes if 0 <= t < duration})
    if not kfs:
        return None
    starts, anchors, first, pinned = [0.0], [0], 0, pin <= 0
    while True:
        base, cuts, at = kfs[first], [], first
        for k in range(1, CHUNK_SEGMENTS + 1):
            prev = cuts[-1] if cuts else base
            while at < len(kfs) and not (kfs[at] > prev and kfs[at] >= base + k * SEGMENT_SECONDS - 1e-6):
                at += 1
            if at == len(kfs):
                break
            cuts.append(kfs[at])
        exhausted = len(cuts) < CHUNK_SEGMENTS
        if not pinned:
            inside = [cut for cut in cuts if cut <= pin]
            if len(inside) < len(cuts) or exhausted:
                pinned = True
                if inside:  # the chunk ends at the last cut at or before the pin
                    cuts, exhausted, at = inside, False, kfs.index(inside[-1])
        starts += cuts
        if exhausted:
            break
        first = at
        anchors.append(len(starts) - 1)
    return _plan(starts, duration, tuple(anchors))


def _plan(starts: list[float], duration: float, anchors: tuple[int, ...] | None) -> Plan:
    ends = [*starts[1:], duration]
    return Plan(tuple(starts), tuple(max(0.001, end - start) for start, end in zip(starts, ends)), anchors)


_keyframes: OrderedDict[tuple[str, int, int], list[float]] = OrderedDict()  # [] = the scan failed
_scans: dict[tuple[str, int, int], threading.Event] = {}
_lock = threading.Lock()


def _scan(ffprobe: str, path: Path, video_index: int) -> list[float]:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", *LOCAL_INPUT_ARGS, "-select_streams", str(video_index), "-show_entries", "packet=pts_time,flags",
             "-of", "csv=p=0", "--", str(path)],
            capture_output=True, text=True, timeout=KEYFRAME_SCAN_SECONDS, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    times = []
    for line in result.stdout.splitlines():
        stamp, _, flags = line.partition(",")
        if "K" in flags:
            try:
                times.append(float(stamp))
            except ValueError:
                pass
    return times


def keyframes(ffprobe: str, path: Path, video_index: int, wait: float = 0) -> list[float] | None:
    """Keyframe times of one video stream, scanned once per file version in the background. Waits up to ``wait``
    seconds for a scan in flight; None while it is unfinished or failed (the caller keeps the growing playlist).

    In memory, so the first play after a restart rescans (ffprobe reads the whole file: seconds on SSD,
    longer over a NAS); persist next to the probe, or read Matroska Cues, if that shows up.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    key = (str(path), info.st_size, info.st_mtime_ns)
    with _lock:
        if key in _keyframes:
            _keyframes.move_to_end(key)
            return _keyframes[key] or None
        done = _scans.get(key)
        if done is None:
            done = _scans[key] = threading.Event()

            def run() -> None:
                try:
                    found = _scan(ffprobe, path, video_index)
                except Exception:  # never leave waiters hanging
                    logging.getLogger(__name__).exception("keyframe scan failed")
                    found = []
                with _lock:
                    _keyframes[key] = found
                    while len(_keyframes) > KEYFRAME_CACHE:
                        _keyframes.popitem(last=False)
                    _scans.pop(key, None)
                done.set()

            threading.Thread(target=run, name="keyframe-scan", daemon=True).start()
    if wait and done.wait(wait):
        with _lock:
            return _keyframes.get(key) or None
    return None


def plan_for(video: str | None, ffprobe: str | None, path: Path, facts: dict, video_index: int | None, resume: float = 0) -> Plan | None:
    """The whole-file plan for a video session, or None to keep the growing playlist."""
    duration = facts.get("duration")
    if video not in ("copy", "encode") or not duration or duration < 1:
        return None
    if video == "encode":
        return encode_plan(duration)
    if not ffprobe or video_index is None:
        return None
    found = keyframes(ffprobe, path, video_index, KEYFRAME_WAIT_SECONDS)
    return copy_plan(found, duration, resume) if found else None
