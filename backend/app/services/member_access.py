"""Member access (ADR 0019): which Sections a member sees, rating ceilings, streaming limits, schedule and permissions.

One ``member_access`` row per restricted member; no row (and every admin) = all libraries, no limits. Library limits are
SQL inside ``LibraryService.visible_predicate`` (``item_clause``), so every surface that already routes through it obeys.
Ratings: a title's effective ``official_rating`` field (user > nfo > tmdb, media_titles.apply_field) is normalized into
``media_titles.rating``/``rating_rank`` on flush; seasons and episodes copy their series' (``_sync_ratings``).
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Integer, and_, case, event, func, inspect, literal, or_, select, text, update
from sqlalchemy.orm import Session, aliased, object_session

from app.db import session_scope
from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MemberAccess, ScreenTime, StorageRoot, User
from app.persistence import queue_after_commit, write_transaction

SECTIONS = ("movies", "shows", "anime", "music", "vault")  # plus "root:<storage_root_id>" for untitled files of a root
VIDEO_SECTIONS = ("movies", "shows", "anime")  # media_titles.category values
MUSIC_TYPES = ("album", "artist")
ROOT_PREFIX = "root:"
EXTERNAL_ORIGIN = "external_library"  # library.EXTERNAL_LIBRARY_ORIGIN (not imported: library imports this module)

MOVIE_RATINGS = {"G": 1, "PG": 2, "PG-13": 3, "R": 4, "NC-17": 5}
TV_RATINGS = {"TV-Y": 1, "TV-Y7": 2, "TV-G": 2, "TV-PG": 3, "TV-14": 4, "TV-MA": 5}
RATING_RANK = {**MOVIE_RATINGS, **TV_RATINGS}  # every canonical certificate (normalize_rating's names)
# A title's rank is on its own scale, so it compares with the matching ceiling: a movie on the film scale, a series (and
# its seasons and episodes) on the TV scale. A certificate from the other scale maps to its nearest equivalent, rounded
# strict (TV-Y/TV-Y7/TV-G = G, TV-PG = PG, TV-14 = PG-13, TV-MA = R; G = TV-G, PG = TV-PG, PG-13 = TV-14, R/NC-17 = TV-MA).
FILM_SCALE = {**MOVIE_RATINGS, "TV-Y": 1, "TV-Y7": 1, "TV-G": 1, "TV-PG": 2, "TV-14": 3, "TV-MA": 4}
TV_SCALE = {**TV_RATINGS, "G": 2, "PG": 3, "PG-13": 4, "R": 5, "NC-17": 5}
TV_TYPES = ("series", "season", "episode")
STREAMING_DEFAULTS = {"youtube": True, "twitch": True, "kick": True, "live": True, "open_search": True, "followed_only": False}
STREAMING_KINDS = ("youtube", "twitch", "kick", "live", "open_search")

# Longest alternatives first; \b keeps "RATED" from reading as R and "TV-Y7-FV" as TV-Y7.
_CERT = re.compile(r"(?<![A-Z0-9])(NC-?17|PG-?13|TV-?Y7|TV-?Y|TV-?G|TV-?PG|TV-?14|TV-?MA|PG|G|R)(?![A-Z0-9])")
_COUNTRY = re.compile(r"^([A-Z]{2})\s*:")


def normalize_rating(raw: object) -> str | None:
    """A US certificate from TMDB, NFO <mpaa>/<certification> or a hand edit: "Rated R", "US:PG-13", "tv14" -> canonical.

    "US:R / GB:15" takes the US part; another country's certificate ("GB:15", "DE:12") and anything unknown is unrated.
    """
    if not isinstance(raw, str):
        return None
    parts = [part.strip().upper() for part in raw.split("/") if part.strip()]
    for part in sorted(parts, key=lambda p: not p.startswith("US")):
        if (country := _COUNTRY.match(part)) and country[1] != "US":
            continue
        if match := _CERT.search(part.removeprefix("US:")):
            cert = match[1].replace("-", "")
            return next(name for name in RATING_RANK if name.replace("-", "") == cert)
    return None


def rating_rank(rating: str | None, type_: str = "movie") -> int | None:
    """``rating``'s rank on the scale a title of ``type_`` is compared on (film for movies, TV for series and below)."""
    return (TV_SCALE if type_ in TV_TYPES else FILM_SCALE).get(rating) if rating else None


def section_of_title(type_: str, category: str | None) -> str | None:
    """A title's Section: its category for movies and shows, music for albums and artists; None for a boxset."""
    return "music" if type_ in MUSIC_TYPES else category


@dataclass(frozen=True)
class EffectiveAccess:
    sections: frozenset[str] | None = None  # None = every section
    movie_rating_max: str | None = None
    tv_rating_max: str | None = None
    unrated: str = "allow"  # allow | hide
    streaming: dict[str, bool] = field(default_factory=lambda: dict(STREAMING_DEFAULTS))
    schedule: dict[str, list[list[str]]] | None = None
    daily_limit_minutes: int | None = None
    bonus_date: date | None = None
    bonus_minutes: int = 0
    can_download: bool = True

    @property
    def blocked_streaming(self) -> list[str]:
        return [kind for kind in STREAMING_KINDS if not self.streaming.get(kind, True)]


def limits_library(access: EffectiveAccess | None) -> bool:
    """Does this access hide any library item (Sections, a rating ceiling, hidden unrated)?"""
    return access is not None and (
        access.sections is not None or bool(access.movie_rating_max or access.tv_rating_max) or access.unrated == "hide"
    )


def snapshot_limits_library(user: User) -> bool:
    """``limits_library`` for a request snapshot carrying its access (``carry_access``)."""
    return user.role != "admin" and limits_library(user.__dict__.get(ACCESS_ATTR))


def effective(row: MemberAccess) -> EffectiveAccess:
    return EffectiveAccess(
        sections=None if row.sections is None else frozenset(row.sections),
        movie_rating_max=row.movie_rating_max, tv_rating_max=row.tv_rating_max, unrated=row.unrated or "allow",
        streaming={**STREAMING_DEFAULTS, **(row.streaming or {})}, schedule=row.schedule,
        daily_limit_minutes=row.daily_limit_minutes, bonus_date=row.bonus_date, bonus_minutes=row.bonus_minutes or 0,
        can_download=row.can_download,
    )


# Per session (= per request) memo, keyed with a process-wide generation that ``invalidate`` bumps.
# A long-lived session keeps the row it loaded; every request/task session is short.
_MEMO = "member_access"
_generation: dict[str, int] = {}


def invalidate(user_id: str) -> None:
    """Call after writing a member's row: later ``for_user`` lookups in any session reload it, and a running stream's
    next fetch re-checks viewing hours and the daily limit instead of trusting the last minute's allow."""
    from app.services import screen_time  # screen_time imports this module

    _generation[user_id] = _generation.get(user_id, 0) + 1
    _seen.clear()  # access saves are rare; a scoped art tag must not ride a cached allow past one
    screen_time.forget(user_id)


def generation(user_id: str) -> int:
    """Bumps whenever the member's access is saved: per-member caches key on it so tightened access shows at once."""
    return _generation.get(user_id, 0)


# ---- Member-scoped art tags --------------------------------------------------------------------------------------
# A restricted member's token-less art capability (Jellyfin image tags, /api/art signatures) carries an art scope: the
# 8 hex of the member's library limits + their id. The limits digest is durable (unlike ``generation``, which
# resets on restart), so a tag stays stable until the limits change, then never verifies again. Unrestricted members
# and admins get no scope: their tags stay the shared, cache-friendly ones.
ART_SCOPE = re.compile(r"[0-9a-f]{8}[0-9A-Za-z-]{1,36}")  # fullmatch; also keeps scopes URL-safe
SEEN_TTL_SECONDS = 60.0
_seen: dict[tuple[str, str], tuple[float, bool]] = {}  # (scope, entity id) -> (checked at, visible)


def art_scope(db: Session, user: User) -> str:
    """'' when ``user`` sees the whole library; else the scope their art tags and signatures are bound to."""
    access = for_user(db, user)
    if not limits_library(access):
        return ""
    limits = (sorted(access.sections) if access.sections is not None else None, access.movie_rating_max, access.tv_rating_max, access.unrated)
    return hashlib.sha256(repr(limits).encode()).hexdigest()[:8] + user.id


def scope_sees(db: Session, scope: str, entity_id: str, visible: Callable[[User], bool]) -> bool:
    """Whether a scoped tag still admits ``entity_id``: its member exists, still has exactly these limits, and
    ``visible(member)``. Cached SEEN_TTL_SECONDS (a rating edit shows within that); ``invalidate`` drops the cache."""
    key, now = (scope, entity_id), time.monotonic()
    hit = _seen.get(key)
    if hit is not None and now - hit[0] < SEEN_TTL_SECONDS:
        return hit[1]
    member = db.get(User, scope[8:]) if ART_SCOPE.fullmatch(scope) else None
    ok = member is not None and art_scope(db, member) == scope and visible(member)
    if len(_seen) >= 20_000:  # Wholesale clear bounds memory; an LRU if restricted households get huge
        _seen.clear()
    _seen[key] = (now, ok)
    return ok


def for_user(db: Session, user: User) -> EffectiveAccess | None:
    """The member's access for Python checks (streaming, schedule, /api/me/access); None = unrestricted (admins, no row).

    Library visibility does not use this: ``item_clause`` reads the row inside the query itself.
    """
    if user.role == "admin":
        return None
    memo = db.info.setdefault(_MEMO, {})
    key = (user.id, _generation.get(user.id, 0))
    if key not in memo:
        row = db.get(MemberAccess, user.id)
        memo[key] = effective(row) if row is not None else None
    return memo[key]


def today() -> date:
    """The household-local day screen time and bonuses count against (screen_time.local_day)."""
    from app.services import screen_time  # screen_time imports this module

    return screen_time.local_day()


def screen_time_seconds(db: Session, user_ids: list[str]) -> dict[str, int]:
    """Today's watched seconds per member (absent = 0)."""
    if not user_ids:
        return {}
    return dict(db.execute(select(ScreenTime.user_id, ScreenTime.seconds).where(ScreenTime.day == today(), ScreenTime.user_id.in_(user_ids))).all())


def admin_view(db: Session, user_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Per member: ``access`` (None = no row: every library, no limits) and ``screen_time_today_seconds``; two queries."""
    rows = {row.user_id: row for row in db.scalars(select(MemberAccess).where(MemberAccess.user_id.in_(user_ids)))} if user_ids else {}
    seconds = screen_time_seconds(db, user_ids)
    return {
        user_id: {
            "access": as_dict(effective(rows[user_id])) if user_id in rows else None,
            "screen_time_today_seconds": seconds.get(user_id, 0),
        }
        for user_id in user_ids
    }


def schedule_state(db: Session, user: User, access: EffectiveAccess | None) -> dict[str, Any] | None:
    """``/api/me/access``'s ``{allowed_now, code, until, remaining_minutes}`` ; None = no schedule or daily limit."""
    if access is None or (access.schedule is None and access.daily_limit_minutes is None):
        return None
    from app.services import screen_time  # screen_time imports this module

    return screen_time.watch_state(db, user)


# ---- Library limits as SQL (ADR 0019) --------------------------------------------------------------------------------

ACCESS_ATTR = "_member_access"  # a transient User snapshot (connected apps, request snapshots) may carry its access here


def _known_access(user: User, db: Session | None = None) -> tuple[bool, EffectiveAccess | None]:
    """(known, access) without new plumbing: a snapshot's carried access, else the attached user's (or ``db``'s) session."""
    if ACCESS_ATTR in user.__dict__:
        return True, user.__dict__[ACCESS_ATTR]
    if (db := object_session(user) or db) is not None:
        return True, for_user(db, user)
    return False, None


def carry_access(db: Session, snapshot: User, source: User) -> User:
    """Give a detached User snapshot its member's access, so its queries take the literal (fast) clause."""
    snapshot.__dict__[ACCESS_ATTR] = for_user(db, source)
    return snapshot


def carry_loaded_access(db: Session, snapshot: User, source: User, row: MemberAccess | None) -> User:
    """Carry a MemberAccess row already selected with authentication into this request only."""
    access = None if source.role == "admin" or row is None else effective(row)
    db.info.setdefault(_MEMO, {})[(source.id, generation(source.id))] = access
    snapshot.__dict__[ACCESS_ATTR] = access
    return snapshot


def _limits(user: User, db: Session | None = None):  # noqa: ANN202
    """(title_ok(t), untitled_ok(item)) builders for a limited member, or None (admin, or known to have no row).

    When the member's access is known (an attached user, or a snapshot carrying it), values are literals. Otherwise (a
    bare transient User) the same conditions read the member's row through uncorrelated subqueries, gated by NOT EXISTS
    so a member without a row passes, which is correct for any session.
    """
    if user.role == "admin":
        return None
    known, access = _known_access(user, db)
    if known and access is None:
        return None
    if known:
        sections = None if access.sections is None else sorted(access.sections)
        every = literal(sections is None)

        def has(value):  # noqa: ANN001, ANN202
            return value.in_(sections or [""])

        def ceiling(column, scale):  # noqa: ANN001, ANN202
            value = scale.get(getattr(access, column.key))
            return None if value is None else literal(value, type_=Integer)

        keep_unrated, gate = literal(access.unrated != "hide"), None
    else:
        row = MemberAccess.user_id == user.id

        def field(column):  # noqa: ANN001, ANN202
            return select(column).where(row).scalar_subquery()

        sections_json = field(MemberAccess.sections)
        granted = func.json_each(sections_json).table_valued("value")
        every = sections_json.is_(None)

        def has(value):  # noqa: ANN001, ANN202
            return value.in_(select(granted.c.value))

        def ceiling(column, scale):  # noqa: ANN001, ANN202
            return case(scale, value=field(column))

        keep_unrated = func.coalesce(field(MemberAccess.unrated), "allow") != "hide"
        gate = ~select(MemberAccess.user_id).where(row).exists()  # no row: no limits

    def title_ok(t):  # noqa: ANN001, ANN202
        rank = t.rating_rank

        def rated_ok(types, column, scale):  # noqa: ANN001, ANN202
            limit = ceiling(column, scale)  # None: no ceiling on this scale
            under = or_(rank.is_(None), rank <= limit) if limit is not None else literal(True)
            if limit is not None and gate is not None:  # the row form: a NULL ceiling column is no ceiling
                under = or_(under, limit.is_(None))
            return or_(t.type.notin_(types), and_(under, or_(rank.is_not(None), keep_unrated)))

        ok = and_(
            or_(every, has(t.category), and_(t.type.in_(MUSIC_TYPES), has(literal("music")))),
            rated_ok(("movie",), MemberAccess.movie_rating_max, MOVIE_RATINGS),
            rated_ok(TV_TYPES, MemberAccess.tv_rating_max, TV_RATINGS),
        )
        return ok if gate is None else or_(gate, ok)

    def untitled_ok(item):  # noqa: ANN001, ANN202
        imported = func.coalesce(item.extractor, "") == EXTERNAL_ORIGIN
        in_root = (
            select(LibraryItemArtifact.library_item_id)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .where(LibraryItemArtifact.library_item_id == item.id, has(literal(ROOT_PREFIX) + MediaArtifact.root_id))
            .correlate(item)
            .exists()
        )
        ok = and_(
            func.coalesce(item.title_id, "") == "",
            or_(every, and_(~imported, has(literal("vault"))), and_(imported, in_root)),
        )
        return ok if gate is None else or_(gate, ok)

    return title_ok, untitled_ok


def item_clause(user: User, item=LibraryItem, db: Session | None = None):  # noqa: ANN001, ANN201
    """SQL over the enclosing query's ``item``: may this member see the item's Section and rating? None = no limit.

    Titled items check their leaf title by primary key; untitled ones are the vault or, when imported, their storage
    root's ``root:<id>`` Section. Columns are wrapped so SQLite never drives a query from them. ``item`` may be an alias.
    """
    if (limits := _limits(user, db)) is None:
        return None
    title_ok, untitled_ok = limits
    t = aliased(MediaTitle)
    return or_(select(t.id).where(t.id == item.title_id, title_ok(t)).correlate(item).exists(), untitled_ok(item))


def title_clause(user: User):  # noqa: ANN201
    """A cheap pre-check on the enclosing query's ``MediaTitle`` itself: a movie, series, season or episode carries its
    own Section and (inherited) rating, so one failing here has no visible item below it. None = no limit."""
    if (limits := _limits(user)) is None:
        return None
    return or_(MediaTitle.type.notin_(("movie", *TV_TYPES)), limits[0](MediaTitle))


def allows_item(item: LibraryItem, user: User) -> bool:
    """Python twin of ``item_clause`` for one loaded item (``LibraryService.can_view``): the same SQL, by id."""
    db = object_session(item)
    if item.user_id == user.id or (clause := item_clause(user, db=db)) is None:
        return True
    if db is None:  # a transient item never reaches a member
        return False
    return bool(db.scalar(select(select(LibraryItem.id).where(LibraryItem.id == item.id, clause).exists())))


# ---- Ratings follow the effective official_rating; seasons and episodes copy their series' --------------------------

_INHERIT_SQL = (
    "UPDATE media_titles SET rating = s.rating, rating_rank = s.rating_rank FROM media_titles s"
    " WHERE media_titles.type = 'season' AND s.id = media_titles.parent_id AND s.id IN ({ids})"
    " AND (media_titles.rating IS NOT s.rating OR media_titles.rating_rank IS NOT s.rating_rank)",
    "UPDATE media_titles SET rating = se.rating, rating_rank = se.rating_rank FROM media_titles se"
    " WHERE media_titles.type = 'episode' AND se.id = media_titles.parent_id AND se.parent_id IN ({ids})"
    " AND (media_titles.rating IS NOT se.rating OR media_titles.rating_rank IS NOT se.rating_rank)",
)


def inherit_ratings(db: Session, series_ids: list[str]) -> None:
    """Copy each series' rating to its seasons, then their episodes (a scan's new episodes, a series' new rating)."""
    for start in range(0, len(series_ids), 500):
        chunk = series_ids[start:start + 500]
        params = {f"s{i}": series_id for i, series_id in enumerate(chunk)}
        ids = ", ".join(f":{name}" for name in params)
        for statement in _INHERIT_SQL:
            db.execute(text(statement.format(ids=ids)), params)


def sync_rating(title: MediaTitle) -> bool:
    """Normalize a movie's or series' effective official_rating into its rating columns; True when they changed."""
    rating = normalize_rating((title.metadata_json or {}).get("official_rating"))
    rank = rating_rank(rating, title.type)
    if (title.rating, title.rating_rank) == (rating, rank):
        return False
    title.rating, title.rating_rank = rating, rank
    return True


@event.listens_for(Session, "before_flush")
def _sync_ratings(session: Session, _context, _instances) -> None:  # noqa: ANN001
    """Every ORM write of official_rating (scan, refresh, edit, revert, undo) lands here; inheritance runs after flush."""
    series, seasons = session.info.setdefault("rating_series", set()), session.info.setdefault("rating_seasons", set())
    for title in (*session.new, *session.dirty):
        if not isinstance(title, MediaTitle):
            continue
        if title.type in ("movie", "series"):
            changed = title in session.new or inspect(title).attrs.metadata_json.history.has_changes()
            if changed and sync_rating(title) and title.type == "series":
                series.add(title.id)
        elif title in session.new and title.parent_id:  # a new season copies its series', a new episode its season's
            (series if title.type == "season" else seasons).add(title.parent_id)


@event.listens_for(Session, "after_flush")
def _inherit_after_flush(session: Session, _context) -> None:  # noqa: ANN001
    series, seasons = session.info.pop("rating_series", set()), session.info.pop("rating_seasons", set())
    if seasons:
        series |= set(session.scalars(select(MediaTitle.parent_id).where(MediaTitle.id.in_(seasons), MediaTitle.parent_id.is_not(None))))
    if series:
        inherit_ratings(session, sorted(series))


# ---- Admin payloads --------------------------------------------------------------------------------------------------

def as_dict(access: EffectiveAccess | None) -> dict[str, Any]:
    """The MemberAccess shape the admin API and ``/api/me/access`` return; None = no row (unrestricted)."""
    access = access or EffectiveAccess()
    return {
        "sections": None if access.sections is None else sorted(access.sections),
        "movie_rating_max": access.movie_rating_max, "tv_rating_max": access.tv_rating_max, "unrated": access.unrated,
        "streaming": dict(access.streaming), "schedule": access.schedule, "daily_limit_minutes": access.daily_limit_minutes,
        "bonus_date": access.bonus_date, "bonus_minutes": access.bonus_minutes, "can_download": access.can_download,
    }


def save(db: Session, user_id: str, values: dict[str, Any]) -> EffectiveAccess:
    """Set a member's row from validated ``values`` (schemas.MemberAccessIn); ``for_user`` reloads it once committed."""
    row = db.get(MemberAccess, user_id) or MemberAccess(user_id=user_id)
    for name, value in values.items():
        setattr(row, name, value)
    db.add(row)
    db.flush()
    queue_after_commit(db, lambda: invalidate(user_id))
    return effective(row)


# ---- "Fill in ratings" (admin task): normalize what titles already hold, queue TMDB for matched titles without one ----

_filling = threading.Lock()
FILL_BATCH = 500


def fill_ratings(now: datetime) -> dict[str, int]:
    """Backfill after the upgrade: every movie and series takes its rating from its stored official_rating (no network),
    seasons and episodes follow, and matched unlocked titles with no rating become due for the TMDB refresh queue, which
    paces itself (title_metadata.run_due_batch). Raises RuntimeError when a fill is already running."""
    if not _filling.acquire(blocking=False):
        raise RuntimeError("Ratings are already being filled in.")
    rated, after = 0, ""
    try:
        while True:
            with session_scope() as db, write_transaction(db, name="fill_ratings"):
                titles = list(db.scalars(
                    select(MediaTitle).where(MediaTitle.type.in_(("movie", "series")), MediaTitle.id > after)
                    .order_by(MediaTitle.id).limit(FILL_BATCH)
                ))
                rated += sum(sync_rating(title) for title in titles)
                db.flush()
                inherit_ratings(db, [title.id for title in titles if title.type == "series"])
            if len(titles) < FILL_BATCH:
                break
            after = titles[-1].id
        with session_scope() as db, write_transaction(db, name="fill_ratings_queue"):
            queued = db.execute(
                update(MediaTitle)
                .where(
                    MediaTitle.type.in_(("movie", "series")), MediaTitle.locked.is_(False), MediaTitle.rating.is_(None),
                    func.json_extract(MediaTitle.provider_ids, "$.Tmdb").is_not(None),
                    func.json_extract(MediaTitle.metadata_json, "$.official_rating").is_(None),
                    or_(MediaTitle.metadata_due_at.is_(None), MediaTitle.metadata_due_at > now),
                )
                .values(metadata_due_at=now)
                .execution_options(synchronize_session=False)
            ).rowcount
    finally:
        _filling.release()
    return {"rated": rated, "queued": queued}


def section_counts(db: Session) -> list[dict[str, Any]]:
    """Every Section with something in it: movies/shows/anime (walls' titles), music (albums), vault and roots (files)."""
    counts = dict(db.execute(
        select(MediaTitle.category, func.count()).where(MediaTitle.type.in_(("movie", "series"))).group_by(MediaTitle.category)
    ).all())
    rows = [{"id": s, "label": s.title(), "count": counts.get(s, 0)} for s in VIDEO_SECTIONS]
    rows.append({"id": "music", "label": "Music", "count": db.scalar(select(func.count()).where(MediaTitle.type == "album")) or 0})
    untitled = [LibraryItem.title_id.is_(None), LibraryItem.status != "missing"]
    rows.append({"id": "vault", "label": "Vault", "count": db.scalar(
        select(func.count()).select_from(LibraryItem).where(*untitled, func.coalesce(LibraryItem.extractor, "") != EXTERNAL_ORIGIN)
    ) or 0})
    for root_id, label, count in db.execute(
        select(MediaArtifact.root_id, StorageRoot.label, func.count(func.distinct(LibraryItem.id)))
        .select_from(LibraryItem)
        .join(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
        .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
        .join(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
        .where(*untitled, LibraryItem.extractor == EXTERNAL_ORIGIN)
        .group_by(MediaArtifact.root_id, StorageRoot.label)
    ).all():
        rows.append({"id": f"{ROOT_PREFIX}{root_id}", "label": label, "count": count})
    return rows

