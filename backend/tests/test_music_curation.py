"""Preview capabilities for bulk artwork and reversible, owner-only file writeback."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import threading

import pytest

from app.models import AppSettings, MediaTitle, MusicFileWriteback, MusicLookup, MusicTrackOverride
from app.services import music_curation, music_writeback, title_images
from app.services.titles import title_image_source
from test_music_api import ADMIN, ALBUM, ALICE, BOB, TRACKS, music_world  # noqa: F401


def preview_art(client, title_ids=None, base=None):  # noqa: ANN001, ANN201
    title_ids = title_ids or [ALBUM]
    return client.post("/api/music/artwork/preview", json={
        "title_ids": title_ids,
        "base": base or {title_id: "1" for title_id in title_ids},
        "image_type": "Primary",
    })


def _fake_encoded(data: bytes) -> title_images.Encoded:
    return title_images.Encoded("a" * 64, "image/jpeg", 200, 200, data)


def test_artwork_plan_is_owner_bound_expires_and_is_claimed_once_before_encoding(
    music_world, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    alice = client_for(ALICE)
    first = preview_art(alice)
    assert first.status_code == 200 and len(first.json()["plan_id"]) == 64
    first_id = first.json()["plan_id"]

    calls: list[bytes] = []
    monkeypatch.setattr(title_images, "encode", lambda data, *_args, **_kwargs: calls.append(data) or _fake_encoded(data))
    bob = client_for(BOB)
    other_user = bob.put(f"/api/music/artwork/bulk?plan_id={first_id}", content=b"large-image-body")
    assert (other_user.status_code, calls) == (409, [])

    with factory() as db:
        db.get(MusicLookup, first_id).expires_at = music_curation.utcnow() - timedelta(seconds=1)
        db.commit()
    alice = client_for(ALICE)
    expired = alice.put(f"/api/music/artwork/bulk?plan_id={first_id}", content=b"image")
    assert expired.status_code == 409 and calls == []

    second_id = preview_art(alice).json()["plan_id"]
    applied = alice.put(f"/api/music/artwork/bulk?plan_id={second_id}", content=b"new-cover")
    assert applied.status_code == 200
    assert (applied.json()["applied"], applied.json()["skipped"], calls) == (1, [], [b"new-cover"])
    replay = alice.put(f"/api/music/artwork/bulk?plan_id={second_id}", content=b"new-cover")
    assert replay.status_code == 409 and calls == [b"new-cover"]


def test_artwork_conflicts_are_skipped_and_successful_history_can_be_undone(
    music_world, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    monkeypatch.setattr(title_images, "encode", lambda data, *_args, **_kwargs: _fake_encoded(data))
    alice = client_for(ALICE)

    stale_id = preview_art(alice).json()["plan_id"]
    with factory() as db:
        album = db.get(MediaTitle, ALBUM)
        album.images = {"Primary": {"path": "changed.jpg", "tag": "changed"}}
        db.commit()
    stale = alice.put(f"/api/music/artwork/bulk?plan_id={stale_id}", content=b"cover")
    assert stale.json() == {
        "batch_id": None, "applied": 0, "skipped": [{"title_id": ALBUM, "reason": "changed_since"}],
    }

    current_id = preview_art(alice, base={ALBUM: "changed"}).json()["plan_id"]
    applied = alice.put(f"/api/music/artwork/bulk?plan_id={current_id}", content=b"cover")
    batch_id = applied.json()["batch_id"]
    assert applied.json()["applied"] == 1 and batch_id
    with factory() as db:
        assert title_image_source(db.get(MediaTitle, ALBUM), "Primary") == "a" * 64

    undone = alice.post(f"/api/metadata/batches/{batch_id}/undo")
    assert undone.status_code == 200 and undone.json()["restored"] == 1
    with factory() as db:
        assert title_image_source(db.get(MediaTitle, ALBUM), "Primary") == "changed"


def test_artwork_permission_is_checked_before_the_upload_is_processed(
    music_world, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    alice = client_for(ALICE)
    plan_id = preview_art(alice).json()["plan_id"]
    with factory() as db:
        db.get(AppSettings, 1).members_edit_metadata = False
        db.commit()
    processed: list[bytes] = []
    monkeypatch.setattr(title_images, "encode", lambda data, *_args, **_kwargs: processed.append(data) or _fake_encoded(data))
    alice = client_for(ALICE)
    reply = alice.put(f"/api/music/artwork/bulk?plan_id={plan_id}", content=b"must-not-process")
    assert reply.status_code == 403 and processed == []


def test_writeback_preview_apply_and_restore_are_admin_only_and_path_free(
    music_world, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    factory, client_for, root = music_world
    alice = client_for(ALICE)
    body = {"item_ids": [TRACKS[0]], "include_artwork": False}
    assert alice.post("/api/music/writeback/preview", json=body).status_code == 403

    admin = client_for(ADMIN)
    preview = admin.post("/api/music/writeback/preview", json=body)
    assert preview.status_code == 200
    assert str(root) not in repr(preview.json())
    assert set(preview.json()["entries"][0]) == {"item_id", "title", "format", "fields", "supported"}
    plan_id = preview.json()["plan_id"]

    writes: list[tuple[Path, dict]] = []
    monkeypatch.setattr(music_writeback, "writeback", lambda path, fields, **_kwargs: writes.append((path, fields)) or {
        "backup_id": "b" * 32, "format": path.suffix.removeprefix("."),
    })
    applied = admin.post("/api/music/writeback/apply", json={"plan_id": plan_id})
    assert applied.status_code == 200 and len(applied.json()["applied"]) == 1 and not applied.json()["skipped"]
    assert writes and writes[0][0].is_file()
    record_id = applied.json()["applied"][0]["id"]
    assert admin.post("/api/music/writeback/apply", json={"plan_id": plan_id}).status_code == 409

    alice = client_for(ALICE)
    assert alice.post(f"/api/music/writeback/{record_id}/restore").status_code == 403
    restored: list[tuple[Path, str]] = []
    monkeypatch.setattr(music_writeback, "restore", lambda path, backup_id, **_kwargs: restored.append((path, backup_id)))
    admin = client_for(ADMIN)
    reply = admin.post(f"/api/music/writeback/{record_id}/restore")
    assert reply.json() == {"restored": True} and restored[0][1] == "b" * 32
    assert admin.post(f"/api/music/writeback/{record_id}/restore").status_code == 409
    with factory() as db:
        assert db.get(MusicFileWriteback, record_id).restored_at is not None


def test_writeback_changed_since_is_refused_before_the_file_writer(
    music_world, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    factory, client_for, root = music_world
    admin = client_for(ADMIN)
    preview = admin.post("/api/music/writeback/preview", json={"item_ids": [TRACKS[0]]}).json()
    assert str(root) not in repr(preview)
    with factory() as db:
        db.add(MusicTrackOverride(item_id=TRACKS[0], fields={"title": "Changed after preview"}))
        db.commit()
    writes: list[Path] = []
    monkeypatch.setattr(music_writeback, "writeback", lambda path, *_args, **_kwargs: writes.append(path))

    result = admin.post("/api/music/writeback/apply", json={"plan_id": preview["plan_id"]})
    assert result.status_code == 200 and result.json()["applied"] == []
    assert result.json()["skipped"] == [{"item_id": TRACKS[0], "reason": "Track changed after the preview"}]
    assert writes == []


def test_explicit_lyrics_clear_is_previewed_and_written_as_a_tag_removal(music_world, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    with factory() as db:
        db.add(MusicTrackOverride(item_id=TRACKS[0], fields={"lyrics": ""}))
        db.commit()
    written = []
    monkeypatch.setattr(music_writeback, "writeback", lambda _path, fields, **_kwargs: written.append(fields) or {"backup_id": "c" * 32, "format": "flac"})
    admin = client_for(ADMIN)
    preview = admin.post("/api/music/writeback/preview", json={"item_ids": [TRACKS[0]], "include_artwork": False}).json()
    assert "lyrics" in preview["entries"][0]["fields"] and preview["entries"][0]["fields"]["lyrics"] is None
    result = admin.post("/api/music/writeback/apply", json={"plan_id": preview["plan_id"]})
    assert result.status_code == 200 and len(result.json()["applied"]) == 1
    assert written[0]["lyrics"] is None


def test_writeback_contention_preserves_preview_and_slot_is_released_after_success_or_failure(
    music_world, monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    factory, client_for, _root = music_world
    first_admin = client_for(ADMIN)
    second_admin = client_for(ADMIN)
    first_plan = first_admin.post(
        "/api/music/writeback/preview", json={"item_ids": [TRACKS[0]], "include_artwork": False},
    ).json()["plan_id"]
    second_plan = second_admin.post(
        "/api/music/writeback/preview", json={"item_ids": [TRACKS[1]], "include_artwork": False},
    ).json()["plan_id"]
    entered = threading.Event()
    proceed = threading.Event()
    writes: list[Path] = []

    def writeback(path: Path, _fields: dict, **_kwargs) -> dict:  # noqa: ANN003
        writes.append(path)
        if len(writes) == 1:
            entered.set()
            assert proceed.wait(timeout=2)
        return {"backup_id": "d" * 32, "format": "flac"}

    monkeypatch.setattr(music_writeback, "writeback", writeback)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(first_admin.post, "/api/music/writeback/apply", json={"plan_id": first_plan})
        assert entered.wait(timeout=2)
        refused = second_admin.post("/api/music/writeback/apply", json={"plan_id": second_plan})
        assert refused.status_code == 409
        assert "already running" in refused.json()["detail"]
        assert len(writes) == 1
        with factory() as db:
            assert db.get(MusicLookup, second_plan) is not None
        proceed.set()
        completed = active.result(timeout=2)

    assert completed.status_code == 200 and len(completed.json()["applied"]) == 1
    retried = second_admin.post("/api/music/writeback/apply", json={"plan_id": second_plan})
    assert retried.status_code == 200 and len(retried.json()["applied"]) == 1

    real_disk_usage = music_curation.shutil.disk_usage
    space_available = [False]
    monkeypatch.setattr(
        music_curation.shutil,
        "disk_usage",
        lambda path: real_disk_usage(path) if space_available[0] else real_disk_usage(path)._replace(free=0),
    )
    failed_plan = second_admin.post(
        "/api/music/writeback/preview", json={"item_ids": [TRACKS[0]], "include_artwork": False},
    ).json()["plan_id"]
    failed = second_admin.post("/api/music/writeback/apply", json={"plan_id": failed_plan})
    assert failed.status_code == 507

    space_available[0] = True
    after_failure_plan = second_admin.post(
        "/api/music/writeback/preview", json={"item_ids": [TRACKS[0]], "include_artwork": False},
    ).json()["plan_id"]
    after_failure = second_admin.post("/api/music/writeback/apply", json={"plan_id": after_failure_plan})
    assert after_failure.status_code == 200 and len(after_failure.json()["applied"]) == 1
