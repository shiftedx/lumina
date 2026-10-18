"""Admin-only, write-only connection setup and read-only infrastructure diagnostics."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ServiceConnector, utcnow
from app.persistence import write_transaction
from app.security import get_admin_user
from app.services import infrastructure_connectors as connectors
from app.services.local_ai import validate_endpoint_url


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathMapping(Strict):
    remote: str = Field(min_length=1, max_length=2048)
    local: str = Field(min_length=1, max_length=2048)


class ArrBinding(Strict):
    server_id: str = Field(min_length=1, max_length=36)
    client_name: str = Field(min_length=1, max_length=120)


class ConnectorSettings(Strict):
    root_folder: str | None = Field(default=None, max_length=2048)
    quality_profile_id: int | None = Field(default=None, ge=1)
    metadata_profile_id: int | None = Field(default=None, ge=1)
    path_mappings: list[PathMapping] = Field(default_factory=list, max_length=50)
    arr_clients: list[ArrBinding] = Field(default_factory=list, max_length=20)


class ConnectorBody(Strict):
    kind: Literal["lidarr", "prowlarr", "qbittorrent", "nzbget"]
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=256)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=256)
    settings: ConnectorSettings = Field(default_factory=ConnectorSettings)

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value: str) -> str:
        result = validate_endpoint_url(value)
        if not result:
            raise ValueError("Enter the service address")
        return result


def _row(db: Session, connector_id: str) -> ServiceConnector:
    row = db.get(ServiceConnector, connector_id, populate_existing=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return row


def _time(value):  # noqa: ANN001, ANN202
    return value.isoformat() + "Z" if value else None


def serialize(row: ServiceConnector) -> dict:
    return {"id": row.id, "kind": row.kind, "name": row.name, "base_url": row.base_url, "settings": row.settings,
            "api_key_set": bool(row.api_key), "username_set": bool(row.username), "password_set": bool(row.password),
            "enabled": row.enabled, "version": row.version, "last_ok_at": _time(row.last_ok_at), "last_error": row.last_error,
            "stale": not row.enabled or row.last_ok_at is None or bool(row.last_error) or row.last_ok_at < utcnow() - timedelta(minutes=5),
            "capabilities": (row.snapshot or {}).get("capabilities", {})}


def listing(db: Session = Depends(get_db, scope="function")) -> dict:
    return {"items": [serialize(row) for row in db.scalars(select(ServiceConnector).order_by(ServiceConnector.name))]}


def save(body: ConnectorBody, connector_id: str | None = None, db: Session = Depends(get_db, scope="function")) -> dict:
    row = _row(db, connector_id) if connector_id else None
    changed_identity = row is not None and (row.kind != body.kind or row.base_url != body.base_url)
    if changed_identity and (body.kind in ("lidarr", "prowlarr") and not body.api_key or body.kind in ("qbittorrent", "nzbget") and (not body.username or not body.password)):
        raise HTTPException(status_code=400, detail="New credentials are required for a changed endpoint")
    with write_transaction(db, name="service_connector_save"):
        if row is None:
            row = ServiceConnector(id=str(uuid4()))
            db.add(row)
        row.kind, row.name, row.base_url = body.kind, body.name.strip(), body.base_url
        row.settings = body.settings.model_dump()
        for key in ("api_key", "username", "password"):
            value = getattr(body, key)
            if value is not None:
                setattr(row, key, value)
            elif changed_identity:
                setattr(row, key, None)
        row.enabled, row.snapshot, row.version, row.last_ok_at, row.last_error = False, {}, None, None, None
    return serialize(row)


def refresh(connector_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
    _row(db, connector_id)
    try:
        return serialize(connectors.refresh(db, connector_id))
    except connectors.ConnectorChanged as exc:
        raise HTTPException(409, str(exc)) from None


def details(connector_id: str, dataset: Literal["transfers", "health", "indexers", "indexer_status", "history", "stats", "root_folders", "quality_profiles", "metadata_profiles", "artists", "albums", "queue"] = "transfers",
            page: int = Query(default=1, ge=1, le=100_000), page_size: int = Query(default=50, ge=1, le=100),
            db: Session = Depends(get_db, scope="function")) -> dict:
    row = _row(db, connector_id)
    values = (row.snapshot or {}).get(dataset, [])
    values = values if isinstance(values, list) else [values]
    return {**serialize(row), "dataset": dataset, "items": values[(page - 1) * page_size:page * page_size], "total": len(values), "page": page, "page_size": page_size}


def disable(connector_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
    with write_transaction(db, name="service_connector_disable"):
        row = _row(db, connector_id)
        row.enabled = False
        row.api_key = row.username = row.password = None
        row.snapshot = {}
        row.last_ok_at = None
    return serialize(row)


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get("/api/admin/connectors", dependencies=admin)(listing)
    app.post("/api/admin/connectors", dependencies=admin)(save)
    app.put("/api/admin/connectors/{connector_id}", dependencies=admin)(save)
    app.get("/api/admin/connectors/{connector_id}", dependencies=admin)(details)
    app.post("/api/admin/connectors/{connector_id}/refresh", dependencies=admin)(refresh)
    app.delete("/api/admin/connectors/{connector_id}", dependencies=admin)(disable)
