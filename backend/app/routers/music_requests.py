"""MusicBrainz-identified, explicitly approved Lidarr acquisitions."""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ServiceConnector, User
from app.security import get_admin_user, get_current_user
from app.services import lidarr


class MusicRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    connector_id: str = Field(min_length=1, max_length=36)
    foreign_artist_id: UUID
    foreign_album_id: UUID
    artist_name: str | None = Field(default=None, max_length=300)
    album_name: str | None = Field(default=None, max_length=300)


def _http(call):  # noqa: ANN001, ANN202
    try:
        return call()
    except lidarr.MusicRequestNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except lidarr.MusicRequestConflict as exc:
        detail = {"detail": exc.detail}
        if exc.request is not None:
            detail["request"] = lidarr._serialize(exc.request)
        raise HTTPException(status_code=409, detail=detail) from None
    except lidarr.LidarrError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def create(body: MusicRequestBody, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    payload = body.model_dump(mode="json")
    return _http(lambda: lidarr.create(db, user, payload))


def mine(page: int = Query(default=1, ge=1, le=10_000), page_size: int = Query(default=50, ge=1, le=100),
         user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return lidarr.list_requests(db, user, page=page, page_size=page_size)


def targets(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    rows = db.scalars(select(ServiceConnector).where(ServiceConnector.kind == "lidarr", ServiceConnector.enabled.is_(True),
                                                    ServiceConnector.api_key.is_not(None)).order_by(ServiceConnector.name))
    return {"items": [{"id": row.id, "name": row.name} for row in rows if row.api_key]}


def all_requests(page: int = Query(default=1, ge=1, le=10_000), page_size: int = Query(default=50, ge=1, le=100),
                 db: Session = Depends(get_db, scope="function")) -> dict:
    return lidarr.list_requests(db, None, page=page, page_size=page_size)


def approve(request_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
    return _http(lambda: lidarr.approve(db, request_id))


def reconcile(request_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
    return _http(lambda: lidarr.reconcile(db, request_id))


def inventory(connector_id: str, page: int = Query(default=1, ge=1, le=10_000), page_size: int = Query(default=50, ge=1, le=100),
              db: Session = Depends(get_db, scope="function")) -> dict:
    return _http(lambda: lidarr.inventory(db, connector_id, page=page, page_size=page_size))


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.post("/api/music/requests")(create)
    app.get("/api/music/requests")(mine)
    app.get("/api/music/request-targets")(targets)
    app.get("/api/admin/music/requests", dependencies=admin)(all_requests)
    app.post("/api/admin/music/requests/{request_id}/approve", dependencies=admin)(approve)
    app.post("/api/admin/music/requests/{request_id}/reconcile", dependencies=admin)(reconcile)
    app.get("/api/admin/lidarr/{connector_id}/inventory", dependencies=admin)(inventory)
