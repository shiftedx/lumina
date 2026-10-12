"""Server-owned search history API. Per-member, bounded, never blocks search."""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_current_user
from app.services.search_history import SearchHistoryRecord, SearchHistoryService

URL = "/api/search/history"


class SearchHistoryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class SearchHistoryResponse(BaseModel):
    id: str
    query: str
    searched_at: datetime


class SearchHistoryClearResponse(BaseModel):
    deleted: int


def _response(record: SearchHistoryRecord) -> SearchHistoryResponse:
    return SearchHistoryResponse(id=record.id, query=record.query, searched_at=record.searched_at)


def list_search_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[SearchHistoryResponse]:
    return [_response(record) for record in SearchHistoryService(db).list_for(current_user)]


def record_search_history(
    payload: SearchHistoryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SearchHistoryResponse | None:
    """Record one query. The client calls this fire-and-forget; a null body means the query was skipped, not an error."""
    record = SearchHistoryService(db).record(current_user, payload.query)
    return _response(record) if record else None


def delete_search_history_entry(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> None:
    SearchHistoryService(db).delete(current_user, entry_id)


def clear_search_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SearchHistoryClearResponse:
    return SearchHistoryClearResponse(deleted=SearchHistoryService(db).clear(current_user))


def register(app: FastAPI) -> None:
    """Flat app routes (not include_router) so route-walking contract checks see them."""
    app.get(URL, response_model=list[SearchHistoryResponse])(list_search_history)
    app.post(URL, response_model=SearchHistoryResponse | None)(record_search_history)
    app.delete(URL + "/{entry_id}", status_code=204)(delete_search_history_entry)
    app.delete(URL, response_model=SearchHistoryClearResponse)(clear_search_history)
