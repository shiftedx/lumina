"""New-item webhook delivery and library SSE events must escape only after the durable commit.

Reproduces the integrated shape of JobManager._run_job -> write_transaction("library_item_upsert")
-> LibraryService.upsert_from_info -> WebhookService.notify_new_video -> httpx POST, and the
route shape where set_visibility/delete_file commit at the get_db teardown.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.db as app_db
import app.services.webhooks as webhooks
from app.config import settings
from app.db import Base
from app.models import LibraryItem, User
from app.persistence import write_transaction
from app.security import hash_password
from app.services.library import LibraryService
from app.services.media_artifacts import MediaArtifactService
from support import seed_app_settings


def make_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'boundary.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 0.1},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=100")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def seed_household(factory, tmp_path, *, webhook_enabled: bool = True) -> None:
    ui_prefs = (
        {
            "webhook_url": "https://discord.com/api/webhooks/1/token",
            "webhook_enabled": True,
            "webhook_notify_new_videos": True,
        }
        if webhook_enabled
        else {}
    )
    with factory() as setup:
        setup.add(
            User(
                id="member-1",
                username="member",
                display_name="Member",
                password_hash=hash_password("secret-password"),
                role="viewer",
                is_active=True,
            )
        )
        seed_app_settings(
            setup, temp_root=str(tmp_path), archive_path=str(tmp_path / "archive.txt"), yt_dlp_defaults={}, ui_prefs=ui_prefs
        )


def _library_file():  # noqa: ANN202
    settings.library_root.mkdir(parents=True, exist_ok=True)
    return settings.library_root / "video.mp4"


def upsert_info(media_path) -> dict:  # noqa: ANN001
    return {
        "id": "vid-1",
        "extractor_key": "Youtube",
        "title": "Boundary video",
        "webpage_url": "https://example.com/watch?v=vid-1",
        "filepath": str(media_path),
    }


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _RecordingEvents:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, name: str, payload: dict) -> None:
        self.published.append((name, payload))


def test_new_item_webhook_posts_only_after_the_durable_commit(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    factory = make_session_factory(tmp_path)
    seed_household(factory, tmp_path)
    db = factory()
    observations: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):  # noqa: ANN001
            driver = db.connection().connection.driver_connection
            with factory() as verifier:
                durable_rows = verifier.query(LibraryItem).filter(LibraryItem.remote_id == "vid-1").count()
            observations.append(
                {
                    "write_transaction_open": bool(driver.in_transaction),
                    "durable_rows": durable_rows,
                }
            )
            return _FakeResponse()

    monkeypatch.setattr(webhooks.httpx, "Client", FakeClient)
    media = _library_file()
    media.write_bytes(b"x")
    library = LibraryService(db)
    # The exact integrated shape from JobManager._run_job (write_transaction "library_item_upsert").
    with write_transaction(db, name="library_item_upsert"):
        library.upsert_from_info(upsert_info(media), owner_user_id="member-1", visibility="shared")
    db.close()

    assert observations, "webhook delivery was never attempted (gating changed?)"
    assert [obs["write_transaction_open"] for obs in observations] == [False], (
        f"the new-video webhook HTTP POST ran with a write transaction open: {observations}"
    )
    assert [obs["durable_rows"] for obs in observations] == [1], (
        f"the new-video webhook HTTP POST ran before the commit was durable: {observations}"
    )


def test_new_item_webhook_does_not_post_when_the_transaction_rolls_back(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    factory = make_session_factory(tmp_path)
    seed_household(factory, tmp_path)
    db = factory()
    posts: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):  # noqa: ANN001
            posts.append(url)
            return _FakeResponse()

    monkeypatch.setattr(webhooks.httpx, "Client", FakeClient)
    media = _library_file()
    media.write_bytes(b"x")
    library = LibraryService(db)
    with pytest.raises(RuntimeError, match="boom"):
        with write_transaction(db, name="library_item_upsert"):
            library.upsert_from_info(upsert_info(media), owner_user_id="member-1", visibility="shared")
            raise RuntimeError("boom")
    db.close()

    assert posts == [], "the new-video webhook posted for a rolled-back upsert"
    with factory() as verifier:
        assert verifier.query(LibraryItem).count() == 0


def test_upsert_event_publishes_exactly_once_after_commit_and_never_on_rollback(tmp_path) -> None:  # noqa: ANN001
    factory = make_session_factory(tmp_path)
    seed_household(factory, tmp_path, webhook_enabled=False)
    db = factory()
    events = _RecordingEvents()
    media = _library_file()
    media.write_bytes(b"x")
    library = LibraryService(db, events)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="boom"):
        with write_transaction(db, name="library_item_upsert"):
            library.upsert_from_info(upsert_info(media), owner_user_id="member-1", visibility="shared")
            raise RuntimeError("boom")
    assert events.published == [], "library_item_upserted escaped for a rolled-back upsert"

    with write_transaction(db, name="library_item_upsert"):
        library.upsert_from_info(upsert_info(media), owner_user_id="member-1", visibility="shared")
        assert events.published == [], "library_item_upserted escaped before the durable commit"
    db.close()
    assert [name for name, _ in events.published] == ["library_item_upserted"]


def _seed_item(factory, media_path) -> None:  # noqa: ANN001
    with factory() as setup:
        setup.add(
            LibraryItem(
                id="item-1",
                user_id="member-1",
                visibility="private",
                extractor="Youtube",
                remote_id="vid-1",
                title="Boundary item",
                file_path=str(media_path),
                metadata_json={"id": "vid-1", "filepath": str(media_path)},
                status="available",
            )
        )
        setup.flush()
        MediaArtifactService(setup).register_file(setup.get(LibraryItem, "item-1"), str(media_path))
        setup.commit()


def test_set_visibility_event_publishes_once_on_the_route_teardown_commit(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    factory = make_session_factory(tmp_path)
    seed_household(factory, tmp_path, webhook_enabled=False)
    media = _library_file()
    media.write_bytes(b"x")
    _seed_item(factory, media)
    monkeypatch.setattr(app_db, "SessionLocal", factory)
    events = _RecordingEvents()

    # The route shape: no seam call, the durable commit is the get_db teardown.
    with app_db.session_scope() as session:
        service = LibraryService(session, events)  # type: ignore[arg-type]
        user = session.get(User, "member-1")
        item = session.get(LibraryItem, "item-1")
        service.set_visibility(item, "shared", user)
        assert events.published == [], "library_item_upserted escaped before the teardown commit"

    assert [name for name, _ in events.published] == ["library_item_upserted"]
    with factory() as verifier:
        assert verifier.get(LibraryItem, "item-1").visibility == "shared"


def test_delete_file_event_publishes_once_on_the_route_teardown_commit(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    factory = make_session_factory(tmp_path)
    seed_household(factory, tmp_path, webhook_enabled=False)
    media = _library_file()
    media.write_bytes(b"x")
    _seed_item(factory, media)
    monkeypatch.setattr(app_db, "SessionLocal", factory)
    events = _RecordingEvents()

    with app_db.session_scope() as session:
        service = LibraryService(session, events)  # type: ignore[arg-type]
        item = session.get(LibraryItem, "item-1")
        service.delete_file(item)
        assert events.published == [], "library_item_missing escaped before the teardown commit"

    assert [name for name, _ in events.published] == ["library_item_missing"]
    with factory() as verifier:
        assert verifier.get(LibraryItem, "item-1").status == "missing"


def test_delete_file_event_is_discarded_when_the_request_rolls_back(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    factory = make_session_factory(tmp_path)
    seed_household(factory, tmp_path, webhook_enabled=False)
    media = _library_file()
    media.write_bytes(b"x")
    _seed_item(factory, media)
    monkeypatch.setattr(app_db, "SessionLocal", factory)
    events = _RecordingEvents()

    with pytest.raises(RuntimeError, match="boom"):
        with app_db.session_scope() as session:
            service = LibraryService(session, events)  # type: ignore[arg-type]
            item = session.get(LibraryItem, "item-1")
            service.delete_file(item)
            raise RuntimeError("boom")

    assert events.published == [], "library_item_missing escaped for a rolled-back request"
    with factory() as verifier:
        assert verifier.get(LibraryItem, "item-1").status == "available"
