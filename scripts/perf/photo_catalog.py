"""Synthetic 100k-photo catalog measurement; never opens household data or image files.

mise exec -- backend/.venv/bin/python scripts/perf/photo_catalog.py --count 100000
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    args = parser.parse_args()
    if not 100 <= args.count <= 1_000_000:
        parser.error("count must be between 100 and 1000000")
    with tempfile.TemporaryDirectory(prefix="lumina-photo-measurement-") as temporary:
        os.environ["LUMINA_DATA_DIR"] = temporary
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
        from sqlalchemy import create_engine, event, insert
        from sqlalchemy.orm import Session
        from app.db import Base
        from app.models import User
        from app.photo_models import PhotoItem
        from app import photo_face_models  # noqa: F401 - register metadata
        from app.services.photos import PhotoService, _cursor_encode

        engine = create_engine(f"sqlite:///{temporary}/catalog.sqlite")
        Base.metadata.create_all(engine)
        now = datetime(2026, 10, 24, 12)
        with Session(engine, expire_on_commit=False) as db:
            user = User(id="photo-perf-owner", username="photo-perf-owner", display_name="Synthetic owner", role="admin", is_active=True)
            db.add(user)
            for start in range(0, args.count, 2000):
                db.execute(insert(PhotoItem), [{
                    "id": f"00000000-0000-4000-8000-{index:012d}", "owner_user_id": user.id, "visibility": "private",
                    "storage_kind": "managed", "storage_key": f"originals/00/00000000-0000-4000-8000-{index:012d}.png",
                    "original_filename": f"Synthetic landscape {index}.png", "content_type": "image/png", "sha256": f"{index:064x}",
                    "bytes": 100_000, "width": 1440, "height": 960, "orientation": 1,
                    "captured_at": now - timedelta(minutes=index), "created_at": now - timedelta(seconds=index), "updated_at": now,
                    "metadata_json": {},
                } for index in range(start, min(start + 2000, args.count))])
            db.commit()
            queries: list[tuple[str, object]] = []
            def record(_connection, _cursor, statement, parameters, _context, _many):
                queries.append((statement, parameters))
            event.listen(engine, "before_cursor_execute", record)
            service = PhotoService(db)
            measurements = {}
            for sort in ("captured", "added"):
                depth = int(args.count * 0.9)
                taken = now - timedelta(minutes=depth)
                created = now - timedelta(seconds=depth)
                cursor = _cursor_encode([sort, (taken if sort == "captured" else created).isoformat(), created.isoformat(), f"00000000-0000-4000-8000-{depth:012d}"])
                for label, page_cursor in (("first", None), ("deep", cursor)):
                    durations = []; counts = []; plan = []
                    for _trial in range(5):
                        queries.clear(); started = time.perf_counter()
                        rows, total, next_cursor = service.list(user, limit=100, cursor=page_cursor, sort=sort, q=None, favorite=None, album_id=None, person_id=None, camera=None, date_from=None, date_to=None, deleted=False)
                        payload = service.serialize_many(rows, user)
                        durations.append((time.perf_counter() - started) * 1000); counts.append(len(queries))
                        assert len(payload) == 100 and total == args.count and next_cursor
                        row_query = next(((sql, params) for sql, params in queries if sql.startswith("SELECT photo_items.") and "LIMIT" in sql), None)
                        if row_query:
                            plan = [entry[3] for entry in db.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + row_query[0], row_query[1])]
                    measurements[f"{sort}_{label}"] = {"median_ms": round(statistics.median(durations), 2), "max_ms": round(max(durations), 2), "sql_queries": max(counts), "plan": plan}
            print(json.dumps({"photos": args.count, "page_size": 100, "trials": 5, "deep_fraction": 0.9, "measurements": measurements}, indent=2))
        engine.dispose()


if __name__ == "__main__":
    main()
