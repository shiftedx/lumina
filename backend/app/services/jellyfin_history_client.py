"""Read Jellyfin watch history from the admin-configured Jellyfin server (ADR 0010 amendment).

The Jellyfin username and password are used for this one sign-in and are never stored or
logged, and the access token is signed out when the read ends, however it ends. Requests go only
to the admin's address through ``local_ai.bounded_json_request``: no redirects, no proxy env vars,
bounded in time and bytes. Pages and entries are capped too.
"""
from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config import APP_VERSION
from app.services.local_ai import LocalAiError, bounded_json_request

PAGE_SIZE = 500
# One import holds every entry in memory; the cap also keeps the matcher's IN lists under
# SQLite's 32,766-variable limit. Chunk the matcher's queries if households outgrow it.
MAX_ENTRIES = 20_000
MAX_PAGE_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 30.0
SERIES_BATCH = 100
TICKS_PER_SECOND = 10_000_000
PROVIDER_KEYS = {"tmdb": "Tmdb", "imdb": "Imdb", "tvdb": "Tvdb"}
# (Jellyfin filter, item types): played and resumable movies and episodes, then favorites including whole series.
PASSES = (("IsPlayed", "Movie,Episode"), ("IsResumable", "Movie,Episode"), ("IsFavorite", "Movie,Episode,Series"))
MAX_USERS = 50
NOT_ADMIN = "That Jellyfin account is not an administrator. Sign in with a Jellyfin administrator account."


class JellyfinImportError(RuntimeError):
    """A content-free failure that is safe to show the member: never a credential, token or request detail."""


@dataclass(frozen=True)
class JellyfinEntry:
    id: str
    type: str  # Movie | Episode | Series
    name: str
    path: str | None = None
    provider_ids: dict[str, str] = field(default_factory=dict)  # Tmdb | Imdb | Tvdb
    year: int | None = None
    series_id: str | None = None
    series_name: str | None = None
    season: int | None = None
    episode: int | None = None
    played: bool = False
    favorite: bool = False
    position_seconds: int = 0
    last_played: datetime | None = None  # naive UTC, like every Lumina timestamp

    @property
    def label(self) -> str:
        """How an unmatched entry is named back to the member: only what their own Jellyfin server said."""
        if self.type == "Episode" and self.season is not None and self.episode is not None:
            return f"{self.series_name or self.name} S{self.season:02d}E{self.episode:02d}"
        return f"{self.name} ({self.year})" if self.year else self.name


@dataclass(frozen=True)
class JellyfinHistory:
    entries: list[JellyfinEntry]
    series_provider_ids: dict[str, dict[str, str]]  # Jellyfin series id -> provider ids, for matching episodes


@dataclass(frozen=True)
class JellyfinUser:
    id: str
    name: str
    disabled: bool = False


def _too_many() -> JellyfinImportError:
    return JellyfinImportError(f"This Jellyfin history has more than {MAX_ENTRIES:,} items, more than Lumina imports at once.")


def _message(exc: LocalAiError) -> str:
    if exc.status in (401, 403):
        return "Jellyfin did not accept that username and password."
    if exc.status is not None and 300 <= exc.status < 400:
        return "The Jellyfin server address redirects somewhere else. Ask a vault owner to save the address it redirects to."
    if exc.status is not None:
        return f"The Jellyfin server answered with HTTP {exc.status}."
    return f"{exc}."  # "Jellyfin server endpoint is unreachable", "… timed out", "… response exceeded the size limit"


class JellyfinSession:
    def __init__(self, base_url: str) -> None:
        self.base_url, self.token = base_url, None
        self.device_id = f"lumina-import-{uuid.uuid4().hex}"

    def call(self, method: str, path: str, **send: Any) -> Any:
        header = f'MediaBrowser Client="Lumina", Device="Lumina history import", DeviceId="{self.device_id}", Version="{APP_VERSION}"'
        if self.token:
            header += f', Token="{self.token}"'
        try:
            return bounded_json_request(
                self.base_url, method, path, timeout=TIMEOUT_SECONDS, label="Jellyfin server", max_bytes=MAX_PAGE_BYTES,
                headers={"Authorization": header}, **send,
            )
        except LocalAiError as exc:
            raise JellyfinImportError(_message(exc)) from None  # no chained httpx error: it could carry request details


@contextlib.contextmanager
def _signed_in(base_url: str, username: str, password: str) -> Iterator[tuple[JellyfinSession, dict[str, Any]]]:
    """Signed in as ``username``; the token is signed out when the block ends, however it ends."""
    session = JellyfinSession(base_url)
    signed_in = session.call("POST", "Users/AuthenticateByName", json={"Username": username, "Pw": password})
    token, user = signed_in.get("AccessToken"), signed_in.get("User")
    if not (isinstance(token, str) and token.isalnum() and isinstance(user, dict) and _str(user.get("Id"))):
        raise JellyfinImportError("The Jellyfin server sent an unexpected sign-in reply.")
    session.token = token
    try:
        yield session, user
    finally:
        with contextlib.suppress(JellyfinImportError):
            session.call("POST", "Sessions/Logout")  # best effort; Jellyfin's 204 No Content lands here too


def fetch_history(base_url: str, username: str, password: str) -> JellyfinHistory:
    """A member's own history, read with their own sign-in."""
    with _signed_in(base_url, username, password) as (session, user):
        return read_history(session, user["Id"])


@contextlib.contextmanager
def admin_session(base_url: str, username: str, password: str) -> Iterator[tuple[JellyfinSession, str]]:
    """Signed in as a Jellyfin administrator, whose one token reads every user's history; refused for anyone else."""
    with _signed_in(base_url, username, password) as (session, user):
        policy = user.get("Policy")
        if not (isinstance(policy, dict) and policy.get("IsAdministrator") is True):
            raise JellyfinImportError(NOT_ADMIN)
        yield session, user["Id"]


def list_users(session: JellyfinSession) -> list[JellyfinUser]:
    users: list[JellyfinUser] = []
    for raw in session.call("GET", "Users", array=True):
        if isinstance(raw, dict) and (user_id := _str(raw.get("Id"))) and (name := _str(raw.get("Name"))):
            policy = raw.get("Policy") if isinstance(raw.get("Policy"), dict) else {}
            users.append(JellyfinUser(user_id, name, policy.get("IsDisabled") is True))
    if len(users) > MAX_USERS:
        raise JellyfinImportError(f"This Jellyfin server has more than {MAX_USERS} users, more than Lumina brings over at once.")
    return users


def read_history(session: JellyfinSession, user_id: str) -> JellyfinHistory:
    """``user_id``'s played, resumable and favorite entries, read with their own token or an administrator's."""
    entries: dict[str, JellyfinEntry] = {}
    for filter_name, types in PASSES:
        for raw in _pages(session, user_id, filter_name, types):
            if (entry := _entry(raw)) is not None:
                entries[entry.id] = entry  # an item in several passes carries the same data
            if len(entries) > MAX_ENTRIES:
                raise _too_many()
    series_ids = sorted({entry.series_id for entry in entries.values() if entry.series_id})
    series: dict[str, dict[str, str]] = {}
    for start in range(0, len(series_ids), SERIES_BATCH):
        page = session.call("GET", "Items", params={
            "userId": user_id, "Ids": ",".join(series_ids[start:start + SERIES_BATCH]),
            "Fields": "ProviderIds", "EnableImages": "false", "EnableUserData": "false",
        })
        for raw in _items(page):
            if isinstance(raw.get("Id"), str):
                series[raw["Id"]] = _provider_ids(raw)
    return JellyfinHistory(list(entries.values()), series)


def _pages(session: JellyfinSession, user_id: str, filter_name: str, types: str) -> Iterator[dict[str, Any]]:
    start = 0
    while True:
        page = session.call("GET", "Items", params={
            "userId": user_id, "Recursive": "true", "IncludeItemTypes": types, "Filters": filter_name,
            "Fields": "Path,ProviderIds", "EnableUserData": "true", "EnableImages": "false",
            "SortBy": "SortName", "StartIndex": start, "Limit": PAGE_SIZE,
        })
        items = _items(page)
        yield from items
        start += len(items)
        if start > MAX_ENTRIES:  # a server that never stops paging
            raise _too_many()
        total = page.get("TotalRecordCount")
        if len(items) < PAGE_SIZE or (isinstance(total, int) and start >= total):
            return


def _items(page: dict[str, Any]) -> list[dict[str, Any]]:
    items = page.get("Items")
    return [raw for raw in items if isinstance(raw, dict)] if isinstance(items, list) else []


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


def _provider_ids(raw: dict[str, Any]) -> dict[str, str]:
    ids = raw.get("ProviderIds")
    if not isinstance(ids, dict):
        return {}
    return {
        PROVIDER_KEYS[key.lower()]: str(value) for key, value in ids.items()
        if isinstance(key, str) and key.lower() in PROVIDER_KEYS and isinstance(value, (str, int)) and str(value)
    }


def _entry(raw: dict[str, Any]) -> JellyfinEntry | None:
    kind, item_id, name = raw.get("Type"), _str(raw.get("Id")), _str(raw.get("Name"))
    if kind not in ("Movie", "Episode", "Series") or item_id is None or name is None:
        return None
    data = raw.get("UserData") if isinstance(raw.get("UserData"), dict) else {}
    ticks = data.get("PlaybackPositionTicks")
    return JellyfinEntry(
        id=item_id, type=kind, name=name, path=_str(raw.get("Path")), provider_ids=_provider_ids(raw),
        year=_int(raw.get("ProductionYear")), series_id=_str(raw.get("SeriesId")), series_name=_str(raw.get("SeriesName")),
        season=_int(raw.get("ParentIndexNumber")), episode=_int(raw.get("IndexNumber")),
        played=data.get("Played") is True, favorite=data.get("IsFavorite") is True,
        position_seconds=ticks // TICKS_PER_SECOND if isinstance(ticks, int) and 0 < ticks < 10**15 else 0,
        last_played=_date(data.get("LastPlayedDate")),
    )
