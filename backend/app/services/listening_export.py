"""Opt-in listening exports with one durable claim per logical play and provider."""
from __future__ import annotations

import asyncio
import hashlib
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ListeningExportConnection, ListeningPlayEvent, MusicTrackOverride, User, utcnow
from app.persistence import write_transaction
from app.services import music_library
from app.services.local_ai import LocalAiError, bounded_json_request

LASTFM = "https://ws.audioscrobbler.com"
LISTENBRAINZ = "https://api.listenbrainz.org"


class ExportRejected(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.retryable = retryable


def lastfm(credentials: dict, method: str, **params: str) -> dict:
    data = {"method": method, "api_key": credentials["api_key"], **params}
    if credentials.get("session_key") and method not in ("auth.getToken", "auth.getSession"):
        data["sk"] = credentials["session_key"]
    signed = "".join(key + str(data[key]) for key in sorted(data)) + credentials["api_secret"]
    data["api_sig"] = hashlib.md5(signed.encode(), usedforsecurity=False).hexdigest()  # Last.fm protocol signature
    result = bounded_json_request(LASTFM, "POST", "2.0/", timeout=15, label="Last.fm", max_bytes=256*1024,
                                  data={**data, "format": "json"})
    if result.get("error"):
        code = str(result["error"])
        raise ExportRejected(f"Last.fm error {code}", retryable=code in ("11", "16"))
    return result


def listenbrainz(token: str, path: str, *, body: dict | None = None, params: dict | None = None) -> dict:
    return bounded_json_request(LISTENBRAINZ, "POST" if body else "GET", path, timeout=15, label="ListenBrainz",
                                max_bytes=1024*1024, headers={"Authorization": f"Token {token}"},
                                **({"json": body} if body else {"params": params or {}}))


def account_snapshot(row: ListeningExportConnection) -> dict:
    return {"provider": row.provider, "generation": row.generation, "enabled": row.enabled and bool(row.credentials), "username": row.username,
            "credentials_set": bool(row.credentials), "authorization_pending": bool((row.credentials or {}).get("_pending"))}


def replace_account(db: Session, user: User, provider: str, credentials: dict | None, *, username: str | None = None, enabled: bool = False, expected_generation: str | None = None, compare_generation: bool = False, expected_request_token: str | None = None) -> ListeningExportConnection:
    with write_transaction(db, name="listening_export_account"):
        row = db.get(ListeningExportConnection, (user.id, provider), populate_existing=True)
        if (compare_generation or expected_generation is not None) and (row.generation if row else None) != expected_generation:
            raise HTTPException(409, "Listening export connection changed")
        if expected_request_token is not None and (row is None or ((row.credentials or {}).get("_pending") or {}).get("request_token") != expected_request_token):
            raise HTTPException(409, "Last.fm authorization changed")
        if row is None:
            row = ListeningExportConnection(user_id=user.id, provider=provider)
            db.add(row)
        row.credentials, row.enabled, row.username, row.generation = credentials, enabled, username, str(uuid4())
    return row


def stage_lastfm(db: Session, user: User, credentials: dict, expected_generation: str | None) -> None:
    with write_transaction(db, name="listening_export_authorize"):
        row = db.get(ListeningExportConnection, (user.id, "lastfm"), populate_existing=True)
        if (row.generation if row else None) != expected_generation:
            raise HTTPException(409, "Listening export connection changed")
        if row is None:
            row = ListeningExportConnection(user_id=user.id, provider="lastfm", generation=str(uuid4()), enabled=False, credentials={})
            db.add(row)
        row.credentials = {**(row.credentials or {}), "_pending": credentials}


def event_snapshot(row: ListeningPlayEvent) -> dict:
    return {"id": row.id, "item_id": row.item_id, "started_at": row.started_at, "listened_seconds": row.listened_seconds,
            "deliveries": {provider: {"status": entry["status"], "error": entry.get("error")} for provider, entry in (row.deliveries or {}).items()}}


def record(db: Session, user: User, identity: str, item_id: str, started_at: int, listened_seconds: float, account_generations: dict[str, str]) -> dict:
    existing = db.get(ListeningPlayEvent, identity)
    if existing and (existing.user_id != user.id or existing.item_id != item_id or existing.started_at != started_at):
        raise HTTPException(409, "Listening event identity conflict")
    accounts = list(db.scalars(select(ListeningExportConnection).where(ListeningExportConnection.user_id == user.id, ListeningExportConnection.enabled.is_(True))))
    accounts = [row for row in accounts if row.generation == account_generations.get(row.provider)]
    if not existing and not any(row.credentials for row in accounts):
        return {"recorded": False}
    # Metadata and duration come from the member's current registered library; clients send IDs and elapsed listening only.
    items = music_library.visible_tracks(db, user, [item_id])
    track = music_library.manifests(db, user, items)[0]
    source_tags = (items[0].metadata_json or {}).get("lumina_audio_tags")
    source_tags = source_tags if isinstance(source_tags, dict) else {}
    override = db.get(MusicTrackOverride, item_id)
    fields = override.fields if override and isinstance(override.fields, dict) else {}
    # Last.fm requires structured metadata, not guesses from a filename or folder.
    if not (fields.get("title") or source_tags.get("title")) or not (fields.get("artist") or source_tags.get("artist") or source_tags.get("album_artist")):
        return {"recorded": False}
    duration = track.duration
    if not duration or duration <= 30 or listened_seconds < 0 or listened_seconds > duration + 1:
        return {"recorded": False}
    now = int(time.time())
    if started_at > now + 30 or started_at < now - 7*86400 or listened_seconds > max(0, now - started_at) + 5:
        raise HTTPException(422, "Listening duration is outside the event interval")
    eligible = listened_seconds >= min(duration / 2, 240)
    with write_transaction(db, name="listening_export_event"):
        row = db.get(ListeningPlayEvent, identity, populate_existing=True)
        if row is None:
            duplicate = db.scalar(select(ListeningPlayEvent).where(ListeningPlayEvent.user_id == user.id, ListeningPlayEvent.item_id == item_id,
                                                                  ListeningPlayEvent.started_at == started_at))
            if duplicate is not None:
                return event_snapshot(duplicate)
            row = ListeningPlayEvent(id=identity, user_id=user.id, item_id=item_id, started_at=started_at,
                                     metadata_json={"title": track.title[:300], "artist": track.artist[:300], "album": track.album[:300] if source_tags.get("album") else "", "duration": duration},
                                     listened_seconds=0, deliveries={provider.provider: {"generation": provider.generation, "status": "ineligible"} for provider in accounts if provider.credentials})
            db.add(row)
        elif row.user_id != user.id or row.item_id != item_id or row.started_at != started_at:
            raise HTTPException(409, "Listening event identity conflict")
        row.listened_seconds = max(row.listened_seconds, listened_seconds)
        row.deliveries = {provider: {**entry, "status": "pending" if eligible and entry["status"] == "ineligible" else entry["status"]}
                          for provider, entry in (row.deliveries or {}).items()}
        row.updated_at = utcnow()
    for provider in row.deliveries:
        dispatch(db, identity, provider)
    return event_snapshot(db.get(ListeningPlayEvent, identity, populate_existing=True))


def dispatch(db: Session, event_id: str, provider: str) -> None:
    with write_transaction(db, name="listening_export_claim"):
        row = db.get(ListeningPlayEvent, event_id, populate_existing=True)
        entry = (row.deliveries or {}).get(provider) if row else None
        if not entry or entry["status"] != "pending":
            return
        account = db.get(ListeningExportConnection, (row.user_id, provider), populate_existing=True)
        if not account or not account.enabled or not account.credentials or account.generation != entry["generation"]:
            row.deliveries = {**row.deliveries, provider: {**entry, "status": "revoked"}}
            return
        credentials, metadata, timestamp = dict(account.credentials), dict(row.metadata_json), row.started_at
        row.deliveries = {**row.deliveries, provider: {**entry, "status": "dispatching"}}
    status, error = "sent", None
    try:
        if provider == "lastfm":
            result = lastfm(credentials, "track.scrobble", artist=metadata["artist"], track=metadata["title"], album=metadata["album"],
                            timestamp=str(timestamp), duration=str(int(metadata["duration"])))
            counts = result.get("scrobbles", {}).get("@attr", {})
            if str(counts.get("accepted")) != "1":
                if str(counts.get("ignored")) == "1":
                    status, error = "ignored", "Provider ignored this listen"
                else:
                    status, error = "uncertain", "Provider acknowledgement was incomplete"
        else:
            result = listenbrainz(credentials["token"], "1/submit-listens", body={"listen_type": "single", "payload": [{
                "listened_at": timestamp, "track_metadata": {"artist_name": metadata["artist"], "track_name": metadata["title"],
                "release_name": metadata["album"], "additional_info": {"media_player": "Lumina", "duration_ms": int(metadata["duration"] * 1000)}}}]})
            if result.get("status") != "ok":
                status, error = "uncertain", "Provider acknowledgement was incomplete"
    except ExportRejected as exc:
        status, error = ("pending" if exc.retryable else "failed"), str(exc)
    except (LocalAiError, ValueError, TypeError, KeyError, AttributeError):
        status, error = "uncertain", "Export outcome is unknown; it will not be sent again automatically"
    with write_transaction(db, name="listening_export_result"):
        row = db.get(ListeningPlayEvent, event_id, populate_existing=True)
        if row and row.deliveries.get(provider, {}).get("status") == "dispatching":
            row.deliveries = {**row.deliveries, provider: {**row.deliveries[provider], "status": status, "error": error}}


def reconcile(db: Session, user: User, event_id: str) -> dict:
    row = db.get(ListeningPlayEvent, event_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Listening event not found")
    for provider, entry in dict(row.deliveries or {}).items():
        if entry["status"] != "uncertain":
            continue
        account = db.get(ListeningExportConnection, (user.id, provider))
        if not account or not account.enabled or not account.credentials or account.generation != entry["generation"] or not account.username:
            continue
        try:
            metadata, stamp = row.metadata_json, row.started_at
            if provider == "lastfm":
                result = lastfm(account.credentials, "user.getRecentTracks", user=account.username, **{"from": str(stamp - 1), "to": str(stamp + 1), "limit": "200"})
                tracks = result.get("recenttracks", {}).get("track", [])
                tracks = [tracks] if isinstance(tracks, dict) else tracks
                found = any(str(track.get("date", {}).get("uts")) == str(stamp) and track.get("name") == metadata["title"]
                            and track.get("artist", {}).get("#text") == metadata["artist"] for track in tracks)
            else:
                from urllib.parse import quote
                result = listenbrainz(account.credentials["token"], f"1/user/{quote(account.username, safe='')}/listens",
                                      params={"min_ts": stamp - 1, "max_ts": stamp + 1, "count": 100})
                found = any(track.get("listened_at") == stamp and track.get("track_metadata", {}).get("track_name") == metadata["title"]
                            and track.get("track_metadata", {}).get("artist_name") == metadata["artist"] for track in result.get("payload", {}).get("listens", []))
        except (LocalAiError, ExportRejected, ValueError, TypeError, AttributeError):
            continue
        if found:
            with write_transaction(db, name="listening_export_reconcile"):
                db.refresh(row)
                if row.deliveries.get(provider, {}).get("status") == "uncertain":
                    row.deliveries = {**row.deliveries, provider: {**row.deliveries[provider], "status": "sent", "error": None}}
    return event_snapshot(row)


def recover_and_retry(*, recover: bool = False) -> None:
    with SessionLocal() as db:
        if recover:
            query = select(ListeningPlayEvent).where(or_(
                ListeningPlayEvent.deliveries["lastfm"]["status"].as_string() == "dispatching",
                ListeningPlayEvent.deliveries["listenbrainz"]["status"].as_string() == "dispatching"))
            while rows := list(db.scalars(query.limit(1000))):
                with write_transaction(db, name="listening_export_recover"):
                    for row in rows:
                        row.deliveries = {provider: {**entry, "status": "uncertain" if entry["status"] == "dispatching" else entry["status"]}
                                          for provider, entry in (row.deliveries or {}).items()}
        rows = list(db.scalars(select(ListeningPlayEvent).where(or_(
            ListeningPlayEvent.deliveries["lastfm"]["status"].as_string() == "pending",
            ListeningPlayEvent.deliveries["listenbrainz"]["status"].as_string() == "pending"
        )).order_by(ListeningPlayEvent.updated_at).limit(1000)))
        for row in rows:
            for provider in row.deliveries or {}:
                dispatch(db, row.id, provider)


async def run() -> None:
    await asyncio.to_thread(recover_and_retry, recover=True)
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(recover_and_retry)
