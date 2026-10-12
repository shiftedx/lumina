"""Model servers start on demand on loopback behind a secret, stop when idle, fail after two crashes."""
from __future__ import annotations

import os
import signal
import threading

import httpx
import pytest

from app.services import model_supervisor
from app.services.model_supervisor import ModelUnavailable, Supervisor
from model_support import default_pair, install, model_entry, file_entry, records, stub_runtime, use_catalog, wait_until


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def sup(clock):  # noqa: ANN001
    supervisor = Supervisor(clock=clock)
    yield supervisor
    supervisor.stop_all()


@pytest.fixture
def models(monkeypatch, tmp_path):  # noqa: ANN001
    catalog = use_catalog(monkeypatch, tmp_path, default_pair())
    search, speech = catalog.default_for("search"), catalog.default_for("speech")
    install(search)
    install(speech)
    monkeypatch.setattr(model_supervisor, "available_memory", lambda: 64 << 30)
    return search, speech


def _pid(record) -> int:  # noqa: ANN001
    return [entry for entry in records(record) if "pid" in entry][-1]["pid"]


def test_starts_on_demand_on_loopback_behind_a_secret(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    assert not sup.running(search.id) and records(record) == []  # nothing runs until a feature asks

    endpoint = sup.acquire(search, threads=3, wait=True)
    assert endpoint.base_url.startswith("http://127.0.0.1:") and endpoint.base_url.endswith("/v1")
    assert len(endpoint.secret) >= 32 and sup.running(search.id)
    url = endpoint.base_url + "/embeddings"
    assert httpx.post(url, json={"input": ["x"]}, timeout=5).status_code == 401
    ok = httpx.post(url, json={"input": ["x"]}, headers={"Authorization": f"Bearer {endpoint.secret}"}, timeout=5)
    assert ok.status_code == 200
    sup.release(search.id, endpoint)

    [start] = [entry for entry in records(record) if "argv" in entry]
    assert endpoint.secret not in " ".join(start["argv"])  # the secret travels in the environment only
    assert "LLAMA_ARG_API_KEY" in start["env_names"] and "STUB_RECORD" in start["env_names"]
    argv = start["argv"]
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    for flag, value in (("-t", "3"), ("-tb", "3"), ("-np", "4"), ("-c", "8192"), ("-b", "2048"), ("-ub", "2048")):
        assert argv[argv.index(flag) + 1] == value
    assert "--embeddings" in argv and argv[argv.index("-m") + 1].endswith("/models/test-search/search.gguf")


def test_each_start_gets_a_new_secret(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    first = sup.acquire(search, threads=1, wait=True)
    sup.release(search.id, first)
    sup.stop(search.id)
    second = sup.acquire(search, threads=1, wait=True)
    sup.release(search.id, second)
    assert first.secret != second.secret


def test_the_speech_server_command(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    catalog = use_catalog(monkeypatch, tmp_path, [
        default_pair()[0],
        model_entry("test-speech", "speech", [file_entry("http://127.0.0.1:9", "model.bin")], default=True, engine={"language": "en"}),
    ])
    speech = catalog.default_for("speech")
    install(speech)
    endpoint = sup.acquire(speech, threads=2, wait=True)
    sup.release(speech.id, endpoint)
    [start] = [entry for entry in records(record) if "argv" in entry]
    argv = start["argv"]
    assert argv[argv.index("--model") + 1].endswith("/models/test-speech")
    assert argv[argv.index("--threads") + 1] == "2" and argv[argv.index("--language") + 1] == "en"
    assert {"LUMINA_ASR_SECRET", "HF_HUB_OFFLINE", "OMP_NUM_THREADS"} <= set(start["env_names"])
    assert "LLAMA_ARG_API_KEY" not in start["env_names"]


def test_wait_false_starts_in_the_background(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    assert sup.acquire(search, threads=1, wait=False) is None
    wait_until(lambda: sup.running(search.id))
    endpoint = sup.acquire(search, threads=1, wait=False)
    assert endpoint is not None
    sup.release(search.id, endpoint)


def test_idle_stop_after_five_minutes(monkeypatch, tmp_path, models, sup, clock) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    changes: list[str] = []
    sup.on_change = changes.append
    sup.release(search.id, sup.acquire(search, threads=1, wait=True))
    pid = _pid(record)
    clock.now += 299
    sup.reap_idle()
    assert sup.running(search.id)
    clock.now += 1
    sup.reap_idle()
    assert not sup.running(search.id)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the child is gone, not orphaned
    assert changes[-1] == search.id


def test_a_leased_server_is_never_idle_stopped(monkeypatch, tmp_path, models, sup, clock) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    endpoint = sup.acquire(search, threads=1, wait=True)  # a long transcription holds its lease
    clock.now += 3600
    sup.reap_idle()
    assert sup.running(search.id)
    sup.release(search.id, endpoint)
    clock.now += 300
    sup.reap_idle()
    assert not sup.running(search.id)


def test_a_crash_restarts_once_then_fails_until_retry(monkeypatch, tmp_path, models, sup, clock) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    sup.release(search.id, sup.acquire(search, threads=1, wait=True))
    os.kill(_pid(record), signal.SIGKILL)
    wait_until(lambda: not sup.running(search.id))
    restarted = sup.acquire(search, threads=1, wait=True)  # first crash: start again
    sup.release(search.id, restarted)
    os.kill(_pid(record), signal.SIGKILL)
    wait_until(lambda: not sup.running(search.id))
    clock.now += 60  # second crash well inside ten minutes
    sup.reap_idle()  # the maintenance tick notices it
    with pytest.raises(ModelUnavailable, match="stopped unexpectedly twice; Retry"):
        sup.acquire(search, threads=1, wait=True)
    assert sup.is_failed(search.id) and sup.failure(search.id) == "The search model stopped unexpectedly twice; Retry"
    sup.retry(search.id)
    endpoint = sup.acquire(search, threads=1, wait=True)
    sup.release(search.id, endpoint)


def test_crashes_ten_minutes_apart_are_each_restarted(monkeypatch, tmp_path, models, sup, clock) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    for _ in range(3):
        sup.release(search.id, sup.acquire(search, threads=1, wait=True))
        os.kill(_pid(record), signal.SIGKILL)
        wait_until(lambda: not sup.running(search.id))
        clock.now += 601
    sup.release(search.id, sup.acquire(search, threads=1, wait=True))
    assert not sup.is_failed(search.id)


def test_a_server_that_dies_while_starting_counts_as_a_crash(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path, STUB_CRASH="1")
    search, _speech = models
    with pytest.raises(ModelUnavailable, match="stopped while starting"):
        sup.acquire(search, threads=1, wait=True)
    with pytest.raises(ModelUnavailable):
        sup.acquire(search, threads=1, wait=True)
    assert sup.is_failed(search.id)


def test_not_enough_memory_refuses_without_starting(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    needed = search.ram_bytes + model_supervisor.MEMORY_MARGIN_BYTES
    monkeypatch.setattr(model_supervisor, "available_memory", lambda: needed - 1)
    with pytest.raises(ModelUnavailable, match="^Not enough free memory to start the search model$"):
        sup.acquire(search, threads=1, wait=True)
    assert records(record) == [] and sup.failure(search.id) == "Not enough free memory to start the search model"
    assert not sup.is_failed(search.id)  # a refusal is not a crash: the next request tries again
    monkeypatch.setattr(model_supervisor, "available_memory", lambda: needed)
    sup.release(search.id, sup.acquire(search, threads=1, wait=True))
    assert sup.failure(search.id) is None


def test_a_missing_runtime_is_reported(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    from app.config import settings

    search, _speech = models
    monkeypatch.setattr(settings, "llama_server_bin", str(tmp_path / "no-such-binary"))
    with pytest.raises(ModelUnavailable, match="runtime is not installed"):
        sup.acquire(search, threads=1, wait=True)


def test_stop_all_terminates_every_child(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, speech = models
    sup.release(search.id, sup.acquire(search, threads=1, wait=True))
    sup.release(speech.id, sup.acquire(speech, threads=1, wait=True))
    pids = [entry["pid"] for entry in records(record) if "pid" in entry]
    sup.stop_all()
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def _memory_files(monkeypatch, tmp_path, **contents: str) -> None:  # noqa: ANN001
    """Point the memory probes at fake files; a name left out does not exist."""
    names = {
        "meminfo": "MEMINFO", "v2_max": "CGROUP_V2_MAX", "v2_current": "CGROUP_V2_CURRENT", "v2_stat": "CGROUP_V2_STAT",
        "v1_limit": "CGROUP_V1_LIMIT", "v1_usage": "CGROUP_V1_USAGE", "v1_stat": "CGROUP_V1_STAT",
    }
    for key, attribute in names.items():
        path = tmp_path / "memfiles" / key
        path.parent.mkdir(exist_ok=True)
        if key in contents:
            path.write_text(contents[key])
        monkeypatch.setattr(model_supervisor, attribute, path)


GIB = 1 << 30
REAL_AVAILABLE_MEMORY = model_supervisor.available_memory  # captured before any fixture replaces it
MEMINFO_8G = f"MemTotal: {16 * GIB // 1024} kB\nMemAvailable: {8 * GIB // 1024} kB\n"  # the host's view, as an LXC often shows


@pytest.mark.parametrize(("files", "expected"), [
    ({}, None),  # nothing known (macOS development host)
    ({"meminfo": MEMINFO_8G}, 8 * GIB),
    ({"meminfo": MEMINFO_8G, "v2_max": "max\n", "v2_current": str(GIB)}, 8 * GIB),  # unlimited v2 cgroup
    ({"meminfo": MEMINFO_8G, "v2_max": str(3 * GIB), "v2_current": str(2 * GIB)}, GIB),  # the container cap wins
    ({"meminfo": MEMINFO_8G, "v2_max": str(GIB), "v2_current": str(2 * GIB)}, 0),  # over its limit: no headroom
    ({"meminfo": MEMINFO_8G, "v1_limit": str(4 * GIB), "v1_usage": str(GIB)}, 3 * GIB),  # cgroup v1 host
    ({"meminfo": MEMINFO_8G, "v1_limit": "9223372036854771712", "v1_usage": str(GIB)}, 8 * GIB),  # v1 "unlimited"
    ({"v2_max": str(2 * GIB), "v2_current": str(GIB)}, GIB),  # no meminfo, cgroup only
    ({"meminfo": "garbage", "v2_max": "junk", "v2_current": "1"}, None),  # unreadable values are ignored
    # Reclaimable page cache (inactive_file) is not in use: a media container sits near its cap on cache alone.
    ({"v2_max": str(4 * GIB), "v2_current": str(4 * GIB - 1), "v2_stat": f"anon 1\ninactive_file {3 * GIB}\n"}, 3 * GIB + 1),
    ({"v1_limit": str(4 * GIB), "v1_usage": str(4 * GIB), "v1_stat": f"cache 9\ntotal_inactive_file {2 * GIB}\n"}, 2 * GIB),
    ({"v2_max": str(4 * GIB), "v2_current": str(3 * GIB), "v2_stat": "inactive_file junk\n"}, GIB),  # unparseable stat: plain usage
])
def test_available_memory_is_the_smallest_known_headroom(monkeypatch, tmp_path, files, expected) -> None:  # noqa: ANN001
    _memory_files(monkeypatch, tmp_path, **files)
    assert model_supervisor.available_memory() == expected


def test_a_container_cap_below_the_host_view_refuses_the_start(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    monkeypatch.setattr(model_supervisor, "available_memory", REAL_AVAILABLE_MEMORY)  # undo the models fixture's stub
    _memory_files(monkeypatch, tmp_path, meminfo=MEMINFO_8G, v2_max=str(search.ram_bytes + GIB), v2_current=str(GIB))
    with pytest.raises(ModelUnavailable, match="^Not enough free memory to start the search model$"):
        sup.acquire(search, threads=1, wait=True)  # host says 8 GiB free; the container has ram_bytes left, < ram + 512 MiB
    assert records(record) == []


@pytest.mark.parametrize(("cpus", "auto"), [(1, 1), (2, 1), (4, 3), (16, 12), (64, 12)])
def test_threads_default_to_cpus_minus_one_and_admins_only_lower(monkeypatch, cpus, auto) -> None:  # noqa: ANN001
    monkeypatch.setattr(model_supervisor, "cpu_count", lambda: cpus)
    assert model_supervisor.default_threads() == auto
    assert model_supervisor.effective_threads(None) == auto
    assert model_supervisor.effective_threads(1) == 1
    assert model_supervisor.effective_threads(99) == auto


def test_reclaimable_cache_near_the_cap_does_not_refuse_the_start(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    monkeypatch.setattr(model_supervisor, "available_memory", REAL_AVAILABLE_MEMORY)
    cap = 4 * GIB
    _memory_files(monkeypatch, tmp_path, v2_max=str(cap), v2_current=str(cap - 1), v2_stat=f"inactive_file {3 * GIB}\n")
    sup.release(search.id, sup.acquire(search, threads=1, wait=True))  # usage is at the cap, but mostly page cache


def test_a_stop_during_start_gives_a_fixed_reason(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    entered, proceed = threading.Event(), threading.Event()

    def blocked_probe() -> int:
        entered.set()
        proceed.wait(10)
        return 64 << 30

    monkeypatch.setattr(model_supervisor, "available_memory", blocked_probe)
    outcome: list[BaseException] = []

    def waiter() -> None:
        try:
            sup.acquire(search, threads=1, wait=True)
        except ModelUnavailable as exc:
            outcome.append(exc)

    thread = threading.Thread(target=waiter)
    thread.start()
    assert entered.wait(10)
    sup.stop(search.id)
    proceed.set()
    thread.join(10)
    assert [str(exc) for exc in outcome] == ["The search model stopped while starting"]


def test_acquire_after_close_raises_and_starts_nothing(monkeypatch, tmp_path, models, sup) -> None:  # noqa: ANN001
    record = stub_runtime(monkeypatch, tmp_path)
    search, _speech = models
    sup.close()  # shutdown: a job still running must not spawn a fresh server
    assert sup.closed
    with pytest.raises(ModelUnavailable):
        sup.acquire(search, threads=1, wait=True)
    assert not [entry for entry in records(record) if "pid" in entry]
