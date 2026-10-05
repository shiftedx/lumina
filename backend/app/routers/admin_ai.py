"""Admin-only local AI endpoint configuration. The endpoint secret is write-only."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppSettings
from app.persistence import write_transaction
from app.security import get_admin_user
from app.services import embeddings, model_endpoints, model_supervisor
from app.services.local_ai import check_connection, effective_config, validate_endpoint_url
from app.services.yt_dlp_service import YtDlpService

URL = "/api/admin/ai"
# Feature keys, e.g. "recap"; short and shape-checked so a stored list can't grow unbounded.
AiFeatureKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class FeatureReadinessResponse(BaseModel):
    """What an AI feature needs and whether it is available now; the Off switch is ai_features_disabled."""

    requires: Literal["search_model", "speech_model", "assistant"] | None
    ready: bool
    reason: str | None = None


class AiConfigResponse(BaseModel):
    base_url: str
    model: str
    has_api_key: bool
    max_concurrency: int
    context_tokens: int
    asr_base_url: str
    asr_model: str
    enabled: bool
    asr_available: bool
    embedding_model: str = ""
    ai_features_disabled: list[str] = Field(default_factory=list)  # AppSettings.ai_features_disabled (ADR 0013)
    features: dict[str, FeatureReadinessResponse] = Field(default_factory=dict)  # per feature key
    model_threads: int | None = None  # the admin's cap on model threads; None = automatic
    model_threads_auto: int = 1  # CPUs available to the container minus one, 1..12


class AiConfigUpdateRequest(BaseModel):
    """Omitted fields are unchanged. ``api_key: ""`` removes the secret; ``base_url: ""`` disables AI."""

    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    max_concurrency: int | None = Field(default=None, ge=1, le=16)
    context_tokens: int | None = Field(default=None, ge=4096, le=2_000_000)
    asr_base_url: str | None = Field(default=None, max_length=2048)
    asr_model: str | None = Field(default=None, max_length=200)
    embedding_model: str | None = Field(default=None, max_length=200)
    ai_features_disabled: list[AiFeatureKey] | None = Field(default=None, max_length=32)
    model_threads: int | None = Field(default=None, ge=0, le=12)  # 0 = automatic

    @field_validator("base_url", "asr_base_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else validate_endpoint_url(value)

    @field_validator("ai_features_disabled")
    @classmethod
    def _dedupe(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else list(dict.fromkeys(value))


class AiTestResponse(BaseModel):
    ok: bool
    models: list[str]
    model_available: bool
    error: str | None


def _serialize(record: AppSettings) -> AiConfigResponse:
    config = effective_config(record)
    return AiConfigResponse(
        base_url=config.ai_base_url,
        model=config.ai_model,
        has_api_key=bool(config.ai_api_key),
        max_concurrency=config.ai_max_concurrency,
        context_tokens=config.ai_context_tokens,
        asr_base_url=config.asr_base_url,
        asr_model=config.asr_model,
        enabled=config.enabled,
        asr_available=config.asr_available,
        embedding_model=config.ai_embedding_model or "",
        ai_features_disabled=record.ai_features_disabled or [],
        features={
            key: FeatureReadinessResponse(requires=entry.requires, ready=entry.ready, reason=entry.reason)
            for key, entry in model_endpoints.readiness(record).items()
        },
        model_threads=record.model_threads,
        model_threads_auto=model_supervisor.default_threads(),
    )


def get_ai_config(db: Session = Depends(get_db, scope="function")) -> AiConfigResponse:
    return _serialize(YtDlpService(db).get_app_settings())


def update_ai_config(payload: AiConfigUpdateRequest, db: Session = Depends(get_db, scope="function")) -> AiConfigResponse:
    with write_transaction(db, name="ai_config_update"):
        record = YtDlpService(db).ensure_app_settings()
        previous_embedding_model = record.ai_embedding_model or ""
        values = payload.model_dump(exclude_unset=True)
        threads = values.pop("model_threads", None)
        if threads is not None:
            record.model_threads = threads or None
        for field, value in values.items():
            if value is None:
                continue
            attr = field if field.startswith(("asr_", "ai_")) else "ai_" + field
            setattr(record, attr, value.strip() if isinstance(value, str) else value)
        embedding_model_changed = (record.ai_embedding_model or "") != previous_embedding_model
    if embedding_model_changed:
        embeddings.reset_caches()
        embeddings.start_backfill()
    return _serialize(record)


def probe_ai_connection(db: Session = Depends(get_db, scope="function")) -> AiTestResponse:
    return AiTestResponse(**check_connection(effective_config(YtDlpService(db).get_app_settings())))


def register(app: FastAPI) -> None:
    admin = [Depends(get_admin_user)]
    app.get(URL + "/config", response_model=AiConfigResponse, dependencies=admin)(get_ai_config)
    app.put(URL + "/config", response_model=AiConfigResponse, dependencies=admin)(update_ai_config)
    app.post(URL + "/test", response_model=AiTestResponse, dependencies=admin)(probe_ai_connection)
