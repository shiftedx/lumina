"""Standalone household photo catalog and visibility boundary."""
from __future__ import annotations

import base64
import json
import os
import re
import stat
import unicodedata
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import and_, exists, false, func, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from app.models import User, utcnow
from app.photo_models import PhotoAlbum, PhotoAlbumMembership, PhotoDerivative, PhotoItem, PhotoState
from app.services import member_access, photo_derivatives

MAX_ORIGINAL_BYTES = 128 * 1024 * 1024
_STORAGE_KEY = re.compile(r"^originals/[0-9a-f]{2}/[0-9a-f-]{36}\.(?:jpg|png|webp|tiff|heic|heif|avif)$")


class PhotoError(ValueError):
    pass


class PhotoNotFound(PhotoError):
    pass


class PhotoForbidden(PhotoError):
    pass


def safe_filename(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise PhotoError("A valid photo filename is required.")
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."} or len(name) > 255:
        raise PhotoError("A valid photo filename is required.")
    return name


def photos_section_allowed(db: Session, user: User) -> bool:
    access = member_access.for_user(db, user)
    return access is None or access.sections is None or "photos" in access.sections


def visible_photo_clause(db: Session, user: User):  # noqa: ANN201 - SQLAlchemy expression
    """One reusable predicate for every photo, album and face query."""
    return or_(
        PhotoItem.owner_user_id == user.id,
        and_(PhotoItem.visibility == "shared", literal(photos_section_allowed(db, user))),
    )


def visible_photo_filter(db: Session, user: User, *, include_deleted: bool = False):  # noqa: ANN201
    clauses = [visible_photo_clause(db, user), PhotoItem.missing_at.is_(None)]
    if not include_deleted:
        clauses.append(PhotoItem.deleted_at.is_(None))
    else:
        clauses.append(or_(PhotoItem.deleted_at.is_(None), PhotoItem.owner_user_id == user.id))
    return and_(*clauses)


def owned_photo(db: Session, user: User, photo_id: str) -> PhotoItem:
    photo = db.scalar(select(PhotoItem).where(PhotoItem.id == photo_id, PhotoItem.owner_user_id == user.id))
    if photo is None:
        raise PhotoNotFound("Photo not found")
    return photo


def can_download(db: Session, user: User) -> bool:
    access = member_access.for_user(db, user)
    return access is None or access.can_download


def source_path(db: Session, photo: PhotoItem) -> Path:
    if photo.storage_kind == "managed":
        if not _STORAGE_KEY.fullmatch(photo.storage_key):
            raise FileNotFoundError("Photo is unavailable.")
        path = photo_derivatives.original_path(photo.storage_key)
    else:
        # External paths can be replaced after validation and therefore never
        # leave this service. Callers must use photo_files.snapshot_photo.
        raise FileNotFoundError("Photo is unavailable.")
    try:
        info = path.lstat()
    except OSError as exc:
        raise FileNotFoundError("Photo is unavailable.") from exc
    if not stat.S_ISREG(info.st_mode):
        raise FileNotFoundError("Photo is unavailable.")
    size = info.st_size
    if size <= 0 or size > MAX_ORIGINAL_BYTES:
        raise FileNotFoundError("Photo is unavailable.")
    return path


def photo_source_path(db: Session, photo: PhotoItem) -> Path:
    return source_path(db, photo)


def load_photo_bytes(db: Session, photo: PhotoItem) -> bytes:
    """Bounded loader for the face worker; storage paths never leave backend services."""
    from app.persistence import read_regular_file
    from app.services.photo_files import snapshot_photo
    with snapshot_photo(db, photo) as snapshot:
        return read_regular_file(snapshot.path, MAX_ORIGINAL_BYTES)


def _clean(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    result = " ".join(value.split())[:limit]
    return result or None


def _captured(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        found = datetime.fromisoformat(value)
    except ValueError:
        return None
    if found.tzinfo is not None:
        found = found.astimezone(UTC).replace(tzinfo=None)
    return found


def _album_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def _cursor_encode(values: list[str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(values, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _cursor_decode(value: str | None, sort: str) -> tuple[datetime, datetime, str] | None:
    if value is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != 4 or values[0] != sort:
            raise ValueError
        first, created = datetime.fromisoformat(values[1]), datetime.fromisoformat(values[2])
        if not isinstance(values[3], str) or len(values[3]) != 36:
            raise ValueError
        return first, created, values[3]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise PhotoError("Invalid photo cursor.") from None


class PhotoService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def visible_predicate(self, user: User):  # noqa: ANN201
        return visible_photo_clause(self.db, user)

    def get(self, photo_id: str, user: User, *, include_deleted: bool = False) -> PhotoItem:
        clauses = [PhotoItem.id == photo_id, self.visible_predicate(user), PhotoItem.missing_at.is_(None)]
        if not include_deleted:
            clauses.append(PhotoItem.deleted_at.is_(None))
        else:
            # A shared photo's trash state is private to its owner.
            clauses.append(or_(PhotoItem.deleted_at.is_(None), PhotoItem.owner_user_id == user.id))
        photo = self.db.scalar(select(PhotoItem).where(*clauses))
        if photo is None:
            raise PhotoNotFound("Photo not found")
        return photo

    @staticmethod
    def can_edit(photo: PhotoItem, user: User) -> bool:
        return photo.owner_user_id == user.id

    def duplicate(self, user: User, content_hash: str) -> PhotoItem | None:
        return self.db.scalar(select(PhotoItem).where(
            PhotoItem.owner_user_id == user.id, PhotoItem.sha256 == content_hash,
        ))

    def import_staged(self, staged: Path, filename: str, user: User, content_hash: str, size: int, facts: dict[str, Any]) -> tuple[PhotoItem, bool]:
        filename = safe_filename(filename)
        existing = self.duplicate(user, content_hash)
        if existing is not None:
            return existing, False
        content_type = facts.get("content_type")
        extension = photo_derivatives.EXTENSIONS.get(content_type)
        if extension is None:
            raise PhotoError("Unsupported photo format.")
        photo_id = str(uuid.uuid4())
        storage_key = f"originals/{content_hash[:2]}/{photo_id}.{extension}"
        target = photo_derivatives.original_path(storage_key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        published = False
        try:
            os.replace(staged, target)
            published = True
            directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            photo = PhotoItem(
                id=photo_id, owner_user_id=user.id, visibility="private", storage_kind="managed", storage_key=storage_key,
                original_filename=filename, content_type=content_type, sha256=content_hash, bytes=size,
                width=facts["width"], height=facts["height"], orientation=facts.get("orientation", 1),
                captured_at=_captured(facts.get("captured_at")), captured_at_raw=_clean(facts.get("captured_at_raw"), 64),
                camera_make=_clean(facts.get("camera_make"), 120), camera_model=_clean(facts.get("camera_model"), 120),
                metadata_json={key: value for key, value in (facts.get("metadata") or {}).items() if value is not None},
            )
            self.db.add(photo)
            for width in photo_derivatives.WIDTHS:
                self.db.add(PhotoDerivative(
                    id=str(uuid.uuid4()), photo_id=photo.id, width=width,
                    source_key=photo_derivatives.source_key(content_hash, width), state="pending",
                ))
            self.db.flush()
        except Exception:
            if published:
                target.unlink(missing_ok=True)
            raise
        return photo, True

    def mark_derivatives(self, photo: PhotoItem, products: dict[int, tuple[str, int]] | None, error: str | None = None) -> None:
        rows = list(self.db.scalars(select(PhotoDerivative).where(PhotoDerivative.photo_id == photo.id)))
        for row in rows:
            product = products.get(row.width) if products else None
            expected = photo_derivatives.source_key(photo.sha256, row.width)
            if product is None or product[0] != expected:
                row.state, row.error = "failed", (error or "render_failed")[:64]
            else:
                row.source_key, row.bytes, row.state, row.error = product[0], product[1], "ready", None

    def derivative(self, photo: PhotoItem, width: int) -> PhotoDerivative | None:
        return self.db.scalar(select(PhotoDerivative).where(
            PhotoDerivative.photo_id == photo.id, PhotoDerivative.width == width,
            PhotoDerivative.source_key == photo_derivatives.source_key(photo.sha256, width),
            PhotoDerivative.state == "ready",
        ))

    def list(
        self, user: User, *, limit: int, cursor: str | None, sort: Literal["captured", "added"], q: str | None,
        favorite: bool | None, album_id: str | None, person_id: str | None, camera: str | None,
        date_from: date | None, date_to: date | None, deleted: bool,
    ) -> tuple[list[PhotoItem], int, str | None]:
        clauses = [
            self.visible_predicate(user), PhotoItem.missing_at.is_(None),
            PhotoItem.deleted_at.is_not(None) if deleted else PhotoItem.deleted_at.is_(None),
        ]
        if deleted:
            clauses.append(PhotoItem.owner_user_id == user.id)
        favorite_exists = exists(select(PhotoState.id).where(
            PhotoState.photo_id == PhotoItem.id, PhotoState.user_id == user.id, PhotoState.favorite.is_(True),
        ))
        if favorite is not None:
            clauses.append(favorite_exists if favorite else ~favorite_exists)
        if album_id:
            self.album(album_id, user)
            clauses.append(exists(select(PhotoAlbumMembership.id).where(
                PhotoAlbumMembership.album_id == album_id, PhotoAlbumMembership.photo_id == PhotoItem.id,
            )))
        if q and (needle := " ".join(q.split())):
            clauses.append(or_(
                PhotoItem.title.contains(needle, autoescape=True), PhotoItem.original_filename.contains(needle, autoescape=True),
                PhotoItem.camera_make.contains(needle, autoescape=True), PhotoItem.camera_model.contains(needle, autoescape=True),
            ))
        if camera and (needle := " ".join(camera.split())):
            clauses.append(or_(PhotoItem.camera_make.contains(needle, autoescape=True), PhotoItem.camera_model.contains(needle, autoescape=True)))
        if date_from:
            clauses.append(PhotoItem.captured_at >= datetime.combine(date_from, time.min))
        if date_to:
            clauses.append(PhotoItem.captured_at <= datetime.combine(date_to, time.max))
        if person_id:
            try:
                from app.photo_face_models import PhotoFace, PhotoPerson
                if self.db.scalar(select(PhotoPerson.id).where(
                    PhotoPerson.id == person_id, PhotoPerson.owner_user_id == user.id,
                    PhotoPerson.hidden.is_(False), PhotoPerson.redirected_to_id.is_(None),
                )) is None:
                    raise PhotoNotFound("Person not found")
                clauses.append(exists(select(PhotoFace.id).where(
                    PhotoFace.photo_id == PhotoItem.id, PhotoFace.person_id == person_id,
                    PhotoFace.owner_user_id == user.id,
                )))
            except ImportError:
                clauses.append(false())
        primary = func.coalesce(PhotoItem.captured_at, PhotoItem.created_at) if sort == "captured" else PhotoItem.created_at
        unpaged = select(PhotoItem).where(*clauses)
        total = self.db.scalar(select(func.count()).select_from(unpaged.subquery())) or 0
        parsed = _cursor_decode(cursor, sort)
        if parsed is not None:
            first, created, photo_id = parsed
            if sort == "captured":
                # The redundant scalar range lets SQLite seek into the
                # coalesce-expression index; the tuple keeps exact tie rules.
                clauses.extend((
                    primary <= first,
                    tuple_(primary, PhotoItem.created_at, PhotoItem.id) < tuple_(first, created, photo_id),
                ))
            else:
                clauses.append(tuple_(PhotoItem.created_at, PhotoItem.id) < tuple_(created, photo_id))
        base = select(PhotoItem).where(*clauses)
        ordering = (
            (primary.desc(), PhotoItem.created_at.desc(), PhotoItem.id.desc())
            if sort == "captured" else (PhotoItem.created_at.desc(), PhotoItem.id.desc())
        )
        rows = list(self.db.scalars(base.order_by(*ordering).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if more and rows:
            last = rows[-1]
            first = last.captured_at or last.created_at if sort == "captured" else last.created_at
            next_cursor = _cursor_encode([sort, first.isoformat(), last.created_at.isoformat(), last.id])
        return rows, total, next_cursor

    def serialize_many(self, photos: list[PhotoItem], user: User) -> list[dict[str, Any]]:
        if not photos:
            return []
        ids = [photo.id for photo in photos]
        favorites = set(self.db.scalars(select(PhotoState.photo_id).where(
            PhotoState.user_id == user.id, PhotoState.photo_id.in_(ids), PhotoState.favorite.is_(True),
        )))
        albums: dict[str, list[str]] = {}
        visible_albums = or_(
            PhotoAlbum.owner_user_id == user.id,
            and_(PhotoAlbum.visibility == "shared", literal(photos_section_allowed(self.db, user))),
        )
        for photo_id, album_id in self.db.execute(
            select(PhotoAlbumMembership.photo_id, PhotoAlbumMembership.album_id)
            .join(PhotoAlbum, PhotoAlbum.id == PhotoAlbumMembership.album_id)
            .where(PhotoAlbumMembership.photo_id.in_(ids), PhotoAlbum.deleted_at.is_(None), visible_albums)
        ):
            albums.setdefault(photo_id, []).append(album_id)
        people: dict[str, list[str]] = {}
        try:
            from app.photo_face_models import PhotoFace, PhotoPerson
            for photo_id, person_id in self.db.execute(
                select(PhotoFace.photo_id, PhotoFace.person_id)
                .join(PhotoPerson, PhotoPerson.id == PhotoFace.person_id)
                .where(
                    PhotoFace.photo_id.in_(ids), PhotoFace.owner_user_id == user.id,
                    PhotoPerson.owner_user_id == user.id, PhotoPerson.hidden.is_(False),
                    PhotoPerson.redirected_to_id.is_(None),
                )
            ):
                people.setdefault(photo_id, []).append(person_id)
        except ImportError:
            pass
        download = can_download(self.db, user)
        return [self.serialize(photo, user, favorite=photo.id in favorites, album_ids=albums.get(photo.id, []), person_ids=people.get(photo.id, []), download=download) for photo in photos]

    def serialize(
        self, photo: PhotoItem, user: User, *, favorite: bool | None = None, album_ids: list[str] | None = None,
        person_ids: list[str] | None = None, download: bool | None = None,
    ) -> dict[str, Any]:
        if favorite is None:
            favorite = self.db.scalar(select(PhotoState.favorite).where(PhotoState.user_id == user.id, PhotoState.photo_id == photo.id)) or False
        camera = " ".join(dict.fromkeys(filter(None, (photo.camera_make, photo.camera_model)))) or None
        return {
            "id": photo.id, "title": photo.title, "filename": photo.original_filename, "content_type": photo.content_type,
            "width": photo.width, "height": photo.height, "captured_at": photo.captured_at, "added_at": photo.created_at,
            "camera": camera, "camera_make": photo.camera_make, "camera_model": photo.camera_model, "bytes": photo.bytes,
            "favorite": favorite, "can_edit": self.can_edit(photo, user), "visibility": photo.visibility,
            "thumbnail_url": f"/api/photos/{photo.id}/image?width=400",
            "preview_url": f"/api/photos/{photo.id}/image?width=1200",
            "original_url": f"/api/photos/{photo.id}/original" if (download if download is not None else can_download(self.db, user)) else None,
            "deleted_at": photo.deleted_at, "album_ids": sorted(album_ids or []), "person_ids": sorted(set(person_ids or [])),
        }

    def patch(self, photo: PhotoItem, user: User, values: dict[str, Any]) -> PhotoItem:
        owner_fields = {"title", "visibility", "deleted"}
        if owner_fields.intersection(values) and not self.can_edit(photo, user):
            raise PhotoForbidden("Only the photo owner can change it.")
        if "title" in values:
            photo.title = _clean(values["title"], 500)
        if "visibility" in values and values["visibility"] is not None:
            photo.visibility = values["visibility"]
        if "deleted" in values and values["deleted"] is not None:
            photo.deleted_at = utcnow() if values["deleted"] else None
        if "favorite" in values and values["favorite"] is not None:
            state = self.db.scalar(select(PhotoState).where(PhotoState.user_id == user.id, PhotoState.photo_id == photo.id))
            if state is None:
                state = PhotoState(id=str(uuid.uuid4()), user_id=user.id, photo_id=photo.id)
                self.db.add(state)
            state.favorite = values["favorite"]
        self.db.flush()
        return photo

    def albums(self, user: User, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        clauses = [or_(
            PhotoAlbum.owner_user_id == user.id,
            and_(PhotoAlbum.visibility == "shared", literal(photos_section_allowed(self.db, user))),
        )]
        clauses.append(PhotoAlbum.deleted_at.is_not(None) if include_deleted else PhotoAlbum.deleted_at.is_(None))
        if include_deleted:
            clauses.append(PhotoAlbum.owner_user_id == user.id)
        visible = visible_photo_clause(self.db, user)
        membership = and_(
            PhotoAlbumMembership.album_id == PhotoAlbum.id,
            PhotoAlbumMembership.photo_id == PhotoItem.id,
            visible, PhotoItem.deleted_at.is_(None), PhotoItem.missing_at.is_(None),
        )
        count = select(func.count()).select_from(PhotoAlbumMembership).join(
            PhotoItem, PhotoItem.id == PhotoAlbumMembership.photo_id,
        ).where(membership).correlate(PhotoAlbum).scalar_subquery()
        cover = select(PhotoItem.id).select_from(PhotoAlbumMembership).join(
            PhotoItem, PhotoItem.id == PhotoAlbumMembership.photo_id,
        ).where(membership).order_by(
            PhotoAlbumMembership.position, PhotoAlbumMembership.id,
        ).limit(1).correlate(PhotoAlbum).scalar_subquery()
        rows = self.db.execute(select(PhotoAlbum, count, cover).where(*clauses).order_by(
            PhotoAlbum.created_at.desc(), PhotoAlbum.id.desc(),
        )).all()
        return [self._album_dict(album, user, photo_count, cover_id) for album, photo_count, cover_id in rows]

    def album(self, album_id: str, user: User, *, edit: bool = False, include_deleted: bool = False) -> PhotoAlbum:
        album = self.db.get(PhotoAlbum, album_id)
        if album is None or (album.deleted_at is not None and not include_deleted):
            raise PhotoNotFound("Album not found")
        if album.deleted_at is not None and album.owner_user_id != user.id:
            raise PhotoNotFound("Album not found")
        if album.owner_user_id != user.id and (
            edit or album.visibility != "shared" or not photos_section_allowed(self.db, user)
        ):
            raise PhotoNotFound("Album not found")
        return album

    def create_album(self, user: User, values: dict[str, Any]) -> PhotoAlbum:
        name = _clean(values.get("name"), 120)
        if not name:
            raise PhotoError("An album name is required.")
        album = PhotoAlbum(
            id=str(uuid.uuid4()), owner_user_id=user.id, name=name, name_key=_album_key(name),
            description=_clean(values.get("description"), 2000), visibility=values.get("visibility") or "private",
        )
        self.db.add(album); self.db.flush(); return album

    def patch_album(self, album: PhotoAlbum, values: dict[str, Any]) -> PhotoAlbum:
        if "name" in values and values["name"] is not None:
            name = _clean(values["name"], 120)
            if not name: raise PhotoError("An album name is required.")
            album.name, album.name_key = name, _album_key(name)
        if "description" in values: album.description = _clean(values["description"], 2000)
        if values.get("visibility") is not None: album.visibility = values["visibility"]
        if values.get("deleted") is not None: album.deleted_at = utcnow() if values["deleted"] else None
        self.db.flush(); return album

    def add_to_album(self, album: PhotoAlbum, photo: PhotoItem) -> None:
        found = self.db.scalar(select(PhotoAlbumMembership).where(
            PhotoAlbumMembership.album_id == album.id, PhotoAlbumMembership.photo_id == photo.id,
        ))
        if found is None:
            position = self.db.scalar(select(func.max(PhotoAlbumMembership.position)).where(PhotoAlbumMembership.album_id == album.id))
            position = -1 if position is None else position
            self.db.add(PhotoAlbumMembership(id=str(uuid.uuid4()), album_id=album.id, photo_id=photo.id, position=position + 1))

    def remove_from_album(self, album: PhotoAlbum, photo_id: str) -> None:
        found = self.db.scalar(select(PhotoAlbumMembership).where(
            PhotoAlbumMembership.album_id == album.id, PhotoAlbumMembership.photo_id == photo_id,
        ))
        if found is not None: self.db.delete(found)

    def serialize_album(self, album: PhotoAlbum, user: User) -> dict[str, Any]:
        visible = visible_photo_clause(self.db, user)
        members = select(PhotoItem).join(PhotoAlbumMembership, PhotoAlbumMembership.photo_id == PhotoItem.id).where(
            PhotoAlbumMembership.album_id == album.id, visible,
            PhotoItem.deleted_at.is_(None), PhotoItem.missing_at.is_(None),
        )
        count = self.db.scalar(select(func.count()).select_from(members.subquery())) or 0
        cover = self.db.scalar(members.order_by(PhotoAlbumMembership.position, PhotoAlbumMembership.id).limit(1))
        return self._album_dict(album, user, count, cover.id if cover else None)

    @staticmethod
    def _album_dict(album: PhotoAlbum, user: User, count: int, cover_id: str | None) -> dict[str, Any]:
        return {
            "id": album.id, "name": album.name, "description": album.description, "visibility": album.visibility,
            "can_edit": album.owner_user_id == user.id,
            "photo_count": count, "cover_url": f"/api/photos/{cover_id}/image?width=400" if cover_id else None,
            "created_at": album.created_at, "deleted_at": album.deleted_at,
        }
