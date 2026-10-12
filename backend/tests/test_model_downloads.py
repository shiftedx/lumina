"""Catalog-only, resumable, size-capped, sha256-verified model downloads."""
from __future__ import annotations

import pytest

from app.services import model_catalog, model_downloads
from app.services.model_downloads import CORRUPTED, GONE, OVERSIZE, REDIRECT, UNREACHABLE, DownloadManager
from model_support import REV, FakeModelHost, file_entry, model_entry, payload_for, use_catalog, wait_until

BIG = bytes(range(256)) * 1024  # 256 KiB


@pytest.fixture
def host():
    fake = FakeModelHost()
    yield fake
    fake.close()


@pytest.fixture
def manager(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(model_downloads, "RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(model_downloads, "CHUNK_BYTES", 16 * 1024)
    manager = DownloadManager()
    events: list[str] = []
    manager.on_change = events.append
    manager.events = events  # the test's own record of on_change calls
    yield manager
    manager.shutdown()


def _catalog(monkeypatch, tmp_path, host, *, big: bytes = BIG, hosts: tuple[str, ...] = ("127.0.0.1",)):  # noqa: ANN001
    """A default search model (one big file) and a default speech model (two files), all served by ``host``."""
    search_file = file_entry(host.base, "search.gguf", big)
    speech_files = [file_entry(host.base, "model.bin"), file_entry(host.base, "config.json")]
    catalog = use_catalog(monkeypatch, tmp_path, [
        model_entry("test-search", "search", [search_file], default=True),
        model_entry("test-speech", "speech", speech_files, default=True),
    ], hosts=hosts)
    host.serve(search_file, big)
    for entry in speech_files:
        host.serve(entry)
    return catalog.default_for("search"), catalog.default_for("speech")


def _settled(manager, model):  # noqa: ANN001
    wait_until(lambda: manager.status(model).state not in {"downloading", "verifying"})
    return manager.status(model)


def test_download_verifies_then_installs(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    _search, speech = _catalog(monkeypatch, tmp_path, host)
    started = manager.start(speech)
    assert started.state in {"downloading", "verifying", "ready"}
    status = _settled(manager, speech)
    assert (status.state, status.bytes_done, status.bytes_total) == ("ready", speech.size_bytes, speech.size_bytes)
    directory = model_catalog.model_dir(speech)
    assert model_catalog.is_installed(speech)
    assert sorted(path.name for path in directory.iterdir()) == [".ready", "config.json", "model.bin"]  # no .part, no .wanted
    assert (directory / "model.bin").read_bytes() == payload_for("model.bin")
    assert manager.events and manager.events[-1] == "test-speech"


def test_an_interrupted_download_resumes_with_a_range_request(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    path = f"/org/repo/resolve/{REV}/search.gguf"
    host.drop_after[path] = 98_304  # six whole 16 KiB chunks reach the .part before the cut
    manager.start(search)
    assert _settled(manager, search).state == "ready"
    ranges = [wanted for requested, wanted in host.requests if requested == path]
    assert ranges[0] is None and ranges[1] == "bytes=98304-"
    assert (model_catalog.model_dir(search) / "search.gguf").read_bytes() == BIG


def test_a_restart_resumes_from_the_part_file(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    directory = model_catalog.model_dir(search)
    directory.mkdir(parents=True)
    (directory / "search.gguf.part").write_bytes(BIG[:50_000])
    (directory / model_catalog.WANTED_MARKER).touch()
    assert manager.status(search).bytes_done == 50_000  # partial progress shows while absent

    assert manager.resume_wanted() == ["test-search"]
    assert _settled(manager, search).state == "ready"
    assert host.requests == [(f"/org/repo/resolve/{REV}/search.gguf", "bytes=50000-")]


def test_a_part_longer_than_the_file_starts_over(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    directory = model_catalog.model_dir(search)
    directory.mkdir(parents=True)
    (directory / "search.gguf.part").write_bytes(b"x" * (len(BIG) + 10))
    manager.start(search)
    assert _settled(manager, search).state == "ready"
    assert host.requests[0][1] is None


def test_a_server_ignoring_range_rewrites_the_file(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    directory = model_catalog.model_dir(search)
    directory.mkdir(parents=True)
    (directory / "search.gguf.part").write_bytes(b"stale-bytes")
    host.ignore_range = True
    manager.start(search)
    assert _settled(manager, search).state == "ready"
    assert (directory / "search.gguf").read_bytes() == BIG


def test_a_checksum_mismatch_deletes_and_fails_then_retry_works(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    _search, speech = _catalog(monkeypatch, tmp_path, host)
    path = f"/org/repo/resolve/{REV}/model.bin"
    good = host.files[path]
    host.files[path] = bytes(len(good))  # same size, wrong bytes
    manager.start(speech)
    status = _settled(manager, speech)
    assert (status.state, status.reason) == ("failed", CORRUPTED)
    directory = model_catalog.model_dir(speech)
    assert not (directory / "model.bin").exists() and not (directory / "model.bin.part").exists()
    assert not (directory / model_catalog.WANTED_MARKER).exists()  # a restart does not loop on a corrupt upstream
    assert not model_catalog.is_installed(speech)

    host.files[path] = good
    manager.start(speech)  # Retry
    assert _settled(manager, speech).state == "ready"


def test_three_failures_without_progress_stop_with_retry(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    path = f"/org/repo/resolve/{REV}/search.gguf"
    host.statuses[path] = 503
    manager.start(search)
    status = _settled(manager, search)
    assert (status.state, status.reason) == ("failed", UNREACHABLE)
    assert [requested for requested, _ in host.requests].count(path) == 3


def test_a_vanished_file_is_terminal(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    host.files.clear()
    manager.start(search)
    assert _settled(manager, search).reason == GONE
    assert len(host.requests) == 1


def test_cancel_stops_and_deletes_partial_files(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host, big=BIG * 8)  # 2 MiB, slow
    host.slow = True
    manager.start(search)
    wait_until(lambda: (manager.status(search).bytes_done or 0) > 0)
    manager.cancel(search)
    assert manager.status(search).state == "absent"
    directory = model_catalog.model_dir(search)
    assert not any(path.name.endswith(".part") for path in directory.iterdir())
    assert not (directory / model_catalog.WANTED_MARKER).exists()


def test_remove_deletes_an_installed_model(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    _search, speech = _catalog(monkeypatch, tmp_path, host)
    manager.start(speech)
    assert _settled(manager, speech).state == "ready"
    manager.remove(speech)
    assert manager.status(speech).state == "absent" and not model_catalog.model_dir(speech).exists()


def test_remove_during_download_leaves_nothing(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host, big=BIG * 8)
    host.slow = True
    manager.start(search)
    wait_until(lambda: (manager.status(search).bytes_done or 0) > 0)
    manager.remove(search)
    assert manager.status(search).state == "absent"
    assert not model_catalog.model_dir(search).exists()


def test_second_start_is_a_noop_while_running(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host, big=BIG * 8)
    host.slow = True
    manager.start(search)
    manager.start(search)
    assert _settled(manager, search).state == "ready"
    assert len(host.requests) == 1


def test_not_enough_space_refuses_before_any_request(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    monkeypatch.setattr(model_downloads, "disk_free", lambda path: 1000)
    status = manager.start(search)
    needed = -(-len(BIG) * 110 // 100)  # size + 10 %, rounded up
    assert status.state == "failed" and status.reason == model_downloads.space_reason(needed, 1000)
    assert "needs 1 MB" in status.reason
    assert host.requests == [] and not (model_catalog.model_dir(search) / model_catalog.WANTED_MARKER).exists()


def test_an_oversize_body_is_aborted_and_discarded(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    path = f"/org/repo/resolve/{REV}/search.gguf"
    host.files[path] = BIG + b"x" * (len(BIG) // 50)  # 2 % larger than the catalog size
    host.hide_length.add(path)  # no Content-Length: the streaming cap must catch it
    manager.start(search)
    status = _settled(manager, search)
    assert (status.state, status.reason) == ("failed", OVERSIZE)
    assert not (model_catalog.model_dir(search) / "search.gguf.part").exists()


def test_an_oversize_content_length_is_refused_before_the_body(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    host.files[f"/org/repo/resolve/{REV}/search.gguf"] = BIG * 2
    manager.start(search)
    assert _settled(manager, search).reason == OVERSIZE


@pytest.mark.parametrize("location", [
    "http://localhost:{port}/org/repo/resolve/{rev}/search.gguf",  # not a catalog host
    "http://huggingface.co/org/repo/resolve/{rev}/search.gguf",  # plain http off loopback, never dialed
    "https://evil.example/search.gguf",
])
def test_redirects_leave_the_catalog_hosts_only_never(monkeypatch, tmp_path, host, manager, location) -> None:  # noqa: ANN001
    # huggingface.co is a catalog host here, so only the https rule can refuse the plain-http hop
    search, _speech = _catalog(monkeypatch, tmp_path, host, hosts=("127.0.0.1", "huggingface.co"))
    path = f"/org/repo/resolve/{REV}/search.gguf"
    host.redirects[path] = location.format(port=host.server.server_port, rev=REV)
    manager.start(search)
    assert _settled(manager, search).reason == REDIRECT
    assert len(host.requests) == 1  # the refused hop was never requested


def test_a_redirect_on_the_catalog_host_is_followed(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    path = f"/org/repo/resolve/{REV}/search.gguf"
    host.files["/cdn/blob"] = host.files[path]
    host.redirects[path] = "/cdn/blob"  # relative, like huggingface.co's small-file redirects
    manager.start(search)
    assert _settled(manager, search).state == "ready"


def test_endless_redirects_fail(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host)
    path = f"/org/repo/resolve/{REV}/search.gguf"
    host.redirects[path] = path
    manager.start(search)
    assert _settled(manager, search).reason == REDIRECT
    assert len(host.requests) == model_downloads.MAX_REDIRECTS + 1


def test_shutdown_keeps_the_part_and_wanted_marker(monkeypatch, tmp_path, host, manager) -> None:  # noqa: ANN001
    search, _speech = _catalog(monkeypatch, tmp_path, host, big=BIG * 8)
    host.slow = True
    manager.start(search)
    wait_until(lambda: (manager.status(search).bytes_done or 0) > 0)
    manager.shutdown()
    directory = model_catalog.model_dir(search)
    assert (directory / "search.gguf.part").stat().st_size > 0
    assert (directory / model_catalog.WANTED_MARKER).exists()
