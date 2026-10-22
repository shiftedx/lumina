"""Plex history import: bounded PMS reads, public preview/import, and household provisioning."""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AccountToken, AppSettings, MemberFavorite, PlaybackProgress, User
from app.services import jellyfin_household, plex_history_client
from app.services.jellyfin_history_client import JellyfinImportError
from support import make_user, seed_app_settings
from title_support import ALICE, BOB, FILE, MOVIE, S1E1, SECRET_EPISODE, seed_tree

OWNER_TOKEN = "plex-owner-token"
ALICE_TOKEN = "plex-alice-token"
CAROL_TOKEN = "plex-carol-token"
OWNER_ID = "101"
ALICE_PLEX_ID = "102"
CAROL_PLEX_ID = "103"
MACHINE_ID = "fake-machine-id"
URL = "/api/me/plex-import"
HOUSEHOLD_URL = "/api/admin/plex-household"
HTTPX_CLIENT = httpx.Client


def movie(item_id: str, name: str, *, path: str | None = None, watched: bool = False, seconds: int = 0,
          last: int | None = None, year: int | None = None, **providers: str) -> dict:
    item = {
        "ratingKey": item_id, "type": "movie", "title": name, "viewCount": int(watched),
        "viewOffset": seconds * 1000, "Guid": [{"id": f"{key.lower()}://{value}"} for key, value in providers.items()],
    }
    if path:
        item["Media"] = [{"Part": [{"file": path}]}]
    if last is not None:
        item["lastViewedAt"] = last
    if year is not None:
        item["year"] = year
    return item


def show(item_id: str, name: str, **providers: str) -> dict:
    return {
        "ratingKey": item_id, "type": "show", "title": name,
        "Guid": [{"id": f"{key.lower()}://{value}"} for key, value in providers.items()],
    }


def episode(item_id: str, show_id: str, show_name: str, season: int, number: int, *, path: str | None = None,
            watched: bool = False, seconds: int = 0, last: int | None = None) -> dict:
    item = {
        "ratingKey": item_id, "type": "episode", "title": f"Episode {number}",
        "grandparentRatingKey": show_id, "grandparentTitle": show_name,
        "parentIndex": season, "index": number, "viewCount": int(watched), "viewOffset": seconds * 1000,
    }
    if path:
        item["Media"] = [{"Part": [{"file": path}]}]
    if last is not None:
        item["lastViewedAt"] = last
    return item


class FakePlex:
    """Loopback PMS JSON surface. Cloud account XML is supplied by ``install_cloud`` below."""

    def __init__(self) -> None:
        watched_at = 1_790_000_000
        self.items = {
            OWNER_TOKEN: {
                "movies": [movie("pm-owner", "The Movie", watched=True, last=watched_at, Tmdb="603")],
                "shows": [], "episodes": [],
            },
            ALICE_TOKEN: {
                "movies": [movie("pm-alice", "The Movie", watched=True, last=watched_at, Tmdb="603")],
                "shows": [show("ps-show", "Show", Tmdb="100", Tvdb="7")],
                "episodes": [episode(
                    "pe-pilot", "ps-show", "Show", 1, 1,
                    seconds=300, last=watched_at + 60,
                )],
            },
            CAROL_TOKEN: {
                "movies": [movie("pm-carol", "The Movie", watched=True, last=watched_at + 120, Tmdb="603")],
                "shows": [], "episodes": [],
            },
        }
        self.status: dict[str, int] = {}
        self.redirect: str | None = None
        self.page_chunk_limit: int | None = None
        self.repeat_pages = False
        self.requests: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _reply(self, status: int, body: dict | None = None, *, headers: dict[str, str] | None = None) -> None:
                raw = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                if raw:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                selected = {
                    key: self.headers[key] for key in (
                        "Accept", "X-Plex-Token", "X-Plex-Client-Identifier", "X-Plex-Container-Start", "X-Plex-Container-Size",
                    ) if key in self.headers
                }
                fake.requests.append(("GET", parsed.path, query, selected))
                if fake.redirect:
                    return self._reply(302, headers={"Location": fake.redirect})
                token = self.headers.get("X-Plex-Token") or ""
                if token not in fake.items:
                    return self._reply(401)
                if status := fake.status.get(token):
                    return self._reply(status)
                if parsed.path == "/":
                    return self._reply(200, {"MediaContainer": {
                        "machineIdentifier": MACHINE_ID, "myPlex": True, "friendlyName": "Fake Plex",
                    }})
                if parsed.path == "/myplex/account":
                    return self._reply(200, {"MediaContainer": {"username": "root@example.test"}})
                if parsed.path == "/library/sections":
                    return self._reply(200, {"MediaContainer": {"Directory": [
                        {"key": "1", "type": "movie"}, {"key": "2", "type": "show"}, {"key": "music", "type": "artist"},
                    ]}})
                if parsed.path in ("/library/sections/1/all", "/library/sections/2/all"):
                    kind = {"1": "movies", "2": "shows", "4": "episodes"}.get(query.get("type", ""), "")
                    rows = list(fake.items[token].get(kind, []))
                    if query.get("unwatched") == "0":
                        rows = [row for row in rows if int(row.get("viewCount") or 0) > 0]
                    if query.get("inProgress") == "1":
                        rows = [row for row in rows if int(row.get("viewOffset") or 0) > 0]
                    if identifiers := query.get("id"):
                        selected_ids = set(identifiers.split(","))
                        rows = [row for row in rows if row.get("ratingKey") in selected_ids]
                    start = int(self.headers.get("X-Plex-Container-Start") or 0)
                    size = int(self.headers.get("X-Plex-Container-Size") or 500)
                    if fake.page_chunk_limit is not None:
                        size = min(size, fake.page_chunk_limit)
                    page_start = 0 if fake.repeat_pages else start
                    total_size = 1_000 if fake.repeat_pages and rows else len(rows)
                    return self._reply(200, {"MediaContainer": {
                        "size": len(rows[page_start:page_start + size]), "offset": start, "totalSize": total_size,
                        "Metadata": rows[page_start:page_start + size],
                    }})
                return self._reply(404)

            def log_message(self, *_args) -> None:  # noqa: ANN002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def plex() -> FakePlex:
    fake = FakePlex()
    yield fake
    fake.close()


def install_cloud(
    monkeypatch, *, owned: bool = True, owner_name: str = "Root", missing_tokens: set[str] | None = None,
    overrides: dict[str, tuple[int, bytes, dict[str, str]]] | None = None,
) -> list[tuple[str, str]]:  # noqa: ANN001
    """Run the fixed Plex.tv requests through a real httpx MockTransport and retain their token/path."""
    calls: list[tuple[str, str]] = []
    def answer(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("X-Plex-Token", "")))
        assert request.url.host == "plex.tv" and request.headers["Accept"] == "application/xml"
        if overrides and request.url.path in overrides:
            status, content, headers = overrides[request.url.path]
            return httpx.Response(status, content=content, headers=headers)
        if request.url.path == "/api/v2/user":
            body = f'<user id="{OWNER_ID}" friendlyName="{owner_name}" username="root" email="root@example.test" />'
        elif request.url.path == "/api/v2/resources":
            body = f'<MediaContainer><Device clientIdentifier="{MACHINE_ID}" provides="server" owned="{int(owned)}" /></MediaContainer>'
        elif request.url.path == "/api/users/":
            body = (
                f'<MediaContainer><User id="{ALICE_PLEX_ID}" title="Alice Display" username="alice" email="alice@example.test">'
                f'<Server machineIdentifier="{MACHINE_ID}" /></User>'
                f'<User id="{CAROL_PLEX_ID}" title="Carol Display" username="carol" email="carol@example.test">'
                f'<Server machineIdentifier="{MACHINE_ID}" /></User></MediaContainer>'
            )
        elif request.url.path == f"/api/servers/{MACHINE_ID}/shared_servers":
            alice_token = "" if ALICE_PLEX_ID in (missing_tokens or set()) else f' accessToken="{ALICE_TOKEN}"'
            carol_token = "" if CAROL_PLEX_ID in (missing_tokens or set()) else f' accessToken="{CAROL_TOKEN}"'
            body = (
                f'<MediaContainer><SharedServer userID="{ALICE_PLEX_ID}"{alice_token} />'
                f'<SharedServer userID="{CAROL_PLEX_ID}"{carol_token} /></MediaContainer>'
            )
        else:
            return httpx.Response(404)
        return httpx.Response(200, content=body.encode(), headers={"Content-Type": "application/xml"})

    def client(**kwargs):  # noqa: ANN003, ANN202
        if str(kwargs.get("base_url", "")).startswith(plex_history_client.PLEX_TV):
            kwargs["transport"] = httpx.MockTransport(answer)
        return HTTPX_CLIENT(**kwargs)

    monkeypatch.setattr(plex_history_client.httpx, "Client", client)
    return calls


@pytest.fixture
def vault(db_factory, tmp_path, plex):  # noqa: ANN001, ANN201
    with db_factory() as db:
        db.add_all([
            make_user("owner-id", role="admin", username="dana"),
            make_user(ALICE, username="alice"), make_user(BOB, username="bob"),
        ])
        seed_tree(db, tmp_path.resolve() / "media")
        seed_app_settings(db, plex_import_url=plex.url)
    return db_factory


def as_user(api_client, factory, user_id: str = ALICE):  # noqa: ANN001, ANN201
    with factory() as db:
        user = db.get(User, user_id)
    return api_client(user=user, base_url="http://localhost")


def counts(factory) -> tuple[int, int, int, int]:  # noqa: ANN001
    with factory() as db:
        return (
            db.query(User).count(), db.query(PlaybackProgress).count(),
            db.query(MemberFavorite).count(), db.query(AccountToken).count(),
        )


def test_client_reads_paged_progress_paths_and_provider_ids(plex) -> None:  # noqa: ANN001
    # A PMS may return fewer items than requested while totalSize says more. Keep paging to the total.
    plex.page_chunk_limit = 1
    plex.items[ALICE_TOKEN]["movies"][0]["viewOffset"] = 120_000  # returned by both filtered passes
    plex.items[ALICE_TOKEN]["movies"].append(movie(
        "pm-legacy", "Legacy", watched=True, last=1_790_000_500,
        guid="com.plexapp.agents.imdb://tt0133093?lang=en",
    ))
    plex.items[ALICE_TOKEN]["movies"].extend(
        movie(f"unplayed-{index}", "Never watched") for index in range(1_000)
    )
    plex.items[ALICE_TOKEN]["shows"].append(show("ps-unused", "Unused", Tmdb="999"))
    # Legacy GUIDs are a top-level string on old agents rather than Guid[].
    plex.items[ALICE_TOKEN]["movies"][1]["Guid"] = []
    plex.items[ALICE_TOKEN]["movies"][1]["guid"] = "com.plexapp.agents.imdb://tt0133093?lang=en"

    history = plex_history_client.fetch_history(plex.url, ALICE_TOKEN)
    entries = {entry.id: entry for entry in history.entries}

    assert set(entries) == {"pm-alice", "pm-legacy", "pe-pilot"}
    assert (entries["pm-alice"].played, entries["pm-alice"].provider_ids) == (True, {"Tmdb": "603"})
    assert entries["pm-legacy"].provider_ids == {"Imdb": "tt0133093"}
    pilot = entries["pe-pilot"]
    assert (pilot.position_seconds, pilot.last_played, pilot.path, pilot.season, pilot.episode) == (
        300, datetime.fromtimestamp(1_790_000_060, UTC).replace(tzinfo=None),
        None, 1, 1,
    )
    assert history.series_provider_ids == {"ps-show": {"Tmdb": "100", "Tvdb": "7"}}
    movie_starts = [(query, headers["X-Plex-Container-Start"]) for _, path, query, headers in plex.requests
                    if path == "/library/sections/1/all" and query.get("type") == "1"]
    assert movie_starts == [
        ({"type": "1", "includeGuids": "1", "unwatched": "0"}, "0"),
        ({"type": "1", "includeGuids": "1", "unwatched": "0"}, "1"),
        ({"type": "1", "includeGuids": "1", "inProgress": "1"}, "0"),
    ]
    show_queries = [query for _, path, query, _ in plex.requests
                    if path == "/library/sections/2/all" and query.get("type") == "2"]
    assert show_queries == [{"type": "2", "includeGuids": "1", "id": "ps-show"}]
    assert {headers["X-Plex-Container-Size"] for _, path, _, headers in plex.requests
            if path.startswith("/library/sections/")} == {"500"}
    assert all(headers.get("Accept") == "application/json" and headers.get("X-Plex-Token") == ALICE_TOKEN
               for _, _, _, headers in plex.requests)


def test_client_rejects_redirects_and_caps_history_and_repeated_pages(plex, monkeypatch) -> None:  # noqa: ANN001
    plex.redirect = "http://127.0.0.1:9/elsewhere"
    with pytest.raises(JellyfinImportError, match="redirects"):
        plex_history_client.fetch_history(plex.url, ALICE_TOKEN)
    assert len(plex.requests) == 1

    plex.redirect = None
    plex.requests.clear()
    monkeypatch.setattr(plex_history_client, "MAX_ENTRIES", 1)
    with pytest.raises(JellyfinImportError, match="more than 1 entries"):
        plex_history_client.fetch_history(plex.url, ALICE_TOKEN)

    monkeypatch.setattr(plex_history_client, "MAX_ENTRIES", 20_000)
    plex.requests.clear()
    plex.repeat_pages = True
    monkeypatch.setattr(plex_history_client, "MAX_SCAN_PAGES", 2)
    with pytest.raises(JellyfinImportError, match="more pages"):
        plex_history_client.fetch_history(plex.url, ALICE_TOKEN)
    assert [headers["X-Plex-Container-Start"] for _, path, query, headers in plex.requests
            if path.endswith("/all") and query.get("type") == "1"] == ["0", "1"]


def test_cloud_xml_rejects_utf16_dtd_redirects_and_oversize_replies(vault, api_client, monkeypatch, caplog) -> None:  # noqa: ANN001
    """Plex account discovery crosses a fixed internet trust boundary; every reply remains bounded and entity-free."""
    admin = as_user(api_client, vault, "owner-id")
    caplog.set_level(logging.DEBUG)
    hostile = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE user [<!ENTITY secret SYSTEM "file:///etc/passwd">]><user id="101" username="&secret;" />'
    ).encode("utf-16")
    install_cloud(monkeypatch, overrides={
        "/api/v2/user": (200, hostile, {"Content-Type": "application/xml"}),
    })
    malformed = admin.post(f"{HOUSEHOLD_URL}/preview", json={"token": OWNER_TOKEN})
    assert (malformed.status_code, malformed.json()["detail"]) == (400, "Plex.tv sent an unexpected XML reply.")

    install_cloud(monkeypatch, overrides={
        "/api/v2/user": (302, b"", {"Location": "https://example.test/elsewhere"}),
    })
    redirected = admin.post(f"{HOUSEHOLD_URL}/preview", json={"token": OWNER_TOKEN})
    assert (redirected.status_code, redirected.json()["detail"]) == (400, "Plex.tv returned an unexpected redirect.")

    install_cloud(monkeypatch, overrides={
        "/api/users/": (200, b"x" * (plex_history_client.MAX_PAGE_BYTES + 1), {"Content-Type": "application/xml"}),
    })
    oversized = admin.post(f"{HOUSEHOLD_URL}/preview", json={"token": OWNER_TOKEN})
    assert (oversized.status_code, oversized.json()["detail"]) == (
        400, "Plex.tv sent a response larger than Lumina accepts.",
    )
    assert OWNER_TOKEN not in caplog.text and "file:///etc/passwd" not in caplog.text


def test_preview_is_read_only_import_applies_and_rerun_is_a_no_op(vault, api_client, plex) -> None:  # noqa: ANN001
    client = as_user(api_client, vault)
    assert client.get(URL).json() == {"server": plex.url}
    preview = client.post(f"{URL}/preview", json={"token": ALICE_TOKEN})
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "watched": 1, "in_progress": 1, "favorites": 0, "up_to_date": 0, "unmatched": 0, "unmatched_names": [],
    }
    assert counts(vault) == (3, 0, 0, 0)
    assert client.post(URL, json={"token": ALICE_TOKEN}).json() == preview.json()
    assert counts(vault) == (3, 2, 0, 0)
    assert client.post(URL, json={"token": ALICE_TOKEN}).json() == {
        **preview.json(), "watched": 0, "in_progress": 0, "up_to_date": 2,
    }
    assert counts(vault) == (3, 2, 0, 0)


def test_newer_lumina_progress_wins_and_hidden_media_is_not_disclosed(vault, api_client, plex) -> None:  # noqa: ANN001
    with vault() as db:
        db.add(PlaybackProgress(
            id="newer", user_id=ALICE, item_id=FILE[S1E1], position_seconds=900,
            last_watched_at=datetime(2027, 1, 1),
        ))
        db.commit()
    plex.items[ALICE_TOKEN] = {
        "movies": [], "shows": [],
        "episodes": [episode(
            "private", "private-show", "Their Show", 1, 1,
            path="/plex/tv/Secret Show/Season 01/Secret S01E01.mkv", watched=True, last=1_790_000_000,
        ), episode(
            "pilot", "ps-show", "Show", 1, 1,
            path="/plex/tv/Show/Season 01/Show S01E01.mkv", seconds=300, last=1_790_000_000,
        )],
    }
    response = as_user(api_client, vault).post(URL, json={"token": ALICE_TOKEN})
    assert response.json() == {
        "watched": 0, "in_progress": 0, "favorites": 0, "up_to_date": 1,
        "unmatched": 1, "unmatched_names": ["Their Show S01E01"],
    }
    assert FILE[SECRET_EPISODE] not in response.text and "Hidden" not in response.text
    with vault() as db:
        assert db.get(PlaybackProgress, "newer").position_seconds == 900


def test_fixed_server_token_secrecy_validation_and_rate_limit(vault, api_client, plex, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    client = as_user(api_client, vault)
    injected = client.post(f"{URL}/preview", json={"token": ALICE_TOKEN, "server": "http://169.254.169.254"})
    assert injected.status_code == 422 and plex.requests == []
    malformed = client.post(f"{URL}/preview", json={"token": "bad token"})
    assert malformed.status_code == 422 and "bad token" not in malformed.text

    with vault() as db:
        db.get(AppSettings, 1).plex_import_url = None
        db.commit()
    assert client.get(URL).json() == {"server": None}
    refused = client.post(f"{URL}/preview", json={"token": ALICE_TOKEN})
    assert (refused.status_code, refused.json()["detail"]) == (
        409, "Ask a vault owner to set the Plex server address under Media server first.",
    )
    with vault() as db:
        db.get(AppSettings, 1).plex_import_url = plex.url
        db.commit()
    codes = [client.post(f"{URL}/preview", json={"token": "wrong-token"}).status_code for _ in range(9)]
    # The unset-address attempt above consumed one of this member's ten attempts.
    assert codes == [400] * 9
    assert client.post(f"{URL}/preview", json={"token": "wrong-token"}).status_code == 429
    assert ALICE_TOKEN not in caplog.text and "wrong-token" not in caplog.text


def test_household_preview_imports_existing_and_provisions_new_member(vault, api_client, plex, monkeypatch) -> None:  # noqa: ANN001
    install_cloud(monkeypatch)
    owner = as_user(api_client, vault, "owner-id")
    preview = owner.post(f"{HOUSEHOLD_URL}/preview", json={"token": OWNER_TOKEN})
    assert preview.status_code == 200, preview.text
    rows = preview.json()["members"]
    assert [(row["plex_name"], row["action"], row["lumina_username"]) for row in rows] == [
        ("Root", "import", "dana"), ("alice", "import", "alice"), ("carol", "create", "carol"),
    ]
    assert [(row["summary"]["watched"], row["summary"]["in_progress"]) for row in rows] == [(1, 0), (1, 1), (1, 0)]
    assert counts(vault) == (3, 0, 0, 0)

    result = owner.post(HOUSEHOLD_URL, json={
        "token": OWNER_TOKEN, "plex_ids": [OWNER_ID, ALICE_PLEX_ID, CAROL_PLEX_ID],
    })
    assert result.status_code == 200, result.text
    links = [row["reset_url"] for row in result.json()["members"] if row["reset_url"]]
    assert len(links) == 1 and "/#reset=" in links[0]
    assert counts(vault) == (4, 4, 0, 1)
    with vault() as db:
        carol = db.query(User).filter_by(username="carol").one()
        assert (carol.role, carol.is_active, carol.password_hash, carol.display_name) == ("viewer", True, None, "carol")
    again = owner.post(HOUSEHOLD_URL, json={
        "token": OWNER_TOKEN, "plex_ids": [OWNER_ID, ALICE_PLEX_ID, CAROL_PLEX_ID],
    }).json()
    assert all(row["summary"] and not row["summary"]["watched"] and not row["summary"]["in_progress"] for row in again["members"])
    assert not any(row["reset_url"] for row in again["members"])
    assert counts(vault) == (4, 4, 0, 1)


def test_household_missing_user_token_reports_that_member_and_keeps_other_imports(vault, api_client, monkeypatch) -> None:  # noqa: ANN001
    install_cloud(monkeypatch, missing_tokens={CAROL_PLEX_ID})
    response = as_user(api_client, vault, "owner-id").post(HOUSEHOLD_URL, json={
        "token": OWNER_TOKEN, "plex_ids": [OWNER_ID, ALICE_PLEX_ID, CAROL_PLEX_ID],
    })
    assert response.status_code == 200, response.text
    rows = {row["plex_name"]: row for row in response.json()["members"]}
    assert rows["Root"]["summary"]["watched"] == 1
    assert (rows["alice"]["summary"]["watched"], rows["alice"]["summary"]["in_progress"]) == (1, 1)
    assert rows["carol"]["error"] == "Plex did not provide a server token for this user."
    assert rows["carol"]["reset_url"] is None
    assert counts(vault) == (3, 3, 0, 0)


def test_household_is_admin_only_requires_owner_token_and_isolates_member_failures(
    vault, api_client, plex, monkeypatch, caplog,
) -> None:  # noqa: ANN001
    assert as_user(api_client, vault, ALICE).post(
        f"{HOUSEHOLD_URL}/preview", json={"token": OWNER_TOKEN},
    ).status_code == 403

    install_cloud(monkeypatch, owned=False)
    admin = as_user(api_client, vault, "owner-id")
    refused = admin.post(f"{HOUSEHOLD_URL}/preview", json={"token": OWNER_TOKEN})
    assert (refused.status_code, refused.json()["detail"]) == (400, "That Plex token does not belong to this server's owner.")

    install_cloud(monkeypatch)
    plex.status[ALICE_TOKEN] = 500
    real_apply = jellyfin_household.apply

    def fail_carol(db, user, result):  # noqa: ANN001, ANN202
        if user.username == "carol":
            raise IntegrityError("INSERT", {}, Exception("refused"))
        return real_apply(db, user, result)

    monkeypatch.setattr(jellyfin_household, "apply", fail_carol)
    caplog.set_level(logging.DEBUG)
    response = admin.post(HOUSEHOLD_URL, json={
        "token": OWNER_TOKEN, "plex_ids": [OWNER_ID, ALICE_PLEX_ID, CAROL_PLEX_ID],
    })
    assert response.status_code == 200, response.text
    rows = {row["plex_name"]: row for row in response.json()["members"]}
    assert rows["Root"]["summary"]["watched"] == 1
    assert rows["alice"]["error"] == "The Plex server answered with HTTP 500."
    assert rows["carol"]["error"] == jellyfin_household.SAVE_FAILED
    with vault() as db:
        assert db.query(User).filter_by(username="carol").count() == 0
        assert db.query(AccountToken).count() == 0
        assert [row.user_id for row in db.query(PlaybackProgress)] == ["owner-id"]
    assert OWNER_TOKEN not in caplog.text and ALICE_TOKEN not in caplog.text and CAROL_TOKEN not in caplog.text
