"""Requests catalog (TMDB + AniList). No network: fakes serve the hand-written JSON under fixtures/requests."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import settings
from app.services import tmdb
from app.services.artwork import RemoteArtworkResponse
from app.services.network_policy import PublicSourcePolicy
from app.services.requests import anilist, catalog
from support import make_user, seed_app_settings
from test_artwork import resolver_for

FIXTURES = Path(__file__).parent / "fixtures" / "requests"
PUBLIC = ["93.184.216.34"]
KEY = "0123456789abcdef0123456789abcdef"


def fx(name: str) -> dict:
    text = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    noon = int(datetime.combine(date.today(), datetime.min.time()).astimezone().timestamp()) + 20 * 3600
    return json.loads(text.replace("__AT__", str(noon)))


class Fake:
    """One fetcher for both upstreams: TMDB routes by path, AniList by GraphQL operation name."""

    def __init__(self) -> None:
        self.tmdb = {
            "/3/genre/movie/list": fx("tmdb_genres_movie"), "/3/genre/tv/list": fx("tmdb_genres_tv"),
            "/3/trending/all/week": fx("tmdb_trending_all"), "/3/trending/movie/week": fx("tmdb_movie_list"), "/3/trending/tv/week": {"results": []},
            "/3/movie/popular": fx("tmdb_movie_list"), "/3/movie/upcoming": fx("tmdb_movie_list"), "/3/movie/top_rated": fx("tmdb_movie_list"),
            "/3/discover/movie": fx("tmdb_movie_list"), "/3/tv/popular": {"results": []}, "/3/tv/on_the_air": {"results": []},
            "/3/movie/603": fx("tmdb_movie_603"), "/3/tv/1399": fx("tmdb_tv_1399"),
            "/3/search/multi": fx("tmdb_search_multi"), "/3/search/tv": fx("tmdb_search_tv_frieren"), "/3/search/movie": {"results": []},
            "/3/tv/209867/external_ids": fx("tmdb_external_ids"), "/3/tv/37854/external_ids": fx("tmdb_external_ids"),
        }
        self.anilist = {
            "Browse": fx("anilist_browse"), "Detail": fx("anilist_detail"), "Schedule": fx("anilist_schedule"),
            "Genres": fx("anilist_genres"), "Match": fx("anilist_match"),
        }
        self.calls: list[tuple[str, dict]] = []
        self.headers: list[dict] = []

    def fetch(self, url, *, validate_redirect, headers=None, data=None):  # noqa: ANN001, ANN201
        validate_redirect(url)
        parts = urlsplit(url)
        self.headers.append(dict(headers or {}))
        if parts.hostname == "graphql.anilist.co":
            body = json.loads(data)
            op = re.match(r"query (\w+)", body["query"]).group(1)
            self.calls.append((op, body["variables"]))
            payload = self.anilist[op]
        else:
            self.calls.append((parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}))
            if parts.path not in self.tmdb:
                return RemoteArtworkResponse(content_type="application/json", body=[b"{}"], status_code=404)
            payload = self.tmdb[parts.path]
        return RemoteArtworkResponse(content_type="application/json", body=[json.dumps(payload).encode()])

    def paths(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def fake(monkeypatch):  # noqa: ANN001, ANN201
    policy = PublicSourcePolicy(resolver=resolver_for({"api.themoviedb.org": PUBLIC, "graphql.anilist.co": PUBLIC}))
    f = Fake()
    for module in (tmdb, anilist):
        monkeypatch.setattr(module, "policy", policy)
        monkeypatch.setattr(module, "transport", f)
        monkeypatch.setattr(module, "LIMITER", tmdb.RateLimiter(1_000_000))
    monkeypatch.setattr(settings, "tmdb_api_key", KEY)
    catalog.clear_cache()
    yield f
    catalog.clear_cache()


@pytest.fixture
def client(db_factory, api_client):  # noqa: ANN001, ANN201
    with db_factory() as session:
        session.add(make_user("member"))
        seed_app_settings(session)
        user = session.get(type(make_user("member")), "member")
    return api_client(user=user, base_url="http://localhost")


BASE = "/api/requests/catalog"


def test_home_rails_in_order_with_hero_and_default_status(client, fake) -> None:  # noqa: ANN001
    body = client.get(f"{BASE}/home").json()
    assert [r["key"] for r in body["rails"]] == [
        "anime_this_season", "trending", "upcoming_movies", "popular_shows", "popular_movies",
        "anime_airing_today", "anime_next_season", "on_the_air", "top_rated_movies",
    ]
    trending = next(r for r in body["rails"] if r["key"] == "trending")
    assert [i["key"] for i in trending["items"]] == ["movie:603", "show:1399"]  # the person entry is dropped
    matrix = trending["items"][0]
    assert matrix["poster_url"] == "https://image.tmdb.org/t/p/w500/matrix.jpg"
    assert matrix["backdrop_url"] == "https://image.tmdb.org/t/p/w1280/matrix-bd.jpg"
    assert matrix["genres"] == ["Action", "Science Fiction"] and matrix["requestable"] is True and matrix["status"] == {"state": "none"}
    assert [h["key"] for h in body["hero"]] == ["movie:603", "show:1399"]  # trailer-capable first
    assert body["hero"][0]["logo_url"] == "https://image.tmdb.org/t/p/original/logo-en.png" and "trailers" not in body["hero"][0]


def test_anime_items_map_to_tmdb_and_tvdb_or_are_unrequestable(client, fake) -> None:  # noqa: ANN001
    items = client.get(f"{BASE}/list/anime").json()["items"]
    frieren, film = items
    assert frieren["key"] == "anime:154587" and frieren["kind"] == "anime" and frieren["media_type"] == "tv"
    assert (frieren["tmdb_id"], frieren["tvdb_id"], frieren["requestable"]) == (209867, 424536, True)  # the live-action decoy is skipped
    assert frieren["title"] == "Frieren: Beyond Journey's End" and frieren["original_title"] == "Sousou no Frieren"
    assert frieren["overview"] == "An elf mage\noutlives her party."  # AniList markup is stripped
    assert frieren["anime"]["studios"] == ["Madhouse"] and frieren["anime"]["season"] == "FALL" and frieren["anime"]["color"] == "#aee4f5"
    assert frieren["anime"]["next_episode"]["number"] == 2 and frieren["anime"]["next_episode"]["airing_at"].count("T") == 1
    assert frieren["rating"] == 9.1 and frieren["poster_url"].startswith("https://s4.anilist.co/")
    assert film["media_type"] == "movie" and film["requestable"] is False and film["unrequestable_reason"] == "unmapped"
    assert "release_date" not in film  # a partial AniList date is not a date
    again = client.get(f"{BASE}/list/anime").json()["items"]
    assert again == items and fake.paths().count("/3/tv/209867/external_ids") == 1  # the mapping is cached


def test_movie_detail_trailers_logo_certification(client, fake) -> None:  # noqa: ANN001
    body = client.get(f"{BASE}/title/movie/603").json()
    assert [t["youtube_id"] for t in body["trailers"]] == ["official111", "fantrailer1", "teaser11111", "clipclip111"]
    assert body["trailers"][0] == {"youtube_id": "official111", "name": "Official Trailer", "type": "Trailer", "official": True}
    assert body["certification"] == "R" and body["runtime"] == 136 and body["tagline"].startswith("Welcome")
    assert body["logo_url"].endswith("/original/logo-en.png")  # English beats German for en-US
    assert body["cast"] == [{"name": "Keanu Reeves", "character": "Neo", "profile_url": "https://image.tmdb.org/t/p/w185/keanu.jpg"}]
    assert body["crew"] == [{"name": "Lana Wachowski", "job": "Director"}]
    assert [i["key"] for i in body["recommendations"]] == ["movie:604"] and body["recommendations"][0]["status"] == {"state": "none"}
    assert body["studios"] == ["Warner Bros."] and body["seasons"] == [] and body["status"] == {"state": "none"}
    request = next(q for path, q in fake.calls if path == "/3/movie/603")
    assert request["append_to_response"] == "videos,credits,recommendations,similar,external_ids,images,release_dates"


def test_show_detail_seasons_tvdb_and_missing_title(client, fake) -> None:  # noqa: ANN001
    body = client.get(f"{BASE}/title/show/1399").json()
    assert body["tvdb_id"] == 121361 and body["certification"] == "TV-MA" and body["networks"] == ["HBO"] and body["runtime"] == 57
    assert body["seasons"] == [{"number": 1, "name": "Season 1", "episode_count": 10, "air_date": "2011-04-17", "poster_url": "https://image.tmdb.org/t/p/w500/s1.jpg"}]
    assert body["crew"] == [{"name": "David Benioff", "job": "Creator"}]
    assert client.get(f"{BASE}/title/show/5").status_code == 404


def test_anime_detail_relations_trailer_and_recommendations(client, fake) -> None:  # noqa: ANN001
    body = client.get(f"{BASE}/title/anime/154587").json()
    assert body["trailers"] == [{"youtube_id": "frierentrailer", "name": "Trailer", "type": "Trailer", "official": True}]
    assert [i["key"] for i in body["anime_relations"]] == ["anime:20001"]  # the manga is not an anime relation
    assert [i["key"] for i in body["recommendations"]] == ["anime:20002"] and body["studios"] == ["Madhouse"]
    assert body["backdrop_url"].endswith("frieren-banner.jpg") and body["tvdb_id"] == 424536 and body["status_text"] == "Airing"


def test_mapped_anime_detail_borrows_tmdb_and_unmapped_keeps_anilist(client, fake) -> None:  # noqa: ANN001
    fake.tmdb["/3/tv/209867"] = fx("tmdb_tv_1399")  # the mapped TMDB show: seasons, networks, a Teaser trailer, Creator crew
    body = client.get(f"{BASE}/title/anime/154587").json()
    assert [t["youtube_id"] for t in body["trailers"]] == ["frierentrailer", "gotteaser11"]
    assert [s["number"] for s in body["seasons"]] == [1] and body["networks"] == ["HBO"] and body["crew"][0]["job"] == "Creator"
    assert body["cast"] == [
        {"name": "Atsumi Tanezaki (JP) \u00b7 Jennifer Sun Bell (EN)", "character": "Frieren", "profile_url": "https://s4.anilist.co/c1.jpg"},
        {"name": "Fern", "character": "Fern", "profile_url": "https://s4.anilist.co/c2.jpg"},
    ]
    fake.tmdb["/3/search/tv"] = {"results": []}  # unmapped: AniList only, still a full shape
    catalog.clear_cache()
    plain = client.get(f"{BASE}/title/anime/154587").json()
    assert plain["seasons"] == [] and len(plain["trailers"]) == 1 and plain["cast"][0]["character"] == "Frieren"


def test_search_rekeys_japanese_animation_to_anime_only_on_an_anilist_match(client, fake) -> None:  # noqa: ANN001
    body = client.get(f"{BASE}/search", params={"q": "frieren"}).json()
    assert [i["key"] for i in body["items"]] == ["anime:154587", "movie:603"]
    anime = body["items"][0]
    assert anime["tmdb_id"] == 37854 and anime["tvdb_id"] == 424536 and anime["requestable"] is True
    assert fake.paths().count("Match") == 1  # one batched AniList call, not one per result
    assert client.get(f"{BASE}/search", params={"q": " "}).json()["items"] == []


def test_search_keeps_tmdb_kind_when_anilist_is_down(client, fake) -> None:  # noqa: ANN001
    fake.anilist["Match"] = {"errors": []}
    items = client.get(f"{BASE}/search", params={"q": "frieren"}).json()["items"]
    assert [i["key"] for i in items] == ["show:37854", "movie:603"]


def test_lists_sections_genres_and_validation(client, fake) -> None:  # noqa: ANN001
    body = client.get(f"{BASE}/list/movie", params={"section": "popular", "page": 2}).json()
    assert (body["page"], body["total_pages"]) == (2, 2) and [i["key"] for i in body["items"]] == ["movie:603"]
    client.get(f"{BASE}/list/movie", params={"section": "upcoming", "genre": "28"})
    discover = next(q for path, q in fake.calls if path == "/3/discover/movie")
    assert discover["with_genres"] == "28" and discover["sort_by"] == "primary_release_date.asc"
    assert client.get(f"{BASE}/list/movie", params={"section": "this_season"}).status_code == 422
    assert client.get(f"{BASE}/list/podcast").status_code == 422
    assert client.get(f"{BASE}/genres/movie").json()["genres"][0] == {"id": "28", "name": "Action"}
    assert client.get(f"{BASE}/genres/anime").json()["genres"] == [{"id": "Action", "name": "Action"}, {"id": "Fantasy", "name": "Fantasy"}]


def test_anime_list_sections_and_season_query(client, fake) -> None:  # noqa: ANN001
    season, year = anilist.current_season()
    client.get(f"{BASE}/list/anime", params={"section": "next_season", "genre": "Fantasy"})
    variables = next(v for op, v in fake.calls if op == "Browse")
    assert (variables["season"], variables["seasonYear"], variables["genre"]) == (*anilist.next_season(season, year), "Fantasy")
    body = client.get(f"{BASE}/anime/season", params={"sort": "score"}).json()
    assert (body["season"], body["year"]) == (season, year) and len(body["items"]) == 2
    assert [v for op, v in fake.calls if op == "Browse"][-1]["sort"] == ["SCORE_DESC"]
    assert client.get(f"{BASE}/anime/season", params={"season": "monsoon"}).status_code == 422
    client.get(f"{BASE}/list/anime", params={"section": "movies"})
    assert [v for op, v in fake.calls if op == "Browse"][-1]["format"] == "MOVIE"


def test_schedule_groups_a_week_and_drops_adult_titles(client, fake) -> None:  # noqa: ANN001
    days = client.get(f"{BASE}/anime/schedule").json()["days"]
    assert len(days) == 7 and days[0]["date"] == date.today().isoformat()
    entries = [e for d in days for e in d["entries"]]
    assert [e["item"]["key"] for e in entries] == ["anime:888", "anime:154587"]  # most popular first
    assert entries[1]["episode"] == 3 and entries[1]["airing_at"].startswith(date.today().isoformat())
    assert {e["item"]["unrequestable_reason"] for e in entries} == {"mapping_pending"}  # no per-entry TMDB mapping here
    assert not any(p.startswith("/3/") and "search" in p or "external_ids" in p for p in fake.paths())


def test_without_a_tmdb_key_tmdb_endpoints_503_and_anime_stays_browsable(client, fake, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "tmdb_api_key", "")
    for path in ("home", "list/movie", "search?q=x", "genres/show", "title/movie/603"):
        response = client.get(f"{BASE}/{path}")
        assert (response.status_code, response.json()["detail"]) == (503, "tmdb_not_configured"), path
    items = client.get(f"{BASE}/list/anime").json()["items"]
    assert {i["unrequestable_reason"] for i in items} == {"tmdb_not_configured"} and not any(i["requestable"] for i in items)
    assert not any(p.startswith("/3/search") for p in fake.paths())


def test_upstream_failure_is_a_content_free_502(client, fake) -> None:  # noqa: ANN001
    del fake.tmdb["/3/movie/popular"]
    response = client.get(f"{BASE}/list/movie", params={"section": "popular"})
    assert response.status_code == 502 and KEY not in response.text


def test_home_survives_one_failing_rail(client, fake) -> None:  # noqa: ANN001
    del fake.tmdb["/3/tv/on_the_air"]
    keys = [r["key"] for r in client.get(f"{BASE}/home").json()["rails"]]
    assert "on_the_air" not in keys and "trending" in keys


def test_statuses_are_looked_up_once_per_response_and_cached_data_is_not_polluted(client, fake, monkeypatch) -> None:  # noqa: ANN001
    seen: list[list[str]] = []

    def statuses(session, user, keys):  # noqa: ANN001, ANN202
        seen.append(keys)
        return {"movie:603": {"state": "available", "library_title_id": "t1"}}

    monkeypatch.setattr(catalog, "catalog_statuses", statuses)
    body = client.get(f"{BASE}/title/movie/603").json()
    assert len(seen) == 1 and body["status"]["state"] == "available" and body["recommendations"][0]["status"] == {"state": "none"}
    monkeypatch.setattr(catalog, "catalog_statuses", lambda *_: {})
    assert client.get(f"{BASE}/title/movie/603").json()["status"] == {"state": "none"}
    assert fake.paths().count("/3/movie/603") == 1  # the second answer came from the cache


def test_signed_in_member_required(api_client, fake) -> None:  # noqa: ANN001
    assert api_client(base_url="http://localhost").get(f"{BASE}/home").status_code in (401, 403)


def test_season_arithmetic_and_cache_bound(monkeypatch) -> None:  # noqa: ANN001
    assert [anilist.current_season(date(2026, m, 1))[0] for m in (1, 4, 7, 10)] == ["WINTER", "SPRING", "SUMMER", "FALL"]
    assert anilist.next_season("FALL", 2026) == ("WINTER", 2027) and anilist.next_season("WINTER", 2026) == ("SPRING", 2026)
    monkeypatch.setattr(catalog, "MAX_ENTRIES", 3)
    catalog.clear_cache()
    for n in range(5):
        catalog.cached(f"k{n}", 60, lambda n=n: n)
    assert list(catalog._cache) == ["k2", "k3", "k4"]
    catalog.clear_cache()


def test_descriptions_become_plain_text() -> None:
    raw = "Line one<br>Line two <i>italic</i> &amp; more.<br><br><br>(Source: Crunchyroll)<br>[Written by MAL Rewrite]"
    assert anilist.clean_text(raw) == "Line one\nLine two italic & more."
    assert anilist.clean_text(None) is None and anilist.clean_text("<br>") is None


def test_warm_once_refreshes_when_enabled_and_idles_otherwise(db_factory, fake) -> None:  # noqa: ANN001
    from support import file_backed_session_factory

    with file_backed_session_factory("member")() as session:
        record = seed_app_settings(session, requests_enabled=False)
    catalog.warm_once(0)
    assert fake.calls == []  # disabled: nothing fetched
    with file_backed_session_factory()() as session:
        session.get(type(record), 1).requests_enabled = True
        session.commit()
    catalog.warm_once(0)
    ops = fake.paths()
    assert catalog._warm_failed == set(), catalog._warm_failed
    assert ops.count("Schedule") >= 1 and "/3/trending/all/week" in ops and "/3/movie/popular" in ops
    before = len(fake.calls)
    catalog.warm_once(1)  # off-cycle tick: only the schedule refreshes, and it bypasses the cache
    assert [name for name, _ in fake.calls[before:]] == ["Schedule"]
    assert catalog._refreshing is False
    catalog._warm_failed.add("season")  # a rate-limited season at startup is retried on the very next tick
    before = len(fake.calls)
    catalog.warm_once(2)
    assert [name for name, _ in fake.calls[before:]].count("Browse") >= 2 and catalog._warm_failed == set()


def test_anilist_sends_a_client_user_agent_and_reports_a_challenge_page_by_status(fake, monkeypatch) -> None:  # noqa: ANN001
    """Cloudflare challenges yt-dlp's browser-imitating headers with a 403 HTML page; Lumina identifies itself instead."""
    anilist.genre_names()
    sent = fake.headers[-1]
    assert sent["User-Agent"].startswith("Lumina/") and "Chrome" not in sent["User-Agent"]

    challenge = RemoteArtworkResponse(content_type="text/html", body=[b"<!DOCTYPE html><title>Just a moment...</title>"], status_code=403)
    monkeypatch.setattr(fake, "fetch", lambda *args, **kwargs: challenge)
    with pytest.raises(anilist.AniListError, match="HTTP 403"):
        anilist.genre_names()
