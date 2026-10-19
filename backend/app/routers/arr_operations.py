"""Admin-only acquisition controls for managed Sonarr/Radarr items."""
from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_admin_user
from app.services import arr_operations
from app.services.requests.arr import ArrError

HEX = re.compile(r"^[0-9a-f]{64}$")


class OperationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    base_revision: str = Field(min_length=64, max_length=64)
    action: Literal["monitor", "profile", "search", "rescan", "grab"]
    monitored: bool | None = None
    season_number: int | None = Field(default=None, ge=0, le=1000)
    profile_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    release_id: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_action(self) -> OperationBody:
        if not HEX.fullmatch(self.base_revision):
            raise ValueError("base_revision must be a SHA-256 revision")
        if self.release_id is not None and not HEX.fullmatch(self.release_id):
            raise ValueError("release_id must be an opaque release id")
        values = {"monitor": {"monitored", "season_number"}, "profile": {"profile_id"}, "search": {"season_number"},
                  "rescan": set(), "grab": {"release_id"}}[self.action]
        given = {key for key in ("monitored", "season_number", "profile_id", "release_id") if getattr(self, key) is not None}
        if not given.issubset(values):
            raise ValueError("Fields do not match this action")
        required = {"monitor": "monitored", "profile": "profile_id", "grab": "release_id"}.get(self.action)
        if required and getattr(self, required) is None:
            raise ValueError(f"{required} is required")
        return self


def _http(call):  # noqa: ANN001, ANN202
    try:
        return call()
    except arr_operations.OperationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except arr_operations.OperationConflict as exc:
        detail = {"detail": exc.detail}
        if exc.operation is not None:
            detail["operation"] = arr_operations._serialize(exc.operation)
        raise HTTPException(status_code=409, detail=detail) from None
    except (ArrError, ValueError) as exc:
        raise HTTPException(status_code=502 if isinstance(exc, ArrError) else 400, detail=str(exc)) from None


def item(server_id: str, upstream_id: int, db: Session = Depends(get_db, scope="function")) -> dict:
    return _http(lambda: arr_operations.get_state(db, server_id, upstream_id))


def releases(server_id: str, upstream_id: int, db: Session = Depends(get_db, scope="function")) -> dict:
    return _http(lambda: arr_operations.get_releases(db, server_id, upstream_id))


def operation(server_id: str, upstream_id: int, body: OperationBody, user: User = Depends(get_admin_user),
              db: Session = Depends(get_db, scope="function")) -> dict:
    payload = body.model_dump()
    payload["operation_id"] = str(payload["operation_id"])
    return _http(lambda: arr_operations.operate(db, user, server_id, upstream_id, payload))


def operations(server_id: str | None = None, page: int = Query(default=1, ge=1, le=10_000),
               page_size: int = Query(default=50, ge=1, le=100), db: Session = Depends(get_db, scope="function")) -> dict:
    return arr_operations.list_operations(db, server_id, page, page_size)


def reconcile(operation_id: str, db: Session = Depends(get_db, scope="function")) -> dict:
    return _http(lambda: arr_operations.reconcile(db, operation_id))


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get("/api/admin/arr/servers/{server_id}/items/{upstream_id}", dependencies=admin)(item)
    app.get("/api/admin/arr/servers/{server_id}/items/{upstream_id}/releases", dependencies=admin)(releases)
    app.post("/api/admin/arr/servers/{server_id}/items/{upstream_id}/operations", dependencies=admin)(operation)
    app.get("/api/admin/arr/operations", dependencies=admin)(operations)
    app.post("/api/admin/arr/operations/{operation_id}/reconcile", dependencies=admin)(reconcile)
