from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import EnvSettings
from app.db import get_db
from app.main import app
from app.models import Base, User
from app.security import create_app_session, hash_password, resolve_session_user
from app.services.rate_limit import rate_limiter
from app.services.users import UserService


def test_fresh_configuration_has_no_reusable_administrator_credentials() -> None:
    config = EnvSettings(_env_file=None)

    assert not hasattr(config, "bootstrap_admin_username")
    assert not hasattr(config, "bootstrap_admin_password")


def test_fresh_database_requires_user_owned_bootstrap(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with session_factory() as db:
        assert UserService(db).needs_bootstrap() is True


def test_opaque_session_survives_database_restart(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with session_factory() as db:
        user = User(
            id="admin-1",
            username="owner",
            display_name="Owner",
            password_hash=hash_password("test-only-passphrase"),
            role="admin",
            is_active=True,
        )
        db.add(user)
        _, session_token = create_app_session(db, user)
        db.commit()

    engine.dispose()
    restarted_engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}", future=True)
    restarted_session_factory = sessionmaker(bind=restarted_engine, expire_on_commit=False, future=True)
    with restarted_session_factory() as db:
        resolved = resolve_session_user(db, session_token)

    assert resolved is not None
    assert resolved.id == "admin-1"


def test_bootstrap_and_cookie_session_survive_application_reconnect(tmp_path: Path) -> None:
    database_path = tmp_path / "application.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    current_session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def override_db():
        with current_session_factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    rate_limiter.clear()
    first_client = TestClient(app, base_url="http://localhost")
    try:
        assert first_client.get("/api/bootstrap/status").json() == {"needs_setup": True}
        created = first_client.post(
            "/api/bootstrap/admin",
            json={"username": "owner", "display_name": "Owner", "password": "Test-only passphrase 42"},
        )
        assert created.status_code == 201
        assert first_client.get("/api/bootstrap/status").json() == {"needs_setup": False}

        first_login = first_client.post(
            "/api/session/login",
            json={"username": "owner", "password": "Test-only passphrase 42"},
        )
        second_client = TestClient(app, base_url="http://localhost")
        second_login = second_client.post(
            "/api/session/login",
            json={"username": "owner", "password": "Test-only passphrase 42"},
        )
        second_client.close()
        first_token = first_login.cookies[EnvSettings().session_cookie_name]
        second_token = second_login.cookies[EnvSettings().session_cookie_name]
        assert first_login.status_code == 200
        assert second_login.status_code == 200
        assert first_token != second_token
        assert len(first_token) >= 64
    finally:
        first_client.close()

    engine.dispose()
    restarted_engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    current_session_factory = sessionmaker(bind=restarted_engine, expire_on_commit=False, future=True)
    restarted_client = TestClient(app, base_url="http://localhost")
    restarted_client.cookies.set(EnvSettings().session_cookie_name, first_token)
    try:
        current_session = restarted_client.get("/api/session/me")
        assert current_session.status_code == 200
        assert current_session.json()["user"]["username"] == "owner"
    finally:
        restarted_client.close()
        restarted_engine.dispose()
        app.dependency_overrides.clear()
        rate_limiter.clear()


def test_docker_publishes_only_to_loopback() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert compose["services"]["lumina"]["ports"] == ["127.0.0.1:8765:8765"]
