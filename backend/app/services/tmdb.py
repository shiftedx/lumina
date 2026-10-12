"""TMDB client and TMDB JSON -> Lumina title fields.

Requests use Lumina's public-only transport (DNS-pinned, public addresses only), with every
URL and redirect hop pinned to TMDB's two hosts. Replies are capped, accepted only as JSON
objects and never logged. Every failure is a content-free ``TmdbError`` with no URL, key or body.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

from app.config import settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSettings, MediaTitle, NfoPerson, Person
from app.services.artwork import (
    ArtworkNotFoundError,
    ArtworkResponse,
    ArtworkService,
    ArtworkUnavailableError,
    PublicArtworkFetcher,
    RemoteArtworkFetcher,
    _evict_oldest,
)
from app.services.media_titles import synthetic_id
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError

API_HOST = "api.themoviedb.org"
IMAGE_HOST = "image.tmdb.org"
API_BASE = f"https://{API_HOST}/3"
IMAGE_BASE = f"https://{IMAGE_HOST}/t/p"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUESTS_PER_SECOND = 20
MAX_RETRY_AFTER_SECONDS = 30
# TMDB's terms require this notice; the web UI shows it in About and Admin → Media server.
TMDB_ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."
_V3_KEY = re.compile(r"[0-9a-f]{32}")
_BEARER_PREFIX = re.compile(r"^bearer(\s+|$)", re.IGNORECASE)
# A host rejection covers both a non-TMDB redirect hop and a TMDB host that did not resolve publicly.
_HOST_REJECTED = "TMDB request went to a host outside TMDB, or the host could not be resolved"

IMAGE_SIZES = {"Primary": "w500", "Backdrop": "w1280", "Logo": "w500", "Thumb": "w500", "Banner": "w1280", "Person": "w185"}
CANDIDATE_POSTER_SIZE = "w185"
CANDIDATE_SIZES = {"Primary": "w185", "Backdrop": "w300", "Logo": "w185"}  # candidate-picker previews
CANDIDATE_CACHE_BYTES = 256 * 1024 * 1024  # the previews' own LRU bucket under the pinned root
PINNED_CACHE_BYTES = 4 * 1024**3  # the pinned root itself; only art no title or person uses is evicted (#162)
CAP_PINNED_EVERY_S = 3600
_ALLOWED_SIZES = frozenset({*IMAGE_SIZES.values(), "w300"})
# TMDB image paths are "/<token>.<ext>"; anything else never becomes a URL. SVG logos are skipped (not served as art).
_IMAGE_PATH = re.compile(r"/[A-Za-z0-9_-]{1,64}\.(?:jpg|jpeg|png|webp)")
_EXTERNAL_ID = re.compile(r"tt\d{1,10}|\d{1,10}")
_IMDB_ID = re.compile(r"tt\d{1,10}")
ENDED_STATUSES = frozenset({"Ended", "Canceled"})
WRITER_JOBS = frozenset({"Screenplay", "Writer", "Teleplay"})
MAX_ACTORS = 20
MAX_CREW = 10
MAX_EPISODE_PEOPLE = 10
SEARCH_CANDIDATES = 10
ACCEPT_SCORE = 0.92
ACCEPT_LEAD = 0.08

policy = PublicSourcePolicy()
transport: RemoteArtworkFetcher = PublicArtworkFetcher(policy)


class TmdbError(RuntimeError):
    """Content-free TMDB failure: never carries a URL, key or response body."""


class TmdbNotFound(TmdbError):
    pass


class TmdbRateLimited(TmdbError):
    pass


def effective_api_key(record: AppSettings) -> str | None:
    """The saved key, else LUMINA_TMDB_API_KEY (a removed key is stored as NULL); None = feature off.

    Same rule as ``has_tmdb_key = bool(record.tmdb_api_key or settings.tmdb_api_key)``.
    A v4 token pasted with its ``Bearer`` prefix is used bare.
    """
    value = (getattr(record, "tmdb_api_key", None) or settings.tmdb_api_key or "").strip()
    return _BEARER_PREFIX.sub("", value, count=1).strip() or None


def pinned_validator(source_policy: PublicSourcePolicy) -> Callable[[str], str]:
    """Allow only https to TMDB's API and image hosts, then apply the public-address policy."""

    def validate(url: str) -> str:
        try:
            parts = urlsplit(url.strip() if isinstance(url, str) else "")
            port = parts.port
        except ValueError as exc:
            raise PublicSourcePolicyError() from exc
        if (
            parts.scheme != "https"
            or parts.hostname not in (API_HOST, IMAGE_HOST)
            or port not in (None, 443)
            or parts.username is not None
            or parts.password is not None
        ):
            raise PublicSourcePolicyError()
        return source_policy.validate_url(url)

    return validate


class RateLimiter:
    """One process-wide request spacing (TMDB allows ~50 req/s; Lumina stays at 20)."""

    def __init__(
        self, per_second: float, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = 1.0 / per_second
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            slot = max(now, self._next)
            self._next = slot + self._interval
        if slot > now:
            self._sleep(slot - now)


LIMITER = RateLimiter(REQUESTS_PER_SECOND)


def _retry_after(headers: Mapping[str, str]) -> float:
    value = next((str(v) for k, v in (headers or {}).items() if str(k).lower() == "retry-after"), "").strip()
    return float(min(int(value), MAX_RETRY_AFTER_SECONDS)) if value.isdigit() else 1.0


class TmdbClient:
    """TMDB v3 API. ``get`` returns one JSON object or raises a content-free ``TmdbError``."""

    def __init__(self, api_key: str, language: str = "en-US", *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._api_key = api_key
        self.language = language
        lang2, _, region = language.partition("-")
        self.lang2, self.region = lang2, region or "US"
        self._sleep = sleep
        self._fetcher = transport
        self._validate = pinned_validator(policy)

    def __repr__(self) -> str:
        return f"TmdbClient(language={self.language!r})"

    @property
    def image_languages(self) -> str:
        return ",".join(dict.fromkeys((self.lang2, "en", "null")))

    def get(self, path: str, **params: object) -> dict[str, Any]:
        query: dict[str, object] = {"language": self.language, **{k: v for k, v in params.items() if v is not None}}
        headers = {"Accept": "application/json"}
        if _V3_KEY.fullmatch(self._api_key):
            query["api_key"] = self._api_key
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = f"{API_BASE}{path}?{urlencode(query)}"
        for attempt in (1, 2):
            status, retry_after, body = self._once(url, headers)
            if status != 429:
                break
            if attempt == 2:
                raise TmdbRateLimited("TMDB rate limit exceeded")
            self._sleep(retry_after)
        if status == 404:
            raise TmdbNotFound("TMDB has no such entry")
        if status in (401, 403):
            raise TmdbError("TMDB rejected the API key")
        if not 200 <= status < 300 or body is None:
            raise TmdbError(f"TMDB returned HTTP {status}")
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            raise TmdbError("TMDB returned malformed JSON") from None
        if not isinstance(data, dict):
            raise TmdbError("TMDB returned an unexpected response")
        return data

    def _once(self, url: str, headers: dict[str, str]) -> tuple[int, float, bytes | None]:
        """One request: (status, retry-after seconds, body for 2xx else None)."""
        LIMITER.wait()
        try:
            response = self._fetcher.fetch(url, validate_redirect=self._validate, headers=headers)
        except PublicSourcePolicyError:
            raise TmdbError(_HOST_REJECTED) from None
        except Exception:  # noqa: BLE001 - transport errors can carry the URL (and so a v3 key)
            raise TmdbError("TMDB is unreachable") from None
        try:
            if response.final_url is not None:
                self._validate(response.final_url)
            status = int(response.status_code)
            if not 200 <= status < 300:
                return status, _retry_after(response.headers), None
            if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise TmdbError("TMDB returned a non-JSON response")
            body = bytearray()
            for chunk in response.body:
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise TmdbError("TMDB response exceeded the size limit")
            return status, 0.0, bytes(body)
        except TmdbError:
            raise
        except PublicSourcePolicyError:
            raise TmdbError(_HOST_REJECTED) from None
        except Exception:  # noqa: BLE001
            raise TmdbError("TMDB is unreachable") from None
        finally:
            response.close()

    def search(self, kind: Literal["movie", "tv"], name: str, year: int | None) -> list[dict]:
        year_param = "primary_release_year" if kind == "movie" else "first_air_date_year"
        data = self.get(f"/search/{kind}", query=name, include_adult="false", **{year_param: year})
        return [c for c in (candidate(raw, kind) for raw in _list(data.get("results"))[:SEARCH_CANDIDATES]) if c]

    def find(self, kind: Literal["movie", "tv"], source: str, value: str) -> list[dict]:
        """``/find`` by an Imdb (``imdb_id``) or Tvdb (``tvdb_id``) id; the value is validated before it enters the path."""
        if not _EXTERNAL_ID.fullmatch(value):
            return []
        data = self.get(f"/find/{value}", external_source=source)
        return [c for c in (candidate(raw, kind) for raw in _list(data.get(f"{kind}_results"))) if c]

    # ---- Requests catalog: thin typed wrappers over ``get`` (raw TMDB JSON; services/requests/catalog.py normalizes) ----

    def trending(self, kind: Literal["all", "movie", "tv"], page: int = 1) -> dict[str, Any]:
        return self.get(f"/trending/{kind}/week", page=page)

    def listing(self, kind: Literal["movie", "tv"], name: str, page: int = 1) -> dict[str, Any]:
        """A named list: movie popular|upcoming|now_playing|top_rated, tv popular|on_the_air|airing_today|top_rated."""
        return self.get(f"/{kind}/{name}", page=page)

    def discover(self, kind: Literal["movie", "tv"], page: int = 1, **params: object) -> dict[str, Any]:
        return self.get(f"/discover/{kind}", page=page, include_adult="false", **params)

    def search_multi(self, query: str, page: int = 1) -> dict[str, Any]:
        return self.get("/search/multi", query=query, page=page, include_adult="false")

    def genres(self, kind: Literal["movie", "tv"]) -> dict[str, Any]:
        return self.get(f"/genre/{kind}/list")

    def details(self, kind: Literal["movie", "tv"], tmdb_id: int) -> dict[str, Any]:
        ratings = "release_dates" if kind == "movie" else "content_ratings"
        return self.get(
            f"/{kind}/{tmdb_id}", append_to_response=f"videos,credits,recommendations,similar,external_ids,images,{ratings}",
            include_image_language=self.image_languages, include_video_language=f"{self.lang2},en",
        )

    def external_ids(self, kind: Literal["movie", "tv"], tmdb_id: int) -> dict[str, Any]:
        return self.get(f"/{kind}/{tmdb_id}/external_ids")


def client_for(record: AppSettings) -> TmdbClient | None:
    """A client for the effective key and library language, or None when TMDB is off."""
    key = effective_api_key(record)
    return TmdbClient(key, getattr(record, "metadata_language", None) or "en-US") if key else None


# ---- Lenient getters: a wrong type is None/[], never an exception ---------------

def _str(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _int(value: object) -> int | None:
    return value if type(value) is int else None


def _positive(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _float(value: object) -> float | None:
    return float(value) if type(value) in (int, float) else None


def _date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _year(value: object) -> int | None:
    parsed = _date(value)
    return int(parsed[:4]) if parsed else None


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _names(value: object) -> list[str]:
    return [name for entry in _list(value) if (name := _str(_dict(entry).get("name")))]


def _image_path(value: object) -> str | None:
    return value if isinstance(value, str) and _IMAGE_PATH.fullmatch(value) else None


def _rating(raw: dict) -> float | None:
    """vote_average to 1 dp, only when at least 10 people voted."""
    average, votes = _float(raw.get("vote_average")), _int(raw.get("vote_count"))
    return round(average, 1) if average is not None and average > 0 and votes is not None and votes >= 10 else None


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None and value != [] and value != ""}


@dataclass
class Parsed:
    """Lumina field values from one TMDB object; ``fields`` keys are ``apply_field`` field names."""

    fields: dict[str, Any]
    people: list[dict] = field(default_factory=list)  # Person rows: {id, tmdb_id, name, profile_path}
    provider_ids: dict[str, str] = field(default_factory=dict)
    status: str | None = None
    season_numbers: frozenset[int] = frozenset()
    collection: dict | None = None  # {"id": int, "name": str}
    episodes: dict[int, dict[str, Any]] = field(default_factory=dict)  # season only: episode number -> fields


def _images(raw: dict, lang2: str, **paths: object) -> dict[str, dict]:
    """``images.<Type>: {"tmdb": path}`` for each valid path; Logo is the best logo for the language."""
    order = {lang2: 0, "en": 1, None: 2}

    def rank_logo(logo: dict) -> int:
        lang = logo.get("iso_639_1")
        return order.get(lang, 3) if lang is None or isinstance(lang, str) else 3

    logos = [logo for logo in _list(_dict(raw.get("images")).get("logos")) if _image_path(_dict(logo).get("file_path"))]
    best = min(logos, key=rank_logo, default=None)
    paths["Logo"] = best["file_path"] if best else None
    return {f"images.{kind}": {"tmdb": path} for kind, value in paths.items() if (path := _image_path(value))}


def _people(entries: Iterable[tuple[object, str, object]], limit: int) -> tuple[list[dict], list[dict]]:
    """(Person rows, title refs) for ``(entry, PersonType, role)`` triples; bad entries are skipped."""
    rows: list[dict] = []
    refs: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for entry, kind, role in entries:
        entry = _dict(entry)
        tmdb_id, name = _positive(entry.get("id")), _str(entry.get("name"))
        if tmdb_id is None or name is None or (tmdb_id, kind) in seen:
            continue
        seen.add((tmdb_id, kind))
        person_id = synthetic_id(f"tmdb-person:{tmdb_id}")
        rows.append({"id": person_id, "tmdb_id": tmdb_id, "name": name, "profile_path": _image_path(entry.get("profile_path"))})
        refs.append({"person_id": person_id, "name": name, "role": _str(role), "type": kind})
        if len(refs) >= limit:
            break
    return rows, refs


def _crew(entries: object) -> Iterator[tuple[object, str, object]]:
    for entry in _list(entries):
        job = _str(_dict(entry).get("job"))
        if job == "Director":
            yield entry, "Director", None
        elif job in WRITER_JOBS:
            yield entry, "Writer", None


def _first_role(entry: object) -> object:
    roles = _list(_dict(entry).get("roles"))
    return _dict(roles[0]).get("character") if roles else None


def _provider_ids(raw: dict) -> dict[str, str]:
    external = _dict(raw.get("external_ids"))
    ids = {"Tmdb": str(tmdb_id)} if (tmdb_id := _positive(raw.get("id"))) else {}
    imdb = _str(external.get("imdb_id")) or _str(raw.get("imdb_id"))
    if imdb and _IMDB_ID.fullmatch(imdb):
        ids["Imdb"] = imdb
    if tvdb := _positive(external.get("tvdb_id")):
        ids["Tvdb"] = str(tvdb)
    return ids


def _movie_rating(release_dates: object, region: str) -> str | None:
    """The region's certification, theatrical release (type 3) first."""
    for country in map(_dict, _list(_dict(release_dates).get("results"))):
        if country.get("iso_3166_1") != region:
            continue
        dates = sorted((d for d in _list(country.get("release_dates")) if isinstance(d, dict)), key=lambda d: d.get("type") != 3)
        return next((cert for d in dates if (cert := _str(d.get("certification")))), None)
    return None


def _tv_rating(content_ratings: object, region: str) -> str | None:
    return next((_str(r.get("rating")) for r in map(_dict, _list(_dict(content_ratings).get("results"))) if r.get("iso_3166_1") == region), None)


def movie_fields(raw: dict, lang2: str, region: str) -> Parsed:
    credits = _dict(raw.get("credits"))
    actor_rows, actors = _people(((c, "Actor", _dict(c).get("character")) for c in _list(credits.get("cast"))), MAX_ACTORS)
    crew_rows, crew = _people(_crew(credits.get("crew")), MAX_CREW)
    collection = _dict(raw.get("belongs_to_collection"))
    collection_id, collection_name = _positive(collection.get("id")), _str(collection.get("name"))
    return Parsed(
        fields=_clean({
            "name": _str(raw.get("title")),
            "year": _year(raw.get("release_date")),
            "overview": _str(raw.get("overview")),
            "tagline": _str(raw.get("tagline")),
            "genres": _names(raw.get("genres")),
            "studios": _names(raw.get("production_companies")),
            "official_rating": _movie_rating(raw.get("release_dates"), region),
            "community_rating": _rating(raw),
            "premiered": _date(raw.get("release_date")),
            "runtime_minutes": _positive(raw.get("runtime")),
            "people": actors + crew,
            **_images(raw, lang2, Primary=raw.get("poster_path"), Backdrop=raw.get("backdrop_path")),
        }),
        people=actor_rows + crew_rows,
        provider_ids=_provider_ids(raw),
        collection={"id": collection_id, "name": collection_name} if collection_id and collection_name else None,
    )


def series_fields(raw: dict, lang2: str, region: str) -> Parsed:
    status = _str(raw.get("status"))
    credits = _dict(raw.get("aggregate_credits"))
    actor_rows, actors = _people(((c, "Actor", _first_role(c)) for c in _list(credits.get("cast"))), MAX_ACTORS)
    creator_rows, creators = _people(((c, "Creator", None) for c in _list(raw.get("created_by"))), MAX_CREW)
    runtimes = [minutes for minutes in _list(raw.get("episode_run_time")) if _positive(minutes)]
    return Parsed(
        fields=_clean({
            "name": _str(raw.get("name")),
            "year": _year(raw.get("first_air_date")),
            "overview": _str(raw.get("overview")),
            "tagline": _str(raw.get("tagline")),
            "genres": _names(raw.get("genres")),
            "studios": _names(raw.get("networks")),
            "official_rating": _tv_rating(raw.get("content_ratings"), region),
            "community_rating": _rating(raw),
            "premiered": _date(raw.get("first_air_date")),
            "end_date": _date(raw.get("last_air_date")) if status in ENDED_STATUSES else None,
            "status": status,
            "runtime_minutes": runtimes[0] if runtimes else None,
            "people": actors + creators,
            **_images(raw, lang2, Primary=raw.get("poster_path"), Backdrop=raw.get("backdrop_path")),
        }),
        people=actor_rows + creator_rows,
        provider_ids=_provider_ids(raw),
        status=status,
        season_numbers=frozenset(n for s in _list(raw.get("seasons")) if (n := _int(_dict(s).get("season_number"))) is not None),
    )


def season_fields(raw: dict, lang2: str, today: date) -> Parsed:
    """Season fields plus ``episodes`` by number; episode people are crew first, then guest stars (≤ 10)."""
    episodes: dict[int, dict[str, Any]] = {}
    rows: list[dict] = []
    aired = 0
    for entry in map(_dict, _list(raw.get("episodes"))):
        premiered = _date(entry.get("air_date"))
        if premiered and premiered <= today.isoformat():
            aired += 1
        number = _int(entry.get("episode_number"))
        if number is None:
            continue
        guests = ((g, "GuestStar", _dict(g).get("character")) for g in _list(entry.get("guest_stars")))
        people_rows, refs = _people([*_crew(entry.get("crew")), *guests], MAX_EPISODE_PEOPLE)
        rows += people_rows
        episodes[number] = _clean({
            "name": _str(entry.get("name")),
            "overview": _str(entry.get("overview")),
            "premiered": premiered,
            "runtime_minutes": _positive(entry.get("runtime")),
            "community_rating": _rating(entry),
            "people": refs,
            **_images({}, lang2, Primary=entry.get("still_path")),
        })
    return Parsed(
        fields=_clean({
            "name": _str(raw.get("name")),
            "overview": _str(raw.get("overview")),
            "premiered": _date(raw.get("air_date")),
            "aired_episode_count": aired,
            **_images({}, lang2, Primary=raw.get("poster_path")),
        }),
        people=rows,
        episodes=episodes,
    )


def collection_fields(raw: dict, lang2: str) -> Parsed:
    return Parsed(fields=_clean({
        "overview": _str(raw.get("overview")),
        **_images(raw, lang2, Primary=raw.get("poster_path"), Backdrop=raw.get("backdrop_path")),
    }))


def candidate(raw: object, kind: Literal["movie", "tv"]) -> dict | None:
    raw = _dict(raw)
    tmdb_id = _positive(raw.get("id"))
    name = _str(raw.get("title" if kind == "movie" else "name"))
    if tmdb_id is None or name is None:
        return None
    return {
        "tmdb_id": tmdb_id,
        "name": name,
        "original_name": _str(raw.get("original_title" if kind == "movie" else "original_name")),
        "year": _year(raw.get("release_date" if kind == "movie" else "first_air_date")),
        "overview": _str(raw.get("overview")),
        "poster_path": _image_path(raw.get("poster_path")),
    }


def normalize_name(value: str) -> str:
    """Casefolded, accent-free, punctuation-free; dashes become spaces ("Spider-Man" == "Spider Man")."""
    kept: list[str] = []
    for char in unicodedata.normalize("NFKD", value.casefold()):
        category = unicodedata.category(char)
        if category == "Pd":
            kept.append(" ")
        elif not category.startswith("P") and not unicodedata.combining(char):
            kept.append(char)
    return " ".join("".join(kept).split())


def score(name: str, year: int | None, candidate: dict) -> float:
    query = normalize_name(name)
    ratio = max(
        SequenceMatcher(None, query, normalize_name(option)).ratio()
        for option in (candidate.get("name"), candidate.get("original_name")) if option
    )
    other = candidate.get("year")
    if year is not None and other is not None:
        gap = abs(year - other)
        ratio += 0.1 if gap == 0 else 0.0 if gap == 1 else -0.3
    return round(ratio, 4)


def rank(name: str, year: int | None, candidates: list[dict]) -> list[tuple[float, dict]]:
    """Best first; ties keep TMDB's order."""
    return sorted(((score(name, year, c), c) for c in candidates), key=lambda pair: -pair[0])


def is_confident(ranked: list[tuple[float, dict]]) -> bool:
    return bool(ranked) and ranked[0][0] >= ACCEPT_SCORE and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= ACCEPT_LEAD)


def image_url(path: object, size: str) -> str | None:
    return f"{IMAGE_BASE}/{size}{path}" if _image_path(path) and size in _ALLOWED_SIZES else None


MAX_CANDIDATES = 60
_CANDIDATE_LISTS = {"Primary": "posters", "Backdrop": "backdrops", "Logo": "logos", "Still": "stills"}


def image_candidates(raw: dict, kind: str, lang2: str) -> list[dict]:
    """TMDB ``/images`` entries for one kind: valid paths only, best vote then language match then width, at most 60."""
    entries = raw.get(_CANDIDATE_LISTS.get(kind, ""))
    rows = []
    for item in entries if isinstance(entries, list) else []:
        path = _image_path(item.get("file_path")) if isinstance(item, dict) else None
        if path is None:
            continue
        language = item.get("iso_639_1") if isinstance(item.get("iso_639_1"), str) else None
        try:
            vote = float(item.get("vote_average"))
        except (TypeError, ValueError):
            vote = 0.0
        size = [v if isinstance(v, int) and not isinstance(v, bool) else None for v in (item.get("width"), item.get("height"))]
        rows.append({"tmdb_path": path, "width": size[0], "height": size[1], "language": language, "vote": vote})
    rank = {lang2: 0, "en": 1, None: 2}
    rows.sort(key=lambda r: (-r["vote"], rank.get(r["language"], 3), -(r["width"] or 0)))
    return rows[:MAX_CANDIDATES]


_last_cap = -float("inf")


def cap_pinned(db: Session, artwork: ArtworkService, max_bytes: int = PINNED_CACHE_BYTES, *, force: bool = False) -> None:
    """At most hourly: delete the oldest pinned TMDB art no title or person points at, until the root holds ``max_bytes``.

    Art a title or person uses is never evicted (renditions read it offline), so the cap is soft when that alone passes
    it. An evicted file is fetched again on its next request. Reads every title's images once an hour, like
    title_images.gc_uploads.
    """
    global _last_cap  # noqa: PLW0603
    root = artwork._pinned_root  # noqa: SLF001
    if (not force and time.monotonic() - _last_cap < CAP_PINNED_EVERY_S) or root is None or root.is_symlink() or not root.is_dir():
        return
    _last_cap = time.monotonic()
    urls = [image_url(path, IMAGE_SIZES["Person"]) for path in db.scalars(select(Person.profile_path))]
    urls += [image_url(path, IMAGE_SIZES["Person"]) for path in db.scalars(select(NfoPerson.tmdb_path))]
    for images in db.scalars(select(MediaTitle.images)):
        for key, entry in images.items() if isinstance(images, dict) else ():
            if isinstance(entry, dict) and (size := IMAGE_SIZES.get(str(key).partition(".")[0])):
                urls.append(image_url(entry.get("tmdb"), size))
    _evict_oldest(root, max_bytes, keep={hashlib.sha256(url.encode()).hexdigest() for url in urls if url})


def load_image(artwork: ArtworkService, path: str, kind: str, *, size: str | None = None, **pinned: Any) -> ArtworkResponse:
    """Bytes for a stored ``{"tmdb": path}`` (``kind`` = an ImageType or "Person"), fetched on first request, then disk.

    A DNS failure or a redirect off TMDB is ``ArtworkUnavailableError``, so image routes answer 502, not 500.
    """
    url = image_url(path, size or IMAGE_SIZES.get(kind, ""))
    if url is None:
        raise ArtworkNotFoundError("Artwork is unavailable.")
    try:
        return artwork.load_pinned(url, validate_redirect=pinned_validator(policy), **pinned)
    except PublicSourcePolicyError:
        raise ArtworkUnavailableError("Artwork is temporarily unavailable.") from None
