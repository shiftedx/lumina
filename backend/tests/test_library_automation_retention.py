"""Bounded import history retention."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from library_automation_support import Env

from app import db as db_module
from app.models import ImportEntry, ImportRun
from app.services import library_automation, library_retention
from app.services.library_retention import prune_history


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    db_module.init_db()
    yield Env(tmp_path)
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def db(env):
    with db_module.SessionLocal() as session:
        yield session

SC = [{"dir": "x", "deep": True}]


def runs(env, root_id, n, *, state="succeeded", scope=None, start=0):
    base = env.fake_clock.naive_utc - timedelta(days=30)
    return [env.run(root_id, state=state, scope=scope, created=base + timedelta(minutes=start + i)) for i in range(n)]


def entries(run_id, n):
    with db_module.SessionLocal() as db:
        db.add_all(ImportEntry(run_id=run_id, relative_path=f"f{i}", outcome="failed") for i in range(n))
        db.commit()


def ids(db, root_id):
    return {r for (r,) in db.query(ImportRun.id).filter(ImportRun.root_id == root_id)}


def test_keeps_newest_100_terminal_runs_per_root_and_deletes_older_with_entries(env, db):
    root = env.root("a")
    # scope=[...] makes these scoped runs, so the newest-full-run exemption does not apply
    made = runs(env, root, 105, scope=[{"dir": "x", "deep": True}])
    entries(made[0], 3)
    assert prune_history(db) == 5 + 3
    assert ids(db, root) == set(made[5:])
    assert db.query(ImportEntry).count() == 0


def test_never_deletes_the_newest_full_run_an_active_run_or_a_held_run(env, db):
    root = env.root("a")
    full = runs(env, root, 1)[0]  # oldest, and the newest full run
    held = runs(env, root, 1, state="needs_confirmation", scope=SC, start=1)[0]
    active = runs(env, root, 1, state="running", scope=SC, start=2)[0]
    scoped = runs(env, root, 101, scope=[{"dir": "x", "deep": True}], start=3)
    prune_history(db)
    left = ids(db, root)
    assert {full, held, active} <= left
    assert scoped[0] not in left and scoped[1] in left


def test_at_most_2000_rows_per_call_in_batches(env, db):
    root = env.root("a")
    made = runs(env, root, 110, scope=[{"dir": "x", "deep": True}])
    for run_id in made[:10]:
        entries(run_id, 400)
    assert prune_history(db) == 2000
    assert db.query(ImportEntry).count() + db.query(ImportRun).count() == 4000 + 110 - 2000
    while prune_history(db):
        pass
    assert ids(db, root) == set(made[10:])
    assert db.query(ImportEntry).count() == 0


def test_each_batch_is_one_write_transaction(env, db, monkeypatch):
    root = env.root("a")
    runs(env, root, 103, scope=[{"dir": "x", "deep": True}])
    calls = []
    real = library_retention.write_transaction
    monkeypatch.setattr(library_retention, "write_transaction", lambda d, *, name: (calls.append(name), real(d, name=name))[1])
    prune_history(db)
    assert calls == ["library_retention"] * 3


def test_other_roots_are_not_affected(env, db):
    a, b = env.root("a"), env.root("b")
    runs(env, a, 105, scope=[{"dir": "x", "deep": True}])
    kept = runs(env, b, 50)
    prune_history(db)
    assert ids(db, b) == set(kept)
    assert len(ids(db, a)) == 100


def test_runs_hourly_not_per_tick(env, monkeypatch):
    env.root("a")
    calls = []
    monkeypatch.setattr(library_automation, "prune_history", lambda db, **kw: calls.append(1) or 0)
    env.tick(240)
    assert len(calls) == 1
    env.tick(1)
    assert len(calls) == 2


def test_a_failed_prune_is_logged_and_retried_next_hour(env, monkeypatch):
    env.root("a")
    calls = []

    def boom(db, **kw):
        calls.append(1)
        raise RuntimeError("db busy")

    monkeypatch.setattr(library_automation, "prune_history", boom)
    env.tick(240)
    assert len(calls) == 1  # failed, not retried within the hour
    env.tick(1)
    assert len(calls) == 2
