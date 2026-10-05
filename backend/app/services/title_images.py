"""Uploaded title artwork: sniff, bound, re-encode, store, collect. No HTTP here.

Every upload is judged by its magic bytes (never the client's Content-Type), bounded in size and pixels from its
header before anything decodes it, re-encoded by ffmpeg from stdin to stdout with its metadata dropped, judged again,
and stored in SQLite (``title_uploads``) so backups carry it. Logs and errors carry no bytes, paths or ffmpeg stderr.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import resource
import struct
import subprocess
import threading
import uuid
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models import MediaTitle, PersonOverride, TitleEdit, TitleUpload, utcnow
from app.services import metadata_editor, renditions
from app.services.titles import EXTRA_BACKDROPS, image_key, title_image_source

MAX_UPLOAD_BYTES = 15 * 1024 * 1024        # the scoped body cap
MAX_OUTPUT_BYTES = renditions.MAX_OUTPUT_BYTES  # 4 MiB: also the TitleUpload.data bound
MIN_SIDE, MAX_SIDE, MAX_PIXELS = 64, 8000, 40_000_000
FIT_SIDE = 3840
ENCODE_TIMEOUT_SECONDS = 20.0
# RLIMIT_AS on the ffmpeg child (Linux). Address space, not RSS: jellyfin-ffmpeg maps ~450 MiB of libraries, and the
# worst legitimate input (40 MP 16-bit RGBA PNG, ~400 MB RSS) needed 0.9-1 GiB measured; 1.25 GiB leaves ~800 MiB of heap.
ENCODER_ADDRESS_SPACE = 1280 * 1024 * 1024
BUSY_WAIT_SECONDS = 5.0
MAX_STORED_BYTES = 2 * 1024**3        # all uploads together; past it an upload is 507 storage_full
GC_MIN_AGE = timedelta(hours=1)        # an upload is written to its title right after it is stored

OUTPUT = {"image/jpeg": ["-c:v", "mjpeg", "-q:v", "3"], "image/png": ["-c:v", "png"], "image/webp": ["-c:v", "libwebp", "-quality", "88"]}

# One encode at a time per process; a second waits BUSY_WAIT_SECONDS then gets 503. Raise if editors queue.
_ENCODER = threading.BoundedSemaphore(1)
_PRLIMIT_WARNED = False
log = logging.getLogger(__name__)


class UploadError(Exception):
    """A refused upload: 409 conflict/duplicate | 413 too_large | 415 unsupported_image/animated_image | 422 image_dimensions |
    507 storage_full | 503 encoder_busy/encoder_unavailable."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Encoded:
    sha256: str
    content_type: str
    width: int
    height: int
    data: bytes


def sniff(data: bytes) -> str | None:
    """The type from the first 32 bytes; the client's Content-Type is never consulted. SVG, GIF, AVIF, HEIC, TIFF, BMP, ICO, PDF, HTML: None."""
    head = data[:32]
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def check_dimensions(data: bytes) -> tuple[int, int]:
    size = renditions.image_size(data)  # reads the header and never decodes
    if size is None or not all(size):
        raise UploadError(415, "unsupported_image")
    width, height = size
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE) or width * height > MAX_PIXELS:
        raise UploadError(422, "image_dimensions")
    return width, height


MAX_PNG_CHUNKS = 65_536  # libpng writes ~2k IDATs for a 15 MiB PNG
_PNG_KEEP = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"})


def strip_png(data: bytes) -> bytes:
    """The PNG rebuilt from IHDR, PLTE, tRNS, IDAT and IEND only, or 415 when the chunk walk is malformed.

    ffmpeg inflates iCCP/zTXt in full before ``-map_metadata`` applies (540 KiB -> 1.8 GB), so no ancillary
    chunk reaches it. Every chunk's CRC is checked; bytes after IEND are dropped.
    """
    view, at, count, first, seen_idat, last = memoryview(data), 8, 0, None, False, None
    out = bytearray(data[:8])  # one buffer, no object per chunk
    while last != b"IEND":
        count += 1
        if count > MAX_PNG_CHUNKS or at + 12 > len(data):
            raise UploadError(415, "unsupported_image")
        length, kind = struct.unpack_from(">I4s", data, at)
        end = at + 12 + length
        if length > 0x7FFFFFFF or end > len(data) or zlib.crc32(view[at + 4:end - 4]) != int.from_bytes(view[end - 4:end], "big"):
            raise UploadError(415, "unsupported_image")
        if kind in _PNG_KEEP:
            out += view[at:end]
        first, seen_idat, last, at = first or kind, seen_idat or kind == b"IDAT", kind, end
    if first != b"IHDR" or not seen_idat:
        raise UploadError(415, "unsupported_image")
    return bytes(out)


def _argv(ffmpeg: str, content_type: str, out_type: str) -> list[str]:
    # No shell, no request path, filename or URL: the demuxer is forced from the sniffed type, the pipe-only
    # protocol whitelist stops any file:/http: reference inside the data, -max_pixels stops a header lie.
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-filter_threads", "1",
        "-protocol_whitelist", "pipe", "-f", renditions.DEMUXERS[content_type], "-max_pixels", str(MAX_PIXELS), "-threads", "1", "-i", "pipe:0",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-vf", f"scale='min({FIT_SIDE},iw)':'min({FIT_SIDE},ih)':force_original_aspect_ratio=decrease",
        "-frames:v", "1", "-an", "-sn", "-dn", "-threads", "1", *OUTPUT[out_type], "-f", "image2pipe", "pipe:1",
    ]


def _feed(stream, data: bytes) -> None:  # noqa: ANN001
    with contextlib.suppress(OSError):  # the encoder may exit (or be killed) before it reads everything
        stream.write(data)
    with contextlib.suppress(OSError):
        stream.close()


def _run(argv: list[str], data: bytes, timeout: float) -> bytes:
    """ffmpeg's stdout, at most MAX_OUTPUT_BYTES; every failure is the same constant 415 (stderr is discarded)."""
    failed = UploadError(415, "unsupported_image")
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        raise failed from None
    timer = threading.Timer(timeout, proc.kill)
    try:
        with contextlib.suppress(OSError):  # it may already have exited
            os.setpriority(os.PRIO_PROCESS, proc.pid, 19)
        # Backstop for an allocation bomb the chunk filter misses; set before a byte of input is fed. Not preexec_fn:
        # that is unsafe with the server's threads. macOS has no prlimit (and does not enforce RLIMIT_AS).
        if hasattr(resource, "prlimit"):
            try:
                resource.prlimit(proc.pid, resource.RLIMIT_AS, (ENCODER_ADDRESS_SPACE, ENCODER_ADDRESS_SPACE))
            except ProcessLookupError:
                pass
            except OSError as error:  # denied (seccomp, hard limit): the PNG strip is the primary defence, so go on
                global _PRLIMIT_WARNED  # noqa: PLW0603
                if not _PRLIMIT_WARNED:
                    _PRLIMIT_WARNED = True
                    log.warning("encoder address-space limit not applied (%s)", type(error).__name__)
        timer.start()
        # stdin is fed from another thread so a large input cannot deadlock against a full stdout pipe
        threading.Thread(target=_feed, args=(proc.stdin, data), daemon=True).start()
        output = proc.stdout.read(MAX_OUTPUT_BYTES + 1)
        if len(output) > MAX_OUTPUT_BYTES:
            raise failed
        returncode = proc.wait()
        if returncode != 0 or not output or not timer.is_alive():
            raise failed
        return output
    finally:
        timer.cancel()
        with contextlib.suppress(OSError):
            proc.kill()
        proc.wait()
        with contextlib.suppress(OSError):
            proc.stdout.close()


def encode(data: bytes, image_type: str, *, ffmpeg: str | None) -> Encoded:
    """Sniff -> dimensions -> one encoder slot -> ffmpeg -> re-sniff -> re-check. Pure: no DB, no filesystem. Raises UploadError."""
    content_type = sniff(data)
    if content_type is None:
        raise UploadError(415, "unsupported_image")
    # VP8X flag bit 1 = animation. ffmpeg's WebP decoder skips ANIM/ANMF chunks and finds no image, and there is no
    # Pillow to take the first frame, so say why instead of the generic 415 (#162).
    if content_type == "image/webp" and data[12:16] == b"VP8X" and len(data) > 20 and data[20] & 0x02:
        raise UploadError(415, "animated_image")
    check_dimensions(data)
    if ffmpeg is None:
        raise UploadError(503, "encoder_unavailable")
    # Primary and Backdrop are JPEG. A transparent PNG poster loses its alpha to JPEG; no flatten step.
    out_type = "image/jpeg" if image_type != "Logo" else "image/webp" if renditions.has_libwebp(ffmpeg) else "image/png"
    if not _ENCODER.acquire(timeout=BUSY_WAIT_SECONDS):
        raise UploadError(503, "encoder_busy")
    try:
        if content_type == "image/png":
            data = strip_png(data)  # inside the slot: one parse at a time
        output = _run(_argv(ffmpeg, content_type, out_type), data, ENCODE_TIMEOUT_SECONDS)
    finally:
        _ENCODER.release()
    if sniff(output) != out_type:
        raise UploadError(415, "unsupported_image")
    width, height = check_dimensions(output)
    return Encoded(hashlib.sha256(output).hexdigest(), out_type, width, height, output)


def save(db: Session, encoded: Encoded, user_id: str | None) -> None:
    """Insert-if-absent into title_uploads (idempotent by sha256; the first uploader is kept). The caller commits.

    507 storage_full when the stored uploads plus this one would pass MAX_STORED_BYTES (bytes already stored count once).
    """
    others = select(func.coalesce(func.sum(func.length(TitleUpload.data)), 0)).where(TitleUpload.sha256 != encoded.sha256)
    if db.scalar(others) + len(encoded.data) > MAX_STORED_BYTES:
        raise UploadError(507, "storage_full")
    db.execute(insert(TitleUpload).values(
        sha256=encoded.sha256, content_type=encoded.content_type, width=encoded.width, height=encoded.height,
        data=encoded.data, created_by=user_id, created_at=utcnow(),
    ).on_conflict_do_nothing(index_elements=["sha256"]))


def _uploads(images) -> set[str]:  # noqa: ANN001
    if not isinstance(images, dict):
        return set()
    return {entry["upload"] for entry in images.values() if isinstance(entry, dict) and isinstance(entry.get("upload"), str)}


def gc_uploads(db: Session, now: datetime) -> int:
    """Delete uploads older than an hour that no title and no surviving title_edits row references (undo can restore any)."""
    # Reads every title's images JSON once an hour (~2,500 rows); index the references past 50k titles.
    referenced: set[str] = set()
    for images in db.scalars(select(MediaTitle.images)):
        referenced |= _uploads(images)
    # History lives 365 days (metadata_history.KEEP_DAYS) and undo restores any row, so every row counts.
    edits = select(TitleEdit.before, TitleEdit.after).where(TitleEdit.field.like("images.%"))
    for before, after in db.execute(edits):
        for entry in (before, after):
            if isinstance(entry, dict) and isinstance(entry.get("upload"), str):
                referenced.add(entry["upload"])
    referenced |= set(db.scalars(select(PersonOverride.photo).where(PersonOverride.photo.is_not(None))))  # #164 people photos
    for before, after in db.execute(select(TitleEdit.before, TitleEdit.after).where(TitleEdit.field == "person.photo")):
        referenced |= {sha for sha in (before, after) if isinstance(sha, str)}
    result = db.execute(delete(TitleUpload).where(TitleUpload.sha256.not_in(referenced), TitleUpload.created_at < now - GC_MIN_AGE))
    return result.rowcount or 0


# ---- slots and writes: every write goes through metadata_editor.write_user_field, one batch per request ----

ALLOWED_TYPES: dict[str, frozenset[str]] = {  # image type -> title types that may have it
    "Primary": frozenset({"movie", "series", "season", "episode"}),
    "Backdrop": frozenset({"movie", "series", "season"}),
    "Logo": frozenset({"movie", "series"}),
}


def _backdrops(title: MediaTitle) -> list[int]:
    """The occupied backdrop indices (an entry that is present and not a tombstone), ascending."""
    return [n for n in range(EXTRA_BACKDROPS + 1) if title_image_source(title, image_key("Backdrop", n))]


def check_slot(title: MediaTitle, image_type: str, index: int, *, adding: bool = False) -> str:
    """The images-map key for a route's type and index, or UploadError 422 (type_not_allowed | invalid_index | index_gap)."""
    if title.type not in ALLOWED_TYPES.get(image_type, ()):
        raise UploadError(422, "type_not_allowed")
    if index != 0 and image_type != "Backdrop" or not 0 <= index <= EXTRA_BACKDROPS:
        raise UploadError(422, "invalid_index")
    if adding and image_type == "Backdrop" and index > len(_backdrops(title)):
        raise UploadError(422, "index_gap")
    return image_key(image_type, index)


def check_base(title: MediaTitle, key: str, base_tag: str | None) -> None:
    """409 conflict unless the caller saw this slot's current tag (None or "" = it saw an empty slot)."""
    if (base_tag or None) != title_image_source(title, key):
        raise UploadError(409, "conflict")


def check_write(title: MediaTitle, image_type: str, index: int, base_tag: str | None, tag: str) -> str:
    """Run inside the write transaction against the reloaded title; returns the images-map key.

    The slot check again (a delete that landed after the route's pre-check can leave ``index`` past the end),
    the caller's base tag, and 409 duplicate when another backdrop slot holds ``tag`` (a repeated tag locks reorder out).
    """
    key = check_slot(title, image_type, index, adding=True)
    check_base(title, key, base_tag)
    if image_type == "Backdrop" and any(title_image_source(title, image_key("Backdrop", n)) == tag for n in _backdrops(title) if n != index):
        raise UploadError(409, "duplicate")
    return key


def write_image(title: MediaTitle, key: str, entry: dict, batch: metadata_editor.Batch) -> bool:
    return metadata_editor.write_user_field(title, f"images.{key}", entry, batch)


def _place(title: MediaTitle, n: int, entry: dict, batch: metadata_editor.Batch) -> None:
    """Write a moved entry; a slot that keeps its entry is not touched (no needless lock or history row)."""
    key = image_key("Backdrop", n)
    if entry != (title.images or {}).get(key):
        write_image(title, key, entry, batch)


def tombstone() -> dict:
    return {"removed": True, "tag": f"removed:{uuid.uuid4()}"}


def delete_image(title: MediaTitle, key: str, batch: metadata_editor.Batch) -> None:
    """Remove a slot; a removed Backdrop shifts the later ones down and the last occupied slot becomes the tombstone."""
    if title_image_source(title, key) is None:
        raise UploadError(404, "empty_slot")
    if key.partition(".")[0] != "Backdrop":
        write_image(title, key, tombstone(), batch)
        return
    slots = _backdrops(title)
    keep = [dict(title.images[image_key("Backdrop", n)]) for n in slots if image_key("Backdrop", n) != key]
    for n, entry in zip(slots, [*keep, tombstone()], strict=True):  # the last occupied slot becomes the tombstone
        _place(title, n, entry, batch)


def reorder_backdrops(title: MediaTitle, tags: list[str], batch: metadata_editor.Batch) -> None:
    """tags = the occupied backdrops' current tags in the new order: 422 invalid_order for a repeat, 409 when the set differs."""
    slots = _backdrops(title)
    current = [title_image_source(title, image_key("Backdrop", n)) for n in slots]
    if len(set(tags)) != len(tags):
        raise UploadError(422, "invalid_order")
    if Counter(tags) != Counter(current):
        raise UploadError(409, "conflict")
    by_tag = {tag: dict(title.images[image_key("Backdrop", n)]) for tag, n in zip(current, slots, strict=True)}
    for n, tag in zip(slots, tags, strict=True):
        _place(title, n, by_tag[tag], batch)
