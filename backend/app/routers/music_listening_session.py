"""Member-owned music listening-session HTTP contract (ADR 0026)."""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.music_schemas import MusicBody, MusicTrack
from app.security import get_current_user
from app.services.music_listening_session import (
    MAX_ENTRIES,
    MAX_POSITION_SECONDS,
    ListeningSession,
    MusicListeningSessionConflict,
    MusicListeningSessionInvalid,
    MusicListeningSessionService,
)

URL = "/api/me/music-session"
MusicItemId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class TrackQueueEntry(MusicBody):
    kind: Literal["track"]
    item_id: MusicItemId


class AcquisitionQueueEntry(MusicBody):
    kind: Literal["acquisition"]
    request_id: UUID


QueueEntry = Annotated[Union[TrackQueueEntry, AcquisitionQueueEntry], Field(discriminator="kind")]


class BrowserOutput(MusicBody):
    kind: Literal["browser"]


class MusicListeningSessionWrite(MusicBody):
    base_revision: int = Field(ge=0)
    item_ids: list[MusicItemId] = Field(max_length=MAX_ENTRIES)
    current_index: int | None = Field(default=None, ge=0)
    position_seconds: float = Field(ge=0, le=MAX_POSITION_SECONDS)
    repeat: Literal["off", "all", "one"]
    shuffle: bool
    active_output: BrowserOutput
    acknowledged_output_ticket: str | None = Field(default=None, min_length=1, max_length=128)
    entries: list[QueueEntry] | None = Field(default=None, max_length=MAX_ENTRIES)
    entry_index: int | None = Field(default=None, ge=0)
    unavailable_policy: Literal["skip", "wait"] = "skip"

    @field_validator("position_seconds")
    @classmethod
    def finite_position(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("position_seconds must be finite")
        return value

    @model_validator(mode="after")
    def current_track_is_in_queue(self) -> "MusicListeningSessionWrite":
        if self.entries is None and self.current_index is not None and self.current_index >= len(self.item_ids):
            raise ValueError("current_index must identify a queued track")
        return self


class MusicListeningSessionResponse(MusicBody):
    revision: int
    queue: list[MusicTrack]
    current_index: int | None
    position_seconds: float
    repeat: Literal["off", "all", "one"]
    shuffle: bool
    active_output: dict[str, Any]
    updated_at: str | None
    removed_count: int
    entries: list[dict[str, Any]]
    entry_index: int | None
    unavailable_policy: Literal["skip", "wait"]


def _time(value) -> str | None:  # noqa: ANN001, ANN202
    return value.isoformat() + "Z" if value else None


def _response(value: ListeningSession) -> MusicListeningSessionResponse:
    return MusicListeningSessionResponse(
        revision=value.revision, queue=value.queue, current_index=value.current_index,
        position_seconds=value.position_seconds, repeat=value.repeat, shuffle=value.shuffle,
        active_output=value.active_output, updated_at=_time(value.updated_at), removed_count=value.removed_count,
        entries=value.entries or [], entry_index=value.entry_index, unavailable_policy=value.unavailable_policy,
    )


def get_session(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> MusicListeningSessionResponse:
    return _response(MusicListeningSessionService(db).read(user))


def replace_session(
    body: MusicListeningSessionWrite,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MusicListeningSessionResponse:
    service = MusicListeningSessionService(db)
    try:
        session = service.replace(
            user, base_revision=body.base_revision, item_ids=body.item_ids, current_index=body.current_index,
            position_seconds=body.position_seconds, repeat=body.repeat, shuffle=body.shuffle, acknowledged_output_ticket=body.acknowledged_output_ticket,
            entries=[entry.model_dump(mode="json") for entry in body.entries] if body.entries is not None else None, entry_index=body.entry_index, unavailable_policy=body.unavailable_policy,
        )
    except MusicListeningSessionConflict:
        raise HTTPException(status_code=409, detail={"code": "music_session_conflict", "session": _response(service.read(user)).model_dump(mode="json")}) from None
    except MusicListeningSessionInvalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return _response(session)


def register(app: FastAPI) -> None:
    app.get(URL, response_model=MusicListeningSessionResponse)(get_session)
    app.put(URL, response_model=MusicListeningSessionResponse)(replace_session)
