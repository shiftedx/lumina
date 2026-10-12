"""Storage roots: admin-registered managed and read-only external directories.

A root is registered by canonical container path only. Probing never writes into
an external root; a managed root gets a Lumina-owned identity sentinel and a
dedicated probe subdirectory. A root whose identity cannot be confirmed (missing
mount, swapped disk, empty mount point) is not "online", so it is never mistaken
for an empty library.
"""
from __future__ import annotations

from hashlib import sha256
import os
import re
import stat
import uuid
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MediaArtifact, StorageRoot, StorageRuleSet, utcnow

MODES = ("managed", "external")
SENTINEL_NAME = ".lumina-root"
PROBE_DIRNAME = ".lumina-probe"
ONLINE_STATES = ("available", "low_space")


MOUNTINFO = Path("/proc/self/mountinfo")
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def _mount_of(path: Path, device: int) -> tuple[str, str, str] | None:
    """(fstype, source, root) of the mount ``path`` sits on: the deepest mount point above it on ``device``."""
    major, minor = os.major(device), os.minor(device)
    best: tuple[int, tuple[str, str, str]] | None = None
    try:
        lines = MOUNTINFO.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        fields, _, tail = line.partition(" - ")
        parts, rest = fields.split(), tail.split()
        if len(parts) < 5 or len(rest) < 2 or parts[2] != f"{major}:{minor}":
            continue
        point = _OCTAL_ESCAPE.sub(lambda match: chr(int(match[1], 8)), parts[4])
        if (path == Path(point) or Path(point) in path.parents) and (best is None or len(point) > best[0]):
            best = (len(point), (rest[0], rest[1], parts[3]))
    return best[1] if best else None


def external_identity(path: Path, device: int) -> str:
    """What is mounted at an external root, stable across remounts and reboots.

    A local filesystem's statvfs fsid comes from its UUID. A network filesystem reports fsid 0 and gets a new
    st_dev on every mount, so it is named by its mount instead: type, source and root, hashed to fit the column.
    """
    fsid = os.statvfs(path).f_fsid
    if fsid:
        return f"fsid:{fsid:x}"
    mount = _mount_of(path, device)
    if mount is None:
        return f"dev:{device}"
    return "mount:" + sha256("\0".join(mount).encode()).hexdigest()[:32]


class StorageRootError(ValueError):
    """Rejected root registration (surfaced as 422)."""


class StorageRootInUseError(RuntimeError):
    """Deregistration refused while artifacts still reference the root (409)."""


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or child.is_relative_to(parent)


def canonical_root_path(raw: str) -> Path:
    """Absolute, normalized, symlink-free path or StorageRootError.

    The realpath equality catches symlinks/aliases but not case-only
    aliases on case-insensitive filesystems; containers run on Linux.
    """
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        raise StorageRootError("Storage root must be an absolute container path.")
    if ".." in Path(raw).parts:
        raise StorageRootError("Storage root must not contain '..'.")
    normalized = Path(os.path.normpath(raw))
    if Path(os.path.realpath(normalized)) != normalized:
        raise StorageRootError("Storage root must not be or pass through a symlink.")
    return normalized


def mount_parents() -> list[Path]:
    return [Path(os.path.realpath(value.strip())) for value in settings.storage_mount_parents.split(",") if value.strip()]


class StorageRootService:
    def __init__(self, db: Session):
        self.db = db

    def list_roots(self) -> list[StorageRoot]:
        return self.db.query(StorageRoot).order_by(StorageRoot.created_at.asc(), StorageRoot.id.asc()).all()

    def get(self, root_id: str) -> StorageRoot | None:
        return self.db.get(StorageRoot, root_id)

    def library_root(self) -> StorageRoot:
        """The built-in managed root for Lumina's own Library folder, created on first use."""
        path = Path(os.path.realpath(settings.library_root))
        root = self.db.query(StorageRoot).filter_by(path=str(path)).one_or_none()
        if root is None:
            path.mkdir(parents=True, exist_ok=True)
            # Reuse the sentinel's ID: a rolled-back creation may already have written it.
            try:
                root_id = (path / SENTINEL_NAME).read_text(encoding="utf-8")[:36].strip()
            except OSError:
                root_id = ""
            if not root_id or self.db.get(StorageRoot, root_id) is not None:
                root_id = str(uuid.uuid4())
            root = StorageRoot(id=root_id, label="Library", path=str(path), mode="managed", enabled=True)
            self.db.add(root)
            self.probe(root)
            self.db.flush()
        return root

    def create(self, *, label: str, container_path: str, mode: str, enabled: bool = True, minimum_free_bytes: int = 0) -> StorageRoot:
        if mode not in MODES:
            raise StorageRootError("Mode must be 'managed' or 'external'.")
        path = canonical_root_path(container_path)
        if not any(_contains(parent, path) for parent in mount_parents()):
            raise StorageRootError("Storage root must be inside a configured mount parent (LUMINA_STORAGE_MOUNT_PARENTS).")
        data_dir = Path(os.path.realpath(settings.data_dir))
        if _contains(path, data_dir) or _contains(data_dir, path):
            raise StorageRootError("Storage root must not overlap Lumina's application data.")
        self._require_no_overlap(path)
        root = StorageRoot(
            id=str(uuid.uuid4()),
            label=label,
            path=str(path),
            mode=mode,
            enabled=enabled,
            minimum_free_bytes=minimum_free_bytes,
        )
        self.db.add(root)
        self.probe(root)
        self.db.flush()
        return root

    def update(self, root: StorageRoot, *, label: str | None = None, enabled: bool | None = None, minimum_free_bytes: int | None = None) -> StorageRoot:
        if label is not None:
            root.label = label
        if enabled is not None:
            root.enabled = enabled
        if minimum_free_bytes is not None:
            root.minimum_free_bytes = minimum_free_bytes
        self.db.flush()
        return root

    def artifact_counts(self, root_ids: list[str]) -> dict[str, int]:
        rows = (
            self.db.query(MediaArtifact.root_id, func.count(MediaArtifact.id))
            .filter(MediaArtifact.root_id.in_(root_ids))
            .group_by(MediaArtifact.root_id)
            .all()
        ) if root_ids else []
        return dict(rows)

    def delete(self, root: StorageRoot) -> None:
        """Deregister metadata only; the directory and its contents are never touched."""
        if self.db.query(MediaArtifact.id).filter_by(root_id=root.id).first() is not None:
            raise StorageRootInUseError("Storage root still holds registered media; it cannot be removed.")
        rule_set = self.db.get(StorageRuleSet, 1)
        if rule_set is not None and (rule_set.default_root_id == root.id or any(rule.get("target_root_id") == root.id for rule in rule_set.rules)):
            raise StorageRootInUseError("Storage rules still route media to this root; change them first.")
        self.db.delete(root)
        self.db.flush()

    def probe(self, root: StorageRoot) -> StorageRoot:
        """Full probe: may establish identity; writes only inside a managed root's probe dir."""
        root.observation = self._observe(root, verify=True)
        return root

    def is_online(self, root: StorageRoot) -> bool:
        """Read-only check that the root is enabled, mounted and still the same identity."""
        return root.enabled and self._observe(root, verify=False)["state"] in ONLINE_STATES

    def _require_no_overlap(self, path: Path) -> None:
        for existing in self.db.query(StorageRoot.path).all():
            other = Path(existing.path)
            if _contains(other, path) or _contains(path, other):
                raise StorageRootError("Storage root overlaps an existing root.")

    def _observe(self, root: StorageRoot, *, verify: bool) -> dict:
        path = Path(root.path)
        observation: dict = {
            "state": "available",
            "checked_at": utcnow().isoformat(),
            "free_bytes": None,
            "total_bytes": None,
            "identity_verified": False,
        }
        try:
            status = os.lstat(path)
            if not stat.S_ISDIR(status.st_mode):
                return {**observation, "state": "offline"}
            with os.scandir(path) as entries:  # bounded read: at most one entry
                next(entries, None)
        except (FileNotFoundError, NotADirectoryError):
            return {**observation, "state": "offline"}
        except PermissionError:
            return {**observation, "state": "permission_denied"}

        if root.mode == "external":
            identity = external_identity(path, status.st_dev)
            if root.identity is None and verify:
                root.identity = identity
            elif root.identity is not None and root.identity.isdigit() and root.identity == str(status.st_dev):
                root.identity = identity  # a pre-2.11 st_dev identity, upgraded while it still matches
            if root.identity != identity:
                return {**observation, "state": "identity_mismatch"}
        else:
            sentinel = path / SENTINEL_NAME
            try:
                found: str | None = sentinel.read_text(encoding="utf-8")[:64].strip()
            except FileNotFoundError:
                found = None
            except OSError:
                return {**observation, "state": "permission_denied"}
            try:
                if found is None and root.identity is None and verify:
                    sentinel.write_text(root.id, encoding="utf-8")
                    root.identity = found = root.id
                if found != root.id:
                    return {**observation, "state": "identity_mismatch"}
                if verify:
                    probe_dir = path / PROBE_DIRNAME
                    probe_dir.mkdir(exist_ok=True)
                    probe_file = probe_dir / uuid.uuid4().hex
                    probe_file.write_bytes(b"ok")
                    probe_file.unlink()
            except OSError:
                return {**observation, "state": "permission_denied"}

        observation["identity_verified"] = True
        try:
            fs = os.statvfs(path)
            observation["free_bytes"] = fs.f_bavail * fs.f_frsize
            observation["total_bytes"] = fs.f_blocks * fs.f_frsize
        except OSError:
            pass  # space metrics unavailable: reported as null, not as zero
        if root.mode == "managed" and observation["free_bytes"] is not None and observation["free_bytes"] < (root.minimum_free_bytes or 0):
            observation["state"] = "low_space"
        return observation
