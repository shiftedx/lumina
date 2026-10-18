"""Loopback Lidarr v1 fixture for approved MusicBrainz acquisitions."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

API_KEY = "lidarr-test-key"


class FakeLidarr:
    def __init__(self) -> None:
        self.catalog_artists: dict[str, dict] = {}
        self.catalog_albums: dict[str, dict] = {}
        self.artists: dict[int, dict] = {}
        self.albums: dict[int, dict] = {}
        self.commands: dict[int, dict] = {}
        self.queue: list[dict] = []
        self.history: list[dict] = []
        self.requests: list[tuple[str, str, dict | None]] = []
        self.roots = [{"id": 1, "path": "/music"}]
        self.quality = [{"id": 4, "name": "Lossless"}]
        self.metadata = [{"id": 8, "name": "Standard"}]
        self.next_id = 100
        self.down = False
        self.route_hook = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _reply(self, status: int, body: object = None) -> None:
                raw = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
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
                if fake.down:
                    return self._reply(503)
                if self.headers.get("X-Api-Key") != API_KEY:
                    return self._reply(401)
                status, reply = fake.route(self.command, parsed.path.removeprefix("/api/v1/"), query, body)
                self._reply(status, reply)

            do_GET = do_POST = do_PUT = _serve

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    @staticmethod
    def _page(rows: list[dict], query: dict) -> dict:
        page, size = int(query.get("page", 1)), int(query.get("pageSize", 10))
        return {"page": page, "pageSize": size, "totalRecords": len(rows), "records": rows[(page - 1) * size:page * size]}

    def route(self, method: str, path: str, query: dict, body: dict | None) -> tuple[int, object]:  # noqa: C901, PLR0911
        if self.route_hook:
            override = self.route_hook(method, path, query, body)
            if override is not None:
                return override
        if (method, path) == ("GET", "system/status"):
            return 200, {"version": "2.7.1"}
        if (method, path) == ("GET", "rootfolder"):
            return 200, self.roots
        if (method, path) == ("GET", "qualityprofile"):
            return 200, self.quality
        if (method, path) == ("GET", "metadataprofile"):
            return 200, self.metadata
        if (method, path) == ("GET", "artist/lookup"):
            return 200, [self.catalog_artists[query["term"].removeprefix("lidarr:")]] if query.get("term", "").removeprefix("lidarr:") in self.catalog_artists else []
        if (method, path) == ("GET", "album/lookup"):
            return 200, [self.catalog_albums[query["term"].removeprefix("lidarr:")]] if query.get("term", "").removeprefix("lidarr:") in self.catalog_albums else []
        if (method, path) == ("GET", "artist"):
            rows = list(self.artists.values())
            return 200, [r for r in rows if r.get("foreignArtistId") == query["mbId"]] if "mbId" in query else rows
        if (method, path) == ("GET", "album"):
            rows = list(self.albums.values())
            if "foreignAlbumId" in query:
                rows = [r for r in rows if r.get("foreignAlbumId") == query["foreignAlbumId"]]
            if "artistId" in query:
                rows = [r for r in rows if str(r.get("artist", {}).get("id")) == query["artistId"]]
            return 200, rows
        if (method, path) == ("POST", "artist") and body:
            created = {**body, "id": self._id()}
            self.artists[created["id"]] = created
            return 201, created
        if (method, path) == ("POST", "album") and body:
            created = {**body, "id": self._id()}
            self.albums[created["id"]] = created
            return 201, created
        if (method, path) == ("PUT", "album/monitor") and body:
            for album_id in body.get("albumIds", []):
                if album_id in self.albums:
                    self.albums[album_id]["monitored"] = bool(body.get("monitored"))
            return 200, [self.albums[item] for item in body.get("albumIds", []) if item in self.albums]
        if (method, path) == ("POST", "command") and body:
            command = {"id": self._id(), "status": "queued", **body}
            self.commands[command["id"]] = command
            return 201, command
        if method == "GET" and path.startswith("command/"):
            command = self.commands.get(int(path.rsplit("/", 1)[1]))
            return (200, command) if command else (404, None)
        if (method, path) == ("GET", "queue"):
            return 200, self._page(self.queue, query)
        if (method, path) == ("GET", "history"):
            return 200, self._page(self.history, query)
        return 404, None
