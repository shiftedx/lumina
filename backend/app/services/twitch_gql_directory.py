"""Anonymous Twitch live-directory client.

Talks to Twitch's public, unauthenticated web GraphQL endpoint — the same
anonymous access class yt-dlp uses for Twitch stream metadata. No account,
credential, app token, or member identity is ever involved, and there is no
authenticated (Helix) fallback.

Evidence basis: the query/response shape below was observed from anonymous
twitch.tv page loads (last verified 2026-09). It is an undocumented public web
surface, not a stable official API contract: every field is validated and any
drift fails closed to a labelled "unavailable" state, never a crash.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.services import provider_budget

logger = logging.getLogger(__name__)

_GQL_URL = "https://gql.twitch.tv/gql"
# Twitch's public web client id (shipped in every anonymous twitch.tv page load).
_PUBLIC_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_TIMEOUT = httpx.Timeout(10.0)

PUBLIC_TWITCH_ERROR = "Twitch streams are temporarily unavailable."
# Twitch logins are 1-25 word characters; anything else could smuggle a path
# or host into the channel URL built from it.
_LOGIN = re.compile(r"[A-Za-z0-9_]{1,25}")

_STREAM_NODE_FIELDS = """
        title
        viewersCount
        previewImageURL(width: 640, height: 360)
        broadcaster { login displayName }
        game { displayName }
"""

# Twitch caps a directory's stream page at 100 edges and the overall top-streams page at 30 (verified 2026-10).
MAX_PAGE = 100
MAX_TOP_PAGE = 30

_TOP_STREAMS_QUERY = f"""
query LuminaTopStreams($limit: Int!, $after: Cursor) {{
  streams(first: $limit, after: $after) {{
    pageInfo {{ hasNextPage }}
    edges {{ cursor node {{ {_STREAM_NODE_FIELDS} }} }}
  }}
}}
"""

_GAME_STREAMS_QUERY = f"""
query LuminaGameStreams($name: String!, $limit: Int!, $after: Cursor) {{
  game(name: $name) {{
    broadcastersCount
    streams(first: $limit, after: $after) {{
      pageInfo {{ hasNextPage }}
      edges {{ cursor node {{ {_STREAM_NODE_FIELDS} }} }}
    }}
  }}
}}
"""

_TOP_GAMES_QUERY = """
query LuminaTopGames($limit: Int!) {
  games(first: $limit, options: {sort: VIEWER_COUNT}) {
    edges { node { name broadcastersCount } }
  }
}
"""


class TwitchDirectoryError(RuntimeError):
    """The anonymous Twitch directory is unavailable; message is member-safe."""

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after  # seconds, from a 429's Retry-After; read by the refresh back-off


class TwitchBudgetExhausted(TwitchDirectoryError):
    """Skipped, not failed: the shared Twitch budget is spent or paused (provider_budget)."""


@dataclass(frozen=True)
class TwitchLiveStream:
    login: str
    display_name: str
    title: str | None
    viewers_count: int | None
    preview_image_url: str | None
    category_name: str | None


@dataclass(frozen=True)
class TwitchPage:
    streams: list[TwitchLiveStream]
    next_cursor: str | None  # Twitch's own edge cursor; the wall wraps it in an opaque token
    live_count: int | None  # the directory's broadcastersCount; None when Twitch gives no total


def _dig(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    try:
        return float(response.headers["Retry-After"]) if response is not None else None
    except (KeyError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _safe_preview_url(value: Any) -> str | None:
    """Only Twitch's own https image CDN; any other address is dropped."""
    url = _text(value)
    if url is None:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    return url if parts.scheme == "https" and (host == "jtvnw.net" or host.endswith(".jtvnw.net")) else None


class TwitchGqlDirectory:
    def __init__(self, *, http_client: httpx.Client | None = None) -> None:
        self._http_client = http_client
        self._owns_client = http_client is None
        self._lock = threading.Lock()
        self._drift_logged = False

    def _client(self) -> httpx.Client:
        with self._lock:
            if self._http_client is None:
                self._http_client = httpx.Client(timeout=_TIMEOUT)
            return self._http_client

    def close(self) -> None:
        with self._lock:
            if self._owns_client and self._http_client is not None:
                self._http_client.close()
                self._http_client = None

    def top_streams(self, limit: int) -> list[TwitchLiveStream]:
        return self.top_page(limit).streams

    def game_streams(self, game_name: str, limit: int) -> list[TwitchLiveStream]:
        return self.game_page(game_name, limit).streams

    def top_page(self, limit: int, after: str | None = None) -> TwitchPage:
        payload = self._post(_TOP_STREAMS_QUERY, {"limit": min(limit, MAX_TOP_PAGE), "after": after})
        return self._parse_page(_dig(payload, "data", "streams"), None)

    def game_page(self, game_name: str, limit: int, after: str | None = None) -> TwitchPage:
        payload = self._post(
            _GAME_STREAMS_QUERY, {"name": game_name, "limit": min(limit, MAX_PAGE), "after": after}
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            self._log_drift_once("response-shape")
            raise TwitchDirectoryError(PUBLIC_TWITCH_ERROR)
        if "game" in data and data["game"] is None:
            # Twitch returns `game: null` for an unknown/renamed category name —
            # a legitimate empty result, not a schema break.
            return TwitchPage([], None, None)
        return self._parse_page(_dig(data, "game", "streams"), _count(_dig(data, "game", "broadcastersCount")))

    def top_games(self, limit: int = MAX_PAGE) -> list[tuple[str, int]]:
        """(name, live broadcasters) for the busiest directories, busiest first."""
        payload = self._post(_TOP_GAMES_QUERY, {"limit": min(limit, MAX_PAGE)})
        edges = _dig(payload, "data", "games", "edges")
        if not isinstance(edges, list):
            self._log_drift_once("response-shape")
            raise TwitchDirectoryError(PUBLIC_TWITCH_ERROR)
        games = []
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            name, count = _text(_dig(node, "name")), _count(_dig(node, "broadcastersCount"))
            if name and count is not None:
                games.append((name, count))
        return games

    def _parse_page(self, connection: Any, live_count: int | None) -> TwitchPage:
        edges = _dig(connection, "edges")
        streams = self._parse_edges(edges)
        last = edges[-1] if isinstance(edges, list) and edges else None
        cursor = _text(_dig(last, "cursor"))
        has_next = _dig(connection, "pageInfo", "hasNextPage") is True
        return TwitchPage(streams, cursor if has_next else None, live_count)

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if not provider_budget.twitch.take():
            raise TwitchBudgetExhausted(PUBLIC_TWITCH_ERROR)
        try:
            response = self._client().post(
                _GQL_URL,
                json={"query": query, "variables": variables},
                headers={"Client-ID": _PUBLIC_WEB_CLIENT_ID},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            provider_budget.twitch.failed(error, _retry_after(error))
            raise TwitchDirectoryError(PUBLIC_TWITCH_ERROR, retry_after=_retry_after(error)) from error
        provider_budget.twitch.succeeded()
        if not isinstance(payload, dict) or payload.get("errors"):
            self._log_drift_once("gql-errors")
            raise TwitchDirectoryError(PUBLIC_TWITCH_ERROR)
        return payload

    def _parse_edges(self, edges: Any) -> list[TwitchLiveStream]:
        if not isinstance(edges, list):
            self._log_drift_once("response-shape")
            raise TwitchDirectoryError(PUBLIC_TWITCH_ERROR)
        streams: list[TwitchLiveStream] = []
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                continue
            broadcaster = node.get("broadcaster") if isinstance(node.get("broadcaster"), dict) else {}
            login = _text(broadcaster.get("login"))
            if not login or not _LOGIN.fullmatch(login):
                continue
            viewers = node.get("viewersCount")
            game = node.get("game")
            streams.append(
                TwitchLiveStream(
                    login=login,
                    display_name=_text(broadcaster.get("displayName")) or login,
                    title=_text(node.get("title")),
                    viewers_count=viewers if isinstance(viewers, int) and not isinstance(viewers, bool) else None,
                    preview_image_url=_safe_preview_url(node.get("previewImageURL")),
                    category_name=_text(game.get("displayName")) if isinstance(game, dict) else None,
                )
            )
        return streams

    def _log_drift_once(self, kind: str) -> None:
        with self._lock:
            if self._drift_logged:
                return
            self._drift_logged = True
        logger.warning("Anonymous Twitch directory schema drift detected (%s); failing closed.", kind)
