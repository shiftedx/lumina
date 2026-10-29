"""Durable OMDb request-budget contracts at the ratings-worker boundary."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, select

from app import db as db_module
from app.db import session_scope
from app.models import MediaTitle, OmdbRatingCache, OmdbRequestReservation
from app.services import omdb, omdb_ratings
from support import seed_app_settings


NOW = datetime(2026, 10, 28, 12, 0, 0)


class Scores:
    def __init__(self, values: dict[str, dict[str, float]] | None = None, *, quota_first: bool = False) -> None:
        self.values = values or {}
        self.calls: list[str] = []
        self.quota_first = quota_first

    def ratings(self, imdb_id: str) -> dict[str, float]:
        self.calls.append(imdb_id)
        if self.quota_first and len(self.calls) == 1:
            raise omdb.OmdbQuotaError("OMDb request limit reached")
        return self.values.get(imdb_id, {"imdb_rating": 8.0})


@pytest.fixture
def ratings_db() -> None:
    db_module.init_db()
    with session_scope() as db:
        seed_app_settings(db)


def add_title(
    imdb_id: str,
    *,
    added_at: datetime = NOW,
    metadata_json: dict | None = None,
    field_sources: dict | None = None,
) -> str:
    title_id = str(uuid.uuid4())
    with session_scope() as db:
        db.add(MediaTitle(
            id=title_id, type="movie", key=f"root:Movies/{title_id}", name=title_id, added_at=added_at,
            provider_ids={"Imdb": imdb_id}, metadata_json=metadata_json or {}, field_sources=field_sources or {}, images={},
        ))
    return title_id


def title(title_id: str) -> MediaTitle:
    with session_scope() as db:
        return db.get(MediaTitle, title_id)


def install(monkeypatch: pytest.MonkeyPatch, client: Scores) -> Scores:
    monkeypatch.setattr(omdb_ratings.omdb, "client_for", lambda _record: client)
    return client


def test_rolling_quota_is_shared_by_workers_and_key_tests_then_reopens(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    first, second = add_title("tt0000001"), add_title("tt0000002")
    with session_scope() as db:
        db.add_all(OmdbRequestReservation(requested_at=NOW - timedelta(hours=1), imdb_id=f"tt9{number:09d}") for number in range(949))
    client = install(monkeypatch, Scores())

    omdb_ratings.run_due_batch(NOW)
    assert client.calls == ["tt0000001"]
    with pytest.raises(omdb.OmdbQuotaError):
        omdb_ratings.test_key(client, NOW)
    assert client.calls == ["tt0000001"]  # the rejected connection test never reaches the provider

    omdb_ratings.run_due_batch(NOW + timedelta(hours=24, seconds=1))
    assert client.calls == ["tt0000001", "tt0000002"]
    assert title(first).metadata_json["imdb_rating"] == title(second).metadata_json["imdb_rating"] == 8.0


def test_concurrent_batches_make_one_durable_claim(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    add_title("tt0000010")
    client = install(monkeypatch, Scores())
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: omdb_ratings.run_due_batch(NOW), range(2)))
    assert client.calls == ["tt0000010"]
    with session_scope() as db:
        cache = db.get(OmdbRatingCache, "tt0000010")
        assert cache is not None and cache.fetched_at == NOW and cache.lease_token is None


def test_parallel_workers_at_the_950th_slot_make_exactly_one_request(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    add_title("tt0000011")
    add_title("tt0000012")
    with session_scope() as db:
        db.add_all(OmdbRequestReservation(requested_at=NOW - timedelta(hours=1), imdb_id=f"tt8{number:09d}") for number in range(949))
    client = install(monkeypatch, Scores())

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: omdb_ratings.run_due_batch(NOW), range(2)))
    assert len(client.calls) == 1 and client.calls[0] in {"tt0000011", "tt0000012"}
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(OmdbRequestReservation)) == 950


def test_new_unscored_first_deduplicates_and_caches_partial_or_empty_results(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    newest = add_title("tt0000020", added_at=NOW)
    newest_copy = add_title("tt0000020", added_at=NOW)
    oldest = add_title("tt0000021", added_at=NOW - timedelta(days=1))
    scored = add_title("tt0000022", added_at=NOW - timedelta(days=2), metadata_json={"imdb_rating": 7.0}, field_sources={"imdb_rating": "tmdb"})
    client = install(monkeypatch, Scores({"tt0000020": {"imdb_rating": 8.4}, "tt0000021": {"rotten_tomatoes_rating": 81}, "tt0000022": {}}))

    omdb_ratings.run_due_batch(NOW)
    assert client.calls == ["tt0000020", "tt0000021", "tt0000022"]
    assert title(newest).metadata_json["imdb_rating"] == title(newest_copy).metadata_json["imdb_rating"] == 8.4
    assert title(oldest).metadata_json["rotten_tomatoes_rating"] == 81
    assert title(scored).metadata_json["imdb_rating"] == 7.0

    omdb_ratings.run_due_batch(NOW + timedelta(minutes=1))
    assert client.calls == ["tt0000020", "tt0000021", "tt0000022"]


def test_fresh_cache_populates_new_duplicates_and_respects_manual_nfo_and_manual_imdb(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    cached_id = "tt0000030"
    with session_scope() as db:
        db.add(OmdbRatingCache(imdb_id=cached_id, scores={"imdb_rating": 8.1, "rotten_tomatoes_rating": 81, "metacritic_rating": 66}, fetched_at=NOW))
    plain = add_title(cached_id)
    nfo = add_title(cached_id, metadata_json={"imdb_rating": 9.4}, field_sources={"imdb_rating": "nfo"})
    edited = add_title(cached_id, metadata_json={"rotten_tomatoes_rating": 99}, field_sources={"rotten_tomatoes_rating": "user"})
    manual = add_title("tt0000031", field_sources={"provider_ids": "user"})
    client = install(monkeypatch, Scores({"tt0000031": {"imdb_rating": 7.7}}))

    omdb_ratings.run_due_batch(NOW)
    assert client.calls == ["tt0000031"]
    assert title(plain).metadata_json == {"imdb_rating": 8.1, "rotten_tomatoes_rating": 81, "metacritic_rating": 66}
    assert title(nfo).metadata_json["imdb_rating"] == 9.4 and title(nfo).metadata_json["metacritic_rating"] == 66
    assert title(edited).metadata_json["rotten_tomatoes_rating"] == 99 and title(edited).metadata_json["imdb_rating"] == 8.1
    assert title(manual).metadata_json["imdb_rating"] == 7.7


def test_changed_identity_during_fetch_never_receives_old_identity_scores(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    title_id = add_title("tt0000040")

    class ChangingScores(Scores):
        def ratings(self, imdb_id: str) -> dict[str, float]:
            self.calls.append(imdb_id)
            with session_scope() as db:
                db.get(MediaTitle, title_id).provider_ids = {"Imdb": "tt0000041"}
            return {"imdb_rating": 8.8}

    client = install(monkeypatch, ChangingScores())
    omdb_ratings.run_due_batch(NOW)
    changed = title(title_id)
    assert client.calls == ["tt0000040"]
    assert changed.provider_ids == {"Imdb": "tt0000041"} and "imdb_rating" not in changed.metadata_json


def test_interrupted_request_keeps_its_reservation_and_resumes_after_restart(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    title_id = add_title("tt0000042")

    class Interrupted(Scores):
        def ratings(self, imdb_id: str) -> dict[str, float]:
            self.calls.append(imdb_id)
            raise SystemExit("simulated process exit")

    interrupted = install(monkeypatch, Interrupted())
    with pytest.raises(SystemExit, match="simulated process exit"):
        omdb_ratings.run_due_batch(NOW)
    assert interrupted.calls == ["tt0000042"]

    db_module.engine.dispose()  # a restarted process obtains new SQLite connections, not the crashed worker's session
    resumed = install(monkeypatch, Scores({"tt0000042": {"imdb_rating": 8.6}}))
    omdb_ratings.run_due_batch(NOW + omdb_ratings.LEASE_FOR - timedelta(seconds=1))
    assert resumed.calls == []
    omdb_ratings.run_due_batch(NOW + omdb_ratings.LEASE_FOR + timedelta(seconds=1))
    assert resumed.calls == ["tt0000042"] and title(title_id).metadata_json["imdb_rating"] == 8.6
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(OmdbRequestReservation)) == 2


def test_provider_quota_cooldown_blocks_all_ids_then_resumes(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    add_title("tt0000050")
    add_title("tt0000051")
    client = install(monkeypatch, Scores(quota_first=True))

    omdb_ratings.run_due_batch(NOW)
    assert client.calls == ["tt0000050"]
    with session_scope() as db:
        blocked = db.scalar(select(OmdbRequestReservation.blocked_until).where(OmdbRequestReservation.imdb_id == "tt0000050"))
    assert blocked == NOW + timedelta(hours=24)
    omdb_ratings.run_due_batch(NOW + timedelta(hours=1))
    assert client.calls == ["tt0000050"]

    omdb_ratings.run_due_batch(NOW + timedelta(hours=24, seconds=1))
    assert "tt0000051" in client.calls


def test_protected_new_titles_do_not_starve_an_older_unscored_identity(ratings_db, monkeypatch) -> None:  # noqa: ANN001
    protected = {"imdb_rating": 9.0, "rotten_tomatoes_rating": 95, "metacritic_rating": 80}
    sources = {field: "nfo" for field in protected}
    with session_scope() as db:
        db.add_all(
            MediaTitle(
                id=f"protected-{number}", type="movie", key=f"root:Movies/protected-{number}", name="Protected",
                added_at=NOW + timedelta(minutes=1), provider_ids={"Imdb": f"tt{1_000_000 + number:07d}"},
                metadata_json=protected, field_sources=sources, images={},
            )
            for number in range(501)
        )
    add_title("tt0000060", added_at=NOW - timedelta(days=1))
    monkeypatch.setattr(omdb_ratings, "BATCH_SIZE", 1)
    client = install(monkeypatch, Scores())

    omdb_ratings.run_due_batch(NOW)
    assert client.calls == ["tt0000060"]
