"""Idea graph API. Access mirrors the library item's visibility; 404 hides private items."""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers import visible_item_or_404
from app.models import IdeaGraph, Summary, User
from app.security import MEMBER_JOB, get_current_user
from app.services.idea_graph import request_graph
from app.services.local_ai import effective_config
from app.services.summaries import SummaryBusyError, state_of
from app.services.transcripts import TranscriptService
from app.services.yt_dlp_service import YtDlpService


class IdeaGraphResponse(BaseModel):
    id: str
    library_item_id: str
    summary_id: str
    transcript_id: str
    transcript_revision: int
    model_id: str
    state: str  # queued | running | succeeded | failed | interrupted
    nodes: list[dict]
    edges: list[dict]
    dropped: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None


def _serialize(graph: IdeaGraph) -> IdeaGraphResponse:
    state = state_of(graph)  # refreshes a row whose worker just finished: read its fields after, not before
    return IdeaGraphResponse.model_validate(graph, from_attributes=True).model_copy(update={"state": state})


def create_graph(
    item_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> IdeaGraphResponse:
    visible_item_or_404(db, item_id, current_user)
    config = effective_config(YtDlpService(db).get_app_settings())
    if not config.enabled:
        raise HTTPException(status_code=409, detail="Local AI is not configured")
    summary = (
        db.query(Summary)
        .filter(Summary.library_item_id == item_id, Summary.state == "succeeded")
        .order_by(Summary.completed_at.desc())
        .first()
    )
    if summary is None:
        raise HTTPException(status_code=409, detail="Summarize this video before mapping its ideas")
    try:
        graph, created = request_graph(db, summary, config.ai_model, current_user.id)
    except SummaryBusyError as exc:
        raise HTTPException(status_code=429, detail="Too many AI jobs in progress; try again shortly") from exc
    response.status_code = 202 if created else 200
    return _serialize(graph)


def get_latest_graph(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> IdeaGraphResponse:
    visible_item_or_404(db, item_id, current_user)
    graph = (
        db.query(IdeaGraph)
        .filter(IdeaGraph.library_item_id == item_id, IdeaGraph.state == "succeeded")
        .order_by(IdeaGraph.completed_at.desc())
        .first()
    )
    if graph is None:
        raise HTTPException(status_code=404, detail="No idea graph yet")
    return _serialize(graph)


def get_graph(
    graph_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> IdeaGraphResponse:
    graph = db.get(IdeaGraph, graph_id)
    if graph is None or TranscriptService(db).visible_item(graph.library_item_id, current_user) is None:
        raise HTTPException(status_code=404, detail="Idea graph not found")
    return _serialize(graph)


def register(app: FastAPI) -> None:
    app.post("/api/library/{item_id}/idea-graphs", response_model=IdeaGraphResponse, status_code=202, dependencies=MEMBER_JOB)(create_graph)
    app.get("/api/library/{item_id}/idea-graph", response_model=IdeaGraphResponse)(get_latest_graph)
    app.get("/api/idea-graphs/{graph_id}", response_model=IdeaGraphResponse)(get_graph)
