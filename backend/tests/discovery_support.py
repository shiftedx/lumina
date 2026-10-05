"""Title-library builders for discovery tests (search, recommendations, recaps, smart collections, playlists).

Ids are predictable: series ``show`` has seasons ``show-s1`` and episodes ``show-s1e2``, whose single
version is the item ``show-s1e2-v``. Movies ``m`` have the version ``m-v``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.models import LibraryItem, MediaTitle, PlaybackProgress, Summary

BASE = datetime(2026, 9, 1, 12, 0, 0)


def add_title(session, title_id: str, type_: str, name: str, *, parent_id: str | None = None, boxset_id: str | None = None,
              index: int | None = None, year: int | None = None, genres=(), people=(), overview: str | None = None,
              rating: float | None = None, days: int = 0, provider_ids: dict | None = None, **metadata) -> MediaTitle:
    metadata = {
        "genres": list(genres),
        "people": [{"person_id": None, "name": name_, "role": None, "type": "Actor"} for name_ in people],
        **metadata,
    }
    if overview is not None:
        metadata["overview"] = overview
    if rating is not None:
        metadata["community_rating"] = rating
    title = MediaTitle(
        id=title_id, type=type_, key=f"test:{title_id}", name=name, parent_id=parent_id, boxset_id=boxset_id,
        index_number=index, year=year, provider_ids=provider_ids or {}, field_sources={}, images={},
        metadata_json=metadata, created_at=BASE + timedelta(days=days), updated_at=BASE,
    )
    session.add(title)
    return title


def add_version(session, item_id: str, title_id: str, *, owner: str = "owner", visibility: str = "shared",
                status: str = "available", kind: str = "movie", file_size: int = 1_000, extra_type: str | None = None) -> LibraryItem:
    item = LibraryItem(
        id=item_id, user_id=owner, visibility=visibility, title=item_id, title_id=title_id, extra_type=extra_type,
        duration=1200, file_size=file_size, metadata_json={}, status=status, kind=kind,
    )
    session.add(item)
    return item


def add_movie(session, movie_id: str, name: str, *, owner: str = "owner", visibility: str = "shared", **fields) -> MediaTitle:
    title = add_title(session, movie_id, "movie", name, **fields)
    add_version(session, f"{movie_id}-v", movie_id, owner=owner, visibility=visibility)
    return title


def add_series(session, series_id: str, name: str, *, seasons: dict[int, int], owner: str = "owner",
               visibility: str = "shared", genres=(), people=(), days: int = 0) -> MediaTitle:
    """A series with ``seasons`` = {season number: episode count}; one version per episode."""
    series = add_title(session, series_id, "series", name, genres=genres, people=people, days=days)
    for season_no, count in seasons.items():
        season_id = f"{series_id}-s{season_no}"
        add_title(session, season_id, "season", f"Season {season_no}", parent_id=series_id, index=season_no, days=days)
        for episode_no in range(1, count + 1):
            episode_id = f"{season_id}e{episode_no}"
            add_title(session, episode_id, "episode", f"{name} {season_no}x{episode_no}", parent_id=season_id,
                      index=episode_no, days=days, overview=f"Overview of {season_no}x{episode_no}")
            add_version(session, f"{episode_id}-v", episode_id, owner=owner, visibility=visibility, kind="episode")
    return series


def add_progress(session, user_id: str, item_id: str, *, completed: bool = False, position: int = 60,
                 minutes_ago: int = 0, at: datetime | None = None) -> PlaybackProgress:
    progress = PlaybackProgress(
        id=str(uuid.uuid4()), user_id=user_id, item_id=item_id, position_seconds=position, duration_seconds=1200,
        completed=completed, last_watched_at=at or BASE - timedelta(minutes=minutes_ago),
    )
    session.add(progress)
    return progress


def add_summary(session, item_id: str, key_points: list[dict], *, overview: str = "An overview.", minutes_ago: int = 0) -> Summary:
    summary = Summary(
        id=str(uuid.uuid4()), library_item_id=item_id, transcript_id=f"t-{item_id}", transcript_revision=1,
        model_id="fake-model", state="succeeded", overview=overview, key_points=key_points, chapters=[],
        completed_at=BASE - timedelta(minutes=minutes_ago),
    )
    session.add(summary)
    return summary
