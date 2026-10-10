"""Jellyfin wire format over Lumina's library.

Plain PascalCase dicts with None dropped. Ids are resolved visibility-first (views, Media titles,
Library items, synthetic Channels); nothing here builds a filesystem path from client input.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import hmac
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Integer, and_, bindparam, cast, false, func, literal, or_, select, true
from sqlalchemy.orm import Session, aliased, defer
from sqlalchemy.sql import operators, visitors
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from app.media_schemas import TitlePerson, TitleUserData
from app.models import AppSettings, DeviceToken, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MemberAccess, MemberFavorite, PlaybackProgress, StorageRoot, User
from app.schemas import PlaybackProgressUpdateRequest
from app.services.artwork import ArtworkError, ArtworkService
from app.services.connected_apps import jellyfin_server_id, jellyfin_server_key
from app.services.library import LibraryService
from app.services.media_artifacts import MediaArtifactService, artifact_file
from app.services.media_titles import jellyfin_id, parse_item_id, person_name_id, synthetic_id, to_ticks
from app.services.transcripts import to_iso639_2 as iso639_2
from app.services import playlists as pl
from app.services import member_access, renditions
from app.services.playback import PlaybackProgressService
from app.services.subtitle_tracks import sidecars
from app.services.title_metadata import _credits, people_for_titles
from app.services.titles import (
    ADDED, FOLDER_TYPES, HDR_TRANSFERS, LEAF_TYPES, SORT_NAME, UNNUMBERED, WALL_TYPES, TitleBatch, TitleService,
    image_key, latest_progress, title_image_bytes, title_image_source, user_data_from, version_label,
)

JF_TYPES = {"series": "Series", "season": "Season", "episode": "Episode", "movie": "Movie", "boxset": "BoxSet"}
FROM_JF_TYPES = {name.lower(): kind for kind, name in JF_TYPES.items()}
VIEWS = {
    "movies": ("Movies", "movies"), "tvshows": ("Shows", "tvshows"), "anime": ("Anime", "tvshows"),
    "boxsets": ("Collections", "boxsets"), "channels": ("Channels", "tvshows"), "playlists": ("Playlists", "playlists"),
}
VIEW_IDS = {synthetic_id(f"view:{view}"): view for view in VIEWS}
VIEW_OF_TYPE = {"movie": "movies", "series": "tvshows", "boxset": "boxsets"}
VIEW_TYPES = {"movies": {"movie"}, "tvshows": {"series", "season", "episode"}, "anime": {"series", "season", "episode"}, "boxsets": {"boxset"}}
VIEW_DEFAULT = {"movies": "movie", "tvshows": "series", "anime": "series", "boxsets": "boxset"}
VIEW_CATEGORY = {"tvshows": "shows", "anime": "anime"}  # views narrowed by category
# One visible anime series lists the Anime view. WALL_TYPES as literals plus a type test the planner cannot seek from
# make it seek ix_media_titles_category_sort (category=?) instead of walking every series.
ANIME_SERIES_WHERE = (WALL_TYPES, func.coalesce(MediaTitle.type, "") == "series", MediaTitle.category == "anime")
EXTRA_TYPES = {
    "trailer": "Trailer", "featurette": "Featurette", "behindthescenes": "BehindTheScenes", "deletedscene": "DeletedScene",
    "interview": "Interview", "scene": "Scene", "short": "Short", "clip": "Clip", "other": "Unknown",
}
IMAGE_TYPES = {name.lower(): name for name in ("Primary", "Backdrop", "Logo", "Thumb", "Banner")}
STREAM_TYPES = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle"}
SUBTITLE_CODECS = {"srt": "subrip", "ass": "ass", "ssa": "ssa", "vtt": "webvtt"}
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
MAX_PAGE = 1000  # the largest page any list returns, whatever Limit a client sends


def jid(value: str | None) -> str | None:
    return jellyfin_id(value) if value else None


def view_of(title: MediaTitle) -> str:
    """A movie's, series' or boxset's view: anime series have their own; anime movies stay in Movies."""
    return "anime" if title.type == "series" and title.category == "anime" else VIEW_OF_TYPE[title.type]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def jf_date(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%S.0000000Z") if value else None


def jf_day(value: object) -> str | None:
    """"2020-03-01" or yt-dlp's "20200301" -> a Jellyfin date."""
    digits = str(value or "").replace("-", "")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}T00:00:00.0000000Z" if len(digits) == 8 and digits.isdigit() else None


def query_result(items: list[dict], total: int, start: int = 0) -> dict:
    return {"Items": items, "TotalRecordCount": total, "StartIndex": start}


def image_tag(key: bytes, target_id: str, image_type: str, source: object, scope: str = "") -> str:
    """Signed art tag. Only authorized callers receive one, so it also admits a token-less art fetch.

    A restricted member's tags end in their art scope (member_access.art_scope), which the HMAC also covers, so a
    scoped tag can neither be re-pointed at another member nor stripped back to the shared tag.
    """
    message = f"{target_id}:{image_type}:{source}" + (f":{scope}" if scope else "")
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()[:32] + scope


def title_tag(key: bytes, title: MediaTitle | None, image_type: str, scope: str = "") -> str | None:
    source = title_image_source(title, image_type) if title is not None else None
    return image_tag(key, title.id, image_type, source, scope) if source else None


_AIRS = (("airsbefore_season", "AirsBeforeSeasonNumber"), ("airsbefore_episode", "AirsBeforeEpisodeNumber"), ("airsafter_season", "AirsAfterSeasonNumber"))


_LOCKABLE = {"name": "Name", "overview": "Overview", "genres": "Genres", "official_rating": "OfficialRating", "people": "Cast",
             "studios": "Studios", "tags": "Tags", "runtime_minutes": "Runtime"}


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def title_metadata_fields(title: MediaTitle) -> dict[str, Any]:
    """BaseItemDto fields from metadata_json. Wrong types drop only themselves (lenient, like the NFO parser)."""
    meta = title.metadata_json if isinstance(title.metadata_json, dict) else {}
    out: dict[str, Any] = {}
    if isinstance(meta.get("tagline"), str) and meta["tagline"]:
        out["Taglines"] = [meta["tagline"]]
    studios = [name for name in meta["studios"] if isinstance(name, str) and name] if isinstance(meta.get("studios"), list) else []
    if studios:
        out["Studios"] = [{"Name": name, "Id": jid(synthetic_id(f"studio:{name}"))} for name in studios]
    if isinstance(meta.get("status"), str) and meta["status"]:
        out["Status"] = meta["status"]
    if isinstance(meta.get("end_date"), str) and len(meta["end_date"]) == 10:
        out["EndDate"] = f"{meta['end_date']}T00:00:00.0000000Z"
    out.update({name: meta[key] for key, name in _AIRS if _count(meta.get(key))})
    if _count(meta.get("runtime_minutes")) and meta["runtime_minutes"] > 0:
        out["RunTimeTicks"] = to_ticks(meta["runtime_minutes"] * 60)  # the caller keeps a probed runtime when it has one
    if isinstance(meta.get("original_title"), str) and meta["original_title"]:
        out["OriginalTitle"] = meta["original_title"]
    tags = meta["tags"] if isinstance(meta.get("tags"), list) else []
    if tags := [t for t in tags if isinstance(t, str) and t]:
        out["Tags"] = tags
    if _count(meta.get("critic_rating")) and 0 <= meta["critic_rating"] <= 100:
        out["CriticRating"] = meta["critic_rating"]
    if isinstance(meta.get("custom_rating"), str) and meta["custom_rating"]:
        out["CustomRating"] = meta["custom_rating"]
    if isinstance(meta.get("air_days"), list) and (days := [d for d in meta["air_days"] if isinstance(d, str)]):
        out["AirDays"] = days
    if isinstance(meta.get("air_time"), str) and meta["air_time"]:
        out["AirTime"] = meta["air_time"]
    if meta.get("display_order") in ("dvd", "absolute"):  # #164: Jellyfin's SeriesDisplayOrder values
        out["DisplayOrder"] = meta["display_order"]
    sources = getattr(title, "field_sources", None)
    sources = sources if isinstance(sources, dict) else {}
    out["LockData"] = bool(getattr(title, "locked", False))
    out["LockedFields"] = sorted(name for key, name in _LOCKABLE.items() if sources.get(key) == "user")
    return out


def person_dtos(people: list[TitlePerson], tag: Callable[[str, str, str], str]) -> list[dict[str, Any]]:
    """BaseItemPerson list. NFO-only names get a stable synthetic id so clients can key them."""
    rendered = []
    for person in people:
        ref = person.id or person_name_id(person.name)  # refs scanned before 1.6.0 have no id; same id either way
        dto = {"Name": person.name, "Id": jid(ref), "Role": person.role, "Type": person.type}
        if person.id and person.image_url:
            dto["PrimaryImageTag"] = tag(person.id, "Primary", person.image_url)
        rendered.append({key: value for key, value in dto.items() if value is not None})
    return rendered


def item_tag(key: bytes, item: LibraryItem, scope: str = "") -> str:
    return image_tag(key, item.id, "Primary", item.updated_at.isoformat() if item.updated_at else item.id, scope)


def user_data_dto(target_id: str, data: TitleUserData, runtime_seconds: float | None = None) -> dict:
    duration = data.duration_seconds or runtime_seconds
    return compact({
        "PlaybackPositionTicks": to_ticks(data.position_seconds) or 0,
        "PlayCount": 1 if data.played else 0,
        "IsFavorite": data.is_favorite,
        "Played": data.played,
        "LastPlayedDate": jf_date(data.last_watched_at),
        "PlayedPercentage": round(100 * data.position_seconds / duration, 2) if data.position_seconds and duration else None,
        "UnplayedItemCount": data.unplayed_count,
        "Key": jid(target_id),
        "ItemId": jid(target_id),
    })


def _number(value: object) -> float | None:
    """ffprobe numbers arrive as ints, floats or strings such as "48000" and "24000/1001"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        numerator, _, denominator = value.partition("/")
        try:
            return float(numerator) / float(denominator) if denominator else float(numerator)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def _int(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def display_title(kind: str, stream: dict, codec: str | None, *, external: bool = False) -> str:
    name = (codec or "").upper()
    if kind == "Video":
        height = _int(stream.get("height"))
        hdr = " HDR" if stream.get("color_transfer") in HDR_TRANSFERS else ""
        return f"{f'{height}p ' if height else ''}{name}{hdr}".strip()
    language = str(stream.get("language") or "Unknown").title() + (" Forced" if stream.get("forced") else "")
    layout = str(stream.get("channel_layout") or "") if kind == "Audio" else ""
    return " - ".join(part for part in (language, name, layout, "External" if external else "") if part)


def sidecar_base(probe: dict) -> int:
    """External streams start after the highest embedded ffprobe index (append-only order)."""
    indexes = [s["index"] for s in probe.get("streams") or [] if isinstance(s, dict) and isinstance(s.get("index"), int)]
    return max(indexes, default=-1) + 1


def media_streams(probe: dict, item: LibraryItem) -> list[dict]:
    """Embedded streams in probe order, then scanner sidecars. The integration module appends transcript tracks after these."""
    streams = []
    for stream in probe.get("streams") or []:
        kind = STREAM_TYPES.get(stream.get("type")) if isinstance(stream, dict) else None
        if kind is None or not isinstance(stream.get("index"), int):
            continue
        codec, transfer, video = stream.get("codec"), stream.get("color_transfer"), kind == "Video"
        streams.append(compact({
            "Index": stream["index"], "Type": kind, "Codec": codec, "Profile": stream.get("profile"), "Level": _int(stream.get("level")),
            "Language": iso639_2(stream.get("language")), "Title": stream.get("title"),
            "DisplayTitle": display_title(kind, stream, codec),
            "IsDefault": bool(stream.get("default")), "IsForced": bool(stream.get("forced")), "IsExternal": False, "IsInterlaced": False,
            "Width": _int(stream.get("width")), "Height": _int(stream.get("height")), "Channels": _int(stream.get("channels")),
            "ChannelLayout": stream.get("channel_layout"), "SampleRate": _int(stream.get("sample_rate")),
            "BitRate": _int(stream.get("bit_rate")), "PixelFormat": stream.get("pix_fmt"), "ColorTransfer": transfer,
            "RealFrameRate": _number(stream.get("frame_rate")), "AverageFrameRate": _number(stream.get("frame_rate")),
            "VideoRange": ("HDR" if transfer in HDR_TRANSFERS else "SDR") if video else "Unknown",
            "VideoRangeType": {"smpte2084": "HDR10", "arib-std-b67": "HLG"}.get(transfer, "SDR") if video else "Unknown",
            "AudioSpatialFormat": "None", "IsOriginal": False,  # non-null in the current spec
            "DeliveryMethod": "Embed" if kind == "Subtitle" else None,
            # Non-null in Jellyfin SDK 1.7 (Android TV 0.19 fails to load the item without them).
            "IsHearingImpaired": bool(stream.get("hearing_impaired")),
            "IsTextSubtitleStream": kind == "Subtitle" and codec in TEXT_SUBTITLE_CODECS,
            "SupportsExternalStream": False,
        }))
    base, hex_id = sidecar_base(probe), jid(item.id)
    for offset, sidecar in enumerate(sidecars(item)):
        fmt = sidecar.get("format") if isinstance(sidecar, dict) else None
        if fmt not in SUBTITLE_CODECS:
            continue  # its index stays reserved, so later sidecars never shift
        index = base + offset
        streams.append(compact({
            "Index": index, "Type": "Subtitle", "Codec": SUBTITLE_CODECS[fmt], "Language": iso639_2(sidecar.get("language")),
            "DisplayTitle": display_title("Subtitle", sidecar, SUBTITLE_CODECS[fmt], external=True),
            "IsDefault": bool(sidecar.get("default")), "IsForced": bool(sidecar.get("forced")),
            "IsHearingImpaired": bool(sidecar.get("hearing_impaired")), "IsExternal": True, "IsInterlaced": False,
            "VideoRange": "Unknown", "VideoRangeType": "Unknown", "AudioSpatialFormat": "None", "IsOriginal": False,
            "IsTextSubtitleStream": True, "SupportsExternalStream": True, "DeliveryMethod": "External",
            "DeliveryUrl": f"/Videos/{hex_id}/{hex_id}/Subtitles/{index}/0/Stream.{fmt}",
        }))
    return streams


def media_source(item: LibraryItem, artifact: MediaArtifact | None, probe: dict | None = None) -> dict:
    """One MediaSourceInfo per version: cached probe facts, and a library-relative Path (never the server mount path)."""
    probe = probe if probe is not None else ((artifact.probe if artifact else None) or {})
    streams = media_streams(probe, item)
    duration = _number(probe.get("duration")) or item.duration
    size = item.file_size or (artifact.size if artifact else None)
    audio = [s for s in streams if s["Type"] == "Audio"]
    subtitles = [s for s in streams if s["Type"] == "Subtitle"]
    fingerprint = str(probe.get("fingerprint") or (f"{artifact.size}:{artifact.mtime_ns}" if artifact else item.id))
    return compact({
        "Id": jid(item.id), "Name": version_label(item) or item.title, "Protocol": "File", "Type": "Default",
        "Container": (Path(artifact.relative_path).suffix.lstrip(".").lower() or None) if artifact else None,
        # Library-relative, never the server's mount path: Infuse decodes items by file name.
        "Path": f"/{artifact.relative_path.lstrip('/')}" if artifact else None,
        "Size": size, "Bitrate": int(size * 8 / duration) if size and duration else None,
        "RunTimeTicks": to_ticks(duration) if duration else None,
        "ETag": hashlib.sha256(fingerprint.encode()).hexdigest()[:32],
        "IsRemote": False, "UseMostCompatibleTranscodingProfile": False, "ReadAtNativeFramerate": False, "IgnoreDts": False, "IgnoreIndex": False, "GenPtsInput": False,
        "SupportsDirectPlay": True, "SupportsDirectStream": True,
        "SupportsTranscoding": False,  # 09 integration sets this and TranscodingUrl from decide()
        "TranscodingSubProtocol": "http", "HasSegments": False,  # Jellyfin's defaults; required by SDK 1.7
        "IsInfiniteStream": False, "RequiresOpening": False, "RequiresClosing": False, "RequiresLooping": False,
        "SupportsProbing": True, "MediaStreams": streams, "MediaAttachments": [], "Formats": [], "RequiredHttpHeaders": {},
        "DefaultAudioStreamIndex": next((s["Index"] for s in audio if s.get("IsDefault")), audio[0]["Index"] if audio else None),
        "DefaultSubtitleStreamIndex": next((s["Index"] for s in subtitles if s.get("IsForced") or s.get("IsDefault")), None),
    })


CHANNEL_KINDS = ("video", "recording")
# Managed videos with no Media title: yt-dlp downloads and live recordings.
CHANNEL_ITEM = (
    LibraryItem.title_id.is_(None), LibraryItem.extractor.is_not(None),
    LibraryItem.kind.in_(CHANNEL_KINDS), LibraryItem.status != "missing",
)
UPLOADER = func.coalesce(LibraryItem.uploader, "")
UPLOAD_DAY = func.coalesce(func.json_extract(LibraryItem.metadata_summary, "$.upload_date"), func.strftime("%Y%m%d", LibraryItem.created_at))
UPLOAD_YEAR = cast(func.substr(UPLOAD_DAY, 1, 4), Integer)


def channel_id(extractor: str, uploader: str, year: int | None = None) -> str:
    key = f"channel:{extractor}:{uploader}"
    return synthetic_id(key if year is None else f"{key}:{year}")


def channel_year(item: LibraryItem) -> int:
    day = str((item.metadata_summary or {}).get("upload_date") or "")
    return int(day[:4]) if day[:4].isdigit() else item.created_at.year


@dataclass(frozen=True)
class Channel:
    """A synthetic Channels Series (year None) or Season (one upload year) of one uploader."""

    id: str
    extractor: str
    uploader: str
    year: int | None
    count: int
    children: int
    newest_item_id: str
    newest_at: datetime

    @property
    def name(self) -> str:
        return str(self.year) if self.year is not None else (self.uploader or self.extractor)


def channel_index(db: Session, user: User | None) -> dict[str, Channel]:
    """Every visible Channels series and season by synthetic id, from one grouped query (``user=None``: every member's).

    SQLite fills the bare ``LibraryItem.id`` from the row holding ``max(created_at)``: each group's newest video.
    Rebuilt per request that needs it; cache per member if channel counts grow large.
    """
    rows = db.execute(
        select(LibraryItem.extractor, UPLOADER, UPLOAD_YEAR, func.count(), LibraryItem.id, func.max(LibraryItem.created_at))
        .where(*CHANNEL_ITEM, *([LibraryService.visible_predicate(user)] if user is not None else []))
        .group_by(LibraryItem.extractor, UPLOADER, UPLOAD_YEAR)
    ).all()
    index: dict[str, Channel] = {}
    for extractor, uploader, year, count, newest_id, newest_at in rows:
        season_id, series_id = channel_id(extractor, uploader, year), channel_id(extractor, uploader)
        index[season_id] = Channel(season_id, extractor, uploader, year, count, count, newest_id, newest_at)
        series = index.get(series_id)
        newer = series is None or newest_at > series.newest_at
        index[series_id] = Channel(
            series_id, extractor, uploader, None,
            count + (series.count if series else 0), 1 + (series.children if series else 0),
            newest_id if newer else series.newest_item_id, newest_at if newer else series.newest_at,
        )
    return index


def channel_items_query(user: User | None, channel: Channel | None = None):  # noqa: ANN201
    """Visible Channels videos (all, one uploader, or one uploader-year) in upload order (``user=None``: every member's)."""
    query = select(LibraryItem).options(defer(LibraryItem.metadata_json)).where(*CHANNEL_ITEM, *([LibraryService.visible_predicate(user)] if user is not None else []))
    if channel is not None:
        query = query.where(LibraryItem.extractor == channel.extractor, UPLOADER == channel.uploader)
        if channel.year is not None:
            query = query.where(UPLOAD_YEAR == channel.year)
    return query.order_by(UPLOAD_DAY, LibraryItem.created_at, LibraryItem.id)


class ItemsQuery(BaseModel):
    """/Items query parameters. Keys arrive lowercased by JellyfinPathMiddleware; lists accept "a,b" and repeats."""

    model_config = ConfigDict(extra="ignore")

    parentid: str | None = Field(None, max_length=64)
    includeitemtypes: list[str] = []
    excludeitemtypes: list[str] = []
    recursive: bool = False
    ids: list[str] = []
    searchterm: str | None = Field(None, max_length=200)
    sortby: list[str] = []
    sortorder: list[str] = []
    startindex: int = Field(0, ge=0)
    limit: int | None = Field(None, ge=0)
    filters: list[str] = []
    isplayed: bool | None = None
    isfavorite: bool | None = None
    anyprovideridequals: list[str] = []
    seriesid: str | None = Field(None, max_length=64)
    fields: list[str] = []
    # Shows/{id}/Episodes only:
    seasonid: str | None = Field(None, max_length=64)
    season: int | None = Field(None, ge=0)
    startitemid: str | None = Field(None, max_length=64)
    # Shows/NextUp only: kept on this model (not separate route params) so FastAPI's
    # single-query-model spreading still applies (it only spreads when there is exactly
    # one query field in the dependant; see fastapi.dependencies.utils.request_params_to_args).
    nextupdatecutoff: datetime | None = None
    enablerewatching: bool | None = None
    # Facet browsing: /Genres, /Studios, /Persons and the same filters on /Items. Names are pipe-separated, ids comma-separated.
    genres: list[str] = []
    genreids: list[str] = []
    studios: list[str] = []
    studioids: list[str] = []
    persons: list[str] = []
    personids: list[str] = []
    persontypes: list[str] = []
    years: list[str] = []
    namestartswith: str | None = Field(None, max_length=200)

    def pipes(self, name: str) -> list[str]:
        return [part.strip() for value in getattr(self, name) for part in value.split("|") if part.strip()]

    def csv(self, name: str) -> list[str]:
        return [part.strip() for value in getattr(self, name) for part in value.split(",") if part.strip()]

    def types(self, name: str = "includeitemtypes") -> set[str]:
        return {FROM_JF_TYPES[value.lower()] for value in self.csv(name) if value.lower() in FROM_JF_TYPES}

    def page_limit(self, default: int = MAX_PAGE) -> int:
        return min(default if self.limit is None else self.limit, MAX_PAGE)


# kind -> (Items name filter, Items id filter, BaseItemDto Type)
FACETS = {"genre": ("genres", "genreids", "Genre"), "studio": ("studios", "studioids", "Studio"), "person": ("persons", "personids", "Person")}


def _facet_refs(kind: str, title: MediaTitle) -> list[tuple[str, str, str]]:
    """(dashed id, name, credit type) for a title's genres, studios or people; ids match BaseItemDto Studios and People."""
    if kind == "person":
        return [(ref.get("person_id") or person_name_id(ref["name"]), ref["name"], ref.get("type") or "Actor") for ref in _credits(title)]
    meta = title.metadata_json if isinstance(title.metadata_json, dict) else {}
    values = meta.get(f"{kind}s")
    return [(synthetic_id(f"{kind}:{name}"), name, kind) for name in values if isinstance(name, str) and name] if isinstance(values, list) else []


@dataclass
class Entity:
    """What a client id names: a view, a Media title, a Library item, or a synthetic Channels folder."""

    id: str
    view: str | None = None
    title: MediaTitle | None = None
    item: LibraryItem | None = None
    channel: Channel | None = None


# A title view's Sections (ADR 0019): anime films stay in Movies, so Anime alone still lists Movies.
VIEW_SECTIONS = {"movies": {"movies", "anime"}, "tvshows": {"shows"}, "anime": {"anime"}}


def section_allows_view(db: Session, user: User, view: str) -> bool:
    """A member without the view's Section never sees the library at all: no empty tile, and its id is a 404."""
    access = member_access.for_user(db, user)
    return access is None or access.sections is None or view not in VIEW_SECTIONS or bool(VIEW_SECTIONS[view] & access.sections)


def resolve(db: Session, user: User, raw_id: object) -> Entity | None:
    """Visibility-first id resolution. None means 404: unknown, invalid and invisible look the same."""
    entity_id = parse_item_id(raw_id)
    if entity_id is None:
        return None
    if entity_id in VIEW_IDS:
        view = VIEW_IDS[entity_id]
        return Entity(entity_id, view=view) if section_allows_view(db, user, view) else None
    title = TitleService(db).get_visible(entity_id, user)
    if title is not None:
        return Entity(entity_id, title=title) if title.type in JF_TYPES else None  # album, artist: no music in Jellyfin (A5)
    item = LibraryService(db).get_item(entity_id, user)
    if item is not None and item.status != "missing":
        return Entity(entity_id, item=item)
    channel = channel_index(db, user).get(entity_id)
    return Entity(entity_id, channel=channel) if channel is not None else None


def playlist_dto(playlist: pl.Playlist, server_id: str) -> dict:
    return {
        "Id": jid(playlist.id), "ServerId": server_id, "Name": playlist.name, "Type": "Playlist",
        "IsFolder": True, "MediaType": "Video", "ChildCount": len(playlist.entries), "ImageTags": {},
        "BackdropImageTags": [], "LocationType": "FileSystem", "PlayAccess": "Full", "CanDelete": False, "CanDownload": False,
        "UserData": user_data_dto(playlist.id, TitleUserData()),
    }


class JellyfinMapper:
    """BaseItemDto builder for one request: one member, one server key, one field projection."""

    def __init__(self, db: Session, user: User, fields: Iterable[str] = ()):
        self.db, self.user = db, user
        self.titles = TitleService(db)
        pinned = db.get(AppSettings, 1)  # noqa: F841 - the identity map holds weak refs; this keeps the row for both reads below
        self.key = jellyfin_server_key(db)
        self.scope = member_access.art_scope(db, user)  # '' unless the member's library is limited
        self.server_id = jellyfin_server_id(db)
        self.sources = bool({value.lower() for value in fields} & {"mediasources", "mediastreams"})
        self.people = "people" in {value.lower() for value in fields}

    # ---- views and single entities ------------------------------------------------

    def view_dto(self, view: str) -> dict:
        name, collection = VIEWS[view]
        view_id = synthetic_id(f"view:{view}")
        return {
            "Id": jid(view_id), "ServerId": self.server_id, "Name": name, "SortName": name, "Type": "CollectionFolder",
            "CollectionType": collection, "IsFolder": True, "MediaType": "Unknown", "ImageTags": {}, "BackdropImageTags": [],
            "LocationType": "FileSystem", "PlayAccess": "Full", "CanDelete": False, "CanDownload": False, "UserData": user_data_dto(view_id, TitleUserData()),
        } | self.view_counts(view)

    def view_counts(self, view: str) -> dict:
        """ChildCount/RecursiveItemCount for title views: Infuse asks for them, and a view without them can read as empty."""
        if view not in ("movies", "tvshows", "anime"):
            return {}
        stmt = self.title_filter(Entity(synthetic_id(f"view:{view}"), view=view), ItemsQuery())
        count = self.db.scalar(select(func.count()).select_from(stmt.subquery())) if stmt is not None else 0
        return {"ChildCount": count, "RecursiveItemCount": count}

    def views(self) -> list[dict]:
        views = [view for view in ("movies", "tvshows") if section_allows_view(self.db, self.user, view)]
        if section_allows_view(self.db, self.user, "anime") and self.db.scalar(
            select(MediaTitle.id).where(*ANIME_SERIES_WHERE, TitleService.visible(self.user)).limit(1)
        ):
            views.append("anime")
        if self.db.scalar(select(MediaTitle.id).where(MediaTitle.type == "boxset", TitleService.visible(self.user)).limit(1)):
            views.append("boxsets")
        if self.db.scalar(select(LibraryItem.id).where(*CHANNEL_ITEM, LibraryService.visible_predicate(self.user)).limit(1)):
            views.append("channels")
        return [self.view_dto(view) for view in views]

    def entity_dto(self, entity: Entity) -> dict:
        if entity.view is not None:
            return self.view_dto(entity.view)
        if entity.title is not None:
            return self.title_dtos([entity.title], sources=True)[0]
        if entity.channel is not None:
            return self.channel_dtos([entity.channel])[0]
        return self.item_dtos([entity.item], sources=True)[0]

    # ---- titles -------------------------------------------------------------------

    def title_dtos(self, titles: list[MediaTitle], *, sources: bool | None = None, batch: TitleBatch | None = None) -> list[dict]:
        sources = self.sources if sources is None else sources
        batch = batch or self.titles.load(
            self.user, titles, with_artifacts=sources, with_metadata=sources, with_artwork=False,
        )
        dtos = [self.title_dto(title, batch, sources=sources) for title in titles]
        people = people_for_titles(self.db, titles) if (sources or self.people) else {}  # one query, only when projected
        for title, dto in zip(titles, dtos, strict=True):
            extra = title_metadata_fields(title)
            if dto.get("RunTimeTicks"):
                extra.pop("RunTimeTicks", None)  # probe runtime wins over TMDB's
            dto.update(extra)
            if people.get(title.id):
                dto["People"] = person_dtos(people[title.id], lambda target, kind, source: image_tag(self.key, target, kind, source))
        return dtos

    def title_dto(self, title: MediaTitle, batch: TitleBatch, *, sources: bool = False) -> dict:
        meta = title.metadata_json or {}
        season, series = batch.ancestors(title)
        folder = title.type in FOLDER_TYPES
        preferred = batch.preferred_version(title.id)
        runtime = batch.runtime(title)
        children, leaves, _unplayed = batch.folder_counts.get(title.id, (0, 0, 0))
        parent_id = title.parent_id if title.type in ("season", "episode") else synthetic_id(f"view:{view_of(title)}")
        series_backdrop = title_tag(self.key, series, "Backdrop", self.scope)
        backdrops = [t for n in range(5) if (t := title_tag(self.key, title, image_key("Backdrop", n), self.scope))]
        image_tags = {kind: tag for kind in ("Primary", "Logo", "Thumb", "Banner") if (tag := title_tag(self.key, title, kind, self.scope))}
        dto = compact({
            "Id": jid(title.id), "ServerId": self.server_id, "Name": title.name, "SortName": title.sort_name or title.name,
            # An episode with no season or series would reach clients as an Episode without SeriesId/SeasonId, which they dereference.
            "Type": "Video" if title.type == "episode" and not (season and series) else JF_TYPES[title.type], "IsFolder": folder, "MediaType": "Unknown" if folder else "Video", "ParentId": jid(parent_id),
            "SeriesId": jid(series.id) if series else None, "SeriesName": series.name if series else None,
            "SeasonId": jid(season.id) if season else None, "SeasonName": season.name if season else None,
            "IndexNumber": title.index_number, "IndexNumberEnd": title.index_number_end,
            "ParentIndexNumber": season.index_number if season else None,
            "Overview": meta.get("overview"), "ProductionYear": title.year, "PremiereDate": jf_day(meta.get("premiered")),
            "DateCreated": jf_date(title.arrived_at), "OfficialRating": meta.get("official_rating"),
            "CommunityRating": meta.get("community_rating"), "Genres": list(meta.get("genres") or []),
            "ProviderIds": dict(title.provider_ids or {}), "RunTimeTicks": to_ticks(runtime) if runtime else None,
            "ChildCount": children if folder else None, "RecursiveItemCount": leaves if folder else None,
            "ImageTags": image_tags, "BackdropImageTags": backdrops,
            "SeriesPrimaryImageTag": title_tag(self.key, series, "Primary", self.scope),
            "ParentBackdropItemId": jid(series.id) if series_backdrop else None,
            "ParentBackdropImageTags": [series_backdrop] if series_backdrop else None,
            "LocationType": "FileSystem", "PlayAccess": "Full", "CanDelete": False, "CanDownload": not folder,
            "UserData": user_data_dto(title.id, batch.user_data(title), runtime),
        })
        if sources:  # detail views: clients index these without a check
            dto |= {"People": [], "Taglines": [], "Tags": [], "Chapters": []}
        if sources and not folder:
            versions = sorted(batch.versions.get(title.id, []), key=lambda item: preferred is None or item.id != preferred.id)
            dto["MediaSources"] = [media_source(item, batch.artifacts.get(item.id)) for item in versions]
            dto["MediaStreams"] = dto["MediaSources"][0]["MediaStreams"] if dto["MediaSources"] else []
            if dto["MediaSources"] and dto["MediaSources"][0].get("Path"):
                dto["Path"] = dto["MediaSources"][0]["Path"]
        return dto

    # ---- Library items shown as their own entries --------------------------------------

    def item_dtos(self, items: list[LibraryItem], *, sources: bool | None = None) -> list[dict]:
        """Extras ("Video" + ExtraType), versions reached by their own id, and Channels videos."""
        sources = self.sources if sources is None else sources
        ids = [item.id for item in items]
        progress, favorites = self.titles.progress_for(self.user, ids), self.titles.favorites_for(self.user, ids)
        artifacts = self.titles.artifacts_for(ids)  # one query; imported extras only have the probe's runtime
        dtos = []
        for item in items:
            tag = item_tag(self.key, item, self.scope)
            artifact = artifacts.get(item.id)
            runtime = item.duration or _number(((artifact.probe if artifact else None) or {}).get("duration"))
            dto = compact({
                "Id": jid(item.id), "ServerId": self.server_id, "Name": item.title, "SortName": item.title,
                "Type": "Video", "IsFolder": False, "MediaType": "Video",
                "ParentId": jid(item.title_id) if item.kind != "track" else None,  # a track's album is not a Jellyfin item (A5)
                "ExtraType": EXTRA_TYPES.get(item.extra_type or ""), "DateCreated": jf_date(item.created_at),
                "PremiereDate": jf_day((item.metadata_summary or {}).get("upload_date")),
                "RunTimeTicks": to_ticks(runtime) if runtime else None,
                "ImageTags": {"Primary": tag}, "BackdropImageTags": [], "ProviderIds": {},
                "LocationType": "FileSystem", "PlayAccess": "Full", "CanDelete": False, "CanDownload": True,
                "UserData": user_data_dto(item.id, user_data_from(progress.get(item.id), favorite=item.id in favorites), runtime),
            })
            dto |= self.channel_fields(item)
            if sources:
                dto |= {"People": [], "Taglines": [], "Tags": [], "Chapters": []}
                dto["MediaSources"] = [media_source(item, artifact)]
                dto["MediaStreams"] = dto["MediaSources"][0]["MediaStreams"]
                if dto["MediaSources"][0].get("Path"):
                    dto["Path"] = dto["MediaSources"][0]["Path"]
            dtos.append(dto)
        return dtos

    # ---- /Items ------------------------------------------------------------------------

    def items(self, q: ItemsQuery) -> dict:
        """/Items: views at the root, titles below them (set-based page + batch), Channels."""
        start, limit = q.startindex, min(MAX_PAGE if q.limit is None else q.limit, MAX_PAGE)
        if q.csv("ids"):
            found = self.by_ids(q.csv("ids"))
            return query_result(found[start:start + limit], len(found), start)
        parent = resolve(self.db, self.user, q.parentid) if q.parentid else None
        if q.parentid and parent is None:
            raise LookupError("parent")
        if parent is None and not (q.recursive or q.searchterm):
            views = self.views()
            return query_result(views[start:start + limit], len(views), start)
        if parent is not None and (parent.item is not None or parent.channel is not None or parent.view == "channels"):
            return self.channel_items(parent, q, start, limit)
        stmt = self.title_filter(parent, q)
        if stmt is None:
            return query_result([], 0, start)
        rows = self.db.execute(stmt.add_columns(func.count().over()).order_by(*self.order(q, parent)).offset(start).limit(limit)).all()
        total = rows[0][1] if rows else self.db.scalar(select(func.count()).select_from(stmt.subquery()))
        return query_result(self.title_dtos([row[0] for row in rows]), total, start)

    def by_ids(self, raw_ids: list[str]) -> list[dict]:
        """Entries named by id, in request order: the titles and items the member may see."""
        wanted = [entity_id for entity_id in (parse_item_id(raw) for raw in raw_ids[:MAX_PAGE]) if entity_id]
        titles = list(self.db.scalars(
            select(MediaTitle).where(MediaTitle.id.in_(wanted), MediaTitle.type.in_(tuple(JF_TYPES)), TitleService.visible(self.user))
        ))
        dtos = dict(zip([title.id for title in titles], self.title_dtos(titles), strict=True))
        rest = [entity_id for entity_id in wanted if entity_id not in dtos]
        if rest:
            items = list(self.db.scalars(
                select(LibraryItem).options(defer(LibraryItem.metadata_json))
                .where(LibraryItem.id.in_(rest), LibraryItem.status != "missing", LibraryService.visible_predicate(self.user))
            ))
            dtos |= dict(zip([item.id for item in items], self.item_dtos(items), strict=True))
        missing = [entity_id for entity_id in wanted if entity_id not in dtos]
        if missing:
            index = channel_index(self.db, self.user)
            found = [index[entity_id] for entity_id in missing if entity_id in index]
            dtos |= dict(zip([channel.id for channel in found], self.channel_dtos(found), strict=True))
        for entity_id in (entity_id for entity_id in wanted if entity_id not in dtos):
            if (playlist := self.playlist(entity_id)) is not None:
                dtos[entity_id] = playlist
        return [dtos[entity_id] for entity_id in wanted if entity_id in dtos]

    def playlist(self, entity_id: str) -> dict | None:
        """The member's Watchlist or a collection they may see, as a Playlist folder; None otherwise."""
        try:
            return playlist_dto(pl.get_playlist(self.db, self.user, entity_id), self.server_id)
        except pl.PlaylistNotFound:
            return None

    def title_filter(self, parent: Entity | None, q: ItemsQuery):  # noqa: ANN201
        """The visible titles a query names, or None when it can match nothing."""
        wanted = q.types()
        if q.csv("includeitemtypes") and not wanted:
            return None  # only types Lumina never serves (MusicAlbum, Audio, …): nothing, not the default set
        stmt = select(MediaTitle).where(TitleService.visible(self.user))
        season = aliased(MediaTitle)
        if parent is None:
            wanted = wanted or {"movie", "series", "boxset"}
        elif parent.view is not None:
            wanted = (wanted or {VIEW_DEFAULT[parent.view]}) & VIEW_TYPES[parent.view]
            if parent.view in VIEW_CATEGORY:  # Shows and Anime split by category; seasons and episodes carry their series'
                stmt = stmt.where(MediaTitle.category == VIEW_CATEGORY[parent.view])
        elif parent.title.type == "series":
            if q.recursive:
                seasons = select(season.id).where(season.parent_id == parent.title.id)
                stmt = stmt.where(or_(MediaTitle.parent_id == parent.title.id, MediaTitle.parent_id.in_(seasons)))
                wanted = wanted or {"season", "episode"}
            else:
                stmt, wanted = stmt.where(MediaTitle.parent_id == parent.title.id), wanted or {"season"}
        elif parent.title.type == "season":
            stmt, wanted = stmt.where(MediaTitle.parent_id == parent.title.id), wanted or {"episode"}
        elif parent.title.type == "boxset":
            stmt, wanted = stmt.where(MediaTitle.boxset_id == parent.title.id), (wanted or {"movie"}) & {"movie"}
        else:
            return None  # a movie or episode has no children
        wanted -= q.types("excludeitemtypes")
        if not wanted:
            return None
        stmt = stmt.where(MediaTitle.type.in_(sorted(wanted)))
        if q.seriesid:
            series_id = parse_item_id(q.seriesid)
            if series_id is None:
                return None  # a garbage SeriesId matches nothing
            stmt = stmt.where(or_(MediaTitle.parent_id == series_id, MediaTitle.parent_id.in_(select(season.id).where(season.parent_id == series_id))))
        if q.searchterm:
            term = q.searchterm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(MediaTitle.name.ilike(f"%{term}%", escape="\\"))
        providers = [self._provider_clause(value) for value in q.csv("anyprovideridequals")]
        if providers:
            stmt = stmt.where(or_(*providers))
        for kind in FACETS:
            if (clause := self._facet_clause(kind, q)) is not None:
                stmt = stmt.where(clause)
        if years := [int(y) for y in q.csv("years") if y.isdigit()]:
            stmt = stmt.where(MediaTitle.year.in_(years))
        filters = {value.lower() for value in q.csv("filters")}
        if q.isfavorite or "isfavorite" in filters:
            stmt = stmt.where(MediaTitle.id.in_(select(MemberFavorite.target_id).where(MemberFavorite.user_id == self.user.id)))
        completed = latest_progress(self.user, PlaybackProgress.completed)
        if q.isplayed is True or "isplayed" in filters:
            stmt = stmt.where(MediaTitle.type.in_(LEAF_TYPES), completed.is_(True))
        if q.isplayed is False or "isunplayed" in filters:
            # Played filters judge movies/episodes; folders pass IsUnplayed unfiltered.
            stmt = stmt.where(or_(completed.is_(None), completed.is_(False)))
        if "isresumable" in filters:
            stmt = stmt.where(completed.is_(False), latest_progress(self.user, PlaybackProgress.position_seconds) > 0)
        return stmt

    # ---- Genres, Studios, Persons ------------------------------------------------------

    def _facet_rows(self, kind: str, stmt) -> dict[str, tuple[str, set[str]]]:  # noqa: ANN001
        """id -> (name, credited types) over the titles ``stmt`` selects (already visibility-scoped)."""
        found: dict[str, tuple[str, set[str]]] = {}
        for (meta,) in self.db.execute(stmt.with_only_columns(MediaTitle.metadata_json)):
            for ref_id, name, credit in _facet_refs(kind, MediaTitle(metadata_json=meta)):  # transient: only metadata_json is read
                found.setdefault(ref_id, (name, set()))[1].add(credit)
        return found

    def _facet_clause(self, kind: str, q: ItemsQuery):  # noqa: ANN202
        """The /Items filter for Genres=/GenreIds=, Studios=/StudioIds= or Persons=/PersonIds= (any match), or None."""
        names_field, ids_field, _type = FACETS[kind]
        names, ids = {n.casefold() for n in q.pipes(names_field)}, q.csv(ids_field)
        if not names and not ids:
            return None
        if ids:  # an id names a facet only among titles this member can see; an unknown one matches nothing
            known = self._facet_rows(kind, select(MediaTitle).where(TitleService.visible(self.user), MediaTitle.type.in_(tuple(VIEW_OF_TYPE))))
            wanted = {i for i in map(parse_item_id, ids) if i}
            names |= {name.casefold() for ref_id, (name, _) in known.items() if ref_id in wanted}
        if not names:
            return false()
        paths = ("$.people", "$.crew") if kind == "person" else (f"$.{kind}s",)
        clauses = []
        for path in paths:
            refs = func.json_each(MediaTitle.metadata_json, path).table_valued("value")
            value = func.json_extract(refs.c.value, "$.name") if kind == "person" else refs.c.value
            clauses.append(select(true()).select_from(refs).where(func.lower(value).in_(sorted(names))).correlate(MediaTitle).exists())
        # People match by name, so two people sharing a name both match; add person_id matching if that bites.
        return or_(*clauses)

    def _facet_stmt(self, q: ItemsQuery):  # noqa: ANN202
        """The visible titles under ParentId/IncludeItemTypes, or None when there are none; LookupError for an unknown parent."""
        parent = resolve(self.db, self.user, q.parentid) if q.parentid else None
        if q.parentid and parent is None:
            raise LookupError("parent")
        if parent is not None and parent.title is None and parent.view is None:
            return None  # Channels hold no titles
        return self.title_filter(parent, q.model_copy(update={"searchterm": None}))

    def facets(self, kind: str, q: ItemsQuery) -> dict:
        """/Genres, /Studios, /Persons: the facets of the titles this member can see."""
        start, limit = q.startindex, q.page_limit()
        stmt = self._facet_stmt(q)
        found = self._facet_rows(kind, stmt) if stmt is not None else {}
        prefix, term = (q.namestartswith or "").casefold(), (q.searchterm or "").casefold()
        wanted_types = {t.casefold() for t in q.csv("persontypes")} if kind == "person" else set()
        rows = sorted(
            ((name, ref_id) for ref_id, (name, types) in found.items()
             if name.casefold().startswith(prefix) and term in name.casefold() and (not wanted_types or wanted_types & {t.casefold() for t in types})),
            key=lambda row: (row[0].casefold(), row[1]),
        )
        items = [
            {"Name": name, "ServerId": self.server_id, "Id": jid(ref_id), "Type": FACETS[kind][2], "ImageTags": {}, "BackdropImageTags": [],
             "UserData": user_data_dto(ref_id, TitleUserData())}
            for name, ref_id in rows[start:start + limit]
        ]
        return query_result(items, len(rows), start)

    def query_filters(self, q: ItemsQuery, *, legacy: bool) -> dict:
        """/Items/Filters (names) and /Items/Filters2 (genres as name/id pairs): what the visible titles offer to filter by."""
        stmt = self._facet_stmt(q)
        genres: dict[str, str] = {}
        tags, ratings, years = set(), set(), set()
        for meta, year in (self.db.execute(stmt.with_only_columns(MediaTitle.metadata_json, MediaTitle.year)) if stmt is not None else ()):
            meta = meta if isinstance(meta, dict) else {}
            genres.update({name: ref_id for ref_id, name, _ in _facet_refs("genre", MediaTitle(metadata_json=meta))})
            tags.update(t for t in meta.get("tags") or [] if isinstance(t, str) and t)
            if isinstance(meta.get("official_rating"), str) and meta["official_rating"]:
                ratings.add(meta["official_rating"])
            if year:
                years.add(year)
        names = sorted(genres, key=str.casefold)
        return {
            "Genres": names if legacy else [{"Name": n, "Id": jid(genres[n])} for n in names],
            "Tags": sorted(tags, key=str.casefold), "OfficialRatings": sorted(ratings), "Years": sorted(years),
        }

    @staticmethod
    def _provider_clause(value: str):  # noqa: ANN205
        provider, _, provider_id = value.partition(".")
        name = {"tmdb": "Tmdb", "imdb": "Imdb", "tvdb": "Tvdb"}.get(provider.lower())
        if name is None or not provider_id:
            return false()
        return func.json_extract(MediaTitle.provider_ids, f"$.{name}") == provider_id

    def order(self, q: ItemsQuery, parent: Entity | None) -> list:
        parent_title = aliased(MediaTitle)
        parent_index = select(parent_title.index_number).where(parent_title.id == MediaTitle.parent_id).scalar_subquery()
        by_index = [func.coalesce(parent_index, UNNUMBERED), func.coalesce(MediaTitle.index_number, UNNUMBERED)]
        columns = {
            "sortname": [SORT_NAME], "name": [SORT_NAME], "datecreated": [ADDED],
            "premieredate": [func.json_extract(MediaTitle.metadata_json, "$.premiered")],
            "productionyear": [MediaTitle.year],
            "communityrating": [func.json_extract(MediaTitle.metadata_json, "$.community_rating")],
            "dateplayed": [latest_progress(self.user, PlaybackProgress.last_watched_at)],
            "indexnumber": by_index, "parentindexnumber": by_index, "airedepisodeorder": by_index,
            "random": [func.random()],
        }
        show_children = parent is not None and parent.title is not None and parent.title.type in ("series", "season")
        default = ["indexnumber"] if show_children or (q.types() and q.types() <= {"season", "episode"}) else ["sortname"]
        keys = [column for name in ([value.lower() for value in q.csv("sortby")] or default) for column in columns.get(name, [])]
        descending = [value.lower() for value in q.csv("sortorder")][:1] == ["descending"]
        return [key.desc() if descending else key for key in keys or [SORT_NAME]] + [MediaTitle.id]

    # ---- Channels ------------------------------------------------------------------------

    def channel_fields(self, item: LibraryItem) -> dict:
        """Episode fields for a Channels video; {} for anything that is not one."""
        if item.title_id is not None or not item.extractor or item.kind not in CHANNEL_KINDS:
            return {}
        uploader, year = item.uploader or "", channel_year(item)
        series_id, season_id = channel_id(item.extractor, uploader), channel_id(item.extractor, uploader, year)
        return {
            "Type": "Episode", "SeriesId": jid(series_id), "SeriesName": uploader or item.extractor,
            "SeasonId": jid(season_id), "SeasonName": str(year), "ParentId": jid(season_id),
            # Signed for the SeriesId with this video as its source: token-less series art validates (review #20a).
            "ParentIndexNumber": year, "SeriesPrimaryImageTag": image_tag(self.key, series_id, "Primary", item.id, self.scope),
        }

    def channel_dtos(self, channels: list[Channel]) -> list[dict]:
        favorites = self.titles.favorites_for(self.user, [channel.id for channel in channels])
        dtos = []
        for channel in channels:
            tag = image_tag(self.key, channel.id, "Primary", channel.newest_item_id, self.scope)
            dto = {
                "Id": jid(channel.id), "ServerId": self.server_id, "Name": channel.name, "SortName": channel.name,
                "IsFolder": True, "MediaType": "Unknown", "CanDelete": False, "CanDownload": False, "ChildCount": channel.children, "RecursiveItemCount": channel.count,
                "DateCreated": jf_date(channel.newest_at), "ImageTags": {"Primary": tag}, "BackdropImageTags": [],
                "ProviderIds": {}, "LocationType": "FileSystem", "PlayAccess": "Full",
                "UserData": user_data_dto(channel.id, TitleUserData(is_favorite=channel.id in favorites)),
            }
            if channel.year is None:
                dto |= {"Type": "Series", "ParentId": jid(synthetic_id("view:channels"))}
            else:
                series_id = channel_id(channel.extractor, channel.uploader)
                dto |= {"Type": "Season", "IndexNumber": channel.year, "SeriesId": jid(series_id),
                        "SeriesName": channel.uploader or channel.extractor, "ParentId": jid(series_id),
                        "SeriesPrimaryImageTag": image_tag(self.key, series_id, "Primary", channel.newest_item_id, self.scope)}
            dtos.append(dto)
        return dtos

    def channel_items(self, parent: Entity, q: ItemsQuery, start: int, limit: int) -> dict:
        """/Items below the Channels view or a Channels folder: folders from the index, videos from SQL."""
        channel, wanted = parent.channel, q.types()
        if parent.item is not None or (wanted and not wanted & {"series", "season", "episode"}):
            return query_result([], 0, start)  # a video has no children; Channels hold no movies, boxsets, ...
        if "episode" in wanted or (not wanted and (q.recursive or (channel is not None and channel.year is not None))):
            query = channel_items_query(self.user, channel)
            rows = self.db.execute(query.add_columns(func.count().over()).offset(start).limit(limit)).all()
            total = rows[0][1] if rows else self.db.scalar(select(func.count()).select_from(query.subquery()))
            return query_result(self.item_dtos([row[0] for row in rows]), total, start)
        index = channel_index(self.db, self.user).values()
        if channel is None:
            series_level = not wanted or "series" in wanted
            folders = [c for c in index if (c.year is None) == series_level]
        else:
            folders = [c for c in index if c.year is not None and (c.extractor, c.uploader) == (channel.extractor, channel.uploader)]
        folders.sort(key=lambda c: (c.year or 0, c.name.casefold()))
        return query_result(self.channel_dtos(folders[start:start + limit]), len(folders), start)

    # ---- shows, Next Up, Latest, Resume, search ------------------------------------------

    def seasons(self, entity: Entity) -> dict:
        if entity.channel is not None and entity.channel.year is None:
            seasons = sorted(
                (c for c in channel_index(self.db, self.user).values()
                 if c.year is not None and (c.extractor, c.uploader) == (entity.channel.extractor, entity.channel.uploader)),
                key=lambda c: c.year,
            )
            return query_result(self.channel_dtos(seasons), len(seasons))
        if entity.title is None or entity.title.type != "series":
            raise LookupError("series")
        seasons = self.titles.children(self.user, entity.title)
        return query_result(self.title_dtos(seasons), len(seasons))

    def episodes(self, entity: Entity, q: ItemsQuery, start: int, limit: int) -> dict:
        if entity.channel is not None:
            channel = entity.channel
            season = channel_index(self.db, self.user).get(parse_item_id(q.seasonid) or "") if q.seasonid else None
            if season is not None:
                channel = season
            elif q.season is not None:
                channel = replace(channel, year=q.season)
            entries: list = list(self.db.scalars(channel_items_query(self.user, channel)))
            build = self.item_dtos
        elif entity.title is not None and entity.title.type == "series":
            entries = self.titles.episodes(self.user, entity.title, q.season)
            if q.seasonid:
                entries = [episode for episode in entries if episode.parent_id == parse_item_id(q.seasonid)]
            build = self.title_dtos
        else:
            raise LookupError("series")
        first = parse_item_id(q.startitemid) if q.startitemid else None
        ids = [entry.id for entry in entries]
        if first in ids:
            entries = entries[ids.index(first):]
        return query_result(build(entries[start:start + limit]), len(entries), start)

    def next_up(self, series_id: str | None, start: int, limit: int, *, pick=None) -> dict:
        target = None
        if series_id:
            entity = resolve(self.db, self.user, series_id)
            if entity is None or entity.title is None or entity.title.type != "series":
                return query_result([], 0, start)
            target = entity.title.id
        picks = self.titles.next_up_titles(self.user, series_id=target, pick=pick)
        return query_result(self.title_dtos(picks[start:start + limit]), len(picks), start)

    def latest(self, parent_id: str | None, types: set[str], limit: int) -> list[dict]:
        """Movies by date added; shows grouped to their Series by newest episode file (Shows and Anime split by
        category, both without a view); Channels videos by date."""
        parent = resolve(self.db, self.user, parent_id) if parent_id else None
        if parent_id and parent is None:
            raise LookupError("parent")
        view = parent.view if parent is not None else None
        if parent is not None and (parent.channel is not None or view == "channels"):
            query = channel_items_query(self.user, parent.channel).order_by(None).order_by(LibraryItem.created_at.desc(), LibraryItem.id).limit(limit)
            return self.item_dtos(list(self.db.scalars(query)))
        if parent is not None and view is None:
            return []  # Latest below one series/boxset; add when a client asks for it
        dated: list[tuple[MediaTitle, datetime]] = []
        if view in (None, "movies") and (not types or "movie" in types):
            movies = self.db.scalars(
                select(MediaTitle).where(MediaTitle.type == "movie", TitleService.visible(self.user))
                .order_by(ADDED.desc(), MediaTitle.id).limit(limit)
            )
            dated += [(movie, movie.arrived_at) for movie in movies]
        if view in (None, "tvshows", "anime") and (not types or types & {"series", "episode"}):
            season, episode = aliased(MediaTitle), aliased(MediaTitle)
            newest = func.max(func.coalesce(episode.added_at, episode.created_at))  # when its newest episode arrived
            narrowed = [episode.category == VIEW_CATEGORY[view]] if view else []  # home "Latest" spans Shows and Anime
            rows = self.db.execute(
                select(season.parent_id, newest).select_from(LibraryItem)
                .join(episode, episode.id == LibraryItem.title_id).join(season, season.id == episode.parent_id)
                .where(episode.type == "episode", *narrowed, LibraryItem.extra_type.is_(None), LibraryItem.status != "missing",
                       LibraryService.visible_predicate(self.user))
                .group_by(season.parent_id).order_by(newest.desc()).limit(limit)
            ).all()
            series = {title.id: title for title in self.db.scalars(select(MediaTitle).where(MediaTitle.id.in_([row[0] for row in rows])))}
            dated += [(series[series_id], at) for series_id, at in rows if series_id in series]
        dated.sort(key=lambda pair: pair[1], reverse=True)
        return self.title_dtos([title for title, _at in dated[:limit]])

    def resume(self, parent_id: str | None, types: set[str], start: int, limit: int) -> dict:
        """Continue Watching restricted to movies, episodes and Channels videos, newest first."""
        parent = resolve(self.db, self.user, parent_id) if parent_id else None
        if parent_id and parent is None:
            raise LookupError("parent")
        playback = PlaybackProgressService(self.db)  # keeps the JOIN-loaded items alive for the identity-map gets below
        items = [self.db.get(LibraryItem, row.item_id) for row in playback.list_continue_watching(self.user)]
        title_ids = {item.title_id for item in items if item is not None and item.title_id and not item.extra_type}
        titles = {
            title.id: title
            for title in self.db.scalars(select(MediaTitle).where(MediaTitle.id.in_(title_ids), MediaTitle.type.in_(LEAF_TYPES)))
        } if title_ids else {}
        batch = self.titles.load(self.user, list(titles.values()), with_artifacts=self.sources, with_metadata=self.sources)
        entries: list[MediaTitle | LibraryItem] = []
        for item in items:
            if item is None or item.extra_type:
                continue
            entry = titles.get(item.title_id) if item.title_id else (item if self.channel_fields(item) else None)
            if entry is not None and entry not in entries and self._under(entry, parent, batch) and self._typed(entry, types):
                entries.append(entry)
        page = entries[start:start + limit]
        page_titles = [entry for entry in page if isinstance(entry, MediaTitle)]
        page_items = [entry for entry in page if isinstance(entry, LibraryItem)]
        dtos = dict(zip([title.id for title in page_titles], self.title_dtos(page_titles, batch=batch), strict=True))
        dtos |= dict(zip([item.id for item in page_items], self.item_dtos(page_items), strict=True))
        return query_result([dtos[entry.id] for entry in page], len(entries), start)

    @staticmethod
    def _under(entry: MediaTitle | LibraryItem, parent: Entity | None, batch: TitleBatch) -> bool:
        if parent is None:
            return True
        if isinstance(entry, LibraryItem):
            if parent.view is not None:
                return parent.view == "channels"
            channel = parent.channel
            return channel is not None and (entry.extractor, entry.uploader or "") == (channel.extractor, channel.uploader) \
                and (channel.year is None or channel_year(entry) == channel.year)
        if parent.view is not None:  # an episode's view follows its category
            return parent.view == ("movies" if entry.type == "movie" else "anime" if entry.category == "anime" else "tvshows")
        season, series = batch.ancestors(entry)
        return parent.title is not None and parent.title.id in {entry.id, season.id if season else None, series.id if series else None}

    @staticmethod
    def _typed(entry: MediaTitle | LibraryItem, types: set[str]) -> bool:
        return not types or ("episode" if isinstance(entry, LibraryItem) else entry.type) in types

    def user_data_for(self, entity: Entity) -> dict:
        if entity.title is not None:
            return self.title_dto(entity.title, self.titles.load(self.user, [entity.title]))["UserData"]
        if entity.item is not None:
            return self.item_dtos([entity.item], sources=False)[0]["UserData"]
        if entity.channel is not None:
            return self.channel_dtos([entity.channel])[0]["UserData"]
        raise LookupError("a view has no user data")


def playable_versions(db: Session, user: User, entity: Entity | None) -> list[LibraryItem]:
    """The files a client may play for an id: a movie/episode's visible versions (preferred first), or the item itself."""
    if entity is None:
        return []
    if entity.item is not None:
        return [entity.item]
    if entity.title is not None and entity.title.type in LEAF_TYPES:
        batch = TitleService(db).load(user, [entity.title])
        preferred = batch.preferred_version(entity.title.id)
        return sorted(batch.versions.get(entity.title.id, []), key=lambda item: item.id != preferred.id)
    return []


def pick_version(db: Session, user: User, entity: Entity | None, media_source_id: str | None = None) -> LibraryItem | None:
    """MediaSourceId selects a version of the named item; without one, the preferred version."""
    wanted = parse_item_id(media_source_id) if media_source_id else None
    if (
        media_source_id and entity is not None and entity.title is not None
        and entity.title.type in LEAF_TYPES and wanted != entity.id
    ):
        # A named version needs no preferred-version ranking or title-page data.
        # Keep both the parent check and the live item visibility check.
        return db.scalar(select(LibraryItem).options(defer(LibraryItem.metadata_json)).where(
            LibraryItem.id == wanted, LibraryItem.title_id == entity.title.id,
            LibraryItem.extra_type.is_(None), LibraryItem.status != "missing",
            LibraryService.visible_predicate(user),
        ))
    versions = playable_versions(db, user, entity)
    own = entity is not None and wanted == entity.id  # Jellyfin's primary source id is the item's own id: "the default"
    if media_source_id and not own:
        return next((version for version in versions if version.id == wanted), None)
    return versions[0] if versions else None


def stream_file(db: Session, user: User, raw_id: str, media_source_id: str | None) -> tuple[str, Path] | None:
    """Resolve a visible version and its registered file; the item's own id means its preferred version."""
    entity_id, wanted = parse_item_id(raw_id), parse_item_id(media_source_id) if media_source_id else None
    if media_source_id and wanted != entity_id:
        if entity_id is None or wanted is None:
            return None
        parent = select(MediaTitle.id).where(
            MediaTitle.id == entity_id, MediaTitle.type.in_(LEAF_TYPES), TitleService.visible(user),
        )
        row = db.execute(select(
            LibraryItem.id, MediaArtifact.lifecycle, MediaArtifact.relative_path, StorageRoot.enabled, StorageRoot.path,
        ).outerjoin(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
            .outerjoin(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .outerjoin(StorageRoot, StorageRoot.id == MediaArtifact.root_id).where(
            LibraryItem.id == wanted, LibraryItem.title_id.in_(parent), LibraryItem.extra_type.is_(None),
            LibraryItem.status != "missing", LibraryService.visible_predicate(user),
        )).one_or_none()
        if row is None:
            return None
        version_id, lifecycle, relative_path, enabled, root_path = row
        if lifecycle != "available" or not enabled:
            raise FileNotFoundError("Library item media is not available.")
        return version_id, artifact_file(root_path, relative_path)
    version = pick_version(db, user, resolve(db, user, raw_id), media_source_id)
    return (version.id, LibraryService(db).resolve_media_path(version)) if version is not None else None


@dataclass(frozen=True)
class CredentialStreamFile:
    """One explicit-token stream lookup: gate/auth rows plus an optional selected file."""

    enabled: bool
    token: DeviceToken | None
    user: User | None
    access: MemberAccess | None
    selected: tuple[str, str | None, str | None, bool | None, str | None] | None


def _credential_user(credential, role: str) -> User:  # noqa: ANN001
    """A transient user whose identity comes from the one-row credential CTE."""
    return User(
        id=select(credential.c.user_id).scalar_subquery(),
        role=role,
    )


def _credential_visible_item(credential):  # noqa: ANN001, ANN202
    """The normal live member predicate, driven by credential CTE scalars.

    A transient viewer retains the uncorrelated MemberAccess subqueries; the
    admin branch deliberately has the ordinary unrestricted/ownerless rule.
    """
    viewer = _credential_user(credential, "viewer")
    admin = _credential_user(credential, "admin")
    return or_(
        and_(credential.c.user_role == "admin", LibraryService.visible_predicate(admin)),
        and_(credential.c.user_role != "admin", LibraryService.visible_predicate(viewer)),
    )


def _credential_visible_title(credential):  # noqa: ANN001, ANN202
    viewer = _credential_user(credential, "viewer")
    admin = _credential_user(credential, "admin")
    return or_(
        and_(credential.c.user_role == "admin", TitleService.visible(admin)),
        and_(credential.c.user_role != "admin", TitleService.visible(viewer)),
    )


_STATIC_STREAM_TYPE_SETS = frozenset((
    LEAF_TYPES,
    member_access.MUSIC_TYPES,
    ("movie",),
    member_access.TV_TYPES,
    ("movie", *member_access.TV_TYPES),
))


def _fixed_stream_type_lists(statement):  # noqa: ANN001, ANN202
    """Replace only the cached statement's immutable type-list expansions.

    The live member-access predicates deliberately use scalar subqueries.  Their
    fixed media-type categories otherwise become SQLAlchemy expanding parameters
    on every range request, even though this cached template never changes.
    """
    def replace(node):  # noqa: ANN001, ANN202
        if not (
            isinstance(node, BinaryExpression)
            and node.operator in (operators.in_op, operators.not_in_op)
            and isinstance(node.right, BindParameter)
            and node.right.expanding
            and isinstance(node.right.value, (list, tuple))
            and tuple(node.right.value) in _STATIC_STREAM_TYPE_SETS
        ):
            return None
        values = tuple(literal(value) for value in node.right.value)
        return node.left.in_(values) if node.operator is operators.in_op else node.left.not_in(values)

    return visitors.replacement_traverse(statement, {}, replace)


@lru_cache(maxsize=1)
def _selected_stream_with_credential_statement():  # noqa: ANN202
    """The immutable SQL shape for the explicit selected-stream fast path.

    Values remain bind parameters: this only avoids rebuilding the large visibility
    expression for every range request.
    """
    token_digest = bindparam("token_digest")
    cutoff = bindparam("cutoff")
    entity_id = bindparam("entity_id")
    wanted = bindparam("wanted")
    credential = (
        select(
            AppSettings.jellyfin_enabled.label("enabled"),
            DeviceToken.id.label("token_id"),
            User.id.label("user_id"),
            User.role.label("user_role"),
            User.is_active.label("user_active"),
            DeviceToken.last_seen_at.label("last_seen_at"),
        )
        .select_from(AppSettings)
        .outerjoin(DeviceToken, and_(DeviceToken.token_digest == token_digest, DeviceToken.kind == "jellyfin"))
        .outerjoin(User, User.id == DeviceToken.user_id)
        .where(AppSettings.id == 1)
        .cte("stream_credential")
    )
    caller_is_live = and_(
        credential.c.enabled.is_(True),
        credential.c.user_id.is_not(None),
        credential.c.user_role.is_not(None),
        credential.c.user_active.is_(True),
        credential.c.last_seen_at > cutoff,
    )
    parent = select(MediaTitle.id).where(
        MediaTitle.id == entity_id,
        MediaTitle.type.in_(LEAF_TYPES),
        _credential_visible_title(credential),
    )
    selected = (
        select(
            LibraryItem.id.label("version_id"),
            MediaArtifact.lifecycle.label("lifecycle"),
            MediaArtifact.relative_path.label("relative_path"),
            StorageRoot.enabled.label("root_enabled"),
            StorageRoot.path.label("root_path"),
        )
        .select_from(LibraryItem)
        .join(credential, true())
        .outerjoin(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
        .outerjoin(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
        .outerjoin(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
        .where(
            caller_is_live,
            LibraryItem.id == wanted,
            LibraryItem.title_id.in_(parent),
            LibraryItem.extra_type.is_(None),
            LibraryItem.status != "missing",
            _credential_visible_item(credential),
        )
        .cte("selected_stream_file")
    )
    statement = (
        select(
            credential.c.enabled,
            DeviceToken,
            User,
            MemberAccess,
            selected.c.version_id,
            selected.c.lifecycle,
            selected.c.relative_path,
            selected.c.root_enabled,
            selected.c.root_path,
        )
        .select_from(credential)
        .outerjoin(DeviceToken, DeviceToken.id == credential.c.token_id)
        .outerjoin(User, User.id == credential.c.user_id)
        .outerjoin(MemberAccess, MemberAccess.user_id == credential.c.user_id)
        .outerjoin(selected, true())
    )
    return _fixed_stream_type_lists(statement)


def selected_stream_with_credential(
    db: Session, raw_id: str, media_source_id: str, *, token_digest: str, cutoff: datetime,
) -> CredentialStreamFile | None:
    """Load the enabled gate, current credential, and a distinct selected file in one statement.

    This does not validate or cache credentials.  The caller validates the returned
    token/member immediately, so idle, revoked, orphaned, and inactive credentials
    retain their existing lifecycle behavior before a path can be used.
    """
    entity_id, wanted = parse_item_id(raw_id), parse_item_id(media_source_id)
    if entity_id is None or wanted is None or wanted == entity_id:
        raise ValueError("This resolver is only for a distinct valid MediaSourceId.")

    row = db.execute(_selected_stream_with_credential_statement(), {
        "token_digest": token_digest,
        "cutoff": cutoff,
        "entity_id": entity_id,
        "wanted": wanted,
    }).one_or_none()
    if row is None:
        return None
    enabled, token, user, access, *file_row = row
    selected_row = tuple(file_row) if file_row[0] is not None else None
    return CredentialStreamFile(enabled=enabled, token=token, user=user, access=access, selected=selected_row)


def credential_stream_file(result: CredentialStreamFile) -> tuple[str, Path] | None:
    """Turn a selected-file row into a safely registered path after the credential is validated."""
    if result.selected is None:
        return None
    version_id, lifecycle, relative_path, enabled, root_path = result.selected
    if lifecycle != "available" or not enabled or relative_path is None or root_path is None:
        raise FileNotFoundError("Library item media is not available.")
    return version_id, artifact_file(root_path, relative_path)


def sidecar_file(db: Session, item: LibraryItem, index: int) -> tuple[Path, str] | None:
    """The stored sidecar behind external stream ``index``: resolved from the scanner's list, never a client path."""
    service = MediaArtifactService(db)
    found = service.artifact_for(item.id)
    entries = sidecars(item)
    offset = index - sidecar_base((found[0].probe or {}) if found else {})
    entry = entries[offset] if found and 0 <= offset < len(entries) else None
    filename, fmt = (entry.get("filename"), entry.get("format")) if isinstance(entry, dict) else (None, None)
    if not isinstance(filename, str) or filename in ("", ".", "..") or Path(filename).name != filename or fmt not in SUBTITLE_CODECS:
        return None
    try:
        media, root = service.locate(item)
        return artifact_file(root, str(media.relative_to(root).parent / filename)), fmt
    except (FileNotFoundError, ValueError):
        return None


def has_live_file(db: Session, title_id: str) -> bool:
    """A non-missing file is linked at or below the title (own, children, grandchildren, boxset members)."""
    below = or_(
        MediaTitle.id == title_id, MediaTitle.parent_id == title_id, MediaTitle.boxset_id == title_id,
        MediaTitle.parent_id.in_(select(MediaTitle.id).where(MediaTitle.parent_id == title_id)),
    )
    live = select(LibraryItem.id).where(LibraryItem.title_id.in_(select(MediaTitle.id).where(below)), LibraryItem.status != "missing")
    return db.scalar(live.limit(1)) is not None


def tagged_entity(db: Session, raw_id: str, image_type: str, tag: str | None) -> tuple[Entity, str | None]:
    """Token-less art: the id's signed tag must match, and a live file must still back it.

    Returns the entity and the member whose visibility loads a Library item's art. Raises LookupError.
    A Channels folder's tag names the newest video its member saw; any live video of the folder may match.
    A restricted member's tag carries their art scope: it verifies only while their limits are unchanged and they still
    see the title or video (member_access.scope_sees). Shared (unscoped) tags were only ever issued to members who see
    the whole library.
    """
    key, entity_id = jellyfin_server_key(db), parse_item_id(raw_id) or ""
    scope = (tag or "")[32:]
    title = db.get(MediaTitle, entity_id)
    if title is not None and title.type not in JF_TYPES:
        raise LookupError("image")  # album, artist: no music in Jellyfin (A5)
    if title is not None:
        expected, entity, member = title_tag(key, title, image_type, scope), Entity(title.id, title=title), None
        live = has_live_file(db, title.id)
    elif image_type != "Primary" or tag is None:
        raise LookupError("image")
    elif (item := db.get(LibraryItem, entity_id)) is not None:
        expected, entity, member = item_tag(key, item, scope), Entity(item.id, item=item), item.user_id
        live = item.status != "missing"
    elif (channel := channel_index(db, None).get(entity_id)) is not None:  # neither a title nor an item: a Channels folder
        # One HMAC per video of the folder, newest first; index tags by item if channels grow huge.
        videos = db.scalars(channel_items_query(None, channel).order_by(None).order_by(LibraryItem.created_at.desc()))
        item = next((v for v in videos if hmac.compare_digest(image_tag(key, channel.id, "Primary", v.id, scope), tag)), None)
        if item is None:
            raise LookupError("image")
        expected, entity, member, live = tag, Entity(item.id, item=item), item.user_id, True
    else:
        raise LookupError("image")
    if not live or expected is None or tag is None or not hmac.compare_digest(expected, tag):
        raise LookupError("image")
    if scope and not member_access.scope_sees(db, scope, entity.id, partial(_sees, db, entity)):
        raise LookupError("image")
    return entity, member


def _sees(db: Session, entity: Entity, member: User) -> bool:
    if entity.title is not None:
        return TitleService(db).get_visible(entity.title.id, member) is not None
    return db.scalar(select(LibraryItem.id).where(LibraryItem.id == entity.id, LibraryService.visible_predicate(member))) is not None


def load_image(
    db: Session, caller: User | None, raw_id: str, image_type: str, tag: str | None, artwork: ArtworkService,
    *, size: Mapping[str, str] | None = None, accept: str = "", head: bool = False,
) -> renditions.Served:
    """Art for an id: with a valid token, or token-less with the signed tag it was issued. LookupError = 404.

    A title image with size parameters gets its rendition after the same authorisation. This route
    never 503s (Infuse has no retry): past budget, it serves the original uncached instead of raising Preparing.
    Everything else is the original, as before.
    """
    if caller is None:
        entity, member = tagged_entity(db, raw_id, image_type, tag)
    else:
        entity, member = resolve(db, caller, raw_id), caller.id
    if entity is None:
        raise LookupError("image")
    try:
        if entity.title is not None:
            served, preparing = None, False
            if size:
                try:
                    served = renditions.sized(db, entity.title, image_type, size, accept, artwork, head=head)
                except renditions.Preparing:
                    preparing = True
            if served is not None:
                return served
            return renditions.Served(*title_image_bytes(db, entity.title, image_type, artwork), cacheable=not preparing)
        item_id = entity.item.id if entity.item is not None else (entity.channel.newest_item_id if entity.channel is not None else None)
        if item_id is None or member is None or image_type != "Primary":
            raise LookupError("image")
        resolved = artwork.load_library_artwork(member, item_id)
        return renditions.Served(resolved.content_type, resolved.content)
    except (FileNotFoundError, ArtworkError) as exc:
        raise LookupError("image") from exc


MAX_TICKS = 10**15  # ~3 years; a bound on client-reported positions


def set_played(db: Session, user: User, entity: Entity, played: bool) -> None:
    """Played = completed at position 0; unplayed = progress cleared; folders fan out. Caller owns the transaction."""
    if entity.title is not None:
        TitleService(db).set_watched(user, entity.title, played)
        return
    if entity.item is not None:
        items = [entity.item]
    elif entity.channel is not None:
        items = list(db.scalars(channel_items_query(user, entity.channel)))
    else:
        raise LookupError("a view has no played state")
    playback = PlaybackProgressService(db)
    for item in items:
        if played:
            playback.update(item.id, PlaybackProgressUpdateRequest(position_seconds=0, completed=True), user)
        else:
            playback.clear(item.id, user)
