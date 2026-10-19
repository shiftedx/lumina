"""Standard-library-only raster header and container validation."""
from __future__ import annotations

import struct
import zlib


MAX_PIXELS = 40_000_000
MAX_FRAMES = 256
MAX_TOTAL_PIXELS = 80_000_000
MAX_BLOCKS = 65_536
MAX_METADATA_BYTES = 1024 * 1024
MAX_PNG_CHUNKS = 65_536
_PNG_KEEP = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"})


def image_size(data: bytes) -> tuple[int, int] | None:
    """Read PNG, WebP or JPEG dimensions without decoding pixels."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if chunk == b"VP8L" and data[20:21] == b"\x2f":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        return None
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 <= len(data):
            if data[index] != 0xFF:
                return None
            marker = data[index + 1]
            if marker == 0xFF:
                index += 1
            elif marker == 0x01 or 0xD0 <= marker <= 0xD8:
                index += 2
            elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[index + 7:index + 9], "big"), int.from_bytes(data[index + 5:index + 7], "big")
            else:
                index += 2 + int.from_bytes(data[index + 2:index + 4], "big")
    return None


def strip_png(data: bytes) -> bytes:
    """Return only a CRC-checked PNG's critical decoding chunks."""
    out = bytearray(data[:8])
    first, seen_idat, last = None, False, None
    for kind, _payload, start, end in _png_chunks(data):
        if kind in _PNG_KEEP:
            out += memoryview(data)[start:end]
        first, seen_idat, last = first or kind, seen_idat or kind == b"IDAT", kind
    if first != b"IHDR" or not seen_idat:
        raise ValueError
    return bytes(out)


def _png_chunks(data: bytes):  # noqa: ANN202 - private iterator of memory views and offsets
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError
    view, at, count, last = memoryview(data), 8, 0, None
    while last != b"IEND":
        count += 1
        if count > MAX_PNG_CHUNKS or at + 12 > len(data):
            raise ValueError
        length, kind = struct.unpack_from(">I4s", data, at)
        end = at + 12 + length
        if length > 0x7FFFFFFF or end > len(data) or zlib.crc32(view[at + 4:end - 4]) != int.from_bytes(view[end - 4:end], "big"):
            raise ValueError
        yield kind, view[at + 8:end - 4], at, end
        last, at = kind, end


def is_book_image(data: bytes, mime: str) -> bool:
    try:
        width, height, frames = image_shape(data, mime)
    except (ValueError, OverflowError):
        return False
    pixels = width * height
    return width > 0 and height > 0 and 0 < frames <= MAX_FRAMES and pixels <= MAX_PIXELS and pixels * frames <= MAX_TOTAL_PIXELS


def image_shape(data: bytes, mime: str) -> tuple[int, int, int]:
    size = image_size(data)
    if mime == "image/jpeg":
        if not data.startswith(b"\xff\xd8\xff") or size is None:
            raise ValueError
        return *size, 1
    if mime == "image/png":
        frames, controls, metadata, first, seen_idat, separate_default = 1, 0, 0, None, False, False
        for kind, payload, _start, _end in _png_chunks(data):
            first, seen_idat = first or kind, seen_idat or kind == b"IDAT"
            if kind == b"acTL":
                if len(payload) != 8:
                    raise ValueError
                frames = int.from_bytes(payload[:4], "big")
            elif kind == b"fcTL":
                if len(payload) != 26 or size is None:
                    raise ValueError
                if controls == 0 and seen_idat:
                    separate_default = True
                controls += 1
                width, height, left, top = struct.unpack_from(">IIII", payload, 4)
                if not width or not height or left + width > size[0] or top + height > size[1]:
                    raise ValueError
            elif kind in {b"iCCP", b"zTXt", b"iTXt"}:
                metadata += _png_metadata_size(kind, bytes(payload), MAX_METADATA_BYTES - metadata)
                if metadata > MAX_METADATA_BYTES:
                    raise ValueError
            if kind == b"IEND":
                break
        if size is None or first != b"IHDR" or not seen_idat or (frames > 1 or controls) and controls != frames:
            raise ValueError
        return *size, frames + int(separate_default)
    if mime == "image/gif":
        return _gif_shape(data)
    if mime == "image/webp":
        return _webp_shape(data, size)
    raise ValueError


def _gif_shape(data: bytes) -> tuple[int, int, int]:
    if len(data) < 14 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValueError
    width, height = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    at = 13 + (3 * (2 ** ((data[10] & 0x07) + 1)) if data[10] & 0x80 else 0)
    frames = blocks = 0

    def skip_subblocks(index: int) -> int:
        nonlocal blocks
        while index < len(data):
            blocks += 1
            if blocks > MAX_BLOCKS:
                raise ValueError
            length = data[index]
            index += 1
            if length == 0:
                return index
            index += length
            if index > len(data):
                raise ValueError
        raise ValueError

    while at < len(data):
        blocks += 1
        if blocks > MAX_BLOCKS:
            raise ValueError
        marker = data[at]
        at += 1
        if marker == 0x3B:
            if frames == 0:
                raise ValueError
            return width, height, frames
        if marker == 0x21:
            if at >= len(data):
                raise ValueError
            at = skip_subblocks(at + 1)
            continue
        if marker != 0x2C or at + 9 > len(data):
            raise ValueError
        left = int.from_bytes(data[at:at + 2], "little")
        top = int.from_bytes(data[at + 2:at + 4], "little")
        frame_width = int.from_bytes(data[at + 4:at + 6], "little")
        frame_height = int.from_bytes(data[at + 6:at + 8], "little")
        if not frame_width or not frame_height or left + frame_width > width or top + frame_height > height:
            raise ValueError
        packed = data[at + 8]
        at += 9 + (3 * (2 ** ((packed & 0x07) + 1)) if packed & 0x80 else 0)
        if at >= len(data):
            raise ValueError
        at = skip_subblocks(at + 1)
        frames += 1
    raise ValueError


def _webp_shape(data: bytes, size: tuple[int, int] | None) -> tuple[int, int, int]:
    if size is None or len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError
    end = int.from_bytes(data[4:8], "little") + 8
    if end != len(data):
        raise ValueError
    at, frames, animated, saw_animation = 12, 0, False, False
    for _count in range(MAX_BLOCKS):
        if at == end:
            if animated and (not saw_animation or frames == 0):
                raise ValueError
            return *size, frames if animated else 1
        if at + 8 > end:
            raise ValueError
        kind = data[at:at + 4]
        length = int.from_bytes(data[at + 4:at + 8], "little")
        chunk_end = at + 8 + length
        if chunk_end > end:
            raise ValueError
        if kind == b"VP8X":
            if length != 10:
                raise ValueError
            animated = bool(data[at + 8] & 0x02)
        elif kind == b"ANIM":
            saw_animation = True
        elif kind == b"ANMF":
            if length < 16:
                raise ValueError
            left = 2 * int.from_bytes(data[at + 8:at + 11], "little")
            top = 2 * int.from_bytes(data[at + 11:at + 14], "little")
            width = 1 + int.from_bytes(data[at + 14:at + 17], "little")
            height = 1 + int.from_bytes(data[at + 17:at + 20], "little")
            if not width or not height or left + width > size[0] or top + height > size[1]:
                raise ValueError
            encoded = _webp_frame_size(data, at + 24, chunk_end)
            if encoded is None or encoded[0] > width or encoded[1] > height:
                raise ValueError
            frames += 1
        at = chunk_end + (length & 1)
    raise ValueError


def _png_metadata_size(kind: bytes, payload: bytes, remaining: int) -> int:
    try:
        if kind in {b"iCCP", b"zTXt"}:
            _name, compressed = payload.split(b"\0", 1)
            if not compressed or compressed[0] != 0:
                raise ValueError
            compressed = compressed[1:]
        else:
            _keyword, rest = payload.split(b"\0", 1)
            if len(rest) < 2:
                raise ValueError
            compressed_flag, method, rest = rest[0], rest[1], rest[2:]
            _language, rest = rest.split(b"\0", 1)
            _translated, text = rest.split(b"\0", 1)
            if compressed_flag == 0:
                return len(text)
            if compressed_flag != 1 or method != 0:
                raise ValueError
            compressed = text
        decoder = zlib.decompressobj()
        expanded = decoder.decompress(compressed, max(0, remaining) + 1)
        if len(expanded) > remaining or not decoder.eof or decoder.unused_data:
            raise ValueError
        return len(expanded)
    except (ValueError, zlib.error):
        raise ValueError from None


def _webp_frame_size(data: bytes, at: int, end: int) -> tuple[int, int] | None:
    for _count in range(16):
        if at + 8 > end:
            return None
        kind = data[at:at + 4]
        length = int.from_bytes(data[at + 4:at + 8], "little")
        chunk_end = at + 8 + length
        if chunk_end > end:
            return None
        if kind in {b"VP8 ", b"VP8L"}:
            chunk = data[at:chunk_end + (length & 1)]
            static = b"RIFF" + (len(chunk) + 4).to_bytes(4, "little") + b"WEBP" + chunk
            return image_size(static)
        at = chunk_end + (length & 1)
    return None
