"""SearchHistoryService: per-member bound, dedupe, and idempotent writes."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import SearchHistoryEntry
from app.services.search_history import MAX_ENTRIES, SearchHistoryService
from support import make_user as _member, memory_session_factory


def _session():
    return memory_session_factory()()


def test_record_dedupes_case_insensitively_and_moves_to_front() -> None:
    db = _session()
    member = _member("alice")
    db.add(member)
    db.commit()
    service = SearchHistoryService(db)

    service.record(member, "Cats")
    service.record(member, "dogs")
    touched = service.record(member, "cats")

    assert touched is not None
    listing = [record.query for record in service.list_for(member)]
    # Touching moves the entry to the front and refreshes its display casing
    # to the latest search text (a single row is kept, never a duplicate).
    assert listing == ["cats", "dogs"]
    assert db.query(SearchHistoryEntry).filter_by(user_id=member.id).count() == 2


def test_record_trims_to_max_entries_most_recent_first(monkeypatch) -> None:
    db = _session()
    member = _member("alice")
    db.add(member)
    db.commit()
    service = SearchHistoryService(db)

    # Deterministic, strictly increasing clock: a tight loop of real utcnow()
    # calls could tie at microsecond resolution and make "most recent" ambiguous.
    ticks = iter(datetime(2026, 1, 1) + timedelta(seconds=i) for i in range(MAX_ENTRIES + 5))
    monkeypatch.setattr("app.services.search_history.utcnow", lambda: next(ticks))

    for i in range(MAX_ENTRIES + 5):
        service.record(member, f"query {i}")

    listing = service.list_for(member)
    assert len(listing) == MAX_ENTRIES
    assert listing[0].query == f"query {MAX_ENTRIES + 4}"
    assert db.query(SearchHistoryEntry).filter_by(user_id=member.id).count() == MAX_ENTRIES


def test_record_skips_blank_and_oversized_queries_without_raising() -> None:
    db = _session()
    member = _member("alice")
    db.add(member)
    db.commit()
    service = SearchHistoryService(db)

    assert service.record(member, "   ") is None
    assert service.record(member, "x" * 501) is None
    assert service.list_for(member) == []


def test_delete_only_touches_the_owning_members_entry() -> None:
    db = _session()
    alice, bob = _member("alice"), _member("bob")
    db.add_all([alice, bob])
    db.commit()
    service = SearchHistoryService(db)
    entry = service.record(alice, "quiet music")
    assert entry is not None

    assert service.delete(bob, entry.id) is False
    assert service.delete(alice, "missing-id") is False
    assert service.delete(alice, entry.id) is True
    assert service.list_for(alice) == []


def test_clear_removes_only_that_members_entries() -> None:
    db = _session()
    alice, bob = _member("alice"), _member("bob")
    db.add_all([alice, bob])
    db.commit()
    service = SearchHistoryService(db)
    service.record(alice, "alpha")
    service.record(bob, "beta")

    deleted = service.clear(alice)

    assert deleted == 1
    assert service.list_for(alice) == []
    assert [record.query for record in service.list_for(bob)] == ["beta"]


def test_record_stays_idempotent_when_the_insert_races(monkeypatch) -> None:
    db = _session()
    member = _member("alice")
    db.add(member)
    db.commit()
    service = SearchHistoryService(db)
    first = service.record(member, "race query")
    assert first is not None

    real_existing = SearchHistoryService._existing
    calls = {"n": 0}

    def flaky_existing(self, *args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_existing(self, *args, **kwargs)

    monkeypatch.setattr(SearchHistoryService, "_existing", flaky_existing)
    second = service.record(member, "race query")

    assert second is not None and second.id == first.id
    assert db.query(SearchHistoryEntry).filter_by(user_id=member.id).count() == 1
