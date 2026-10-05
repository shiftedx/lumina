from __future__ import annotations

import base64
import json
import math
import threading
import weakref
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from random import random as default_random
from time import time
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlsplit, urlunsplit

from app.persistence import atomic_write
from app.schemas import MediaSourceCapabilities
from app.services import provider_budget


PopularState = Literal["loading", "ready", "partial", "stale", "empty", "failed"]
CategoryState = Literal["pending", "ready", "stale", "empty", "failed"]

BUDGET_RETRY_SECONDS = 15 * 60  # a spent or paused provider budget is not a failure: keep the rows, look again soon
PUBLIC_PROVIDER_ERROR = "Popular results are temporarily unavailable."
_STORE_VERSION = 1
MIN_ROW_ITEMS = 8  # a row thinner than this after refresh-time filtering is backfilled from its related queries
OMIT_ROW_BELOW = 4  # a row thinner than this is not shown at all
WALL_MAX = 120  # the deepest a See-all wall goes (DEEP_SEARCH_LIMIT in the search service)
_SHORT_SECONDS = 60  # reco.pool.MIN_DURATION_SECONDS: shorter videos are shorts


@dataclass(frozen=True)
class PopularCategory:
    key: str
    label: str
    query: str = field(repr=False)
    related: tuple[str, ...] = field(default=(), repr=False)  # backfill queries, tried in order (1-3)


POPULAR_CATEGORIES: tuple[PopularCategory, ...] = (
    PopularCategory("documentaries", "Documentaries", "documentary films", related=('true story documentary', 'nature and history documentary')),
    PopularCategory("music", "Music", "music videos", related=('official music video', 'new music releases')),
    PopularCategory("live-performances", "Live Performances", "live music performances", related=('concert full show', 'live acoustic session')),
    PopularCategory("gaming", "Gaming", "gaming", related=('gameplay walkthrough', 'game trailers')),
    PopularCategory("news", "News", "news", related=('breaking news today', 'world news report')),
    PopularCategory("sports", "Sports", "sports highlights", related=('football highlights', 'basketball highlights')),
    PopularCategory("film", "Film", "short films and movie trailers", related=('official movie trailer', 'award winning short film')),
    PopularCategory("comedy", "Comedy", "comedy", related=('stand up comedy', 'funny sketches')),
    PopularCategory("education", "Education", "educational videos", related=('lecture explained', 'how things work')),
    PopularCategory("science-technology", "Science & Technology", "science and technology", related=('science explained', 'new technology review')),
    PopularCategory("podcasts", "Podcasts", "podcasts", related=('podcast full episode', 'interview podcast')),
    PopularCategory("cooking", "Cooking", "cooking recipes", related=('easy dinner recipes', 'baking recipes')),
    PopularCategory("travel", "Travel", "travel", related=('travel vlog', 'city travel guide')),
    PopularCategory("diy-crafts", "DIY & Crafts", "DIY crafts", related=('diy projects', 'woodworking projects')),
    PopularCategory("home-garden", "Home & Garden", "home and garden", related=('gardening tips', 'home renovation')),
    PopularCategory("cars", "Cars", "cars and automotive", related=('car review', 'classic cars')),
    PopularCategory("fitness", "Fitness", "fitness workouts", related=('home workout', 'yoga routine')),
    PopularCategory("fashion-beauty", "Fashion & Beauty", "fashion and beauty", related=('makeup tutorial', 'fashion haul')),
    PopularCategory("nature", "Nature", "nature", related=('wildlife documentary', 'nature relaxation')),
    PopularCategory("history", "History", "history", related=('ancient history documentary', 'world war history')),
    PopularCategory("business-finance", "Business & Finance", "business and finance", related=('investing explained', 'startup business advice')),
    PopularCategory("photography", "Photography", "photography", related=('photography tips', 'camera review')),
    PopularCategory("animation", "Animation", "animation", related=('animated short film', 'animation behind the scenes')),
    PopularCategory("family", "Family", "family videos", related=('family friendly videos', 'kids activities')),
    PopularCategory("culture", "Culture", "arts and culture", related=('art history', 'world culture')),
)


@dataclass(frozen=True)
class PopularItem:
    id: str | None
    title: str | None
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    artwork_url: str | None
    webpage_url: str | None
    view_count: int | None
    availability: str | None
    published_at: datetime | None
    source: str
    source_label: str
    capabilities: MediaSourceCapabilities | None
    category_keys: tuple[str, ...]
    # Channel identity for #87's follow derivation. Optional and defaulted so
    # the frozen record stays backward compatible; a candidate without a
    # channel address simply cannot become a follow and is skipped downstream.
    uploader_url: str | None = None
    uploader_id: str | None = None


@dataclass(frozen=True)
class PopularCategorySnapshot:
    key: str
    label: str
    state: CategoryState
    last_success_at: datetime | None
    next_refresh_at: datetime | None


@dataclass(frozen=True)
class PopularSnapshot:
    """The complete route-facing response from the Popular discovery module."""

    items: tuple[PopularItem, ...]
    categories: tuple[PopularCategorySnapshot, ...]
    state: PopularState
    refreshing: bool
    stale: bool
    last_success_at: datetime | None
    refreshed_at: datetime | None
    next_refresh_at: datetime | None
    error: str | None


class RefreshExecutor(Protocol):
    def submit(self, function: Callable[[], None]) -> object: ...


@dataclass
class _PathCoordinator:
    lock: threading.RLock = field(default_factory=threading.RLock)
    refreshing: bool = False
    memory_store: dict[str, Any] | None = None
    wall_locks: dict[str, threading.Lock] = field(default_factory=dict)


_COORDINATORS_LOCK = threading.Lock()
_COORDINATORS: weakref.WeakValueDictionary[str, _PathCoordinator] = weakref.WeakValueDictionary()
def _coordinator_for(path: Path) -> _PathCoordinator:
    key = str(path)
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(key)
        if coordinator is None:
            coordinator = _PathCoordinator()
            _COORDINATORS[key] = coordinator
        return coordinator


class PopularDiscovery:
    """Build a durable, keyless Popular snapshot behind one request interface.

    ``get_snapshot`` never performs provider I/O in the request thread when the
    default executor is used. It returns the best durable snapshot and coalesces
    one bounded refresh for all instances using the same snapshot path.
    """

    def __init__(
        self,
        snapshot_path: Path,
        search: Callable[[str, int], object],
        *,
        categories: Sequence[PopularCategory] = POPULAR_CATEGORIES,
        clock: Callable[[], float] = time,
        random: Callable[[], float] = default_random,
        executor: RefreshExecutor | None = None,
        batch_size: int = 3,
        max_concurrency: int = 2,
        per_category_limit: int = 40,
        feed_limit: int = 24,
        category_ttl_seconds: float = 2 * 60 * 60,
        ttl_jitter_ratio: float = 0.2,
        batch_interval_seconds: float = 30,
        backoff_base_seconds: float = 60,
        backoff_max_seconds: float = 60 * 60,
        retry_after_max_seconds: float = 6 * 60 * 60,
        retention_seconds: float = 7 * 24 * 60 * 60,
    ) -> None:
        configured_categories = tuple(categories)
        keys = [category.key for category in configured_categories]
        if not configured_categories or len(keys) != len(set(keys)) or any(not key for key in keys):
            raise ValueError("Popular categories must have unique, non-empty keys.")
        if batch_size <= 0 or max_concurrency <= 0 or per_category_limit <= 0 or feed_limit <= 0:
            raise ValueError("Popular discovery limits must be positive.")
        if category_ttl_seconds <= 0 or batch_interval_seconds < 0 or retention_seconds <= 0:
            raise ValueError("Popular discovery TTLs and retention must be positive.")
        if not 0 <= ttl_jitter_ratio < 1:
            raise ValueError("Popular discovery TTL jitter must be at least zero and less than one.")
        if backoff_base_seconds <= 0 or backoff_max_seconds < backoff_base_seconds or retry_after_max_seconds <= 0:
            raise ValueError("Popular discovery backoff limits are invalid.")

        self._snapshot_path = Path(snapshot_path).expanduser().resolve(strict=False)
        self._search = search
        self._categories = configured_categories
        self._category_by_key = {category.key: category for category in configured_categories}
        self._clock = clock
        self._random = random
        self._owns_executor = executor is None
        self._executor: RefreshExecutor | None = executor
        self._executor_lock = threading.Lock()
        self._batch_size = min(batch_size, len(configured_categories))
        self._max_concurrency = min(max_concurrency, self._batch_size)
        self._per_category_limit = per_category_limit
        self._feed_limit = feed_limit
        self._category_ttl_seconds = category_ttl_seconds
        self._ttl_jitter_ratio = ttl_jitter_ratio
        self._batch_interval_seconds = batch_interval_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._retry_after_max_seconds = retry_after_max_seconds
        self._retention_seconds = retention_seconds
        self._coordinator = _coordinator_for(self._snapshot_path)

    def close(self) -> None:
        if not self._owns_executor:
            return
        with self._executor_lock:
            executor = self._executor
            self._executor = None
            if isinstance(executor, ThreadPoolExecutor):
                executor.shutdown(wait=True, cancel_futures=True)

    def start(self) -> None:
        if not self._owns_executor:
            return
        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="popular-refresh")

    def _active_executor(self) -> RefreshExecutor:
        self.start()
        with self._executor_lock:
            if self._executor is None:
                raise RuntimeError("Popular discovery is not running.")
            return self._executor

    def get_snapshot(self) -> PopularSnapshot:
        """Return the best cached snapshot and schedule at most one due refresh."""

        now = self._clock()
        should_schedule = False
        with self._coordinator.lock:
            store = self._load_store(now)
            if (
                not self._coordinator.refreshing
                and now >= self._number(store.get("next_batch_at"), 0.0)
                and self._has_due_category(store, now)
            ):
                self._coordinator.refreshing = True
                should_schedule = True

        if should_schedule:
            try:
                self._active_executor().submit(self._refresh_job)
            except Exception:  # Scheduling failure is a sanitized availability failure.
                with self._coordinator.lock:
                    self._coordinator.refreshing = False
                    store = self._load_store(now)
                    store["last_error"] = PUBLIC_PROVIDER_ERROR
                    store["next_batch_at"] = now + self._backoff_base_seconds
                    self._save_store(store)

        with self._coordinator.lock:
            store = self._load_store(self._clock())
            return self._build_snapshot(store, self._clock(), refreshing=self._coordinator.refreshing)

    def candidates(self) -> tuple[PopularItem, ...]:
        """Every ranked item of every category, uncut by ``feed_limit``.

        A pure read of the stored snapshot: it never schedules a refresh and never touches a provider, so it is safe on a
        request thread. ``get_snapshot`` stays the one place a refresh starts.
        """
        now = self._clock()
        with self._coordinator.lock:
            records = self._load_store(now).get("categories")
        return tuple(self._rank_items(records if isinstance(records, Mapping) else {}, now, cut=False))

    def _refresh_job(self) -> None:
        try:
            self._refresh_batch()
        except Exception:
            now = self._clock()
            with self._coordinator.lock:
                store = self._load_store(now)
                store["last_error"] = PUBLIC_PROVIDER_ERROR
                store["next_batch_at"] = max(
                    self._number(store.get("next_batch_at"), 0.0),
                    now + self._backoff_base_seconds,
                )
                self._save_store(store)
        finally:
            with self._coordinator.lock:
                self._coordinator.refreshing = False

    def _refresh_batch(self) -> None:
        started_at = self._clock()
        with self._coordinator.lock:
            store = self._load_store(started_at)
            selected = self._select_due_categories(store, started_at)
            if not selected:
                return
            store["next_batch_at"] = started_at + self._batch_interval_seconds
            store["cursor"] = (self._categories.index(selected[-1]) + 1) % len(self._categories)
            store["refreshed_at"] = started_at
            self._save_store(store)

        outcomes = self._search_categories(selected)
        finished_at = self._clock()
        any_success = False
        any_failure = False
        with self._coordinator.lock:
            store = self._load_store(finished_at)
            records = store.setdefault("categories", {})
            for category in selected:
                outcome = outcomes[category.key]
                previous = records.get(category.key)
                record = previous if isinstance(previous, dict) else {}
                if isinstance(outcome, provider_budget.BudgetExhausted):
                    record["next_refresh_at"] = finished_at + BUDGET_RETRY_SECONDS
                elif isinstance(outcome, Exception):
                    any_failure = True
                    self._note_failure(record, outcome, finished_at)
                    record["last_attempt_at"] = finished_at
                else:
                    any_success = True
                    jitter = 1 + ((self._bounded_random() * 2) - 1) * self._ttl_jitter_ratio
                    record = {
                        "items": outcome,
                        "last_success_at": finished_at,
                        "last_attempt_at": finished_at,
                        "next_refresh_at": finished_at + self._category_ttl_seconds * jitter,
                        "failure_count": 0,
                        "error": None,
                    }
                records[category.key] = record

            store["refreshed_at"] = finished_at
            if any_success:
                store["last_success_at"] = finished_at
            store["last_error"] = PUBLIC_PROVIDER_ERROR if any_failure else None
            self._prune_store(store, finished_at)
            self._save_store(store)

    def _note_failure(self, record: dict[str, Any], error: Exception, now: float) -> None:
        """The one back-off rule for a category, shared by the background refresh and the wall's deep fetch."""
        failures = min(31, int(self._number(record.get("failure_count"), 0)) + 1)
        exponential = min(self._backoff_max_seconds, self._backoff_base_seconds * (2 ** (failures - 1)))
        retry_after = self._retry_after_seconds(error, now)
        record["failure_count"] = failures
        record["next_refresh_at"] = now + max(exponential, retry_after or 0.0)
        record["error"] = PUBLIC_PROVIDER_ERROR

    def _search_categories(self, categories: Sequence[PopularCategory]) -> dict[str, list[dict[str, Any]] | Exception]:
        if self._max_concurrency == 1:
            outcomes: dict[str, list[dict[str, Any]] | Exception] = {}
            for category in categories:
                try:
                    outcomes[category.key] = self._run_search(category)
                except Exception as exc:
                    outcomes[category.key] = exc
            return outcomes

        outcomes = {}
        with ThreadPoolExecutor(max_workers=self._max_concurrency, thread_name_prefix="popular-category") as pool:
            pending = {pool.submit(self._run_search, category): category for category in categories}
            for future in as_completed(pending):
                category = pending[future]
                try:
                    outcomes[category.key] = future.result()
                except Exception as exc:
                    outcomes[category.key] = exc
        return outcomes

    def _run_search(self, category: PopularCategory) -> list[dict[str, Any]]:
        with provider_budget.priority("background"):
            return self._run_search_rows(category)

    def _run_search_rows(self, category: PopularCategory) -> list[dict[str, Any]]:
        items = self._playable(self._normalize_search_items(self._search(category.query, self._per_category_limit)))
        seen = {self._dedupe_key(item) for item in items}
        for query in category.related:
            if len(items) >= MIN_ROW_ITEMS:
                break
            try:  # a failing backfill never costs the row its primary results (a 429 here also stops the backfill)
                extra = self._playable(self._normalize_search_items(self._search(query, self._per_category_limit)))
            except Exception:
                break
            for item in extra:
                if self._dedupe_key(item) not in seen and len(items) < self._per_category_limit:
                    seen.add(self._dedupe_key(item))
                    items.append(item)
        return items

    @staticmethod
    def _playable(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop Shorts, which no rail shows for anyone (reco pool rule); backfill counts only what survives."""
        return [
            item for item in items
            if not ((item.get("duration") is not None and item["duration"] < _SHORT_SECONDS)
                    or "/shorts/" in (item.get("webpage_url") or ""))
        ]

    def wall(self, category_key: str, cursor: str | None, limit: int) -> tuple[list[PopularItem], str | None]:
        """One page of a category's See-all wall from the cache; the next upstream page is fetched only when the page needs it.

        The cursor is an opaque offset token. Raises KeyError for an unknown category and ValueError for a bad cursor."""
        category = self._category_by_key[category_key]
        offset = self._decode_cursor(cursor)
        limit = max(1, min(limit, 100))
        with self._coordinator.wall_locks.setdefault(category_key, threading.Lock()):
            # One deep fetch per category: concurrent callers wait here, then find the cache already filled and join it.
            with self._coordinator.lock:
                record = self._load_store(self._clock()).get("categories", {}).get(category_key) or {}
                have = [dict(i) for i in (record.get("wall") or record.get("items") or []) if isinstance(i, dict)]
                exhausted = bool(record.get("wall_exhausted"))
                backing_off = int(self._number(record.get("failure_count"), 0)) > 0 and self._clock() < self._number(record.get("next_refresh_at"), 0.0)
            if len(have) < offset + limit and not exhausted and len(have) < WALL_MAX and not backing_off:
                want = min(WALL_MAX, max(offset + limit, len(have) + self._per_category_limit))
                try:
                    # A member opening the wall waits for its first page; deeper pages are background work.
                    with provider_budget.priority("interactive" if offset == 0 else "background"):
                        normalized = self._normalize_search_items(self._search(category.query, want), limit=want)
                except provider_budget.BudgetExhausted:
                    backing_off = True  # serve what is cached; the next page asks again once the budget allows
                except Exception as exc:  # serve what is cached and back the category off like a failed refresh
                    backing_off = True
                    with self._coordinator.lock:
                        store = self._load_store(self._clock())
                        stored = store.get("categories", {}).get(category_key)
                        if isinstance(stored, dict):
                            self._note_failure(stored, exc, self._clock())
                            self._save_store(store)
                else:
                    known = {self._dedupe_key(i) for i in have}  # cached items keep their positions so earlier cursors stay valid
                    have = (have + [i for i in self._playable(normalized) if self._dedupe_key(i) not in known])[:WALL_MAX]
                    exhausted = len(normalized) < want
                    with self._coordinator.lock:
                        store = self._load_store(self._clock())
                        stored = store.get("categories", {}).get(category_key)
                        if isinstance(stored, dict):
                            stored["wall"], stored["wall_exhausted"] = have, exhausted
                            self._save_store(store)
        page = have[offset:offset + limit]
        more = offset + limit < len(have) or (not backing_off and not exhausted and len(have) < WALL_MAX and bool(page))
        return [self._public_item({**i, "category_keys": [category_key]}) for i in page], \
            (self._encode_cursor(offset + len(page)) if more else None)

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(f"w{offset}".encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
            offset = int(text[1:]) if text.startswith("w") else -1
        except (ValueError, UnicodeError):
            raise ValueError("Invalid cursor.") from None
        if not 0 <= offset <= WALL_MAX:
            raise ValueError("Invalid cursor.")
        return offset

    def _normalize_search_items(self, response: object, limit: int | None = None) -> list[dict[str, Any]]:
        if isinstance(response, Mapping):
            raw_items = response.get("items", [])
        elif hasattr(response, "items") and not isinstance(getattr(response, "items"), Callable):
            raw_items = getattr(response, "items")
        elif isinstance(response, Sequence) and not isinstance(response, (str, bytes, bytearray)):
            raw_items = response
        else:
            raise ValueError("The provider returned an invalid search response.")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
            raise ValueError("The provider returned invalid search items.")

        normalized: list[dict[str, Any]] = []
        for raw_item in raw_items[: limit or self._per_category_limit]:
            item = self._item_mapping(raw_item)
            if item is None:
                continue
            item_id = self._text(item.get("id"), 256)
            title = self._text(item.get("title"), 500)
            webpage_url = self._text(item.get("webpage_url"), 2_048)
            if item_id is None and title is None and webpage_url is None:
                continue
            capabilities = self._capabilities(item.get("capabilities"))
            normalized.append(
                {
                    "id": item_id,
                    "title": title,
                    "uploader": self._text(item.get("uploader"), 300),
                    # Channel identity survives so #87 can derive a followable
                    # channel candidate from the video-shaped popular record.
                    "uploader_url": self._text(item.get("uploader_url") or item.get("channel_url"), 2_048),
                    "uploader_id": self._text(item.get("channel_id") or item.get("uploader_id"), 256),
                    "duration": self._nonnegative_integer(item.get("duration")),
                    "thumbnail": self._text(item.get("thumbnail"), 2_048),
                    # Opaque artwork IDs are process-local capabilities and
                    # must never be persisted.  Keep the provider thumbnail;
                    # the authenticated route registers it on each response.
                    "artwork_url": None,
                    "webpage_url": webpage_url,
                    "view_count": self._nonnegative_integer(item.get("view_count")),
                    "availability": self._text(item.get("availability"), 64),
                    "published_at": self._timestamp(item.get("published_at")),
                    "source": self._text(item.get("source"), 64) or "youtube",
                    "source_label": self._text(item.get("source_label"), 100) or "YouTube",
                    "capabilities": capabilities.model_dump(mode="json") if capabilities else None,
                }
            )
        return normalized

    @staticmethod
    def _capabilities(value: object) -> MediaSourceCapabilities | None:
        if not isinstance(value, Mapping):
            return None
        try:
            return MediaSourceCapabilities.model_validate(value)
        except ValueError:
            return None

    @staticmethod
    def _item_mapping(item: object) -> Mapping[str, Any] | None:
        if isinstance(item, Mapping):
            return item
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="python")
            return dumped if isinstance(dumped, Mapping) else None
        if is_dataclass(item) and not isinstance(item, type):
            dumped = asdict(item)
            return dumped if isinstance(dumped, Mapping) else None
        return None

    def _select_due_categories(self, store: Mapping[str, Any], now: float) -> list[PopularCategory]:
        records = store.get("categories")
        records = records if isinstance(records, Mapping) else {}
        cursor = int(self._number(store.get("cursor"), 0)) % len(self._categories)
        selected: list[PopularCategory] = []
        for offset in range(len(self._categories)):
            category = self._categories[(cursor + offset) % len(self._categories)]
            record = records.get(category.key)
            due = not isinstance(record, Mapping) or now >= self._number(record.get("next_refresh_at"), 0.0)
            if due:
                selected.append(category)
                if len(selected) == self._batch_size:
                    break
        return selected

    def _has_due_category(self, store: Mapping[str, Any], now: float) -> bool:
        return bool(self._select_due_categories(store, now))

    def _load_store(self, now: float) -> dict[str, Any]:
        loaded: dict[str, Any] | None = None
        try:
            raw = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("version") == _STORE_VERSION:
                loaded = raw
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        if loaded is None:
            loaded = self._coordinator.memory_store or self._empty_store()
        self._prune_store(loaded, now)
        self._coordinator.memory_store = loaded
        return loaded

    @staticmethod
    def _empty_store() -> dict[str, Any]:
        return {
            "version": _STORE_VERSION,
            "cursor": 0,
            "categories": {},
            "next_batch_at": 0.0,
            "last_success_at": None,
            "refreshed_at": None,
            "last_error": None,
        }

    def _save_store(self, store: dict[str, Any]) -> None:
        self._coordinator.memory_store = store
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self._snapshot_path,
            json.dumps(store, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True),
        )

    def _prune_store(self, store: dict[str, Any], now: float) -> None:
        raw_records = store.get("categories")
        records = raw_records if isinstance(raw_records, dict) else {}
        for key in tuple(records):
            if key not in self._category_by_key or not isinstance(records[key], dict):
                records.pop(key, None)
                continue
            record = records[key]
            success_at = self._optional_number(record.get("last_success_at"))
            if success_at is not None and now - success_at > self._retention_seconds:
                record["items"] = []
                record["last_success_at"] = None
            items = record.get("items")
            if not isinstance(items, list):
                record["items"] = []
            else:
                record["items"] = [item for item in items[: self._per_category_limit] if isinstance(item, dict)]
        store["categories"] = records
        store["version"] = _STORE_VERSION

    def _build_snapshot(self, store: Mapping[str, Any], now: float, *, refreshing: bool) -> PopularSnapshot:
        raw_records = store.get("categories")
        records = raw_records if isinstance(raw_records, Mapping) else {}
        category_snapshots: list[PopularCategorySnapshot] = []
        completed = 0
        failed = 0
        has_error = False
        stale = False
        next_due_times: list[float] = []
        for category in self._categories:
            raw_record = records.get(category.key)
            record = raw_record if isinstance(raw_record, Mapping) else None
            if record is None:
                state: CategoryState = "pending"
                last_success = None
                next_refresh = now
            else:
                last_success = self._optional_number(record.get("last_success_at"))
                next_refresh = self._number(record.get("next_refresh_at"), now)
                items = record.get("items") if isinstance(record.get("items"), list) else []
                error = record.get("error") == PUBLIC_PROVIDER_ERROR
                has_error = has_error or error
                if last_success is not None:
                    completed += 1
                if error and last_success is not None:
                    state = "stale"
                elif error:
                    state = "failed"
                    failed += 1
                elif last_success is None:
                    state = "pending"
                elif now >= next_refresh:
                    state = "stale"
                elif items:
                    state = "ready"
                else:
                    state = "empty"
                stale = stale or state == "stale"
            next_due_times.append(next_refresh)
            category_snapshots.append(
                PopularCategorySnapshot(
                    key=category.key,
                    label=category.label,
                    state=state,
                    last_success_at=self._datetime(last_success),
                    next_refresh_at=self._datetime(next_refresh),
                )
            )

        items = self._rank_items(records, now)
        if completed == 0 and (failed > 0 or store.get("last_error") == PUBLIC_PROVIDER_ERROR):
            state: PopularState = "failed"
        elif completed == 0:
            state = "loading"
        elif completed < len(self._categories):
            state = "partial"
        elif stale:
            state = "stale"
        elif not items:
            state = "empty"
        else:
            state = "ready"

        next_category_due = min(next_due_times) if next_due_times else now
        next_refresh = max(self._number(store.get("next_batch_at"), 0.0), next_category_due)
        public_error = PUBLIC_PROVIDER_ERROR if has_error or store.get("last_error") == PUBLIC_PROVIDER_ERROR else None
        return PopularSnapshot(
            items=tuple(items),
            categories=tuple(category_snapshots),
            state=state,
            refreshing=refreshing,
            stale=stale,
            last_success_at=self._datetime(self._optional_number(store.get("last_success_at"))),
            refreshed_at=self._datetime(self._optional_number(store.get("refreshed_at"))),
            next_refresh_at=self._datetime(next_refresh),
            error=public_error,
        )

    def _rank_items(self, records: Mapping[str, Any], now: float, *, cut: bool = True) -> list[PopularItem]:
        deduplicated: dict[str, dict[str, Any]] = {}
        category_positions = {category.key: index for index, category in enumerate(self._categories)}
        for category in self._categories:
            raw_record = records.get(category.key)
            if not isinstance(raw_record, Mapping):
                continue
            raw_items = raw_record.get("items")
            if not isinstance(raw_items, list):
                continue
            for position, raw_item in enumerate(raw_items[: self._per_category_limit]):
                if not isinstance(raw_item, Mapping):
                    continue
                item = dict(raw_item)
                key = self._dedupe_key(item)
                existing = deduplicated.get(key)
                if existing is None:
                    item["category_keys"] = [category.key]
                    item["primary_category"] = category.key
                    item["provider_position"] = position
                    deduplicated[key] = item
                    continue
                if category.key not in existing["category_keys"]:
                    existing["category_keys"].append(category.key)
                for field_name in (
                    "id",
                    "title",
                    "uploader",
                    "duration",
                    "thumbnail",
                    "artwork_url",
                    "webpage_url",
                    "availability",
                ):
                    if existing.get(field_name) is None and item.get(field_name) is not None:
                        existing[field_name] = item[field_name]
                existing["view_count"] = self._maximum_optional(existing.get("view_count"), item.get("view_count"))
                existing["published_at"] = self._maximum_optional(existing.get("published_at"), item.get("published_at"))

        signaled: list[tuple[str, dict[str, Any]]] = []
        fallback_buckets: dict[str, deque[tuple[str, dict[str, Any]]]] = {
            category.key: deque() for category in self._categories
        }
        for key, item in deduplicated.items():
            if item.get("view_count") is not None or item.get("published_at") is not None:
                signaled.append((key, item))
            else:
                fallback_buckets[item["primary_category"]].append((key, item))

        signaled.sort(
            key=lambda entry: (
                -self._popularity_score(entry[1], now),
                category_positions[entry[1]["primary_category"]],
                int(entry[1]["provider_position"]),
                entry[0],
            )
        )
        ordered = signaled
        while any(fallback_buckets.values()):
            for category in self._categories:
                bucket = fallback_buckets[category.key]
                if bucket:
                    ordered.append(bucket.popleft())

        # cut=False: ranks every category's items, not the 24-item feed.
        return [self._public_item(item) for _key, item in (ordered[: self._feed_limit] if cut else ordered)]

    @staticmethod
    def _popularity_score(item: Mapping[str, Any], now: float) -> float:
        view_count = PopularDiscovery._optional_number(item.get("view_count"))
        published_at = PopularDiscovery._optional_number(item.get("published_at"))
        views = math.log1p(max(0.0, view_count)) if view_count is not None else 0.0
        if published_at is None:
            return views
        age_days = max(0.0, now - published_at) / 86_400
        recency = 12.0 / (1.0 + age_days / 30.0)
        return views + recency

    @staticmethod
    def _public_item(item: Mapping[str, Any]) -> PopularItem:
        published_at = PopularDiscovery._optional_number(item.get("published_at"))
        return PopularItem(
            id=item.get("id") if isinstance(item.get("id"), str) else None,
            title=item.get("title") if isinstance(item.get("title"), str) else None,
            uploader=item.get("uploader") if isinstance(item.get("uploader"), str) else None,
            duration=PopularDiscovery._nonnegative_integer(item.get("duration")),
            thumbnail=item.get("thumbnail") if isinstance(item.get("thumbnail"), str) else None,
            artwork_url=None,
            webpage_url=item.get("webpage_url") if isinstance(item.get("webpage_url"), str) else None,
            view_count=PopularDiscovery._nonnegative_integer(item.get("view_count")),
            availability=item.get("availability") if isinstance(item.get("availability"), str) else None,
            published_at=PopularDiscovery._datetime(published_at),
            source=item.get("source") if isinstance(item.get("source"), str) else "youtube",
            source_label=item.get("source_label") if isinstance(item.get("source_label"), str) else "YouTube",
            capabilities=PopularDiscovery._capabilities(item.get("capabilities")),
            category_keys=tuple(key for key in item.get("category_keys", []) if isinstance(key, str)),
            uploader_url=item.get("uploader_url") if isinstance(item.get("uploader_url"), str) else None,
            uploader_id=item.get("uploader_id") if isinstance(item.get("uploader_id"), str) else None,
        )

    @staticmethod
    def _dedupe_key(item: Mapping[str, Any]) -> str:
        return _source_key(
            str(item.get("source") or "youtube"),
            str(item.get("id") or ""),
            str(item.get("webpage_url") or "").strip(),
            str(item.get("title") or ""),
            str(item.get("uploader") or ""),
        )

    def _retry_after_seconds(self, error: BaseException, now: float) -> float | None:
        headers = getattr(error, "headers", None)
        if not isinstance(headers, Mapping):
            response = getattr(error, "response", None)
            headers = getattr(response, "headers", None)
        if not isinstance(headers, Mapping):
            retry_after = getattr(error, "retry_after", None)
        else:
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after is None:
            return None
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(retry_after))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                seconds = retry_at.timestamp() - now
            except (TypeError, ValueError, OverflowError):
                return None
        return min(self._retry_after_max_seconds, max(0.0, seconds))

    def _bounded_random(self) -> float:
        try:
            value = float(self._random())
        except (TypeError, ValueError, OverflowError):
            return 0.5
        if not math.isfinite(value):
            return 0.5
        return min(1.0, max(0.0, value))

    @staticmethod
    def _text(value: object, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized[:limit] if normalized else None

    @staticmethod
    def _nonnegative_integer(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            return max(0, int(value)) if math.isfinite(float(value)) else None
        except (OverflowError, ValueError):
            return None

    @staticmethod
    def _timestamp(value: object) -> float | None:
        if isinstance(value, datetime):
            normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            return normalized.timestamp()
        if isinstance(value, str):
            try:
                normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
                normalized = normalized.replace(tzinfo=UTC) if normalized.tzinfo is None else normalized.astimezone(UTC)
                return normalized.timestamp()
            except ValueError:
                return None
        return PopularDiscovery._optional_number(value)

    @staticmethod
    def _datetime(value: float | None) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _number(value: object, default: float) -> float:
        parsed = PopularDiscovery._optional_number(value)
        return default if parsed is None else parsed

    @staticmethod
    def _optional_number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            parsed = float(value)
        except (OverflowError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _maximum_optional(first: object, second: object) -> int | float | None:
        first_number = PopularDiscovery._optional_number(first)
        second_number = PopularDiscovery._optional_number(second)
        if first_number is None:
            return second_number
        if second_number is None:
            return first_number
        return max(first_number, second_number)


def _source_key(source: str | None, item_id: str | None, webpage_url: str | None, title: str | None, uploader: str | None) -> str:
    """A stable, comparable identity for one Media source across every pool.

    Home, Up Next, the current-playback context, Continue Watching, and the
    visible-Library exclusion all derive keys through this one helper so a source
    matches itself regardless of which surface produced it.
    """
    normalized_source = (source or "youtube").strip().casefold()
    if item_id and item_id.strip():
        return f"id:{normalized_source}:{item_id.strip()}"
    if webpage_url and webpage_url.strip():
        try:
            parsed = urlsplit(webpage_url)
            hostname = (parsed.hostname or "").casefold()
            if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
                video_id = parsed.path.lstrip("/") if hostname == "youtu.be" else parse_qs(parsed.query).get("v", [""])[0]
                if video_id:
                    return f"id:youtube:{video_id}"
            canonical = urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), parsed.query, ""))
            return f"url:{canonical}"
        except ValueError:
            return f"url:{webpage_url.strip()}"
    return f"text:{normalized_source}:{(title or '').strip().casefold()}:{(uploader or '').strip().casefold()}"


def viable_category_keys(items: Sequence[Any], categories: Sequence[Any]) -> set[str]:
    """Keys of the categories that still hold at least OMIT_ROW_BELOW of ``items`` (the member's filtered rails)."""
    counts: dict[str, int] = {}
    for item in items:
        for key in item.category_keys:
            counts[key] = counts.get(key, 0) + 1
    return {c.key for c in categories if counts.get(c.key, 0) >= OMIT_ROW_BELOW}


def cap_rows(items: Sequence[Any], cap: int = 20) -> list[Any]:
    """At most ``cap`` items per category row, best first: an item loses the category keys of rows already full
    and is dropped when none are left. The See-all wall keeps the full list."""
    counts: dict[str, int] = {}
    kept = []
    for item in items:
        keys = [k for k in item.category_keys if counts.get(k, 0) < cap]
        if not keys:
            continue
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
        kept.append(item if len(keys) == len(item.category_keys) else item.model_copy(update={"category_keys": keys}))
    return kept
