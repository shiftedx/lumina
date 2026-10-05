"""Scoped imports: normalisation, walking, missing marks, start()."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.library_import import ImportRunError, normalize_scope

D, S = True, False


def e(path: str, deep: bool) -> dict:
    return {"dir": path, "deep": deep}


def test_normalize_sorts_and_keeps_disjoint_entries() -> None:
    got = normalize_scope([e("Show B/Season 1", S), e("Show A", D)])
    assert got == [e("Show A", D), e("Show B/Season 1", S)]


def test_deep_ancestor_absorbs_everything_below() -> None:
    assert normalize_scope([e("A/B", S), e("A", D), e("A/B/C", D)]) == [e("A", D)]


def test_shallow_ancestor_keeps_a_deep_descendant_and_sorts_by_parts() -> None:
    assert normalize_scope([e("A/B", D), e("A", S)]) == [e("A", S), e("A/B", D)]


def test_duplicate_merges_and_deep_wins() -> None:
    assert normalize_scope([e("A", S), e("A", D)]) == [e("A", D)]


def test_deep_root_means_a_full_run() -> None:
    assert normalize_scope([e("", D), e("A", S)]) is None


def test_shallow_root_is_allowed_and_sorts_first() -> None:
    assert normalize_scope([e("A", D), e("", S)]) == [e("", S), e("A", D)]


@pytest.mark.parametrize("bad", ["/A", "A/", "A//B", ".", "./A", "A/../B", "..", ".hidden", "A/.actors", "backdrops", "A/Theme-Music", "a\\b", "x/" * 25 + "y"])
def test_invalid_dirs_are_refused(bad: str) -> None:
    with pytest.raises(ImportRunError):
        normalize_scope([e(bad, D)])


def test_depth_limit_is_inclusive() -> None:
    ok = "/".join(["d"] * 24)  # MAX_DEPTH components
    assert normalize_scope([e(ok, S)]) == [e(ok, S)]


@pytest.mark.parametrize("bad", [[], [{"dir": "A"}], [{"dir": 1, "deep": D}], [{"dir": "A", "deep": 1}], [{"deep": D}], ["A"]])
def test_malformed_entries_are_refused(bad: list) -> None:
    with pytest.raises(ImportRunError):
        normalize_scope(bad)


@pytest.mark.parametrize("bad", ["A\x00B", "A\nB", "A\x1fB"])
def test_control_characters_in_a_scope_path_are_refused(bad: str) -> None:
    with pytest.raises(ImportRunError):
        normalize_scope([e(bad, D)])


def test_more_than_200_entries_is_refused_but_200_after_collapse_is_fine() -> None:
    many = [e(f"s{i:03d}", S) for i in range(201)]
    with pytest.raises(ImportRunError):
        normalize_scope(many)
    assert len(normalize_scope(many[:200])) == 200
    assert normalize_scope([e("A", D), *[e(f"A/s{i}", S) for i in range(300)]]) == [e("A", D)]  # collapse first, then count


def test_media_name_filter_is_shared_with_walk() -> None:
    from app.services.library_import import is_media_name

    assert is_media_name("movie.mkv")
    assert not is_media_name("theme.mp3") and not is_media_name("clip-sample.mkv") and not is_media_name("notes.txt")


# --- walk(recurse=) and walk_scope ---

from pathlib import Path  # noqa: E402

from app.services.library_import import MAX_DEPTH, is_media_name, is_walked_dir, walk, walk_scope  # noqa: E402


def touch(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def names(found) -> list[str]:  # noqa: ANN001
    return ["/".join(parts) + (f"!{error}" if error else "") for parts, _, error, _ in found]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    touch(root, "top.mkv", "A/a1.mkv", "A/a9.mkv", "A/Season 2/e1.mkv", "A/Season 2/e2.mkv", "A/Season 2/Extras/x.mkv",
          "B/b1.mkv", "B/Season 1/s.mkv", "C/c1.mkv")
    return root


def test_walk_recurse_false_yields_only_direct_files(tree: Path) -> None:
    assert names(walk(tree, (), ("A",), recurse=False)) == ["A/a1.mkv", "A/a9.mkv"]


def test_shallow_root_scope_yields_only_top_level_files(tree: Path) -> None:
    assert names(walk_scope(tree, (), [{"dir": "", "deep": False}])) == ["top.mkv"]


def test_deep_entry_yields_the_subtree(tree: Path) -> None:
    assert names(walk_scope(tree, (), [{"dir": "A", "deep": True}])) == [
        "A/Season 2/Extras/x.mkv", "A/Season 2/e1.mkv", "A/Season 2/e2.mkv", "A/a1.mkv", "A/a9.mkv"]  # upper-case sorts before lower-case


def test_walk_scope_output_is_globally_sorted_for_shallow_ancestor_and_deep_descendant(tree: Path) -> None:
    scope = [{"dir": "A", "deep": False}, {"dir": "A/Season 2", "deep": True}, {"dir": "C", "deep": True}]
    got = names(walk_scope(tree, (), scope))
    assert got == sorted(got, key=lambda n: tuple(n.split("/")))  # not a concatenation
    assert got == ["A/Season 2/Extras/x.mkv", "A/Season 2/e1.mkv", "A/Season 2/e2.mkv", "A/a1.mkv", "A/a9.mkv", "C/c1.mkv"]


def test_resume_after_every_file_yields_each_file_once_and_skips_none(tree: Path) -> None:
    scope = [{"dir": "A", "deep": False}, {"dir": "A/Season 2", "deep": True}, {"dir": "B", "deep": True}, {"dir": "C", "deep": False}]
    full = [parts for parts, *_ in walk_scope(tree, (), scope)]
    seen: list = []
    cursor: tuple = ()
    while True:
        batch = []
        for found in walk_scope(tree, cursor, scope):
            batch.append(found)
            if len(batch) == 1:  # a batch size of one: resume after every file
                break
        if not batch:
            break
        seen.append(batch[0][0])
        cursor = batch[0][0]
    assert seen == full and len(set(seen)) == len(seen)


def test_cursor_inside_a_later_entry_skips_earlier_entries_wholly(tree: Path) -> None:
    scope = [{"dir": "A", "deep": True}, {"dir": "C", "deep": True}]
    assert names(walk_scope(tree, ("B", "b1.mkv"), scope)) == ["C/c1.mkv"]


def test_removed_entry_directory_yields_nothing(tree: Path) -> None:
    assert names(walk_scope(tree, (), [{"dir": "Gone", "deep": True}, {"dir": "C", "deep": False}])) == ["C/c1.mkv"]


def test_unreadable_entry_is_reported_once_and_not_after_its_cursor(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os as _os
    real = _os.lstat

    def lstat(path, *a, **k):  # noqa: ANN001, ANN202
        if str(path).endswith("/B"):
            raise PermissionError(13, "denied")
        return real(path, *a, **k)

    monkeypatch.setattr("app.services.library_import.os.lstat", lstat)
    scope = [{"dir": "B", "deep": True}, {"dir": "C", "deep": True}]
    assert names(walk_scope(tree, (), scope)) == ["B!unreadable", "C/c1.mkv"]
    assert names(walk_scope(tree, ("B",), scope)) == ["C/c1.mkv"]  # a failed entry directory is not re-yielded after its cursor


def test_symlinked_entry_directory_is_reported_not_followed(tree: Path, tmp_path: Path) -> None:
    (tree / "L").symlink_to(tmp_path)
    assert names(walk_scope(tree, (), [{"dir": "L", "deep": True}])) == ["L!symlink"]


def test_ignore_marker_still_hides_a_shallow_and_a_deep_entry(tree: Path) -> None:
    touch(tree, "A/.ignore")
    assert names(walk_scope(tree, (), [{"dir": "A", "deep": False}])) == []
    assert names(walk_scope(tree, (), [{"dir": "A", "deep": True}, {"dir": "C", "deep": False}])) == ["C/c1.mkv"]


def test_walk_still_recurses_by_default_and_skips_sample_and_hidden(tree: Path) -> None:
    touch(tree, "C/clip-sample.mkv", "C/.hid.mkv", "C/theme.mp3")
    assert names(walk(tree, ())) [-1] == "top.mkv"
    assert "C/clip-sample.mkv" not in names(walk(tree, ()))


def test_name_filters_are_exported_for_the_watcher() -> None:
    assert is_media_name("e01.mkv") and not is_media_name(".hid.mkv") and not is_media_name("theme.mp3")
    assert not is_media_name("clip-sample.mkv") and not is_media_name("show.nfo")
    assert is_walked_dir("Season 1") and not is_walked_dir(".git") and not is_walked_dir("Backdrops")


# --- scoped _mark_unseen_missing and confirm ---

import shutil  # noqa: E402

from sqlalchemy import func, select  # noqa: E402

from app import db as db_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import ImportRun, LibraryItem, MediaArtifact, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import library_import as library_import_module  # noqa: E402
from app.services.library_import import LibraryImportService, drive  # noqa: E402

from test_v1_scan import PASSWORD, client_for, register_root, write  # noqa: E402  (shared helpers)


@pytest.fixture
def lib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    parent = tmp_path / "mnt"
    root = parent / "media"
    root.mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_mount_parents", str(parent))
    library_import_module.stop_event.clear()  # an earlier module's app shutdown leaves it set
    db_module.init_db()
    with db_module.SessionLocal() as db:
        db.add(User(id="admin", username="admin", display_name="Admin", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    root_id = register_root(client_for("admin"), root)  # no `with`: the app's lifespan shutdown would set drive()'s stop_event
    return root, root_id


def run_import(root_id: str, scope: list[dict] | None = None, trigger: str = "manual") -> ImportRun:
    with db_module.session_scope() as db:
        run = LibraryImportService(db).start(root_id, "admin", trigger=trigger, scope=scope)
        run_id = run.id
    drive(run_id)
    with db_module.SessionLocal() as db:
        run = db.get(ImportRun, run_id)
        db.expunge(run)
        return run


def lifecycles() -> dict[str, str]:
    with db_module.SessionLocal() as db:
        return {a.relative_path: a.lifecycle for a in db.scalars(select(MediaArtifact))}


def populate(root: Path, per: int = 3, folders=("A", "B", "C")) -> None:  # noqa: ANN001
    for f in folders:
        for i in range(per):
            write(root / f / f"{f}{i}.mkv", f"{f}{i}".encode())


def test_scoped_run_marks_only_gone_files_inside_its_scope(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root)
    run_import(root_id)
    (root / "A" / "A0.mkv").unlink()
    (root / "B" / "B0.mkv").unlink()           # outside the scope: must stay available
    run = run_import(root_id, [{"dir": "A", "deep": True}], trigger="watch")
    assert (run.state, run.counters["missing"]) == ("succeeded", 1)
    assert lifecycles()["A/A0.mkv"] == "missing" and lifecycles()["B/B0.mkv"] == "available"


def test_shallow_scope_does_not_touch_deeper_files(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    write(root / "S" / "top.mkv"); write(root / "S" / "Sub" / "deep.mkv")
    run_import(root_id)
    (root / "S" / "top.mkv").unlink(); (root / "S" / "Sub" / "deep.mkv").unlink()
    run_import(root_id, [{"dir": "S", "deep": False}], trigger="watch")
    assert (lifecycles()["S/top.mkv"], lifecycles()["S/Sub/deep.mkv"]) == ("missing", "available")


def test_shallow_root_scope_touches_only_top_level_files(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    write(root / "top.mkv"); write(root / "S" / "deep.mkv")
    run_import(root_id)
    (root / "top.mkv").unlink(); (root / "S" / "deep.mkv").unlink()
    run_import(root_id, [{"dir": "", "deep": False}], trigger="watch")
    assert (lifecycles()["top.mkv"], lifecycles()["S/deep.mkv"]) == ("missing", "available")


def test_prefix_match_is_exact_and_case_sensitive(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    write(root / "Show" / "a.mkv"); write(root / "Show2" / "b.mkv"); write(root / "Other" / "c.mkv")
    run_import(root_id)
    for p in ("Show2/b.mkv", "Other/c.mkv"):
        (root / p).unlink()
    with db_module.session_scope() as db:  # a lower-case twin row: macOS test disks are case-insensitive, so fake it in the DB
        db.scalar(select(MediaArtifact).where(MediaArtifact.relative_path == "Other/c.mkv")).relative_path = "show/c.mkv"
    run_import(root_id, [{"dir": "Show", "deep": True}], trigger="watch")
    assert (lifecycles()["Show2/b.mkv"], lifecycles()["show/c.mkv"]) == ("available", "available")


def test_dir_names_with_like_wildcards_do_not_leak(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    write(root / "100%" / "a.mkv"); write(root / "1000" / "b.mkv")
    run_import(root_id)
    (root / "1000" / "b.mkv").unlink()
    run_import(root_id, [{"dir": "100%", "deep": True}], trigger="watch")
    assert lifecycles()["1000/b.mkv"] == "available"


def test_a_deleted_scope_folder_marks_its_files_missing(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=2)
    run_import(root_id)
    shutil.rmtree(root / "C")
    run = run_import(root_id, [{"dir": "C", "deep": True}], trigger="watch")
    assert (run.state, run.counters["inspected"], run.counters["missing"]) == ("succeeded", 0, 2)  # no root_empty guard for a scoped run


def test_a_file_hidden_by_a_new_ignore_but_still_on_disk_stays_available(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=2)
    run_import(root_id)
    (root / "A" / ".ignore").write_bytes(b"")
    run = run_import(root_id, [{"dir": "A", "deep": True}], trigger="watch")
    assert run.counters.get("missing", 0) == 0 and lifecycles()["A/A0.mkv"] == "available"


def test_offline_root_marks_nothing_in_a_scoped_run(lib, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=2)
    run_import(root_id)
    (root / "A" / "A0.mkv").unlink()
    calls = {"n": 0}
    real = LibraryImportService._online

    def online(self, run, root_):  # noqa: ANN001, ANN202
        calls["n"] += 1
        return calls["n"] < 2 and real(self, run, root_)  # online at the step's start, offline for the probe after the lstats

    monkeypatch.setattr(LibraryImportService, "_online", online)
    run = run_import(root_id, [{"dir": "A", "deep": True}], trigger="watch")
    assert calls["n"] == 2  # the step's end probe runs after the lstats; no second probe
    assert (run.state, run.coverage) == ("partial", "incomplete") and lifecycles()["A/A0.mkv"] == "available"


def test_mass_guard_uses_the_root_denominator(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=100, folders=("big", "other"))   # 200 available in the root
    run_import(root_id)
    for i in range(30):
        (root / "big" / f"big{i}.mkv").unlink()          # 30 gone = 15% of 200, and > 25: applied
    applied = run_import(root_id, [{"dir": "big", "deep": True}], trigger="watch")
    assert (applied.state, applied.counters["missing"]) == ("succeeded", 30)
    for i in range(30, 100):
        (root / "big" / f"big{i}.mkv").unlink()          # 70 more of 170 available = 41%: held
    held = run_import(root_id, [{"dir": "big", "deep": True}], trigger="watch")
    assert (held.state, held.counters["missing_candidates"], held.counters["available"]) == ("needs_confirmation", 70, 170)
    assert sum(v == "missing" for v in lifecycles().values()) == 30


def test_more_candidates_than_the_lstat_budget_are_held_without_probing(lib, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=5, folders=("A",))
    run_import(root_id)
    monkeypatch.setattr(library_import_module, "MAX_GONE_CHECKS", 3)
    monkeypatch.setattr(library_import_module, "_gone", lambda path: pytest.fail("lstat past the budget"))
    (root / "A" / ".ignore").write_bytes(b"")  # 5 unseen candidates, all still on disk
    held = run_import(root_id, [{"dir": "A", "deep": True}], trigger="watch")
    assert (held.state, held.counters["missing_candidates"], held.counters["available"]) == ("needs_confirmation", 5, 5)


def test_scoped_confirm_marks_only_in_scope_gone_files(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=100, folders=("big", "other"))
    run_import(root_id)
    for i in range(100):
        (root / "big" / f"big{i}.mkv").unlink()
    (root / "other" / "other0.mkv").unlink()             # outside the held scope: confirm must not take it
    held = run_import(root_id, [{"dir": "big", "deep": True}], trigger="watch")
    assert held.state == "needs_confirmation"
    write(root / "big" / "big0.mkv", b"back")            # reappeared before the admin confirmed: _gone() spares it
    with db_module.session_scope() as db:
        run = LibraryImportService(db).confirm(db.get(ImportRun, held.id))
        assert (run.state, run.counters["missing"]) == ("succeeded", 99)
    assert lifecycles()["other/other0.mkv"] == "available" and lifecycles()["big/big0.mkv"] == "available"


def test_a_move_between_folders_converges_whichever_scope_runs_first(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    write(root / "A" / "m.mkv", b"moved-bytes"); write(root / "B" / "keep.mkv")
    run_import(root_id)
    with db_module.SessionLocal() as db:
        before = db.scalar(select(func.count(LibraryItem.id)))
    (root / "B" / "m.mkv").write_bytes(b"moved-bytes"); (root / "A" / "m.mkv").unlink()
    run_import(root_id, [{"dir": "A", "deep": False}], trigger="watch")   # source first: marked missing
    run_import(root_id, [{"dir": "B", "deep": False}], trigger="watch")   # then destination: relinks and sets available
    states = lifecycles()
    assert (states["B/m.mkv"], "A/m.mkv" in states) == ("available", False)
    with db_module.SessionLocal() as db:
        assert db.scalar(select(func.count(LibraryItem.id))) == before



def _slot_held() -> bool:
    """True while some thread (here: this one) owns the SQLite writer slot; an RLock needs another thread to tell."""
    import threading

    from app.persistence import writer_admission_lock

    got: list[bool] = []

    def probe() -> None:
        got.append(writer_admission_lock.acquire(blocking=False))
        if got[0]:
            writer_admission_lock.release()

    thread = threading.Thread(target=probe)
    thread.start(); thread.join()
    return not got[0]


def test_scoped_lstats_and_probes_never_hold_the_writer_slot(lib, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=100, folders=("big", "other"))
    run_import(root_id)
    for i in range(50):
        (root / "big" / f"big{i}.mkv").unlink()            # 25% of the root: held for confirmation
    events: list[tuple[str, bool]] = []
    real_gone, real_online = library_import_module._gone, LibraryImportService._online
    monkeypatch.setattr(library_import_module, "_gone", lambda path: events.append(("lstat", _slot_held())) or real_gone(path))
    monkeypatch.setattr(LibraryImportService, "_online", lambda self, run, r: events.append(("probe", _slot_held())) or real_online(self, run, r))
    held = run_import(root_id, [{"dir": "big", "deep": True}], trigger="watch")
    assert held.state == "needs_confirmation"
    with db_module.session_scope() as db:
        assert LibraryImportService(db).confirm(db.get(ImportRun, held.id)).counters["missing"] == 50
    assert events and not [e for e in events if e[1]], "filesystem I/O ran under the writer slot"
    kinds = [k for k, _ in events]
    runs = [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k]
    assert kinds.count("lstat") == 100 and runs[-4:] == ["lstat", "probe", "lstat", "probe"]  # step and confirm probe after their lstats


@pytest.mark.parametrize("skip", ["symlink", "too_deep"])
def test_a_harmless_skip_in_scope_still_marks_gone_files(lib, skip: str) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root)
    run_import(root_id)
    (root / "A" / "A0.mkv").unlink()
    if skip == "symlink":
        (root / "A" / "link.txt").symlink_to(root / "B" / "B1.mkv")
    else:
        write(root / "A" / Path(*["d"] * MAX_DEPTH) / "deep.mkv", b"deep")
    run = run_import(root_id, [{"dir": "A", "deep": True}], trigger="watch")
    assert run.counters["skipped"] >= 1
    assert (run.state, run.counters["missing"]) == ("succeeded", 1) and lifecycles()["A/A0.mkv"] == "missing"


def test_start_stores_trigger_and_normalised_scope(lib) -> None:  # noqa: ANN001
    _, root_id = lib
    run = run_import(root_id, [{"dir": "B", "deep": False}, {"dir": "A", "deep": True}, {"dir": "A/x", "deep": False}], trigger="watch")
    assert (run.trigger, run.scope) == ("watch", [{"dir": "A", "deep": True}, {"dir": "B", "deep": False}])


def test_a_full_run_stores_sql_null_scope_and_default_trigger(lib) -> None:  # noqa: ANN001
    _, root_id = lib
    run = run_import(root_id)
    with db_module.engine.connect() as c:
        raw = c.execute(text("SELECT trigger, scope, scope IS NULL FROM import_runs WHERE id = :i"), {"i": run.id}).one()
    assert tuple(raw) == ("manual", None, 1)


def test_deep_root_scope_is_a_full_run(lib) -> None:  # noqa: ANN001
    _, root_id = lib
    assert run_import(root_id, [{"dir": "", "deep": True}], trigger="scheduled").scope is None


def test_bad_scope_or_trigger_creates_no_run(lib) -> None:  # noqa: ANN001
    _, root_id = lib
    with db_module.session_scope() as db, pytest.raises(ImportRunError):
        LibraryImportService(db).start(root_id, "admin", scope=[{"dir": "../x", "deep": True}])
    with db_module.session_scope() as db, pytest.raises(ImportRunError):
        LibraryImportService(db).start(root_id, "admin", trigger="cron")
    with db_module.SessionLocal() as db:
        assert db.query(ImportRun).count() == 0


def test_one_active_run_per_root_any_trigger(lib) -> None:  # noqa: ANN001
    _, root_id = lib
    with db_module.session_scope() as db:
        LibraryImportService(db).start(root_id, "admin", trigger="watch", scope=[{"dir": "A", "deep": True}])
    with db_module.session_scope() as db, pytest.raises(ImportRunError):
        LibraryImportService(db).start(root_id, "admin")


# --- 2.1.0 final review fixes (bfa: I1, I5, I6) ---

import threading  # noqa: E402
import time  # noqa: E402


def test_resume_after_a_newer_run_is_refused_and_marks_nothing(lib, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """I1: a cancelled full run resumed after a watch run would mark the watch run's re-stamped files missing."""
    root, root_id = lib
    populate(root, per=20)                                # 60 files, A0..C19
    run_import(root_id)
    monkeypatch.setattr(library_import_module, "BATCH_SIZE", 10)
    with db_module.session_scope() as db:
        full_id = LibraryImportService(db).start(root_id, "admin").id
    with db_module.session_scope() as db:
        LibraryImportService(db).step(full_id)            # one batch: A0..A17 stamped, cursor inside A
    with db_module.session_scope() as db:
        LibraryImportService(db).cancel(db.get(ImportRun, full_id))
    drive(full_id)
    run_import(root_id, [{"dir": "A", "deep": True}], trigger="watch")  # re-stamps every A file with its own run id
    with db_module.session_scope() as db, pytest.raises(ImportRunError, match="newer import"):
        LibraryImportService(db).resume(db.get(ImportRun, full_id))
    with db_module.SessionLocal() as db:
        assert db.get(ImportRun, full_id).state == "cancelled"
    assert set(lifecycles().values()) == {"available"}


def test_the_newest_cancelled_run_still_resumes(lib) -> None:  # noqa: ANN001
    root, root_id = lib
    populate(root, per=2)
    with db_module.session_scope() as db:
        run_id = LibraryImportService(db).start(root_id, "admin").id
    with db_module.session_scope() as db:
        LibraryImportService(db).cancel(db.get(ImportRun, run_id))
    drive(run_id)
    with db_module.session_scope() as db:
        assert LibraryImportService(db).resume(db.get(ImportRun, run_id)).state == "running"


def test_concurrent_starts_for_one_root_create_one_run(lib, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """I6: the poller and an admin start in the same instant; the idle check and the insert are one transaction."""
    _, root_id = lib
    real = LibraryImportService._require_idle

    def slow_check(self, rid):  # noqa: ANN001, ANN202
        real(self, rid)
        time.sleep(0.3)                                    # widen the window between the check and the insert

    monkeypatch.setattr(LibraryImportService, "_require_idle", slow_check)
    outcomes: list[str] = []

    def start(trigger: str) -> None:
        try:
            with db_module.session_scope() as db:
                LibraryImportService(db).start(root_id, "admin", trigger=trigger)
            outcomes.append("started")
        except ImportRunError:
            outcomes.append("refused")

    threads = [threading.Thread(target=start, args=(t,)) for t in ("scheduled", "manual")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(outcomes) == ["refused", "started"]
    with db_module.SessionLocal() as db:
        assert db.query(ImportRun).filter(ImportRun.root_id == root_id, ImportRun.state == "running").count() == 1


@pytest.fixture
def hung_root(lib, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    """The first root's step hangs in its online probe (a hard NFS mount); root B is a second, healthy root."""
    root_a, a = lib
    populate(root_a, per=1)
    other = tmp_path / "mnt" / "other"
    other.mkdir()
    write(other / "M" / "m.mkv", b"m")
    b = register_root(client_for("admin"), other)
    release, entered = threading.Event(), threading.Event()
    real = LibraryImportService._online

    def online(self, run, root_):  # noqa: ANN001, ANN202
        if root_.id == a and not release.is_set():
            entered.set()
            release.wait(10)
        return real(self, run, root_)

    monkeypatch.setattr(LibraryImportService, "_online", online)
    with db_module.session_scope() as db:
        run_a = LibraryImportService(db).start(a, "admin").id
    stuck = threading.Thread(target=drive, args=(run_a,), daemon=True)
    stuck.start()
    assert entered.wait(5)
    yield a, b
    release.set()
    stuck.join(10)


def test_a_hung_root_does_not_block_another_roots_scan(hung_root) -> None:  # noqa: ANN001
    """I5: a step hung on one root's share holds that root only."""
    _, b = hung_root
    done: list[ImportRun] = []
    worker = threading.Thread(target=lambda: done.append(run_import(b)), daemon=True)
    worker.start()
    worker.join(5)
    assert done and done[0].state == "succeeded"


def test_confirm_on_a_hung_root_answers_busy_instead_of_waiting(hung_root, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """I5(a): confirm waits a bounded time for the root's step, then refuses with a clear message."""
    a, _ = hung_root
    monkeypatch.setattr(library_import_module, "CONFIRM_WAIT_S", 0.2)
    with db_module.SessionLocal() as db:
        held = ImportRun(id="held-a", root_id=a, user_id="admin", state="needs_confirmation", cursor="[]", counters={})
        db.add(held)
        db.commit()
        held_id = held.id
    started = time.monotonic()
    with db_module.session_scope() as db, pytest.raises(ImportRunError, match="busy"):
        LibraryImportService(db).confirm(db.get(ImportRun, held_id))
    assert time.monotonic() - started < 3
