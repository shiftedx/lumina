"""Separate fMP4 audio/video renditions for Roku's combined-stream audio limitation."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from app.services.local_playback_sessions import ConversionError, PlaybackSession, sessions

SPLIT_FILE = re.compile(r"^(audio|video)-(init\.mp4|seg(\d{1,6})\.m4s)$")


def rendition_playlist(playlist: str, kind: str) -> str:
    return re.sub(r"\b(init\.mp4|seg\d{1,6}\.m4s)\b", lambda m: f"{kind}-{m[0]}", playlist)


def rendition_file(session: PlaybackSession, name: str, ffmpeg: str) -> Path | None:
    """Copy a completed muxed segment into separate, cached renditions; never encode it again.

    Both renditions retain the original packets' timestamps and the same whole-file plan.
    The session's seek lock prevents a restart from deleting the input during the copy.
    """
    match = SPLIT_FILE.fullmatch(name)
    if match is None:
        return None
    target = session.directory / name
    index = match[3]
    current = session.run.index if session.run is not None else 0
    source_name = f"seg{index if index is not None else max(current, session.max_requested)}.m4s"
    for attempt in range(2):
        segment = sessions.segment(session, source_name)
        with session.seek_lock:
            if target.is_file():
                return target
            if segment is None:
                return None
            if not segment.is_file():
                if attempt == 0:  # a concurrent seek removed it after segment() returned
                    continue
                return None
            with tempfile.TemporaryDirectory(prefix=".rendition-", dir=session.directory) as temp:
                root = Path(temp)
                command = [ffmpeg, "-nostdin", "-v", "error", "-copyts", "-protocol_whitelist", "file,concat",
                           "-i", f"concat:{session.directory / 'init.mp4'}|{segment}", "-sn", "-dn"]
                for kind in ("video", "audio"):
                    command += ["-map", f"0:{kind[0]}:0", "-c", "copy", "-f", "hls", "-hls_time", "3600",
                                "-hls_segment_type", "fmp4", "-hls_segment_options", "movflags=+frag_discont",
                                "-hls_fmp4_init_filename", f"{kind}-init.mp4",
                                "-hls_segment_filename", str(root / f"{kind}-seg%d.m4s"), str(root / f"{kind}.m3u8")]
                try:
                    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise ConversionError("Playback audio could not be prepared.") from exc
                if result.returncode:
                    raise ConversionError("Playback audio could not be prepared.")
                for kind in ("video", "audio"):
                    init = session.directory / f"{kind}-init.mp4"
                    if not init.is_file():
                        (root / f"{kind}-init.mp4").replace(init)
                    copied = root / f"{kind}-seg0.m4s"
                    if copied.is_file():
                        copied.replace(session.directory / f"{kind}-{segment.name}")
            return target if target.is_file() else None
    return None
