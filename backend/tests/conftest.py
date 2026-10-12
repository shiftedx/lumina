"""Global test isolation guards.

Every automated backend test runs against a per-test temporary app-data root
with provider/external network denied by default. Loopback connections stay
permitted for local fixture servers. The policy itself lives in
``scripts/test_safety.py`` (the canonical, stdlib-only source of truth, also
run by the bootstrap lane) so both lanes enforce the same rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import test_safety  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect every app root into this test's temporary directory.

    Derived settings properties (database_url, library_root, temp_root,
    remote_stream_cache_root, archive_path) all read ``data_dir`` at call
    time, so patching it isolates the whole application root. The
    module-level engine in ``app.db`` is bound to the database URL at
    import time (before any test can redirect settings); rebind it so all
    session_scope/SessionLocal traffic reaches this test's root and restore
    the original engine on teardown.
    """
    app_data = tmp_path / "lumina-test-data"
    app_data.mkdir()
    test_safety.check_root_isolation(app_data)
    monkeypatch.setattr(settings, "data_dir", app_data)

    from app.services import model_supervisor

    monkeypatch.setattr(model_supervisor.supervisor, "closed", False)  # an app lifespan's shutdown closes it

    from sqlalchemy import create_engine, event

    from app import db as db_module

    original_engine = db_module.engine
    test_engine = create_engine(
        settings.database_url,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    event.listens_for(test_engine, "connect")(db_module.configure_sqlite_connection)
    db_module.engine = test_engine
    db_module.SessionLocal.configure(bind=test_engine)
    try:
        yield
    finally:
        test_engine.dispose()
        db_module.engine = original_engine
        db_module.SessionLocal.configure(bind=original_engine)


@pytest.fixture
def db_factory():
    """Session factory over a fresh in-memory database (see ``support.memory_session_factory``)."""
    from support import memory_session_factory

    factory = memory_session_factory()
    try:
        yield factory
    finally:
        factory.kw["bind"].dispose()


@pytest.fixture
def api_client(db_factory):
    """Build TestClients whose ``get_db`` uses ``db_factory``.

    ``api_client(user=...)`` also authenticates every request as that User (or
    as whatever a zero-argument callable returns); extra kwargs go to TestClient.
    Overrides and the rate limiter are reset on teardown.
    """
    from fastapi.testclient import TestClient

    from app.db import get_db, session_scope, stream_session_scope
    from app.main import app
    from app.security import get_current_user
    from app.services.rate_limit import rate_limiter

    def override_db():
        with db_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    async def override_stream_session_scope():
        return lambda: session_scope(db_factory)

    clients: list[TestClient] = []

    def build(user=None, **kwargs) -> TestClient:
        if user is not None:
            app.dependency_overrides[get_current_user] = user if callable(user) else (lambda: user)
        client = TestClient(app, **kwargs)
        clients.append(client)
        return client

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[stream_session_scope] = override_stream_session_scope
    rate_limiter.clear()
    try:
        yield build
    finally:
        for client in clients:
            client.close()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(stream_session_scope, None)
        app.dependency_overrides.pop(get_current_user, None)
        rate_limiter.clear()


@pytest.fixture(autouse=True)
def no_provider_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deny non-loopback sockets and external DNS for the duration of the test."""
    test_safety.LoopbackOnlyGuard().apply(monkeypatch.setattr)


@pytest.fixture(autouse=True)
def fresh_provider_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with full discovery budgets; the process-wide ones would otherwise drain across tests."""
    from app.services import provider_budget

    from app.services import yt_dlp_service

    for name in ("youtube", "twitch"):
        monkeypatch.setattr(provider_budget, name, provider_budget.fresh(name))
    # Shared extraction caches outlive a test otherwise (a preview stays ten minutes).
    yt_dlp_service._PREVIEW_CACHE.clear()
    yt_dlp_service._RESOLVE_CACHE.clear()


@pytest.fixture(autouse=True)
def empty_follow_feed_cache() -> None:
    """The household channel-feed cache is process-wide; each test starts without a cached feed."""
    from app.services import source_automation

    source_automation._FEED_CACHE.clear()


@pytest.fixture(autouse=True)
def no_image_grants() -> None:
    """A Jellyfin call grants its address tag-less art for minutes; the table is process-wide, so each test starts empty."""
    from app.services.connected_apps import image_grants

    image_grants.clear()


@pytest.fixture(autouse=True)
def import_stop_event_clear() -> None:
    """An app lifespan shutdown leaves the process-wide import stop_event set, which makes drive() exit at once."""
    from app.services import library_import

    library_import.stop_event.clear()
