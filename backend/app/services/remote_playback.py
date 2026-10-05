from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, case, or_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import RemotePlaybackProgress, User, utcnow
from app.schemas import RemotePlaybackProgressResponse, RemotePlaybackProgressUpdateRequest
from app.services.reco.events import DepthStep, RecoEventService, channel_key, depth_step


EXTRACTOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
MAX_SOURCE_IDENTITY_LENGTH = 4096
MAX_REMOTE_PLAYBACK_PROGRESS_RECORDS_PER_USER = 100


class RemotePlaybackProgressService:
    """Owns durable, member-private resume checkpoints for remote media."""

    def __init__(self, db: Session):
        self.db = db

    def get(self, source_identity: str, user: User) -> RemotePlaybackProgress | None:
        canonical = self.canonical_source_identity(source_identity)
        return self._record(canonical, user)

    def update(
        self,
        source_identity: str,
        payload: RemotePlaybackProgressUpdateRequest,
        user: User,
    ) -> RemotePlaybackProgress:
        canonical = self.canonical_source_identity(source_identity)
        if self.canonical_source_identity(payload.source_identity) != canonical:
            raise ValueError("Remote source identity does not match the request path")
        self._lock_user(user)
        now = utcnow()
        extractor, remote_id = self._identity_metadata(canonical)
        fields = payload.model_fields_set
        # Read the row as it was before this checkpoint (the member lock is held), derive depth in
        # Python and let the same atomic upsert apply it. A checkpoint the upsert refuses changes nothing, depth included.
        previous = self._record(canonical, user)
        completed = payload.completed or self._position_is_complete(payload.position_seconds, payload.duration_seconds)
        step = depth_step(
            known=previous is not None,
            last_watched_at=previous.last_watched_at if previous else None,
            was_completed=bool(previous and previous.completed),
            was_cleared=bool(previous and previous.cleared),
            max_fraction=(previous.max_fraction or 0.0) if previous else 0.0,
            completed=completed,
            position_seconds=payload.position_seconds,
            duration_seconds=payload.duration_seconds,
            now=now,
        )
        previous_revision = previous.checkpoint_revision if previous else None
        channel = self._channel_key(previous.channel_key if previous else None, extractor, payload)
        values: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "user_id": user.id,
            "source_identity": canonical,
            "source_identity_key": self.source_identity_key(canonical),
            "extractor": extractor,
            "remote_id": remote_id,
            "source_url": payload.source_url.strip(),
            "title": payload.title.strip() if payload.title else None,
            "uploader": payload.uploader.strip() if payload.uploader else None,
            "artwork_url": payload.artwork_url.strip() if payload.artwork_url else None,
            "position_seconds": payload.position_seconds,
            "duration_seconds": payload.duration_seconds,
            "completed": completed,
            "max_fraction": step.max_fraction,
            "plays": int(step.play),  # on a conflict these two are increments, see _ordered_upsert(depth=True)
            "completions": int(step.complete),
            "channel_key": channel,
            "selected_rendition_id": (
                payload.selected_rendition_id.strip() if payload.selected_rendition_id else None
            ),
            "checkpoint_client_id": payload.checkpoint_client_id,
            "checkpoint_sequence": payload.checkpoint_sequence,
            "checkpoint_revision": 1,
            "cleared": False,
            "last_watched_at": now,
            "created_at": now,
            "updated_at": now,
        }
        update_columns = {
            "source_url",
            "position_seconds",
            "duration_seconds",
            "completed",
            "checkpoint_client_id",
            "checkpoint_sequence",
            "cleared",
            "last_watched_at",
            "updated_at",
        }
        if channel is not None:
            update_columns.add("channel_key")
        if "title" in fields:
            update_columns.add("title")
        if "uploader" in fields:
            update_columns.add("uploader")
        if "artwork_url" in fields:
            update_columns.add("artwork_url")
        if "selected_rendition_id" in fields:
            update_columns.add("selected_rendition_id")
        self._ordered_upsert(
            values,
            expected_revision=payload.expected_revision,
            update_columns=update_columns,
            depth=True,
        )
        self._enforce_retention(user, preserve_source_identity=canonical)
        progress = self._record_after_write(canonical, user)
        if previous is None or progress.checkpoint_revision != previous_revision:  # the upsert accepted this checkpoint
            self._record_reco(user, progress, step, now)
        return progress

    def clear(
        self,
        source_identity: str,
        user: User,
        checkpoint_client_id: str,
        checkpoint_sequence: int,
        expected_revision: int,
    ) -> RemotePlaybackProgress:
        canonical = self.canonical_source_identity(source_identity)
        self._validate_checkpoint_token(checkpoint_client_id, checkpoint_sequence, expected_revision)
        self._lock_user(user)
        now = utcnow()
        extractor, remote_id = self._identity_metadata(canonical)
        values: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "user_id": user.id,
            "source_identity": canonical,
            "source_identity_key": self.source_identity_key(canonical),
            "extractor": extractor,
            "remote_id": remote_id,
            "source_url": "",
            "position_seconds": 0,
            "duration_seconds": None,
            "completed": False,
            "selected_rendition_id": None,
            "checkpoint_client_id": checkpoint_client_id,
            "checkpoint_sequence": checkpoint_sequence,
            "checkpoint_revision": 1,
            "cleared": True,
            "last_watched_at": now,
            "created_at": now,
            "updated_at": now,
        }
        self._ordered_upsert(
            values,
            expected_revision=expected_revision,
            # max_fraction, plays, completions and channel_key are deliberately absent: a clear keeps the member's watch depth.
            update_columns={
                "position_seconds",
                "duration_seconds",
                "completed",
                "selected_rendition_id",
                "checkpoint_client_id",
                "checkpoint_sequence",
                "cleared",
                "last_watched_at",
                "updated_at",
            },
        )
        self._enforce_retention(user, preserve_source_identity=canonical)
        return self._record_after_write(canonical, user)

    @staticmethod
    def serialize(progress: RemotePlaybackProgress) -> RemotePlaybackProgressResponse:
        return RemotePlaybackProgressResponse(
            id=progress.id,
            user_id=progress.user_id,
            source_identity=progress.source_identity,
            source_url=progress.source_url,
            extractor=progress.extractor,
            remote_id=progress.remote_id,
            title=progress.title,
            uploader=progress.uploader,
            artwork_url=progress.artwork_url,
            position_seconds=progress.position_seconds,
            duration_seconds=progress.duration_seconds,
            completed=progress.completed,
            selected_rendition_id=progress.selected_rendition_id,
            checkpoint_client_id=progress.checkpoint_client_id,
            checkpoint_sequence=progress.checkpoint_sequence,
            checkpoint_revision=progress.checkpoint_revision,
            cleared=progress.cleared,
            last_watched_at=progress.last_watched_at,
            created_at=progress.created_at,
            updated_at=progress.updated_at,
        )

    @staticmethod
    def source_identity_key(canonical_source_identity: str) -> str:
        """Fixed-width lookup key: long canonical identities stay unindexable as raw values."""
        return hashlib.sha256(canonical_source_identity.encode("utf-8")).hexdigest()

    @staticmethod
    def canonical_source_identity(source_identity: str) -> str:
        candidate = source_identity.strip()
        if not candidate or len(candidate) > MAX_SOURCE_IDENTITY_LENGTH or any(ord(char) < 32 for char in candidate):
            raise ValueError("Invalid remote source identity")
        prefix, separator, value = candidate.partition(":")
        if not separator or not value:
            raise ValueError("Invalid remote source identity")
        prefix = prefix.casefold()
        if prefix == "url":
            parts = urlsplit(value)
            if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
                raise ValueError("Invalid remote source URL identity")
            if parts.username or parts.password:
                raise ValueError("Remote source URL identities cannot contain credentials")
            try:
                port = parts.port
            except ValueError as exc:
                raise ValueError("Invalid remote source URL identity") from exc
            hostname = parts.hostname.casefold()
            if ":" in hostname:
                hostname = f"[{hostname}]"
            netloc = f"{hostname}:{port}" if port is not None else hostname
            canonical_url = urlunsplit((parts.scheme.casefold(), netloc, parts.path or "/", parts.query, ""))
            return f"url:{canonical_url}"
        if not EXTRACTOR_PATTERN.fullmatch(prefix) or not value.strip():
            raise ValueError("Invalid remote source identity")
        return f"{prefix}:{value.strip()}"

    def _query(self, user: User):
        return self.db.query(RemotePlaybackProgress).filter(RemotePlaybackProgress.user_id == user.id)

    def _record(self, canonical_source_identity: str, user: User) -> RemotePlaybackProgress | None:
        return (
            self._query(user)
            .filter(RemotePlaybackProgress.source_identity_key == self.source_identity_key(canonical_source_identity))
            .first()
        )

    def _record_after_write(self, canonical_source_identity: str, user: User) -> RemotePlaybackProgress:
        self.db.expire_all()
        progress = self._record(canonical_source_identity, user)
        if progress is None:  # The upsert always leaves either the new record or its conflict winner.
            raise RuntimeError("Remote playback checkpoint disappeared after an atomic update")
        return progress

    def _lock_user(self, user: User) -> None:
        # PostgreSQL needs an explicit per-member lock so concurrent new identities
        # cannot each observe room below the cap. SQLite serializes these writes.
        self.db.query(User.id).filter(User.id == user.id).with_for_update().one()

    def _enforce_retention(self, user: User, *, preserve_source_identity: str) -> None:
        record_limit = max(1, MAX_REMOTE_PLAYBACK_PROGRESS_RECORDS_PER_USER)
        preserve_key = self.source_identity_key(preserve_source_identity)
        other_keep_ids = [
            record_id
            for (record_id,) in (
                self._query(user)
                .filter(RemotePlaybackProgress.source_identity_key != preserve_key)
                .order_by(
                    RemotePlaybackProgress.last_watched_at.desc(),
                    RemotePlaybackProgress.updated_at.desc(),
                    RemotePlaybackProgress.created_at.desc(),
                    RemotePlaybackProgress.id.desc(),
                )
                .with_entities(RemotePlaybackProgress.id)
                .limit(record_limit - 1)
                .all()
            )
        ]
        stale = self._query(user).filter(
            RemotePlaybackProgress.source_identity_key != preserve_key
        )
        if other_keep_ids:
            stale = stale.filter(RemotePlaybackProgress.id.notin_(other_keep_ids))
        stale.delete(synchronize_session=False)
        self.db.flush()

    def _ordered_upsert(
        self,
        values: dict[str, object],
        *,
        expected_revision: int,
        update_columns: set[str],
        depth: bool = False,
    ) -> None:
        table = RemotePlaybackProgress.__table__
        dialect = self.db.get_bind().dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(table).values(**values)
        elif dialect == "postgresql":
            statement = postgresql_insert(table).values(**values)
        else:  # Keep unsupported stores from silently losing ordering guarantees.
            raise RuntimeError(f"Atomic remote playback checkpoints are unsupported for {dialect}")
        excluded = statement.excluded
        same_client = table.c.checkpoint_client_id == excluded.checkpoint_client_id
        newer_same_client = and_(
            same_client,
            excluded.checkpoint_sequence > table.c.checkpoint_sequence,
        )
        different_client_at_expected_revision = and_(
            or_(
                table.c.checkpoint_client_id.is_(None),
                table.c.checkpoint_client_id != excluded.checkpoint_client_id,
            ),
            table.c.checkpoint_revision == expected_revision,
        )
        updates = {column: getattr(excluded, column) for column in update_columns}
        updates["checkpoint_revision"] = table.c.checkpoint_revision + 1
        if depth:
            # Depth only ever grows: the deepest point wins and the counters add this checkpoint's increment (0 or 1).
            updates["max_fraction"] = case((excluded.max_fraction > table.c.max_fraction, excluded.max_fraction), else_=table.c.max_fraction)
            updates["plays"] = table.c.plays + excluded.plays
            updates["completions"] = table.c.completions + excluded.completions
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.user_id, table.c.source_identity_key],
            set_=updates,
            where=or_(newer_same_client, different_client_at_expected_revision),
        )
        self.db.execute(statement)
        self.db.flush()

    @staticmethod
    def _channel_key(current: str | None, extractor: str | None, payload: RemotePlaybackProgressUpdateRequest) -> str | None:
        """A stable key (channel id or address) always wins and never downgrades; a name key only fills an empty column."""
        found = channel_key(extractor, payload.channel_id, payload.channel_url, payload.uploader)
        if found is None:
            return current
        if current is None or not found.startswith("name:"):
            return found
        return current

    def _record_reco(self, user: User, progress: RemotePlaybackProgress, step: DepthStep, now: datetime) -> None:
        events = RecoEventService(self.db)
        if step.play:
            events.record_play(user.id, target_kind="remote", item_key=progress.source_identity_key, channel_key=progress.channel_key, fraction=step.fraction, now=now)
        if step.complete:
            events.record_complete(user.id, target_kind="remote", item_key=progress.source_identity_key, channel_key=progress.channel_key, fraction=1.0, now=now)

    @staticmethod
    def _validate_checkpoint_token(client_id: str, sequence: int, expected_revision: int) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", client_id)
            or sequence <= 0
            or sequence > 9_007_199_254_740_991
            or expected_revision < 0
            or expected_revision > 9_007_199_254_740_991
        ):
            raise ValueError("Invalid playback checkpoint token")

    @staticmethod
    def _identity_metadata(source_identity: str) -> tuple[str | None, str | None]:
        extractor, _, remote_id = source_identity.partition(":")
        if extractor == "url":
            return None, None
        return extractor, remote_id

    @staticmethod
    def _position_is_complete(position: float, duration: float | None) -> bool:
        if not duration or duration <= 0:
            return False
        return position >= max(1, duration * 0.95)
