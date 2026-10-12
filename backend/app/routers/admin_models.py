"""Admin-only on-device model management.

A model id in a path is looked up in the catalog: an unknown id is 404 and never reaches the file system.
Every route requires an admin (members get 403), model events go to active admins' streams only, and no
response or event ever carries a model server's port or secret.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db, session_scope
from app.events import EventBus
from app.models import AppSettings, User
from app.persistence import write_transaction
from app.security import get_admin_user
from app.services import embeddings, model_catalog, model_downloads, model_endpoints, model_supervisor
from app.services.local_ai import effective_config
from app.services.model_catalog import CatalogModel
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)

URL = "/api/admin/models"
EVENT = "model_state"
ModelState = Literal["absent", "downloading", "verifying", "ready", "failed"]


class ModelView(BaseModel):
    id: str
    role: Literal["search", "speech"]
    name: str
    description: str
    licence: str
    size_bytes: int
    ram_bytes: int
    default: bool
    active: bool  # the admin's chosen model for its role (only one per role)
    state: ModelState
    bytes_done: int | None = None  # downloading, and absent/failed with a partial file
    bytes_total: int | None = None
    reason: str | None = None  # failed: why; ready: a start refusal such as "Not enough free memory to start the search model"
    running: bool  # its server process is up right now
    features: list[str]  # ai_features_disabled keys that use it


class ModelsResponse(BaseModel):
    models: list[ModelView]


class ModelRemoveResponse(BaseModel):
    model: ModelView
    disabled_features: list[str]  # features switched off because nothing else serves them


def view(record: AppSettings, model: CatalogModel) -> ModelView:
    status = model_downloads.manager.status(model)
    supervisor = model_supervisor.supervisor
    state, reason = status.state, status.reason
    if state == "ready":
        reason = supervisor.failure(model.id)
        if supervisor.is_failed(model.id):
            state = "failed"
    return ModelView(
        id=model.id, role=model.role, name=model.name, description=model.description, licence=model.licence,
        size_bytes=model.size_bytes, ram_bytes=model.ram_bytes, default=model.default,
        active=model_endpoints.active_model(record, model.role) is model, state=state,
        bytes_done=status.bytes_done, bytes_total=status.bytes_total, reason=reason,
        running=supervisor.running(model.id), features=list(model_catalog.FEATURES[model.role]),
    )


def _model_or_404(model_id: str) -> CatalogModel:
    model = model_catalog.catalog().get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown model")
    return model


def list_models(db: Session = Depends(get_db, scope="function")) -> ModelsResponse:
    record = YtDlpService(db).get_app_settings()
    return ModelsResponse(models=[view(record, model) for model in model_catalog.catalog().models])


def download_model(model_id: str, db: Session = Depends(get_db, scope="function")) -> ModelView:
    """Download, or Retry: a Retry also clears a crash failure, so an installed model starts again."""
    model = _model_or_404(model_id)
    if model.role == "search" and model_supervisor.supervisor.is_failed(model.id):
        embeddings.reset_caches()  # its kept index lacks what was imported meanwhile: serve another until it is topped up
    model_supervisor.supervisor.retry(model.id)  # emits a change: wire() starts the backfill for a ready search model
    model_downloads.manager.start(model)
    return view(YtDlpService(db).get_app_settings(), model)


def cancel_model_download(model_id: str, db: Session = Depends(get_db, scope="function")) -> ModelView:
    model = _model_or_404(model_id)
    model_downloads.manager.cancel(model)
    return view(YtDlpService(db).get_app_settings(), model)


def remove_model(model_id: str, db: Session = Depends(get_db, scope="function")) -> ModelRemoveResponse:
    """Delete a model's files. If it was its role's active model and nothing external serves that role,
    the features that need it are switched off (the UI asks for confirmation first)."""
    model = _model_or_404(model_id)
    model_supervisor.supervisor.stop(model.id)
    model_downloads.manager.remove(model)
    requirement = "search_model" if model.role == "search" else "speech_model"
    disabled: list[str] = []
    with write_transaction(db, name="model_remove"):
        record = YtDlpService(db).ensure_app_settings()
        external = model_endpoints.external_serves(effective_config(record), model.role)
        if model_endpoints.active_model(record, model.role) is model and not external:
            current = list(record.ai_features_disabled or [])
            disabled = [key for key in model_catalog.FEATURES[model.role] if model_endpoints.REQUIRES.get(key) == requirement and key not in current]
            record.ai_features_disabled = current + disabled
    if model.role == "search":
        embeddings.reset_caches()
        embeddings.start_backfill()
    return ModelRemoveResponse(model=view(record, model), disabled_features=disabled)


def activate_model(model_id: str, db: Session = Depends(get_db, scope="function")) -> ModelView:
    """Choose the active model for its role; switching search models re-indexes in the background."""
    model = _model_or_404(model_id)
    with write_transaction(db, name="model_activate"):
        record = YtDlpService(db).ensure_app_settings()
        changed = model_endpoints.active_model(record, model.role) is not model
        setattr(record, "local_search_model" if model.role == "search" else "local_speech_model", model.id)
    if changed and model.role == "search":
        embeddings.reset_caches()
        embeddings.start_backfill()
    return view(record, model)


def publish(events: EventBus, model_id: str) -> None:
    """Send one model's row to every active admin's event stream; members never receive model events."""
    model = model_catalog.catalog().get(model_id)
    if model is None:
        return
    with session_scope() as db:
        payload = view(YtDlpService(db).get_app_settings(), model).model_dump(mode="json")
        admins = [user_id for (user_id,) in db.query(User.id).filter(User.role == "admin", User.is_active.is_(True))]
    for user_id in admins:
        events.publish(EVENT, {"model": payload, "user_id": user_id})


def wire(events: EventBus) -> None:
    """Lifespan: download and server changes reach admins' streams; a search model that becomes ready starts indexing."""
    def changed(model_id: str) -> None:
        try:
            publish(events, model_id)
            model = model_catalog.catalog().get(model_id)
            if model is not None and model.role == "search" and model_downloads.manager.status(model).state == "ready":
                embeddings.start_backfill()  # a no-op unless something is left to embed (never starts a server for nothing)
        except Exception as exc:  # noqa: BLE001 - a notification never breaks a download or a server
            logger.warning("Model change notification failed: %s", type(exc).__name__)

    model_downloads.manager.on_change = changed
    model_supervisor.supervisor.on_change = changed


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get(URL, response_model=ModelsResponse, dependencies=admin)(list_models)
    app.post(URL + "/{model_id}/download", response_model=ModelView, dependencies=admin)(download_model)
    app.post(URL + "/{model_id}/cancel", response_model=ModelView, dependencies=admin)(cancel_model_download)
    app.post(URL + "/{model_id}/activate", response_model=ModelView, dependencies=admin)(activate_model)
    app.delete(URL + "/{model_id}", response_model=ModelRemoveResponse, dependencies=admin)(remove_model)
