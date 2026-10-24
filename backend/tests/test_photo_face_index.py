"""Scalable face-index contracts at the SQLite/service boundary."""
from __future__ import annotations

from datetime import timedelta
import hashlib
from pathlib import Path
import threading

import numpy as np
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import utcnow
from app.photo_face_index_models import PhotoFaceIndexVector
from app.photo_face_models import PhotoFace, PhotoPerson
from app.photo_models import PhotoItem
from app.persistence import write_transaction
from app.services import photo_face_index


PIPELINE = "face-index-test"


def _embedding(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    value = rng.normal(size=128).astype(np.float32)
    value /= np.linalg.norm(value)
    return value.tobytes()


def _photo(photo_id: str, owner: str = "owner") -> PhotoItem:
    return PhotoItem(
        id=photo_id, owner_user_id=owner, visibility="private", storage_kind="managed",
        storage_key=f"originals/{owner}/{photo_id}.jpg", original_filename=f"{photo_id}.jpg",
        content_type="image/jpeg", sha256=hashlib.sha256(f"{owner}:{photo_id}".encode()).hexdigest(),
        bytes=100, width=100, height=100, orientation=1, metadata_json={},
    )


def _face(face_id: str, photo_id: str, embedding: bytes, *, owner: str = "owner", person: str | None = None,
          pipeline: str = PIPELINE, updated_at=None) -> PhotoFace:  # noqa: ANN001
    return PhotoFace(
        id=face_id, photo_id=photo_id, owner_user_id=owner, person_id=person,
        pipeline_signature=pipeline, detector_score=.9, x1=.1, y1=.1, x2=.8, y2=.8,
        embedding=embedding, assignment_source="automatic", updated_at=updated_at or utcnow(),
    )


def test_schema_triggers_version_vectors_without_reusing_native_keys(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    with db_factory() as db:
        db.add(_photo("photo"))
        row = _face("face", "photo", _embedding(1))
        db.add(row)
        db.commit()
        first = db.scalar(select(PhotoFaceIndexVector).where(PhotoFaceIndexVector.face_id == "face"))
        assert first is not None and first.active

        row.embedding = _embedding(2)
        db.commit()
        versions = list(db.scalars(select(PhotoFaceIndexVector).where(
            PhotoFaceIndexVector.face_id == "face"
        ).order_by(PhotoFaceIndexVector.id).execution_options(populate_existing=True)))
        assert [value.active for value in versions] == [False, True]
        assert versions[1].id > versions[0].id


def test_bounded_candidate_queries_start_from_primary_keys(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        exact_plan = " ".join(row[3] for row in db.execute(text("""
          EXPLAIN QUERY PLAN SELECT id FROM photo_face_index_vectors NOT INDEXED
          WHERE rowid IN (1,2)
        """)))
        face_plan = " ".join(row[3] for row in db.execute(text("""
          EXPLAIN QUERY PLAN SELECT v.id FROM photo_faces f INDEXED BY sqlite_autoindex_photo_faces_1
          CROSS JOIN photo_face_index_vectors v INDEXED BY ix_photo_face_index_vectors_face
            ON v.face_id=f.id AND v.active=1 WHERE f.id IN ('a','b')
        """)))
        prototype_plan = " ".join(row[3] for row in db.execute(text("""
          EXPLAIN QUERY PLAN WITH target(person_id) AS MATERIALIZED (SELECT 'person')
          SELECT (SELECT f.id FROM photo_faces f INDEXED BY ix_photo_faces_ann_prototype
            JOIN photo_items i ON i.id=f.photo_id
            WHERE f.owner_user_id='owner' AND f.pipeline_signature=:pipeline
              AND f.person_id=target.person_id AND i.deleted_at IS NULL AND i.missing_at IS NULL
            ORDER BY f.updated_at DESC,f.id ASC LIMIT 1)
          FROM target
        """), {"pipeline": PIPELINE}))
        assert "SEARCH photo_face_index_vectors USING INTEGER PRIMARY KEY" in exact_plan
        assert "SEARCH f USING" in face_plan and "ix_photo_face_index_vectors_face" in face_plan
        assert "ix_photo_faces_ann_prototype" in prototype_plan
        assert "CORRELATED SCALAR SUBQUERY" in prototype_plan


def test_already_canceled_build_publishes_no_scope(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    cancel = threading.Event()
    cancel.set()
    with db_factory() as db:
        db.add_all([_photo("photo"), _face("face", "photo", _embedding(3))])
        db.commit()
        with pytest.raises(photo_face_index.IndexInterrupted):
            photo_face_index.prepare_candidates(db, "owner", PIPELINE, [_embedding(3)], cancel=cancel)
    assert not photo_face_index._scopes  # noqa: SLF001


def test_canceled_mid_replay_drops_partial_scope_and_retry_is_current(db_factory, monkeypatch) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    target = _embedding(4)
    cancel = threading.Event()
    with db_factory() as db:
        db.add_all([_photo("photo"), _face("face", "photo", target)])
        db.commit()
        photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])
        db.add(PhotoPerson(id="person", owner_user_id="owner", pipeline_signature=PIPELINE))
        row = db.get(PhotoFace, "face")
        assert row is not None
        row.person_id = "person"
        db.commit()

        original = photo_face_index._current_rows  # noqa: SLF001

        def interrupt(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            cancel.set()
            raise photo_face_index.IndexInterrupted("test")

        monkeypatch.setattr(photo_face_index, "_current_rows", interrupt)
        with pytest.raises(photo_face_index.IndexInterrupted):
            photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target], cancel=cancel)
        assert not photo_face_index._scopes  # noqa: SLF001

        monkeypatch.setattr(photo_face_index, "_current_rows", original)
        cancel.clear()
        retried = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert retried.prototype_face_ids == ("face",)
        assert retried.unassigned_face_ids == ()


def test_candidates_cover_old_people_and_keep_one_latest_prototype(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    now = utcnow()
    with db_factory() as db:
        for index in range(80):
            person_id = f"person-{index:03d}"
            photo_id = f"photo-{index:03d}"
            db.add_all([
                PhotoPerson(id=person_id, owner_user_id="owner", pipeline_signature=PIPELINE),
                _photo(photo_id),
                _face(f"face-{index:03d}", photo_id, _embedding(index), person=person_id,
                      updated_at=now + timedelta(seconds=index)),
            ])
        db.add(_photo("photo-newer"))
        db.add(_face("face-newer", "photo-newer", _embedding(0), person="person-000", updated_at=now + timedelta(days=1)))
        db.commit()

        result = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [_embedding(0)], prototype_limit=80)[0]
        assert "face-newer" in result.prototype_face_ids
        assert "face-000" not in result.prototype_face_ids


def test_three_owner_scopes_remain_resident_under_default_budget(db_factory, monkeypatch) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    original = photo_face_index._build  # noqa: SLF001
    builds: list[str] = []

    def tracked(db, owner, pipeline, cancel=None):  # noqa: ANN001, ANN202
        builds.append(owner)
        return original(db, owner, pipeline, cancel)

    monkeypatch.setattr(photo_face_index, "_build", tracked)
    with db_factory() as db:
        for owner in ("owner-a", "owner-b", "owner-c"):
            db.add_all([_photo(f"photo-{owner}", owner), _face(
                f"face-{owner}", f"photo-{owner}", _embedding(len(builds) + 20), owner=owner,
            )])
        db.commit()
        for owner in ("owner-a", "owner-b", "owner-c", "owner-a"):
            photo_face_index.prepare_candidates(db, owner, PIPELINE, [_embedding(20)])

    assert builds == ["owner-a", "owner-b", "owner-c"]


def test_scope_cache_evicts_lru_under_soft_byte_budget(db_factory, monkeypatch) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    one_scope = photo_face_index.SCOPE_ESTIMATE_BASE_BYTES + photo_face_index.VECTOR_ESTIMATE_BYTES
    monkeypatch.setattr(photo_face_index.settings, "photo_face_index_cache_max_bytes", one_scope * 2)
    original = photo_face_index._build  # noqa: SLF001
    builds: list[str] = []

    def tracked(db, owner, pipeline, cancel=None):  # noqa: ANN001, ANN202
        builds.append(owner)
        return original(db, owner, pipeline, cancel)

    monkeypatch.setattr(photo_face_index, "_build", tracked)
    with db_factory() as db:
        for index, owner in enumerate(("owner-a", "owner-b", "owner-c")):
            db.add_all([_photo(f"photo-{owner}", owner), _face(
                f"face-{owner}", f"photo-{owner}", _embedding(index + 30), owner=owner,
            )])
        db.commit()
        for owner in ("owner-a", "owner-b", "owner-c", "owner-a"):
            photo_face_index.prepare_candidates(db, owner, PIPELINE, [_embedding(30)])

    assert builds == ["owner-a", "owner-b", "owner-c", "owner-a"]
    assert len(photo_face_index._scopes) == 2  # noqa: SLF001


def test_latest_prototype_probe_is_bounded_for_a_prolific_person(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    with db_factory() as db:
        db.execute(text("""
          INSERT INTO photo_people
            (id,owner_user_id,pipeline_signature,name,hidden,revision,created_at,updated_at)
          VALUES ('person','owner',:pipeline,NULL,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),
                 ('person-small','owner',:pipeline,NULL,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """), {"pipeline": PIPELINE})
        db.execute(text("""
          WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq WHERE x<20000)
          INSERT INTO photo_items
            (id,owner_user_id,visibility,storage_kind,storage_key,original_filename,content_type,
             sha256,bytes,width,height,orientation,metadata_json,created_at,updated_at)
          SELECT printf('bulk-photo-%06d',x),'owner','private','managed',printf('bulk/%06d',x),
            'bulk.jpg','image/jpeg',printf('%064x',x),1,1,1,1,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
          FROM seq
        """))
        db.execute(text("""
          WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq WHERE x<20000)
          INSERT INTO photo_faces
            (id,photo_id,owner_user_id,person_id,pipeline_signature,detector_score,x1,y1,x2,y2,
             embedding,assignment_source,created_at,updated_at)
          SELECT printf('bulk-face-%06d',x),printf('bulk-photo-%06d',x),'owner','person',:pipeline,
            .9,.1,.1,.8,.8,:embedding,'automatic',CURRENT_TIMESTAMP,
            datetime('2020-01-01','+' || x || ' seconds') FROM seq
        """), {"pipeline": PIPELINE, "embedding": _embedding(5)})
        db.execute(text("""
          WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq WHERE x<2000)
          INSERT INTO photo_items
            (id,owner_user_id,visibility,storage_kind,storage_key,original_filename,content_type,
             sha256,bytes,width,height,orientation,metadata_json,created_at,updated_at)
          SELECT printf('small-photo-%06d',x),'owner','private','managed',printf('small/%06d',x),
            'small.jpg','image/jpeg',printf('%064x',x+20000),1,1,1,1,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
          FROM seq
        """))
        db.execute(text("""
          WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq WHERE x<2000)
          INSERT INTO photo_faces
            (id,photo_id,owner_user_id,person_id,pipeline_signature,detector_score,x1,y1,x2,y2,
             embedding,assignment_source,created_at,updated_at)
          SELECT printf('small-face-%06d',x),printf('small-photo-%06d',x),'owner','person-small',:pipeline,
            .9,.1,.1,.8,.8,:embedding,'automatic',CURRENT_TIMESTAMP,
            datetime('2020-01-01','+' || x || ' seconds') FROM seq
        """), {"pipeline": PIPELINE, "embedding": _embedding(6)})
        db.commit()

        driver = db.connection().connection.driver_connection

        def measured(person_id: str) -> tuple[object, int]:
            steps = 0

            def progress() -> int:
                nonlocal steps
                steps += 1
                return 1 if steps > 50 else 0

            driver.set_progress_handler(progress, 1000)
            try:
                row = db.execute(photo_face_index._PROTOTYPES, {  # noqa: SLF001
                    "owner": "owner", "pipeline": PIPELINE, "people": [person_id],
                }).one()
            finally:
                driver.set_progress_handler(None, 0)
            return row, steps

        small, small_steps = measured("person-small")
        large, large_steps = measured("person")
        assert small.face_id == "small-face-002000"
        assert large.face_id == "bulk-face-020000"
        assert large_steps <= small_steps + 5

        latest_vector_id = db.execute(text("""
          SELECT v.id FROM photo_face_index_vectors v
          JOIN photo_faces f ON f.id=v.face_id
          WHERE f.id='bulk-face-020000' AND v.active=1
        """)).scalar_one()
        query = np.frombuffer(_embedding(5), dtype=np.float32)
        exact, exact_steps = None, 0

        def exact_progress() -> int:
            nonlocal exact_steps
            exact_steps += 1
            return 1 if exact_steps > 50 else 0

        driver.set_progress_handler(exact_progress, 1000)
        try:
            exact = photo_face_index._exact(  # noqa: SLF001
                db, "owner", PIPELINE, [latest_vector_id], query, prototype=True, limit=1,
            )
        finally:
            driver.set_progress_handler(None, 0)
        assert exact == ("bulk-face-020000",)
        assert exact_steps <= 50


def test_owner_pipeline_live_state_and_assignment_changes_are_replayed(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    target = _embedding(7)
    with db_factory() as db:
        db.add_all([_photo("ours"), _photo("theirs", "other")])
        db.add_all([
            _face("ours", "ours", target),
            _face("wrong-owner", "theirs", target, owner="other"),
            _face("wrong-pipeline", "ours", target, pipeline="other-pipeline"),
        ])
        db.commit()
        result = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert result.unassigned_face_ids == ("ours",)

        db.add(PhotoPerson(id="person", owner_user_id="owner", pipeline_signature=PIPELINE))
        ours = db.get(PhotoFace, "ours")
        assert ours is not None
        ours.person_id = "person"
        db.commit()
        result = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert result.prototype_face_ids == ("ours",)
        assert result.unassigned_face_ids == ()

        item = db.get(PhotoItem, "ours")
        assert item is not None
        item.deleted_at = utcnow()
        db.commit()
        result = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert result.prototype_face_ids == ()


def test_nonstructural_updates_do_not_duplicate_native_vector_ids(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    target = _embedding(8)
    with db_factory() as db:
        db.add_all([_photo("photo"), _face("face", "photo", target)])
        db.commit()
        photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])
        scope = next(iter(photo_face_index._scopes.values()))  # noqa: SLF001
        assert scope.unassigned.ntotal == 1
        for offset in range(10):
            row = db.get(PhotoFace, "face")
            assert row is not None
            row.updated_at = utcnow() + timedelta(seconds=offset)
            db.commit()
        result = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert result.unassigned_face_ids == ("face",)
        assert scope.unassigned.ntotal == 1
        assert scope.stale == 0


def test_more_than_4096_closer_tombstones_cannot_hide_a_live_candidate(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    query = np.zeros(128, dtype=np.float32)
    query[12] = 1
    live = query.copy()
    live[13] = .7
    live /= np.linalg.norm(live)
    with db_factory() as db:
        db.add_all([_photo("live-photo"), _face("live-face", "live-photo", live.tobytes())])
        db.commit()
        live_vector_id = db.execute(text(
            "SELECT id FROM photo_face_index_vectors WHERE face_id='live-face'"
        )).scalar_one()
        graph = photo_face_index._index()  # noqa: SLF001
        known: dict[int, int] = {}
        # These graph keys deliberately have no authoritative face row. They
        # represent deleted/corrected members that all rank ahead of the live key.
        stale = 4_100
        rng = np.random.default_rng(88)
        stale_vectors = query + rng.normal(0, .01, (stale, 128)).astype(np.float32)
        stale_vectors /= np.linalg.norm(stale_vectors, axis=1, keepdims=True)
        rows = [(live_vector_id + offset + 1, value.tobytes()) for offset, value in enumerate(stale_vectors)]
        rows.append((live_vector_id, live.tobytes()))
        photo_face_index._add(graph, known, rows)  # noqa: SLF001
        scope = photo_face_index._Scope(  # noqa: SLF001
            "owner", PIPELINE, photo_face_index._index(), graph, {}, known, {}, {"live-face": live_vector_id},
            stale=stale,
        )
        found = photo_face_index._search(db, scope, query, graph, 1, prototype=False)  # noqa: SLF001
        assert found == ("live-face",)


def test_restart_rebuild_and_owner_erasure_leave_no_derived_biometrics(db_factory) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    target = _embedding(9)
    with db_factory() as db:
        db.add(_photo("photo"))
        db.add(_face("face", "photo", target))
        db.commit()
        assert photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0].unassigned_face_ids == ("face",)
        photo_face_index.reset_for_tests()
        assert photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0].unassigned_face_ids == ("face",)

        photo_face_index.delete_owner_data(db, "owner")
        db.commit()
        assert db.execute(text("SELECT count(*) FROM photo_face_index_vectors WHERE owner_user_id='owner'")).scalar_one() == 0
        assert db.execute(text("""
          SELECT count(*) FROM photo_face_index_changes
          WHERE old_owner_user_id='owner' OR new_owner_user_id='owner'
        """)).scalar_one() == 0


def test_validated_checkpoint_reloads_and_corruption_falls_back_to_sqlite(tmp_path) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    engine = create_engine(f"sqlite:///{tmp_path / 'faces.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    target = _embedding(11)
    with factory() as db:
        db.add_all([_photo("photo"), _face("face", "photo", target)])
        db.commit()
        assert photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0].unassigned_face_ids == ("face",)
        snapshot = db.execute(text("SELECT unassigned_path FROM photo_face_index_snapshots")).scalar_one()
        db.add_all([_photo("photo-delta"), _face("face-delta", "photo-delta", target)])
        db.commit()

    photo_face_index.reset_for_tests()
    with factory() as db:
        restarted = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert set(restarted.unassigned_face_ids) == {"face", "face-delta"}
        scope = next(iter(photo_face_index._scopes.values()))  # noqa: SLF001
        assert scope.unassigned.ntotal == 2 and scope.stale == 0
        db.delete(db.get(PhotoFace, "face"))
        db.commit()
        after_delete = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert after_delete.unassigned_face_ids == ("face-delta",)
        assert scope.stale == 1
        photo_face_index._checkpoint(scope, engine)  # noqa: SLF001 - force a stale-generation restart fixture
        snapshot = db.execute(text("SELECT unassigned_path FROM photo_face_index_snapshots")).scalar_one()
    photo_face_index.reset_for_tests()
    with factory() as db:
        after_restart = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        scope = next(iter(photo_face_index._scopes.values()))  # noqa: SLF001
        assert after_restart.unassigned_face_ids == ("face-delta",)
        assert scope.stale == 1
    with open(snapshot, "wb") as stream:
        stream.write(b"corrupt")
    photo_face_index.reset_for_tests()
    with factory() as db:
        rebuilt = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert rebuilt.unassigned_face_ids == ("face-delta",)
    engine.dispose()


def test_idle_settle_checkpoints_the_final_face_for_restart(tmp_path) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    engine = create_engine(f"sqlite:///{tmp_path / 'settle.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    target = _embedding(14)
    with factory() as db:
        db.add_all([_photo("first"), _face("first", "first", target)])
        db.commit()
        photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])
        db.add_all([_photo("final"), _face("final", "final", target)])
        db.commit()
        assert photo_face_index.settle_pending(db) == 1
        assert db.execute(text("SELECT watermark FROM photo_face_index_snapshots")).scalar_one() > 0
    photo_face_index.reset_for_tests()
    with factory() as db:
        restarted = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert set(restarted.unassigned_face_ids) == {"first", "final"}
    engine.dispose()


def test_idle_settle_removes_old_snapshot_when_scope_becomes_empty(tmp_path) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    target = _embedding(17)
    with factory() as db:
        db.add_all([_photo("photo"), _face("face", "photo", target)])
        db.commit()
        photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])
        paths = [value for row in db.execute(text(
            "SELECT prototype_path,unassigned_path FROM photo_face_index_snapshots"
        )) for value in row]
        db.delete(db.get(PhotoFace, "face"))
        db.commit()
        assert photo_face_index.settle_pending(db) == 1
        assert db.execute(text("SELECT count(*) FROM photo_face_index_snapshots")).scalar_one() == 0
    assert all(not Path(value).exists() for value in paths)
    engine.dispose()


def test_disk_pressure_skips_optional_checkpoint_without_losing_candidates(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    engine = create_engine(f"sqlite:///{tmp_path / 'pressure.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    real_disk_usage = photo_face_index.shutil.disk_usage
    monkeypatch.setattr(photo_face_index.shutil, "disk_usage", lambda _path: type("Usage", (), {"free": 0})())
    target = _embedding(15)
    with factory() as db:
        db.add_all([_photo("photo"), _face("face", "photo", target)])
        db.commit()
        result = photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])[0]
        assert result.unassigned_face_ids == ("face",)
        assert db.execute(text("SELECT count(*) FROM photo_face_index_snapshots")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM photo_face_index_changes")).scalar_one() > 0
        # A different owner's successful checkpoint must not prune the dirty
        # resident scope that could not persist under pressure.
        monkeypatch.setattr(photo_face_index.shutil, "disk_usage", real_disk_usage)
        db.add_all([_photo("other-photo", "other"), _face(
            "other-face", "other-photo", target, owner="other",
        )])
        db.commit()
        photo_face_index.prepare_candidates(db, "other", PIPELINE, [target])
        assert db.execute(text("""
          SELECT count(*) FROM photo_face_index_changes
          WHERE old_owner_user_id='owner' OR new_owner_user_id='owner'
        """)).scalar_one() > 0
    engine.dispose()


def test_serialized_owner_erasure_cannot_be_undone_by_a_racing_checkpoint(tmp_path) -> None:  # noqa: ANN001
    photo_face_index.reset_for_tests()
    engine = create_engine(
        f"sqlite:///{tmp_path / 'erase.db'}", future=True, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    target = _embedding(16)
    with factory() as db:
        db.add_all([_photo("photo"), _face("face", "photo", target)])
        db.commit()
        photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])
        paths = [value for row in db.execute(text(
            "SELECT prototype_path,unassigned_path FROM photo_face_index_snapshots"
        )) for value in row]

    started = threading.Event()
    finished = threading.Event()

    def race() -> None:
        with factory() as db:
            started.set()
            photo_face_index.prepare_candidates(db, "owner", PIPELINE, [target])
        finished.set()

    with photo_face_index.serialized_index_mutation():
        thread = threading.Thread(target=race)
        thread.start()
        assert started.wait(1) and not finished.wait(.05)
        with factory() as db, write_transaction(db, name="test_face_erase"):
            db.query(PhotoFace).filter(PhotoFace.owner_user_id == "owner").delete(synchronize_session=False)
            photo_face_index.delete_owner_data(db, "owner")
    thread.join(timeout=5)
    assert finished.is_set()
    with factory() as db:
        assert db.execute(text("SELECT count(*) FROM photo_face_index_snapshots")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM photo_face_index_vectors")).scalar_one() == 0
    assert all(not Path(value).exists() for value in paths)
    engine.dispose()


def test_bulk_face_queue_uses_covering_owner_keyset_index(db_factory) -> None:  # noqa: ANN001
    with db_factory() as db:
        plan = " ".join(str(row) for row in db.execute(text("""
            EXPLAIN QUERY PLAN SELECT id,sha256 FROM photo_items
            WHERE owner_user_id='owner' AND deleted_at IS NULL AND id>'previous'
            ORDER BY id LIMIT 500
        """)))
        assert "ix_photo_items_face_queue" in plan
        assert "COVERING" in plan and "id>?" in plan
        assert "TEMP B-TREE" not in plan
