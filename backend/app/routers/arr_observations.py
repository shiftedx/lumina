"""Admin arr operations read model and member-safe title availability explanations."""
from __future__ import annotations

import math
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ArrObservationState, ArrObservedItem, ArrObservedRecord, ArrServer, User
from app.security import get_admin_user, get_current_user
from app.services import arr_observer

DATASET = Literal["queue", "history", "health", "calendar", "missing", "cutoff"]


def _time(value):  # noqa: ANN001, ANN202
    return value.isoformat() + "Z" if value else None


def _server(server: ArrServer, state: ArrObservationState | None) -> dict:
    return {"server_id": server.id, "kind": server.kind, "name": server.name, "enabled": server.enabled,
            "version": state.version if state else None, "capabilities": dict(state.capabilities or {}) if state else {},
            "summary": dict(state.summary or {}) if state else {}, "last_ok_at": _time(state.last_ok_at) if state else None,
            "last_error": state.last_error if state else None, "attempted_at": _time(state.attempted_at) if state else None,
            "stale": arr_observer.stale(server, state)}


def observations(
    server_id: str | None = None, page: int = Query(default=1, ge=1, le=10_000), page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    servers = list(db.scalars(select(ArrServer).order_by(ArrServer.created_at)))
    if server_id is not None and not any(server.id == server_id for server in servers):
        raise HTTPException(status_code=404, detail="Server not found")
    states = {row.server_id: row for row in db.scalars(select(ArrObservationState)).all()}
    query = select(ArrObservedItem).order_by(ArrObservedItem.observed_at.desc(), ArrObservedItem.server_id, ArrObservedItem.upstream_id)
    if server_id is not None:
        query = query.where(ArrObservedItem.server_id == server_id)
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)))
    return {"servers": [_server(server, states.get(server.id)) for server in servers], "items": [
        {"server_id": item.server_id, "upstream_id": item.upstream_id, "media_type": item.media_type, "tmdb_id": item.tmdb_id,
         "tvdb_id": item.tvdb_id, "title": item.title, "year": item.year, "monitored": item.monitored, "has_file": item.has_file,
         "import_state": item.import_state, "details": dict(item.details or {}), "observed_at": _time(item.observed_at)} for item in rows],
        "page": page, "page_size": page_size, "total": total, "total_pages": max(1, math.ceil(total / page_size))}


def records(
    server_id: str, dataset: DATASET, page: int = Query(default=1, ge=1, le=10_000), page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    server = db.get(ArrServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    state = db.get(ArrObservationState, server_id)
    query = select(ArrObservedRecord).where(ArrObservedRecord.server_id == server_id, ArrObservedRecord.dataset == dataset).order_by(ArrObservedRecord.observed_at.desc(), ArrObservedRecord.record_id)
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)))
    return {"items": [{"record_id": row.record_id, "item_id": row.item_id, "data": dict(row.data or {}), "observed_at": _time(row.observed_at)} for row in rows],
            "page": page, "page_size": page_size, "total": total, "total_pages": max(1, math.ceil(total / page_size)),
            "stale": arr_observer.stale(server, state)}


def refresh(server_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
    if db.get(ArrServer, server_id) is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if not arr_observer.sync_once(server_id):
        raise HTTPException(status_code=409, detail="refresh_in_progress")
    db.expire_all()
    server = db.get(ArrServer, server_id)
    return _server(server, db.get(ArrObservationState, server_id))


def title(title_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    try:
        integrations = arr_observer.title_integrations(db, user, title_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Title not found") from None
    return {"title_id": title_id, "integrations": integrations}


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get("/api/admin/arr/observations", dependencies=admin)(observations)
    app.get("/api/admin/arr/servers/{server_id}/{dataset}", dependencies=admin)(records)
    app.post("/api/admin/arr/servers/{server_id}/refresh", dependencies=admin)(refresh)
    app.get("/api/arr/titles/{title_id}")(title)
