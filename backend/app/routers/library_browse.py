"""Member-visible library browse and local-search routes."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import SearchScope
from app.models import PlaybackProgress, User
from app.routers import visible_item_or_404
from app.schemas import ItemProgress, LibraryChannelResponse, LibraryGroupResponse, LibraryItemResponse, LibraryPageResponse, LocalSearchMatchResponse, SearchResultResponse
from app.security import get_current_user
from app.services import streaming_gate
from app.services.library import LIBRARY_PAGE_DEFAULT_LIMIT, LIBRARY_PAGE_MAX_LIMIT, LibraryService, LibraryViewKind, decode_library_cursor, encode_library_cursor
from app.services.semantic_discovery import discovery as local_discovery, match_title_id
from app.services.title_summaries import title_summaries


SEARCH_SCOPE_TYPES = {
    "movies": ("movie", "moment"), "shows": ("series", "episode", "moment"), "anime": ("movie", "series", "episode", "moment"), "music": ("album", "artist"),
}


def register(app: FastAPI, artwork_url_resolver: Callable[[object], str | None]):
    @app.get("/api/library", response_model=LibraryPageResponse)
    def list_library(
        search: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=LIBRARY_PAGE_DEFAULT_LIMIT),
        kind: LibraryViewKind | None = Query(default=None),
        source: str | None = Query(default=None, max_length=64),
        group: str | None = Query(default=None, max_length=1000),
        status: Literal["available", "missing"] | None = Query(default=None),
        sort: Literal["recent", "title"] = Query(default="recent"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> LibraryPageResponse:
        service = LibraryService(db)
        bounded_limit = max(1, min(LIBRARY_PAGE_MAX_LIMIT, limit))
        try:
            if search and search.strip():
                # A blank-after-strip query is not a search: fall through to the
                # bounded keyset list path rather than the FTS search path (whose
                # empty-query fallback would load the whole library).
                # Indexed FTS results are paged by opaque offset so every page keeps
                # the same {items, next_cursor} shape.
                offset = 0
                if cursor is not None:
                    offset = decode_library_cursor(cursor).get("o")
                    if not isinstance(offset, int) or offset < 0:
                        raise ValueError("Invalid library page cursor")
                items, has_more = service.search_page(search, current_user, offset=offset, limit=bounded_limit)
                next_cursor = encode_library_cursor({"o": offset + bounded_limit}) if has_more else None
            else:
                items, next_cursor = service.list_items_page(
                    current_user, cursor=cursor, limit=bounded_limit, kind=kind, source=source, group=group, status=status, sort=sort
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        service.prime_owners(items)
        service.prime_media_states(items)
        responses = [service.serialize(item, current_user, summary=True) for item in items]
        if responses:  # The member's progress on the page, in one query
            progress = {row.item_id: row for row in db.scalars(select(PlaybackProgress).where(
                PlaybackProgress.user_id == current_user.id, PlaybackProgress.item_id.in_([item.id for item in items]),
            ))}
            for response in responses:
                if (row := progress.get(response.id)) is not None:
                    response.progress = ItemProgress(position_seconds=row.position_seconds or 0, duration_seconds=row.duration_seconds,
                                                     completed=bool(row.completed))
        return LibraryPageResponse(items=responses, next_cursor=next_cursor)


    @app.get("/api/library/groups", response_model=list[LibraryGroupResponse])
    def list_library_groups(
        kind: Literal["episode", "music"],
        source: str | None = Query(default=None, max_length=64),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> list[LibraryGroupResponse]:
        return LibraryService(db).list_groups(current_user, kind=kind, source=source)


    @app.get("/api/library/channels", response_model=list[LibraryChannelResponse])
    def list_library_channels(
        source: Literal["youtube"] = Query(default="youtube"),
        sort: Literal["recent", "name"] = Query(default="recent"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> list[LibraryChannelResponse]:
        """Saved YouTube grouped by channel, as Infuse groups it."""
        return LibraryService(db).list_channels(current_user, source=source, sort=sort, artwork_url=artwork_url_resolver)


    @app.get("/api/search", response_model=SearchResultResponse)
    def search_library(
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=12, ge=1, le=30),
        scope: SearchScope | None = None,  # a wall's "Find the one where…"
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db, scope="function"),
    ) -> SearchResultResponse:
        service = LibraryService(db)
        types = SEARCH_SCOPE_TYPES[scope] if scope else None
        discovery = local_discovery.search(db, current_user, q, limit=limit, types=types)
        service.prime_owners(match.library_item for match in discovery.matches if match.library_item)
        title_ids = [title_id for title_id in map(match_title_id, discovery.matches) if title_id]
        summaries = {summary.id: summary for summary in title_summaries(db, current_user, title_ids)} if title_ids else {}
        blocked = set(streaming_gate.blocked_kinds(db, current_user))  # a blocked kind's followed sources are left out, not an error
        found = tuple(match for match in discovery.matches
                      if not (match.automation and streaming_gate.url_kind(match.automation.source_url) in blocked))
        if scope is not None and scope != "music":  # a match belongs to the wall when its title (a moment's movie or episode) is in the wall's category
            found = tuple(match for match in found if getattr(summaries.get(match_title_id(match) or ""), "category", None) == scope)
        matches = [
            LocalSearchMatchResponse(
                kind=match.kind,
                id=match.record_id,
                title=match.title,
                subtitle=match.subtitle,
                score=match.score,
                lexical_score=match.lexical_score,
                semantic_score=match.semantic_score,
                match_mode=match.match_mode,
                item=service.serialize(match.library_item, current_user, summary=True) if match.library_item else None,
                source_url=match.automation.source_url if match.automation else None,
                source_type=match.automation.source_type if match.automation else None,
                title_id=match_title_id(match),
                start_ms=match.start_ms,
                media_title=summaries.get(match_title_id(match) or ""),
            )
            for match in found
        ]
        return SearchResultResponse(
            query=discovery.query,
            mode=discovery.mode,
            matches=matches,
            items=[match.item for match in matches if match.item is not None],
            index_generation=discovery.index_generation,
        )


    @app.get("/api/library/{item_id}", response_model=LibraryItemResponse)
    def get_library_item(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LibraryItemResponse:
        service = LibraryService(db)
        item = visible_item_or_404(db, item_id, current_user)
        service.prime_media_states([item])
        return service.serialize(item, current_user)

    return list_library, search_library
