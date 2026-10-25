"""Authenticated REST boundary for the standalone Photos surface."""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from app.db import get_db
from app.http_boundaries import MAX_PHOTO_UPLOAD_BODY_BYTES, PHOTO_UPLOAD_TOO_LARGE_MESSAGE
from app.models import User
from app.persistence import write_transaction
from app.photo_schemas import (
    PhotoAlbumCreate, PhotoAlbumPatch, PhotoAlbumResponse, PhotoAlbumsPage, PhotoPatch, PhotoResponse,
    PhotoSourceCreate, PhotoSourceResponse, PhotoSourcesPage, PhotosPage,
)
from app.photo_root_models import PhotoImportSource
from app.security import get_admin_user, get_current_user
from app.services import photo_cache, photo_derivatives, photo_files, photo_imports
from app.services.photos import (
    PhotoError, PhotoForbidden, PhotoNotFound, PhotoService, can_download, photos_section_allowed, source_path,
)
from app.services.upload_admission import UploadBusy, UploadStorageFull, upload_admission


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (photo_cache.PhotoStorageFull, photo_cache.PhotoStorageBusy)):
        return HTTPException(status_code=503, detail=exc.code, headers={"Retry-After": str(exc.retry_after)})
    if isinstance(exc, PhotoNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PhotoForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


async def import_photo(
    filename: str, request: Request, current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    if not photos_section_allowed(db, current_user):
        raise HTTPException(status_code=404, detail="Photos not found")
    root = photo_derivatives.photos_root(); root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        lease = upload_admission.acquire(
            current_user.id, root,
            content_length=None if request.headers.get("transfer-encoding") else request.headers.get("content-length"),
            maximum_bytes=MAX_PHOTO_UPLOAD_BODY_BYTES,
        )
    except UploadBusy as exc:
        raise HTTPException(
            status_code=429, detail="upload_busy", headers={"Retry-After": "2", "Cache-Control": "no-store"},
        ) from exc
    except UploadStorageFull as exc:
        raise HTTPException(status_code=507, detail="storage_full", headers={"Cache-Control": "no-store"}) from exc
    staged: Path | None = None
    digest, size, target = hashlib.sha256(), 0, None
    products = {}
    try:
        fd, temporary = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=root)
        staged = Path(temporary)
        with os.fdopen(fd, "wb") as handle:
            try:
                async for block in request.stream():
                    size += len(block); digest.update(block); handle.write(block)
            except ClientDisconnect as exc:
                if request.scope.get("state", {}).get("body_too_large"):
                    raise HTTPException(status_code=413, detail=PHOTO_UPLOAD_TOO_LARGE_MESSAGE) from exc
                if request.scope.get("state", {}).get("body_timeout"):
                    raise HTTPException(status_code=408, detail="Photo upload timed out.") from exc
                raise HTTPException(status_code=400, detail="Photo upload was interrupted.") from exc
            handle.flush(); os.fsync(handle.fileno())
        if size <= 0:
            raise HTTPException(status_code=422, detail="The photo upload is empty.")
        def read_header() -> bytes:
            with staged.open("rb") as handle:
                return handle.read(128)
        header = await anyio.to_thread.run_sync(read_header)
        content_type = photo_derivatives.sniff(header)
        if content_type is None:
            raise HTTPException(status_code=422, detail="Photos must be JPEG, PNG, WebP, TIFF, AVIF or HEIC/HEIF images.")
        service = PhotoService(db)
        content_hash = digest.hexdigest()
        duplicate = service.duplicate(current_user, content_hash)
        if duplicate is not None:
            return service.serialize(duplicate, current_user)
        db.rollback()  # do not hold the duplicate-check read transaction across decode/render
        try:
            facts, products = await anyio.to_thread.run_sync(
                photo_derivatives.prepare, staged, content_type, content_hash,
            )
            with write_transaction(db, name="photo_import"):
                photo, created = service.import_staged(staged, filename, current_user, content_hash, size, facts)
                if created:
                    target = source_path(db, photo)
                    service.mark_derivatives(photo, products)
        except IntegrityError as exc:
            if target is not None: target.unlink(missing_ok=True)
            db.rollback()
            # A simultaneous upload by the same owner is the same idempotent
            # operation. Its random losing target was removed by import_staged.
            photo = service.duplicate(current_user, content_hash)
            if photo is None:
                raise _error(exc) from exc
            created = False
        except (PhotoError, photo_derivatives.PhotoDecodeError) as exc:
            if target is not None: target.unlink(missing_ok=True)
            raise _error(exc) from exc
        except Exception:
            if target is not None: target.unlink(missing_ok=True)
            raise
        if created:
            try:
                from app.services import photo_faces
                photo_faces.queue_photo(db, photo, requested=False)
            except photo_faces.FaceError:
                pass
        return service.serialize(photo, current_user)
    finally:
        try:
            if staged is not None:
                staged.unlink(missing_ok=True)
        finally:
            try:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(photo_cache.release_publication, {key for key, _size in products.values()})
            finally:
                lease.release()


def list_photos(
    limit: int = Query(default=40, ge=1, le=100), cursor: str | None = None,
    sort: Literal["captured", "added"] = "captured", q: str | None = Query(default=None, max_length=200),
    favorite: bool | None = None, album_id: str | None = None, person_id: str | None = None,
    camera: str | None = Query(default=None, max_length=200), date_from: date | None = None, date_to: date | None = None,
    deleted: bool = False, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = PhotoService(db)
    try:
        rows, total, next_cursor = service.list(
            current_user, limit=limit, cursor=cursor, sort=sort, q=q, favorite=favorite, album_id=album_id,
            person_id=person_id, camera=camera, date_from=date_from, date_to=date_to, deleted=deleted,
        )
    except PhotoError as exc:
        raise _error(exc) from exc
    return {"items": service.serialize_many(rows, current_user), "total": total, "next_cursor": next_cursor}


def patch_photo(
    photo_id: str, payload: PhotoPatch, current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = PhotoService(db)
    try:
        with write_transaction(db, name="photo_edit"):
            photo = service.get(photo_id, current_user, include_deleted=True)
            service.patch(photo, current_user, payload.model_dump(exclude_unset=True))
        return service.serialize(photo, current_user)
    except PhotoError as exc:
        raise _error(exc) from exc


def restore_photo(
    photo_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = PhotoService(db)
    try:
        with write_transaction(db, name="photo_restore"):
            photo = service.get(photo_id, current_user, include_deleted=True)
            service.patch(photo, current_user, {"deleted": False})
        return service.serialize(photo, current_user)
    except PhotoError as exc:
        raise _error(exc) from exc


async def photo_image(
    photo_id: str, request: Request, width: int = Query(default=1200),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> Response:
    if width not in photo_derivatives.WIDTHS:
        raise HTTPException(status_code=422, detail="Photo width must be 400, 1200 or 2400.")
    service = PhotoService(db)
    try:
        # Owners need previews in Trash; include_deleted still hides another
        # member's deleted shared item.
        photo = service.get(photo_id, current_user, include_deleted=True)
    except PhotoError as exc:
        raise _error(exc) from exc
    row = service.derivative(photo, width)
    data = None
    if row is not None:
        etag = f'"{row.source_key}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={
                "ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate",
            })
        try:
            data = await anyio.to_thread.run_sync(photo_derivatives.read, row.source_key)
            photo_cache.touch(row.source_key)
        except OSError:
            pass
    if data is None:
        # Repair a missing or interrupted durable preview through bounded admission.
        products = {}
        try:
            source_root, source_relative = photo_files.photo_location(db, photo)
            expected_sha256 = photo.sha256
            db.rollback()  # do not hold a SQLite read transaction across decode/render
            def repair() -> tuple[dict[int, tuple[str, int]], str]:
                products, fingerprint = photo_files.build_derivatives(source_root, source_relative, expected_sha256)
                return products, fingerprint.sha256
            products, source_sha256 = await anyio.to_thread.run_sync(repair)
            with write_transaction(db, name="photo_derivative_repair"):
                photo = db.get(type(photo), photo_id)
                if photo is None:
                    raise photo_files.PhotoFileChanged("Photo content changed.")
                if photo.sha256 != source_sha256:
                    raise photo_files.PhotoFileChanged("Photo content changed.")
                service.mark_derivatives(photo, products)
            photo_cache.trim()
            row = service.derivative(photo, width)
            if row is not None:
                data = await anyio.to_thread.run_sync(photo_derivatives.read, row.source_key)
                photo_cache.touch(row.source_key)
        except (FileNotFoundError, OSError, photo_derivatives.PhotoDecodeError):
            data = None
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(photo_cache.release_publication, {key for key, _size in products.values()})
    if row is None or data is None:
        return Response(status_code=503, headers={"Retry-After": "2", "Cache-Control": "no-store"})
    etag = f'"{row.source_key}"'
    return Response(
        content=b"" if request.method == "HEAD" else data, media_type="image/webp",
        headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate", "Content-Length": str(len(data)), "X-Content-Type-Options": "nosniff"},
    )


def photo_original(
    photo_id: str, request: Request, current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    service = PhotoService(db)
    snapshot_context = None
    try:
        photo = service.get(photo_id, current_user)
        if not can_download(db, current_user):
            raise PhotoForbidden("downloads_not_allowed")
        source_root, source_relative = photo_files.photo_location(db, photo)
        expected_sha256, content_type, original_filename = photo.sha256, photo.content_type, photo.original_filename
        db.rollback()
        snapshot_context = photo_files.snapshot_external(
            source_root, source_relative, photo_derivatives.photos_root() / ".snapshots",
            expected_sha256=expected_sha256,
        )
        snapshot = snapshot_context.__enter__()
    except photo_files.PhotoSnapshotBusy as exc:
        raise HTTPException(
            status_code=503, detail="photo_snapshot_busy", headers={"Retry-After": "2", "Cache-Control": "no-store"},
        ) from exc
    except (PhotoError, FileNotFoundError, photo_files.PhotoFileUnavailable) as exc:
        raise _error(exc if isinstance(exc, PhotoError) else PhotoNotFound("Photo not found")) from exc
    disposition = f"attachment; filename*=utf-8''{quote(original_filename, safe='')}"
    headers = {
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": disposition, "Content-Length": str(snapshot.fingerprint.size),
    }
    if request.method == "HEAD":
        snapshot_context.__exit__(None, None, None)
        return Response(content=b"", media_type=content_type, headers=headers)

    def body():  # noqa: ANN202 - Starlette sync body iterator
        try:
            with snapshot.path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    yield block
        finally:
            snapshot_context.__exit__(None, None, None)

    return StreamingResponse(body(), media_type=content_type, headers=headers)


def list_albums(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    return {"items": PhotoService(db).albums(current_user)}


def create_album(
    payload: PhotoAlbumCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = PhotoService(db)
    if not photos_section_allowed(db, current_user): raise HTTPException(status_code=404, detail="Photos not found")
    try:
        with write_transaction(db, name="photo_album_create"):
            album = service.create_album(current_user, payload.model_dump())
        return service.serialize_album(album, current_user)
    except (PhotoError, IntegrityError) as exc: raise _error(exc) from exc


def patch_album(
    album_id: str, payload: PhotoAlbumPatch, current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = PhotoService(db)
    try:
        with write_transaction(db, name="photo_album_edit"):
            album = service.patch_album(service.album(album_id, current_user, edit=True, include_deleted=True), payload.model_dump(exclude_unset=True))
        return service.serialize_album(album, current_user)
    except (PhotoError, IntegrityError) as exc: raise _error(exc) from exc


def delete_album(
    album_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> Response:
    service = PhotoService(db)
    try:
        with write_transaction(db, name="photo_album_delete"):
            service.patch_album(service.album(album_id, current_user, edit=True), {"deleted": True})
    except PhotoError as exc: raise _error(exc) from exc
    return Response(status_code=204)


def add_album_photo(
    album_id: str, photo_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = PhotoService(db)
    try:
        with write_transaction(db, name="photo_album_add"):
            album = service.album(album_id, current_user, edit=True)
            service.add_to_album(album, service.get(photo_id, current_user))
        return service.serialize_album(album, current_user)
    except PhotoError as exc: raise _error(exc) from exc


def remove_album_photo(
    album_id: str, photo_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> Response:
    service = PhotoService(db)
    try:
        with write_transaction(db, name="photo_album_remove"):
            service.remove_from_album(service.album(album_id, current_user, edit=True), photo_id)
    except PhotoError as exc: raise _error(exc) from exc
    return Response(status_code=204)


def _photo_source(db: Session, source_id: str) -> PhotoImportSource:
    source = db.get(PhotoImportSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Photo source not found")
    return source


def list_photo_sources(
    _admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = photo_imports.PhotoImportService(db)
    sources = list(db.query(PhotoImportSource).order_by(PhotoImportSource.created_at, PhotoImportSource.id))
    roots = []
    from app.models import StorageRoot
    for root in db.query(StorageRoot).filter(StorageRoot.mode == "external").order_by(StorageRoot.label, StorageRoot.id):
        # Never touch a NAS mount on this request path. The worker updates
        # source state asynchronously; root observations come from the existing
        # bounded storage-root probe and may intentionally be stale/unknown.
        state = (root.observation or {}).get("state")
        roots.append({
            "id": root.id, "label": root.label,
            "online": root.enabled and state not in {"offline", "permission_denied", "identity_mismatch"},
        })
    return {"items": [service.serialize(source, db) for source in sources], "roots": roots}


def create_photo_source(
    payload: PhotoSourceCreate, admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = photo_imports.PhotoImportService(db)
    try:
        with write_transaction(db, name="photo_source_create"):
            source = service.create(payload.root_id, payload.relative_folder, admin, payload.visibility)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="That photo source is already registered.") from exc
    except photo_imports.PhotoImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    photo_imports.worker.wake()
    return service.serialize(source, db)


def scan_photo_source(
    source_id: str, _admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = photo_imports.PhotoImportService(db)
    try:
        with write_transaction(db, name="photo_source_scan"):
            source = _photo_source(db, source_id)
            service.scan(source)
    except photo_imports.PhotoImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    photo_imports.worker.wake()
    return service.serialize(source, db)


def cancel_photo_source(
    source_id: str, _admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> dict:
    service = photo_imports.PhotoImportService(db)
    with write_transaction(db, name="photo_source_cancel"):
        source = _photo_source(db, source_id)
        service.cancel(source)
    photo_imports.worker.wake()
    return service.serialize(source, db)


def delete_photo_source(
    source_id: str, _admin: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
) -> Response:
    with write_transaction(db, name="photo_source_delete"):
        photo_imports.PhotoImportService(db).delete(_photo_source(db, source_id))
    photo_imports.worker.wake()
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    app.put("/api/photos/import", response_model=PhotoResponse, status_code=201)(import_photo)
    app.get("/api/photos", response_model=PhotosPage)(list_photos)
    app.patch("/api/photos/{photo_id}", response_model=PhotoResponse)(patch_photo)
    app.post("/api/photos/{photo_id}/restore", response_model=PhotoResponse)(restore_photo)
    app.api_route("/api/photos/{photo_id}/image", methods=["GET", "HEAD"])(photo_image)
    app.api_route("/api/photos/{photo_id}/original", methods=["GET", "HEAD"])(photo_original)
    app.get("/api/photo-albums", response_model=PhotoAlbumsPage)(list_albums)
    app.post("/api/photo-albums", response_model=PhotoAlbumResponse, status_code=201)(create_album)
    app.patch("/api/photo-albums/{album_id}", response_model=PhotoAlbumResponse)(patch_album)
    app.delete("/api/photo-albums/{album_id}", status_code=204)(delete_album)
    app.put("/api/photo-albums/{album_id}/photos/{photo_id}", response_model=PhotoAlbumResponse)(add_album_photo)
    app.delete("/api/photo-albums/{album_id}/photos/{photo_id}", status_code=204)(remove_album_photo)
    app.get("/api/admin/photo-sources", response_model=PhotoSourcesPage)(list_photo_sources)
    app.post("/api/admin/photo-sources", response_model=PhotoSourceResponse, status_code=201)(create_photo_source)
    app.post("/api/admin/photo-sources/{source_id}/scan", response_model=PhotoSourceResponse, status_code=202)(scan_photo_source)
    app.post("/api/admin/photo-sources/{source_id}/cancel", response_model=PhotoSourceResponse)(cancel_photo_source)
    app.delete("/api/admin/photo-sources/{source_id}", status_code=204)(delete_photo_source)
