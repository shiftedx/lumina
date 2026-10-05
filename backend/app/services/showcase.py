"""The public sign-in showcase: ~12 public releases (upcoming, in cinemas, on the air, this season's anime) as slides.

Unauthenticated, so built ONLY from public catalog lists (TMDB, AniList): never the household library, requests,
members or request status. Images leave only as ``/api/public/showcase/art/{token}``, a token that is an HMAC over
the upstream URL; only URLs a build put in a slide are served, so the route cannot proxy anything else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import random
import threading
import time
from datetime import date

from app.models import AppSettings
from app.services import art_urls, tmdb
from app.services.requests import anilist, catalog

logger = logging.getLogger(__name__)
TTL, EMPTY_TTL = 6 * 3600, 15 * 60  # an empty or failed build retries after 15 min, so visitors never drive upstream calls
MIX = (("upcoming", "movie", 4), ("now_playing", "movie", 3), ("on_the_air", "show", 2), ("this_season", "anime", 3))
SKIP_GENRES = frozenset({"Ecchi", "Hentai", "Erotic"})
ART_PREFIX = "/api/public/showcase/art/"

_lock = threading.Lock()
_built: tuple[float, list[dict]] | None = None
_sources: dict[str, str] = {}  # token -> upstream URL: this build's and the previous build's (open pages keep working)
_previous: dict[str, str] = {}  # the last build's alone


def clear() -> None:
    global _built
    _built = None
    _sources.clear()
    _previous.clear()


def source(token: str) -> str | None:
    return _sources.get(token)


def _token(url: str) -> str:
    digest = hmac.new(art_urls.secret(), f"showcase:{url}".encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _caption(section: str, release: str | None, today: date) -> str:
    if section == "upcoming" and release and (day := date.fromisoformat(release)) > today:
        return f"In cinemas {day:%A}" if (day - today).days < 7 else f"In cinemas {day.day} {day:%B}"
    return {"on_the_air": "New episodes this week", "this_season": "New this season"}.get(section, "In cinemas now")


def _build(client: tmdb.TmdbClient, today: date) -> list[dict]:
    rng = random.Random(today.toordinal())  # the same slides all day, new ones tomorrow
    groups: list[list[dict]] = []
    seen: set[tuple[str, int]] = set()
    for section, kind, count in MIX:
        try:
            items = catalog.list_items(client, kind, section, None, 1)["items"]
        except (tmdb.TmdbError, anilist.AniListError, catalog.CatalogError):
            continue  # one failing list drops its slides, not the showcase
        items = [i for i in items if i.get("tmdb_id") and not SKIP_GENRES & set(i.get("genres") or ())]
        if section == "upcoming":
            items = [i for i in items if (i.get("release_date") or "") > today.isoformat()]
        picks = rng.sample(items, min(len(items), count + 2))  # spares for titles without a backdrop

        def enrich(item: dict, section: str = section) -> dict | None:
            kind = "movie" if item["media_type"] == "movie" else "show"
            try:
                full = catalog.detail(client, kind, item["tmdb_id"])
            except (tmdb.TmdbError, catalog.CatalogError):
                return None
            backdrop = full.get("backdrop_url")
            if not backdrop:
                return None
            logo = full.get("logo_url")
            logo = f"{tmdb.IMAGE_BASE}/w500/{logo.rsplit('/', 1)[1]}" if logo and logo.endswith(".png") else None
            return {
                "key": (kind, item["tmdb_id"]), "title": item["title"] if item["kind"] == "anime" else full["title"],
                "caption": _caption(section, item.get("release_date"), today), "kind": item["kind"],
                "backdrop": backdrop, "logo": logo,
            }

        group = []
        for slide in catalog._parallel(enrich, picks):
            if slide and slide["key"] not in seen and len(group) < count:
                seen.add(slide["key"])
                group.append(slide)
        groups.append(group)
    ordered = [group[i] for i in range(max(map(len, groups), default=0)) for group in groups if i < len(group)]  # interleave
    sources: dict[str, str] = {}
    slides = []
    for s in ordered:
        out = {"title": s["title"], "caption": s["caption"], "kind": s["kind"]}
        for field, url in (("backdrop_url", s["backdrop"]), ("logo_url", s["logo"])):
            if url:
                token = _token(url)
                sources[token] = url
                out[field] = ART_PREFIX + token
        slides.append(out)
    _sources.clear()
    _sources.update({**_previous, **sources})
    _previous.clear()
    _previous.update(sources)
    return slides


def slides(record: AppSettings) -> list[dict]:
    """Cached for 6 h; one build at a time, and visitors arriving mid-build get the last slides (or none) at once."""
    global _built
    now = time.monotonic()
    if _built is not None and _built[0] > now:
        return _built[1]
    if not _lock.acquire(blocking=False):
        return _built[1] if _built else []
    try:
        client = tmdb.client_for(record)
        built: list[dict] = []
        if record.requests_enabled and client is not None:
            try:
                built = _build(client, date.today())
            except Exception as exc:  # noqa: BLE001 - the sign-in page falls back to its calm design
                logger.warning("Sign-in showcase build failed: %s", type(exc).__name__)
        _built = (now + (TTL if built else EMPTY_TTL), built)
        return built
    finally:
        _lock.release()
