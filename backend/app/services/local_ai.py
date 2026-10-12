"""Admin-approved private local inference endpoint.

This is the ONE deliberate private-network destination: requests go only to the
admin-configured base URL, never follow redirects, ignore proxy env vars and are
bounded in time and bytes. It is a separate client from the public-media
``PublicSourcePolicy`` guard, which stays unchanged and still denies private
hosts for media. No cloud fallback exists; an empty base URL disables the
feature. Request/response content is never logged.
"""

from __future__ import annotations

import json
import math
import threading
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

import httpx
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, object_session

from app.config import settings
from app.models import AppSettings
from app.persistence import queue_after_commit, write_transaction

FIELDS = ("ai_base_url", "ai_model", "ai_api_key", "ai_max_concurrency", "ai_context_tokens", "asr_base_url", "asr_model", "ai_embedding_model")
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 5.0
TEST_TIMEOUT_SECONDS = 10.0
CHAT_TIMEOUT_SECONDS = 600.0  # a long-context prefill on a local GPU is slow
EMBED_TIMEOUT_SECONDS = 60.0
EMBED_BATCH = 32
EMBED_INPUT_BYTES = 2048
EMBED_MAX_DIMENSIONS = 8192


class LocalAiError(RuntimeError):
    """Stable, content-free failure description safe to show an admin or store on a job."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # the endpoint's HTTP status when it answered with something other than 200


def validate_endpoint_url(value: str) -> str:
    """http(s) origin plus optional path; no credentials, query or fragment. "" means disabled."""
    value = value.strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        parsed.port  # noqa: B018 - raises on a malformed port
    except ValueError as exc:
        raise ValueError("Endpoint URL is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Endpoint URL must be http or https with a host")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("Endpoint URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Endpoint URL must not contain a query or fragment")
    return value


@dataclass(frozen=True)
class AiConfig:
    ai_base_url: str
    ai_model: str
    ai_api_key: str | None
    ai_max_concurrency: int
    ai_context_tokens: int
    asr_base_url: str
    asr_model: str
    ai_embedding_model: str | None = None  # None/"" = search uses the deterministic encoder
    asr_timeout_seconds: float = 300.0  # per transcription chunk; model_endpoints raises it for the on-device server
    # The on-device speech server's per-start secret. Never an admin/env value (kept out of FIELDS below):
    # asr_base_url is a separate host from ai_base_url, so the chat/embedding key must never travel here.
    asr_api_key: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.ai_base_url and self.ai_model)

    @property
    def asr_available(self) -> bool:
        return bool(self.asr_base_url and self.asr_model)


def effective_config(record: AppSettings) -> AiConfig:
    """Saved admin values win; NULL columns fall back to the env defaults."""
    values = {name: getattr(record, name, None) for name in FIELDS}
    return AiConfig(**{name: getattr(settings, name, None) if value is None else value for name, value in values.items()})


_slots = threading.Condition()
_active = 0


@contextmanager
def inference_slot(limit: int, *, wait: float | None = None) -> Iterator[None]:
    """Global cap on concurrent inference requests; honors the currently configured limit.

    ``wait`` bounds the queue time (typeahead must not sit behind a long chat); None waits forever.
    """
    global _active
    with _slots:
        if not _slots.wait_for(lambda: _active < max(1, limit), timeout=wait):
            raise LocalAiError("Local AI is busy")
        _active += 1
    try:
        yield
    finally:
        with _slots:
            _active -= 1
            _slots.notify_all()


def bounded_json_request(
    base_url: str, method: str, path: str, *, timeout: float, label: str,
    max_bytes: int = MAX_RESPONSE_BYTES, array: bool = False, ok: tuple[int, ...] = (200,), **send: Any,
) -> Any:
    """One no-redirect, no-proxy request to the admin endpoint whose JSON object (list with ``array``) reply is capped at ``max_bytes``.

    Every failure is a content-free ``LocalAiError`` prefixed with ``label``.
    """
    try:
        with httpx.Client(
            base_url=base_url + "/",
            timeout=httpx.Timeout(timeout, connect=min(timeout, CONNECT_TIMEOUT_SECONDS)),
            follow_redirects=False,
            trust_env=False,
        ) as client, client.stream(method, path, **send) as response:
            if response.status_code not in ok:
                raise LocalAiError(f"{label} endpoint returned HTTP {response.status_code}", status=response.status_code)
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload += chunk
                if len(payload) > max_bytes:
                    raise LocalAiError(f"{label} response exceeded the size limit")
        data = json.loads(payload)
    except httpx.TimeoutException as exc:
        raise LocalAiError(f"{label} endpoint timed out") from exc
    except httpx.HTTPError as exc:
        raise LocalAiError(f"{label} endpoint is unreachable") from exc
    except ValueError as exc:
        raise LocalAiError(f"{label} endpoint returned malformed JSON") from exc
    if not isinstance(data, list if array else dict):
        raise LocalAiError(f"{label} endpoint returned an unexpected response")
    return data


def _request(config: AiConfig, method: str, path: str, *, timeout: float, body: dict | None = None) -> dict[str, Any]:
    if not config.enabled:
        raise LocalAiError("Local AI is not configured")
    headers = {"Authorization": f"Bearer {config.ai_api_key}"} if config.ai_api_key else {}
    return bounded_json_request(config.ai_base_url, method, path, timeout=timeout, label="Local AI", json=body, headers=headers)


class JobRegistry:
    """This process's queued/running AI job ids: bounded admission, cancel flags and restart detection."""

    def __init__(self, limit: int, busy_error: type[Exception]) -> None:
        self.limit, self.busy_error = limit, busy_error
        self.lock = threading.Lock()
        self.running: set[str] = set()
        self.canceled: set[str] = set()

    def is_active(self, job_id: str) -> bool:
        with self.lock:
            return job_id in self.running

    def state_of(self, row: Any) -> str:
        """A queued/running row with no worker in this process was interrupted by a restart."""
        if row.state in {"queued", "running"} and not self.is_active(row.id):
            # The worker commits its outcome before it leaves the registry, so it may have finished after this row
            # was read: read it again before calling it an orphan.
            if (session := object_session(row)) is not None:
                try:
                    session.refresh(row)
                except InvalidRequestError:  # deleted since: nothing is running it
                    return "interrupted"
            if row.state in {"queued", "running"}:
                return "interrupted"
        return row.state

    def is_canceled(self, job_id: str) -> bool:
        with self.lock:
            return job_id in self.canceled

    def cancel(self, job_id: str) -> bool:
        """Flag an in-process job as canceled; True if one was signaled."""
        with self.lock:
            if job_id not in self.running:
                return False
            self.canceled.add(job_id)
        return True

    def running_ids(self) -> set[str]:
        with self.lock:
            return set(self.running)

    def start(self, db: Session, make_row: Callable[[str], Any], target: Callable[[str], None], name: str) -> Any:
        """Reserve a slot, insert the queued row and start its worker thread after commit."""
        with self.lock:
            if len(self.running) >= self.limit:
                raise self.busy_error()
            job_id = str(uuid.uuid4())
            self.running.add(job_id)
        try:
            with write_transaction(db, name=name):
                row = make_row(job_id)
                db.add(row)
                queue_after_commit(db, lambda: threading.Thread(target=target, args=(job_id,), daemon=True).start())
        except BaseException:
            self.release(job_id)
            raise
        return row

    def release(self, job_id: str) -> None:
        with self.lock:
            self.running.discard(job_id)
            self.canceled.discard(job_id)

    def admit(self, job_id: str) -> bool:
        """Reserve a slot for an existing row (the pending pump); False when full or already running."""
        with self.lock:
            if job_id in self.running or len(self.running) >= self.limit:
                return False
            self.running.add(job_id)
            return True


def list_models(config: AiConfig) -> list[str]:
    data = _request(config, "GET", "models", timeout=TEST_TIMEOUT_SECONDS).get("data")
    if not isinstance(data, list):
        raise LocalAiError("Local AI endpoint returned an unexpected model list")
    return [entry["id"] for entry in data if isinstance(entry, dict) and isinstance(entry.get("id"), str)][:50]


def chat(config: AiConfig, messages: list[dict[str, str]], *, max_tokens: int) -> str:
    """One bounded, tool-free chat completion; returns the assistant text."""
    body = {"model": config.ai_model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2, "stream": False}
    with inference_slot(config.ai_max_concurrency):
        data = _request(config, "POST", "chat/completions", timeout=CHAT_TIMEOUT_SECONDS, body=body)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalAiError("Local AI endpoint returned an unexpected completion") from exc
    if not isinstance(content, str):
        raise LocalAiError("Local AI endpoint returned an unexpected completion")
    return content


def _is_vector(value: object) -> bool:
    return (
        isinstance(value, list) and 0 < len(value) <= EMBED_MAX_DIMENSIONS
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value)
    )


def embed(
    config: AiConfig, texts: list[str], *, timeout: float = EMBED_TIMEOUT_SECONDS, slot_wait: float | None = None,
) -> list[list[float]]:
    """Embed ``texts`` with the admin's embedding model: 32 per request, each cut to 2 KB of UTF-8."""
    if not config.ai_embedding_model:
        raise LocalAiError("No embedding model is configured")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = [text.encode()[:EMBED_INPUT_BYTES].decode(errors="ignore") for text in texts[start : start + EMBED_BATCH]]
        # A local (supervisor-backed, asr_api_key set) server is gated by supervisor.heavy, not the external slot.
        with nullcontext() if config.asr_api_key else inference_slot(config.ai_max_concurrency, wait=slot_wait):
            data = _request(config, "POST", "embeddings", timeout=timeout, body={"model": config.ai_embedding_model, "input": batch}).get("data")
        rows = data if isinstance(data, list) and len(data) == len(batch) else None
        if rows is None or not all(isinstance(row, dict) and _is_vector(row.get("embedding")) for row in rows):
            raise LocalAiError("Local AI endpoint returned unexpected embeddings")
        vectors.extend([float(x) for x in row["embedding"]] for row in rows)
    return vectors


def check_connection(config: AiConfig) -> dict[str, Any]:
    try:
        models = list_models(config)
    except LocalAiError as exc:
        return {"ok": False, "models": [], "model_available": False, "error": str(exc)}
    available = config.ai_model in models
    return {
        "ok": available,
        "models": models,
        "model_available": available,
        "error": None if available else "Configured model is not served by this endpoint",
    }
