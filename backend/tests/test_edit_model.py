"""ADR 0016: the edit and lock model on apply_field."""
from __future__ import annotations

import itertools
from datetime import datetime

import pytest
from sqlalchemy import event

from app.models import MediaTitle
from app.services.media_titles import (
    CLEARED, apply_field, is_tombstone, keep_source_value, no_match_locked, revert_field, set_item_locked,
    would_apply, write_outcome,
)

NOW = datetime(2026, 10, 2, 12, 0, 0)


def title(**fields) -> MediaTitle:  # noqa: ANN003
    defaults = {"name": "Movie", "field_sources": {}, "images": {}, "metadata_json": {}, "provider_ids": {},
                "source_values": {}, "locked": False}
    return MediaTitle(id="t1", type="movie", key="r:Movie", **{**defaults, **fields})


SOURCES = [None, "path", "tmdb", "nfo", "user"]
INCOMING = ["path", "tmdb", "nfo", "user"]
RANK = {"path": 0, "tmdb": 1, "nfo": 2, "user": 3}


@pytest.mark.parametrize(("current", "incoming", "locked"), list(itertools.product(SOURCES, INCOMING, (False, True))))
def test_write_outcome_table(current, incoming, locked) -> None:  # noqa: ANN001
    t = title(locked=locked, metadata_json={"overview": "old"}, field_sources={"overview": current} if current else {})
    outcome = write_outcome(t, "overview", "new", incoming)
    if incoming != "user" and current == "user":
        expected = "kept_edit"
    elif incoming != "user" and locked:
        expected = "kept_lock"
    elif current and RANK[incoming] < RANK[current]:
        expected = "kept_higher"
    else:
        expected = "write"  # the value differs
    assert outcome == expected
    assert would_apply(t, "overview", "new", incoming) is (expected == "write")
    snapshot = (dict(t.metadata_json), dict(t.field_sources))
    would_apply(t, "overview", "new", incoming)
    assert (dict(t.metadata_json), dict(t.field_sources)) == snapshot  # pure
    changed = apply_field(t, "overview", "new", incoming)
    assert changed is (expected == "write")
    assert (t.metadata_json["overview"] == "new") is (expected == "write")


def test_none_and_same_and_structural() -> None:
    t = title(metadata_json={"overview": "x"}, field_sources={"overview": "nfo"})
    assert write_outcome(t, "overview", None, "nfo") == "empty"
    assert write_outcome(t, "overview", None, "user") == "write"  # a clear (CLEARED is None)
    assert write_outcome(t, "name", None, "user") == "empty"  # a title always has a name
    assert write_outcome(t, "overview", "x", "nfo") == "same" and apply_field(t, "overview", "x", "nfo") is False
    assert write_outcome(t, "overview", "x", "user") == "write"  # same value, new source: still a write (a pin)
    with pytest.raises(ValueError):
        write_outcome(t, "key", "k", "user")


def test_a_user_write_lands_on_a_locked_item() -> None:
    t = title(locked=True)
    assert apply_field(t, "name", "Mine", "user") is True and t.name == "Mine"


def test_kept_value_is_recorded_on_edit_and_tracks_equal_or_higher_rank_only() -> None:
    t = title(metadata_json={"overview": "from nfo"}, field_sources={"overview": "nfo"})
    keep_source_value(t, "overview")
    apply_field(t, "overview", "mine", "user")
    assert t.source_values == {"overview": {"source": "nfo", "value": "from nfo"}}
    keep_source_value(t, "overview")  # already user: the entry is not replaced
    assert t.source_values["overview"]["value"] == "from nfo"
    assert apply_field(t, "overview", "tmdb text", "tmdb") is False   # below nfo: not recorded
    assert t.source_values["overview"]["value"] == "from nfo"
    assert apply_field(t, "overview", "nfo v2", "nfo") is False       # equal rank: the revert gets the fix
    assert t.source_values["overview"] == {"source": "nfo", "value": "nfo v2"}
    apply_field(t, "overview", "ignored", "path")                     # lower: still nfo v2
    assert t.source_values["overview"]["value"] == "nfo v2" and t.metadata_json["overview"] == "mine"


def test_kept_value_with_no_source_takes_anything() -> None:
    t = title()
    keep_source_value(t, "tagline")
    apply_field(t, "tagline", "mine", "user")
    assert t.source_values["tagline"] == {"source": None, "value": None}
    apply_field(t, "tagline", "tmdb tagline", "tmdb")
    assert t.source_values["tagline"] == {"source": "tmdb", "value": "tmdb tagline"}


def test_a_repeated_blocked_write_changes_nothing() -> None:
    t = title(metadata_json={"overview": "mine"}, field_sources={"overview": "user"})
    apply_field(t, "overview", "tmdb text", "tmdb")
    first = t.source_values
    assert apply_field(t, "overview", "tmdb text", "tmdb") is False
    assert t.source_values is first  # not reassigned: SQLAlchemy sees no change


def test_revert_restores_value_and_source() -> None:
    t = title(name="Dune (2021)", field_sources={"name": "nfo"})
    keep_source_value(t, "name")
    apply_field(t, "name", "Dune", "user")
    assert revert_field(t, "name", NOW) is True
    assert (t.name, t.field_sources["name"], t.source_values) == ("Dune (2021)", "nfo", {})
    assert revert_field(t, "name", NOW) is False  # not locked any more: a no-op


def test_revert_from_tmdb_sets_due_now_but_other_sources_do_not() -> None:
    for source, due in (("tmdb", NOW), ("nfo", None)):
        t = title(metadata_json={"overview": "src"}, field_sources={"overview": source})
        keep_source_value(t, "overview")
        apply_field(t, "overview", "mine", "user")
        revert_field(t, "overview", NOW)
        assert t.metadata_due_at == due


def test_revert_with_nothing_kept_empties_the_field_and_wakes_a_matchable_title() -> None:
    t = title(metadata_json={"tagline": "mine"}, field_sources={"tagline": "user"})
    assert revert_field(t, "tagline", NOW) is True
    assert "tagline" not in t.metadata_json and "tagline" not in t.field_sources and t.metadata_due_at == NOW
    episode = MediaTitle(id="e", type="episode", key="k", name="E", field_sources={"year": "user"}, images={},
                         metadata_json={}, provider_ids={}, source_values={}, locked=False, year=1999)
    revert_field(episode, "year", NOW)
    assert episode.year is None and episode.metadata_due_at is None  # an episode is not matchable
    t = title(name="Mine", field_sources={"name": "user"})
    revert_field(t, "name", NOW)
    assert t.name == "Mine" and "name" not in t.field_sources  # a name is never emptied


def test_revert_never_wakes_a_locked_item_or_a_deliberate_no_match() -> None:
    t = title(metadata_json={"tagline": "x"}, field_sources={"tagline": "user", "provider_ids": "user"})
    assert no_match_locked(t)
    revert_field(t, "tagline", NOW)
    assert t.metadata_due_at is None


def test_revert_an_image_and_a_tombstone() -> None:
    t = title(images={"Primary": {"path": "a.jpg", "tag": "1"}}, field_sources={"images.Primary": "path"})
    keep_source_value(t, "images.Primary")
    apply_field(t, "images.Primary", {"removed": True, "tag": "removed:abc"}, "user")
    assert is_tombstone(t.images["Primary"]) and is_tombstone(CLEARED) and not is_tombstone({"tmdb": "/x.jpg"})
    revert_field(t, "images.Primary", NOW)
    assert t.images["Primary"] == {"path": "a.jpg", "tag": "1"} and t.field_sources["images.Primary"] == "path"
    apply_field(t, "images.Logo", {"tmdb": "/l.png"}, "user")
    revert_field(t, "images.Logo", NOW)
    assert "Logo" not in t.images


def test_item_lock_blocks_every_non_user_write_and_records_kept_values() -> None:
    t = title(metadata_json={"overview": "scan"}, field_sources={"overview": "nfo", "year": "path"}, year=1999)
    assert set_item_locked(t, True, NOW) is True and t.locked and t.metadata_due_at is None
    assert set_item_locked(t, True, NOW) is False
    assert apply_field(t, "overview", "nfo v2", "nfo") is False and t.metadata_json["overview"] == "scan"
    assert apply_field(t, "overview", "tmdb", "tmdb") is False          # below nfo: nothing kept
    assert apply_field(t, "year", 2000, "nfo") is False and t.year == 1999
    assert t.source_values == {"overview": {"source": "nfo", "value": "nfo v2"}, "year": {"source": "nfo", "value": 2000}}
    assert apply_field(t, "overview", "scan", "nfo") is False           # back at the current value: the stale 'nfo v2' goes
    assert t.source_values == {"year": {"source": "nfo", "value": 2000}}
    assert apply_field(t, "name", "Mine", "user") is True               # an edit still saves


def test_item_unlock_applies_kept_values_and_wakes_the_title() -> None:
    t = title(metadata_json={"overview": "scan", "tagline": "mine"}, field_sources={"overview": "nfo", "tagline": "user"}, year=1999)
    t.source_values = {"tagline": {"source": "tmdb", "value": "tmdb tagline"}}
    set_item_locked(t, True, NOW)
    apply_field(t, "overview", "nfo v2", "nfo")
    set_item_locked(t, False, NOW)
    assert not t.locked and t.metadata_json["overview"] == "nfo v2" and t.field_sources["overview"] == "nfo"
    assert t.metadata_json["tagline"] == "mine" and t.source_values == {"tagline": {"source": "tmdb", "value": "tmdb tagline"}}
    assert t.metadata_due_at == NOW  # user fields keep their pin and entry; the title is matchable


def test_an_unedited_title_never_loads_source_values(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add(MediaTitle(id="u", type="movie", key="r:u", name="U", field_sources={"name": "path"}, images={},
                               metadata_json={}, provider_ids={}))
        session.commit()
    with db_factory() as session:
        statements: list[str] = []
        event.listen(session.get_bind(), "before_cursor_execute", lambda *a: statements.append(a[2]))
        row = session.get(MediaTitle, "u")
        for field, value, source in (("name", "U", "path"), ("name", "V", "nfo"), ("overview", "o", "tmdb"), ("name", "W", "path")):
            apply_field(row, field, value, source)
        assert not [s for s in statements if "source_values" in s.split("FROM")[0]]  # deferred column never selected


def test_a_user_clear_is_null_with_source_user_and_blocks_lower_writes() -> None:
    t = title(metadata_json={"overview": "from nfo"}, field_sources={"overview": "nfo"})
    keep_source_value(t, "overview")
    assert apply_field(t, "overview", CLEARED, "user") is True
    assert t.metadata_json["overview"] is None and t.field_sources["overview"] == "user"
    assert write_outcome(t, "overview", None, "user") == "same"
    assert apply_field(t, "overview", "tmdb text", "tmdb") is False and t.metadata_json["overview"] is None
    revert_field(t, "overview", NOW)
    assert (t.metadata_json["overview"], t.field_sources["overview"]) == ("from nfo", "nfo")


def test_the_rank_rule_for_user_fields_is_unchanged_from_2_0_1() -> None:
    """2.0.1's apply_field is: skip when rank < current rank, None never writes. A rolled-back server must agree."""
    t = title(metadata_json={"overview": "mine"}, field_sources={"overview": "user"})
    for source in ("path", "tmdb", "nfo"):
        assert apply_field(t, "overview", "x", source) is False and t.metadata_json["overview"] == "mine"
    assert apply_field(t, "overview", "mine again", "user") is True


def test_a_user_clear_of_a_title_column_blocks_the_scan_and_reverts() -> None:
    t = title(year=1999, field_sources={"year": "path"})
    keep_source_value(t, "year")
    assert apply_field(t, "year", None, "user") is True and t.year is None and t.field_sources["year"] == "user"
    assert apply_field(t, "year", 2001, "nfo") is False and t.year is None
    revert_field(t, "year", NOW)
    assert t.year == 2001 and t.field_sources["year"] == "nfo"  # the better source value kept meanwhile comes back
