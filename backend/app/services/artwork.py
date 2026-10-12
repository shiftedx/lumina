from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Protocol

from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError

from app.persistence import atomic_write, read_regular_file
from app.services.network_policy import PolicyYoutubeDL, PublicSourcePolicy, PublicSourcePolicyError


SUPPORTED_ARTWORK_CONTENT_TYPES = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
LOCAL_ARTWORK_TYPES = (
    (".webp", "image/webp"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".png", "image/png"),
    (".avif", "image/avif"),
    (".gif", "image/gif"),
)


class ArtworkError(RuntimeError):
    """A stable error produced while resolving display artwork."""


class ArtworkNotFoundError(ArtworkError):
    """The requested artwork is unavailable or not visible to this household member."""


class ArtworkContentError(ArtworkError):
    """Artwork did not meet Lumina's image content or size contract."""


class ArtworkUnavailableError(ArtworkError):
    """An otherwise valid remote artwork source could not be reached."""


class ArtworkCapacityError(ArtworkError):
    """The bounded remote artwork registry has no safe slot available."""


@dataclass(frozen=True)
class ArtworkResponse:
    """Backend-owned artwork bytes safe for an authenticated route to return."""

    content_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class RemoteArtworkResponse:
    """The response contract required from the policy-aware remote fetch adapter.

    The adapter must call ``validate_redirect`` before following each redirect and
    must use its public-source connection strategy for the request itself. The
    final URL is checked again here as a defense-in-depth assertion.
    """

    content_type: str
    body: Iterable[bytes] = field(repr=False)
    status_code: int = 200
    final_url: str | None = field(default=None, repr=False)
    close: Callable[[], None] = field(default=lambda: None, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class LibraryArtwork:
    """Artwork metadata supplied only after the caller applies Library visibility."""

    library_item_id: str
    media_path: Path | None = field(repr=False)
    provider_artwork_url: str | None = field(repr=False)
    library_root: Path | None = field(default=None, repr=False)
    # A sidecar poster already resolved through the artifact registry (imports).
    artwork_path: Path | None = field(default=None, repr=False)


class LibraryArtworkAccess(Protocol):
    """The authorization seam a route adapter supplies for a Library item."""

    def find_visible_artwork(self, household_member_id: str, library_item_id: str) -> LibraryArtwork | None: ...


class RemoteArtworkFetcher(Protocol):
    """A transport adapter that follows redirects only after policy validation."""

    def fetch(
        self,
        url: str,
        *,
        validate_redirect: Callable[[str], str],
        headers: Mapping[str, str] | None = None,
    ) -> RemoteArtworkResponse: ...


class PublicArtworkFetcher:
    """Fetch image bytes through Lumina's public-only yt-dlp transport."""

    chunk_size = 64 * 1024

    def __init__(self, policy: PublicSourcePolicy | None = None, request_timeout_seconds: float = 20) -> None:
        self._policy = policy or PublicSourcePolicy()
        self._request_timeout_seconds = request_timeout_seconds

    def fetch(
        self, url: str, *, validate_redirect: Callable[[str], str], headers: Mapping[str, str] | None = None,
        data: bytes | None = None,  # a request body makes it a POST (AniList GraphQL)
    ) -> RemoteArtworkResponse:
        validate_redirect(url)
        ydl = PolicyYoutubeDL({"ignoreconfig": True, "quiet": True, "proxy": ""}, policy=self._policy)
        response = None
        try:
            response = ydl.urlopen(Request(url, data=data, headers=dict(headers or {}), extensions={"timeout": self._request_timeout_seconds}))
        except HTTPError as exc:
            response = exc.response
        except BaseException:
            ydl.close()
            raise
        try:
            final_url = getattr(response, "url", None)
            if isinstance(final_url, str):
                validate_redirect(final_url)
            status_code = int(response.status)
            content_type = str(response.headers.get("Content-Type") or "")
            response_headers = {str(key): str(value) for key, value in response.headers.items()}
        except BaseException:
            try:
                response.close()
            finally:
                ydl.close()
            raise

        def close() -> None:
            try:
                response.close()
            finally:
                ydl.close()

        def chunks() -> Iterable[bytes]:
            while chunk := response.read(self.chunk_size):
                yield chunk

        return RemoteArtworkResponse(
            content_type=content_type,
            body=chunks(),
            status_code=status_code,
            final_url=final_url if isinstance(final_url, str) else None,
            close=close,
            headers=response_headers,
        )


@dataclass
class _RemoteArtwork:
    source_url: str = field(repr=False)
    cache_key: str = ""
    created_at: float = 0
    last_accessed_at: float = 0
    cached: ArtworkResponse | None = field(default=None, repr=False)
    fresh_until: float = 0
    stale_until: float = 0


@dataclass
class _InFlightFetch:
    complete: threading.Event = field(default_factory=threading.Event)
    result: ArtworkResponse | None = field(default=None, repr=False)
    error: BaseException | None = field(default=None, repr=False)
    waiters: int = 0


class ArtworkService:
    """Resolve artwork through small, opaque, bounded backend-owned interfaces.

    Routes can register provider artwork and expose only the returned opaque ID.
    Library routes use ``load_library_artwork`` so access is checked by a caller-
    supplied Library adapter before any local path or provider metadata is used.
    """

    def __init__(
        self,
        *,
        remote_fetcher: RemoteArtworkFetcher,
        library_access: LibraryArtworkAccess | None = None,
        library_root: Path | None = None,
        cache_root: Path | None = None,
        pinned_root: Path | None = None,
        public_source_policy: PublicSourcePolicy | None = None,
        token_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time,
        max_artwork_bytes: int = 8 * 1024 * 1024,
        cache_ttl_seconds: float = 60 * 60,
        stale_if_error_seconds: float = 6 * 60 * 60,
        source_idle_ttl_seconds: float = 24 * 60 * 60,
        max_cached_artworks: int = 500,
        max_cache_bytes: int = 256 * 1024 * 1024,
        max_remote_sources: int = 1_000,
        max_concurrent_fetches: int = 4,
        max_waiters_per_fetch: int = 2,
        coalesced_wait_timeout_seconds: float = 1,
    ) -> None:
        if max_artwork_bytes <= 0 or max_cached_artworks <= 0 or max_cache_bytes < max_artwork_bytes or max_remote_sources <= 0:
            raise ValueError("Artwork cache limits must leave room for one bounded artwork.")
        if max_concurrent_fetches <= 0 or max_waiters_per_fetch <= 0 or coalesced_wait_timeout_seconds <= 0:
            raise ValueError("Artwork fetch concurrency and wait limits must be positive.")
        if cache_ttl_seconds <= 0 or stale_if_error_seconds < 0 or source_idle_ttl_seconds <= 0:
            raise ValueError("Artwork cache TTLs must be positive, except stale-on-error which may be zero.")
        self._remote_fetcher = remote_fetcher
        self._library_access = library_access
        self._library_root = library_root.resolve() if library_root is not None else None
        self._cache_root = None
        if cache_root is not None:
            cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if cache_root.is_symlink() or not cache_root.is_dir():
                raise ValueError("Artwork cache root must be a private directory, not a symbolic link.")
            cache_root.chmod(0o700)
            self._cache_root = cache_root.resolve()
        # Immutable remote art (TMDB paths never change); created lazily on first write.
        self._pinned_root = pinned_root
        self._public_source_policy = public_source_policy or PublicSourcePolicy()
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._clock = clock
        self._max_artwork_bytes = max_artwork_bytes
        self._cache_ttl_seconds = cache_ttl_seconds
        self._stale_if_error_seconds = stale_if_error_seconds
        self._source_idle_ttl_seconds = source_idle_ttl_seconds
        self._max_cached_artworks = max_cached_artworks
        self._max_cache_bytes = max_cache_bytes
        self._max_remote_sources = max_remote_sources
        self._max_waiters_per_fetch = max_waiters_per_fetch
        self._coalesced_wait_timeout_seconds = coalesced_wait_timeout_seconds
        self._fetch_slots = threading.BoundedSemaphore(max_concurrent_fetches)
        self._records: dict[str, _RemoteArtwork] = {}
        self._ids_by_source: dict[str, str] = {}
        self._inflight: dict[str, _InFlightFetch] = {}
        self._cached_bytes = 0
        self._lock = threading.RLock()
        self._evict_disk_cache(preserve_key=None, now=self._clock())

    def register_remote_artwork(self, provider_artwork_url: str) -> str:
        """Return an opaque ID after validating a public provider artwork URL."""

        # Known sources were validated on first registration: skip the blocking DNS
        # lookup on repeat hits (validate_url only strips, so the key is stable).
        if (known_id := self._touch_known_source(provider_artwork_url.strip() if isinstance(provider_artwork_url, str) else "")) is not None:
            return known_id
        source_url = self._public_source_policy.validate_url(provider_artwork_url)
        now = self._clock()
        with self._lock:
            if (known_id := self._touch_known_source(source_url)) is not None:
                return known_id
            self._make_remote_source_space()
            artwork_id = self._new_artwork_id()
            self._records[artwork_id] = _RemoteArtwork(
                source_url=source_url,
                cache_key=self._source_cache_key(source_url),
                created_at=now,
                last_accessed_at=now,
            )
            self._ids_by_source[source_url] = artwork_id
            return artwork_id

    def _touch_known_source(self, source_url: str) -> str | None:
        now = self._clock()
        with self._lock:
            self._evict_sources(now)
            existing_id = self._ids_by_source.get(source_url)
            if existing_id is None or existing_id not in self._records:
                return None
            self._records[existing_id].last_accessed_at = now
            return existing_id

    def load_remote_artwork(
        self,
        artwork_id: str,
        *,
        before_upstream_fetch: Callable[[], None] | None = None,
    ) -> ArtworkResponse:
        """Return cached artwork or coalesce one refresh for every concurrent caller.

        ``before_upstream_fetch`` runs only when this call is about to fetch from
        the provider, so callers can budget cold fetches without taxing warm
        cache hits; an exception it raises aborts the fetch and propagates.
        """

        now = self._clock()
        with self._lock:
            record = self._records.get(artwork_id)
            if record is None:
                raise ArtworkNotFoundError("Artwork is unavailable.")
            record.last_accessed_at = now
            self._evict_sources(now, preserve_id=artwork_id)
            if record.cached is None:
                self._load_disk_cache(record, now)
            if record.cached is not None and now < record.fresh_until:
                return record.cached
            flight = self._inflight.get(artwork_id)
            if flight is None:
                flight = _InFlightFetch()
                self._inflight[artwork_id] = flight
                fetch_here = True
            else:
                fetch_here = False
                if record.cached is not None and now < record.stale_until:
                    return record.cached
                if flight.waiters >= self._max_waiters_per_fetch:
                    return self._stale_or_raise(record, now, ArtworkCapacityError("Artwork is busy; try again shortly."))
                flight.waiters += 1

        if not fetch_here:
            completed = flight.complete.wait(timeout=self._coalesced_wait_timeout_seconds)
            with self._lock:
                flight.waiters = max(0, flight.waiters - 1)
            if not completed:
                return self._stale_or_raise(record, self._clock(), ArtworkCapacityError("Artwork is busy; try again shortly."))
            if flight.result is not None:
                return flight.result
            assert flight.error is not None
            return self._stale_or_raise(record, self._clock(), flight.error)

        if before_upstream_fetch is not None:
            try:
                before_upstream_fetch()
            except BaseException as exc:
                with self._lock:
                    flight.error = exc
                    self._inflight.pop(artwork_id, None)
                    flight.complete.set()
                return self._stale_or_raise(record, self._clock(), exc)

        # Dense shelves can discover several uncached images at once. Give an
        # existing fetch a short chance to finish instead of turning temporary
        # concurrency pressure into a permanent thumbnail placeholder.
        if not self._fetch_slots.acquire(timeout=self._coalesced_wait_timeout_seconds):
            error = ArtworkCapacityError("Artwork is busy; try again shortly.")
            with self._lock:
                flight.error = error
                self._inflight.pop(artwork_id, None)
                flight.complete.set()
            return self._stale_or_raise(record, self._clock(), error)
        try:
            result = self._fetch_remote(record.source_url)
        except BaseException as exc:
            with self._lock:
                flight.error = exc
                self._inflight.pop(artwork_id, None)
                flight.complete.set()
            return self._stale_or_raise(record, self._clock(), exc)
        finally:
            self._fetch_slots.release()

        now = self._clock()
        with self._lock:
            self._cache(record, result, now)
            flight.result = result
            self._inflight.pop(artwork_id, None)
            flight.complete.set()
        return result

    def load_library_artwork(
        self,
        household_member_id: str,
        library_item_id: str,
        *,
        before_upstream_fetch: Callable[[], None] | None = None,
    ) -> ArtworkResponse:
        """Authorize a Library item, prefer adjacent local artwork, then cache provider artwork."""

        if self._library_access is None or self._library_root is None:
            raise RuntimeError("Library artwork access is not configured.")
        source = self._library_access.find_visible_artwork(household_member_id, library_item_id)
        if source is None or source.library_item_id != library_item_id:
            raise ArtworkNotFoundError("Artwork is unavailable.")
        content_type = dict(LOCAL_ARTWORK_TYPES).get(source.artwork_path.suffix.lower()) if source.artwork_path else None
        if content_type:
            try:
                return ArtworkResponse(content_type=content_type, content=read_regular_file(source.artwork_path, self._max_artwork_bytes))
            except OSError as exc:
                raise ArtworkUnavailableError("Artwork is temporarily unavailable.") from exc
        local_artwork = self._load_adjacent_artwork(source.media_path, source.library_root or self._library_root)
        if local_artwork is not None:
            return local_artwork
        if not source.provider_artwork_url:
            raise ArtworkNotFoundError("Artwork is unavailable.")
        return self.load_remote_artwork(
            self.register_remote_artwork(source.provider_artwork_url),
            before_upstream_fetch=before_upstream_fetch,
        )

    def load_pinned(
        self,
        url: str,
        *,
        validate_redirect: Callable[[str], str],
        bucket: str = "",
        max_bytes: int | None = None,
        before_upstream_fetch: Callable[[], None] | None = None,
    ) -> ArtworkResponse:
        """Remote art whose URL never changes content (TMDB image paths): fetched once, kept on disk, no TTL.

        ``validate_redirect`` pins the allowed hosts; it runs for the first URL, every redirect hop and the
        final URL. Cold fetches share the remote fetch-slot limit and first run ``before_upstream_fetch`` (a rate
        limit). ``bucket`` is a subdirectory; with ``max_bytes`` it is a least-recently-used cache (a hit touches the
        file, a write evicts the oldest past the cap). The main root is capped hourly by tmdb.cap_pinned, which keeps every
        file a title or person still uses. Concurrent cold requests for one URL both fetch.
        """
        if self._pinned_root is None:
            raise RuntimeError("Pinned artwork storage is not configured.")
        root = self._pinned_root / bucket if bucket else self._pinned_root
        key = self._source_cache_key(url)
        for extension, content_type in LOCAL_ARTWORK_TYPES:
            path = root / f"{key}{extension}"
            if path.is_file():
                try:
                    content = read_regular_file(path, self._max_artwork_bytes)
                except OSError:
                    break  # unreadable or not a regular file: refetch and replace it
                if max_bytes is not None:
                    with contextlib.suppress(OSError):
                        os.utime(path)
                return ArtworkResponse(content_type=content_type, content=content)
        if before_upstream_fetch is not None:
            before_upstream_fetch()
        if not self._fetch_slots.acquire(timeout=self._coalesced_wait_timeout_seconds):
            raise ArtworkCapacityError("Artwork is busy; try again shortly.")
        try:
            result = self._fetch_remote(url, validate_redirect)
        finally:
            self._fetch_slots.release()
        extension = next(ext for ext, content_type in LOCAL_ARTWORK_TYPES if content_type == result.content_type)
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self._pinned_root.is_symlink() and not root.is_symlink():
                atomic_write(root / f"{key}{extension}", result.content)
                if max_bytes is not None:
                    _evict_oldest(root, max_bytes)
        except OSError:
            pass  # still served; the next request fetches again
        return result

    def _fetch_remote(self, source_url: str, validate_redirect: Callable[[str], str] | None = None) -> ArtworkResponse:
        validate = validate_redirect or self._public_source_policy.validate_url
        try:
            response = self._remote_fetcher.fetch(source_url, validate_redirect=validate)
        except PublicSourcePolicyError:
            raise
        except ArtworkError:
            raise
        except Exception as exc:
            raise ArtworkUnavailableError("Artwork is temporarily unavailable.") from exc
        try:
            if response.final_url is not None:
                validate(response.final_url)
            if not isinstance(response.status_code, int) or not 200 <= response.status_code < 300:
                raise ArtworkUnavailableError("Artwork is temporarily unavailable.")
            if not isinstance(response.content_type, str):
                raise ArtworkContentError("Artwork must be a supported image type.")
            content_type = response.content_type.split(";", 1)[0].strip().lower()
            if content_type not in SUPPORTED_ARTWORK_CONTENT_TYPES:
                raise ArtworkContentError("Artwork must be a supported image type.")
            content = bytearray()
            for chunk in response.body:
                if not isinstance(chunk, bytes):
                    raise ArtworkContentError("Artwork response did not contain image bytes.")
                if len(content) + len(chunk) > self._max_artwork_bytes:
                    raise ArtworkContentError("Artwork exceeds the maximum allowed size.")
                content.extend(chunk)
        except ArtworkError:
            raise
        except Exception as exc:
            raise ArtworkUnavailableError("Artwork is temporarily unavailable.") from exc
        finally:
            response.close()
        return ArtworkResponse(content_type=content_type, content=bytes(content))

    def _load_adjacent_artwork(self, media_path: Path | None, library_root: Path | None) -> ArtworkResponse | None:
        if media_path is None:
            return None
        if library_root is None:
            raise RuntimeError("Library artwork root is not configured.")
        resolved_library_root = library_root.resolve(strict=False)
        resolved_media_path = media_path.resolve(strict=False)
        self._require_library_path(resolved_media_path, resolved_library_root)
        for extension, content_type in LOCAL_ARTWORK_TYPES:
            candidate = resolved_media_path.with_suffix(extension).resolve(strict=False)
            self._require_library_path(candidate, resolved_library_root)
            if not candidate.is_file():
                continue
            try:
                if candidate.stat().st_size > self._max_artwork_bytes:
                    raise ArtworkContentError("Artwork exceeds the maximum allowed size.")
                return ArtworkResponse(content_type=content_type, content=candidate.read_bytes())
            except ArtworkError:
                raise
            except OSError as exc:
                raise ArtworkUnavailableError("Artwork is temporarily unavailable.") from exc
        return None

    @staticmethod
    def _require_library_path(path: Path, library_root: Path) -> None:
        try:
            path.relative_to(library_root)
        except ValueError as exc:
            raise ArtworkNotFoundError("Artwork is unavailable.") from exc

    def _stale_or_raise(self, record: _RemoteArtwork, now: float, error: BaseException) -> ArtworkResponse:
        if isinstance(error, (ArtworkUnavailableError, ArtworkCapacityError)) and record.cached is not None and now < record.stale_until:
            return record.cached
        raise error

    def _cache(self, record: _RemoteArtwork, result: ArtworkResponse, now: float) -> None:
        if record.cached is not None:
            self._cached_bytes -= len(record.cached.content)
        record.cached = result
        record.fresh_until = now + self._cache_ttl_seconds
        record.stale_until = record.fresh_until + self._stale_if_error_seconds
        self._cached_bytes += len(result.content)
        self._evict_cache(preserve_record=record)
        self._write_disk_cache(record, result, now)

    @staticmethod
    def _source_cache_key(source_url: str) -> str:
        return hashlib.sha256(source_url.encode("utf-8")).hexdigest()

    def _disk_paths(self, cache_key: str) -> tuple[Path, Path]:
        assert self._cache_root is not None
        return self._cache_root / f"{cache_key}.bin", self._cache_root / f"{cache_key}.json"

    def _load_disk_cache(self, record: _RemoteArtwork, now: float) -> None:
        if self._cache_root is None:
            return
        content_path, metadata_path = self._disk_paths(record.cache_key)
        if not content_path.is_file() or not metadata_path.is_file():
            return
        try:
            metadata = json.loads(read_regular_file(metadata_path, 16 * 1024).decode("utf-8"))
            content_type = metadata.get("content_type")
            size = metadata.get("size")
            fresh_until = float(metadata.get("fresh_until"))
            stale_until = float(metadata.get("stale_until"))
            if (
                metadata.get("version") != 1
                or content_type not in SUPPORTED_ARTWORK_CONTENT_TYPES
                or not isinstance(size, int)
                or size <= 0
                or size > self._max_artwork_bytes
                or stale_until < fresh_until
            ):
                raise ValueError("Invalid artwork cache metadata")
            if now >= stale_until:
                self._delete_disk_entry(record.cache_key)
                return
            content = read_regular_file(content_path, self._max_artwork_bytes)
            if len(content) != size:
                raise ValueError("Artwork cache size mismatch")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._delete_disk_entry(record.cache_key)
            return
        record.cached = ArtworkResponse(content_type=content_type, content=content)
        record.fresh_until = fresh_until
        record.stale_until = stale_until
        self._cached_bytes += len(content)
        self._evict_cache(preserve_record=record)
        metadata["last_accessed_at"] = now
        try:
            atomic_write(metadata_path, json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        except OSError:
            pass

    def _write_disk_cache(self, record: _RemoteArtwork, result: ArtworkResponse, now: float) -> None:
        if self._cache_root is None:
            return
        content_path, metadata_path = self._disk_paths(record.cache_key)
        metadata = {
            "version": 1,
            "content_type": result.content_type,
            "size": len(result.content),
            "fresh_until": record.fresh_until,
            "stale_until": record.stale_until,
            "last_accessed_at": now,
        }
        try:
            atomic_write(content_path, result.content)
            atomic_write(metadata_path, json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode("utf-8"))
            self._evict_disk_cache(preserve_key=record.cache_key, now=now)
        except OSError:
            self._delete_disk_entry(record.cache_key)

    def _evict_disk_cache(self, *, preserve_key: str | None, now: float) -> None:
        if self._cache_root is None:
            return
        for temporary_path in self._cache_root.glob(".*.tmp"):
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        entries: list[tuple[float, str, int]] = []
        total_bytes = 0
        for metadata_path in self._cache_root.glob("*.json"):
            cache_key = metadata_path.stem
            if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
                metadata_path.unlink(missing_ok=True)
                continue
            content_path, _ = self._disk_paths(cache_key)
            try:
                metadata = json.loads(read_regular_file(metadata_path, 16 * 1024).decode("utf-8"))
                size = int(metadata["size"])
                last_accessed_at = float(metadata["last_accessed_at"])
                stale_until = float(metadata["stale_until"])
                content_size = os.lstat(content_path).st_size
                if (
                    metadata.get("version") != 1
                    or stale_until <= now
                    or size <= 0
                    or size > self._max_artwork_bytes
                    or content_path.is_symlink()
                    or not stat.S_ISREG(os.lstat(content_path).st_mode)
                    or content_size != size
                ):
                    raise ValueError("Invalid artwork cache entry")
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                self._delete_disk_entry(cache_key)
                continue
            entries.append((last_accessed_at, cache_key, size))
            total_bytes += size
        for content_path in self._cache_root.glob("*.bin"):
            if not content_path.with_suffix(".json").exists():
                content_path.unlink(missing_ok=True)
        while len(entries) > self._max_cached_artworks or total_bytes > self._max_cache_bytes:
            candidates = [entry for entry in entries if entry[1] != preserve_key]
            if not candidates:
                break
            victim = min(candidates)
            entries.remove(victim)
            total_bytes -= victim[2]
            self._delete_disk_entry(victim[1])

    def _delete_disk_entry(self, cache_key: str) -> None:
        if self._cache_root is None:
            return
        content_path, metadata_path = self._disk_paths(cache_key)
        for path in (content_path, metadata_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _evict_sources(self, now: float, *, preserve_id: str | None = None) -> None:
        expired = [
            artwork_id
            for artwork_id, record in self._records.items()
            if artwork_id != preserve_id
            and artwork_id not in self._inflight
            and now - record.last_accessed_at >= self._source_idle_ttl_seconds
        ]
        for artwork_id in expired:
            self._remove_record(artwork_id)

    def _make_remote_source_space(self) -> None:
        while len(self._records) >= self._max_remote_sources:
            candidates = [
                (record.last_accessed_at, artwork_id)
                for artwork_id, record in self._records.items()
                if artwork_id not in self._inflight
            ]
            if not candidates:
                raise ArtworkCapacityError("Artwork cache is at capacity.")
            self._remove_record(min(candidates)[1])

    def _evict_cache(self, *, preserve_record: _RemoteArtwork) -> None:
        def cached_records() -> list[_RemoteArtwork]:
            return [record for record in self._records.values() if record.cached is not None]

        cached = cached_records()
        while len(cached) > self._max_cached_artworks or self._cached_bytes > self._max_cache_bytes:
            candidates = [record for record in cached if record is not preserve_record]
            if not candidates:
                break
            oldest = min(candidates, key=lambda record: record.last_accessed_at)
            self._clear_cached(oldest)
            cached = cached_records()

    def _remove_record(self, artwork_id: str) -> None:
        record = self._records.pop(artwork_id, None)
        if record is None:
            return
        if self._ids_by_source.get(record.source_url) == artwork_id:
            self._ids_by_source.pop(record.source_url, None)
        self._clear_cached(record)

    def _clear_cached(self, record: _RemoteArtwork) -> None:
        if record.cached is None:
            return
        self._cached_bytes -= len(record.cached.content)
        record.cached = None
        record.fresh_until = 0
        record.stale_until = 0

    def _new_artwork_id(self) -> str:
        for _ in range(100):
            artwork_id = self._token_factory()
            if artwork_id and artwork_id not in self._records:
                return artwork_id
        raise ArtworkCapacityError("Artwork cache could not allocate an opaque identifier.")


def _evict_oldest(root: Path, max_bytes: int, keep: frozenset[str] | set[str] = frozenset()) -> None:
    """Delete the least recently used regular files in ``root`` until it holds at most ``max_bytes``.

    A file whose name (before its extension) is in ``keep`` is counted but never deleted, so the cap is soft when the
    kept files alone pass it.
    """
    # Stats the whole bucket on every cold write; fine at the candidate cache's few thousand files.
    files = sorted((entry.stat().st_mtime, entry.stat().st_size, entry.path, entry.name) for entry in os.scandir(root) if entry.is_file(follow_symlinks=False))
    total = sum(size for _, size, _, _ in files)
    for _, size, path, name in files:
        if total <= max_bytes:
            break
        if name.partition(".")[0] in keep:
            continue
        with contextlib.suppress(OSError):
            os.unlink(path)
            total -= size
