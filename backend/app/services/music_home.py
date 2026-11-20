"""Member-scoped Music Home data and durable shelf preferences."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import User
from app.persistence import write_transaction
from app.services import music_library, music_playlists
from app.services.music_listening_session import ListeningSession, MusicListeningSessionService
from app.services.user_settings import UserSettingsService

SHELVES = ("resume", "recent", "playlists", "downloads", "mixes")
_SHELF_SET = frozenset(SHELVES)
_LAYOUT_KEY = "music_home_layout"

MIXES = (
    {"kind": "rotation", "name": "Rotation"},
    {"kind": "favorites", "name": "Favorites"},
    {"kind": "unplayed", "name": "Unplayed"},
)


def default_layout() -> dict[str, list[str]]:
    return {"order": list(SHELVES), "hidden": []}


def canonical_layout(value: object, *, reject_invalid: bool = False) -> dict[str, list[str]]:
    """Return the complete canonical layout, treating corrupt stored state as a reset."""
    if not isinstance(value, dict):
        if reject_invalid:
            raise HTTPException(422, "Music Home layout must contain order and hidden lists")
        return default_layout()
    order, hidden = value.get("order"), value.get("hidden")
    valid = (
        isinstance(order, list) and isinstance(hidden, list)
        and all(isinstance(shelf, str) and shelf in _SHELF_SET for shelf in [*order, *hidden])
        and len(order) == len(set(order)) and len(hidden) == len(set(hidden))
    )
    if not valid:
        if reject_invalid:
            raise HTTPException(422, "Music Home shelf ids must be known and unique")
        return default_layout()
    result = [*order, *(shelf for shelf in SHELVES if shelf not in order)]
    return {"order": result, "hidden": list(hidden)}


def layout(db: Session, user: User) -> dict[str, list[str]]:
    settings = UserSettingsService(db).snapshot_for_user(user)
    prefs = settings.ui_prefs if isinstance(settings.ui_prefs, dict) else {}
    return canonical_layout(prefs.get(_LAYOUT_KEY))


def save_layout(db: Session, user: User, value: object) -> dict[str, list[str]]:
    saved = canonical_layout(value, reject_invalid=True)
    with write_transaction(db, name="music_home_layout"):
        settings = UserSettingsService(db).ensure_for_user(user)
        prefs = dict(settings.ui_prefs) if isinstance(settings.ui_prefs, dict) else {}
        prefs[_LAYOUT_KEY] = saved
        settings.ui_prefs = prefs
        db.flush()
    return saved


def _resume(session: ListeningSession) -> dict[str, Any] | None:
    """Expose only the checkpoint a Home client can present, never the queue/output capability."""
    if session.current_index is not None and 0 <= session.current_index < len(session.queue):
        track = session.queue[session.current_index]
        return {
            "state": "playable", "revision": session.revision, "entry_index": session.entry_index,
            "current_index": session.current_index, "position_seconds": session.position_seconds,
            "track": {
                "id": track.id, "title": track.title, "artist": track.artist, "album": track.album,
                "album_id": track.album_id, "artwork_url": track.artwork_url, "duration": track.duration,
            },
        }
    if session.entry_index is not None and 0 <= session.entry_index < len(session.entries or []):
        entry = session.entries[session.entry_index]
        if entry.get("state") != "playable":
            return {
                "state": "waiting", "revision": session.revision, "entry_index": session.entry_index,
                "current_index": None, "position_seconds": 0.0,
                "track": {"id": None, "title": entry.get("name"), "artist": None, "album": None,
                          "album_id": None, "artwork_url": None, "duration": None},
            }
    return None


def read(db: Session, user: User) -> dict[str, Any]:
    """Build Home from existing bounded projections and one visibility-pruned session read."""
    session = MusicListeningSessionService(db).read(user)
    return {
        "layout": layout(db, user),
        "resume": _resume(session),
        "recent": [item.model_dump(mode="json") for item in music_library.recently_played_albums(db, user, 12)],
        "playlists": music_playlists.list_playlists(db, user, limit=12),
        # Downloads are device-local. The client renders its own availability/empty state from this layout slot.
        "mixes": list(MIXES),
    }
