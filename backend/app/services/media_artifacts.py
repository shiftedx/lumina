"""Media artifact registry: every served file is a root + relative path.

Library entries link to artifacts; media is opened only through this registry,
re-validated at access time, never from a raw stored absolute path.
"""
from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import settings
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, StorageRoot
from app.services.storage_roots import StorageRootService

RESERVED_PREFIX = ".lumina-"


def artifact_file(root_path: str | Path, relative_path: str) -> Path:
    """The artifact's regular file, refusing symlinks anywhere below the root.

    Validated by path, then reopened by the caller (FileResponse);
    upgrade to descriptor-relative O_NOFOLLOW streaming if roots become
    writable by untrusted parties.
    """
    root = Path(root_path)
    candidate = root / relative_path
    if Path(os.path.realpath(candidate)) != candidate or not candidate.is_relative_to(root):
        raise FileNotFoundError("Media file is not a plain file inside its storage root.")
    try:
        if not stat.S_ISREG(os.lstat(candidate).st_mode):
            raise FileNotFoundError("Media file is not a regular file.")
    except OSError as exc:
        raise FileNotFoundError("Media file is missing.") from exc
    return candidate


class MediaArtifactService:
    def __init__(self, db: Session):
        self.db = db

    def register_file(self, item: LibraryItem, raw_path: str) -> MediaArtifact | None:
        """Link the item to the artifact at raw_path; None when no registered root contains it."""
        path = Path(os.path.realpath(raw_path))
        root = self._root_containing(path)
        if root is None:
            return None
        relative = path.relative_to(root.path).as_posix()
        if relative == "." or relative.startswith(RESERVED_PREFIX):
            return None
        try:
            size: int | None = path.stat().st_size
        except OSError:
            size = None
        artifact = self.db.query(MediaArtifact).filter_by(root_id=root.id, relative_path=relative).one_or_none()
        if artifact is None:
            artifact = MediaArtifact(
                id=str(uuid.uuid4()),
                root_id=root.id,
                relative_path=relative,
                ownership=root.mode,
                owner_user_id=item.user_id,
                size=size,
            )
            self.db.add(artifact)
        else:
            # A fresh file written at a quarantined address supersedes the old copy.
            artifact.lifecycle = "available"
            artifact.quarantined_at = None
            artifact.size = size if size is not None else artifact.size
        link = self.db.get(LibraryItemArtifact, item.id)
        if link is None:
            self.db.add(LibraryItemArtifact(library_item_id=item.id, artifact_id=artifact.id))
        else:
            link.artifact_id = artifact.id
        self.db.flush()
        return artifact

    def artifact_for(self, item_id: str) -> tuple[MediaArtifact, StorageRoot] | None:
        row = (
            self.db.query(MediaArtifact, StorageRoot)
            .join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id)
            .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
            .filter(LibraryItemArtifact.library_item_id == item_id)
            .one_or_none()
        )
        return (row[0], row[1]) if row else None

    def locate(self, item: LibraryItem) -> tuple[Path, Path]:
        """(media file, root directory) for a playable item, or FileNotFoundError."""
        found = self.artifact_for(item.id)
        if found is None:
            raise FileNotFoundError("Library item has no registered media file.")
        artifact, root = found
        if artifact.lifecycle != "available" or not root.enabled:
            raise FileNotFoundError("Library item media is not available.")
        return artifact_file(root.path, artifact.relative_path), Path(root.path)

    def observe(self, items: Iterable[LibraryItem]) -> dict[str, tuple[bool | None, int | None]]:
        """Per item: (exists, size); exists is None when its root is offline (unknown, not missing)."""
        ids = [item.id for item in items]
        rows = (
            self.db.query(LibraryItemArtifact.library_item_id, MediaArtifact, StorageRoot)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
            .filter(LibraryItemArtifact.library_item_id.in_(ids))
            .all()
        ) if ids else []
        roots = StorageRootService(self.db)
        online: dict[str, bool] = {}
        observed: dict[str, tuple[bool | None, int | None]] = {}
        for item_id, artifact, root in rows:
            if root.id not in online:
                online[root.id] = roots.is_online(root)
            if not online[root.id]:
                observed[item_id] = (None, None)
                continue
            if artifact.lifecycle != "available":
                observed[item_id] = (False, None)
                continue
            try:
                path = artifact_file(root.path, artifact.relative_path)
                observed[item_id] = (True, path.stat().st_size)
            except (FileNotFoundError, OSError):
                observed[item_id] = (False, None)
        return observed

    def _root_containing(self, path: Path) -> StorageRoot | None:
        roots = self.db.query(StorageRoot).filter(StorageRoot.enabled.is_(True)).all()
        matches = [root for root in roots if path.is_relative_to(root.path)]
        if not matches:
            if not path.is_relative_to(os.path.realpath(settings.library_root)):
                return None
            library = StorageRootService(self.db).library_root()
            return library if library.enabled else None
        root = max(matches, key=lambda candidate: len(candidate.path))
        if root.identity is None:
            StorageRootService(self.db).probe(root)
        return root
