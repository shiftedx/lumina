"""Read-only editor views: the TMDB refresh preview and the season episode table. Neither writes."""
from __future__ import annotations

import dataclasses
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MediaTitle, User, utcnow
from app.services import media_titles, metadata_editor as me, title_metadata, titles, tmdb
from app.services.library import LibraryService
from app.services.yt_dlp_service import YtDlpService

EPISODE_CAP = 500
TABLE_FIELDS = ("name", "premiered", "runtime_minutes", "overview", "community_rating", "index_number", "index_number_end")
METHODS = {"user": "provider_id", "id": "provider_id"}


class PreviewError(Exception):
    """``code`` in tmdb_not_configured | title_locked | no_match | tmdb_unavailable; ``match`` carries the failed match record."""

    def __init__(self, code: str, match: dict | None = None) -> None:
        super().__init__(code)
        self.code, self.match = code, match


def _outcome(title: MediaTitle, key: str, current: Any, incoming: Any) -> str:
    source = (title.field_sources or {}).get(key)
    if current == incoming:
        return "same"
    if source == "user":
        kept = (title.source_values or {}).get(key)
        return "kept_lock" if kept and kept["value"] == current else "kept_edit"
    if not media_titles.would_apply(title, key, incoming, "tmdb"):
        return "kept_higher_source"
    return "new" if current is None else "update"


def refresh_preview(db: Session, title: MediaTitle) -> dict[str, Any]:
    record = YtDlpService(db).get_app_settings()
    client = tmdb.client_for(record)
    if client is None:
        raise PreviewError("tmdb_not_configured")
    if title.locked:
        raise PreviewError("title_locked")
    if title.type not in title_metadata.MATCHABLE:
        raise me.FieldError("wrong_type", title_id=title.id)
    snap = dataclasses.replace(title_metadata.snapshot(db, title), season_numbers=frozenset())  # no season calls
    try:
        fetched = title_metadata.fetch(client, snap, title_metadata.match_ai_config(record), utcnow().date())
    except tmdb.TmdbError as error:
        raise PreviewError("tmdb_unavailable") from error
    if fetched.tmdb_id is None:
        raise PreviewError("no_match", fetched.match)
    fields, images = [], []
    for key, incoming in fetched.details.fields.items():
        if key == "aired_episode_count":
            continue
        if key.startswith("images."):
            kind = key.removeprefix("images.")
            if kind not in ("Primary", "Backdrop", "Logo"):
                continue
            current, _ = me.read_field(title, key)
            path = incoming.get("tmdb") if isinstance(incoming, dict) else None
            if not path:
                continue
            same = isinstance(current, dict) and current.get("tmdb") == path
            images.append({
                "type": kind, "current_url": titles.image_url(title, kind),
                "incoming_preview_url": f"/api/metadata/candidate-image?path={quote(path, safe='')}&type={kind}",
                "outcome": "same" if same else _outcome(title, key, current, {"tmdb": path}),
            })
        elif key in me.CATALOGUE:
            current, _ = me.read_field(title, key)
            fields.append({"field": key, "current": current, "incoming": incoming, "outcome": _outcome(title, key, current, incoming)})
    method = fetched.match.get("method")
    return {"tmdb_id": fetched.tmdb_id, "match": {"method": METHODS.get(method, method)}, "fields": fields, "images": images}


def _season_ref(season: MediaTitle, count: int) -> dict[str, Any]:
    return {"id": season.id, "name": season.name, "index_number": season.index_number, "episode_count": count}


def episode_table(db: Session, user: User, series: MediaTitle, season_id: str | None) -> dict[str, Any]:
    if series.type != "series":
        raise me.TitleNotFound()
    seasons = list(db.scalars(
        select(MediaTitle).where(MediaTitle.parent_id == series.id, MediaTitle.type == "season", LibraryService.visible_title_predicate(user))
        .order_by(MediaTitle.index_number.is_(None), MediaTitle.index_number, MediaTitle.id)))
    season = next((s for s in seasons if s.id == season_id), None) if season_id else (seasons[0] if seasons else None)
    if season is None:
        raise me.TitleNotFound()
    visible = LibraryService.visible_title_predicate(user)
    counts = dict(db.execute(select(MediaTitle.parent_id, func.count()).where(
        MediaTitle.parent_id.in_([s.id for s in seasons]), MediaTitle.type == "episode", visible).group_by(MediaTitle.parent_id)).all())
    episodes = db.scalars(
        select(MediaTitle).where(MediaTitle.parent_id == season.id, MediaTitle.type == "episode", visible)
        .order_by(MediaTitle.index_number.is_(None), MediaTitle.index_number, MediaTitle.id).limit(EPISODE_CAP))
    rows = []
    for episode in episodes:
        row: dict[str, Any] = {"title_id": episode.id, "locked": bool(episode.locked), "still_url": titles.image_url(episode, "Primary")}
        row.update({key: me.read_field(episode, key)[0] for key in TABLE_FIELDS})
        row["locked_fields"] = [key for key in TABLE_FIELDS if (episode.field_sources or {}).get(key) == "user"]
        rows.append(row)
    return {"season": _season_ref(season, counts.get(season.id, 0)),
            "seasons": [_season_ref(s, counts.get(s.id, 0)) for s in seasons], "episodes": rows}
