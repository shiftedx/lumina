"""Bounded ffmpeg remux/transcode sessions that turn a local file into HLS.

Each session is an opaque id owning one ffmpeg child and one disposable directory of fMP4 segments, keyed per (member, device) so a TV and a phone can play at once. Originals are only read. Video encodes are capped by max_playback_sessions; copies share a fixed limit. A child far ahead of its player is paused (SIGSTOP) and resumed (SIGCONT). Directories go on cancel, idle expiry, least-recent use over the cache cap, shutdown and startup.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from app.config import settings
from app.services import subtitle_tracks
from app.services.hwaccel import RENDER_NODE, hwaccel
from app.services.media_probe import LOCAL_INPUT_ARGS
from app.services.playback_decision import Decision

if TYPE_CHECKING:
    from app.services.jellyfin_hls import HlsRun, Plan

IDLE_SECONDS = 600
MANIFEST_WAIT_SECONDS = 20
SEGMENT_SECONDS = 4
FIRST_SEGMENT_SECONDS = 1  # An encode cuts 1 s segments for its first SHORT_SEGMENTS, then 4 s ones
SHORT_SEGMENTS = 4
MANIFEST_POLL_SECONDS = 0.025
# hls.js first re-polls a growing playlist ~2 target durations after loading it, and plays only what that first one
# listed until then. So a session answers once this many segments are listed (or the playlist ended): an encode's three
# 1 s segments outlast its 2 s re-poll, and copied video, cut at source keyframes (often ~10 s apart), copies far
# faster than real time.
SEGMENTS_LISTED = 3
FILE_NAME = re.compile(r"^(index\.m3u8|init\.mp4|seg\d{1,6}\.m4s)$")
SEGMENT = re.compile(r"^seg(\d{1,6})\.m4s$")
OTHER_SESSIONS = 16  # remux/audio-only copies; max_playback_sessions caps video encodes
# A seekable (Jellyfin) session starts a new ffmpeg run when a segment is requested this far past the one in progress:
# Jellyfin's 24 s for encodes; copies run far faster than real time, so they wait longer.
RUN_GAP_SEGMENTS = 6
RUN_GAP_COPY_SEGMENTS = 30
THROTTLE_AHEAD_SECONDS = 120
RESUME_AHEAD_SECONDS = 60
ACTIVE_SECONDS = 30  # a session read this recently is never evicted for space
LOG_LIMIT_BYTES = 64 * 1024  # Ffmpeg.log stays under 64 KB
BUSY = "Every playback conversion slot is busy. Try again shortly."
# ffmpeg stderr tails for admin diagnostics, newest first; raw text, cleaned by admin_diagnostics._clean.
recent_errors: deque[str] = deque(maxlen=10)


class SessionCapError(RuntimeError):
    pass


class ConversionError(RuntimeError):
    pass


class SupersededError(RuntimeError):
    """A newer request for the same member device replaced this session before its first playlist."""


@dataclass
class PlaybackSession:
    id: str
    user_id: str
    item_id: str
    mode: str
    directory: Path
    process: subprocess.Popen
    start: float = 0  # the requested start: what a reused session must match
    media_start: float = 0  # where the media timeline really begins: copied video starts at the keyframe before ``start``
    last_access: float = field(default_factory=time.monotonic)
    device: str = "web"  # "web" or the connected app's DeviceToken id
    play_session_id: str | None = None  # Jellyfin PlaySessionId
    kind: str = "remux"  # remux | audio | video_sw | video_hw
    key: tuple = ()  # the choices a reused session must match
    bandwidth: int = 8_000_000  # advertised in a Jellyfin master playlist
    max_requested: int = -1  # highest segment index the player asked for
    throttled: bool = False
    hw: str = "none"  # none | qsv | vaapi: what encoded the video, for the admin Activity page
    info: dict = field(default_factory=dict)  # {"video": {...}, "audio": {...}} playback decision summary
    started_wall: float = field(default_factory=time.time)
    stopped_by_admin: bool = False
    # Jellyfin clients get the whole file's timeline up front (jellyfin_hls.Plan) and ffmpeg runs wherever they seek.
    plan: Plan | None = None
    respawn: Callable[[HlsRun], subprocess.Popen] | None = None
    run: HlsRun | None = None  # what the current ffmpeg child writes
    seek_lock: threading.Lock = field(default_factory=threading.Lock)


def describe_decision(decision: Decision, facts: dict[str, Any]) -> dict[str, Any]:
    """Source codec to output codec for the admin Activity page (encodes are always H.264/AAC)."""
    streams = {s.get("index"): s for s in facts.get("streams") or []}
    video, audio = streams.get(decision.video_index), streams.get(decision.audio_index)
    out: dict[str, Any] = {}
    if decision.video:
        source = video.get("codec") if video else None
        out["video"] = {
            "from": source, "to": "h264" if decision.video == "encode" else source,
            "height": decision.height if decision.video == "encode" else (video or {}).get("height"), "tonemap": decision.tonemap,
        }
    if decision.audio:
        source = audio.get("codec") if audio else None
        out["audio"] = {"from": source, "to": "aac" if decision.audio == "encode" else source}
    return out


# Called with every session that ends (stop, replace, idle reap, eviction); set by app.services.activity.
on_session_end: Callable[[PlaybackSession], None] | None = None


def session_root() -> Path:
    return settings.data_dir / "playback-sessions"


HW_DECODE = frozenset({"h264", "hevc", "av1", "vp9", "mpeg2video", "vc1"})
SOFTWARE_TONEMAP = "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
OPENCL_TONEMAP = "tonemap_opencl=tonemap=bt2390:desat=0:peak=100:t=bt709:m=bt709:p=bt709:format=nv12"
# Segments are cut at every keyframe, so only the forced ones may exist (Jellyfin 10.11 EncodingHelper): libx264 adds
# none at scene cuts, and h264_qsv makes a forced I frame an IDR (qsvenc.c marks only IDRs key). Jellyfin's x264opts
# also make the first segments come sooner.
ENCODERS = {
    "none": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-sc_threshold", "0", "-x264opts", "subme=0:me_range=16:rc_lookahead=10:me=hex:open_gop=0"],
    # QVBR/ICQ with -maxrate is the documented pairing; tune on the target hardware if bitrates overshoot.
    "qsv": ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "23", "-look_ahead", "0", "-forced_idr", "1"],
    "vaapi": ["-c:v", "h264_vaapi", "-rc_mode", "QVBR", "-global_quality", "23"],
}


def _video_graph(decision: Decision, codec: str | None, hw: str, tonemap: str) -> tuple[list[str], str]:
    """(hardware-device input args, filtergraph from the video stream to [v]) for an encoded video stream."""
    height = decision.height
    tm = tonemap if decision.tonemap else "none"
    pre: list[str] = []
    download: list[str] = []
    upload: list[str] = []
    if hw == "none":
        chain = [f"scale=-2:{height}", SOFTWARE_TONEMAP if tm == "software" else "format=yuv420p"]
    else:
        hw_decode = codec in HW_DECODE and tm != "software"
        pre = ["-init_hw_device", f"vaapi=va:{RENDER_NODE}"]
        if hw == "qsv":
            pre += ["-init_hw_device", "qsv=qs@va"]
        if tm == "opencl":
            pre += ["-init_hw_device", "opencl=ocl@va"]
        pre += ["-filter_hw_device", "qs" if hw == "qsv" else "va"]
        if hw_decode:
            pre += ["-hwaccel", hw, "-hwaccel_output_format", hw]
        if tm == "software":
            chain = [f"scale=-2:{height}", SOFTWARE_TONEMAP, "format=nv12", "hwupload=extra_hw_frames=64"]
        else:
            fmt = "p010" if tm == "opencl" else "nv12"
            chain = [] if hw_decode else [f"format={fmt}", "hwupload=extra_hw_frames=64"]
            if hw == "qsv" and tm == "vpp_qsv":
                chain.append(f"vpp_qsv=w=-1:h={height}:tonemap=1:format=nv12")
            elif hw == "qsv":
                chain.append(f"scale_qsv=w=-1:h={height}:format={fmt}")
            else:
                chain.append(f"scale_vaapi=w=-2:h={height}:format={fmt}")
            if tm == "opencl":
                chain += ["hwmap=derive_device=opencl", OPENCL_TONEMAP, f"hwmap=derive_device={hw}:reverse=1:extra_hw_frames=16"]
        download, upload = ["hwdownload", "format=nv12"], ["hwupload=extra_hw_frames=64"]
    head = f"[0:{decision.video_index}]{','.join(chain)}"
    if decision.burn_subtitle is None:
        return pre, f"{head}[v]"
    # Image subtitles are drawn in software; overlay_qsv would speed up burn-in where it is slow.
    return pre, (
        f"{','.join([head, *download])}[base];"
        f"[0:{decision.burn_subtitle}]scale=-2:{height}[sub];"
        f"[base][sub]{','.join(['overlay=eof_action=pass', *upload])}[v]"
    )


def ffmpeg_command(ffmpeg: str, source: Path, facts: dict[str, Any], directory: Path, decision: Decision, *, hw: str = "none", tonemap: str = "none", start: float = 0, audio_gain_db: float | None = None, run: HlsRun | None = None) -> list[str]:
    """One command for any decision: copy what the client plays, encode the rest to H.264/AAC.

    A non-zero ``start`` input-seeks there (keyframe-accurate for copied video) and keeps source
    timestamps, so the player can seek anywhere without waiting.
    """
    # Copied video starts at the keyframe before ``start``; -noaccurate_seek starts encoded audio there too, or the first
    # segment's audio begins seconds after its video and hls.js misplaces fragments (playback hangs after a restart).
    # A run of a Jellyfin whole-file playlist starts at its segment's first timestamp (``run.seek``). ffmpeg seeks a stream
    # with B-frames 3/23 s before the time asked (it matches keyframes by DTS), so copied video asks that much past the
    # keyframe to land on it rather than the one before, and the output offset is that same time so a timestamp stays itself.
    start = run.seek if run else start
    seek_at = start + 3 / 23 + 0.002 if run and decision.video == "copy" else start
    places = ".6f" if run else ".3f"
    seek = [*(["-noaccurate_seek"] if decision.video == "copy" else []), "-ss", f"{seek_at:{places}}"] if start else []
    offset = ["-output_ts_offset", f"{seek_at if run else start:{places}}"] if start else []
    length = ["-t", f"{run.length:.6f}"] if run and run.length else []
    codec = next((s.get("codec") for s in facts.get("streams") or [] if s.get("index") == decision.video_index), None)
    pre: list[str] = []
    out: list[str] = []
    if decision.video == "encode":
        pre, video_graph = _video_graph(decision, codec, hw, tonemap)
        bitrate = decision.bitrate or 8_000_000
        out += [
            "-filter_complex", video_graph, "-map", "[v]", *ENCODERS[hw],
            "-maxrate", str(bitrate), "-bufsize", str(bitrate * 2),
            # Web encodes: keyframes at 0, 1, 2, 3, 4, 8, 12 … s, where the 1 s first segments and the 4 s ones after them are
            # cut. A seekable (Jellyfin) run keeps the fixed 0, 1, 5, 9 … grid its up-front playlist promises (jellyfin_hls).
            "-force_key_frames", (f"expr:gte(t,max(0,n_forced*{SEGMENT_SECONDS}-{0 if run.index else SEGMENT_SECONDS - FIRST_SEGMENT_SECONDS}))" if run
                                  else f"expr:gte(t,if(lt(n_forced,{SHORT_SEGMENTS}),n_forced,{SEGMENT_SECONDS}*(n_forced-{SHORT_SEGMENTS - 1})))"),
        ]
    elif decision.video == "copy":
        out += ["-map", f"0:{decision.video_index}", "-c:v", "copy", *(["-tag:v", "hvc1"] if codec == "hevc" else [])]
    if decision.audio == "copy":
        out += ["-map", f"0:{decision.audio_index}", "-c:a", "copy"]
    elif decision.audio == "encode":
        channels = decision.audio_channels or 2
        out += ["-map", f"0:{decision.audio_index}", "-c:a", "aac", "-b:a", "160k" if channels <= 2 else "384k", "-ac", str(channels)]
        if audio_gain_db:  # only Jellyfin clients get server-side gain, and only when audio is encoded anyway
            out += ["-af", f"volume={audio_gain_db:.1f}dB"]
    # An EVENT playlist applies hls_init_time to every segment when video is copied, so only an encode (whose forced
    # keyframes already cut 1, 1, 1, 1, 4, 4 …, or 1, 4, 4 … for a seekable run) asks for the short first segments.
    first_segment = ["-hls_init_time", str(FIRST_SEGMENT_SECONDS)] if decision.video == "encode" and not (run and run.index) else []
    # Without frag_discont the first fragment is timed 0 and the start offset lands in an empty edit in init.mp4: runs would
    # not splice, and Firefox applies that edit on top of hls.js's own shift to playlist time, so a resume buffers at twice
    # its position and never plays. With it every fragment carries its absolute time and every run writes the same init.mp4.
    # A seekable run numbers its segments from its own and keeps ffmpeg's playlist apart: the client's is the Plan.
    playlist = ["-hls_segment_options", "movflags=+frag_discont", *(["-start_number", str(run.index)] if run else ["-hls_playlist_type", "event"])]
    return [
        ffmpeg, "-nostdin", "-v", "error", "-progress", str(directory / "progress"), *pre,
        *LOCAL_INPUT_ARGS, *seek, "-i", str(source), *offset, *length, "-sn", "-dn", *out,
        "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), *first_segment, *playlist,
        "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", run.init if run else "init.mp4",
        "-hls_segment_filename", str(directory / "seg%d.m4s"),
        "-hls_flags", "temp_file+independent_segments", str(directory / ("ffmpeg.m3u8" if run else "index.m3u8")),
    ]


def _signal(process: subprocess.Popen, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.send_signal(sig)


def _terminate(process: subprocess.Popen, grace_seconds: float = 5) -> None:
    if process.poll() is not None:
        return
    _signal(process, signal.SIGCONT)  # a throttled child ignores SIGTERM until it runs again
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _tail(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - limit))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _speed(directory: Path) -> float | None:
    """Latest ``speed=`` from ffmpeg's -progress file."""
    speeds = re.findall(r"speed=\s*([\d.]+)x", _tail(directory / "progress", 2048))
    return float(speeds[-1]) if speeds else None


def _listed(manifest: Path, segments: int) -> bool:
    """Whether the playlist exists and lists ``segments`` segments or has ended (temp_file: it is replaced whole)."""
    try:
        text = manifest.read_text()
    except OSError:
        return False
    return segments <= 1 or text.count("#EXTINF") >= segments or "#EXT-X-ENDLIST" in text


def _first_pts(ffprobe: str, directory: Path, fallback: float) -> float:
    """The earliest stream start of the finished first segment; ``fallback`` when it cannot be read."""
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-protocol_whitelist", "file,concat", "-show_entries", "stream=start_time", "-of", "csv=p=0",
             f"concat:{directory / 'init.mp4'}|{directory / 'seg0.m4s'}"],
            capture_output=True, text=True, timeout=5, check=False, stdin=subprocess.DEVNULL,
        )
        return min(float(line.strip(",")) for line in result.stdout.split())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return fallback


def _tree_bytes(path: Path) -> int:
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(folder, name)).st_size
    return total


def with_api_key(playlist: str, api_key: str) -> str:
    """Append ``ApiKey`` to every URI (segments and #EXT-X-MAP): AVPlayer drops auth headers on segment requests."""
    token = quote(api_key, safe="")

    def sign(uri: str) -> str:
        return f"{uri}{'&' if '?' in uri else '?'}ApiKey={token}"

    lines = []
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MAP:"):
            line = re.sub(r'URI="([^"]*)"', lambda match: f'URI="{sign(match[1])}"', line)
        elif line and not line.startswith("#"):
            line = sign(line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def master_playlist(session: PlaybackSession, variant_uri: str) -> str:
    """A one-rendition master playlist for Jellyfin clients; the variant is the session's index.m3u8."""
    return f"#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-INDEPENDENT-SEGMENTS\n#EXT-X-STREAM-INF:BANDWIDTH={session.bandwidth}\n{variant_uri}\n"


class LocalPlaybackSessions:
    def __init__(self) -> None:
        self._sessions: dict[str, PlaybackSession] = {}
        self._lock = threading.Lock()
        self.cache_cap_bytes = 10 * 1024**3  # AppSettings.transcode_cache_gb, refreshed by every start

    def start(
        self, *, user_id: str, item_id: str, ffmpeg: str, source: Path, facts: dict[str, Any], decision: Decision, cap: int,
        start: float = 0, device: str = "web", play_session_id: str | None = None, hwaccel_mode: str = "auto",
        min_free_bytes: int = 0, cache_cap_bytes: int | None = None, audio_gain_db: float | None = None, ffprobe: str | None = None,
        plan: Plan | None = None, resume: float = 0,
    ) -> PlaybackSession:
        """``plan`` makes the session seekable (Jellyfin clients): ffmpeg starts at the segment holding ``resume`` seconds."""
        if decision.mode == "unavailable":  # nothing to map: ffmpeg would fail anyway
            raise ConversionError("This file could not be converted for playback.")
        if cache_cap_bytes:
            self.cache_cap_bytes = cache_cap_bytes
        self.reap()
        duration = facts.get("duration")
        if duration:  # a seek to the very end still gets a last segment to play
            start = min(start, max(0.0, duration - SEGMENT_SECONDS))
        encode = decision.video == "encode"
        hw, tonemap = hwaccel.choice(ffmpeg, hwaccel_mode) if encode else ("none", "none")
        key = (decision.video_index, decision.audio_index, decision.burn_subtitle, decision.height, decision.video, decision.audio, plan is not None)
        spawn = functools.partial(
            self._spawn, user_id=user_id, item_id=item_id, device=device, play_session_id=play_session_id, ffmpeg=ffmpeg,
            source=source, facts=facts, decision=decision, start=start, audio_gain_db=audio_gain_db, key=key,
            plan=plan, run=plan.run_for(plan.index_at(resume)) if plan else None,
        )
        with self._lock:
            for session in self._sessions.values():
                if (session.user_id, session.device, session.item_id, session.start, session.key) == (user_id, device, item_id, start, key) and session.process.poll() in (None, 0):
                    session.last_access = time.monotonic()
                    session.play_session_id = play_session_id or session.play_session_id
                    return session
            # One conversion per member device: its previous one (another item, start or choice) yields the slot.
            stale = [s for s in self._sessions.values() if s.user_id == user_id and s.device == device]
            others = [s for s in self._sessions.values() if s not in stale]
            if sum(s.kind.startswith("video") == encode for s in others) >= (cap if encode else OTHER_SESSIONS):
                raise SessionCapError(BUSY)
            session_root().mkdir(parents=True, exist_ok=True)
            if min_free_bytes and shutil.disk_usage(session_root()).free < min_free_bytes:
                raise SessionCapError("The server is low on disk space, so playback conversion is paused.")
            for old in stale:
                self._sessions.pop(old.id)
            session = spawn(hw=hw, tonemap=tonemap)
        for old in stale:
            self._dispose(old)
        try:
            session = self._await_manifest(session)
            if decision.video == "copy" and start and ffprobe:
                session.media_start = _first_pts(ffprobe, session.directory, start)
        except ConversionError:
            if hw == "none":
                raise
            hwaccel.record_failure()
            video = next((s for s in facts.get("streams") or [] if s.get("index") == decision.video_index), {})
            if decision.tonemap and video.get("dv_profile") == 5:  # Software tone-mapping cannot reshape DV5
                raise
            with self._lock:  # one software retry: the hardware path died before its first playlist
                if any(s.user_id == user_id and s.device == device for s in self._sessions.values()):
                    raise SupersededError("A newer playback request replaced this one.") from None
                if sum(s.kind.startswith("video") for s in self._sessions.values()) >= cap:
                    raise SessionCapError(BUSY) from None
                session = spawn(hw="none", tonemap=hwaccel.software_tonemap(ffmpeg, hwaccel_mode))
            return self._await_manifest(session)
        if hw != "none":
            hwaccel.record_success()
        return session

    def _spawn(
        self, *, user_id: str, item_id: str, device: str, play_session_id: str | None, ffmpeg: str, source: Path,
        facts: dict[str, Any], decision: Decision, start: float, audio_gain_db: float | None, key: tuple, hw: str, tonemap: str,
        plan: Plan | None = None, run: HlsRun | None = None,
    ) -> PlaybackSession:
        """Launch one ffmpeg child in a fresh directory. The caller holds the lock."""
        session_id = secrets.token_urlsafe(24)
        directory = session_root() / session_id
        directory.mkdir(parents=True)

        def launch(run: HlsRun | None) -> subprocess.Popen:
            # O_APPEND, so reap() can truncate the log under a running child.
            with open(directory / "ffmpeg.log", "ab") as log:  # the child keeps its own descriptor
                return subprocess.Popen(
                    ffmpeg_command(ffmpeg, source, facts, directory, decision, hw=hw, tonemap=tonemap, start=start, audio_gain_db=audio_gain_db, run=run),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                )

        process = launch(run)
        kind = ("video_hw" if hw != "none" else "video_sw") if decision.video == "encode" else decision.kind
        bandwidth = decision.bitrate or int(facts.get("bit_rate") or 0) or 8_000_000
        session = PlaybackSession(
            session_id, user_id, item_id, decision.mode, directory, process, start, start,
            device=device, play_session_id=play_session_id, kind=kind, key=key, bandwidth=bandwidth,
            hw=hw, info=describe_decision(decision, facts), plan=plan, run=run, respawn=launch if plan else None,
        )
        self._sessions[session_id] = session
        return session

    def _await_manifest(self, session: PlaybackSession) -> PlaybackSession:
        manifest = session.directory / "index.m3u8"
        first = session.directory / f"seg{session.run.index}.m4s" if session.run else None  # a seekable session has no growing playlist
        deadline = time.monotonic() + MANIFEST_WAIT_SECONDS
        while not (first.is_file() and (session.directory / "init.mp4").is_file() if first else _listed(manifest, SEGMENTS_LISTED)):
            failed = session.process.poll() not in (None, 0) or time.monotonic() > deadline
            with self._lock:  # read after poll(): a supersede pops the session before it kills the child
                superseded = self._sessions.get(session.id) is not session
            if superseded:
                raise SupersededError("A newer playback request replaced this one.")
            if failed:
                recent_errors.appendleft(_tail(session.directory / "ffmpeg.log", 500).strip() or "ffmpeg stopped before its first segment")
                self.stop(session.id)
                raise ConversionError("This file could not be converted for playback.")
            time.sleep(MANIFEST_POLL_SECONDS)
        return session

    def get(self, session_id: str, user_id: str) -> PlaybackSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.user_id != user_id:
                return None
            session.last_access = time.monotonic()
            return session

    def find_play_session(self, user_id: str, device: str, play_session_id: str, item_id: str | None = None) -> PlaybackSession | None:
        """A Jellyfin client's live session by its PlaySessionId, only for the same member and device."""
        with self._lock:
            for session in self._sessions.values():
                if (session.user_id, session.device, session.play_session_id) == (user_id, device, play_session_id) \
                        and item_id in (None, session.item_id) and session.process.poll() in (None, 0):
                    session.last_access = time.monotonic()
                    return session
        return None

    def file(self, session: PlaybackSession, name: str) -> Path | None:
        if not FILE_NAME.fullmatch(name):
            return None
        if match := SEGMENT.fullmatch(name):
            session.max_requested = max(session.max_requested, int(match[1]))
        self._throttle(session)
        path = session.directory / name
        return path if path.is_file() else None

    def segment(self, session: PlaybackSession, name: str) -> Path | None:
        """``file()`` for a seekable session: a segment ffmpeg has not reached is waited for, and one it will not reach
        soon (behind the current run, past its end or beyond ``gap`` segments ahead) starts a run there first."""
        path = self.file(session, name)
        if path is not None or session.plan is None or session.run is None:
            return path
        match = SEGMENT.fullmatch(name)
        index = int(match[1]) if match else None
        if index is not None and index >= len(session.plan.starts):
            return None
        with session.seek_lock:
            if index is not None and not (session.directory / name).is_file() and self._needs_run(session, index):
                self._restart(session, session.plan.run_for(index))
        deadline = time.monotonic() + MANIFEST_WAIT_SECONDS
        while time.monotonic() < deadline:
            if (session.directory / name).is_file():
                return self.file(session, name)
            with self._lock:
                if self._sessions.get(session.id) is not session:
                    return None
            if session.process.poll() not in (None, 0):  # died: the log has why; one retry at this segment
                if index is None or session.run is None:
                    return None
                with session.seek_lock:
                    if session.process.poll() not in (None, 0):
                        self._restart(session, session.plan.run_for(index))
                        deadline = min(deadline, time.monotonic() + MANIFEST_WAIT_SECONDS)
                        if session.process.poll() not in (None, 0):
                            return None
            time.sleep(MANIFEST_POLL_SECONDS)
        return None

    @staticmethod
    def _needs_run(session: PlaybackSession, index: int) -> bool:
        """Whether segment ``index`` is out of the current ffmpeg run's reach. Files at and after a run's first segment
        are its own (``_restart`` clears older ones), so ``index - gap`` existing means the run is within ``gap``."""
        run, plan = session.run, session.plan
        if session.process.poll() is not None or index < run.index or index >= plan.run_end(run):
            return True
        gap = RUN_GAP_SEGMENTS if session.kind.startswith("video") else RUN_GAP_COPY_SEGMENTS
        return index - gap > run.index and not (session.directory / f"seg{index - gap}.m4s").is_file()

    def _restart(self, session: PlaybackSession, run: HlsRun) -> None:
        """Replace the session's ffmpeg with one that writes from ``run``. The caller holds ``seek_lock``."""
        with self._lock:
            if self._sessions.get(session.id) is not session:
                return
        _terminate(session.process)
        for stale in session.directory.glob("seg*.m4s"):  # a run's files after its start are its own; older runs' are kept
            if (match := SEGMENT.fullmatch(stale.name)) and int(match[1]) >= run.index:
                with contextlib.suppress(OSError):
                    stale.unlink()
        run = replace(run, init="init-restart.mp4")
        session.max_requested, session.throttled, session.run = run.index, False, run
        session.process = session.respawn(run)
        with self._lock:
            if self._sessions.get(session.id) is not session:  # stopped meanwhile
                _terminate(session.process)

    @staticmethod
    def _throttle(session: PlaybackSession) -> None:
        """Pause ffmpeg > 120 s ahead of the player, resume under 60 s. ffmpeg renames segN into place only when complete."""
        if session.process.poll() is not None:
            return
        ahead = session.directory / f"seg{session.max_requested + THROTTLE_AHEAD_SECONDS // SEGMENT_SECONDS}.m4s"
        near = session.directory / f"seg{session.max_requested + RESUME_AHEAD_SECONDS // SEGMENT_SECONDS}.m4s"
        if not session.throttled and ahead.exists():
            _signal(session.process, signal.SIGSTOP)
            session.throttled = True
        elif session.throttled and not near.exists():
            _signal(session.process, signal.SIGCONT)
            session.throttled = False

    def stop(self, session_id: str, user_id: str | None = None) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or (user_id is not None and session.user_id != user_id):
                return False
            self._sessions.pop(session_id)
        self._dispose(session)
        return True

    def stop_where(self, *, user_id: str, device: str | None = None, play_session_id: str | None = None) -> int:
        """Stop a member's sessions, optionally only one device's or one Jellyfin PlaySessionId's."""
        with self._lock:
            doomed = [
                s for s in self._sessions.values()
                if s.user_id == user_id and device in (None, s.device) and play_session_id in (None, s.play_session_id)
            ]
            for session in doomed:
                self._sessions.pop(session.id)
        for session in doomed:
            self._dispose(session)
        return len(doomed)

    def reap(self) -> None:
        now = time.monotonic()
        with self._lock:
            idle = [s for s in self._sessions.values() if s.last_access < now - IDLE_SECONDS]
            for session in idle:
                self._sessions.pop(session.id)
            live = list(self._sessions.values())
        for session in idle:
            self._dispose(session)
        for session in live:  # a paused player sends no requests; the maintenance tick still throttles
            self._throttle(session)
            log = session.directory / "ffmpeg.log"
            with contextlib.suppress(OSError):
                if log.stat().st_size > LOG_LIMIT_BYTES:
                    os.truncate(log, 0)
        self._evict_over_cap(now)
        subtitle_tracks.prune_cache()

    def _evict_over_cap(self, now: float) -> None:
        """Dispose the least recently read sessions until the cache fits the cap.

        One long session's own segments are never trimmed.
        """
        with self._lock:
            candidates = sorted(self._sessions.values(), key=lambda s: s.last_access)
        sizes = {s.id: _tree_bytes(s.directory) for s in candidates}
        total = sum(sizes.values())
        for session in candidates:
            if total <= self.cache_cap_bytes:
                return
            if session.last_access > now - ACTIVE_SECONDS:
                continue
            if self.stop(session.id):
                total -= sizes[session.id]

    def live(self) -> list[PlaybackSession]:
        with self._lock:
            return list(self._sessions.values())

    def for_device(self, user_id: str, device: str, item_id: str) -> PlaybackSession | None:
        with self._lock:
            return next((s for s in self._sessions.values() if (s.user_id, s.device, s.item_id) == (user_id, device, item_id)), None)

    def video_encodes(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.kind.startswith("video") and s.process.poll() is None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            live = list(self._sessions.values())
        return {
            "sessions": dict(Counter(s.kind for s in live)),
            "throttled": sum(1 for s in live if s.throttled),
            "cache_bytes": _tree_bytes(session_root()),
            "speeds": {s.id[:8]: speed for s in live if (speed := _speed(s.directory)) is not None},
        }

    def close_all(self) -> None:
        """Stop every child and drop all derivatives, including ones a crashed process left."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            _terminate(session.process)
        shutil.rmtree(session_root(), ignore_errors=True)

    @staticmethod
    def _dispose(session: PlaybackSession) -> None:
        if on_session_end is not None:
            try:
                on_session_end(session)
            except Exception:  # history must never block freeing the slot
                logging.getLogger(__name__).exception("activity history write failed")
        _terminate(session.process)
        shutil.rmtree(session.directory, ignore_errors=True)

sessions = LocalPlaybackSessions()
