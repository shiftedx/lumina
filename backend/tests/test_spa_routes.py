from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import SpaStaticFiles
from app.services import public_address


def spa(tmp_path, origin="https://lumina.example.test"):
    (tmp_path / "index.html").write_text("<!doctype html><html><head><title>Lumina</title></head><body><div id=root></div></body></html>")
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "assets" / "app.js").write_text("ok")
    (tmp_path / "assets" / "app-AbCd1234.js").write_text("versioned js")
    (tmp_path / "assets" / "app-AbCd1234.css").write_text("versioned css")
    (tmp_path / "fonts").mkdir(exist_ok=True)
    (tmp_path / "fonts" / "lumina.woff2").write_bytes(b"font")
    (tmp_path / "music-sw.js").write_text("worker")
    social = tmp_path / "social"
    social.mkdir(exist_ok=True)
    (social / "lumina-share-v2.jpg").write_bytes(b"generic-card")
    (social / "lumina-invite-v1.jpg").write_bytes(b"invite-card")
    app = FastAPI()
    origin_option = {} if origin is None else {"public_origin": lambda: origin}
    app.mount("/", SpaStaticFiles(directory=tmp_path, html=True, **origin_option), name="frontend")
    return TestClient(app)


def test_client_routes_serve_index_but_missing_assets_and_api_stay_404(tmp_path):
    client = spa(tmp_path)

    for route in ("/", "/index.html", "/library", "/watch/library/abc", "/explore?q=cats"):
        response = client.get(route)
        assert response.status_code == 200 and "root" in response.text, route
        assert response.headers["cache-control"] == "no-cache"
    immutable = "public, max-age=31536000, immutable"
    for asset in ("/assets/app-AbCd1234.js", "/assets/app-AbCd1234.css"):
        response = client.get(asset)
        assert response.status_code == 200 and response.headers["cache-control"] == immutable
    for asset in ("/assets/app.js", "/fonts/lumina.woff2", "/music-sw.js"):
        response = client.get(asset)
        assert response.status_code == 200 and "immutable" not in response.headers.get("cache-control", "")
    assert client.get("/assets/app.js").text == "ok"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/not-a-route").status_code == 404


def test_spa_shell_has_server_rendered_generic_social_preview_from_the_trusted_origin(tmp_path):
    client = spa(tmp_path)
    response = client.get(
        "/title/private-media-id?token=query-secret",
        headers={"Host": "attacker.example", "Cookie": "archive_session=session-secret"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert '<meta property="og:title" content="Lumina — Your household media vault"' in response.text
    assert '<meta property="og:description" content="Movies, music, and favorites. One household. One media vault."' in response.text
    assert '<meta property="og:url" content="https://lumina.example.test/"' in response.text
    assert '<meta property="og:image" content="https://lumina.example.test/social/lumina-share-v2.jpg"' in response.text
    assert '<meta property="og:image:type" content="image/jpeg"' in response.text
    assert '<meta property="og:image:width" content="1200"' in response.text
    assert '<meta property="og:image:height" content="630"' in response.text
    assert '<meta name="twitter:card" content="summary_large_image"' in response.text
    assert '<meta name="twitter:title" content="Lumina — Your household media vault"' in response.text
    assert '<meta name="twitter:description" content="Movies, music, and favorites. One household. One media vault."' in response.text
    assert '<meta name="twitter:image" content="https://lumina.example.test/social/lumina-share-v2.jpg"' in response.text
    for private in ("attacker.example", "private-media-id", "query-secret", "session-secret"):
        assert private not in response.text


def test_spa_shell_defaults_to_the_configured_link_base(tmp_path, monkeypatch):
    monkeypatch.setattr(public_address, "link_base", lambda: "https://configured.example.test")
    response = spa(tmp_path, origin=None).get("/", headers={"Host": "attacker.example"})

    assert "https://configured.example.test/social/lumina-share-v2.jpg" in response.text
    assert "attacker.example" not in response.text


def test_invite_path_has_fixed_token_free_invitation_preview(tmp_path):
    client = spa(tmp_path)
    expected = {
        "You’re invited to a Lumina media vault",
        "You’re invited to explore a Lumina media vault. Movies, music, and favorites, ready to discover.",
        "https://lumina.example.test/invite",
        "https://lumina.example.test/social/lumina-invite-v1.jpg",
    }

    for path in ("/invite", "/invite/?invite=query-secret"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert all(value in response.text for value in expected)
        assert response.text.count("You’re invited to a Lumina media vault") == 2
        assert response.text.count("lumina-invite-v1.jpg") == 2
        assert "query-secret" not in response.text


def test_shell_head_and_versioned_social_assets_keep_http_semantics(tmp_path):
    client = spa(tmp_path)

    shell = client.get("/")
    shell_head = client.head("/")
    assert shell_head.status_code == 200 and shell_head.content == b""
    assert shell_head.headers["content-length"] == shell.headers["content-length"]
    assert shell_head.headers["cache-control"] == "no-cache"

    image = client.get("/social/lumina-share-v2.jpg")
    image_head = client.head("/social/lumina-share-v2.jpg")
    assert image.content == b"generic-card" and image.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert image_head.content == b"" and image_head.headers["content-length"] == image.headers["content-length"]
    assert image_head.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.post("/invite").status_code == 405


def test_a_websocket_the_app_does_not_serve_is_refused_before_accept(tmp_path):
    """A Jellyfin client's /socket must not reach StaticFiles, which asserts an http scope."""
    import pytest
    from starlette.websockets import WebSocketDisconnect

    (tmp_path / "index.html").write_text("<div id=root></div>")
    app = FastAPI()
    app.mount("/", SpaStaticFiles(directory=tmp_path, html=True), name="frontend")
    with pytest.raises(WebSocketDisconnect) as refused, TestClient(app).websocket_connect("/socket"):
        pass
    assert refused.value.code == 1008
