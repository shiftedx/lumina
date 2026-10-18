"""Read-only infrastructure observations. Arr managers retain transfer control."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from uuid import UUID
import math
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ArrServer, MusicAcquisition, ServiceConnector, utcnow
from app.persistence import write_transaction
from app.services.local_ai import LocalAiError, bounded_json_request
from app.services.requests.arr import ArrClient, ArrError, MAX_BYTES

logger = logging.getLogger(__name__)


class ConnectorError(RuntimeError):
    pass


class ProwlarrClient(ArrClient):
    def call(self, method: str, path: str, *, array: bool = False, **send: Any) -> Any:
        if method != "GET":
            raise ConnectorError("Prowlarr is read-only")
        try:
            return bounded_json_request(self.base_url, "GET", f"api/v1/{path}", timeout=15, label="Prowlarr",
                                        max_bytes=MAX_BYTES, array=array, headers={"X-Api-Key": self.api_key}, **send)
        except LocalAiError as exc:
            raise ConnectorError(str(exc)) from None


def _rows(value: Any) -> list[dict]:
    if not isinstance(value, list) or len(value) > 100_000 or any(not isinstance(row, dict) for row in value):
        raise ConnectorError("Connector returned an incomplete resource list")
    return value


def _number(value: Any) -> float | int | None:
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _id(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _uuid(value: Any) -> str | None:
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _name(value: Any) -> str | None:
    return value[:300] if isinstance(value, str) else None


def _token(value: Any) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not all(c.isalnum() or c in "_/-" for c in value):
        return None
    return value


def _selected(row: dict, keys: tuple[str, ...]) -> dict:
    return {key: row[key] for key in keys if _number(row.get(key)) is not None}


def _raw(client: httpx.Client, method: str, path: str, **send: Any) -> bytes:
    try:
        with client.stream(method, path, **send) as response:
            if response.status_code != 200:
                raise ConnectorError(f"Connector endpoint returned HTTP {response.status_code}")
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_BYTES:
                    raise ConnectorError("Connector response exceeded the size limit")
        return bytes(payload)
    except httpx.HTTPError:
        raise ConnectorError("Connector endpoint is unreachable") from None


def _json(payload: bytes) -> Any:
    try:
        return json.loads(payload)
    except (ValueError, UnicodeError):
        raise ConnectorError("Connector returned malformed JSON") from None


def _qbit(connector: ServiceConnector) -> tuple[str, dict]:
    with httpx.Client(base_url=connector.base_url.rstrip("/") + "/", timeout=httpx.Timeout(15, connect=5),
                      follow_redirects=False, trust_env=False, headers={"Referer": connector.base_url}) as client:
        if not connector.username or not connector.password:
            raise ConnectorError("Configure qBittorrent WebUI username and password")
        reply = _raw(client, "POST", "api/v2/auth/login", data={"username": connector.username, "password": connector.password})
        if reply.strip() != b"Ok." or not client.cookies.get("SID"):
            raise ConnectorError("qBittorrent authentication failed")
        version = _raw(client, "GET", "api/v2/app/version").decode("utf-8")
        api = _raw(client, "GET", "api/v2/app/webapiVersion").decode("utf-8")
        value = _json(_raw(client, "GET", "api/v2/sync/maindata", params={"rid": 0}))
        if not isinstance(value, dict) or value.get("full_update") is not True or not isinstance(value.get("torrents", {}), dict):
            raise ConnectorError("qBittorrent did not return a complete snapshot")
        torrents = value.get("torrents", {})
        if len(torrents) > 100_000:
            raise ConnectorError("Connector returned too many transfers")
        transfers = []
        for identity, row in torrents.items():
            if not isinstance(identity, str) or len(identity) not in (40, 64) or any(c not in "0123456789abcdefABCDEF" for c in identity) or not isinstance(row, dict):
                raise ConnectorError("qBittorrent returned an invalid transfer identity")
            transfers.append({"id": identity.lower(), "state": _token(row.get("state")),
                              **_selected(row, ("progress", "size", "amount_left", "dlspeed", "upspeed", "eta"))})
    return _version(version), {"api_version": _version(api), "transfers": transfers, "capabilities": {"observe": True, "control": False}}


def _version(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 64 or not all(c.isalnum() or c in ".-_+ " for c in value):
        raise ConnectorError("Connector returned an invalid version")
    return value.strip()


def _nzbget(connector: ServiceConnector) -> tuple[str, dict]:
    def rpc(method: str) -> Any:
        try:
            result = bounded_json_request(connector.base_url, "POST", "jsonrpc", timeout=15, label="NZBGet", max_bytes=MAX_BYTES,
                                          auth=(connector.username or "", connector.password or ""),
                                          json={"method": method, "params": [], "id": 1})
        except LocalAiError as exc:
            raise ConnectorError(str(exc)) from None
        if result.get("error") or "result" not in result:
            raise ConnectorError("NZBGet rejected the read request")
        return result["result"]
    version, status, groups, history = rpc("version"), rpc("status"), _rows(rpc("listgroups")), _rows(rpc("history"))
    if not isinstance(status, dict):
        raise ConnectorError("NZBGet returned invalid status")
    transfers = []
    for source, rows in (("queue", groups), ("history", history)):
        for row in rows:
            identity = row.get("NZBID")
            if type(identity) is not int or identity <= 0:
                raise ConnectorError("NZBGet returned an invalid transfer identity")
            transfers.append({"id": str(identity), "source": source, "state": _token(row.get("Status")),
                              **_selected(row, ("FileSizeMB", "RemainingSizeMB", "DownloadedSizeMB", "Health", "ArticleCache"))})
    return _version(version), {"transfers": transfers, "health": _selected(status, ("DownloadRate", "RemainingSizeMB", "FreeDiskSpaceMB")),
                               "capabilities": {"observe": True, "control": False}}


def observe(connector: ServiceConnector) -> tuple[str, dict]:
    if connector.kind == "qbittorrent":
        return _qbit(connector)
    if connector.kind == "nzbget":
        return _nzbget(connector)
    if connector.kind == "lidarr":
        from app.services.lidarr import LidarrClient, LidarrError
        from app.services.requests.sync import RescanOffer, map_path, reconciliation_digest
        client = LidarrClient.of(connector)
        try:
            result = client.test()
            artists, albums = _rows(client.call("GET", "artist", array=True)), _rows(client.call("GET", "album", array=True))
            queue, history = client.paged("queue"), client.paged("history")
        except LidarrError as exc:
            raise ConnectorError(str(exc)) from None
        paths = []
        for artist in artists:
            stats = artist.get("statistics") if isinstance(artist.get("statistics"), dict) else {}
            if (_number(stats.get("trackFileCount")) or 0) > 0 and isinstance(artist.get("path"), str):
                local = map_path(artist["path"], (connector.settings or {}).get("path_mappings", []))
                if local:
                    artist_id = _id(artist.get("id"))
                    artist_albums = [] if artist_id is None else [album for album in albums if _id(album.get("artistId")) == artist_id]
                    album_ids = {identity for album in artist_albums if (identity := _id(album.get("id"))) is not None}
                    artist_history = [] if artist_id is None else [row for row in history
                                                                  if _id(row.get("artistId")) == artist_id
                                                                  or _id(row.get("albumId")) in album_ids]
                    paths.append(RescanOffer(local, reconciliation_digest(_lidarr_file_identity(artist, artist_albums, artist_history)),
                                             f"lidarr:{connector.id}", priority=0))
        return _version(result.pop("version")), {**result,
            "artists": [{"id": _id(row.get("id")), "foreign_artist_id": _uuid(row.get("foreignArtistId")), "name": _name(row.get("artistName")), "monitored": row.get("monitored") is True} for row in artists],
            "albums": [{"id": _id(row.get("id")), "foreign_album_id": _uuid(row.get("foreignAlbumId")), "artist_id": _id(row.get("artistId")), "title": _name(row.get("title")), "monitored": row.get("monitored") is True} for row in albums],
            "queue": [{"id": _id(row.get("id")), "album_id": _id(row.get("albumId")), "artist_id": _id(row.get("artistId")), "state": _token(row.get("status")), "failed": row.get("trackedDownloadState") in ("importFailed", "downloadFailed")} for row in queue],
            "history": [{"id": _id(row.get("id")), "album_id": _id(row.get("albumId")), "event_type": _token(row.get("eventType"))} for row in history],
            "_scan_paths": paths, "capabilities": {"observe": True, "approved_music_requests": True}}
    if connector.kind != "prowlarr":
        raise ConnectorError("Unsupported connector")
    client = ProwlarrClient("prowlarr", connector.base_url, connector.api_key)
    status = client.call("GET", "system/status")
    indexers = _rows(client.call("GET", "indexer", array=True))
    health = _rows(client.call("GET", "health", array=True))
    indexer_status = _rows(client.call("GET", "indexerstatus", array=True))
    stats = client.call("GET", "indexerstats")
    history = client.paged("history")
    return _version(status.get("version")), {
        "indexers": [{"id": _id(row.get("id")), "enabled": row.get("enable") is True, "protocol": _token(row.get("protocol"))} for row in indexers],
        "health": [{"type": _token(row.get("type")), "source": _token(row.get("source"))} for row in health],
        "indexer_status": [{"id": _id(row.get("id")), "initial_failure": bool(row.get("initialFailure")), "disabled": bool(row.get("disabledTill"))} for row in indexer_status],
        "stats": _selected(stats, ("totalGrabs", "totalQueries")),
        "history": [{"id": _id(row.get("id")), "indexer_id": _id(row.get("indexerId")), "event_type": _token(row.get("eventType"))} for row in history],
        "capabilities": {"observe": True, "control": False},
    }


def correlate(db: Session, connector: ServiceConnector, snapshot: dict) -> dict:
    """A configured manager/client-name binding and exact queue identity are required for a join."""
    transfers = snapshot.get("transfers", [])
    links: dict[str, list[dict]] = {}
    for binding in (connector.settings or {}).get("arr_clients", []):
        server = db.get(ArrServer, binding["server_id"])
        if server is None or not server.enabled:
            continue
        for record in ArrClient.of(server).queue():
            if record.get("downloadClient") != binding["client_name"]:
                continue
            identity = str(record.get("downloadId") or "").lower()
            item_id = record.get("movieId") if server.kind == "radarr" else record.get("seriesId")
            if type(item_id) is int and item_id > 0:
                links.setdefault(identity, []).append({"server_id": server.id, "upstream_id": item_id})
    return {**snapshot, "transfers": [{**row, "correlation": "matched" if len(links.get(row["id"], [])) == 1 else "ambiguous" if links.get(row["id"]) else "unmatched",
                                      "items": links.get(row["id"], [])} for row in transfers]}


_refresh_lock = threading.Lock()
_refreshing: set[str] = set()


class ConnectorChanged(ConnectorError):
    pass


def refresh(db: Session, connector_id: str) -> ServiceConnector:
    with _refresh_lock:
        if connector_id in _refreshing:
            raise ConnectorChanged("Connector refresh is already running")
        _refreshing.add(connector_id)
    try:
        row = db.get(ServiceConnector, connector_id, populate_existing=True)
        if row is None:
            raise ConnectorChanged("Connector no longer exists")
        signature = (row.kind, row.base_url, row.api_key, row.username, row.password, row.settings)
        paths = []
        try:
            version, snapshot = observe(row)
            paths = snapshot.pop("_scan_paths", [])
            if row.kind in ("qbittorrent", "nzbget"):
                snapshot = correlate(db, row, snapshot)
            error = None
        except (ConnectorError, ArrError) as exc:
            version, snapshot, error = None, None, str(exc)
        except (ValueError, TypeError, KeyError, UnicodeError):
            version, snapshot, error = None, None, "Connector returned an unexpected response"
        with write_transaction(db, name="service_connector_observe"):
            row = db.get(ServiceConnector, connector_id, populate_existing=True)
            if row is None or signature != (row.kind, row.base_url, row.api_key, row.username, row.password, row.settings):
                raise ConnectorChanged("Connection changed during refresh")
            if error is None:
                row.version, row.snapshot, row.last_ok_at, row.last_error, row.enabled = version, snapshot, utcnow(), None, True
                if row.kind == "lidarr":
                    _music_observations(db, row, snapshot)
            else:
                row.last_error = error
        # Reuse the scoped scanner's completed-first-import gate, mount checks and throttle.
        if error is None:
            from app.services.requests.sync import rescan_folders
            try:
                rescan_folders(paths)
            except Exception as exc:  # noqa: BLE001 - observation is durable; a later poll retries reconciliation
                logger.warning("Lidarr inventory reconciliation failed: %s", type(exc).__name__)
        return row
    finally:
        with _refresh_lock:
            _refreshing.discard(connector_id)


def _lidarr_file_identity(artist: dict[str, Any], albums: list[dict[str, Any]], history: list[dict[str, Any]]) -> dict[str, Any]:
    def stats(row: dict[str, Any]) -> dict[str, Any]:
        value = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
        return {key: value[key] for key in ("trackFileCount", "sizeOnDisk")
                if value.get(key) is not None}

    album_values = [{"id": row.get("id"), "foreignAlbumId": row.get("foreignAlbumId"), "statistics": stats(row)} for row in albums]
    history_values = [{key: row[key] for key in ("id", "eventType", "date", "artistId", "albumId", "trackFileId", "downloadId")
                       if row.get(key) is not None} for row in history]
    history_values = sorted(history_values, key=_history_newness, reverse=True)[:64]
    history_values.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"), default=str))
    return {"artist": {"id": artist.get("id"), "statistics": stats(artist)},
            "albums": sorted(album_values, key=lambda row: (row.get("id") or -1, row.get("foreignAlbumId") or "")),
            "history": history_values}


def _history_newness(value: dict[str, Any]) -> tuple[str, int, str]:
    identity = value.get("id")
    return str(value.get("date") or ""), identity if isinstance(identity, int) else -1, str(identity or "")


def _music_observations(db: Session, connector: ServiceConnector, snapshot: dict) -> None:
    for request in db.scalars(select(MusicAcquisition).where(MusicAcquisition.connector_id == connector.id)):
        if not request.album_id:
            continue
        album = next((album for album in snapshot.get("albums", []) if album.get("id") == request.album_id), None)
        if not album or album.get("foreign_album_id") != request.foreign_album_id:
            continue
        active = [row for row in snapshot.get("queue", []) if row.get("album_id") == request.album_id]
        failed = any(row.get("failed") for row in active)
        state = "failed" if failed else "downloading" if active else "awaiting_import"
        request.result = {**(request.result or {}), "observation": {"state": state, "observed_at": utcnow().isoformat() + "Z"}}


async def observer_loop() -> None:
    from app.db import SessionLocal
    while True:
        with SessionLocal() as db:
            identities = list(db.scalars(select(ServiceConnector.id).where(ServiceConnector.enabled.is_(True))))
        for identity in identities:
            def poll(connector_id: str = identity) -> None:
                with SessionLocal() as db:
                    try:
                        refresh(db, connector_id)
                    except ConnectorChanged:
                        pass
            await asyncio.to_thread(poll)
        await asyncio.sleep(60)
