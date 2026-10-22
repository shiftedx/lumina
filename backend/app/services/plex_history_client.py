"""Read Plex movie and episode progress with request-scoped Plex tokens.

The configured PMS address is the only local-network destination. Plex.tv calls use fixed
paths on a fixed HTTPS origin. Both clients reject redirects and proxy environment variables,
bound time and response bytes, and never put tokens in URLs or errors.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from app.config import APP_VERSION
from app.services.jellyfin_history_client import (
    JellyfinEntry, JellyfinHistory, JellyfinImportError, JellyfinUser,
)
from app.services.local_ai import LocalAiError, bounded_json_request

PlexImportError = JellyfinImportError

PLEX_TV = "https://plex.tv"
PAGE_SIZE = 500
MAX_ENTRIES = 20_000
MAX_USERS = 50
MAX_VIDEO_SECTIONS = 100
MAX_SCAN_PAGES = 250
MAX_SHOW_ID_PARAM_BYTES = 4_000
MAX_PAGE_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 5.0
PROVIDER_KEYS = {"imdb": "Imdb", "tmdb": "Tmdb", "tvdb": "Tvdb"}
LEGACY_PROVIDERS = {"imdb": "imdb", "themoviedb": "tmdb", "thetvdb": "tvdb"}
TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{1,4096}\Z")  # legacy tokens and newer compact JWTs
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class _TokenMap(dict[str, str]):
    """A missing per-user token becomes that member's safe import error, not a KeyError."""

    def __missing__(self, key: str) -> str:
        raise PlexImportError("Plex did not provide a server token for this user.")

    def __repr__(self) -> str:
        return f"_TokenMap(users={list(self)!r})"


def _headers(token: str, *, json: bool = True) -> dict[str, str]:
    if not isinstance(token, str) or TOKEN_RE.fullmatch(token) is None:
        raise PlexImportError("That Plex token is not valid.")
    return {
        "Accept": "application/json" if json else "application/xml",
        "X-Plex-Client-Identifier": "lumina-history-import",
        "X-Plex-Product": "Lumina",
        "X-Plex-Version": APP_VERSION,
        "X-Plex-Token": token,
    }


def _message(exc: LocalAiError) -> str:
    if exc.status in (401, 403):
        return "Plex did not accept that token."
    if exc.status is not None and 300 <= exc.status < 400:
        return "The Plex server address redirects somewhere else. Ask a vault owner to save the address it redirects to."
    if exc.status is not None:
        return f"The Plex server answered with HTTP {exc.status}."
    return f"{exc}."


def _pms(base_url: str, token: str, path: str, **send: Any) -> dict[str, Any]:
    headers = _headers(token)
    extra_headers = send.pop("headers", None)
    if isinstance(extra_headers, dict):
        headers.update(extra_headers)
    try:
        return bounded_json_request(
            base_url, "GET", path, timeout=TIMEOUT_SECONDS, label="Plex server", max_bytes=MAX_PAGE_BYTES,
            headers=headers, **send,
        )
    except LocalAiError as exc:
        raise PlexImportError(_message(exc)) from None


def _cloud_xml(path: str, token: str) -> ElementTree.Element:
    """One bounded entity-free XML request to the fixed Plex.tv origin."""
    try:
        with httpx.Client(
            base_url=PLEX_TV + "/",
            timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            follow_redirects=False,
            trust_env=False,
        ) as client, client.stream("GET", path.lstrip("/"), headers=_headers(token, json=False)) as response:
            if response.status_code not in (200,):
                if response.status_code in (401, 403):
                    raise PlexImportError("Plex did not accept that owner token.")
                if 300 <= response.status_code < 400:
                    raise PlexImportError("Plex.tv returned an unexpected redirect.")
                raise PlexImportError(f"Plex.tv answered with HTTP {response.status_code}.")
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_PAGE_BYTES:
                    raise PlexImportError("Plex.tv sent a response larger than Lumina accepts.")
        raw = bytes(payload)
        if b"\x00" in raw:
            raise PlexImportError("Plex.tv sent an unexpected XML reply.")
        text = raw.decode("utf-8")
        lowered = text.lower()
        if "<!doctype" in lowered or "<!entity" in lowered:
            raise PlexImportError("Plex.tv sent an unexpected XML reply.")
        return ElementTree.fromstring(text)
    except PlexImportError:
        raise
    except httpx.TimeoutException:
        raise PlexImportError("Plex.tv timed out.") from None
    except httpx.HTTPError:
        raise PlexImportError("Plex.tv is unreachable.") from None
    except (ElementTree.ParseError, ValueError):
        raise PlexImportError("Plex.tv sent an unexpected XML reply.") from None


def fetch_history(base_url: str, token: str) -> JellyfinHistory:
    """Read one Plex user's watched and resumable movies and episodes."""
    return read_history(base_url, token)


def household(base_url: str, token: str) -> tuple[str, list[JellyfinUser], dict[str, str]]:
    """Return the owner and users who have access to this PMS, plus their scoped server tokens."""
    root = _container(_pms(base_url, token, ""))
    machine_id = _text(root.get("machineIdentifier"))
    if not _truthy(root.get("myPlex")) or machine_id is None or IDENTIFIER_RE.fullmatch(machine_id) is None:
        raise PlexImportError("That Plex server is not linked to a Plex account.")

    profile = _cloud_xml("api/v2/user", token)
    owner_id = _attribute(profile, "id")
    owner_name = _attribute(profile, "friendlyName", "title", "username", "email")
    if owner_id is None or owner_name is None:
        raise PlexImportError("Plex.tv sent an unexpected account reply.")

    # A shared user's token can read the PMS root too. Plex.tv's owned flag is the owner proof.
    resources = _cloud_xml("api/v2/resources?includeHttps=1&includeRelay=1&includeIPv6=1", token)
    owned = any(
        element.attrib.get("clientIdentifier") == machine_id and _truthy(element.attrib.get("owned"))
        and "server" in (element.attrib.get("provides") or "").split(",")
        for element in resources.iter()
    )
    if not owned:
        raise PlexImportError("That Plex token does not belong to this server's owner.")

    raw_users = _cloud_xml("api/users/", token)
    shares = _cloud_xml(f"api/servers/{quote(machine_id, safe='')}/shared_servers", token)
    tokens: _TokenMap = _TokenMap({owner_id: token})
    shared_ids: set[str] = set()
    for element in shares.iter():
        user_id = _attribute(element, "userID", "userId")
        if user_id is None:
            continue
        shared_ids.add(user_id)
        access_token = _attribute(element, "accessToken")
        if access_token is not None and TOKEN_RE.fullmatch(access_token):
            tokens[user_id] = access_token

    users_by_id: dict[str, JellyfinUser] = {}
    for element in raw_users.iter():
        if _tag(element) != "user":
            continue
        user_id = _attribute(element, "id")
        name = _attribute(element, "username", "title", "email")
        if user_id is None or name is None:
            continue
        target_share = any(
            _tag(child) == "server" and child.attrib.get("machineIdentifier") == machine_id
            for child in element.iter()
        )
        if user_id in shared_ids or target_share:
            users_by_id[user_id] = JellyfinUser(user_id, name)

    owner = JellyfinUser(owner_id, owner_name)
    users = [owner, *sorted(
        (user for user_id, user in users_by_id.items() if user_id != owner_id),
        key=lambda user: user.name.casefold(),
    )]
    if len(users) > MAX_USERS:
        raise PlexImportError(f"This Plex server has more than {MAX_USERS} users, more than Lumina brings over at once.")
    return owner_id, users, tokens


def read_history(base_url: str, token: str) -> JellyfinHistory:
    """Read progress in the security context of ``token``; Plex view state is token-specific."""
    sections = _container(_pms(base_url, token, "library/sections")).get("Directory")
    video_sections: list[tuple[str, str]] = []
    for raw in _objects(sections):
        key, kind = _text(raw.get("key")), _text(raw.get("type"))
        if key is not None and kind in ("movie", "show") and key.isdigit():
            video_sections.append((key, kind))
    if len(video_sections) > MAX_VIDEO_SECTIONS:
        raise PlexImportError(
            f"This Plex server has more than {MAX_VIDEO_SECTIONS} video libraries, more than Lumina scans at once."
        )

    entries: dict[str, JellyfinEntry] = {}
    series_provider_ids: dict[str, dict[str, str]] = {}
    needed_shows: dict[str, set[str]] = {}
    pages = [0]
    for section_key, kind in video_sections:
        media_type = 1 if kind == "movie" else 4
        for media_filter in ({"unwatched": 0}, {"inProgress": 1}):
            for raw in _pages(base_url, token, section_key, media_type, pages, params=media_filter):
                entry = _entry(raw)
                if entry is None or not (entry.played or entry.position_seconds > 0):
                    continue
                entries.setdefault(entry.id, entry)  # an item may match both filtered passes
                if len(entries) > MAX_ENTRIES:
                    raise PlexImportError(
                        f"This Plex history has more than {MAX_ENTRIES:,} entries, more than Lumina imports at once."
                    )
                if kind == "show" and entry.series_id is not None and IDENTIFIER_RE.fullmatch(entry.series_id):
                    needed_shows.setdefault(section_key, set()).add(entry.series_id)

    metadata_count = 0
    for section_key, show_ids in needed_shows.items():
        for batch in _show_id_batches(show_ids):
            for raw in _pages(base_url, token, section_key, 2, pages, params={"id": batch}):
                metadata_count += 1
                if metadata_count > MAX_ENTRIES:
                    raise PlexImportError(
                        f"The Plex server returned more than {MAX_ENTRIES:,} referenced shows, more than Lumina accepts."
                    )
                series_id = _text(raw.get("ratingKey"))
                if series_id in show_ids:
                    series_provider_ids[series_id] = _provider_ids(raw)
    return JellyfinHistory(list(entries.values()), series_provider_ids)


def _pages(
    base_url: str, token: str, section_key: str, media_type: int, pages: list[int], *,
    params: dict[str, object] | None = None,
) -> Iterator[dict[str, Any]]:
    start = 0
    safe_key = quote(section_key, safe="")
    while True:
        pages[0] += 1
        if pages[0] > MAX_SCAN_PAGES:
            raise PlexImportError("The Plex server needs more pages than Lumina scans in one import.")
        page = _container(_pms(
            base_url, token, f"library/sections/{safe_key}/all",
            params={"type": media_type, "includeGuids": 1, **(params or {})},
            headers={
                "X-Plex-Container-Start": str(start),
                "X-Plex-Container-Size": str(PAGE_SIZE),
            },
        ))
        items = _objects(page.get("Metadata"))
        yield from items
        total = _integer(page.get("totalSize"))
        next_start = start + len(items)
        if not items or (total is not None and next_start >= total):
            return
        if total is None and len(items) < PAGE_SIZE:
            return
        if next_start <= start:
            raise PlexImportError("The Plex server sent an unexpected paged reply.")
        start = next_start


def _show_id_batches(show_ids: set[str]) -> Iterator[str]:
    """Keep the caller-controlled CSV filter short even when rating keys are unusually long."""
    batch: list[str] = []
    size = 0
    for show_id in sorted(show_ids):
        added = len(show_id) + (1 if batch else 0)
        if batch and size + added > MAX_SHOW_ID_PARAM_BYTES:
            yield ",".join(batch)
            batch, size = [], 0
            added = len(show_id)
        batch.append(show_id)
        size += added
    if batch:
        yield ",".join(batch)


def _container(payload: dict[str, Any]) -> dict[str, Any]:
    container = payload.get("MediaContainer")
    if not isinstance(container, dict):
        raise PlexImportError("The Plex server sent an unexpected reply.")
    return container


def _objects(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: object) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
        return str(value)
    return None


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _truthy(value: object) -> bool:
    return value is True or value == 1 or (isinstance(value, str) and value.lower() in ("1", "true"))


def _attribute(element: ElementTree.Element, *names: str) -> str | None:
    for name in names:
        if (value := element.attrib.get(name)):
            return value
    return None


def _tag(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _provider_ids(raw: dict[str, Any]) -> dict[str, str]:
    values = [item.get("id") for item in _objects(raw.get("Guid"))]
    values.append(raw.get("guid"))
    provider_ids: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str):
            continue
        clean = value.split("?", 1)[0]
        if "://" not in clean:
            continue
        scheme, identifier = clean.split("://", 1)
        provider = scheme.lower().rsplit(".", 1)[-1]
        provider = LEGACY_PROVIDERS.get(provider, provider)
        if provider in PROVIDER_KEYS and identifier:
            provider_ids[PROVIDER_KEYS[provider]] = identifier
    return provider_ids


def _path(raw: dict[str, Any]) -> str | None:
    for media in _objects(raw.get("Media")):
        for part in _objects(media.get("Part")):
            if (path := _text(part.get("file"))) is not None:
                return path
    return None


def _date(value: object) -> datetime | None:
    stamp = _integer(value)
    try:
        return datetime.fromtimestamp(stamp, UTC).replace(tzinfo=None) if stamp is not None and stamp >= 0 else None
    except (OverflowError, OSError, ValueError):
        return None


def _entry(raw: dict[str, Any]) -> JellyfinEntry | None:
    kind = _text(raw.get("type"))
    item_id, name = _text(raw.get("ratingKey")), _text(raw.get("title"))
    if kind not in ("movie", "episode") or item_id is None or name is None:
        return None
    offset = _integer(raw.get("viewOffset")) or 0
    view_count = _integer(raw.get("viewCount")) or 0
    return JellyfinEntry(
        id=item_id,
        type="Movie" if kind == "movie" else "Episode",
        name=name,
        path=_path(raw),
        provider_ids=_provider_ids(raw),
        year=_integer(raw.get("year")),
        series_id=_text(raw.get("grandparentRatingKey")),
        series_name=_text(raw.get("grandparentTitle")),
        season=_integer(raw.get("parentIndex")),
        episode=_integer(raw.get("index")),
        played=view_count > 0,
        position_seconds=offset // 1000 if 0 < offset < 10**15 else 0,
        last_played=_date(raw.get("lastViewedAt")),
    )
