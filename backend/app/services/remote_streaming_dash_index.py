"""Bounded DASH and WebM SegmentBase index parsers."""
from __future__ import annotations

import struct

from app.services.remote_streaming_types import DashIndex, UnsupportedPlaybackError


def _parse_dash_index(data: bytes, *, content_length: int | None) -> DashIndex:
    position = 0
    saw_ftyp = False
    saw_moov = False
    while position + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, position)
        header_size = 8
        if size == 1:
            if position + 16 > len(data):
                break
            size = struct.unpack_from(">Q", data, position + 8)[0]
            header_size = 16
        if size < header_size or position + size > len(data):
            break
        if kind == b"ftyp":
            saw_ftyp = True
        elif kind == b"moov":
            saw_moov = True
        if kind != b"sidx":
            position += size
            continue
        cursor = position + header_size
        if cursor + 12 > position + size:
            break
        version = data[cursor]
        cursor += 4
        _reference_id, timescale = struct.unpack_from(">II", data, cursor)
        cursor += 8
        if timescale <= 0:
            break
        if version == 0:
            if cursor + 8 > position + size:
                break
            _earliest, first_offset = struct.unpack_from(">II", data, cursor)
            cursor += 8
        elif version == 1:
            if cursor + 16 > position + size:
                break
            _earliest, first_offset = struct.unpack_from(">QQ", data, cursor)
            cursor += 16
        else:
            break
        if cursor + 4 > position + size:
            break
        _reserved, reference_count = struct.unpack_from(">HH", data, cursor)
        cursor += 4
        if reference_count <= 0 or reference_count > 8_192 or cursor + reference_count * 12 > position + size:
            break
        duration = 0
        referenced_bytes = 0
        max_referenced_size = 0
        for _index in range(reference_count):
            referenced_size, subsegment_duration, _sap = struct.unpack_from(">III", data, cursor)
            cursor += 12
            if referenced_size & 0x80000000:
                raise UnsupportedPlaybackError("Indirect DASH indexes are not supported.")
            referenced_size &= 0x7FFFFFFF
            if referenced_size <= 0:
                raise UnsupportedPlaybackError("The DASH index contains an empty media reference.")
            referenced_bytes += referenced_size
            max_referenced_size = max(max_referenced_size, referenced_size)
            duration += subsegment_duration
        media_start = position + size + first_offset
        media_end = media_start + referenced_bytes
        if (
            position <= 0
            or not saw_ftyp
            or not saw_moov
            or duration <= 0
            or media_start < position + size
            or (content_length is not None and media_end > content_length)
        ):
            break
        return DashIndex(
            initialization_end=position - 1,
            index_start=position,
            index_end=position + size - 1,
            duration_seconds=duration / timescale,
            max_referenced_size=max_referenced_size,
            content_length=content_length or media_end,
        )
    raise UnsupportedPlaybackError("The source did not provide a bounded DASH segment index.")


def _ebml_vint(data: bytes, position: int, *, element_id: bool = False) -> tuple[int, int]:
    """Read one EBML variable-length integer; IDs keep their length marker, sizes drop it."""
    first = data[position]
    length = 9 - first.bit_length()
    if first == 0 or position + length > len(data):
        raise IndexError("truncated EBML integer")
    value = first if element_id else first & (0xFF >> length)
    value = int.from_bytes(bytes([value]) + data[position + 1 : position + length], "big")
    if not element_id and value == (1 << (7 * length)) - 1:
        raise IndexError("unknown-size EBML element")
    return value, position + length


def _parse_webm_index(data: bytes, *, content_length: int | None) -> DashIndex:
    """Find a WebM (YouTube ``webm_dash``) init range and its Cues, the SegmentBase index dash.js reads."""
    try:
        element, position = _ebml_vint(data, 0, element_id=True)
        size, position = _ebml_vint(data, position)
        if element != 0x1A45DFA3:
            raise IndexError("not EBML")
        element, position = _ebml_vint(data, position + size, element_id=True)
        size, position = _ebml_vint(data, position)
        if element != 0x18538067:
            raise IndexError("no Segment")
        segment_start, segment_end = position, position + size
        timecode_scale, duration, cues = 1_000_000, None, None
        while cues is None:
            element_start = position
            element, position = _ebml_vint(data, position, element_id=True)
            size, position = _ebml_vint(data, position)
            end = position + size
            if element == 0x1F43B675:
                raise IndexError("Cues after media")
            if element == 0x1C53BB6B:
                cues = (element_start, position, end)
            elif element == 0x1549A966:
                cursor = position
                while cursor < end:
                    child, cursor = _ebml_vint(data, cursor, element_id=True)
                    child_size, cursor = _ebml_vint(data, cursor)
                    value = data[cursor : cursor + child_size]
                    if child == 0x2AD7B1:
                        timecode_scale = int.from_bytes(value, "big")
                    elif child == 0x4489:
                        duration = struct.unpack(">f" if child_size == 4 else ">d", value)[0]
                    cursor += child_size
            position = end
        cues_start, cursor, cues_end = cues
        if cues_end > len(data):
            raise IndexError("Cues exceed the probe")
        positions: list[int] = []
        while cursor < cues_end:
            element, cursor = _ebml_vint(data, cursor, element_id=True)
            size, cursor = _ebml_vint(data, cursor)
            point_end = cursor + size
            if element == 0xBB:
                child_cursor, cluster = cursor, None
                while child_cursor < point_end and cluster is None:
                    child, child_cursor = _ebml_vint(data, child_cursor, element_id=True)
                    child_size, child_cursor = _ebml_vint(data, child_cursor)
                    if child == 0xB7:
                        track_cursor = child_cursor
                        while track_cursor < child_cursor + child_size:
                            leaf, track_cursor = _ebml_vint(data, track_cursor, element_id=True)
                            leaf_size, track_cursor = _ebml_vint(data, track_cursor)
                            if leaf == 0xF1:
                                cluster = int.from_bytes(data[track_cursor : track_cursor + leaf_size], "big")
                                break
                            track_cursor += leaf_size
                    child_cursor += child_size
                if cluster is not None:
                    positions.append(segment_start + cluster)
            cursor = point_end
    except (IndexError, struct.error):
        raise UnsupportedPlaybackError("The source did not provide a bounded DASH segment index.") from None
    media_end = min(segment_end, content_length) if content_length is not None else segment_end
    bounds = [*positions, media_end]
    sizes = [end - start for start, end in zip(bounds, bounds[1:])]
    if not positions or not duration or positions[0] < cues_end or any(size <= 0 for size in sizes):
        raise UnsupportedPlaybackError("The source did not provide a bounded DASH segment index.")
    return DashIndex(
        initialization_end=cues_start - 1,
        index_start=cues_start,
        index_end=cues_end - 1,
        duration_seconds=duration * timecode_scale / 1_000_000_000,
        max_referenced_size=max(sizes),
        content_length=content_length or media_end,
    )
