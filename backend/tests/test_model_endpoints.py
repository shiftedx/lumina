"""Local wins while ready, else external, else nothing; one place decides; bulk work alternates."""
from __future__ import annotations

import threading
import time

import httpx
import pytest

from app.services import local_ai, model_endpoints, model_supervisor
from app.services.local_ai import LocalAiError, embed
from app.services.model_endpoints import LOCAL_TRANSCRIBE_TIMEOUT_SECONDS, Choice, Readiness, connect
from model_support import default_pair, fresh_models, install, model_entry, file_entry, stub_runtime, use_catalog  # noqa: F401
from support import memory_session_factory, seed_app_settings


@pytest.fixture
def catalog(monkeypatch, tmp_path):  # noqa: ANN001
    return use_catalog(monkeypatch, tmp_path, [
        *default_pair(),
        model_entry("other-search", "search", [file_entry("http://127.0.0.1:9", "other.gguf")]),
    ])


def _record(**fields):  # noqa: ANN003, ANN202
    session = memory_session_factory()()
    return seed_app_settings(session, **fields)


EXTERNAL = {"ai_base_url": "http://127.0.0.1:9/v1", "ai_model": "chat", "ai_embedding_model": "ext-embed",
            "asr_base_url": "http://127.0.0.1:9/v1", "asr_model": "ext-asr"}


def test_local_wins_while_ready_then_external_then_nothing(catalog, fresh_models, monkeypatch) -> None:  # noqa: ANN001
    search = catalog.default_for("search")
    record = _record(**EXTERNAL)
    assert [(c.kind, c.model_id) for c in model_endpoints.search_choices(record)] == [("external", "ext-embed")]
    install(search)
    choices = model_endpoints.search_choices(record)
    assert [(c.kind, c.model_id) for c in choices] == [("local", "local:test-search"), ("external", "ext-embed")]
    assert choices[0].model is search and choices[0].threads == model_supervisor.effective_threads(None)
    assert [c.kind for c in model_endpoints.search_choices(_record())] == ["local"]
    monkeypatch.setattr(fresh_models[0], "is_failed", lambda model_id: True)  # crash-failed until Retry
    assert [c.kind for c in model_endpoints.search_choices(record)] == ["external"]
    assert model_endpoints.search_choices(_record()) == []


def test_the_active_model_comes_first_and_others_keep_serving(catalog, fresh_models) -> None:  # noqa: ANN001
    install(catalog.get("other-search"))
    install(catalog.default_for("search"))
    record = _record(local_search_model="other-search")
    assert [c.model_id for c in model_endpoints.search_choices(record)] == ["local:other-search", "local:test-search"]
    assert model_endpoints.active_model(_record(local_search_model="nope"), "search") is catalog.default_for("search")


def test_speech_choices(catalog, fresh_models) -> None:  # noqa: ANN001
    speech = catalog.default_for("speech")
    assert model_endpoints.speech_choice(_record()) is None
    external = model_endpoints.speech_choice(_record(**EXTERNAL))
    assert (external.kind, external.model_id, external.config.asr_model) == ("external", "ext-asr", "ext-asr")
    install(speech)
    assert model_endpoints.speech_choice(_record(**EXTERNAL)).model_id == "local:test-speech"

    record = _record(**EXTERNAL)
    job = model_endpoints.speech_choice_for_job(record, "older-asr")  # jobs keep the model they were requested with
    assert (job.kind, job.config.asr_model) == ("external", "older-asr")
    assert model_endpoints.speech_choice_for_job(record, "local:test-speech").model is speech
    with pytest.raises(LocalAiError, match="The speech model is not installed"):
        model_endpoints.speech_choice_for_job(record, "local:test-search")  # wrong role
    with pytest.raises(LocalAiError, match="Local ASR is not configured"):
        model_endpoints.speech_choice_for_job(_record(), "older-asr")


def test_external_connect_passes_the_config_through(catalog, fresh_models) -> None:  # noqa: ANN001
    choice = model_endpoints.search_choices(_record(**EXTERNAL))[0]
    with connect(choice, wait=True, heavy=True) as config:
        assert config is choice.config
    assert not fresh_models[0].heavy.locked()


def test_local_config_points_at_the_loopback_server_with_its_secret(monkeypatch, tmp_path, catalog, fresh_models) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    install(catalog.default_for("search"))
    choice = model_endpoints.search_choices(_record(ai_max_concurrency=2))[0]
    with connect(choice, wait=True, heavy=False) as config:
        assert config.ai_base_url.startswith("http://127.0.0.1:") and config.ai_base_url == config.asr_base_url
        assert config.ai_embedding_model == config.ai_model == config.asr_model == "local:test-search"
        assert config.ai_api_key and config.ai_max_concurrency == 2
        assert config.asr_timeout_seconds == LOCAL_TRANSCRIBE_TIMEOUT_SECONDS == 3600.0
        assert embed(config, ["zombie night", "picnic"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert local_ai.AiConfig("", "", None, 1, 4096, "", "").asr_timeout_seconds == 300.0


def test_wait_false_yields_none_while_starting_and_on_refusal(monkeypatch, tmp_path, catalog, fresh_models) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    search = catalog.default_for("search")
    install(search)
    choice = model_endpoints.search_choices(_record())[0]
    with connect(choice, wait=False, heavy=False) as config:
        assert config is None
    monkeypatch.setattr(model_supervisor, "available_memory", lambda: 1)
    fresh_models[0].stop_all()
    with connect(choice, wait=False, heavy=False) as config:
        assert config is None
    with pytest.raises(LocalAiError, match="Not enough free memory to start the search model"), connect(choice, wait=True, heavy=False):
        pass


def test_bulk_work_alternates_but_queries_do_not_wait(monkeypatch, tmp_path, catalog, fresh_models) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    install(catalog.default_for("search"))
    install(catalog.default_for("speech"))
    record = _record()
    search, speech = model_endpoints.search_choices(record)[0], model_endpoints.speech_choice(record)
    order: list[str] = []
    speech_held, finish_speech = threading.Event(), threading.Event()

    def transcribing() -> None:
        with connect(speech, wait=True, heavy=True):
            order.append("speech start")
            speech_held.set()
            finish_speech.wait(10)
            order.append("speech end")

    def backfilling() -> None:
        with connect(search, wait=True, heavy=True):
            order.append("backfill")

    threads = [threading.Thread(target=transcribing)]
    threads[0].start()
    speech_held.wait(10)
    threads.append(threading.Thread(target=backfilling))
    threads[1].start()
    time.sleep(0.3)
    with connect(search, wait=True, heavy=False) as config:  # an interactive query does not wait for speech
        order.append("query")
        assert httpx.post(config.ai_base_url + "/embeddings", json={"input": ["q"]}, headers={"Authorization": f"Bearer {config.ai_api_key}"}, timeout=5).status_code == 200
    finish_speech.set()
    for thread in threads:
        thread.join(10)
    assert order == ["speech start", "query", "speech end", "backfill"]


def test_readiness_per_feature(catalog, fresh_models, monkeypatch) -> None:  # noqa: ANN001
    search, speech = catalog.default_for("search"), catalog.default_for("speech")
    table = model_endpoints.readiness(_record())
    assert table["semantic_search"] == Readiness("search_model", False, "The search model is not installed")
    assert table["subtitles_from_speech"] == Readiness("speech_model", False, "The speech model is not installed")
    assert table["recap"] == Readiness("assistant", False, "Needs your assistant server")
    assert table["sync"] == Readiness(None, True) and table["mute_strong_language"] == Readiness(None, True)
    assert set(table) == set(model_endpoints.REQUIRES)

    table = model_endpoints.readiness(_record(**EXTERNAL))
    assert all(entry.ready for entry in table.values())  # the external server covers every requirement

    install(search)
    assert model_endpoints.readiness(_record())["semantic_search"] == Readiness("search_model", True)
    monkeypatch.setattr(fresh_models[0], "failure", lambda model_id: "Not enough free memory to start the search model")
    assert model_endpoints.readiness(_record())["semantic_search"] == Readiness(
        "search_model", False, "Not enough free memory to start the search model")

    monkeypatch.setattr(fresh_models[1], "status", lambda model: type("S", (), {"state": "downloading", "reason": None})())
    assert model_endpoints.readiness(_record())["subtitles_from_speech"].reason == "Waiting for the speech model"


def test_external_serves_checks_the_role_specific_fields() -> None:
    assert model_endpoints.external_serves(local_ai.effective_config(_record(**EXTERNAL)), "search") is True
    assert model_endpoints.external_serves(local_ai.effective_config(_record(**EXTERNAL)), "speech") is True
    assert model_endpoints.external_serves(local_ai.effective_config(_record()), "search") is False
    assert model_endpoints.external_serves(local_ai.effective_config(_record()), "speech") is False
    # an assistant server with no embedding model configured does not serve search
    no_embed = {k: v for k, v in EXTERNAL.items() if k != "ai_embedding_model"}
    assert model_endpoints.external_serves(local_ai.effective_config(_record(**no_embed)), "search") is False


def test_a_failed_local_model_is_ready_while_the_external_server_serves_the_role(catalog, fresh_models, monkeypatch) -> None:  # noqa: ANN001
    install(catalog.default_for("search"))
    monkeypatch.setattr(fresh_models[0], "failure", lambda model_id: "The search model stopped unexpectedly twice; Retry")
    assert model_endpoints.readiness(_record(**EXTERNAL))["semantic_search"] == Readiness("search_model", True)


def test_a_held_external_slot_does_not_block_a_local_embed(monkeypatch) -> None:  # noqa: ANN001
    local = local_ai.AiConfig(
        ai_base_url="http://127.0.0.1:9/v1", ai_model="local:test-search", ai_api_key="s", ai_max_concurrency=1,
        ai_context_tokens=4096, asr_base_url="http://127.0.0.1:9/v1", asr_model="local:test-search",
        ai_embedding_model="local:test-search", asr_api_key="s",  # the supervisor-backed config (connect)
    )
    monkeypatch.setattr(local_ai, "_request", lambda *args, **kwargs: {"data": [{"embedding": [1.0, 0.0]}]})
    with local_ai.inference_slot(1):  # a long external chat holds the only global slot
        assert embed(local, ["q"], slot_wait=0.2) == [[1.0, 0.0]]
