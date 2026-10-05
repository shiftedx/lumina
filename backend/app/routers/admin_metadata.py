"""TMDB metadata routes: admin Identify/unmatch/refresh, the key test, and member person images.

Titles are shared by the household, so only admins identify, unmatch or refresh. Every title
lookup applies ``visible_title_predicate``: an invisible title is 404, never 403.
"""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import (
    ConnectionTestResponse,
    IdentifyCandidate,
    IdentifyRequest,
    MetadataBulkRefreshResponse,
    MetadataRefreshResponse,
    TitleSummary,
)
from app.models import MediaTitle, User, utcnow
from app.persistence import write_transaction
from app.routers import admin_media_server  # Owns /api/admin/media-server; siblings register under its URL
from app.security import get_admin_user, get_current_user
from app.services import title_metadata, tmdb
from app.services.artwork import ArtworkError, ArtworkNotFoundError, ArtworkResponse, ArtworkService
from app.services.library import LibraryService
from app.services.media_titles import parse_item_id
from app.services.rate_limit import enforce_rate_limit
from app.services.yt_dlp_service import YtDlpService

NOT_CONFIGURED = "tmdb_not_configured"
POSTER_URL = "/api/admin/metadata/poster"
IMAGE_HEADERS = {"Cache-Control": "private, max-age=86400", "Vary": "Cookie, Authorization", "X-Content-Type-Options": "nosniff"}


def _title_or_404(db: Session, title_id: str, user: User) -> MediaTitle:
    parsed = parse_item_id(title_id)
    title = None if parsed is None else db.scalar(select(MediaTitle).where(
        MediaTitle.id == parsed, MediaTitle.type.in_(title_metadata.MATCHABLE), LibraryService.visible_title_predicate(user),
    ))
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return title


def _summary(title: MediaTitle) -> TitleSummary:
    overview = (title.metadata_json or {}).get("overview")
    return TitleSummary(id=title.id, type=title.type, name=title.name, sort_name=title.sort_name, year=title.year,
                        overview=overview if isinstance(overview, str) else None, added_at=title.arrived_at)


def _refreshed(title: MediaTitle) -> MetadataRefreshResponse:
    return MetadataRefreshResponse(title_id=title.id, metadata_due_at=title.metadata_due_at)


def _poster_url(path: object) -> str | None:
    """Candidate posters are served from the pinned TMDB art cache (w185) by the admin poster route."""
    return f"{POSTER_URL}?path={quote(path)}" if tmdb.image_url(path, tmdb.CANDIDATE_POSTER_SIZE) else None


def _image(request: Request, user: User, load: Callable[[], ArtworkResponse]) -> Response:
    enforce_rate_limit("artwork_serve", request, user_id=user.id)
    try:
        image = load()
    except ArtworkNotFoundError:
        raise HTTPException(status_code=404, detail="Artwork not found") from None
    except ArtworkError:  # tmdb.load_image maps DNS/redirect failures here too
        raise HTTPException(status_code=502, detail="Artwork is temporarily unavailable") from None
    return Response(content=image.content, media_type=image.content_type, headers=IMAGE_HEADERS)


def register(app: FastAPI, artwork: ArtworkService) -> None:
    @app.get("/api/admin/metadata/unmatched", response_model=list[TitleSummary])
    def list_unmatched(user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> list[TitleSummary]:
        return [_summary(title) for title in title_metadata.unmatched_titles(db, user)]

    @app.get(POSTER_URL)
    def candidate_poster(
        request: Request, path: str = Query(max_length=100), user: User = Depends(get_admin_user),
    ) -> Response:
        # Same size as a headshot (w185 = CANDIDATE_POSTER_SIZE); load_image validates the path.
        return _image(request, user, lambda: tmdb.load_image(artwork, path, "Person"))

    @app.get("/api/admin/titles/{title_id}/identify", response_model=list[IdentifyCandidate])
    def search_matches(
        title_id: str,
        q: str | None = Query(default=None, max_length=200),
        year: int | None = Query(default=None, ge=1870, le=2100),
        user: User = Depends(get_admin_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> list[IdentifyCandidate]:
        title = _title_or_404(db, title_id, user)
        client = tmdb.client_for(YtDlpService(db).get_app_settings())
        if client is None:
            raise HTTPException(status_code=409, detail=NOT_CONFIGURED)
        name = (q or "").strip() or title.name
        year_filter = year if year is not None else (None if q else title.year)
        try:
            candidates = client.search("movie" if title.type == "movie" else "tv", name, year_filter)
        except tmdb.TmdbError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        return [
            IdentifyCandidate(
                tmdb_id=c["tmdb_id"], name=c["name"], original_name=c["original_name"], year=c["year"], overview=c["overview"],
                poster_url=_poster_url(c["poster_path"]), score=score,
            )
            for score, c in tmdb.rank(name, year_filter, candidates)
        ]

    @app.post("/api/admin/titles/{title_id}/identify", status_code=202, response_model=MetadataRefreshResponse)
    def identify_title(
        title_id: str, payload: IdentifyRequest, user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function"),
    ) -> MetadataRefreshResponse:
        with write_transaction(db, name="tmdb_identify"):
            title = _title_or_404(db, title_id, user)
            try:
                title_metadata.identify(title, payload.tmdb_id, utcnow())
            except title_metadata.TitleLocked:
                raise HTTPException(status_code=409, detail="title_locked") from None
        return _refreshed(title)

    @app.post("/api/admin/titles/{title_id}/unmatch", response_model=MetadataRefreshResponse)
    def unmatch_title(title_id: str, user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> MetadataRefreshResponse:
        with write_transaction(db, name="tmdb_unmatch"):
            title = _title_or_404(db, title_id, user)
            try:
                title_metadata.unmatch(title)
            except title_metadata.TitleLocked:
                raise HTTPException(status_code=409, detail="title_locked") from None
        return _refreshed(title)

    @app.post("/api/admin/titles/{title_id}/refresh", status_code=202, response_model=MetadataRefreshResponse)
    def refresh_title(title_id: str, user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> MetadataRefreshResponse:
        with write_transaction(db, name="tmdb_refresh"):
            title = _title_or_404(db, title_id, user)
            if title.locked:
                raise HTTPException(status_code=409, detail="title_locked")
            title.metadata_due_at = utcnow()
        return _refreshed(title)

    @app.post("/api/admin/metadata/refresh-all", status_code=202, response_model=MetadataBulkRefreshResponse)
    def refresh_all(user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> MetadataBulkRefreshResponse:
        with write_transaction(db, name="tmdb_refresh_all"):
            queued = title_metadata.queue_all(db, user, utcnow())
        return MetadataBulkRefreshResponse(queued=queued)

    @app.post(admin_media_server.URL + "/tmdb-test", response_model=ConnectionTestResponse)
    def test_tmdb_key(_: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> ConnectionTestResponse:
        client = tmdb.client_for(YtDlpService(db).get_app_settings())
        if client is None:
            return ConnectionTestResponse(ok=False, error="TMDB API key is not configured")
        try:
            client.get("/configuration")
        except tmdb.TmdbError as exc:
            return ConnectionTestResponse(ok=False, error=str(exc))
        return ConnectionTestResponse(ok=True)

    @app.get("/api/people/{person_id}/image")
    def person_image(
        person_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> Response:
        return _image(request, user, lambda: title_metadata.load_person_image(db, artwork, person_id, user))
