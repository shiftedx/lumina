"""Durable, allowlisted Sonarr/Radarr acquisition controls.

The arr APIs do not offer a caller supplied idempotency key.  An operation is
therefore recorded before dispatch and a transport failure stays ``uncertain``
until a read can prove its result.  This service deliberately knows only the
small set of controls exposed by the admin UI; it is not an arr API proxy.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ArrOperation, ArrServer, User, utcnow
from app.persistence import write_transaction
from app.services.requests.arr import ArrClient, ArrError

ACTIVE = ("pending", "dispatching", "uncertain")
RESOURCE = {"radarr": "movie", "sonarr": "series"}
RELEASE_FIELDS = {
    # Both official ReleaseResource schemas.  The arr, rather than a browser,
    # remains the authority for the URLs and protocol fields in this payload.
    "guid", "indexerId", "downloadUrl", "infoUrl", "magnetUrl", "protocol", "title", "quality", "languages", "size",
    "age", "ageHours", "ageMinutes", "seeders", "leechers", "releaseGroup", "releaseHash", "infoHash", "indexer",
    "customFormats", "customFormatScore", "approved", "downloadAllowed", "releaseWeight", "qualityWeight", "languageWeight",
    "sceneSource", "edition", "movieId", "mappedMovieId", "tmdbId", "imdbId", "seriesId", "episodeId", "episodeIds",
    "episodeNumbers", "seasonNumber", "seasonNumbers", "absoluteEpisodeNumbers", "mappedAbsoluteEpisodeNumbers",
    "mappedEpisodeInfo", "mappedEpisodeNumbers", "mappedSeasonNumber", "tvdbId", "tvRageId", "fullSeason", "special",
    "isDaily", "isAbsoluteNumbering", "isPossibleSpecialEpisode", "subGroup", "shouldOverride", "temporarilyRejected",
}


class OperationConflict(RuntimeError):
    """A durable operation has a different request or owns this resource."""

    def __init__(self, detail: str, operation: ArrOperation | None = None) -> None:
        super().__init__(detail)
        self.detail, self.operation = detail, operation


class OperationNotFound(LookupError):
    pass


def _text(value: Any, limit: int = 512) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _controlled(item: dict[str, Any], profiles: list[dict[str, Any]]) -> dict:
    """The only arr fields Lumina may revise, canonicalized for conflict hashes."""
    seasons = []
    for season in item.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        number = season.get("seasonNumber")
        if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= 1000:
            seasons.append({"season_number": number, "monitored": bool(season.get("monitored"))})
    seasons.sort(key=lambda season: season["season_number"])
    profile_id = _number(item.get("qualityProfileId"))
    profile = next((p for p in profiles if _number(p.get("id")) == profile_id), None)
    movie_file = item.get("movieFile") if isinstance(item.get("movieFile"), dict) else {}
    quality = movie_file.get("quality") if isinstance(movie_file.get("quality"), dict) else {}
    current = quality.get("quality") if isinstance(quality.get("quality"), dict) else quality
    current_name = _text(current.get("name")) if isinstance(current, dict) else _text(current)
    cutoff = None
    if isinstance(profile, dict):
        cutoff_id = _number(profile.get("cutoff"))
        for entry in profile.get("items") or []:
            if not isinstance(entry, dict):
                continue
            raw_quality = entry.get("quality")
            if isinstance(raw_quality, dict) and _number(raw_quality.get("id")) == cutoff_id:
                cutoff = _text(raw_quality.get("name"))
                break
    return {
        "monitored": bool(item.get("monitored")),
        "quality_profile_id": profile_id or 0,
        "seasons": seasons,
        "quality": {"current": current_name, "target": _text(profile.get("name")) if isinstance(profile, dict) else None, "cutoff": cutoff},
    }


def _revision(state: dict) -> str:
    controlled = {key: state[key] for key in ("monitored", "quality_profile_id", "seasons")}
    raw = json.dumps(controlled, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _profiles(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("arr sent an unexpected reply")
    profiles = []
    for profile in raw:
        if not isinstance(profile, dict):
            continue
        profile_id, name = _number(profile.get("id")), _text(profile.get("name"), 160)
        if profile_id and name:
            profiles.append({"id": profile_id, "name": name, "_raw": profile})
    return profiles


def read_state(server: ArrServer, upstream_id: int) -> tuple[dict, dict, list[dict]]:
    """Fetch the resource and usable quality profiles fresh from its owner."""
    client = ArrClient.of(server)
    resource = RESOURCE.get(server.kind)
    if resource is None:
        raise ValueError("Unsupported arr server")
    item = client.call("GET", f"{resource}/{upstream_id}")
    profiles = _profiles(client.call("GET", "qualityprofile", array=True))
    if not isinstance(item, dict):
        raise ValueError("arr sent an unexpected reply")
    state = _controlled(item, [p["_raw"] for p in profiles])
    public = {
        "server_id": server.id, "upstream_id": upstream_id, "revision": _revision(state),
        "monitored": state["monitored"], "quality_profile_id": state["quality_profile_id"],
        "seasons": state["seasons"], "profiles": [{"id": p["id"], "name": p["name"]} for p in profiles], "quality": state["quality"],
    }
    return public, item, profiles


def get_state(db: Session, server_id: str, upstream_id: int) -> dict:
    server = _server(db, server_id)
    return read_state(server, upstream_id)[0]


def _release_id(release: dict) -> str | None:
    guid, indexer = _text(release.get("guid"), 2048), release.get("indexerId")
    if guid is None or not isinstance(indexer, (str, int)) or isinstance(indexer, bool):
        return None
    return hashlib.sha256(json.dumps([guid, indexer], separators=(",", ":")).encode()).hexdigest()


def _release_row(release: dict) -> dict | None:
    release_id = _release_id(release)
    if release_id is None:
        return None
    languages: list[str] = []
    for language in release.get("languages") or []:
        name = _text(language.get("name")) if isinstance(language, dict) else _text(language)
        if name and name not in languages:
            languages.append(name)
    rejections: list[str] = []
    for rejection in release.get("rejections") or []:
        text = _text(rejection.get("reason")) if isinstance(rejection, dict) else _text(rejection)
        if text and text not in rejections:
            rejections.append(text)
    quality = release.get("quality")
    if isinstance(quality, dict):
        quality = quality.get("quality") if isinstance(quality.get("quality"), dict) else quality
    size = release.get("size")
    return {
        "release_id": release_id, "title": _text(release.get("title"), 512) or "Unknown release",
        "quality": _text(quality.get("name")) if isinstance(quality, dict) else _text(quality),
        "languages": languages[:20], "rejections": rejections[:20], "approved": bool(release.get("approved")),
        "size": size if isinstance(size, (int, float)) and not isinstance(size, bool) and size >= 0 else None,
    }


def _releases(server: ArrServer, upstream_id: int) -> list[tuple[dict, dict]]:
    key = "movieId" if server.kind == "radarr" else "seriesId"
    raw = ArrClient.of(server).call("GET", "release", array=True, params={key: upstream_id})
    if not isinstance(raw, list):
        raise ValueError("arr sent an unexpected reply")
    rows = []
    for release in raw:
        if isinstance(release, dict) and (row := _release_row(release)) is not None:
            rows.append((row, release))
    return rows


def get_releases(db: Session, server_id: str, upstream_id: int) -> dict:
    server = _server(db, server_id)
    state, _, _ = read_state(server, upstream_id)
    return {"items": [row for row, _ in _releases(server, upstream_id)], "revision": state["revision"]}


def _server(db: Session, server_id: str) -> ArrServer:
    server = db.get(ArrServer, server_id)
    if server is None:
        raise OperationNotFound("Server not found")
    if not server.enabled:
        raise ValueError("This arr server is disabled")
    if server.kind not in RESOURCE:
        raise ValueError("Unsupported arr server")
    return server


def _args(body: dict) -> dict:
    return {key: body[key] for key in ("monitored", "season_number", "profile_id", "release_id") if body.get(key) is not None}


def _same(existing: ArrOperation, *, actor: User, server_id: str, upstream_id: int, action: str, revision: str, args: dict) -> bool:
    return (existing.actor_id == actor.id and existing.server_id == server_id and existing.upstream_id == upstream_id
            and existing.action == action and existing.resource_revision == revision and dict(existing.args or {}) == args)


def _serialize(operation: ArrOperation) -> dict:
    return {
        "id": operation.id, "server_id": operation.server_id, "upstream_id": operation.upstream_id, "action": operation.action,
        "status": operation.status, "result": {key: value for key, value in dict(operation.result or {}).items() if not key.startswith("_")}, "error": operation.error,
        "created_at": _time(operation.created_at), "updated_at": _time(operation.updated_at),
    }


def _time(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _finish(db: Session, operation_id: str, status: str, *, result: dict | None = None, error: str | None = None) -> ArrOperation:
    with write_transaction(db, name="arr_operation_finish"):
        operation = db.get(ArrOperation, operation_id, populate_existing=True)
        if operation is None:
            raise OperationNotFound("Operation not found")
        operation.status, operation.error = status, _text(error, 256)
        if result is not None:
            operation.result = result
        operation.updated_at = utcnow()
    return operation


def _admit(db: Session, actor: User, server_id: str, upstream_id: int, body: dict, connection: str) -> tuple[ArrOperation, bool]:
    operation_id, action, revision, args = body["operation_id"], body["action"], body["base_revision"], _args(body)
    with write_transaction(db, name="arr_operation_admit"):
        existing = db.get(ArrOperation, operation_id, populate_existing=True)
        if existing is not None:
            if _same(existing, actor=actor, server_id=server_id, upstream_id=upstream_id, action=action, revision=revision, args=args):
                return existing, False
            raise OperationConflict("operation_id_conflict")
        blocked = db.scalars(select(ArrOperation).where(
            ArrOperation.server_id == server_id, ArrOperation.upstream_id == upstream_id, ArrOperation.status.in_(ACTIVE),
        )).first()
        if blocked is not None:
            raise OperationConflict("operation_in_progress", blocked)
        operation = ArrOperation(id=operation_id, actor_id=actor.id, server_id=server_id, upstream_id=upstream_id,
                                 action=action, args=args, resource_revision=revision, status="pending", result={"_connection": connection})
        db.add(operation)
    return operation, True


def _mark_dispatching(db: Session, operation_id: str, signature: tuple) -> bool:
    with write_transaction(db, name="arr_operation_dispatch"):
        operation = db.get(ArrOperation, operation_id, populate_existing=True)
        server = db.get(ArrServer, operation.server_id, populate_existing=True) if operation else None
        if operation is None or server is None:
            return False
        current = (server.kind, server.base_url, server.api_key, server.enabled)
        if current != signature:
            operation.status, operation.error, operation.updated_at = "failed", "Connection settings changed", utcnow()
            return False
        operation.status, operation.updated_at = "dispatching", utcnow()
        operation.result = {**dict(operation.result or {}), "_connection": _connection(signature)}
    return True


def _connection(signature: tuple) -> str:
    """A non-reversible binding for reconciliation, never part of the API response."""
    return hashlib.sha256("\0".join(str(part) for part in signature).encode()).hexdigest()


def _receipt(db: Session, operation_id: str, signature: tuple, receipt: dict) -> ArrOperation:
    with write_transaction(db, name="arr_operation_receipt"):
        operation = db.get(ArrOperation, operation_id, populate_existing=True)
        if operation is None:
            raise OperationNotFound("Operation not found")
        operation.result = {"_connection": _connection(signature), **receipt}
        operation.updated_at = utcnow()
    return operation


def _mutate(item: dict, action: str, args: dict, kind: str) -> dict:
    updated = dict(item)
    if action == "monitor":
        if "season_number" in args:
            if kind != "sonarr":
                raise ValueError("Seasons are a Sonarr feature")
            seasons = [dict(row) for row in item.get("seasons") or [] if isinstance(row, dict)]
            found = False
            for season in seasons:
                if season.get("seasonNumber") == args["season_number"]:
                    season["monitored"], found = args["monitored"], True
            if not found:
                raise ValueError("Season not found")
            updated["seasons"] = seasons
        else:
            updated["monitored"] = args["monitored"]
    elif action == "profile":
        updated["qualityProfileId"] = args["profile_id"]
    return updated


def _validate_current(action: str, args: dict, server: ArrServer, profiles: list[dict]) -> None:
    if action == "profile" and args.get("profile_id") not in {row["id"] for row in profiles}:
        raise ValueError("Quality profile not found")
    if action == "search" and args.get("season_number") is not None and server.kind != "sonarr":
        raise ValueError("Seasons are a Sonarr feature")


def _grab_payload(server: ArrServer, upstream_id: int, release_id: str) -> tuple[dict, dict]:
    """Refetch an approved release and retain only documented ReleaseResource fields."""
    selected = next(((row, raw) for row, raw in _releases(server, upstream_id) if row["release_id"] == release_id), None)
    if selected is None:
        raise ValueError("Release no longer available")
    row, raw = selected
    if not row["approved"] or row["rejections"]:
        raise ValueError("Release is not approved")
    return row, {key: raw[key] for key in RELEASE_FIELDS if key in raw}


def _dispatch(server: ArrServer, upstream_id: int, item: dict, action: str, args: dict, *, grab: dict | None = None) -> dict:
    client, resource = ArrClient.of(server), RESOURCE[server.kind]
    if action in ("monitor", "profile"):
        client.call("PUT", f"{resource}/{upstream_id}", json=_mutate(item, action, args, server.kind))
        return {}
    if action == "grab":
        if grab is None:
            raise ValueError("Release no longer available")
        reply = client.call("POST", "release", json=grab)
        return {"release_id": args["release_id"], **({"upstream_id": _number(reply.get("id"))} if isinstance(reply, dict) else {})}
    if action == "search":
        if server.kind == "radarr":
            command = {"name": "MoviesSearch", "movieIds": [upstream_id]}
        elif args.get("season_number") is not None:
            command = {"name": "SeasonSearch", "seriesId": upstream_id, "seasonNumber": args["season_number"]}
        else:
            command = {"name": "SeriesSearch", "seriesId": upstream_id}
    else:  # targeted rescan only
        command = {"name": "RescanMovie" if server.kind == "radarr" else "RescanSeries", f"{resource}Id": upstream_id}
    reply = client.call("POST", "command", json=command)
    return {"command_id": _number(reply.get("id"))} if isinstance(reply, dict) else {}


def operate(db: Session, actor: User, server_id: str, upstream_id: int, body: dict) -> dict:
    """Persist, reread, dispatch one allowlisted operation, then reread its state."""
    # Do not create a permanently pending ledger row for an invalid route.
    initial_server = _server(db, server_id)
    initial_signature = (initial_server.kind, initial_server.base_url, initial_server.api_key, initial_server.enabled)
    operation, claimed = _admit(db, actor, server_id, upstream_id, body, _connection(initial_signature))
    if not claimed:
        return _serialize(operation)
    attempted = False
    accepted = False
    try:
        server = _server(db, server_id)
        signature = (server.kind, server.base_url, server.api_key, server.enabled)
        state, item, profiles = read_state(server, upstream_id)
        if state["revision"] != body["base_revision"]:
            return _serialize(_finish(db, operation.id, "conflict", result={"state": state}, error="Resource changed"))
        args = _args(body)
        _validate_current(body["action"], args, server, profiles)
        release = _grab_payload(server, upstream_id, args["release_id"])[1] if body["action"] == "grab" else None
        if not _mark_dispatching(db, operation.id, signature):
            return _serialize(db.get(ArrOperation, operation.id, populate_existing=True))
        attempted = True
        dispatched = _dispatch(server, upstream_id, item, body["action"], args, grab=release)
        accepted = True
        _receipt(db, operation.id, signature, dispatched)
        refreshed = read_state(server, upstream_id)[0]
        return _serialize(_finish(db, operation.id, "succeeded", result={"state": refreshed, **dispatched}))
    except OperationNotFound as exc:
        return _serialize(_finish(db, operation.id, "failed", error=str(exc)))
    except ArrError as exc:
        status = "uncertain" if accepted else "failed" if not attempted or exc.status is not None and 400 <= exc.status < 500 else "uncertain"
        return _serialize(_finish(db, operation.id, status, error=str(exc)))
    except (KeyError, TypeError, ValueError) as exc:
        return _serialize(_finish(db, operation.id, "uncertain" if attempted else "failed", error=str(exc)))


def reconcile(db: Session, operation_id: str) -> dict:
    """Read-only reconciliation.  It never sends the operation again."""
    operation = db.get(ArrOperation, operation_id, populate_existing=True)
    if operation is None:
        raise OperationNotFound("Operation not found")
    if operation.status not in ACTIVE:
        return _serialize(operation)
    try:
        server = _server(db, operation.server_id)
        signature = (server.kind, server.base_url, server.api_key, server.enabled)
        if (operation.result or {}).get("_connection") != _connection(signature):
            return _serialize(_finish(db, operation.id, "uncertain", error="Connection settings changed"))
        state, _, _ = read_state(server, operation.upstream_id)
        args = dict(operation.args or {})
        monitored = next(
            (season["monitored"] for season in state["seasons"] if season["season_number"] == args.get("season_number")),
            state["monitored"],
        )
        proved = ((operation.action == "monitor" and monitored == args.get("monitored"))
                  or (operation.action == "profile" and state["quality_profile_id"] == args.get("profile_id")))
        receipt = dict(operation.result or {})
        command_id = _number(receipt.get("command_id"))
        if not proved and command_id:
            command = ArrClient.of(server).call("GET", f"command/{command_id}")
            proved = isinstance(command, dict) and _text(command.get("status"), 32) in {"completed", "complete"}
        status, error = ("succeeded", None) if proved else ("uncertain", "Outcome needs upstream confirmation")
        return _serialize(_finish(db, operation.id, status, result={"state": state}, error=error))
    except (ArrError, KeyError, TypeError, ValueError) as exc:
        return _serialize(_finish(db, operation.id, "uncertain", error=str(exc)))


def reconcile_pending() -> None:
    """Startup recovery: inspect unfinished work, never redeliver it blindly."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(ArrOperation.id).where(ArrOperation.status.in_(("pending", "dispatching")))).all())
        for operation_id in ids:
            try:
                reconcile(db, operation_id)
            except Exception:
                db.rollback()


def list_operations(db: Session, server_id: str | None, page: int, page_size: int) -> dict:
    query = select(ArrOperation).order_by(ArrOperation.created_at.desc(), ArrOperation.id)
    if server_id is not None:
        query = query.where(ArrOperation.server_id == server_id)
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)))
    return {"items": [_serialize(row) for row in rows], "page": page, "page_size": page_size,
            "total": total, "total_pages": max(1, (total + page_size - 1) // page_size)}
