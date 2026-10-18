"""Bounded Lidarr v1 client and durable music-acquisition service."""
from __future__ import annotations

import json
import math
import hashlib
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MusicAcquisition,
                        ServiceConnector, StorageRoot, User, utcnow)
from app.persistence import write_transaction
from app.services.library import LibraryService
from app.services.local_ai import LocalAiError, bounded_json_request

TIMEOUT_SECONDS = 15.0
MAX_BYTES = 16 * 1024 * 1024
MAX_PAGED_RECORDS = 100_000
MAX_PAGED_BYTES = 64 * 1024 * 1024
OK = (200, 201, 202)
class LidarrError(RuntimeError):
    """Content-free connector error safe for a request ledger."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MusicRequestConflict(RuntimeError):
    def __init__(self, detail: str, request: MusicAcquisition | None = None) -> None:
        super().__init__(detail)
        self.detail, self.request = detail, request


class MusicRequestNotFound(LookupError):
    pass


class LidarrClient:
    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url, self.api_key = base_url, api_key or ""

    @classmethod
    def of(cls, connector: ServiceConnector) -> LidarrClient:
        return cls(connector.base_url, connector.api_key)

    def call(self, method: str, path: str, *, array: bool = False, **send: Any) -> Any:
        try:
            return bounded_json_request(
                self.base_url, method, f"api/v1/{path}", timeout=TIMEOUT_SECONDS, label="Lidarr", max_bytes=MAX_BYTES,
                array=array, ok=OK, headers={"X-Api-Key": self.api_key}, **send,
            )
        except LocalAiError as exc:
            raise LidarrError(str(exc), status=exc.status) from None

    def test(self) -> dict:
        status = self.call("GET", "system/status")
        roots = self.call("GET", "rootfolder", array=True)
        quality = self.call("GET", "qualityprofile", array=True)
        metadata = self.call("GET", "metadataprofile", array=True)
        if not isinstance(status, dict) or any(not isinstance(rows, list) for rows in (roots, quality, metadata)):
            raise LidarrError("Lidarr sent an unexpected reply")
        return {
            "version": _text(status.get("version"), 64) or "",
            "root_folders": [{"id": _number(row.get("id")), "path": _text(row.get("path"), 1024)} for row in roots if isinstance(row, dict)],
            "quality_profiles": [{"id": _number(row.get("id")), "name": _text(row.get("name"), 160)} for row in quality if isinstance(row, dict)],
            "metadata_profiles": [{"id": _number(row.get("id")), "name": _text(row.get("name"), 160)} for row in metadata if isinstance(row, dict)],
        }

    def paged(self, path: str, *, page_size: int = 1000) -> list[dict]:
        records: list[dict] = []
        page, total, bytes_seen = 1, None, 0
        seen: set[str] = set()
        while True:
            reply = self.call("GET", path, params={"page": page, "pageSize": page_size})
            if not isinstance(reply, dict):
                raise LidarrError("Lidarr sent malformed pagination")
            page_records, current_total = reply.get("records"), reply.get("totalRecords")
            if (not isinstance(page_records, list) or type(current_total) is not int or current_total < 0
                    or reply.get("page") != page or current_total > MAX_PAGED_RECORDS or (total is not None and total != current_total)
                    or len(page_records) > page_size or any(not isinstance(row, dict) for row in page_records)):
                raise LidarrError("Lidarr sent malformed pagination")
            total = current_total
            keys = {_page_key(row) for row in page_records}
            if len(keys) != len(page_records) or keys & seen:
                raise LidarrError("Lidarr sent malformed pagination")
            seen |= keys
            bytes_seen += sum(len(json.dumps(row, sort_keys=True, default=str, separators=(",", ":")).encode()) for row in page_records)
            if bytes_seen > MAX_PAGED_BYTES:
                raise LidarrError("Lidarr sent too many records")
            records.extend(page_records)
            if len(records) >= total:
                if len(records) != total:
                    raise LidarrError("Lidarr sent malformed pagination")
                return records
            if not page_records:
                raise LidarrError("Lidarr sent malformed pagination")
            page += 1


def _page_key(row: dict) -> str:
    value = row.get("id")
    return f"id:{value}" if isinstance(value, (str, int)) and not isinstance(value, bool) else json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))


def _text(value: Any, limit: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _number(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _connector(db: Session, connector_id: str) -> ServiceConnector:
    connector = db.get(ServiceConnector, connector_id)
    if connector is None or connector.kind != "lidarr":
        raise MusicRequestNotFound("Lidarr connector not found")
    if not connector.enabled or not connector.api_key:
        raise ValueError("Lidarr connector is disabled or missing credentials")
    return connector


def _identity(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _connection(connector: ServiceConnector) -> str:
    """Non-reversible connector binding retained only inside the operation result."""
    raw = json.dumps((connector.kind, connector.base_url, connector.api_key, connector.settings, connector.enabled), sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _same(row: MusicAcquisition, user: User, body: dict) -> bool:
    return (row.user_id == user.id and row.connector_id == body["connector_id"] and row.foreign_artist_id == body["foreign_artist_id"]
            and row.foreign_album_id == body["foreign_album_id"] and dict(row.args or {}) == _args(body))


def _args(body: dict) -> dict:
    return {"artist_name": _text(body.get("artist_name")) or "", "album_name": _text(body.get("album_name")) or ""}


def _serialize(row: MusicAcquisition, user: User | None = None, db: Session | None = None) -> dict:
    availability = _availability(db, user, row.foreign_album_id) if db is not None and user is not None else None
    result = {key: value for key, value in dict(row.result or {}).items() if not key.startswith("_")}
    return {
        "id": row.id, "connector_id": row.connector_id, "foreign_artist_id": row.foreign_artist_id,
        "foreign_album_id": row.foreign_album_id, "artist_name": row.artist_name, "album_name": row.album_name,
        "artist_id": row.artist_id, "album_id": row.album_id, "status": row.status, "result": result,
        "error": row.error, "created_at": _time(row.created_at), "updated_at": _time(row.updated_at),
        "availability": availability,
    }


def _time(value):  # noqa: ANN001, ANN202
    return value.isoformat() + "Z" if value else None


def available_tracks(db: Session, user: User, foreign_album_id: str, *, limit: int | None = None) -> tuple[MediaTitle | None, list[LibraryItem]]:
    """Lidarr albums identify release groups; local MusicBrainz album IDs identify editions."""
    album = db.scalar(select(MediaTitle).where(
        MediaTitle.type == "album", or_(
            func.json_extract(MediaTitle.metadata_json, "$.release_group_id") == foreign_album_id,
            func.json_extract(MediaTitle.provider_ids, "$.MusicBrainzReleaseGroup") == foreign_album_id,
        ), LibraryService.visible_title_predicate(user),
    ).order_by(MediaTitle.id))
    if album is None:
        return None, []
    query = select(LibraryItem).join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id) \
        .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id) \
        .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id).where(
            LibraryItem.title_id == album.id, LibraryItem.kind.in_(("track", "audio")), LibraryItem.status == "available",
            MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True), LibraryService.visible_predicate(user),
        ).order_by(LibraryItem.id).distinct()
    if limit is not None:
        query = query.limit(max(0, limit))
    tracks = list(db.scalars(query))
    return album, tracks


def _availability(db: Session, user: User, foreign_album_id: str) -> dict:
    album, tracks = available_tracks(db, user, foreign_album_id)
    return {"state": "playable", "library_album_id": album.id} if album is not None and tracks else {"state": "awaiting_import"}


def create(db: Session, user: User, body: dict) -> dict:
    """Create a member request. Approval is the only external-write path."""
    _connector(db, body["connector_id"])
    with write_transaction(db, name="music_request_create"):
        existing = db.get(MusicAcquisition, body["id"], populate_existing=True)
        if existing is not None:
            if _same(existing, user, body):
                return _serialize(existing, user, db)
            raise MusicRequestConflict("request_id_conflict")
        row = MusicAcquisition(id=body["id"], user_id=user.id, connector_id=body["connector_id"],
                               foreign_artist_id=body["foreign_artist_id"], foreign_album_id=body["foreign_album_id"],
                               artist_name=_args(body)["artist_name"] or "Unknown artist", album_name=_args(body)["album_name"] or "Unknown album",
                               status="pending_approval", args=_args(body), result={})
        db.add(row)
    return _serialize(row, user, db)


def list_requests(db: Session, user: User | None, *, page: int, page_size: int) -> dict:
    query = select(MusicAcquisition).order_by(MusicAcquisition.created_at.desc(), MusicAcquisition.id)
    if user is not None:
        query = query.where(MusicAcquisition.user_id == user.id)
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)))
    return {"items": [_serialize(row, user, db) for row in rows], "page": page, "page_size": page_size,
            "total": total, "total_pages": max(1, math.ceil(total / page_size))}


def _lookup(client: LidarrClient, row: MusicAcquisition) -> tuple[dict, dict]:
    artists = client.call("GET", "artist/lookup", array=True, params={"term": f"lidarr:{row.foreign_artist_id}"})
    albums = client.call("GET", "album/lookup", array=True, params={"term": f"lidarr:{row.foreign_album_id}"})
    artist = next((value for value in artists if isinstance(value, dict) and _identity(value.get("foreignArtistId")) == row.foreign_artist_id), None)
    album = next((value for value in albums if isinstance(value, dict) and _identity(value.get("foreignAlbumId")) == row.foreign_album_id), None)
    nested = album.get("artist") if isinstance(album, dict) else None
    if artist is None or album is None or not isinstance(nested, dict) or _identity(nested.get("foreignArtistId")) != row.foreign_artist_id:
        raise ValueError("Lidarr could not confirm this artist and album")
    return artist, album


def _managed(client: LidarrClient, row: MusicAcquisition) -> tuple[dict | None, dict | None]:
    artists = client.call("GET", "artist", array=True, params={"mbId": row.foreign_artist_id})
    artist = next((value for value in artists if isinstance(value, dict) and _identity(value.get("foreignArtistId")) == row.foreign_artist_id), None)
    albums = client.call("GET", "album", array=True, params={"foreignAlbumId": row.foreign_album_id})
    album = next((value for value in albums if isinstance(value, dict) and _identity(value.get("foreignAlbumId")) == row.foreign_album_id), None)
    return artist, album


def _settings(connector: ServiceConnector, client: LidarrClient) -> dict:
    settings = dict(connector.settings or {})
    root, quality, metadata = _text(settings.get("root_folder"), 1024), _number(settings.get("quality_profile_id")), _number(settings.get("metadata_profile_id"))
    if not root or not quality or not metadata:
        raise ValueError("Lidarr root and profiles must be configured")
    roots = client.call("GET", "rootfolder", array=True)
    profiles = client.call("GET", "qualityprofile", array=True)
    metadata_profiles = client.call("GET", "metadataprofile", array=True)
    if root not in {_text(value.get("path"), 1024) for value in roots if isinstance(value, dict)}:
        raise ValueError("Configured Lidarr root was not found")
    if quality not in {_number(value.get("id")) for value in profiles if isinstance(value, dict)}:
        raise ValueError("Configured Lidarr quality profile was not found")
    if metadata not in {_number(value.get("id")) for value in metadata_profiles if isinstance(value, dict)}:
        raise ValueError("Configured Lidarr metadata profile was not found")
    return {"root": root, "quality": quality, "metadata": metadata}


def _finish(db: Session, request_id: str, status: str, *, result: dict | None = None, error: str | None = None) -> MusicAcquisition:
    with write_transaction(db, name="music_request_finish"):
        row = db.get(MusicAcquisition, request_id, populate_existing=True)
        if row is None:
            raise MusicRequestNotFound("Music request not found")
        row.status, row.error, row.updated_at = status, _text(error, 256), utcnow()
        if result is not None:
            row.result = {**{key: value for key, value in dict(row.result or {}).items() if key.startswith("_")}, **result}
    return row


def approve(db: Session, request_id: str) -> dict:
    """Admin-only caller uses this to add/monitor/search a verified Lidarr album once."""
    with write_transaction(db, name="music_request_admit"):
        row = db.get(MusicAcquisition, request_id, populate_existing=True)
        if row is None:
            raise MusicRequestNotFound("Music request not found")
        if row.status != "pending_approval":
            return _serialize(row)
        connector = _connector(db, row.connector_id)
        prior = db.scalar(select(MusicAcquisition).where(
            MusicAcquisition.foreign_album_id == row.foreign_album_id,
            MusicAcquisition.id != row.id, MusicAcquisition.status.in_(("dispatching", "uncertain", "awaiting_import")),
        ).order_by(MusicAcquisition.created_at).limit(1))
        if prior is not None:
            if prior.connector_id == row.connector_id and prior.status == "awaiting_import" and (prior.result or {}).get("_connection") == _connection(connector):
                row.status, row.updated_at = "awaiting_import", utcnow()
                row.artist_id, row.album_id = prior.artist_id, prior.album_id
                row.artist_name, row.album_name = prior.artist_name, prior.album_name
                row.result = {**(prior.result or {}), "_shared_acquisition_id": prior.id}
                return _serialize(row)
            raise MusicRequestConflict("album_operation_pending")
        row.status, row.updated_at = "dispatching", utcnow()
        row.result = {"_connection": _connection(connector)}
    attempted = False
    accepted = False
    try:
        client = LidarrClient.of(connector)
        known_artist, known_album = _managed(client, row)
        artist, album = _lookup(client, row)
        artist_name, album_name = _text(artist.get("artistName")) or "Unknown artist", _text(album.get("title")) or "Unknown album"
        if known_artist is None:
            config = _settings(connector, client)
            artist_payload = {key: value for key, value in artist.items() if key not in {"id", "path", "folder", "images", "remotePoster"}}
            artist_payload.update(rootFolderPath=config["root"], qualityProfileId=config["quality"], metadataProfileId=config["metadata"], monitored=True,
                                  addOptions={"monitored": True, "monitor": "none", "albumsToMonitor": [row.foreign_album_id], "searchForMissingAlbums": False})
            attempted = True
            known_artist = client.call("POST", "artist", json=artist_payload)
            accepted = True
        artist_id = _number(known_artist.get("id")) if isinstance(known_artist, dict) else None
        if artist_id is None:
            raise ValueError("Lidarr did not return an artist id")
        if known_album is None:
            albums = client.call("GET", "album", array=True, params={"foreignAlbumId": row.foreign_album_id})
            known_album = next((value for value in albums if isinstance(value, dict) and _identity(value.get("foreignAlbumId")) == row.foreign_album_id), None)
        if known_album is None and known_artist is not None:
            album_payload = {key: value for key, value in album.items() if key not in {"id", "images", "remoteCover", "releases", "statistics"}}
            album_payload.update(artist=known_artist, monitored=True)
            attempted = True
            known_album = client.call("POST", "album", json=album_payload)
            accepted = True
        album_id = _number(known_album.get("id")) if isinstance(known_album, dict) else None
        if album_id is None:
            raise ValueError("Lidarr did not add the requested album")
        attempted = True
        client.call("PUT", "album/monitor", array=True, json={"albumIds": [album_id], "monitored": True})
        accepted = True  # this successful write makes later command failures non-retryable
        command = client.call("POST", "command", json={"name": "AlbumSearch", "albumIds": [album_id]})
        command_id = _number(command.get("id")) if isinstance(command, dict) else None
        if command_id is None:
            raise ValueError("Lidarr did not return a command receipt")
        receipt = {"artist_id": artist_id, "album_id": album_id, "command_id": command_id,
                   "import_state": "awaiting_import"}
        with write_transaction(db, name="music_request_receipt"):
            latest = db.get(MusicAcquisition, row.id, populate_existing=True)
            latest.artist_name, latest.album_name, latest.artist_id, latest.album_id = artist_name, album_name, artist_id, album_id
            latest.result, latest.updated_at = {**dict(latest.result or {}), **receipt}, utcnow()
        return _serialize(_finish(db, row.id, "awaiting_import", result=receipt))
    except LidarrError as exc:
        status = "uncertain" if accepted or attempted and not (exc.status is not None and 400 <= exc.status < 500) else "failed"
        return _serialize(_finish(db, row.id, status, error=str(exc)))
    except (KeyError, TypeError, ValueError) as exc:
        return _serialize(_finish(db, row.id, "uncertain" if attempted else "failed", error=str(exc)))


def reconcile(db: Session, request_id: str) -> dict:
    """Read-only reconciliation; never issues a new Lidarr write or search."""
    row = db.get(MusicAcquisition, request_id, populate_existing=True)
    if row is None:
        raise MusicRequestNotFound("Music request not found")
    if row.status not in ("dispatching", "uncertain"):
        return _serialize(row)
    try:
        connector = _connector(db, row.connector_id)
        if (row.result or {}).get("_connection") != _connection(connector):
            return _serialize(_finish(db, row.id, "uncertain", error="Connection settings changed"))
        client = LidarrClient.of(connector)
        artist, album = _managed(client, row)
        artist_id, album_id = _number(artist.get("id")) if artist else None, _number(album.get("id")) if album else None
        command_id = _number((row.result or {}).get("command_id"))
        # A monitored album proves only the monitor write.  Search has a durable
        # receipt only when its recorded command can be read as completed.
        command = client.call("GET", f"command/{command_id}") if command_id else None
        command_status = _text(command.get("status"), 32).lower() if isinstance(command, dict) and _number(command.get("id")) == command_id else ""
        if artist_id and album_id and bool(album.get("monitored")) and command_status in {"completed", "complete"}:
            result = {"artist_id": artist_id, "album_id": album_id, "command_id": command_id, "import_state": "awaiting_import"}
            return _serialize(_finish(db, row.id, "awaiting_import", result=result))
        return _serialize(_finish(db, row.id, "uncertain", error="Outcome needs Lidarr confirmation"))
    except (LidarrError, KeyError, TypeError, ValueError) as exc:
        return _serialize(_finish(db, row.id, "uncertain", error=str(exc)))


def inventory(db: Session, connector_id: str, *, page: int, page_size: int) -> dict:
    connector = _connector(db, connector_id)
    client = LidarrClient.of(connector)
    artists = client.call("GET", "artist", array=True)
    if not isinstance(artists, list) or len(artists) > MAX_PAGED_RECORDS or any(not isinstance(row, dict) for row in artists):
        raise LidarrError("Lidarr sent an unexpected reply")
    rows = []
    record_count = 0
    bytes_seen = 0
    for artist in artists:
        artist_id, foreign = _number(artist.get("id")), _identity(artist.get("foreignArtistId"))
        if not artist_id or not foreign:
            continue
        albums = client.call("GET", "album", array=True, params={"artistId": artist_id})
        if not isinstance(albums, list) or any(not isinstance(value, dict) for value in albums):
            raise LidarrError("Lidarr sent an unexpected reply")
        record_count += len(albums)
        bytes_seen += sum(len(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()) for value in albums)
        if record_count > MAX_PAGED_RECORDS or bytes_seen > MAX_PAGED_BYTES:
            raise LidarrError("Lidarr sent too many records")
        rows.append({"id": artist_id, "foreign_artist_id": foreign, "name": _text(artist.get("artistName")), "monitored": bool(artist.get("monitored")),
                     "albums": [{"id": _number(album.get("id")), "foreign_album_id": _identity(album.get("foreignAlbumId")),
                                 "title": _text(album.get("title")), "monitored": bool(album.get("monitored"))}
                                for album in albums if isinstance(album, dict) and _number(album.get("id")) and _identity(album.get("foreignAlbumId"))]})
    queue, history = client.paged("queue"), client.paged("history")
    total = len(rows)
    return {"items": rows[(page - 1) * page_size:page * page_size], "page": page, "page_size": page_size,
            "total": total, "total_pages": max(1, math.ceil(total / page_size)),
            "queue_count": len(queue), "history_count": len(history)}
