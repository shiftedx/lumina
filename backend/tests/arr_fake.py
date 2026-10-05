"""A loopback Sonarr or Radarr (API v3) for the request tests: status, roots, profiles, lookup, add, monitor, commands,
queue, custom formats. ``catalog`` answers lookups; ``library`` holds what the arr has added; knobs bend replies."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

API_KEY = "arr-secret-key-0123456789"


class FakeArr:
    def __init__(self, kind: str = "sonarr") -> None:
        self.kind = kind
        self.resource = "series" if kind == "sonarr" else "movie"
        self.catalog: dict[str, dict] = {}  # lookup term ("tvdb:1" / "tmdb:603") -> item
        self.library: dict[int, dict] = {}  # id -> added item
        self.queue: list[dict] = []
        self.commands: list[dict] = []
        self.requests: list[tuple[str, str, dict | None]] = []
        self.custom_formats: list[dict] = []
        self.profiles: list[dict] = [
            {"id": 1, "name": "Any", "cutoff": 1, "items": [{"quality": {"id": 1}, "allowed": True}], "minFormatScore": 0,
             "cutoffFormatScore": 0, "formatItems": []},
            {"id": 4, "name": "HD-1080p", "cutoff": 7, "items": [{"quality": {"id": 7}, "allowed": True}], "minFormatScore": 0,
             "cutoffFormatScore": 0, "formatItems": []},
        ]
        self.down = False  # every request answers 503
        self.redirect: str | None = None
        self._next_id = 100
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _reply(self, status: int, body: object = None, headers: dict[str, str] | None = None) -> None:
                raw = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                if raw:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _serve(self) -> None:
                parsed = urlsplit(self.path)
                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                fake.requests.append((self.command, parsed.path, body))
                if fake.redirect:
                    return self._reply(302, headers={"Location": fake.redirect})
                if fake.down:
                    return self._reply(503)
                if self.headers.get("X-Api-Key") != API_KEY:
                    return self._reply(401)
                status, reply = fake.route(self.command, parsed.path.removeprefix("/api/v3/"), query, body)
                self._reply(status, reply)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def route(self, method: str, path: str, query: dict, body: dict | None) -> tuple[int, object]:  # noqa: C901, PLR0911
        res = self.resource
        if (method, path) == ("GET", "system/status"):
            return 200, {"version": "4.0.9.2244" if self.kind == "sonarr" else "5.8.3.8933"}
        if (method, path) == ("GET", "rootfolder"):
            roots = ["/tv/Shows/", "/tv/Anime/"] if self.kind == "sonarr" else ["/movies/"]
            return 200, [{"id": i, "path": p, "freeSpace": 10**12} for i, p in enumerate(roots, 1)]
        if (method, path) == ("GET", "qualityprofile"):
            return 200, self.profiles
        if (method, path) == ("POST", "qualityprofile"):
            profile = {**body, "id": self.new_id()}
            self.profiles.append(profile)
            return 201, profile
        if (method, path) == ("GET", "customformat"):
            return 200, self.custom_formats
        if (method, path) == ("POST", "customformat"):
            fmt = {**body, "id": self.new_id()}
            self.custom_formats.append(fmt)
            return 201, fmt
        if (method, path) == ("GET", f"{res}/lookup"):
            item = self.catalog.get(query.get("term", ""))
            if item is None:
                return 200, []
            added = next((i for i in self.library.values() if i["lookup_term"] == query["term"]), None)
            return 200, [added or {k: v for k, v in item.items()}]
        if (method, path) == ("POST", res):
            if any(i["lookup_term"] == body.get("lookup_term") for i in self.library.values()):
                return 400, [{"errorMessage": "already added"}]
            item = {**body, "id": self.new_id()}
            self.library[item["id"]] = item
            return 201, item
        if path.startswith(f"{res}/") and path.split("/")[1].isdigit():
            item_id = int(path.split("/")[1])
            if item_id not in self.library:
                return 404, None
            if method == "PUT":
                self.library[item_id] = {**body, "id": item_id}
                return 202, self.library[item_id]
            return 200, self.library[item_id]
        if (method, path) == ("POST", "command"):
            self.commands.append(body)
            return 201, {**body, "id": self.new_id(), "status": "queued"}
        if (method, path) == ("GET", "queue"):
            return 200, {"page": 1, "pageSize": 1000, "totalRecords": len(self.queue), "records": self.queue}
        return 404, None

    def add_lookup(self, term: str, title: str, **fields: object) -> None:
        """A lookup hit; a series gets seasons 0-2 unless ``seasons`` is given."""
        item = {"title": title, "lookup_term": term, **fields}
        if self.kind == "sonarr":
            item.setdefault("seasons", [{"seasonNumber": n, "monitored": False} for n in range(3)])
        self.catalog[term] = item

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class FakeTmdb:
    """Recorded TMDB replies by path for engine._describe; install with ``monkeypatch.setattr(tmdb, "client_for", ...)``."""

    MOVIES = {
        603: {"id": 603, "title": "The Matrix", "release_date": "1999-03-31", "poster_path": "/matrix.jpg"},
        604: {"id": 604, "title": "The Matrix Reloaded", "release_date": "2003-05-15"},
        129: {"id": 129, "title": "Spirited Away", "release_date": "2001-07-20", "genres": [{"id": 16}], "original_language": "ja"},
    }
    SHOWS = {
        1399: {"id": 1399, "name": "Game of Thrones", "first_air_date": "2011-04-17", "poster_path": "/got.jpg", "external_ids": {"tvdb_id": 121361}},
        37854: {"id": 37854, "name": "One Piece", "first_air_date": "1999-10-20", "poster_path": "/op.jpg", "external_ids": {"tvdb_id": 81797},
                "genres": [{"id": 16, "name": "Animation"}], "origin_country": ["JP"], "original_language": "ja"},
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, path: str, **params: object) -> dict:
        from app.services import tmdb

        self.calls.append(path)
        kind, raw = path.strip("/").split("/")
        found = (self.MOVIES if kind == "movie" else self.SHOWS).get(int(raw))
        if found is None:
            raise tmdb.TmdbNotFound("TMDB has no such entry")
        return found

    def find(self, kind: str, source: str, value: str) -> list[dict]:
        return [{"tmdb_id": s["id"]} for s in self.SHOWS.values() if str(s["external_ids"]["tvdb_id"]) == value]


def library_title(session, title_type: str, title_id: str = "title-1", **provider_ids: str):  # noqa: ANN001, ANN201
    """A visible (shared, available) Media title with these provider ids."""
    from app.models import LibraryItem, MediaTitle

    session.add(MediaTitle(id=title_id, type=title_type, key=f"{title_type}:{title_id}", name=title_id, provider_ids=provider_ids))
    session.add(LibraryItem(id=f"item-{title_id}", title=title_id, visibility="shared", status="available", title_id=title_id))
    session.commit()


def seed_anime(anilist_id: int, mapping: dict | None, fmt: str = "TV") -> None:
    """Put an AniList entry and its TMDB mapping in the catalog cache, as browsing the catalog would have."""
    from app.services.requests import catalog

    catalog.cached(f"anilist:detail:{anilist_id}", catalog.TTL_DETAIL, lambda: {"id": anilist_id, "format": fmt})
    catalog.cached(f"map:{anilist_id}", catalog.TTL_MAPPING, lambda: mapping)
