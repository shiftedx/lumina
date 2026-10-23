"""Member Home shelf layout for the opt-in Jellyfin Connected-app surface."""
from __future__ import annotations

from typing import Annotated
from hashlib import sha256
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import SmartCollectionRule
from app.models import LibraryItem, MediaTitle, PlaybackProgress, SourceAutomation
from app.persistence import write_transaction
from app.routers import discovery
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.routers.jellyfin_settings import native_streaming_enabled
from app.services import jellyfin as jf
from app.services import smart_collections, streaming_gate
from app.services.household_collections import HouseholdCollectionService
from app.services.library import LibraryService
from app.services.playlists import WatchQueueService
from app.services.titles import TitleService
from app.services.user_settings import UserSettingsService


# Keep this in lockstep with frontend/src/features/home/homeShelves.ts.  It is
# intentionally a frozen wire-level allowlist rather than a client-provided catalogue.
HOME_SHELF_IDS = (
    "continue", "next_up", "live", "watchlist", "new_in_library", "because_you_watched", "recommended",
    "picked_for_you", "collections", "from_follows", "recently_saved", "new_anime", "recent_music", "continue_listening",
)
_HOME_SHELF_ID_SET = frozenset(HOME_SHELF_IDS)
_STORED_SHELVES_CAP = 64

router = APIRouter(prefix="/jellyfin/lumina/home")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]


class Shelf(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr
    visible: StrictBool


class LayoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shelves: list[Shelf] | None = Field(..., max_length=_STORED_SHELVES_CAP)

    @model_validator(mode="after")
    def valid_shelves(self) -> "LayoutBody":
        if self.shelves is None:
            return self
        ids = [shelf.id for shelf in self.shelves]
        if any(shelf_id not in _HOME_SHELF_ID_SET for shelf_id in ids):
            raise ValueError("Unknown Home shelf id.")
        if len(ids) != len(set(ids)):
            raise ValueError("Home shelf ids must be unique.")
        return self


def _normalize_shelves(stored: object) -> list[dict[str, str | bool]]:
    """Match the website's resilient reader for a non-null stored preference."""
    layout: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    entries = stored[:_STORED_SHELVES_CAP] if isinstance(stored, list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        shelf_id = entry.get("id")
        if not isinstance(shelf_id, str) or shelf_id not in _HOME_SHELF_ID_SET or shelf_id in seen:
            continue
        seen.add(shelf_id)
        visible = entry.get("visible")
        layout.append({"id": shelf_id, "visible": visible if isinstance(visible, bool) else True})
    for shelf_id in HOME_SHELF_IDS:
        if shelf_id not in seen:
            layout.append({"id": shelf_id, "visible": True})
    return layout


def _layout_from_prefs(prefs: object) -> list[dict[str, str | bool]] | None:
    if not isinstance(prefs, dict) or prefs.get("home_shelves") is None:
        return None
    return _normalize_shelves(prefs["home_shelves"])


@router.get("/layout")
def get_layout(caller: Caller, db: Db) -> dict[str, list[dict[str, str | bool]] | None]:
    """Read only this member's shared web/native Home preference without creating a row."""
    settings = UserSettingsService(db).snapshot_for_user(caller.user)
    return {"shelves": _layout_from_prefs(settings.ui_prefs)}


@router.put("/layout")
async def put_layout(request: Request, caller: Caller, db: Db) -> dict[str, list[dict[str, str | bool]] | None]:
    """Replace only ``ui_prefs.home_shelves`` and retain every unrelated web preference."""
    try:
        body = LayoutBody.model_validate(await request.json())
    except ValueError as exc:
        # The global Jellyfin-compatible validation handler uses 400 for legacy
        # clients. This Lumina JSON extension has an explicit 422 contract.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    shelves = None if body.shelves is None else _normalize_shelves([shelf.model_dump() for shelf in body.shelves])
    with write_transaction(db, name="native_home_layout"):
        settings = UserSettingsService(db).ensure_for_user(caller.user)
        prefs = dict(settings.ui_prefs) if isinstance(settings.ui_prefs, dict) else {}
        prefs["home_shelves"] = shelves
        settings.ui_prefs = prefs
        db.flush()
    return {"shelves": shelves}


_ROW_LIMIT = 20
_BECAUSE_ROWS = 3
_SMART_COLLECTION_ROWS = 4


def _row(id: str, shelf_id: str, title: str, shape: str, items: list[dict]) -> dict | None:
    """Emit only real, currently playable rows; empty web shelves stay absent."""
    return {"id": id, "shelf_id": shelf_id, "title": title, "shape": shape, "items": items[:_ROW_LIMIT]} if items else None


def _watchlist(mapper: jf.JellyfinMapper, caller: JellyfinCaller, db: Session) -> list[dict]:
    _revision, entries = WatchQueueService(db).read(caller.user)
    return mapper.item_dtos([item for _entry, item in entries if item is not None and item.status != "missing"])


def _continue_listening(mapper: jf.JellyfinMapper, caller: JellyfinCaller, db: Session) -> list[dict]:
    """The audio subset of the same member-scoped checkpoint source as web Home."""
    rows = list(db.scalars(
        select(LibraryItem)
        .join(PlaybackProgress, PlaybackProgress.item_id == LibraryItem.id)
        .where(
            PlaybackProgress.user_id == caller.user.id,
            PlaybackProgress.completed.is_(False),
            PlaybackProgress.position_seconds > 0,
            PlaybackProgress.dismissed_at.is_(None),
            LibraryItem.kind.in_(("track", "audio")),
            LibraryItem.status != "missing",
            LibraryService.visible_predicate(caller.user),
        )
        .order_by(PlaybackProgress.last_watched_at.desc(), PlaybackProgress.updated_at.desc(), PlaybackProgress.id.desc())
        .limit(_ROW_LIMIT)
    ))
    return mapper.item_dtos(rows)


def _title_dtos(mapper: jf.JellyfinMapper, ids: list[str]) -> list[dict]:
    """The mapper keeps a title's member progress, art scope, and preferred version intact."""
    return mapper.by_ids(ids[:_ROW_LIMIT])


def _new_in_library(mapper: jf.JellyfinMapper, caller: JellyfinCaller, db: Session) -> list[dict]:
    """The website's All wall: movie and series titles by arrival time."""
    titles, _start, _following = TitleService(db).page(
        caller.user, types=("movie", "series"), sort="created", limit=_ROW_LIMIT,
    )
    return mapper.title_dtos(titles)


def _new_anime(mapper: jf.JellyfinMapper, caller: JellyfinCaller, db: Session) -> list[dict]:
    """The website's Anime category wall, including both films and series."""
    titles, _start, _following = TitleService(db).page(
        caller.user, types=("movie", "series"), sort="created", limit=_ROW_LIMIT, category="anime",
    )
    return mapper.title_dtos(titles)


def _recent_music(mapper: jf.JellyfinMapper, caller: JellyfinCaller, db: Session) -> list[dict]:
    titles, _start, _following = TitleService(db).page(
        caller.user, types=("album",), sort="created", limit=_ROW_LIMIT,
    )
    return [mapper.music_title_dto(title) for title in titles]


def _recently_saved(mapper: jf.JellyfinMapper, caller: JellyfinCaller, db: Session) -> list[dict]:
    """The web Library's current saved-media page, without browser-only remote rows."""
    items, _cursor = LibraryService(db).list_items_page(caller.user, limit=_ROW_LIMIT, source="saved", sort="recent")
    return mapper.item_dtos(items)


def _collections(mapper: jf.JellyfinMapper, caller: JellyfinCaller, db: Session) -> list[dict]:
    service = HouseholdCollectionService(db)
    collections = [
        collection for collection in service.shown_to(caller.user, service.list_visible(caller.user.id), remote=False)
        if collection.rules is not None and collection.owner_user_id == caller.user.id
    ][:_SMART_COLLECTION_ROWS]
    rows: list[dict] = []
    for collection in collections:
        # Stored rules originate from the web's strict editor. Validate once more
        # before evaluation rather than interpreting arbitrary JSON in this adapter.
        try:
            rule = SmartCollectionRule.model_validate(collection.rules)
            matches = smart_collections.evaluate(db, caller.user, rule)[:_ROW_LIMIT]
        except ValueError:
            continue
        if rule.type == "channel_video":
            items = mapper.item_dtos([item for item in matches if isinstance(item, LibraryItem)])
            shape = "still"
        elif rule.type in {"album", "artist"}:
            items = [mapper.music_title_dto(title) for title in matches if isinstance(title, MediaTitle) and title.type in {"album", "artist"}]
            shape = "square"
        else:
            # TV navigation only supports playable video title cards. A smart rule
            # can also match box sets, albums, artists, or books; do not surface a
            # card that this client cannot open or play.
            titles = [
                title for title in matches
                if isinstance(title, MediaTitle) and title.type in {"movie", "series", "season", "episode"}
            ]
            items = mapper.title_dtos(titles)
            shape = "poster"
        if row := _row(f"collections:{collection.id}", "collections", collection.name, shape, items):
            rows.append(row)
    return rows


def _external_row(shelf_id: str, title: str, entries: list, caller: JellyfinCaller, db: Session) -> dict | None:
    """Read-only source handoffs: metadata never grants native extraction or acquisition."""
    items: list[dict] = []
    seen: set[str] = set()
    for value in entries:
        entry = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        if not isinstance(entry, dict):
            continue
        url = entry.get("webpage_url")
        if not isinstance(url, str) or url in seen:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
                continue
        except ValueError:
            continue
        # Match the member's provider/follow/live restrictions before exposing a source.
        channels = [value for key in ("uploader_url", "channel_url") if isinstance(value := entry.get(key), str)]
        if not streaming_gate.allows_url(db, caller.user, url, channels=channels, live=shelf_id == "live" or entry.get("kind") == "live"):
            continue
        seen.add(url)
        thumbnail = entry.get("thumbnail")
        try:
            artwork = urlsplit(thumbnail) if isinstance(thumbnail, str) else None
            image_hosts = ("ytimg.com", "youtube.com", "ggpht.com", "jtvnw.net", "twitch.tv", "kick.com", "kickcdn.com", "vimeocdn.com", "archive.org", "cloudfront.net")
            safe_artwork = bool(artwork and artwork.scheme == "https" and not artwork.username and not artwork.password
                                and any(artwork.hostname == host or (artwork.hostname or "").endswith("." + host) for host in image_hosts))
        except ValueError:
            safe_artwork = False
        items.append({
            "id": sha256(url.encode()).hexdigest()[:24], "title": entry.get("title") if isinstance(entry.get("title"), str) else "Untitled video",
            "subtitle": entry.get("uploader") if isinstance(entry.get("uploader"), str) else "", "url": url,
            "artwork_url": thumbnail if safe_artwork else None,
        })
        if len(items) == _ROW_LIMIT:
            break
    return {"id": shelf_id, "shelf_id": shelf_id, "title": title, "shape": "still", "items": [], "external_items": items} if items else None


def _attach_home_metadata(rows: list[dict], caller: JellyfinCaller, db: Session) -> None:
    """Add local category and title facts only to this native Home response.

    The ordinary Jellyfin DTO remains compatible. Home may contain direct
    ``LibraryItem`` watchlist entries, so resolve their title/series facts here
    rather than treating an item's filename type as a catalog category.
    """
    dtos = [item for row in rows for item in row.get("items", []) if isinstance(item, dict)]
    dto_ids = {entity_id for dto in dtos if (entity_id := jf.parse_item_id(dto.get("Id")))}
    if not dto_ids:
        return
    library_titles = dict(db.execute(
        select(LibraryItem.id, MediaTitle.id)
        .join(MediaTitle, MediaTitle.id == LibraryItem.title_id)
        .where(LibraryItem.id.in_(dto_ids))
    ).all())
    titles = {
        title.id: title
        for title in db.scalars(select(MediaTitle).where(MediaTitle.id.in_(dto_ids | set(library_titles.values()))))
    }
    # A queued episode's title points to its season; load one further ancestor
    # so a matching Series card can truthfully participate in Watchlist.
    for _ in range(2):
        missing = {title.parent_id for title in titles.values() if title.parent_id and title.parent_id not in titles}
        if not missing:
            break
        titles.update({
            title.id: title
            for title in db.scalars(select(MediaTitle).where(MediaTitle.id.in_(missing)))
        })
    for dto in dtos:
        entity_id = jf.parse_item_id(dto.get("Id"))
        if not entity_id:
            continue
        title = titles.get(entity_id) or titles.get(library_titles.get(entity_id, ""))
        if title is None:
            continue
        if title.category in {"movies", "shows", "anime"}:
            dto["LuminaCategory"] = title.category
        series = None
        if title.type == "episode":
            season = titles.get(title.parent_id or "")
            series = titles.get(season.parent_id or "") if season and season.type == "season" else None
            if series is not None:
                dto["SeriesId"] = jf.jid(series.id)
        elif title.type == "season":
            series = titles.get(title.parent_id or "")
        genres = [genre for genre in (title.metadata_json or {}).get("genres", []) if isinstance(genre, str) and genre]
        if not genres and series is not None:
            genres = [genre for genre in (series.metadata_json or {}).get("genres", []) if isinstance(genre, str) and genre]
        if genres:
            dto["Genres"] = genres


def _external_rows(request: Request, caller: JellyfinCaller, db: Session, visible: set[str]) -> dict[str, dict | None]:
    # Reuse the website's cached snapshots and ranking; never perform extraction here.
    from app.main import home_recommendations, live_now
    rows: dict[str, dict | None] = {}
    for shelf_id, title, kind, loader in [
        ("live", "Live now", "live", live_now),
        ("picked_for_you", "Picked for you", "youtube", home_recommendations),
    ]:
        if shelf_id not in visible:
            continue
        try:
            streaming_gate.check(db, caller.user, kind, beyond_follows=True)
            snapshot = loader(request, caller.user, db)
            entries = [*getattr(snapshot, "hero", []), *snapshot.items]
            rows[shelf_id] = _external_row(shelf_id, title, entries, caller, db)
        except HTTPException as error:
            if error.status_code not in {403, 429, 503}:
                raise
    if "from_follows" in visible:
        follows = db.scalars(select(SourceAutomation).where(
            SourceAutomation.user_id == caller.user.id, SourceAutomation.source_type == "channel",
        ).order_by(SourceAutomation.updated_at.desc())).all()
        entries = [{**entry, "channel_url": entry.get("channel_url") or follow.source_url}
                   for follow in follows for entry in (follow.feed_entries or []) if isinstance(entry, dict)]
        entries.sort(key=lambda entry: str(entry.get("published_at") or ""), reverse=True)
        rows["from_follows"] = _external_row("from_follows", "New from your follows", entries, caller, db)
    if "watchlist" in visible:
        _revision, queued = WatchQueueService(db).read(caller.user)
        entries = [{"webpage_url": entry.source_url, "title": entry.title, "uploader": entry.uploader,
                    "thumbnail": entry.artwork_url} for entry, _item in queued if entry.library_item_id is None]
        rows["watchlist"] = _external_row("watchlist", "Your watchlist", entries, caller, db)
    return rows


@router.get("/rows")
def get_rows(request: Request, caller: Caller, db: Db, include_external: bool = False) -> dict[str, list[dict]]:
    """The shared Home sources: local playback DTOs plus read-only external source links."""
    settings = UserSettingsService(db).snapshot_for_user(caller.user)
    layout = _layout_from_prefs(settings.ui_prefs) or _normalize_shelves([])
    mapper = jf.JellyfinMapper(db, caller.user)
    visible_ids = {entry["id"] for entry in layout if entry["visible"]}
    title_rows = discovery.get_home_title_rows(caller.user, db).rows if visible_ids & {"because_you_watched", "recommended"} else []
    external = _external_rows(request, caller, db, visible_ids) if include_external and native_streaming_enabled(db, caller.user) else {}
    builders = {
        "continue": lambda: [_row("continue", "continue", "Continue watching", "still", mapper.resume(None, set(), 0, _ROW_LIMIT)["Items"])],
        "next_up": lambda: [_row("next_up", "next_up", "Next up", "still", mapper.next_up(None, 0, _ROW_LIMIT)["Items"])],
        "watchlist": lambda: [_row("watchlist", "watchlist", "Your watchlist", "still", _watchlist(mapper, caller, db))],
        "new_in_library": lambda: [_row("new_in_library", "new_in_library", "New in your library", "poster", _new_in_library(mapper, caller, db))],
        "recently_saved": lambda: [_row("recently_saved", "recently_saved", "Recently saved", "still", _recently_saved(mapper, caller, db))],
        "new_anime": lambda: [_row("new_anime", "new_anime", "New in Anime", "poster", _new_anime(mapper, caller, db))],
        "recent_music": lambda: [_row("recent_music", "recent_music", "Recently added music", "square", _recent_music(mapper, caller, db))],
        "continue_listening": lambda: [_row("continue_listening", "continue_listening", "Continue listening", "square", _continue_listening(mapper, caller, db))],
        "because_you_watched": lambda: [
            _row(row.id, "because_you_watched", row.title, "poster", _title_dtos(mapper, [item.id for item in row.items]))
            for row in [row for row in title_rows if row.kind == "because_you_watched"][:_BECAUSE_ROWS]
        ],
        "recommended": lambda: [
            _row(row.id, "recommended", row.title, "poster", _title_dtos(mapper, [item.id for item in row.items]))
            for row in title_rows if row.kind == "recommended"
        ],
        "collections": lambda: _collections(mapper, caller, db),
    }
    rows: list[dict] = []
    for preference in layout:
        if not preference["visible"]:
            continue
        built = [row for row in build() if row is not None] if (build := builders.get(preference["id"])) else []
        if remote := external.get(preference["id"]):
            if built:
                built[0]["external_items"] = remote["external_items"]
            else:
                built.append(remote)
        rows.extend(built)
    _attach_home_metadata(rows, caller, db)
    return {"rows": rows}
