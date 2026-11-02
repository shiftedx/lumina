"""Language requests remain distinct and fulfillment uses actual visible file tracks."""
from sqlalchemy import select

from app.models import LibraryItem, LibraryItemArtifact, MediaArtifact, MediaRequest, User
from app.services.requests import engine, languages, sync
from arr_fake import library_title
from test_requests_engine import db, user  # noqa: F401
from test_requests_api import clients  # noqa: F401
from title_support import ALICE, BOB, MOVIE_1080
from test_category_walls import walls  # noqa: F401


def test_request_languages_follow_exact_preferences_and_manual_review(clients):  # noqa: ANN001, F811
    sub = {"kind": "anime", "anilist_id": 21, "language": "sub"}
    first = clients("alice").post("/api/requests", json=sub)
    assert first.status_code == 201
    assert (first.json()["audio_language"], first.json()["subtitle_language"]) == ("ja", "en")
    same = clients("bob").post("/api/requests", json={"kind": "anime", "anilist_id": 21, "audio_language": "jpn", "subtitle_language": "eng"})
    assert same.status_code == 200 and same.json()["id"] == first.json()["id"]
    dub = clients("alice").post("/api/requests", json={"kind": "anime", "anilist_id": 21, "audio_language": "en"})
    assert dub.status_code == 201 and dub.json()["id"] != first.json()["id"]
    reviewed = clients("admin").post(f"/api/requests/{dub.json()['id']}/approve", json={})
    assert (reviewed.json()["status"], reviewed.json()["audio_language"], reviewed.json()["fulfillment_mode"]) == ("approved", "en", "manual")
    assert "failure_reason" not in reviewed.json()


def test_language_alternative_is_not_blocked_by_existing_film(db):  # noqa: ANN001, F811
    library_title(db, "movie", Tmdb="603")
    req, created = engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603, "audio_language": "jpn", "subtitle_language": "eng"})
    assert created and languages.preference(req) == ("ja", "en") and languages.mode(req, db) == "manual"


def test_detail_uses_visible_per_file_facts_and_never_guesses_coverage(walls):  # noqa: ANN001, F811
    with walls.db() as db:
        artifact_id = db.scalar(select(LibraryItemArtifact.artifact_id).where(LibraryItemArtifact.library_item_id == MOVIE_1080))
        artifact = db.get(MediaArtifact, artifact_id)
        artifact.probe = {"fingerprint": f"{artifact.size}:{artifact.mtime_ns}", "streams": [{"type": "audio", "language": "jpn"}, {"type": "subtitle", "language": "eng"}]}
        movie = db.get(LibraryItem, MOVIE_1080)
        movie.visibility, movie.user_id = "private", ALICE
        db.commit()
        title_id = movie.title_id
        alice = languages.availability(db, db.get(User, ALICE), title_id)
        bob = languages.availability(db, db.get(User, BOB), title_id)
        assert any(f["audio_languages"] == ["ja"] and f["subtitle_languages"] == ["en"] for f in alice["files"])
        assert not any("ja" in f["audio_languages"] for f in bob["files"])
        artifact.probe = {"fingerprint": "stale", "streams": [{"type": "audio", "language": "jpn"}]}
        db.flush()
        stale = languages.availability(db, db.get(User, ALICE), title_id)
        assert not any("ja" in f["audio_languages"] for f in stale["files"])
        req = MediaRequest(kind="show", media_type="tv", audio_language="ja", subtitle_language="en", seasons=[1])
        assert not sync._language_complete(req, alice, {})
        assert not sync._language_complete(req, alice, {1: 2})
        assert sync._language_complete(req, {"truncated": False, "files": [
            {"season": 1, "episode": 1, "audio_languages": ["ja"], "subtitle_languages": ["en"]},
            {"season": 1, "episode": 2, "audio_languages": ["ja"], "subtitle_languages": ["en"]},
        ]}, {1: 2})
        empty = languages.availability(db, db.get(User, ALICE), None)
        assert empty == {"state": "unknown", "files": [], "truncated": False, "total_files": 0}


def test_approval_can_clear_only_the_requested_language_fields(clients):  # noqa: ANN001, F811
    body = {"kind": "show", "tmdb_id": 1399, "audio_language": "ja", "subtitle_language": "en"}
    created = clients("alice").post("/api/requests", json=body).json()
    approved = clients("admin").post(f"/api/requests/{created['id']}/approve", json={"subtitle_language": None}).json()
    assert approved["audio_language"] == "ja" and approved["subtitle_language"] is None
    assert approved["fulfillment_mode"] == "manual" and approved["status"] == "approved"


def test_legacy_anime_uses_automatic_mode_only_with_its_configured_profile(db):  # noqa: ANN001, F811
    from app.models import ArrServer

    req = MediaRequest(kind="anime", media_type="tv", language="dub")
    server = ArrServer(id="sonarr-test", kind="sonarr", name="Sonarr", base_url="http://example.test", api_key="x", enabled=True)
    db.add(server)
    db.commit()
    assert languages.mode(req, db) == "manual"
    server.dub_profile_id = 12
    db.commit()
    assert languages.mode(req, db) == "automatic"
    server.enabled = False
    db.commit()
    assert languages.mode(req, db) == "manual"


def test_regional_languages_are_not_conflated(db):  # noqa: ANN001, F811
    en_us, _ = engine.create(db, user(db, "alice"), {"kind": "movie", "tmdb_id": 603, "audio_language": "en-US"})
    en_gb, created = engine.create(db, user(db, "bob"), {"kind": "movie", "tmdb_id": 603, "audio_language": "eng-GB"})
    assert created and en_us.id != en_gb.id
    assert languages.preference(en_us) == ("en-us", None)
    assert languages.preference(en_gb) == ("en-gb", None)
    assert not languages.matches({"audio_languages": ["en"], "subtitle_languages": []}, en_us)
    assert languages.matches({"audio_languages": ["en-us"], "subtitle_languages": []}, en_us)
