"""Request sync loop: Sonarr/Radarr queue → processing + progress; files → a targeted rescan of the mapped
folder through the existing import service; ``available`` only once a visible Media title with the matching provider id
exists (then ``library_title_id`` is set). Runs every 60 s and on demand after a dispatch; skips cleanly when Requests
are off or no server is set up.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models import ArrServer, ImportRun, MediaRequest, StorageRoot, User, utcnow
from app.persistence import write_transaction
from app.services.requests import notify
from app.services.requests.arr import ArrClient, ArrError
from app.services.requests.engine import ACTIVE
from app.services.requests.status import library_title_for
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)
SYNC_INTERVAL_SECONDS = 60
RESCAN_EVERY_SECONDS = 600
_wake = threading.Event()
# Per-process memory of folders rescanned recently; a restart may rescan a folder once more, which is harmless.
_rescanned: dict[str, float] = {}


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


def rescan_folder(local: str) -> None:
    """A targeted, deep scan of ``local`` in the external storage root holding it (the watcher's own kind of run)."""
    from app.services.library_automation import automation, start_run_default
    from app.services.library_import import ImportRunError

    now = time.monotonic()
    if now - _rescanned.get(local, -RESCAN_EVERY_SECONDS) < RESCAN_EVERY_SECONDS:
        return
    folder = PurePosixPath(local)
    with session_scope() as db:
        roots = [r for r in db.scalars(select(StorageRoot).where(StorageRoot.mode == "external", StorageRoot.enabled.is_(True)))
                 if folder == PurePosixPath(r.path) or PurePosixPath(r.path) in folder.parents]
        if not roots:
            return
        root = max(roots, key=lambda r: len(r.path))
        newest = db.scalars(select(ImportRun).where(ImportRun.root_id == root.id).order_by(ImportRun.created_at.desc())).first()
        if newest is None:  # a root never imported waits for the admin's first import
            return
        root_id, user_id, visibility = root.id, newest.user_id, newest.visibility
        relative = folder.relative_to(PurePosixPath(root.path)).as_posix()
    try:
        run_id = start_run_default(root_id, user_id, visibility, trigger="watch", scope=[{"dir": "" if relative == "." else relative, "deep": True}])
    except (ImportRunError, LookupError):  # the root is busy: the next tick tries again
        return
    _rescanned[local] = now
    automation.submit(run_id)


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
