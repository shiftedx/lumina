"""Jellyfin-compatible API at the server root. Clean-room from the OpenAPI and observed clients.

Routes are mounted under the private prefix /jellyfin, which clients cannot reach. ``JellyfinPathMiddleware``
maps a root path whose first segment is a Jellyfin route segment (``/Items`` -> ``/jellyfin/items``, case and
slash variants too) onto those lowercase routes and lowercases query keys, so apps connect with host:port alone.
``app.routers.jellyfin_auth`` owns sign-in and the user routes and is registered first; ``jellyfin_probes`` answers the
endpoints clients call that Lumina has nothing behind; this router owns everything else and ends in an empty-bodied 404 catch-all. Every route is gated by
``require_jellyfin_enabled``; legacy ``/users/{uid}/…`` routes rely on ``jellyfin_user``'s uid check.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import time
import uuid
from collections import Counter
from contextlib import suppress
from dataclasses import replace
from typing import Annotated
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.models import Person, PlaybackProgress, User
from app.db import session_scope
from app.persistence import read_regular_file, write_transaction
from app.security import resolve_device_token
from app.services.rate_limit import enforce_rate_limit, grant_address
from app.schemas import PlaybackProgressUpdateRequest
from app.routers import jellyfin_integration, jellyfin_probes
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller, playback_body, playlist_listing, transcript_subtitle
from app.routers.jellyfin_probes import Caller, Db, entity_or_404, split_csv
from app.services import activity, cast_photos, screen_time
from app.services import jellyfin as jf
from app.services import renditions
from app.services import tmdb
from app.services.playlists import has_playlists
from app.services.artwork import ArtworkError, ArtworkService
from app.services.jellyfin_discovery import dismisses_resume, naive_utc, next_up_filtered
from app.services.jellyfin_playback import PLAY_SESSION_ID, annotate_media_sources, append_transcript_streams, playback_request
from app.services.connected_apps import (
    IMAGE_GRANT_KEY, image_grants, jellyfin_server_key, jellyfin_stream_user, jellyfin_user, parse_client_auth, require_jellyfin_enabled,
    stream_grants,
)
from app.services.library import LibraryService
from app.services.local_playback_sessions import sessions
from app.services.media_artifacts import MediaArtifactService
from app.services.media_probe import MediaProbeService
from app.services.media_response import MediaFileResponse
from app.services.media_titles import from_ticks, parse_item_id
from app.services.playback import PlaybackProgressService
from app.services.title_metadata import load_person_image, person_visible
from app.services.titles import EXTRA_BACKDROPS, TitleService, image_key, title_image_source
from app.services.transcripts import TranscriptError, parse_caption, render

logger = logging.getLogger("lumina.jellyfin")
PRIVATE_PREFIX = "/jellyfin"  # where the routes are mounted; clients use the root form only
_RETIRED = re.compile(r"^/+(?:jellyfin|emby)(?:/|$)", re.IGNORECASE)  # /jellyfin/… and /emby/… from clients: 404
_ID_SEGMENT = re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f-]{27}|\d+)(?=\.|$)")
# Top-level paths the SPA answers (frontend/src/app/routes.ts parseRoute); test_jellyfin_api keeps them in sync.
SPA_SEGMENTS = frozenset({"admin", "channel", "downloads", "explore", "library", "live", "music", "settings", "streaming", "subscriptions", "title", "watch"})
_CREDENTIAL_HEADERS = ("x-emby-authorization", "x-emby-token", "x-mediabrowser-token")
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE"]
SUBTITLE_MEDIA_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/x-ssa", "ssa": "text/x-ssa"}
MAX_SUBTITLE_BYTES = 10 * 1024 * 1024
MEDIA_UNAVAILABLE = "Media file is not available."  # fixed text: an OSError can name a server path
SOCKET_KEEP_ALIVE_SECONDS = 60  # what ForceKeepAlive asks of the client; clients send KeepAlive at half of it
SOCKET_IDLE_SECONDS = 150
SOCKET_LIFETIME_SECONDS = 3600  # a revoked token ends the socket within the hour; clients reconnect
MAX_SOCKET_MESSAGE = 4096
MAX_SOCKETS = 256
MAX_SOCKETS_PER_TOKEN = 4
open_sockets: Counter[str] = Counter()  # token -> open sockets; one event loop, so no lock


def normalize_jellyfin_path(path: str, root_segments: frozenset[str], *, any_segment: bool = False) -> str | None:
    """/Items//ABC/ -> /jellyfin/items/abc when "items" is in ``root_segments`` (or ``any_segment`` and not /api);
    None when not a Jellyfin path."""
    lowered = re.sub(r"/{2,}", "/", path.lower())
    first = lowered.split("/")[1]
    if first not in root_segments and not (any_segment and first not in ("", "api")):
        return None
    return (PRIVATE_PREFIX + lowered).rstrip("/")


def jellyfin_segments(routes: list[BaseRoute]) -> frozenset[str]:
    """First segments of the registered /jellyfin/<segment>/… routes: what the root form may answer."""
    firsts = (getattr(route, "path", "").split("/")[2] for route in routes if getattr(route, "path", "").startswith(PRIVATE_PREFIX + "/"))
    return frozenset(segment for segment in firsts if segment and not segment.startswith("{"))


def has_jellyfin_credential(scope: Scope) -> bool:
    """A MediaBrowser/Emby Authorization, an X-Emby-*/X-MediaBrowser-Token header or an api_key/ApiKey query key."""
    headers = Headers(scope=scope)
    return (
        headers.get("authorization", "").lower().startswith(("mediabrowser ", "emby "))
        or any(name in headers for name in _CREDENTIAL_HEADERS)
        or any(key.lower() in ("api_key", "apikey") for key, _ in parse_qsl(scope.get("query_string", b"").decode("latin-1")))
    )


def is_browser_navigation(scope: Scope) -> bool:
    """A page load, not an API client: Sec-Fetch-Mode navigate, or HTML first in Accept with no Jellyfin credential."""
    headers = Headers(scope=scope)
    if headers.get("sec-fetch-mode") == "navigate":
        return True
    if headers.get("accept", "").split(",")[0].split(";")[0].strip() != "text/html":
        return False
    return not has_jellyfin_credential(scope)


def masked_path(path: str) -> str:
    """/jellyfin/a/b/<anything>... -> /jellyfin/a/b/{id}...: only the first two segments after the private prefix
    may reach a log, and only when they are not ids (a later segment can be a title or user name)."""
    # A name in the second segment (e.g. /persons/<name>) still reaches the log; log segment counts if that matters.
    parts = path.split("/")  # ["", "jellyfin", first, second, ...]
    return "/".join(p if i < 4 and not _ID_SEGMENT.match(p) else "{id}" for i, p in enumerate(parts))


def lower_query_keys(query_string: bytes) -> bytes:
    """Lowercase query keys (clients send ParentId, parentId, parentid); values are untouched."""
    pairs = parse_qsl(query_string.decode("latin-1"), keep_blank_values=True)
    return urlencode([(key.lower(), value) for key, value in pairs]).encode("ascii")


class JellyfinPathMiddleware:
    """Pure ASGI: normalize the Jellyfin surface before routing; optionally trace it (names only)."""

    def __init__(self, app: ASGIApp, root_segments: frozenset[str] = frozenset()):
        self.app = app
        self.root_segments = root_segments

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if _RETIRED.match(scope["path"]):  # the mount is private and there are no prefixed aliases
            await Response(status_code=404)(scope, receive, send)
            return
        # A credentialed call to an endpoint we do not serve (/Genres) reaches the gated JSON catch-all, never the SPA shell.
        normalized = normalize_jellyfin_path(scope["path"], self.root_segments, any_segment=has_jellyfin_credential(scope))
        # A root path the SPA also answers (/library/…) stays the SPA's for a browser page load.
        if normalized and normalized.split("/")[2] in SPA_SEGMENTS and is_browser_navigation(scope):
            normalized = None
        if normalized is None:
            await self.app(scope, receive, send)
            return
        # In place, so outer middleware (FailedRequestLogMiddleware) sees the matched route template.
        scope["path"], scope["raw_path"] = normalized, quote(normalized).encode("ascii")
        scope["query_string"] = lower_query_keys(scope.get("query_string", b""))
        try:
            await self.app(scope, receive, send)
        finally:
            if settings.jellyfin_trace:
                route = getattr(scope.get("route"), "path", "[unmatched]")
                names = sorted({key for key, _ in parse_qsl(scope["query_string"].decode("ascii"), keep_blank_values=True)})
                logger.info("jellyfin.trace %s %s %s", scope["method"], route, ",".join(names))


router = APIRouter(prefix=PRIVATE_PREFIX, dependencies=[Depends(require_jellyfin_enabled)])


# ==== A. Fixed paths: keep ABOVE every "/items/{item_id}…" and "/users/{uid}/items/{item_id}…" route. ====

@router.get("/items/latest")
@router.get("/users/{uid}/items/latest")
def latest_items(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> list[dict]:
    asked = {value.lower() for value in query.csv("includeitemtypes")}
    if asked and not query.types() and "video" not in asked:
        return []  # only types Lumina does not serve (Audio, MusicAlbum, ...): empty, not everything
    try:
        return jf.JellyfinMapper(db, user, query.csv("fields")).latest(query.parentid, query.types(), min(query.page_limit(20), 200))
    except LookupError:
        return []  # an unknown or hidden parent is an empty list: a client reads an error object as items


@router.get("/useritems/resume")
@router.get("/users/{uid}/items/resume")
def resume_items(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    try:
        return jf.JellyfinMapper(db, user, query.csv("fields")).resume(query.parentid, query.types(), query.startindex, query.page_limit())
    except LookupError:
        return jf.query_result([], 0, query.startindex)


@router.get("/shows/nextup")
def next_up(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    # enableRewatching is tri-state on purpose: Jellyfin's own default is false, but Lumina's
    # rewatch rule is the default when a client does not send it.
    def pick(ordered, candidate):  # noqa: ANN001, ANN202
        return next_up_filtered(ordered, candidate, cutoff=naive_utc(query.nextupdatecutoff), rewatching=query.enablerewatching is not False)

    return jf.JellyfinMapper(db, user, query.csv("fields")).next_up(query.seriesid, query.startindex, query.page_limit(), pick=pick)


# ==== B. Entity routes (Tasks 6–10 add here). ====

@router.get("/userviews")
@router.get("/users/{uid}/views")
def user_views(user: Caller, db: Db) -> dict:
    mapper = jf.JellyfinMapper(db, user)
    views = mapper.views()
    if has_playlists(db, user):  # A non-empty queue or any visible collection
        views.append(mapper.view_dto("playlists"))
    return jf.query_result(views, len(views))


@router.get("/userviews/groupingoptions")
def grouping_options(user: Caller, db: Db) -> list[dict]:
    """SpecialViewOptionDto[]: Infuse asks right after sign-in and aborts adding the server on a 404."""
    return [{"Name": view["Name"], "Id": view["Id"]} for view in jf.JellyfinMapper(db, user).views()]


@router.get("/library/virtualfolders")
def virtual_folders(user: Caller, db: Db) -> list[dict]:
    """VirtualFolderInfo[]: Infuse asks right after GroupingOptions. The caller's own views; Locations stay empty (no server paths)."""
    return [
        {"Name": view["Name"], "ItemId": view["Id"], "CollectionType": view.get("CollectionType"), "Locations": [], "LibraryOptions": {}}
        for view in jf.JellyfinMapper(db, user).views()
    ]


def facet_page(kind: str, user: User, db: Session, query: jf.ItemsQuery) -> dict:
    try:
        return jf.JellyfinMapper(db, user).facets(kind, query)
    except LookupError:
        return jf.query_result([], 0, query.startindex)


@router.get("/genres")
def genres(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    return facet_page("genre", user, db, query)


@router.get("/studios")
def studios(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    return facet_page("studio", user, db, query)


@router.get("/persons")
def persons(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    return facet_page("person", user, db, query)


# Not indexed for Jellyfin clients: valid empty shapes so a client's first sync does not 404.
@router.get("/musicgenres")
@router.get("/years")
def empty_facet(_user: Caller) -> dict:
    return jf.query_result([], 0)


def filters_for(user: User, db: Session, query: jf.ItemsQuery, *, legacy: bool) -> dict:
    try:
        return jf.JellyfinMapper(db, user).query_filters(query, legacy=legacy)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Item not found") from exc


@router.get("/items/filters")
def item_filters(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    return filters_for(user, db, query, legacy=True)


@router.get("/items/filters2")
def item_filters2(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    return filters_for(user, db, query, legacy=False)


@router.get("/items")
@router.get("/users/{uid}/items")
def list_items(user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    if query.searchterm:
        return jellyfin_integration.search_items(db, user, query)  # the ranked search; the LIKE filter never runs for a term
    if query.parentid and (listing := playlist_listing(db, user, query)) is not None:
        return listing
    try:
        return jf.JellyfinMapper(db, user, query.csv("fields")).items(query)
    except LookupError:
        return jf.query_result([], 0, query.startindex)


@router.get("/items/{item_id}")
@router.get("/users/{uid}/items/{item_id}")
def get_item(item_id: str, user: Caller, db: Db) -> dict:
    mapper, entity = jf.JellyfinMapper(db, user), jf.resolve(db, user, item_id)
    if entity is not None:
        return mapper.entity_dto(entity)
    playlist = mapper.playlist(entity_id) if (entity_id := parse_item_id(item_id)) else None  # playlists open by id too
    if playlist is None:
        playlist = person_dto(mapper, entity_id)
    if playlist is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return playlist


def person_dto(mapper: jf.JellyfinMapper, person_id: str | None) -> dict | None:
    """A person the caller's visible titles credit (the ids People[] and /Persons emit); None otherwise."""
    wanted = jf.jid(person_id)
    if wanted is None:
        return None
    found = mapper.facets("person", jf.ItemsQuery(personids=[wanted], limit=jf.MAX_PAGE))
    return next((dto for dto in found["Items"] if dto["Id"] == wanted), None)


@router.get("/shows/{series_id}/seasons")
def show_seasons(series_id: str, user: Caller, db: Db, fields: list[str] = Query([])) -> dict:
    entity = jf.resolve(db, user, series_id)
    try:
        return jf.JellyfinMapper(db, user, split_csv(fields)).seasons(entity) if entity is not None else jf.query_result([], 0)
    except LookupError:
        return jf.query_result([], 0)


@router.get("/shows/{series_id}/episodes")
def show_episodes(series_id: str, request: Request, user: Caller, db: Db, query: Annotated[jf.ItemsQuery, Query()]) -> dict:
    entity = jf.resolve(db, user, series_id)
    try:
        if entity is None:
            raise LookupError("series")
        mapper = jf.JellyfinMapper(db, user, query.csv("fields"))
        if adjacent := request.query_params.get("adjacentto"):  # keys arrive lower-cased; read here to keep the query model alone
            return around_episode(mapper.episodes(entity, query, 0, jf.MAX_PAGE), parse_item_id(adjacent))
        return mapper.episodes(entity, query, query.startindex, query.page_limit())
    except LookupError:
        return jf.query_result([], 0, query.startindex)



def _person_photo(request: Request, db: Session, photo, kind: str, tag: str | None, caller: User | None) -> Response:  # noqa: ANN001
    """An NFO person's photo: with the signed tag from People[].PrimaryImageTag, or a device token
    when a title the caller can see credits them. Sized like title art (120/240); never a 503 (Infuse has no retry)."""
    source = title_image_source(photo, "Primary")
    url = cast_photos.photo_url(photo.id, source) if source else None
    signed = url is not None and tag is not None and hmac.compare_digest(tag, jf.image_tag(jellyfin_server_key(db), photo.id, "Primary", url))
    if kind != "Primary" or url is None or not (signed or (caller is not None and person_visible(db, photo.id, caller))):
        raise HTTPException(status_code=404, detail="Image not found")
    size = {key: value for key, value in request.query_params.items() if key in renditions.SIZE_PARAMS} or {"maxwidth": "240"}
    artwork = request.app.state.artwork
    try:
        try:
            served = renditions.sized(db, photo, "Primary", size, request.headers.get("accept", ""), artwork, head=request.method == "HEAD")
        except renditions.Preparing:
            served = None
        served = served or renditions.Served(*cast_photos.image_bytes(photo, artwork, db), cacheable=False)
    except FileNotFoundError as exc:  # also from sized()'s height-only branch, which reads the original
        raise HTTPException(status_code=404, detail="Image not found") from exc
    return Response(content=served.content, media_type=served.content_type, headers={
        "Cache-Control": ("public" if signed else "private") + ", max-age=31536000" if served.cacheable else "no-store",
        "Vary": "Accept", "X-Content-Type-Options": "nosniff",
    })


def around_episode(episodes: dict, wanted: str | None) -> dict:
    """The episode before, the episode and the one after, in play order (a first episode has no predecessor)."""
    items = episodes["Items"]
    at = next((i for i, dto in enumerate(items) if parse_item_id(dto["Id"]) == wanted), None)
    if at is None:
        return jf.query_result([], 0)
    around = items[max(at - 1, 0):at + 2]
    return jf.query_result(around, len(around))


@router.api_route("/items/{item_id}/images/{image_type}", methods=["GET", "HEAD"])
@router.api_route("/items/{item_id}/images/{image_type}/{image_index}", methods=["GET", "HEAD"])
def item_image(
    item_id: str, image_type: str, request: Request, db: Db,
    image_index: int = 0, tag: str | None = Query(None, max_length=128), tags: str | None = Query(None, max_length=128),
) -> Response:
    tag = tag or tags  # Roku sends the misspelt "Tags" for people, similar items and list rows
    kind = jf.IMAGE_TYPES.get(image_type)
    extra_backdrop = kind == "Backdrop" and 0 < image_index <= EXTRA_BACKDROPS
    if kind is None or not (image_index == 0 or extra_backdrop):
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        caller = jellyfin_user(request, db)  # token OR signed tag, so not a dependency
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        caller = None
    granted = False
    if caller is None and tag is None and (grant := image_grants.get(grant_address(request), IMAGE_GRANT_KEY)):
        # A poster that can send neither header nor tag: the token an authenticated call from this address presented
        # minutes ago. It is that member's full check (revocation, visibility), but only for the ids checked below.
        request.state.stream_grant_token = grant
        try:
            caller, granted = jellyfin_user(request, db), True
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
    enforce_rate_limit("artwork_serve", request, user_id=caller.id if caller is not None else None)
    if granted:  # titles and Library items have random ids; a person's, view's or Channels folder's is derived from a name
        entity = jf.resolve(db, caller, item_id) if caller is not None else None
        if entity is None or (entity.title is None and entity.item is None):
            raise HTTPException(status_code=404, detail="Image not found")
    person_id = parse_item_id(item_id)
    photo = cast_photos.subject(db, person_id) if person_id is not None else None  # NFO person, or a household photo
    person = db.get(Person, person_id) if person_id is not None and photo is None else None
    if photo is not None:
        return _person_photo(request, db, photo, kind, tag, caller)
    if person is not None:
        # Same rule as title art: a device token, or the signed tag we only ever hand to
        # authorized callers in People[].PrimaryImageTag. Infuse sends no token on image requests.
        if kind != "Primary":
            raise HTTPException(status_code=404, detail="Image not found")
        expected = jf.image_tag(jellyfin_server_key(db), person.id, "Primary", f"/api/people/{person.id}/image")
        try:
            if caller is not None:
                art = load_person_image(db, request.app.state.artwork, person.id, caller)  # E: 404 unless a visible title credits them
            elif tag is not None and hmac.compare_digest(tag, expected) and person.profile_path:
                art = tmdb.load_image(request.app.state.artwork, person.profile_path, "Person")
            else:
                raise HTTPException(status_code=404, detail="Image not found")
        except ArtworkError as exc:
            raise HTTPException(status_code=404, detail="Image not found") from exc
        return Response(content=art.content, media_type=art.content_type, headers={
            "Cache-Control": ("private" if caller is not None else "public") + ", max-age=31536000", "X-Content-Type-Options": "nosniff",
        })
    size = {key: value for key, value in request.query_params.items() if key in renditions.SIZE_PARAMS}  # keys arrive lower-cased
    try:
        served = jf.load_image(
            db, caller, item_id, image_key(kind, image_index), tag, request.app.state.artwork,
            size=size, accept=request.headers.get("accept", ""), head=request.method == "HEAD",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc
    # A restricted member's tag (longer than the shared 32 chars) dies with their access: never in a shared cache.
    # Art a token (or a grant) earned is that caller's answer, never for a shared cache; a signed tag's is shareable.
    cache = ("private, max-age=300" if tag is not None and len(tag) > 32 else "private, max-age=31536000" if caller is not None else "public, max-age=31536000") if served.cacheable else "no-store"
    return Response(content=served.content, media_type=served.content_type, headers={
        "Cache-Control": cache, "Vary": "Accept", "X-Content-Type-Options": "nosniff",
    })


@router.api_route("/items/{item_id}/playbackinfo", methods=["GET", "POST"])
def playback_info(
    item_id: str, request: Request, user: Caller, db: Db, body: Annotated[dict, Depends(playback_body)],
    caller: Annotated[JellyfinCaller, Depends(jellyfin_caller)],
) -> dict:
    """MediaSources for the named item, probing on demand (cached by fingerprint); decide() per source."""
    screen_time.enforce(db, user, fresh=True)
    entity = entity_or_404(db, user, item_id)
    versions = jf.playable_versions(db, user, entity)
    wanted = playback_request(body, request.query_params)  # MediaSourceId from the body or the query, any key case
    if wanted.media_source_id == entity.id:  # the item's own id is Jellyfin's "primary source", i.e. no selection
        wanted = replace(wanted, media_source_id=None)
    if wanted.media_source_id is not None:
        versions = [version for version in versions if version.id == wanted.media_source_id]
    probes, artifacts, sources = MediaProbeService(db), MediaArtifactService(db), []
    for version in versions:
        try:
            plan = probes.plan(version)
        except (FileNotFoundError, OSError):
            continue
        found = artifacts.artifact_for(version.id)
        sources.append(jf.media_source(version, found[0] if found else None, plan.get("facts") or {}))
        # Transcript tracks only on PlaybackInfo (one query per version), where clients pick subtitles;
        # /Items?fields=MediaStreams shows embedded + sidecars. Batch it if a client needs them from lists.
        append_transcript_streams(db, sources[-1], version, plan.get("facts") or {})
    if not sources:
        raise HTTPException(status_code=404, detail=MEDIA_UNAVAILABLE)
    play_session_id = uuid.uuid4().hex
    stream_grants.put(grant_address(request), [parse_item_id(item_id), *(version.id for version in versions)], caller.token)
    annotate_media_sources(db, caller.user, caller.token_id, caller.token, sources, wanted, play_session_id)
    for stream in (stream for source in sources for stream in source.get("MediaStreams") or []):
        if stream.get("DeliveryMethod") == "External" and stream.get("DeliveryUrl"):
            # Players fetch DeliveryUrl without auth headers; Jellyfin appends the caller's ApiKey the same way.
            stream["DeliveryUrl"] += f"?ApiKey={quote(caller.token, safe='')}"
    return {"MediaSources": sources, "PlaySessionId": play_session_id}


@router.api_route("/videos/{item_id}/stream", methods=["GET", "HEAD"])
@router.api_route("/videos/{item_id}/stream.{container}", methods=["GET", "HEAD"])
def video_stream(item_id: str, request: Request, user: Annotated[User, Depends(jellyfin_stream_user)], db: Db, mediasourceid: str | None = Query(None, max_length=64)) -> MediaFileResponse:
    """Direct play with Range (like /api/library/{id}/media); the session closes before bytes stream."""
    return media_file(item_id, request, user, db, mediasourceid)


@router.api_route("/items/{item_id}/download", methods=["GET", "HEAD"])
def download(item_id: str, request: Request, user: Caller, db: Db, mediasourceid: str | None = Query(None, max_length=64)) -> MediaFileResponse:
    """The version's file as an attachment: allowed exactly when streaming is. A token is required (no PlaybackInfo grant)."""
    return media_file(item_id, request, user, db, mediasourceid, attachment=True)


def media_file(item_id: str, request: Request, user: User, db: Session, mediasourceid: str | None, *, attachment: bool = False) -> MediaFileResponse:
    version = jf.pick_version(db, user, jf.resolve(db, user, item_id), mediasourceid)
    if version is None:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        path = LibraryService(db).resolve_media_path(version)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=MEDIA_UNAVAILABLE) from exc
    device = getattr(request.state, "connected_app_id", None) or "web"  # set by the Connected app token check
    screen_time.enforce(db, user)  # every Range request: at most one real check a minute
    activity.guard(user.id, version.id, device)  # an admin stop ends downloads too
    if request.method == "GET" and not attachment:  # a download is not a watch session
        activity.touch(user.id, version.id, device)
    return MediaFileResponse(path, filename=path.name if attachment else None)


@router.get("/videos/{item_id}/{source_id}/subtitles/{index}/stream.{fmt}")
@router.get("/videos/{item_id}/{source_id}/subtitles/{index}/{start_ticks}/stream.{fmt}")
def subtitle_stream(item_id: str, source_id: str, index: int, fmt: str, user: Caller, db: Db) -> Response:
    """External sidecar bytes as stored, or an SRT/WebVTT sidecar converted to the other; transcript tracks are answered elsewhere."""
    version = jf.pick_version(db, user, jf.resolve(db, user, item_id), source_id)
    found = jf.sidecar_file(db, version, index) if version is not None else None
    convert = found is not None and found[1] != fmt and {found[1], fmt} == {"srt", "vtt"}
    if found is None or (found[1] != fmt and not convert):
        return transcript_subtitle(db, version, index, fmt)  # 404 unless index names a transcript track
    try:
        content = read_regular_file(found[0], MAX_SUBTITLE_BYTES)
        if convert:  # one line per cue, styling dropped: the transcript parser/renderer pair, no new converter
            content = render(parse_caption(content, found[1]), fmt).encode()
    except (OSError, TranscriptError) as exc:
        raise HTTPException(status_code=404, detail="Subtitle not found") from exc
    return Response(content=content, media_type=SUBTITLE_MEDIA_TYPES[fmt])


@router.get("/items/{item_id}/specialfeatures")
@router.get("/users/{uid}/items/{item_id}/specialfeatures")
def special_features(item_id: str, user: Caller, db: Db) -> list[dict]:
    entity = entity_or_404(db, user, item_id)
    extras = TitleService(db).extras(user, entity.title) if entity.title is not None else []
    return jf.JellyfinMapper(db, user).item_dtos(extras)


@router.get("/items/{item_id}/localtrailers")
@router.get("/users/{uid}/items/{item_id}/localtrailers")
def local_trailers(item_id: str, user: Caller, db: Db) -> list:
    entity_or_404(db, user, item_id)
    return []  # trailers are served as SpecialFeatures (ExtraType "Trailer")


class PlaybackReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ItemId: str | None = Field(None, max_length=64)
    MediaSourceId: str | None = Field(None, max_length=64)
    PositionTicks: int | None = Field(None, ge=0, le=jf.MAX_TICKS)
    PlaySessionId: str | None = Field(None, max_length=64)


class UserDataUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Played: bool | None = None
    IsFavorite: bool | None = None
    PlaybackPositionTicks: int | None = Field(None, ge=0, le=jf.MAX_TICKS)


def _record_position(db: Session, user: User, version_id: str, ticks: int) -> None:
    with write_transaction(db, name="jellyfin_playback"):
        PlaybackProgressService(db).update(version_id, PlaybackProgressUpdateRequest(position_seconds=int(from_ticks(ticks))), user)


@router.post("/sessions/playing", status_code=204)
@router.post("/sessions/playing/progress", status_code=204)
@router.post("/sessions/playing/stopped", status_code=204)
def report_playback(report: PlaybackReport, request: Request, user: Caller, db: Db, caller: JellyfinCaller = Depends(jellyfin_caller)) -> Response:
    """Start, Progress and Stopped all record the position on the version played: the one progress
    store Lumina's UI reads, whose 95% rule completes it. No position, nothing recorded.
    Stopped also ends the PlaySessionId's encode, with or without a position."""
    if request.url.path.endswith("/stopped") and PLAY_SESSION_ID.fullmatch(report.PlaySessionId or ""):
        sessions.stop_where(user_id=caller.user.id, device=caller.token_id, play_session_id=report.PlaySessionId)
    version = None
    if report.PositionTicks is not None or report.ItemId:
        try:
            version = jf.pick_version(db, user, jf.resolve(db, user, report.ItemId), report.MediaSourceId)
        except HTTPException:
            if report.PositionTicks is not None:
                raise
    if report.PositionTicks is None:
        _track(request, user, caller, version, None)
    else:
        if version is None:
            raise HTTPException(status_code=404, detail="Item not found")
        _record_position(db, user, version.id, report.PositionTicks)
        _track(request, user, caller, version, report.PositionTicks)
    if version is not None:  # screen time from any report on a real item; Stopped counts its tail and ends the beat
        screen_time.heartbeat(db, user, stopped=request.url.path.endswith("/stopped"))
    return Response(status_code=204)


def _track(request: Request, user: User, caller: JellyfinCaller, version, ticks: int | None) -> None:  # noqa: ANN001
    """Feed the admin Activity tracker: any report keeps the session alive, Stopped ends it."""
    if version is None:
        return
    if request.url.path.endswith("/stopped"):
        activity.finish(user.id, version.id, caller.token_id)
    else:
        activity.touch(user.id, version.id, caller.token_id, position=from_ticks(ticks) if ticks is not None else None, duration=version.duration)


def _played(db: Session, user: User, item_id: str, played: bool) -> dict:
    entity = entity_or_404(db, user, item_id)
    try:
        with write_transaction(db, name="jellyfin_played"):
            jf.set_played(db, user, entity, played)
        return jf.JellyfinMapper(db, user).user_data_for(entity)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Item not found") from exc


@router.post("/userplayeditems/{item_id}")
@router.post("/users/{uid}/playeditems/{item_id}")
def mark_played(item_id: str, user: Caller, db: Db) -> dict:
    return _played(db, user, item_id, True)


@router.delete("/userplayeditems/{item_id}")
@router.delete("/users/{uid}/playeditems/{item_id}")
def mark_unplayed(item_id: str, user: Caller, db: Db) -> dict:
    return _played(db, user, item_id, False)


def _favorite(db: Session, user: User, item_id: str, favorite: bool) -> dict:
    entity = entity_or_404(db, user, item_id)
    if entity.view is not None:
        raise HTTPException(status_code=404, detail="Item not found")
    with write_transaction(db, name="jellyfin_favorite"):
        TitleService(db).set_favorite(user, entity.id, favorite)
    return jf.JellyfinMapper(db, user).user_data_for(entity)


@router.post("/userfavoriteitems/{item_id}")
@router.post("/users/{uid}/favoriteitems/{item_id}")
def add_favorite(item_id: str, user: Caller, db: Db) -> dict:
    return _favorite(db, user, item_id, True)


@router.delete("/userfavoriteitems/{item_id}")
@router.delete("/users/{uid}/favoriteitems/{item_id}")
def remove_favorite(item_id: str, user: Caller, db: Db) -> dict:
    return _favorite(db, user, item_id, False)


@router.get("/useritems/{item_id}/userdata")
@router.get("/users/{uid}/items/{item_id}/userdata")
def get_user_data(item_id: str, user: Caller, db: Db) -> dict:
    try:
        return jf.JellyfinMapper(db, user).user_data_for(entity_or_404(db, user, item_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Item not found") from exc


@router.post("/useritems/{item_id}/userdata")
@router.post("/users/{uid}/items/{item_id}/userdata")
def update_user_data(item_id: str, payload: UserDataUpdate, user: Caller, db: Db) -> dict:
    entity = entity_or_404(db, user, item_id)
    if entity.view is not None:
        raise HTTPException(status_code=404, detail="Item not found")
    versions = jf.playable_versions(db, user, entity)
    resumable = db.query(PlaybackProgress).filter(
        PlaybackProgress.user_id == user.id,
        PlaybackProgress.item_id.in_([version.id for version in versions]),
        PlaybackProgress.completed.is_(False),
        PlaybackProgress.position_seconds > 0,
        PlaybackProgress.dismissed_at.is_(None),
    ).all() if versions else []
    if dismisses_resume(payload.model_dump(exclude_unset=True), resumable=bool(resumable)):
        with write_transaction(db, name="jellyfin_dismiss_resume"):
            for row in resumable:
                PlaybackProgressService(db).dismiss(row.item_id, user)
        return jf.JellyfinMapper(db, user).user_data_for(entity)  # still reports the real position
    try:
        with write_transaction(db, name="jellyfin_user_data"):
            if payload.IsFavorite is not None:
                TitleService(db).set_favorite(user, entity.id, payload.IsFavorite)
            if payload.Played is not None:
                jf.set_played(db, user, entity, payload.Played)
            elif payload.PlaybackPositionTicks is not None:
                version = jf.pick_version(db, user, entity)
                if version is None:
                    raise LookupError("not playable")
                PlaybackProgressService(db).update(
                    version.id, PlaybackProgressUpdateRequest(position_seconds=int(from_ticks(payload.PlaybackPositionTicks))), user)
        return jf.JellyfinMapper(db, user).user_data_for(entity)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Item not found") from exc

# ==== Client probes answered with fixed values; the integration module wires the real functions here. ====

@router.post("/sessions/capabilities", status_code=204)
@router.post("/sessions/capabilities/full", status_code=204)
@router.post("/sessions/playing/ping", status_code=204)
def accepted(_user: Caller) -> Response:
    return Response(status_code=204)


@router.get("/branding/configuration")
def branding_configuration() -> dict:
    return {"LoginDisclaimer": "", "CustomCss": "", "SplashscreenEnabled": False}


@router.get("/branding/css")
@router.get("/branding/css.css")
def branding_css() -> Response:
    return Response(content="", media_type="text/css")


@router.get("/displaypreferences/{preferences_id}")
def display_preferences(
    _user: Caller, preferences_id: str = Path(max_length=64), client: str = Query("emby", max_length=64),
) -> dict:
    return {
        "Id": preferences_id, "SortBy": "SortName", "SortOrder": "Ascending", "RememberIndexing": False,
        "RememberSorting": False, "ScrollDirection": "Horizontal", "ShowBackdrop": True, "ShowSidebar": False,
        "Client": client, "CustomPrefs": {}, "PrimaryImageHeight": 250, "PrimaryImageWidth": 250,
    }


@router.post("/displaypreferences/{preferences_id}", status_code=204)
def save_display_preferences(_user: Caller) -> Response:
    return Response(status_code=204)


@router.get("/playback/bitratetest")
def bitrate_test(_user: Caller, size: int = Query(102_400, ge=0)) -> Response:
    return Response(content=bytes(min(size, 10_000_000)), media_type="application/octet-stream")


@router.get("/plugins")
@router.get("/packages")
@router.get("/localization/cultures")
@router.get("/localization/countries")
@router.get("/localization/parentalratings")
@router.get("/localization/options")
def empty_list(_user: Caller) -> list:
    return []


@router.get("/userimage")
@router.get("/users/{uid}/images/{image_type}")
def no_user_image() -> Response:
    """Members have no avatar here. Roku asks before it has a token, so this needs none (and says 404, not 401)."""
    return Response(status_code=404)


# ==== D. Catch-all: keep LAST. A probe we do not serve is an empty 404, logged without query values. ====

@router.api_route("", methods=_ALL_METHODS, include_in_schema=False)
@router.api_route("/{rest:path}", methods=_ALL_METHODS, include_in_schema=False)
def unhandled(request: Request) -> Response:
    logger.warning("jellyfin.unhandled %s %s", request.method, masked_path(request.url.path))
    return Response(status_code=404)


def socket_token(websocket: WebSocket) -> str | None:
    """The same credential sources and token check as the HTTP routes (cookies are never read)."""
    with session_scope() as db:
        try:
            require_jellyfin_enabled(db)
        except HTTPException:
            return None
        # WebSocket and Request share the headers, query and client the resolver reads.
        token = parse_client_auth(websocket).token
        return token if resolve_device_token(db, websocket, token, kind="jellyfin") is not None else None  # type: ignore[arg-type]


def socket_message(kind: str, data: object = None) -> dict:
    return {"MessageType": kind, "MessageId": uuid.uuid4().hex} | ({} if data is None else {"Data": data})


async def jellyfin_socket(websocket: WebSocket) -> None:
    """Jellyfin's /socket: an authenticated client is told how often to send KeepAlive, which is answered; any other
    message is ignored (Lumina pushes nothing and takes no remote commands). Idle, oversize or long-lived sockets end."""
    token = await run_in_threadpool(socket_token, websocket)
    if token is None:
        await websocket.close(code=1008)
        return
    if open_sockets[token] >= MAX_SOCKETS_PER_TOKEN or sum(open_sockets.values()) >= MAX_SOCKETS:
        await websocket.close(code=1013)  # try again later
        return
    open_sockets[token] += 1
    try:
        await websocket.accept()
        deadline = time.monotonic() + SOCKET_LIFETIME_SECONDS
        await asyncio.wait_for(websocket.send_json(socket_message("ForceKeepAlive", SOCKET_KEEP_ALIVE_SECONDS)), SOCKET_IDLE_SECONDS)
        while (remaining := deadline - time.monotonic()) > 0:
            incoming = await asyncio.wait_for(websocket.receive(), min(remaining, SOCKET_IDLE_SECONDS))
            if incoming["type"] == "websocket.disconnect":
                return
            text = incoming.get("text")
            if len(text or incoming.get("bytes") or "") > MAX_SOCKET_MESSAGE:
                await websocket.close(code=1009)
                return
            try:
                message = json.loads(text) if text else None
            except ValueError:
                message = None
            if isinstance(message, dict) and message.get("MessageType") == "KeepAlive":
                await asyncio.wait_for(websocket.send_json(socket_message("KeepAlive")), SOCKET_IDLE_SECONDS)
    except (TimeoutError, WebSocketDisconnect, RuntimeError):
        pass
    finally:
        open_sockets[token] -= 1
        if open_sockets[token] <= 0:
            del open_sockets[token]
    with suppress(RuntimeError):  # already closed by the peer
        await websocket.close()


def register(app: FastAPI, artwork: ArtworkService) -> None:
    """Call after jellyfin_auth.register(app) so its routes precede this catch-all."""
    app.state.artwork = artwork
    jellyfin_integration.register(app)  # before this router: its routes win over the stubs and the catch-all
    app.include_router(jellyfin_probes.router)
    app.add_api_websocket_route("/socket", jellyfin_socket)  # before main.py mounts the SPA, which closes websockets
    app.include_router(router)
    # app.routes lists the auth and integration routes; these routers are included as opaque entries, so read their own routes.
    app.add_middleware(JellyfinPathMiddleware, root_segments=jellyfin_segments([*app.routes, *jellyfin_probes.router.routes, *router.routes]))
