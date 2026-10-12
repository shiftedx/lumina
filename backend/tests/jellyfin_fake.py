"""A loopback Jellyfin for the import tests: sign-in by name, users, paged /Items per user, series lookup, logout."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = "0123456789abcdef0123456789abcdef"
USER_ID = "5f0e0e5e0e0e4e0e8e0e0e0e0e0e0e01"
TICKS = 10_000_000
FILTERS = {
    "IsPlayed": lambda data: data["Played"],
    "IsResumable": lambda data: not data["Played"] and data["PlaybackPositionTicks"] > 0,
    "IsFavorite": lambda data: data["IsFavorite"],
}


def _user_data(played: bool, seconds: int, favorite: bool, last: str | None) -> dict:
    data = {"Played": played, "PlaybackPositionTicks": seconds * TICKS, "IsFavorite": favorite, "PlayCount": int(played)}
    return {**data, "LastPlayedDate": last} if last else data


def movie(item_id: str, name: str, *, path: str | None = None, year: int | None = None, played: bool = False,
          seconds: int = 0, favorite: bool = False, last: str | None = None, **providers: str) -> dict:
    return {"Id": item_id, "Type": "Movie", "Name": name, "Path": path, "ProductionYear": year, "ProviderIds": providers,
            "UserData": _user_data(played, seconds, favorite, last)}


def episode(item_id: str, series_id: str, series_name: str, season: int, number: int, *, path: str | None = None,
            played: bool = False, seconds: int = 0, favorite: bool = False, last: str | None = None) -> dict:
    return {"Id": item_id, "Type": "Episode", "Name": f"Episode {number}", "Path": path, "SeriesId": series_id,
            "SeriesName": series_name, "ParentIndexNumber": season, "IndexNumber": number, "ProviderIds": {},
            "UserData": _user_data(played, seconds, favorite, last)}


def series(item_id: str, name: str, *, favorite: bool = False, **providers: str) -> dict:
    return {"Id": item_id, "Type": "Series", "Name": name, "ProviderIds": providers, "UserData": _user_data(False, 0, favorite, None)}


class FakeJellyfin:
    """``items`` answer the filtered /Items passes; ``series`` answers the ``Ids=`` lookup. Knobs bend replies."""

    def __init__(self, username: str = "alice", password: str = "jf-secret-pw", admin: bool = False) -> None:
        self.username, self.password, self.admin = username, password, admin
        self.items: list[dict] = []  # the signed-in user's
        self.others: dict[str, dict] = {}  # other users by id: {"Name", "Items", optional "Disabled"}; only an admin reads them
        self.series: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.redirect: str | None = None  # every request answers 302 to here
        self.items_status: int | None = None  # /Items answers this status with an empty body
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _reply(self, status: int, body: dict | list | None = None, headers: dict[str, str] | None = None) -> None:
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
                fake.requests.append((self.command, parsed.path, query))
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                route = (self.command, parsed.path)
                if fake.redirect:
                    return self._reply(302, headers={"Location": fake.redirect})
                if route == ("POST", "/Users/AuthenticateByName"):
                    if body != {"Username": fake.username, "Pw": fake.password}:
                        return self._reply(401)
                    policy = {"IsAdministrator": fake.admin, "IsDisabled": False}
                    return self._reply(200, {"User": {"Id": USER_ID, "Name": fake.username, "Policy": policy}, "AccessToken": TOKEN, "ServerId": "fake"})
                if f'Token="{TOKEN}"' not in (self.headers.get("Authorization") or ""):
                    return self._reply(401)
                if route == ("POST", "/Sessions/Logout"):
                    return self._reply(204)
                if route == ("GET", "/Users"):
                    if not fake.admin:
                        return self._reply(403)
                    return self._reply(200, [{"Id": USER_ID, "Name": fake.username, "Policy": {"IsAdministrator": True, "IsDisabled": False}}] + [
                        {"Id": user_id, "Name": user["Name"], "Policy": {"IsAdministrator": False, "IsDisabled": user.get("Disabled", False)}}
                        for user_id, user in fake.others.items()
                    ])
                owner = query.get("userId")
                if route == ("GET", "/Items") and (owner == USER_ID or (fake.admin and owner in fake.others)):
                    items = fake.items if owner == USER_ID else fake.others[owner]["Items"]
                    if fake.items_status:
                        return self._reply(fake.items_status)
                    if "Ids" in query:
                        found = [fake.series[i] for i in query["Ids"].split(",") if i in fake.series]
                        return self._reply(200, {"Items": found, "TotalRecordCount": len(found)})
                    types, keep = set(query.get("IncludeItemTypes", "").split(",")), FILTERS[query.get("Filters", "")]
                    matching = [item for item in items if item.get("Type") in types and keep(item["UserData"])]
                    start, limit = int(query.get("StartIndex", 0)), int(query.get("Limit", 100))
                    return self._reply(200, {"Items": matching[start:start + limit], "TotalRecordCount": len(matching), "StartIndex": start})
                return self._reply(404)

            do_GET = do_POST = _serve

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def logouts(self) -> int:
        return sum(1 for method, path, _ in self.requests if (method, path) == ("POST", "/Sessions/Logout"))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
