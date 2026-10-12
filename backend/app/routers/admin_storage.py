"""Admin-only storage root registry API. Paths are shown to admins only."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import StorageRoot
from app.security import get_admin_user
from app.services.storage_roots import StorageRootError, StorageRootInUseError, StorageRootService
from app.services.storage_routing import MediaKind, RuleSetConflict, Source, StorageRoutingService, StorageRule

URL = "/api/admin/storage/roots"
RULES_URL = "/api/admin/storage/rules"
RESOLVE_URL = "/api/admin/storage/resolve"


class StorageRootCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    container_path: str = Field(min_length=1, max_length=4096)
    mode: Literal["managed", "external"]
    enabled: bool = True
    minimum_free_bytes: int = Field(default=0, ge=0)


class StorageRootUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    minimum_free_bytes: int | None = Field(default=None, ge=0)


class StorageRootResponse(BaseModel):
    id: str
    label: str
    path: str
    mode: str
    enabled: bool
    minimum_free_bytes: int
    observation: dict
    artifact_count: int
    created_at: datetime
    updated_at: datetime


def _serialize(root: StorageRoot, service: StorageRootService, counts: dict[str, int] | None = None) -> StorageRootResponse:
    counts = counts if counts is not None else service.artifact_counts([root.id])
    return StorageRootResponse(
        id=root.id,
        label=root.label,
        path=root.path,
        mode=root.mode,
        enabled=bool(root.enabled),
        minimum_free_bytes=root.minimum_free_bytes or 0,
        observation=root.observation or {},
        artifact_count=counts.get(root.id, 0),
        created_at=root.created_at,
        updated_at=root.updated_at,
    )


def _get_root(service: StorageRootService, root_id: str) -> StorageRoot:
    root = service.get(root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Storage root not found")
    return root


def list_storage_roots(db: Session = Depends(get_db, scope="function")) -> list[StorageRootResponse]:
    service = StorageRootService(db)
    roots = service.list_roots()
    counts = service.artifact_counts([root.id for root in roots])
    return [_serialize(root, service, counts) for root in roots]


def create_storage_root(payload: StorageRootCreateRequest, db: Session = Depends(get_db, scope="function")) -> StorageRootResponse:
    service = StorageRootService(db)
    try:
        root = service.create(**payload.model_dump())
    except StorageRootError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize(root, service)


def update_storage_root(root_id: str, payload: StorageRootUpdateRequest, db: Session = Depends(get_db, scope="function")) -> StorageRootResponse:
    service = StorageRootService(db)
    return _serialize(service.update(_get_root(service, root_id), **payload.model_dump()), service)


def probe_storage_root(root_id: str, db: Session = Depends(get_db, scope="function")) -> StorageRootResponse:
    service = StorageRootService(db)
    return _serialize(service.probe(_get_root(service, root_id)), service)


def delete_storage_root(root_id: str, db: Session = Depends(get_db, scope="function")) -> None:
    service = StorageRootService(db)
    try:
        service.delete(_get_root(service, root_id))
    except StorageRootInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class StorageRuleSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    default_root_id: str | None = Field(default=None, max_length=36)
    rules: list[StorageRule] = Field(default_factory=list, max_length=100)


class StorageRuleSetResponse(BaseModel):
    revision: int
    default_root_id: str | None
    rules: list[StorageRule]


class StorageResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    media_kind: MediaKind
    # Actual output height; null means not inspected yet, never "assume the request".
    height: int | None = Field(default=None, ge=1, le=20_000)


class StorageResolveResponse(BaseModel):
    rule_id: str | None
    rule_revision: int
    root_id: str
    root_label: str
    relative_path: str
    reason: str
    can_admit: bool


def get_storage_rules(db: Session = Depends(get_db, scope="function")) -> StorageRuleSetResponse:
    routing = StorageRoutingService(db)
    rule_set = routing.rule_set()
    return StorageRuleSetResponse(revision=rule_set.revision, default_root_id=rule_set.default_root_id, rules=routing.rules(rule_set))


def put_storage_rules(payload: StorageRuleSetRequest, db: Session = Depends(get_db, scope="function")) -> StorageRuleSetResponse:
    try:
        StorageRoutingService(db).replace(**{key: getattr(payload, key) for key in ("expected_revision", "default_root_id", "rules")})
    except RuleSetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorageRootError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_storage_rules(db)


def resolve_storage(payload: StorageResolveRequest, db: Session = Depends(get_db, scope="function")) -> StorageResolveResponse:
    """Dry run: which root/folder a file with these facts would land in. Writes nothing."""
    routing = StorageRoutingService(db)
    decision = routing.decide(payload.source, payload.media_kind, payload.height)
    root = db.get(StorageRoot, decision.root_id)
    return StorageResolveResponse(
        rule_id=decision.rule_id,
        rule_revision=decision.revision,
        root_id=decision.root_id,
        root_label=root.label if root else "",
        relative_path=decision.folder,
        reason=decision.reason,
        can_admit=routing.writable_root(decision) is not None,
    )


def register(app: FastAPI) -> None:
    """Flat app routes (not include_router) so route-walking contract checks see them."""
    admin = [Depends(get_admin_user)]
    app.get(URL, response_model=list[StorageRootResponse], dependencies=admin)(list_storage_roots)
    app.post(URL, response_model=StorageRootResponse, status_code=201, dependencies=admin)(create_storage_root)
    app.patch(URL + "/{root_id}", response_model=StorageRootResponse, dependencies=admin)(update_storage_root)
    app.post(URL + "/{root_id}/probe", response_model=StorageRootResponse, dependencies=admin)(probe_storage_root)
    app.delete(URL + "/{root_id}", status_code=204, dependencies=admin)(delete_storage_root)
    app.get(RULES_URL, response_model=StorageRuleSetResponse, dependencies=admin)(get_storage_rules)
    app.put(RULES_URL, response_model=StorageRuleSetResponse, dependencies=admin)(put_storage_rules)
    app.post(RESOLVE_URL, response_model=StorageResolveResponse, dependencies=admin)(resolve_storage)
