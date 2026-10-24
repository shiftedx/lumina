"""Idempotent SQLite tables and change-capture triggers for the face ANN."""
from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection

from app.db import Base


TABLE_SQL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS photo_face_index_vectors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        face_id VARCHAR(36) NOT NULL,
        owner_user_id VARCHAR(36) NOT NULL,
        pipeline_signature VARCHAR(255) NOT NULL,
        embedding BLOB NOT NULL,
        active BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_photo_face_index_vectors_scope ON photo_face_index_vectors "
    "(owner_user_id, pipeline_signature, active, id)",
    "CREATE INDEX IF NOT EXISTS ix_photo_face_index_vectors_face ON photo_face_index_vectors (face_id, active, id)",
    """CREATE TABLE IF NOT EXISTS photo_face_index_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        face_id VARCHAR(36) NOT NULL,
        old_owner_user_id VARCHAR(36),
        old_pipeline_signature VARCHAR(255),
        old_person_id VARCHAR(36),
        new_owner_user_id VARCHAR(36),
        new_pipeline_signature VARCHAR(255),
        new_person_id VARCHAR(36),
        operation VARCHAR(12) NOT NULL,
        created_at DATETIME NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_photo_face_index_changes_old_scope ON photo_face_index_changes "
    "(old_owner_user_id, old_pipeline_signature, id)",
    "CREATE INDEX IF NOT EXISTS ix_photo_face_index_changes_new_scope ON photo_face_index_changes "
    "(new_owner_user_id, new_pipeline_signature, id)",
    "CREATE INDEX IF NOT EXISTS ix_photo_faces_ann_prototype ON photo_faces "
    "(owner_user_id, pipeline_signature, person_id, updated_at DESC, id)",
    "CREATE INDEX IF NOT EXISTS ix_photo_faces_ann_unassigned ON photo_faces "
    "(owner_user_id, pipeline_signature, assignment_source, person_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_photo_face_jobs_priority ON photo_face_jobs "
    "(state, requested DESC, updated_at, id)",
    """CREATE TABLE IF NOT EXISTS photo_face_index_snapshots (
        owner_user_id VARCHAR(36) NOT NULL,
        pipeline_signature VARCHAR(255) NOT NULL,
        generation VARCHAR(64) NOT NULL,
        watermark INTEGER NOT NULL,
        runtime_version VARCHAR(32) NOT NULL,
        dimensions INTEGER NOT NULL,
        metric VARCHAR(16) NOT NULL,
        hnsw_m INTEGER NOT NULL,
        ef_construction INTEGER NOT NULL,
        ef_search INTEGER NOT NULL,
        prototype_path TEXT NOT NULL,
        prototype_size INTEGER NOT NULL,
        prototype_sha256 VARCHAR(64) NOT NULL,
        unassigned_path TEXT NOT NULL,
        unassigned_size INTEGER NOT NULL,
        unassigned_sha256 VARCHAR(64) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (owner_user_id, pipeline_signature)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_photo_face_index_vectors_inactive ON photo_face_index_vectors (active, id)",
    "CREATE INDEX IF NOT EXISTS ix_photo_people_ann_scope ON photo_people "
    "(owner_user_id, pipeline_signature, redirected_to_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_photo_items_face_queue ON photo_items "
    "(owner_user_id, deleted_at, id, sha256)",
)

TRIGGER_SQL: tuple[str, ...] = (
    """CREATE TRIGGER IF NOT EXISTS trg_photo_face_index_insert
    AFTER INSERT ON photo_faces
    BEGIN
      INSERT INTO photo_face_index_vectors
        (face_id, owner_user_id, pipeline_signature, embedding, active, created_at)
      VALUES (NEW.id, NEW.owner_user_id, NEW.pipeline_signature, NEW.embedding, 1, CURRENT_TIMESTAMP);
      INSERT INTO photo_face_index_changes
        (face_id, new_owner_user_id, new_pipeline_signature, new_person_id, operation, created_at)
      VALUES (NEW.id, NEW.owner_user_id, NEW.pipeline_signature, NEW.person_id, 'insert', CURRENT_TIMESTAMP);
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_photo_face_index_update
    AFTER UPDATE OF owner_user_id, pipeline_signature, person_id, assignment_source, embedding, photo_id, updated_at
    ON photo_faces
    WHEN OLD.owner_user_id IS NOT NEW.owner_user_id
      OR OLD.pipeline_signature IS NOT NEW.pipeline_signature
      OR OLD.person_id IS NOT NEW.person_id
      OR OLD.assignment_source IS NOT NEW.assignment_source
      OR OLD.embedding IS NOT NEW.embedding
      OR OLD.photo_id IS NOT NEW.photo_id
      OR OLD.updated_at IS NOT NEW.updated_at
    BEGIN
      UPDATE photo_face_index_vectors SET active = 0
       WHERE face_id = OLD.id AND active = 1
         AND (OLD.owner_user_id IS NOT NEW.owner_user_id
           OR OLD.pipeline_signature IS NOT NEW.pipeline_signature
           OR OLD.embedding IS NOT NEW.embedding);
      INSERT INTO photo_face_index_vectors
        (face_id, owner_user_id, pipeline_signature, embedding, active, created_at)
      SELECT NEW.id, NEW.owner_user_id, NEW.pipeline_signature, NEW.embedding, 1, CURRENT_TIMESTAMP
       WHERE OLD.owner_user_id IS NOT NEW.owner_user_id
          OR OLD.pipeline_signature IS NOT NEW.pipeline_signature
          OR OLD.embedding IS NOT NEW.embedding;
      INSERT INTO photo_face_index_changes
        (face_id, old_owner_user_id, old_pipeline_signature, old_person_id,
         new_owner_user_id, new_pipeline_signature, new_person_id, operation, created_at)
      VALUES (NEW.id, OLD.owner_user_id, OLD.pipeline_signature, OLD.person_id,
              NEW.owner_user_id, NEW.pipeline_signature, NEW.person_id, 'update', CURRENT_TIMESTAMP);
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_photo_face_index_delete
    AFTER DELETE ON photo_faces
    BEGIN
      UPDATE photo_face_index_vectors SET active = 0 WHERE face_id = OLD.id AND active = 1;
      INSERT INTO photo_face_index_changes
        (face_id, old_owner_user_id, old_pipeline_signature, old_person_id, operation, created_at)
      VALUES (OLD.id, OLD.owner_user_id, OLD.pipeline_signature, OLD.person_id, 'delete', CURRENT_TIMESTAMP);
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_photo_face_index_photo_update
    AFTER UPDATE OF owner_user_id, deleted_at, missing_at ON photo_items
    WHEN OLD.owner_user_id IS NOT NEW.owner_user_id
      OR OLD.deleted_at IS NOT NEW.deleted_at
      OR OLD.missing_at IS NOT NEW.missing_at
    BEGIN
      INSERT INTO photo_face_index_changes
        (face_id, old_owner_user_id, old_pipeline_signature, old_person_id,
         new_owner_user_id, new_pipeline_signature, new_person_id, operation, created_at)
      SELECT f.id, f.owner_user_id, f.pipeline_signature, f.person_id,
             f.owner_user_id, f.pipeline_signature, f.person_id, 'photo', CURRENT_TIMESTAMP
        FROM photo_faces f WHERE f.photo_id = NEW.id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_photo_face_index_photo_delete
    AFTER DELETE ON photo_items
    BEGIN
      INSERT INTO photo_face_index_changes
        (face_id, old_owner_user_id, old_pipeline_signature, old_person_id, operation, created_at)
      SELECT f.id, f.owner_user_id, f.pipeline_signature, f.person_id, 'photo', CURRENT_TIMESTAMP
        FROM photo_faces f WHERE f.photo_id = OLD.id;
      UPDATE photo_face_index_vectors SET active = 0
       WHERE face_id IN (SELECT id FROM photo_faces WHERE photo_id = OLD.id) AND active = 1;
    END""",
)

BACKFILL_SQL = """INSERT INTO photo_face_index_vectors
    (face_id, owner_user_id, pipeline_signature, embedding, active, created_at)
SELECT f.id, f.owner_user_id, f.pipeline_signature, f.embedding, 1, CURRENT_TIMESTAMP
  FROM photo_faces f
 WHERE NOT EXISTS (
   SELECT 1 FROM photo_face_index_vectors v WHERE v.face_id = f.id AND v.active = 1
 )"""

# Existing databases need the backfill once in the numbered migration. Fresh
# databases are empty when metadata's after_create hook installs the triggers.
MIGRATION_SQL: tuple[str, ...] = (*TABLE_SQL, BACKFILL_SQL, *TRIGGER_SQL)


def ensure_schema(connection: Connection) -> None:
    """Install tables/indexes/triggers without rescanning historical faces."""
    tables = inspect(connection).get_table_names()
    # The shared Base is also used by isolated domain tests that intentionally
    # import only a subset of models. Always create the index's own tables, but
    # install source-table indexes/triggers only when their targets exist.
    for statement in TABLE_SQL[:6]:
        connection.exec_driver_sql(statement)
    if "photo_faces" in tables:
        connection.exec_driver_sql(TABLE_SQL[6])
        connection.exec_driver_sql(TABLE_SQL[7])
    if "photo_face_jobs" in tables:
        connection.exec_driver_sql(TABLE_SQL[8])
    connection.exec_driver_sql(TABLE_SQL[9])
    connection.exec_driver_sql(TABLE_SQL[10])
    if "photo_people" in tables:
        connection.exec_driver_sql(TABLE_SQL[11])
    if "photo_items" in tables:
        connection.exec_driver_sql(TABLE_SQL[12])
    if "photo_faces" in tables and "photo_items" in tables:
        for statement in TRIGGER_SQL:
            connection.exec_driver_sql(statement)


@event.listens_for(Base.metadata, "after_create")
def _ensure_after_create(_metadata, connection: Connection, **_kwargs) -> None:  # noqa: ANN001
    ensure_schema(connection)
