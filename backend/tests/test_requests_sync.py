"""Request sync: queue progress, file stats, path mapping, targeted rescan, available only once the vault has the title."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from arr_fake import API_KEY, FakeArr, library_title
from support import file_backed_session_factory, make_user, seed_app_settings

from app.models import AppMaintenanceState, ArrServer, ImportRun, MediaRequest, StorageRoot, utcnow
from app.services.requests import sync

REAL_RESCAN = sync.rescan_folder  # the fixture replaces the module's
MAPPINGS = [{"remote": "/movies", "local": "/media/movies"}, {"remote": "/tv", "local": "/media/tv"}, {"remote": "/tv/Anime", "local": "/media/anime"}]


@pytest.fixture
def setup(monkeypatch):  # noqa: ANN201
    factory = file_backed_session_factory("alice")
    fakes = {"radarr": FakeArr("radarr"), "sonarr": FakeArr("sonarr")}
    rescans: list[str] = []
    monkeypatch.setattr(sync, "rescan_folder", rescans.append)
    with factory() as session:
        session.add(make_user("admin", role="admin"))
        seed_app_settings(session, requests_enabled=True)
        for kind, fake in fakes.items():
            session.add(ArrServer(id=kind, kind=kind, name=kind, base_url=fake.url, api_key=API_KEY, path_mappings=MAPPINGS, enabled=True))
        session.commit()
    yield factory, fakes, rescans
    for fake in fakes.values():
        fake.close()


def add_request(factory, **fields) -> str:  # noqa: ANN001
    with factory() as session:
        session.add(MediaRequest(**{"id": "req", "kind": "movie", "media_type": "movie", "tmdb_id": 603, "title": "The Matrix",
                                    "status": "approved", "requested_by": "alice", "arr_item_id": 101, **fields}))
        session.commit()
    return fields.get("id", "req")


def load(factory, request_id: str = "req") -> MediaRequest:  # noqa: ANN001
    with factory() as session:
        return session.get(MediaRequest, request_id)


def test_map_path_uses_the_longest_remote_prefix() -> None:
    assert sync.map_path("/tv/Anime/One Piece", MAPPINGS) == "/media/anime/One Piece"
    assert sync.map_path("/tv/Shows/Lost", MAPPINGS) == "/media/tv/Shows/Lost"
    assert sync.map_path("/tvx/Lost", MAPPINGS) is None
    assert sync.map_path("/movies", MAPPINGS) == "/media/movies"
    assert sync.map_path(None, MAPPINGS) is None


def test_queue_progress_marks_processing(setup) -> None:  # noqa: ANN001
    factory, fakes, rescans = setup
    add_request(factory, arr_server_id="radarr")
    fakes["radarr"].library[101] = {"id": 101, "hasFile": False, "path": "/movies/The Matrix (1999)"}
    fakes["radarr"].queue = [{"movieId": 101, "size": 1000, "sizeleft": 250}, {"movieId": 999, "size": 5, "sizeleft": 5}]
    sync.sync_once()
    req = load(factory)
    assert (req.status, req.progress) == ("processing", 0.75)
    assert rescans == []
    with factory() as session:
        assert session.get(ArrServer, "radarr").last_ok_at is not None


def test_movie_file_rescans_the_mapped_folder_and_is_available_once_the_title_exists(setup) -> None:  # noqa: ANN001
    factory, fakes, rescans = setup
    add_request(factory, arr_server_id="radarr", status="processing", progress=0.9)
    fakes["radarr"].library[101] = {"id": 101, "hasFile": True, "path": "/movies/The Matrix (1999)"}
    sync.sync_once()
    assert rescans == ["/media/movies/The Matrix (1999)"]
    assert (load(factory).status, load(factory).progress) == ("processing", 1.0)  # file there, vault not yet
    with factory() as session:
        library_title(session, "movie", "matrix", Tmdb="603")
    sync.sync_once()
    req = load(factory)
    assert (req.status, req.library_title_id) == ("available", "matrix")


def test_series_with_some_requested_episodes_is_partially_available(setup) -> None:  # noqa: ANN001
    factory, fakes, _ = setup
    add_request(factory, kind="show", media_type="tv", tmdb_id=1399, tvdb_id=121361, seasons=[1, 2], arr_server_id="sonarr", arr_item_id=7)
    stats = lambda files, count: {"episodeFileCount": files, "episodeCount": count}  # noqa: E731
    fakes["sonarr"].library[7] = {"id": 7, "path": "/tv/Shows/Game of Thrones", "seasons": [
        {"seasonNumber": 1, "statistics": stats(10, 10)}, {"seasonNumber": 2, "statistics": stats(3, 10)},
        {"seasonNumber": 3, "statistics": stats(0, 10)}]}
    with factory() as session:
        library_title(session, "series", "got", Tvdb="121361")
    sync.sync_once()
    req = load(factory)
    assert (req.status, req.library_title_id) == ("partially_available", "got")
    fakes["sonarr"].library[7]["seasons"][1]["statistics"] = stats(10, 10)
    sync.sync_once()
    assert load(factory).status == "available"


def test_unreachable_server_records_the_error_and_keeps_the_request(setup) -> None:  # noqa: ANN001
    factory, fakes, _ = setup
    add_request(factory, arr_server_id="radarr")
    fakes["radarr"].down = True
    sync.sync_once()
    assert load(factory).status == "approved"
    with factory() as session:
        assert session.get(ArrServer, "radarr").last_error == "Radarr endpoint returned HTTP 503"


def test_sync_skips_when_requests_are_off(setup) -> None:  # noqa: ANN001
    factory, fakes, _ = setup
    add_request(factory, arr_server_id="radarr")
    with factory() as session:
        from app.models import AppSettings

        session.get(AppSettings, 1).requests_enabled = False
        session.commit()
    sync.sync_once()
    assert fakes["radarr"].requests == []


def test_rescan_folder_starts_a_scoped_run_on_the_root_holding_it(setup, monkeypatch) -> None:  # noqa: ANN001
    factory, _, _ = setup
    monkeypatch.setattr(sync, "_rescanned", {})
    started, submitted = [], []
    from app.services import library_automation

    monkeypatch.setattr(library_automation, "start_run_default", lambda *a, **k: started.append((a, k)) or "run-1")
    monkeypatch.setattr(library_automation.automation, "submit", submitted.append)
    with factory() as session:
        session.add_all([
            StorageRoot(id="tv", label="TV", path="/media/tv", mode="external", enabled=True),
            StorageRoot(id="movies", label="Movies", path="/media/movies", mode="external", enabled=True),
            StorageRoot(id="held", label="Held", path="/media/held", mode="external", enabled=True),
            ImportRun(id="old", root_id="movies", user_id="admin", visibility="shared", state="succeeded", cursor="[]", counters={}),
            ImportRun(id="failed", root_id="tv", user_id="admin", visibility="shared", state="failed", cursor="[]", counters={}),
            ImportRun(id="hold", root_id="held", user_id="admin", visibility="shared", state="needs_confirmation", cursor="[]", counters={}),
        ])
        session.commit()
    REAL_RESCAN("/media/movies/The Matrix (1999)")
    REAL_RESCAN("/media/movies/The Matrix (1999)")  # throttled
    REAL_RESCAN("/media/tv/Lost")  # never imported: waits for the admin's first import
    REAL_RESCAN("/media/held/Lost")  # a held confirmation never yields an automatic scoped import
    assert started == [(("movies", "admin", "shared"), {"trigger": "manager", "scope": [{"dir": "The Matrix (1999)", "deep": True}]})]
    assert submitted == ["run-1"]


def test_inventory_rescans_do_not_starve_folders_when_a_root_only_accepts_one_per_poll(setup, monkeypatch) -> None:  # noqa: ANN001
    factory, _, _ = setup
    from app.services import library_automation
    from app.services.library_import import ImportRunError

    with factory() as db:
        db.add(StorageRoot(id="movies", label="Movies", path="/media/movies", mode="external", enabled=True))
        db.add(ImportRun(id="baseline", root_id="movies", user_id="admin", visibility="shared", state="succeeded", cursor="[]", counters={}))
        db.commit()
    now = [1000.0]
    accepted: list[str] = []
    available = [True]

    def start(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if not available[0]:
            raise ImportRunError("Root is busy")
        available[0] = False
        accepted.append(kwargs["scope"][0]["dir"])
        return "run"

    monkeypatch.setattr(sync, "_rescanned", {})
    monkeypatch.setattr(sync, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(sync, "rescan_folder", REAL_RESCAN)
    monkeypatch.setattr(library_automation, "start_run_default", start)
    monkeypatch.setattr(library_automation.automation, "submit", lambda _: None)
    sync.rescan_folders([
        sync.RescanOffer("/media/movies/A", sync.reconciliation_digest({"name": "A"}), "arr:one"),
        sync.RescanOffer("/media/movies/B", sync.reconciliation_digest({"name": "B"}), "arr:one"),
        sync.RescanOffer("/media/movies/Z", sync.reconciliation_digest({"name": "Z"}), "arr:one", priority=0),
    ])
    assert accepted == ["Z"]  # a new pending import leads a large first-seen registered inventory
    folders = ["/media/movies/C", "/media/movies/A", "/media/movies/B", "/media/movies/A"]
    for _ in range(3):
        available[0] = True
        sync.rescan_folders(folders)
        # Even after the cooldown expires, older work must precede a repeated A.
        now[0] += sync.RESCAN_EVERY_SECONDS + 1
    assert accepted == ["Z", "A", "B", "C"]


def test_inventory_digest_is_durable_and_only_acknowledged_after_success(setup, monkeypatch) -> None:  # noqa: ANN001
    factory, _, _ = setup
    from app.services import library_automation

    with factory.begin() as db:
        db.add(StorageRoot(id="movies", label="Movies", path="/media/movies", mode="external", enabled=True))
        db.add(ImportRun(id="baseline", root_id="movies", user_id="admin", visibility="shared", state="succeeded", cursor="[]", counters={}))
    started: list[str] = []

    def start(root_id, user_id, visibility, **kwargs):  # noqa: ANN001, ANN202
        run_id = f"manager-{len(started)}"
        started.append(kwargs["scope"][0]["dir"])
        with factory.begin() as db:
            db.add(ImportRun(id=run_id, root_id=root_id, user_id=user_id, visibility=visibility, state="running",
                             trigger=kwargs["trigger"], scope=kwargs["scope"], cursor="[]", counters={}))
        return run_id

    clock = [utcnow()]
    monkeypatch.setattr(sync, "utcnow", lambda: clock[0])
    monkeypatch.setattr(sync, "_rescanned", {})
    monkeypatch.setattr(sync, "rescan_folder", REAL_RESCAN)
    monkeypatch.setattr(library_automation, "start_run_default", start)
    monkeypatch.setattr(library_automation.automation, "submit", lambda _: None)
    offer = sync.RescanOffer("/media/movies/Film", sync.reconciliation_digest({"file_id": 1}), "arr:one")
    companion = sync.RescanOffer(offer.path, sync.reconciliation_digest({"file_id": 9}), offer.source)
    snapshot = [offer, companion]

    sync.rescan_folders(snapshot)
    sync._rescanned.clear()  # a process restart must not lose the reconciliation admission
    sync.rescan_folders(snapshot)
    assert started == ["Film"]
    with factory.begin() as db:
        db.get(ImportRun, "manager-0").state = "succeeded"
        db.get(ImportRun, "manager-0").finished_at = clock[0]
    sync.rescan_folders(snapshot)
    assert started == ["Film"]
    with factory() as db:
        unchanged_at = db.query(AppMaintenanceState).filter(AppMaintenanceState.key.like("manager_reconcile:%")).one().updated_at
    sync.rescan_folders(snapshot)
    with factory() as db:
        assert db.query(AppMaintenanceState).filter(AppMaintenanceState.key.like("manager_reconcile:%")).one().updated_at == unchanged_at
    clock[0] += sync.SAFETY_RECONCILE_AFTER
    sync.rescan_folders(snapshot)
    assert started == ["Film", "Film"]  # bounded safety reconciliation covers signals the manager cannot expose
    with factory.begin() as db:
        db.get(ImportRun, "manager-1").state = "failed"
        db.get(ImportRun, "manager-1").finished_at = clock[0]
    sync.rescan_folders(snapshot)
    assert started == ["Film", "Film"]
    clock[0] += sync.RETRY_AFTER
    sync.rescan_folders(snapshot)
    assert started == ["Film", "Film", "Film"]
    with factory.begin() as db:
        db.get(ImportRun, "manager-2").state = "succeeded"
        db.get(ImportRun, "manager-2").finished_at = clock[0]
    sync.rescan_folders([sync.RescanOffer(offer.path, offer.digest, "arr:two"),
                         sync.RescanOffer(companion.path, companion.digest, "arr:two")])
    assert started == ["Film", "Film", "Film", "Film"]  # managers sharing a mapped folder keep independent baselines
    with factory() as db:
        states = db.query(AppMaintenanceState).filter(AppMaintenanceState.key.like("manager_reconcile:%")).all()
        assert len(states) == 2 and all("/media" not in state.key and "/media" not in state.value for state in states)

    with factory.begin() as db:
        db.get(ImportRun, "manager-3").state = "succeeded"
        db.get(ImportRun, "manager-3").finished_at = clock[0]

    changed = sync.RescanOffer(offer.path, sync.reconciliation_digest({"file_id": 2}), offer.source)
    sync.rescan_folders([changed, companion])
    assert started == ["Film", "Film", "Film", "Film", "Film"]
    with factory.begin() as db:
        db.get(ImportRun, "manager-4").state = "succeeded"
        db.get(ImportRun, "manager-4").finished_at = clock[0]
    sync.rescan_folders([
        sync.RescanOffer(changed.path, sync.reconciliation_digest({"file_id": 3}), changed.source),
        sync.RescanOffer("/media/movies/New", sync.reconciliation_digest({"file_id": 1}), changed.source, priority=0),
    ])
    assert started[-1] == "Film"  # a known change leads even a high-priority first-seen bootstrap folder


def test_failed_reconciliation_is_retried_but_one_root_is_admitted_per_pass(setup, monkeypatch) -> None:  # noqa: ANN001
    factory, _, _ = setup
    from app.services import library_automation

    with factory.begin() as db:
        db.add(StorageRoot(id="movies", label="Movies", path="/media/movies", mode="external", enabled=True))
        db.add(ImportRun(id="baseline", root_id="movies", user_id="admin", visibility="shared", state="succeeded", cursor="[]", counters={}))
    started: list[str] = []

    def start(root_id, user_id, visibility, **kwargs):  # noqa: ANN001, ANN202
        run_id = f"attempt-{len(started)}"
        started.append(kwargs["scope"][0]["dir"])
        with factory.begin() as db:
            db.add(ImportRun(id=run_id, root_id=root_id, user_id=user_id, visibility=visibility, state="running",
                             trigger=kwargs["trigger"], scope=kwargs["scope"], cursor="[]", counters={}))
        return run_id

    clock = [utcnow()]
    monkeypatch.setattr(sync, "utcnow", lambda: clock[0])
    monkeypatch.setattr(sync, "_rescanned", {})
    monkeypatch.setattr(sync, "rescan_folder", REAL_RESCAN)
    monkeypatch.setattr(library_automation, "start_run_default", start)
    monkeypatch.setattr(library_automation.automation, "submit", lambda _: None)
    offers = [sync.RescanOffer(f"/media/movies/{name}", sync.reconciliation_digest({"name": name})) for name in ("A", "B")]

    sync.rescan_folders(offers)
    assert started == ["A"]
    with factory.begin() as db:
        db.get(ImportRun, "attempt-0").state = "failed"
        db.get(ImportRun, "attempt-0").finished_at = clock[0]
    sync.rescan_folders(offers)
    assert started == ["A", "B"]  # A's retry cooldown lets the next folder make progress
    with factory.begin() as db:
        db.get(ImportRun, "attempt-1").state = "failed"
        db.get(ImportRun, "attempt-1").finished_at = clock[0]
    clock[0] += sync.RETRY_AFTER
    sync.rescan_folders(offers)
    assert started == ["A", "B", "A"]


def test_an_item_removed_from_the_arr_fails_only_that_request(setup) -> None:  # noqa: ANN001
    factory, fakes, _ = setup
    add_request(factory, arr_server_id="radarr", arr_item_id=404)
    add_request(factory, id="other", tmdb_id=604, arr_server_id="radarr", arr_item_id=101)
    fakes["radarr"].library[101] = {"id": 101, "hasFile": False, "path": "/movies/x"}
    fakes["radarr"].queue = [{"movieId": 101, "size": 10, "sizeleft": 5}]
    sync.sync_once()
    assert (load(factory).status, load(factory).failure_reason) == ("failed", "Removed from Radarr")
    assert (load(factory, "other").status, load(factory, "other").progress) == ("processing", 0.5)
    with factory() as session:
        assert session.get(ArrServer, "radarr").last_error is None
