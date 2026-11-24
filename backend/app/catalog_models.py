from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.model_time import utcnow


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
    # When the media arrived: a leaf's earliest file mtime, a folder's newest child. NULL: no file time.
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


class OmdbRatingCache(Base):
    """One durable OMDb response state per IMDb identity, shared by every matching title."""

    __tablename__ = "omdb_rating_cache"
    __table_args__ = (Index("ix_omdb_rating_cache_due_lease", "next_attempt_at", "lease_until"),)

    imdb_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    scores: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OmdbRequestReservation(Base):
    """Append-only rolling quota reservations; a crashed request remains spent."""

    __tablename__ = "omdb_request_reservations"
    __table_args__ = (Index("ix_omdb_request_reservations_requested_at", "requested_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    imdb_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MusicLookup(Base):
    """Shared public-provider results. Library visibility is applied when serving them."""

    __tablename__ = "music_lookups"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class MusicTrackOverride(Base):
    """Explicit track edits survive a source rescan without changing its file."""

    __tablename__ = "music_track_overrides"

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MusicEditBatch(Base):
    __tablename__ = "music_edit_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    changes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MusicCastTicket(Base):
    """A revocable, expiring capability for the member's specific Sonos queue."""

    __tablename__ = "music_cast_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    speaker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    output_format: Mapped[str] = mapped_column(String(16), default="sonos")


class MusicReceiverTicket(Base):
    """A paired native app grants one Chromecast receiver a bounded music queue."""

    __tablename__ = "music_receiver_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    issuer_device_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MusicFileWriteback(Base):
    """A file backup handle; restore also verifies the artifact's current digest."""

    __tablename__ = "music_file_writebacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MusicPlaylist(Base):
    __tablename__ = "music_playlists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


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
