"""Whole-library arr observation remains separate from request dispatch and member visibility."""
from __future__ import annotations

import pytest

from arr_fake import API_KEY, FakeArr, library_title
from support import file_backed_session_factory, make_user, seed_app_settings

from app.models import (ArrObservationState, ArrObservedItem, ArrObservedRecord, ArrServer, ImportRun, LibraryItem,
                        LibraryItemArtifact, MediaArtifact, StorageRoot, User)
from app.models import MediaTitle
from app.services import arr_observer
from app.services.requests import arr
from app.services.requests.arr import ArrClient


@pytest.fixture
def observed():  # noqa: ANN201
    factory = file_backed_session_factory()
    fake = FakeArr("radarr")
    with factory() as db:
        db.add_all([
            make_user("admin", role="admin"), make_user("alice"), make_user("bob"),
            ArrServer(id="radarr", kind="radarr", name="Radarr", base_url=fake.url, api_key=API_KEY,
                      path_mappings=[{"remote": "/movies", "local": "/media/movies"}], enabled=True),
            StorageRoot(id="movies", label="Movies", path="/media/movies", mode="external", enabled=True),
            ImportRun(id="prior", root_id="movies", user_id="admin", visibility="shared", state="succeeded", cursor="[]", counters={}),
        ])
        seed_app_settings(db, requests_enabled=False)
    try:
        yield factory, fake
    finally:
        fake.close()


def _movie(fake: FakeArr) -> None:
    fake.library[101] = {"id": 101, "title": "The Matrix", "tmdbId": 603, "hasFile": True, "monitored": True,
                         "path": "/movies/The Matrix (1999)", "year": 1999}


def test_observation_runs_when_requests_are_disabled_paginates_and_starts_a_scoped_import(observed, monkeypatch) -> None:  # noqa: ANN001
    factory, fake = observed
    _movie(fake)
    fake.library[102] = {"id": 102, "title": "Placeholder", "tmdbId": 0, "hasFile": False, "path": "/movies/Placeholder"}
    fake.queue = [{"id": f"queue-{number}", "movieId": 101, "status": "downloading"} for number in range(1001)]
    rescans: list[str] = []
    monkeypatch.setattr(arr_observer, "rescan_folders", rescans.extend)

    assert arr_observer.sync_once("radarr") is True

    with factory() as db:
        state = db.get(ArrObservationState, "radarr")
        item = db.get(ArrObservedItem, ("radarr", 101))
        assert state and state.last_ok_at and state.last_error is None and state.summary["queue_count"] == 1001
        assert item and item.import_state == "pending_import" and item.details == {}
        assert db.query(ArrObservedRecord).filter_by(server_id="radarr", dataset="queue").count() == 1001
    assert [offer.path for offer in rescans] == ["/media/movies/The Matrix (1999)"]
    assert [path for method, path, _ in fake.requests if method == "GET" and path.endswith("/queue")] == ["/api/v3/queue", "/api/v3/queue"]


@pytest.mark.parametrize("kind", ["sonarr", "radarr"])
def test_registered_titles_keep_reconciling_new_seasons_and_replacements(observed, monkeypatch, kind) -> None:  # noqa: ANN001
    factory, fake = observed
    fake.kind = kind
    fake.resource = "series" if kind == "sonarr" else "movie"
    folder = "/media/movies/Series" if kind == "sonarr" else "/media/movies/Film"
    raw = {"id": 101, "title": "Series" if kind == "sonarr" else "Film", "tmdbId": 603,
           "hasFile": kind == "radarr", "path": "/movies/Series" if kind == "sonarr" else "/movies/Film",
           "seasons": [{"seasonNumber": 1, "statistics": {"episodeFileCount": 1}}]}
    fake.library[101] = raw
    with factory() as db:
        db.get(ArrServer, "radarr").kind = kind
        library_title(db, "series" if kind == "sonarr" else "movie", "existing", Tmdb="603")
        if kind == "sonarr":
            db.add(MediaTitle(id="season", type="season", key="season:1", name="Season 1", parent_id="existing"))
            db.add(MediaTitle(id="episode", type="episode", key="episode:1", name="Episode 1", parent_id="season"))
            db.get(LibraryItem, "item-existing").title_id = "episode"
        db.add(MediaArtifact(id="existing-file", root_id="movies", relative_path="existing.mkv", ownership="external"))
        db.add(LibraryItemArtifact(library_item_id="item-existing", artifact_id="existing-file"))
        db.commit()
    rescans: list[str] = []
    monkeypatch.setattr(arr_observer, "rescan_folders", rescans.extend)
    arr_observer.sync_once("radarr")
    rescans.clear()
    # Existing availability must not end reconciliation: later seasons and same-count
    # file replacements need the ordinary scoped scanner too, without a Request row.
    raw["seasons"].append({"seasonNumber": 2, "statistics": {"episodeFileCount": 2}})
    arr_observer.sync_once("radarr")
    assert [offer.path for offer in rescans] == [folder]
    with factory() as db:
        assert db.get(ArrObservedItem, ("radarr", 101)).import_state == "registered"


@pytest.mark.parametrize("kind", ["sonarr", "radarr"])
def test_reconciliation_fingerprint_ignores_unchanged_polls_and_detects_file_changes(observed, monkeypatch, kind) -> None:  # noqa: ANN001
    factory, fake = observed
    fake.kind = kind
    fake.resource = "series" if kind == "sonarr" else "movie"
    raw = {"id": 101, "title": "Title", "tmdbId": 603, "hasFile": kind == "radarr", "path": "/movies/Title",
           "movieFile": {"id": 10, "relativePath": "Title.mkv", "size": 100, "dateAdded": "2026-01-01T00:00:00Z"},
           "seasons": [{"seasonNumber": 1, "statistics": {"episodeFileCount": 1, "sizeOnDisk": 100}}]}
    fake.library[101] = raw
    id_field = "seriesId" if kind == "sonarr" else "movieId"
    fake.history = [{"id": 41, id_field: 101, "eventType": "downloadFolderImported", "date": "2026-01-01T00:00:00Z"},
                    {"id": 42, id_field: 101, "eventType": "grabbed", "date": "2025-12-31T00:00:00Z"}]
    with factory.begin() as db:
        db.get(ArrServer, "radarr").kind = kind
    offered = []
    monkeypatch.setattr(arr_observer, "rescan_folders", lambda values: offered.append(list(values)))

    arr_observer.sync_once("radarr")
    fake.history.reverse()
    arr_observer.sync_once("radarr")
    assert offered[0][0].digest == offered[1][0].digest

    fake.history.append({"id": 44, id_field: 101, "eventType": "downloadFolderImported", "date": "2026-01-02T00:00:00Z",
                         "episodeFileId" if kind == "sonarr" else "movieFileId": 11})
    arr_observer.sync_once("radarr")
    assert offered[2][0].digest != offered[1][0].digest
    if kind == "sonarr":
        raw["seasons"].append({"seasonNumber": 2, "statistics": {"episodeFileCount": 1, "sizeOnDisk": 200}})
        arr_observer.sync_once("radarr")
        assert offered[3][0].digest != offered[2][0].digest


def test_failed_or_malformed_refresh_retains_last_complete_snapshot_and_marks_it_stale(observed) -> None:  # noqa: ANN001
    factory, fake = observed
    _movie(fake)
    arr_observer.sync_once("radarr")
    fake.down = True
    arr_observer.sync_once("radarr")

    with factory() as db:
        state = db.get(ArrObservationState, "radarr")
        assert state and state.last_error == "Radarr endpoint returned HTTP 503"
        assert db.get(ArrObservedItem, ("radarr", 101)) is not None
        assert arr_observer.stale(db.get(ArrServer, "radarr"), state) is True


def test_malformed_second_page_is_not_a_partial_success(observed, monkeypatch) -> None:  # noqa: ANN001
    factory, fake = observed
    _movie(fake)
    arr_observer.sync_once("radarr")
    fake.queue = [{"id": f"queue-{number}", "movieId": 101} for number in range(1001)]
    route = fake.route

    def malformed(method, path, query, body):  # noqa: ANN001, ANN202
        status, reply = route(method, path, query, body)
        if (method, path, query.get("page")) == ("GET", "queue", "2"):
            reply["page"] = 1
        return status, reply

    monkeypatch.setattr(fake, "route", malformed)
    arr_observer.sync_once("radarr")

    with factory() as db:
        state = db.get(ArrObservationState, "radarr")
        assert state and state.last_error == "Radarr sent malformed pagination"
        assert db.query(ArrObservedRecord).filter_by(server_id="radarr", dataset="queue").count() == 0


def test_bounded_pagination_and_inflight_config_change_preserve_the_prior_snapshot(observed, monkeypatch) -> None:  # noqa: ANN001
    factory, fake = observed
    _movie(fake)
    arr_observer.sync_once("radarr")
    fake.queue = [{"id": "queue-1", "movieId": 101}]
    monkeypatch.setattr(arr, "MAX_PAGED_BYTES", 1)
    arr_observer.sync_once("radarr")
    with factory() as db:
        server = db.get(ArrServer, "radarr")
        state = db.get(ArrObservationState, "radarr")
        assert state and state.last_error == "Radarr sent too many records"
    monkeypatch.setattr(arr, "MAX_PAGED_BYTES", 64 * 1024 * 1024)
    with factory() as db:
        server = db.get(ArrServer, "radarr")
        prior_signature, observed = arr_observer._signature(server), ArrClient.of(server).observation()
        server.base_url = "http://127.0.0.1:1"
        db.commit()

    arr_observer._replace_success("radarr", prior_signature, observed)

    with factory() as db:
        assert db.get(ArrObservedItem, ("radarr", 101)) is not None
        assert db.get(ArrObservationState, "radarr").last_error == "Radarr sent too many records"


def test_member_title_explanation_uses_current_visibility_and_never_exposes_inventory(observed, api_client) -> None:  # noqa: ANN001
    factory, fake = observed
    _movie(fake)
    fake.library[102] = {"id": 102, "title": "Placeholder", "tmdbId": 0, "hasFile": False, "path": "/movies/Placeholder"}
    fake.health = [{"id": 1, "type": "warning", "source": "/movies/private", "wikiUrl": "https://example.test/diagnostic"}]
    arr_observer.sync_once("radarr")
    with factory() as db:
        library_title(db, "movie", "matrix", Tmdb="603")
        db.get(LibraryItem, "item-matrix").visibility = "private"
        db.get(LibraryItem, "item-matrix").user_id = "alice"
        db.add(MediaArtifact(id="matrix-artifact", root_id="movies", relative_path="The Matrix (1999)/The Matrix.mkv", ownership="external"))
        db.add(MediaTitle(id="album", type="album", key="album:one", name="Album", provider_ids={"Tmdb": "603"}))
        db.add(LibraryItem(id="item-album", title="Album", visibility="private", user_id="alice", status="available", title_id="album"))
        db.commit()
    current = {"id": "alice"}

    def who() -> User:
        with factory() as db:
            return db.get(User, current["id"])

    client = api_client(user=who, base_url="http://localhost")
    from app.db import get_db
    from app.main import app

    def observed_db():  # noqa: ANN202
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = observed_db
    assert client.get("/api/admin/arr/observations").status_code == 403
    current["id"] = "admin"
    inventory = client.get("/api/admin/arr/observations?page_size=100")
    assert inventory.status_code == 200
    assert inventory.json()["items"][0]["title"] == "The Matrix" and "/movies" not in inventory.text
    health = client.get("/api/admin/arr/servers/radarr/health").json()
    assert health["items"][0]["data"] == {"type": "warning", "source": "[path]"}
    assert next(item for item in inventory.json()["items"] if item["upstream_id"] == 102)["tmdb_id"] is None
    current["id"] = "alice"
    assert client.get("/api/arr/titles/matrix").json() == {"title_id": "matrix", "integrations": [{"state": "pending_import", "stale": False}]}
    with factory() as db:
        db.add(LibraryItemArtifact(library_item_id="item-album", artifact_id="matrix-artifact"))
        db.commit()
    assert client.get("/api/arr/titles/album").json() == {"title_id": "album", "integrations": []}
    with factory() as db:
        db.add(LibraryItemArtifact(library_item_id="item-matrix", artifact_id="matrix-artifact"))
        db.commit()
    assert client.get("/api/arr/titles/matrix").json() == {"title_id": "matrix", "integrations": [{"state": "registered", "stale": False}]}
    current["id"] = "bob"
    assert client.get("/api/arr/titles/matrix").status_code == 404
