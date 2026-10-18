"""Request sync loop: Sonarr/Radarr queue → processing + progress; files → a targeted rescan of the mapped
folder through the existing import service; ``available`` only once a visible Media title with the matching provider id
exists (then ``library_title_id`` is set). Runs every 60 s and on demand after a dispatch; skips cleanly when Requests
are off or no server is set up.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Iterable

from sqlalchemy import select

from app.db import session_scope
from app.models import AppMaintenanceState, ArrServer, ImportRun, MediaRequest, StorageRoot, User, utcnow
from app.persistence import write_transaction
from app.services.requests import notify
from app.services.requests.arr import ArrClient, ArrError
from app.services.requests.engine import ACTIVE
from app.services.requests.status import library_title_for
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)
SYNC_INTERVAL_SECONDS = 60
RESCAN_EVERY_SECONDS = 600
RETRY_AFTER = timedelta(seconds=RESCAN_EVERY_SECONDS)
SAFETY_RECONCILE_AFTER = timedelta(hours=6)
_wake = threading.Event()
# Per-process memory of folders rescanned recently; a restart may rescan a folder once more, which is harmless.
_rescanned: dict[str, float] = {}


@dataclass(frozen=True)
class RescanOffer:
    path: str
    digest: str | None = None
    source: str | None = None
    priority: int = 1


def reconciliation_digest(value: Any) -> str:
    """A stable opaque identity for upstream file-bearing state; callers may include transient private paths."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def wake() -> None:
    _wake.set()


async def request_sync_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(sync_once)
        except Exception as exc:  # noqa: BLE001 - the type only; the next interval retries
            logger.warning("Request sync failed; retrying next interval: %s", type(exc).__name__)
        for _ in range(SYNC_INTERVAL_SECONDS):
            if _wake.is_set():
                break
            await asyncio.sleep(1)
        _wake.clear()


def map_path(path: str | None, mappings: list[dict[str, str]] | None) -> str | None:
    """The arr's path as Lumina sees it, through the longest matching remote prefix; None when nothing maps."""
    if not path:
        return None
    arr_path = PurePosixPath(path)
    for mapping in sorted(mappings or [], key=lambda m: -len(m.get("remote") or "")):
        remote = PurePosixPath(mapping.get("remote") or "/")
        if arr_path == remote or remote in arr_path.parents:
            return str(PurePosixPath(mapping.get("local") or "/") / arr_path.relative_to(remote))
    return None


def _state_key(local: str, source: str | None = None) -> str:
    identity = f"{source or ''}\0{local}"
    return f"manager_reconcile:{hashlib.sha256(identity.encode()).hexdigest()[:48]}"


def _stored_digest(digest: str) -> str:
    return hashlib.sha256(digest.encode()).hexdigest()


def _read_state(row: AppMaintenanceState | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(row.value)
        return value if isinstance(value, dict) and value.get("v") == 1 else {}
    except (TypeError, ValueError):
        return {}


def _at(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _gate(db: Any, local: str, source: str | None, digest: str, now: datetime) -> tuple[int, dict[str, Any] | None]:
    """Return known-change (0), bootstrap (1), safety (2), or blocked (3), plus a state transition."""
    row = db.get(AppMaintenanceState, _state_key(local, source))
    state = _read_state(row)
    update = None
    if run_id := state.get("run_id"):
        run = db.get(ImportRun, run_id)
        if run is not None and run.state in ("running", "cancel_requested", "needs_confirmation"):
            return 3, None
        update = {**state, "run_id": None}
        if run is not None and run.state == "succeeded":
            update["digest"] = state.get("attempt_digest")
            update["completed_at"] = (run.finished_at or now).isoformat()
        state = update
    desired = _stored_digest(digest)
    completed = state.get("digest")
    admitted_at = _at(state.get("admitted_at"))
    if desired != completed:
        # A failed attempt is retried after the short cooldown. A genuinely newer
        # upstream identity bypasses that cooldown and gets changed-state priority.
        blocked = desired == state.get("attempt_digest") and admitted_at is not None and now - admitted_at < RETRY_AFTER
        if blocked:
            return 3, update
        return (0 if completed is not None else 1), update
    completed_at = _at(state.get("completed_at"))
    if (desired == state.get("attempt_digest") and admitted_at is not None and now - admitted_at < RETRY_AFTER
            and (completed_at is None or completed_at < admitted_at)):
        return 3, update
    return (2 if completed_at is None or now - completed_at >= SAFETY_RECONCILE_AFTER else 3), update


def _persist_states(db: Any, updates: list[tuple[str, dict[str, Any]]]) -> None:
    if not updates:
        return
    with write_transaction(db, name="manager_reconciliation_complete"):
        for key, value in updates:
            row = db.get(AppMaintenanceState, key)
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
            if row is None:
                db.add(AppMaintenanceState(key=key, value=encoded))
            elif row.value != encoded:
                row.value = encoded


def _root_id(local: str, roots: list[StorageRoot]) -> str | None:
    folder = PurePosixPath(local)
    matches = [root for root in roots if folder == PurePosixPath(root.path) or PurePosixPath(root.path) in folder.parents]
    return max(matches, key=lambda root: len(root.path)).id if matches else None


def rescan_folders(folders: Iterable[str | RescanOffer]) -> None:
    """Admit changed inventories first and at most one scoped run per root in this observation pass."""
    grouped: dict[tuple[str | None, str], list[RescanOffer]] = {}
    for value in folders:
        offer = value if isinstance(value, RescanOffer) else RescanOffer(value)
        grouped.setdefault((offer.source, offer.path), []).append(offer)
    unique = [
        RescanOffer(path, None if any(offer.digest is None for offer in offers)
                    else reconciliation_digest(sorted(offer.digest for offer in offers if offer.digest is not None)),
                    source, min(offer.priority for offer in offers))
        for (source, path), offers in grouped.items()
    ]
    now = utcnow()
    planned: list[tuple[int, int, float, str, str | None, RescanOffer]] = []
    with session_scope() as db:
        roots = list(db.scalars(select(StorageRoot).where(StorageRoot.mode == "external", StorageRoot.enabled.is_(True))))
        updates = []
        for offer in unique:
            root_id = _root_id(offer.path, roots)
            if root_id is None:
                continue
            rank = 0
            if offer.digest is not None:
                rank, update = _gate(db, offer.path, offer.source, offer.digest, now)
                if update is not None:
                    updates.append((_state_key(offer.path, offer.source), update))
            if rank < 3:
                planned.append((rank, offer.priority, _rescanned.get(offer.path, -RESCAN_EVERY_SECONDS),
                                offer.path, root_id, offer))
    if updates:
        with session_scope() as db:
            _persist_states(db, updates)
    admitted_roots: set[str] = set()
    for _, _, _, local, root_id, offer in sorted(
        planned, key=lambda value: (value[0], value[1], value[2], value[3], value[5].source or "")
    ):
        if root_id in admitted_roots:
            continue
        try:
            rescan_folder(local, digest=offer.digest, source=offer.source, admitted_roots=admitted_roots)
        except Exception as exc:  # noqa: BLE001 - one folder never stops the next poll's reconciliation
            logger.warning("Arr folder rescan failed: %s", type(exc).__name__)


def rescan_folder(local: str, *, digest: str | None = None, source: str | None = None,
                  admitted_roots: set[str] | None = None) -> bool:
    """Offer a targeted deep manager scan; return whether this call admitted a run."""
    from app.services.library_automation import automation, start_run_default
    from app.services.library_import import ImportRunError

    now = time.monotonic()
    if digest is None and now - _rescanned.get(local, -RESCAN_EVERY_SECONDS) < RESCAN_EVERY_SECONDS:
        return False
    folder = PurePosixPath(local)
    observed_at = utcnow()
    with session_scope() as db:
        roots = [r for r in db.scalars(select(StorageRoot).where(StorageRoot.mode == "external", StorageRoot.enabled.is_(True)))
                 if folder == PurePosixPath(r.path) or PurePosixPath(r.path) in folder.parents]
        if not roots:
            return False
        root = max(roots, key=lambda r: len(r.path))
        if admitted_roots is not None and root.id in admitted_roots:
            return False
        state_update = None
        if digest is not None:
            rank, state_update = _gate(db, local, source, digest, observed_at)
            if state_update is not None:
                _persist_states(db, [(_state_key(local, source), state_update)])
            if rank >= 3:
                return False
        newest = db.scalars(select(ImportRun).where(ImportRun.root_id == root.id).order_by(ImportRun.created_at.desc())).first()
        baseline = db.scalars(select(ImportRun).where(
            ImportRun.root_id == root.id, ImportRun.state == "succeeded", ImportRun.coverage == "complete", ImportRun.scope.is_(None),
        ).order_by(ImportRun.created_at.desc())).first()
        if newest is None or newest.state == "needs_confirmation" or baseline is None:
            # A root needs one complete full baseline; never bypass a held confirmation with an automatic scoped run.
            return False
        root_id, user_id, visibility = root.id, baseline.user_id, baseline.visibility
        relative = folder.relative_to(PurePosixPath(root.path)).as_posix()
    try:
        run_id = start_run_default(root_id, user_id, visibility, trigger="manager", scope=[{"dir": "" if relative == "." else relative, "deep": True}])
    except (ImportRunError, LookupError):  # the root is busy: the next tick tries again
        return False
    _rescanned[local] = now
    if admitted_roots is not None:
        admitted_roots.add(root_id)
    if digest is not None:
        key = _state_key(local, source)
        with session_scope() as db:
            state = _read_state(db.get(AppMaintenanceState, key))
            state.update(v=1, attempt_digest=_stored_digest(digest), run_id=run_id, admitted_at=observed_at.isoformat())
            _persist_states(db, [(key, state)])
    automation.submit(run_id)
    return True


def _observe(client: ArrClient, req: MediaRequest, queue: list[dict[str, Any]]) -> dict[str, Any]:
    field = "movieId" if req.media_type == "movie" else "seriesId"
    records = [r for r in queue if r.get(field) == req.arr_item_id]
    size = sum(float(r.get("size") or 0) for r in records)
    left = sum(float(r.get("sizeleft") or 0) for r in records)
    seen: dict[str, Any] = {"downloading": bool(records), "progress": round(1 - left / size, 3) if size > 0 else 0.0}
    if req.media_type == "movie":
        movie = client.call("GET", f"movie/{req.arr_item_id}")
        seen.update(complete=bool(movie.get("hasFile")), partial=False, folder=movie.get("path"))
    else:
        series = client.call("GET", f"series/{req.arr_item_id}")
        files = count = 0
        for season in series.get("seasons") or []:
            number = season.get("seasonNumber", -1)
            if req.seasons == "all" and number > 0 or isinstance(req.seasons, list) and number in req.seasons:
                stats = season.get("statistics") or {}
                files += int(stats.get("episodeFileCount") or 0)
                count += int(stats.get("episodeCount") or 0)
        seen.update(complete=count > 0 and files >= count, partial=files > 0, folder=series.get("path"))
    return seen


def sync_once() -> None:
    with session_scope() as db:
        if not YtDlpService(db).get_app_settings().requests_enabled:
            return
        servers = {s.id: s for s in db.scalars(select(ArrServer).where(ArrServer.enabled.is_(True)))}
        if not servers:
            return
        groups: dict[str, list[MediaRequest]] = defaultdict(list)
        for req in db.scalars(select(MediaRequest).where(MediaRequest.status.in_(ACTIVE), MediaRequest.arr_server_id.in_(servers))):
            groups[req.arr_server_id].append(req)
        for server_id, requests in groups.items():
            server = servers[server_id]
            client, removed, error = ArrClient.of(server), set(), None
            try:
                queue = client.queue()
            except ArrError as exc:  # only the queue call speaks for the whole server
                queue, error = None, str(exc)
            except (TypeError, ValueError, AttributeError):
                queue, error = None, "The download server sent an unexpected reply"
            updates = {}
            for req in requests if queue is not None else []:
                try:
                    updates[req.id] = _next_state(db, server, req, _observe(client, req, queue))
                except ArrError as exc:
                    if exc.status == 404:
                        removed.add(req.id)
                    else:
                        logger.warning("Request sync skipped one request: %s", exc)
                except Exception as exc:  # noqa: BLE001 - one odd item never stops the others
                    logger.warning("Request sync skipped one request: %s", type(exc).__name__)
            with write_transaction(db, name="request_sync"):
                server.last_error = error
                if error is None:
                    server.last_ok_at = utcnow()
                for req in requests:
                    if req.id in removed:
                        req.status, req.failure_reason = "failed", f"Removed from {'Radarr' if server.kind == 'radarr' else 'Sonarr'}"
                        notify.request_event(db, req, "failed")
                    if req.id not in updates:
                        continue
                    status, progress, title_id = updates[req.id]
                    if status == "available" and req.status != "available":
                        req.status = status  # notify reads it
                        notify.request_event(db, req, "available")
                    req.status, req.progress = status, progress
                    req.library_title_id = title_id or req.library_title_id


def _next_state(db: Any, server: ArrServer, req: MediaRequest, seen: dict[str, Any]) -> tuple[str, float | None, str | None]:
    title_id = None
    if seen["complete"] or seen["partial"]:
        if local := map_path(seen["folder"], server.path_mappings):
            try:
                rescan_folder(local)
            except Exception as exc:  # noqa: BLE001 - a scan problem never stops the sync
                logger.warning("Request rescan failed: %s", type(exc).__name__)
        requester = db.get(User, req.requested_by)
        title_id = requester and library_title_for(db, requester, req.media_type, req.tmdb_id, req.tvdb_id)
    if seen["complete"] and title_id:
        return "available", 1.0, title_id
    if seen["downloading"]:
        return "processing", seen["progress"], title_id
    if seen["partial"] and req.media_type == "tv":
        return "partially_available", None, title_id
    if seen["complete"]:  # the file is there; the vault has not met it yet
        return "processing", 1.0, title_id
    return req.status, req.progress, title_id
