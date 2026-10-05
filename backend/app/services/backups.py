"""Verifiable database backups.

A backup is ``backups/<name>.db`` (a consistent online copy made with SQLite's backup API) plus
``backups/<name>.json`` (its manifest). The manifest is written last, so a copy without one is an
incomplete backup and is never offered. Media files are NOT included: artifacts are stored as a
storage root plus a relative path, so a restore needs the same media mounts.
Restore is offline only — see ``app.restore``.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from app import db as db_module
from app.config import APP_VERSION, settings
from app.persistence import atomic_write

NAME = re.compile(r"^lumina-\d{8}T\d{6}Z-(manual|scheduled|pre-upgrade)$")
COUNTED_TABLES = ("users", "library_items", "media_artifacts", "library_notes", "playback_progress", "storage_roots")
SCHEDULE_INTERVAL = timedelta(days=1)
LOCK_FILENAME = ".lumina.lock"
_create_lock = threading.Lock()


class BackupError(RuntimeError):
    pass


def backup_root() -> Path:
    return settings.data_dir / "backups"


def backup_path(name: str) -> Path:
    """Resolve a backup name from a request; anything but the exact generated shape is refused."""
    if not NAME.fullmatch(name):
        raise BackupError("Unknown backup")
    path = backup_root() / f"{name}.db"
    if not path.is_file() or not path.with_suffix(".json").is_file():
        raise BackupError("Unknown backup")
    return path


def hold_server_lock() -> Any:
    """Shared lock held for the server's lifetime; the offline restore needs it exclusively."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(settings.data_dir / LOCK_FILENAME, "a")  # noqa: SIM115 - held until process exit
    fcntl.flock(handle, fcntl.LOCK_SH)
    return handle


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(kind: str = "manual") -> dict[str, Any]:
    if not _create_lock.acquire(blocking=False):
        raise BackupError("A backup is already being created")
    try:
        root = backup_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for stale in root.glob("*.partial"):  # an interrupted earlier attempt
            stale.unlink(missing_ok=True)
        now = datetime.now(UTC)
        name = f"lumina-{now:%Y%m%dT%H%M%S}Z-{kind}"
        target = root / f"{name}.db"
        if target.exists():
            raise BackupError("A backup was created a moment ago; try again")
        partial = root / f"{name}.db.partial"
        os.close(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        raw = db_module.engine.raw_connection()
        try:
            destination = sqlite3.connect(partial)
            try:
                raw.driver_connection.backup(destination)  # one consistent snapshot, writers keep going
                destination.execute("PRAGMA journal_mode=DELETE")  # self-contained single file
                # API keys never leave the live database; secure_delete zeroes the freed bytes too.
                # jellyfin_server_key is kept on purpose: a restore keeps the same ServerId for clients.
                destination.execute("PRAGMA secure_delete=ON")
                # A pre-upgrade backup snapshots the old schema before the media-vault columns exist.
                columns = {row[1] for row in destination.execute("PRAGMA table_info(app_settings)")}
                with destination:
                    destination.execute("UPDATE app_settings SET ai_api_key = NULL")
                    if "tmdb_api_key" in columns:
                        destination.execute("UPDATE app_settings SET tmdb_api_key = NULL")
                    if "smtp_password" in columns:  # 2.6.0 Requests: the SMTP password and Sonarr/Radarr keys too
                        destination.execute("UPDATE app_settings SET smtp_password = NULL")
                        destination.execute("UPDATE arr_servers SET api_key = NULL")
                counts = {table: destination.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in COUNTED_TABLES}  # noqa: S608
                schema_version = destination.execute("PRAGMA user_version").fetchone()[0]
            finally:
                destination.close()
        finally:
            raw.close()
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(partial, target)
        manifest = {
            "name": name,
            "kind": kind,
            "created_at": now.isoformat(),
            "app_version": APP_VERSION,
            "schema_version": schema_version,
            "size": target.stat().st_size,
            "sha256": _sha256(target),
            "counts": counts,
        }
        atomic_write(target.with_suffix(".json"), json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest
    finally:
        _create_lock.release()


def list_backups() -> list[dict[str, Any]]:
    """Complete backups only (copy + manifest), newest first."""
    backups = []
    for manifest_path in backup_root().glob("lumina-*.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("name") == manifest_path.stem and manifest_path.with_suffix(".db").is_file():
            backups.append(manifest)
    return sorted(backups, key=lambda item: item["created_at"], reverse=True)


def verify_file(path: Path) -> list[str]:
    """Problems with a backup copy against its manifest; an empty list means it is a valid recovery point."""
    try:
        manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["The manifest is missing or unreadable"]
    if _sha256(path) != manifest.get("sha256"):
        return ["The copy does not match its recorded checksum"]
    problems = []
    try:
        copy = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            integrity = copy.execute("PRAGMA integrity_check").fetchone()[0]
            version = copy.execute("PRAGMA user_version").fetchone()[0]
        finally:
            copy.close()
    except sqlite3.DatabaseError as exc:
        return [f"The copy cannot be opened as a database ({exc.__class__.__name__})"]
    if integrity != "ok":
        problems.append("SQLite integrity check failed")
    # An older version is a valid recovery point: startup upgrades it after restore.
    if not 1 <= version <= db_module.SCHEMA_VERSION or manifest.get("schema_version") != version:
        problems.append(f"Schema version {version} cannot be restored by this release ({db_module.SCHEMA_VERSION})")
    return problems


def delete_backup(name: str) -> None:
    path = backup_path(name)
    path.with_suffix(".json").unlink()  # manifest first: a half-deleted backup is never listed
    path.unlink(missing_ok=True)


def run_scheduled(keep: int, now: datetime | None = None) -> dict[str, Any] | None:
    """Daily backup plus retention of the newest ``keep`` scheduled ones; manual backups are never pruned."""
    now = now or datetime.now(UTC)
    scheduled = [item for item in list_backups() if item.get("kind") == "scheduled"]
    created = None
    if not scheduled or now - datetime.fromisoformat(scheduled[0]["created_at"]) >= SCHEDULE_INTERVAL:
        created = create_backup("scheduled")
        scheduled.insert(0, created)
    for stale in scheduled[keep:]:
        delete_backup(stale["name"])
    return created
