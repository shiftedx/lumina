from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import SpaStaticFiles


def test_client_routes_serve_index_but_missing_assets_and_api_stay_404(tmp_path):
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("ok")
    app = FastAPI()
    app.mount("/", SpaStaticFiles(directory=tmp_path, html=True), name="frontend")
    client = TestClient(app)

    for route in ("/", "/library", "/watch/library/abc", "/explore?q=cats"):
        response = client.get(route)
        assert response.status_code == 200 and "root" in response.text, route
    assert client.get("/assets/app.js").text == "ok"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/not-a-route").status_code == 404


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
