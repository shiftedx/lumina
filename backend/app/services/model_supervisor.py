"""On-demand model servers.

Lumina starts a model's server as a child process only when a feature needs it. The child binds to
127.0.0.1 on a free port and requires a random per-start bearer secret that only this process holds.
The secret is passed in the child's environment, never on its command line. Callers hold a lease
(``acquire``/``release``) while they use a server; a server with no lease for IDLE_SECONDS stops on the
next maintenance tick, and every server stops on shutdown. A crash is restarted by the next request; a
second crash within CRASH_WINDOW_SECONDS marks the model failed until an admin retries. Before starting,
available memory must cover the catalog's RAM estimate plus MEMORY_MARGIN_BYTES. ``heavy`` is the lock
that keeps the CPU-bound bulk work (speech transcription, embedding backfill) from running at once.
"""
from __future__ import annotations

import logging
import math
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings
from app.services import model_catalog
from app.services.model_catalog import CatalogModel

logger = logging.getLogger(__name__)

IDLE_SECONDS = 300
CRASH_WINDOW_SECONDS = 600
START_TIMEOUT_SECONDS = 120.0
HEALTH_POLL_SECONDS = 0.1
STOP_GRACE_SECONDS = 5.0
MEMORY_MARGIN_BYTES = 512 * 1024 * 1024
MAX_THREADS = 12
STOPPED = "stopped"


class ModelUnavailable(RuntimeError):
    """The model cannot serve right now; ``str()`` is the admin-facing reason."""


@dataclass(frozen=True)
class LocalEndpoint:
    base_url: str
    secret: str


@dataclass(eq=False)
class _Server:
    model: CatalogModel
    secret: str
    port: int
    process: subprocess.Popen | None = None
    ready: bool = False
    error: str | None = None
    stopping: bool = False
    leases: int = 0
    last_used: float = 0.0


def cpu_count() -> int:
    """CPUs this container may use: its affinity mask, capped by a cgroup v2 CPU quota."""
    try:
        count = len(os.sched_getaffinity(0))
    except AttributeError:  # macOS development hosts
        count = os.cpu_count() or 1
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            count = min(count, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError):
        pass
    return count


def default_threads() -> int:
    """CPUs minus one, 1..MAX_THREADS: 3 on the 4-vCPU reference host, which the benchmark found fastest."""
    return max(1, min(MAX_THREADS, cpu_count() - 1))


def effective_threads(configured: int | None) -> int:
    """The admin's cap (``AppSettings.model_threads``) can lower the default, never raise it."""
    automatic = default_threads()
    return max(1, min(automatic, configured)) if configured else automatic


MEMINFO = Path("/proc/meminfo")
CGROUP_V2_MAX = Path("/sys/fs/cgroup/memory.max")
CGROUP_V2_CURRENT = Path("/sys/fs/cgroup/memory.current")
CGROUP_V2_STAT = Path("/sys/fs/cgroup/memory.stat")
CGROUP_V1_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
CGROUP_V1_USAGE = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")
CGROUP_V1_STAT = Path("/sys/fs/cgroup/memory/memory.stat")


def _reclaimable(stat_path: Path, key: str) -> int:
    """The cgroup's inactive page cache from memory.stat; 0 when missing or unparseable (usage is then taken as is)."""
    try:
        for line in stat_path.read_text().splitlines():
            name, _, value = line.partition(" ")
            if name == key:
                return int(value)
    except (OSError, ValueError):
        pass
    return 0


def _headroom(limit_path: Path, usage_path: Path, stat_path: Path, stat_key: str, unlimited: str | None) -> int | None:
    """limit − (usage − inactive_file) from one cgroup's files; None when absent, unreadable or unlimited.

    usage counts page cache, which a media server fills to the cap; the inactive part is reclaimed on demand.
    """
    try:
        limit = limit_path.read_text().strip()
        if limit == unlimited:
            return None
        usage = int(usage_path.read_text().strip())
        return max(0, int(limit) - max(0, usage - _reclaimable(stat_path, stat_key)))
    except (OSError, ValueError):
        return None


def available_memory() -> int | None:
    """Bytes a new child may still use: the smallest of MemAvailable, the cgroup v2 headroom (memory.max − memory.current,
    unless "max") and the cgroup v1 headroom (limit_in_bytes − usage_in_bytes), each less inactive page cache. None when nothing is known (macOS dev).

    An LXC or container often shows the host's /proc/meminfo; the cgroup files are what actually bounds this container.
    A cgroup v1 "unlimited" limit is a huge number, so min() ignores it without a special case.
    """
    candidates: list[int] = []
    try:
        for line in MEMINFO.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                candidates.append(int(line.split()[1]) * 1024)
    except (OSError, ValueError, IndexError):
        pass
    for headroom in (
        _headroom(CGROUP_V2_MAX, CGROUP_V2_CURRENT, CGROUP_V2_STAT, "inactive_file", "max"),
        _headroom(CGROUP_V1_LIMIT, CGROUP_V1_USAGE, CGROUP_V1_STAT, "total_inactive_file", None),
    ):
        if headroom is not None:
            candidates.append(headroom)
    return min(candidates) if candidates else None


def command(model: CatalogModel, port: int, threads: int, secret: str) -> tuple[list[str], dict[str, str]]:
    """argv and the complete child environment (nothing else is inherited) for one model server."""
    directory = model_catalog.model_dir(model)
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "OMP_NUM_THREADS": str(threads)}
    if model.role == "search":
        argv = [
            settings.llama_server_bin, "-m", str(directory / model.files[0].name), "--embeddings",
            "--host", "127.0.0.1", "--port", str(port), "-t", str(threads), "-tb", str(threads),
            "-np", "4", "-c", "8192", "-b", "2048", "-ub", "2048",
        ]
        if model.engine.get("pooling"):
            argv += ["--pooling", str(model.engine["pooling"])]
        return argv, {**env, "LLAMA_ARG_API_KEY": secret}
    argv = [settings.asr_python, settings.asr_server_script, "--model", str(directory), "--port", str(port), "--threads", str(threads)]
    if model.engine.get("language"):
        argv += ["--language", str(model.engine["language"])]
    return argv, {**env, "LUMINA_ASR_SECRET": secret, "HF_HUB_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _terminate(process: subprocess.Popen | None) -> None:
    """SIGTERM the child's process group, SIGKILL after the grace period; always reaps."""
    if process is None or process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


class Supervisor:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._servers: dict[str, _Server] = {}
        self._crashes: dict[str, list[float]] = {}
        self._failed: dict[str, str] = {}
        self._refused: dict[str, str] = {}
        self.heavy = threading.Lock()
        self.closed = False  # set by close() at shutdown: no server starts again in this process
        self.on_change: Callable[[str], None] = lambda model_id: None

    # -- state -------------------------------------------------------------------------------------
    def running(self, model_id: str) -> bool:
        with self._cond:
            server = self._servers.get(model_id)
            return bool(server and server.ready and server.process and server.process.poll() is None)

    def is_failed(self, model_id: str) -> bool:
        with self._cond:
            return model_id in self._failed

    def failure(self, model_id: str) -> str | None:
        with self._cond:
            return self._failed.get(model_id) or self._refused.get(model_id)

    def retry(self, model_id: str) -> None:
        """Retry: forget crashes and refusals so the next request starts the server again."""
        with self._cond:
            self._failed.pop(model_id, None)
            self._crashes.pop(model_id, None)
            self._refused.pop(model_id, None)
        self._emit(model_id)

    # -- leases ------------------------------------------------------------------------------------
    def acquire(self, model: CatalogModel, *, threads: int, wait: bool) -> LocalEndpoint | None:
        """A ready server's endpoint with one lease held (pair with ``release``); starts the server if needed.

        ``wait=False`` never blocks: it kicks off a start and returns None until the server is ready.
        Raises ModelUnavailable (failed, not enough memory, runtime missing, did not start in time).
        """
        changed = False
        try:
            with self._cond:
                if self.closed:
                    raise ModelUnavailable("Lumina is shutting down")
                if model.id in self._failed:
                    raise ModelUnavailable(self._failed[model.id])
                server = self._servers.get(model.id)
                if server is not None and server.ready and server.process.poll() is not None:
                    self._crashed(server)  # died since its last request: this request restarts it
                    changed = True
                    if model.id in self._failed:
                        raise ModelUnavailable(self._failed[model.id])
                    server = None
                if server is None:
                    server = _Server(model=model, secret=secrets.token_urlsafe(32), port=_free_port())
                    self._servers[model.id] = server
                    threading.Thread(target=self._start, args=(server, threads), daemon=True, name=f"model-start-{model.id}").start()
                if not server.ready and not wait:
                    return None
                deadline = time.monotonic() + START_TIMEOUT_SECONDS + STOP_GRACE_SECONDS
                while not server.ready and server.error is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ModelUnavailable(f"The {model.role} model did not start in time")
                    self._cond.wait(min(remaining, 0.5))
                if server.error == STOPPED:  # stopped (idle, shutdown, removal) before it was ready
                    raise ModelUnavailable(f"The {model.role} model stopped while starting")
                if server.error is not None:
                    raise ModelUnavailable(self._failed.get(model.id) or server.error)
                server.leases += 1
                server.last_used = self._clock()
                return LocalEndpoint(f"http://127.0.0.1:{server.port}/v1", server.secret)
        finally:
            if changed:
                self._emit(model.id)

    def release(self, model_id: str, endpoint: LocalEndpoint) -> None:
        with self._cond:
            server = self._servers.get(model_id)
            if server is not None and server.secret == endpoint.secret:  # a lease on a restarted server is not this one's
                server.leases = max(0, server.leases - 1)
                server.last_used = self._clock()

    # -- stopping ----------------------------------------------------------------------------------
    def reap_idle(self) -> None:
        """Maintenance tick: stop servers idle for IDLE_SECONDS and notice ones that died."""
        stopped: list[_Server] = []
        changed: list[str] = []
        with self._cond:
            now = self._clock()
            for server in list(self._servers.values()):
                if not server.ready:
                    continue
                if server.process.poll() is not None:
                    self._crashed(server)
                    changed.append(server.model.id)
                elif server.leases == 0 and now - server.last_used >= IDLE_SECONDS:
                    server.stopping = True
                    del self._servers[server.model.id]
                    stopped.append(server)
        for server in stopped:
            _terminate(server.process)
            changed.append(server.model.id)
        for model_id in changed:
            self._emit(model_id)

    def stop(self, model_id: str) -> None:
        with self._cond:
            server = self._servers.pop(model_id, None)
            if server is not None:
                server.stopping = True
                self._cond.notify_all()
        if server is not None:
            _terminate(server.process)
            self._emit(model_id)

    def stop_all(self) -> None:
        with self._cond:
            model_ids = list(self._servers)
        for model_id in model_ids:
            self.stop(model_id)

    def close(self) -> None:
        """Shutdown: refuse every later acquire, then stop every server."""
        with self._cond:
            self.closed = True
        self.stop_all()

    # -- internals ---------------------------------------------------------------------------------
    def _crashed(self, server: _Server) -> None:
        """Caller holds the lock. Forget the dead server; a second crash within the window fails the model."""
        model = server.model
        if self._servers.get(model.id) is server:
            del self._servers[model.id]
        now = self._clock()
        recent = [at for at in self._crashes.get(model.id, []) if now - at < CRASH_WINDOW_SECONDS] + [now]
        self._crashes[model.id] = recent
        if len(recent) >= 2:
            self._failed[model.id] = f"The {model.role} model stopped unexpectedly twice; Retry"
        logger.warning("Model server %s exited unexpectedly", model.id)

    def _start(self, server: _Server, threads: int) -> None:
        model = server.model
        error: str | None = None
        crashed = False
        available = available_memory()
        if available is not None and available < model.ram_bytes + MEMORY_MARGIN_BYTES:
            error = f"Not enough free memory to start the {model.role} model"
        else:
            argv, env = command(model, server.port, threads, server.secret)
            try:
                process = subprocess.Popen(
                    argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True,
                )
            except OSError:
                error = "The model runtime is not installed in this build"
            else:
                with self._cond:
                    server.process = process
                error, crashed = self._await_health(server)
        with self._cond:
            if error is None and server.stopping:
                error = STOPPED
            if error is None:
                server.ready = True
                server.last_used = self._clock()
                self._refused.pop(model.id, None)
            else:
                server.error = error
                if self._servers.get(model.id) is server:
                    del self._servers[model.id]
                if crashed:
                    self._crashed(server)
                elif error != STOPPED:
                    self._refused[model.id] = error
            self._cond.notify_all()
        if error is not None:
            _terminate(server.process)
        self._emit(model.id)

    def _await_health(self, server: _Server) -> tuple[str | None, bool]:
        """(error, crashed) once the child answers /health, exits, is stopped or times out."""
        role = server.model.role
        url = f"http://127.0.0.1:{server.port}/health"
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if server.stopping:
                return STOPPED, False
            if server.process.poll() is not None:
                return f"The {role} model stopped while starting", True
            try:
                if httpx.get(url, timeout=2.0, trust_env=False).status_code == 200:
                    return None, False
            except httpx.HTTPError:
                pass
            time.sleep(HEALTH_POLL_SECONDS)
        return f"The {role} model did not start in time", False

    def _emit(self, model_id: str) -> None:
        try:
            self.on_change(model_id)
        except Exception as exc:  # noqa: BLE001 - a listener never breaks a server
            logger.warning("Model change listener failed: %s", type(exc).__name__)


supervisor = Supervisor()
