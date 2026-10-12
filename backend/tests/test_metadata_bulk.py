"""Bulk edit, vocabulary and people suggestions."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import MediaTitle, TitleEdit, User
from app.persistence import write_transaction
from app.services import metadata_bulk as mb, metadata_history as mh
from app.services import metadata_editor as me
from metadata_support import ADMIN, t1_seam, world  # noqa: F401
from title_support import ALICE, MOVIE, SECRET_SERIES, SERIES


def run(world, ops, ids=(MOVIE, SERIES), user=ADMIN):  # noqa: ANN001, ANN201
    db_factory, _ = world
    with db_factory() as db:
        return mb.bulk_edit(db, db.get(User, user), list(ids), ops, owner=True)


def meta(world, title_id, key):  # noqa: ANN001, ANN201
    with world[0]() as db:
        return (db.get(MediaTitle, title_id).metadata_json or {}).get(key)


def test_add_and_remove_are_case_insensitive_and_deduped(world) -> None:  # noqa: ANN001
    run(world, [{"op": "add", "field": "genres", "values": ["noir", "ACTION"]}])
    assert meta(world, MOVIE, "genres") == ["Action", "noir"]
    assert meta(world, SERIES, "genres") == ["Drama", "noir", "ACTION"]
    run(world, [{"op": "remove", "field": "genres", "values": ["drama"]}], ids=(SERIES,))
    assert meta(world, SERIES, "genres") == ["noir", "ACTION"]


def test_set_official_rating_and_series_only_status(world) -> None:  # noqa: ANN001
    run(world, [{"op": "set", "field": "official_rating", "value": "TV-14"}])
    assert meta(world, MOVIE, "official_rating") == "TV-14" == meta(world, SERIES, "official_rating")
    result = run(world, [{"op": "set", "field": "status", "value": "Ended"}])
    assert result["skipped"] == [{"title_id": MOVIE, "reason": "wrong_type"}] and result["applied"] == 1


def test_lock_pins_and_unlock_reverts(world) -> None:  # noqa: ANN001
    run(world, [{"op": "lock", "fields": ["genres"]}], ids=(MOVIE,))
    with world[0]() as db:
        assert db.get(MediaTitle, MOVIE).field_sources["genres"] == "user"
    run(world, [{"op": "unlock", "fields": ["genres"]}], ids=(MOVIE,))
    with world[0]() as db:
        assert db.get(MediaTitle, MOVIE).field_sources.get("genres") != "user"


def test_lock_item_and_skip_locked_items_for_field_ops(world) -> None:  # noqa: ANN001
    run(world, [{"op": "lock_item"}])
    result = run(world, [{"op": "add", "field": "genres", "values": ["x"]}])
    assert result["applied"] == 0 and {s["reason"] for s in result["skipped"]} == {"locked_item"}
    result = run(world, [{"op": "unlock_item"}, {"op": "add", "field": "genres", "values": ["x"]}])
    assert result["applied"] == 2


def test_invisible_and_unknown_ids_are_skipped_not_errors(world) -> None:  # noqa: ANN001
    result = run(world, [{"op": "add", "field": "tags", "values": ["t"]}], ids=(MOVIE, SECRET_SERIES, "nope"), user=ALICE)
    assert result["applied"] == 1
    assert {(s["title_id"], s["reason"]) for s in result["skipped"]} == {(SECRET_SERIES, "not_found"), ("nope", "not_found")}


def test_one_batch_one_undo(world) -> None:  # noqa: ANN001
    result = run(world, [{"op": "add", "field": "tags", "values": ["t"]}])
    with world[0]() as db:
        rows = db.scalars(select(TitleEdit)).all()
    assert {r.batch_id for r in rows} == {result["batch_id"]} and {r.kind for r in rows} == {"bulk"}
    with world[0]() as db:
        assert mh.undo(db, db.get(User, ADMIN), result["batch_id"], owner=True)["restored"] == 2
    assert meta(world, MOVIE, "tags") is None


def test_member_bulk_unlock_of_an_owner_set_tmdb_id_is_refused(world) -> None:  # noqa: ANN001
    with world[0]() as db:
        title = db.get(MediaTitle, MOVIE)
        title.field_sources = {"provider_ids": "user"}
        title.source_values = {"provider_ids": {"source": "tmdb", "value": {"Tmdb": "1"}}}
        title.provider_ids = {"Tmdb": "2"}
        db.commit()
        with pytest.raises(me.IdentifyRequiresOwner):
            mb.bulk_edit(db, db.get(User, ALICE), [MOVIE], [{"op": "unlock", "fields": ["provider_ids"]}], owner=False)


def test_500_cap_and_dedupe(world) -> None:  # noqa: ANN001
    with pytest.raises(me.FieldError) as error:
        run(world, [{"op": "add", "field": "tags", "values": ["t"]}], ids=[str(i) for i in range(501)])
    assert error.value.reason == "too_many"
    assert run(world, [{"op": "add", "field": "tags", "values": ["t"]}], ids=(MOVIE, MOVIE))["applied"] == 1


@pytest.mark.parametrize("op", [
    {"op": "add", "field": "name", "values": ["x"]}, {"op": "add", "field": "genres"}, {"op": "set", "field": "genres", "value": "x"},
    {"op": "lock", "fields": ["bogus"]}, {"op": "set", "field": "status", "value": "Paused"},
])
def test_invalid_ops_apply_nothing(world, op) -> None:  # noqa: ANN001
    with pytest.raises(me.FieldError):
        run(world, [{"op": "add", "field": "tags", "values": ["t"]}, op])
    assert meta(world, MOVIE, "tags") is None


def test_chunks_of_100_share_one_batch(world, monkeypatch) -> None:  # noqa: ANN001
    ids = _seed_movies(world, 250)
    entered = []
    real = mb.write_transaction
    monkeypatch.setattr(mb, "write_transaction", lambda *a, **k: (entered.append(1), real(*a, **k))[1])
    result = run(world, [{"op": "add", "field": "tags", "values": ["t"]}], ids=ids)
    assert len(entered) == 3 and result["applied"] == 250
    with world[0]() as db:
        assert len({r.batch_id for r in db.scalars(select(TitleEdit))}) == 1


def test_bulk_of_500_is_fast(world) -> None:  # noqa: ANN001
    import time
    ids = _seed_movies(world, 500)
    start = time.perf_counter()
    run(world, [{"op": "add", "field": "genres", "values": ["Noir"]}], ids=ids)
    assert time.perf_counter() - start < 3


def _seed_movies(world, count):  # noqa: ANN001, ANN202
    from pathlib import Path

    from app.models import StorageRoot
    from title_support import ROOT, add_file, uid
    ids = [uid(50_000 + i) for i in range(count)]
    with world[0]() as db:
        root = Path(db.get(StorageRoot, ROOT).path)
        for n, i in enumerate(ids):
            db.add(MediaTitle(id=i, type="movie", key=f"{ROOT}:m{n}", root_id=ROOT, name=f"M{n}", metadata_json={"genres": ["Action"]}))
            add_file(db, root, f"bulk/m{n}.mkv", item_id=uid(60_000 + n), title_id=i, owner=ALICE, title=f"M{n}", kind="movie")
        db.commit()
    return ids


def test_vocabulary_is_most_used_first_visible_only(world) -> None:  # noqa: ANN001
    db_factory, _ = world
    with db_factory() as db:
        db.get(MediaTitle, SECRET_SERIES).metadata_json = {"genres": ["Hidden"]}
        db.get(MediaTitle, MOVIE).metadata_json = {**db.get(MediaTitle, MOVIE).metadata_json, "official_rating": "PG"}
        db.commit()
        alice = db.get(User, ALICE)
        assert mb.vocabulary(db, alice, "genres") == [{"value": "Action", "count": 1}, {"value": "Drama", "count": 1}]
        assert mb.vocabulary(db, alice, "official_rating") == [{"value": "PG", "count": 1}]
        with pytest.raises(me.FieldError):
            mb.vocabulary(db, alice, "name")


def test_people_suggestions(world) -> None:  # noqa: ANN001
    db_factory, _ = world
    with db_factory() as db:
        db.get(MediaTitle, MOVIE).metadata_json = {**db.get(MediaTitle, MOVIE).metadata_json, "people": [
            {"person_id": "p-amy", "name": "Amy Adams", "type": "Actor"}, {"person_id": "p-ad", "name": "Adam West", "type": "Actor"}]}
        db.get(MediaTitle, SECRET_SERIES).metadata_json = {"people": [{"person_id": "p-hid", "name": "Amy Hidden", "type": "Actor"}]}
        db.commit()
        alice = db.get(User, ALICE)
        assert [p["name"] for p in mb.people_suggestions(db, alice, "amy")] == ["Amy Adams"]
        assert [p["name"] for p in mb.people_suggestions(db, alice, "ad")] == ["Adam West", "Amy Adams"]  # prefix before substring
        assert [p["person_id"] for p in mb.people_suggestions(db, alice, "ad", limit=1)] == ["p-ad"]
        assert mb.people_suggestions(db, alice, "%") == []
        assert mb.people_suggestions(db, alice, " ") == []


def test_cap_breach_across_ops_is_caught_before_any_write_and_names_the_title(world) -> None:  # noqa: ANN001
    ops = [{"op": "add", "field": "genres", "values": [f"a{i}" for i in range(20)]},
           {"op": "add", "field": "genres", "values": [f"b{i}" for i in range(20)]}]
    before = meta(world, MOVIE, "genres")
    with pytest.raises(me.FieldError) as caught:
        run(world, ops, ids=(MOVIE,))
    assert (caught.value.reason, caught.value.field, caught.value.title_id) == ("too_many", "genres", MOVIE)
    assert meta(world, MOVIE, "genres") == before


def test_set_then_add_validates_against_the_earlier_result(world) -> None:  # noqa: ANN001
    ops = [{"op": "set", "field": "status", "value": "Paused"}, {"op": "add", "field": "tags", "values": ["t"]}]
    with pytest.raises(me.FieldError) as caught:
        run(world, ops, ids=(SERIES,))
    assert (caught.value.field, caught.value.title_id) == ("status", SERIES)
    assert meta(world, SERIES, "tags") is None
