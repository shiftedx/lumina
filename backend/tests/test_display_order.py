"""#164 (b): a series' display order says how its files are numbered; TMDB metadata is read through that order
(TMDB episode groups, absolute fallback from aired seasons), local numbering is never rewritten."""
from __future__ import annotations

from app.services import title_metadata as tm
from test_tmdb_metadata import add_title, fixture, library, load, run, tmdb_env  # noqa: F401

DVD_GROUP = {"groups": [
    {"order": 0, "episodes": [{"order": 0, "season_number": 0, "episode_number": 1}]},
    {"order": 1, "episodes": [{"order": 0, "season_number": 1, "episode_number": 2},
                              {"order": 1, "season_number": 1, "episode_number": 1}]},
]}


def test_order_maps() -> None:
    dvd = tm.order_from_groups(DVD_GROUP["groups"], absolute=False)
    assert dvd == {(0, 1): (0, 1), (1, 1): (1, 2), (1, 2): (1, 1)}
    absolute = tm.order_from_groups(DVD_GROUP["groups"], absolute=True)
    assert absolute == {(-1, 1): (1, 2), (-1, 2): (1, 1)}  # specials are left out
    assert tm.aired_number(absolute, 1, 2) == (1, 1) and tm.aired_number(absolute, 0, 1) == (0, 1)
    assert tm.aired_number(dvd, 1, 9) is None and tm.aired_number(None, 3, 4) == (3, 4)
    seasons = tm.order_from_seasons([{"season_number": 0, "episode_count": 5}, {"season_number": 2, "episode_count": 1},
                                     {"season_number": 1, "episode_count": 2}])
    assert seasons == {(-1, 1): (1, 1), (-1, 2): (1, 2), (-1, 3): (2, 1)}


def _show(display_order: str, **meta):  # noqa: ANN003, ANN202
    ids = {"provider_ids": {"Tmdb": "1396"}, "field_sources": {"provider_ids": "nfo"}}
    series = add_title(type="series", key="root:TV/BB", name="Breaking Bad", year=2008,
                       metadata_json={"display_order": display_order, **meta}, **ids)
    child = dict(year=None, metadata_due_at=None)
    s1 = add_title(type="season", parent_id=series, key="root:TV/BB#s1", name="Season 1", index_number=1, **child)
    e1 = add_title(type="episode", parent_id=s1, key="root:TV/BB#s1e1", name="One", index_number=1, **child)
    e2 = add_title(type="episode", parent_id=s1, key="root:TV/BB#s1e2", name="Two", index_number=2, **child)
    return series, s1, e1, e2


def test_dvd_order_reads_tmdb_through_the_largest_dvd_group(library) -> None:  # noqa: ANN001, F811
    fake = library({
        "/3/tv/1396": fixture("tv_1396"),
        "/3/tv/1396/season/1": fixture("tv_1396_season_1"),
        "/3/tv/1396/episode_groups": {"results": [
            {"id": "a" * 24, "name": "DVD small", "type": 3, "episode_count": 1},
            {"id": "b" * 24, "name": "DVD", "type": 3, "episode_count": 3},
            {"id": "c" * 24, "name": "Absolute", "type": 2, "episode_count": 62},
        ]},
        f"/3/tv/episode_group/{'b' * 24}": DVD_GROUP,
    })
    series, s1, e1, e2 = _show("dvd")
    assert run(series) == "matched"
    assert f"/3/tv/episode_group/{'b' * 24}" in fake.paths()
    assert (load(e1).name, load(e2).name) == ("Cat's in the Bag...", "Pilot")
    assert load(e1).index_number == 1 and load(e1).parent_id == s1  # the files' numbering stays
    assert load(s1).metadata_json == {}  # a DVD season is not a TMDB season
    assert load(series).metadata_json["match"]["episode_group"] == "b" * 24


def test_absolute_without_a_group_flattens_aired_seasons(library) -> None:  # noqa: ANN001, F811
    library({
        "/3/tv/1396": fixture("tv_1396"),
        "/3/tv/1396/season/1": fixture("tv_1396_season_1"),
        "/3/tv/1396/episode_groups": {"results": []},
    })
    series, _, e1, e2 = _show("absolute")
    assert run(series) == "matched"
    assert (load(e1).name, load(e2).name) == ("Pilot", "Cat's in the Bag...")
    assert load(series).metadata_json["match"]["order"] == "absolute"


def test_jellyfin_series_carries_its_display_order() -> None:
    from app.models import MediaTitle
    from app.services.jellyfin import title_metadata_fields

    assert title_metadata_fields(MediaTitle(type="series", metadata_json={"display_order": "dvd"}))["DisplayOrder"] == "dvd"
    assert "DisplayOrder" not in title_metadata_fields(MediaTitle(type="series", metadata_json={"display_order": "aired"}))


def test_display_order_is_an_editable_series_field_that_wakes_a_refresh(db_factory, api_client, tmp_path) -> None:  # noqa: ANN001
    from metadata_support import ADMIN
    from support import make_user, seed_app_settings
    from title_support import SERIES, seed_tree

    from app.models import MediaTitle, User

    with db_factory() as session:
        session.add(make_user(ADMIN, role="admin", username="admin"))
        seed_tree(session, tmp_path.resolve())
        seed_app_settings(session)
    with db_factory() as session:
        admin = api_client(user=session.get(User, ADMIN), base_url="http://localhost")
    bad = admin.post("/api/metadata/edits", json={"edits": [{"title_id": SERIES, "changes": {"display_order": {"value": "story"}}}]})
    assert (bad.status_code, bad.json()["field"]) == (422, "display_order")
    ok = admin.post("/api/metadata/edits", json={"edits": [{"title_id": SERIES, "changes": {
        "display_order": {"value": "dvd"}, "episode_group": {"value": "b" * 24}}}]})
    assert ok.status_code == 200
    with db_factory() as session:
        series = session.get(MediaTitle, SERIES)
        assert series.field_sources["display_order"] == "user"
        assert series.metadata_json["display_order"] == "dvd" and series.metadata_due_at is not None
    assert admin.get(f"/api/titles/{SERIES}/metadata/episode-groups").json() == {"detail": "tmdb_not_configured"}
