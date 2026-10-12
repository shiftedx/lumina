from app.models import LibraryItem, PlaybackProgress, User
from support import make_user


def test_playback_progress_is_isolated_and_continue_watching_is_ordered(db_factory, api_client) -> None:
    session_factory = db_factory
    user_a = make_user("user-a", username="alice", display_name="Alice")
    user_b = make_user("user-b", username="bob", display_name="Bob")
    item = LibraryItem(
        id="item-1",
        user_id=user_a.id,
        visibility="shared",
        title="Household favorite",
        duration=600,
        metadata_json={},
        status="available",
    )
    item_two = LibraryItem(
        id="item-2",
        user_id=user_b.id,
        visibility="shared",
        title="A newer checkpoint",
        duration=300,
        metadata_json={},
        status="available",
    )
    with session_factory.begin() as session:
        session.add_all([user_a, user_b, item, item_two])

    active_user = {"value": user_a}

    def override_current_user() -> User:
        return active_user["value"]

    client = api_client(user=override_current_user, base_url="http://localhost")
    saved = client.put(
        "/api/library/item-1/playback",
        json={"position_seconds": 120, "duration_seconds": 600, "completed": False},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["position_seconds"] == 120

    active_user["value"] = user_b
    assert client.get("/api/library/item-1/playback").json() is None
    assert client.get("/api/playback/continue").json() == []

    client.put(
        "/api/library/item-1/playback",
        json={"position_seconds": 45, "duration_seconds": 600, "completed": False},
    )

    active_user["value"] = user_a
    assert client.get("/api/library/item-1/playback").json()["position_seconds"] == 120
    shelf = client.get("/api/playback/continue").json()
    assert [(entry["item"]["id"], entry["position_seconds"]) for entry in shelf] == [("item-1", 120)]

    client.put(
        "/api/library/item-2/playback",
        json={"position_seconds": 30, "duration_seconds": 300, "completed": False},
    )
    assert [entry["item_id"] for entry in client.get("/api/playback/continue").json()] == ["item-2", "item-1"]
    client.delete("/api/library/item-2/playback")

    completed = client.put(
        "/api/library/item-1/playback",
        json={"position_seconds": 590, "duration_seconds": 600, "completed": False},
    ).json()
    assert completed["completed"] is True
    assert client.get("/api/playback/continue").json() == []

    replayed = client.put(
        "/api/library/item-1/playback",
        json={"position_seconds": 0, "duration_seconds": 600, "completed": False},
    ).json()
    assert replayed["completed"] is False
    assert client.get("/api/playback/continue").json() == []

    client.put(
        "/api/library/item-1/playback",
        json={"position_seconds": 20, "duration_seconds": 600, "completed": False},
    )
    assert len(client.get("/api/playback/continue").json()) == 1

    assert client.delete("/api/library/item-1/playback").status_code == 204
    assert client.get("/api/library/item-1/playback").json() is None
    assert client.get("/api/playback/continue").json() == []


def test_continue_watching_is_bounded_to_one_join_query_and_visible_items(db_factory, api_client) -> None:
    from datetime import datetime

    from sqlalchemy import event

    from app.services.library import LibraryService

    session_factory = db_factory
    engine = db_factory.kw["bind"]
    viewer = make_user("viewer-1", username="alice", display_name="Alice")
    other = make_user("other-1", username="bob", display_name="Bob")

    def shared_item(item_id: str, status: str = "available", user_id: str = "other-1", visibility: str = "shared") -> LibraryItem:
        metadata = {"id": item_id}
        return LibraryItem(
            id=item_id,
            user_id=user_id,
            visibility=visibility,
            title=item_id,
            duration=600,
            metadata_json=metadata,
            metadata_summary=LibraryService.summarize_metadata(metadata),
            status=status,
        )

    from app.models import PlaybackProgress

    def progress(item_id: str, *, minute: int, position: int = 30, completed: bool = False) -> PlaybackProgress:
        watched = datetime(2026, 3, 1, 12, minute)
        return PlaybackProgress(
            id=f"progress-{item_id}",
            user_id=viewer.id,
            item_id=item_id,
            position_seconds=position,
            duration_seconds=600,
            completed=completed,
            last_watched_at=watched,
            created_at=watched,
            updated_at=watched,
        )

    with session_factory.begin() as session:
        session.add_all([viewer, other])
        for index in range(30):
            session.add(shared_item(f"watch-{index:02d}"))
            session.add(progress(f"watch-{index:02d}", minute=index))
        session.add(shared_item("missing-item", status="missing"))
        session.add(progress("missing-item", minute=58))
        session.add(shared_item("done-item"))
        session.add(progress("done-item", minute=57, completed=True))
        session.add(shared_item("unstarted-item"))
        session.add(progress("unstarted-item", minute=56, position=0))
        session.add(shared_item("private-item", visibility="private"))
        session.add(progress("private-item", minute=55))

    client = api_client(user=viewer, base_url="http://localhost")
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    try:
        shelf = client.get("/api/playback/continue").json()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert len(shelf) == 24
    assert [entry["item_id"] for entry in shelf] == [f"watch-{index:02d}" for index in reversed(range(6, 30))]
    excluded = {"missing-item", "done-item", "unstarted-item", "private-item"}
    assert excluded.isdisjoint({entry["item_id"] for entry in shelf})

    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    library_selects = [statement for statement in selects if "library_items" in statement]
    assert len(library_selects) == 1, library_selects
    assert "playback_progress" in library_selects[0]
    assert "metadata_json" not in library_selects[0]


# ---- Remove from Continue watching / Next up ----------------------------------

from discovery_support import add_movie, add_progress, add_series, add_version  # noqa: E402


def test_dismiss_hides_from_continue_watching_keeps_progress_and_replay_undoes(db_factory, api_client) -> None:
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_movie(session, "arr", "Arrival")
    client = api_client(user=viewer, base_url="http://localhost")
    client.put("/api/library/arr-v/playback", json={"position_seconds": 300, "duration_seconds": 1200})

    assert client.post("/api/library/arr-v/playback/dismiss").status_code == 204
    assert client.get("/api/playback/continue").json() == []
    assert client.get("/api/library/arr-v/playback").json()["position_seconds"] == 300  # never deleted

    assert client.delete("/api/library/arr-v/playback/dismiss").status_code == 204  # the undo toast
    assert [entry["item_id"] for entry in client.get("/api/playback/continue").json()] == ["arr-v"]

    client.post("/api/library/arr-v/playback/dismiss")
    client.put("/api/library/arr-v/playback", json={"position_seconds": 320, "duration_seconds": 1200})  # played again anywhere
    assert [entry["item_id"] for entry in client.get("/api/playback/continue").json()] == ["arr-v"]


def test_dismiss_hides_every_version_of_the_title(db_factory, api_client) -> None:
    """Dismissing a collapsed shelf card hides every version, not just the shown one."""
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_movie(session, "arr", "Arrival")
        add_version(session, "arr-4k", "arr")
        add_progress(session, "viewer", "arr-v", minutes_ago=30)
        add_progress(session, "viewer", "arr-4k", minutes_ago=10)
    client = api_client(user=viewer, base_url="http://localhost")

    assert client.post("/api/library/arr-4k/playback/dismiss").status_code == 204
    assert client.get("/api/playback/continue").json() == []
    with db_factory() as session:
        dismissed = {row.item_id: row.dismissed_at is not None for row in session.query(PlaybackProgress)}
    assert dismissed == {"arr-v": True, "arr-4k": True}

    assert client.delete("/api/library/arr-v/playback/dismiss").status_code == 204  # undo from either version
    with db_factory() as session:
        dismissed = {row.item_id: row.dismissed_at is not None for row in session.query(PlaybackProgress)}
    assert dismissed == {"arr-v": False, "arr-4k": False}


def test_dismiss_edge_cases_are_404(db_factory, api_client) -> None:
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_movie(session, "fresh", "Never played")
        add_movie(session, "hidden", "Private", owner="other", visibility="private")
        add_series(session, "priv", "Private Show", seasons={1: 1}, owner="other", visibility="private")
        add_progress(session, "other", "priv-s1e1-v")
    client = api_client(user=viewer, base_url="http://localhost")

    assert client.post("/api/library/fresh-v/playback/dismiss").status_code == 404
    assert client.delete("/api/library/fresh-v/playback/dismiss").status_code == 404
    assert client.post("/api/library/hidden-v/playback/dismiss").status_code == 404
    assert client.post("/api/library/nope/playback/dismiss").status_code == 404
    assert client.post("/api/titles/priv/next-up/dismiss").status_code == 404
    assert client.post("/api/titles/nope/next-up/dismiss").status_code == 404


def test_continue_watching_collapses_versions_and_carries_the_title(db_factory, api_client) -> None:
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_movie(session, "arr", "Arrival")
        add_version(session, "arr-4k", "arr")
        add_series(session, "show", "Show", seasons={1: 2})
        add_progress(session, "viewer", "arr-v", minutes_ago=30)
        add_progress(session, "viewer", "arr-4k", minutes_ago=10)
        add_progress(session, "viewer", "show-s1e2-v", minutes_ago=20)
    client = api_client(user=viewer, base_url="http://localhost")

    shelf = client.get("/api/playback/continue").json()

    assert [(entry["item_id"], entry["title"]["id"]) for entry in shelf] == [("arr-4k", "arr"), ("show-s1e2-v", "show-s1e2")]
    episode = shelf[1]["title"]
    assert (episode["series_name"], episode["season_number"], episode["index_number"]) == ("Show", 1, 2)


def test_next_up_dismiss_marks_the_series_anchor_only(db_factory, api_client) -> None:
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_series(session, "show", "Show", seasons={1: 3})
        add_progress(session, "viewer", "show-s1e1-v", completed=True, minutes_ago=60)
        add_progress(session, "viewer", "show-s1e2-v", completed=True, minutes_ago=5)
    client = api_client(user=viewer, base_url="http://localhost")

    assert client.post("/api/titles/show/next-up/dismiss").status_code == 204
    with db_factory() as session:
        dismissed = {row.item_id: row.dismissed_at is not None for row in session.query(PlaybackProgress)}
    assert dismissed == {"show-s1e1-v": False, "show-s1e2-v": True}
    assert client.post("/api/titles/show-s1e1/next-up/dismiss").status_code == 404  # an episode is not a series


def test_next_up_dismiss_skips_a_season_0_special_even_when_it_is_the_latest_checkpoint(db_factory, api_client) -> None:
    """Ruling G-F2: the anchor must match B's resume_anchor (regular seasons only, completed or position > 0)."""
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_series(session, "show", "Show", seasons={1: 1, 0: 1})
        add_progress(session, "viewer", "show-s1e1-v", completed=True, minutes_ago=60)
        add_progress(session, "viewer", "show-s0e1-v", completed=True, minutes_ago=1)
    client = api_client(user=viewer, base_url="http://localhost")

    assert client.post("/api/titles/show/next-up/dismiss").status_code == 204
    with db_factory() as session:
        dismissed = {row.item_id: row.dismissed_at is not None for row in session.query(PlaybackProgress)}
    assert dismissed == {"show-s1e1-v": True, "show-s0e1-v": False}


def test_a_dismissed_anchor_hides_the_series_from_next_up_until_replay(db_factory, api_client) -> None:
    """next_up_titles passes the anchor row's dismissed state; replaying undoes it."""
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_series(session, "show", "Show", seasons={1: 3})
        add_series(session, "other", "Other", seasons={1: 2})
        add_progress(session, "viewer", "show-s1e1-v", completed=True, minutes_ago=60)
        add_progress(session, "viewer", "show-s1e2-v", completed=True, minutes_ago=5)
        add_progress(session, "viewer", "other-s1e1-v", completed=True, minutes_ago=30)
    client = api_client(user=viewer, base_url="http://localhost")

    def up_next() -> list[str]:
        return [title["id"] for title in client.get("/api/titles/next-up").json()]

    assert up_next() == ["show-s1e3", "other-s1e2"]
    assert client.post("/api/titles/show/next-up/dismiss").status_code == 204
    assert up_next() == ["other-s1e2"]
    replay = client.put("/api/library/show-s1e2-v/playback", json={"position_seconds": 0, "duration_seconds": 600, "completed": True})
    assert replay.status_code == 200, replay.text
    assert up_next() == ["show-s1e3", "other-s1e2"]


def test_the_playback_put_response_carries_the_titled_items_title_summary(db_factory, api_client) -> None:
    """P-I1: the hero and Continue rely on the PUT response's ``title`` to pick and caption the right entry."""
    viewer = make_user("viewer")
    with db_factory.begin() as session:
        session.add(viewer)
        add_movie(session, "film", "Film")
    client = api_client(user=viewer, base_url="http://localhost")

    saved = client.put("/api/library/film-v/playback", json={"position_seconds": 120, "duration_seconds": 600, "completed": False})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["title"] is not None
    assert body["title"]["id"] == "film"
    assert body["title"]["name"] == "Film"
