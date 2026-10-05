"""The member's own markers on remote entries.

Every remote listing (search, popular, live, a channel page, a follow's feed) is annotated per request, after any
cache, with the member's saved copy and watch position, so a web card shows the same marker a Library still would.
Two indexed IN queries answer a whole listing; nothing here calls a provider.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import LibraryItem, PlaybackProgress, RemotePlaybackProgress, User
from app.schemas import PreviewEntry, RemoteProgressAnnotation, YouTubeSearchResult
from app.services.library import LibraryService
from app.services.remote_playback import RemotePlaybackProgressService

Entry = TypeVar("Entry", YouTubeSearchResult, PreviewEntry)


def _bare_host(host: str) -> str:
    return host.lower().removeprefix("www.")


def _is_youtube(host: str) -> bool:
    bare = _bare_host(host)
    return bare in {"youtu.be", "youtube.com"} or bare.endswith(".youtube.com")


def _youtube_id(url: str) -> str | None:
    """playbackModel.youtubeIdFromUrl: the youtu.be path or the watch page's `v`."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = _bare_host(parts.hostname or "")
    if host == "youtu.be":
        return next((segment for segment in parts.path.split("/") if segment), None)
    if host == "youtube.com" or host.endswith(".youtube.com"):
        return (parse_qs(parts.query).get("v") or [None])[0]
    return None


def _canonical_url(url: str) -> str:
    """playbackModel.canonicalRemoteSourceUrl: no fragment, lower-case host, no utm_* (nor feature/si on YouTube), sorted query.

    URLSearchParams percent-encodes `~` and Python's quote_plus does not; no provider address we list carries one.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url.strip()
    if not parts.scheme or not parts.hostname:
        return url.strip()
    host = parts.hostname.lower()
    default_port = (parts.scheme.lower(), port) in {("http", 80), ("https", 443)}
    netloc = f"{host}:{port}" if port is not None and not default_port else host
    youtube = _is_youtube(host)
    pairs = [
        (name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not name.lower().startswith("utm_") and not (youtube and name.lower() in {"feature", "si"})
    ]
    pairs.sort(key=lambda pair: pair[0])
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(pairs, safe="*"), ""))


def remote_source_identity(provider: str | None, entry_id: str | None, webpage_url: str | None) -> str | None:
    """The client's remoteSourceIdentity (playbackModel.ts), in Python: `youtube:<id>` or `url:<canonical url>`."""
    url = (webpage_url or "").strip()
    source = (provider or "").strip().lower()
    own_id = entry_id.strip() if entry_id and entry_id.strip() else None
    youtube_id = (_youtube_id(url) if url else None) or (own_id if source in {"youtube", "youtube:tab"} else None)
    if youtube_id:
        return f"youtube:{youtube_id}"
    return f"url:{_canonical_url(url)}" if url else None


def _provider(entry: YouTubeSearchResult | PreviewEntry) -> str:
    source = getattr(entry, "source", None) or (entry.capabilities.provider if entry.capabilities else None) or "youtube"
    return source.lower()


def _progress_key(identity: str | None) -> str | None:
    if identity is None:
        return None
    try:
        return RemotePlaybackProgressService.source_identity_key(RemotePlaybackProgressService.canonical_source_identity(identity))
    except ValueError:
        return None


def _marker(position: float | None, duration: float | None, completed: bool | None) -> RemoteProgressAnnotation | None:
    if position is None or (position <= 0 and not completed):
        return None
    return RemoteProgressAnnotation(position_seconds=float(position), duration_seconds=float(duration) if duration else None, completed=bool(completed))


def _saved(db: Session, user: User, remote_ids: set[str]) -> dict[tuple[str, str], tuple[str, RemoteProgressAnnotation | None]]:
    """(provider family, remote id) -> (item id, the member's progress on it). Query 1: visible, present items only."""
    if not remote_ids:
        return {}
    rows = db.execute(
        select(LibraryItem.id, LibraryItem.extractor, LibraryItem.remote_id,
               PlaybackProgress.position_seconds, PlaybackProgress.duration_seconds, PlaybackProgress.completed)
        .outerjoin(PlaybackProgress, and_(PlaybackProgress.item_id == LibraryItem.id, PlaybackProgress.user_id == user.id))
        .where(LibraryItem.remote_id.in_(sorted(remote_ids)), LibraryItem.status != "missing", LibraryService.visible_predicate(user))
        .order_by(LibraryItem.id)
    ).all()
    found: dict[tuple[str, str], tuple[str, RemoteProgressAnnotation | None]] = {}
    for item_id, extractor, remote_id, position, duration, completed in rows:
        # Stored extractors vary in case and carry sub-extractors (twitch:vod): compare the provider family.
        family = (extractor or "youtube").lower().split(":", 1)[0]
        found.setdefault((family, remote_id), (item_id, _marker(position, duration, completed)))
    return found


def _remote(db: Session, user: User, keys: set[str]) -> dict[str, RemoteProgressAnnotation | None]:
    """Query 2: the member's uncleared remote checkpoints by identity key (uq_remote_playback_progress_user_source_key)."""
    if not keys:
        return {}
    rows = db.execute(
        select(RemotePlaybackProgress.source_identity_key, RemotePlaybackProgress.position_seconds,
               RemotePlaybackProgress.duration_seconds, RemotePlaybackProgress.completed, RemotePlaybackProgress.cleared)
        .where(RemotePlaybackProgress.user_id == user.id, RemotePlaybackProgress.source_identity_key.in_(sorted(keys)))
    ).all()
    # `cleared` is filtered here: in SQL, SQLite (no ANALYZE) seeks ix_remote_playback_progress_cleared instead of the key.
    return {key: _marker(position, duration, completed) for key, position, duration, completed, cleared in rows if not cleared}


def annotate_remote_entries(db: Session, user: User, entries: Sequence[Entry]) -> list[Entry]:
    """Copies of `entries` with saved_item_id and progress for this member: two indexed IN queries."""
    if not entries:
        return []
    plans: list[tuple[str, str | None, str | None]] = []
    for entry in entries:
        provider = _provider(entry)
        identity = remote_source_identity(provider, entry.id, entry.webpage_url)
        remote_id = identity.split(":", 1)[1] if identity and identity.startswith("youtube:") else (entry.id.strip() if entry.id and entry.id.strip() else None)
        plans.append((provider, remote_id, _progress_key(identity)))
    saved = _saved(db, user, {remote_id for _, remote_id, _ in plans if remote_id})
    remote = _remote(db, user, {key for _, _, key in plans if key})
    annotated: list[Entry] = []
    for entry, (provider, remote_id, key) in zip(entries, plans, strict=True):
        match = saved.get((provider, remote_id)) if remote_id else None
        item_id, progress = match if match else (None, remote.get(key) if key else None)
        annotated.append(entry.model_copy(update={"saved_item_id": item_id, "progress": progress}))
    return annotated
