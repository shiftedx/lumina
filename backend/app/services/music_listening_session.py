"""Durable, member-owned music listening sessions (ADR 0026)."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import LibraryItem, MusicCastTicket, MusicListeningSession, User, utcnow
from app.music_schemas import MusicTrack
from app.persistence import write_transaction
from app.services import music_library
from app.services import music_queue_entries
from app.services.library import LibraryService

MAX_ENTRIES = 5_000
MAX_POSITION_SECONDS = 7 * 24 * 60 * 60
RepeatMode = Literal["off", "all", "one"]


class MusicListeningSessionConflict(Exception):
    """The submitted revision no longer names the member's current session."""


class MusicListeningSessionInvalid(ValueError):
    pass


@dataclass(frozen=True)
class ListeningSession:
    revision: int
    queue: list[MusicTrack]
    current_index: int | None
    position_seconds: float
    repeat: RepeatMode
    shuffle: bool
    active_output: dict[str, str]
    updated_at: datetime | None
    removed_count: int = 0
    dirty: bool = False
    entries: list[dict] = None  # type: ignore[assignment]
    entry_index: int | None = None
    unavailable_policy: str = "skip"


def _browser_output(value: Any) -> dict[str, str]:
    return {"kind": "browser"}


def _finite_position(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return 0.0
    return min(float(value), MAX_POSITION_SECONDS)


def _repeat(value: Any) -> RepeatMode:
    return value if value in ("off", "all", "one") else "off"


def _visible_items(db: Session, user: User, values: Any) -> tuple[list[LibraryItem], list[bool]]:
    """Return each currently visible track in stored order, including duplicates."""
    raw = list(values) if isinstance(values, list) else []
    ids = [value for value in raw if isinstance(value, str)]
    unique = list(dict.fromkeys(ids))
    if not unique:
        return [], [False] * len(raw)
    rows = list(db.scalars(select(LibraryItem).where(
        LibraryItem.id.in_(unique), LibraryItem.kind.in_(("track", "audio")), LibraryItem.status != "missing",
        LibraryService.visible_predicate(user),
    ).execution_options(populate_existing=True)))
    by_id = {row.id: row for row in rows}
    present = [isinstance(value, str) and value in by_id for value in raw]
    return [by_id[value] for value in raw if isinstance(value, str) and value in by_id], present


def _canonical(
    db: Session,
    user: User,
    values: Any,
    *,
    revision: int,
    current_index: Any,
    position_seconds: Any,
    repeat: Any,
    shuffle: Any,
    updated_at: datetime | None,
    active_output: Any = None,
    entries: Any = None,
    entry_index: Any = None,
    unavailable_policy: Any = "skip",
) -> ListeningSession:
    if isinstance(entries, list):
        policy = unavailable_policy if unavailable_policy in ("skip", "wait") else "skip"
        logical = music_queue_entries.valid_refs(entries)
        selected_entry = entry_index if isinstance(entry_index, int) and 0 <= entry_index < len(logical) else (current_index if isinstance(current_index, int) and 0 <= current_index < len(logical) else (0 if logical else None))
        resolved = music_queue_entries.resolve(db, user, logical, policy=policy, active_entry=selected_entry)
        remapped_entry = None
        if resolved.retained_original_indices:
            if selected_entry in resolved.retained_original_indices:
                remapped_entry = resolved.retained_original_indices.index(selected_entry)
            else:
                remapped_entry = 0
        selected_index = resolved.playable_track_offsets.get(selected_entry) if selected_entry is not None else None
        if selected_index is not None:
            selected_tracks = next((entry.get("tracks", []) for entry in resolved.entries
                                    if entry.get("state") == "playable" and entry.get("kind") == logical[selected_entry].get("kind")
                                    and ((entry.get("item_id") == logical[selected_entry].get("item_id")) if entry.get("kind") == "track"
                                         else (entry.get("request_id") == logical[selected_entry].get("request_id")))), [])
            selected_id = values[current_index] if isinstance(values, list) and isinstance(current_index, int) and 0 <= current_index < len(values) and isinstance(values[current_index], str) else None
            intra = next((index for index, track in enumerate(selected_tracks) if track.id == selected_id), None)
            if intra is None:
                intra = max(0, min((current_index if isinstance(current_index, int) else selected_index) - selected_index, len(selected_tracks) - 1))
            selected_index += intra
        position = _finite_position(position_seconds) if selected_index is not None else 0.0
        if selected_index is not None:
            duration = resolved.tracks[selected_index].duration
            if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
                position = min(position, float(duration))
        return ListeningSession(revision, resolved.tracks, selected_index, position,
                                _repeat(repeat), bool(shuffle), active_output if isinstance(active_output, dict) else _browser_output(None), updated_at,
                                len(logical) - len(resolved.entries), False, resolved.entries, remapped_entry, policy)
    raw = list(values) if isinstance(values, list) else []
    items, present = _visible_items(db, user, raw)
    tracks = music_library.manifests(db, user, items)
    old_index = current_index if isinstance(current_index, int) and not isinstance(current_index, bool) else None
    selected_survives = old_index is not None and 0 <= old_index < len(raw) and present[old_index]
    if tracks:
        selected_index = sum(present[:old_index]) if selected_survives else 0
        selected_index = min(selected_index, len(tracks) - 1)
    else:
        selected_index = None
    position = _finite_position(position_seconds)
    if not selected_survives:
        position = 0.0
    elif selected_index is not None:
        duration = tracks[selected_index].duration
        if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
            position = min(position, float(duration))
    mode = _repeat(repeat)
    shuffled = shuffle if isinstance(shuffle, bool) else False
    return ListeningSession(
        revision=revision, queue=tracks, current_index=selected_index, position_seconds=position,
        repeat=mode, shuffle=shuffled, active_output=active_output if isinstance(active_output, dict) else _browser_output(None), updated_at=updated_at,
        removed_count=len(raw) - len(tracks), entries=[{"kind": "track", "item_id": track.id, "state": "playable", "tracks": [track]} for track in tracks], entry_index=selected_index, unavailable_policy="skip",
    )


def _normalized(db: Session, user: User, row: MusicListeningSession) -> ListeningSession:
    stored_entries = row.queue_entries if isinstance(row.queue_entries, list) and row.queue_entries else None
    session = _canonical(
        db, user, row.item_ids, revision=row.revision, current_index=row.current_index,
        position_seconds=row.position_seconds, repeat=row.repeat, shuffle=row.shuffle, updated_at=row.updated_at, active_output=row.active_output,
        entries=stored_entries, entry_index=row.entry_index, unavailable_policy=row.unavailable_policy,
    )
    # A shared descriptor is never a capability by itself. It survives only while
    # the owner-bound cast ticket is live and still grants every queued cast item.
    output = session.active_output
    if output.get("kind") == "sonos":
        ticket_id = output.get("ticket_id")
        ticket = db.get(MusicCastTicket, ticket_id) if isinstance(ticket_id, str) else None
        queue_ids = {track.id for track in session.queue}
        ticket_ids = set(ticket.item_ids) if ticket and isinstance(ticket.item_ids, list) else set()
        if ticket is None or ticket.user_id != user.id or ticket.expires_at <= utcnow() or not ticket_ids or not ticket_ids.issubset(queue_ids):
            session = replace(session, active_output=_browser_output(None))
    item_ids = [track.id for track in session.queue]
    dirty = (
        (list(row.item_ids) if isinstance(row.item_ids, list) else []) != item_ids
        or row.current_index != session.current_index
        or row.position_seconds != session.position_seconds
        or row.repeat != session.repeat
        or row.shuffle is not session.shuffle
        or row.active_output != session.active_output
        or row.queue_entries != music_queue_entries.valid_refs(row.queue_entries)
        or row.entry_index != session.entry_index
        or row.unavailable_policy != session.unavailable_policy
    )
    return replace(session, dirty=dirty)


def _empty() -> ListeningSession:
    return ListeningSession(0, [], None, 0.0, "off", False, {"kind": "browser"}, None, entries=[], entry_index=None, unavailable_policy="skip")


class MusicListeningSessionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def read(self, user: User) -> ListeningSession:
        """Read the current safe session, atomically pruning stored items that lost visibility."""
        while True:
            row = self.db.get(MusicListeningSession, user.id, populate_existing=True)
            if row is None:
                return _empty()
            session = _normalized(self.db, user, row)
            if not session.dirty:
                return session
            now = utcnow()
            with write_transaction(self.db, name="music_listening_session_prune"):
                prior_output = row.active_output if isinstance(row.active_output, dict) else {}
                if prior_output.get("kind") == "sonos" and session.active_output.get("kind") != "sonos" and isinstance(prior_output.get("ticket_id"), str):
                    self.db.execute(update(MusicCastTicket).where(
                        MusicCastTicket.id == prior_output["ticket_id"], MusicCastTicket.user_id == user.id,
                    ).values(expires_at=now))
                claimed = self.db.execute(update(MusicListeningSession).where(
                    MusicListeningSession.user_id == user.id, MusicListeningSession.revision == row.revision,
                ).values(
                    revision=MusicListeningSession.revision + 1,
                    item_ids=[track.id for track in session.queue], current_index=session.current_index,
                    position_seconds=session.position_seconds, repeat=session.repeat, shuffle=session.shuffle,
                    active_output=session.active_output, updated_at=now,
                    queue_entries=music_queue_entries.valid_refs(session.entries), entry_index=session.entry_index, unavailable_policy=session.unavailable_policy,
                ).execution_options(synchronize_session=False)).rowcount
            if claimed:
                return replace(session, revision=session.revision + 1, updated_at=now, dirty=False)
            self.db.expire_all()

    def replace(
        self,
        user: User,
        *,
        base_revision: int,
        item_ids: list[str],
        current_index: int | None,
        position_seconds: float,
        repeat: RepeatMode,
        shuffle: bool,
        entries: list[dict] | None = None,
        entry_index: int | None = None,
        unavailable_policy: str = "skip",
        acknowledged_output_ticket: str | None = None,
    ) -> ListeningSession:
        if entries is None and current_index is not None and (current_index < 0 or current_index >= len(item_ids)):
            raise MusicListeningSessionInvalid("Current track is outside the queue")
        if entries is None and not item_ids and current_index is not None:
            raise MusicListeningSessionInvalid("An empty queue has no current track")
        if entries is not None and entry_index is not None and entry_index >= len(entries):
            raise MusicListeningSessionInvalid("Current queue entry is outside the queue")
        if type(position_seconds) not in (int, float) or not math.isfinite(position_seconds) or not 0 <= position_seconds <= MAX_POSITION_SECONDS:
            raise MusicListeningSessionInvalid("Position must be finite and within the supported range")
        if repeat not in ("off", "all", "one"):
            raise MusicListeningSessionInvalid("Unknown repeat mode")
        now = utcnow()
        with write_transaction(self.db, name="music_listening_session_replace"):
            row = self.db.get(MusicListeningSession, user.id, populate_existing=True)
            if row is None:
                if base_revision != 0:
                    raise MusicListeningSessionConflict
                revision = 1
                row = MusicListeningSession(user_id=user.id, revision=revision)
                self.db.add(row)
            else:
                claimed = self.db.execute(update(MusicListeningSession).where(
                    MusicListeningSession.user_id == user.id, MusicListeningSession.revision == base_revision,
                ).values(revision=MusicListeningSession.revision + 1).execution_options(synchronize_session=False)).rowcount
                if not claimed:
                    raise MusicListeningSessionConflict
                revision = row.revision + 1
            # Revalidate after writer admission. Hidden, deleted, and stale legacy IDs are pruned without ever
            # returning their old metadata; duplicates and the surviving selected entry retain their order.
            source_entries = entries if entries is not None else [{"kind": "track", "item_id": item_id} for item_id in item_ids]
            session = _canonical(
                self.db, user, item_ids, revision=revision, current_index=current_index,
                position_seconds=position_seconds, repeat=repeat, shuffle=shuffle, updated_at=now,
                entries=source_entries if entries is not None else None, entry_index=entry_index, unavailable_policy=unavailable_policy,
            )
            row.revision = revision
            row.item_ids = [track.id for track in session.queue]
            row.queue_entries = music_queue_entries.valid_refs(source_entries)
            row.entry_index = session.entry_index
            row.unavailable_policy = session.unavailable_policy
            row.current_index = session.current_index
            row.position_seconds = session.position_seconds
            row.repeat = session.repeat
            row.shuffle = session.shuffle
            prior_output = row.active_output if isinstance(row.active_output, dict) else {"kind": "browser"}
            if prior_output.get("kind") == "sonos" and prior_output.get("ticket_id") != acknowledged_output_ticket:
                row.active_output = prior_output
            else:
                row.active_output = {"kind": "browser"}
            row.updated_at = now
        return replace(session, active_output=row.active_output if isinstance(row.active_output, dict) else _browser_output(None))

    def acknowledge_sonos(self, user: User, *, base_revision: int, output: dict[str, Any]) -> ListeningSession:
        """Commit a credential-free output descriptor after the speaker has acknowledged playback."""
        now = utcnow()
        with write_transaction(self.db, name="music_listening_session_sonos"):
            row = self.db.get(MusicListeningSession, user.id, populate_existing=True)
            if row is None or row.revision != base_revision:
                raise MusicListeningSessionConflict
            row.revision += 1
            row.active_output = output
            row.updated_at = now
            session = _canonical(self.db, user, row.item_ids, revision=row.revision, current_index=row.current_index,
                                 position_seconds=row.position_seconds, repeat=row.repeat, shuffle=row.shuffle, updated_at=now, active_output=output,
                                 entries=row.queue_entries if isinstance(row.queue_entries, list) and row.queue_entries else None,
                                 entry_index=row.entry_index, unavailable_policy=row.unavailable_policy)
        return replace(session, active_output=output)
