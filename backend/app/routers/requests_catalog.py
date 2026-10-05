"""GET /api/requests/catalog/...: Requests discovery (TMDB + AniList), read-only for any signed-in member.

The upstream clients are blocking, so every route is a plain ``def`` (FastAPI runs it in the threadpool).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_current_user
from app.services import tmdb
from app.services.requests import anilist, catalog
from app.services.yt_dlp_service import YtDlpService

Kind = Literal["movie", "show", "anime"]
Page = Annotated[int, Query(ge=1, le=500)]


def _serve(db: Session, user: User, build: Callable[[tmdb.TmdbClient | None], dict[str, Any]]) -> dict[str, Any]:
    """Run a catalog call with the household's TMDB client (None when no key) and annotate request status."""
    try:
        response = build(tmdb.client_for(YtDlpService(db).get_app_settings()))
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from None
    except (tmdb.TmdbError, anilist.AniListError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return catalog.annotate(response, db, user)


def register(app: FastAPI) -> None:
    base = "/api/requests/catalog"

    @app.get(f"{base}/home")
    def home(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return _serve(db, user, catalog.home)

    @app.get(f"{base}/list/{{kind}}")
    def list_items(
        kind: Kind, section: str | None = Query(default=None, max_length=32), genre: str | None = Query(default=None, max_length=64),
        page: Page = 1, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        return _serve(db, user, lambda client: catalog.list_items(client, kind, section, genre, page))

    @app.get(f"{base}/anime/season")
    def anime_season(
        season: str | None = Query(default=None, max_length=8), year: int | None = Query(default=None, ge=1940, le=2100),
        sort: str = Query(default="popularity", max_length=16), page: Page = 1,
        user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        return _serve(db, user, lambda client: catalog.anime_season(client, season, year, sort, page))

    @app.get(f"{base}/anime/schedule")
    def anime_schedule(user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return _serve(db, user, catalog.anime_schedule)

    @app.get(f"{base}/search")
    def search(
        q: str = Query(default="", max_length=200), page: Page = 1,
        user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        return _serve(db, user, lambda client: catalog.search(client, q, page))

    @app.get(f"{base}/genres/{{kind}}")
    def genres(kind: Kind, user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict:
        return _serve(db, user, lambda client: catalog.genres(client, kind))

    @app.get(f"{base}/title/{{kind}}/{{item_id}}")
    def title(
        kind: Kind, item_id: Annotated[int, Path(ge=1)], user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
    ) -> dict:
        return _serve(db, user, lambda client: catalog.detail(client, kind, item_id))
