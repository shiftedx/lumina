"""Catalog status: what the household has done about each catalog key, and whether the vault has it."""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import MediaRequest, MediaTitle, User
from app.services.library import LibraryService


def parse_key(key: str) -> tuple[str, int] | None:
    kind, _, raw = key.partition(":")
    return (kind, int(raw)) if kind in {"movie", "show", "anime"} and raw.isdigit() and len(raw) <= 10 else None


def library_titles(db: Session, user: User, title_type: str, tmdb_ids: Iterable[int], tvdb_ids: Iterable[int] = ()) -> dict[tuple[str, str], str]:
    """Visible movie/series titles by ("Tmdb"|"Tvdb", id) -> title id, under the one visibility predicate (ADR 0009)."""
    tmdb, tvdb = [str(i) for i in set(tmdb_ids)], [str(i) for i in set(tvdb_ids)]
    if not tmdb and not tvdb:
        return {}
    tmdb_col, tvdb_col = func.json_extract(MediaTitle.provider_ids, "$.Tmdb"), func.json_extract(MediaTitle.provider_ids, "$.Tvdb")
    rows = db.execute(select(MediaTitle.id, tmdb_col, tvdb_col).where(
        MediaTitle.type == title_type, or_(tmdb_col.in_(tmdb), tvdb_col.in_(tvdb)), LibraryService.visible_title_predicate(user),
    )).all()
    found: dict[tuple[str, str], str] = {}
    for title_id, tmdb_id, tvdb_id in rows:
        for provider, value in (("Tmdb", tmdb_id), ("Tvdb", tvdb_id)):
            if value is not None:
                found.setdefault((provider, str(value)), title_id)
    return found


def visible_title_ids(db: Session, user: User, title_ids: Iterable[str]) -> set[str]:
    wanted = list(set(title_ids))
    if not wanted:
        return set()
    return set(db.scalars(select(MediaTitle.id).where(MediaTitle.id.in_(wanted), LibraryService.visible_title_predicate(user))))


def library_title_for(db: Session, user: User, media_type: str, tmdb_id: int | None, tvdb_id: int | None) -> str | None:
    found = library_titles(db, user, "movie" if media_type == "movie" else "series", [tmdb_id] if tmdb_id else [], [tvdb_id] if tvdb_id else [])
    return found.get(("Tmdb", str(tmdb_id))) or found.get(("Tvdb", str(tvdb_id)))


def _keep(latest: dict, slot: tuple[str, int], req: MediaRequest) -> None:
    """The newest request wins, but a declined one never hides a later or earlier live one."""
    if slot not in latest or latest[slot].status == "declined" and req.status != "declined":
        latest[slot] = req


def catalog_statuses(session: Session, user: User, keys: list[str]) -> dict[str, dict[str, Any]]:
    """{key: CatalogStatus} for "movie:603", "show:1399", "anime:16498" (an AniList id). Unknown keys are left out."""
    parsed = {key: p for key in keys if (p := parse_key(key))}
    if not parsed:
        return {}
    by_kind: dict[str, set[int]] = {}
    for kind, value in parsed.values():
        by_kind.setdefault(kind, set()).add(value)
    # Anime keys match the AniList id; movie and show keys the TMDB id, whatever kind the request became (a show TMDB
    # calls Japanese animation is requested as anime).
    clauses = [MediaRequest.anilist_id.in_(ids) if kind == "anime" else
               (MediaRequest.media_type == ("movie" if kind == "movie" else "tv")) & MediaRequest.tmdb_id.in_(ids)
               for kind, ids in by_kind.items()]
    latest: dict[tuple[str, int], MediaRequest] = {}
    for req in session.scalars(select(MediaRequest).where(or_(*clauses)).order_by(MediaRequest.created_at.desc())):
        slots = [("anime", req.anilist_id)] if req.anilist_id else []
        slots.append(("movie" if req.media_type == "movie" else "show", req.tmdb_id))
        for slot in slots:
            _keep(latest, slot, req)
    movies = {v for k, v in parsed.values() if k == "movie"} | {r.tmdb_id for r in latest.values() if r.media_type == "movie" and r.tmdb_id}
    shows = {v for k, v in parsed.values() if k == "show"} | {r.tmdb_id for r in latest.values() if r.media_type == "tv" and r.tmdb_id}
    in_vault = {
        "movie": library_titles(session, user, "movie", movies),
        "tv": library_titles(session, user, "series", shows, {r.tvdb_id for r in latest.values() if r.media_type == "tv" and r.tvdb_id}),
    }
    linked = visible_title_ids(session, user, [r.library_title_id for r in latest.values() if r.library_title_id])
    result: dict[str, dict[str, Any]] = {}
    for key, (kind, value) in parsed.items():
        req = latest.get((kind, value))
        media_type = req.media_type if req else "movie" if kind == "movie" else "tv"  # an unrequested anime key has no TMDB id
        tmdb_id = req.tmdb_id if req else None if kind == "anime" else value
        found = in_vault[media_type]
        # A linked title the viewer cannot see (member access, ADR 0019) is not available to them.
        title_id = (req and req.library_title_id in linked and req.library_title_id) or found.get(("Tmdb", str(tmdb_id))) or (req and found.get(("Tvdb", str(req.tvdb_id))))
        if req is None:
            result[key] = {"state": "available", "library_title_id": title_id} if title_id else {"state": "none"}
            continue
        state = "approved" if not title_id and req.status in ("available", "partially_available") else req.status
        status: dict[str, Any] = {"state": state, "request_id": req.id}
        if req.status == "declined" and title_id:
            status = {"state": "available"}
        if req.status == "processing" and req.progress is not None:
            status["progress"] = req.progress
        if title_id:
            status["library_title_id"] = title_id
        result[key] = status
    return result
