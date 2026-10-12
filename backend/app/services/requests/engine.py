"""Media requests (ADR 0018): create, approve, decline, cancel, retry; policy and quota; dedupe and followers; dispatch.

Dispatch talks to Sonarr/Radarr outside any write transaction: the request is committed first, the arr is called, and
the outcome is written in a second transaction. An unreachable arr marks the request failed; it is never lost.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import ArrServer, MediaRequest, MediaRequestFollower, RequestPolicy, User, utcnow
from app.persistence import write_transaction
from app.services import tmdb, two_factor
from app.services.requests import notify, status as catalog_status
from app.services.requests.arr import ArrClient, ArrError, add_movie, add_series
from app.services.yt_dlp_service import YtDlpService

logger = logging.getLogger(__name__)
KINDS = ("movie", "show", "anime")
STATES = ("pending", "approved", "processing", "partially_available", "available", "declined", "failed")
OPEN = ("pending", "approved", "processing", "partially_available")
ACTIVE = ("approved", "processing", "partially_available")  # what the sync loop follows
DEFAULT_POLICY = {"can_request": True, "auto_approve": False, "quota_count": 10, "quota_days": 7}
POSTER_BASE = f"{tmdb.IMAGE_BASE}/w500"


class RequestError(Exception):
    """A refusal the router maps straight to ``{"detail": code}`` with ``status``."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


# ---- Policy and quota -------------------------------------------------------------------------------------------

def policy_dict(row: RequestPolicy | None, kind: str) -> dict[str, Any]:
    if row is None:
        return {"kind": kind, **DEFAULT_POLICY}
    return {"kind": kind, "can_request": row.can_request, "auto_approve": row.auto_approve,
            "quota_count": row.quota_count, "quota_days": row.quota_days}


def policy_for(db: Session, user_id: str, kind: str) -> dict[str, Any]:
    """The member's override for the kind, else the household default, else the built-in default."""
    rows = {r.user_id: r for r in db.scalars(select(RequestPolicy).where(
        RequestPolicy.kind == kind, or_(RequestPolicy.user_id == user_id, RequestPolicy.user_id.is_(None))))}
    return policy_dict(rows.get(user_id) or rows.get(None), kind)


def quota(db: Session, user: User, kind: str) -> dict[str, Any]:
    if two_factor.owner_capable(db, user):  # admins always may request and auto-approve
        return {"kind": kind, "can_request": True, "auto_approve": True, "limit": None, "days": None, "used": 0, "remaining": None}
    policy = policy_for(db, user.id, kind)
    limit, days = policy["quota_count"], policy["quota_days"]
    used = 0
    if limit is not None:
        query = select(func.count()).select_from(MediaRequest).where(
            MediaRequest.requested_by == user.id, MediaRequest.kind == kind, MediaRequest.status != "declined")
        if days:
            query = query.where(MediaRequest.created_at >= utcnow() - timedelta(days=days))
        used = int(db.scalar(query) or 0)
    return {"kind": kind, "can_request": policy["can_request"], "auto_approve": policy["auto_approve"], "limit": limit,
            "days": days, "used": used, "remaining": None if limit is None else max(0, limit - used)}


# ---- Create and decide ------------------------------------------------------------------------------------------

def _merge_seasons(old: Any, new: Any) -> Any:
    if old == "all" or new == "all":
        return "all"
    return sorted(set(old or []) | set(new or []))


def _covers(have: Any, want: Any) -> bool:
    return have == "all" or (want != "all" and set(want or []) <= set(have or []))


def _extra_seasons(have: Any, want: Any) -> Any:
    """The seasons ``want`` adds to ``have``; "all" stays "all" (the arr skips what it already has)."""
    return "all" if want == "all" else sorted(set(want) - set(have or []))


def anime_mapping(client: tmdb.TmdbClient, anilist_id: int) -> tuple[str, dict | None]:
    """(media_type, {tmdb_id, tvdb_id?} | None) for an AniList id, through the catalog's own cached mapping."""
    from app.services.requests import anilist, catalog

    try:
        media = catalog.cached(f"anilist:detail:{anilist_id}", catalog.TTL_DETAIL,
                               lambda: anilist.query(anilist.DETAIL, {"id": anilist_id})["Media"])
    except anilist.AniListError:
        raise RequestError(502, "anilist_unavailable") from None
    if not isinstance(media, dict):
        raise RequestError(404, "not_found")
    mapping = catalog.cached(f"map:{anilist_id}", catalog.TTL_MAPPING, lambda: catalog._map_to_tmdb(client, media))
    return ("movie" if media.get("format") == "MOVIE" else "tv"), mapping


def _resolve(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    """Every id, the kind and the media type from AniList/TMDB, never from the client; plus title, year and poster.

    Anime maps its AniList id through the catalog. A movie or show TMDB calls Japanese animation is anime, so anime
    policy and Sonarr's anime root apply however it was asked for. A TVDB id from the client only finds the TMDB entry.
    """
    from app.services.requests import catalog

    kind = body["kind"]
    client = tmdb.client_for(YtDlpService(db).get_app_settings())
    if client is None:
        raise RequestError(503, "tmdb_unavailable")
    anilist_id = body.get("anilist_id") if kind == "anime" else None
    try:
        if kind == "anime":
            if not anilist_id:
                raise RequestError(422, "anilist_id_required")
            media_type, mapping = anime_mapping(client, anilist_id)
            if not mapping or (media_type == "tv" and not mapping.get("tvdb_id")):
                raise RequestError(422, "unmapped")
            tmdb_id = mapping["tmdb_id"]
        else:
            media_type, tmdb_id = ("movie" if kind == "movie" else "tv"), body.get("tmdb_id")
            if not tmdb_id and media_type == "tv" and body.get("tvdb_id"):
                hits = client.find("tv", "tvdb_id", str(body["tvdb_id"]))
                tmdb_id = hits[0]["tmdb_id"] if hits else None
            if not tmdb_id:
                raise RequestError(422, "unmapped")
        if media_type == "movie":
            raw = client.get(f"/movie/{int(tmdb_id)}")
            name, date, tvdb_id = raw.get("title"), raw.get("release_date"), None
        else:
            raw = client.get(f"/tv/{int(tmdb_id)}", append_to_response="external_ids")
            name, date = raw.get("name"), raw.get("first_air_date")
            tvdb_id = tmdb._positive((raw.get("external_ids") or {}).get("tvdb_id"))
    except tmdb.TmdbNotFound:
        raise RequestError(404, "not_found") from None
    except tmdb.TmdbError:
        raise RequestError(502, "tmdb_unavailable") from None
    if not isinstance(name, str) or not name:
        raise RequestError(404, "not_found")
    if kind != "anime" and catalog._is_japanese_animation({**raw, "genre_ids": [g.get("id") for g in raw.get("genres") or [] if isinstance(g, dict)]}):
        kind = "anime"
    poster = raw.get("poster_path")
    return {
        "kind": kind, "media_type": media_type, "tmdb_id": int(tmdb_id), "tvdb_id": tvdb_id, "anilist_id": anilist_id,
        "title": name[:500], "year": int(date[:4]) if isinstance(date, str) and date[:4].isdigit() else None,
        "poster_url": f"{POSTER_BASE}{poster}" if isinstance(poster, str) and poster.startswith("/") else None,
    }


def create(db: Session, user: User, body: dict[str, Any]) -> tuple[MediaRequest, bool]:
    """(request, created). An open request for the same title gains the caller as a follower.

    New seasons merge into it only while it is pending or when the caller needs no approval; otherwise the extra seasons
    become the caller's own pending request, through their policy and quota like any other.
    """
    if not YtDlpService(db).get_app_settings().requests_enabled:
        raise RequestError(403, "requests_disabled")
    admin = two_factor.owner_capable(db, user)
    if not admin and not policy_for(db, user.id, body["kind"])["can_request"]:
        raise RequestError(403, "not_allowed")
    if body["kind"] == "anime" and not body.get("anilist_id"):
        raise RequestError(422, "anilist_id_required")
    found = _resolve(db, body)
    kind, media_type, tmdb_id = found["kind"], found["media_type"], found["tmdb_id"]
    if kind != body["kind"] and not admin and not policy_for(db, user.id, kind)["can_request"]:
        raise RequestError(403, "not_allowed")
    seasons = None if media_type == "movie" else body.get("seasons") or "all"
    language = body.get("language") if kind == "anime" and media_type == "tv" else None
    if kind == "anime" and media_type == "tv" and language not in ("dub", "sub"):
        raise RequestError(422, "language_required")
    no_approval = admin or policy_for(db, user.id, kind)["auto_approve"]

    same = list(db.scalars(select(MediaRequest).where(MediaRequest.media_type == media_type, MediaRequest.tmdb_id == tmdb_id)
                           .order_by(MediaRequest.created_at)))
    opens = [r for r in same if r.status in OPEN]
    covering = next((r for r in opens if media_type == "movie" or _covers(r.seasons, seasons)), None)
    if opens:
        # Follow the open request that already covers the ask; else, needing no approval, merge into the running one;
        # else into a pending one (its approval is still to come); else the extra seasons become the caller's own request.
        running = next((r for r in opens if r.status in ACTIVE), None) if no_approval else None
        target = covering or running or next((r for r in opens if r.status == "pending"), None) or (opens[0] if no_approval else None)
        with write_transaction(db, name="request_follow"):
            follow = target or opens[0]
            if follow.requested_by != user.id and db.get(MediaRequestFollower, (follow.id, user.id)) is None:
                db.add(MediaRequestFollower(request_id=follow.id, user_id=user.id))
            if target is not None and target is not covering:
                target.seasons = _merge_seasons(target.seasons, seasons)
        if target is not None:
            if target is not covering and target.status in ACTIVE:
                dispatch(db, target, followup=True)
            return target, False
        have: Any = []
        for r in opens:
            have = _merge_seasons(have, r.seasons)
        seasons = _extra_seasons(have, seasons)
    elif any(r.status == "available" and (media_type == "movie" or _covers(r.seasons, seasons)) for r in same) or (
        media_type == "movie" and catalog_status.library_title_for(db, user, "movie", tmdb_id, None)
    ):
        raise RequestError(409, "already_available")
    now = utcnow()
    req = MediaRequest(
        id=str(uuid.uuid4()), kind=kind, media_type=media_type, tmdb_id=tmdb_id, tvdb_id=found["tvdb_id"],
        anilist_id=found["anilist_id"], title=found["title"], year=found["year"], poster_url=found["poster_url"],
        seasons=seasons, language=language, status="approved" if no_approval else "pending", requested_by=user.id,
        decided_by=user.id if admin else None, decided_at=now if no_approval else None, created_at=now, updated_at=now,
    )
    with write_transaction(db, name="request_create"):
        if not admin and quota(db, user, kind)["remaining"] == 0:  # inside the writer slot: two clicks cannot both fit
            raise RequestError(429, "quota_exceeded")
        db.add(req)
        db.flush()
        if not no_approval:
            notify.request_event(db, req, "pending")
    if no_approval:
        dispatch(db, req)
    return req, True


def approve(db: Session, admin: User, req: MediaRequest, seasons: Any = None, language: str | None = None) -> None:
    if req.status != "pending":
        raise RequestError(409, "not_pending")
    with write_transaction(db, name="request_approve"):
        if seasons is not None and req.media_type == "tv":
            req.seasons = seasons
        if language in ("dub", "sub") and req.kind == "anime" and req.media_type == "tv":
            req.language = language
        req.status, req.decided_by, req.decided_at = "approved", admin.id, utcnow()
        notify.request_event(db, req, "approved")
    dispatch(db, req)


def decline(db: Session, admin: User, req: MediaRequest, reason: str | None) -> None:
    if req.status != "pending":
        raise RequestError(409, "not_pending")
    with write_transaction(db, name="request_decline"):
        req.status, req.decided_by, req.decided_at, req.decline_reason = "declined", admin.id, utcnow(), reason or None
        notify.request_event(db, req, "declined")


def retry(db: Session, req: MediaRequest) -> None:
    """A failed request, or an approved one that never reached an arr (a crash between commit and dispatch)."""
    if not (req.status == "failed" or req.status == "approved" and req.arr_server_id is None):
        raise RequestError(409, "not_failed")
    with write_transaction(db, name="request_retry"):
        req.status, req.failure_reason = "approved", None
    dispatch(db, req)


def cancel(db: Session, user: User, req: MediaRequest) -> None:
    """The requester while pending, or an admin at any time. Files already downloaded stay."""
    if not two_factor.owner_capable(db, user) and not (req.requested_by == user.id and req.status == "pending"):
        raise RequestError(403, "not_allowed")
    with write_transaction(db, name="request_cancel"):
        db.query(MediaRequestFollower).filter(MediaRequestFollower.request_id == req.id).delete(synchronize_session=False)
        db.delete(req)


# ---- Dispatch ---------------------------------------------------------------------------------------------------

def server_for(db: Session, media_type: str) -> ArrServer | None:
    kind = "radarr" if media_type == "movie" else "sonarr"
    return db.scalars(select(ArrServer).where(ArrServer.kind == kind, ArrServer.enabled.is_(True)).order_by(ArrServer.created_at)).first()


def dispatch(db: Session, req: MediaRequest, *, followup: bool = False) -> None:
    """Send an approved request to Radarr/Sonarr per the dispatch rules; failure → failed + failure_reason.

    A ``followup`` (extra seasons on a request already running) that fails is logged and leaves the request as it was.
    """
    server = server_for(db, req.media_type)
    item_id, error = None, None
    try:
        if server is None:
            raise ArrError(f"No {'Radarr' if req.media_type == 'movie' else 'Sonarr'} server is set up")
        client = ArrClient.of(server)
        if req.media_type == "movie":
            item_id = add_movie(client, server, req.tmdb_id)
        elif not req.tvdb_id:
            raise ArrError("This show has no TVDB id, which Sonarr needs")
        else:
            item_id = add_series(client, server, req.tvdb_id, req.seasons or "all", anime=req.kind == "anime", language=req.language)
    except ArrError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - an arr reply without the fields we read, or anything else: never lose the request
        logger.warning("Request dispatch failed: %s", type(exc).__name__)
        error = "The download server sent an unexpected reply"
    if error and followup:
        logger.warning("Extra seasons for a running request were not sent: %s", error)
        return
    with write_transaction(db, name="request_dispatch"):
        if error:
            req.status, req.failure_reason = "failed", error[:500]
            notify.request_event(db, req, "failed")
        else:
            req.arr_server_id, req.arr_item_id, req.failure_reason = server.id, item_id, None
    if not error:
        from app.services.requests import sync

        sync.wake()


# ---- Serialization ----------------------------------------------------------------------------------------------

def key_of(req: MediaRequest) -> str:
    return f"{req.kind}:{req.anilist_id if req.kind == 'anime' else req.tmdb_id}"


def serialize(db: Session, requests: list[MediaRequest], viewer: User | None = None) -> list[dict[str, Any]]:
    """MediaRequest, names batch-loaded. A ``viewer`` who cannot see the request's title
    (member access, ADR 0019) gets no title link, and an available request reads as approved: not available to them."""
    linked = [r.library_title_id for r in requests if r.library_title_id]
    hidden = set(linked) - catalog_status.visible_title_ids(db, viewer, linked) if viewer is not None else set()
    followers: dict[str, list[str]] = defaultdict(list)
    if requests:
        for f in db.scalars(select(MediaRequestFollower).where(MediaRequestFollower.request_id.in_([r.id for r in requests]))
                            .order_by(MediaRequestFollower.created_at)):
            followers[f.request_id].append(f.user_id)
    ids = {r.requested_by for r in requests} | {r.decided_by for r in requests if r.decided_by} | {u for v in followers.values() for u in v}
    names = dict(db.execute(select(User.id, User.display_name).where(User.id.in_(ids))).all()) if ids else {}
    person = lambda user_id: {"id": user_id, "name": names.get(user_id, "Former member")}  # noqa: E731
    items = []
    for r in requests:
        withheld = r.library_title_id in hidden
        item = {
            "id": r.id, "kind": r.kind, "media_type": r.media_type, "key": key_of(r), "tmdb_id": r.tmdb_id, "tvdb_id": r.tvdb_id,
            "anilist_id": r.anilist_id, "title": r.title, "year": r.year, "poster_url": r.poster_url, "seasons": r.seasons,
            "language": r.language, "status": "approved" if withheld and r.status in ("available", "partially_available") else r.status,
            "progress": r.progress, "requested_by": person(r.requested_by),
            "followers": [person(u) for u in followers[r.id]], "decided_by": person(r.decided_by) if r.decided_by else None,
            "decline_reason": r.decline_reason, "failure_reason": r.failure_reason, "library_title_id": None if withheld else r.library_title_id,
            "created_at": r.created_at.isoformat() + "Z", "updated_at": r.updated_at.isoformat() + "Z",
        }
        items.append({k: v for k, v in item.items() if v is not None or k in ("seasons", "language")})
    return items
