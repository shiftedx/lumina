from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DownloadJob(Base):
    __tablename__ = "download_jobs"
    __table_args__ = (
        Index("ix_download_jobs_user_status_created", "user_id", "status", text("created_at DESC")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # user_id seeks are served by the leading column of
    # ix_download_jobs_user_status_created.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    format_selection: Mapped[dict] = mapped_column(JSON, default=dict)
    output_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    preview_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Internal (never serialized): staging root, pending publication journal and applied rule decisions.
    routing: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Append-only outcomes of earlier attempts; a retry never erases them.
    attempts: Mapped[list] = mapped_column(JSON, default=list)
    # Set for jobs dispatched by an acquisition batch: they retry through their batch entry.
    acquisition_batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acquisition_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LibraryItem(Base):
    __tablename__ = "library_items"
    __table_args__ = (
        Index(
            "ix_library_items_visibility_downloaded_created_id",
            "visibility",
            text("downloaded_at DESC"),
            text("created_at DESC"),
            "id",
        ),
        Index("ix_library_items_user_downloaded", "user_id", text("downloaded_at DESC")),
        Index("ix_library_items_extractor_remote_id", "extractor", "remote_id"),
        # Library views (Movies/Series/Music/...) page one kind in recency order.
        Index(
            "ix_library_items_kind_downloaded_created_id",
            "kind",
            text("downloaded_at DESC"),
            text("created_at DESC"),
            "id",
        ),
        # A member's saves per channel.
        Index("ix_library_items_user_channel", "user_id", "channel_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Single-column seeks on user_id, visibility, and extractor are served by
    # the leading columns of the v4 composite indexes above.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    visibility: Mapped[str] = mapped_column(String(24), default="shared")
    extractor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    playlist_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Indexed for source dedupe and Home's already-saved check (remote_id IN ... OR webpage_url IN ...).
    webpage_url: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    availability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="available", index=True)
    # video | audio | movie | episode | track | recording; derived from metadata on every write.
    kind: Mapped[str] = mapped_column(String(16), default="video")
    # Media title this file belongs to (ADR 0009): the leaf episode/movie, or the owning movie/series for an extra.
    title_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # media_schemas.ExtraType; NULL means the file is a version of its title.
    extra_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Stable channel identity: https://www.youtube.com/channel/UC… when known, else NULL.
    channel_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HouseholdCollection(Base):
    __tablename__ = "household_collections"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name_key", name="uq_household_collection_owner_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(24), default="private", index=True)
    # Bumped by every membership add/remove/reorder; guards reorders against lost updates.
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # media_schemas.SmartCollectionRule as a dict; NULL = a manual collection. Evaluated as the viewer (ADR 0003).
    rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HouseholdCollectionMembership(Base):
    """One collection entry: a library item id, or a remote source snapshot. Never a download request."""

    __tablename__ = "household_collection_memberships"
    __table_args__ = (
        UniqueConstraint("collection_id", "library_item_id", name="uq_household_collection_item"),
        Index("ix_household_collection_memberships_collection_position", "collection_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collection_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Library entries keep only the id; display metadata is re-read through
    # current visibility so a revoked item never leaks its title (tombstoned on read).
    library_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Remote ref snapshot (same shape as WatchQueueEntry): a public source outside the vault.
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    artwork_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AcquisitionBatch(Base):
    __tablename__ = "acquisition_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="dispatching", index=True)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    format_selection: Mapped[dict] = mapped_column(JSON, default=dict)
    output_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AcquisitionBatchEntry(Base):
    __tablename__ = "acquisition_batch_entries"
    __table_args__ = (
        UniqueConstraint("batch_id", "selection_index", name="uq_acquisition_batch_selection"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    selection_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    extractor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_identity: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AcquisitionSourceReservation(Base):
    __tablename__ = "acquisition_source_reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "source_identity", name="uq_acquisition_reservation_member_source"),
        UniqueConstraint("batch_entry_id", name="uq_acquisition_reservation_entry"),
        UniqueConstraint("idempotency_key", name="uq_acquisition_reservation_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_identity: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    batch_entry_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="reserved", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AcquisitionJobOutput(Base):
    __tablename__ = "acquisition_job_outputs"
    __table_args__ = (
        UniqueConstraint("batch_entry_id", name="uq_acquisition_output_entry"),
        UniqueConstraint("download_job_id", name="uq_acquisition_output_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_entry_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    download_job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    library_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PlaybackProgress(Base):
    __tablename__ = "playback_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", name="uq_playback_progress_user_item"),
        Index("ix_playback_progress_user_completed_watched", "user_id", "completed", text("last_watched_at DESC")),
        # Recent plays across completed states (Home channel affinity, profile export).
        Index("ix_playback_progress_user_watched", "user_id", text("last_watched_at DESC")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # user_id seeks are served by ix_playback_progress_user_completed_watched.
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position_seconds: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set by "Remove from Continue watching"; any update() clears it. Progress itself is never deleted by a dismiss.
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Watch depth: the furthest fraction reached, play sessions and completions. A dismiss
    # keeps them; "Clear progress" deletes the row.
    max_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default=text("0"))
    plays: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    completions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_watched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RemotePlaybackProgress(Base):
    """A member's resume checkpoint for media that has not been acquired."""

    __tablename__ = "remote_playback_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "source_identity_key", name="uq_remote_playback_progress_user_source_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # The raw canonical identity stays for the API contract; lookups and
    # uniqueness use the fixed-width hash key so the column stays indexable.
    source_identity: Mapped[str] = mapped_column(Text, nullable=False)
    source_identity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    artwork_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_seconds: Mapped[float] = mapped_column(Float, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    selected_rendition_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkpoint_client_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    checkpoint_sequence: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    checkpoint_revision: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Watch depth: kept when the row is cleared from Continue watching.
    max_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default=text("0"))
    plays: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    completions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    # Stable channel identity; NULL when the checkpoint carried no channel id or address.
    channel_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_watched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ChatReplayAsset(Base):
    """A member's durable, bounded timed chat asset for one completed-live source.

    One row per (Household member, source identity). It records the build
    lifecycle the way a Download job records its own — a non-terminal
    ``building`` state with ``build_id``/``started_at`` for safe resume-or-
    replace, then a terminal status carrying the bounded normalized events.
    The stored events never include continuations, headers, cookies, or
    upstream addresses.
    """

    __tablename__ = "chat_replay_assets"
    __table_args__ = (
        UniqueConstraint("user_id", "source_identity_key", name="uq_chat_replay_asset_member_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_identity: Mapped[str] = mapped_column(Text, nullable=False)
    source_identity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="building", index=True)
    build_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    total_seen: Mapped[int] = mapped_column(Integer, default=0)
    bytes_processed: Mapped[int] = mapped_column(Integer, default=0)
    dropped_malformed: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    events_json: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LiveRecording(Base):
    """A member-owned, durable acquisition of a currently-live broadcast.

    One row coordinates two *sibling outputs* — a media recording published as a
    Library item, and a forward-only chat capture published as a timed chat asset
    (``ChatReplayAsset``) — with EXPLICIT partial outcomes. The overall ``status``
    is only ``completed`` when both requested outputs reached a documented
    terminal success; a usable media recording can survive chat failure and a
    chat asset can survive media-finalization failure, and either case is
    reported as ``partial`` rather than as false success or false total failure.

    A live source has no known total duration, so ``status`` moves through
    ``queued`` -> ``live`` -> (``stopping`` on a deliberate stop) -> ``finalizing``
    -> a terminal ``completed``/``partial``/``failed``/``cancelled`` without ever
    pretending a percentage of a fixed length. Media capture begins at the
    current edge and chat capture begins when Lumina connects; neither promises
    earlier history. The row and its durably-captured outputs survive a process
    restart: a non-terminal recording is deterministically recovered or
    finalized to a partial outcome on the next start.

    Member-scoped idempotency is enforced at the write layer by a partial unique
    index over (``user_id``, ``source_identity_key``) restricted to non-terminal
    rows, so a duplicate submission or an Acquisition-batch reconciliation can
    never create a second active recording for the same member and source.
    """

    __tablename__ = "live_recordings"
    __table_args__ = (
        # At most one active (non-terminal) recording per member + source. A
        # terminal row is exempt so a member can record the same source again
        # later. Re-checked inside the write transaction via the IntegrityError
        # this raises, never by a read-then-write race.
        Index(
            "uq_live_recording_member_active",
            "user_id",
            "source_identity_key",
            unique=True,
            sqlite_where=text("status NOT IN ('completed','partial','failed','cancelled')"),
        ),
        Index("ix_live_recordings_status_created", "status", text("created_at DESC")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_identity: Mapped[str] = mapped_column(Text, nullable=False)
    source_identity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Overall live-aware acquisition state (never a fake duration percentage):
    # queued | live | stopping | finalizing | completed | partial | failed | cancelled.
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    # Deliberate stop is distinct from abrupt cancel: stop finalizes usable
    # outputs, cancel discards in-flight work. Both are durable so recovery after
    # a restart honors the member's last intent.
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Sibling media output: pending | recording | finalizing | completed | failed.
    media_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    media_failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Sibling chat output: pending | capturing | completed | unavailable | failed.
    chat_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    chat_failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    format_selection: Mapped[dict] = mapped_column(JSON, default=dict)
    output_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    # Bookkeeping for restart recovery: how many times this recording has been
    # (re)claimed by a worker. Bounded so a poison recording cannot loop forever.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # -- scheduling + from-start (issue #98) --------------------------------
    # The best-available provider start time for an UPCOMING broadcast. When set,
    # the recording starts durably in ``waiting`` and the waiter wakes near this
    # time with bounded retries; a schedule change durably updates it. Null for a
    # source that was already live when recording began.
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Member INTENT (never a raw yt-dlp flag): from_start | live_edge. Default
    # ``live_edge`` preserves the exact #97 record-from-now semantics.
    start_intent: Mapped[str] = mapped_column(String(16), nullable=False, default="live_edge")
    # What to do when from-start is unavailable at connect: allow_live_edge (honest
    # fallback, recorded as such) | require_choice (refuse to silently fall back).
    fallback_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="allow_live_edge")
    # Whether the inspected source actually offered from-start (best-effort). Null
    # until connect. Recorded so the outcome never implies from-start was possible.
    from_start_supported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Where media capture began: pending | source_beginning | live_edge. The honest
    # answer to "did capture begin at the beginning or the live edge?".
    capture_origin: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # From-start history honesty: pending | complete | partial | from_edge. ``partial``
    # is the explicit partial-history condition; from-start NEVER reports ``complete``
    # unless the whole-broadcast-from-the-start history was actually captured.
    history: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    # Set when from-start was unavailable and the saved intent required an explicit
    # choice: the acquisition failed WITHOUT silently recording the edge, and the
    # member can re-record from the edge deliberately.
    awaiting_fallback_choice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Why a scheduled/waiting acquisition ended abnormally before capture:
    # source_cancelled | excessive_delay | auth_expired | from_start_unavailable.
    # Null when the recording connected normally. Surfaced so a cancelled
    # broadcast reads distinctly from a generic failure.
    waiting_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Why media capture ended: source_ended | stopped | time_limit |
    # size_limit | disk_low | owner_disabled | shutdown | interrupted.
    media_end_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # The member chose to keep this recording: the retention sweep never removes it.
    kept: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The media-edge start time; the chat capture bases its media offsets on it so
    # the completed Library item and its timed chat asset stay synchronized.
    recording_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recording_stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LibraryNote(Base):
    """A member's plain-text note on a Library item, optionally pinned to a playback time.

    ``private`` notes are author-only; ``household`` notes are readable by every
    member who can view the item.
    """

    __tablename__ = "library_notes"
    __table_args__ = (
        Index("ix_library_notes_item_visibility_user", "item_id", "visibility", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(24), default="private", index=True)
    timestamp_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LibraryTag(Base):
    __tablename__ = "library_tags"
    __table_args__ = (
        Index("ix_library_tags_item_user", "item_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tag: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_settings_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    download_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    automation_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    ui_prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    notification_prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    remote_playback_cache: Mapped[dict] = mapped_column(JSON, default=dict)
    # 2.6.0 Requests: where request emails go (NULL = none) and whether this member wants them.
    notify_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    notify_requests: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MemberInterest(Base):
    """One durable Interest category selected by one Household member."""

    __tablename__ = "member_interests"
    __table_args__ = (UniqueConstraint("user_id", "category_key", name="uq_member_interest_category"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    category_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MemberRecommendationSuppression(Base):
    """One reversible Discovery suppression owned by one Household member.

    ``scope`` is ``item`` (one stable Media-source identity) or ``channel`` (one
    stable source-channel identity). ``target_key`` is the same identity the
    shared recommendation policy derives everywhere else (a ``_source_key`` for
    an item, a casefolded channel key for a channel), so a suppressed candidate
    is filtered by its own key on every surface. The remaining columns hold only
    safe display metadata used to identify a suppression in Settings; no raw
    search text is ever persisted here. Suppression is separate from ownership
    and following: it never removes a Library item, erases playback history, or
    touches a channel Source automation.
    """

    __tablename__ = "member_recommendation_suppressions"
    __table_args__ = (
        UniqueConstraint("user_id", "scope", "target_key", name="uq_member_suppression_target"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # A url:-branch _source_key can reach ~2KB (webpage_url is bounded at 2048),
    # so this is Text, not a bounded String: the stored key must equal the
    # policy's _source_key byte-for-byte to match a candidate, so it can never be
    # hashed or truncated. The (user_id, scope, target_key) unique constraint
    # already indexes it for the only lookups we run; no separate index is added.
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The stable channel key beside the legacy name key in target_key (scopes channel, fewer),
    # so a rollback to 1.8.0 still matches by name.
    channel_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SearchHistoryEntry(Base):
    """One Household member's past search query.

    Server-owned so history never leaks between accounts sharing a browser (the
    prior localStorage store was browser-global). ``query_key`` is the
    case-folded query, unique per member, so re-searching the same text touches
    ``searched_at`` (moving it to the front) instead of duplicating a row. The
    service caps each member at the 50 most recent entries.
    """

    __tablename__ = "search_history_entries"
    __table_args__ = (UniqueConstraint("user_id", "query_key", name="uq_search_history_user_query"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    query_key: Mapped[str] = mapped_column(String(500), nullable=False)
    searched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class SourceAutomation(Base):
    __tablename__ = "source_automations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="generic_url", index=True)
    # The channel avatar resolved by the automation sweep's most recent
    # successful inspection (backend/app/services/yt_dlp_service.py
    # resolve_channel_avatar); lets Subscriptions render an avatar instantly
    # instead of waiting on a live per-channel check. Never a cookie/token URL.
    artwork_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Automatic saving is an explicit opt-in per follow; following alone never acquires.
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False)
    format_selection: Mapped[dict] = mapped_column(JSON, default=dict)
    output_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    duplicate_policy: Mapped[str] = mapped_column(String(48), default="skip_same_source")
    max_items_per_run: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_items_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backfill_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_run_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # Last-known newest entries from the latest successful check (bounded); a failed
    # check keeps them so the follow feed stays useful through a provider outage.
    feed_entries: Mapped[list] = mapped_column(JSON, default=list)
    # Schema-only for now: a later stage implements Automation run lease semantics.
    run_lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        Index("ix_automation_runs_automation_started", "automation_id", text("started_at DESC")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    automation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, default=0)
    manual_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AutomationDecision(Base):
    __tablename__ = "automation_decisions"
    __table_args__ = (
        Index("ix_automation_decisions_automation_action_created", "automation_id", "action", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    automation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppMaintenanceState(Base):
    """Durable cursors for resumable background maintenance sweeps."""

    __tablename__ = "app_maintenance_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    temp_root: Mapped[str] = mapped_column(Text, nullable=False)
    archive_path: Mapped[str] = mapped_column(Text, nullable=False)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    max_active_jobs_per_user: Mapped[int] = mapped_column(Integer, default=25)
    min_free_disk_mb: Mapped[int] = mapped_column(Integer, default=2048)
    max_playback_sessions: Mapped[int] = mapped_column(Integer, default=2)
    ffmpeg_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    yt_dlp_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    ui_prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    # Local AI endpoint. NULL = use the env default; "" = disabled.
    ai_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ai_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # write-only; never serialized
    ai_max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asr_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    asr_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Scheduled database backups: daily, keeping the newest N scheduled copies.
    backup_daily: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_keep: Mapped[int] = mapped_column(Integer, default=7)
    # Live-recording retention; 0 disables each bound. Kept recordings are exempt.
    recording_keep_days: Mapped[int] = mapped_column(Integer, default=0)
    recording_max_gb: Mapped[int] = mapped_column(Integer, default=0)
    # Media server (media-vault pass). Secrets below are write-only and never serialized.
    jellyfin_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    # secrets.token_hex(32), created lazily; derives ServerId and image-tag HMACs.
    jellyfin_server_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # NULL = env LUMINA_TMDB_API_KEY; nulled in backups next to ai_api_key.
    tmdb_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_language: Mapped[str] = mapped_column(String(16), default="en-US", server_default=text("'en-US'"), nullable=False)
    # NULL or "" = no external embedding model. A change re-indexes in the background. Only a switch between local
    # models keeps the old vectors serving until the new index is complete; the previous external model is no longer
    # a source, so its vectors stop serving at once (services/embeddings.py).
    ai_embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # JSON list of disabled AI feature keys; [] = every configured AI feature on (ADR 0013).
    ai_features_disabled: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"), nullable=False)
    # Off by default: each TheIntroDB lookup tells a third party what the household owns.
    introdb_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    hwaccel: Mapped[str] = mapped_column(String(8), default="auto", server_default=text("'auto'"), nullable=False)  # auto|off|qsv|vaapi
    transcode_cache_gb: Mapped[int] = mapped_column(Integer, default=10, server_default=text("10"), nullable=False)
    # On-device models. NULL = the catalog default / automatic threads.
    local_search_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    local_speech_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_threads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The household's old Jellyfin server that members import watch history from; NULL = importing off (ADR 0010).
    jellyfin_import_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Folder names whose titles are Anime rather than Movies or Shows; [] = nothing is anime.
    anime_folders: Mapped[list] = mapped_column(JSON, default=lambda: ["Anime"], server_default=text("'[\"Anime\"]'"), nullable=False)
    # 2.1.0 (ADR 0016): household members may edit title details too; vault owners always can.
    members_edit_metadata: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    # 2.1.0 library automation: the server-local hour (0-23) at which nightly scans become due.
    library_scan_night_hour: Mapped[int] = mapped_column(Integer, default=3, server_default=text("3"), nullable=False)
    # 2.6.0 Requests (ADR 0018). smtp_password is write-only, never serialized, and nulled in backups.
    requests_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    smtp_host: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_security: Mapped[str] = mapped_column(String(8), default="starttls", server_default=text("'starttls'"), nullable=False)  # starttls|ssl|none
    smtp_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 2.8.0 member access: the address invite links use (e.g. https://lumina.example.com); NULL = the request's own origin.
    public_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The home-network address (e.g. http://lumina.home.arpa), LAN HTTP mode only; NULL = none (ADR 0001 local address).
    local_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Default MemberAccess preset an invite starts from (services/member_access.py field names); {} = no limits.
    invite_permissions: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False)
    # 2.9.0: vault owners must turn on two-step verification before using owner settings.
    require_owner_two_factor: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(24), default="viewer", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    # New members start pending so first-run onboarding can prepare their Home.
    onboarding_status: Mapped[str] = mapped_column(
        String(24), default="pending", server_default=text("'pending'"), nullable=False
    )
    # 2.9.0 two-step verification (services/two_factor.py). The TOTP secret is AES-GCM encrypted under a key derived from
    # the app secret; it is a pending enrollment until totp_enabled_at is set. Recovery codes are stored as SHA-256 digests.
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)  # replay guard
    totp_recovery: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"), nullable=False)
    # Consecutive wrong codes; at two_factor.TOTP_LOCK_AFTER codes pause until totp_locked_until (recovery codes still work).
    totp_failures: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    totp_locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StorageRoot(Base):
    """An admin-registered directory: ``managed`` (Lumina writes) or ``external`` (read-only)."""

    __tablename__ = "storage_roots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    minimum_free_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # Managed: the root ID written to the sentinel. External: the observed st_dev.
    identity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    # 2.1.0 library automation. External roots only; a managed root keeps the defaults.
    scan_schedule: Mapped[str] = mapped_column(String(8), default="off", server_default=text("'off'"), nullable=False)  # off|15m|1h|6h|nightly
    watch_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    watch_interval_s: Mapped[int] = mapped_column(Integer, default=300, server_default=text("300"), nullable=False)  # 60|300|900
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StorageRuleSet(Base):
    """The single ordered routing rule set; replaced whole under an expected revision."""

    __tablename__ = "storage_rule_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # None routes unmatched media to the built-in Library root.
    default_root_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MediaArtifact(Base):
    """One physical file, addressed as root + relative path; never a raw absolute path."""

    __tablename__ = "media_artifacts"
    __table_args__ = (UniqueConstraint("root_id", "relative_path", name="uq_media_artifact_root_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    root_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    ownership: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lifecycle: Mapped[str] = mapped_column(String(16), default="available", index=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # ffprobe facts keyed by the file's size:mtime fingerprint; never alters media bytes.
    probe: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {fingerprint, segments: [...], introdb_checked_at}; same size:mtime fingerprint as probe, separate so re-probing never wipes segments.
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # External scan fingerprint: unchanged files are not re-hashed; renames re-link by inode or hash prefix.
    mtime_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inode: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LibraryItemArtifact(Base):
    """Links a library entry to its file; several entries may share one artifact."""

    __tablename__ = "library_item_artifacts"

    library_item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSession(Base):
    __tablename__ = "app_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    client_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Transcript(Base):
    """One immutable transcript revision of a library item.

    Access always follows the item's own visibility. A source with a new cue
    digest becomes a new revision; old revisions stay for citations.
    """

    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("library_item_id", "language", "source_kind", "revision", name="uq_transcript_revision"),
        UniqueConstraint("library_item_id", "language", "source_kind", "source_digest", name="uq_transcript_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    library_item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # source_caption | asr | synced | translated
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    cue_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model_label: Mapped[str | None] = mapped_column(String(120), nullable=True)  # ASR engine/model revision
    # Source transcript id, "sidecar:{filename}" (ingested subtitle file) or "stream:{index}" (embedded text stream); NULL for original sources.
    derived_from: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TranscriptCue(Base):
    """Cue ID is (transcript_id, ordinal); stable because revisions are immutable."""

    __tablename__ = "transcript_cues"

    transcript_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # [[start_ms, end_ms, "word"], ...] from ASR word timestamps; NULL when the source had none.
    words: Mapped[list | None] = mapped_column(JSON, nullable=True)


class Summary(Base):
    """One local summary attempt of one transcript revision. Access follows the library item."""

    __tablename__ = "summaries"
    __table_args__ = (Index("ix_summaries_transcript_model", "transcript_id", "model_id", "state"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    library_item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    transcript_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transcript_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # queued | running | succeeded | failed | canceled
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points: Mapped[list] = mapped_column(JSON, default=list)  # [{text, cue_ordinals, start_ms}]
    chapters: Mapped[list] = mapped_column(JSON, default=list)  # [{title, cue_ordinal, start_ms}]
    dropped_points: Mapped[int] = mapped_column(Integer, default=0)  # ungrounded points removed
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IdeaGraph(Base):
    """Bounded concept map of one summary; every node and edge cites transcript cues. Access follows the item."""

    __tablename__ = "idea_graphs"
    __table_args__ = (Index("ix_idea_graphs_summary_model", "summary_id", "model_id", "state"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    library_item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    summary_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transcript_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transcript_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # queued | running | succeeded | failed
    nodes: Mapped[list] = mapped_column(JSON, default=list)  # [{id, label, cue_ordinals, start_ms}]
    edges: Mapped[list] = mapped_column(JSON, default=list)  # [{source, target, label, cue_ordinals, start_ms}]
    dropped: Mapped[int] = mapped_column(Integer, default=0)  # ungrounded concepts/relations removed
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AsrJob(Base):
    """One Enrichment job attempt on a library item (S51; kinds since v3). Access follows the library item."""

    __tablename__ = "asr_jobs"
    __table_args__ = (
        Index("ix_asr_jobs_item_model", "library_item_id", "model_id", "state"),
        # pump_pending() admits the oldest pending rows.
        Index("ix_asr_jobs_state_created", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    library_item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # asr | sync | translate | segments | captions (media_schemas.EnrichmentJobKind)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="asr", server_default=text("'asr'"))
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # kind-specific, see the Contract reference
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # pending | queued | running | succeeded | failed | canceled | interrupted
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    transcript_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AccountToken(Base):
    """Single-use local invitation or password-reset link; only the token digest is stored."""

    __tablename__ = "account_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # "invite" | "reset"
    role: Mapped[str | None] = mapped_column(String(24), nullable=True)  # invite: server-bound role
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # reset target / redeemed invitee
    issued_by: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # 2.8.0 external invites: who it was emailed to, the member access preset redeeming applies, when it was last sent.
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    access: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ImportRun(Base):
    """One resumable scan of an external root; its id is the scan generation."""

    __tablename__ = "import_runs"
    __table_args__ = (Index("ix_import_runs_root_created", "root_id", text("created_at DESC")),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    root_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    visibility: Mapped[str] = mapped_column(String(24), default="private")
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    # JSON list of the last visited root-relative path parts; scans resume strictly after it.
    cursor: Mapped[str] = mapped_column(Text, default="[]")
    counters: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage: Mapped[str] = mapped_column(String(16), default="complete")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_observation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    trigger: Mapped[str | None] = mapped_column(String(16), nullable=True)  # manual|scheduled|watch; NULL = a 2.0.1 run, read as manual
    # NULL = a full run; else 1-200 {"dir", "deep"} entries (library_import.ScopeEntry). none_as_null: Python None must be
    # SQL NULL, because "scope IS NULL" is the full-run test (the scheduler's anchor and the 2.0.1 rollback).
    scope: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)


class ImportEntry(Base):
    """A scan outcome worth an admin's attention (failed/skipped); indexed files are not listed."""

    __tablename__ = "import_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    library_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WatchQueue(Base):
    """A member's deliberate play-next list; revision guards reorders."""

    __tablename__ = "watch_queues"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WatchQueueEntry(Base):
    """One queued MediaRef: a library item id, or a remote source snapshot. Never a download request."""

    __tablename__ = "watch_queue_entries"
    __table_args__ = (Index("ix_watch_queue_entries_user_position", "user_id", "position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Library entries keep only the id; display metadata is re-read through
    # current visibility so a revoked item never leaks its title.
    library_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    artwork_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


WALL_TYPES_SQL = "type IN ('movie', 'series')"  # the only types a wall lists; titles.WALL_TYPES repeats it for the planner
MUSIC_TYPES_SQL = "type IN ('album', 'artist')"  # the music titles; titles.MUSIC_TYPES repeats it for the planner
# A new title's category until db.CATEGORY_SQL decides anime (the scan's categories.refresh_category, schema step 8).
CATEGORY_OF_TYPE = {"movie": "movies", "series": "shows", "season": "shows", "episode": "shows"}


def default_category(context) -> str | None:  # noqa: ANN001 - SQLAlchemy's execution context
    return CATEGORY_OF_TYPE.get(context.get_current_parameters().get("type"))


class MediaTitle(Base):
    """A Series, Season, Episode, Movie or BoxSet above library items (ADR 0009). Ownerless: visibility flows up from items."""

    __tablename__ = "media_titles"
    __table_args__ = (
        # Wall pages seek these; the sort expression is titles.SORT_NAME verbatim. Partial, so an
        # unanalyzed planner can never walk every episode through them instead of seeking parent_id (titles.WALL_TYPES).
        Index("ix_media_titles_type_sort", "type", text("coalesce(sort_name, name) COLLATE NOCASE"), "id", sqlite_where=text(WALL_TYPES_SQL)),
        Index("ix_media_titles_type_created", "type", text("created_at DESC"), "id", sqlite_where=text(WALL_TYPES_SQL)),
        # Recently added (titles.ADDED): superseded ix_media_titles_type_created, which stays so a rollback finds it.
        Index("ix_media_titles_type_added", "type", text("coalesce(added_at, created_at) DESC"), "id", sqlite_where=text(WALL_TYPES_SQL)),
        # Library gallery: category walls and music walls. Partial; each query repeats its WHERE as literals.
        Index("ix_media_titles_category_sort", "category", text("coalesce(sort_name, name) COLLATE NOCASE"), "id", sqlite_where=text(WALL_TYPES_SQL)),
        Index("ix_media_titles_category_added", "category", text("coalesce(added_at, created_at) DESC"), "id", sqlite_where=text(WALL_TYPES_SQL)),
        Index("ix_media_titles_music_sort", "type", text("coalesce(sort_name, name) COLLATE NOCASE"), "id", sqlite_where=text(MUSIC_TYPES_SQL)),
        Index("ix_media_titles_music_added", "type", text("coalesce(added_at, created_at) DESC"), "id", sqlite_where=text(MUSIC_TYPES_SQL)),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4; never derived from a path
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # series | season | episode | movie | boxset | album | artist
    # season -> series, episode -> season, album -> artist. Movies, boxsets and artists: NULL (boxset membership is boxset_id).
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # movie -> boxset; never emitted as Jellyfin ParentId.
    boxset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Identity lookup key; versions converge on it and renames re-key instead of re-creating.
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    root_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # root that image paths are relative to; NULL for boxset
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    index_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # season no. / episode no.
    index_number_end: Mapped[int | None] = mapped_column(Integer, nullable=True)  # multi-episode end
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_ids: Mapped[dict] = mapped_column(JSON, default=dict)  # {"Tmdb": "603", "Imdb": "tt…", "Tvdb": "…"}
    field_sources: Mapped[dict] = mapped_column(JSON, default=dict)  # {field: user|nfo|tmdb|path}; write via media_titles.apply_field
    images: Mapped[dict] = mapped_column(JSON, default=dict)  # {ImageType: {"path": rel, "tag": …} | {"tmdb": "/x.jpg"}}
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # The only TMDB refresh queue state; NULL = not due.
    metadata_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # When the media arrived (db.ADDED_AT_SQL): a leaf's earliest file mtime, a folder's newest child. NULL: no file time.
    added_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Which Library tab: movies | shows | anime for movie, series, season, episode (db.CATEGORY_SQL); NULL for boxset, album, artist.
    category: Mapped[str | None] = mapped_column(String(8), nullable=True, default=default_category)
    # Lock this item (ADR 0016): no non-user write reaches any field; the scanner's structural writes continue.
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    # {field: {"source": "nfo"|"tmdb"|"path"|None, "value": ...}} for user-sourced fields and every field of a locked item:
    # what the sources currently offer, for revert. Write only through media_titles helpers. Deferred: walls never load it.
    source_values: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False, deferred=True)
    # 2.8.0 member access: normalized certificate (G … NC-17, TV-Y … TV-MA) and its rank on the title's scale (film for movies, TV for series; member_access.rating_rank);
    # NULL = unrated. Source in field_sources["rating"] (user > nfo > tmdb); seasons and episodes copy their series'.
    rating: Mapped[str | None] = mapped_column(String(8), nullable=True)
    rating_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def arrived_at(self) -> datetime:
        """Recently added's date: when the files arrived, else when a scan first met the title."""
        return self.added_at or self.created_at


class DeviceToken(Base):
    """A Connected app credential (ADR 0010): one per (member, device); only the digest is stored."""

    __tablename__ = "device_tokens"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_device_tokens_user_device"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # user_id seeks are served by the leading column of uq_device_tokens_user_device.
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # jellyfin | agent | app_password
    scope: Mapped[str] = mapped_column(String(8), nullable=False)  # read | write
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # security.session_digest(token)
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)  # client DeviceId; agent tokens: a uuid4
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client: Mapped[str | None] = mapped_column(String(120), nullable=True)
    client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A jellyfin token minted by signing in with an app password: revoking that app password signs the app out too.
    app_password_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class MemberFavorite(Base):
    """A member's favorite Media title or Library item (Jellyfin IsFavorite)."""

    __tablename__ = "member_favorites"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # media_titles.id or library_items.id
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Person(Base):
    """A TMDB cast/crew reference; id = media_titles.synthetic_id(f"tmdb-person:{tmdb_id}")."""

    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    profile_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class NfoPerson(Base):
    """A person NFO files credit, keyed by normalised name: id = media_titles.person_name_id(name).

    Rows come only from scanned credits; titles' people refs point here by ``person_id``. Not member data.
    """

    __tablename__ = "nfo_people"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # relative to LUMINA_PEOPLE_DIR
    tmdb_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # "/x.jpg": an image.tmdb.org <thumb> or the TMDB top-up
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PersonOverride(Base):
    """A household edit of one person across every title that credits them (#164): a name and/or an uploaded photo.

    ``id`` is the person id the titles' people refs carry (a TMDB person or an NFO name id). Read-time overlay: refs,
    TMDB refresh and scans keep their own values, so removing a column value is the revert. History: title_edits rows
    with title_id = this id and field ``person.name`` / ``person.photo``.
    """

    __tablename__ = "person_overrides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo: Mapped[str | None] = mapped_column(String(64), nullable=True)  # title_uploads.sha256
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SearchEmbedding(Base):
    """One target's vector under one embedding model; vector spaces never mix."""

    __tablename__ = "search_embeddings"

    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # media_titles.id or library_items.id
    model_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # title | item
    signature: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of the embedded text
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # array("f", values).tobytes()


class SeriesRecap(Base):
    """A cached "Previously on" recap for one episode title; served only if the requester sees every input item."""

    __tablename__ = "series_recaps"
    __table_args__ = (
        UniqueConstraint("episode_title_id", "model_id", "inputs_digest", name="uq_series_recaps_episode_model_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    series_id: Mapped[str] = mapped_column(String(36), nullable=False)
    episode_title_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    inputs_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_item_ids: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # queued | running | succeeded | failed
    points: Mapped[list] = mapped_column(JSON, default=list)  # [{text, citations: [{episode_id, cue_ordinal}]}]
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TitleArtwork(Base):
    """Derived facts about one title image: preview, colours, source size. Disposable; keyed by source identity."""

    __tablename__ = "title_artwork"

    title_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    image_type: Mapped[str] = mapped_column(String(16), primary_key=True)  # Primary | Backdrop | Logo
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)  # art_urls.source_key(); a mismatch = stale
    state: Mapped[str] = mapped_column(String(12), nullable=False)  # ready | failed | unsupported
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)  # source pixels
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preview: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # <= 600 bytes
    preview_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # image/webp | image/jpeg
    dominant: Mapped[str | None] = mapped_column(String(7), nullable=True)  # "#rrggbb"
    accent: Mapped[str | None] = mapped_column(String(7), nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)  # stable, content-free reason
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TitleEdit(Base):
    """One changed field of one editor save. A save, revert, bulk op, image write or undo is one batch (ADR 0016)."""

    __tablename__ = "title_edits"
    __table_args__ = (
        Index("ix_title_edits_title_created", "title_id", text("created_at DESC")),
        Index("ix_title_edits_batch", "batch_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)  # uuid4
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # edit | lock | revert | bulk | undo | item_lock | image
    title_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # NULL after the member is deleted
    field: Mapped[str] = mapped_column(String(40), nullable=False)  # catalogue key, images.<key>, or "locked" for the item flag
    before: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)  # as stored; a cleared field is null
    before_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    after: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    after_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    undone_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # batch_id of the undo that reversed it


class TitleUpload(Base):
    """Re-encoded uploaded artwork, kept in the database so backups and restores carry it."""

    __tablename__ = "title_uploads"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)  # of the re-encoded bytes
    content_type: Mapped[str] = mapped_column(String(16), nullable=False)  # image/jpeg | image/png | image/webp
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    # Uploads in SQLite (<= 4 MiB each); move to a content-addressed directory plus a backup manifest past 2 GB.
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LibraryWatchDir(Base):
    """The known-good mtime of one directory of a watched external root. Deleted with the root."""

    __tablename__ = "library_watch_dirs"

    root_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    path: Mapped[str] = mapped_column(Text, primary_key=True)  # root-relative POSIX; "" = the root itself
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)


class ClientMetricDay(Base):
    """One UTC day of one client metric and label as a fixed log-scale histogram. Anonymous."""

    __tablename__ = "client_metric_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(40), primary_key=True)  # "" when the metric has no label
    histogram: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 25 bucket counts
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RemoteMedia(Base):
    """Public metadata of one remote video a recommendation source fetched.

    A household cache with no member column: it never says whom a video was fetched for. ``reco_pool`` holds that, per
    member, and ranking reads a member's candidates only through their own pool rows.
    """

    __tablename__ = "remote_media"
    __table_args__ = (
        Index("ix_remote_media_channel", "channel_key"),
        Index("ix_remote_media_nominated", "last_nominated_at"),
    )

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # RemotePlaybackProgressService.source_identity_key
    source_identity: Mapped[str] = mapped_column(Text, nullable=False)
    extractor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    webpage_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    category_keys: Mapped[list] = mapped_column(JSON, default=list)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    availability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens: Mapped[list] = mapped_column(JSON, default=list)  # ≤ 40 strings
    # The on-device embedder's vector of "{title} — {uploader}"; NULL until the backfill reaches it.
    vector_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vector_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_nominated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecoPool(Base):
    """One remote video in one member's Candidate pool, and which of that member's sources nominated it."""

    __tablename__ = "reco_pool"
    __table_args__ = (
        Index("ix_reco_pool_user_nominated", "user_id", "last_nominated_at"),
        Index("ix_reco_pool_item", "item_key"),
    )

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_key: Mapped[str] = mapped_column(String(64), primary_key=True)  # remote_media.key
    sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # SOURCE_* bits (app.services.reco)
    seed_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)  # the member's own row id or interest key; never text
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_nominated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecoEvent(Base):
    """One entry of a member's Recommendation history: an impression, open, play, completion or feedback."""

    __tablename__ = "reco_events"
    __table_args__ = (
        Index("ix_reco_events_user_at", "user_id", "at"),
        Index("ix_reco_events_user_item", "user_id", "item_key", "at"),
        # Covers Diagnostics' three window scans (services/reco/metrics.py) so none reads the table, and orders the grouped one so it
        # streams without a sort. Each impression batch item pays for one extra ~110-byte entry; `at` trails kind, so a window is a filter.
        Index("ix_reco_events_kind_surface", "kind", "surface", "slot", "target_kind", "at", "list_id", "user_id", "item_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # app.services.reco.EventKind
    surface: Mapped[str | None] = mapped_column(String(24), nullable=True)
    list_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slot: Mapped[str | None] = mapped_column(String(8), nullable=True)
    p_shown: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(24), nullable=True)
    target_kind: Mapped[str] = mapped_column(String(8), nullable=False)  # remote | title
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fraction: Mapped[float | None] = mapped_column(Float, nullable=True)


class PlaybackHistory(Base):
    """One finished playback session (admin Activity, schema 11). Title and client are snapshots; pruned after 90 days on write."""

    __tablename__ = "playback_history"
    __table_args__ = (Index("ix_playback_history_ended", text("ended_at DESC")),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # library | remote
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client: Mapped[dict] = mapped_column(JSON, nullable=False)  # {"kind","name","device"}
    method: Mapped[str] = mapped_column(String(12), nullable=False)  # direct | remux | transcode | relay
    hardware: Mapped[str | None] = mapped_column(String(10), nullable=True)
    video: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    watched_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    stopped_by_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MediaRequest(Base):
    """A Media request (ADR 0018): a member asks the household's Sonarr/Radarr for a movie, show or anime."""

    __tablename__ = "media_requests"
    __table_args__ = (
        Index("ix_media_requests_kind_tmdb", "kind", "tmdb_id"),
        Index("ix_media_requests_requester_created", "requested_by", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # movie | show | anime
    media_type: Mapped[str] = mapped_column(String(8), nullable=False)  # movie | tv: what the arr sees
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anilist_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    seasons: Mapped[Any] = mapped_column(JSON, nullable=True)  # list[int] | "all"; NULL for movies
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)  # dub | sub (anime series only)
    # pending | approved | processing | partially_available | available | declined | failed
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(36), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    arr_server_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    arr_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_title_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MediaRequestFollower(Base):
    """A second member who asked for a title already requested follows that request instead of duplicating it."""

    __tablename__ = "media_request_followers"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ArrServer(Base):
    """The household's Sonarr or Radarr. api_key is write-only, never serialized, and nulled in backups."""

    __tablename__ = "arr_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # sonarr | radarr
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_folder: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anime_root_folder: Mapped[str | None] = mapped_column(Text, nullable=True)  # sonarr
    anime_quality_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dub_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # sonarr: Anime language
    sub_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path_mappings: Mapped[list] = mapped_column(JSON, default=list)  # [{"remote": "/tv", "local": "/media/tv"}]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RequestPolicy(Base):
    """A Request policy row: user_id NULL is the household default for the kind, else that member's override."""

    __tablename__ = "request_policies"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_request_policies_user_kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    can_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_approve: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quota_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL = unlimited
    quota_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MemberAccess(Base):
    """What one member may see and when. No row = all libraries, no limits; admins never have one."""

    __tablename__ = "member_access"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sections: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # member_access.SECTIONS / "root:<id>"; NULL = all
    movie_rating_max: Mapped[str | None] = mapped_column(String(8), nullable=True)  # G|PG|PG-13|R|NC-17; NULL = no ceiling
    tv_rating_max: Mapped[str | None] = mapped_column(String(8), nullable=True)  # TV-Y … TV-MA; NULL = no ceiling
    unrated: Mapped[str] = mapped_column(String(8), default="allow", server_default=text("'allow'"), nullable=False)  # allow|hide
    streaming: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False)  # missing keys = defaults
    schedule: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # {"mon": [["07:00", "20:00"]], …}; NULL = any time
    daily_limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bonus_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    bonus_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    can_download: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ScreenTime(Base):
    """Seconds a member watched on one household-local day, counted from playback heartbeats."""

    __tablename__ = "screen_time"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
