"""Infrastructure API workflow contracts against bounded loopback provider fixtures."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from app.models import ArrServer, ServiceConnector
from support import make_user


@pytest.fixture
def provider():  # noqa: ANN201
    state = {"kind": "qbittorrent", "down": False, "partial": False, "requests": [], "cookies": [], "many": 1001}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: ANN002, ANN201
            pass

        def do_GET(self):  # noqa: N802, ANN201
            self.reply()

        def do_POST(self):  # noqa: N802, ANN201
            self.reply()

        def reply(self):  # noqa: ANN201
            from urllib.parse import parse_qs, urlparse
            url = urlparse(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.command, self.path, body))
            if self.command == "GET":
                state["cookies"].append(self.headers.get("Cookie"))
            value, raw = {}, False
            code = 503 if state["down"] else 200
            if url.path.endswith("auth/login"):
                code = state.get("login_status", code)
                value, raw = state.get("login_body", "Ok."), True
            elif url.path.endswith("app/version"):
                value, raw = state.get("version", "v5.0.0"), True
            elif url.path.endswith("app/webapiVersion"):
                value, raw = state.get("api_version", "2.11.0"), True
            elif url.path.endswith("sync/maindata"):
                value = {"full_update": not state["partial"], "torrents": {"a" * 40: {"state": "downloading", "progress": .5, "name": "PRIVATE", "save_path": "/secret"}}}
            elif url.path == "/jsonrpc":
                method = json.loads(body)["method"]
                value = {"id": 1, "result": {"version": "24.6", "status": {"DownloadRate": 12}, "listgroups": [{"NZBID": 7, "Status": "DOWNLOADING", "NZBName": "PRIVATE", "DestDir": "/secret"}], "history": [{"NZBID": 8, "Status": "SUCCESS/ALL", "NZBName": "PRIVATE"}]}[method]}
            elif url.path.endswith("system/status"):
                value = {"version": "1.0.0"}
            elif url.path.endswith("history"):
                page = int(parse_qs(url.query)["page"][0])
                values = [{"id": n, "indexerId": 2, "eventType": "grab", "data": {"apikey": "PRIVATE"}} for n in range(state["many"])]
                value = {"page": page, "totalRecords": len(values), "records": values[(page - 1)*1000:page*1000]}
            elif url.path.endswith("indexer"):
                value = [{"id": 2, "enable": True, "name": "PRIVATE", "fields": [{"apiKey": "PRIVATE"}]}]
            elif url.path.endswith("indexerstats"):
                value = {"totalGrabs": 5}
            elif url.path.endswith("queue"):
                value = {"page": 1, "totalRecords": 1, "records": [{"id": 1, "movieId": 101, "downloadClient": "Household", "downloadId": "A" * 40}]}
            else:
                value = []
            if self.command == "GET" and url.path.startswith("/api/v2/") and state.get("require_session"):
                if self.headers.get("Cookie") != state["login_cookie"]:
                    code = 403
                else:
                    code = state.get("read_status", {}).get(url.path, code)
            payload = value.encode() if raw else json.dumps(value).encode()
            self.send_response(code)
            if url.path.endswith("auth/login") and state.get("login_cookie", "SID=fixture"):
                self.send_header("Set-Cookie", state.get("login_cookie", "SID=fixture") + "; Path=/")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def setup(api_client, db_factory, provider, kind, **extra):  # noqa: ANN001, ANN201
    url, state = provider
    with db_factory() as db:
        db.add(make_user("admin", role="admin"))
        db.commit()
    client = api_client(user=make_user("admin", role="admin"), base_url="http://localhost")
    body = {"kind": kind, "name": kind, "base_url": url, "username": "fixture-user", "password": "fixture-secret", "api_key": "fixture-key", **extra}
    response = client.post("/api/admin/connectors", json=body)
    assert response.status_code == 200, response.text
    assert "fixture-secret" not in response.text and "fixture-key" not in response.text and "fixture-user" not in response.text
    return client, response.json()["id"], state


@pytest.mark.parametrize("modern_login", [False, True])
def test_qbittorrent_probe_correlates_exact_manager_identity_and_preserves_stale_snapshot(api_client, db_factory, provider, modern_login):  # noqa: ANN001, ANN201
    client, identity, state = setup(api_client, db_factory, provider, "qbittorrent", settings={"arr_clients": [{"server_id": "arr", "client_name": "Household"}]})
    if modern_login:
        state.update(login_status=204, login_body="", login_cookie="QBT_SID_8080=fixture", require_session=True)
    with db_factory() as db:
        db.add(ArrServer(id="arr", kind="radarr", name="Radarr", base_url=provider[0], api_key="key", enabled=True))
        db.commit()
    assert client.post(f"/api/admin/connectors/{identity}/refresh").json()["enabled"] is True
    result = client.get(f"/api/admin/connectors/{identity}").json()
    assert result["version"] == "v5.0.0" and result["items"][0]["correlation"] == "matched"
    assert result["items"][0]["items"] == [{"server_id": "arr", "upstream_id": 101}]
    assert "PRIVATE" not in json.dumps(result) and "/secret" not in json.dumps(result)
    state["partial"] = True
    assert client.post(f"/api/admin/connectors/{identity}/refresh").json()["stale"] is True
    assert client.get(f"/api/admin/connectors/{identity}").json()["total"] == 1
    assert {method for method, path, _ in state["requests"] if not path.endswith("auth/login")} == {"GET"}


@pytest.mark.parametrize(("status", "body", "cookie", "accepted"), [
    (200, "Ok.", "SID=fixture", True),
    (204, "", "SID=fixture", False),
    (204, "", "QBT_SID_8080=fixture", True),
    (200, "Fails.", "SID=fixture", False),
    (401, "Unauthorized", "QBT_SID_8080=fixture", False),
    (403, "Forbidden", "SID=fixture", False),
    (302, "", "SID=fixture", False),
    (200, "<html>Sign in</html>", "SID=fixture", False),
    (200, "Ok.", "", False),
    (204, "", "", False),
    (204, "", "unrelated=fixture", False),
    (204, "", "QBT_SID_8080=", False),
    (204, "", "QBT_SID_invalid=fixture", False),
    (204, "", "QBT_SID_0=fixture", False),
    (204, "", "QBT_SID_65536=fixture", False),
    (204, "", "QBT_SID_=fixture", False),
    (200, "", "QBT_SID_8080=fixture", False),
    (200, "Ok.", "QBT_SID_8080=fixture", False),
])
def test_qbittorrent_login_contract(api_client, db_factory, provider, status, body, cookie, accepted):  # noqa: ANN001, ANN201
    client, identity, state = setup(api_client, db_factory, provider, "qbittorrent")
    state.update(login_status=status, login_body=body, login_cookie=cookie, require_session=True, version="v5.3.0", api_version="2.16.2")
    response = client.post(f"/api/admin/connectors/{identity}/refresh")
    assert response.status_code == 200
    result = response.json()
    assert result["enabled"] is accepted
    reads = [path for method, path, _ in state["requests"] if method == "GET"]
    if accepted:
        assert result["last_error"] is None
        assert result["version"] == "v5.3.0"
        assert state["cookies"] and all(value == cookie for value in state["cookies"])
        assert any(path.endswith("sync/maindata?rid=0") for path in reads)
    else:
        assert result["last_error"]
        assert not reads


@pytest.mark.parametrize(("path", "status"), [
    ("app/version", 403), ("app/webapiVersion", 403), ("sync/maindata", 403),
    ("app/version", 204), ("app/webapiVersion", 204), ("sync/maindata", 204),
])
def test_qbittorrent_requires_protected_reads_after_modern_login(api_client, db_factory, provider, path, status):  # noqa: ANN001, ANN201
    client, identity, state = setup(api_client, db_factory, provider, "qbittorrent")
    state.update(login_status=204, login_body="", login_cookie="QBT_SID_8080=fixture", require_session=True,
                 read_status={f"/api/v2/{path}": status})
    result = client.post(f"/api/admin/connectors/{identity}/refresh").json()
    assert result["enabled"] is False
    assert result["last_error"] == f"Connector endpoint returned HTTP {status}"


def test_qbittorrent_modern_login_keeps_bounded_snapshot_reads(api_client, db_factory, provider, monkeypatch):  # noqa: ANN001, ANN201
    from app.services import infrastructure_connectors
    monkeypatch.setattr(infrastructure_connectors, "MAX_BYTES", 16)
    client, identity, state = setup(api_client, db_factory, provider, "qbittorrent")
    state.update(login_status=204, login_body="", login_cookie="QBT_SID_8080=fixture", require_session=True)
    result = client.post(f"/api/admin/connectors/{identity}/refresh").json()
    assert result["enabled"] is False
    assert result["last_error"] == "Connector response exceeded the size limit"


def test_nzbget_uses_read_only_rpc_and_hides_paths(api_client, db_factory, provider):  # noqa: ANN001, ANN201
    client, identity, state = setup(api_client, db_factory, provider, "nzbget")
    assert client.post(f"/api/admin/connectors/{identity}/refresh").json()["version"] == "24.6"
    result = client.get(f"/api/admin/connectors/{identity}").json()
    assert [row["id"] for row in result["items"]] == ["7", "8"]
    assert all(row["correlation"] == "unmatched" for row in result["items"])
    assert "PRIVATE" not in json.dumps(result) and "/secret" not in json.dumps(result)
    assert {json.loads(body)["method"] for _, _, body in state["requests"]} == {"version", "status", "listgroups", "history"}


def test_prowlarr_paginates_diagnostics_without_configuration_or_grab(api_client, db_factory, provider):  # noqa: ANN001, ANN201
    client, identity, state = setup(api_client, db_factory, provider, "prowlarr")
    assert client.post(f"/api/admin/connectors/{identity}/refresh").json()["enabled"] is True
    result = client.get(f"/api/admin/connectors/{identity}?dataset=history&page=11&page_size=100").json()
    assert result["total"] == 1001 and len(result["items"]) == 1
    assert "PRIVATE" not in json.dumps(result)
    assert all(method == "GET" for method, _, _ in state["requests"])
    assert client.post(f"/api/admin/connectors/{identity}/grab", json={}).status_code in (404, 405)


def test_connectors_are_admin_only_and_endpoint_change_cannot_reuse_secrets(api_client, db_factory, provider):  # noqa: ANN001, ANN201
    client, identity, _ = setup(api_client, db_factory, provider, "prowlarr")
    changed = {"kind": "prowlarr", "name": "Changed", "base_url": "http://127.0.0.1:9"}
    assert client.put(f"/api/admin/connectors/{identity}", json=changed).status_code == 400
    assert client.delete(f"/api/admin/connectors/{identity}").json()["api_key_set"] is False
    with db_factory() as db:
        assert db.get(ServiceConnector, identity).api_key is None
    api_client(user=make_user("member"), base_url="http://localhost")
    assert client.get("/api/admin/connectors").status_code == 403
    assert client.post(f"/api/admin/connectors/{identity}/refresh").status_code == 403
