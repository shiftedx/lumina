"""Ranked refs must invalidate with SQLite content and caller visibility changes."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import LibraryItem, MediaTitle, User
from app.services import jellyfin_discovery, member_access
from app.services.library_search import ensure_search_index
from support import seed_app_settings


def test_ranked_refs_reuse_unchanged_sqlite_data_and_never_cache_dtos(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = create_engine(f"sqlite:///{tmp_path / 'search.db'}", pool_use_lifo=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        seed_app_settings(db)
        source = User(id="reader", username="reader", role="viewer", display_name="Reader", is_active=True)
        db.add_all([
            source,
            User(id="owner", username="owner", role="viewer", display_name="Owner", is_active=True),
            MediaTitle(id="movie", type="movie", key="test:cache", name="Cache Aurora", category="movies"),
            LibraryItem(id="file", title="Cache Aurora", title_id="movie", user_id="owner", visibility="shared", status="available"),
        ])
        db.commit()
        ensure_search_index(engine)
        reader = member_access.carry_access(db, User(id=source.id, role=source.role), source)
        calls = []
        original = jellyfin_discovery.search_ids

        def counted(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            calls.append(True)
            return original(*args, **kwargs)

        monkeypatch.setattr(jellyfin_discovery, "search_ids", counted)
        first = jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200)
        assert first == ["movie"]
        first.clear()
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == ["movie"]
        assert len(calls) == 1, "unchanged data reuses ranked refs, returning a fresh list"

        # A commit on another connection changes this connection's data_version.
        with factory() as writer:
            writer.get(LibraryItem, "file").visibility = "private"
            writer.commit()
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == []
        assert len(calls) == 2
        owner = User(id="owner", role="viewer")
        owner.__dict__[member_access.ACCESS_ATTR] = None
        assert jellyfin_discovery.search_refs(db, owner, "aurora", {"movie"}, 200) == ["movie"]
        assert len(calls) == 3

        # Writes by this same connection do not change data_version; total_changes does.
        db.get(LibraryItem, "file").visibility = "shared"
        db.commit()
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == ["movie"]
        assert len(calls) == 4

        restricted = User(id=reader.id, role="viewer")
        restricted.__dict__[member_access.ACCESS_ATTR] = member_access.EffectiveAccess(sections=frozenset({"shows"}))
        assert jellyfin_discovery.search_refs(db, restricted, "aurora", {"movie"}, 200) == []
        assert len(calls) == 5

        from sqlalchemy import update
        raw = db.connection().connection.driver_connection
        db.execute(update(LibraryItem).where(LibraryItem.id == "file").values(visibility="private"))
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == []
        db.rollback()
        assert db.connection().connection.driver_connection is raw
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == ["movie"]

        for index in range(jellyfin_discovery.SEARCH_CACHE_ENTRIES + 5):
            assert jellyfin_discovery.search_refs(db, reader, f"absent-{index}", {"movie"}, 200) == []
        assert len(db.connection().info[jellyfin_discovery._SEARCH_CACHE][1]) == jellyfin_discovery.SEARCH_CACHE_ENTRIES
        # Unflushed ORM writes are never hidden by a cache hit.
        db.get(LibraryItem, "file").title = "Pending edit"
        before = len(calls)
        jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200)
        jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200)
        assert len(calls) == before + 2
        db.rollback()

    engine.dispose()


def test_model_backed_and_adapted_searches_are_not_cached(monkeypatch) -> None:  # noqa: ANN001
    from app.services import embeddings
    from app.services.semantic_discovery import SearchRef, discovery
    from support import memory_session_factory

    with memory_session_factory()() as db:
        seed_app_settings(db)
        reader = User(id="reader", role="viewer")
        reader.__dict__[member_access.ACCESS_ATTR] = None
        calls = []

        def changing(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            ref = f"result-{len(calls)}"
            calls.append(ref)
            return [SearchRef(kind="title", id=ref, title_id=ref, start_ms=None)]

        monkeypatch.setattr(jellyfin_discovery, "search_ids", changing)
        monkeypatch.setattr(embeddings, "serving", lambda _db: object())
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == ["result-0"]
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == ["result-1"]
        monkeypatch.setattr(embeddings, "serving", lambda _db: None)
        monkeypatch.setattr(discovery, "_encoder", object())
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == ["result-2"]
        assert jellyfin_discovery.search_refs(db, reader, "aurora", {"movie"}, 200) == ["result-3"]
