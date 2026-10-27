"""Lumina music extensions authenticated by the opt-in Jellyfin Connected app."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.music_schemas import CastControl, CastRequest, MusicPlaylistSave, MusicQueueRequest
from app.routers import music, music_listening_session, music_receiver
from app.routers.jellyfin_integration import JellyfinCaller, jellyfin_caller
from app.services import music_playlists
from app.services.media_titles import jellyfin_id, parse_item_id

router = APIRouter(prefix="/jellyfin/lumina/music")
Caller = Annotated[JellyfinCaller, Depends(jellyfin_caller)]
Db = Annotated[Session, Depends(get_db, scope="function")]


def _id(value: str) -> str:
    return parse_item_id(value) or value


def _native(value):  # noqa: ANN001, ANN202
    """Keep service metadata, replacing browser URLs throughout queues/conflict snapshots."""
    value = jsonable_encoder(value)
    if isinstance(value, list):
        return [_native(row) for row in value]
    if not isinstance(value, dict):
        return value
    result = {key: _native(row) for key, row in value.items()}
    if "audio_url" in result and "id" in result:
        result["audio_url"] = f"/Audio/{jellyfin_id(result['id'])}/stream"
        album = result.get("album_id")
        result["artwork_url"] = f"/Items/{jellyfin_id(album)}/Images/Primary" if album and result.get("artwork_url") else None
    return result


def _playlist_body(body: MusicPlaylistSave) -> MusicPlaylistSave:
    rules = body.rules
    if rules and rules.artist_id:
        rules = rules.model_copy(update={"artist_id": _id(rules.artist_id)})
    return body.model_copy(update={"item_ids": [_id(value) for value in body.item_ids], "rules": rules})


@router.get("/playlists")
def playlists(caller: Caller, db: Db) -> list[dict]:
    return music_playlists.list_playlists(db, caller.user)


@router.post("/playlists")
def create_playlist(body: MusicPlaylistSave, caller: Caller, db: Db) -> dict:
    return music_playlists.save(db, caller.user, _playlist_body(body))


@router.put("/playlists/{playlist_id}")
def save_playlist(playlist_id: str, body: MusicPlaylistSave, caller: Caller, db: Db) -> dict:
    return music_playlists.save(db, caller.user, _playlist_body(body), _id(playlist_id))


@router.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: str, caller: Caller, db: Db) -> dict:
    music_playlists.remove(db, caller.user, _id(playlist_id))
    return {"deleted": True}


@router.get("/playlists/{playlist_id}/tracks")
def playlist_tracks(playlist_id: str, caller: Caller, db: Db) -> list:
    return _native(music.playlist_tracks(_id(playlist_id), caller.user, db))


@router.get("/mixes/{kind}")
def mix(kind: Literal["favorites", "unplayed", "rotation", "radio"], caller: Caller, db: Db, seed_id: str | None = None) -> list:
    return _native(music.mix(kind, _id(seed_id) if seed_id else None, caller.user, db))


@router.post("/queue")
def queue(body: MusicQueueRequest, caller: Caller, db: Db) -> list:
    normalized = body.model_copy(update={"item_ids": [_id(value) for value in body.item_ids],
                                         "title_id": _id(body.title_id) if body.title_id else None})
    return _native(music.queue(normalized, caller.user, db))


@router.get("/session")
def session(caller: Caller, db: Db) -> dict:
    return _native(music_listening_session.get_session(caller.user, db))


@router.put("/session")
def replace_session(body: music_listening_session.MusicListeningSessionWrite, caller: Caller, db: Db):  # noqa: ANN201
    entries = [entry.model_copy(update={"item_id": _id(entry.item_id)}) if isinstance(entry, music_listening_session.TrackQueueEntry) else entry
               for entry in body.entries] if body.entries is not None else None
    body = body.model_copy(update={"item_ids": [_id(value) for value in body.item_ids], "entries": entries})
    try:
        return _native(music_listening_session.replace_session(body, caller.user, db))
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return JSONResponse(status_code=409, content={"detail": _native(error.detail)})


@router.get("/tracks/{item_id}")
def track(item_id: str, caller: Caller, db: Db) -> dict:
    return _native(music.get_track(_id(item_id), caller.user, db))


@router.get("/tracks/{item_id}/lyrics")
def lyrics(item_id: str, caller: Caller, db: Db, retry: bool = False) -> dict:
    return music.lyrics(_id(item_id), caller.user, db, retry)


@router.api_route("/tracks/{item_id}/download", methods=["GET", "HEAD"])
def download(item_id: str, request: Request, caller: Caller, db: Db,
             format_: Literal["native", "flac", "wav"] = Query("native", alias="format")):
    return music.audio(_id(item_id), request, format_=format_, download=True, user=caller.user, db=db)


@router.get("/sonos")
def speakers(request: Request, caller: Caller) -> list:
    return music.speakers(request, caller.user)


@router.post("/cast")
def cast(body: CastRequest, request: Request, caller: Caller, db: Db):  # noqa: ANN201
    body = body.model_copy(update={"item_ids": [_id(value) for value in body.item_ids]})
    try:
        return _native(music.cast(body, request, caller.user, db))
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return JSONResponse(status_code=409, content={"detail": _native(error.detail)})


@router.post("/cast/{ticket_id}/control")
def control(ticket_id: str, body: CastControl, request: Request, caller: Caller, db: Db) -> dict:
    return music.control(_id(ticket_id), body, request, caller.user, db)


@router.get("/cast/{ticket_id}/status")
def cast_status(ticket_id: str, request: Request, caller: Caller, db: Db) -> dict:
    return music.cast_status(_id(ticket_id), request, caller.user, db)


@router.post("/receiver/tickets")
def receiver_mint(body: music_receiver.MusicReceiverTicketBody, caller: Caller, db: Db) -> dict:
    return music_receiver.mint_native(body, caller.user, db, issuer_device_id=caller.token_id)


@router.post("/receiver/tickets/{ticket_id}/renew")
def receiver_renew(ticket_id: str, caller: Caller, db: Db) -> dict:
    return music_receiver.renew_native(ticket_id, caller.user, db, issuer_device_id=caller.token_id)


@router.delete("/receiver/tickets/{ticket_id}")
def receiver_revoke(ticket_id: str, caller: Caller, db: Db) -> Response:
    return music_receiver.revoke_native(ticket_id, caller.user, db, issuer_device_id=caller.token_id)
