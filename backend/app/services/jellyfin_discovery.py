"""Jellyfin discovery glue: search, similar, suggestions, dismiss, Next Up options."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import sqlite3
from typing import Any

from sqlalchemy.orm import Session

from app.models import MediaTitle, User
from app.routers.discovery import similar_types
from app.services import jellyfin as jf  # the Jellyfin type map (FROM_JF_TYPES)
from app.services import embeddings, member_access, reco
from app.services.library import LibraryService
from app.services.member_recommendations import MemberRecommendationPolicy
from app.services.reco.policy import RecommendationPolicy
from app.services.semantic_discovery import DeterministicLocalEncoder, discovery, search_ids
from app.services.titles import EpisodeProgress

SEARCH_LIMIT = 200
SEARCH_CACHE_ENTRIES = 32
_SEARCH_CACHE = "jellyfin_ranked_refs"
_SEARCH_PAGE_CACHE = "jellyfin_movie_pages"
SEARCH_PAGE_CACHE_ENTRIES = 4
SEARCH_PAGE_CACHE_BYTES = 128 * 1024
# FROM_JF_TYPES already covers series/season/episode/movie/boxset; the only
# hand-off addition is Video -> library (untitled Channels items have no title type).
_VIDEO_TYPE = "library"
_HINT_KEYS = ("Type", "IsFolder", "MediaType", "IndexNumber", "ParentIndexNumber", "ProductionYear", "RunTimeTicks", "Status", "EndDate")


def item_types(raw: str | None) -> set[str] | None:
    """Jellyfin includeItemTypes -> TitleType set. None = no filter; an empty set = only unsupported types asked."""
    requested = [part.strip().casefold() for part in (raw or "").split(",") if part.strip()]
    if not requested:
        return None
    mapped = {jf.FROM_JF_TYPES[name] for name in requested if name in jf.FROM_JF_TYPES}
    if "video" in requested:
        mapped.add(_VIDEO_TYPE)
    return mapped


_JELLYFIN_KINDS = frozenset({"title", "moment", "library"})  # channel/automation hits have no Jellyfin item


def _search_types(types: set[str] | None) -> set[str]:
    """TitleTypes -> the search document types. Episodes include moments (shown as their episode) and untitled Channels items."""
    wanted = set(types) if types is not None else {"movie", "series", "episode", "boxset"}
    return wanted | {"moment", "library"} if "episode" in wanted else wanted


def _search_cache(db: Session, user: User, term: str, types: set[str], limit: int):  # noqa: ANN202
    """Connection-local refs, valid only while SQLite data and literal caller limits are unchanged."""
    if (
        member_access.ACCESS_ATTR not in user.__dict__ or db.new or db.dirty or db.deleted
        or type(discovery._encoder) is not DeterministicLocalEncoder or embeddings.serving(db) is not None
    ):
        return None
    connection = db.connection()
    raw = connection.connection.driver_connection
    if not isinstance(raw, sqlite3.Connection) or raw.in_transaction:
        return None
    # data_version detects other connections' commits. It is meaningful only
    # on this same connection; total_changes also catches its own writes,
    # including rolled-back writes, which conservatively evict. Active SQLite
    # transactions bypass reuse so uncommitted refs cannot survive a rollback.
    version = (connection.exec_driver_sql("PRAGMA data_version").scalar_one(), raw.total_changes)
    previous, cache = connection.info.get(_SEARCH_CACHE, (None, OrderedDict()))
    if version != previous:
        cache = OrderedDict()
        connection.info[_SEARCH_CACHE] = (version, cache)
    access = user.__dict__[member_access.ACCESS_ATTR] if user.role != "admin" else None
    scope = (access.sections, access.movie_rating_max, access.tv_rating_max, access.unrated) if access is not None else None
    key = (user.id, user.role, scope, " ".join(term.strip().split()), frozenset(types), limit)
    return connection, raw, version, cache, key


def search_refs(db: Session, user: User, term: str, types: set[str] | None, limit: int) -> list[str]:
    """Ordered, de-duplicated refs (title id, or item id for Channels items). A moment collapses to its episode."""
    if not term.strip() or types == set():
        return []
    wanted = _search_types(types)
    cached = _search_cache(db, user, term, wanted, limit)
    if cached is not None:
        connection, raw, version, cache, key = cached
        if key in cache:
            cache.move_to_end(key)
            return list(cache[key])
    refs: list[str] = []
    for hit in search_ids(db, user, term, types=wanted, limit=limit):  # visibility-filtered before ranking (G)
        if hit.kind not in _JELLYFIN_KINDS:
            continue
        ref = hit.id if hit.kind == "title" else (hit.title_id or hit.id)
        if ref not in refs:
            refs.append(ref)
    if cached is not None:
        current = (connection.exec_driver_sql("PRAGMA data_version").scalar_one(), raw.total_changes)
        if current == version and not raw.in_transaction and not (db.new or db.dirty or db.deleted):
            cache[key] = tuple(refs)  # callers always receive their own mutable list
            cache.move_to_end(key)
            while len(cache) > SEARCH_CACHE_ENTRIES:
                cache.popitem(last=False)
    return refs


def cached_search_page(
    db: Session, user: User, query: jf.ItemsQuery, mapper: jf.JellyfinMapper, render: Callable[[], dict],
) -> dict:
    """Copy unchanged default movie cards; other projections keep their ordinary rendering path."""
    types = item_types(",".join(query.csv("includeitemtypes")))
    if types != {"movie"} or query.csv("fields") or query.csv("sortby"):
        return render()
    cached = _search_cache(db, user, query.searchterm or "", types, SEARCH_LIMIT)
    if cached is None:
        return render()
    connection, raw, version, _refs, ref_key = cached
    previous, pages = connection.info.get(_SEARCH_PAGE_CACHE, (None, OrderedDict()))
    if previous != version:
        pages = OrderedDict()
        connection.info[_SEARCH_PAGE_CACHE] = (version, pages)
    # Default movie cards depend only on DB rows, caller limits and signing key.
    # ServerId identifies the current key without keeping that secret in the cache.
    key = (ref_key, query.startindex, query.page_limit(), mapper.server_id, mapper.scope)
    if key in pages:
        pages.move_to_end(key)
        return json.loads(pages[key])
    result = render()
    try:
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, OverflowError):
        return result  # retain the normal response encoder's contract for unusual metadata
    current = (connection.exec_driver_sql("PRAGMA data_version").scalar_one(), raw.total_changes)
    if (
        current == version and not raw.in_transaction and not (db.new or db.dirty or db.deleted)
        and len(encoded) <= SEARCH_PAGE_CACHE_BYTES
    ):
        pages[key] = encoded
        pages.move_to_end(key)
        while len(pages) > SEARCH_PAGE_CACHE_ENTRIES:
            pages.popitem(last=False)
    return result


def search_hint(dto: dict[str, Any], term: str) -> dict[str, Any]:
    """SearchHint from a BaseItemDto (Jellyfin property names only)."""
    hint: dict[str, Any] = {"ItemId": dto["Id"], "Id": dto["Id"], "Name": dto.get("Name"), "MatchedTerm": term, "Artists": []}
    hint.update({key: dto[key] for key in _HINT_KEYS if dto.get(key) is not None})
    if dto.get("SeriesName"):
        hint["Series"] = dto["SeriesName"]
    tags = dto.get("ImageTags") or {}
    if "Primary" in tags:
        hint["PrimaryImageTag"] = tags["Primary"]
    if "Thumb" in tags:
        hint["ThumbImageTag"], hint["ThumbImageItemId"] = tags["Thumb"], dto["Id"]
    if dto.get("BackdropImageTags"):
        hint["BackdropImageTag"], hint["BackdropImageItemId"] = dto["BackdropImageTags"][0], dto["Id"]
    return hint


def similar_refs(db: Session, user: User, ref: str, limit: int) -> list[str] | None:
    """Similar titles for a visible anchor (G anchors episodes/seasons on their series); None when the anchor is invisible."""
    visible = db.query(MediaTitle).filter(MediaTitle.id == ref, LibraryService.visible_title_predicate(user)).one_or_none()
    if visible is None:
        return None
    if reco.enabled(db):
        # Jellyfin Similar is title_similar's ranking; Jellyfin clients receive no reasons.
        served = RecommendationPolicy(db, refresher=None, channel_pages=None).similar(user, visible, k=limit)
        return [ranked.candidate.key for ranked in served.items if ranked.candidate.key != visible.id][:limit]
    policy = MemberRecommendationPolicy(db, limit=limit)
    candidates = policy.titles(user, anchor=visible.id, types=similar_types(visible.type))
    return [candidate.id for candidate in candidates if candidate.id != visible.id][:limit]


def suggestion_refs(db: Session, user: User, types: set[str] | None, limit: int) -> list[str]:
    if types == set():
        return []
    if reco.enabled(db):
        # Jellyfin Suggestions rank like Recommended for you (2 of 12 exploring), no reasons.
        served = RecommendationPolicy(db, refresher=None, channel_pages=None).suggestions(user, k=limit, types=types or {"movie", "series"})
        return [ranked.candidate.key for ranked in served.items][:limit]
    policy = MemberRecommendationPolicy(db, limit=limit)
    return [title.id for title in policy.titles(user, types=types or {"movie", "series"})][:limit]


def dismisses_resume(body: Mapping[str, Any], *, resumable: bool) -> bool:
    """Jellyfin has no HideFromResume: clients POST UserData with PlaybackPositionTicks 0 and not Played.

    Confirm against the golden-traffic capture.
    """
    ticks = body.get("PlaybackPositionTicks")
    return resumable and type(ticks) is int and ticks == 0 and body.get("Played") is not True


def naive_utc(value: datetime | None) -> datetime | None:
    """Database timestamps are naive UTC."""
    return value.astimezone(UTC).replace(tzinfo=None) if value is not None and value.tzinfo else value


def next_up_filtered(
    ordered: Sequence[EpisodeProgress], candidate: EpisodeProgress | None, *, cutoff: datetime | None, rewatching: bool,
) -> EpisodeProgress | None:
    """Apply /Shows/NextUp's nextUpDateCutoff and enableRewatching=false to the ``next_up`` result for one series."""
    if candidate is None:
        return None
    anchor_at = max((episode.last_watched_at for episode in ordered if episode.last_watched_at), default=None)
    if cutoff is not None and (anchor_at is None or anchor_at < cutoff):
        return None
    if rewatching or not candidate.completed:
        return candidate
    later = ordered[ordered.index(candidate):]
    return next((episode for episode in later if not episode.completed and episode.season != 0), None)  # specials never Next Up
