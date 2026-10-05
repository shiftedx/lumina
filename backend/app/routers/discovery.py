"""Discovery API for Media titles: More like this, Home title rows, recaps, dismiss, smart-collection rules.

Every lookup goes through ``visible_title_predicate`` / ``visible_predicate``; invisible means 404.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import (
    RecapResponse,
    SmartRuleDraft,
    SmartRuleDraftRequest,
    SmartRulePreview,
    TitleRow,
    TitleRowsResponse,
    TitleSummary,
)
from app.models import User, utcnow
from app.persistence import write_transaction
from app.routers.titles import visible_title_or_404
from app.security import get_current_user
from app.services import reco, recaps, smart_collections
from app.services.local_ai import AiConfig, LocalAiError, effective_config
from app.services.member_recommendations import MemberRecommendationPolicy
from app.services.playback import PlaybackProgressService
from app.services.reco.policy import RecommendationPolicy, annotated_titles
from app.services.summaries import SummaryBusyError
from app.services.title_summaries import title_summaries
from app.services.yt_dlp_service import YtDlpService

HOME_ROW_LIMIT = 20
BECAUSE_YOU_WATCHED_WINDOW = timedelta(days=30)
BECAUSE_YOU_WATCHED_ROWS = 3


def similar_types(title_type: str) -> tuple[str, ...]:
    """More like this compares like with like: movies (and collections) to movies, anything in a show to shows."""
    return ("movie",) if title_type in ("movie", "boxset") else ("series",)


def list_similar_titles(
    title_id: str,
    limit: int = Query(default=12, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[TitleSummary]:
    title = visible_title_or_404(db, title_id, current_user)
    if reco.enabled(db):
        # More like this is title_similar, every item annotated.
        served = RecommendationPolicy(db, refresher=None, channel_pages=None).similar(current_user, title, k=limit)
        return annotated_titles(db, current_user, served)
    ranked = MemberRecommendationPolicy(db, limit=limit).titles(current_user, anchor=title.id, types=similar_types(title.type))
    return title_summaries(db, current_user, [candidate.id for candidate in ranked])


def get_home_title_rows(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> TitleRowsResponse:
    if not reco.enabled(db):
        return _legacy_title_rows(current_user, db)
    # Home_because and home_recommended, deduplicated in order, every item annotated.
    rows: list[TitleRow] = []
    for kind, anchor, served in RecommendationPolicy(db, refresher=None, channel_pages=None).title_rows(current_user):
        items = annotated_titles(db, current_user, served)
        if not items:
            continue
        if anchor is not None:
            rows.append(TitleRow(id=f"because:{anchor.id}", kind=kind, title=f"Because you watched {anchor.name}",
                                 anchor_title_id=anchor.id, items=items))
        else:
            rows.append(TitleRow(id="recommended", kind=kind, title="Recommended for you", items=items))
    return TitleRowsResponse(rows=rows)


def _legacy_title_rows(current_user: User, db: Session) -> TitleRowsResponse:
    """ADR 0011's title rows, unchanged: what the kill switch serves."""
    policy = MemberRecommendationPolicy(db, limit=HOME_ROW_LIMIT)
    rows: list[TitleRow] = []
    for anchor in policy.recently_completed(current_user, since=utcnow() - BECAUSE_YOU_WATCHED_WINDOW, limit=BECAUSE_YOU_WATCHED_ROWS):
        ids = [title.id for title in policy.titles(current_user, anchor=anchor.id, types=similar_types(anchor.type))]
        if ids:
            rows.append(TitleRow(
                id=f"because:{anchor.id}", kind="because_you_watched", title=f"Because you watched {anchor.name}",
                anchor_title_id=anchor.id, items=title_summaries(db, current_user, ids),
            ))
    recommended = [title.id for title in policy.titles(current_user)]
    if recommended:
        rows.append(TitleRow(id="recommended", kind="recommended", title="Recommended for you", items=title_summaries(db, current_user, recommended)))
    return TitleRowsResponse(rows=rows)


def _dismiss_action(db: Session, name: str, action: Callable[[PlaybackProgressService], None]) -> Response:
    try:
        with write_transaction(db, name=name):
            action(PlaybackProgressService(db))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


def dismiss_continue_watching(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    return _dismiss_action(db, "playback_dismiss", lambda service: service.dismiss(item_id, current_user))


def undo_dismiss_continue_watching(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    return _dismiss_action(db, "playback_undismiss", lambda service: service.undismiss(item_id, current_user))


def dismiss_next_up(series_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    return _dismiss_action(db, "next_up_dismiss", lambda service: service.dismiss_next_up(series_id, current_user))


def _ai_config(db: Session, feature: str) -> AiConfig:
    """The AI config, disabled when the admin switched ``feature`` off (AppSettings.ai_features_disabled, ADR 0013)."""
    record = YtDlpService(db).get_app_settings()
    config = effective_config(record)
    return dataclasses.replace(config, ai_base_url="") if feature in (record.ai_features_disabled or []) else config


def _recap_context_or_404(db: Session, episode_id: str, user: User) -> recaps.RecapContext:
    context = recaps.load_context(db, user, episode_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return context


def get_recap(episode_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> RecapResponse:
    context = _recap_context_or_404(db, episode_id, current_user)
    return recaps.recap_response(db, current_user, context, _ai_config(db, recaps.FEATURE_KEY), now=utcnow())


def request_recap(
    episode_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> RecapResponse:
    context = _recap_context_or_404(db, episode_id, current_user)
    config = _ai_config(db, recaps.FEATURE_KEY)
    if not config.enabled:
        raise HTTPException(status_code=409, detail="ai_not_configured")
    if not context.inputs:
        raise HTTPException(status_code=409, detail="No earlier episode has a summary to recap yet.")
    try:
        _recap, created = recaps.request_recap(db, current_user, context, config.ai_model)
    except SummaryBusyError as exc:
        raise HTTPException(status_code=429, detail="Too many AI jobs in progress; try again shortly") from exc
    response.status_code = 202 if created else 200
    return recaps.recap_response(db, current_user, context, config, now=utcnow())


async def preview_smart_collection_rules(
    request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> SmartRulePreview:
    """The body is re-parsed strictly through ``parse_rule``, never the lax frozen body model."""
    try:
        rule = smart_collections.parse_rule(await request.json())
        return smart_collections.preview(db, current_user, rule)
    except smart_collections.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def draft_smart_collection_rules(
    payload: SmartRuleDraftRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> SmartRuleDraft:
    """Natural-language rule builder; hidden (404) without an AI endpoint. The model never produces titles."""
    config = _ai_config(db, smart_collections.FEATURE_KEY)
    if not config.enabled:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return smart_collections.draft_rule(db, current_user, payload.prompt, config)
    except smart_collections.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LocalAiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def register(app: FastAPI) -> None:
    app.get("/api/titles/{title_id}/similar", response_model=list[TitleSummary])(list_similar_titles)
    app.get("/api/home/title-rows", response_model=TitleRowsResponse)(get_home_title_rows)
    app.post("/api/library/{item_id}/playback/dismiss", status_code=204)(dismiss_continue_watching)
    app.delete("/api/library/{item_id}/playback/dismiss", status_code=204)(undo_dismiss_continue_watching)
    app.post("/api/titles/{series_id}/next-up/dismiss", status_code=204)(dismiss_next_up)
    app.get("/api/titles/{episode_id}/recap", response_model=RecapResponse)(get_recap)
    app.post("/api/collections/rules/preview", response_model=SmartRulePreview)(preview_smart_collection_rules)
    app.post("/api/collections/rules/draft", response_model=SmartRuleDraft)(draft_smart_collection_rules)
    app.post("/api/titles/{episode_id}/recap", response_model=RecapResponse, status_code=202)(request_recap)
