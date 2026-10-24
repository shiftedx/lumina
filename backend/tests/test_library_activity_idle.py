"""Passive progress polling must not keep optional media work paused."""
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import security
from app.db import get_db
from app.http_activity import AuthenticatedActivityMiddleware
from app.media_schemas import LibraryActivity
from app.routers import admin_library_automation
from app.services.background_work import background_work
from app.services import background_work as work_module


def test_progress_polling_allows_idle_but_browsing_still_pauses_background_work(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(work_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(security, "resolve_user_from_request", lambda *args, **kwargs: SimpleNamespace(id="member"))
    monkeypatch.setattr(admin_library_automation, "automation_service", lambda: SimpleNamespace(
        public_activity=lambda: LibraryActivity().model_dump(exclude={
            "artwork_pending", "artwork_failed", "artwork_missing", "artwork_paused",
        })))
    app = FastAPI()
    app.add_middleware(AuthenticatedActivityMiddleware)
    app.dependency_overrides[get_db] = lambda: None
    app.add_api_route("/api/library/activity", admin_library_automation.get_library_activity)
    app.add_api_route("/api/library", lambda user=Depends(security.get_current_user): {"items": []})
    for path in security.PASSIVE_AUTHENTICATED_GET_PATHS - {"/api/library/activity"}:
        app.add_api_route(path, lambda user=Depends(security.get_current_user): {"items": []})
    with TestClient(app) as client:
        assert client.get("/api/library").status_code == 200
        assert background_work.busy()
        for elapsed in range(5, 65, 5):
            now[0] = 100 + elapsed
            assert client.get("/api/library/activity").status_code == 200
            for path in security.PASSIVE_AUTHENTICATED_GET_PATHS - {"/api/library/activity"}:
                assert client.get(path).status_code == 200
        assert not background_work.busy()
        assert client.get("/api/library").status_code == 200
        assert background_work.busy()


@pytest.mark.parametrize("method,path", [("POST", "/api/library/activity"), ("GET", "/api/library/activity/other")])
def test_passive_status_exception_is_exact_and_read_only(monkeypatch, method, path):
    from starlette.requests import Request
    monkeypatch.setattr(security, "resolve_user_from_request", lambda *args, **kwargs: SimpleNamespace(id="member"))
    request = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""})
    security.get_current_user(request, db=None, credentials=None)
    try:
        assert background_work.busy()
    finally:
        background_work.end_request(request.scope)


@pytest.mark.parametrize("path", sorted(security.PASSIVE_AUTHENTICATED_GET_PATHS))
def test_passive_status_still_requires_authentication_without_touching_activity(monkeypatch, path):
    from starlette.requests import Request
    before = background_work._last_activity  # noqa: SLF001 - observable quiet-period contract
    monkeypatch.setattr(
        security, "resolve_user_from_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=401)),
    )
    request = Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})
    with pytest.raises(HTTPException) as failure:
        security.get_current_user(request, db=None, credentials=None)
    assert failure.value.status_code == 401
    assert background_work._last_activity == before  # noqa: SLF001
    assert not request.scope.get("state", {}).get("background_work_active", False)
