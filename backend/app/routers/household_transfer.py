"""Fast first-owner transfer: account discovery, then one finite per-member import stream."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db, stream_session_scope
from app.media_schemas import HouseholdTransfer, HouseholdTransferMember, HouseholdTransferRequest
from app.models import User
from app.security import get_admin_user
from app.services import household_import, plex_history_client
from app.services.jellyfin_history_client import (
    JellyfinHistory, JellyfinImportError, JellyfinUser, admin_session, list_users, read_history,
)
from app.services.media_response import ClosingStreamingResponse
from app.services.rate_limit import enforce_rate_limit
from app.services.yt_dlp_service import YtDlpService

URL = "/api/admin/household-transfer"
Provider = Literal["jellyfin", "plex"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceHousehold:
    label: str
    owner_id: str
    users: list[JellyfinUser]
    read: Callable[[str], JellyfinHistory]
    errors: dict[str, str] | None = None


def _credentials(provider: Provider, payload: HouseholdTransferRequest) -> tuple[str, str]:
    if provider == "plex":
        token = payload.token.strip()
        if plex_history_client.TOKEN_RE.fullmatch(token) is None:
            raise HTTPException(status_code=422, detail="Enter a valid Plex server owner token.")
        return token, ""
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Enter the Jellyfin administrator username.")
    return username, payload.password


def _server(db: Session, request: Request, admin: User, provider: Provider) -> str:
    enforce_rate_limit(f"{provider}_import", request, user_id=admin.id)
    settings = YtDlpService(db).get_app_settings()
    server = settings.plex_import_url if provider == "plex" else settings.jellyfin_import_url
    if not server:
        label = "Plex" if provider == "plex" else "Jellyfin"
        raise HTTPException(status_code=409, detail=f"Set the {label} server address under Media server first.")
    return server


@contextmanager
def _source(provider: Provider, server: str, first: str, second: str) -> Iterator[SourceHousehold]:
    if provider == "jellyfin":
        with admin_session(server, first, second) as (session, owner_id):
            yield SourceHousehold(
                "Jellyfin", owner_id, list_users(session), read=lambda user_id: read_history(session, user_id),
            )
        return

    owner_id, users, tokens = plex_history_client.household(server, first)
    missing = {
        user.id: "Plex did not provide a server token for this user."
        for user in users if user.id not in tokens
    }
    yield SourceHousehold(
        "Plex", owner_id, users,
        read=lambda user_id: plex_history_client.read_history(server, tokens[user_id]),
        errors=missing,
    )


def _member(row: HouseholdTransferMember) -> dict[str, Any]:
    member = row.model_dump(mode="json", exclude={"reset_expires_at"})
    if member["summary"] is not None:
        member["summary"]["unmatched_names"] = []
    member["reset_expires_at"] = row.reset_expires_at.isoformat() if row.reset_expires_at else None
    return member


def discover(
    provider: Provider,
    payload: HouseholdTransferRequest,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, list[dict[str, Any]]]:
    """Authenticate the source owner and pair accounts without reading any history."""
    first, second = _credentials(provider, payload)
    server = _server(db, request, admin, provider)
    try:
        with _source(provider, server, first, second) as source:
            rows = household_import.discover(
                db, admin, source.owner_id, source.users, source=source.label, errors=source.errors,
            )
    except JellyfinImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"members": [_member(row) for row in rows]}


def _event(body: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(body, separators=(',', ':'))}\n\n".encode()


def _events(
    provider: Provider,
    server: str,
    payload: HouseholdTransferRequest,
    admin_id: str,
    db_scope: Callable[[], AbstractContextManager[Session]],
) -> Generator[bytes, None, None]:
    """The response owns source credentials and its database session, so closing it releases both."""
    yield _event({"type": "connecting"})
    try:
        first, second = _credentials(provider, payload)
        with db_scope() as db:
            admin = db.get(User, admin_id)
            if admin is None or admin.role != "admin" or not admin.is_active:
                yield _event({"type": "error", "message": "Admin access is no longer available."})
                return
            with _source(provider, server, first, second) as source:
                chosen = set(payload.member_ids)
                discovered = household_import.discover(
                    db, admin, source.owner_id, source.users, source=source.label, errors=source.errors,
                )
                actionable = {
                    row.source_id for row in discovered if row.action != "skip" and row.error is None
                }
                if not chosen.issubset(actionable):
                    yield _event({
                        "type": "error",
                        "message": "The selected source members changed. Discover the household again.",
                    })
                    return
                completed = 0
                total = 0
                steps = household_import.iter_run(
                    db, admin, source.owner_id, source.users, chosen,
                    read=source.read, source=source.label, progress=True, include_unselected=False,
                )
                for step in steps:
                    if isinstance(step, household_import.ReadProgress):
                        total = step.total
                        yield _event({
                            "type": "progress",
                            "source_id": step.pairing.member.id,
                            "source_name": step.pairing.member.name,
                            "completed": step.completed,
                            "total": step.total,
                        })
                        continue
                    completed += 1
                    yield _event({
                        "type": "member", "member": _member(step),
                        "completed": completed, "total": total,
                    })
                yield _event({"type": "complete", "completed": completed, "total": total})
    except JellyfinImportError as exc:
        yield _event({"type": "error", "message": str(exc)})
    except GeneratorExit:
        raise
    except Exception as exc:
        logger.warning("household_transfer.failed provider=%s error=%s", provider, type(exc).__name__)
        yield _event({"type": "error", "message": "The household transfer could not continue."})


def import_transfer(
    provider: Provider,
    payload: HouseholdTransferRequest,
    request: Request,
    db_scope: Annotated[Callable[[], AbstractContextManager[Session]], Depends(stream_session_scope)],
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db, scope="function"),
) -> ClosingStreamingResponse:
    """Read each selected member once and report each durable result as soon as it commits."""
    if not payload.member_ids:
        raise HTTPException(status_code=400, detail="Select at least one source member to import.")
    _credentials(provider, payload)
    server = _server(db, request, admin, provider)
    content = _events(provider, server, payload, admin.id, db_scope)
    return ClosingStreamingResponse(
        content,
        close=content.close,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


def register(app: FastAPI) -> None:
    app.post(URL + "/{provider}/discover", response_model=HouseholdTransfer)(discover)
    app.post(URL + "/{provider}/import")(import_transfer)
