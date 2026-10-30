"""Durable, fail-closed IRAP maps for qualified Dolby Vision M2TS originals."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.background_work import background_work
from app.services.mpegts_dolby_vision import (
    M2TS_PACKET_SIZE, M2TS_PREFIX_SIZE, TS_PACKET_SIZE, DolbyVisionTransportPair,
    _Sections, _pat, _pmt,
)

MAX_KEYS = 20_000
MAX_JSON_BYTES = 1024 * 1024
MAX_PENDING_EL = 512
MAX_QUEUE = 100
PTS_WRAP = 1 << 33


@dataclass(frozen=True)
class IRAPIndex:
    fingerprint: str
    source_size: int
    base_pid: int
    enhancement_pid: int
    keyframe_pts: list[int]
    packet_offsets: list[int]

    def response(self, etag: str, source_id: str, stream_index: int) -> dict[str, Any]:
        return {
            "Status": "complete", "SourceId": source_id, "Version": 1, "Validator": etag,
            "SourceSize": self.source_size, "BasePID": self.base_pid,
            "EnhancementPID": self.enhancement_pid, "StreamIndex": stream_index,
            "TimeBaseNum": 1, "TimeBaseDen": 90_000,
            "KeyframePTS": self.keyframe_pts, "PacketOffsets": self.packet_offsets,
        }


def source_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def pair_from_facts(facts: dict[str, Any]) -> DolbyVisionTransportPair | None:
    value = facts.get("dv_transport_pair")
    if not isinstance(value, dict):
        return None
    try:
        return DolbyVisionTransportPair(**{key: int(value[key]) for key in (
            "packet_size", "program_number", "pmt_pid", "base_pid", "enhancement_pid", "dv_profile", "dv_level", "dv_compat",
        )})
    except (KeyError, TypeError, ValueError):
        return None


def _pts(value: bytes) -> int | None:
    if len(value) != 5 or value[0] & 1 != 1 or value[2] & 1 != 1 or value[4] & 1 != 1:
        return None
    return ((value[0] >> 1 & 7) << 30) | (value[1] << 22) | ((value[2] >> 1) << 15) | (value[3] << 7) | (value[4] >> 1)


class _PES:
    def __init__(self) -> None:
        self.header = bytearray()
        self.pts: int | None = None
        self.ready = False
        self.nal = bytearray()

    def start(self, payload: bytes) -> bytes | None:
        self.header = bytearray(payload)
        self.pts = None
        self.ready = False
        self.nal.clear()
        return self.feed(b"")

    def feed(self, payload: bytes) -> bytes | None:
        if self.ready:
            return payload
        self.header.extend(payload)
        if len(self.header) < 9:
            return None
        if self.header[:3] != b"\0\0\1" or self.header[6] & 0xC0 != 0x80:
            return None
        length = self.header[8]
        needed = 9 + length
        if needed > 264 or len(self.header) < needed:
            return None
        flags = self.header[7] & 0xC0
        self.pts = _pts(self.header[9:14]) if flags in (0x80, 0xC0) else None
        self.ready = self.pts is not None
        result = bytes(self.header[needed:]) if self.ready else None
        self.header.clear()
        return result

    def irap(self, payload: bytes) -> bool:
        self.nal.extend(payload)
        # Five bytes suffice for Annex-B's longest start code and HEVC header.
        while len(self.nal) >= 5:
            zeroes = 0
            while zeroes < len(self.nal) and self.nal[zeroes] == 0:
                zeroes += 1
            if zeroes < 2 or zeroes == len(self.nal) or self.nal[zeroes] != 1:
                del self.nal[0]
                continue
            if len(self.nal) < zeroes + 3:
                break
            first, second = self.nal[zeroes + 1], self.nal[zeroes + 2]
            del self.nal[:zeroes + 3]
            nal_type, layer = (first >> 1) & 0x3F, ((first & 1) << 5) | (second >> 3)
            if layer == 0 and 16 <= nal_type <= 21:
                return True
        if len(self.nal) > 5:
            del self.nal[:-5]
        return False


def scan(path: Path, expected: DolbyVisionTransportPair, stop: threading.Event | None = None) -> IRAPIndex | None:
    """Read a complete M2TS once; anything ambiguous yields no usable index."""
    try:
        before = path.stat()
        if before.st_size == 0 or before.st_size % M2TS_PACKET_SIZE:
            return None
        sections: dict[int, _Sections] = {0: _Sections()}
        continuity: dict[int, int] = {}
        pat = None
        pair = None
        pes = {expected.base_pid: _PES(), expected.enhancement_pid: _PES()}
        el_offsets: dict[int, int] = {}
        pending: dict[int, tuple[int, int]] = {}
        keys: list[tuple[int, int]] = []
        with path.open("rb") as source:
            packet_number = 0
            while packet := source.read(M2TS_PACKET_SIZE):
                if stop is not None and stop.is_set():
                    return None
                if len(packet) != M2TS_PACKET_SIZE:
                    return None
                ts = packet[M2TS_PREFIX_SIZE:]
                if ts[0] != 0x47:
                    return None
                tei, starts, pid = bool(ts[1] & 0x80), bool(ts[1] & 0x40), ((ts[1] & 0x1F) << 8) | ts[2]
                scramble, adaptation, cc = (ts[3] >> 6) & 3, (ts[3] >> 4) & 3, ts[3] & 15
                if tei or scramble or adaptation == 0:
                    return None
                offset, has_payload = 4, adaptation in (1, 3)
                if adaptation in (2, 3):
                    offset += 1 + ts[offset]
                    if offset > TS_PACKET_SIZE:
                        return None
                if has_payload and pid != 0x1FFF:
                    if pid in continuity and (continuity[pid] + 1) & 15 != cc:
                        return None
                    continuity[pid] = cc
                payload = ts[offset:] if has_payload else b""
                if has_payload and pid in sections:
                    completed = sections[pid].add(payload, starts)
                    if completed is None:
                        return None
                    for section in completed:
                        if pid == 0:
                            parsed = _pat(section)
                            if parsed is None or (pat is not None and parsed != pat):
                                return None
                            pat = parsed
                            sections.setdefault(parsed[3], _Sections())
                        elif pat is not None and pid == pat[3]:
                            parsed = _pmt(section, pat[2], pid)
                            if parsed is None or parsed[0] != expected or (pair is not None and parsed != pair):
                                return None
                            pair = parsed
                if pid in pes and has_payload:
                    parser = pes[pid]
                    body = parser.start(payload) if starts else parser.feed(payload)
                    if body is None or parser.pts is None:
                        return None
                    pts = parser.pts
                    if pid == expected.enhancement_pid and starts:
                        if pts in el_offsets or len(el_offsets) >= MAX_PENDING_EL:
                            return None
                        el_offsets[pts] = packet_number * M2TS_PACKET_SIZE
                        if pts in pending:
                            base_offset, _ = pending.pop(pts)
                            keys.append((pts, min(base_offset, el_offsets.pop(pts))))
                    elif pid == expected.base_pid and parser.irap(body):
                        if len(keys) + len(pending) >= MAX_KEYS or pts in pending:
                            return None
                        if pts in el_offsets:
                            keys.append((pts, min(packet_number * M2TS_PACKET_SIZE, el_offsets.pop(pts))))
                        else:
                            pending[pts] = (packet_number * M2TS_PACKET_SIZE, pts)
                packet_number += 1
        if pat is None or pair is None or pending or not keys:
            return None
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        # Keep the source's 90 kHz timeline, including a legal wrap, in decode order.
        unwrapped: list[tuple[int, int]] = []
        wrap, last = 0, None
        for pts, offset in keys:
            value = pts + wrap
            if last is not None and value < last - PTS_WRAP // 2:
                wrap += PTS_WRAP
                value += PTS_WRAP
            if last is not None and value <= last:
                return None
            unwrapped.append((value, offset))
            last = value
        result = IRAPIndex(source_fingerprint(path), before.st_size, expected.base_pid, expected.enhancement_pid,
                           [value for value, _ in unwrapped], [offset for _, offset in unwrapped])
        encoded = json.dumps(asdict(result), separators=(",", ":")).encode()
        return result if len(encoded) <= MAX_JSON_BYTES else None
    except OSError:
        return None


class M2TSIRAPIndexes:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, bool]] = {}
        self._queue: list[tuple[Path, DolbyVisionTransportPair, str, str]] = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _file(source_id: str) -> Path:
        return settings.data_dir / "m2ts-irap-index" / f"{source_id}.json"

    def get(self, path: Path, source_id: str) -> IRAPIndex | None:
        try:
            data = json.loads(self._file(source_id).read_text())
            result = IRAPIndex(**data)
            return result if result.fingerprint == source_fingerprint(path) else None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def request(self, path: Path, pair: DolbyVisionTransportPair, source_id: str, *, explicit: bool = True) -> IRAPIndex | None:
        result = self.get(path, source_id)
        if result is not None:
            return result
        try:
            fingerprint = source_fingerprint(path)
        except OSError:
            return None
        with self._lock:
            pending = self._pending.get(source_id)
            if pending is None or pending[0] != fingerprint:
                if len(self._queue) >= MAX_QUEUE:
                    if not explicit:
                        return None
                    _, _, dropped_id, dropped_fingerprint = self._queue.pop()
                    if self._pending.get(dropped_id, (None, False))[0] == dropped_fingerprint:
                        self._pending.pop(dropped_id, None)
                self._pending[source_id] = (fingerprint, explicit)
                self._queue.insert(0, (path, pair, source_id, fingerprint))
                self._wake.set()
            elif explicit:
                self._pending[source_id] = (fingerprint, True)
        return None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="m2ts-irap-index", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                work = self._queue.pop(0) if self._queue else None
            if work is None:
                self._wake.wait(60); self._wake.clear(); continue
            path, pair, source_id, fingerprint = work
            with self._lock:
                pending = self._pending.get(source_id)
                explicit = pending[1] if pending is not None and pending[0] == fingerprint else False
            if pending is None or pending[0] != fingerprint:
                continue
            if self._stop.is_set():
                break
            # A polling client is authenticated activity itself; making explicit work
            # wait for quiet would therefore deadlock its first cold seek.
            if not explicit and (background_work.busy() or not background_work.backfill_allowed()):
                with self._lock:
                    if self._pending.get(source_id, (None, False))[0] == fingerprint:
                        self._queue.append(work)
                self._wake.wait(1); self._wake.clear()
                continue
            result = scan(path, pair, self._stop)
            if result is not None and not self._stop.is_set():
                target = self._file(source_id); target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".tmp")
                try:
                    temporary.write_text(json.dumps(asdict(result), separators=(",", ":")))
                    os.replace(temporary, target)
                except OSError:
                    temporary.unlink(missing_ok=True)
            with self._lock:
                if self._pending.get(source_id, (None, False))[0] == fingerprint:
                    self._pending.pop(source_id, None)


m2ts_irap_indexes = M2TSIRAPIndexes()
