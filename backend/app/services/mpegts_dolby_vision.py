"""Bounded MPEG-TS PSI evidence for explicitly signaled Dolby Vision pairs.

This reads only PAT/PMT signalling.  It never guesses a Dolby Vision base and
enhancement relationship from PID order, stream order, or an HDMV convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


M2TS_PACKET_SIZE: Final = 192
TS_PACKET_SIZE: Final = 188
M2TS_PREFIX_SIZE: Final = 4
MAX_SECTION_LENGTH: Final = 1021
DOVI_DESCRIPTOR_TAG: Final = 0xB0
HEVC_STREAM_TYPE: Final = 0x24


@dataclass(frozen=True)
class DolbyVisionTransportPair:
    packet_size: int
    program_number: int
    pmt_pid: int
    base_pid: int
    enhancement_pid: int
    dv_profile: int
    dv_level: int
    dv_compat: int


def _crc32_mpeg(section: bytes) -> int:
    """The MPEG-2 CRC has polynomial 0x04C11DB7 and an all-ones seed."""
    crc = 0xFFFFFFFF
    for byte in section:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


class _Sections:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def add(self, payload: bytes, starts: bool) -> list[bytes] | None:
        """Append PSI bytes, returning complete CRC-validatable sections.

        A malformed pointer, unfinished previous section, or unbounded section
        is an invalid transport signal rather than an opportunity to resync.
        """
        completed: list[bytes] = []
        if starts:
            if not payload:
                return None
            pointer = payload[0]
            if pointer > len(payload) - 1:
                return None
            if pointer:
                # The pointer prefix may only complete a section already in
                # progress (or be stuffing). Do not splice an unfinished old
                # section into the payload after this new section start.
                prefix = payload[1:1 + pointer]
                if not self.buffer and any(value != 0xFF for value in prefix):
                    return None
                prior = self._feed(prefix)
                if prior is None or self.buffer:
                    return None
                completed.extend(prior)
            elif self.buffer:
                return None
            payload = payload[1 + pointer:]
        following = self._feed(payload)
        if following is None:
            return None
        completed.extend(following)
        return completed

    def _feed(self, payload: bytes) -> list[bytes] | None:
        self.buffer.extend(payload)
        completed: list[bytes] = []
        while self.buffer:
            if self.buffer[0] == 0xFF:
                if any(value != 0xFF for value in self.buffer):
                    return None
                self.buffer.clear()
                break
            if len(self.buffer) < 3:
                break
            if not self.buffer[1] & 0x80:
                return None
            length = ((self.buffer[1] & 0x0F) << 8) | self.buffer[2]
            if length > MAX_SECTION_LENGTH or length < 9:
                return None
            size = 3 + length
            if len(self.buffer) < size:
                break
            section = bytes(self.buffer[:size])
            del self.buffer[:size]
            if _crc32_mpeg(section) != 0:
                return None
            completed.append(section)
        return completed


def _header(section: bytes, table_id: int) -> tuple[int, int] | None:
    if len(section) < 12 or section[0] != table_id or not section[5] & 1 or section[6] != 0 or section[7] != 0:
        return None
    return int.from_bytes(section[3:5], "big"), (section[5] >> 1) & 0x1F


def _pat(section: bytes) -> tuple[int, int, int, int, bytes] | None:
    header = _header(section, 0x00)
    if header is None:
        return None
    transport_stream_id, version = header
    entries = section[8:-4]
    if len(entries) != 4:
        return None
    program = int.from_bytes(entries[:2], "big")
    pmt_pid = ((entries[2] & 0x1F) << 8) | entries[3]
    if program == 0 or not _valid_pid(pmt_pid):
        return None
    return transport_stream_id, version, program, pmt_pid, section


def _valid_pid(pid: int) -> bool:
    return 0 < pid < 0x1FFF


def _dovi_descriptor(data: bytes) -> tuple[int, int, int, int] | None:
    """Read a v1.0 Dolby TS descriptor's P7 EL dependency relationship."""
    if len(data) != 7 or data[:2] != b"\x01\x00":
        return None
    flags = int.from_bytes(data[2:4], "big")
    profile, level = (flags >> 9) & 0x7F, (flags >> 3) & 0x3F
    rpu, enhancement, base = bool(flags & 4), bool(flags & 2), bool(flags & 1)
    dependency = int.from_bytes(data[4:6], "big")
    dependency_pid, reserved = dependency >> 3, dependency & 0x07
    compatibility, compatibility_reserved = data[6] >> 4, data[6] & 0x0F
    if (
        profile != 7 or not 1 <= level <= 9 or compatibility != 6
        or not (rpu and enhancement and not base)
        or not _valid_pid(dependency_pid) or reserved != 0x07 or compatibility_reserved != 0x0F
    ):
        return None
    return dependency_pid, profile, level, compatibility


def _pmt(section: bytes, expected_program: int, pmt_pid: int) -> tuple[DolbyVisionTransportPair, bytes] | None:
    header = _header(section, 0x02)
    if header is None or header[0] != expected_program:
        return None
    program_number, _version = header
    if len(section) < 16:
        return None
    program_info_length = ((section[10] & 0x0F) << 8) | section[11]
    offset, end = 12 + program_info_length, len(section) - 4
    if offset > end:
        return None
    streams: dict[int, tuple[int, bool]] = {}
    dovi: list[tuple[int, int, int, int, int]] = []
    while offset < end:
        if end - offset < 5:
            return None
        stream_type = section[offset]
        pid = ((section[offset + 1] & 0x1F) << 8) | section[offset + 2]
        info_length = ((section[offset + 3] & 0x0F) << 8) | section[offset + 4]
        offset += 5
        if not _valid_pid(pid) or offset + info_length > end or pid in streams:
            return None
        descriptors = section[offset:offset + info_length]
        offset += info_length
        descriptor_offset = 0
        has_dovi_registration = False
        has_hevc_descriptor = False
        dovi_for_stream: tuple[int, int, int, int] | None = None
        while descriptor_offset < len(descriptors):
            if len(descriptors) - descriptor_offset < 2:
                return None
            tag, size = descriptors[descriptor_offset], descriptors[descriptor_offset + 1]
            descriptor_offset += 2
            if descriptor_offset + size > len(descriptors):
                return None
            value = descriptors[descriptor_offset:descriptor_offset + size]
            if tag == 0x05 and value == b"DOVI":
                has_dovi_registration = True
            if tag == 0x38 and size == 13:
                has_hevc_descriptor = True
            if tag == DOVI_DESCRIPTOR_TAG:
                parsed = _dovi_descriptor(value)
                if parsed is None:
                    return None
                if dovi_for_stream is not None:
                    return None
                dovi_for_stream = parsed
            descriptor_offset += size
        if dovi_for_stream is not None:
            if not has_dovi_registration:
                return None
            dependency, profile, level, compatibility = dovi_for_stream
            dovi.append((pid, dependency, profile, level, compatibility))
        streams[pid] = stream_type, has_hevc_descriptor
    if offset != end or len(dovi) != 1:
        return None
    enhancement_pid, base_pid, profile, level, compatibility = dovi[0]
    enhancement = streams.get(enhancement_pid)
    base = streams.get(base_pid)
    if enhancement != (0x06, True) or base != (HEVC_STREAM_TYPE, True):
        return None
    return DolbyVisionTransportPair(
        packet_size=M2TS_PACKET_SIZE, program_number=program_number, pmt_pid=pmt_pid,
        base_pid=base_pid, enhancement_pid=enhancement_pid,
        dv_profile=profile, dv_level=level, dv_compat=compatibility,
    ), section


def parse_m2ts_dolby_vision_pair(data: bytes) -> DolbyVisionTransportPair | None:
    """Return one explicit P7 dual-PID association from bounded M2TS bytes.

    The caller supplies at most four MiB from a regular local ``.m2ts`` file.
    Invalid packet geometry, scrambled packets, continuity gaps, PSI CRC errors,
    multi-program PATs, or changing PAT/PMT content are all refused.
    """
    packet_count = len(data) // M2TS_PACKET_SIZE
    if packet_count == 0:
        return None
    assemblers: dict[int, _Sections] = {0: _Sections()}
    continuity: dict[int, int] = {}
    pat_value: tuple[int, int, int, int, bytes] | None = None
    pmt_value: tuple[DolbyVisionTransportPair, bytes] | None = None
    for packet_number in range(packet_count):
        packet = data[packet_number * M2TS_PACKET_SIZE + M2TS_PREFIX_SIZE:(packet_number + 1) * M2TS_PACKET_SIZE]
        if len(packet) != TS_PACKET_SIZE or packet[0] != 0x47:
            return None
        tei, starts, pid = bool(packet[1] & 0x80), bool(packet[1] & 0x40), ((packet[1] & 0x1F) << 8) | packet[2]
        scrambled, adaptation = (packet[3] >> 6) & 0x03, (packet[3] >> 4) & 0x03
        if tei or scrambled or adaptation == 0:
            return None
        has_payload = adaptation in {1, 3}
        offset = 4
        if adaptation in {2, 3}:
            offset += 1 + packet[offset]
            if offset > len(packet):
                return None
        if has_payload:
            # PID 0x1fff is the legal null-packet PID. Its continuity counter
            # is not an association fact, unlike every useful transport PID.
            if pid != 0x1FFF:
                previous = continuity.get(pid)
                if previous is not None and ((previous + 1) & 0x0F) != (packet[3] & 0x0F):
                    return None
                continuity[pid] = packet[3] & 0x0F
        if not has_payload or pid not in assemblers:
            continue
        sections = assemblers[pid].add(packet[offset:], starts)
        if sections is None:
            return None
        for section in sections:
            if pid == 0:
                parsed = _pat(section)
                if parsed is None:
                    return None
                if pat_value is not None and pat_value != parsed:
                    return None
                pat_value = parsed
                if len(assemblers) == 1:
                    assemblers[parsed[3]] = _Sections()
            elif pat_value is not None and pid == pat_value[3]:
                parsed = _pmt(section, pat_value[2], pid)
                if parsed is None:
                    return None
                if pmt_value is not None and pmt_value != parsed:
                    return None
                pmt_value = parsed
    return pmt_value[0] if pat_value is not None and pmt_value is not None else None
