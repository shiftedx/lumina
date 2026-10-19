"""Measure directory enumeration while the real import service advances a flat root.

The fixture uses empty ``.mp4`` files in a temporary external root and an isolated
SQLite database. It does not read real media or contact the network. The optional
budget turns the output into a focused regression check.

Run: mise exec -- backend/.venv/bin/python scripts/perf/audit_library_discovery.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


class CountingScandir:
    def __init__(self, inner, *, counted: bool, counts: Counter):  # noqa: ANN001
        self.inner = inner
        self.counted = counted
        self.counts = counts

    def __enter__(self):  # noqa: ANN204
        self.inner.__enter__()
        return self

    def __exit__(self, *args):  # noqa: ANN002, ANN204
        return self.inner.__exit__(*args)

    def __iter__(self):  # noqa: ANN204
        return self

    def __next__(self):  # noqa: ANN204
        entry = next(self.inner)
        if self.counted:
            self.counts["enumerated_entries"] += 1
        return entry

    def close(self) -> None:
        self.inner.close()


def measure(count: int) -> dict:
    with TemporaryDirectory(prefix="lumina-discovery-audit-") as temporary:
        temporary_path = Path(temporary)
        data = temporary_path / "data"
        media = temporary_path / "media"
        data.mkdir()
        media.mkdir()
        for index in range(count):
            (media / f"media-{index:06d}.mp4").touch()

        # Import after selecting the isolated data directory: app.db creates its
        # engine when imported.
        os.environ["LUMINA_DATA_DIR"] = str(data)
        from sqlalchemy import event
        from app import db as db_module
        from app.models import LibraryItem, StorageRoot, User
        from app.services import library_import

        db_module.Base.metadata.create_all(db_module.engine)
        root = StorageRoot(id="audit-root", label="Audit", path=str(media), mode="external", enabled=True)
        user = User(id="audit-user", username="audit", display_name="Audit", role="admin", is_active=True)
        counts: Counter = Counter()
        statements: Counter = Counter()
        real_scandir = os.scandir

        def counted_scandir(path):  # noqa: ANN001, ANN202
            counted = Path(path) == media
            if counted:
                counts["directory_listings"] += 1
            return CountingScandir(real_scandir(path), counted=counted, counts=counts)

        def count_statement(_connection, _cursor, statement, _parameters, _context, _executemany):  # noqa: ANN001
            statements[statement.lstrip().partition(" ")[0].upper()] += 1

        library_import.after_import_hooks = []
        with db_module.SessionLocal() as db:
            db.add_all([root, user])
            db.commit()
            service = library_import.LibraryImportService(db)
            run = service.start(root.id, user.id)
            db.commit()
            event.listen(db_module.engine, "before_cursor_execute", count_statement)
            try:
                with patch.object(library_import.os, "scandir", counted_scandir):
                    steps = 0
                    while True:
                        steps += 1
                        if service.step(run.id):
                            break
            finally:
                event.remove(db_module.engine, "before_cursor_execute", count_statement)
            run = db.get(type(run), run.id)
            indexed = db.query(LibraryItem).count()

        assert indexed == count, (indexed, count)
        assert run.counters["candidate_completed"] == run.counters["candidate_total"] == count
        enumerated = counts["enumerated_entries"]
        return {
            "candidates": count,
            "batch_size": library_import.BATCH_SIZE,
            "service_steps": steps,
            "directory_listings": counts["directory_listings"],
            "enumerated_entries": enumerated,
            "enumeration_factor": enumerated / count,
            "sql_statements": dict(sorted(statements.items())),
            "scope": "real importer advancement over an isolated flat external root and SQLite database",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, choices=range(1, 10001), default=1000, metavar="1..10000")
    parser.add_argument("--max-enumeration-factor", type=float)
    args = parser.parse_args()
    result = measure(args.files)
    print(json.dumps(result, indent=2))
    return int(args.max_enumeration_factor is not None and result["enumeration_factor"] > args.max_enumeration_factor)


if __name__ == "__main__":
    raise SystemExit(main())
