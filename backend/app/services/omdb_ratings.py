"""Durable, quota-limited OMDb score refreshes independent of TMDB metadata."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, case, delete, func, or_, select

from app.db import session_scope
from app.models import MediaTitle, OmdbRatingCache, OmdbRequestReservation, utcnow
from app.persistence import write_transaction
from app.services import omdb
from app.services.media_titles import apply_field
from app.services.yt_dlp_service import YtDlpService

DAILY_LIMIT = 950
BATCH_SIZE = 20
FRESH_FOR = timedelta(days=90)
FAILURE_DELAY = timedelta(hours=1)
LEASE_FOR = timedelta(minutes=10)
QUOTA_WINDOW = timedelta(hours=24)
PROVIDER_COOLDOWN = timedelta(hours=24)
SCORE_FIELDS = ("imdb_rating", "rotten_tomatoes_rating", "metacritic_rating")


def _imdb(title: MediaTitle) -> str | None:
    value = (title.provider_ids or {}).get("Imdb")
    return value if isinstance(value, str) and omdb.IMDB_ID.fullmatch(value) else None


def _protected(title: MediaTitle) -> bool:
    return all((title.field_sources or {}).get(field) in ("nfo", "user") for field in SCORE_FIELDS)


def _titles(db, imdb_id: str) -> list[MediaTitle]:  # noqa: ANN001
    stmt = select(MediaTitle).where(
        MediaTitle.type.in_(("movie", "series")), MediaTitle.locked.is_(False),
        func.json_extract(MediaTitle.provider_ids, "$.Imdb") == imdb_id,
    )
    return [title for title in db.scalars(stmt) if not _protected(title)]


def _apply(title: MediaTitle, scores: dict) -> None:
    if _imdb(title) is None:
        return
    for field in SCORE_FIELDS:
        value = scores.get(field)
        if type(value) in (int, float):
            apply_field(title, field, value, "omdb")


def _candidates(db, now: datetime) -> list[str]:  # noqa: ANN001
    """Filter and group before limiting, so cached/new duplicate rows cannot hide older work."""
    imdb = func.json_extract(MediaTitle.provider_ids, "$.Imdb")
    values = [func.json_extract(MediaTitle.metadata_json, f"$.{field}") for field in SCORE_FIELDS]
    protected = [func.coalesce(func.json_extract(MediaTitle.field_sources, f"$.{field}"), "").in_(("nfo", "user")) for field in SCORE_FIELDS]
    fresh = and_(OmdbRatingCache.fetched_at.is_not(None), OmdbRatingCache.fetched_at > now - FRESH_FOR)
    cached_changes = or_(*[
        and_(~locked, func.json_extract(OmdbRatingCache.scores, f"$.{field}").is_not(None),
             or_(value.is_(None), value != func.json_extract(OmdbRatingCache.scores, f"$.{field}")))
        for field, value, locked in zip(SCORE_FIELDS, values, protected)
    ])
    due = and_(
        or_(OmdbRatingCache.fetched_at.is_(None), OmdbRatingCache.fetched_at <= now - FRESH_FOR),
        or_(OmdbRatingCache.next_attempt_at.is_(None), OmdbRatingCache.next_attempt_at <= now),
        or_(OmdbRatingCache.lease_until.is_(None), OmdbRatingCache.lease_until <= now),
    )
    priority = case((and_(*(value.is_(None) for value in values)), 0),
                    (OmdbRatingCache.fetched_at.is_(None), 1), else_=2)
    stmt = (select(imdb).select_from(MediaTitle).outerjoin(OmdbRatingCache, OmdbRatingCache.imdb_id == imdb)
            .where(MediaTitle.type.in_(("movie", "series")), MediaTitle.locked.is_(False),
                   func.substr(imdb, 1, 2) == "tt", func.length(imdb).between(3, 12),
                   ~func.substr(imdb, 3).op("GLOB")("*[^0-9]*"),
                   ~and_(*protected), or_(due, and_(fresh, cached_changes)))
            .group_by(imdb).order_by(func.min(priority), func.max(func.coalesce(MediaTitle.added_at, MediaTitle.created_at)).desc(), imdb)
            .limit(BATCH_SIZE))
    return list(db.scalars(stmt))


def _cooldown(db, now: datetime) -> datetime | None:  # noqa: ANN001
    return db.scalar(select(func.max(OmdbRequestReservation.blocked_until)).where(OmdbRequestReservation.blocked_until > now))


def _reserve(db, imdb_id: str | None, now: datetime) -> str:
    """Reserve one persisted quota slot before any network I/O, or raise OmdbQuotaError."""
    db.execute(delete(OmdbRequestReservation).where(
        OmdbRequestReservation.requested_at <= now - QUOTA_WINDOW,
        (OmdbRequestReservation.blocked_until.is_(None) | (OmdbRequestReservation.blocked_until <= now)),
    ))
    if _cooldown(db, now) is not None:
        raise omdb.OmdbQuotaError("OMDb request limit reached")
    used = db.scalar(select(func.count()).select_from(OmdbRequestReservation).where(OmdbRequestReservation.requested_at > now - QUOTA_WINDOW)) or 0
    if used >= DAILY_LIMIT:
        raise omdb.OmdbQuotaError("OMDb request limit reached")
    db.add(OmdbRequestReservation(requested_at=now, imdb_id=imdb_id))
    return str(uuid.uuid4())


def _claim(imdb_id: str, now: datetime) -> str | None:
    with session_scope() as db, write_transaction(db, name="omdb_rating_claim"):
        cache = db.get(OmdbRatingCache, imdb_id)
        if cache is not None and cache.lease_until is not None and cache.lease_until > now:
            return None
        if cache is not None and ((cache.fetched_at is not None and cache.fetched_at + FRESH_FOR > now)
                                  or (cache.next_attempt_at is not None and cache.next_attempt_at > now)):
            return None
        token = _reserve(db, imdb_id, now)
        if cache is None:
            cache = OmdbRatingCache(imdb_id=imdb_id)
            db.add(cache)
        cache.lease_token, cache.lease_until = token, now + LEASE_FOR
        return token


def _finish(imdb_id: str, token: str, scores: dict[str, float] | None, error: Exception | None, now: datetime) -> None:
    with session_scope() as db, write_transaction(db, name="omdb_rating_finish"):
        cache = db.get(OmdbRatingCache, imdb_id)
        if cache is None or cache.lease_token != token:
            return
        cache.lease_token = cache.lease_until = None
        if error is None:
            cache.scores, cache.fetched_at, cache.next_attempt_at = dict(scores or {}), now, now + FRESH_FOR
            for title in _titles(db, imdb_id):
                _apply(title, cache.scores)
            return
        cache.next_attempt_at = now + FAILURE_DELAY
        if isinstance(error, omdb.OmdbQuotaError):
            reservation = db.scalar(select(OmdbRequestReservation).where(
                OmdbRequestReservation.imdb_id == imdb_id,
            ).order_by(OmdbRequestReservation.id.desc()).limit(1))
            if reservation is not None:
                reservation.blocked_until = now + PROVIDER_COOLDOWN


def run_due_batch(now: datetime | None = None) -> bool:
    """Refresh due IMDb identities. Database leases and quota reservations survive process crashes."""
    batch_now = now or utcnow()
    with session_scope() as db:
        record = YtDlpService(db).get_app_settings()
        client = omdb.client_for(record)
        if client is None:
            return False
        selected = _candidates(db, batch_now)
    quota_exhausted = False
    for imdb_id in selected:
        with session_scope() as db, write_transaction(db, name="omdb_rating_cached_apply"):
            cache = db.get(OmdbRatingCache, imdb_id)
            if cache is not None and cache.fetched_at is not None and cache.fetched_at + FRESH_FOR > (now or utcnow()):
                for title in _titles(db, imdb_id):
                    _apply(title, cache.scores)
                continue
        attempt_now = now or utcnow()
        try:
            token = _claim(imdb_id, attempt_now)
        except omdb.OmdbQuotaError:
            quota_exhausted = True
            break
        if token is None:
            continue
        try:
            scores = client.ratings(imdb_id)
        except omdb.OmdbError as exc:
            _finish(imdb_id, token, None, exc, now or utcnow())
        except Exception as exc:  # noqa: BLE001 - provider failures never stop the batch
            _finish(imdb_id, token, None, exc, now or utcnow())
        else:
            _finish(imdb_id, token, scores, None, now or utcnow())
    return not quota_exhausted and len(selected) == BATCH_SIZE


def test_key(client: omdb.OmdbClient, now: datetime | None = None) -> None:
    """Reserve the same shared quota used by workers before the admin key probe."""
    now = now or utcnow()
    with session_scope() as db, write_transaction(db, name="omdb_key_test_reserve"):
        _reserve(db, None, now)
    try:
        client.ratings("tt0133093")
    except omdb.OmdbQuotaError:
        with session_scope() as db, write_transaction(db, name="omdb_key_test_cooldown"):
            reservation = db.scalar(select(OmdbRequestReservation).where(OmdbRequestReservation.imdb_id.is_(None))
                                    .order_by(OmdbRequestReservation.id.desc()).limit(1))
            if reservation is not None:
                reservation.blocked_until = now + PROVIDER_COOLDOWN
        raise
