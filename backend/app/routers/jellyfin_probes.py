"""Jellyfin client probes: correctly shaped answers for endpoints clients call that Lumina has no data behind.

Included by ``app.routers.jellyfin.register`` before the main router, so these fixed paths win over its
``/items/{item_id}`` captures and its catch-all. Every route needs a Jellyfin token (legacy ``/users/{uid}/…`` twins rely
on ``jellyfin_user``'s uid check) and answers only from what the caller may see. A list is empty, never an error body,
because clients such as Roku read an error object as a result.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import TitleUserData
from app.models import DeviceToken, MediaTitle, User
from app.routers.jellyfin_auth import JELLYFIN_SERVER_NAME, PREFIX, session_info
from app.security import client_ip
from app.services import jellyfin as jf
from app.services.connected_apps import jellyfin_server_id, jellyfin_user, require_jellyfin_enabled
from app.services.media_titles import jellyfin_id, synthetic_id
from app.services.titles import TitleService

router = APIRouter(prefix=PREFIX, dependencies=[Depends(require_jellyfin_enabled)])
Caller = Annotated[User, Depends(jellyfin_user)]
Db = Annotated[Session, Depends(get_db, scope="function")]
MAX_CLIENT_LOG_BYTES = 1024 * 1024
MAX_ANCESTORS = 6
ROOT_ID = synthetic_id("root")


def split_csv(values: list[str]) -> list[str]:
    """A list-valued query key sent repeated (?fields=A&fields=B), comma-joined, or both."""
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def entity_or_404(db: Session, user: User, raw_id: str) -> jf.Entity:
    entity = jf.resolve(db, user, raw_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return entity


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"  # 7 fractional digits, like every Jellyfin timestamp


# ---- empty results ---------------------------------------------------------------------------------------------

@router.get("/videos/{item_id}/additionalparts")  # also for an unknown id: Roku asks before it knows the item exists
@router.get("/items/{item_id}/intros")
@router.get("/items/{item_id}/instantmix")
@router.get("/users/{uid}/items/{item_id}/instantmix")
@router.get("/users/{uid}/items/{item_id}/intros")
@router.get("/artists")
@router.get("/artists/albumartists")
@router.get("/shows/upcoming")
@router.get("/trailers")
@router.get("/channels")
@router.get("/livetv/channels")
@router.get("/livetv/programs")
@router.get("/livetv/programs/recommended")
def empty_result(_user: Caller) -> dict:
    return jf.query_result([], 0)


@router.get("/movies/recommendations")
def no_recommendations(_user: Caller) -> list:
    return []


@router.get("/livetv/info")
def live_tv_info(_user: Caller) -> dict:
    return {"Services": [], "IsEnabled": False, "EnabledUsers": []}


def theme_result(owner: str) -> dict:
    return jf.query_result([], 0) | {"OwnerId": owner}


@router.get("/items/{item_id}/themesongs")
@router.get("/items/{item_id}/themevideos")
def theme_media(item_id: str, user: Caller, db: Db) -> dict:
    return theme_result(jellyfin_id(entity_or_404(db, user, item_id).id))


@router.get("/items/{item_id}/thememedia")
def all_theme_media(item_id: str, user: Caller, db: Db) -> dict:
    owner = jellyfin_id(entity_or_404(db, user, item_id).id)
    return {"ThemeVideosResult": theme_result(owner), "ThemeSongsResult": theme_result(owner), "SoundtrackSongsResult": theme_result(owner)}


# ---- the caller's own library -----------------------------------------------------------------------------------

def plain_dto(mapper: jf.JellyfinMapper, entity: jf.Entity) -> dict:
    """The entity's DTO without MediaSources (no probe, no artifact lookups)."""
    if entity.title is not None:
        return mapper.title_dtos([entity.title], sources=False)[0]
    if entity.item is not None:
        return mapper.item_dtos([entity.item], sources=False)[0]
    return mapper.entity_dto(entity)


@router.get("/items/{item_id}/ancestors")
def ancestors(item_id: str, user: Caller, db: Db) -> list[dict]:
    """Parent first up to the library view; each parent is resolved for the caller, so a hidden one ends the chain."""
    mapper = jf.JellyfinMapper(db, user)
    dto, chain = plain_dto(mapper, entity_or_404(db, user, item_id)), list[dict]()
    while len(chain) < MAX_ANCESTORS and (parent := dto.get("ParentId")) and (entity := jf.resolve(db, user, parent)) is not None:
        dto = plain_dto(mapper, entity)
        chain.append(dto)
    return chain


@router.get("/items/{item_id}/images")
def image_infos(item_id: str, user: Caller, db: Db) -> list[dict]:
    """ImageInfo for the art the item has: the same tags its DTO carries. Dimensions are not stored, so they are 0."""
    dto = plain_dto(jf.JellyfinMapper(db, user), entity_or_404(db, user, item_id))
    unknown = {"Height": 0, "Width": 0, "Size": 0}
    return [
        *({"ImageType": kind, "ImageTag": tag} | unknown for kind, tag in (dto.get("ImageTags") or {}).items()),
        *({"ImageType": "Backdrop", "ImageIndex": index, "ImageTag": tag} | unknown for index, tag in enumerate(dto.get("BackdropImageTags") or [])),
    ]


@router.get("/items/counts")
def item_counts(user: Caller, db: Db) -> dict:
    counts = {kind: count for kind, count in db.execute(
        select(MediaTitle.type, func.count()).where(MediaTitle.type.in_(("movie", "series", "episode", "boxset")), TitleService.visible(user))
        .group_by(MediaTitle.type)
    )}
    return {
        "MovieCount": counts.get("movie", 0), "SeriesCount": counts.get("series", 0), "EpisodeCount": counts.get("episode", 0),
        "BoxSetCount": counts.get("boxset", 0), "ItemCount": sum(counts.values()),
        "ArtistCount": 0, "ProgramCount": 0, "TrailerCount": 0, "SongCount": 0, "AlbumCount": 0, "MusicVideoCount": 0, "BookCount": 0,
    }


@router.get("/items/root")
def items_root(user: Caller, db: Db) -> dict:
    return {
        "Id": jellyfin_id(ROOT_ID), "ServerId": jellyfin_server_id(db), "Name": "Media Folders", "Type": "AggregateFolder", "IsFolder": True,
        "ImageTags": {}, "BackdropImageTags": [], "LocationType": "FileSystem", "PlayAccess": "Full",
        "UserData": jf.user_data_dto(ROOT_ID, TitleUserData()),
    }


@router.get("/library/mediafolders")
def media_folders(user: Caller, db: Db) -> dict:
    views = jf.JellyfinMapper(db, user).views()
    return jf.query_result(views, len(views))


# ---- sessions, system and client reports --------------------------------------------------------------------------

@router.get("/sessions")
def own_sessions(request: Request, user: Caller, db: Db, deviceid: str | None = Query(None, max_length=255)) -> list[dict]:
    """Only the caller's own Connected app: another member's session (or device) is never listed."""
    record = db.get(DeviceToken, request.state.connected_app_id)
    if record is None or (deviceid is not None and deviceid != record.device_id):
        return []
    return [session_info(record, user, jellyfin_server_id(db))]


@router.get("/system/endpoint")
def system_endpoint(request: Request, _user: Caller) -> dict:
    try:
        address = ip_address(client_ip(request) or "")
    except ValueError:
        return {"IsLocal": False, "IsInNetwork": False}
    return {"IsLocal": address.is_loopback, "IsInNetwork": address.is_loopback or address.is_private}


@router.get("/system/configuration/encoding")
def encoding_options(_user: Caller) -> dict:
    """EncodingOptions: Kodi reads EnableSubtitleExtraction before every play. No paths, no hardware claims."""
    return {
        "EnableSubtitleExtraction": True, "EnableThrottling": False, "HardwareAccelerationType": "none", "EncodingThreadCount": -1,
        "TranscodingTempPath": "", "EnableHardwareEncoding": False, "AllowHevcEncoding": False, "AllowAv1Encoding": False,
    }


@router.get("/system/configuration")
def server_configuration(_user: Caller) -> dict:
    return {"ServerName": JELLYFIN_SERVER_NAME, "IsStartupWizardCompleted": True, "EnableMetrics": False, "QuickConnectAvailable": False}


@router.get("/getutctime")
def utc_time(_user: Caller) -> dict:
    received = datetime.now(UTC)
    return {"RequestReceptionTime": stamp(received), "ResponseTransmissionTime": stamp(datetime.now(UTC))}


@router.post("/quickconnect/initiate")
def quick_connect_initiate() -> None:
    raise HTTPException(status_code=401, detail="Quick Connect is not active")  # Jellyfin's answer while it is off


@router.post("/devices/options", status_code=204)
def accept_device_options(_user: Caller) -> Response:
    return Response(status_code=204)  # a device's name is whatever its app said at sign-in


@router.post("/clientlog/document")
async def client_log(request: Request, _user: Caller) -> dict:
    """Accept and discard a client's crash log (it can hold anything); the cap is on bytes read, not on the header."""
    if request.headers.get("content-length", "0").isdigit() and int(request.headers["content-length"]) > MAX_CLIENT_LOG_BYTES:
        raise HTTPException(status_code=413, detail="Log too large")
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_CLIENT_LOG_BYTES:
            raise HTTPException(status_code=413, detail="Log too large")
    return {"FileName": f"{uuid.uuid4().hex}.log"}


@router.post("/users/configuration", status_code=204)
@router.post("/users/{uid}/configuration", status_code=204)
def accept_user_configuration(_user: Caller) -> Response:
    return Response(status_code=204)  # preferences are Lumina's own settings, not set through this API
