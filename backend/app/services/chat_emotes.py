"""A small, isolated artwork registry for remote chat emotes."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from app.services.artwork import ArtworkError, ArtworkService, RemoteArtworkFetcher
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError


EMOTE_ARTWORK_PREFIX = "/api/artwork/emotes/"
MAX_EMOTE_ARTWORK_BYTES = 128 * 1024
MAX_EMOTE_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHED_EMOTES = 256
MAX_EMOTE_SOURCES = 4096
MAX_RESPONSE_EMOTE_SOURCES = 64
MAX_EMOTE_URL_CHARS = 2048


class ChatEmoteArtworkService(ArtworkService):
    """Remote artwork with chat-sized limits and no poster-cache contention."""

    def __init__(
        self,
        *,
        remote_fetcher: RemoteArtworkFetcher,
        public_source_policy: PublicSourcePolicy | None = None,
        max_remote_sources: int = MAX_EMOTE_SOURCES,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            remote_fetcher=remote_fetcher,
            public_source_policy=public_source_policy,
            cache_root=None,
            max_artwork_bytes=MAX_EMOTE_ARTWORK_BYTES,
            max_cached_artworks=MAX_CACHED_EMOTES,
            max_cache_bytes=MAX_EMOTE_CACHE_BYTES,
            max_remote_sources=max_remote_sources,
            max_concurrent_fetches=2,
            token_factory=token_factory,
        )
        self._response_source_limit = min(MAX_RESPONSE_EMOTE_SOURCES, max_remote_sources)

    def new_resolver(self) -> Callable[[object], str | None]:
        """Return one response-scoped URL mapper with bounded registrations.

        A response can never register enough distinct sources to evict one of its
        own earlier IDs. Repeated URLs share one ID, and all first-time URLs on the
        same host share one DNS validation. Any invalid or over-budget image falls
        back to the event's text label.
        """

        paths_by_source: dict[str, str | None] = {}
        resolved_hosts: set[tuple[str, int]] = set()

        def resolve(provider_artwork_url: object) -> str | None:
            if not isinstance(provider_artwork_url, str):
                return None
            source_url = provider_artwork_url.strip()
            if source_url in paths_by_source:
                return paths_by_source[source_url]
            if not self._candidate_url(source_url) or len(paths_by_source) >= self._response_source_limit:
                return None
            try:
                artwork_id = self.register_remote_artwork(source_url, resolved=resolved_hosts)
            except (ArtworkError, PublicSourcePolicyError):
                path = None
            else:
                path = f"{EMOTE_ARTWORK_PREFIX}{artwork_id}"
            paths_by_source[source_url] = path
            return path

        return resolve

    @staticmethod
    def _candidate_url(source_url: str) -> bool:
        if not source_url or len(source_url) > MAX_EMOTE_URL_CHARS:
            return False
        try:
            parsed = urlsplit(source_url)
            parsed.port
        except ValueError:
            return False
        return parsed.scheme.lower() == "https" and bool(parsed.hostname)
