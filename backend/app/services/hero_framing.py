"""Bounded, local hero-art framing analysis.

The browser may ask for framing for an artwork it is about to show, but never
performs analysis itself.  Requests only validate and enqueue a source; one
low-priority worker fetches the already-authorised image, runs the bundled
analyser, and keeps a tiny disposable result cache.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.config import settings
from app.persistence import atomic_write, read_regular_file
from app.services import art_urls, raster_images
from app.services.background_work import background_work
from app.services.local_playback_sessions import sessions

ANALYSIS_VERSION = 2
MAX_PENDING = 32
MAX_WARM_PENDING = 8
WARM_CANDIDATES = 4
MAX_MEMORY = 128
MAX_CACHE_FILES = 512
MAX_CACHE_BYTES = 8192
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_DIMENSION = 16_000
TIMEOUT_SECONDS = 8.0
# TMDB fetches use the main artwork service's bounded 20 second transport.
# Allow that fetch plus the analyser's timeout to leave cleanly on shutdown.
SHUTDOWN_JOIN_SECONDS = TIMEOUT_SECONDS + 20.0 + 2.0
UNAVAILABLE_SECONDS = 60 * 60
POLL_SECONDS = 1.0
WARM_GAP_SECONDS = 2.0
CATALOG_SOURCE_TTL_SECONDS = 24 * 60 * 60
MAX_CATALOG_SOURCES = 2_048
MAX_CATALOG_NODES = 2_048

State = Literal["pending", "ready", "unavailable"]
Loader = Callable[[], tuple[bytes, str] | None]


@dataclass(frozen=True)
class Result:
    state: State
    width: int | None = None
    height: int | None = None
    regions: tuple[dict[str, Any], ...] = ()
    digest: str | None = None
    expires_at: float | None = None

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"state": self.state}
        if self.state == "ready":
            body.update({"width": self.width, "height": self.height, "regions": list(self.regions)})
        return body


def cache_root() -> Path:
    return settings.data_dir / "artwork-cache" / "hero-framing"


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError("Hero framing cache is not a private directory.")
    return path


def _cache_path(key: str) -> Path:
    return cache_root() / key[:2] / f"{key}.json"


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _validate(payload: object) -> Result | None:
    if not isinstance(payload, dict) or payload.get("version") != ANALYSIS_VERSION:
        return None
    state = payload.get("state")
    if state == "unavailable":
        expires_at = _finite(payload.get("expires_at"))
        if expires_at is None or expires_at <= time.time():
            return None
        return Result("unavailable", expires_at=expires_at)
    if state != "ready":
        return None
    width, height = payload.get("width"), payload.get("height")
    if not isinstance(width, int) or isinstance(width, bool) or not isinstance(height, int) or isinstance(height, bool):
        return None
    if width <= 0 or height <= 0 or width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        return None
    digest = payload.get("digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    raw_regions = payload.get("regions")
    if not isinstance(raw_regions, list) or len(raw_regions) > 16:
        return None
    regions: list[dict[str, Any]] = []
    for raw in raw_regions:
        if not isinstance(raw, dict) or raw.get("kind") not in ("face", "subject"):
            return None
        values = {name: _finite(raw.get(name)) for name in ("x", "y", "width", "height", "weight")}
        if any(value is None for value in values.values()):
            return None
        x, y, region_width, region_height, weight = (values[name] for name in ("x", "y", "width", "height", "weight"))
        if x < 0 or y < 0 or region_width <= 0 or region_height <= 0 or weight < 0 or weight > 1 or x + region_width > 1 or y + region_height > 1:
            return None
        regions.append({"x": x, "y": y, "width": region_width, "height": region_height, "kind": raw["kind"], "weight": weight})
    return Result("ready", width=width, height=height, regions=tuple(regions), digest=digest)


def _read(key: str) -> Result | None:
    try:
        return _validate(json.loads(read_regular_file(_cache_path(key), MAX_CACHE_BYTES).decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _write(key: str, result: Result) -> None:
    root = _private_dir(cache_root())
    path = root / key[:2] / f"{key}.json"
    _private_dir(path.parent)
    payload: dict[str, Any] = {"version": ANALYSIS_VERSION, "state": result.state}
    if result.state == "ready":
        payload.update({"width": result.width, "height": result.height, "regions": list(result.regions), "digest": result.digest})
    else:
        payload["expires_at"] = result.expires_at
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_CACHE_BYTES:
        raise ValueError("Hero framing result exceeded the cache limit.")
    atomic_write(path, encoded)


def _sweep() -> None:
    root = cache_root()
    if root.is_symlink() or not root.is_dir():
        return
    entries: list[tuple[float, Path]] = []
    for shard in root.iterdir():
        if shard.is_symlink() or not shard.is_dir() or len(shard.name) != 2:
            continue
        for path in shard.glob("*.json"):
            try:
                info = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            entries.append((info.st_mtime, path))
    for _, path in sorted(entries)[:-MAX_CACHE_FILES]:
        with contextlib.suppress(OSError):
            path.unlink()


def analyze(data: bytes, *, on_process: Callable[[subprocess.Popen[bytes] | None], None] | None = None) -> Result:
    """Run the bundled analyser in a bounded, thread-limited child process."""
    size = raster_images.image_size(data)
    if size is None or not all(size) or size[0] > MAX_DIMENSION or size[1] > MAX_DIMENSION or size[0] * size[1] > MAX_PIXELS:
        return Result("unavailable", expires_at=time.time() + UNAVAILABLE_SECONDS)
    environment = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENCV_FOR_THREADS_NUM": "1"}
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.services.hero_analyzer"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=environment,
        )
        if on_process is not None:
            on_process(process)
        with contextlib.suppress(OSError):
            os.setpriority(os.PRIO_PROCESS, process.pid, 19)
        stdout, _ = process.communicate(input=data, timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            with contextlib.suppress(OSError):
                process.kill()
            with contextlib.suppress(OSError):
                process.wait()
        return Result("unavailable", expires_at=time.time() + UNAVAILABLE_SECONDS)
    finally:
        if on_process is not None:
            on_process(None)
    if process.returncode != 0 or len(stdout) > MAX_CACHE_BYTES:
        return Result("unavailable", expires_at=time.time() + UNAVAILABLE_SECONDS)
    try:
        raw = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Result("unavailable", expires_at=time.time() + UNAVAILABLE_SECONDS)
    payload = {"version": ANALYSIS_VERSION, "state": "ready", "digest": hashlib.sha256(data).hexdigest(), **(raw if isinstance(raw, dict) else {})}
    return _validate(payload) or Result("unavailable", expires_at=time.time() + UNAVAILABLE_SECONDS)


def local_loader(
    title_id: str,
    expected: str,
    key: str,
    artwork: Any = None,
    *,
    user_id: str | None = None,
    offline: bool = False,
    session_factory: Callable[[], Any] | None = None,
) -> Loader:
    """Deferred current-source loader. Warm callers use cache-only artwork and recheck visibility."""
    def load() -> tuple[bytes, str] | None:
        from app.db import SessionLocal
        from app.models import MediaTitle, User
        from app.services.artwork import ArtworkError
        from app.services.renditions import offline_artwork
        from app.services.titles import TitleService, image_url, title_image_bytes, title_image_source

        with (session_factory or SessionLocal)() as db:
            user = db.get(User, user_id) if user_id is not None else None
            if user_id is not None and user is None:
                return None
            title = TitleService(db).get_visible(title_id, user) if user is not None else db.get(MediaTitle, title_id)
            if title is None or image_url(title, "Backdrop") != expected:
                return None
            source = title_image_source(title, "Backdrop")
            if source is None or art_urls.source_key(title.id, "Backdrop", source) != key:
                return None
            try:
                content_type, content = title_image_bytes(db, title, "Backdrop", offline_artwork() if offline else artwork)
                return content, content_type
            except (ArtworkError, FileNotFoundError):
                return None
    return load


def remote_loader(path: str, artwork: Any = None, *, offline: bool = False) -> Loader:
    """Deferred TMDB reader; cache-only warm reads never initiate network traffic."""
    def load() -> tuple[bytes, str] | None:
        from app.services import tmdb
        from app.services.artwork import ArtworkError
        from app.services.renditions import offline_artwork

        try:
            response = tmdb.load_image(offline_artwork() if offline else artwork, path, "Backdrop", size="w1280", max_bytes=MAX_INPUT_BYTES)
        except ArtworkError:
            return None
        return response.content, response.content_type
    return load


class HeroFraming:
    """One dormant-on-idle worker; callers provide only deferred byte loaders."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: OrderedDict[str, Loader] = OrderedDict()
        self._warm: OrderedDict[str, Loader] = OrderedDict()
        self._running: set[str] = set()
        self._running_warm: set[str] = set()
        self._interactive_retries: dict[str, Loader] = {}
        self._cache: OrderedDict[str, Result] = OrderedDict()
        self._child: subprocess.Popen[bytes] | None = None
        self._last_was_warm = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hero-framing")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._child is not None:
            with contextlib.suppress(OSError):
                self._child.kill()
        if self._thread is not None:
            self._thread.join(timeout=SHUTDOWN_JOIN_SECONDS)

    def request(self, key: str, loader: Loader) -> Result:
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                if cached.expires_at is not None and cached.expires_at <= time.time():
                    self._cache.pop(key, None)
                else:
                    self._cache.move_to_end(key)
                    return cached
        cached = _read(key)
        if cached is not None:
            self._remember(key, cached)
            return cached
        if not self.running:
            return Result("unavailable")
        with self._lock:
            if key in self._warm:
                if len(self._pending) >= MAX_PENDING:
                    return Result("unavailable")
                self._warm.pop(key)
                self._pending[key] = loader  # an opened title preempts its own warm-up
            elif key in self._running:
                # A cache-only warm read may miss; retain the guarded interactive
                # loader so that miss can immediately become high-priority work.
                if key in self._running_warm:
                    if len(self._pending) >= MAX_PENDING:
                        return Result("unavailable")
                    self._interactive_retries[key] = loader
            elif key not in self._pending and key not in self._running:
                if len(self._pending) >= MAX_PENDING:
                    return Result("unavailable")
                self._pending[key] = loader
            self._wake.set()
        return Result("pending")

    def warm(self, key: str, loader: Loader) -> None:
        """Queue a cache-only candidate after a browse surface; never displaces an interactive request."""
        if not self.running:
            return
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and (cached.expires_at is None or cached.expires_at > time.time()):
                return
        if _read(key) is not None:
            return
        with self._lock:
            if key in self._pending or key in self._warm or key in self._running:
                return
            if len(self._warm) >= MAX_WARM_PENDING:
                return
            self._warm[key] = loader
            self._wake.set()

    def warm_library(self, user_id: str, titles: Iterable[Any]) -> None:
        """At most four already-authorized movie/series Backdrops, read later from local/pinned cache only."""
        from app.services.titles import image_url, title_image_source

        found = 0
        for title in titles:
            if getattr(title, "type", None) not in ("movie", "series"):
                continue
            source = title_image_source(title, "Backdrop")
            expected = image_url(title, "Backdrop")
            if source is None or expected is None:
                continue
            key = art_urls.source_key(title.id, "Backdrop", source)
            self.warm(key, local_loader(title.id, expected, key, user_id=user_id, offline=True))
            found += 1
            if found == WARM_CANDIDATES:
                return

    def warm_catalog(self, urls: Iterable[str]) -> None:
        """At most four catalog URLs already admitted by CatalogSources; pinned-cache reads only."""
        from app.services import tmdb

        found = 0
        prefix = "https://image.tmdb.org/t/p/w1280"
        for url in urls:
            if not isinstance(url, str) or not url.startswith(prefix):
                continue
            path = tmdb._image_path(url[len(prefix):])
            if path is None or f"{prefix}{path}" != url:
                continue
            key = hashlib.sha256(f"tmdb:{path}".encode()).hexdigest()
            self.warm(key, remote_loader(path, offline=True))
            found += 1
            if found == WARM_CANDIDATES:
                return

    def _remember(self, key: str, result: Result) -> None:
        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_MEMORY:
                self._cache.popitem(last=False)

    def run_once(self) -> bool:
        with self._lock:
            if self._pending:
                key, loader = self._pending.popitem(last=False)
                warm = False
            elif self._warm:
                # Leave a low-priority candidate queued until the machine is quiet.
                # That lets an opened title promote this exact source immediately.
                if sessions.video_encodes() > 0 or background_work.busy():
                    return False
                key, loader = self._warm.popitem(last=False)
                warm = True
            else:
                return False
            self._running.add(key)
            if warm:
                self._running_warm.add(key)
            self._last_was_warm = warm
        while sessions.video_encodes() > 0 and not self._stop.wait(POLL_SECONDS):
            pass
        warm_missed = False
        try:
            if self._stop.is_set():
                return False
            if warm:
                with self._lock:
                    retry = self._interactive_retries.pop(key, None)
                    if retry is not None and len(self._pending) < MAX_PENDING:
                        self._pending[key] = retry
                        self._wake.set()
                        return False
                    if background_work.busy() or self._pending:
                        if len(self._warm) < MAX_WARM_PENDING:
                            self._warm[key] = loader
                            self._warm.move_to_end(key, last=False)
                        return False
            try:
                loaded = loader()
                if self._stop.is_set():
                    return False
                if loaded is None and warm:
                    warm_missed = True
                    return True  # cache-only misses stay eligible for a later interactive load
                result = analyze(loaded[0], on_process=self._set_child) if loaded is not None and len(loaded[0]) <= MAX_INPUT_BYTES else Result("unavailable", expires_at=time.time() + UNAVAILABLE_SECONDS)
            except Exception:
                result = Result("unavailable", expires_at=time.time() + UNAVAILABLE_SECONDS)
            # Shutdown cancellation is not an analysis failure.  Do not make the
            # next browser session wait an hour before it may try again.
            if self._stop.is_set():
                return False
            try:
                _write(key, result)
                _sweep()
            except OSError:
                pass
            self._remember(key, result)
        finally:
            with self._lock:
                self._running.discard(key)
                self._running_warm.discard(key)
                retry = self._interactive_retries.pop(key, None)
                if warm_missed and retry is not None and not self._stop.is_set() and len(self._pending) < MAX_PENDING:
                    self._pending[key] = retry
                    self._wake.set()
        return True

    def _set_child(self, process: subprocess.Popen[bytes] | None) -> None:
        self._child = process
        if process is not None and self._stop.is_set():
            with contextlib.suppress(OSError):
                process.kill()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            if self.run_once():
                if self._last_was_warm:
                    self._wake.wait(WARM_GAP_SECONDS)
                continue
            with self._lock:
                has_warm = bool(self._warm)
            self._wake.wait(POLL_SECONDS if has_warm else None)


class CatalogSources:
    """Short-lived record of backdrop URLs the signed-in catalog just issued."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: OrderedDict[str, float] = OrderedDict()

    def register(self, response: object) -> tuple[str, ...]:
        now = time.monotonic()
        found: list[str] = []
        pending: list[tuple[object, int]] = [(response, 0)]
        nodes = 0
        while pending and nodes < MAX_CATALOG_NODES and len(found) < MAX_CATALOG_SOURCES:
            value, depth = pending.pop()
            nodes += 1
            if isinstance(value, dict):
                backdrop = value.get("backdrop_url")
                if isinstance(backdrop, str):
                    found.append(backdrop)
                if depth < 8:
                    for child in value.values():
                        if nodes + len(pending) >= MAX_CATALOG_NODES:
                            break
                        pending.append((child, depth + 1))
            elif isinstance(value, list) and depth < 8:
                for child in value:
                    if nodes + len(pending) >= MAX_CATALOG_NODES:
                        break
                    pending.append((child, depth + 1))
        with self._lock:
            expired = [url for url, expiry in self._values.items() if expiry <= now]
            for url in expired:
                self._values.pop(url, None)
            for url in found:
                self._values[url] = now + CATALOG_SOURCE_TTL_SECONDS
                self._values.move_to_end(url)
            while len(self._values) > MAX_CATALOG_SOURCES:
                self._values.popitem(last=False)
        return tuple(found)

    def contains(self, url: str) -> bool:
        now = time.monotonic()
        with self._lock:
            expiry = self._values.get(url)
            if expiry is None or expiry <= now:
                self._values.pop(url, None)
                return False
            self._values.move_to_end(url)
            return True


hero_framing = HeroFraming()
catalog_sources = CatalogSources()
