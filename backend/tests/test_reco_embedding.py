"""Remote video documents in the embedding backfill: the vector lives on remote_media."""
from __future__ import annotations

import hashlib
import time
from array import array
from datetime import timedelta

import pytest

from app import db as db_module
from app.models import AppSettings, RemoteMedia, SearchEmbedding, utcnow
from app.services import embeddings
from app.services.yt_dlp_service import YtDlpService
from support import memory_session_factory
from test_v1_ai_config import MODEL, FakeAi

EMBED_MODEL = "fake-embed"
TEXT = "A harbour crane restored — Alpha Channel"


@pytest.fixture(autouse=True)
def _fresh_embedding_caches():
    embeddings.reset_caches()
    yield
    embeddings.reset_caches()


@pytest.fixture
def fake_ai():
    fake = FakeAi()

    def handler(method, path, body):  # noqa: ANN001
        if path.endswith("/embeddings"):
            return 200, {"data": [{"index": i, "embedding": [3.0, 4.0]} for i, _ in enumerate(body["input"])]}, {}
        return 200, {"data": [{"id": MODEL}]}, {}

    fake.handler = handler
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def add_media(session, key: str, *, title: str | None = "A harbour crane restored", uploader: str | None = "Alpha Channel", days: float = 0, **fields) -> RemoteMedia:
    row = RemoteMedia(
        key=key, source_identity=f"youtube:{key}", webpage_url=f"https://www.youtube.com/watch?v={key}", title=title, uploader=uploader,
        tokens=[], category_keys=[], fetched_at=utcnow(), last_nominated_at=utcnow() - timedelta(days=days), **fields,
    )
    session.add(row)
    return row


def _embedding_posts(fake) -> int:  # noqa: ANN001
    return len([path for _method, path, _headers, _body in fake.requests if path == "/v1/embeddings"])


def _wait_for_drain() -> None:
    deadline = time.monotonic() + 10
    while embeddings._draining.locked():
        assert time.monotonic() < deadline, "embedding backfill did not finish"
        time.sleep(0.01)


def test_remote_documents_are_recent_titled_rows_newest_first() -> None:
    session = memory_session_factory()()
    add_media(session, "new1", days=0)
    add_media(session, "new2", days=1, uploader=None)
    add_media(session, "stale", days=20)  # outside the 14-day window: a sweep candidate, not an embedding one
    add_media(session, "done", days=0, vector_model=EMBED_MODEL, vector_signature="x", vector=b"\x00" * 8)
    add_media(session, "other", days=2, vector_model="another-model", vector_signature="x", vector=b"\x00" * 8)  # lazily re-embedded
    add_media(session, "untitled", days=0, title=None)
    session.commit()

    documents = embeddings._remote_documents(session, EMBED_MODEL, 10)

    assert documents == [("new1", "remote", TEXT), ("new2", "remote", "A harbour crane restored"), ("other", "remote", TEXT)]
    assert embeddings._remote_documents(session, EMBED_MODEL, 2) == documents[:2]


def test_backfill_remote_writes_the_vector_on_the_row_and_is_idempotent(fake_ai) -> None:
    session = memory_session_factory()()
    add_media(session, "new1")
    session.commit()
    config = fake_ai.config(ai_embedding_model=EMBED_MODEL)

    assert embeddings.backfill_remote(session, config) == 1

    row = session.get(RemoteMedia, "new1")
    stored = array("f")
    stored.frombytes(row.vector)
    assert row.vector_model == EMBED_MODEL
    assert row.vector_signature == hashlib.sha256(TEXT.encode()).hexdigest()
    assert [round(value, 3) for value in stored] == [0.6, 0.8]  # unit length
    assert session.query(SearchEmbedding).count() == 0  # search_embeddings.target_id is 36 characters; remote keys are 64
    before = _embedding_posts(fake_ai)
    assert embeddings.backfill_remote(session, config) == 0
    assert _embedding_posts(fake_ai) == before


def test_a_drain_embeds_one_remote_batch_per_run(fake_ai) -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model, record.ai_embedding_model = fake_ai.url, MODEL, EMBED_MODEL
        for index in range(40):
            add_media(db, f"key{index:02d}")

    embeddings.start_backfill()
    _wait_for_drain()
    with db_module.session_scope() as db:
        assert db.query(RemoteMedia).filter(RemoteMedia.vector_model == EMBED_MODEL).count() == 32
    assert _embedding_posts(fake_ai) == 1  # one batch of 32 per maintenance cycle

    embeddings.start_backfill()
    _wait_for_drain()
    with db_module.session_scope() as db:
        assert db.query(RemoteMedia).filter(RemoteMedia.vector_model == EMBED_MODEL).count() == 40
    assert _embedding_posts(fake_ai) == 2


def test_the_semantic_switch_keeps_remote_rows_off_the_endpoint(fake_ai) -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model, record.ai_embedding_model = fake_ai.url, MODEL, EMBED_MODEL
        record.ai_features_disabled = ["semantic_search"]
        add_media(db, "key00")

    embeddings.start_backfill()
    _wait_for_drain()

    assert _embedding_posts(fake_ai) == 0
    with db_module.session_scope() as db:
        assert db.get(RemoteMedia, "key00").vector is None
        assert db.get(AppSettings, 1).ai_features_disabled == ["semantic_search"]
