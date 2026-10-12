"""Library list progress: each /api/library page carries the member's own progress, from
one query; other serialisations leave it out."""
from __future__ import annotations

from sqlalchemy import event

from discovery_support import add_progress
from test_category_walls import walls  # noqa: F401 - the shared gallery fixture
from title_support import ALICE, BOB, CHANNEL_NEW, CHANNEL_OLD, TRAILER


def test_list_pages_carry_the_members_own_progress(walls) -> None:  # noqa: ANN001, F811
    with walls.db() as session:
        add_progress(session, ALICE, CHANNEL_NEW, position=120)
        add_progress(session, ALICE, CHANNEL_OLD, completed=True, position=600)
        add_progress(session, BOB, CHANNEL_NEW, completed=True)
        session.commit()
    alice = {item["id"]: item["progress"] for item in walls.get("/api/library", kind="video").json()["items"]}
    assert alice[CHANNEL_NEW] == {"position_seconds": 120, "duration_seconds": 1200, "completed": False}
    assert alice[CHANNEL_OLD] == {"position_seconds": 600, "duration_seconds": 1200, "completed": True}
    assert alice[TRAILER] is None
    bob = {item["id"]: item["progress"] for item in walls.get("/api/library", kind="video", member="bob").json()["items"]}
    assert (bob[CHANNEL_NEW]["completed"], bob[CHANNEL_OLD]) == (True, None)
    assert walls.get(f"/api/library/{CHANNEL_NEW}").json()["progress"] is None


def test_progress_is_one_query_per_page(walls) -> None:  # noqa: ANN001, F811
    statements: list[str] = []
    engine = walls.db.kw["bind"]

    def record(_connection, _cursor, statement, *_rest) -> None:  # noqa: ANN001
        if "playback_progress" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert walls.get("/api/library", limit=60).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 1, statements
