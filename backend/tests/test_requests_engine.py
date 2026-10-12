"""Request engine: policy, quota, dedupe, followers, dispatch to a fake Sonarr/Radarr, failure and retry, anime profiles."""
from __future__ import annotations

import pytest
from datetime import timedelta

from arr_fake import API_KEY, FakeArr, FakeTmdb, library_title, seed_anime
from support import make_user, seed_app_settings

from app.models import ArrServer, MediaRequest, MediaRequestFollower, RequestPolicy, utcnow
from app.services import tmdb
from app.services.requests import arr, catalog, engine
from app.services.requests.status import catalog_statuses


@pytest.fixture
def db(db_factory, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(tmdb, "client_for", lambda record: FakeTmdb())
    catalog.clear_cache()
    seed_anime(21, {"tmdb_id": 37854, "tvdb_id": 81797})
    seed_anime(99, None)
    session = db_factory()
    session.add_all([make_user("admin", role="admin"), make_user("alice"), make_user("bob")])
    seed_app_settings(session, requests_enabled=True)
    yield session
    session.close()


@pytest.fixture
def radarr():  # noqa: ANN201
    fake = FakeArr("radarr")
    fake.add_lookup("tmdb:603", "The Matrix", tmdbId=603)
    yield fake
    fake.close()


@pytest.fixture
def sonarr():  # noqa: ANN201
    fake = FakeArr("sonarr")
    fake.add_lookup("tvdb:121361", "Game of Thrones", tvdbId=121361)
    fake.add_lookup("tvdb:81797", "One Piece", tvdbId=81797)
    yield fake
    fake.close()


def add_server(db, fake: FakeArr, **fields) -> ArrServer:  # noqa: ANN001
    defaults = {"root_folder": "/movies/" if fake.kind == "radarr" else "/tv/Shows/", "quality_profile_id": 4}
    if fake.kind == "sonarr":
        defaults.update(anime_root_folder="/tv/Anime/", anime_quality_profile_id=1)
    server = ArrServer(id=f"{fake.kind}-1", kind=fake.kind, name=fake.kind.title(), base_url=fake.url, api_key=API_KEY,
                       path_mappings=[], enabled=True, **{**defaults, **fields})
    db.add(server)
    db.commit()
    return server


def user(db, user_id: str):  # noqa: ANN001, ANN201
    from app.models import User

    return db.get(User, user_id)


def test_policy_resolves_member_override_then_household_then_builtin(db) -> None:  # noqa: ANN001
    assert engine.policy_for(db, "alice", "movie") == {"kind": "movie", **engine.DEFAULT_POLICY}
    db.add(RequestPolicy(id="h", user_id=None, kind="movie", can_request=True, auto_approve=True, quota_count=3, quota_days=1))
    db.commit()
    assert engine.policy_for(db, "alice", "movie")["quota_count"] == 3
    db.add(RequestPolicy(id="m", user_id="alice", kind="movie", can_request=False, auto_approve=False, quota_count=None, quota_days=None))
    db.commit()
    assert engine.policy_for(db, "alice", "movie")["can_request"] is False
    assert engine.policy_for(db, "bob", "movie")["quota_count"] == 3
    assert engine.policy_for(db, "alice", "show") == {"kind": "show", **engine.DEFAULT_POLICY}


def test_member_without_permission_is_refused(db) -> None:  # noqa: ANN001
    db.add(RequestPolicy(id="m", user_id="alice", kind="movie", can_request=False, auto_approve=False))
    db.commit()
    with pytest.raises(engine.RequestError) as caught:
        engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603})
    assert (caught.value.status, caught.value.code) == (403, "not_allowed")


def test_quota_counts_the_window_excluding_declined_and_admins_bypass(db) -> None:  # noqa: ANN001
    db.add(RequestPolicy(id="h", user_id=None, kind="movie", can_request=True, auto_approve=False, quota_count=2, quota_days=7))
    old = utcnow() - timedelta(days=8)
    for i, (status, created) in enumerate([("pending", utcnow()), ("declined", utcnow()), ("available", old)]):
        db.add(MediaRequest(id=f"r{i}", kind="movie", media_type="movie", tmdb_id=900 + i, title="x", status=status,
                            requested_by="alice", created_at=created, updated_at=created))
    db.commit()
    assert engine.quota(db, user(db, "alice"), "movie") == {
        "kind": "movie", "can_request": True, "auto_approve": False, "limit": 2, "days": 7, "used": 1, "remaining": 1}
    engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603})
    with pytest.raises(engine.RequestError) as caught:
        engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 604})
    assert (caught.value.status, caught.value.code) == (429, "quota_exceeded")
    assert engine.quota(db, user(db, "admin"), "movie")["remaining"] is None


def test_second_member_follows_the_open_request_instead_of_duplicating(db) -> None:  # noqa: ANN001
    first, created = engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603})
    assert created and first.status == "pending" and first.title == "The Matrix" and first.year == 1999
    assert first.poster_url == "https://image.tmdb.org/t/p/w500/matrix.jpg"
    again, created = engine.create(db, user(db, "bob"), {"kind": "movie", "tmdb_id": 603})
    assert not created and again.id == first.id
    engine.create(db, user(db, "bob"), {"kind": "movie", "tmdb_id": 603})  # following twice is one follower
    assert [f.user_id for f in db.query(MediaRequestFollower)] == ["bob"]
    assert db.query(MediaRequest).count() == 1
    assert engine.serialize(db, [first])[0]["followers"] == [{"id": "bob", "name": "Bob"}]


def test_show_request_merges_seasons_into_the_open_request(db) -> None:  # noqa: ANN001
    req, _ = engine.create(db, user(db, "alice"), {"kind": "show", "tmdb_id": 1399, "seasons": [1]})
    assert req.tvdb_id == 121361
    engine.create(db, user(db, "bob"), {"kind": "show", "tmdb_id": 1399, "seasons": [2, 3]})
    assert req.seasons == [1, 2, 3]
    engine.create(db, user(db, "bob"), {"kind": "show", "tmdb_id": 1399, "seasons": "all"})
    assert req.seasons == "all"


def test_anime_series_needs_a_language_and_an_anilist_id(db) -> None:  # noqa: ANN001
    for body, code in (({"kind": "anime", "tmdb_id": 37854, "anilist_id": 21}, "language_required"),
                       ({"kind": "anime", "tmdb_id": 37854, "language": "dub"}, "anilist_id_required"),
                       ({"kind": "anime", "anilist_id": 99, "language": "dub"}, "unmapped")):
        with pytest.raises(engine.RequestError) as caught:
            engine.create(db, user(db, "alice"), body)
        assert caught.value.code == code


def test_movie_already_in_the_vault_is_refused(db) -> None:  # noqa: ANN001
    library_title(db, "movie", Tmdb="603")
    with pytest.raises(engine.RequestError) as caught:
        engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603})
    assert (caught.value.status, caught.value.code) == (409, "already_available")


def test_admin_request_auto_approves_and_adds_the_movie_to_radarr(db, radarr) -> None:  # noqa: ANN001
    add_server(db, radarr)
    req, _ = engine.create(db, user(db, "admin"), {"kind": "movie", "tmdb_id": 603})
    assert req.status == "approved" and req.arr_server_id == "radarr-1" and req.arr_item_id
    [added] = radarr.library.values()
    assert (added["rootFolderPath"], added["qualityProfileId"], added["monitored"]) == ("/movies/", 4, True)
    assert added["addOptions"] == {"searchForMovie": True}


def test_member_auto_approve_policy_dispatches_an_anime_series_with_the_dub_profile(db, sonarr) -> None:  # noqa: ANN001
    add_server(db, sonarr, dub_profile_id=11, sub_profile_id=12)
    db.add(RequestPolicy(id="m", user_id="alice", kind="anime", can_request=True, auto_approve=True))
    db.commit()
    req, _ = engine.create(db, user(db, "alice"), {"kind": "anime", "tmdb_id": 37854, "anilist_id": 21, "seasons": [1], "language": "dub"})
    assert req.status == "approved" and req.decided_by is None
    [added] = sonarr.library.values()
    assert (added["seriesType"], added["rootFolderPath"], added["qualityProfileId"]) == ("anime", "/tv/Anime/", 11)
    assert [s["monitored"] for s in added["seasons"]] == [False, True, False]
    assert added["addOptions"] == {"searchForMissingEpisodes": True}


def test_series_already_in_sonarr_monitors_the_extra_seasons_and_searches_them(db, sonarr) -> None:  # noqa: ANN001
    add_server(db, sonarr)
    existing = {"id": 7, "title": "Game of Thrones", "lookup_term": "tvdb:121361", "monitored": True,
                "seasons": [{"seasonNumber": 0, "monitored": False}, {"seasonNumber": 1, "monitored": True}, {"seasonNumber": 2, "monitored": False}]}
    sonarr.library[7] = existing
    req, _ = engine.create(db, user(db, "alice"), {"kind": "show", "tmdb_id": 1399, "seasons": [2]})
    engine.approve(db, user(db, "admin"), req)
    assert req.arr_item_id == 7 and len(sonarr.library) == 1
    assert [s["monitored"] for s in sonarr.library[7]["seasons"]] == [False, True, True]
    assert sonarr.commands == [{"name": "SeasonSearch", "seriesId": 7, "seasonNumber": 2}]


def test_unreachable_arr_fails_the_request_and_retry_dispatches_again(db, radarr) -> None:  # noqa: ANN001
    add_server(db, radarr)
    radarr.down = True
    req, _ = engine.create(db, user(db, "admin"), {"kind": "movie", "tmdb_id": 603})
    assert req.status == "failed" and req.failure_reason == "Radarr endpoint returned HTTP 503"
    assert API_KEY not in req.failure_reason
    radarr.down = False
    engine.retry(db, req)
    assert req.status == "approved" and req.failure_reason is None and len(radarr.library) == 1


def test_no_server_fails_with_a_reason_and_redirect_is_not_followed(db, radarr) -> None:  # noqa: ANN001
    req, _ = engine.create(db, user(db, "admin"), {"kind": "movie", "tmdb_id": 603})
    assert (req.status, req.failure_reason) == ("failed", "No Radarr server is set up")
    add_server(db, radarr)
    radarr.redirect = "http://127.0.0.1:1/elsewhere"
    engine.retry(db, req)
    assert req.failure_reason == "Radarr endpoint returned HTTP 302"


def test_decline_and_cancel_rules(db) -> None:  # noqa: ANN001
    req, _ = engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603})
    with pytest.raises(engine.RequestError):
        engine.cancel(db, user(db, "bob"), req)
    engine.decline(db, user(db, "admin"), req, "Not this one")
    assert (req.status, req.decline_reason, req.decided_by) == ("declined", "Not this one", "admin")
    with pytest.raises(engine.RequestError):
        engine.cancel(db, user(db, "alice"), req)  # no longer pending
    again, created = engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603})  # declined is not open
    assert created and again.id != req.id
    engine.cancel(db, user(db, "alice"), again)
    assert db.get(MediaRequest, again.id) is None


def test_catalog_statuses_report_requests_and_vault_titles(db) -> None:  # noqa: ANN001
    req, _ = engine.create(db, user(db, "alice"), {"kind": "anime", "tmdb_id": 37854, "anilist_id": 21, "language": "sub"})
    library_title(db, "series", "got", Tvdb="121361", Tmdb="1399")
    statuses = catalog_statuses(db, user(db, "bob"), ["anime:21", "show:1399", "movie:603", "bogus", "anime:99"])
    assert statuses == {
        "anime:21": {"state": "pending", "request_id": req.id},
        "show:1399": {"state": "available", "library_title_id": "got"},
        "movie:603": {"state": "none"},
        "anime:99": {"state": "none"},
    }


def test_anime_language_profiles_are_created_once(db, sonarr) -> None:  # noqa: ANN001
    server = add_server(db, sonarr)
    client = arr.ArrClient.of(server)
    dub, sub = arr.ensure_anime_language_profiles(client, server)
    assert arr.ensure_anime_language_profiles(client, server) == (dub, sub)
    assert sorted(f["name"] for f in sonarr.custom_formats) == [arr.DUAL_AUDIO, arr.ENGLISH_AUDIO]
    profiles = {p["name"]: p for p in sonarr.profiles}
    assert len(profiles) == 4
    dub_profile, sub_profile = profiles[arr.DUB_PROFILE], profiles[arr.SUB_PROFILE]
    scores = lambda p: {i["name"]: i["score"] for i in p["formatItems"]}  # noqa: E731
    assert scores(dub_profile) == {arr.DUAL_AUDIO: 1000, arr.ENGLISH_AUDIO: 500} and dub_profile["minFormatScore"] == 500
    assert scores(sub_profile) == {arr.DUAL_AUDIO: 100, arr.ENGLISH_AUDIO: -1000} and sub_profile["minFormatScore"] == 0
    assert dub_profile["items"] == profiles["Any"]["items"]  # cloned from the anime profile (id 1)
    [dual] = [f for f in sonarr.custom_formats if f["name"] == arr.DUAL_AUDIO]
    assert dual["specifications"][0]["fields"] == [{"name": "value", "value": arr.DUAL_AUDIO_REGEX}]


# ---- Review fixes: ids from the server, anime routing, season follow-ups, retry --------------------------------

def test_anime_ids_come_from_the_catalog_mapping_never_the_client(db) -> None:  # noqa: ANN001
    req, _ = engine.create(db, user(db, "alice"), {"kind": "anime", "anilist_id": 21, "tmdb_id": 603, "tvdb_id": 5,
                                                   "media_type": "movie", "language": "sub"})
    assert (req.kind, req.media_type, req.tmdb_id, req.tvdb_id, req.title) == ("anime", "tv", 37854, 81797, "One Piece")


def test_an_anime_film_maps_to_radarr(db) -> None:  # noqa: ANN001
    seed_anime(199, {"tmdb_id": 129}, fmt="MOVIE")
    req, _ = engine.create(db, user(db, "alice"), {"kind": "anime", "anilist_id": 199})
    assert (req.kind, req.media_type, req.tmdb_id, req.seasons, req.language) == ("anime", "movie", 129, None, None)


def test_japanese_animation_asked_as_a_show_or_movie_is_anime(db) -> None:  # noqa: ANN001
    with pytest.raises(engine.RequestError) as caught:
        engine.create(db, user(db, "alice"), {"kind": "show", "tmdb_id": 37854})
    assert caught.value.code == "language_required"
    db.add(RequestPolicy(id="m", user_id="alice", kind="anime", can_request=False, auto_approve=False))
    db.commit()
    for body in ({"kind": "show", "tmdb_id": 37854, "language": "dub"}, {"kind": "movie", "tmdb_id": 129}):
        with pytest.raises(engine.RequestError) as caught:
            engine.create(db, user(db, "alice"), body)
        assert caught.value.code == "not_allowed"  # the anime policy applies
    req, _ = engine.create(db, user(db, "bob"), {"kind": "show", "tmdb_id": 37854, "language": "dub", "seasons": [2]})
    assert (req.kind, req.seasons, req.language) == ("anime", [2], "dub")  # explicit seasons are kept, never forced to "all"
    assert catalog_statuses(db, user(db, "bob"), ["show:37854"])["show:37854"]["request_id"] == req.id


def test_tvdb_comes_from_tmdb_and_a_tvdb_only_ask_finds_the_tmdb_id(db) -> None:  # noqa: ANN001
    req, _ = engine.create(db, user(db, "alice"), {"kind": "show", "tmdb_id": 1399, "tvdb_id": 999})
    assert req.tvdb_id == 121361
    again, created = engine.create(db, user(db, "bob"), {"kind": "show", "tvdb_id": 121361})
    assert not created and again.id == req.id and again.tmdb_id == 1399


def test_extra_seasons_on_a_running_request_need_the_members_own_approval(db, sonarr) -> None:  # noqa: ANN001
    add_server(db, sonarr)
    running, _ = engine.create(db, user(db, "admin"), {"kind": "show", "tmdb_id": 1399, "seasons": [1]})
    running.status = "processing"
    db.commit()
    extra, created = engine.create(db, user(db, "bob"), {"kind": "show", "tmdb_id": 1399, "seasons": [1, 2]})
    assert created and (extra.status, extra.seasons, extra.requested_by) == ("pending", [2], "bob")
    assert running.seasons == [1] and engine.quota(db, user(db, "bob"), "show")["used"] == 1
    assert [f.user_id for f in db.query(MediaRequestFollower)] == ["bob"]
    # A second ask for season 2 follows bob's pending request instead of making a third.
    same, created = engine.create(db, user(db, "alice"), {"kind": "show", "tmdb_id": 1399, "seasons": [2]})
    assert not created and same.id == extra.id
    # An admin's extra season merges into the running request; a failed follow-up leaves it running.
    sonarr.down = True
    merged, _ = engine.create(db, user(db, "admin"), {"kind": "show", "tmdb_id": 1399, "seasons": [3]})
    assert merged.id == running.id and running.seasons == [1, 3]
    assert (running.status, running.failure_reason) == ("processing", None)


def test_retry_accepts_an_approved_request_that_never_reached_an_arr(db, radarr) -> None:  # noqa: ANN001
    add_server(db, radarr)
    db.add(MediaRequest(id="stuck", kind="movie", media_type="movie", tmdb_id=603, title="The Matrix", status="approved", requested_by="alice"))
    db.commit()
    req = db.get(MediaRequest, "stuck")
    engine.retry(db, req)
    assert req.arr_server_id == "radarr-1"
    with pytest.raises(engine.RequestError):
        engine.retry(db, req)  # dispatched now


def test_unexpected_dispatch_errors_fail_the_request(db, radarr, monkeypatch) -> None:  # noqa: ANN001
    add_server(db, radarr)
    monkeypatch.setattr(engine, "add_movie", lambda *a: 1 / 0)
    req, _ = engine.create(db, user(db, "admin"), {"kind": "movie", "tmdb_id": 603})
    assert (req.status, req.failure_reason) == ("failed", "The download server sent an unexpected reply")


def test_existing_anime_series_gets_the_requested_language_profile(db, sonarr) -> None:  # noqa: ANN001
    add_server(db, sonarr, dub_profile_id=11, sub_profile_id=12)
    sonarr.library[8] = {"id": 8, "title": "One Piece", "lookup_term": "tvdb:81797", "qualityProfileId": 11,
                         "seasons": [{"seasonNumber": 1, "monitored": True}]}
    engine.create(db, user(db, "admin"), {"kind": "anime", "anilist_id": 21, "language": "sub", "seasons": [1]})
    assert sonarr.library[8]["qualityProfileId"] == 12
