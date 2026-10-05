"""Requests catalog: TMDB + AniList -> normalized CatalogItem / CatalogDetail (spec: requests-design, Catalog).

Plain dicts shaped like the web app's TypeScript types. Every response is built from deep copies of cached upstream
results, then annotated once with request status. Anime items carry a cached AniList -> TMDB/TVDB mapping.
"""
from __future__ import annotations

import copy
import logging
import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import User
from app.services import tmdb
from app.services.requests import anilist
from app.services.requests.status import catalog_statuses
from app.services.tmdb import TmdbClient

logger = logging.getLogger(__name__)
KINDS = ("movie", "show", "anime")
SECTIONS = {
    "movie": ("trending", "popular", "upcoming", "now_playing", "top_rated"),
    "show": ("trending", "popular", "on_the_air", "airing_today", "top_rated"),
    "anime": ("this_season", "next_season", "trending", "popular", "top", "movies"),
}
TTL_LIST, TTL_DETAIL, TTL_SEASON, TTL_SCHEDULE, TTL_MAPPING = 6 * 3600, 24 * 3600, 3600, 900, 7 * 24 * 3600
MAX_ENTRIES = 1024
RAIL_SIZE = 20
MAP_BUDGET = 40  # anime items resolved against TMDB per response; the rest resolve on later loads or on their detail page
ANIMATION_GENRE = 16
NONE_STATUS = {"state": "none"}


class CatalogError(Exception):
    """A request the catalog refuses: ``status`` and a stable ``code``."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


# Per-process TTL cache (dict + monotonic clock, oldest-first eviction); concurrent identical requests may both
# fetch. Fine under ADR 0006's single process; move to a table if a second process ever serves the catalog.
_cache: dict[str, tuple[float, Any]] = {}


def clear_cache() -> None:
    _cache.clear()
    _warm_failed.clear()


_refreshing = False  # A process-wide flag, not per-call; a user request during a warm pass just refetches too


def cached(key: str, ttl: float, fetch: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[0] > now and not (_refreshing and not key.startswith("map:")):
        return copy.deepcopy(hit[1])
    value = fetch()
    for stale in [k for k, (expires, _) in _cache.items() if expires <= now]:
        del _cache[stale]
    while len(_cache) >= MAX_ENTRIES:
        del _cache[next(iter(_cache))]
    _cache[key] = (now + ttl, copy.deepcopy(value))
    return value


def _need(client: TmdbClient | None) -> TmdbClient:
    if client is None:
        raise CatalogError(503, "tmdb_not_configured")
    return client


def _parallel(fn: Callable, values: list) -> list:
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(fn, values))


# ---- TMDB -> CatalogItem ----------------------------------------------------------

def _image(path: object, size: str) -> str | None:
    path = tmdb._image_path(path)
    return f"{tmdb.IMAGE_BASE}/{size}{path}" if path else None


def _genre_names(client: TmdbClient, tmdb_kind: str) -> dict[int, str]:
    def fetch() -> dict[int, str]:
        return {g["id"]: g["name"] for g in tmdb._list(client.genres(tmdb_kind).get("genres")) if type(g.get("id")) is int and g.get("name")}
    return cached(f"tmdb:genres:{tmdb_kind}", TTL_DETAIL, fetch)


def _tmdb_item(raw: dict, media_type: str, names: dict[int, str]) -> dict | None:
    tmdb_id = tmdb._positive(raw.get("id"))
    title = tmdb._str(raw.get("title") or raw.get("name"))
    if tmdb_id is None or title is None or raw.get("adult") is True:  # adult titles never reach the catalog (or the showcase)
        return None
    kind = "movie" if media_type == "movie" else "show"
    release = tmdb._date(raw.get("release_date") or raw.get("first_air_date"))
    genres = [g["name"] for g in tmdb._list(raw.get("genres")) if g.get("name")] or [
        names[g] for g in tmdb._list(raw.get("genre_ids")) if g in names
    ]
    fields = {
        "key": f"{kind}:{tmdb_id}", "kind": kind, "tmdb_id": tmdb_id, "media_type": media_type, "title": title,
        "original_title": tmdb._str(raw.get("original_title") or raw.get("original_name")), "year": tmdb._year(release),
        "overview": tmdb._str(raw.get("overview")), "release_date": release,
        "poster_url": _image(raw.get("poster_path"), "w500"), "backdrop_url": _image(raw.get("backdrop_path"), "w1280"),
        "rating": tmdb._rating(raw), "genres": genres, "requestable": True,
    }
    if fields["original_title"] == title:
        del fields["original_title"]
    return {k: v for k, v in fields.items() if v is not None}


def _tmdb_items(client: TmdbClient, raws: Iterable, media_type: str | None) -> list[dict]:
    """Items from a TMDB results list; ``media_type`` None reads each entry's own (trending/search multi)."""
    all_names = {t: _genre_names(client, t) for t in ("movie", "tv")}
    items = []
    for raw in tmdb._list(raws):
        raw = tmdb._dict(raw)
        kind = media_type or raw.get("media_type")
        if kind in ("movie", "tv") and (item := _tmdb_item(raw, kind, all_names[kind])):
            items.append(item)
    return items


# ---- AniList -> CatalogItem + TMDB mapping ----------------------------------------

def _is_japanese_animation(raw: dict) -> bool:
    return ANIMATION_GENRE in tmdb._list(raw.get("genre_ids")) and (
        raw.get("original_language") == "ja" or "JP" in tmdb._list(raw.get("origin_country"))
    )


def _map_to_tmdb(client: TmdbClient, media: dict, hint: int | None = None) -> dict | None:
    """``{tmdb_id, tvdb_id?}`` for an AniList media (search english/romaji title, then external_ids), or None."""
    movie = media.get("format") == "MOVIE"
    tmdb_kind = "movie" if movie else "tv"
    tmdb_id = hint
    if tmdb_id is None:
        titles = media.get("title") or {}
        year = (media.get("startDate") or {}).get("year") or media.get("seasonYear")
        names = [n for n in dict.fromkeys((titles.get("english"), titles.get("romaji"))) if n]
        for name, search_year in [(n, y) for y in ((year, None) if year else (None,)) for n in names]:
            params = {("primary_release_year" if movie else "first_air_date_year"): search_year} if search_year else {}
            found = client.get(f"/search/{tmdb_kind}", query=name, include_adult="false", **params)
            match = next((r for r in tmdb._list(found.get("results"))[:5] if _is_japanese_animation(tmdb._dict(r))), None)
            if match:
                tmdb_id = match["id"]
                break
    if tmdb_id is None:
        return None
    result: dict = {"tmdb_id": tmdb_id}
    if not movie:
        tvdb = tmdb._positive(client.external_ids("tv", tmdb_id).get("tvdb_id"))
        if tvdb:
            result["tvdb_id"] = tvdb
    return result


def _anime_items(client: TmdbClient | None, medias: list[dict], hints: dict[int, int] | None = None, budget: int = MAP_BUDGET) -> list[dict]:
    """Anime CatalogItems with ids and ``requestable`` from the (cached) TMDB mapping."""
    items = [anilist.item(m) for m in medias]
    pending: list[dict] = []
    mapped: dict[int, dict | None] = {}
    for media in medias:
        hit = _cache.get(f"map:{media['id']}")
        if hit is not None and hit[0] > time.monotonic():
            mapped[media["id"]] = hit[1]
        elif client is not None and len(pending) < budget:
            pending.append(media)

    def resolve(media: dict) -> dict | None:
        try:
            return cached(f"map:{media['id']}", TTL_MAPPING, lambda: _map_to_tmdb(client, media, (hints or {}).get(media["id"])))
        except tmdb.TmdbError:
            return False  # transient: not cached, reported as unavailable

    for media, result in zip(pending, _parallel(resolve, pending)):
        mapped[media["id"]] = result
    for item in items:
        result = mapped.get(item["anilist_id"], "pending")
        reason = None
        if client is None:
            reason = "tmdb_not_configured"
        elif result == "pending":
            reason = "mapping_pending"
        elif result is False:
            reason = "tmdb_unavailable"
        elif result is None or (item["media_type"] == "tv" and "tvdb_id" not in result):
            reason = "unmapped"
        if isinstance(result, dict):
            item.update(result)
        item["requestable"] = reason is None
        if reason:
            item["unrequestable_reason"] = reason
    return items


def _rekey_anime(client: TmdbClient, items: list[dict], raws: list[dict]) -> list[dict]:
    """Japanese animation in TMDB search results becomes its AniList entry when one title matches (one batched call)."""
    flagged = [(i, raw) for i, raw in enumerate(raws) if _is_japanese_animation(raw)][:6]
    if not flagged:
        return items
    try:
        matches = anilist.search_titles([(items[i]["title"], items[i]["media_type"] == "movie") for i, _ in flagged])
    except anilist.AniListError:
        return items
    out = list(items)
    for (i, _), media in zip(flagged, matches):
        if media is None:
            continue
        titles = {_norm(v) for v in (media.get("title") or {}).values() if v}
        if _norm(items[i]["title"]) in titles or _norm(items[i].get("original_title") or "") in titles:
            out[i] = _anime_items(client, [media], {media["id"]: items[i]["tmdb_id"]})[0]
    return out


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


# ---- Lists ------------------------------------------------------------------------

def _today() -> str:
    return date.today().isoformat()


def _tmdb_section(client: TmdbClient, kind: str, section: str, genre: str | None, page: int) -> dict:
    media_type = "movie" if kind == "movie" else "tv"
    names = _genre_names(client, media_type)
    today = date.today()
    day = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    if genre:
        by = {
            "trending": {"sort_by": "popularity.desc"}, "popular": {"sort_by": "popularity.desc"},
            "top_rated": {"sort_by": "vote_average.desc", "vote_count.gte": 300},
            "upcoming": {"sort_by": "primary_release_date.asc", "primary_release_date.gte": day(1)},
            "now_playing": {"sort_by": "popularity.desc", "primary_release_date.gte": day(-45), "primary_release_date.lte": _today()},
            "on_the_air": {"sort_by": "popularity.desc", "air_date.gte": _today(), "air_date.lte": day(7)},
            "airing_today": {"sort_by": "popularity.desc", "air_date.gte": _today(), "air_date.lte": _today()},
        }[section]
        data = client.discover(media_type, page, with_genres=genre, **by)
    elif section == "trending":
        data = client.trending(media_type, page)
    else:
        data = client.listing(media_type, section, page)
    return {"items": _tmdb_items(client, data.get("results"), media_type), "page": page,
            "total_pages": min(int(data.get("total_pages") or 1), 500)}


def _anime_section(client: TmdbClient | None, section: str, genre: str | None, page: int) -> dict:
    season, year = anilist.current_season()
    if section == "next_season":
        season, year = anilist.next_season(season, year)
    variables: dict[str, Any] = {"genre": genre or None, "sort": ["POPULARITY_DESC"]}
    if section in ("this_season", "next_season"):
        variables.update(season=season, seasonYear=year)
    elif section == "trending":
        variables["sort"] = ["TRENDING_DESC"]
    elif section == "top":
        variables["sort"] = ["SCORE_DESC"]
    elif section == "movies":
        variables["format"] = "MOVIE"
    medias, pages = cached(
        f"anilist:{section}:{variables}:{page}", TTL_SEASON if "season" in section else TTL_LIST,
        lambda: anilist.browse(page=page, **variables),
    )
    return {"items": _anime_items(client, medias), "page": page, "total_pages": pages}


def list_items(client: TmdbClient | None, kind: str, section: str | None, genre: str | None, page: int) -> dict:
    section = section or ("this_season" if kind == "anime" else "trending")
    if section not in SECTIONS[kind]:
        raise CatalogError(422, "invalid_section")
    if kind == "anime":
        return _anime_section(client, section, genre, page)
    client = _need(client)
    return cached(f"tmdb:list:{kind}:{section}:{genre}:{page}:{_today()}", TTL_LIST, lambda: _tmdb_section(client, kind, section, genre, page))


def anime_season(client: TmdbClient | None, season: str | None, year: int | None, sort: str, page: int) -> dict:
    cur_season, cur_year = anilist.current_season()
    season, year = (season or cur_season).upper(), year or cur_year
    if season not in anilist.SEASONS or sort not in ("popularity", "score", "title"):
        raise CatalogError(422, "invalid_season")
    order = {"popularity": "POPULARITY_DESC", "score": "SCORE_DESC", "title": "TITLE_ROMAJI"}[sort]
    medias, _ = cached(
        f"anilist:season:{season}:{year}:{order}:{page}", TTL_SEASON,
        lambda: anilist.browse(page=page, per_page=50, season=season, seasonYear=year, sort=[order]),
    )
    return {"season": season, "year": year, "items": _anime_items(client, medias)}


def anime_schedule(client: TmdbClient | None, budget: int = 0) -> dict:
    """Mapping is lazy here (budget 0: only cached mappings, the rest ``mapping_pending``); the warm-up passes a budget."""
    start = datetime.combine(date.today(), datetime.min.time()).astimezone()
    entries = cached(
        f"anilist:schedule:{_today()}", TTL_SCHEDULE,
        lambda: anilist.schedule(int(start.timestamp()), int((start + timedelta(days=7)).timestamp()), 6),
    )
    return _group_schedule(entries, _anime_items(client, list({e["media"]["id"]: e["media"] for e in entries}.values()), budget=budget), start.date())


def _group_schedule(entries: list[dict], items: list[dict], first_day: date) -> dict:
    by_id = {i["anilist_id"]: i for i in items}
    days = [{"date": (first_day + timedelta(days=n)).isoformat(), "entries": []} for n in range(7)]
    for e in entries:
        airing = datetime.fromtimestamp(e["airingAt"]).astimezone()
        index = (airing.date() - first_day).days
        if 0 <= index < 7:
            days[index]["entries"].append({"item": copy.deepcopy(by_id[e["media"]["id"]]), "episode": e["episode"], "airing_at": airing.isoformat()})
    for day in days:  # the busiest shows first; the frontend caps each day
        day["entries"].sort(key=lambda e: -(e["item"].get("anime", {}).get("popularity") or 0))
    return {"days": days}


# ---- Search, genres, detail -------------------------------------------------------

def search(client: TmdbClient | None, q: str, page: int) -> dict:
    client = _need(client)
    q = q.strip()
    if not q:
        return {"items": [], "page": page, "total_pages": 1}
    data = cached(f"tmdb:search:{q.lower()}:{page}", TTL_SEASON, lambda: client.search_multi(q, page))
    raws = [tmdb._dict(r) for r in tmdb._list(data.get("results")) if tmdb._dict(r).get("media_type") in ("movie", "tv")]
    items = _tmdb_items(client, raws, None)
    raws = [r for r in raws if tmdb._positive(r.get("id")) and tmdb._str(r.get("title") or r.get("name"))]
    return {"items": _rekey_anime(client, items, raws), "page": page, "total_pages": min(int(data.get("total_pages") or 1), 500)}


def genres(client: TmdbClient | None, kind: str) -> dict:
    if kind == "anime":
        names = cached("anilist:genres", TTL_DETAIL, anilist.genre_names)
        return {"genres": [{"id": n, "name": n} for n in names]}
    names = _genre_names(_need(client), "movie" if kind == "movie" else "tv")
    return {"genres": [{"id": str(i), "name": n} for i, n in sorted(names.items(), key=lambda kv: kv[1])]}


def _trailers(videos: object) -> list[dict]:
    """YouTube videos, official Trailers first, then Teasers, then the rest (stable within each rank)."""
    found = [
        v for v in tmdb._list(tmdb._dict(videos).get("results"))
        if tmdb._dict(v).get("site") == "YouTube" and isinstance(v.get("key"), str) and re.fullmatch(r"[\w-]{6,20}", v["key"])
    ]
    rank = lambda v: (("Trailer", "Teaser").index(v.get("type")) if v.get("type") in ("Trailer", "Teaser") else 2, not v.get("official"))  # noqa: E731
    return [
        {"youtube_id": v["key"], "name": tmdb._str(v.get("name")) or "Trailer", "type": str(v.get("type") or "Clip"), "official": bool(v.get("official"))}
        for v in sorted(found, key=rank)
    ]


def _certification(raw: dict, media_type: str, region: str) -> str | None:
    if media_type == "movie":
        for entry in tmdb._list(tmdb._dict(raw.get("release_dates")).get("results")):
            if entry.get("iso_3166_1") == region:
                certs = [r.get("certification") for r in tmdb._list(entry.get("release_dates")) if r.get("certification")]
                return certs[0] if certs else None
        return None
    for entry in tmdb._list(tmdb._dict(raw.get("content_ratings")).get("results")):
        if entry.get("iso_3166_1") == region:
            return tmdb._str(entry.get("rating"))
    return None


def _tmdb_detail(client: TmdbClient, kind: str, tmdb_id: int) -> dict:
    media_type = "movie" if kind == "movie" else "tv"
    raw = client.details(media_type, tmdb_id)
    names = _genre_names(client, media_type)
    detail = _tmdb_item(raw, media_type, names)
    if detail is None:
        raise tmdb.TmdbNotFound("TMDB has no such entry")
    tvdb = tmdb._positive(tmdb._dict(raw.get("external_ids")).get("tvdb_id"))
    logos = tmdb._list(tmdb._dict(raw.get("images")).get("logos"))
    order = {client.lang2: 0, "en": 1, None: 2}
    logo = min((l for l in logos if tmdb._image_path(l.get("file_path"))), key=lambda l: order.get(l.get("iso_639_1"), 3), default=None)
    crew_jobs = ("Director", "Writer", "Screenplay", "Creator", "Producer")
    crew = [{"name": c["name"], "job": c["job"]} for c in tmdb._list(tmdb._dict(raw.get("credits")).get("crew")) if c.get("name") and c.get("job") in crew_jobs]
    crew += [{"name": c["name"], "job": "Creator"} for c in tmdb._list(raw.get("created_by")) if c.get("name")]
    runtime = tmdb._positive(raw.get("runtime")) or next((m for m in tmdb._list(raw.get("episode_run_time")) if type(m) is int and m > 0), None)
    extra = {
        "tvdb_id": tvdb, "logo_url": _image(logo["file_path"], "original") if logo else None,
        "tagline": tmdb._str(raw.get("tagline")), "runtime": runtime, "certification": _certification(raw, media_type, client.region),
        "status_text": tmdb._str(raw.get("status")),
    }
    detail.update({k: v for k, v in extra.items() if v is not None})
    detail.update(
        seasons=[
            {k: v for k, v in {"number": s["season_number"], "name": tmdb._str(s.get("name")) or f"Season {s['season_number']}",
             "episode_count": tmdb._int(s.get("episode_count")) or 0, "air_date": tmdb._date(s.get("air_date")),
             "poster_url": _image(s.get("poster_path"), "w500")}.items() if v is not None}
            for s in tmdb._list(raw.get("seasons")) if type(tmdb._dict(s).get("season_number")) is int and s["season_number"] > 0
        ],
        trailers=_trailers(raw.get("videos")),
        cast=[
            {k: v for k, v in {"name": c["name"], "character": tmdb._str(c.get("character")), "profile_url": _image(c.get("profile_path"), "w185")}.items() if v is not None}
            for c in tmdb._list(tmdb._dict(raw.get("credits")).get("cast"))[:20] if c.get("name")
        ],
        crew=crew[:10], networks=tmdb._names(raw.get("networks")), studios=tmdb._names(raw.get("production_companies")),
        recommendations=_tmdb_items(client, tmdb._dict(raw.get("recommendations")).get("results"), media_type)[:12],
        similar=_tmdb_items(client, tmdb._dict(raw.get("similar")).get("results"), media_type)[:12],
    )
    return detail


def _anime_detail(client: TmdbClient | None, anilist_id: int) -> dict:
    media = cached(f"anilist:detail:{anilist_id}", TTL_DETAIL, lambda: anilist.query(anilist.DETAIL, {"id": anilist_id})["Media"])
    if not isinstance(media, dict):
        raise CatalogError(404, "not_found")
    relations = [e["node"] for e in (media.get("relations") or {}).get("edges") or [] if (e.get("node") or {}).get("type") == "ANIME"]
    recommended = [n["mediaRecommendation"] for n in (media.get("recommendations") or {}).get("nodes") or [] if n.get("mediaRecommendation")]
    items = _anime_items(client, [media, *relations, *recommended])
    detail, rest = items[0], items[1:]
    detail.update(
        runtime=media.get("duration"), status_text=anilist.status_text(media), seasons=[], trailers=anilist.trailer(media),
        cast=anilist.cast(media), crew=[], networks=[], studios=detail["anime"].get("studios", []),
        recommendations=rest[len(relations):], similar=[], anime_relations=rest[:len(relations)],
    )
    if client is not None and detail.get("tmdb_id"):
        try:  # the mapped TMDB entry fills what AniList lacks (seasons, networks, crew, similar, more trailers)
            tmdb_full = _tmdb_cached_detail(client, "movie" if detail["media_type"] == "movie" else "show", detail["tmdb_id"])
        except (tmdb.TmdbError, CatalogError):
            tmdb_full = None
        if tmdb_full:
            seen = {t["youtube_id"] for t in detail["trailers"]}
            detail["trailers"] += [t for t in tmdb_full["trailers"] if t["youtube_id"] not in seen]
            detail["cast"] = detail["cast"] or tmdb_full["cast"]
            for field in ("seasons", "networks", "similar", "crew"):
                detail[field] = tmdb_full[field]
    return {k: v for k, v in detail.items() if v is not None}


def _tmdb_cached_detail(client: TmdbClient, kind: str, item_id: int) -> dict:
    try:
        return cached(f"tmdb:detail:{kind}:{item_id}", TTL_DETAIL, lambda: _tmdb_detail(client, kind, item_id))
    except tmdb.TmdbNotFound:
        raise CatalogError(404, "not_found") from None


def detail(client: TmdbClient | None, kind: str, item_id: int) -> dict:
    if kind == "anime":
        return _anime_detail(client, item_id)
    return _tmdb_cached_detail(_need(client), kind, item_id)


# ---- Home -------------------------------------------------------------------------

def _airing_today(client: TmdbClient | None) -> list[dict]:
    start = datetime.combine(date.today(), datetime.min.time()).astimezone()
    entries = cached(
        f"anilist:airing-today:{_today()}", TTL_SCHEDULE,
        lambda: anilist.schedule(int(start.timestamp()), int((start + timedelta(days=1)).timestamp()), 1),
    )
    medias = list({e["media"]["id"]: e["media"] for e in entries}.values())
    return _anime_items(client, medias[:RAIL_SIZE])


def _hero(client: TmdbClient, trending: list[dict]) -> list[dict]:
    """Up to 8 trending titles with a backdrop, trailer-capable ones first, enriched with their logo."""
    candidates = [i for i in trending if i.get("backdrop_url")][:12]

    def enrich(item: dict) -> tuple[bool, dict]:
        try:
            full = detail(client, item["kind"], item["tmdb_id"])
        except (tmdb.TmdbError, CatalogError):
            return False, item
        return bool(full.get("trailers")), {k: full[k] for k in full if k not in ("seasons", "trailers", "cast", "crew", "networks", "studios", "recommendations", "similar")}

    enriched = _parallel(enrich, candidates)
    return [item for _, item in sorted(enriched, key=lambda pair: not pair[0])][:8]


def home(client: TmdbClient | None) -> dict:
    client = _need(client)
    rails: list[tuple[str, str, str | None, str | None, Callable[[], list[dict]]]] = [
        ("anime_this_season", "This season in anime", "anime", "/requests/anime", lambda: _anime_section(client, "this_season", None, 1)["items"]),
        ("trending", "Trending this week", None, None, lambda: _tmdb_items(client, cached("tmdb:trending:all", TTL_LIST, lambda: client.trending("all")).get("results"), None)),
        ("upcoming_movies", "Upcoming movies", "movie", "/requests/movies?section=upcoming", lambda: list_items(client, "movie", "upcoming", None, 1)["items"]),
        ("popular_shows", "Popular shows", "show", "/requests/shows?section=popular", lambda: list_items(client, "show", "popular", None, 1)["items"]),
        ("popular_movies", "Popular movies", "movie", "/requests/movies?section=popular", lambda: list_items(client, "movie", "popular", None, 1)["items"]),
        ("anime_airing_today", "Airing today", "anime", "/requests/anime", lambda: _airing_today(client)),
        ("anime_next_season", "Coming next season", "anime", "/requests/anime?section=next_season", lambda: _anime_section(client, "next_season", None, 1)["items"]),
        ("on_the_air", "On the air", "show", "/requests/shows?section=on_the_air", lambda: list_items(client, "show", "on_the_air", None, 1)["items"]),
        ("top_rated_movies", "Top rated movies", "movie", "/requests/movies?section=top_rated", lambda: list_items(client, "movie", "top_rated", None, 1)["items"]),
    ]

    def build(rail: tuple) -> dict | None:
        key, title, kind, see_all, fetch = rail
        try:
            items = fetch()[:RAIL_SIZE]  # the upstream pages are cached; anime mappings resolve progressively, so rails are not
        except (tmdb.TmdbError, anilist.AniListError):
            return None  # one failing source drops its rail, not the page
        return {k: v for k, v in {"key": key, "title": title, "kind": kind, "see_all": see_all, "items": items}.items() if v is not None}

    built = [r for r in _parallel(build, rails) if r]
    trending = next((r["items"] for r in built if r["key"] == "trending"), [])
    try:
        hero = cached(f"home:hero:{_today()}", TTL_LIST, lambda: _hero(client, trending))
    except tmdb.TmdbError:
        hero = []
    return {"hero": hero, "rails": built}


# ---- Status annotation ------------------------------------------------------------

def _walk(value: Any) -> Iterable[dict]:
    """Every CatalogItem (dict with a ``key`` and ``kind``) nested anywhere in a response."""
    if isinstance(value, dict):
        if "key" in value and "kind" in value and "media_type" in value:
            yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def annotate(response: dict, session: Session, user: User) -> dict:
    """Set ``status`` on every CatalogItem in ``response`` with one ``catalog_statuses`` call."""
    items = list(_walk(response))
    statuses = catalog_statuses(session, user, sorted({i["key"] for i in items})) if items else {}
    for item in items:
        item["status"] = statuses.get(item["key"], NONE_STATUS)
    return response


# ---- Warm-up ----------------------------------------------------------------------

WARM_TICK_SECONDS = 12 * 60
WARM_SEASON_TICKS = 4  # ~48 min, inside the 1 h season TTL
WARM_LIST_TICKS = 25  # ~5 h, inside the 6 h list TTL
_warm_failed: set[str] = set()  # steps to retry on the next tick (AniList's rate limit at startup)


def warm_once(tick: int) -> None:
    """Refresh the first-load pages: schedule every tick, anime seasons every ~48 min, home and lists every ~5 h."""
    global _refreshing
    from app.db import session_scope
    from app.services.yt_dlp_service import YtDlpService

    with session_scope() as db:
        record = YtDlpService(db).get_app_settings()
        enabled, client = bool(record.requests_enabled), tmdb.client_for(record)
    if not enabled or client is None:
        return
    season, year = anilist.current_season()
    steps: dict[str, Callable[[], Any]] = {"schedule": lambda: anime_schedule(client, MAP_BUDGET)}
    if tick % WARM_SEASON_TICKS == 0 or _warm_failed & {"season", "next_season"}:
        steps["season"] = lambda: anime_season(client, season, year, "popularity", 1)
        steps["next_season"] = lambda: anime_season(client, *anilist.next_season(season, year), "popularity", 1)
    if tick % WARM_LIST_TICKS == 0 or _warm_failed - {"schedule", "season", "next_season"}:
        steps["home"] = lambda: home(client)
        steps.update({f"list:{k}": (lambda k=k: list_items(client, k, None, None, 1)) for k in KINDS})
    _refreshing = True
    try:
        for name, step in steps.items():
            try:
                step()
                _warm_failed.discard(name)
            except Exception as exc:  # noqa: BLE001 - a failed step is retried on the next tick, not its next cycle
                _warm_failed.add(name)
                logger.warning("Catalog warm-up step %s failed: %s", name, exc if isinstance(exc, anilist.AniListError) else type(exc).__name__)
    finally:
        _refreshing = False


async def warm_loop() -> None:
    import asyncio

    tick = 0
    while True:
        try:
            await asyncio.to_thread(warm_once, tick)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Catalog warm-up failed: %s", type(exc).__name__)
        tick = (tick + 1) % (WARM_SEASON_TICKS * WARM_LIST_TICKS)
        await asyncio.sleep(WARM_TICK_SECONDS)
