"""Map stored Lumina media to Jellyfin response DTOs."""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.media_schemas import TitlePerson, TitleUserData
from app.models import LibraryItem, MediaArtifact, MediaTitle
from app.services.chapters import normalize_chapters
from app.services.media_titles import jellyfin_id, person_name_id, synthetic_id, to_ticks
from app.services.transcripts import to_iso639_2 as iso639_2
from app.services.subtitle_tracks import sidecars
from app.services.titles import HDR_TRANSFERS, title_image_source, version_label


VIEW_OF_TYPE = {"movie": "movies", "series": "tvshows", "boxset": "boxsets"}


EXTRA_TYPES = {
    "trailer": "Trailer", "featurette": "Featurette", "behindthescenes": "BehindTheScenes", "deletedscene": "DeletedScene",
    "interview": "Interview", "scene": "Scene", "short": "Short", "clip": "Clip", "other": "Unknown",
}
IMAGE_TYPES = {name.lower(): name for name in ("Primary", "Backdrop", "Logo", "Thumb", "Banner")}
STREAM_TYPES = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle"}
SUBTITLE_CODECS = {"srt": "subrip", "ass": "ass", "ssa": "ssa", "vtt": "webvtt"}
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}


@lru_cache(maxsize=4096)  # pure UUID conversion; never stores authorization or DTOs
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


def chapter_dtos(probe: object) -> list[dict]:
    """Safe cached ffprobe chapters in the standard Jellyfin detail projection."""
    if not isinstance(probe, Mapping):
        return []
    chapters = normalize_chapters(
        provider_chapters=probe.get("chapters"),
        description=None,
        duration=probe.get("duration"),
    )["chapters"]
    return [
        {"Name": chapter["title"], "StartPositionTicks": to_ticks(chapter["start_time"])}
        for chapter in chapters
    ]


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
    # Lumina extensions preserve source identity; CriticRating remains the legacy generic score.
    for key, name, maximum in (("imdb_rating", "LuminaIMDbRating", 10),
                               ("rotten_tomatoes_rating", "LuminaRottenTomatoesRating", 100),
                               ("metacritic_rating", "LuminaMetacriticRating", 100)):
        value = meta.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= maximum:
            out[name] = value
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
        "Id": jid(item.id), "Name": version_label(item) or item.title, "Protocol": "File", "Type": "Default", "VideoType": "VideoFile",
        "Container": (Path(artifact.relative_path).suffix.lstrip(".").lower() or None) if artifact else None,
        # Library-relative, never the server's mount path: Infuse decodes items by file name.
        "Path": f"/{artifact.relative_path.lstrip('/')}" if artifact else None,
        "Size": size, "Bitrate": int(size * 8 / duration) if size and duration else None,
        "RunTimeTicks": to_ticks(duration) if duration else None,
        "ETag": hashlib.sha256(fingerprint.encode()).hexdigest()[:32],
        "IsRemote": False, "UseMostCompatibleTranscodingProfile": False, "ReadAtNativeFramerate": False, "IgnoreDts": False, "IgnoreIndex": False, "GenPtsInput": False,
        "SupportsDirectPlay": True, "SupportsDirectStream": False,
        "SupportsTranscoding": False,  # 09 integration sets this and TranscodingUrl from decide()
        "TranscodingSubProtocol": "http", "HasSegments": False,  # Jellyfin's defaults; required by SDK 1.7
        "IsInfiniteStream": False, "RequiresOpening": False, "RequiresClosing": False, "RequiresLooping": False,
        "SupportsProbing": True, "MediaStreams": streams, "MediaAttachments": [], "Formats": [], "RequiredHttpHeaders": {},
        "DefaultAudioStreamIndex": next((s["Index"] for s in audio if s.get("IsDefault")), audio[0]["Index"] if audio else None),
        "DefaultSubtitleStreamIndex": next((s["Index"] for s in subtitles if s.get("IsForced") or s.get("IsDefault")), None),
    })
