"""Where each AI feature's model runs. Decided here and only here.

- Semantic search: the active local search model when installed and ready, else the admin's external
  ``ai_embedding_model`` (today's behaviour), else nothing (search keeps the built-in keyword encoder).
- Subtitles from speech, and so the precise path of Sync (it reads speech transcripts): the active local
  speech model when ready, else the external ``asr_base_url``, else unavailable.
- Assistant features: the external AI server only.

A local model reaches the existing OpenAI-compatible client code as an ordinary ``AiConfig`` (base URL +
secret) inside ``connect()``, which holds a supervisor lease meanwhile. The features themselves do not
change. The ``ai_features_disabled`` kill switch is checked by each feature's entry point BEFORE it asks
for a choice here, so a switched-off feature never starts a model server.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Literal

from app.models import AppSettings
from app.services import model_catalog, model_downloads, model_supervisor
from app.services.local_ai import AiConfig, LocalAiError, effective_config
from app.services.model_catalog import CatalogModel

LOCAL_TRANSCRIBE_TIMEOUT_SECONDS = 3600.0  # a 10-minute chunk with the accurate model on a busy 4-core host

Requirement = Literal["search_model", "speech_model", "assistant"]
REQUIRES: dict[str, Requirement | None] = {
    "semantic_search": "search_model",
    "subtitles_from_speech": "speech_model",
    "sync": None,  # works without a model (speech detection); a speech transcript only makes it precise
    "translate": "assistant",
    "recap": "assistant",
    "episode_summaries": "assistant",
    "key_scenes": "assistant",
    "smart_collection_builder": "assistant",
    "match_tie_breaker": "assistant",
    "mute_strong_language": None,
}


@dataclass(frozen=True)
class Choice:
    kind: Literal["local", "external"]
    model_id: str  # SearchEmbedding.model_id / AsrJob.model_id: "local:<catalog id>" or the external model name
    config: AiConfig  # external: ready to use; local: the admin config whose endpoint connect() fills in
    model: CatalogModel | None = None
    threads: int = 1


@dataclass(frozen=True)
class Readiness:
    requires: Requirement | None
    ready: bool
    reason: str | None = None


def active_model(record: AppSettings, role: str) -> CatalogModel:
    configured = record.local_search_model if role == "search" else record.local_speech_model
    return model_catalog.active(role, configured)


def local_ready(model: CatalogModel) -> bool:
    """Downloaded and verified, and not failed by crashes (a memory refusal is retried on each request)."""
    return model_downloads.manager.status(model).state == "ready" and not model_supervisor.supervisor.is_failed(model.id)


def external_serves(config: AiConfig, role: str) -> bool:
    """Whether the admin's external server covers this role: search needs an embedding model; speech needs ASR."""
    return config.enabled and bool(config.ai_embedding_model) if role == "search" else config.asr_available


def _local(record: AppSettings, model: CatalogModel) -> Choice:
    return Choice("local", model.choice_id, effective_config(record), model, model_supervisor.effective_threads(record.model_threads))


def search_choices(record: AppSettings) -> list[Choice]:
    """Every source search may read, best first: the active local model, other ready local models, external."""
    active = active_model(record, "search")
    ready = [model for model in model_catalog.catalog().models if model.role == "search" and local_ready(model)]
    ready.sort(key=lambda model: model is not active)
    choices = [_local(record, model) for model in ready]
    config = effective_config(record)
    if external_serves(config, "search"):
        choices.append(Choice("external", config.ai_embedding_model, config))
    return choices


def speech_choice(record: AppSettings) -> Choice | None:
    model = active_model(record, "speech")
    if local_ready(model):
        return _local(record, model)
    config = effective_config(record)
    return Choice("external", config.asr_model, config) if external_serves(config, "speech") else None


def speech_choice_for_job(record: AppSettings, model_id: str) -> Choice:
    """The source a queued speech job was requested with: running jobs keep their model."""
    model = model_catalog.from_choice_id(model_id)
    if model is not None:
        if model.role != "speech" or not local_ready(model):
            raise LocalAiError("The speech model is not installed")
        return _local(record, model)
    config = dataclasses.replace(effective_config(record), asr_model=model_id)
    if not config.asr_available:
        raise LocalAiError("Local ASR is not configured")
    return Choice("external", model_id, config)


@contextmanager
def connect(choice: Choice, *, wait: bool, heavy: bool) -> Iterator[AiConfig | None]:
    """The config to call ``choice`` with, valid inside the block.

    External: the config as is. Local: a lease on the model's loopback server, its URL and per-start secret.
    ``wait=False`` (interactive search) yields None while the server starts or cannot start; ``wait=True``
    raises LocalAiError with the supervisor's reason. ``heavy`` (bulk transcription and backfill) holds the
    supervisor's heavy lock for local models, so those two never run at the same time.
    """
    if choice.kind == "external":
        yield choice.config
        return
    supervisor = model_supervisor.supervisor
    with supervisor.heavy if heavy else nullcontext():
        try:
            endpoint = supervisor.acquire(choice.model, threads=choice.threads, wait=wait)
        except model_supervisor.ModelUnavailable as exc:
            if wait:
                raise LocalAiError(str(exc)) from exc
            endpoint = None
        if endpoint is None:
            yield None
            return
        try:
            yield dataclasses.replace(
                choice.config, ai_base_url=endpoint.base_url, ai_model=choice.model_id, ai_api_key=endpoint.secret,
                ai_embedding_model=choice.model_id, asr_base_url=endpoint.base_url, asr_model=choice.model_id,
                asr_timeout_seconds=LOCAL_TRANSCRIBE_TIMEOUT_SECONDS, asr_api_key=endpoint.secret,
            )
        finally:
            supervisor.release(choice.model.id, endpoint)


def _model_readiness(model: CatalogModel, requirement: Requirement, *, external: bool) -> Readiness:
    status = model_downloads.manager.status(model)
    if status.state == "ready":
        failure = model_supervisor.supervisor.failure(model.id)  # crash-failed, or the last memory/runtime refusal
        if failure is None or external:  # the external server serves the role while the local model is failed
            return Readiness(requirement, True)
        return Readiness(requirement, False, failure)
    if external:
        return Readiness(requirement, True)
    if status.state in ("downloading", "verifying"):
        return Readiness(requirement, False, f"Waiting for the {model.role} model")
    if status.state == "failed":
        return Readiness(requirement, False, status.reason)
    return Readiness(requirement, False, f"The {model.role} model is not installed")


def readiness(record: AppSettings) -> dict[str, Readiness]:
    """Per AI feature key: what it needs and whether that is available now (the kill switch is reported apart)."""
    config = effective_config(record)
    by_requirement = {
        "search_model": _model_readiness(active_model(record, "search"), "search_model", external=external_serves(config, "search")),
        "speech_model": _model_readiness(active_model(record, "speech"), "speech_model", external=external_serves(config, "speech")),
        "assistant": Readiness("assistant", config.enabled, None if config.enabled else "Needs your assistant server"),
    }
    return {key: by_requirement[requirement] if requirement else Readiness(None, True) for key, requirement in REQUIRES.items()}
