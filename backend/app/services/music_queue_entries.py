"""Resolve durable logical music queue references without exposing unavailable media."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LibraryItem, MusicAcquisition, ServiceConnector, User
from app.music_schemas import MusicTrack
from app.services import lidarr, music_library
from app.services.library import LibraryService

MAX_ENTRIES = 5_000
_STALE_AFTER = timedelta(minutes=3)
_STATES = frozenset({"pending_approval", "requested", "downloading", "awaiting_import", "failed"})


@dataclass(frozen=True)
class ResolvedQueue:
    entries: list[dict[str, Any]]
    tracks: list[MusicTrack]
    item_ids: list[str]
    retained_original_indices: list[int]
    playable_track_offsets: dict[int, int]


def valid_refs(values: Any) -> list[dict[str, str]]:
    """Strip every client-controlled field except bounded durable identities."""
    result: list[dict[str, str]] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "track" and isinstance(value.get("item_id"), str) and 0 < len(value["item_id"]) <= 64 and value["item_id"].isascii() and value["item_id"].replace("_", "").replace("-", "").isalnum():
            result.append({"kind": "track", "item_id": value["item_id"]})
        elif value.get("kind") == "acquisition" and isinstance(value.get("request_id"), str):
            try:
                result.append({"kind": "acquisition", "request_id": str(UUID(value["request_id"]))})
            except ValueError:
                continue
        if len(result) == MAX_ENTRIES:
            break
    return result


def _visible_track_items(db: Session, user: User, refs: list[dict[str, str]]) -> dict[str, LibraryItem]:
    ids = list(dict.fromkeys(ref["item_id"] for ref in refs if ref["kind"] == "track"))
    if not ids:
        return {}
    rows = db.scalars(select(LibraryItem).where(
        LibraryItem.id.in_(ids), LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing",
        LibraryService.visible_predicate(user),
    ).execution_options(populate_existing=True))
    return {row.id: row for row in rows}


def _track_order(item: LibraryItem) -> tuple[int, int, str]:
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    disc, number = metadata.get("disc_number"), metadata.get("track_number")
    return (disc if type(disc) is int and disc >= 0 else 999_999, number if type(number) is int and number >= 0 else 999_999, item.id)


def _observation_state(row: MusicAcquisition, connector: ServiceConnector | None) -> tuple[str, str | None]:
    result = row.result if isinstance(row.result, dict) else {}
    observation = result.get("observation") if isinstance(result.get("observation"), dict) else {}
    observed_at = observation.get("observed_at")
    stale = bool(connector and connector.last_error)
    if isinstance(observed_at, str):
        try:
            stamped = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            stale = stale or stamped.astimezone(UTC) < datetime.now(UTC) - _STALE_AFTER
        except ValueError:
            stale = True
    elif observation:
        stale = True
    state = observation.get("state") if isinstance(observation.get("state"), str) else row.status
    state = state if state in _STATES else "awaiting_import"
    if stale and state == "downloading":
        return "awaiting_import", "Download status is stale; waiting for library import"
    if state == "failed":
        return state, "Request failed"
    if state == "pending_approval":
        return state, "Waiting for approval"
    if state == "downloading":
        return state, "Downloading"
    return state, "Waiting for library import"


def resolve(db: Session, user: User, refs: Any, *, policy: str, active_entry: int | None) -> ResolvedQueue:
    """Resolve logical references in their queue order, retaining refs beyond the flat playback cap."""
    logical = valid_refs(refs)
    visible_by_id = _visible_track_items(db, user, logical)
    request_ids = list(dict.fromkeys(ref["request_id"] for ref in logical if ref["kind"] == "acquisition"))
    acquisitions = {row.id: row for row in db.scalars(select(MusicAcquisition).where(
        MusicAcquisition.id.in_(request_ids), MusicAcquisition.user_id == user.id,
    ))} if request_ids else {}
    connector_ids = {row.connector_id for row in acquisitions.values()}
    connectors = {row.id: row for row in db.scalars(select(ServiceConnector).where(ServiceConnector.id.in_(connector_ids)))} if connector_ids else {}

    # At most one queue's worth of album members plus one queue's worth of explicit
    # tracks is materialized. First appearances follow the member's durable order.
    albums: dict[str, list[LibraryItem]] = {}
    album_visible_beyond_cap: set[str] = set()
    album_budget = MAX_ENTRIES
    for ref in logical:
        if ref["kind"] != "acquisition":
            continue
        row = acquisitions.get(ref["request_id"])
        if row is None or row.foreign_album_id in albums:
            continue
        if album_budget:
            _album, items = lidarr.available_tracks(db, user, row.foreign_album_id, limit=album_budget)
            albums[row.foreign_album_id] = sorted(items, key=_track_order)
            album_budget -= len(items)
        else:
            _album, probe = lidarr.available_tracks(db, user, row.foreign_album_id, limit=1)
            albums[row.foreign_album_id] = []
            if probe:
                album_visible_beyond_cap.add(row.foreign_album_id)

    # Build manifests in logical reference order, never by SQL/request dictionary order.
    ordered_unique: list[LibraryItem] = []
    seen_ids: set[str] = set()
    for ref in logical:
        items = [visible_by_id[ref["item_id"]]] if ref["kind"] == "track" and ref["item_id"] in visible_by_id else (
            albums.get(acquisitions[ref["request_id"]].foreign_album_id, []) if ref["kind"] == "acquisition" and ref["request_id"] in acquisitions else []
        )
        for item in items:
            if item.id not in seen_ids:
                ordered_unique.append(item)
                seen_ids.add(item.id)
    manifests_by_id = {track.id: track for track in music_library.manifests(db, user, ordered_unique)}

    entries: list[dict[str, Any]] = []
    tracks: list[MusicTrack] = []
    offsets: dict[int, int] = {}
    retained: list[int] = []
    blocked = False
    for original_index, ref in enumerate(logical):
        resolved: list[MusicTrack] = []
        entry: dict[str, Any]
        if ref["kind"] == "track":
            track = manifests_by_id.get(ref["item_id"])
            if track is None:
                continue  # hidden/deleted IDs are pruned without their metadata
            resolved = [track]
            entry = {**ref, "state": "playable"}
        else:
            row = acquisitions.get(ref["request_id"])
            if row is None:
                continue  # another member or a deleted request
            album_items = albums.get(row.foreign_album_id, [])
            resolved = [manifests_by_id[item.id] for item in album_items if item.id in manifests_by_id]
            if resolved or row.foreign_album_id in album_visible_beyond_cap:
                entry = {**ref, "state": "playable", "name": row.album_name, "tracks": resolved}
            else:
                state, message = _observation_state(row, connectors.get(row.connector_id))
                entry = {**ref, "state": state, "name": row.album_name, "message": message}
        retained.append(original_index)
        entries.append(entry)
        if entry["state"] != "playable":
            if policy == "wait" and active_entry is not None and original_index >= active_entry:
                blocked = True
            continue
        available = [] if blocked else resolved[:MAX_ENTRIES - len(tracks)]
        if available:
            offsets[original_index] = len(tracks)
            tracks.extend(available)
        # Safe descriptors retain playable tail intent even when it is beyond the
        # engine's 5k window; never mislabel it as a failed/awaiting request.
        entry["tracks"] = available
        if not available and (resolved or row.foreign_album_id in album_visible_beyond_cap if ref["kind"] == "acquisition" else True):
            entry["message"] = "Available after the current queue"
    return ResolvedQueue(entries, tracks, [track.id for track in tracks], retained, offsets)
