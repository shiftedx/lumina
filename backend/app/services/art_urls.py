"""Signed, content-addressed artwork rendition URLs and the preparation queue.

No database and no ffmpeg here: callers pass the title's source identity and its title_artwork row.
Summaries are built with ``title_art``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import settings
from app.media_schemas import TitleArt

RENDITION_VERSION = 1  # bump when encoder settings change: every source_key, URL and file changes with it
ART_TYPES = ("Primary", "Backdrop", "Logo")
SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})  # AVIF and GIF: unsupported, originals only
POSTER_WIDTHS = (240, 480)
STILL_WIDTHS = (400,)
SQUARE_WIDTHS = (240, 480, 960)
SQUARE_TYPES = frozenset({"album", "artist"})  # their Primary is centre-cropped to exactly 1:1
PERSON_WIDTHS = (120, 240)  # a person's photo: 2:3 portraits
TYPE_WIDTHS = {"Backdrop": (960, 1920), "Logo": (600,)}
# Every width the /api/art route may serve for an image type (the route cannot tell a still from a poster).
ALLOWED_WIDTHS = {
    "Primary": frozenset(POSTER_WIDTHS + STILL_WIDTHS + SQUARE_WIDTHS + PERSON_WIDTHS), "Backdrop": frozenset(TYPE_WIDTHS["Backdrop"]),
    "Logo": frozenset(TYPE_WIDTHS["Logo"]),
}
EXTENSIONS = ("webp", "jpg")
PREVIEW_TYPES = ("image/webp", "image/jpeg")
MAX_PENDING = 2000
SECRET_BYTES = 32

_extension = "webp"
_secret: tuple[Path, bytes] | None = None
_secret_lock = threading.Lock()
_pending: OrderedDict[tuple[str, str], None] = OrderedDict()
_pending_lock = threading.Lock()
on_enqueue: Callable[[], None] | None = None  # the preparation pass sets this to wake itself


def widths(title_type: str, image_type: str) -> tuple[int, ...]:
    if image_type == "Primary":
        if title_type in SQUARE_TYPES:
            return SQUARE_WIDTHS
        if title_type == "person":
            return PERSON_WIDTHS
        return STILL_WIDTHS if title_type == "episode" else POSTER_WIDTHS
    return TYPE_WIDTHS.get(image_type, ())


def square(title_type: str, image_type: str) -> bool:
    """Whether this image's renditions are centre-cropped to 1:1 (album and artist Primary)."""
    return image_type == "Primary" and title_type in SQUARE_TYPES


def portrait(title_type: str, image_type: str) -> bool:
    """Whether this image's renditions are centre-cropped to exactly 2:3 (a person's photo)."""
    return image_type == "Primary" and title_type == "person"


def supported(entry: Any) -> bool:
    """A stored image entry ({"path": …, "tag": …}, {"embedded": …, "tag": …}, {"tmdb": "/x.jpg"} or {"upload": sha}) whose format ffmpeg
    renditions. An embedded picture is JPEG or PNG, or titles.title_image_bytes refuses it and the pass records that."""
    if not isinstance(entry, dict):
        return False
    if isinstance(entry.get("upload"), str):  # re-encoded by title_images to JPEG, PNG or WebP
        return True
    if isinstance(entry.get("embedded"), str) and not entry.get("path"):
        return True
    name = entry.get("path") or entry.get("tmdb")
    return isinstance(name, str) and PurePosixPath(name).suffix.lower() in SUPPORTED_SUFFIXES


def local(entry: Any) -> bool:
    """Whether a stored image is read from its title's storage root (a file or a track's picture), not from TMDB."""
    return isinstance(entry, dict) and bool(entry.get("path") or entry.get("embedded"))


def source_key(title_id: str, image_type: str, source: str) -> str:
    """64 hex; ``source`` is titles.title_image_source(title, image_type)."""
    return hashlib.sha256(f"{title_id}:{image_type}:{source}:{RENDITION_VERSION}".encode()).hexdigest()


def extension() -> str:
    return _extension


def set_extension(value: str) -> None:
    """T1 calls this once at startup: "jpg" when the host ffmpeg lacks libwebp."""
    global _extension
    if value not in EXTENSIONS:
        raise ValueError(value)
    _extension = value


def secret_path() -> Path:
    return settings.data_dir / "art-secret"


def secret() -> bytes:
    """32 random bytes in app-data/art-secret (0600), created on first use; deleting the file re-keys every URL."""
    global _secret
    path = secret_path()
    cached = _secret
    if cached is not None and cached[0] == path:
        return cached[1]
    with _secret_lock:
        try:
            value = path.read_bytes()
        except FileNotFoundError:
            value = b""
        if len(value) != SECRET_BYTES:
            value = secrets.token_bytes(SECRET_BYTES)
            temporary = path.with_name(f".art-secret.{secrets.token_hex(4)}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(value)
            os.replace(temporary, path)
        _secret = (path, value)
        return value


def signature(title_id: str, image_type: str, key: str, scope: str = "") -> str:
    """22 chars: base64url (no padding) of the first 16 bytes of HMAC-SHA256(secret, "title_id:image_type:key").

    A restricted member's signature appends their art scope (member_access.art_scope) after the 22 chars and signs
    it too; the route then re-checks that member still sees the title. Unscoped signatures are unchanged.
    """
    message = f"{title_id}:{image_type}:{key}" + (f":{scope}" if scope else "")
    digest = hmac.new(secret(), message.encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode() + scope


def verify(sig: str, title_id: str, image_type: str, key: str) -> bool:
    return hmac.compare_digest(sig.encode(), signature(title_id, image_type, key, sig[22:]).encode())


def rendition_template(title_id: str, image_type: str, key: str, scope: str = "") -> str:
    return f"/api/art/{signature(title_id, image_type, key, scope)}/{title_id}/{image_type}/{key}-{{w}}.{_extension}"


def rendition_file(key: str, width: int, ext: str) -> Path:
    """app-data/artwork-cache/renditions/<key[0:2]>/<key>-<w>.<ext>; key, width and ext must already be validated."""
    return settings.data_dir / "artwork-cache" / "renditions" / key[:2] / f"{key}-{width}.{ext}"


def preview_uri(row: Any) -> str | None:
    if row is None or not row.preview or row.preview_type not in PREVIEW_TYPES:
        return None
    return f"data:{row.preview_type};base64,{base64.b64encode(row.preview).decode()}"


def needs_preparation(title: Any, image_type: str, source: str | None, row: Any) -> bool:
    """A supported source whose title_artwork row is missing or keyed to an older source."""
    if source is None or not supported((title.images or {}).get(image_type)):
        return False
    return row is None or row.source_key != source_key(title.id, image_type, source)


def title_art(
    title: Any, image_type: str, *, source: str | None, url: str | None, row: Any, preview: bool, scope: str = "",
) -> TitleArt | None:
    """The TitleArt for one image of ``title`` (a MediaTitle), or None when it has no such image.

    ``source`` and ``url`` are titles.title_image_source / titles.image_url for the same image; ``row`` is its
    TitleArtwork (any key). Row facts count only when the row's source_key is current. A current row in state
    failed or unsupported, or an unsupported source format, gives no rendition: the client uses ``url``.
    """
    if source is None or url is None:
        return None
    key = source_key(title.id, image_type, source)
    current = row if row is not None and row.source_key == key else None
    usable = supported((title.images or {}).get(image_type)) and (current is None or current.state == "ready")
    sizes = widths(title.type, image_type) if usable else ()
    facts = current is not None and image_type != "Logo"
    return TitleArt(
        url=url,
        rendition=rendition_template(title.id, image_type, key, scope) if sizes else None,
        widths=list(sizes),
        width=current.width if current is not None else None,
        height=current.height if current is not None else None,
        preview=preview_uri(current) if facts and preview else None,
        dominant=current.dominant if facts else None,
        accent=current.accent if facts else None,
    )


def enqueue(pairs: Iterable[tuple[str, str]]) -> None:
    """Ask the background pass to prepare these (title_id, image_type) next. FIFO, duplicates and overflow dropped."""
    added = False
    with _pending_lock:
        for pair in pairs:
            if pair not in _pending and len(_pending) < MAX_PENDING:
                _pending[pair] = None
                added = True
    if added and on_enqueue is not None:
        on_enqueue()


def take(limit: int) -> list[tuple[str, str]]:
    """Oldest first; removes what it returns."""
    with _pending_lock:
        taken = []
        while _pending and len(taken) < limit:
            taken.append(_pending.popitem(last=False)[0])
        return taken
