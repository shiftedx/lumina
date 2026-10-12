"""Refresh preview and the episode table read."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import AppSettings, MediaTitle, User
from app.services import media_titles, metadata_editor as me, metadata_preview as mp, tmdb
from metadata_support import ADMIN, t1_seam, world  # noqa: F401
from test_tmdb_metadata import V3_KEY, fixture, tmdb_env  # noqa: F401
from app.models import LibraryItem
from title_support import ALICE, BOB, MOVIE, ROOT, S1E1, S1E2, SECRET_SEASON, SECRET_SERIES, SEASON1, SEASON2, SERIES, SPECIALS, uid

RANK = {"user": 3, "nfo": 2, "tmdb": 1}


@pytest.fixture(autouse=True)
def would_apply_seam(monkeypatch):  # noqa: ANN001, ANN201
    """Stand-in for would_apply (rank user > nfo > tmdb; item lock blocks)."""
    def would_apply(title, field, value, source):  # noqa: ANN001, ANN202
        if title.locked:
            return False
        current = (title.field_sources or {}).get(field)
        return RANK.get(current, 0) <= RANK[source]
    try:
        media_titles.would_apply(MediaTitle(), "x", 1, "tmdb")
    except NotImplementedError:
        monkeypatch.setattr(media_titles, "would_apply", would_apply)


@pytest.fixture
def fake(world, tmdb_env, monkeypatch):  # noqa: ANN001, ANN201
    db_factory, _ = world
    with db_factory() as db:
        db.get(AppSettings, 1).tmdb_api_key = V3_KEY
        db.commit()
    transport = tmdb_env({"/3/movie/603": fixture("movie_603"), "/3/tv/1396": fixture("tv_1396"),
                          "/3/find/tt0133093": fixture("find_tt0133093")})
    return db_factory, transport


def preview(db_factory, title_id=MOVIE):  # noqa: ANN001, ANN201
    with db_factory() as db:
        return mp.refresh_preview(db, db.get(MediaTitle, title_id))


def snapshot(db_factory):  # noqa: ANN001, ANN201
    with db_factory() as db:
        return [(t.id, t.name, t.metadata_json, t.field_sources, t.metadata_due_at, t.source_values, t.images, t.locked) for t in db.scalars(select(MediaTitle).order_by(MediaTitle.id))]


def test_outcomes_for_each_source(fake) -> None:  # noqa: ANN001
    db_factory, _ = fake
    matrix = fixture("movie_603")
    with db_factory() as db:
        t = db.get(MediaTitle, MOVIE)
        t.metadata_json = {"overview": "mine", "genres": ["Action"], "official_rating": "NC-17", "community_rating": round(matrix["vote_average"], 1),
                           "runtime_minutes": 90}
        t.field_sources = {"overview": "user", "genres": "user", "official_rating": "nfo", "community_rating": "tmdb", "runtime_minutes": "tmdb"}
        t.source_values = {"genres": {"source": "tmdb", "value": ["Action"]}}  # pinned: kept == current
        db.commit()
    fields = {f["field"]: f for f in preview(db_factory)["fields"]}
    assert fields["overview"]["outcome"] == "kept_edit"
    assert fields["genres"]["outcome"] == "kept_lock"
    assert fields["community_rating"]["outcome"] == "same"
    assert fields["tagline"]["outcome"] == "new"
    assert fields["runtime_minutes"]["outcome"] == "update"
    assert fields["official_rating"]["outcome"] == "kept_higher_source"
    assert "aired_episode_count" not in fields and not any(k.startswith("images.") for k in fields)


def test_preview_stores_nothing_and_makes_one_details_call(fake) -> None:  # noqa: ANN001
    db_factory, transport = fake
    before = snapshot(db_factory)
    preview(db_factory)
    assert snapshot(db_factory) == before
    assert [p for p in transport.paths() if p == "/3/movie/603"] == ["/3/movie/603"]


def test_series_preview_skips_season_calls(fake) -> None:  # noqa: ANN001
    db_factory, transport = fake
    with db_factory() as db:
        db.get(MediaTitle, SERIES).provider_ids = {"Tmdb": "1396"}
        db.commit()
    result = preview(db_factory, SERIES)
    assert result["tmdb_id"] == 1396
    assert not any("/season/" in p for p in transport.paths())


def test_images_list_current_and_incoming_with_outcome(fake) -> None:  # noqa: ANN001
    db_factory, _ = fake
    images = {i["type"]: i for i in preview(db_factory)["images"]}
    path = fixture("movie_603")["poster_path"]
    assert images["Primary"]["incoming_preview_url"] == f"/api/metadata/candidate-image?path={path.replace('/', '%2F')}&type=Primary"
    assert images["Primary"]["current_url"] and images["Primary"]["outcome"] in ("update", "new", "kept_higher_source")


def test_errors(world, tmdb_env, monkeypatch) -> None:  # noqa: ANN001, F811
    db_factory, _ = world
    with db_factory() as db:
        with pytest.raises(mp.PreviewError) as exc:
            mp.refresh_preview(db, db.get(MediaTitle, MOVIE))
        assert exc.value.code == "tmdb_not_configured"
        db.get(AppSettings, 1).tmdb_api_key = V3_KEY
        db.commit()
        tmdb_env({})  # 404 everywhere: the known id fails with TmdbNotFound
        movie = db.get(MediaTitle, MOVIE)
        with pytest.raises(mp.PreviewError) as exc:
            mp.refresh_preview(db, movie)
        assert exc.value.code == "tmdb_unavailable"
        tmdb_env({"/3/search/movie": {"results": []}})
        movie.provider_ids, movie.name = {}, "Zzzz Nothing"
        with pytest.raises(mp.PreviewError) as exc:
            mp.refresh_preview(db, movie)
        assert exc.value.code == "no_match" and exc.value.match
        movie.locked = True
        with pytest.raises(mp.PreviewError) as exc:
            mp.refresh_preview(db, movie)
        assert exc.value.code == "title_locked"
        with pytest.raises(me.FieldError) as wrong:
            mp.refresh_preview(db, db.get(MediaTitle, S1E1))
        assert wrong.value.reason == "wrong_type"


def table(db_factory, user_id, series=SERIES, season=None):  # noqa: ANN001, ANN201
    with db_factory() as db:
        return mp.episode_table(db, db.get(User, user_id), db.get(MediaTitle, series), season)


def test_episode_table_orders_flags_locks_and_scopes(world) -> None:  # noqa: ANN001
    db_factory, _ = world
    with db_factory() as db:
        db.get(MediaTitle, S1E1).name = "Edited"
        db.get(MediaTitle, S1E1).field_sources = {"name": "user"}
        db.get(MediaTitle, S1E2).index_number = None
        db.commit()
    result = table(db_factory, ALICE)
    assert result["season"]["id"] == SPECIALS  # omitted season: the first by index_number
    assert [s["id"] for s in result["seasons"]] == [SPECIALS, SEASON1, SEASON2]
    result = table(db_factory, ALICE, season=SEASON1)
    assert [e["title_id"] for e in result["episodes"]] == [S1E1, S1E2]  # NULL index last
    first = result["episodes"][0]
    assert first["name"] == "Edited" and first["locked_fields"] == ["name"] and first["still_url"] is None
    assert result["seasons"][1]["episode_count"] == 2 and result["season"]["episode_count"] == 2
    assert table(db_factory, ALICE, season=SEASON2)["episodes"][0]["name"] == "Return"
    with pytest.raises(me.TitleNotFound):
        table(db_factory, ALICE, season=SECRET_SEASON)  # another series' season
    with pytest.raises(me.TitleNotFound):
        table(db_factory, ALICE, series=MOVIE)
    with pytest.raises(me.TitleNotFound):  # private series is absent for ALICE, present for BOB
        table(db_factory, ALICE, series=SECRET_SERIES)
    assert table(db_factory, BOB, series=SECRET_SERIES)["season"]["id"] == SECRET_SEASON


def test_episode_table_caps_at_500(world, tmp_path) -> None:  # noqa: ANN001
    db_factory, _ = world
    with db_factory() as db:
        for n in range(3, 504):
            db.add(MediaTitle(id=uid(5000 + n), type="episode", key=f"{ROOT}:Show#s2e{n}", root_id=ROOT, name=f"E{n}", parent_id=SEASON2, index_number=n))
            db.add(LibraryItem(id=uid(6000 + n), user_id=ALICE, visibility="shared", title=f"E{n}", file_size=1, kind="episode",
                               status="available", title_id=uid(5000 + n), metadata_json={}, metadata_summary={}))
        db.commit()
    result = table(db_factory, ALICE, season=SEASON2)
    assert len(result["episodes"]) == 500 and result["season"]["episode_count"] == 502
