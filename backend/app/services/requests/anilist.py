"""AniList GraphQL client (no key) and the AniList -> catalog item mapping.

Same transport rules as tmdb.py: Lumina's public-only fetcher, every hop pinned to AniList's host, capped JSON
replies, 429 Retry-After honoured, and content-free errors.
"""
from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from app.config import APP_VERSION
from app.services import tmdb
from app.services.artwork import PublicArtworkFetcher
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError

HOST = "graphql.anilist.co"
URL = f"https://{HOST}/"
SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")

policy = PublicSourcePolicy()
transport: PublicArtworkFetcher = PublicArtworkFetcher(policy)
# yt-dlp's default headers impersonate a Chrome navigation; over Python's TLS, AniList's Cloudflare answers that with a
# 403 "Just a moment..." challenge page. An honest client User-Agent replaces them.
USER_AGENT = f"Lumina/{APP_VERSION} (+https://github.com/shiftedx/lumina)"
LIMITER = tmdb.RateLimiter(2.1)  # AniList allows 90 requests a minute, 30 while it runs degraded (2026); stay under 30


class AniListError(RuntimeError):
    """Content-free AniList failure."""


def current_season(today: date | None = None) -> tuple[str, int]:
    today = today or date.today()
    return SEASONS[(today.month - 1) // 3], today.year


def next_season(season: str, year: int) -> tuple[str, int]:
    index = SEASONS.index(season) + 1
    return (SEASONS[0], year + 1) if index == 4 else (SEASONS[index], year)


def _validate(url: str) -> str:
    try:
        parts = urlsplit(url.strip() if isinstance(url, str) else "")
        port = parts.port
    except ValueError as exc:
        raise PublicSourcePolicyError() from exc
    if parts.scheme != "https" or parts.hostname != HOST or port not in (None, 443) or parts.username or parts.password:
        raise PublicSourcePolicyError()
    return policy.validate_url(url)


def query(gql: str, variables: dict[str, Any] | None = None, *, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """POST one GraphQL operation; returns its ``data`` object."""
    body = json.dumps({"query": gql, "variables": {k: v for k, v in (variables or {}).items() if v is not None}}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT, "Sec-Fetch-Mode": "cors"}
    for attempt in (1, 2):
        LIMITER.wait()
        try:
            response = transport.fetch(URL, validate_redirect=_validate, headers=headers, data=body)
        except Exception:  # noqa: BLE001 - transport errors stay content-free
            raise AniListError("AniList is unreachable") from None
        try:
            status = int(response.status_code)
            if status == 429 and attempt == 1:
                sleep(tmdb._retry_after(response.headers))
                continue
            payload = bytearray()
            for chunk in response.body:
                payload += chunk
                if len(payload) > tmdb.MAX_RESPONSE_BYTES:
                    raise AniListError("AniList response exceeded the size limit")
        except AniListError:
            raise
        except Exception:  # noqa: BLE001
            raise AniListError("AniList is unreachable") from None
        finally:
            response.close()
        break
    if status == 429:
        raise AniListError("AniList rate limit exceeded")
    if not 200 <= status < 300:
        raise AniListError(f"AniList returned HTTP {status}")
    try:
        data = json.loads(payload)
    except (ValueError, RecursionError):
        raise AniListError("AniList returned malformed JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise AniListError("AniList returned no data")
    return data["data"]


MEDIA = """id title{romaji english native} format status episodes duration season seasonYear startDate{year month day}
 description(asHtml:false) coverImage{extraLarge color} bannerImage averageScore popularity genres
 studios{nodes{name isAnimationStudio}} nextAiringEpisode{episode airingAt} trailer{id site} isAdult"""

BROWSE = f"""query Browse($page:Int,$perPage:Int,$season:MediaSeason,$seasonYear:Int,$sort:[MediaSort],$format:MediaFormat,
 $genre:String){{ Page(page:$page,perPage:$perPage){{ pageInfo{{lastPage}}
 media(type:ANIME,isAdult:false,season:$season,seasonYear:$seasonYear,sort:$sort,format:$format,genre:$genre){{ {MEDIA} }} }} }}"""

SCHEDULE = f"""query Schedule($page:Int,$from:Int,$to:Int){{ Page(page:$page,perPage:50){{ pageInfo{{hasNextPage}}
 airingSchedules(airingAt_greater:$from,airingAt_lesser:$to,sort:TIME){{ episode airingAt media{{ {MEDIA} }} }} }} }}"""

DETAIL = f"""query Detail($id:Int){{ Media(id:$id,type:ANIME){{ {MEDIA}
 relations{{ edges{{ relationType node{{ {MEDIA} type }} }} }}
 characters(sort:[ROLE,RELEVANCE],perPage:16){{ edges{{ node{{ name{{full}} image{{large}} }}
  voiceActors(language:JAPANESE){{name{{full}}}} voiceActorsEn:voiceActors(language:ENGLISH){{name{{full}}}} }} }}
 recommendations(sort:RATING_DESC,perPage:12){{ nodes{{ mediaRecommendation{{ {MEDIA} }} }} }} }} }}"""

GENRES = "query Genres { GenreCollection }"


def browse(*, page: int = 1, per_page: int = 24, **variables: Any) -> tuple[list[dict], int]:
    data = query(BROWSE, {"page": page, "perPage": per_page, **variables})["Page"]
    return [m for m in data["media"] if isinstance(m, dict)], int((data.get("pageInfo") or {}).get("lastPage") or 1)


def schedule(start: int, end: int, max_pages: int) -> list[dict]:
    """Airing entries between two unix times, adult titles dropped."""
    entries: list[dict] = []
    for page in range(1, max_pages + 1):
        data = query(SCHEDULE, {"page": page, "from": start, "to": end})["Page"]
        entries += [e for e in data["airingSchedules"] if isinstance(e.get("media"), dict) and not e["media"].get("isAdult")]
        if not (data.get("pageInfo") or {}).get("hasNextPage"):
            break
    return entries


def genre_names() -> list[str]:
    return [g for g in query(GENRES)["GenreCollection"] if g != "Hentai"]


def search_titles(titles: list[tuple[str, bool]]) -> list[dict | None]:
    """One aliased request: the best AniList match for each ``(title, is_movie)`` (None when AniList has none)."""
    if not titles:
        return []
    parts = [
        f"m{i}: Media(search:$s{i},type:ANIME,isAdult:false,{'format:MOVIE' if movie else 'format_not:MOVIE'}){{ {MEDIA} }}"
        for i, (_, movie) in enumerate(titles)
    ]
    declarations = ",".join(f"$s{i}:String" for i in range(len(titles)))
    data = query(f"query Match({declarations}){{ {' '.join(parts)} }}", {f"s{i}": name for i, (name, _) in enumerate(titles)})
    return [data.get(f"m{i}") if isinstance(data.get(f"m{i}"), dict) else None for i in range(len(titles))]


# ---- AniList media -> CatalogItem -------------------------------------------------

_BREAKS = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_SOURCE = re.compile(r"\s*[(\[]\s*(?:source|written by)[^)\]]*[)\]]\s*", re.IGNORECASE)
_STATUS_TEXT = {"RELEASING": "Airing", "FINISHED": "Finished", "NOT_YET_RELEASED": "Not yet released", "CANCELLED": "Cancelled", "HIATUS": "On hiatus"}


def _iso(timestamp: object) -> str | None:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat() if type(timestamp) is int else None


def _release_date(start: object) -> str | None:
    start = start if isinstance(start, dict) else {}
    try:
        return date(start["year"], start["month"], start["day"]).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def trailer(media: dict) -> list[dict]:
    t = media.get("trailer") or {}
    if str(t.get("site") or "").lower() != "youtube" or not isinstance(t.get("id"), str):
        return []
    return [{"youtube_id": t["id"], "name": "Trailer", "type": "Trailer", "official": True}]


def item(media: dict) -> dict:
    """A CatalogItem (without status, tmdb/tvdb ids or requestable: catalog.py adds those)."""
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or titles.get("native") or ""
    original = titles.get("romaji") if titles.get("romaji") != title else titles.get("native")
    release = _release_date(media.get("startDate"))
    year = media.get("seasonYear") or (media.get("startDate") or {}).get("year")
    cover = media.get("coverImage") or {}
    studios = [n["name"] for n in (media.get("studios") or {}).get("nodes") or [] if n.get("isAnimationStudio")]
    airing = media.get("nextAiringEpisode") or {}
    anime = {
        "season": media.get("season"), "season_year": media.get("seasonYear"), "format": media.get("format"),
        "episodes": media.get("episodes"), "studios": studios, "score": media.get("averageScore"),
        "popularity": media.get("popularity"), "color": cover.get("color"),
    }
    if type(airing.get("episode")) is int and _iso(airing.get("airingAt")):
        anime["next_episode"] = {"number": airing["episode"], "airing_at": _iso(airing["airingAt"])}
    score = media.get("averageScore")
    fields = {
        "key": f"anime:{media['id']}", "kind": "anime", "anilist_id": media["id"],
        "media_type": "movie" if media.get("format") == "MOVIE" else "tv",
        "title": title, "original_title": original or None, "year": year if type(year) is int else None,
        "overview": clean_text(media.get("description")), "release_date": release,
        "poster_url": cover.get("extraLarge"), "backdrop_url": media.get("bannerImage"),
        "rating": round(score / 10, 1) if type(score) is int else None, "genres": media.get("genres") or [],
        "anime": {k: v for k, v in anime.items() if v is not None},
    }
    return {k: v for k, v in fields.items() if v is not None}


def status_text(media: dict) -> str | None:
    return _STATUS_TEXT.get(media.get("status"))


def cast(media: dict) -> list[dict]:
    """Characters with their voice actors as cast: name is "Name (JP) · Name (EN)", profile_url the character image."""
    out = []
    for edge in (media.get("characters") or {}).get("edges") or []:
        node = edge.get("node") or {}
        character = (node.get("name") or {}).get("full")
        voices = [f"{a['name']['full']} ({tag})" for tag, key in (("JP", "voiceActors"), ("EN", "voiceActorsEn"))
                  for a in (edge.get(key) or [])[:1] if (a.get("name") or {}).get("full")]
        if name := " · ".join(voices) or character:
            entry = {"name": name, "character": character, "profile_url": (node.get("image") or {}).get("large")}
            out.append({k: v for k, v in entry.items() if v})
    return out


def clean_text(html_text: object) -> str | None:
    """AniList descriptions as plain text: line breaks kept, tags, entities and "(Source: ...)" notes removed."""
    text = html.unescape(_TAGS.sub("", _BREAKS.sub("\n", html_text if isinstance(html_text, str) else "")))
    text = re.sub(r"\n{3,}", "\n\n", _SOURCE.sub(" ", text))
    return "\n".join(line.strip() for line in text.strip().split("\n")) or None
