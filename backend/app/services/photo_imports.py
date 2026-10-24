"""Resumable, root-confined indexing for registered external photo folders."""
from __future__ import annotations

import hashlib
import logging
import os
import stat
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Iterator

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import StorageRoot, User, utcnow
from app.persistence import write_transaction
from app.photo_models import PhotoDerivative, PhotoItem
from app.photo_root_models import PhotoImportRun, PhotoImportSource
from app.services import photo_derivatives
from app.services.background_work import background_work
from app.services import photo_cache
from app.services.photo_files import PhotoFileChanged, PhotoFileUnavailable, PhotoSnapshotBusy, snapshot_external
from app.services.storage_roots import StorageRootService

BATCH_SIZE = 200
POLL_SECONDS = 900
MAX_SOURCE_WORKERS = 4
PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".avif", ".heic", ".heif"})
logger = logging.getLogger(__name__)


class PhotoImportError(ValueError):
    pass


class ScanIncomplete(RuntimeError):
    pass


class FileSkipped(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedPhoto:
    relative: str
    root_relative: str
    filename: str
    size: int
    mtime_ns: int
    unchanged: bool
    content_type: str | None = None
    content_hash: str | None = None
    facts: dict | None = None
    products: dict[int, tuple[str, int]] | None = None
    error_code: str | None = None


def normalize_folder(value: str | None) -> str:
    raw = (value or "").replace("\\", "/").strip("/")
    if "\x00" in raw:
        raise PhotoImportError("Invalid photo source folder.")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PhotoImportError("Photo source folder must be relative to its registered root.")
    return "" if raw in {"", "."} else path.as_posix()


def source_folder(root: StorageRoot, relative: str) -> Path:
    base = Path(root.path)
    folder = base.joinpath(*PurePosixPath(relative).parts) if relative else base
    if not folder.is_relative_to(base) or Path(os.path.realpath(folder)) != folder:
        raise PhotoImportError("Photo source folder is unavailable.")
    try:
        if not stat.S_ISDIR(os.lstat(folder).st_mode):
            raise PhotoImportError("Photo source folder is unavailable.")
    except OSError as exc:
        raise PhotoImportError("Photo source folder is unavailable.") from exc
    return folder


def folders_overlap(first: str, second: str) -> bool:
    left, right = PurePosixPath(first).parts, PurePosixPath(second).parts
    common = min(len(left), len(right))
    return left[:common] == right[:common]


def walk(folder: Path, after: str = "", parts: tuple[str, ...] = ()) -> Iterator[tuple[str, os.DirEntry]]:
    try:
        with os.scandir(folder.joinpath(*parts)) as listing:
            entries = sorted(listing, key=lambda entry: entry.name)
    except OSError as exc:
        raise ScanIncomplete("walk_failed") from exc
    for entry in entries:
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        relative = PurePosixPath(*parts, entry.name).as_posix()
        try:
            if entry.is_dir(follow_symlinks=False):
                yield from walk(folder, after, (*parts, entry.name))
            elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.casefold() in PHOTO_SUFFIXES and relative > after:
                yield relative, entry
        except OSError as exc:
            raise ScanIncomplete("walk_failed") from exc


def _run(db: Session, source: PhotoImportSource, trigger: str) -> PhotoImportRun:
    active = db.scalar(select(PhotoImportRun).where(
        PhotoImportRun.source_id == source.id, PhotoImportRun.state.in_(("queued", "running")),
    ).order_by(PhotoImportRun.created_at.desc()).limit(1))
    if active is not None:
        return active
    run = PhotoImportRun(id=str(uuid.uuid4()), source_id=source.id, state="queued", trigger=trigger)
    db.add(run)
    source.scan_requested, source.state, source.error_code = True, "queued", None
    db.flush()
    return run


class PhotoImportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, root_id: str, folder: str | None, owner: User, visibility: str) -> PhotoImportSource:
        root = self.db.get(StorageRoot, root_id)
        if root is None or root.mode != "external":
            raise PhotoImportError("Choose a registered external storage root.")
        relative = normalize_folder(folder)
        if not StorageRootService(self.db).is_online(root):
            raise PhotoImportError("The storage root is offline.")
        source_folder(root, relative)
        for existing in self.db.scalars(select(PhotoImportSource).where(PhotoImportSource.root_id == root.id)):
            if existing.relative_folder != relative and folders_overlap(existing.relative_folder, relative):
                raise PhotoImportError("That folder overlaps an existing photo source.")
        source = PhotoImportSource(
            id=str(uuid.uuid4()), root_id=root.id, relative_folder=relative,
            owner_user_id=owner.id, visibility=visibility, state="queued", scan_requested=True,
        )
        self.db.add(source)
        self.db.flush()
        _run(self.db, source, "manual")
        return source

    def scan(self, source: PhotoImportSource, trigger: str = "manual") -> PhotoImportRun:
        if not source.enabled:
            raise PhotoImportError("Photo source is disabled.")
        return _run(self.db, source, trigger)

    def cancel(self, source: PhotoImportSource) -> None:
        run = self.db.scalar(select(PhotoImportRun).where(
            PhotoImportRun.source_id == source.id, PhotoImportRun.state.in_(("queued", "running")),
        ).order_by(PhotoImportRun.created_at.desc()).limit(1))
        if run is not None:
            run.cancel_requested = True
        source.state = "canceling" if run is not None else "idle"

    def delete(self, source: PhotoImportSource) -> None:
        now = utcnow()
        self.cancel(source)
        self.db.query(PhotoItem).filter(
            PhotoItem.source_id == source.id, PhotoItem.deleted_at.is_(None),
        ).update({"deleted_at": now}, synchronize_session=False)
        self.db.delete(source)

    @staticmethod
    def serialize(source: PhotoImportSource, db: Session) -> dict:
        root = db.get(StorageRoot, source.root_id)
        return {
            "id": source.id, "root_id": source.root_id, "root_label": root.label if root else "Unavailable root",
            "relative_folder": source.relative_folder, "owner_user_id": source.owner_user_id,
            "visibility": source.visibility, "state": source.state, "enabled": source.enabled,
            "indexed_count": source.indexed_count, "error_code": source.error_code,
            "error_count": (
                db.scalar(select(PhotoImportRun.error_count).where(
                    PhotoImportRun.source_id == source.id,
                ).order_by(PhotoImportRun.created_at.desc(), PhotoImportRun.id.desc()).limit(1)) or 0
            ),
            "last_scan_at": source.last_scan_at, "last_completed_at": source.last_completed_at,
        }


def _prepare_one(source_id: str, relative: str, entry: os.DirEntry) -> PreparedPhoto:
    try:
        before = entry.stat(follow_symlinks=False)
        if before.st_size <= 0 or before.st_size > photo_derivatives.MAX_OUTPUT_BYTES * 16:
            raise FileSkipped("file_size")
        with session_scope() as db:
            source = db.get(PhotoImportSource, source_id)
            root = db.get(StorageRoot, source.root_id) if source else None
            if source is None or root is None:
                raise ScanIncomplete("source_removed")
            root_relative = PurePosixPath(source.relative_folder, relative).as_posix()
            photo = db.scalar(select(PhotoItem).where(
                PhotoItem.source_id == source.id, PhotoItem.relative_path == root_relative,
            ))
            unchanged = bool(photo and photo.source_size == before.st_size and photo.source_mtime_ns == before.st_mtime_ns)
            root_path = root.path
        prepared = PreparedPhoto(
            relative, root_relative, Path(relative).name, before.st_size, before.st_mtime_ns, unchanged,
        )
        if unchanged:
            return prepared
        work = photo_derivatives.photos_root() / ".snapshots"
        with snapshot_external(root_path, root_relative, work) as snapshot:
            fingerprint = snapshot.fingerprint
            if (fingerprint.size, fingerprint.mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise FileSkipped("file_changed")
            with snapshot.path.open("rb") as handle:
                content_type = photo_derivatives.sniff(handle.read(128))
            if content_type is None:
                raise FileSkipped("unsupported")
            facts = photo_derivatives.inspect(snapshot.path, content_type)
            products = photo_derivatives.render(snapshot.path, fingerprint.sha256)
            return PreparedPhoto(
                relative, root_relative, Path(relative).name, fingerprint.size, fingerprint.mtime_ns, False,
                content_type, fingerprint.sha256, facts, products,
            )
    except PhotoSnapshotBusy:
        raise
    except FileSkipped:
        raise
    except PhotoFileChanged as exc:
        raise FileSkipped("file_changed") from exc
    except (OSError, PhotoFileUnavailable, PhotoImportError, photo_derivatives.PhotoDecodeError) as exc:
        raise FileSkipped("decode_failed") from exc


def _still_current(root_path: str, prepared: PreparedPhoto) -> bool:
    """Rehash the secure source immediately before publishing prepared facts."""
    if prepared.unchanged:
        return True
    try:
        work = photo_derivatives.photos_root() / ".snapshots"
        with snapshot_external(
            root_path, prepared.root_relative, work, expected_sha256=prepared.content_hash,
        ) as snapshot:
            observed = snapshot.fingerprint
    except PhotoSnapshotBusy:
        raise
    except PhotoFileUnavailable:
        return False
    return (observed.sha256, observed.size, observed.mtime_ns) == (
        prepared.content_hash, prepared.size, prepared.mtime_ns,
    )


def _apply_prepared(
    db: Session, source: PhotoImportSource, run: PhotoImportRun, prepared: PreparedPhoto,
) -> tuple[str | None, set[str]]:
    photo = db.scalar(select(PhotoItem).where(
        PhotoItem.source_id == source.id, PhotoItem.relative_path == prepared.root_relative,
    ))
    if prepared.error_code is not None:
        return None, set()
    if prepared.unchanged:
        if photo is None:
            raise ScanIncomplete("catalog_changed")
        photo.source_seen_run_id, photo.missing_at = run.id, None
        return None, set()
    assert prepared.content_type and prepared.content_hash and prepared.facts and prepared.products
    duplicate = db.scalar(select(PhotoItem.id).where(
        PhotoItem.owner_user_id == source.owner_user_id, PhotoItem.sha256 == prepared.content_hash,
        PhotoItem.id != (photo.id if photo else ""),
    ))
    if duplicate is not None:
        # This path now contains bytes already represented by another item.
        # Leave an existing row unseen so the completed scan marks its old
        # identity missing instead of serving stale catalog facts for new bytes.
        return None, set()
    facts, products = prepared.facts, prepared.products
    obsolete: set[str] = set()
    if photo is None:
        photo = PhotoItem(
            id=str(uuid.uuid4()), owner_user_id=source.owner_user_id, visibility=source.visibility,
            storage_kind="external", storage_key=f"external:{source.root_id}:{hashlib.sha256(prepared.root_relative.encode()).hexdigest()}",
            root_id=source.root_id, source_id=source.id, relative_path=prepared.root_relative,
            original_filename=prepared.filename, content_type=prepared.content_type, sha256=prepared.content_hash,
            bytes=prepared.size, width=facts["width"], height=facts["height"],
        )
        db.add(photo)
        for width in photo_derivatives.WIDTHS:
            db.add(PhotoDerivative(
                id=str(uuid.uuid4()), photo_id=photo.id, width=width,
                source_key=products[width][0], bytes=products[width][1], state="ready",
            ))
    else:
        obsolete = {row.source_key for row in db.scalars(select(PhotoDerivative).where(
            PhotoDerivative.photo_id == photo.id,
        ))}
        photo.content_type, photo.sha256, photo.bytes = prepared.content_type, prepared.content_hash, prepared.size
        photo.width, photo.height = facts["width"], facts["height"]
        rows = {row.width: row for row in db.scalars(select(PhotoDerivative).where(PhotoDerivative.photo_id == photo.id))}
        for width in photo_derivatives.WIDTHS:
            derivative = rows.get(width)
            if derivative is None:
                derivative = PhotoDerivative(id=str(uuid.uuid4()), photo_id=photo.id, width=width, source_key=products[width][0])
                db.add(derivative)
            key, size = products[width]
            derivative.source_key, derivative.bytes, derivative.state, derivative.error = key, size, "ready", None
        obsolete.difference_update(key for key, _size in products.values())
    from app.services.photos import _captured, _clean
    photo.orientation = facts.get("orientation", 1)
    photo.captured_at, photo.captured_at_raw = _captured(facts.get("captured_at")), _clean(facts.get("captured_at_raw"), 64)
    photo.camera_make, photo.camera_model = _clean(facts.get("camera_make"), 120), _clean(facts.get("camera_model"), 120)
    photo.metadata_json = {key: value for key, value in (facts.get("metadata") or {}).items() if value is not None}
    photo.source_size, photo.source_mtime_ns = prepared.size, prepared.mtime_ns
    photo.source_seen_run_id, photo.missing_at = run.id, None
    db.flush()
    return photo.id, obsolete


class PhotoImportWorker:
    def __init__(self) -> None:
        self._stop, self._wake = threading.Event(), threading.Event()
        self._threads: list[threading.Thread] = []
        self._claim_lock = threading.Lock()
        self._current = threading.local()
        self._generation = 0

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads):
            return
        self._stop.clear()
        with session_scope() as db, write_transaction(db, name="photo_import_recover"):
            db.query(PhotoImportRun).filter(PhotoImportRun.state == "running").update({"state": "queued"})
        generation = self._generation
        self._threads = [
            threading.Thread(target=self._loop, args=(generation,), daemon=True, name=f"photo-import-worker-{index + 1}")
            for index in range(MAX_SOURCE_WORKERS)
        ]
        for thread in self._threads:
            thread.start()

    def wake(self) -> None:
        self._wake.set()

    def close(self) -> None:
        self._generation += 1
        self._stop.set(); self._wake.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads = []

    def _schedule_poll(self) -> None:
        if not background_work.backfill_allowed():
            return
        cutoff = utcnow() - timedelta(seconds=POLL_SECONDS)
        with session_scope() as db, write_transaction(db, name="photo_import_poll"):
            sources = list(db.scalars(select(PhotoImportSource).where(
                PhotoImportSource.enabled.is_(True),
                (PhotoImportSource.last_scan_at.is_(None) | (PhotoImportSource.last_scan_at < cutoff)),
            ).limit(10)))
            for source in sources:
                _run(db, source, "watch")

    def _active(self, generation: int | None) -> bool:
        return generation is None or generation == self._generation and not self._stop.is_set()

    def run_once(self, generation: int | None = None) -> bool:
        self._current.run_id = None
        if not self._active(generation):
            return False
        # Explicit Add/Scan requests are requested work: like promoted media
        # jobs they bypass ordinary HTTP quiet time but remain shutdown-owned.
        manual_allowed = not background_work.closed
        automatic_allowed = background_work.backfill_allowed()
        if not manual_allowed and not automatic_allowed:
            return False
        with self._claim_lock, session_scope() as db:
            clauses = [PhotoImportRun.state == "queued"]
            if not automatic_allowed:
                clauses.append(PhotoImportRun.trigger != "watch")
            run = db.scalar(select(PhotoImportRun).where(*clauses).order_by(
                case((PhotoImportRun.trigger == "watch", 1), else_=0),
                PhotoImportRun.created_at, PhotoImportRun.id,
            ).limit(1))
            if run is None:
                return False
            source = db.get(PhotoImportSource, run.source_id)
            now = utcnow()
            with write_transaction(db, name="photo_import_claim"):
                run.state, run.started_at = "running", run.started_at or now
                if source is not None:
                    source.last_scan_at = now
                    source.state, source.error_code = "scanning", None
            run_id = run.id
            self._current.run_id = run_id
        with session_scope() as db:
            run = db.get(PhotoImportRun, run_id)
            if run is None:
                return True
            source, root = db.get(PhotoImportSource, run.source_id), None
            if source is not None:
                root = db.get(StorageRoot, source.root_id)
            if source is None or root is None:
                with write_transaction(db, name="photo_import_orphan"):
                    run.state, run.finished_at = "failed", utcnow()
                return True
            online = StorageRootService(db).is_online(root)
            if not self._active(generation):
                return False
            if self._canceled(run_id):
                self._cancel(run_id)
                return True
            if not online:
                with write_transaction(db, name="photo_import_offline"):
                    run.state, run.finished_at = "failed", utcnow()
                    source.state, source.error_code = "offline", "root_offline"
                return True
            try:
                folder = source_folder(root, source.relative_folder)
            except PhotoImportError:
                with write_transaction(db, name="photo_import_folder"):
                    run.state, run.finished_at = "failed", utcnow()
                    source.state, source.error_code = "failed", "folder_unavailable"
                return True
            cursor = run.cursor

        if not self._active(generation):
            return False
        if self._canceled(run_id):
            self._cancel(run_id)
            return True
        prepared: list[PreparedPhoto] = []
        try:
            for relative, entry in walk(folder, cursor):
                automatic = run.trigger == "watch"
                while (
                    (not background_work.backfill_allowed() if automatic else background_work.closed)
                    and self._active(generation) and not self._stop.wait(0.5)
                ):
                    if self._canceled(run.id):
                        self._cancel(run.id)
                        return True
                if not self._active(generation):
                    return False
                if self._canceled(run.id):
                    self._cancel(run.id)
                    return True
                try:
                    prepared.append(_prepare_one(run.source_id, relative, entry))
                except FileSkipped as exc:
                    prepared.append(PreparedPhoto(
                        relative=relative, root_relative=relative, filename=Path(relative).name,
                        size=0, mtime_ns=0, unchanged=False, error_code=exc.code,
                    ))
                if len(prepared) >= BATCH_SIZE:
                    if not self._commit_batch(run.id, prepared, generation):
                        return True
                    prepared = []
            if prepared and not self._commit_batch(run.id, prepared, generation):
                return True
        except ScanIncomplete:
            if prepared and not self._commit_batch(run.id, prepared, generation):
                return True
            self._fail(run.id, "scan_incomplete")
            return True
        except PhotoSnapshotBusy:
            self._defer(run.id)
            # False makes the worker loop take its normal five-second wait;
            # capacity pressure must not become a hot durable-queue retry loop.
            return False
        if not self._active(generation):
            return False
        self._finish(run.id, generation)
        return True

    @staticmethod
    def _canceled(run_id: str) -> bool:
        with session_scope() as db:
            run = db.get(PhotoImportRun, run_id)
            return run is None or run.cancel_requested

    @staticmethod
    def _cancel(run_id: str) -> None:
        with session_scope() as db, write_transaction(db, name="photo_import_cancel"):
            run = db.get(PhotoImportRun, run_id)
            if run is None:
                return
            source = db.get(PhotoImportSource, run.source_id)
            run.state, run.finished_at = "canceled", utcnow()
            if source is not None:
                source.state, source.scan_requested = "idle", False

    @staticmethod
    def _fail(run_id: str, code: str) -> None:
        with session_scope() as db, write_transaction(db, name="photo_import_incomplete"):
            run = db.get(PhotoImportRun, run_id)
            if run is None or run.state != "running":
                return
            source = db.get(PhotoImportSource, run.source_id)
            run.state, run.finished_at = "failed", utcnow()
            run.error_count += 1
            if source is not None:
                source.state, source.error_code, source.scan_requested = "failed", code, False

    @staticmethod
    def _defer(run_id: str) -> None:
        """Return capacity-contended work to the durable queue without an error."""
        with session_scope() as db, write_transaction(db, name="photo_import_defer"):
            run = db.get(PhotoImportRun, run_id)
            if run is None:
                return
            source = db.get(PhotoImportSource, run.source_id)
            run.state = "queued"
            if source is not None:
                source.state, source.scan_requested = "queued", True

    def _commit_batch(
        self, run_id: str, batch: list[PreparedPhoto], generation: int | None = None,
    ) -> bool:
        changed: list[str] = []
        obsolete: set[str] = set()
        if not self._active(generation):
            return False
        with session_scope() as db:
            run = db.get(PhotoImportRun, run_id)
            source = db.get(PhotoImportSource, run.source_id) if run else None
            root = db.get(StorageRoot, source.root_id) if source else None
            if run is None or source is None or root is None:
                self._fail(run_id, "source_changed")
                return False
            batch = [
                prepared if prepared.error_code is not None or _still_current(root.path, prepared)
                else replace(
                    prepared, content_type=None, content_hash=None, facts=None, products=None,
                    error_code="source_changed",
                )
                for prepared in batch
            ]
        with session_scope() as db, write_transaction(db, name="photo_import_batch"):
            run = db.get(PhotoImportRun, run_id)
            source = db.get(PhotoImportSource, run.source_id) if run else None
            if not self._active(generation):
                return False
            if run is None or source is None or run.cancel_requested:
                if run is not None:
                    run.state, run.finished_at = "canceled", utcnow()
                if source is not None:
                    source.state, source.scan_requested = "idle", False
                return False
            for prepared in batch:
                outcome, replaced = _apply_prepared(db, source, run, prepared)
                run.seen_count += 1
                if prepared.error_code is not None:
                    run.error_count += 1
                if outcome is not None:
                    changed.append(outcome); run.indexed_count += 1
                obsolete.update(replaced)
                run.cursor = prepared.relative
        if obsolete:
            with session_scope() as db:
                referenced = set(db.scalars(select(PhotoDerivative.source_key).where(
                    PhotoDerivative.source_key.in_(obsolete),
                )))
            photo_cache.purge(obsolete - referenced)
        photo_cache.trim()
        if changed:
            from app.services import photo_faces
            with session_scope() as db:
                for photo_id in changed:
                    photo = db.get(PhotoItem, photo_id)
                    try:
                        photo_faces.queue_photo(db, photo, requested=False)
                    except photo_faces.FaceError:
                        pass
        return True

    def _finish(self, run_id: str, generation: int | None = None) -> None:
        if not self._active(generation):
            return
        with session_scope() as db:
            run = db.get(PhotoImportRun, run_id)
            source = db.get(PhotoImportSource, run.source_id) if run else None
            root = db.get(StorageRoot, source.root_id) if source else None
            if run is None or source is None or root is None:
                if run is not None and self._active(generation):
                    with write_transaction(db, name="photo_import_finish_orphan"):
                        run.state, run.finished_at = "failed", utcnow()
                return
            if not self._active(generation):
                return
            if run.cancel_requested:
                with write_transaction(db, name="photo_import_finish_cancel"):
                    if not self._active(generation):
                        return
                    run.state, run.finished_at = "canceled", utcnow()
                    source.state, source.scan_requested = "idle", False
                return
            if not StorageRootService(db).is_online(root):
                with write_transaction(db, name="photo_import_finish_offline"):
                    if not self._active(generation):
                        return
                    run.state, run.finished_at = "failed", utcnow()
                    source.state, source.error_code, source.scan_requested = "offline", "root_offline", False
                return
            now = utcnow()
            with write_transaction(db, name="photo_import_finish"):
                if not self._active(generation):
                    return
                if run.error_count:
                    count = db.scalar(select(func.count()).select_from(PhotoItem).where(
                        PhotoItem.source_id == source.id, PhotoItem.missing_at.is_(None),
                    )) or 0
                    run.state, run.finished_at = "failed", now
                    source.state, source.error_code, source.scan_requested = "failed", "partial_scan", False
                    source.indexed_count = count
                    return
                db.query(PhotoItem).filter(
                    PhotoItem.source_id == source.id,
                    (PhotoItem.source_seen_run_id.is_(None) | (PhotoItem.source_seen_run_id != run.id)),
                ).update({"missing_at": now}, synchronize_session=False)
                count = db.scalar(select(func.count()).select_from(PhotoItem).where(
                    PhotoItem.source_id == source.id, PhotoItem.missing_at.is_(None),
                )) or 0
                run.state, run.finished_at = "succeeded", now
                source.state, source.scan_requested = "idle", False
                source.last_completed_at, source.indexed_count = now, count

    def _loop(self, generation: int) -> None:
        while self._active(generation):
            try:
                self._schedule_poll()
            except Exception:
                if not self._active(generation):
                    return
                logger.error("External photo scan scheduling failed")
            if not self._run_guarded_once(generation):
                self._wake.wait(5)
                self._wake.clear()

    def _run_guarded_once(self, generation: int | None) -> bool:
        """Run one lane turn, terminalizing only that thread's claimed run."""
        try:
            return self.run_once(generation)
        except Exception:
            if not self._active(generation):
                return False
            # Never include exception text: filesystem errors can contain a private host path.
            logger.error("External photo scan failed")
            run_id = getattr(self._current, "run_id", None)
            if run_id is not None:
                self._fail(run_id, "scan_failed")
            return True
        finally:
            self._current.run_id = None


worker = PhotoImportWorker()
