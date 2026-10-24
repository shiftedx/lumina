#!/usr/bin/env python3
"""Benchmark the production durable-preview registry against the prior LRU.

Pressure trials use real WebP files and production ``photo_derivatives`` rows.
The scale trial bulk-loads production registry rows, then times the real
reserve/publish/release/recover service operations. It creates no million-file
tree.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Iterator

from PIL import Image
from sqlalchemy import create_engine, event, select

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app import db as db_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.photo_models import PhotoDerivative  # noqa: E402
from app.photo_preview_models import PhotoPreviewAsset, PhotoPreviewStorage  # noqa: E402
from app.services import photo_cache, photo_derivatives  # noqa: E402


def _webp() -> bytes:
    pixels = random.Random(1).randbytes(256 * 256 * 3)
    output = io.BytesIO()
    Image.frombytes("RGB", (256, 256), pixels).save(output, format="WEBP", quality=100)
    return output.getvalue()


WEBP = _webp()


def _revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT,
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _load_baseline(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("lumina_preview_cache_baseline", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load baseline module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine(path: Path):  # noqa: ANN202
    engine = create_engine(f"sqlite:///{path}", future=True)
    event.listens_for(engine, "connect")(db_module.configure_sqlite_connection)
    return engine


def _database(path: Path):  # noqa: ANN202
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = _engine(path)
    PhotoDerivative.__table__.create(engine)
    PhotoPreviewAsset.__table__.create(engine)
    PhotoPreviewStorage.__table__.create(engine)
    db_module.engine = engine
    db_module.SessionLocal.configure(bind=engine)
    return engine


def _configure(data_dir: Path, *, quota: int) -> None:
    settings.data_dir = data_dir
    settings.photo_derivative_cache_max_bytes = quota
    settings.photo_preview_min_free_bytes = 0


def _write_preview(key: str, *, order: int) -> None:
    path = photo_derivatives.derivative_path(key)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(WEBP)
    timestamp = 1_700_000_000_000_000_000 + order
    os.utime(path, ns=(timestamp, timestamp))


def _keys(count: int) -> list[str]:
    return [f"{index:064x}" for index in range(count)]


def _seed_references(engine, keys: list[str]) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        connection.execute(PhotoDerivative.__table__.insert(), [
            {
                "id": f"ref-{index}", "photo_id": f"photo-{index}", "width": 400,
                "source_key": key, "content_type": "image/webp", "state": "ready", "bytes": len(WEBP),
                "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1),
            }
            for index, key in enumerate(keys)
        ])


class EnumerationCount:
    def __init__(self) -> None:
        self.calls = 0
        self.entries = 0


@contextmanager
def _count_derivative_enumeration() -> Iterator[EnumerationCount]:
    """Count Path enumeration rooted in the derivative tree."""
    root = (photo_derivatives.photos_root() / "derivatives").resolve(strict=False)
    original_iterdir, original_glob, original_rglob = Path.iterdir, Path.glob, Path.rglob
    count = EnumerationCount()

    def inside(path: Path) -> bool:
        return path.resolve(strict=False).is_relative_to(root)

    def iterdir(path: Path):  # noqa: ANN202
        measured = inside(path)
        if measured:
            count.calls += 1
        for entry in original_iterdir(path):
            if measured:
                count.entries += 1
            yield entry

    def glob(path: Path, pattern: str):  # noqa: ANN202
        measured = inside(path)
        if measured:
            count.calls += 1
        for entry in original_glob(path, pattern):
            if measured:
                count.entries += 1
            yield entry

    def rglob(path: Path, pattern: str):  # noqa: ANN202
        measured = inside(path)
        if measured:
            count.calls += 1
        for entry in original_rglob(path, pattern):
            if measured:
                count.entries += 1
            yield entry

    Path.iterdir, Path.glob, Path.rglob = iterdir, glob, rglob
    try:
        yield count
    finally:
        Path.iterdir, Path.glob, Path.rglob = original_iterdir, original_glob, original_rglob


def _summary(values: list[float]) -> dict[str, float | list[float]]:
    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "raw_ms": [round(value, 6) for value in values],
        "median_ms": round(statistics.median(values), 6),
        "p95_ms": round(p95, 6),
    }


def _legacy_retention_trial(root: Path, baseline: ModuleType, count: int) -> dict[str, int | float]:
    data_dir = root / "data"
    engine = _database(root / "catalog.db")
    keys = _keys(count)
    referenced = keys[: count // 2]
    quota = max(32 * 1024 * 1024, len(WEBP) * len(referenced))
    _configure(data_dir, quota=quota)
    _seed_references(engine, referenced)
    for order, key in enumerate(keys):
        _write_preview(key, order=order)
    baseline._known_total = None  # noqa: SLF001
    baseline._last_trim = 0.0  # noqa: SLF001
    started = time.perf_counter()
    with _count_derivative_enumeration() as enumeration:
        baseline.trim(force=True)
    elapsed = (time.perf_counter() - started) * 1000
    missing = sum(not photo_derivatives.derivative_path(key).is_file() for key in referenced)
    engine.dispose()
    return {
        "configured_quota_bytes": quota,
        "trim_ms": round(elapsed, 6),
        "referenced_missing": missing,
        "required_redecodes": missing,
        "filesystem_enumeration_calls": enumeration.calls,
        "filesystem_entries_enumerated": enumeration.entries,
    }


def _durable_retention_trial(root: Path, count: int) -> dict[str, object]:
    data_dir = root / "data"
    database = root / "catalog.db"
    engine = _database(database)
    keys = _keys(count)
    referenced = keys[: count // 2]
    quota = max(32 * 1024 * 1024, len(WEBP) * len(referenced))
    _configure(data_dir, quota=quota)
    _seed_references(engine, referenced)
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(PhotoPreviewAsset.__table__.insert(), [
            {
                "source_key": key, "content_hash": hashlib.sha256(key.encode()).hexdigest(),
                "width": 400, "state": "ready", "bytes": len(WEBP), "reserved_bytes": 0,
                "created_at": now, "updated_at": now,
            }
            for key in keys
        ])
        connection.execute(PhotoPreviewStorage.__table__.insert().values(
            id=1, used_bytes=len(WEBP) * count, reserved_bytes=0, updated_at=now,
        ))
    for order, key in enumerate(keys):
        _write_preview(key, order=order)

    started = time.perf_counter()
    with _count_derivative_enumeration() as enumeration:
        for _ in range(math.ceil((count - len(referenced)) / photo_cache.RECLAIM_BATCH)):
            photo_cache.trim(force=True)
    trim_ms = (time.perf_counter() - started) * 1000
    backpressure_code = None
    try:
        with photo_cache.reserve_set(hashlib.sha256(b"capacity-probe").hexdigest()):
            pass
    except photo_cache.PhotoStorageFull as exc:
        backpressure_code = exc.code

    engine.dispose()
    restarted = _engine(database)
    db_module.engine = restarted
    db_module.SessionLocal.configure(bind=restarted)
    started = time.perf_counter()
    with _count_derivative_enumeration() as recovery_enumeration:
        photo_cache.recover()
    recover_ms = (time.perf_counter() - started) * 1000
    with restarted.connect() as connection:
        reference_keys = list(connection.scalars(select(PhotoDerivative.source_key)))
        storage = connection.execute(select(
            PhotoPreviewStorage.used_bytes, PhotoPreviewStorage.reserved_bytes,
        ).where(PhotoPreviewStorage.id == 1)).one()
    missing = sum(not photo_derivatives.derivative_path(key).is_file() for key in reference_keys)
    restarted.dispose()
    return {
        "configured_quota_bytes": quota,
        "trim_ms": round(trim_ms, 6),
        "recover_ms": round(recover_ms, 6),
        "referenced_missing_after_restart": missing,
        "required_redecodes": missing,
        "backpressure_error_code": backpressure_code,
        "restart_used_bytes": storage.used_bytes,
        "restart_reserved_bytes": storage.reserved_bytes,
        "trim_filesystem_enumeration_calls": enumeration.calls,
        "trim_filesystem_entries_enumerated": enumeration.entries,
        "recover_filesystem_enumeration_calls": recovery_enumeration.calls,
        "recover_filesystem_entries_enumerated": recovery_enumeration.entries,
    }


def retention(root: Path, baseline: ModuleType, count: int, trials: int) -> dict[str, object]:
    legacy = [_legacy_retention_trial(root / f"legacy-{index}", baseline, count) for index in range(trials)]
    durable = [_durable_retention_trial(root / f"durable-{index}", count) for index in range(trials)]
    return {
        "actual_files_per_trial": count,
        "actual_catalog_references_per_trial": count // 2,
        "legacy_lru": {
            "implementation": "archived production photo_cache.py",
            "trim": _summary([float(row["trim_ms"]) for row in legacy]),
            "raw_trials": legacy,
        },
        "durable_registry": {
            "implementation": "working-tree app.services.photo_cache",
            "trim": _summary([float(row["trim_ms"]) for row in durable]),
            "recover": _summary([float(row["recover_ms"]) for row in durable]),
            "raw_trials": durable,
        },
    }


def _bulk_registry(engine, assets: int) -> float:  # noqa: ANN001
    now = "2026-01-01 00:00:00"
    statement = (
        "INSERT INTO photo_preview_assets "
        "(source_key, content_hash, width, state, bytes, reserved_bytes, reservation_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'ready', ?, 0, NULL, ?, ?)"
    )
    reference_statement = (
        "INSERT INTO photo_derivatives "
        "(id, photo_id, width, source_key, content_type, state, bytes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'image/webp', 'ready', ?, ?, ?)"
    )
    started = time.perf_counter()
    with engine.begin() as connection:
        raw = connection.connection.driver_connection
        for first in range(0, assets, 10_000):
            rows = [
                (
                    f"{index:064x}", hashlib.sha256(f"asset-{index}".encode()).hexdigest(),
                    photo_derivatives.WIDTHS[index % len(photo_derivatives.WIDTHS)], 100 + index % 17, now, now,
                )
                for index in range(first, min(first + 10_000, assets))
            ]
            raw.executemany(statement, rows)
            raw.executemany(reference_statement, [
                (
                    f"scale-ref-{index}", f"scale-photo-{index}",
                    photo_derivatives.WIDTHS[index % len(photo_derivatives.WIDTHS)],
                    f"{index:064x}", 100 + index % 17, now, now,
                )
                for index in range(first, min(first + 10_000, assets))
            ])
        raw.execute(
            "INSERT INTO photo_preview_storage (id, used_bytes, reserved_bytes, updated_at) "
            "SELECT 1, coalesce(sum(bytes), 0), 0, ? FROM photo_preview_assets",
            (now,),
        )
    return time.perf_counter() - started


def indexed_publication(root: Path, assets: int, trials: int) -> dict[str, object]:
    data_dir = root / "data"
    database = root / "catalog.db"
    engine = _database(database)
    _configure(data_dir, quota=0)
    seed_seconds = _bulk_registry(engine, assets)
    reserve, publish, release, recover, trim = [], [], [], [], []
    enumeration_rows: list[dict[str, int]] = []
    cursor_advances: list[int] = []
    for trial in range(trials):
        content_hash = hashlib.sha256(f"publication-{trial}".encode()).hexdigest()
        context = photo_cache.reserve_set(content_hash)
        with _count_derivative_enumeration() as enumeration:
            started = time.perf_counter()
            reservation = context.__enter__()
            reserve.append((time.perf_counter() - started) * 1000)
            staging = data_dir / f"benchmark-staging-{trial}"
            staging.mkdir(parents=True)
            products = {}
            for width, key in reservation.keys.items():
                path = staging / f"{width}.webp"
                path.write_bytes(WEBP)
                products[width] = (path, key, len(WEBP))
            started = time.perf_counter()
            installed = photo_cache.publish_set(reservation, products)
            publish.append((time.perf_counter() - started) * 1000)
            context.__exit__(None, None, None)
            started = time.perf_counter()
            photo_cache.release_publication({key for key, _size in installed.values()})
            release.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            photo_cache.recover()
            recover.append((time.perf_counter() - started) * 1000)
            with engine.connect() as connection:
                before = connection.scalar(select(PhotoPreviewStorage.sweep_after).where(PhotoPreviewStorage.id == 1))
            started = time.perf_counter()
            photo_cache.trim(force=True)
            trim.append((time.perf_counter() - started) * 1000)
            with engine.connect() as connection:
                after = connection.scalar(select(PhotoPreviewStorage.sweep_after).where(PhotoPreviewStorage.id == 1))
            if after is None:
                cursor_advances.append(0)
            else:
                cursor_advances.append(int(after, 16) - (int(before, 16) if before is not None else -1))
        enumeration_rows.append({"calls": enumeration.calls, "entries": enumeration.entries})

    with engine.connect() as connection:
        storage = connection.execute(select(
            PhotoPreviewStorage.used_bytes, PhotoPreviewStorage.reserved_bytes,
        ).where(PhotoPreviewStorage.id == 1)).one()
        plan = [row[-1] for row in connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT source_key FROM photo_preview_assets "
            "WHERE state = 'ready' AND reservation_id IS NULL AND source_key > ? "
            "ORDER BY source_key LIMIT 256",
            ("",),
        )]
    engine.dispose()
    return {
        "registry_assets": assets,
        "catalog_references": assets,
        "seed_seconds": round(seed_seconds, 6),
        "database_files_bytes": sum(
            candidate.stat().st_size
            for candidate in (database, Path(f"{database}-wal"), Path(f"{database}-shm"))
            if candidate.exists()
        ),
        "reserve": _summary(reserve),
        "publish": _summary(publish),
        "release": _summary(release),
        "recover": _summary(recover),
        "trim_referenced_page": _summary(trim),
        "raw_reclaim_cursor_assets_advanced": cursor_advances,
        "reclaim_query_plan": plan,
        "raw_derivative_filesystem_enumeration": enumeration_rows,
        "restart_used_bytes": storage.used_bytes,
        "restart_reserved_bytes": storage.reserved_bytes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=int, default=1_000_000)
    parser.add_argument("--files", type=int, default=1000)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument(
        "--baseline", type=Path,
        default=REPOSITORY_ROOT / "output/photo-pipeline-baseline/backend/app/services/photo_cache.py",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.assets < 3 or args.files < 2 or args.files % 2 or args.trials < 1:
        parser.error("--assets >= 3, --files >= 2 and even, and --trials >= 1 are required")
    baseline_path = args.baseline.resolve()
    baseline = _load_baseline(baseline_path)
    with tempfile.TemporaryDirectory(prefix="lumina-photo-preview-") as temporary:
        root = Path(temporary)
        result = {
            "versions": {
                "git_revision": _revision(),
                "production_photo_cache_sha256": hashlib.sha256(Path(photo_cache.__file__).read_bytes()).hexdigest(),
                "baseline_path": str(
                    baseline_path.relative_to(REPOSITORY_ROOT)
                    if baseline_path.is_relative_to(REPOSITORY_ROOT) else baseline_path
                ),
                "baseline_photo_cache_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            },
            "parameters": {"assets": args.assets, "files": args.files, "trials": args.trials},
            "retention": retention(root / "retention", baseline, args.files, args.trials),
            "indexed_publication": indexed_publication(root / "scale", args.assets, args.trials),
        }
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
