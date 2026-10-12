import pytest

from app.models import LibraryItem
from app.schemas import NOTE_BODY_MAX_CHARS
from app.services.media_notes import MediaNotesService
from support import make_user as member, memory_session_factory as make_factory


def seed(session):
    owner, friend, admin = member("owner"), member("friend"), member("admin", role="admin")
    item = LibraryItem(id="film", user_id=owner.id, visibility="shared", title="Film", metadata_json={}, status="available")
    session.add_all([owner, friend, admin, item])
    session.commit()
    return owner, friend, admin, item


def test_notes_private_shared_authority() -> None:
    session = make_factory()()
    owner, friend, admin, item = seed(session)
    notes = MediaNotesService(session)
    private = notes.add_note(item.id, "Just mine", "private", owner)
    household = notes.add_note(item.id, "For everyone", "household", owner)
    assert private.visibility == "private"

    assert [n.id for n in notes.list_notes(item.id, friend)] == [household.id]
    with pytest.raises(ValueError, match="not found"):
        notes.update_note(private.id, "peek", "private", friend)
    with pytest.raises(PermissionError):
        notes.update_note(household.id, "hijack", "household", friend)
    with pytest.raises(PermissionError):
        notes.delete_note(household.id, friend)
    with pytest.raises(PermissionError):
        notes.update_note(household.id, "edit", "household", admin)
    assert notes.serialize(household, admin).can_delete is True
    assert notes.serialize(household, friend).can_delete is False

    # Revoked item access hides household notes from the friend immediately.
    item.visibility = "private"
    session.flush()
    with pytest.raises(ValueError, match="not found"):
        notes.list_notes(item.id, friend)
    item.visibility = "shared"
    session.flush()

    notes.delete_note(household.id, admin)
    assert [n.id for n in notes.list_notes(item.id, owner)] == [private.id]


def test_notes_timestamp_order() -> None:
    session = make_factory()()
    owner, _, _, item = seed(session)
    notes = MediaNotesService(session)
    late = notes.add_note(item.id, "late", "private", owner, timestamp_ms=90_000)
    general = notes.add_note(item.id, "general", "private", owner)
    early = notes.add_note(item.id, "early", "household", owner, timestamp_ms=1_500)
    assert [n.id for n in notes.list_notes(item.id, owner)] == [general.id, early.id, late.id]
    with pytest.raises(ValueError):
        notes.add_note(item.id, "bad", "private", owner, timestamp_ms=-1)


def test_notes_untrusted_content_inert(db_factory, api_client) -> None:
    with db_factory() as session:
        owner, *_ = seed(session)

    client = api_client(user=owner, base_url="http://localhost")
    script = "<script>alert(1)</script> [x](javascript:alert(1))"
    created = client.post("/api/library/film/notes", json={"body": script, "visibility": "household", "timestamp_ms": 4200})
    assert created.status_code == 201
    # Stored and returned verbatim as plain text; the UI renders it as text only.
    assert created.json()["body"] == script
    assert created.json()["timestamp_ms"] == 4200
    too_long = {"body": "x" * (NOTE_BODY_MAX_CHARS + 1), "visibility": "private"}
    assert client.post("/api/library/film/notes", json=too_long).status_code == 422
    assert client.post("/api/library/film/notes", json={"body": "hi", "visibility": "shared"}).status_code == 422
    assert client.post("/api/library/film/notes", json={"body": "hi", "user_id": "friend"}).status_code == 422
    assert client.post("/api/library/film/notes", json={"body": "   "}).status_code == 400
    assert client.get("/api/library/missing/notes").status_code == 404
