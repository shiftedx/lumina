"""Metadata editor core: catalogue, edit/lock/revert model, save pipeline."""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.models import AppSettings, MediaTitle, TitleEdit, User, utcnow
from app.persistence import write_transaction
from app.services import embeddings, library_search, metadata_editor as me
from app.services import media_titles
from metadata_support import ADMIN, t1_seam, world  # noqa: F401
from support import make_user
from title_support import ALICE, MOVIE, S1E1, SEASON1, SERIES


def apply_field(*args):  # noqa: ANN002, ANN201
    return media_titles.apply_field(*args)  # late-bound so the seam fixture applies


VALID = [  # (type, field, input, normalised)
    ("movie", "name", "  Dune ", "Dune"), ("movie", "sort_name", "", None), ("movie", "year", 1999, 1999),
    ("movie", "premiered", "2020-03-01", "2020-03-01"), ("series", "end_date", "2024-01-31", "2024-01-31"),
    ("series", "status", "Ended", "Ended"), ("series", "air_days", ["Friday", "Monday", "Monday"], ["Monday", "Friday"]),
    ("series", "air_time", "21:30", "21:30"), ("movie", "community_rating", 7.5, 7.5), ("movie", "critic_rating", 88, 88),
    ("movie", "genres", [" Drama ", "drama", "Noir"], ["Drama", "Noir"]), ("movie", "tags", ["kids"], ["kids"]),
    ("episode", "index_number", 0, 0), ("episode", "index_number_end", None, None),
    ("movie", "provider_ids", {"Tmdb": "603", "Imdb": "tt0133093", "Foo": "x"}, {"Tmdb": "603", "Imdb": "tt0133093", "Foo": "x"}),
    ("movie", "overview", "x" * 20_000, "x" * 20_000), ("movie", "runtime_minutes", 2000, 2000),
]
INVALID = [  # (type, field, input)
    ("movie", "name", ""), ("movie", "name", None), ("movie", "name", "x" * 301), ("movie", "year", 1869), ("movie", "year", True),
    ("movie", "premiered", "20200301"), ("movie", "premiered", "2020-13-01"), ("series", "status", "Paused"),
    ("series", "air_time", "24:00"), ("series", "air_days", ["Funday"]), ("movie", "community_rating", 10.5),
    ("movie", "community_rating", 7.55), ("movie", "critic_rating", 100.5), ("movie", "genres", ["a"] * 31 + ["b"]),
    ("movie", "tags", ["x" * 61]), ("movie", "overview", "x" * 20_001), ("movie", "runtime_minutes", 0),
    ("episode", "index_number", 10_000), ("movie", "index_number", 1),          # index_number is season/episode only
    ("season", "genres", ["Drama"]), ("movie", "end_date", "2024-01-01"),       # wrong type for this title type
    ("movie", "provider_ids", {"Imdb": "tt12"}), ("movie", "provider_ids", {"Tmdb": "abc"}),
    ("movie", "provider_ids", {f"K{i}": "v" for i in range(11)}), ("movie", "bogus", "x"),
    ("movie", "added_at", "2999-01-01T00:00:00"),
]


@pytest.mark.parametrize(("type_", "field", "value", "expected"), VALID)
def test_validate_accepts_and_normalises(type_, field, value, expected) -> None:  # noqa: ANN001
    assert me.validate(type_, field, value) == expected


@pytest.mark.parametrize(("type_", "field", "value"), INVALID)
def test_validate_refuses(type_, field, value) -> None:  # noqa: ANN001
    with pytest.raises(me.FieldError):
        me.validate(type_, field, value)


def test_people_validation_keeps_ids_only_for_existing_credits() -> None:
    refs = me.validate("movie", "people", [{"name": "Amy Adams", "role": "Louise", "type": "Actor"}])
    assert refs[0]["person_id"] and refs[0]["type"] == "Actor"
    with pytest.raises(me.FieldError):
        me.validate("movie", "people", [{"name": "A", "type": "Janitor"}])
    with pytest.raises(me.FieldError):
        me.validate("movie", "people", [{"name": "A", "type": "Actor"}] * 201)


def test_can_edit_details_matrix() -> None:
    off, on = AppSettings(members_edit_metadata=False), AppSettings(members_edit_metadata=True)
    admin, member, gone = make_user("a", role="admin"), make_user("m"), make_user("g", is_active=False)
    assert [me.can_edit_details(u, s) for u, s in ((admin, off), (member, off), (member, on), (gone, on))] == [True, False, True, False]


def edit(db, title_id, field, value, *, user_id=ADMIN, kind="edit"):  # noqa: ANN001, ANN201
    title = db.get(MediaTitle, title_id)
    batch = me.Batch(db, kind, user_id)
    with write_transaction(db, name="test_edit"):
        changed = me.write_user_field(title, field, value, batch)
    return title, batch, changed


def test_an_edit_is_a_user_write_that_keeps_the_source_value(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        apply_field(db.get(MediaTitle, MOVIE), "overview", "From NFO", "nfo"); db.commit()
        title, batch, changed = edit(db, MOVIE, "overview", "Mine")
        assert changed and title.metadata_json["overview"] == "Mine" and title.field_sources["overview"] == "user"
        assert title.source_values["overview"] == {"source": "nfo", "value": "From NFO"}
        row = db.scalar(select(TitleEdit).where(TitleEdit.batch_id == batch.id))
        assert (row.field, row.before, row.before_source, row.after, row.after_source, row.kind) == ("overview", "From NFO", "nfo", "Mine", "user", "edit")
        edit(db, MOVIE, "overview", "Mine again")  # a second edit keeps the FIRST source value, not "Mine"
        assert db.get(MediaTitle, MOVIE).source_values["overview"]["value"] == "From NFO"
        assert edit(db, MOVIE, "overview", "Mine again")[2] is False  # same value, already user: no row


def test_edits_stick_against_every_lower_source(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        title, *_ = edit(db, MOVIE, "overview", "Mine")
        for source in ("path", "tmdb", "nfo"):
            assert apply_field(title, "overview", f"{source} text", source) is False
        assert title.metadata_json["overview"] == "Mine"
        assert title.source_values["overview"]["value"] == "nfo text"  # The blocked nfo write is kept for revert


def test_a_cleared_field_survives_a_refresh_and_reads_as_absent(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        title, *_ = edit(db, MOVIE, "genres", None)
        assert title.metadata_json["genres"] is None and title.field_sources["genres"] == "user"
        assert apply_field(title, "genres", ["Action"], "tmdb") is False
        assert me.read_field(title, "genres") == (None, "user")
        with pytest.raises(me.FieldError):
            edit(db, MOVIE, "name", None)


def test_revert_restores_the_kept_value_and_source_with_no_network(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        apply_field(db.get(MediaTitle, MOVIE), "overview", "From TMDB", "tmdb"); db.commit()
        title, *_ = edit(db, MOVIE, "overview", "Mine")
        batch = me.Batch(db, "revert", ADMIN)
        with write_transaction(db, name="t"):
            assert me.revert_field(title, "overview", batch, utcnow(), owner=True) is True
        assert me.read_field(title, "overview") == ("From TMDB", "tmdb") and "overview" not in title.source_values
        assert title.metadata_due_at is not None  # a tmdb value is refreshed soon
        with write_transaction(db, name="t"):
            assert me.revert_field(title, "overview", batch, utcnow(), owner=True) is False  # not locked: no-op


def test_revert_with_nothing_kept_empties_a_meta_field_and_never_nulls_the_name(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        title, *_ = edit(db, MOVIE, "tagline", "Hello")  # the field was empty: kept = {source: None, value: None}
        with write_transaction(db, name="t"):
            me.revert_field(title, "tagline", me.Batch(db, "revert", ADMIN), utcnow(), owner=True)
        assert "tagline" not in title.metadata_json and "tagline" not in title.field_sources
        title.source_values = {}
        title.field_sources = {**title.field_sources, "name": "user"}
        with write_transaction(db, name="t"):
            me.revert_field(title, "name", me.Batch(db, "revert", ADMIN), utcnow(), owner=True)
        assert title.name == "Movie" and "name" not in title.field_sources


def test_pin_locks_the_current_value_without_changing_it(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        title = db.get(MediaTitle, MOVIE)
        with write_transaction(db, name="t"):
            assert me.pin_field(title, "official_rating", me.Batch(db, "lock", ADMIN)) is True
        assert title.field_sources["official_rating"] == "user" and title.source_values["official_rating"] == {"source": None, "value": None}
        assert db.scalar(select(TitleEdit.kind).where(TitleEdit.field == "official_rating")) == "lock"


def test_item_lock_and_unlock_apply_kept_values(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        title = db.get(MediaTitle, MOVIE)
        with write_transaction(db, name="t"):
            me.set_item_lock(title, True, me.Batch(db, "item_lock", ADMIN), utcnow())
        assert title.locked is True and title.metadata_due_at is None
        assert apply_field(title, "overview", "scan text", "tmdb") is False  # Blocked, kept
        with write_transaction(db, name="t"):
            me.set_item_lock(title, False, me.Batch(db, "item_lock", ADMIN), utcnow())
        assert title.locked is False and title.metadata_json["overview"] == "scan text" and "overview" not in title.source_values
        assert title.metadata_due_at is not None


def entry(title_id, **changes):  # noqa: ANN202
    return me.EditEntry(title_id=title_id, changes={k: me.Change(value=v[0], base=v[1], has_base=True) for k, v in changes.items()}, pin=[], locked=None)


def test_save_applies_in_one_batch_and_finds_the_new_name(world, monkeypatch) -> None:  # noqa: ANN001
    factory, _ = world
    calls = []
    monkeypatch.setattr(embeddings, "start_backfill", lambda *a: calls.append(1))
    with factory() as db:
        user = db.get(User, ADMIN)
        batch_id, applied, conflicts = me.save_edits(db, user, [
            entry(MOVIE, name=("Zebra Heist", "Movie"), overview=("Stripes.", "A heist.")),
            entry(SERIES, tagline=("Hi", None)),
        ], owner=True)
        assert sorted(applied) == sorted([MOVIE, SERIES]) and conflicts == []
        assert {r.batch_id for r in db.scalars(select(TitleEdit))} == {batch_id}
        hits = db.execute(text(f"SELECT title_id FROM {library_search.TITLE_FTS_TABLE} WHERE {library_search.TITLE_FTS_TABLE} MATCH 'zebra'")).all()
        assert [h[0] for h in hits] == [MOVIE] and calls == [1]


def test_save_reindexes_only_searchable_changes(world, monkeypatch) -> None:  # noqa: ANN001
    factory, _ = world
    seen = []
    real = library_search.index_title
    monkeypatch.setattr(library_search, "index_title", lambda db, t: (seen.append(t.id), real(db, t))[1])
    with factory() as db:
        user = db.get(User, ADMIN)
        me.save_edits(db, user, [entry(MOVIE, tagline=("t", None), community_rating=(8.0, 7.9))], owner=True)
        assert seen == []
        me.save_edits(db, user, [entry(MOVIE, genres=(["Noir"], ["Action"]))], owner=True)
        assert seen[0] == MOVIE and set(seen) <= {MOVIE, db.get(MediaTitle, MOVIE).boxset_id}  # a movie's boxset follows


def test_invalid_field_applies_nothing(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        user = db.get(User, ADMIN)
        with pytest.raises(me.FieldError) as raised:
            me.save_edits(db, user, [entry(MOVIE, overview=("ok", "A heist.")), entry(SERIES, year=(3000, 2019))], owner=True)
        assert (raised.value.title_id, raised.value.field) == (SERIES, "year")
        assert db.get(MediaTitle, MOVIE).metadata_json["overview"] == "A heist." and db.scalar(select(TitleEdit.id)) is None


def test_conflict_refuses_only_that_title(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        user = db.get(User, ADMIN)
        _, applied, conflicts = me.save_edits(db, user, [entry(MOVIE, overview=("New", "STALE")), entry(SERIES, overview=("New", "A show."))], owner=True)
        assert applied == [SERIES]
        assert conflicts[0]["title_id"] == MOVIE and conflicts[0]["fields"] == ["overview"]
        assert conflicts[0]["current"]["overview"]["value"] == "A heist."
        assert db.get(MediaTitle, MOVIE).metadata_json["overview"] == "A heist."


def test_member_cannot_change_tmdb_but_owner_identifies(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        move = entry(MOVIE, provider_ids=({"Tmdb": "604", "Imdb": "tt0133093"}, {"Tmdb": "603", "Imdb": "tt0133093"}))
        with pytest.raises(me.IdentifyRequiresOwner):
            me.save_edits(db, db.get(User, ALICE), [move], owner=False)
        me.save_edits(db, db.get(User, ADMIN), [move], owner=True)
        title = db.get(MediaTitle, MOVIE)
        assert title.provider_ids == {"Tmdb": "604", "Imdb": "tt0133093"} and title.metadata_due_at is not None
        row = db.scalar(select(TitleEdit).where(TitleEdit.field == "provider_ids"))
        assert row.before == {"Tmdb": "603", "Imdb": "tt0133093"}  # the history shows the id the title had, not identify's intermediate


def test_item_lock_change_in_a_save(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        me.save_edits(db, db.get(User, ADMIN), [me.EditEntry(title_id=MOVIE, changes={}, pin=["genres"], locked=True)], owner=True)
        title = db.get(MediaTitle, MOVIE)
        assert title.locked and title.field_sources["genres"] == "user"


def test_doc_lists_only_fields_valid_for_the_type_with_state(world) -> None:  # noqa: ANN001
    factory, _ = world
    with factory() as db:
        user = db.get(User, ADMIN)
        edit(db, MOVIE, "overview", "Mine")
        movie, episode = (me.build_docs(db, user, [db.get(MediaTitle, t)])[0] for t in (MOVIE, S1E1))
        assert "end_date" not in movie.fields and "index_number" not in movie.fields and "index_number" in episode.fields
        assert movie.fields["overview"].locked and movie.fields["overview"].source == "user"
        assert movie.history_count == 1 and episode.parent.id == SEASON1 and movie.can_identify
        assert [i.type for i in movie.images] == ["Primary"] and movie.images[0].origin == "local"


def test_revert_of_an_edited_added_at_restores_the_original_date(world) -> None:  # noqa: ANN001
    from datetime import datetime
    factory, _ = world
    with factory() as db:
        db.get(MediaTitle, MOVIE).added_at = datetime(2024, 5, 1)
        db.commit()
        title, *_ = edit(db, MOVIE, "added_at", "2001-01-01T00:00:00")
        assert title.added_at == datetime(2001, 1, 1)
        with write_transaction(db, name="t"):
            me.revert_field(title, "added_at", me.Batch(db, "revert", ADMIN), utcnow(), owner=True)
        assert title.added_at == datetime(2024, 5, 1) and "added_at" not in (title.field_sources or {})
