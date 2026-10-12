"""Recoverable deletion of managed media.

The database changes first; bytes only move after the commit, into a quarantine
folder on the same root. The artifact's lifecycle is the journal: whatever the
filesystem did or failed to do, the retention sweep moves each file to where its
lifecycle says it belongs, so a crash in any phase leaves neither lost bytes nor
orphans. External artifacts are never moved or deleted.
"""
from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, StorageRoot, utcnow
from app.persistence import queue_after_commit, write_transaction
from app.services.media_artifacts import MediaArtifactService
from app.services.storage_roots import StorageRootService

QUARANTINE_DIRNAME = ".lumina-quarantine"


def _paths(root: StorageRoot, artifact: MediaArtifact) -> tuple[Path, Path]:
    return Path(root.path) / artifact.relative_path, Path(root.path) / QUARANTINE_DIRNAME / artifact.id


def _move(source: Path, destination: Path) -> None:
    """Rename a regular file without ever overwriting; a no-op when already settled."""
    try:
        if not stat.S_ISREG(os.lstat(source).st_mode) or os.path.lexists(destination):
            return
    except FileNotFoundError:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, destination)


class ArtifactQuarantineService:
    def __init__(self, db: Session):
        self.db = db

    def _managed(self, item: LibraryItem) -> tuple[MediaArtifact, StorageRoot]:
        found = MediaArtifactService(self.db).artifact_for(item.id)
        if found is None:
            raise FileNotFoundError("Library item has no registered media file.")
        artifact, root = found
        if artifact.ownership != "managed" or root.mode != "managed":
            raise PermissionError("Files in an external library are read-only in Lumina.")
        return artifact, root

    def delete_file(self, item: LibraryItem) -> None:
        """Drop this entry's reference; quarantine the bytes only when it was the last one."""
        artifact, root = self._managed(item)
        if artifact.lifecycle != "available":
            raise FileNotFoundError("This file is already deleted.")
        item.status = "missing"
        other_reference = (
            self.db.query(LibraryItemArtifact.library_item_id)
            .filter(LibraryItemArtifact.artifact_id == artifact.id, LibraryItemArtifact.library_item_id != item.id)
            .first()
        )
        if other_reference is not None:
            self.db.query(LibraryItemArtifact).filter_by(library_item_id=item.id).delete()
            self.db.flush()
            return
        artifact.lifecycle = "quarantined"
        artifact.quarantined_at = utcnow()
        self.db.flush()
        live, quarantined = _paths(root, artifact)
        queue_after_commit(self.db, lambda: _move(live, quarantined))

    def restore_file(self, item: LibraryItem) -> None:
        artifact, root = self._managed(item)
        if artifact.lifecycle != "quarantined":
            raise FileNotFoundError("This file is not in quarantine.")
        live, quarantined = _paths(root, artifact)
        if not (quarantined.is_file() or live.is_file()):
            raise FileNotFoundError("The quarantined copy is no longer available.")
        artifact.lifecycle = "available"
        artifact.quarantined_at = None
        item.status = "available"
        self.db.flush()
        queue_after_commit(self.db, lambda: _move(quarantined, live))

    def sweep(self, now: datetime | None = None) -> int:
        """Settle interrupted moves, purge expired quarantine, remove orphans. Returns files purged.

        Lists each root's whole quarantine folder per cycle; its size is
        bounded by deletions inside the retention window.
        """
        cutoff = (now or utcnow()) - timedelta(hours=settings.quarantine_retention_hours)
        roots = StorageRootService(self.db)
        purged = 0
        for root in self.db.query(StorageRoot).filter_by(mode="managed", enabled=True).all():
            if not roots.is_online(root):
                continue  # never act on a missing or swapped mount
            quarantined = self.db.query(MediaArtifact).filter_by(root_id=root.id, lifecycle="quarantined").all()
            for artifact in quarantined:  # a crash after commit left the file in place
                _move(*_paths(root, artifact))
            expired = [artifact.id for artifact in quarantined if artifact.quarantined_at and artifact.quarantined_at < cutoff]
            if expired:
                with write_transaction(self.db, name="artifact_quarantine_purge"):
                    self.db.query(LibraryItemArtifact).filter(LibraryItemArtifact.artifact_id.in_(expired)).delete(synchronize_session=False)
                    self.db.query(MediaArtifact).filter(MediaArtifact.id.in_(expired)).delete(synchronize_session=False)
            folder = Path(root.path) / QUARANTINE_DIRNAME
            try:
                entries = [entry for entry in os.scandir(folder) if entry.is_file(follow_symlinks=False)]
            except FileNotFoundError:
                continue
            known = {
                artifact.id: artifact
                for artifact in self.db.query(MediaArtifact).filter(MediaArtifact.id.in_([entry.name for entry in entries]))
            }
            for entry in entries:
                artifact = known.get(entry.name)
                if artifact is not None and artifact.lifecycle == "quarantined":
                    continue
                if artifact is not None and not os.path.lexists(_paths(root, artifact)[0]):
                    _move(Path(entry.path), _paths(root, artifact)[0])  # restore interrupted after commit
                    continue
                os.unlink(entry.path)  # purged, or superseded by a fresh download
                purged += 1
        return purged
