"""Folder watching engine: a pure per-root directory-mtime state machine.

No DB and no globals: library_automation runs ``run_batch()`` on the root's one in-flight batch thread, writes the
``CommitRows`` and starts the ``ScopePlan`` runs. NFS defences: a directory's mtime is read before it is listed (a
change during the listing re-dirties it next pass), and file stability is read with open()+fstat() (close-to-open
revalidation) over a 90 s span, longer than acregmax (60 s). The watcher sees adds, deletes and renames only; an
NFO edited in place bumps no directory mtime and is caught by the scheduled scan.
"""
from __future__ import annotations

import math
import os
import stat
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from app.services.library_import import (
    IGNORE_MARKER,
    MAX_DEPTH,
    MEDIA_EXTENSIONS,
    ImportRunError,
    ScopeEntry,
    is_media_name,
    is_walked_dir,
    normalize_scope,
)

# Shared definitions; library_automation re-exports these.
FAILED_RETRY_S = 600             # a failed/cancelled watch run's directories wait this long
WATCH_COALESCE_S = 60
STABLE_POLL_S = 30               # a tracked file is re-read at most this often
STABLE_OBSERVATIONS = 3
STABLE_SPAN_S = 90               # > NFS acregmax (60 s)
# A TIME budget, not a 2,000-stat cap. ~19.5k TV dirs at 1.1 ms on 4 threads ≈ 5-6 s,
# so a whole pass fits one batch; what does not fit rolls to the next batch. Stays under FS_BATCH_STALL_S (30 s).
BATCH_WALL_S = 8.0
STAT_WORKERS = 4
MAX_DIRS = 50_000
MAX_TRACKED_FILES = 5_000


def _join(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _depth(path: str) -> int:
    return path.count("/") + 1 if path else 0


def _under(path: str, top: str) -> bool:
    return path == top or path.startswith(top + "/")


@dataclass(frozen=True)
class Listing:
    subdirs: tuple[str, ...] = ()                          # names, walk()'s directory filters applied
    media: frozenset[str] = frozenset()                    # names; sizes come only from read_file()
    nonmedia: dict[str, int] = field(default_factory=dict)  # name -> mtime_ns, for the deep-escalation rule
    has_ignore: bool = False


class Fs(Protocol):
    def stat_dir(self, path: str) -> int: ...              # the directory's mtime_ns; FileNotFoundError / OSError
    def list_dir(self, path: str) -> Listing: ...
    def read_file(self, path: str) -> tuple[int, int]: ...  # (size, mtime_ns)


class OsFs:
    """The real filesystem, root-relative POSIX paths, through walk()'s own name filters (depth/symlink parity: a test)."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, path: str) -> Path:
        return self.root / path if path else self.root

    def stat_dir(self, path: str) -> int:
        status = os.lstat(self._path(path))
        if not stat.S_ISDIR(status.st_mode):
            raise FileNotFoundError(path)  # replaced by a symlink or a file: the parent's listing reports it
        return status.st_mtime_ns

    def list_dir(self, path: str) -> Listing:
        with os.scandir(self._path(path)) as listing:
            entries = list(listing)
        if any(entry.name == IGNORE_MARKER for entry in entries):
            return Listing(has_ignore=True)
        subdirs: list[str] = []
        media: set[str] = set()
        nonmedia: dict[str, int] = {}
        for entry in entries:
            lower = entry.name.lower()
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if is_walked_dir(entry.name):
                    subdirs.append(entry.name)
            elif entry.is_file(follow_symlinks=False):
                if is_media_name(lower):
                    media.add(entry.name)
                elif Path(lower).suffix not in MEDIA_EXTENSIONS:  # theme.* and *-sample media: neither
                    try:
                        nonmedia[entry.name] = entry.stat(follow_symlinks=False).st_mtime_ns
                    except OSError:
                        pass
        return Listing(tuple(sorted(subdirs)), frozenset(media), nonmedia)

    def read_file(self, path: str) -> tuple[int, int]:
        full = self._path(path)
        try:
            fd = os.open(full, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        except PermissionError:
            status = os.lstat(full)  # unreadable but listable: cached attributes are the best we have
            return status.st_size, status.st_mtime_ns
        try:
            status = os.fstat(fd)
        finally:
            os.close(fd)
        return status.st_size, status.st_mtime_ns


@dataclass
class Obs:
    size: int = -1
    mtime_ns: int = -1
    first_equal_at: float = 0.0
    equal_count: int = 0
    last_read: float = -math.inf
    stable: bool = False


@dataclass
class DirtyDir:
    seen_mtime: int                 # the directory's mtime when it was last listed (written as the row on commit)
    pass_no: int = 0                # the pass that last listed it: dirs dirtied in one pass dispatch together
    deep: bool = False
    new: bool = False               # wholly new: carried by the ancestor's deep entry, never dispatched alone
    files: dict[str, Obs] = field(default_factory=dict)
    new_subdirs: set[str] = field(default_factory=set)
    removed_subdirs: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class WatchStats:
    dirs_watched: int = 0
    pending_files: int = 0
    stats_last_pass: int = 0
    watch_errors_last_pass: int = 0
    last_pass_at: datetime | None = None
    last_pass_seconds: float | None = None
    last_change_at: datetime | None = None
    dirs_listed: int = 0
    dirs_total: int | None = None


@dataclass(frozen=True)
class ScopePlan:
    scope: list[ScopeEntry] | None  # None = a full run (trigger "watch")
    covered: dict[str, int]         # path -> seen_mtime at listing; these rows are written on commit
    removed: frozenset[str]         # subtrees whose rows are deleted on commit

    @property
    def full(self) -> bool:
        return self.scope is None


@dataclass(frozen=True)
class CommitRows:
    upserts: dict[str, int]
    deletes: set[str]


class RootWatch:
    def __init__(self, root_id: str, fs: Fs, *, known: dict[str, int], full_run_cutoff_ns: int | None,
                 interval_s: int, monotonic: Callable[[], float],
                 normalize: Callable[[list[ScopeEntry]], list[ScopeEntry] | None] = normalize_scope,
                 workers: int = STAT_WORKERS):
        self.root_id, self.fs, self.interval_s = root_id, fs, interval_s
        self.cutoff = full_run_cutoff_ns
        self._mono, self._normalize, self._workers = monotonic, normalize, workers
        self.dirs: dict[str, int] = dict(known)  # known-good mtime per directory ('' = the root), as the rows hold
        self.dirty: dict[str, DirtyDir] = {}
        self.cursor = 0
        self.phase: Literal["baseline", "watching", "too_large"] = "watching" if known else "baseline"
        self.overflow = False  # past MAX_TRACKED_FILES: no tracking, the next dispatch is a full run
        self.stats = WatchStats(dirs_watched=len(self.dirs))
        self._queue: deque[str] = deque() if known else deque([""])  # directories still to list
        self._queued: set[str] = set(self._queue)
        self._order: list[str] = []
        self._pass_started: float | None = self._mono() if not known else None
        self._pass_done = False
        self._pass_no = 0
        self._changed = False   # a directory was (re)listed during the current pass
        self._quiet = False     # the last complete pass found no change
        self._spent = self._errors = self._listed = self._listed_base = 0
        self._last_change: datetime | None = None
        self._pending: ScopePlan | None = None
        self._retry_not_before = -math.inf
        self._last_dispatch = -math.inf

    # --- the batch (the only code that touches fs) --------------------------------------------------------------

    def run_batch(self) -> WatchStats:
        """One bounded batch: queued listings, stability reads, then the stat sweep; BATCH_WALL_S between calls."""
        if self.phase != "too_large":
            deadline = self._mono() + BATCH_WALL_S
            self._list_queue(deadline)
            if self.phase == "watching":
                self._read_files(deadline)
                self._sweep(deadline)
            self._check_file_cap()
        self._publish()
        return self.stats

    def _list_queue(self, deadline: float) -> None:
        while self._queue and self._mono() < deadline and self.phase != "too_large":
            path = self._queue.popleft()
            self._queued.discard(path)
            try:
                seen = self.fs.stat_dir(path)
                if self.phase == "baseline":
                    self._baseline_dir(path, seen)
                else:
                    self._relist(path, seen, new=True)
            except FileNotFoundError:
                pass
            except OSError:
                self._errors += 1
                self.dirs.setdefault(path, 0)  # unknown-good: the sweep sees a change and relists it later
        if self.phase == "baseline" and not self._queue:
            self.phase = "watching"
            self._finish_pass()

    def _baseline_dir(self, path: str, seen: int) -> None:
        listing = self.fs.list_dir(path)  # mtime first, then the listing (NFS: a change meanwhile re-dirties)
        self._listed += 1
        mark = self.cutoff is not None and seen > self.cutoff
        # A dirty directory's stored known-good is the last full run, so rows written now re-detect it after a restart.
        self.dirs[path] = self.cutoff if mark else seen
        if mark:
            dirty = self.dirty[path] = DirtyDir(seen, deep=any(m > self.cutoff for m in listing.nonmedia.values()))
            if not self.overflow:
                dirty.files = {name: Obs() for name in listing.media}
        for name in listing.subdirs:
            child = _join(path, name)
            if _depth(child) <= MAX_DEPTH:
                self._enqueue(child)
        self._check_caps()

    def _relist(self, path: str, seen: int, *, new: bool = False) -> None:
        listing = self.fs.list_dir(path)  # before any mutation: a failed listing changes nothing
        self._changed, self._last_change = True, datetime.now(UTC)
        ref = self.dirs.get(path)
        if ref is None:
            self.dirs[path] = seen
        dirty = self.dirty.setdefault(path, DirtyDir(seen, new=new))
        dirty.seen_mtime, dirty.pass_no = seen, self._pass_no
        if ref is not None and any(m > ref for m in listing.nonmedia.values()):
            dirty.deep = True
        present = {_join(path, name) for name in listing.subdirs if _depth(path) < MAX_DEPTH}
        for child in self._children(path) - present:
            if child in dirty.new_subdirs:
                dirty.new_subdirs.discard(child)
                self._purge(child)
            else:
                dirty.removed_subdirs.add(child)
        for child in present:
            dirty.removed_subdirs.discard(child)
            if child not in self.dirs:
                dirty.new_subdirs.add(child)
                self._enqueue(child)
        if not self.overflow:
            dirty.files = {name: dirty.files.get(name) or Obs() for name in listing.media}
        self._check_caps()

    def _read_files(self, deadline: float) -> None:
        now = self._mono()
        for path, dirty in list(self.dirty.items()):
            for name, obs in list(dirty.files.items()):
                if now - obs.last_read < STABLE_POLL_S:
                    continue
                if self._mono() >= deadline:
                    return
                try:
                    size, mtime_ns = self.fs.read_file(_join(path, name))
                except FileNotFoundError:
                    del dirty.files[name]
                    continue
                except OSError:
                    self._errors += 1
                    continue
                at = self._mono()
                if (size, mtime_ns) == (obs.size, obs.mtime_ns):
                    obs.equal_count += 1
                else:
                    obs.size, obs.mtime_ns, obs.equal_count, obs.first_equal_at = size, mtime_ns, 1, at
                obs.last_read = at
                obs.stable = obs.equal_count >= STABLE_OBSERVATIONS and at - obs.first_equal_at >= STABLE_SPAN_S

    def _sweep(self, deadline: float) -> None:
        if self._pass_done or self._pass_started is None:
            if self._pass_started is not None and self._mono() - self._pass_started < self.interval_s:
                return
            self._order, self.cursor, self._pass_done = sorted(self.dirs), 0, False
            self._pass_started, self._changed, self._spent, self._errors = self._mono(), False, 0, 0
            self._listed_base = self._listed  # stats_last_pass counts this pass only
            self._pass_no += 1
        paths = self._order[self.cursor:]
        results = self._stat_many(paths, deadline)
        self._spent += len(results)
        for i, (path, mtime) in enumerate(zip(paths, results, strict=False)):
            if isinstance(mtime, OSError):
                self._errors += not isinstance(mtime, FileNotFoundError)  # missing: the parent's listing reports it
                continue
            dirty = self.dirty.get(path)
            if path not in self.dirs or mtime == (dirty.seen_mtime if dirty else self.dirs[path]):
                continue
            if self._mono() >= deadline:
                self.cursor += i  # re-stat it next batch
                return
            try:
                self._relist(path, mtime)
            except FileNotFoundError:
                pass
            except OSError:
                self._errors += 1
            if self.phase == "too_large":
                return
        self.cursor += len(results)
        if self.cursor >= len(self._order):
            self._finish_pass()

    def _stat_many(self, paths: list[str], deadline: float) -> list[int | OSError]:
        """stat_dir over a prefix of paths on up to STAT_WORKERS daemon threads, stopping at the deadline.

        A hung NFS stat blocks its worker and therefore this batch; the caller's per-root single in-flight batch
        bounds that to one batch (and its workers) per root, and daemon threads never block shutdown.
        """
        out: dict[int, int | OSError] = {}
        indexes = iter(range(len(paths)))
        lock = threading.Lock()

        def work() -> None:
            while True:
                with lock:
                    i = next(indexes, None) if self._mono() < deadline else None
                if i is None:
                    return
                try:
                    out[i] = self.fs.stat_dir(paths[i])
                except OSError as exc:
                    out[i] = exc

        if self._workers <= 1:
            work()
        else:
            threads = [threading.Thread(target=work, name=f"watch-stat-{self.root_id}", daemon=True)
                       for _ in range(min(self._workers, len(paths)))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        done = 0
        while done in out:
            done += 1
        return [out[i] for i in range(done)]  # a contiguous prefix: anything past a gap is re-statted next batch

    def _finish_pass(self) -> None:
        self._pass_done, self._quiet = True, not self._changed
        now = self._mono()
        self.stats = replace(
            self.stats, stats_last_pass=self._spent or self._listed - self._listed_base, watch_errors_last_pass=self._errors,
            last_pass_at=datetime.now(UTC), last_pass_seconds=now - (self._pass_started or now),
            dirs_total=len(self.dirs),
        )

    # --- bookkeeping ----------------------------------------------------------------------------------------------

    def _enqueue(self, path: str) -> None:
        if path not in self._queued:
            self._queue.append(path)
            self._queued.add(path)

    def _children(self, path: str) -> set[str]:
        # O(dirs) per relisted directory; a children index if relists of 50k-dir roots get hot.
        return {p for p in self.dirs if p and p != path and p.rpartition("/")[0] == path}

    def _purge(self, top: str) -> set[str]:
        gone = {p for p in self.dirs if _under(p, top)} | {top}
        for p in gone:
            self.dirs.pop(p, None)
            self.dirty.pop(p, None)
        return gone

    def _check_caps(self) -> None:
        if len(self.dirs) > MAX_DIRS:
            self.phase = "too_large"
            self.dirs, self.dirty, self._order = {}, {}, []
            self._queue.clear()
            self._queued.clear()

    def _check_file_cap(self) -> None:  # once per batch: summing per listed directory is quadratic
        if not self.overflow and sum(len(d.files) for d in self.dirty.values()) > MAX_TRACKED_FILES:
            self.overflow = True
            for dirty in self.dirty.values():
                dirty.files = {}

    def _publish(self) -> None:
        pending = sum(not obs.stable for d in self.dirty.values() for obs in d.files.values())
        self.stats = replace(self.stats, dirs_watched=len(self.dirs), pending_files=pending,
                             last_change_at=self._last_change, dirs_listed=self._listed)

    def pass_complete(self) -> bool:
        return self._pass_done and not self._queue

    def _ready(self, path: str) -> bool:
        dirty = self.dirty.get(path)
        if dirty is None:
            return path not in self._queued
        return all(obs.stable for obs in dirty.files.values()) and all(self._ready(s) for s in dirty.new_subdirs)

    def _new_tree(self, top: str) -> dict[str, int]:
        return {p: d.seen_mtime for p, d in self.dirty.items() if d.new and _under(p, top)}

    # --- dispatch and commit --------------------------------------------------------------------------------------

    def plan(self, *, now_s: float) -> ScopePlan | None:
        """Dispatch: after a complete pass, ready directories (with the rest of their pass) as one scope."""
        if (self.phase != "watching" or self._pending is not None or not self.pass_complete()
                or now_s < self._retry_not_before or now_s - self._last_dispatch < WATCH_COALESCE_S):
            return None
        if self.overflow:
            if not self._quiet:
                return None
            covered = {p: (self.dirty[p].seen_mtime if p in self.dirty else m) for p, m in self.dirs.items()}
            removed = frozenset().union(*(d.removed_subdirs for d in self.dirty.values()))
            return self._dispatch(ScopePlan(None, covered, removed), now_s)
        # A move lands in one run: one unready directory holds back every directory dirtied in the same pass.
        held = {d.pass_no for p, d in self.dirty.items() if not d.new and not self._ready(p)}
        entries: list[ScopeEntry] = []
        covered: dict[str, int] = {}
        removed: set[str] = set()
        for path, dirty in self.dirty.items():
            if dirty.new or dirty.pass_no in held:
                continue
            entries.append(ScopeEntry(dir=path, deep=dirty.deep))
            covered[path] = dirty.seen_mtime
            for sub in dirty.new_subdirs:
                entries.append(ScopeEntry(dir=sub, deep=True))
                covered.update(self._new_tree(sub))
            for sub in dirty.removed_subdirs:
                entries.append(ScopeEntry(dir=sub, deep=True))
                removed.add(sub)
        if not entries:
            return None
        return self._dispatch(ScopePlan(self._collapse(entries), covered, frozenset(removed)), now_s)

    def _collapse(self, entries: list[ScopeEntry]) -> list[ScopeEntry] | None:
        try:
            return self._normalize(entries)
        except ImportRunError:  # over MAX_SCOPE_ENTRIES: the entries' top-level folders, deep
            pass
        try:
            return self._normalize([ScopeEntry(dir=e["dir"].split("/")[0], deep=True) for e in entries])
        except ImportRunError:
            return None  # still too many: a full watch run

    def _dispatch(self, plan: ScopePlan, now_s: float) -> ScopePlan:
        self._pending, self._last_dispatch = plan, now_s
        return plan

    def committed(self, plan: ScopePlan, ok: Literal["succeeded", "partial"]) -> CommitRows:
        """The run succeeded (a partial run's failed files are retried by the next full scan, so none loops)."""
        self._pending = None
        deletes: set[str] = set()
        for top in plan.removed:
            deletes |= self._purge(top)
        upserts: dict[str, int] = {}
        for path, seen in plan.covered.items():
            if path in deletes or path not in self.dirs:
                continue
            self.dirs[path] = upserts[path] = seen
            dirty = self.dirty.get(path)
            if dirty is None:
                continue
            if dirty.seen_mtime == seen:
                del self.dirty[path]
            elif plan.full:
                del self.dirty[path]
                self.dirs[path] = 0  # untracked while overflowing: the next pass relists it from scratch
            else:  # changed during the run: stays dirty with what the run did not cover
                dirty.new = False
                dirty.new_subdirs -= plan.covered.keys()
                dirty.removed_subdirs -= plan.removed
        if plan.full:
            self.overflow = False
        return CommitRows(upserts, deletes)

    def failed(self, plan: ScopePlan, now_s: float) -> None:
        """failed / cancelled: the directories stay dirty and wait FAILED_RETRY_S."""
        self._pending = None
        self._retry_not_before = now_s + FAILED_RETRY_S
