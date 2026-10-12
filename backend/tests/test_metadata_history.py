"""Edit history, exact undo and retention."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.media_schemas import UndoResult
from app.models import MediaTitle, TitleEdit, User, utcnow
from app.persistence import write_transaction
from app.services import media_titles, metadata_editor as me, metadata_history as mh
from metadata_support import ADMIN, t1_seam, world  # noqa: F401
from title_support import ALICE, MOVIE, SECRET_SERIES, SERIES


def edit(db, title_id, field, value, *, user_id=ADMIN, kind="edit"):  # noqa: ANN001, ANN201
    title = db.get(MediaTitle, title_id)
    batch = me.Batch(db, kind, user_id)
    with write_transaction(db, name="test_edit"):
        me.write_user_field(title, field, value, batch)
    return title, batch


def seed(db, title_id, field, value, source):  # noqa: ANN001, ANN201
    media_titles.apply_field(db.get(MediaTitle, title_id), field, value, source)
    db.commit()


def undo(db, batch_id, user_id=ADMIN, owner=True):  # noqa: ANN001, ANN201
    return mh.undo(db, db.get(User, user_id), batch_id, owner=owner)


def test_history_lists_batches_newest_first_with_cut_values(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        edit(db, MOVIE, "overview", "x" * 400)
        edit(db, MOVIE, "name", "New")
        page = mh.history(db, db.get(MediaTitle, MOVIE), None, limit=1)
        assert [c["field"] for c in page["batches"][0]["changes"]] == ["name"] and page["next_cursor"] is not None
        second = mh.history(db, db.get(MediaTitle, MOVIE), page["next_cursor"], limit=1)
        change = second["batches"][0]["changes"][0]
        assert change["field"] == "overview" and len(change["after"]) == 300 and second["next_cursor"] is None
        assert page["batches"][0]["user"]["id"] == ADMIN
        db.get(User, ADMIN).is_active = False
        db.commit()
        assert mh.history(db, db.get(MediaTitle, MOVIE), None)["batches"][0]["user"] is None


def test_undo_restores_before_values_and_records_an_undo_batch(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "overview", "From NFO", "nfo")
        _, batch = edit(db, MOVIE, "overview", "Mine")
        result = undo(db, batch.id)
        assert result["restored"] == 1 and result["skipped"] == []
        title = db.get(MediaTitle, MOVIE)
        assert me.read_field(title, "overview") == ("From NFO", "nfo") and "overview" not in (title.source_values or {})
        assert {r.undone_by for r in db.scalars(select(TitleEdit).where(TitleEdit.batch_id == batch.id))} == {result["batch_id"]}
        assert db.scalar(select(TitleEdit).where(TitleEdit.batch_id == result["batch_id"])).kind == "undo"


def test_undo_skips_fields_changed_since(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        _, a = edit(db, MOVIE, "overview", "A")
        edit(db, MOVIE, "overview", "B")
        result = undo(db, a.id)
        assert result["restored"] == 0 and result["skipped"] == [{"title_id": MOVIE, "field": "overview", "reason": "changed_since"}]


def test_undo_of_an_undo_is_a_redo(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "overview", "From NFO", "nfo")
        _, batch = edit(db, MOVIE, "overview", "Mine")
        first = undo(db, batch.id)
        undo(db, first["batch_id"])
        assert me.read_field(db.get(MediaTitle, MOVIE), "overview") == ("Mine", "user")


def test_undo_of_a_revert_restores_the_kept_value(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "overview", "From TMDB", "tmdb")
        title, _ = edit(db, MOVIE, "overview", "Mine")
        batch = me.Batch(db, "revert", ADMIN)
        with write_transaction(db, name="t"):
            me.revert_field(title, "overview", batch, utcnow(), owner=True)
        undo(db, batch.id)
        title = db.get(MediaTitle, MOVIE)
        assert me.read_field(title, "overview") == ("Mine", "user")
        assert title.source_values["overview"] == {"source": "tmdb", "value": "From TMDB"}


def test_undo_twice_is_409(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        _, batch = edit(db, MOVIE, "overview", "A")
        undo(db, batch.id)
        with pytest.raises(mh.AlreadyUndone):
            undo(db, batch.id)
        with pytest.raises(mh.BatchNotFound):
            undo(db, "nope")


def test_undo_names_titles_the_caller_cannot_see_as_not_visible(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        batch = me.Batch(db, "bulk", ADMIN)
        with write_transaction(db, name="t"):
            me.write_user_field(db.get(MediaTitle, MOVIE), "tagline", "T", batch)
            me.write_user_field(db.get(MediaTitle, SECRET_SERIES), "tagline", "T", batch)
        result = undo(db, batch.id, ALICE)
        assert result["restored"] == 1 and result["skipped"] == [{"title_id": "hidden-1", "field": "tagline", "reason": "not_visible"}]  # never the real id
        page = mh.history(db, db.get(MediaTitle, MOVIE), None, user=db.get(User, ALICE))
        assert page["batches"][-1]["title_count"] == 1  # the hidden title is not counted
        assert mh.history(db, db.get(MediaTitle, MOVIE), None)["batches"][-1]["title_count"] == 2
        hidden = me.Batch(db, "bulk", ADMIN)
        with write_transaction(db, name="t"):
            me.write_user_field(db.get(MediaTitle, SECRET_SERIES), "overview", "T", hidden)
        with pytest.raises(mh.BatchNotFound):
            undo(db, hidden.id, ALICE)


def test_undo_restoring_a_tmdb_id_sets_the_title_due(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "provider_ids", {"Tmdb": "1"}, "tmdb")
        _, batch = edit(db, MOVIE, "provider_ids", {"Tmdb": "2"})
        db.get(MediaTitle, MOVIE).metadata_due_at = None
        db.commit()
        undo(db, batch.id)
        title = db.get(MediaTitle, MOVIE)
        assert title.provider_ids == {"Tmdb": "1"} and title.metadata_due_at is not None


def test_member_cannot_undo_an_owners_tmdb_id_change(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "provider_ids", {"Tmdb": "1"}, "tmdb")
        _, batch = edit(db, MOVIE, "provider_ids", {"Tmdb": "2"})
        db.get(MediaTitle, MOVIE).metadata_due_at = None
        db.commit()
        result = undo(db, batch.id, ALICE, owner=False)
        UndoResult(**result)  # the router validates through the contract schema
        title = db.get(MediaTitle, MOVIE)
        assert result["restored"] == 0 and [s["reason"] for s in result["skipped"]] == ["owner_only"]
        assert title.provider_ids == {"Tmdb": "2"} and title.metadata_due_at is None


def test_undoing_an_item_lock_unlocks_and_applies_kept(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        title = db.get(MediaTitle, MOVIE)
        batch = me.Batch(db, "edit", ADMIN)
        with write_transaction(db, name="t"):
            me.set_item_lock(title, True, batch, utcnow())
        assert db.get(MediaTitle, MOVIE).locked
        undo(db, batch.id)
        assert not db.get(MediaTitle, MOVIE).locked


def test_prune_keeps_the_newest_200_per_title_and_drops_rows_over_a_year(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        for _i in range(205):
            db.add(TitleEdit(batch_id="b", kind="edit", title_id=MOVIE, field="tagline", before=None, after="x"))
        db.add(TitleEdit(batch_id="o", kind="edit", title_id=SERIES, field="tagline", after="x", created_at=utcnow() - timedelta(days=400)))
        db.commit()
        mh.maintenance(db, force=True)
        counts = dict(db.execute(select(TitleEdit.title_id, func.count()).group_by(TitleEdit.title_id)).all())
        assert counts == {MOVIE: 200}


def test_maintenance_runs_at_most_hourly(world, monkeypatch) -> None:  # noqa: ANN001
    factory, _ = world
    calls = []
    monkeypatch.setattr(mh, "_prune", lambda db, now: calls.append(1))
    with factory() as db:
        mh.maintenance(db, force=True)
        mh.maintenance(db)
    assert calls == [1]


def test_member_undo_leaves_skipped_rows_for_the_owner(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "provider_ids", {"Tmdb": "1"}, "tmdb")
        batch = me.Batch(db, "bulk", ADMIN)
        with write_transaction(db, name="t"):
            me.write_user_field(db.get(MediaTitle, MOVIE), "provider_ids", {"Tmdb": "2"}, batch)
            me.write_user_field(db.get(MediaTitle, SERIES), "tagline", "T", batch)
        member = undo(db, batch.id, ALICE, owner=False)
        assert member["restored"] == 1 and [s["reason"] for s in member["skipped"]] == ["owner_only"]
        owner = undo(db, batch.id)  # the skipped row is still open
        assert owner["restored"] == 1 and db.get(MediaTitle, MOVIE).provider_ids == {"Tmdb": "1"}
        with pytest.raises(mh.AlreadyUndone):
            undo(db, batch.id)


def test_owner_undo_after_member_undo_skips_rows_the_member_restored(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "provider_ids", {"Tmdb": "1"}, "tmdb")
        batch = me.Batch(db, "bulk", ADMIN)
        with write_transaction(db, name="t"):
            me.write_user_field(db.get(MediaTitle, MOVIE), "provider_ids", {"Tmdb": "2"}, batch)
            me.write_user_field(db.get(MediaTitle, SERIES), "tagline", "T", batch)
        member = undo(db, batch.id, ALICE, owner=False)
        member_batch = member["batch_id"]
        newer = me.Batch(db, "bulk", ADMIN)
        with write_transaction(db, name="t"):
            me.write_user_field(db.get(MediaTitle, SERIES), "tagline", "T", newer)  # typed again later
        owner = undo(db, batch.id)
        assert owner["restored"] == 1 and owner["skipped"] == []
        assert mh.read_field(db.get(MediaTitle, SERIES), "tagline") == ("T", "user")  # the newer edit is not reverted
        tagline_row = db.scalars(select(TitleEdit).where(TitleEdit.batch_id == batch.id, TitleEdit.field == "tagline")).one()
        assert tagline_row.undone_by == member_batch


def test_history_reads_undone_only_when_every_row_is(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        seed(db, MOVIE, "provider_ids", {"Tmdb": "1"}, "tmdb")
        batch = me.Batch(db, "bulk", ADMIN)
        with write_transaction(db, name="t"):
            me.write_user_field(db.get(MediaTitle, MOVIE), "provider_ids", {"Tmdb": "2"}, batch)
            me.write_user_field(db.get(MediaTitle, MOVIE), "tagline", "T", batch)
        undo(db, batch.id, ALICE, owner=False)  # restores the tagline, skips the owner-only row
        assert {x["batch_id"]: x["undone"] for x in mh.history(db, db.get(MediaTitle, MOVIE), None)["batches"]}[batch.id] is False
        undo(db, batch.id)
        assert {x["batch_id"]: x["undone"] for x in mh.history(db, db.get(MediaTitle, MOVIE), None)["batches"]}[batch.id] is True
