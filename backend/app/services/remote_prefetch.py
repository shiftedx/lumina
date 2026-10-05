"""Pre-resolve a remote source on intent: a card hovered or focused for 600 ms warms the preview cache
that the click then reads, so the click usually skips extraction. Bounded so a mouse sweep never starts a storm:
one extraction per source in flight, at most ``max_per_member`` per member and ``max_household`` across the household, the rest dropped (never queued)."""
from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait

logger = logging.getLogger(__name__)


class IntentPrefetcher:
    def __init__(self, *, max_per_member: int = 2, max_household: int = 3, max_workers: int = 4) -> None:
        self._max_per_member, self._max_household = max_per_member, max_household
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lumina-prefetch")
        self._lock = threading.Lock()
        self._sources: set[str] = set()
        self._active: Counter[str] = Counter()
        self._futures: set[Future] = set()

    def submit(self, member_id: str, source_url: str, job: Callable[[], object], *, household_cap: bool = True) -> bool:
        """Run ``job`` in the background unless ``source_url`` is already resolving or the member (or, for hover
        prefetch, the household) is at their cap. Work for a click that already happened sets ``household_cap=False``."""
        with self._lock:
            if (
                source_url in self._sources
                or self._active[member_id] >= self._max_per_member
                or (household_cap and len(self._sources) >= self._max_household)
            ):
                return False
            self._sources.add(source_url)
            self._active[member_id] += 1

        def run() -> None:
            try:
                job()
            except Exception:  # noqa: BLE001 - a warm-up never fails anything; the click reports its own errors
                logger.debug("Intent prefetch failed", exc_info=True)
            finally:
                with self._lock:
                    self._sources.discard(source_url)
                    self._active[member_id] -= 1

        future = self._pool.submit(run)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(lambda done: self._discard(done))
        return True

    def _discard(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

    def drain(self, timeout: float = 5) -> None:
        """Wait for running prefetches (tests)."""
        with self._lock:
            pending = set(self._futures)
        wait(pending, timeout=timeout)
