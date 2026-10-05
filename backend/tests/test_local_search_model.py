"""The on-device search model indexes the library and answers by meaning."""
from __future__ import annotations

import time

import pytest

from app import db as db_module
from app.models import SearchEmbedding, User
from app.services import embeddings, model_supervisor
from app.services.semantic_discovery import SemanticDiscovery
from app.services.yt_dlp_service import YtDlpService
from discovery_support import add_movie
from model_support import LOOPBACK, default_pair, file_entry, fresh_models, install, model_entry, records, stub_runtime, use_catalog, wait_until  # noqa: F401
from support import make_user


@pytest.fixture
def local_search(monkeypatch, tmp_path, fresh_models):  # noqa: ANN001, F811
    record = stub_runtime(monkeypatch, tmp_path)
    catalog = use_catalog(monkeypatch, tmp_path, [
        model_entry("test-search", "search", [file_entry(LOOPBACK, "search.gguf")], default=True,
                    engine={"dimensions": 2, "pooling": None, "query_prefix": "q: ", "document_prefix": "d: "}),
        model_entry("next-search", "search", [file_entry(LOOPBACK, "next.gguf")]),
        default_pair()[1],
    ])
    db_module.init_db()
    embeddings.reset_caches()
    with db_module.session_scope() as db:
        YtDlpService(db).ensure_app_settings()
        db.add(make_user("member"))
        add_movie(db, "night", "Night of the Living", overview="The undead rise at dusk")
        add_movie(db, "picnic", "Picnic", overview="A sunny afternoon")
    yield catalog, record
    embeddings.reset_caches()


def _drain() -> None:
    embeddings.start_backfill()
    deadline = time.monotonic() + 10
    while embeddings._draining.locked():
        assert time.monotonic() < deadline, "embedding backfill did not finish"
        time.sleep(0.01)


def _starts(record) -> int:  # noqa: ANN001
    return len([entry for entry in records(record) if "argv" in entry])


def test_the_local_search_model_indexes_and_answers_by_meaning(local_search) -> None:  # noqa: ANN001
    catalog, record = local_search
    install(catalog.default_for("search"))
    _drain()
    with db_module.session_scope() as db:
        assert {(row.target_id, row.model_id) for row in db.query(SearchEmbedding)} == {
            ("night", "local:test-search"), ("picnic", "local:test-search"),
        }
        result = SemanticDiscovery().search(db, db.get(User, "member"), "zombie films", limit=5)
    assert [(match.kind, match.record_id, match.match_mode) for match in result.matches] == [("title", "night", "semantic")]
    batches = [entry["inputs"] for entry in records(record) if "inputs" in entry]
    assert all(text.startswith("d: ") for text in batches[0])  # documents carry the model's document prefix
    assert batches[-1] == ["q: zombie films"]  # the query carries its query prefix


def test_switching_models_keeps_the_old_index_until_the_new_one_is_complete(local_search) -> None:  # noqa: ANN001
    catalog, _record = local_search
    install(catalog.default_for("search"))
    _drain()
    install(catalog.get("next-search"))
    with db_module.session_scope() as db:
        YtDlpService(db).ensure_app_settings().local_search_model = "next-search"
    embeddings.reset_caches()
    with db_module.session_scope() as db:
        assert embeddings.target(db).model_id == "local:next-search"
        assert embeddings.serving(db).model_id == "local:test-search"  # the new index is still empty
    _drain()
    with db_module.session_scope() as db:
        assert embeddings.serving(db).model_id == "local:next-search"
        assert {row.model_id for row in db.query(SearchEmbedding)} == {"local:next-search"}


def test_the_kill_switch_never_starts_the_search_server(local_search) -> None:  # noqa: ANN001
    catalog, record = local_search
    install(catalog.default_for("search"))
    with db_module.session_scope() as db:
        YtDlpService(db).ensure_app_settings().ai_features_disabled = ["semantic_search"]
    _drain()
    with db_module.session_scope() as db:
        SemanticDiscovery().search(db, db.get(User, "member"), "zombie", limit=5)
        assert embeddings.target(db) is None and embeddings.serving(db) is None
    assert records(record) == []


def test_idle_backfill_tick_never_starts_the_search_server(local_search) -> None:  # noqa: ANN001
    catalog, record = local_search
    install(catalog.default_for("search"))
    _drain()
    model_supervisor.supervisor.stop_all()
    starts = _starts(record)
    for _tick in range(3):  # three maintenance ticks with nothing new to embed
        _drain()
    assert _starts(record) == starts
    assert not model_supervisor.supervisor.running("test-search")


def test_a_query_while_the_server_sleeps_starts_it_and_goes_lexical_once(local_search) -> None:  # noqa: ANN001
    catalog, _record = local_search
    install(catalog.default_for("search"))
    _drain()
    model_supervisor.supervisor.stop_all()
    with db_module.session_scope() as db:
        member = db.get(User, "member")
        first = SemanticDiscovery().search(db, member, "zombie films", limit=5)
        assert "night" not in {match.record_id for match in first.matches}  # lexical: no words in common
        wait_until(lambda: model_supervisor.supervisor.running("test-search"))
        second = SemanticDiscovery().search(db, member, "zombie films", limit=5)
    assert [match.record_id for match in second.matches] == ["night"]


def test_a_crash_failed_local_model_keeps_its_index_while_external_serves(local_search) -> None:  # noqa: ANN001
    from test_v1_ai_config import MODEL, FakeAi

    catalog, record = local_search
    install(catalog.default_for("search"))
    _drain()
    fake = FakeAi()
    fake.handler = lambda _method, path, body: (200, {"data": [
        {"index": index, "embedding": [1.0, 0.0] if "undead" in text.lower() else [0.0, 1.0]} for index, text in enumerate(body["input"])
    ]}, {}) if path.endswith("/embeddings") else (200, {"data": [{"id": MODEL}]}, {})
    try:
        with db_module.session_scope() as db:
            settings_record = YtDlpService(db).ensure_app_settings()
            settings_record.ai_base_url, settings_record.ai_model, settings_record.ai_embedding_model = fake.url, MODEL, "fake-embed"
        model_supervisor.supervisor._failed["test-search"] = "crashed twice"  # noqa: SLF001 - crash-failed until Retry
        _drain()
        with db_module.session_scope() as db:
            assert embeddings.serving(db).model_id == "fake-embed"
            assert {row.model_id for row in db.query(SearchEmbedding)} == {"local:test-search", "fake-embed"}
        embedded = len([entry for entry in records(record) if "inputs" in entry])
        model_supervisor.supervisor.retry("test-search")
        with db_module.session_scope() as db:
            assert embeddings.serving(db).model_id == "local:test-search"  # Retry: back at once
        embeddings.reset_caches()  # and after a restart, the drain switches back without re-embedding
        _drain()
        with db_module.session_scope() as db:
            assert embeddings.serving(db).model_id == "local:test-search"
            assert {row.model_id for row in db.query(SearchEmbedding)} == {"local:test-search"}
        assert len([entry for entry in records(record) if "inputs" in entry]) == embedded
    finally:
        fake.server.shutdown()
        fake.server.server_close()


def test_a_model_whose_index_was_replaced_is_no_longer_served(local_search) -> None:  # noqa: ANN001
    catalog, _record = local_search
    install(catalog.default_for("search"))
    install(catalog.get("next-search"))
    _drain()
    for model in ("next-search", "test-search"):  # switch away and back without a restart
        with db_module.session_scope() as db:
            YtDlpService(db).ensure_app_settings().local_search_model = model
        if model == "next-search":
            _drain()
    with db_module.session_scope() as db:
        assert embeddings.target(db).model_id == "local:test-search"
        assert {row.model_id for row in db.query(SearchEmbedding)} == {"local:next-search"}
        assert embeddings.serving(db).model_id == "local:next-search"  # not test-search, whose rows are gone
