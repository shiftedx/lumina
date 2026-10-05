"""Artwork renditions: small, fast copies of a title's own art, with previews and colours.

One ffmpeg run per source image makes every product: the bytes arrive on stdin, each output goes to a
Lumina-named file in a private staging directory, and ``install`` moves the renditions into
``artwork-cache/renditions/``. Everything here is derived, disposable data keyed by ``art_urls.source_key``.
"""
from __future__ import annotations

import colorsys
import contextlib
import functools
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.media_schemas import ArtServing, ArtworkProgress
from app.models import LibraryItem, MediaTitle, NfoPerson, PlaybackProgress, StorageRoot, TitleArtwork, utcnow
from app.persistence import read_regular_file, write_transaction
from app.services import art_urls, cast_photos, library_import
from app.services.artwork import ArtworkService, ArtworkUnavailableError
from app.services.local_playback_sessions import sessions
from app.services.media_probe import media_tool
from app.services.storage_roots import ONLINE_STATES, StorageRootService
from app.services.titles import title_image_bytes, title_image_source

logger = logging.getLogger(__name__)

MAX_PIXELS = 80_000_000
MAX_OUTPUT_BYTES = 4 * 1024 * 1024  # per product: a bigger output fails the job
PASS_TIMEOUT_SECONDS = 20.0
PREVIEW_WIDTH = 32
PREVIEW_MAX_BYTES = 600
QUALITY = {"webp": 82, "jpg": 3}  # libwebp -quality, mjpeg -q:v
PREVIEW_QUALITIES = {"webp": (30, 15), "jpg": (12,)}  # tried in order until one fits PREVIEW_MAX_BYTES
DEMUXERS = {"image/jpeg": "jpeg_pipe", "image/png": "png_pipe", "image/webp": "webp_pipe"}
CONTENT_TYPES = {"webp": "image/webp", "jpg": "image/jpeg"}
RENDITION_NAME = re.compile(r"^([0-9a-f]{64})-(\d{3,4})\.(webp|jpg)$")


class RenditionError(Exception):
    """A stable, content-free reason, stored in ``title_artwork.error`` and shown to admins."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Rendered:
    ext: str  # "webp" | "jpg"
    files: dict[int, Path]  # rendition width -> staged file (inside `staging`)
    width: int  # source pixels (square types: the centre-cropped side)
    height: int
    preview: bytes | None
    preview_type: str | None  # "image/webp" | "image/jpeg"
    dominant: str | None  # "#rrggbb" (None for Logo and episode stills)
    accent: str | None
    staging: Path  # renditions/.staging/<hex>/; removed by install() or discard()


def renditions_root() -> Path:
    return settings.data_dir / "artwork-cache" / "renditions"


def staging_root() -> Path:
    return renditions_root() / ".staging"


STAGING_MAX_AGE_SECONDS = 3600


def sweep_staging(max_age: float = STAGING_MAX_AGE_SECONDS) -> None:
    """Remove staging directories a crash left behind; younger ones may belong to a render in flight."""
    cutoff = time.time() - max_age
    with contextlib.suppress(FileNotFoundError):
        for entry in staging_root().iterdir():
            with contextlib.suppress(OSError):
                if entry.lstat().st_mtime < cutoff:
                    shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()


def _private_dir(path: Path) -> Path:
    """Create ``path`` as 0700 if needed; refuse a symlink or a non-directory in its place."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError("Rendition storage is not a private directory.")
    return path


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG, WebP or JPEG header, read without decoding; None when it is none of them."""
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
            if marker == 0xFF:  # fill byte
                index += 1
            elif marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers without a length
                index += 2
            elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # SOFn: precision, height, width
                return int.from_bytes(data[index + 7:index + 9], "big"), int.from_bytes(data[index + 5:index + 7], "big")
            else:
                index += 2 + int.from_bytes(data[index + 2:index + 4], "big")
    return None


def colours(raw: bytes) -> tuple[str, str]:
    """(dominant, accent) of an 8 x 8 rgb24 frame: the mean pixel, and the most vivid pixel
    (highest saturation x value with s >= .25 and .25 <= v <= .95), which falls back to the mean."""
    pixels = [tuple(raw[index:index + 3]) for index in range(0, 192, 3)]
    mean = tuple(round(sum(pixel[channel] for pixel in pixels) / len(pixels)) for channel in range(3))
    accent, best = mean, -1.0
    for pixel in pixels:
        _, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in pixel))
        if saturation >= 0.25 and 0.25 <= value <= 0.95 and saturation * value > best:
            accent, best = pixel, saturation * value
    return "#%02x%02x%02x" % mean, "#%02x%02x%02x" % accent


@functools.cache  # one probe per ffmpeg path per process
def has_libwebp(ffmpeg: str) -> bool:
    """Whether this ffmpeg encodes WebP; without it every rendition and preview is JPEG."""
    try:
        listing = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, timeout=10, check=False, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True  # cannot tell: keep the default; a broken ffmpeg then fails its jobs visibly
    return re.search(rb"\slibwebp\s", listing) is not None


def use_host_format(ffmpeg: str | None = None) -> str:
    """Switch renditions to JPEG when this host's ffmpeg has no WebP encoder; returns the format in use.

    Scripts outside the app (budget tests, dry runs) pass ``ffmpeg``; otherwise the configured binary is used.
    """
    if ffmpeg is None:
        with SessionLocal() as db:
            ffmpeg = media_tool(db, "ffmpeg")
    if ffmpeg is not None and not has_libwebp(ffmpeg):
        art_urls.set_extension("jpg")
    return art_urls.extension()


def _encode(ext: str, quality: int) -> list[str]:
    if ext == "webp":
        return ["-c:v", "libwebp", "-quality", str(quality), "-compression_level", "4", "-f", "image2", "-update", "1"]
    return ["-c:v", "mjpeg", "-q:v", str(quality), "-f", "image2", "-update", "1"]


def ffmpeg_command(ffmpeg: str, content_type: str, staging: Path, *, widths: Sequence[int], ext: str, preview: bool, colours: bool,
                   square: bool = False, portrait: bool = False) -> tuple[list[str], dict[str, Path]]:
    """argv for one run that splits the decoded image into every product, each written to a Lumina-named file.

    ``square`` centre-crops every product and scales both sides,
    so each is exactly 1:1 even for an odd-sized RGB source (``-2`` would round one side up).
    ``portrait`` (a person's photo) centre-crops to 2:3 and scales to exactly width × 1.5 width.
    """
    crop = "crop='min(iw,ih)':'min(iw,ih)'," if square else "crop='min(iw,ih*2/3)':'min(ih,iw*3/2)'," if portrait else ""

    def fit(width: int) -> str:
        if square:
            return f"scale='min(iw,{width})':'min(iw,{width})'"
        if portrait:  # exact, so a small photo is scaled up: every person tile in a row is the same size
            return f"scale={width}:{width * 3 // 2}"
        return f"scale='min(iw,{width})':-2"

    products = [(f"w{width}", f"{crop}{fit(width)}:flags=lanczos", _encode(ext, QUALITY[ext]), ext) for width in widths]
    if preview:
        products += [
            (f"preview{quality}", f"{crop}scale={PREVIEW_WIDTH}:{PREVIEW_WIDTH if square else -2}", _encode(ext, quality), ext)
            for quality in PREVIEW_QUALITIES[ext]
        ]
    if colours:
        products.append(("colours", f"{crop}scale=8:8:flags=area,format=rgb24", ["-f", "rawvideo"], "rgb"))
    paths = {label: staging / f"{label}.{suffix}" for label, _, _, suffix in products}
    graph = f"[0:v]split={len(products)}{''.join(f'[s{index}]' for index in range(len(products)))};" + ";".join(
        f"[s{index}]{chain}[o{index}]" for index, (_, chain, _, _) in enumerate(products)
    )
    argv = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-filter_threads", "1",
        "-protocol_whitelist", "pipe", "-f", DEMUXERS[content_type], "-max_pixels", str(MAX_PIXELS), "-threads", "1", "-i", "pipe:0",
        "-filter_complex", graph,
    ]
    for index, (label, _, encode, _) in enumerate(products):
        argv += ["-map", f"[o{index}]", "-frames:v", "1", "-an", "-sn", "-threads", "1", *encode, str(paths[label])]
    return argv, paths


def _resolve_ffmpeg() -> str:
    with SessionLocal() as db:
        tool = media_tool(db, "ffmpeg")
    if tool is None:
        raise RenditionError("ffmpeg_failed")
    return tool


def render(data: bytes, content_type: str, *, title_type: str, image_type: str, timeout: float, ext: str | None = None, ffmpeg: str | None = None,
           on_process: Callable[[subprocess.Popen | None], None] | None = None) -> Rendered:
    """Every product of one source image from one ffmpeg run. Raises RenditionError.

    ``ext`` defaults to the host's rendition format and ``ffmpeg`` to the configured binary. The process reads the
    bytes on stdin, runs at nice 19 with one thread, and is killed after ``timeout`` seconds.
    """
    ext = ext or art_urls.extension()
    widths = art_urls.widths(title_type, image_type)
    if content_type not in DEMUXERS or not widths or (image_type == "Logo" and ext == "jpg"):
        raise RenditionError("unsupported_format")  # a logo keeps its alpha only as WebP; the original serves instead
    size = image_size(data)
    if size is None or not all(size):
        raise RenditionError("unreadable")
    if size[0] * size[1] > MAX_PIXELS:
        raise RenditionError("unsupported_format")
    tool = ffmpeg or _resolve_ffmpeg()
    wants_preview = image_type != "Logo" and title_type != "person"
    wants_colours = image_type == "Backdrop" or (image_type == "Primary" and title_type not in ("episode", "person"))
    square = art_urls.square(title_type, image_type)
    portrait = art_urls.portrait(title_type, image_type)
    _private_dir(renditions_root())
    staging = _private_dir(staging_root()) / secrets.token_hex(8)
    staging.mkdir(mode=0o700)
    try:
        argv, paths = ffmpeg_command(tool, content_type, staging, widths=widths, ext=ext, preview=wants_preview, colours=wants_colours, square=square,
                                     portrait=portrait)
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise RenditionError("ffmpeg_failed") from None
        if on_process is not None:
            on_process(process)  # the pass keeps it so stop() can kill it
        with contextlib.suppress(OSError):  # it may already have exited
            os.setpriority(os.PRIO_PROCESS, process.pid, 19)
        try:
            process.communicate(input=data, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RenditionError("timeout") from None
        if process.returncode != 0 or not all(path.is_file() for path in paths.values()):
            raise RenditionError("ffmpeg_failed")
        if any(path.stat().st_size > MAX_OUTPUT_BYTES for path in paths.values()):
            raise RenditionError("oversize_output")
        preview = None
        for quality in PREVIEW_QUALITIES[ext] if wants_preview else ():
            candidate = paths[f"preview{quality}"].read_bytes()
            if len(candidate) <= PREVIEW_MAX_BYTES:
                preview = candidate
                break
        dominant = accent = None
        if wants_colours:
            raw = paths["colours"].read_bytes()
            if len(raw) != 192:
                raise RenditionError("ffmpeg_failed")
            dominant, accent = colours(raw)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    side = min(size)  # a square or portrait rendition's frame is the centre crop
    frame_width = min(size[0], size[1] * 2 // 3) if portrait else None
    frame = (side, side) if square else (frame_width, frame_width * 3 // 2) if portrait else size
    return Rendered(
        ext=ext, files={width: paths[f"w{width}"] for width in widths}, width=frame[0],
        height=frame[1], preview=preview, preview_type=CONTENT_TYPES[ext] if preview is not None else None,
        dominant=dominant, accent=accent, staging=staging,
    )


def install(key: str, rendered: Rendered) -> None:
    """Move each rendition to ``art_urls.rendition_file`` (0700 shard directories), then drop the staging directory."""
    try:
        for width, staged in rendered.files.items():
            target = art_urls.rendition_file(key, width, rendered.ext)
            _private_dir(target.parent)
            os.utime(staged)  # stamped at install, so a sweep running now sees it as fresh
            os.replace(staged, target)
    finally:
        discard(rendered)


def discard(rendered: Rendered) -> None:
    shutil.rmtree(rendered.staging, ignore_errors=True)


def ready_row(title_id: str, image_type: str, key: str, rendered: Rendered) -> TitleArtwork:
    return TitleArtwork(
        title_id=title_id, image_type=image_type, source_key=key, state="ready", width=rendered.width, height=rendered.height,
        preview=rendered.preview, preview_type=rendered.preview_type, dominant=rendered.dominant, accent=rendered.accent, error=None,
    )


def failed_row(title_id: str, image_type: str, key: str, reason: str) -> TitleArtwork:
    """``unsupported`` for a format or size renditions cannot handle, else ``failed``; neither retries until the art changes."""
    state = "unsupported" if reason == "unsupported_format" else "failed"
    return TitleArtwork(title_id=title_id, image_type=image_type, source_key=key, state=state, error=reason)


# ---- Cache files and serving counters -------------------------------------------------------

CACHE_CAP_BYTES = 5 * 1024**3
MTIME_BUMP_SECONDS = 24 * 60 * 60  # a read refreshes the LRU clock at most once a day
_serving: Counter[str] = Counter()
_serving_lock = threading.Lock()


def count(event: str) -> None:
    """One /api/art or Jellyfin rendition outcome; ``event`` is an ArtServing field name."""
    with _serving_lock:
        _serving[event] += 1


def serving() -> ArtServing:
    with _serving_lock:
        return ArtServing(**_serving)


def rendition_files() -> Iterator[tuple[Path, str, int, float]]:
    """(path, source key, bytes, mtime) of every rendition file; staging and stray names are skipped."""
    root = renditions_root()
    if not root.is_dir():
        return
    for shard in os.scandir(root):
        if shard.name.startswith(".") or not shard.is_dir(follow_symlinks=False):
            continue
        for entry in os.scandir(shard.path):
            match = RENDITION_NAME.match(entry.name)
            if match and entry.is_file(follow_symlinks=False):
                info = entry.stat(follow_symlinks=False)
                yield Path(entry.path), match[1], info.st_size, info.st_mtime


def cache_bytes() -> int:
    return sum(size for _, _, size, _ in rendition_files())


def touch(path: Path, mtime: float) -> float:
    """Bump ``path``'s mtime when it is over a day old; returns the mtime now on record."""
    now = time.time()
    if now - mtime <= MTIME_BUMP_SECONDS:
        return mtime
    with contextlib.suppress(OSError):
        os.utime(path)
        return now
    return mtime


def read_rendition(path: Path) -> tuple[bytes, float] | None:
    """A rendition's bytes and (bumped) mtime, never through a symlink; None when it is not on disk."""
    try:
        data = read_regular_file(path, MAX_OUTPUT_BYTES)
        return data, touch(path, path.stat().st_mtime)
    except OSError:
        return None


# ---- Background pass ----------------------------------------------------------------------------

ITEM_GAP_SECONDS = 0.05
PAUSE_POLL_SECONDS = 5.0
IDLE_SECONDS = 600
ERROR_BACKOFF_SECONDS = 30  # after an unexpected error (e.g. SQLite busy), instead of the idle wait
SCAN_BATCH = 500
TYPE_RANK = {"Primary": 0, "Backdrop": 1, "Logo": 2}
PROGRESS_MAX_AGE_SECONDS = 30
SWEEP_INTERVAL_SECONDS = 24 * 60 * 60
ORPHAN_GRACE_SECONDS = 60


@dataclass
class Scan:
    """One read of every title's art against title_artwork: the pass's work order and the admin counters."""

    order: list[tuple[str, str]] = field(default_factory=list)  # (title_id, image_type) needing preparation
    total: int = 0  # (title, image type) pairs with a supported source
    prepared: int = 0  # ... whose current row is ready
    failed: int = 0
    unsupported: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)


def scan(db: Session, skipped: Iterable[str] = frozenset()) -> Scan:
    """Titles in id-keyset batches of 500; rows loaded once. ``skipped`` source keys stay out of the order only."""
    skipped = set(skipped)
    rows = {(title_id, image_type): (key, state, error) for title_id, image_type, key, state, error in db.execute(
        select(TitleArtwork.title_id, TitleArtwork.image_type, TitleArtwork.source_key, TitleArtwork.state, TitleArtwork.error)
    )}
    series_of = dict(db.execute(select(MediaTitle.id, MediaTitle.parent_id).where(MediaTitle.type == "season")).all())
    watched_seasons = db.scalars(
        select(MediaTitle.parent_id).join(LibraryItem, LibraryItem.title_id == MediaTitle.id)
        .join(PlaybackProgress, PlaybackProgress.item_id == LibraryItem.id)
        .where(MediaTitle.type == "episode").distinct()
    )
    busy_series = {series_of.get(season) for season in watched_seasons} - {None}
    found = Scan()
    # Movie and series art, album covers, stills of shows being watched, other stills, the rest; people's photos
    # last.
    buckets: list[list[tuple[tuple, tuple[str, str]]]] = [[], [], [], [], [], []]
    last = ""
    while batch := db.execute(
        select(MediaTitle.id, MediaTitle.type, MediaTitle.images, MediaTitle.created_at, MediaTitle.parent_id, MediaTitle.root_id)
        .where(MediaTitle.id > last).order_by(MediaTitle.id).limit(SCAN_BATCH)
    ).all():
        last = batch[-1].id
        for title_id, title_type, images, created_at, parent_id, root_id in batch:
            for image_type in art_urls.ART_TYPES:
                source = title_image_source(SimpleNamespace(images=images), image_type)
                if source is None or not art_urls.supported((images or {}).get(image_type)):
                    continue
                found.total += 1
                key = art_urls.source_key(title_id, image_type, source)
                row = rows.get((title_id, image_type))
                if row is not None and row[0] == key:
                    if row[1] == "ready":
                        found.prepared += 1
                    else:
                        found.failed += row[1] == "failed"
                        found.unsupported += row[1] == "unsupported"
                        found.failure_reasons[row[2] or "unknown"] = found.failure_reasons.get(row[2] or "unknown", 0) + 1
                    continue
                if root_id is None and art_urls.local(images[image_type]):
                    found.failure_reasons["no_storage_root"] = found.failure_reasons.get("no_storage_root", 0) + 1
                if key in skipped:
                    continue
                newest = -(created_at.timestamp() if created_at else 0.0)
                pair = (title_id, image_type)
                if title_type in ("movie", "series"):
                    buckets[0].append(((TYPE_RANK[image_type], newest), pair))
                elif title_type == "album" and image_type == "Primary":
                    buckets[1].append(((newest,), pair))
                elif title_type == "episode" and image_type == "Primary":
                    buckets[2 if series_of.get(parent_id) in busy_series else 3].append(((newest,), pair))
                else:
                    buckets[4].append(((newest,), pair))
    # People last, only local photos (the pass never fetches), only while the folder is mounted; not in the counters,
    # which report title art. A person no title credits any more keeps a row and is still prepared.
    last = ""
    online = cast_photos.people_dir_online()
    while online and (batch := db.execute(
        select(NfoPerson.id, NfoPerson.image_path).where(NfoPerson.id > last, NfoPerson.image_path.is_not(None))
        .order_by(NfoPerson.id).limit(SCAN_BATCH)
    ).all()):
        last = batch[-1].id
        for person_id, image_path in batch:
            key = art_urls.source_key(person_id, "Primary", image_path)
            row = rows.get((person_id, "Primary"))
            if (row is None or row[0] != key) and key not in skipped:
                buckets[5].append(((), (person_id, "Primary")))
    found.order = [pair for bucket in buckets for _, pair in sorted(bucket)]
    return found


class _Offline:
    """The pass never fetches: TMDB art is prepared only once its pinned copy is on disk."""

    def fetch(self, url: str, *, validate_redirect, headers=None):  # noqa: ANN001, ANN201, ARG002
        raise ArtworkUnavailableError("The artwork pass does not fetch.")


def offline_artwork() -> ArtworkService:
    """Reads local art and already-pinned TMDB copies (same directory as main.py's ArtworkService); never the network."""
    return ArtworkService(remote_fetcher=_Offline(), pinned_root=settings.data_dir / "metadata-art")


def _root_online(db: Session, root: StorageRoot | None) -> bool:
    """Live, read-only: the stored observation is only refreshed by a probe, so an unplugged drive still reads available."""
    state = (root.observation or {}).get("state") if root is not None else None
    return root is not None and (state is None or state in ONLINE_STATES) and StorageRootService(db).is_online(root)


def save(row: TitleArtwork) -> None:
    with SessionLocal() as db:
        with write_transaction(db, name="renditions"):
            db.merge(row)


def _subject(db: Session, subject_id: str) -> MediaTitle | SimpleNamespace | None:
    """A title, else an NFO person as cast_photos.subject presents one."""
    return db.get(MediaTitle, subject_id) or cast_photos.subject(db, subject_id)


def _source_online(db: Session, subject: MediaTitle | SimpleNamespace) -> bool:
    if subject.type == "person":
        return "upload" in (subject.images.get("Primary") or {}) or cast_photos.people_dir_online()
    return _root_online(db, db.get(StorageRoot, subject.root_id) if subject.root_id else None)


def _image_bytes(db: Session, subject: MediaTitle | SimpleNamespace, image_type: str, artwork: ArtworkService) -> tuple[str, bytes]:
    if subject.type == "person":
        return cast_photos.image_bytes(subject, artwork, db)
    return title_image_bytes(db, subject, image_type, artwork)


class ArtworkRenditions:
    """The background pass: one daemon thread, one ffmpeg at a time, paused while any video transcode runs."""

    def __init__(self) -> None:
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._urgent: deque[tuple[str, str]] = deque(maxlen=art_urls.MAX_PENDING)
        self._planned: deque[tuple[str, str]] = deque()
        self._skipped: set[str] = set()  # source keys to leave until the next idle wait (offline root, unpinned TMDB)
        self._paused = False
        self._rootless_logged: set[str] = set()
        self._child: subprocess.Popen | None = None  # the running ffmpeg, killed by stop()
        self._extension_before: str | None = None
        self._progress: ArtworkProgress | None = None
        self._progress_at = 0.0
        self._progress_lock = threading.Lock()
        self._swept_at: float | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._paused

    def start(self) -> None:
        if self.after_import not in library_import.after_import_hooks:
            library_import.after_import_hooks.append(self.after_import)
        art_urls.on_enqueue = self._wake.set
        sweep_staging()  # a crash mid-run leaves its staging directory
        self._extension_before = art_urls.extension()
        use_host_format()
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="artwork-renditions", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if (child := self._child) is not None:
            with contextlib.suppress(OSError):
                child.kill()  # its render then fails, and _prepare saves nothing once stopping
        if self._thread:
            self._thread.join(timeout=10)
        if self.after_import in library_import.after_import_hooks:
            library_import.after_import_hooks.remove(self.after_import)
        art_urls.on_enqueue = None
        if self._extension_before is not None:
            art_urls.set_extension(self._extension_before)  # the process is ending; keeps in-process test runs isolated

    def wake(self, priority_titles: Iterable[str] = ()) -> None:
        """Titles a scan created or whose art changed go next, behind on-demand requests."""
        for title_id in priority_titles:
            self._urgent.extend((title_id, image_type) for image_type in art_urls.ART_TYPES)
        self._wake.set()

    def after_import(self, _run_id: str) -> None:
        self._wake.set()

    def run_once(self) -> bool:
        """Prepare one (title, image type). False when nothing is left (tests call this directly)."""
        pair = self._next()
        if pair is None:
            return False
        self._prepare(*pair)
        return True

    def _next(self) -> tuple[str, str] | None:
        taken = art_urls.take(1)
        if taken:
            return taken[0]
        if self._urgent:
            return self._urgent.popleft()
        if not self._planned:
            with SessionLocal() as db:
                found = scan(db, self._skipped)
            with self._progress_lock:
                self._record(found)
            self._planned.extend(found.order)
        return self._planned.popleft() if self._planned else None

    def _prepare(self, title_id: str, image_type: str) -> None:
        """Render one image if it still needs it. Every outcome is a row, except a source it cannot reach right now."""
        failure: str | None = None
        with SessionLocal() as db:
            title = _subject(db, title_id)
            if title is None or image_type not in art_urls.ART_TYPES:
                return
            source = title_image_source(title, image_type)
            if not art_urls.needs_preparation(title, image_type, source, db.get(TitleArtwork, (title_id, image_type))):
                return
            key = art_urls.source_key(title_id, image_type, source)
            local = art_urls.local((title.images or {})[image_type])
            ffmpeg = media_tool(db, "ffmpeg")
            if local and title.root_id is None and title.type != "person" and title_id not in self._rootless_logged:
                self._rootless_logged.add(title_id)
                logger.debug("Artwork for title %s has a local path but no storage root; skipped.", title_id)
            if ffmpeg is None or (local and not _source_online(db, title)):
                self._skipped.add(key)  # no ffmpeg, or the drive (or people folder) is offline: never mark failed for that
                return
            try:
                content_type, data = _image_bytes(db, title, image_type, offline_artwork())
            except FileNotFoundError:
                if not local or not _source_online(db, title):
                    self._skipped.add(key)  # TMDB art not pinned yet (on-demand fetches it), or the drive just went away
                    return
                failure = "unreadable"
            title_type = title.type
        if failure is not None:
            save(failed_row(title_id, image_type, key, failure))
            return
        try:
            rendered = render(data, content_type, title_type=title_type, image_type=image_type, timeout=PASS_TIMEOUT_SECONDS, ffmpeg=ffmpeg,
                              on_process=self._set_child)
        except RenditionError as exc:
            if not self._stop.is_set():  # killed by stop(): not the image's fault
                save(failed_row(title_id, image_type, key, exc.reason))
            return
        finally:
            self._set_child(None)
        if self._stop.is_set():
            discard(rendered)
            return
        try:
            install(key, rendered)
        except OSError:
            self._skipped.add(key)  # disk full or unwritable: tried again after the next idle wait
            return
        save(ready_row(title_id, image_type, key, rendered))

    def _set_child(self, process: subprocess.Popen | None) -> None:
        self._child = process
        if process is not None and self._stop.is_set():
            process.kill()  # stop() ran between the check and the spawn

    def _record(self, found: Scan) -> None:
        self._progress = ArtworkProgress(
            total=found.total, prepared=found.prepared, failed=found.failed, unsupported=found.unsupported,
            failure_reasons=dict(found.failure_reasons), cache_bytes=cache_bytes(), updated_at=utcnow(),
        )
        self._progress_at = time.monotonic()

    def progress(self) -> ArtworkProgress:
        """Admin counters, recomputed at most every 30 s; running, paused and serving are always live."""
        with self._progress_lock:
            if self._progress is None or time.monotonic() - self._progress_at > PROGRESS_MAX_AGE_SECONDS:
                try:
                    with SessionLocal() as db:
                        self._record(scan(db))
                except SQLAlchemyError as exc:  # Diagnostics must still answer
                    logger.warning("Artwork counters are unavailable: %.300s", repr(exc))
                    self._progress = self._progress or ArtworkProgress()
            cached = self._progress
        return cached.model_copy(update={"running": self.running, "paused_for_playback": self._paused, "serving": serving()})

    def sweep(self, cap: int = CACHE_CAP_BYTES) -> None:
        """Eviction: rows of deleted titles, files no row points at, then least recently used files over ``cap``.

        A file younger than ORPHAN_GRACE_SECONDS is never an orphan: an on-demand install lands before its row is saved.
        """
        fresh = time.time() - ORPHAN_GRACE_SECONDS  # taken before the rows are read
        with SessionLocal() as db:
            with write_transaction(db, name="renditions_sweep"):
                db.execute(delete(TitleArtwork).where(
                    ~select(MediaTitle.id).where(MediaTitle.id == TitleArtwork.title_id).exists(),
                    ~select(NfoPerson.id).where(NfoPerson.id == TitleArtwork.title_id).exists(),  # people's photos stay
                ))
            live = set(db.scalars(select(TitleArtwork.source_key)))
        kept: list[tuple[float, int, Path]] = []
        total = 0
        for path, key, size, mtime in rendition_files():
            if key in live:
                kept.append((mtime, size, path))
                total += size
            elif mtime < fresh:
                with contextlib.suppress(OSError):
                    path.unlink()
        evicted: set[str] = set()
        for _, size, path in sorted(kept):
            if total <= cap:
                break
            with contextlib.suppress(OSError):
                path.unlink()
                total -= size
                evicted.add(RENDITION_NAME.match(path.name)[1])
        # An evicted file's row goes too, so scan() queues that art again instead of leaving a permanent miss.
        # A cache that stays over cap re-renders cold art daily; raise CACHE_CAP_BYTES if that shows up.
        keys = sorted(evicted)
        for start in range(0, len(keys), SCAN_BATCH):
            with SessionLocal() as db, write_transaction(db, name="renditions_sweep"):
                db.execute(delete(TitleArtwork).where(TitleArtwork.source_key.in_(keys[start:start + SCAN_BATCH])))
        sweep_staging()
        self._swept_at = time.monotonic()

    def _housekeeping(self) -> None:
        if self._swept_at is None or time.monotonic() - self._swept_at > SWEEP_INTERVAL_SECONDS:
            self.sweep()

    def _wait_while_transcoding(self) -> bool:
        """No work while any video transcode runs. False when stopping."""
        while sessions.video_encodes() > 0:
            self._paused = True
            if self._stop.wait(PAUSE_POLL_SECONDS):
                return False
        self._paused = False
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._wait_while_transcoding():
                return
            errored = False
            try:
                worked = self.run_once()
            except Exception as exc:  # noqa: BLE001 - one bad image or a busy database must not stop the pass
                logger.warning("Artwork preparation failed; retrying later: %.300s", repr(exc))
                worked, errored = False, True
            try:
                self._housekeeping()
            except Exception as exc:  # noqa: BLE001 - counters and eviction retry next time
                logger.warning("Artwork cache housekeeping failed: %.300s", repr(exc))
            if worked:
                self._stop.wait(ITEM_GAP_SECONDS)
            elif errored:
                self._stop.wait(ERROR_BACKOFF_SECONDS)
            else:
                self._skipped.clear()
                self._wake.wait(IDLE_SECONDS)
                self._wake.clear()


renditions = ArtworkRenditions()


# ---- On-demand path -------------------------------------------------------------------------

ON_DEMAND_SLOTS = 2
ON_DEMAND_SLOT_WAIT_SECONDS = 0.05
ON_DEMAND_BUDGET_SECONDS = 0.8
ON_DEMAND_TIMEOUT_SECONDS = 5.0
FALLBACK_ORIGINAL_MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Served:
    content_type: str
    content: bytes = field(repr=False)
    cacheable: bool = True  # False: an original standing in for a rendition, sent with Cache-Control: no-store


class Preparing(Exception):
    """Over the on-demand budget with an original too large to stand in: answer 503 with Retry-After: 2."""


_slots = threading.BoundedSemaphore(ON_DEMAND_SLOTS)  # the pass's own ffmpeg does not count against these
_executor = ThreadPoolExecutor(max_workers=ON_DEMAND_SLOTS, thread_name_prefix="art-on-demand")
_inflight: dict[tuple[str, str], Future] = {}  # (source key, ext) -> the one generation serving every request for it
_inflight_lock = threading.Lock()


def serve_miss(title_id: str, image_type: str, key: str, width: int, ext: str, artwork: ArtworkService) -> Served | None:
    """A rendition that is not on disk. None = 404: the title is gone, ``key`` is no longer its art,
    or the title has no such width. Waits at most ON_DEMAND_BUDGET_SECONDS for generation, then falls back."""
    deadline = time.monotonic() + ON_DEMAND_BUDGET_SECONDS
    with SessionLocal() as db:
        title = _subject(db, title_id)
        source = title_image_source(title, image_type) if title is not None else None
        if source is None or art_urls.source_key(title_id, image_type, source) != key or width not in art_urls.widths(title.type, image_type):
            return None  # the width check comes first: a still width on a poster must not start ffmpeg
        row = db.get(TitleArtwork, (title_id, image_type))
        settled = row is not None and row.source_key == key and row.state != "ready"
    if not settled:
        future = _generation(title_id, image_type, key, ext, artwork)
        if future is not None:
            done, _ = wait([future], timeout=max(0.0, deadline - time.monotonic()))
            produced = future.result() if done else None
            if produced is not None:
                return Served(CONTENT_TYPES[ext], produced[width]) if width in produced else None
        art_urls.enqueue([(title_id, image_type)])
    return _original(title_id, image_type, artwork, any_size=settled)


def _original(title_id: str, image_type: str, artwork: ArtworkService, *, any_size: bool) -> Served | None:
    with SessionLocal() as db:
        title = _subject(db, title_id)
        if title is None:  # deleted mid-request
            return None
        try:
            content_type, data = _image_bytes(db, title, image_type, artwork)
        except FileNotFoundError:
            return None
    if any_size or len(data) <= FALLBACK_ORIGINAL_MAX_BYTES:
        count("fallback_original")
        return Served(content_type, data, cacheable=False)
    count("fallback_unavailable")
    raise Preparing


def _generation(title_id: str, image_type: str, key: str, ext: str, artwork: ArtworkService) -> Future | None:
    """The running generation for this source and format, else a new one if a slot frees within 50 ms."""
    job = (key, ext)
    with _inflight_lock:
        if job in _inflight:
            return _inflight[job]
    if not _slots.acquire(timeout=ON_DEMAND_SLOT_WAIT_SECONDS):
        return None
    with _inflight_lock:
        if job in _inflight:
            _slots.release()
            return _inflight[job]
        future = _executor.submit(_generate, title_id, image_type, key, ext, artwork)
        _inflight[job] = future
    future.add_done_callback(lambda _: _finished(job))
    return future


def _finished(job: tuple[str, str]) -> None:
    with _inflight_lock:
        _inflight.pop(job, None)
    _slots.release()


def _generate(title_id: str, image_type: str, key: str, ext: str, artwork: ArtworkService) -> dict[int, bytes] | None:
    """One on-demand run (nice 19, one thread, 5 s). Installs the files and, when stale, the row. Never raises."""
    try:
        with SessionLocal() as db:
            title = _subject(db, title_id)
            source = title_image_source(title, image_type) if title is not None else None
            ffmpeg = media_tool(db, "ffmpeg")
            if source is None or art_urls.source_key(title_id, image_type, source) != key or ffmpeg is None:
                count("failures")
                return None
            if art_urls.local((title.images or {})[image_type]) and not _source_online(db, title):
                count("failures")  # the drive is away: the queued pass retries once it is back, so no row
                return None
            content_type, data = _image_bytes(db, title, image_type, artwork)
            row = db.get(TitleArtwork, (title_id, image_type))
            stale = row is None or row.source_key != key
            title_type = title.type
        rendered = render(data, content_type, title_type=title_type, image_type=image_type, timeout=ON_DEMAND_TIMEOUT_SECONDS, ext=ext, ffmpeg=ffmpeg)
    except FileNotFoundError:
        count("failures")
        return None
    except RenditionError as exc:
        count("failures")
        if exc.reason != "timeout" and ext == art_urls.extension():
            with contextlib.suppress(SQLAlchemyError):
                save(failed_row(title_id, image_type, key, exc.reason))
        return None
    except Exception as exc:  # noqa: BLE001 - the request falls back to the original either way
        logger.warning("On-demand artwork failed: %.300s", repr(exc))
        count("failures")
        return None
    try:
        produced = {width: path.read_bytes() for width, path in rendered.files.items()}
        with contextlib.suppress(OSError):  # disk full: these bytes are still served, just not cached
            install(key, rendered)
        if stale and ext == art_urls.extension():
            with contextlib.suppress(SQLAlchemyError):
                save(ready_row(title_id, image_type, key, rendered))
    except Exception as exc:  # noqa: BLE001 - an unreadable staged file or a broken save: the request falls back
        logger.warning("On-demand artwork failed: %.300s", repr(exc))
        count("failures")
        return None
    finally:
        discard(rendered)
    count("generated")
    return produced


# ---- Jellyfin image sizing -------------------------------------------------------------------------

SIZE_PARAMS = frozenset({"maxwidth", "fillwidth", "width", "maxheight", "fillheight", "height", "format"})
_SIZE = re.compile(r"^\d{1,5}$")


def _size(value: str | None) -> int | None:
    return int(value) if value is not None and _SIZE.match(value) and 0 < int(value) <= 20_000 else None


def requested_width(params: Mapping[str, str], dims: tuple[int, int] | None) -> int | None:
    """maxwidth, else fillwidth, else width; else a height over the known source aspect, rounded up. None = no size."""
    for name in ("maxwidth", "fillwidth", "width"):
        if (width := _size(params.get(name))) is not None:
            return width
    if dims and dims[0] and dims[1]:
        for name in ("maxheight", "fillheight", "height"):
            if (height := _size(params.get(name))) is not None:
                return math.ceil(height * dims[0] / dims[1])
    return None


def pick_width(widths: Sequence[int], requested: int | None, source_width: int | None) -> int | None:
    """The smallest rendition >= ``requested``; past them all, the largest only when the source is no wider. None = original."""
    if requested is None or not widths:
        return None
    fitting = [width for width in widths if width >= requested]
    if fitting:
        return min(fitting)
    return max(widths) if source_width is not None and source_width <= max(widths) else None


def pick_format(params: Mapping[str, str], accept: str) -> str:
    """WebP when the client asks for no other format, accepts image/webp, and this host makes WebP; else JPEG."""
    wanted = (params.get("format") or "").lower()
    if wanted in ("", "webp") and "image/webp" in accept.lower() and art_urls.extension() == "webp":
        return "webp"
    return "jpg"


_HEIGHT_PARAMS = frozenset({"maxheight", "fillheight", "height"})


def sized(
    db: Session, title: MediaTitle, image_type: str, params: Mapping[str, str], accept: str, artwork: ArtworkService,
    *, head: bool = False,
) -> Served | None:
    """The rendition a sized Jellyfin request should get, from disk or on demand; None = serve the original.

    The caller has already authorised the request (token or signed tag). Raises Preparing when generation is
    running past budget; the Jellyfin route serves the original uncached instead of a 503 (unlike /api/art).
    ``head``: a HEAD request never starts generation — disk or the original's headers, no enqueue.
    """
    if image_type not in art_urls.ART_TYPES:
        return None
    entry = (title.images or {}).get(image_type)
    source = title_image_source(title, image_type)
    if source is None or not art_urls.supported(entry):
        return None
    key = art_urls.source_key(title.id, image_type, source)
    row = db.get(TitleArtwork, (title.id, image_type))
    current = row if row is not None and row.source_key == key else None
    if current is not None and current.state != "ready":
        return None
    dims = (current.width, current.height) if current is not None and current.width and current.height else None
    requested = requested_width(params, dims)
    width = pick_width(art_urls.widths(title.type, image_type), requested, dims[0] if dims else None)
    ext = pick_format(params, accept)
    if width is None or (image_type == "Logo" and ext == "jpg"):  # a JPEG logo would lose its alpha
        if dims is None and requested is None and any(name in params for name in _HEIGHT_PARAMS):
            # a height-only request with no known aspect: the original stands in, but not as the year-cached size
            return Served(*_image_bytes(db, title, image_type, artwork), cacheable=False)
        return None
    found = read_rendition(art_urls.rendition_file(key, width, ext))
    if found is not None:
        count("hits_disk")
        return Served(CONTENT_TYPES[ext], found[0])
    if head:
        return None  # never starts generation for a HEAD request
    count("misses")
    return serve_miss(title.id, image_type, key, width, ext, artwork)
