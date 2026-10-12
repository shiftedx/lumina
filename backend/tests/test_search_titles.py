"""Semantic search over Media titles and transcript moments."""
from __future__ import annotations

import app.main as main
from app.services.library_search import ensure_search_index, index_item_transcript
from app.services.semantic_discovery import SemanticDiscovery, search_ids
from app.services.transcripts import TranscriptService
from discovery_support import add_movie, add_series, add_summary, add_version
from support import make_user, memory_session_factory


def _library():
    session = memory_session_factory()()
    owner, member = make_user("owner"), make_user("member")
    session.add_all([owner, member])
    add_series(session, "sev", "Severance", seasons={1: 2}, owner="owner", visibility="private", genres=["Thriller"])
    add_series(session, "office", "The Office", seasons={1: 2}, genres=["Comedy"])
    add_movie(session, "arr", "Arrival", genres=["Science Fiction"], people=["Amy Adams"])
    add_version(session, "arr-4k", "arr").title = "Arrival remaster"
    session.commit()
    ensure_search_index(session.get_bind())
    transcripts = TranscriptService(session)
    transcripts.store("office-s1e2-v", language="en", source_kind="source_caption",
                      cues=[(0, 900, "welcome to the office"), (90_000, 90_900, "someone put my stapler in jello")])
    transcripts.store("sev-s1e1-v", language="en", source_kind="source_caption",
                      cues=[(30_000, 30_900, "the stapler on the severed floor")])
    session.commit()
    return session, owner, member


def test_private_series_is_invisible_in_search() -> None:
    session, owner, member = _library()

    assert main.search_library(q="severance", limit=10, current_user=member, db=session).matches == []
    owner_matches = main.search_library(q="severance", limit=10, current_user=owner, db=session).matches
    titles = [match for match in owner_matches if match.kind == "title"]
    assert "sev" in {match.id for match in titles}
    assert all(match.title_id == match.id and match.media_title is not None for match in titles)


def test_moment_hit_carries_start_ms_and_its_episode() -> None:
    session, _owner, member = _library()

    matches = main.search_library(q="stapler jello", limit=10, current_user=member, db=session).matches
    moment = next(match for match in matches if match.kind == "moment")
    assert (moment.item.id, moment.start_ms, moment.title_id) == ("office-s1e2-v", 90_000, "office-s1e2")
    assert moment.media_title.series_name == "The Office"
    assert "sev-s1e1-v" not in {match.item.id for match in matches if match.item is not None}


def test_a_version_hit_proposes_its_title_not_a_file_card() -> None:
    session, _owner, member = _library()

    result = SemanticDiscovery().search(session, member, "remaster", limit=5)

    assert [(match.kind, match.record_id) for match in result.matches] == [("title", "arr")]


def test_a_summary_only_match_proposes_its_title() -> None:
    session, _owner, member = _library()
    add_summary(session, "arr-v", [{"text": "The heptapods arrive"}], overview="A linguist decodes glyphs.")
    session.flush()
    index_item_transcript(session, "arr-v")
    session.commit()

    matches = main.search_library(q="heptapods", limit=10, current_user=member, db=session).matches

    assert [(match.kind, match.id, match.title_id) for match in matches] == [("title", "arr", "arr")]


def test_search_ids_filters_types_and_maps_moments_to_their_episode() -> None:
    session, _owner, member = _library()

    refs = search_ids(session, member, "stapler", types={"episode", "moment"}, limit=10)
    assert [(ref.kind, ref.id, ref.title_id, ref.start_ms) for ref in refs] == [("moment", "office-s1e2-v", "office-s1e2", 90_000)]
    assert search_ids(session, member, "arrival", types={"series"}, limit=10) == []
    assert [ref.id for ref in search_ids(session, member, "arrival", types={"movie"}, limit=10)] == ["arr"]


# ---- Optional embeddings --------------------------------------------------------------

import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as db_module  # noqa: E402
from app.models import AppSettings, LibraryTag, SearchEmbedding, User  # noqa: E402
from app.security import get_current_user  # noqa: E402
from app.services import embeddings  # noqa: E402
from app.services.local_ai import embed, inference_slot  # noqa: E402
from app.services.yt_dlp_service import YtDlpService  # noqa: E402
from support import seed_app_settings  # noqa: E402
from test_v1_ai_config import MODEL, FakeAi  # noqa: E402

EMBED_MODEL = "fake-embed"


@pytest.fixture(autouse=True)
def _fresh_embedding_caches():
    embeddings.reset_caches()
    yield
    embeddings.reset_caches()


@pytest.fixture
def fake_ai():
    fake = FakeAi()
    fake.handler = _embedding_handler
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def _vector_for(text: str) -> list[float]:
    """A two-axis toy space: anything about the undead points one way, everything else the other."""
    return [1.0, 0.0] if any(word in text.lower() for word in ("zombie", "undead")) else [0.0, 1.0]


def _embedding_handler(method, path, body):  # noqa: ANN001
    if path.endswith("/embeddings"):
        return 200, {"data": [{"index": i, "embedding": _vector_for(text)} for i, text in enumerate(body["input"])]}, {}
    return 200, {"data": [{"id": MODEL}]}, {}


def _undead_library(fake, *, private_undead: bool = False):
    session = memory_session_factory()()
    owner, member = make_user("owner"), make_user("member")
    session.add_all([owner, member])
    add_movie(session, "night", "Night of the Living", overview="The undead rise at dusk")
    add_movie(session, "picnic", "Picnic", overview="A sunny afternoon")
    if private_undead:
        add_movie(session, "secret", "Home tape", owner="owner", visibility="private", overview="Undead cosplay")
        add_movie(session, "gone", "Lost reel", overview="Undead archive", )
        session.flush()
        from app.models import LibraryItem

        session.get(LibraryItem, "gone-v").status = "missing"
    session.add(LibraryTag(id="tag-1", item_id="night-v", user_id="member", tag="zombiesecretword"))
    session.commit()
    seed_app_settings(session, ai_base_url=fake.url, ai_model=MODEL, ai_embedding_model=EMBED_MODEL)
    ensure_search_index(session.get_bind())
    while embeddings.backfill(session, embeddings.target(session).config):
        pass
    return session, owner, member


def test_embed_batches_32_and_truncates_inputs_to_2kb(fake_ai) -> None:
    config = fake_ai.config(ai_embedding_model=EMBED_MODEL)

    vectors = embed(config, ["é" * 3000] + [f"t{i}" for i in range(40)])

    assert len(vectors) == 41
    posts = [body for method, path, _headers, body in fake_ai.requests if path == "/v1/embeddings"]
    assert [len(body["input"]) for body in posts] == [32, 9]
    assert set(posts[0]) == {"model", "input"} and posts[0]["model"] == EMBED_MODEL
    assert len(posts[0]["input"][0].encode()) <= 2048


def test_backfill_embeds_only_public_text_and_is_idempotent(fake_ai) -> None:
    session, _owner, _member = _undead_library(fake_ai)

    rows = session.query(SearchEmbedding).all()
    assert {(row.target_id, row.kind, row.model_id) for row in rows} == {("night", "title", EMBED_MODEL), ("picnic", "title", EMBED_MODEL)}
    sent = " ".join(" ".join(body["input"]) for _m, path, _h, body in fake_ai.requests if path == "/v1/embeddings")
    assert "undead" in sent.lower() and "zombiesecretword" not in sent
    before = len(fake_ai.requests)
    assert embeddings.backfill(session, embeddings.target(session).config) == 0
    assert len(fake_ai.requests) == before


def test_dense_search_finds_meaning_without_shared_words(fake_ai) -> None:
    session, _owner, member = _undead_library(fake_ai)

    result = SemanticDiscovery().search(session, member, "zombie films", limit=5)

    assert [(match.kind, match.record_id, match.match_mode) for match in result.matches] == [("title", "night", "semantic")]


def test_dense_search_never_surfaces_a_private_title(fake_ai) -> None:
    session, owner, member = _undead_library(fake_ai, private_undead=True)

    assert "secret" not in {match.record_id for match in SemanticDiscovery().search(session, member, "zombie", limit=10).matches}
    assert "gone" not in {match.record_id for match in SemanticDiscovery().search(session, owner, "zombie", limit=10).matches}
    assert "secret" in {match.record_id for match in SemanticDiscovery().search(session, owner, "zombie", limit=10).matches}


def test_endpoint_down_falls_back_to_lexical_and_deterministic() -> None:
    session = memory_session_factory()()
    member = make_user("member")
    session.add(member)
    add_movie(session, "zl", "Zombieland")
    session.commit()
    seed_app_settings(session, ai_base_url="http://127.0.0.1:9/v1", ai_model=MODEL, ai_embedding_model=EMBED_MODEL)
    ensure_search_index(session.get_bind())

    result = SemanticDiscovery().search(session, member, "zombieland", limit=5)

    assert result.mode == "hybrid" and [match.record_id for match in result.matches] == ["zl"]


def test_slow_embedding_endpoint_falls_back_within_budget(fake_ai) -> None:
    session, _owner, member = _undead_library(fake_ai)
    embeddings.reset_caches()

    def slow(method, path, body):  # noqa: ANN001
        if path.endswith("/embeddings"):
            time.sleep(3)
        return _embedding_handler(method, path, body)

    fake_ai.handler = slow
    started = time.monotonic()
    result = SemanticDiscovery().search(session, member, "picnic", limit=5)
    assert time.monotonic() - started < 2.5
    assert [match.record_id for match in result.matches] == ["picnic"]

    # A long chat holding the only inference slot must not block typeahead either.
    session.get(AppSettings, 1).ai_max_concurrency = 1
    session.commit()
    fake_ai.handler = _embedding_handler
    held, release = threading.Event(), threading.Event()

    def hold_slot() -> None:
        with inference_slot(1):
            held.set()
            release.wait(10)

    threading.Thread(target=hold_slot, daemon=True).start()
    held.wait(5)
    try:
        started = time.monotonic()
        result = SemanticDiscovery().search(session, member, "picnic afternoon", limit=5)
        assert time.monotonic() - started < 2.5
        assert [match.record_id for match in result.matches] == ["picnic"]
    finally:
        release.set()


def test_an_embedding_model_change_replaces_old_vectors_once_the_new_index_is_complete(fake_ai) -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model, record.ai_embedding_model = fake_ai.url, MODEL, "old"
        add_movie(db, "m1", "Movie one")
        db.add(SearchEmbedding(target_id="m1", model_id="old", kind="title", signature="s", vector=b"\x00\x00\x80?"))
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: User(id="admin", username="admin", display_name="Admin", role="admin", is_active=True)
    try:
        http = TestClient(app, base_url="http://localhost")
        response = http.put("/api/admin/ai/config", json={"embedding_model": EMBED_MODEL})
        assert response.status_code == 200 and response.json()["embedding_model"] == EMBED_MODEL
        _wait_for_drain()
        with db_module.session_scope() as db:
            assert {(row.target_id, row.model_id) for row in db.query(SearchEmbedding)} == {("m1", EMBED_MODEL)}
    finally:
        app.dependency_overrides.clear()


def _wait_for_drain() -> None:
    deadline = time.monotonic() + 10
    while embeddings._draining.locked():
        assert time.monotonic() < deadline, "embedding backfill did not finish"
        time.sleep(0.01)


def test_start_backfill_drains_in_batches_and_survives_a_down_endpoint(fake_ai) -> None:
    db_module.init_db()
    with db_module.session_scope() as db:
        record = YtDlpService(db).ensure_app_settings()
        record.ai_base_url, record.ai_model, record.ai_embedding_model = fake_ai.url, MODEL, EMBED_MODEL
        for index in range(40):
            add_movie(db, f"m{index:02d}", f"Movie {index}")

    embeddings.start_backfill("run-1")
    embeddings.start_backfill("run-2")  # a trigger while draining is a no-op
    _wait_for_drain()
    with db_module.session_scope() as db:
        assert db.query(SearchEmbedding).count() == 40
    assert len([path for _m, path, _h, _b in fake_ai.requests if path == "/v1/embeddings"]) == 2  # 32 + 8

    with db_module.session_scope() as db:
        db.get(AppSettings, 1).ai_base_url = "http://127.0.0.1:9/v1"
        add_movie(db, "late", "Late arrival")
    embeddings.start_backfill()
    _wait_for_drain()  # the down endpoint pauses the drain without raising


def test_semantic_search_kill_switch_keeps_search_and_backfill_off_the_endpoint(fake_ai) -> None:
    session, _owner, member = _undead_library(fake_ai)
    session.get(AppSettings, 1).ai_features_disabled = ["semantic_search"]
    session.commit()
    before = len(fake_ai.requests)

    result = SemanticDiscovery().search(session, member, "zombie films", limit=5)

    assert embeddings.target(session) is None and embeddings.serving(session) is None
    assert "night" not in {match.record_id for match in result.matches}
    assert len(fake_ai.requests) == before
