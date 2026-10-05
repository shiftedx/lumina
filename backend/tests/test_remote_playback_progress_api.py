import hashlib
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.remote_playback as remote_playback_module
from app.db import Base
from app.models import RemotePlaybackProgress, User
from app.schemas import RemotePlaybackProgressUpdateRequest
from app.services.rate_limit import RATE_LIMIT_RULES, RateLimitRule
from app.services.remote_playback import RemotePlaybackProgressService


def test_remote_playback_progress_is_precise_durable_and_member_private(db_factory, api_client) -> None:
    user_a = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    user_b = User(id="user-b", username="bob", display_name="Bob", role="viewer", is_active=True)
    with db_factory.begin() as session:
        session.add_all([user_a, user_b])

    active_user = {"value": user_a}

    client = api_client(user=lambda: active_user["value"], base_url="http://localhost")
    identity = "youtube:dQw4w9WgXcQ"
    endpoint = f"/api/playback/remote/{quote(identity, safe='')}"
    saved = client.put(
        endpoint,
        json={
            "source_identity": identity,
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "A remote video",
            "uploader": "Creator",
            "artwork_url": "/api/artwork/remote/example",
            "position_seconds": 123.456,
            "duration_seconds": 600.25,
            "completed": False,
            "selected_rendition_id": "r1080",
            "checkpoint_client_id": "player-a",
            "checkpoint_sequence": 1,
            "expected_revision": 0,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["source_identity"] == identity
    assert saved.json()["position_seconds"] == 123.456
    assert saved.json()["extractor"] == "youtube"
    assert saved.json()["remote_id"] == "dQw4w9WgXcQ"

    loaded = client.get(endpoint)
    assert loaded.status_code == 200
    assert loaded.json()["selected_rendition_id"] == "r1080"

    active_user["value"] = user_b
    assert client.get(endpoint).json() is None
    other = client.put(
        endpoint,
        json={
            "source_identity": identity,
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "position_seconds": 42.25,
            "duration_seconds": 600.25,
            "completed": False,
            "checkpoint_client_id": "player-b",
            "checkpoint_sequence": 1,
            "expected_revision": 0,
        },
    )
    assert other.status_code == 200

    active_user["value"] = user_a
    assert client.get(endpoint).json()["position_seconds"] == 123.456
    cleared = client.delete(
        f"{endpoint}?checkpoint_client_id=player-a&checkpoint_sequence=2&expected_revision=1"
    )
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True
    assert client.get(endpoint).json()["cleared"] is True
    stale = client.put(
        endpoint,
        json={
            "source_identity": identity,
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "position_seconds": 150,
            "duration_seconds": 600.25,
            "completed": False,
            "checkpoint_client_id": "player-a",
            "checkpoint_sequence": 1,
            "expected_revision": 1,
        },
    )
    assert stale.status_code == 200
    assert stale.json()["cleared"] is True
    assert client.get(endpoint).json()["cleared"] is True
    assert client.delete(
        f"{endpoint}?checkpoint_client_id=player-a&checkpoint_sequence=3&expected_revision=2"
    ).status_code == 200


def test_checkpoints_key_rows_by_identity_hash_while_serializing_the_raw_identity(db_factory) -> None:
    user = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    with db_factory.begin() as session:
        session.add(user)

    identity = "youtube:hash-key-adoption"
    with db_factory.begin() as session:
        service = RemotePlaybackProgressService(session)
        service.update(
            identity,
            RemotePlaybackProgressUpdateRequest(
                source_identity=identity,
                source_url="https://www.youtube.com/watch?v=hash-key-adoption",
                position_seconds=42.0,
                checkpoint_client_id="player-a",
                checkpoint_sequence=1,
                expected_revision=0,
            ),
            user,
        )

    with db_factory() as session:
        row = session.query(RemotePlaybackProgress).one()
        assert row.source_identity == identity
        assert row.source_identity_key == hashlib.sha256(identity.encode("utf-8")).hexdigest()
        service = RemotePlaybackProgressService(session)
        found = service.get(identity, user)
        assert found is not None and found.id == row.id
        assert service.serialize(found).source_identity == identity


def test_remote_playback_accepts_encoded_url_identity_and_rejects_invalid_identity(db_factory, api_client) -> None:
    user = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    with db_factory.begin() as session:
        session.add(user)

    client = api_client(user=user, base_url="http://localhost")
    identity = "url:https://Example.com/watch?v=remote-1#ignored"
    endpoint = f"/api/playback/remote/{quote(identity, safe='')}"
    response = client.put(
        endpoint,
        json={
            "source_identity": identity,
            "source_url": "https://example.com/watch?v=remote-1",
            "position_seconds": 58.5,
            "duration_seconds": 60,
            "checkpoint_client_id": "player-a",
            "checkpoint_sequence": 1,
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["source_identity"] == "url:https://example.com/watch?v=remote-1"
    assert response.json()["completed"] is True

    invalid = client.put(
        f"/api/playback/remote/{quote('url:file:///private/video', safe='')}",
        json={
            "source_identity": "url:file:///private/video",
            "source_url": "file:///private/video",
            "position_seconds": 1,
            "checkpoint_client_id": "player-a",
            "checkpoint_sequence": 1,
            "expected_revision": 0,
        },
    )
    assert invalid.status_code == 400


def test_atomic_upsert_keeps_the_highest_sequence_from_one_player_under_concurrency(tmp_path) -> None:  # noqa: ANN001
    engine = create_engine(
        f"sqlite:///{tmp_path / 'progress.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    user = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    with session_factory.begin() as session:
        session.add(user)
    barrier = Barrier(2)

    def checkpoint(sequence: int, position: float) -> None:
        with session_factory() as session:
            barrier.wait()
            RemotePlaybackProgressService(session).update(
                "youtube:atomic",
                RemotePlaybackProgressUpdateRequest(
                    source_identity="youtube:atomic",
                    source_url="https://www.youtube.com/watch?v=atomic",
                    position_seconds=position,
                    checkpoint_client_id="same-player",
                    checkpoint_sequence=sequence,
                    expected_revision=0,
                ),
                user,
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(checkpoint, 1, 10), executor.submit(checkpoint, 2, 20)]
        for future in futures:
            future.result(timeout=10)

    with session_factory() as session:
        progress = RemotePlaybackProgressService(session).get("youtube:atomic", user)
        assert progress is not None
        assert progress.position_seconds == 20
        assert progress.checkpoint_sequence == 2


def test_atomic_clear_beats_an_older_concurrent_checkpoint_from_the_same_player(tmp_path) -> None:  # noqa: ANN001
    engine = create_engine(
        f"sqlite:///{tmp_path / 'clear.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    user = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    with session_factory.begin() as session:
        session.add(user)
    barrier = Barrier(2)

    def checkpoint() -> None:
        with session_factory() as session:
            barrier.wait()
            RemotePlaybackProgressService(session).update(
                "youtube:clear-race",
                RemotePlaybackProgressUpdateRequest(
                    source_identity="youtube:clear-race",
                    source_url="https://www.youtube.com/watch?v=clear-race",
                    position_seconds=30,
                    checkpoint_client_id="same-player",
                    checkpoint_sequence=1,
                    expected_revision=0,
                ),
                user,
            )
            session.commit()

    def clear() -> None:
        with session_factory() as session:
            barrier.wait()
            RemotePlaybackProgressService(session).clear(
                "youtube:clear-race",
                user,
                "same-player",
                2,
                0,
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(checkpoint), executor.submit(clear)]
        for future in futures:
            future.result(timeout=10)

    with session_factory() as session:
        progress = RemotePlaybackProgressService(session).get("youtube:clear-race", user)
        assert progress is not None
        assert progress.cleared is True
        assert progress.checkpoint_sequence == 2


def test_other_player_must_present_the_current_server_revision(db_factory) -> None:
    user = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    with db_factory.begin() as session:
        session.add(user)
    with db_factory.begin() as session:
        service = RemotePlaybackProgressService(session)
        first = service.update(
            "youtube:revision",
            RemotePlaybackProgressUpdateRequest(
                source_identity="youtube:revision",
                source_url="https://www.youtube.com/watch?v=revision",
                position_seconds=10,
                checkpoint_client_id="player-a",
                checkpoint_sequence=1,
                expected_revision=0,
            ),
            user,
        )
        assert first.checkpoint_revision == 1

    with db_factory.begin() as session:
        service = RemotePlaybackProgressService(session)
        stale = service.update(
            "youtube:revision",
            RemotePlaybackProgressUpdateRequest(
                source_identity="youtube:revision",
                source_url="https://www.youtube.com/watch?v=revision",
                position_seconds=20,
                checkpoint_client_id="player-b",
                checkpoint_sequence=1,
                expected_revision=0,
            ),
            user,
        )
        assert stale.position_seconds == 10
        assert stale.checkpoint_revision == 1

        accepted = service.update(
            "youtube:revision",
            RemotePlaybackProgressUpdateRequest(
                source_identity="youtube:revision",
                source_url="https://www.youtube.com/watch?v=revision",
                position_seconds=20,
                checkpoint_client_id="player-b",
                checkpoint_sequence=2,
                expected_revision=1,
            ),
            user,
        )
        assert accepted.position_seconds == 20
        assert accepted.checkpoint_revision == 2


def test_remote_playback_retention_evicts_oldest_rows_including_cleared_tombstones(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(remote_playback_module, "MAX_REMOTE_PLAYBACK_PROGRESS_RECORDS_PER_USER", 3)
    engine = create_engine(f"sqlite:///{tmp_path / 'retention.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    user = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    with session_factory.begin() as session:
        session.add(user)

    with session_factory.begin() as session:
        service = RemotePlaybackProgressService(session)
        service.update(
            "youtube:old-cleared",
            RemotePlaybackProgressUpdateRequest(
                source_identity="youtube:old-cleared",
                source_url="https://www.youtube.com/watch?v=old-cleared",
                position_seconds=10,
                checkpoint_client_id="player-a",
                checkpoint_sequence=1,
                expected_revision=0,
            ),
            user,
        )
        service.clear("youtube:old-cleared", user, "player-a", 2, 1)

    for index in range(3):
        identity = f"youtube:new-{index}"
        with session_factory.begin() as session:
            RemotePlaybackProgressService(session).update(
                identity,
                RemotePlaybackProgressUpdateRequest(
                    source_identity=identity,
                    source_url=f"https://www.youtube.com/watch?v=new-{index}",
                    position_seconds=float(index + 1),
                    checkpoint_client_id="player-a",
                    checkpoint_sequence=index + 3,
                    expected_revision=0,
                ),
                user,
            )

    with session_factory() as session:
        records = session.query(RemotePlaybackProgress).filter(RemotePlaybackProgress.user_id == user.id).all()
        assert len(records) == 3
        assert {record.source_identity for record in records} == {
            "youtube:new-0",
            "youtube:new-1",
            "youtube:new-2",
        }


def test_remote_playback_mutations_are_rate_limited_per_member(db_factory, api_client, monkeypatch) -> None:  # noqa: ANN001
    user_a = User(id="user-a", username="alice", display_name="Alice", role="viewer", is_active=True)
    user_b = User(id="user-b", username="bob", display_name="Bob", role="viewer", is_active=True)
    with db_factory.begin() as session:
        session.add_all([user_a, user_b])

    active_user = {"value": user_a}

    monkeypatch.setitem(
        RATE_LIMIT_RULES,
        "remote_playback_mutation",
        RateLimitRule(max_requests=1, window_seconds=60),
    )
    client = api_client(user=lambda: active_user["value"], base_url="http://localhost", client=("127.0.0.2", 50000))
    rotated_client = api_client(base_url="http://localhost", client=("127.0.0.3", 50000))
    other_member_client = api_client(base_url="http://localhost", client=("127.0.0.4", 50000))
    identity = "youtube:rate-limited"
    endpoint = f"/api/playback/remote/{quote(identity, safe='')}"
    payload = {
        "source_identity": identity,
        "source_url": "https://www.youtube.com/watch?v=rate-limited",
        "position_seconds": 10,
        "checkpoint_client_id": "player-a",
        "checkpoint_sequence": 1,
        "expected_revision": 0,
    }
    assert client.put(endpoint, json=payload).status_code == 200
    blocked = rotated_client.delete(
        f"{endpoint}?checkpoint_client_id=player-a&checkpoint_sequence=2&expected_revision=1"
    )
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]

    active_user["value"] = user_b
    assert other_member_client.put(endpoint, json=payload).status_code == 200
