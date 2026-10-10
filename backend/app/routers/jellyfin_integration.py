"""Jellyfin routes that combine several features.

Registered by ``app.routers.jellyfin.register`` before it includes the main router, so these
routes match ahead of its ``/items/{item_id}`` routes and its catch-all. Path/query
normalization applies unchanged (the Jellyfin ASGI middleware), and the Jellyfin gate still applies
because ``jellyfin_user`` depends on ``require_jellyfin_enabled``. Paths are lowercase,
and so are query parameter names (the normalizing middleware lowercases keys).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.media_schemas import PlaybackSessionRequest
from app.models import HouseholdCollection, LibraryItem, User
from app.routers.jellyfin_probes import split_csv
from app.routers.local_playback import open_session  # the shared start path
from app.services import jellyfin as jf
from app.services import playlists as pl
from app.services import screen_time
from app.services.connected_apps import jellyfin_user, parse_client_auth
from app.services.jellyfin_discovery import SEARCH_LIMIT, item_types, search_hint, search_refs, similar_refs, suggestion_refs
from app.services.jellyfin_playback import PLAY_SESSION_ID, pending_transcodes, subtitle_format, transcript_base
from app.services.library import LibraryService
from app.services.local_playback_sessions import master_playlist, sessions, with_api_key
from app.services.media_artifacts import MediaArtifactService
from app.services.media_probe import MediaProbeService, loudness_gain_db
from app.services.media_response import MediaFileResponse
from app.services.media_segments import jellyfin_segments, segments_for
from app.services.media_titles import jellyfin_id, parse_item_id, synthetic_id
from app.services.transcripts import TranscriptService
from app.services.user_settings import normalize_loudness

NOT_FOUND = "Playback session not found"
MPEGURL = "application/vnd.apple.mpegurl"
PLAYLISTS_VIEW_ID = synthetic_id("view:playlists")
_PLAYLIST_ERRORS = ((pl.PlaylistNotFound, 404), (pl.PlaylistForbidden, 403), (pl.PlaylistBadRequest, 400), (pl.PlaylistConflict, 409))


def _run(action):  # noqa: ANN001, ANN202
    try:
        return action()
    except tuple(error for error, _ in _PLAYLIST_ERRORS) as exc:
        status = next(code for error, code in _PLAYLIST_ERRORS if isinstance(exc, error))
        raise HTTPException(status_code=status, detail="Playlist request refused") from exc


def _ids(raw: str) -> list[str]:
    ids = [parse_item_id(part) for part in raw.split(",") if part.strip()]
    if not ids or None in ids:
        raise HTTPException(status_code=400, detail="Invalid ids")
    return ids  # type: ignore[return-value]


@dataclass(frozen=True)
class JellyfinCaller:
    user: User
    token_id: str  # the Connected app id (device_tokens.id): the session "device"
    token: str  # the presented token, echoed only into this caller's own stream URLs


def jellyfin_caller(request: Request, user: User = Depends(jellyfin_user)) -> JellyfinCaller:
    """Jellyfin_user plus the two facts playback needs (FastAPI runs jellyfin_user once per request)."""
    return JellyfinCaller(user, request.state.connected_app_id, parse_client_auth(request).token or "")


async def playback_body(request: Request) -> dict:
    """PlaybackInfo's POST body (DeviceProfile, StartTimeTicks, stream indexes); {} for GET or garbage."""
    if request.method != "POST":
        return {}
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def jellyfin_master_playlist(
    item_id: str, mediasourceid: str | None = None, playsessionid: str = "",
    caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function"),
) -> Response:
    source_id = parse_item_id(mediasourceid) or parse_item_id(item_id)
    if source_id is None or not PLAY_SESSION_ID.fullmatch(playsessionid):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pending = pending_transcodes.get(playsessionid, source_id, caller.user.id, caller.token_id)
    item = LibraryService(db).get_item(source_id, caller.user) if pending else None
    if pending is None or item is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    session = sessions.find_play_session(caller.user.id, caller.token_id, playsessionid, item.id)
    if session is None:
        try:
            facts = MediaProbeService(db).facts(item)
        except OSError as exc:  # A vanished file is 404, never 500
            raise HTTPException(status_code=404, detail=NOT_FOUND) from exc
        request = PlaybackSessionRequest(
            audio_index=pending.audio_index,
            subtitle=f"i:{pending.subtitle_index}" if pending.subtitle_index is not None else None,
        )
        session = open_session(  # raises 404/409/429/502/503 itself and closes db before waiting
            db, caller.user, item, caps=pending.caps, request=request, start=pending.start_seconds, device=caller.token_id,
            play_session_id=playsessionid, max_bitrate=pending.max_bitrate, seekable=True,
            audio_gain_db=loudness_gain_db(facts.get("loudness")) if normalize_loudness(db, caller.user.id) else None,
        )
    variant = f"hls/{playsessionid}/index.m3u8?ApiKey={quote(caller.token, safe='')}"
    return Response(master_playlist(session, variant), media_type=MPEGURL, headers={"Cache-Control": "no-store"})


def hls_file(
    item_id: str, play_session_id: str, name: str,
    caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function"),
) -> Response:
    source_id = parse_item_id(item_id)
    if source_id is None or not PLAY_SESSION_ID.fullmatch(play_session_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    session = sessions.find_play_session(caller.user.id, caller.token_id, play_session_id, source_id)
    if session is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    # Every request re-checks access: one indexed lookup (as the web route does).
    if LibraryService(db).get_item(session.item_id, caller.user) is None:
        sessions.stop_where(user_id=caller.user.id, device=caller.token_id, play_session_id=play_session_id)
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    screen_time.enforce(db, caller.user)
    if name == "index.m3u8" and session.plan is not None:  # the whole file's timeline, not ffmpeg's growing list
        return Response(session.plan.playlist(caller.token), media_type=MPEGURL, headers={"Cache-Control": "no-store"})
    path = sessions.segment(session, name)  # D: FILE_NAME allowlist, highest-segment record and throttling; seeks start runs
    if path is None:
        raise HTTPException(status_code=404, detail="Playback segment not found")
    if name == "index.m3u8":
        return Response(with_api_key(path.read_text(encoding="utf-8"), caller.token), media_type=MPEGURL, headers={"Cache-Control": "no-store"})
    if name == "init.mp4":  # a seek's run rewrites it in place: read it whole, never stat-then-open (pp-encode's playlist race)
        return Response(path.read_bytes(), media_type="video/mp4", headers={"Cache-Control": "no-store"})
    return MediaFileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=600"})


def stop_active_encodings(playsessionid: str = "", caller: JellyfinCaller = Depends(jellyfin_caller)) -> Response:
    if not playsessionid:
        sessions.stop_where(user_id=caller.user.id, device=caller.token_id)  # the calling app's encodes; DeviceId is implied by the token
    elif PLAY_SESSION_ID.fullmatch(playsessionid):
        sessions.stop_where(user_id=caller.user.id, device=caller.token_id, play_session_id=playsessionid)
    return Response(status_code=204)


def transcript_subtitle(db: Session, item: LibraryItem | None, index: int, fmt: str) -> Response:
    """The subtitle route after its sidecar branch: a transcript track by stream index, rendered from the DB."""
    fmt_name = subtitle_format(fmt)
    found = MediaArtifactService(db).artifact_for(item.id) if item is not None else None
    tracks = TranscriptService(db).subtitle_tracks(item.id) if item is not None else []  # only this visible item's tracks
    offset = index - transcript_base((found[0].probe or {}) if found else {}, item) if item is not None else -1
    if fmt_name is None or not 0 <= offset < len(tracks):
        raise HTTPException(status_code=404, detail="Subtitle not found")
    text = TranscriptService(db).render_transcript(tracks[offset].id.removeprefix("t:"), fmt_name)
    media_type = "text/vtt" if fmt_name == "vtt" else "application/x-subrip"
    return PlainTextResponse(text, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})


def media_segments(
    item_id: str, request: Request,
    caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function"),
) -> dict:
    entity = jf.resolve(db, caller.user, item_id)
    item = jf.pick_version(db, caller.user, entity)  # title -> preferred version; item -> itself; None -> 404
    if entity is None or item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    wanted = {part.strip() for value in request.query_params.getlist("includesegmenttypes") for part in value.split(",") if part.strip()}
    # F maps intro->Intro, credits->Outro, recap, preview, commercial; uuid5 ids; ticks; never mute ranges.
    return jellyfin_segments(jellyfin_id(entity.id), item.id, segments_for(db, item.id), wanted or None)


def search_hints(
    searchterm: str = Query("", max_length=200), startindex: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=SEARCH_LIMIT),
    includeitemtypes: str | None = Query(None, max_length=500),
    caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function"),
) -> dict:
    if not searchterm:
        raise HTTPException(status_code=400, detail="searchTerm is required")  # the blank-term 400 stands
    refs = search_refs(db, caller.user, searchterm, item_types(includeitemtypes), SEARCH_LIMIT)
    page = refs[startindex:startindex + limit]
    dtos = jf.JellyfinMapper(db, caller.user).by_ids(page)
    return {"SearchHints": [search_hint(dto, searchterm) for dto in dtos], "TotalRecordCount": len(refs)}


def similar(
    item_id: str, limit: int = Query(12, ge=1, le=100), fields: list[str] = Query([]),
    caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function"),
) -> dict:
    ref = parse_item_id(item_id)
    refs = similar_refs(db, caller.user, ref, limit) if ref else None
    if refs is None:
        # Channels items are item-backed: visible but without title similarity.
        if ref and LibraryService(db).get_item(ref, caller.user) is not None:
            return {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
        raise HTTPException(status_code=404, detail="Item not found")
    dtos = jf.JellyfinMapper(db, caller.user, split_csv(fields)).by_ids(refs)
    return {"Items": dtos, "TotalRecordCount": len(dtos), "StartIndex": 0}


def suggestions(
    type: str | None = Query(None, max_length=500), startindex: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),  # noqa: A002
    caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function"),
) -> dict:
    refs = suggestion_refs(db, caller.user, item_types(type), startindex + limit)
    dtos = jf.JellyfinMapper(db, caller.user).by_ids(refs[startindex:])
    return {"Items": dtos, "TotalRecordCount": len(refs), "StartIndex": startindex}


def search_items(db: Session, user: User, query: jf.ItemsQuery) -> dict:
    """/Items?searchTerm: the ranked refs rendered by by_ids, in relevance order.

    Only SortName is honoured as a client sort and other /Items filters (ParentId, IsPlayed) are ignored
    on search results (accepted ceiling); route the refs through the filtered query if a client needs them.
    """
    refs = search_refs(db, user, query.searchterm or "", item_types(",".join(query.csv("includeitemtypes"))), SEARCH_LIMIT)
    dtos = jf.JellyfinMapper(db, user, query.csv("fields")).by_ids(refs)
    if query.csv("sortby"):
        dtos.sort(key=lambda dto: (dto.get("SortName") or dto.get("Name") or "").casefold())
    start, limit = query.startindex, query.page_limit()
    return jf.query_result(dtos[start:start + limit], len(dtos), start)


def playlist_items_dtos(db: Session, user: User, playlist: pl.Playlist, fields: list[str]) -> list[dict]:
    """Entries shown as their Movie/Episode title (Channels videos as themselves), each with its PlaylistItemId."""
    item_ids = [entry.item_id for entry in playlist.entries]
    title_of = dict(db.execute(
        select(LibraryItem.id, LibraryItem.title_id).where(LibraryItem.id.in_(item_ids), LibraryItem.extra_type.is_(None))
    ).all()) if item_ids else {}
    refs = [title_of.get(entry.item_id) or entry.item_id for entry in playlist.entries]
    by_id = {dto["Id"]: dto for dto in jf.JellyfinMapper(db, user, fields).by_ids(list(dict.fromkeys(refs)))}  # visible only
    return [
        {**by_id[jellyfin_id(ref)], "PlaylistItemId": jellyfin_id(entry.entry_id)}
        for entry, ref in zip(playlist.entries, refs, strict=True) if jellyfin_id(ref) in by_id
    ]


def playlist_listing(db: Session, user: User, query: jf.ItemsQuery) -> dict | None:
    """/Items with ParentId = the Playlists view or one playlist; None hands the request back to the main router."""
    parent = parse_item_id(query.parentid)
    start, limit = query.startindex, query.page_limit()
    if parent == PLAYLISTS_VIEW_ID:
        rows = [jf.playlist_dto(p, jf.JellyfinMapper(db, user).server_id) for p in pl.list_playlists(db, user)]
    elif parent is not None and (parent == pl.watchlist_id(user) or db.get(HouseholdCollection, parent) is not None):
        try:
            rows = playlist_items_dtos(db, user, pl.get_playlist(db, user, parent), query.csv("fields"))
        except pl.PlaylistNotFound:
            return None  # B answers 404 for an invisible collection id
    else:
        return None
    return jf.query_result(rows[start:start + limit], len(rows), start)


def playlist_info(playlist_id: str, caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function")) -> dict:
    playlist = _run(lambda: pl.get_playlist(db, caller.user, parse_item_id(playlist_id) or ""))
    collection = db.get(HouseholdCollection, playlist.id) if playlist.kind != "watchlist" else None
    item_ids = [row["Id"] for row in playlist_items_dtos(db, caller.user, playlist, [])]
    return {"OpenAccess": collection is not None and collection.visibility == "shared", "Shares": [], "ItemIds": item_ids}


def get_playlist_items(
    playlist_id: str, startindex: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500), fields: list[str] = Query([]),
    caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function"),
) -> dict:
    playlist = _run(lambda: pl.get_playlist(db, caller.user, parse_item_id(playlist_id) or ""))
    rows = playlist_items_dtos(db, caller.user, playlist, split_csv(fields))
    return jf.query_result(rows[startindex:startindex + limit], len(rows), startindex)


def add_playlist_items(playlist_id: str, ids: str = Query(..., max_length=4000), caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function")) -> Response:
    refs = _ids(ids)
    _run(lambda: pl.add_to_playlist(db, caller.user, parse_item_id(playlist_id) or "", refs))
    return Response(status_code=204)


def remove_playlist_items(playlist_id: str, entryids: str = Query(..., max_length=4000), caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function")) -> Response:
    entries = _ids(entryids)
    _run(lambda: pl.remove_from_playlist(db, caller.user, parse_item_id(playlist_id) or "", entries))
    return Response(status_code=204)


def move_playlist_item(playlist_id: str, entry_id: str, new_index: int, caller: JellyfinCaller = Depends(jellyfin_caller), db: Session = Depends(get_db, scope="function")) -> Response:
    entry = parse_item_id(entry_id)
    if entry is None or new_index < 0:
        raise HTTPException(status_code=404, detail="Playlist entry not found")
    _run(lambda: pl.move_playlist_entry(db, caller.user, parse_item_id(playlist_id) or "", entry, new_index))
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    app.get("/jellyfin/videos/{item_id}/master.m3u8")(jellyfin_master_playlist)
    app.get("/jellyfin/videos/{item_id}/hls/{play_session_id}/{name}")(hls_file)
    app.delete("/jellyfin/videos/activeencodings", status_code=204)(stop_active_encodings)
    app.get("/jellyfin/mediasegments/{item_id}")(media_segments)
    app.get("/jellyfin/search/hints")(search_hints)
    for prefix in ("items", "shows", "movies"):
        app.get(f"/jellyfin/{prefix}/{{item_id}}/similar")(similar)
    app.get("/jellyfin/items/suggestions")(suggestions)  # precedes /items/{item_id}: registered before the main router
    app.get("/jellyfin/users/{uid}/suggestions")(suggestions)  # legacy; jellyfin_user 404s unless uid is the caller
    app.get("/jellyfin/playlists/{playlist_id}")(playlist_info)
    app.get("/jellyfin/playlists/{playlist_id}/items")(get_playlist_items)
    app.post("/jellyfin/playlists/{playlist_id}/items", status_code=204)(add_playlist_items)
    app.delete("/jellyfin/playlists/{playlist_id}/items", status_code=204)(remove_playlist_items)
    app.post("/jellyfin/playlists/{playlist_id}/items/{entry_id}/move/{new_index}", status_code=204)(move_playlist_item)
