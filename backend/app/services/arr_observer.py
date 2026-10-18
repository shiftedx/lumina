"""Read-only Sonarr/Radarr observation, separate from request dispatch (ADR 0018)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import threading
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.db import session_scope
from app.models import ArrObservationState, ArrObservedItem, ArrObservedRecord, ArrServer, ImportRun, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot, User, utcnow
from app.persistence import write_transaction
from app.services.library import LibraryService
from app.services.redaction import redact
from app.services.requests.arr import ArrClient, ArrError
from app.services.requests.sync import RescanOffer, map_path, reconciliation_digest, rescan_folders

logger = logging.getLogger(__name__)
OBSERVATION_INTERVAL_SECONDS = 60
STALE_AFTER = timedelta(seconds=OBSERVATION_INTERVAL_SECONDS * 2)
DATASETS = ("queue", "history", "health", "calendar", "missing", "cutoff")
_flights_lock = threading.Lock()
_flights: set[str] = set()
RECORD_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def stale(server: ArrServer, state: ArrObservationState | None, *, now: Any = None) -> bool:
    now = now or utcnow()
    return (
        not server.enabled
        or state is None
        or state.last_error is not None
        or state.last_ok_at is None
        or now - state.last_ok_at > STALE_AFTER
    )


async def observer_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(sync_once)
        except Exception as exc:  # noqa: BLE001 - a later poll reconciles the independent server snapshot
            logger.warning("Arr observation loop failed: %s", type(exc).__name__)
        await asyncio.sleep(OBSERVATION_INTERVAL_SECONDS)


def sync_once(server_id: str | None = None) -> bool:
    """Refresh one enabled server, or every enabled server. Returns false when a requested refresh is in flight."""
    if server_id is not None:
        return _sync_server(server_id)
    with session_scope() as db:
        ids = list(db.scalars(select(ArrServer.id).where(ArrServer.enabled.is_(True))))
    for current_id in ids:
        _sync_server(current_id)
    return True


def _sync_server(server_id: str) -> bool:
    with _flights_lock:
        if server_id in _flights:
            return False
        _flights.add(server_id)
    try:
        with session_scope() as db:
            server = db.get(ArrServer, server_id)
            if server is None or not server.enabled:
                return True
            signature = _signature(server)
            client = ArrClient.of(server)
        try:
            observed = client.observation()
        except ArrError as exc:
            _record_failure(server_id, signature, str(exc))
            return True
        except (TypeError, ValueError, KeyError, AttributeError):
            _record_failure(server_id, signature, "The download server sent an unexpected reply")
            return True
        try:
            _replace_success(server_id, signature, observed)
        except Exception as exc:  # noqa: BLE001 - malformed upstream data must retain the prior complete snapshot
            logger.warning("Arr observation persistence failed: %s", type(exc).__name__)
            _record_failure(server_id, signature, "The download server sent an unexpected reply")
        return True
    finally:
        with _flights_lock:
            _flights.discard(server_id)


def _signature(server: ArrServer) -> tuple[Any, ...]:
    return server.kind, server.base_url, server.api_key, tuple(
        (mapping.get("remote"), mapping.get("local")) for mapping in server.path_mappings or [] if isinstance(mapping, dict)
    )


def _state(db: Session, server_id: str) -> ArrObservationState:
    state = db.get(ArrObservationState, server_id)
    if state is None:
        state = ArrObservationState(server_id=server_id)
        db.add(state)
    return state


def _record_failure(server_id: str, signature: tuple[Any, ...], error: str) -> None:
    with session_scope() as db:
        with write_transaction(db, name="arr_observation_failure"):
            server = db.get(ArrServer, server_id, populate_existing=True)
            if server is None or _signature(server) != signature:
                return
            state = _state(db, server_id)
            state.attempted_at, state.last_error = utcnow(), error[:256]


def _replace_success(server_id: str, signature: tuple[Any, ...], observed: dict[str, Any]) -> None:
    now = utcnow()
    rescans: list[RescanOffer] = []
    # Correlation is read-only and can be relatively expensive for a whole library. It deliberately happens before
    # writer admission; the signature is checked again inside the short replacement transaction below.
    with session_scope() as db:
        server = db.get(ArrServer, server_id)
        if server is None or not server.enabled or _signature(server) != signature:
            return
        inventory = observed["inventory"]
        rows = [_inventory_row(db, server, raw, now) for raw in inventory]
        records = [
            _record_row(server_id, dataset, raw, index, now)
            for dataset, values in observed["records"].items()
            for index, raw in enumerate(values)
        ]
        # Playable is not fully synchronized: registered series gain seasons and
        # movies gain replacements. The shared helper throttles each folder and
        # retries busy roots without bypassing first-import/confirmation guards.
        history = _history_by_item(observed["records"].get("history", []))
        rescans = [
            RescanOffer(local, _inventory_digest(server.kind, raw, history.get(row.upstream_id, [])), f"arr:{server.id}",
                        priority=0 if row.import_state == "pending_import" else 1)
            for (row, local), raw in zip(rows, inventory, strict=True)
            if row.has_file and row.import_state in ("pending_import", "registered") and local is not None
        ]
        items = [row for row, _ in rows]
    with session_scope() as db:
        with write_transaction(db, name="arr_observation_success"):
            server = db.get(ArrServer, server_id, populate_existing=True)
            if server is None or not server.enabled or _signature(server) != signature:
                return  # configuration changed while the remote call was outstanding
            db.execute(delete(ArrObservedItem).where(ArrObservedItem.server_id == server_id))
            db.execute(delete(ArrObservedRecord).where(ArrObservedRecord.server_id == server_id))
            db.add_all(items)
            db.add_all(records)
            state = _state(db, server_id)
            state.version = observed["version"][:64]
            state.capabilities = {"inventory": True, **{dataset: True for dataset in DATASETS}}
            state.summary = _summary(items, records)
            state.attempted_at = state.last_ok_at = now
            state.last_error = None
    rescan_folders(rescans)


def _history_by_item(records: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        identity = _int(record.get("movieId")) or _int(record.get("seriesId"))
        movie = record.get("movie")
        identity = identity or _int(movie.get("id")) if isinstance(movie, dict) else identity
        if identity is not None:
            grouped.setdefault(identity, []).append(record)
    return grouped


def _history_identity(record: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "eventType", "date", "movieId", "seriesId", "episodeId", "movieFileId", "episodeFileId", "downloadId")
    value = {key: record[key] for key in keys if record.get(key) is not None}
    data = record.get("data")
    if isinstance(data, dict):
        stable = ("movieFileId", "episodeFileId", "episodeFileIds", "droppedPath", "importedPath", "downloadClient")
        value["data"] = {key: data[key] for key in stable if data.get(key) is not None}
    return value


def _inventory_digest(kind: str, raw: dict[str, Any], history: list[dict[str, Any]]) -> str:
    base = {key: raw[key] for key in ("id", "hasFile", "sizeOnDisk") if raw.get(key) is not None}
    if kind == "radarr":
        movie_file = raw.get("movieFile")
        if isinstance(movie_file, dict):
            keys = ("id", "relativePath", "size", "dateAdded", "edition", "releaseGroup")
            base["movie_file"] = {key: movie_file[key] for key in keys if movie_file.get(key) is not None}
    else:
        seasons = []
        for season in raw.get("seasons") if isinstance(raw.get("seasons"), list) else []:
            if not isinstance(season, dict):
                continue
            stats = season.get("statistics") if isinstance(season.get("statistics"), dict) else {}
            seasons.append({
                **{key: season[key] for key in ("id", "seasonNumber", "monitored") if season.get(key) is not None},
                "statistics": {key: stats[key] for key in ("episodeFileCount", "sizeOnDisk")
                               if stats.get(key) is not None},
            })
        base["seasons"] = sorted(seasons, key=lambda value: (value.get("seasonNumber", -1), value.get("id", -1)))
    identities = [_history_identity(record) for record in history]
    base["history"] = sorted(
        identities, key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    )[:64]
    return reconciliation_digest(base)


def _inventory_row(db: Session, server: ArrServer, raw: dict[str, Any], now: Any) -> tuple[ArrObservedItem, str | None]:
    upstream_id = _int(raw.get("id"))
    title = _text(raw.get("title"), 512)
    if upstream_id is None or title is None:
        raise ArrError("The download server sent an unexpected reply")
    media_type = "movie" if server.kind == "radarr" else "tv"
    tmdb_id, tvdb_id = _provider_int(raw.get("tmdbId")), _provider_int(raw.get("tvdbId"))
    has_file = bool(raw.get("hasFile")) or any(
        (_int((season.get("statistics") or {}).get("episodeFileCount")) or 0) > 0
        for season in (raw.get("seasons") if isinstance(raw.get("seasons"), list) else []) if isinstance(season, dict)
    )
    local = map_path(_text(raw.get("path"), 4096), server.path_mappings)
    import_state = _import_state(db, media_type, tmdb_id, tvdb_id, local)
    details = _inventory_details(raw, media_type)
    return ArrObservedItem(
        server_id=server.id, upstream_id=upstream_id, media_type=media_type, tmdb_id=tmdb_id, tvdb_id=tvdb_id, title=title,
        year=_int(raw.get("year")), monitored=bool(raw.get("monitored")), has_file=has_file,
        import_state=import_state, details=details, observed_at=now,
    ), local


def _import_state(db: Session, media_type: str, tmdb_id: int | None, tvdb_id: int | None, local: str | None) -> str:
    if _registered(db, media_type, tmdb_id, tvdb_id):
        return "registered"
    if local is None:
        return "unmapped"
    folder = PurePosixPath(local)
    roots = [root for root in db.scalars(select(StorageRoot).where(StorageRoot.mode == "external", StorageRoot.enabled.is_(True)))
             if folder == PurePosixPath(root.path) or PurePosixPath(root.path) in folder.parents]
    if not roots:
        return "root_unavailable"
    root = max(roots, key=lambda item: len(item.path))
    latest = db.scalars(select(ImportRun).where(ImportRun.root_id == root.id).order_by(ImportRun.created_at.desc())).first()
    if latest is not None and latest.state == "needs_confirmation":
        return "awaiting_first_import"
    baseline = db.scalar(select(ImportRun.id).where(
        ImportRun.root_id == root.id, ImportRun.state == "succeeded", ImportRun.coverage == "complete", ImportRun.scope.is_(None),
    ).limit(1))
    return "pending_import" if baseline is not None else "awaiting_first_import"


def _registered(db: Session, media_type: str, tmdb_id: int | None, tvdb_id: int | None) -> bool:
    ids = []
    if tmdb_id is not None:
        ids.append(func.json_extract(MediaTitle.provider_ids, "$.Tmdb") == str(tmdb_id))
    if tvdb_id is not None:
        ids.append(func.json_extract(MediaTitle.provider_ids, "$.Tvdb") == str(tvdb_id))
    if not ids:
        return False
    title_ids = list(db.scalars(select(MediaTitle.id).where(
        MediaTitle.type == ("movie" if media_type == "movie" else "series"), or_(*ids),
    )))
    observer = User(id="arr-observer", role="admin")
    return any(_title_available(db, observer, title_id, media_type) for title_id in title_ids)


def _inventory_details(raw: dict[str, Any], media_type: str) -> dict[str, Any]:
    details: dict[str, Any] = {}
    quality = raw.get("qualityProfileId")
    if isinstance(quality, int) and not isinstance(quality, bool):
        details["quality_profile_id"] = quality
    if media_type == "tv":
        totals = {"episode_count": 0, "file_count": 0}
        for season in raw.get("seasons") if isinstance(raw.get("seasons"), list) else []:
            stats = season.get("statistics") if isinstance(season, dict) else None
            if isinstance(stats, dict):
                totals["episode_count"] += _int(stats.get("episodeCount")) or 0
                totals["file_count"] += _int(stats.get("episodeFileCount")) or 0
        details["episodes"] = totals
    return details


def _record_row(server_id: str, dataset: str, raw: dict[str, Any], index: int, now: Any) -> ArrObservedRecord:
    if dataset not in DATASETS:
        raise ArrError("The download server sent an unexpected reply")
    item_id = _int(raw.get("movieId")) or _int(raw.get("seriesId")) or _int(raw.get("movie", {}).get("id") if isinstance(raw.get("movie"), dict) else None)
    record_id = _record_id(raw, index)
    return ArrObservedRecord(server_id=server_id, dataset=dataset, record_id=record_id, item_id=item_id,
                             data=_record_data(dataset, raw), observed_at=now)


def _record_id(raw: dict[str, Any], index: int) -> str:
    candidate = raw.get("id")
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        return str(candidate)
    if isinstance(candidate, str) and RECORD_ID.fullmatch(candidate):
        return candidate
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    return f"{index}-{digest}"[:128]


def _record_data(dataset: str, raw: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "queue": ("status", "trackedDownloadStatus", "trackedDownloadState", "size", "sizeleft", "timeleft", "protocol"),
        "history": ("eventType", "date", "successful"),
        "health": ("type", "source"),
        "calendar": ("airDate", "airDateUtc", "seasonNumber", "episodeNumber", "hasFile", "monitored"),
        "missing": ("seasonNumber", "episodeNumber", "airDateUtc", "monitored"),
        "cutoff": ("seasonNumber", "episodeNumber", "airDateUtc", "monitored"),
    }[dataset]
    data = {}
    for key in allowed:
        value = raw.get(key)
        if isinstance(value, str):
            value = redact(value, paths=True)[:120]
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if value is not None and isinstance(value, (str, int, float, bool)):
            data[key] = value
    quality = raw.get("quality")
    if isinstance(quality, dict):
        nested = quality.get("quality") if isinstance(quality.get("quality"), dict) else quality
        if (name := _text(nested.get("name"), 120)) is not None:
            data["quality"] = {"name": name}
    return data


def _summary(items: list[ArrObservedItem], records: list[ArrObservedRecord]) -> dict[str, Any]:
    counts = {dataset: 0 for dataset in DATASETS}
    for record in records:
        counts[record.dataset] += 1
    imports = {state: 0 for state in ("unmapped", "root_unavailable", "awaiting_first_import", "pending_import", "registered")}
    for item in items:
        imports[item.import_state] += 1
    return {"inventory_count": len(items), **{f"{dataset}_count": count for dataset, count in counts.items()}, "import_counts": imports}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:maximum] if value else None


def title_integrations(db: Session, user: Any, title_id: str) -> list[dict[str, Any]]:
    """The member-safe state for one current visible title. No upstream identity or title crosses this boundary."""
    title = db.scalar(select(MediaTitle).where(MediaTitle.id == title_id, LibraryService.visible_title_predicate(user)))
    if title is None:
        raise LookupError
    if title.type not in ("movie", "series"):
        return []
    tmdb = _provider_int((title.provider_ids or {}).get("Tmdb"))
    tvdb = _provider_int((title.provider_ids or {}).get("Tvdb"))
    clauses = []
    if tmdb is not None:
        clauses.append(ArrObservedItem.tmdb_id == tmdb)
    if tvdb is not None:
        clauses.append(ArrObservedItem.tvdb_id == tvdb)
    if not clauses:
        return []
    rows = db.execute(select(ArrObservedItem, ArrServer, ArrObservationState).join(ArrServer, ArrServer.id == ArrObservedItem.server_id)
                      .outerjoin(ArrObservationState, ArrObservationState.server_id == ArrObservedItem.server_id)
                      .where(ArrServer.enabled.is_(True), ArrObservedItem.media_type == ("movie" if title.type == "movie" else "tv"), or_(*clauses))).all()
    available = _title_available(db, user, title.id, "movie" if title.type == "movie" else "tv")
    # Persisted import state is only an observation cache, never authority for the member's Play explanation.
    return [{"state": "registered" if available else ("pending_import" if item.import_state == "registered" else item.import_state),
             "stale": stale(server, state)} for item, server, state in rows]


def _provider_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    return int(value) if isinstance(value, str) and value.isdigit() and len(value) <= 10 and int(value) > 0 else None


def _title_available(db: Session, user: User, title_id: str, media_type: str) -> bool:
    """Whether the current member can actually play a registered movie or an episode below a series."""
    if media_type == "movie":
        leaves = select(MediaTitle.id).where(MediaTitle.id == title_id, MediaTitle.type == "movie")
    else:
        season = aliased(MediaTitle)
        leaves = select(MediaTitle.id).join(season, season.id == MediaTitle.parent_id).where(
            MediaTitle.type == "episode", season.type == "season", season.parent_id == title_id,
        )
    return db.scalar(select(LibraryItem.id).join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
                     .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
                     .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
                     .where(LibraryItem.title_id.in_(leaves), LibraryItem.status != "missing", LibraryService.visible_predicate(user),
                            MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True)).limit(1)) is not None
