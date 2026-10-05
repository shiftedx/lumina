"""Folder watching engine (an 8 s time budget per batch)."""
from __future__ import annotations

import ast
import shutil
import threading
from pathlib import Path

import pytest

from app.services import library_import
from app.services.library_import import MAX_SCOPE_ENTRIES, ImportRunError
from app.services.library_watch import (
    BATCH_WALL_S,
    Listing,
    FAILED_RETRY_S,
    MAX_DIRS,
    MAX_TRACKED_FILES,
    STABLE_POLL_S,
    OsFs,
    RootWatch,
)

TICK = 15
D, S = True, False


def e(path: str, deep: bool) -> dict:
    return {"dir": path, "deep": deep}


def collapse(entries: list[dict]) -> list[dict] | None:
    """A copy of normalize_scope's collapse rules until S1 merges."""
    merged: dict[str, bool] = {}
    for entry in entries:
        merged[entry["dir"]] = merged.get(entry["dir"], False) or entry["deep"]
    if merged.get(""):
        return None
    deep = [path for path, is_deep in merged.items() if is_deep]
    out = [e(path, is_deep) for path, is_deep in merged.items()
           if not any(top != path and (top == "" or path.startswith(top + "/")) for top in deep)]
    if len(out) > MAX_SCOPE_ENTRIES:
        raise ImportRunError("too many")
    return sorted(out, key=lambda entry: tuple(entry["dir"].split("/")) if entry["dir"] else ())


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeFs:
    """A dict tree: directory mtimes, media files (.mkv) and other files; counts every call."""

    def __init__(self, clock: FakeClock, stat_cost: float = 0.0) -> None:
        self.clock, self.stat_cost = clock, stat_cost
        self.ns = 1_000
        self.dirs: dict[str, int] = {"": self.ns}
        self.files: dict[str, tuple[int, int]] = {}
        self.stat_calls = self.list_calls = self.read_calls = 0
        self.stat_errors: dict[str, OSError] = {}

    def _now(self) -> int:
        self.ns += 1
        return self.ns

    @staticmethod
    def _parent(path: str) -> str:
        return path.rpartition("/")[0]

    def mkdir(self, path: str) -> None:
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in self.dirs:
                self.dirs[sub] = self._now()
                self.dirs[self._parent(sub)] = self._now()

    def add(self, path: str, size: int = 1) -> None:
        self.mkdir(self._parent(path)) if self._parent(path) else None
        self.files[path] = (size, self._now())
        self.dirs[self._parent(path)] = self._now()

    def grow(self, path: str) -> None:
        size, _ = self.files[path]
        self.files[path] = (size + 1, self._now())  # contents change: no directory mtime bump

    def rm(self, path: str) -> None:
        for key in [k for k in self.dirs if k == path or k.startswith(path + "/")]:
            del self.dirs[key]
        for key in [k for k in self.files if k == path or k.startswith(path + "/")]:
            del self.files[key]
        self.dirs[self._parent(path)] = self._now()

    def stat_dir(self, path: str) -> int:
        self.stat_calls += 1
        self.clock.t += self.stat_cost
        if path in self.stat_errors:
            raise self.stat_errors[path]
        if path not in self.dirs:
            raise FileNotFoundError(path)
        return self.dirs[path]

    def list_dir(self, path: str):
        self.list_calls += 1
        if path not in self.dirs:
            raise FileNotFoundError(path)
        kids = [k for k in self.dirs if k and self._parent(k) == path]
        files = {k.rpartition("/")[2]: v for k, v in self.files.items() if self._parent(k) == path}
        return Listing(
            subdirs=tuple(sorted(k.rpartition("/")[2] for k in kids)),
            media=frozenset(n for n in files if n.endswith(".mkv")),
            nonmedia={n: v[1] for n, v in files.items() if not n.endswith(".mkv")},
        )

    def read_file(self, path: str) -> tuple[int, int]:
        self.read_calls += 1
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


def make(fs, clock, *, known=None, cutoff=None, interval=60, **kw) -> RootWatch:
    return RootWatch("root-1", fs, known=known or {}, full_run_cutoff_ns=cutoff, interval_s=interval,
                     monotonic=clock, normalize=collapse, workers=kw.pop("workers", 1), **kw)


def tick(watch: RootWatch, clock: FakeClock, n: int = 1):
    plan = None
    for _ in range(n):
        clock.t += TICK
        watch.run_batch()
        plan = plan or watch.plan(now_s=clock.t)
    return plan


def watching(fs: FakeFs, clock: FakeClock, **kw) -> RootWatch:
    """A watch restored from rows equal to the current tree (restart, nothing changed)."""
    watch = make(fs, clock, known=dict(fs.dirs), **kw)
    assert tick(watch, clock) is None and watch.pass_complete()
    return watch


def until_plan(watch: RootWatch, clock: FakeClock, limit: int = 40):
    for _ in range(limit):
        plan = tick(watch, clock)
        if plan is not None:
            return plan
    raise AssertionError("no plan")


def library(clock: FakeClock, **kw) -> FakeFs:
    fs = FakeFs(clock, **kw)
    fs.add("Show/Season 01/e1.mkv")
    fs.add("Show/tvshow.nfo")
    fs.add("Movie/movie.mkv")
    return fs


# --- baseline ------------------------------------------------------------------------------------------------------

def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_baseline_lists_every_directory_honouring_hidden_ignore_and_skip_dirs(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    touch(root / "Show" / "Season 01" / "e1.mkv")
    touch(root / "Show" / "Backdrops" / "b.mkv")
    touch(root / ".hidden" / "h.mkv")
    touch(root / "Ignored" / ".ignore")
    touch(root / "Ignored" / "Sub" / "i.mkv")
    clock = FakeClock()
    watch = make(OsFs(root), clock, cutoff=0)
    tick(watch, clock)
    assert watch.phase == "watching" and watch.pass_complete()
    assert set(watch.dirs) == {"", "Show", "Show/Season 01", "Ignored"}
    assert set(watch.dirty["Show/Season 01"].files) == {"e1.mkv"}
    assert watch.stats.dirs_listed == 4 and watch.stats.dirs_watched == 4


def test_baseline_marks_directories_newer_than_the_last_full_run_dirty() -> None:
    clock = FakeClock()
    fs = library(clock)
    cutoff = fs.ns
    fs.add("Show/Season 02/e1.mkv")
    watch = make(fs, clock, cutoff=cutoff)
    tick(watch, clock)
    assert set(watch.dirty) == {"Show", "Show/Season 02"}
    assert watch.dirs["Show/Season 02"] == cutoff  # the stored known-good is the last full run, so a restart re-detects it
    assert watch.dirs["Movie"] == fs.dirs["Movie"]
    plan = until_plan(watch, clock)
    assert plan.scope == [e("Show", S), e("Show/Season 02", S)]


# --- stability -----------------------------------------------------------------------------------------------------

def test_new_episode_slow_copy_never_dispatches_while_it_grows() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.add("Show/Season 02/e1.mkv")
    for _ in range(5):
        fs.grow("Show/Season 02/e1.mkv")
        assert tick(watch, clock) is None
    last_write = clock.t
    plan = until_plan(watch, clock)
    assert clock.t - last_write >= 90
    assert plan.scope == [e("Show", S), e("Show/Season 02", D)]


def test_stability_needs_a_90s_span_not_just_three_observations() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.add("Movie/extra.mkv")
    plan, first = None, None
    while plan is None:
        clock.t += TICK
        watch.run_batch()
        obs = watch.dirty.get("Movie") and watch.dirty["Movie"].files["extra.mkv"]
        if obs and first is None and obs.equal_count:
            first = obs.first_equal_at
        plan = watch.plan(now_s=clock.t)
        if obs and obs.equal_count >= 3 and clock.t - first < 90:
            assert plan is None and not obs.stable  # 3 equal reads within 60 s are not enough (NFS acregmax 60 s)
    assert clock.t - first >= 90
    assert plan.scope == [e("Movie", S)]


def test_stability_reads_with_read_file_not_stat_dir() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.add("Movie/extra.mkv")
    for _ in range(20):
        fs.grow("Movie/extra.mkv")  # only read_file sees the growth: the directory mtime is frozen
        assert tick(watch, clock) is None
    assert fs.read_calls >= 20 * TICK // STABLE_POLL_S - 1


# --- shape of the scope --------------------------------------------------------------------------------------------

def test_new_show_folder_is_a_deep_scope_of_the_new_folder_only_never_the_parent() -> None:
    clock = FakeClock()
    fs = library(clock)
    fs.mkdir("TV")
    watch = watching(fs, clock)
    fs.add("TV/New Show/Season 01/e1.mkv")
    fs.add("TV/New Show/Season 01/e2.mkv")
    plan = until_plan(watch, clock)
    assert plan.scope == [e("TV", S), e("TV/New Show", D)]
    assert set(plan.covered) == {"TV", "TV/New Show", "TV/New Show/Season 01"}


def test_nfo_added_in_a_series_folder_escalates_it_to_deep() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.add("Show/poster.jpg")
    plan = until_plan(watch, clock)
    assert plan.scope == [e("Show", D)]


def test_rename_across_two_folders_lands_in_one_plan() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.rm("Show/Season 01/e1.mkv")
    fs.add("Movie/e1.mkv")
    plan = until_plan(watch, clock)
    assert plan.scope == [e("Movie", S), e("Show/Season 01", S)]


def test_removed_subdirectory_becomes_a_deep_entry_and_its_row_is_deleted_on_commit() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.rm("Show/Season 01")
    plan = until_plan(watch, clock)
    assert plan.scope == [e("Show", S), e("Show/Season 01", D)]
    rows = watch.committed(plan, "succeeded")
    assert rows.deletes == {"Show/Season 01"}
    assert rows.upserts == {"Show": fs.dirs["Show"]}
    assert "Show/Season 01" not in watch.dirs and not watch.dirty


# --- commit and failure --------------------------------------------------------------------------------------------

def test_a_dispatched_run_that_fails_keeps_dirs_dirty_and_retries_after_600s() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.add("Movie/extra.mkv")
    plan = until_plan(watch, clock)
    assert tick(watch, clock) is None  # one in flight
    watch.failed(plan, now_s=clock.t)
    failed_at = clock.t
    assert "Movie" in watch.dirty
    again = until_plan(watch, clock, limit=60)
    assert clock.t - failed_at >= FAILED_RETRY_S and again.scope == plan.scope


def test_a_change_during_the_run_redirties_after_commit() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.add("Movie/extra.mkv")
    plan = until_plan(watch, clock)
    fs.add("Movie/extra2.mkv")  # lands while the run is in flight
    tick(watch, clock, 5)
    rows = watch.committed(plan, "succeeded")
    assert rows.upserts["Movie"] == plan.covered["Movie"] != fs.dirs["Movie"]
    assert "Movie" in watch.dirty
    assert until_plan(watch, clock).scope == [e("Movie", S)]


def test_partial_commit_clears_dirty_and_writes_rows() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.add("Movie/extra.mkv")
    plan = until_plan(watch, clock)
    rows = watch.committed(plan, "partial")
    assert rows.upserts == {"Movie": fs.dirs["Movie"]} and not rows.deletes
    assert not watch.dirty and watch.dirs["Movie"] == fs.dirs["Movie"]
    assert tick(watch, clock, 10) is None


def test_restart_with_known_rows_redetects_uncommitted_directories() -> None:
    clock = FakeClock()
    fs = library(clock)
    rows = dict(fs.dirs)
    fs.add("Movie/extra.mkv")  # changed while Lumina was down (or before a run committed)
    watch = make(fs, clock, known=rows)
    assert until_plan(watch, clock).scope == [e("Movie", S)]


# --- budget: time-based) ----------------------------------------------------------------------------

def big(clock: FakeClock, n: int, **kw) -> FakeFs:
    fs = FakeFs(clock, **kw)
    for i in range(n):
        fs.dirs[f"d{i:05}"] = 1
    return fs


@pytest.mark.parametrize("workers", [1, 4])
def test_budget_is_8s_of_stat_time_and_the_rest_rolls_to_the_next_batch(workers: int) -> None:
    clock = FakeClock()
    fs = big(clock, 10_000, stat_cost=0.001)
    watch = make(fs, clock, known=dict(fs.dirs), interval=60, workers=workers)
    watch.run_batch()
    assert fs.stat_calls == pytest.approx(BATCH_WALL_S / 0.001, abs=2 * workers)
    assert not watch.pass_complete() and watch.cursor == fs.stat_calls
    watch.run_batch()
    assert watch.pass_complete() and fs.stat_calls == 10_001
    assert watch.stats.stats_last_pass == 10_001


def test_a_new_pass_starts_once_per_interval() -> None:
    clock = FakeClock()
    fs = big(clock, 100)
    watch = make(fs, clock, known=dict(fs.dirs), interval=300)
    tick(watch, clock)
    assert fs.stat_calls == 101
    tick(watch, clock, 18)  # 270 s later: still inside the interval
    assert fs.stat_calls == 101
    tick(watch, clock, 2)
    assert fs.stat_calls == 202


def test_batch_stops_at_the_wall_deadline_between_syscalls() -> None:
    clock = FakeClock()
    fs = big(clock, 50)
    real = fs.stat_dir

    def slow_first(path: str) -> int:
        if fs.stat_calls == 0:
            clock.t += BATCH_WALL_S  # one hung-ish stat eats the whole budget
        return real(path)

    fs.stat_dir = slow_first
    watch = make(fs, clock, known=dict(fs.dirs))
    watch.run_batch()
    assert fs.stat_calls == 1 and watch.cursor == 1


# --- caps ----------------------------------------------------------------------------------------------------------

def test_over_200_entries_collapse_to_top_level_then_a_full_scan() -> None:
    clock = FakeClock()
    fs = FakeFs(clock)
    for show in range(3):
        for season in range(80):
            fs.mkdir(f"Show {show}/Season {season:02}")
    watch = watching(fs, clock)
    for show in range(3):
        for season in range(80):
            fs.add(f"Show {show}/Season {season:02}/cover.jpg")
    plan = until_plan(watch, clock)
    assert plan.scope == [e(f"Show {i}", D) for i in range(3)]
    watch.committed(plan, "succeeded")

    for show in range(MAX_SCOPE_ENTRIES + 1):
        fs.mkdir(f"Top {show:03}")
    watch2 = watching(fs, clock)
    for show in range(MAX_SCOPE_ENTRIES + 1):
        fs.add(f"Top {show:03}/cover.jpg")
    plan = until_plan(watch2, clock)
    assert plan.full and plan.scope is None


def test_50001_dirs_is_too_large_and_stops_watching() -> None:
    clock = FakeClock()
    fs = big(clock, MAX_DIRS)  # + the root = 50,001
    names = tuple(k for k in fs.dirs if k)
    fs.list_dir = lambda path: Listing(subdirs=names if path == "" else ())  # FakeFs's own listing is O(n) per dir
    watch = make(fs, clock, cutoff=0)
    tick(watch, clock)
    assert watch.phase == "too_large" and not watch.dirs
    calls = fs.stat_calls
    assert tick(watch, clock, 10) is None and fs.stat_calls == calls


def test_5001_tracked_files_stops_tracking_and_requests_a_full_run_after_a_quiet_pass() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    for i in range(MAX_TRACKED_FILES + 1):
        fs.add(f"Dump/f{i}.mkv")
    while not watch.overflow:
        assert tick(watch, clock) is None  # the pass that found the change is not quiet
    assert not any(d.files for d in watch.dirty.values())
    plan = until_plan(watch, clock)
    assert plan.full and plan.covered["Dump"] == fs.dirs["Dump"]
    watch.committed(plan, "succeeded")
    assert not watch.overflow and not watch.dirty


# --- errors --------------------------------------------------------------------------------------------------------

def test_stat_filenotfound_defers_to_the_parent_listing_and_other_errors_count_and_retry() -> None:
    clock = FakeClock()
    fs = library(clock)
    watch = watching(fs, clock)
    fs.stat_errors["Movie"] = PermissionError("denied")
    del fs.dirs["Show/Season 01"]  # gone without the parent noticing yet
    tick(watch, clock, 4)
    assert watch.stats.watch_errors_last_pass == 1
    assert not watch.dirty and "Show/Season 01" in watch.dirs
    del fs.stat_errors["Movie"]
    fs.add("Movie/extra.mkv")
    assert until_plan(watch, clock).scope == [e("Movie", S)]


def test_a_directory_replaced_by_a_symlink_or_ignore_is_treated_as_removed(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    touch(root / "Show" / "Season 01" / "e1.mkv")
    touch(root / "Other" / "Sub" / "o.mkv")
    elsewhere = touch(tmp_path / "elsewhere" / "x.mkv").parent
    clock = FakeClock()
    watch = make(OsFs(root), clock, cutoff=None)
    tick(watch, clock)
    assert not watch.dirty
    shutil.rmtree(root / "Show" / "Season 01")
    (root / "Show" / "Season 01").symlink_to(elsewhere, target_is_directory=True)
    touch(root / "Other" / ".ignore")
    plan = until_plan(watch, clock)
    assert watch.dirty["Show"].removed_subdirs == {"Show/Season 01"}
    assert watch.dirty["Other"].removed_subdirs == {"Other/Sub"}
    assert plan.scope == [e("Other", S), e("Other/Sub", D), e("Show", S), e("Show/Season 01", D)]


# --- threads, purity and parity ------------------------------------------------------------------------------------

def test_four_stat_workers_find_the_same_changes_as_one_and_leave_no_thread(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    for i in range(40):
        touch(root / f"Show {i}" / "Season 01" / "e.mkv")
    clock = FakeClock()
    one, four = make(OsFs(root), clock), make(OsFs(root), clock, workers=4)
    tick(one, clock), tick(four, clock)
    for i in range(0, 40, 7):
        touch(root / f"Show {i}" / "Season 01" / "new.mkv")
    before = threading.active_count()
    tick(one, clock, 4), tick(four, clock, 4)
    assert threading.active_count() == before
    assert set(four.dirty) == set(one.dirty) == {f"Show {i}/Season 01" for i in range(0, 40, 7)}
    assert four.dirs == one.dirs


def test_walk_parity(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    touch(root / "Show" / "Season 01" / "e1.mkv")
    touch(root / "Show" / "Season 01" / "e1.nfo")
    touch(root / "Show" / "poster.jpg")
    touch(root / "Show" / "theme.mp3")
    touch(root / "Show" / "BackDrops" / "b.mkv")
    touch(root / "Show" / "Theme-Music" / "t.mp3")
    touch(root / "Movie" / "Movie.MKV")
    touch(root / "Movie" / "movie-sample.mkv")
    touch(root / "Movie" / ".partial.mkv")
    touch(root / ".hidden" / "h.mkv")
    touch(root / "Ignored" / ".ignore")
    touch(root / "Ignored" / "i.mkv")
    touch(root / "Music" / "a.flac")
    (root / "Linked").symlink_to(root / "Show", target_is_directory=True)
    (root / "Show" / "link.mkv").symlink_to(root / "Movie" / "Movie.MKV")
    deep = root.joinpath(*[f"d{i}" for i in range(26)])
    touch(deep / "deep.mkv")
    touch(root.joinpath(*[f"d{i}" for i in range(23)]) / "ok.mkv")
    touch(root.joinpath(*[f"d{i}" for i in range(24)]) / "edge.mkv")

    walked = {"/".join(parts) for parts, entry, error, _ in library_import.walk(root, ()) if entry is not None}
    clock = FakeClock()
    watch = make(OsFs(root), clock, cutoff=0)
    tick(watch, clock)
    watched = {f"{path}/{name}" if path else name for path, d in watch.dirty.items() for name in d.files}
    assert watched == walked
    assert {m.rpartition("/")[0] for m in walked} <= set(watch.dirs)
    assert max(p.count("/") + 1 for p in watch.dirs if p) == library_import.MAX_DEPTH
    assert not {"Linked", ".hidden", "Show/BackDrops", "Show/Theme-Music"} & set(watch.dirs)


def test_pure_no_db() -> None:
    source = Path(__file__).resolve().parents[1] / "app" / "services" / "library_watch.py"
    imported = set()
    for node in ast.walk(ast.parse(source.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {name for name in imported if name.startswith(("sqlalchemy", "app.db", "app.models"))}
