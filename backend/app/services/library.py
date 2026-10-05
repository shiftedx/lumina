from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased, defer

from app.events import EventBus
from app.models import AppMaintenanceState, LibraryItem, LibraryItemArtifact, LibraryTag, MediaArtifact, MediaTitle, PlaybackProgress, SourceAutomation, StorageRoot, User, utcnow
from app.persistence import queue_after_commit, write_transaction
from app.schemas import LibraryChannelResponse, LibraryGroupResponse, LibraryItemResponse, LibraryTagResponse
from app.services import member_access
from app.services.library_curation import LibraryCurationService
from app.services.library_search import SEARCH_MAX_RESULTS, LibrarySearchService
from app.services.artifact_quarantine import ArtifactQuarantineService
from app.services.chapters import normalize_chapters
from app.services.media_artifacts import MediaArtifactService
from app.services.reco.events import library_channel_key
from app.services.storage_roots import ONLINE_STATES
from app.services.webhooks import WebhookService


LIBRARY_PAGE_DEFAULT_LIMIT = 60
LIBRARY_PAGE_MAX_LIMIT = 100
LIBRARY_GROUPS_MAX = 500
_YOUTUBE_CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
EXTERNAL_LIBRARY_ORIGIN = "external_library"
LibraryViewKind = Literal["video", "audio", "movie", "episode", "track", "recording", "music"]
# Library view -> stored kinds; "music" spans saved audio and imported tracks.
LIBRARY_VIEW_KINDS = {
    "video": ("video",),
    "audio": ("audio",),
    "movie": ("movie",),
    "episode": ("episode",),
    "track": ("track",),
    "recording": ("recording",),
    "music": ("audio", "track"),
}

# Search is bounded: at most this many indexed matches are materialised per
# query so no request loads the whole library. The paged search route walks
# this bounded, relevance-ordered set with its opaque offset cursor. Single
# source of truth lives with the search service.
LIBRARY_SEARCH_MAX_RESULTS = SEARCH_MAX_RESULTS

LIBRARY_MAINTENANCE_BATCH_SIZE = 500
RECONCILE_FILES_CURSOR_KEY = "library_reconcile_files_cursor"

# One sweep step at a time, process-wide, held across read-cursor + stat +
# write. Without it a step stalled in its stat phase can store a stale cursor
# over a concurrent member refresh and make the next sweep skip rows.
_maintenance_sweep_lock = Lock()


def encode_library_cursor(payload: dict[str, Any]) -> str:
    """Opaque, URL-safe page cursor; clients must round-trip it unchanged."""
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


def decode_library_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid library page cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid library page cursor")
    return payload


# Keys list payloads keep in the pre-computed ``library_items.metadata_summary``.
SUMMARY_METADATA_KEYS = {
    "id",
    "title",
    "uploader",
    "channel",
    "artist",
    "track",
    "album",
    "webpage_url",
    "original_url",
    "extractor",
    "extractor_key",
    "categories",
    "genre",
    "upload_date",
    "timestamp",
    "release_timestamp",
    "view_count",
    "like_count",
    "comment_count",
    "channel_follower_count",
    "channel_is_verified",
    "availability",
    "duration",
    "thumbnail",
    "width",
    "height",
    "fps",
    "ext",
    "format_note",
    "vcodec",
    "acodec",
    "dynamic_range",
    "tbr",
    "abr",
    "audio_channels",
    "asr",
    "filepath",
    "filename",
    "lumina_media_kind",
    "lumina_variant_key",
    # Local import grouping.
    "series",
    "season_number",
    "episode_number",
    "episode_number_end",
    "release_year",
    "track_number",
    "lumina_import_kind",
}


class LibraryService:
    def __init__(self, db: Session, events: EventBus | None = None):
        self.db = db
        self.events = events
        self._owner_cache: dict[str, User | None] = {}
        self._media_states: dict[str, str | None] = {}

    def list_items_page(
        self,
        user: User,
        *,
        cursor: str | None = None,
        limit: int = LIBRARY_PAGE_DEFAULT_LIMIT,
        kind: str | None = None,
        source: str | None = None,
        group: str | None = None,
        status: str | None = None,
        sort: str = "recent",
    ) -> tuple[list[LibraryItem], str | None]:
        """One bounded, index-ordered page; summaries only, metadata_json stays unloaded."""
        bounded_limit = max(1, min(LIBRARY_PAGE_MAX_LIMIT, limit))
        if sort == "title":
            query = (
                self._visible_items_query(user)
                .options(defer(LibraryItem.metadata_json))
                .order_by(LibraryItem.title.collate("NOCASE"), LibraryItem.id)
            )
            if cursor is not None:
                query = query.filter(self._after_title_cursor(cursor))
        else:
            query = self.page_query(user, cursor=cursor)
        query = query.filter(*self.view_filters(kind=kind, source=source, group=group, status=status))
        rows = query.limit(bounded_limit + 1).all()
        items = rows[:bounded_limit]
        next_cursor = None
        if len(rows) > bounded_limit:
            last = items[-1]
            next_cursor = encode_library_cursor({"t": last.title, "i": last.id}) if sort == "title" else self._page_cursor_for(last)
        return items, next_cursor

    @staticmethod
    def view_filters(*, kind: str | None = None, source: str | None = None, group: str | None = None, status: str | None = None) -> list:
        """SQL predicates for a library view: kind, origin/provider, grouping name and status."""
        filters = []
        if kind:
            filters.append(LibraryItem.kind.in_(LIBRARY_VIEW_KINDS[kind]))
        if source == "imported":
            filters.append(LibraryItem.extractor == EXTERNAL_LIBRARY_ORIGIN)
        elif source == "saved":
            filters.append(or_(LibraryItem.extractor.is_(None), LibraryItem.extractor != EXTERNAL_LIBRARY_ORIGIN))
        elif source:
            filters.append(func.lower(LibraryItem.extractor) == source.casefold())
        if group is not None:
            # "" is the ungrouped bucket (no series/artist name).
            filters.append(LibraryItem.uploader.is_(None) if group == "" else LibraryItem.uploader == group)
        if status:
            filters.append(LibraryItem.status == status)
        return filters

    @staticmethod
    def _after_title_cursor(cursor: str):
        payload = decode_library_cursor(cursor)
        title, item_id = payload.get("t"), payload.get("i")
        if not isinstance(title, str) or not isinstance(item_id, str):
            raise ValueError("Invalid library page cursor")
        ordered = LibraryItem.title.collate("NOCASE")
        return or_(ordered > title, and_(ordered == title, LibraryItem.id > item_id))

    def list_groups(self, user: User, *, kind: str, source: str | None = None) -> list[LibraryGroupResponse]:
        """Series or artists as one GROUP BY: permission-correct counts, no per-group queries."""
        rows = (
            self.db.query(
                LibraryItem.uploader,
                func.count(LibraryItem.id),
                func.count(func.distinct(LibraryItem.playlist_name)),
                func.min(LibraryItem.id),
            )
            .filter(self.visible_predicate(user), LibraryItem.status != "missing")
            .filter(*self.view_filters(kind=kind, source=source))
            .group_by(LibraryItem.uploader)
            .order_by(LibraryItem.uploader.collate("NOCASE"))
            .limit(LIBRARY_GROUPS_MAX)  # First 500 series/artists; page groups if a household exceeds it
            .all()
        )
        return [
            LibraryGroupResponse(name=name, items=count, subgroups=subgroups, artwork_url=f"/api/library/{artwork_id}/artwork")
            for name, count, subgroups, artwork_id in rows
        ]

    def list_channels(self, user: User, *, source: str, sort: str, artwork_url: Callable[[str | None], str | None]) -> list[LibraryChannelResponse]:
        """Saved videos grouped by channel exactly as Jellyfin's Channels series.

        One grouped query (the member's completed progress LEFT JOINed for the unwatched count through the
        (user_id, item_id) unique index), one IN query for the newest items' channel ids, one for the member's follows.
        A renamed channel splits into two groups; add a stored channel_id column if that is ever reported.
        """
        from app.services.channel_discovery import normalize_channel_source_url  # noqa: PLC0415 - keeps library free of discovery imports
        from app.services.jellyfin import CHANNEL_ITEM, UPLOADER, channel_id  # noqa: PLC0415 - jellyfin imports this module

        rows = self.db.execute(
            select(LibraryItem.extractor, UPLOADER, func.count(LibraryItem.id), func.count(PlaybackProgress.id),
                   LibraryItem.id, func.max(LibraryItem.created_at))
            .outerjoin(PlaybackProgress, and_(PlaybackProgress.item_id == LibraryItem.id, PlaybackProgress.user_id == user.id,
                                              PlaybackProgress.completed.is_(True)))
            .where(*CHANNEL_ITEM, func.lower(LibraryItem.extractor) == source, self.visible_predicate(user))
            .group_by(LibraryItem.extractor, UPLOADER)
        ).all()
        # SQLite fills the bare LibraryItem.id from the row holding max(created_at): each group's newest video.
        if sort == "name":
            rows.sort(key=lambda row: ((row[1] or row[0]).casefold(), row[0]))
        else:
            rows.sort(key=lambda row: row[5], reverse=True)
        rows = rows[:LIBRARY_GROUPS_MAX]
        newest_ids = [row[4] for row in rows]
        channel_ids = dict(self.db.execute(
            select(LibraryItem.id, func.json_extract(LibraryItem.metadata_json, "$.channel_id")).where(LibraryItem.id.in_(newest_ids))
        ).all()) if newest_ids else {}
        follows = self.db.execute(
            select(SourceAutomation.source_url, SourceAutomation.artwork_url)
            .where(SourceAutomation.user_id == user.id, SourceAutomation.source_type == "channel", SourceAutomation.artwork_url.is_not(None))
        ).all()
        avatars = {normalize_channel_source_url(url): art for url, art in follows}
        channels = []
        for extractor, uploader, count, completed, newest_id, newest_at in rows:
            raw = channel_ids.get(newest_id)
            known = raw if isinstance(raw, str) and _YOUTUBE_CHANNEL_ID.fullmatch(raw) else None
            avatar = avatars.get(normalize_channel_source_url(f"https://www.youtube.com/channel/{known}")) if known else None
            channels.append(LibraryChannelResponse(
                key=channel_id(extractor, uploader), extractor=extractor, name=uploader or extractor, uploader=uploader,
                count=count, unwatched_count=count - completed, newest_item_id=newest_id, newest_at=newest_at,
                channel_id=known, avatar_url=artwork_url(avatar),
            ))
        return channels

    def page_query(self, user: User, *, cursor: str | None = None):
        query = (
            self._visible_items_query(user)
            .options(defer(LibraryItem.metadata_json))
            .order_by(
                LibraryItem.downloaded_at.desc().nullslast(),
                LibraryItem.created_at.desc(),
                LibraryItem.id.desc(),
            )
        )
        if cursor is not None:
            query = query.filter(self._after_page_cursor(cursor))
        return query

    @staticmethod
    def _page_cursor_for(item: LibraryItem) -> str:
        return encode_library_cursor(
            {
                "d": item.downloaded_at.isoformat() if item.downloaded_at else None,
                "c": item.created_at.isoformat(),
                "i": item.id,
            }
        )

    @staticmethod
    def _after_page_cursor(cursor: str):
        payload = decode_library_cursor(cursor)
        try:
            downloaded_at = datetime.fromisoformat(payload["d"]) if payload.get("d") is not None else None
            created_at = datetime.fromisoformat(payload["c"])
            item_id = payload["i"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid library page cursor") from exc
        if not isinstance(item_id, str):
            raise ValueError("Invalid library page cursor")
        # Strictly after the cursor row under (downloaded_at DESC NULLS LAST,
        # created_at DESC, id DESC).
        tail = or_(
            LibraryItem.created_at < created_at,
            and_(LibraryItem.created_at == created_at, LibraryItem.id < item_id),
        )
        if downloaded_at is None:
            return and_(LibraryItem.downloaded_at.is_(None), tail)
        return or_(
            LibraryItem.downloaded_at < downloaded_at,
            LibraryItem.downloaded_at.is_(None),
            and_(LibraryItem.downloaded_at == downloaded_at, tail),
        )

    def prime_media_states(self, items: Iterable[LibraryItem]) -> None:
        """Where each item's bytes stand, for one page in one query and no filesystem access.

        Uses the root's last stored observation: a list never stats a mount.
        """
        ids = [item.id for item in items if item.id not in self._media_states]
        if not ids:
            return
        rows = (
            self.db.query(LibraryItemArtifact.library_item_id, MediaArtifact.lifecycle, StorageRoot.enabled, StorageRoot.observation)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
            .filter(LibraryItemArtifact.library_item_id.in_(ids))
            .all()
        )
        for item_id in ids:
            self._media_states[item_id] = None
        for item_id, lifecycle, enabled, observation in rows:
            online = enabled and (observation or {}).get("state", "available") in ONLINE_STATES
            self._media_states[item_id] = "offline" if not online and lifecycle == "available" else lifecycle

    def prime_owners(self, items: Iterable[LibraryItem]) -> None:
        """Resolve every owner on a page with one IN query instead of one lookup per item."""
        missing = {item.user_id for item in items if item.user_id and item.user_id not in self._owner_cache}
        if not missing:
            return
        for owner in self.db.query(User).filter(User.id.in_(missing)).all():
            self._owner_cache[owner.id] = owner
        for owner_id in missing:
            self._owner_cache.setdefault(owner_id, None)

    def _resolve_owner(self, owner_id: str | None) -> User | None:
        if not owner_id:
            return None
        if owner_id not in self._owner_cache:
            self._owner_cache[owner_id] = self.db.get(User, owner_id)
        return self._owner_cache[owner_id]

    def get_item(self, item_id: str, user: User | None = None) -> LibraryItem | None:
        item = self.db.get(LibraryItem, item_id)
        if item is None:
            return None
        if user is None or self.can_view(item, user):
            return item
        return None

    @staticmethod
    def can_view(item: LibraryItem, user: User) -> bool:
        return LibraryCurationService.can_view(item, user)

    @staticmethod
    def can_manage(item: LibraryItem, user: User) -> bool:
        return LibraryCurationService.can_manage_item(item, user)

    def list_tags(self, item_id: str, user: User) -> list[LibraryTag]:
        return LibraryCurationService(self.db).list_tags(item_id, user)

    def add_tag(self, item_id: str, value: str, user: User) -> LibraryTag:
        return LibraryCurationService(self.db).add_tag(item_id, value, user)

    def delete_tag(self, item_id: str, value: str, user: User) -> None:
        normalized = value.strip().lower()
        tag = (
            self.db.query(LibraryTag)
            .filter(LibraryTag.item_id == item_id, LibraryTag.user_id == user.id, LibraryTag.tag == normalized)
            .first()
        )
        if tag is None:
            raise ValueError("Tag not found")
        LibraryCurationService(self.db).delete_tag(tag.id, user)

    def search_page(self, query: str, user: User, *, offset: int, limit: int) -> tuple[list[LibraryItem], bool]:
        """One relevance-ordered search page: rank ids via FTS, then load ONLY the page's rows.

        FTS5 does the ranking and the household-visibility join and returns a
        bounded, ordered id set; only the ``limit`` rows of this page are
        materialised (``metadata_json`` deferred, like the list path), so a deep
        page never loads the whole result set. Returns ``(items, has_more)``.
        """
        normalized = query.strip() if query else ""
        if not normalized:
            return [], False
        bounded_limit = max(1, limit)
        offset = max(0, offset)
        fetch = min(offset + bounded_limit + 1, LIBRARY_SEARCH_MAX_RESULTS)
        hits = LibrarySearchService(self.db).search(user, normalized, limit=fetch)
        page_hits = hits[offset : offset + bounded_limit]
        has_more = len(hits) > offset + bounded_limit
        if not page_hits:
            return [], has_more
        rank_by_id = {hit.item_id: index for index, hit in enumerate(page_hits)}
        rows = (
            self.db.query(LibraryItem)
            .options(defer(LibraryItem.metadata_json))
            .filter(LibraryItem.id.in_(rank_by_id.keys()))
            .all()
        )
        rows.sort(key=lambda item: rank_by_id[item.id])
        return rows, has_more

    def resolve_media_path(self, item: LibraryItem) -> Path:
        """The item's registered artifact file; never a raw stored path."""
        return MediaArtifactService(self.db).locate(item)[0]

    def upsert_from_info(self, info: dict, owner_user_id: str | None = None, visibility: str = "private") -> LibraryItem:
        extractor = info.get("extractor_key") or info.get("extractor")
        remote_id = info.get("id")
        file_path = resolve_download_output_path(info)
        normalized_thumbnail = normalize_thumbnail_url(info, info.get("thumbnail"))
        normalized_metadata = normalize_thumbnail_metadata(info, normalized_thumbnail, file_path)
        variant_key = build_media_variant_key(normalized_metadata, file_path)
        media_kind = infer_media_kind(normalized_metadata, file_path)
        existing = None
        if extractor and remote_id:
            matches = (
                self.db.query(LibraryItem)
                .filter(LibraryItem.extractor == extractor, LibraryItem.remote_id == remote_id)
                .order_by(LibraryItem.created_at.asc())
                .all()
            )
            if owner_user_id:
                matches = [item for item in matches if item.user_id == owner_user_id]
            else:
                owned_shared = [item for item in matches if item.visibility == "shared"]
                if owned_shared:
                    matches = owned_shared

            existing = next((item for item in matches if item_variant_key(item) == variant_key), None)
            if existing is None and len(matches) == 1 and infer_item_media_kind(matches[0]) == media_kind and not item_variant_key(matches[0]):
                existing = matches[0]
        file_size = info.get("filesize") or info.get("filesize_approx")
        normalized_duration = normalize_duration_value(info.get("duration"))
        created = existing is None
        if existing is None:
            existing = LibraryItem(
                id=str(uuid.uuid4()),
                user_id=owner_user_id,
                visibility=visibility if visibility in {"private", "shared"} else "private",
                extractor=extractor,
                remote_id=remote_id,
                title=info.get("title") or remote_id or "Untitled download",
                uploader=info.get("uploader") or info.get("channel"),
                playlist_name=info.get("playlist_title") or info.get("playlist"),
                duration=normalized_duration,
                thumbnail_url=normalized_thumbnail,
                webpage_url=info.get("webpage_url") or info.get("original_url"),
                file_path=file_path,
                file_size=file_size,
                downloaded_at=utcnow(),
                availability=info.get("availability"),
                status="available",
            )
            self._set_metadata(existing, normalized_metadata)
            self.db.add(existing)
        else:
            if existing.user_id is None and owner_user_id and existing.visibility != "shared":
                existing.user_id = owner_user_id
            if existing.visibility not in {"private", "shared"}:
                existing.visibility = "private"
            existing.title = info.get("title") or existing.title
            existing.uploader = info.get("uploader") or info.get("channel") or existing.uploader
            existing.playlist_name = info.get("playlist_title") or info.get("playlist") or existing.playlist_name
            existing.duration = normalized_duration or existing.duration
            existing.thumbnail_url = normalized_thumbnail or existing.thumbnail_url
            existing.webpage_url = info.get("webpage_url") or info.get("original_url") or existing.webpage_url
            existing.file_path = file_path or existing.file_path
            existing.file_size = file_size or existing.file_size
            existing.downloaded_at = utcnow()
            existing.availability = info.get("availability") or existing.availability
            self._set_metadata(existing, normalized_metadata)
            existing.status = "available"
        if existing.channel_key is None:  # The stable channel id, as step 9 backfills it
            existing.channel_key = library_channel_key(existing.extractor, info)
        self.db.flush()
        if file_path:
            MediaArtifactService(self.db).register_file(existing, file_path)
        # Keep the search corpus in step with the item's text in the same
        # transaction, so a committed upsert is immediately searchable. A new
        # item takes the insert-only path to avoid a delete scan of the corpus.
        search = LibrarySearchService(self.db)
        if created:
            search.index_new_item(existing)
        else:
            search.sync_item(existing)
        # Events and webhook delivery escape only after the durable commit:
        # the upsert runs inside the caller's write transaction, and external
        # work must never happen while it is open.
        if self.events:
            payload = self._event_payload(existing)
            queue_after_commit(
                self.db,
                lambda data=payload: self.events.publish("library_item_upserted", data),
            )
        if created:
            self._notify_new_video_after_commit(existing)
        return existing

    def _notify_new_video_after_commit(self, item: LibraryItem) -> None:
        """Snapshot the notification fields pre-commit; deliver on a fresh session post-commit.

        The webhook HTTP POST must never run inside the library write
        transaction, and the delivery session must not touch the writer's
        identity map mid-transaction.
        """
        bind = self.db.get_bind()
        notification = LibraryItem(
            id=item.id,
            title=item.title,
            uploader=item.uploader,
            playlist_name=item.playlist_name,
            duration=item.duration,
            extractor=item.extractor,
            visibility=item.visibility,
            webpage_url=item.webpage_url,
        )

        def notify() -> None:
            with Session(bind=bind) as notification_db:
                WebhookService(notification_db).notify_new_video(notification)

        queue_after_commit(self.db, notify)

    def reconcile_files_step(self, *, batch_size: int = LIBRARY_MAINTENANCE_BATCH_SIZE) -> tuple[list[LibraryItem], bool]:
        """Reconcile one resumable batch; each batch is its own short write transaction."""
        with _maintenance_sweep_lock:
            return self._reconcile_files_batch(batch_size)

    def _reconcile_files_batch(self, batch_size: int) -> tuple[list[LibraryItem], bool]:
        cursor = self._sweep_cursor(RECONCILE_FILES_CURSOR_KEY)
        items, next_cursor, completed = self._sweep_batch(cursor, batch_size)
        if not items and not cursor:
            return [], True
        # Filesystem observation stays outside the writer slot.
        # Existence is observed through the artifact registry; an offline root
        # yields "unknown" and never marks its items missing.
        artifact_observations = MediaArtifactService(self.db).observe(items)
        observations: list[tuple[LibraryItem, str | None, bool | None, int | None]] = []
        for item in items:
            exists, size = artifact_observations.get(item.id, (False, None))
            observations.append((item, self._raw_file_path(item), exists, size))
        updated: list[LibraryItem] = []
        with write_transaction(self.db, name="library_reconcile_batch"):
            for item, raw_path, exists, size in observations:
                if not raw_path:
                    continue
                if item.file_path != raw_path:
                    item.file_path = raw_path
                    updated.append(item)
                metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
                normalized_metadata = normalize_thumbnail_metadata(metadata, item.thumbnail_url, raw_path)
                if item.metadata_json != normalized_metadata:
                    self._set_metadata(item, normalized_metadata)
                    updated.append(item)
                if exists is None:
                    continue
                next_status = "available" if exists else "missing"
                next_size = size if exists else item.file_size
                if next_status != item.status or next_size != item.file_size:
                    item.status = next_status
                    item.file_size = next_size
                    updated.append(item)
                    if self.events:
                        event_name = "library_item_missing" if next_status == "missing" else "library_item_upserted"
                        payload = self._event_payload(item)
                        queue_after_commit(
                            self.db,
                            lambda name=event_name, data=payload: self.events.publish(name, data),
                        )
            self._store_sweep_cursor(RECONCILE_FILES_CURSOR_KEY, next_cursor)
        return updated, completed

    def _sweep_cursor(self, key: str) -> str:
        state = self.db.get(AppMaintenanceState, key)
        return state.value if state is not None else ""

    def _store_sweep_cursor(self, key: str, value: str) -> None:
        state = self.db.get(AppMaintenanceState, key)
        if state is None:
            self.db.add(AppMaintenanceState(key=key, value=value))
        else:
            state.value = value

    def _sweep_batch(self, cursor: str, batch_size: int) -> tuple[list[LibraryItem], str, bool]:
        bounded = max(1, batch_size)
        items = (
            self.db.query(LibraryItem)
            .filter(LibraryItem.id > cursor)
            .order_by(LibraryItem.id.asc())
            .limit(bounded)
            .all()
        )
        completed = len(items) < bounded
        next_cursor = "" if completed else items[-1].id
        return items, next_cursor, completed

    def set_visibility(self, item: LibraryItem, visibility: str, user: User) -> LibraryItem:
        item = LibraryCurationService(self.db).set_visibility(item.id, visibility, user)
        # The route's durable commit is the get_db teardown; the event drains
        # there (or at a seam exit) and is discarded on rollback.
        if self.events:
            payload = self._event_payload(item)
            queue_after_commit(
                self.db,
                lambda data=payload: self.events.publish("library_item_upserted", data),
            )
        return item

    def delete_file(self, item: LibraryItem) -> LibraryItem:
        """Quarantine (never unlink) the managed file; bytes move only after the commit."""
        ArtifactQuarantineService(self.db).delete_file(item)
        # The route's durable commit is the get_db teardown; the event drains
        # there (or at a seam exit) and is discarded on rollback.
        if self.events:
            payload = self._event_payload(item)
            queue_after_commit(
                self.db,
                lambda data=payload: self.events.publish("library_item_missing", data),
            )
        return item

    def restore_file(self, item: LibraryItem) -> LibraryItem:
        ArtifactQuarantineService(self.db).restore_file(item)
        if self.events:
            payload = self._event_payload(item)
            queue_after_commit(
                self.db,
                lambda data=payload: self.events.publish("library_item_upserted", data),
            )
        return item

    def serialize(self, item: LibraryItem, viewer: User | None = None, *, summary: bool = False) -> LibraryItemResponse:
        owner = self._resolve_owner(item.user_id)
        if summary:
            # Summary payloads never touch metadata_json: list queries defer that
            # column and the stored summary is maintained on every metadata write.
            metadata_json = (
                item.metadata_summary
                if item.metadata_summary is not None
                else self.summarize_metadata(item.metadata_json)
            )
        else:
            metadata_json = item.metadata_json
        timeline = {"chapters": [], "timestamps": []} if summary else normalize_chapters(
            provider_chapters=(item.metadata_json or {}).get("chapters") if isinstance(item.metadata_json, dict) else None,
            description=(item.metadata_json or {}).get("description") if isinstance(item.metadata_json, dict) else None,
            duration=item.duration,
        )
        return LibraryItemResponse(
            id=item.id,
            user_id=item.user_id,
            visibility=item.visibility if item.visibility in {"private", "shared"} else "private",
            owner_username=owner.username if owner else None,
            owner_display_name=owner.display_name if owner else None,
            extractor=item.extractor,
            remote_id=item.remote_id,
            title=item.title,
            uploader=item.uploader,
            playlist_name=item.playlist_name,
            duration=normalize_duration_value(item.duration),
            thumbnail_url=item.thumbnail_url,
            artwork_url=f"/api/library/{item.id}/artwork",
            chapters=timeline["chapters"],
            description_timestamps=timeline["timestamps"],
            webpage_url=item.webpage_url,
            file_size=item.file_size,
            downloaded_at=item.downloaded_at,
            availability=item.availability,
            metadata_json=public_metadata(metadata_json),
            status=item.status,
            kind=item.kind or "video",
            media_state=self._media_states.get(item.id),
            title_id=item.title_id,
            extra_type=item.extra_type,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @classmethod
    def _set_metadata(cls, item: LibraryItem, metadata: dict[str, Any]) -> None:
        """Every metadata write keeps the stored summary in step for bounded list payloads.

        This does NOT re-sync the FTS index. The maintenance sweeps that call it
        (thumbnail/variant normalization, file reconciliation) only rewrite
        non-indexed metadata keys (thumbnail/filepath/ext/media-kind/variant), so
        the indexed channel/description stay correct. A future writer here that
        changes title/uploader/channel/description MUST also call
        ``LibrarySearchService.sync_item`` or the index will silently drift.
        """
        item.metadata_json = metadata
        item.metadata_summary = cls.summarize_metadata(metadata)
        item.kind = library_kind(metadata)

    @staticmethod
    def summarize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        """Bounded summary of a metadata dict kept in ``metadata_summary``."""
        if not isinstance(metadata, dict):
            return {}
        summary = {key: metadata[key] for key in SUMMARY_METADATA_KEYS if key in metadata}
        subtitles = metadata.get("subtitles")
        if isinstance(subtitles, dict):
            summary["subtitles_count"] = len(subtitles)
        formats = metadata.get("formats")
        if isinstance(formats, list):
            summary["formats_count"] = len(formats)
        chapters = metadata.get("chapters")
        if isinstance(chapters, list):
            summary["chapters_count"] = len(chapters)
        return summary

    @staticmethod
    def serialize_tag(tag: LibraryTag) -> LibraryTagResponse:
        return LibraryTagResponse(
            id=tag.id,
            item_id=tag.item_id,
            user_id=tag.user_id,
            tag=tag.tag,
            created_at=tag.created_at,
            updated_at=tag.updated_at,
        )

    @staticmethod
    def _metadata_file_path(metadata: dict | None) -> str | None:
        return resolve_download_output_path(metadata)

    @staticmethod
    def _raw_file_path(item: LibraryItem) -> str | None:
        return item.file_path or LibraryService._metadata_file_path(item.metadata_json)

    @staticmethod
    def visible_predicate(user: User, *, driving: bool = False):
        """Household visibility: shared, owned, or ownerless for admins. Reuse on every paged surface.

        By default the columns are wrapped so SQLite cannot drive the query from
        them: rows reached by id, join or another selective column must not start
        from a MULTI-INDEX OR over every visible item (1.5s for one collections
        list at 20k items). Only the library grid page passes ``driving=True``.
        """
        visibility, owner = LibraryItem.visibility, LibraryItem.user_id
        if not driving:
            visibility, owner = func.coalesce(visibility, ""), func.coalesce(owner, "")
        shared = visibility == "shared"
        # Member access (ADR 0019): a restricted member's shared items pass their Section and rating limits; own items always do.
        if (limits := member_access.item_clause(user)) is not None:
            shared = and_(shared, limits)
        visible = or_(shared, owner == user.id)
        if user.role == "admin":
            visible = or_(visible, LibraryItem.user_id.is_(None))
        return visible

    @staticmethod
    def visible_title_predicate(user: User):
        """A Media title is visible exactly when a visible, non-missing linked item exists at or below it (ADR 0009).

        Correlates with the enclosing query's ``MediaTitle``. It uses four index-driven EXISTS
        (own items incl. extras, children's, grandchildren's, boxset members') instead of one
        OR'd join that SQLite cannot index. Member access (ADR 0019) arrives through the item predicate: a title is
        visible exactly when an item at or below it passes the member's Section and rating limits.
        """
        leaf, season = aliased(MediaTitle), aliased(MediaTitle)
        items = select(LibraryItem.id).where(LibraryService.visible_predicate(user), LibraryItem.status != "missing")
        via_leaf = items.join(leaf, leaf.id == LibraryItem.title_id)
        visible = or_(
            items.where(LibraryItem.title_id == MediaTitle.id).exists(),
            via_leaf.where(leaf.parent_id == MediaTitle.id).exists(),
            via_leaf.join(season, season.id == leaf.parent_id).where(season.parent_id == MediaTitle.id).exists(),
            via_leaf.where(leaf.boxset_id == MediaTitle.id).exists(),
        )
        own = member_access.title_clause(user)  # prunes a kid's hidden walls before the item EXISTS run
        return visible if own is None else and_(own, visible)

    def _visible_items_query(self, user: User):
        return self.db.query(LibraryItem).filter(self.visible_predicate(user, driving=True))

    def _event_payload(self, item: LibraryItem) -> dict:
        payload = {"item": self.serialize(item, summary=True).model_dump(mode="json")}
        if item.visibility == "shared":
            payload["broadcast"] = True
        elif item.user_id:
            payload["user_id"] = item.user_id
        return payload


# Display metadata a household member may see. An allowlist, not a
# denylist: raw extraction data (formats/requested_downloads with signed URLs,
# http_headers, cookies, filepath/filename, local thumbnails) never leaves the
# server; playback and file management read the stored row server-side.
PUBLIC_METADATA_KEYS = frozenset({
    "id", "title", "description", "uploader", "uploader_id", "uploader_url", "channel", "channel_id",
    "channel_url", "channel_follower_count", "channel_is_verified", "artist", "track", "album", "genre",
    "categories", "tags", "webpage_url", "original_url", "extractor", "extractor_key", "upload_date",
    "timestamp", "release_timestamp", "view_count", "like_count", "comment_count", "availability",
    "duration", "width", "height", "fps", "resolution", "ext", "format_note", "vcodec", "acodec",
    "dynamic_range", "tbr", "abr", "audio_channels", "asr", "lumina_media_kind", "lumina_variant_key",
    "subtitles_count", "formats_count", "chapters_count",
    "series", "season_number", "episode_number", "episode_number_end", "episode", "release_year", "track_number",
    "lumina_import_kind", "lumina_title_source",
})
_PUBLIC_URL_KEYS = frozenset({"uploader_url", "channel_url", "webpage_url", "original_url"})
_PUBLIC_SCALARS = (str, int, float, bool)


def public_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Member-safe projection: allowlisted keys with scalar (or list-of-str) values only."""
    if not isinstance(metadata, dict):
        return {}
    public: dict[str, Any] = {}
    for key in PUBLIC_METADATA_KEYS & metadata.keys():
        value = metadata[key]
        if isinstance(value, list):
            value = [entry for entry in value if isinstance(entry, str)]
        elif value is not None and not isinstance(value, _PUBLIC_SCALARS):
            continue
        if key in _PUBLIC_URL_KEYS and not (isinstance(value, str) and value.startswith(("https://", "http://"))):
            continue
        public[key] = value
    return public


# Request-credential material that must never be persisted into Library
# metadata. Playback re-resolves fresh transport metadata server-side, so nothing
# reads these from stored metadata; keeping them would let a member read a Saved
# sign-in's request headers or cookies back through the item detail.
_REQUEST_CREDENTIAL_KEYS = frozenset({"http_headers", "cookies"})


def _redact_request_credentials(value: Any) -> Any:
    """Return a copy of ``value`` with request headers and cookies stripped at every depth.

    yt-dlp nests format dicts (formats, requested_formats, and
    requested_downloads[].requested_formats), each of which can carry its own
    http_headers/cookies, so the walk strips those keys from every dict at any
    depth. Fresh containers are built throughout, so the caller's info dict — used
    elsewhere for format resolution — is never mutated.
    """

    if isinstance(value, dict):
        return {
            key: _redact_request_credentials(item)
            for key, item in value.items()
            if key not in _REQUEST_CREDENTIAL_KEYS
        }
    if isinstance(value, list):
        return [_redact_request_credentials(item) for item in value]
    return value


def normalize_thumbnail_metadata(info: dict[str, Any] | None, thumbnail_url: str | None, file_path: str | None = None) -> dict[str, Any]:
    metadata = _redact_request_credentials(dict(info or {}))
    if thumbnail_url:
        metadata["thumbnail"] = thumbnail_url
    resolved_path = file_path or resolve_download_output_path(metadata)
    if resolved_path:
        metadata["filepath"] = resolved_path
        suffix = Path(resolved_path).suffix.lower().lstrip(".")
        if suffix:
            metadata["ext"] = suffix
    metadata["lumina_media_kind"] = infer_media_kind(metadata, resolved_path)
    metadata["lumina_variant_key"] = build_media_variant_key(metadata, resolved_path)
    return metadata


def resolve_download_output_path(info: dict[str, Any] | None) -> str | None:
    if not info:
        return None
    direct_candidates = (
        info.get("filepath"),
        info.get("filename"),
        info.get("_filename"),
    )
    for candidate in direct_candidates:
        if isinstance(candidate, str) and candidate:
            return candidate

    requested_downloads = info.get("requested_downloads")
    if isinstance(requested_downloads, list):
        for download in requested_downloads:
            if not isinstance(download, dict):
                continue
            for candidate in (download.get("filepath"), download.get("filename"), download.get("_filename")):
                if isinstance(candidate, str) and candidate:
                    return candidate

    return None


def normalize_duration_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(round(value)))
    return None


def library_kind(metadata: dict[str, Any]) -> str:
    """The library view an item belongs to: import grouping first, then recording, then audio/video.

    Extras get their own kind, so the Videos/Movies grids never list them.
    """
    imported = metadata.get("lumina_import_kind")
    if imported in {"movie", "episode", "track", "extra"}:
        return imported
    if metadata.get("lumina_live_recording"):
        return "recording"
    return infer_media_kind(metadata)


def infer_media_kind(info: dict[str, Any] | None, file_path: str | None = None) -> str:
    extension = Path(file_path).suffix.lower() if file_path else ""
    if extension in {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}:
        return "audio"
    if extension in {".mp4", ".m4v", ".mkv", ".mov", ".avi"}:
        return "video"

    metadata = info or {}
    raw_ext = metadata.get("ext")
    if isinstance(raw_ext, str) and raw_ext.lower() in {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"}:
        return "audio"
    if isinstance(raw_ext, str) and raw_ext.lower() in {"mp4", "m4v", "mkv", "mov", "avi"}:
        return "video"

    height = metadata.get("height")
    if isinstance(height, (int, float)) and height > 0:
        return "video"

    requested_downloads = metadata.get("requested_downloads")
    if isinstance(requested_downloads, list):
        for download in requested_downloads:
            if not isinstance(download, dict):
                continue
            download_vcodec = download.get("vcodec")
            if isinstance(download_vcodec, str) and download_vcodec.lower() != "none":
                return "video"
            if isinstance(download.get("height"), (int, float)) and download.get("height", 0) > 0:
                return "video"

    vcodec = metadata.get("vcodec")
    if isinstance(vcodec, str) and vcodec.lower() == "none":
        return "audio"

    return "video"


def build_media_variant_key(info: dict[str, Any] | None, file_path: str | None = None) -> str:
    metadata = info or {}
    media_kind = infer_media_kind(metadata, file_path)
    extension = Path(file_path).suffix.lower().lstrip(".") if file_path else ""
    raw_ext = metadata.get("ext")
    container = extension or (raw_ext.lower() if isinstance(raw_ext, str) else "bin")

    if media_kind == "audio":
        audio_codec = metadata.get("acodec")
        if not isinstance(audio_codec, str) or not audio_codec:
            requested_downloads = metadata.get("requested_downloads")
            if isinstance(requested_downloads, list):
                for download in requested_downloads:
                    if isinstance(download, dict) and isinstance(download.get("acodec"), str):
                        audio_codec = download["acodec"]
                        break
        audio_codec = (audio_codec or "audio").split(".")[0].lower()
        return f"audio:{container}:{audio_codec}"

    height = metadata.get("height")
    fps = metadata.get("fps")
    if not isinstance(height, (int, float)) or height <= 0:
        requested_downloads = metadata.get("requested_downloads")
        if isinstance(requested_downloads, list):
            for download in requested_downloads:
                if isinstance(download, dict) and isinstance(download.get("height"), (int, float)) and download.get("height", 0) > 0:
                    height = download.get("height")
                    fps = download.get("fps") if isinstance(download.get("fps"), (int, float)) else fps
                    break
    vcodec = metadata.get("vcodec")
    if not isinstance(vcodec, str) or not vcodec:
        requested_downloads = metadata.get("requested_downloads")
        if isinstance(requested_downloads, list):
            for download in requested_downloads:
                if isinstance(download, dict) and isinstance(download.get("vcodec"), str) and download.get("vcodec"):
                    vcodec = download["vcodec"]
                    break
    vcodec = (vcodec or "video").split(".")[0].lower()
    height_value = int(height) if isinstance(height, (int, float)) else 0
    fps_value = int(fps) if isinstance(fps, (int, float)) else 0
    return f"video:{container}:{height_value}:{fps_value}:{vcodec}"


def item_variant_key(item: LibraryItem) -> str:
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    stored = metadata.get("lumina_variant_key")
    if isinstance(stored, str) and stored:
        return stored
    return build_media_variant_key(metadata, item.file_path)


def infer_item_media_kind(item: LibraryItem) -> str:
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    stored = metadata.get("lumina_media_kind")
    if isinstance(stored, str) and stored:
        return stored
    return infer_media_kind(metadata, item.file_path)


def normalize_thumbnail_url(info: dict[str, Any] | None, fallback: str | None = None) -> str | None:
    candidates = collect_thumbnail_candidates(info, fallback)
    return candidates[0]["url"] if candidates else None


def collect_thumbnail_candidates(info: dict[str, Any] | None, fallback: str | None = None) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}

    def append_candidate(url: str | None, width: int | None = None, height: int | None = None) -> None:
        if not url:
            return
        normalized = url.strip()
        if not normalized or normalized in unique:
            return
        unique[normalized] = {"url": normalized, "width": width, "height": height}

    def append_derived_youtube_candidates(url: str | None) -> None:
        if not url:
            return
        match = re.match(
            r"^(https:\/\/i\.ytimg\.com\/)(vi(?:_webp)?\/[^/]+\/)(maxresdefault|hq720|sddefault|hqdefault|mqdefault|default|[123])(\.(?:jpg|webp))(.*)$",
            url,
            re.IGNORECASE,
        )
        if not match:
            return
        prefix, middle, _variant, extension, suffix = match.groups()
        for variant in ("sddefault", "hqdefault", "mqdefault", "default", "3", "2", "1"):
            append_candidate(f"{prefix}{middle}{variant}{extension}{suffix}")

    thumbnails = info.get("thumbnails") if isinstance(info, dict) else None
    if isinstance(thumbnails, list):
        for entry in thumbnails:
            if not isinstance(entry, dict):
                continue
            width = entry.get("width") if isinstance(entry.get("width"), int) else None
            height = entry.get("height") if isinstance(entry.get("height"), int) else None
            append_candidate(entry.get("url") if isinstance(entry.get("url"), str) else None, width, height)

    raw_thumbnail = info.get("thumbnail") if isinstance(info, dict) and isinstance(info.get("thumbnail"), str) else None
    append_candidate(raw_thumbnail)
    append_derived_youtube_candidates(raw_thumbnail)
    append_candidate(fallback)
    append_derived_youtube_candidates(fallback)

    return sorted(unique.values(), key=score_thumbnail_candidate, reverse=True)


def score_thumbnail_candidate(candidate: dict[str, Any]) -> int:
    lower = str(candidate.get("url") or "").lower()
    width = candidate.get("width") if isinstance(candidate.get("width"), int) else 0
    height = candidate.get("height") if isinstance(candidate.get("height"), int) else 0
    score = width * height

    if "ytimg.com" in lower:
        if "/sddefault" in lower:
            score += 2_000_000
        elif "/hqdefault" in lower:
            score += 1_700_000
        elif "/mqdefault" in lower:
            score += 1_100_000
        elif "/default" in lower:
            score += 900_000
        elif "/1." in lower or "/2." in lower or "/3." in lower:
            score += 700_000
        elif "/hq720" in lower:
            score += 300_000

        if "/maxresdefault" in lower:
            score -= 3_000_000
        if "vi_webp/" in lower:
            score -= 5_000

    if ".jpg" in lower:
        score += 500

    return score
