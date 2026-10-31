"""Bounded reads of a local Matroska seek index, without scanning media packets."""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO
from functools import lru_cache


class _Reader:
    def __init__(self, file: BinaryIO, end: int) -> None:
        self.file, self.end = file, end
        self.remaining = 8 * 1024 * 1024
        self.elements = 1_000_000

    def read(self, size: int) -> bytes:
        if size < 0 or size > self.remaining:
            raise ValueError("Matroska index exceeds read budget")
        self.remaining -= size
        data = self.file.read(size)
        if len(data) != size:
            raise ValueError("Truncated Matroska index")
        return data

    def integer(self, *, identifier: bool = False) -> int | None:
        first = self.read(1)[0]
        length = 9 - first.bit_length()
        if first == 0 or length > (4 if identifier else 8):
            raise ValueError("Invalid Matroska integer")
        value = int.from_bytes(bytes([first if identifier else first & (255 >> length)]) + self.read(length - 1), "big")
        return None if not identifier and value == (1 << (7 * length)) - 1 else value

    def children(self, start: int, end: int):
        while start < end:
            self.elements -= 1
            if self.elements < 0:
                raise ValueError("Too many Matroska index elements")
            self.file.seek(start)
            identifier, size = self.integer(identifier=True), self.integer()
            payload = self.file.tell()
            stop = payload + size if size is not None else end
            if stop > end or stop <= start:
                raise ValueError("Invalid Matroska element bounds")
            yield identifier, payload, stop
            start = stop

    def uint(self, start: int, end: int) -> int:
        if not 0 < end - start <= 8:
            raise ValueError("Invalid Matroska unsigned integer")
        self.file.seek(start)
        return int.from_bytes(self.read(end - start), "big")


def read_video_cues(path: Path, stream_index: int) -> tuple[float, ...] | None:
    """Return selected-track cue times, or None for absent/invalid/oversized metadata.

    Cues can be sparse: callers must use each cue as an independent run, never
    assume these are every keyframe used by a continuous HLS muxer.
    """
    try:
        stat = path.stat()
        return _read_video_cues(path, stream_index, stat.st_size, stat.st_mtime_ns, stat.st_ino)
    except OSError:
        return None


@lru_cache(maxsize=32)
def _read_video_cues(path: Path, stream_index: int, size: int, mtime_ns: int, inode: int) -> tuple[float, ...] | None:
    # File-version arguments invalidate the bounded metadata cache on replacement.
    try:
        with path.open("rb") as file:
            reader = _Reader(file, size)
            top = reader.children(0, reader.end)
            if next(top)[0] != 0x1A45DFA3:
                return None
            segment = next((entry for entry in top if entry[0] == 0x18538067), None)
            if segment is None:
                return None
            _, base, end = segment
            sections: dict[int, tuple[int, int]] = {}
            offsets: list[int] = []
            for identifier, start, stop in reader.children(base, end):
                if identifier in (0x1549A966, 0x1654AE6B, 0x1C53BB6B):  # Info, Tracks, Cues
                    sections[identifier] = (start, stop)
                elif identifier == 0x114D9B74:  # SeekHead points past large clusters
                    for kind, a, b in reader.children(start, stop):
                        if kind != 0x4DBB:
                            continue
                        fields = {key: reader.uint(x, y) for key, x, y in reader.children(a, b) if key in (0x53AB, 0x53AC)}
                        if fields.get(0x53AB) in (0x1549A966, 0x1654AE6B, 0x1C53BB6B) and 0x53AC in fields:
                            offsets.append(base + fields[0x53AC])
                if identifier == 0x1F43B675:  # never traverse media clusters
                    break
            for offset in offsets:
                if not base <= offset < end:
                    raise ValueError("Invalid Matroska seek offset")
                identifier, start, stop = next(reader.children(offset, end))
                if identifier in (0x1549A966, 0x1654AE6B, 0x1C53BB6B):
                    sections[identifier] = (start, stop)
            if not all(key in sections for key in (0x1549A966, 0x1654AE6B, 0x1C53BB6B)):
                return None
            scale = 1_000_000
            for identifier, a, b in reader.children(*sections[0x1549A966]):
                if identifier == 0x2AD7B1:
                    scale = reader.uint(a, b)
            tracks = []
            for identifier, a, b in reader.children(*sections[0x1654AE6B]):
                if identifier == 0xAE:
                    tracks.append({key: reader.uint(x, y) for key, x, y in reader.children(a, b) if key in (0xD7, 0x83)})
            if not 0 <= stream_index < len(tracks) or tracks[stream_index].get(0x83) != 1:
                return None
            track = tracks[stream_index].get(0xD7)
            if not track or scale <= 0:
                return None
            times = []
            for identifier, a, b in reader.children(*sections[0x1C53BB6B]):
                if identifier != 0xBB:
                    continue
                stamp, selected = None, False
                for key, x, y in reader.children(a, b):
                    if key == 0xB3:
                        stamp = reader.uint(x, y)
                    elif key == 0xB7:
                        fields = {k: reader.uint(i, j) for k, i, j in reader.children(x, y) if k in (0xF7, 0xF1)}
                        selected |= fields.get(0xF7) == track and 0 <= fields.get(0xF1, -1) < end - base
                if selected and stamp is not None:
                    times.append(stamp * scale / 1_000_000_000)
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (size, mtime_ns, inode):
                return None
            return tuple(sorted(set(times))) or None
    except (OSError, ValueError, StopIteration):
        return None
