"""Changed directories must not each traverse the whole watched tree."""
from __future__ import annotations

import time

from app.services.library_watch import Listing, RootWatch


class CountedDirs(dict):
    visits = 0

    def __iter__(self):
        for key in super().__iter__():
            self.visits += 1
            yield key


class ChangedTree:
    def __init__(self, known):
        self.known = known

    def stat_dir(self, path):
        return 2

    def list_dir(self, path):
        return Listing(subdirs=tuple(self.known.keys() - {""}) if path == "" else ())

    def read_file(self, path):
        raise AssertionError("No files")


def test_changed_tree_bookkeeping_is_linear():
    known = {"": 1, **{f"show-{i:04d}": 1 for i in range(1000)}}
    watch = RootWatch("root", ChangedTree(known), known=known, full_run_cutoff_ns=None,
                      interval_s=60, monotonic=time.monotonic, workers=1)
    watch.dirs = counted = CountedDirs(watch.dirs)
    watch.run_batch()
    assert watch.pass_complete()
    assert len(watch.dirty) == len(known)
    assert counted.visits <= 3 * len(known)


def test_child_bookkeeping_tracks_new_purged_and_readded_directories():
    known = {"": 1, "Show": 1, "Show/Season": 1, "Showcase": 1}
    tree = ChangedTree(known)
    watch = RootWatch("root", tree, known=known, full_run_cutoff_ns=None,
                      interval_s=60, monotonic=time.monotonic, workers=1)
    assert watch._children("") == {"Show", "Showcase"}
    assert watch._children("Show") == {"Show/Season"}
    assert watch._purge("Show") == {"Show", "Show/Season"}
    assert watch._children("") == {"Showcase"}
    assert watch._children("Show") == set()
    tree.list_dir = lambda path: Listing()
    watch._relist("Show", 2, new=True)
    watch._relist("Show/New", 2, new=True)
    assert watch._children("") == {"Show", "Showcase"}
    assert watch._children("Show") == {"Show/New"}


def test_committing_many_removed_subtrees_does_not_rescan_the_tree():
    from app.services.library_watch import ScopePlan

    known = {"": 1, **{f"show-{i:04d}": 1 for i in range(1000)},
             **{f"show-{i:04d}/season": 1 for i in range(1000)}}
    watch = RootWatch("root", ChangedTree(known), known=known, full_run_cutoff_ns=None,
                      interval_s=60, monotonic=time.monotonic, workers=1)
    watch.dirs = counted = CountedDirs(watch.dirs)
    removed = frozenset(f"show-{i:04d}" for i in range(100))
    rows = watch.committed(ScopePlan([], {}, removed), "succeeded")
    assert rows.deletes == removed | {f"{path}/season" for path in removed}
    assert len(watch.dirs) == 1801
    assert counted.visits <= len(known)


def test_purge_preserves_sparse_restored_tree_semantics():
    known = {"": 1, "Show": 1, "Show/Missing/Child": 1, "Showcase": 1}
    watch = RootWatch("root", ChangedTree(known), known=known, full_run_cutoff_ns=None,
                      interval_s=60, monotonic=time.monotonic, workers=1)
    assert watch._purge("Show") == {"Show", "Show/Missing/Child"}
    assert watch.dirs == {"": 1, "Showcase": 1}
    assert watch._children("") == {"Showcase"}


def test_purge_finds_descendants_added_after_a_missing_parent():
    known = {"": 1, "Other": 1}
    tree = ChangedTree(known)
    tree.list_dir = lambda path: Listing()
    watch = RootWatch("root", tree, known=known, full_run_cutoff_ns=None,
                      interval_s=60, monotonic=time.monotonic, workers=1)
    watch._relist("New/Missing/Child", 2, new=True)
    assert watch._purge("New") == {"New", "New/Missing/Child"}
    assert watch.dirs == known
