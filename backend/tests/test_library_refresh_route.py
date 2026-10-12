"""POST /api/library/refresh nudges the shared resumable sweep instead of running it in-request."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import AppMaintenanceState, LibraryItem, User
from app.security import get_current_user, hash_password
from app.services.library import RECONCILE_FILES_CURSOR_KEY, LibraryService, normalize_thumbnail_metadata


def make_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'refresh.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def _library_item(item_id: str, file_path: str) -> LibraryItem:
    metadata = normalize_thumbnail_metadata({"id": item_id, "filepath": file_path}, None, file_path)
    return LibraryItem(
        id=item_id,
        title=item_id,
        visibility="shared",
        file_path=file_path,
        file_size=5,
        metadata_json=metadata,
        metadata_summary=LibraryService.summarize_metadata(metadata),
        status="available",
    )


def test_refresh_advances_at_most_two_bounded_steps_and_preserves_sweep_progress(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    session_factory = make_session_factory(tmp_path)
    with session_factory() as setup:
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
        for index in range(7):
            media = tmp_path / f"item-{index}.mp4"
            media.write_bytes(b"media")
            setup.add(_library_item(f"item-{index}", str(media)))
        # Another member's refresh is mid-sweep: this cursor must be continued,
        # never reset back to the start by a concurrent refresh.
        setup.add(AppMaintenanceState(key=RECONCILE_FILES_CURSOR_KEY, value="item-2"))
        setup.commit()

    step_calls: list[object] = []
    original_step = LibraryService.reconcile_files_step

    def small_bounded_step(self, *, batch_size=None):  # noqa: ANN001
        step_calls.append(batch_size)
        return original_step(self, batch_size=2)

    monkeypatch.setattr(LibraryService, "reconcile_files_step", small_bounded_step)

    def override_db():
        with session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id="member-1", username="member", display_name="Member", role="viewer", is_active=True
    )
    client = TestClient(app, base_url="http://localhost")
    try:
        response = client.post("/api/library/refresh")
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["items"]) == 7, "the refresh response must still serve the first library page"

    assert len(step_calls) <= 2, f"the refresh route ran {len(step_calls)} sweep steps in-request"
    with session_factory() as verifier:
        cursor = verifier.get(AppMaintenanceState, RECONCILE_FILES_CURSOR_KEY)
        assert cursor is not None
        # Two batches of two continue item-3..item-6 from the seeded cursor; the
        # 60 s maintenance loop finishes the sweep, not the interactive request.
        assert cursor.value == "item-6", f"sweep progress was restarted or completed in-request: {cursor.value!r}"
