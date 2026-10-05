"""Sonarr/Radarr v3 clients (ADR 0018): lookup, add, monitor + search, queue, profiles, roots, custom formats.

Admin-configured LAN URLs go through ``local_ai.bounded_json_request`` like every admin endpoint: no redirects, no
proxy env vars, bounded in time and bytes. The API key travels only in the X-Api-Key header; every failure is a
content-free ``ArrError`` that is safe to store on a request or show an admin.
"""
from __future__ import annotations

from typing import Any

from app.models import ArrServer
from app.services.local_ai import LocalAiError, bounded_json_request

TIMEOUT_SECONDS = 15.0
MAX_BYTES = 16 * 1024 * 1024  # a big Sonarr's /series/{id} or queue page
OK = (200, 201, 202)
LABELS = {"sonarr": "Sonarr", "radarr": "Radarr"}

DUAL_AUDIO = "Lumina · Dual Audio"
ENGLISH_AUDIO = "Lumina · English Audio"
DUB_PROFILE = "Lumina · Anime English Dub"
SUB_PROFILE = "Lumina · Anime Japanese Subs"
DUAL_AUDIO_REGEX = r"\b(dual[ ._-]?audio|multi[ ._-]?audio|eng(lish)?[ ._-]?dub(bed)?)\b"
ENGLISH_LANGUAGE_ID = 1  # Sonarr's Language.English


class ArrError(RuntimeError):
    """A content-free failure: never the API key, a URL query or a response body. ``status``: the arr's HTTP status."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ArrClient:
    def __init__(self, kind: str, base_url: str, api_key: str | None) -> None:
        self.kind, self.base_url, self.api_key = kind, base_url, api_key or ""

    @classmethod
    def of(cls, server: ArrServer) -> ArrClient:
        return cls(server.kind, server.base_url, server.api_key)

    def call(self, method: str, path: str, *, array: bool = False, **send: Any) -> Any:
        try:
            return bounded_json_request(
                self.base_url, method, f"api/v3/{path}", timeout=TIMEOUT_SECONDS, label=LABELS.get(self.kind, "Server"),
                max_bytes=MAX_BYTES, array=array, ok=OK, headers={"X-Api-Key": self.api_key}, **send,
            )
        except LocalAiError as exc:
            raise ArrError(str(exc), status=exc.status) from None  # no chained httpx error: it could carry request details

    def test(self) -> dict[str, Any]:
        status = self.call("GET", "system/status")
        roots = self.call("GET", "rootfolder", array=True)
        profiles = self.call("GET", "qualityprofile", array=True)
        return {
            "version": str(status.get("version") or ""),
            "root_folders": [{"path": r.get("path"), "free_space": r.get("freeSpace")} for r in roots if isinstance(r, dict)],
            "quality_profiles": [{"id": p.get("id"), "name": p.get("name")} for p in profiles if isinstance(p, dict)],
        }

    def lookup(self, term: str) -> dict[str, Any]:
        """The first lookup hit for ``tmdb:603`` / ``tvdb:81189``; an ``id`` > 0 means the arr already has it."""
        resource = "movie" if self.kind == "radarr" else "series"
        found = self.call("GET", f"{resource}/lookup", array=True, params={"term": term})
        if not found or not isinstance(found[0], dict):
            raise ArrError(f"{LABELS[self.kind]} could not find {term}")
        return found[0]

    def queue(self) -> list[dict[str, Any]]:
        # One page of 1000 queue records; page through when a household queue outgrows it.
        page = self.call("GET", "queue", params={"page": 1, "pageSize": 1000})
        return [r for r in page.get("records") or [] if isinstance(r, dict)]


def add_movie(client: ArrClient, server: ArrServer, tmdb_id: int) -> int:
    """Radarr: add and search, or monitor + search a movie it already has. Returns Radarr's movie id."""
    movie = client.lookup(f"tmdb:{tmdb_id}")
    if movie.get("id"):
        movie_id = int(movie["id"])
        current = client.call("GET", f"movie/{movie_id}")
        client.call("PUT", f"movie/{movie_id}", json={**current, "monitored": True})
        client.call("POST", "command", json={"name": "MoviesSearch", "movieIds": [movie_id]})
        return movie_id
    _require(server.root_folder and server.quality_profile_id, "Radarr has no root folder or quality profile chosen")
    added = client.call("POST", "movie", json={
        **movie, "rootFolderPath": server.root_folder, "qualityProfileId": server.quality_profile_id, "monitored": True,
        "minimumAvailability": "released", "addOptions": {"searchForMovie": True},
    })
    return int(added["id"])


def add_series(client: ArrClient, server: ArrServer, tvdb_id: int, seasons: list[int] | str, *, anime: bool, language: str | None) -> int:
    """Sonarr: add and search only the requested seasons, or monitor + search them on a series it already has."""
    series = client.lookup(f"tvdb:{tvdb_id}")
    wanted = lambda number: seasons == "all" and number > 0 or isinstance(seasons, list) and number in seasons  # noqa: E731
    language_profile = (server.dub_profile_id if language == "dub" else server.sub_profile_id if language == "sub" else None) if anime else None
    if series.get("id"):
        series_id = int(series["id"])
        current = client.call("GET", f"series/{series_id}")
        for season in current.get("seasons") or []:
            season["monitored"] = bool(season.get("monitored")) or wanted(season.get("seasonNumber", -1))
        if language_profile:  # the newest anime request's language wins for the household's one copy of the series
            current["qualityProfileId"] = language_profile
        client.call("PUT", f"series/{series_id}", json={**current, "monitored": True})
        if seasons == "all":
            client.call("POST", "command", json={"name": "SeriesSearch", "seriesId": series_id})
        else:
            for number in seasons:
                client.call("POST", "command", json={"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": number})
        return series_id
    if anime:
        root, profile = server.anime_root_folder, language_profile or server.anime_quality_profile_id
    else:
        root, profile = server.root_folder, server.quality_profile_id
    _require(root and profile, "Sonarr has no root folder or quality profile chosen for this kind")
    added = client.call("POST", "series", json={
        **series, "rootFolderPath": root, "qualityProfileId": profile, "seriesType": "anime" if anime else "standard",
        "monitored": True, "seasonFolder": True,
        "seasons": [{**s, "monitored": wanted(s.get("seasonNumber", -1))} for s in series.get("seasons") or []],
        "addOptions": {"searchForMissingEpisodes": True},
    })
    return int(added["id"])


def ensure_anime_language_profiles(client: ArrClient, server: ArrServer) -> tuple[int, int]:
    """Create (idempotently, by name) the two custom formats and the dub/sub quality profiles; returns (dub_id, sub_id)."""
    formats = {f.get("name"): f for f in client.call("GET", "customformat", array=True) if isinstance(f, dict)}
    wanted = {
        DUAL_AUDIO: [_spec("Dual audio title", "ReleaseTitleSpecification", DUAL_AUDIO_REGEX)],
        # "English-only": English audio on a release whose title is not dual audio, so a dual release is never penalised
        # in the Japanese profile. A dual release named without the words still reads as English-only.
        ENGLISH_AUDIO: [_spec("English", "LanguageSpecification", ENGLISH_LANGUAGE_ID),
                        _spec("Not dual audio", "ReleaseTitleSpecification", DUAL_AUDIO_REGEX, negate=True)],
    }
    for name, specifications in wanted.items():
        if name not in formats:
            formats[name] = client.call("POST", "customformat", json={
                "name": name, "includeCustomFormatWhenRenaming": False, "specifications": specifications})
    profiles = [p for p in client.call("GET", "qualityprofile", array=True) if isinstance(p, dict)]
    by_name = {p.get("name"): p for p in profiles}
    source = next((p for pid in (server.anime_quality_profile_id, server.quality_profile_id) for p in profiles if pid and p.get("id") == pid),
                  profiles[0] if profiles else None)
    _require(source, "Sonarr has no quality profile to copy")
    ids = []
    for name, scores, minimum in ((DUB_PROFILE, {DUAL_AUDIO: 1000, ENGLISH_AUDIO: 500}, 500),
                                  (SUB_PROFILE, {DUAL_AUDIO: 100, ENGLISH_AUDIO: -1000}, 0)):
        if name not in by_name:  # an existing one keeps the admin's later tweaks
            clone = {key: value for key, value in source.items() if key != "id"}
            # Sonarr requires every custom format on the server in formatItems.
            clone.update(name=name, minFormatScore=minimum, cutoffFormatScore=max(minimum, int(source.get("cutoffFormatScore") or 0)),
                         formatItems=[{"format": f["id"], "name": n, "score": scores.get(n, 0)} for n, f in formats.items()])
            by_name[name] = client.call("POST", "qualityprofile", json=clone)
        ids.append(int(by_name[name]["id"]))
    return ids[0], ids[1]


def _spec(name: str, implementation: str, value: Any, *, negate: bool = False) -> dict[str, Any]:
    return {"name": name, "implementation": implementation, "negate": negate, "required": True, "fields": [{"name": "value", "value": value}]}


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ArrError(message)
