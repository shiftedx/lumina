"""Watch queue API: the signed-in member's own ordered play-next list."""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import LibraryItem, User, WatchQueueEntry
from app.security import get_current_user
from app.services.watch_queue import (
    MAX_ENTRIES,
    RemoteRef,
    WatchQueueConflict,
    WatchQueueFull,
    WatchQueueNotFound,
    WatchQueueService,
)

URL = "/api/me/watch-queue"


def _http_url(value: str | None) -> bool:
    return bool(value) and urlparse(value).scheme in {"http", "https"} and bool(urlparse(value).netloc)


class QueueRefRequest(BaseModel):
    kind: Literal["library", "remote"]
    library_item_id: str | None = Field(default=None, min_length=1, max_length=36)
    provider: str | None = Field(default=None, max_length=120)
    remote_id: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=1000)
    uploader: str | None = Field(default=None, max_length=500)
    artwork_url: str | None = Field(default=None, max_length=2048)
    duration: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "QueueRefRequest":
        if self.kind == "library" and not self.library_item_id:
            raise ValueError("library refs need library_item_id")
        if self.kind == "remote" and not _http_url(self.url):
            raise ValueError("remote refs need an http(s) url")
        return self


class QueueAddRequest(BaseModel):
    ref: QueueRefRequest
    position: Literal["next", "end"] = "end"
    expected_revision: int | None = Field(default=None, ge=0)


class QueueMoveRequest(BaseModel):
    position: int = Field(ge=0, lt=MAX_ENTRIES)
    expected_revision: int = Field(ge=0)


class QueueEntryRef(BaseModel):
    kind: Literal["library", "remote"]
    library_item_id: str | None = None
    provider: str | None = None
    remote_id: str | None = None
    url: str | None = None


class QueueEntryResponse(BaseModel):
    id: str
    position: int
    availability: Literal["available", "unavailable"]
    ref: QueueEntryRef
    title: str | None
    uploader: str | None
    artwork_url: str | None
    duration: int | None


class WatchQueueResponse(BaseModel):
    revision: int
    limit: int
    entries: list[QueueEntryResponse]


def _entry(entry: WatchQueueEntry, item: LibraryItem | None) -> QueueEntryResponse:
    if entry.library_item_id is None:
        return QueueEntryResponse(
            id=entry.id, position=entry.position, availability="available",
            ref=QueueEntryRef(kind="remote", provider=entry.provider, remote_id=entry.remote_id, url=entry.source_url),
            title=entry.title, uploader=entry.uploader, artwork_url=entry.artwork_url, duration=entry.duration,
        )
    if item is None:
        # Revoked or deleted: a redacted tombstone the member can only remove.
        return QueueEntryResponse(
            id=entry.id, position=entry.position, availability="unavailable", ref=QueueEntryRef(kind="library"),
            title=None, uploader=None, artwork_url=None, duration=None,
        )
    return QueueEntryResponse(
        id=entry.id, position=entry.position, availability="available",
        ref=QueueEntryRef(kind="library", library_item_id=item.id, url=item.webpage_url),
        title=item.title, uploader=item.uploader, artwork_url=f"/api/library/{item.id}/artwork", duration=item.duration,
    )


def _queue(service: WatchQueueService, user: User) -> WatchQueueResponse:
    revision, rows = service.read(user)
    return WatchQueueResponse(revision=revision, limit=MAX_ENTRIES, entries=[_entry(entry, item) for entry, item in rows])


def _mutate(action) -> None:  # noqa: ANN001
    try:
        action()
    except WatchQueueConflict:
        raise HTTPException(status_code=409, detail="The watch queue changed elsewhere. Refresh and try again.") from None
    except WatchQueueFull:
        raise HTTPException(status_code=422, detail=f"The watch queue is full ({MAX_ENTRIES} items).") from None
    except WatchQueueNotFound:
        raise HTTPException(status_code=404, detail="Queue item not found") from None


def get_queue(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> WatchQueueResponse:
    return _queue(WatchQueueService(db), current_user)


def add_entry(
    payload: QueueAddRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> WatchQueueResponse:
    service = WatchQueueService(db)
    ref = payload.ref
    artwork = ref.artwork_url if _http_url(ref.artwork_url) or (ref.artwork_url or "").startswith("/api/") else None
    remote = None if ref.kind == "library" else RemoteRef(
        provider=ref.provider, remote_id=ref.remote_id, url=ref.url or "", title=ref.title,
        uploader=ref.uploader, artwork_url=artwork, duration=ref.duration,
    )
    _mutate(lambda: service.add(
        current_user,
        library_item_id=ref.library_item_id if ref.kind == "library" else None,
        remote=remote,
        position=payload.position,
        expected_revision=payload.expected_revision,
    ))
    return _queue(service, current_user)


def move_entry(
    entry_id: str,
    payload: QueueMoveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> WatchQueueResponse:
    service = WatchQueueService(db)
    _mutate(lambda: service.move(current_user, entry_id, payload.position, payload.expected_revision))
    return _queue(service, current_user)


def remove_entry(
    entry_id: str,
    expected_revision: int | None = Query(default=None, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> WatchQueueResponse:
    service = WatchQueueService(db)
    _mutate(lambda: service.remove(current_user, entry_id, expected_revision))
    return _queue(service, current_user)


def clear_queue(
    confirm: bool = Query(default=False),
    expected_revision: int | None = Query(default=None, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> WatchQueueResponse:
    if not confirm:
        raise HTTPException(status_code=422, detail="Clearing the watch queue requires confirm=true.")
    service = WatchQueueService(db)
    _mutate(lambda: service.clear(current_user, expected_revision))
    return _queue(service, current_user)


def register(app: FastAPI) -> None:
    app.get(URL, response_model=WatchQueueResponse)(get_queue)
    app.post(f"{URL}/entries", response_model=WatchQueueResponse)(add_entry)
    app.patch(f"{URL}/entries/{{entry_id}}", response_model=WatchQueueResponse)(move_entry)
    app.delete(f"{URL}/entries/{{entry_id}}", response_model=WatchQueueResponse)(remove_entry)
    app.delete(URL, response_model=WatchQueueResponse)(clear_queue)
