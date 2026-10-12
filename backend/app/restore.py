"""Offline database restore: ``python -m app.restore <backup.db> [--data-dir DIR]``.

Refuses while a Lumina server holds the data directory, verifies the backup against its manifest,
revokes every session, Connected app and unused invitation/reset link in the restored copy (the AI
API key and TMDB key were never in it and must be re-entered), keeps the previous
database beside it as ``app.db.pre-restore-<timestamp>``, then swaps the copy in atomically.
Media files are not part of a backup; the same storage roots must be mounted at the same paths.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3

from app.config import settings
from app.services.backups import LOCK_FILENAME, verify_file


def restore(backup: Path, data_dir: Path) -> Path | None:
    """Swap ``backup`` in as the live database; returns where the previous database was kept."""
    problems = verify_file(backup)
    if problems:
        raise SystemExit("Backup failed verification: " + "; ".join(problems))
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / LOCK_FILENAME, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Lumina is running on this data directory. Stop it before restoring.") from None
        live = data_dir / settings.database_filename
        staged = live.with_name(live.name + ".restoring")
        shutil.copyfile(backup, staged)
        os.chmod(staged, 0o600)  # every password hash: as private as the backup it came from
        now = datetime.now(UTC)
        copy = sqlite3.connect(staged)
        try:
            # Bearer material captured before the backup must not work again after a restore.
            copy.execute("DELETE FROM app_sessions")
            if copy.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='device_tokens'").fetchone():
                copy.execute("DELETE FROM device_tokens")  # a v2 backup predates Connected apps and has no such table
            copy.execute("UPDATE account_tokens SET revoked_at = ? WHERE used_at IS NULL AND revoked_at IS NULL", (now.replace(tzinfo=None).isoformat(" "),))
            copy.commit()
        finally:
            copy.close()
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
        kept = None
        if live.exists():
            kept = live.with_name(f"{live.name}.pre-restore-{now:%Y%m%dT%H%M%SZ}")
            # A leftover WAL belongs to the old database; it must never be replayed into the restored one.
            for suffix in ("-wal", "-shm"):
                sidecar = live.with_name(live.name + suffix)
                if sidecar.exists():
                    os.replace(sidecar, kept.with_name(kept.name + suffix))
            os.replace(live, kept)
        os.replace(staged, live)
        return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("backup", type=Path, help="a backups/lumina-*.db file (its .json manifest must sit beside it)")
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    args = parser.parse_args()
    kept = restore(args.backup, args.data_dir)
    print(f"Restored {args.backup.name}. All sessions, Connected apps and unused invitation/reset links were revoked; re-enter the AI and TMDB API keys (backups never hold them).")
    if kept:
        print(f"The previous database was kept as {kept}.")


if __name__ == "__main__":
    main()
